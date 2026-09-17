"""Standalone college-football capture. Separate process, separate database.

    python cfb_probe.py                 # run until stopped
    python cfb_probe.py --discover      # catalogue only, no polling
    python cfb_probe.py --report        # what was captured

DELIBERATELY NOT PART OF run_logger.py. NFL Sunday is the stress test for the
main logger and this is a throwaway probe running the day before it: nothing
here may touch the main logger's market universe, its database, its cadence or
its rate-limit budget. Kill it Sunday morning.

  writes   <config storage dir>/cfb_probe.db   (config.DB_PATH is repointed
                                               in main(); the DIRECTORY is
                                               derived from config, never typed)
  raw      <config.RAW_DIR>/cfb_kalshi/        (the adapters archive under `name`)
           <config.RAW_DIR>/cfb_polymarket/

WHAT IS REUSED AND WHAT IS NOT
The venue adapters are reused as-is for QUOTES - `fetch_quotes` takes a list of
market dicts and knows nothing about which sport they are. Discovery is NOT
reused, because `venues.kalshi.is_football_series` explicitly EXCLUDES college:
that exclusion exists to keep KXNCAAFCO-NFL-EAVE out of the NFL catalogue, so
relaxing it would break NFL discovery to fix CFB. CFB gets its own classifier.

The subclasses below change exactly one thing - `name` - so raw lands under
data/raw/cfb_* and every row is stamped with a venue that cannot be confused
with the NFL capture. No adapter behaviour is overridden.

NO depth capture, NO oddsapi, NO settlement, NO outcome mapping. Capture only.

KILL IT SUNDAY MORNING, before the NFL slate:

    Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
      Where-Object { $_.CommandLine -match 'cfb_probe' } |
      ForEach-Object { Stop-Process -Id $_.ProcessId -Force }

It measured 2.31 GB/day of raw - more than the entire NFL logger's 1.71 - and
it shares Kalshi's rate limit with the process that Sunday depends on.
"""
import argparse
import asyncio
import os
import re
import signal
import sqlite3
import time
from datetime import datetime, timezone

import httpx

import config
from core import single_instance

# Repointed before store is used. store reads config at call time, so this has
# to happen before init_db() and it must happen in THIS process only.
# STORAGE LOCATION COMES FROM CONFIG, NEVER FROM A PATH LITERAL.
#
# This was `os.path.join("data", "cfb_probe.db")`, which resolves relative to
# the process working directory - the repo on C: - while raw correctly landed on
# D: because it reads config.RAW_DIR. The probe half-inherited config and put a
# 4.3 GB database on a disk with 22 GB free. Deriving the directory from
# config.DB_PATH means the probe cannot disagree with the logger about where
# data lives, and moving the store moves the probe with it.
NFL_DB = config.DB_PATH
STORAGE_DIR = os.path.dirname(os.path.abspath(NFL_DB))
CFB_DB = os.path.join(STORAGE_DIR, "cfb_probe.db")

# --- the CFB classifier -----------------------------------------------------
# Kalshi files college football as KXNCAAF*. The F is load-bearing: KXNCAAMBB,
# KXNCAAMBS, KXNCAAMBA and KXNCAAMWR are basketball, baseball and wrestling,
# and a bare NCAA match pulls in 151 series that are not football at all
# (288 NCAA/COLLEGE series against 137 with NCAAF).
NCAAF_RE = re.compile(r"NCAAF")
# Belt and braces on the title. Measured 2026-09-10: zero NCAAF-ticker series
# name another sport in their title, so this currently excludes nothing - it is
# here because the ticker scheme is Kalshi's to change and a basketball series
# leaking into a football probe would look like a discovery win.
OTHER_SPORT_RE = re.compile(
    r"BASKETBALL|BASEBALL|HOCKEY|SOCCER|WRESTL|VOLLEY|GOLF|TENNIS|LACROSSE"
    r"|SOFTBALL|TRACK|SWIM")

POLY_TAGS = ("cfb", "ncaaf", "ncaa-football")

POLL_HOT = 15          # inside HOT_WINDOW of kickoff
POLL_GAME = 60
POLL_FUTURES = 300
HOT_WINDOW = 2 * 3600
DISCOVERY_EVERY = 900
LOOP_TICK = 1.0

_stop = asyncio.Event()


def log(msg):
    print(f"{datetime.now(timezone.utc).strftime('%H:%M:%S')}Z {msg}",
          flush=True)


def is_cfb_series(ticker: str, title: str = "", category: str = "") -> bool:
    """Is this Kalshi series college football?

    Category AND ticker, the same shape as the NFL filter and for the same
    reason: category "Sports" alone is 3,755 series, and an NCAAF substring
    without the category guard would trust a ticker scheme to stay a ticker
    scheme.
    """
    t = (ticker or "").upper()
    ti = (title or "").upper()
    if (category or "").strip().lower() != "sports":
        return False
    if OTHER_SPORT_RE.search(ti):
        return False
    return bool(NCAAF_RE.search(t) or "COLLEGE FOOTBALL" in ti)


def classify(ticker: str, title: str) -> str:
    """Coarse market type. Only used to pick a polling cadence."""
    t = (ticker or "").upper()
    ti = (title or "").upper()
    if any(w in t for w in ("CHAMP", "HEISMAN", "CFPPOLL", "WINS", "COTY",
                            "AWARD", "QUAL", "CONF", "COACH")):
        return "future"
    if "SPREAD" in t or "SPREAD" in ti:
        return "spread"
    if "TOTAL" in t or "OVER" in ti:
        return "total"
    if "GAME" in t or "WINNER" in ti:
        return "moneyline"
    return "game"


# --- adapters: reused, renamed --------------------------------------------

def _clients(http):
    """Subclass ONLY to change `name`.

    That one attribute decides two things: the directory raw payloads land in
    (data/raw/cfb_*) and the `venue` stamped on every market and quote row.
    Both must differ from the NFL capture or a shared table could not tell the
    two apart later. Nothing else is overridden - fetch_quotes is the shipped
    adapter, byte for byte.
    """
    from venues.base import RateLimiter
    from venues.kalshi import KalshiClient
    from venues.polymarket import PolymarketClient

    class CfbKalshi(KalshiClient):
        name = "cfb_kalshi"

    class CfbPolymarket(PolymarketClient):
        name = "cfb_polymarket"

    # Its own limiter, at a FRACTION of the NFL budget. This process shares a
    # rate limit with the logger that is about to run a 13-game slate, and the
    # probe is the expendable one of the two.
    return [CfbKalshi(http, RateLimiter(config.rps_for("kalshi") / 2.0)),
            CfbPolymarket(http, RateLimiter(config.rps_for("polymarket") / 2.0))]


# --- discovery -------------------------------------------------------------

async def kalshi_cfb_markets(client):
    """Every open CFB market, series by series.

    Same shape as the NFL path - /series, filter, then /markets per series -
    but with the CFB classifier and no close-time horizon: a college slate is
    one Saturday and the probe wants the whole catalogue, not a rolling window.
    """
    payload = await client.get_json(f"{config.KALSHI_BASE}/series",
                                    params={"category": "Sports"},
                                    archive_as="series")
    series = [s for s in (payload or {}).get("series", [])
              if is_cfb_series(s.get("ticker", ""), s.get("title", ""),
                               s.get("category", ""))]
    log(f"cfb_kalshi   {len(series)} CFB series of "
        f"{len((payload or {}).get('series', []))} Sports series")

    from venues.kalshi import _f, _iso
    out, seen = [], set()
    for s in series:
        ticker = s.get("ticker")
        cursor = None
        for _page in range(20):
            p = {"series_ticker": ticker, "status": "open", "limit": 200}
            if cursor:
                p["cursor"] = cursor
            body = await client.get_json(f"{config.KALSHI_BASE}/markets",
                                         params=p, archive_as="markets")
            markets = (body or {}).get("markets", []) or []
            for m in markets:
                tk = m.get("ticker")
                if not tk or tk in seen:
                    continue
                seen.add(tk)
                out.append({
                    "venue": client.name, "market_id": tk,
                    "event_id": m.get("event_ticker"),
                    "market_type": classify(ticker, m.get("title", "")),
                    "subject": m.get("yes_sub_title") or m.get("no_sub_title"),
                    "line": _f(m.get("floor_strike"))
                            if m.get("floor_strike") is not None
                            else _f(m.get("cap_strike")),
                    "title": m.get("title") or "",
                    "open_ts": _iso(m.get("open_time")),
                    "close_ts": _iso(m.get("close_time")),
                    "settle_ts": _iso(m.get("expiration_time")),
                    "result": m.get("result") or None,
                    "_series": ticker,
                    "_volume": _f(m.get("volume_fp")),
                    "_open_interest": _f(m.get("open_interest_fp")),
                })
            cursor = (body or {}).get("cursor")
            if not cursor or not markets:
                break
    return out


async def poly_cfb_markets(client):
    """CFB events across every tag that resolves, deduped by market id.

    Three tags return results and they overlap: `cfb` and `ncaaf` each return a
    full page where `college-football` returns one event. Union then dedupe
    rather than betting on which tag Polymarket keeps maintaining.
    """
    from venues.polymarket import _f, _iso, _token_ids
    from venues.base import classify_market

    events, seen_ev = [], set()
    for tag in POLY_TAGS:
        offset = 0
        while offset <= config.POLY_MAX_OFFSET:
            body = await client.get_json(
                f"{config.POLY_GAMMA}/events",
                params={"limit": config.POLY_PAGE_LIMIT, "offset": offset,
                        "closed": "false", "tag_slug": tag},
                archive_as="events")
            batch = body if isinstance(body, list) else (body or {}).get("data", [])
            if not batch:
                break
            for e in batch:
                key = e.get("id") or e.get("slug")
                if key and key not in seen_ev:
                    seen_ev.add(key)
                    events.append(e)
            offset += len(batch)
    log(f"cfb_poly     {len(events)} events across tags {POLY_TAGS}")

    out = []
    for e in events:
        for m in e.get("markets") or []:
            if not (m.get("active") and not m.get("closed")
                    and m.get("enableOrderBook") and m.get("acceptingOrders")):
                continue
            if not _token_ids(m):
                continue
            out.append({
                "venue": client.name,
                "market_id": str(_token_ids(m)[0]),
                "event_id": e.get("slug") or e.get("ticker"),
                "market_type": classify_market(m.get("question", ""),
                                               e.get("slug", "")),
                "subject": m.get("groupItemTitle") or None,
                "line": None,
                "title": m.get("question"),
                "open_ts": _iso(m.get("startDate")),
                "close_ts": _iso(m.get("gameStartTime")) or _iso(m.get("endDate")),
                "settle_ts": _iso(m.get("endDate")),
                "result": None,
                "_liquidity": _f(m.get("liquidityNum")),
            })
    return out


async def discover(client):
    if client.name == "cfb_kalshi":
        return await kalshi_cfb_markets(client)
    return await poly_cfb_markets(client)


# --- polling ---------------------------------------------------------------

def tier_for(market, now=None):
    """Coarse, and deliberately NOT the main logger's tiering.

    run_logger keys on kickoff via a market_outcome -> nfl_games join. There is
    no CFB schedule in this store and no mapping in scope, so this keys on the
    venue's own close_ts and accepts that Kalshi's close is game END. For a
    capture probe that is the wrong tier for a few hours per game, which costs
    resolution on in-play prices and nothing else.
    """
    now = time.time() if now is None else now
    if market.get("market_type") == "future":
        return "futures"
    close = market.get("close_ts")
    if not close:
        return "game"
    secs = close - now
    if 0 < secs < HOT_WINDOW:
        return "hot"
    return "game"


async def poll(client, markets, tier):
    subset = [m for m in markets if tier_for(m) == tier]
    if not subset:
        return
    t0 = time.time()
    try:
        rows = await client.fetch_quotes(subset)
        for r in rows:
            r["sport"] = "cfb"            # the adapters stamp 'nfl'
        n = store.write_quotes(rows)
        store.log_poll(client.name, f"quotes:{tier}", len(subset), n, True,
                       None, time.time() - t0)
        log(f"{client.name:15s} {tier:7s} {len(subset):5d} markets -> {n:5d} "
            f"quotes  {time.time()-t0:.1f}s")
    except Exception as e:
        store.log_poll(client.name, f"quotes:{tier}", len(subset), 0, False, e,
                       time.time() - t0)
        log(f"{client.name:15s} {tier:7s} ERROR {type(e).__name__}: {e}")


async def worker(client):
    markets, last_discovery = [], 0.0
    cadence = {"futures": POLL_FUTURES, "game": POLL_GAME, "hot": POLL_HOT}
    next_run = {t: 0.0 for t in cadence}
    inflight = None

    while not _stop.is_set():
        now = time.time()
        if inflight is None and now - last_discovery > DISCOVERY_EVERY:
            inflight = asyncio.create_task(_discover_safe(client))
            last_discovery = now
        if inflight is not None and inflight.done():
            got = inflight.result()
            if got is not None:
                markets = got
            inflight = None

        for tier, every in cadence.items():
            if now >= next_run[tier]:
                await poll(client, markets, tier)
                next_run[tier] = now + every
        try:
            await asyncio.wait_for(_stop.wait(), timeout=LOOP_TICK)
        except asyncio.TimeoutError:
            pass


async def _discover_safe(client):
    t0 = time.time()
    try:
        markets = await discover(client)
        await asyncio.to_thread(store.upsert_markets, markets)
        store.log_poll(client.name, "discovery", len(markets), 0, True, None,
                       time.time() - t0)
        log(f"{client.name:15s} discovery -> {len(markets)} CFB markets "
            f"({time.time()-t0:.1f}s)")
        return markets
    except Exception as e:
        store.log_poll(client.name, "discovery", 0, 0, False, e,
                       time.time() - t0)
        log(f"{client.name:15s} discovery ERROR {type(e).__name__}: {e}")
        return None


# --- the report the brief asks for -----------------------------------------

def report():
    con = sqlite3.connect(f"file:{config.DB_PATH}?mode=ro", uri=True)
    q = lambda s, *a: con.execute(s, a).fetchall()

    print("\n" + "=" * 76)
    print("CFB CAPTURE - catalogue size, market types, rate-limit behaviour")
    print("=" * 76)

    print("\n1. CATALOGUE SIZE - does CFB fit in the main logger?")
    print(f"   {'venue':<18}{'markets':>10}{'events':>9}{'quotes':>11}")
    for v, m, e, in q("""SELECT venue, COUNT(*), COUNT(DISTINCT event_id)
                           FROM markets GROUP BY 1 ORDER BY 2 DESC"""):
        n = q("SELECT COUNT(*) FROM quotes WHERE venue=?", v)[0][0]
        print(f"   {v:<18}{m:>10,}{e:>9,}{n:>11,}")
    nfl_path = NFL_DB
    nfl = (sqlite3.connect(f"file:{nfl_path}?mode=ro", uri=True)
           if nfl_path and os.path.exists(nfl_path) else None)
    if nfl:
        print("\n   for comparison, the NFL logger's live catalogue:")
        for v, m in nfl.execute(
                "SELECT venue, COUNT(*) FROM markets WHERE venue IN "
                "('kalshi','polymarket') GROUP BY 1"):
            print(f"   {v:<18}{m:>10,}")
        nfl.close()

    print("\n2. MARKET TYPES - are there player props at all?")
    print(f"   {'venue':<18}{'market_type':<14}{'markets':>9}")
    for v, t, n in q("""SELECT venue, COALESCE(market_type,'(null)'), COUNT(*)
                          FROM markets GROUP BY 1,2 ORDER BY 1, 3 DESC"""):
        print(f"   {v:<18}{t:<14}{n:>9,}")

    print("\n   Kalshi series behind those markets (the level that decides "
          "whether\n   a player prop exists as a PRODUCT rather than as a "
          "market):")
    for s, n in q("""SELECT substr(market_id, 1, instr(market_id||'-','-')-1),
                            COUNT(*) FROM markets WHERE venue='cfb_kalshi'
                      GROUP BY 1 ORDER BY 2 DESC LIMIT 18"""):
        print(f"     {s:<26}{n:>7,}")

    print("\n3. RATE LIMIT under a bigger catalogue")
    print(f"   {'venue':<18}{'endpoint':<18}{'calls':>7}{'ok':>6}"
          f"{'fail':>6}{'avg s':>8}{'max s':>8}")
    for v, e, n, ok, avg, mx in q(
            """SELECT venue, endpoint, COUNT(*), SUM(ok),
                      ROUND(AVG(elapsed_s),2), ROUND(MAX(elapsed_s),2)
                 FROM poll_log GROUP BY 1,2 ORDER BY 1,2"""):
        print(f"   {v:<18}{e:<18}{n:>7,}{ok or 0:>6}{n-(ok or 0):>6}"
              f"{avg:>8}{mx:>8}")
    fails = q("""SELECT venue, endpoint, substr(error,1,90), COUNT(*)
                   FROM poll_log WHERE ok=0 GROUP BY 1,2,3""")
    if fails:
        print("\n   failures:")
        for v, e, err, n in fails:
            print(f"     {v} {e} x{n}: {err}")
    else:
        print("\n   no failed calls")

    print("\n4. WHAT WAS ACTUALLY CAPTURED")
    for v, n, mk, lo, hi in q(
            """SELECT venue, COUNT(*), COUNT(DISTINCT market_id),
                      MIN(ts), MAX(ts) FROM quotes GROUP BY 1"""):
        span = (hi - lo) / 3600.0 if hi and lo else 0
        print(f"   {v:<18}{n:>9,} quotes over {mk:>6,} markets, "
              f"{span:.1f}h span")
    con.close()


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--discover", action="store_true",
                    help="catalogue only, no polling")
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--minutes", type=float,
                    help="stop after this long (the probe is disposable)")
    args = ap.parse_args()

    # REPOINT BEFORE store IS TOUCHED. Separate file, separate everything.
    config.DB_PATH = CFB_DB
    global store
    import store as _store
    store = _store
    store.init_db()

    if args.report:
        report()
        return
    # Below this line the probe writes raw shards, so below this line is where
    # the lock goes. A --report run writes nothing and must not be refused just
    # because a probe is live. THIS is the process that ran twice on 2026-09-11
    # and left 43 of 114 shards unreadable, and --discover archives too, so the
    # lock has to cover both paths in _run(), not only polling.
    single_instance.acquire(single_instance.CFB_PROBE)
    asyncio.run(_run(args))


async def _run(args):
    deadline = time.time() + args.minutes * 60 if args.minutes else None
    log(f"CFB PROBE  db={config.DB_PATH}  raw={config.RAW_DIR}/cfb_*")
    log(f"tiers: hot {POLL_HOT}s (<{HOT_WINDOW/3600:g}h to close) | "
        f"game {POLL_GAME}s | futures {POLL_FUTURES}s")
    log("NO depth, NO oddsapi, NO settlement, NO mapping. Kill before Sunday.")
    async with httpx.AsyncClient(timeout=config.HTTP_TIMEOUT,
                                 headers={"User-Agent": config.USER_AGENT},
                                 follow_redirects=True) as http:
        clients = _clients(http)
        if args.discover:
            for c in clients:
                await _discover_safe(c)
            report()
            return
        tasks = [asyncio.create_task(worker(c)) for c in clients]
        if deadline:
            async def stopper():
                while not _stop.is_set() and time.time() < deadline:
                    await asyncio.sleep(1)
                _stop.set()
            tasks.append(asyncio.create_task(stopper()))
        await asyncio.gather(*tasks)
    report()


def _sig(*_):
    log("shutdown requested")
    _stop.set()


if __name__ == "__main__":
    signal.signal(signal.SIGINT, _sig)
    try:
        signal.signal(signal.SIGTERM, _sig)
    except (AttributeError, ValueError):
        pass
    main()
