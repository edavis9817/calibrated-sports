"""c-20 - is the book's over bias tradeable on the exchange?

    python -m research.over_bias_exchange --cache C --trades T --fetch    # free prints, ~3/s
    python -m research.over_bias_exchange --cache C --trades T --json-out F

PRE-REGISTRATION: docs/C20-over-bias-on-the-exchange-preregistration.md,
committed at fefa959 BEFORE this file existed. This implements it; it does not
extend it.

WHAT IS READ, AND HOW
- Kalshi quotes, depth, the market->outcome map and game kickoffs/scores come
  from c-19's scratch cache (`--cache`, c-19 used D:/temp/c19),
  opened read-only. Week 2's live quotes began pruning from the store on
  2026-09-29, so the cache is the population, not a convenience.
- Settlement facts come from `market_log.db`, `mode=ro`, through exactly the
  call c-19 Step 4 makes.
- Trade prints come from Kalshi `/markets/trades` (public, unauthenticated,
  free), fetched by `--fetch` into a SCRATCH store (`--trades`; c-20 used
  D:/temp/c20). Every page is gzipped to `<trades dir>/raw/`
  before a field is read. Nothing here writes `market_log.db`, `trades_m01.db`
  or the production raw archive.

LICENCE: everything printed or written by `--json-out` is an aggregate or an
interval. No per-market row leaves this process (`BET_LIST_RESTRICTION`).
"""
import argparse
import gzip
import json
import math
import os
import random
import sqlite3
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime
from decimal import Decimal, ROUND_FLOOR

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
from core.fees import kalshi_fee, series_multiplier, series_of  # noqa: E402

SERIES = {"KXNFLREC": "receptions", "KXNFLRSHATT": "rush_attempts"}
WEEKS = (2, 3)
ENTRY_LEAD = 180 * 60          # E = kickoff - 180 min (c-19 Step 4's entry)
CLOSE_LEAD = 10 * 60           # K = kickoff - 10 min
STALE = 600.0                  # c-19's kalshi_at rule
DEPTH_WINDOW = 60.0
TICKET = 100
TICKETS_DESCRIPTIVE = (10, 1000)
BUCKETS = ((0.0, 0.2), (0.2, 0.4), (0.4, 0.6), (0.6, 0.8), (0.8, 1.0001))
BOOT, SEED = 2000, 20
RPS = 3.0
MAX_PAGES = 20
BACKOFF = [2, 5, 15, 45]


def ro(path):
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)


# =============================================================================
# population
# =============================================================================

def population(cache):
    """Over-side REC/RSHATT markets, weeks 2-3, from the c-19 cache."""
    c = ro(cache)
    games = {r[0]: {"week": r[1], "kick": r[2], "hs": r[3], "as": r[4]}
             for r in c.execute("SELECT game_id, week, kickoff_ts, home_score, away_score FROM games")}
    mk = {}
    for mid, oid, gsis, stat, line, side, game, week, push in c.execute("SELECT * FROM kout"):
        if side != "over" or series_of(mid) not in SERIES or week not in WEEKS:
            continue
        mk[mid] = {"oid": oid, "gsis": gsis, "stat": stat, "line": line, "game": game,
                   "week": week, "push": push, "kick": games.get(game, {}).get("kick")}
    c.close()
    return games, mk


# =============================================================================
# --fetch: the print tape, into a scratch store
# =============================================================================

TSCHEMA = """
CREATE TABLE IF NOT EXISTS trades (trade_id TEXT PRIMARY KEY, market_id TEXT, ts REAL,
    yes_price REAL, no_price REAL, size REAL, taker_side TEXT);
CREATE INDEX IF NOT EXISTS ix_tr ON trades(market_id, ts);
CREATE TABLE IF NOT EXISTS fetch (market_id TEXT PRIMARY KEY, lo REAL, hi REAL, pages INTEGER,
    n INTEGER, status INTEGER, note TEXT, fetched_ts REAL);
"""


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _ts(v):
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def fetch(cache, trades):
    import httpx
    games, mk = population(cache)
    os.makedirs(os.path.join(os.path.dirname(trades), "raw"), exist_ok=True)
    out = sqlite3.connect(trades)
    out.executescript(TSCHEMA)
    done = {r[0] for r in out.execute("SELECT market_id FROM fetch WHERE status = 200")}
    todo = sorted(m for m, k in mk.items()
                  if k["kick"] is not None and m not in done)
    print(f"population {len(mk):,}; already fetched {len(done):,}; to fetch {len(todo):,}", flush=True)
    client = httpx.Client(timeout=30)
    last = 0.0
    n429 = 0
    t0 = time.time()
    for i, mid in enumerate(todo):
        kick = mk[mid]["kick"]
        lo, hi = kick - ENTRY_LEAD - 60, kick
        win_hi, pages, n, code, note, rounds = hi, 0, 0, 200, "", 0
        while rounds < 10:
            rounds += 1
            cursor, capped, earliest = None, True, None
            for page in range(MAX_PAGES):
                params = {"ticker": mid, "limit": 1000, "min_ts": int(lo), "max_ts": int(win_hi)}
                if cursor:
                    params["cursor"] = cursor
                for attempt, wait in enumerate([0] + BACKOFF):
                    if wait:
                        time.sleep(wait)
                    dt = time.time() - last
                    if dt < 1.0 / RPS:
                        time.sleep(1.0 / RPS - dt)
                    last = time.time()
                    r = client.get(f"{config.KALSHI_BASE}/markets/trades", params=params)
                    if r.status_code != 429:
                        break
                    n429 += 1
                    note = f"429 x{attempt + 1}"
                code = r.status_code
                if code != 200:
                    note = note or f"http {code}"
                    capped = False
                    break
                raw = r.content
                path = os.path.join(os.path.dirname(trades), "raw",
                                    f"{mid}.{rounds}.{page}.json.gz")
                with gzip.open(path, "wb") as f:     # RAW FIRST
                    f.write(raw)
                d = json.loads(raw)
                pages += 1
                tr = d.get("trades") or []
                rows = []
                for t in tr:
                    ts = _ts(t.get("created_time"))
                    if ts is None or not t.get("trade_id"):
                        continue
                    earliest = ts if earliest is None else min(earliest, ts)
                    rows.append((t["trade_id"], t.get("ticker") or mid, ts,
                                 _f(t.get("yes_price_dollars")), _f(t.get("no_price_dollars")),
                                 _f(t.get("count_fp")), t.get("taker_side")))
                out.executemany("INSERT OR IGNORE INTO trades VALUES (?,?,?,?,?,?,?)", rows)
                n += len(rows)
                cursor = d.get("cursor") or ""
                if not cursor or not tr:
                    capped = False
                    break
            if code != 200 or not capped or earliest is None or earliest <= lo or earliest >= win_hi:
                break
            win_hi = earliest
        if capped and code == 200:
            note = "STILL hit MAX_PAGES"
        out.execute("INSERT OR REPLACE INTO fetch VALUES (?,?,?,?,?,?,?,?)",
                    (mid, lo, hi, pages, n, code, note, time.time()))
        out.commit()
        if (i + 1) % 200 == 0:
            print(f"  {i + 1:,}/{len(todo):,}  {time.time() - t0:.0f}s  429s {n429}", flush=True)
    st = out.execute("SELECT status, count(*), sum(n), sum(note LIKE '%MAX_PAGES%') "
                     "FROM fetch GROUP BY status").fetchall()
    print(f"fetch done in {time.time() - t0:.0f}s; 429s {n429}; by status {st}", flush=True)
    out.close()


# =============================================================================
# pure pieces
# =============================================================================

def last_quote(ts_list, qs, t):
    """Last (ts, bid, ask) at or before t, or None."""
    import bisect
    i = bisect.bisect_right(ts_list, t) - 1
    return None if i < 0 else (ts_list[i],) + tuple(qs[i])


def two_sided(q, t):
    """c-19's kalshi_at rule: two-sided and no more than STALE old at t."""
    if q is None:
        return False
    ts, bid, ask = q
    return bid is not None and ask is not None and 0 < bid < ask < 1 and t - ts <= STALE


def floor_cent(x):
    return float((Decimal(str(round(x, 6))) * 100).to_integral_value(rounding=ROUND_FLOOR) / 100)


def net_pp(under_won, price, contracts, side, market_id):
    """Per-contract net in pp for a NO (under) buy at `price`. The fee is on the
    WHOLE ORDER of `contracts`, rounded up once, then spread - never fee(p, 1) x C."""
    maker_m, taker_m = series_multiplier(market_id)
    m = maker_m if side == "maker" else taker_m
    fee = kalshi_fee(price, contracts, side, multiplier=m)
    return 100.0 * ((1.0 if under_won else 0.0) - price) - 100.0 * float(fee) / contracts


def fee_pp(price, contracts, side, market_id):
    maker_m, taker_m = series_multiplier(market_id)
    m = maker_m if side == "maker" else taker_m
    return 100.0 * float(kalshi_fee(price, contracts, side, multiplier=m)) / contracts


def nearest_depth(depth, t):
    """{side: (ts, touch_price, touch_size)} from the snapshot nearest t within DEPTH_WINDOW."""
    best = {}
    for ts, side, tp, tsz in depth:
        d = abs(ts - t)
        if d <= DEPTH_WINDOW and (side not in best or d < abs(best[side][0] - t)):
            best[side] = (ts, tp, tsz)
    return best


def boot_games(rows, stat, draws=BOOT, seed=SEED):
    """Game-block percentile bootstrap -> (est, lo, hi, n_games).
    `stat(list_of_rows) -> float | None`. Each call draws independently."""
    by = defaultdict(list)
    for r in rows:
        by[r["game"]].append(r)
    games = sorted(by)
    est = stat(rows)
    if est is None or len(games) < 2:
        return est, None, None, len(games)
    rng = random.Random(seed)
    vals = []
    for _ in range(draws):
        pick = []
        for _g in range(len(games)):
            pick.extend(by[games[rng.randrange(len(games))]])
        v = stat(pick)
        if v is not None:
            vals.append(v)
    if not vals:
        return est, None, None, len(games)
    vals.sort()
    lo = vals[int(0.025 * (len(vals) - 1))]
    hi = vals[int(math.ceil(0.975 * (len(vals) - 1)))]
    return est, lo, hi, len(games)


def _mean(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


def s_bias(rows):
    """Realised over rate minus mean Kalshi YES mid, pp."""
    if not rows:
        return None
    return 100.0 * (sum(r["y"] for r in rows) - sum(r["m"] for r in rows)) / len(rows)


def s_fill(arm):
    def f(rows):
        rs = [r for r in rows if arm in r]
        return 100.0 * sum(r[arm]["filled"] for r in rs) / len(rs) if rs else None
    return f


def s_cond(arm):
    def f(rows):
        return _mean([r[arm]["net"] for r in rows if arm in r and r[arm]["filled"]])
    return f


def s_unfilled(arm):
    def f(rows):
        return _mean([r[arm]["net"] for r in rows if arm in r and not r[arm]["filled"]])
    return f


def s_gap(arm):
    """Filled minus unfilled, ONE quantity over shared draws (brief 018)."""
    def f(rows):
        a, b = s_cond(arm)(rows), s_unfilled(arm)(rows)
        return None if a is None or b is None else a - b
    return f


def s_per_order(arm):
    def f(rows):
        return _mean([(r[arm]["net"] if r[arm]["filled"] else 0.0) for r in rows if arm in r])
    return f


def s_drift(arm):
    def f(rows):
        return _mean([r[arm].get("drift") for r in rows if arm in r and r[arm]["filled"]])
    return f


def s_taker(rows):
    return _mean([r["T"]["net"] for r in rows if "T" in r])


def iv(t, d=2):
    est, lo, hi, g = t
    if est is None:
        return "n/a"
    if lo is None:
        return f"{est:+.{d}f} [no interval, {g} games]"
    tag = "" if g >= 5 else " (fewer than 5 games - not read)"
    return f"{est:+.{d}f} [{lo:+.{d}f}, {hi:+.{d}f}]{tag}"


def verdict(t):
    """The pre-registered rule, applied to a fill-conditional (est, lo, hi, games)."""
    est, lo, _hi, _g = t
    if est is None:
        return "not estimable"
    if est <= 0:
        return "RETIRED"
    if lo is not None and lo > 0:
        return "SUPPORTED (to replication on weeks 4-6)"
    return "OPEN (not a finding; retired if the week 4-6 replication is <= 0)"


# =============================================================================
# the maker simulation - research.maker's fill rule, unchanged
# =============================================================================

def maker_fill(entry, kick, price, queue, prints, contracts):
    """Walk prints in [entry, kick). A passive NO buy at `price` is filled by
    `research.maker.eligible(t, "no", price)` prints once cumulative eligible size
    reaches queue + contracts - M01's rule. The price is passed in because MM
    rests inside the touch, where `maker.passive_price` would not put it."""
    from research import maker
    window = [t for t in prints if entry <= t["ts"] < kick]
    cum, filled, fill_ts, n_el = 0.0, False, None, 0
    for t in window:
        if not maker.eligible(t, "no", price):
            continue
        n_el += 1
        cum += t["size"] or 0.0
        if not filled and cum >= queue + contracts:
            filled, fill_ts = True, t["ts"]
    return {"price": price, "queue": queue, "filled": filled, "fill_ts": fill_ts,
            "n_prints": len(window), "n_eligible": n_el}


# =============================================================================
# run
# =============================================================================

def settle_all(mk, games):
    """c-19 Step 4's settlement call, against market_log.db mode=ro."""
    from research import walkforward as wf
    from core import settlement
    con = ro(config.DB_PATH)
    snaps = wf.snap_rows(con, 2026)
    vals = {}
    for g, w, rec, car in con.execute(
            "SELECT gsis_id, week, receptions, carries FROM nfl_player_week w WHERE season=2026 "
            "AND season_type='REG' AND data_version=(SELECT MAX(data_version) FROM nfl_player_week v "
            "WHERE v.gsis_id=w.gsis_id AND v.season=w.season AND v.week=w.week "
            "AND v.season_type='REG')"):
        vals[(g, w)] = {"receptions": rec, "rush_attempts": car}
    con.close()
    census, out = Counter(), {}
    for mid, km in mk.items():
        census["markets weeks 2-3"] += 1
        g = games.get(km["game"])
        if not g or g["hs"] is None or g["kick"] is None:
            census["game without final score in the cache"] += 1
            continue
        key = (km["gsis"], km["week"])
        has = key in vals
        snap, _t, _p = snaps.get((km["gsis"], km["game"]), (None, None, None))
        res, _a, _v, _s = settlement.settle(vals[key][km["stat"]] if has else None, has, snap,
                                            km["stat"], km["line"], km["push"])
        if res not in (settlement.OVER, settlement.UNDER):
            census[f"settlement {res}"] += 1
            continue
        census["settled over/under"] += 1
        out[mid] = 1.0 if res == settlement.OVER else 0.0
    return out, census


def load_cache(cache, mids):
    """One pass over each table: `depth` has no market_id index in the cache, so a
    per-market query is a full scan of ~8M rows each time."""
    want = set(mids)
    c = ro(cache)
    kq = {m: ([], []) for m in want}
    for mid, ts, bid, ask in c.execute("SELECT market_id, ts, bid, ask FROM kq ORDER BY market_id, ts"):
        if mid in want:
            kq[mid][0].append(ts)
            kq[mid][1].append((bid, ask))
    depth = defaultdict(list)
    for mid, ts, side, tp, tsz in c.execute("SELECT market_id, ts, side, touch_price, touch_size FROM depth"):
        if mid in want:
            depth[mid].append((ts, side, tp, tsz))
    c.close()
    return kq, depth


def load_prints(trades):
    c = ro(trades)
    fetched = {m: (s, n, note) for m, s, n, note in
               c.execute("SELECT market_id, status, n, note FROM fetch")}
    pr = defaultdict(list)
    for mid, ts, yp, np_, sz, tk in c.execute(
            "SELECT market_id, ts, yes_price, no_price, size, taker_side FROM trades "
            "ORDER BY market_id, ts"):
        pr[mid].append({"ts": ts, "yes_price": yp, "no_price": np_, "size": sz, "taker_side": tk})
    c.close()
    return fetched, pr


_LOADED = {}


def loaded(cache, trades):
    """Settlement, quotes and prints are read once and shared by every ticket size."""
    if (cache, trades) not in _LOADED:
        games, mk = population(cache)
        y, census = settle_all(mk, games)
        kq, depth = load_cache(cache, list(y))
        fetched, prints = load_prints(trades)
        _LOADED[(cache, trades)] = (mk, y, census, kq, depth, fetched, prints)
    return _LOADED[(cache, trades)]


def build_rows(cache, trades, contracts=TICKET):
    mk, y, census0, kq, depth, fetched, prints = loaded(cache, trades)
    census = Counter(census0)
    rows = []
    for mid, yy in y.items():
        km = mk[mid]
        kick = km["kick"]
        E, K = kick - ENTRY_LEAD, kick - CLOSE_LEAD
        ts_list, qs = kq[mid]
        qE, qK = last_quote(ts_list, qs, E), last_quote(ts_list, qs, K)
        qEnd = last_quote(ts_list, qs, kick - 1e-6)
        r = {"game": km["game"], "stat": km["stat"], "y": yy, "week": km["week"]}
        if two_sided(qK, K):
            r["K"] = (qK[1] + qK[2]) / 2
        if not two_sided(qE, E):
            census["entry E: not two-sided or stale"] += 1
            if "K" in r:
                rows.append(r)
            continue
        census["entry E: two-sided"] += 1
        _ts, bid, ask = qE
        r["m"] = (bid + ask) / 2
        under = yy == 0.0
        d = nearest_depth(depth[mid], E)
        # T - taker at the ask: buy NO at 1 - yes_bid; needs >= C on the buy_no touch
        if "buy_no" in d and (d["buy_no"][2] or 0) >= contracts:
            p = round(1.0 - bid, 4)
            r["T"] = {"net": net_pp(under, p, contracts, "taker", mid), "price": p,
                      "fee_pp": fee_pp(p, contracts, "taker", mid)}
        else:
            census["T: buy_no touch < C or no depth within 60s"] += 1
        f = fetched.get(mid)
        if f is None or f[0] != 200 or (f[2] or "").startswith("STILL"):
            census["maker: tape missing, failed or capped"] += 1
        elif "buy_yes" not in d:
            census["maker: no depth snapshot within 60s of E"] += 1
        else:
            queue = d["buy_yes"][2] or 0.0
            r["depth_match"] = d["buy_yes"][1] is not None and abs(d["buy_yes"][1] - ask) < 1e-6
            no_touch = round(1.0 - ask, 4)
            no_mid = floor_cent(1.0 - r["m"])
            for arm, price, qa in (("MB", no_touch, queue),
                                   ("MM", no_mid, queue if no_mid <= no_touch + 1e-9 else 0.0)):
                if not (0 < price < 1):
                    continue
                sim = maker_fill(E, kick, price, qa, prints.get(mid, []), contracts)
                sim["net"] = net_pp(under, price, contracts, "maker", mid)
                sim["fee_pp"] = fee_pp(price, contracts, "maker", mid)
                if sim["filled"]:
                    qf = last_quote(ts_list, qs, sim["fill_ts"])
                    if two_sided(qf, sim["fill_ts"]) and qEnd and qEnd[1] and qEnd[2] \
                            and 0 < qEnd[1] < qEnd[2] < 1:
                        # NO mid at the last quote before kickoff minus NO mid at the fill
                        sim["drift"] = 100.0 * ((qf[1] + qf[2]) / 2 - (qEnd[1] + qEnd[2]) / 2)
                sim["same_as_MB"] = arm == "MM" and abs(price - no_touch) < 1e-9
                r[arm] = sim
        rows.append(r)
    return rows, census, fetched


def bucket_label(lo, hi):
    return f"{lo:.1f}-{min(hi, 1.0):.1f}"


def _t(x):
    return {"est": x[0], "lo": x[1], "hi": x[2]}


def run(cache, trades, json_out=None):
    lines = []

    def out(s=""):
        print(s, flush=True)
        lines.append(s)

    rows, census, fetched = build_rows(cache, trades, TICKET)
    res = {"scope": "Kalshi KXNFLREC+KXNFLRSHATT, over side, NFL 2026 weeks 2-3, pre-kickoff; "
                    "under = buy NO at E = kickoff-180m, held to settlement",
           "census": dict(census)}
    out("== census ==")
    for k, v in sorted(census.items()):
        out(f"  {k:<50} {v:>7,}")
    st = Counter(v[0] for v in fetched.values())
    capped = sum(1 for v in fetched.values() if (v[2] or "").startswith("STILL"))
    nprints = sum(v[1] or 0 for v in fetched.values())
    out(f"  tapes fetched by status {dict(st)}; still capped {capped}; prints {nprints:,}")
    res["tapes"] = {"by_status": {str(k): v for k, v in st.items()}, "capped": capped,
                    "prints": nprints}

    E = [r for r in rows if "m" in r]
    Kr = [dict(r, m=r["K"]) for r in rows if "K" in r]
    out("\n== M1 - over bias on Kalshi's own mid (realised - mid, pp; game block) ==")
    m1 = {}
    for label, rs in (("E", E), ("K", Kr)):
        t = boot_games(rs, s_bias)
        out(f"  {label} n {len(rs):>5,}  games {t[3]:>3}  mean mid {_mean([r['m'] for r in rs]):.4f}  "
            f"realised {_mean([r['y'] for r in rs]):.4f}  gap {iv(t)}")
        m1[label] = {"n": len(rs), "games": t[3], **_t(t), "mean_mid": _mean([r["m"] for r in rs]),
                     "realised": _mean([r["y"] for r in rs])}
        for stat in SERIES.values():
            ss = [r for r in rs if r["stat"] == stat]
            t2 = boot_games(ss, s_bias)
            out(f"     {stat:<14} n {len(ss):>5,}  games {t2[3]:>3}  gap {iv(t2)}  (descriptive)")
            m1[f"{label} {stat}"] = {"n": len(ss), "games": t2[3], **_t(t2)}
    res["M1"] = m1

    out(f"\n== trade arms, under side, {TICKET} contracts, placed at E, cancelled at kickoff ==")
    arms = {}
    tr = [r for r in E if "T" in r]
    tT = boot_games(tr, s_taker)
    out(f"  T  taker at the ask   n {len(tr):>5,}  games {tT[3]}  net {iv(tT)}  "
        f"mean fee {_mean([r['T']['fee_pp'] for r in tr]):.2f}pp  -> {verdict(tT)}")
    arms["T"] = {"n": len(tr), "games": tT[3], "net": _t(tT), "verdict": verdict(tT),
                 "fee_pp": _mean([r["T"]["fee_pp"] for r in tr])}
    for arm, name in (("MB", "maker at the bid"), ("MM", "maker at the mid")):
        rs = [r for r in E if arm in r]
        nf = sum(r[arm]["filled"] for r in rs)
        fr, cd, gp = boot_games(rs, s_fill(arm)), boot_games(rs, s_cond(arm)), boot_games(rs, s_gap(arm))
        po, dr = boot_games(rs, s_per_order(arm)), boot_games(rs, s_drift(arm))
        uf = boot_games(rs, s_unfilled(arm))
        fgames = len({r["game"] for r in rs if r[arm]["filled"]})
        out(f"  {arm} {name:<18} placed {len(rs):,}  filled {nf:,}  games {fr[3]} "
            f"(with a fill {fgames})")
        out(f"     fill rate %            {iv(fr)}")
        out(f"     CONDITIONAL net pp     {iv(cd)}   -> {verdict(cd)}")
        out(f"     unfilled (hypoth.) pp  {iv(uf)}")
        out(f"     selection gap pp       {iv(gp)}")
        out(f"     per order placed pp    {iv(po)}")
        out(f"     post-fill drift pp     {iv(dr)}  (NO mid at kickoff - at fill; negative = moved against)")
        extra = {}
        if arm == "MM":
            extra["same_as_MB"] = sum(r[arm]["same_as_MB"] for r in rs)
            out(f"     MM rows identical to MB (1c spread): {extra['same_as_MB']:,} of {len(rs):,}")
        else:
            extra["depth_touch_matches_quote"] = sum(bool(r.get("depth_match")) for r in rs)
            out(f"     depth touch price == quoted ask on {extra['depth_touch_matches_quote']:,} of {len(rs):,}")
            qs_ = sorted(r[arm]["queue"] for r in rs)
            extra["queue_p50"] = qs_[len(qs_) // 2] if qs_ else None
            out(f"     queue ahead, median contracts: {extra['queue_p50']}")
        weeks = len({r["week"] for r in rs}) or 1
        usd = (po[0] or 0) / 100.0 * TICKET * len(rs) / weeks
        out(f"     materiality: {len(rs) / weeks:,.0f} orders/week x per-order EV x {TICKET} -> ${usd:,.0f}/week")
        arms[arm] = {"placed": len(rs), "filled": nf, "games": fr[3], "games_with_fill": fgames,
                     "fill_rate_pct": _t(fr), "conditional": _t(cd), "verdict": verdict(cd),
                     "unfilled_hypothetical": _t(uf), "selection_gap": _t(gp),
                     "per_order": _t(po), "post_fill_drift": _t(dr), "usd_per_week": usd, **extra}
    res["arms"] = arms

    out("\n== by YES-mid bucket at E (DESCRIPTIVE - not tests) ==")
    bk = {}
    for lo, hi in BUCKETS:
        lab = bucket_label(lo, hi)
        rs = [r for r in E if lo <= r["m"] < hi]
        t = boot_games(rs, s_bias)
        tt = boot_games([r for r in rs if "T" in r], s_taker)
        tfee = _mean([r["T"]["fee_pp"] for r in rs if "T" in r])
        b = {"n": len(rs), "games": t[3], "M1": _t(t), "T": _t(tt), "T_fee_pp": tfee}
        out(f"  {lab}  n {len(rs):>5,}  M1 {iv(t)}  T net {iv(tt)}  T fee "
            f"{tfee if tfee is not None else float('nan'):.2f}")
        for arm in ("MB", "MM"):
            ra = [r for r in rs if arm in r]
            fr, cd = boot_games(ra, s_fill(arm)), boot_games(ra, s_cond(arm))
            fg = len({r["game"] for r in ra if r[arm]["filled"]})
            b[arm] = {"placed": len(ra), "filled": sum(r[arm]["filled"] for r in ra),
                      "games_with_fill": fg, "fill_rate_pct": _t(fr), "conditional": _t(cd)}
            out(f"      {arm} placed {len(ra):>5,} filled {b[arm]['filled']:>4,} in {fg:>2} games  "
                f"fill {iv(fr)}  cond {iv(cd)}")
        bk[lab] = b
    res["buckets"] = bk

    out("\n== POST-HOC, not registered: is the maker subset representative? ==")
    # Depth was captured for these series in only some games, so the maker arms run on
    # a subset. M1 on that subset says whether it differs from the whole population.
    mk_, _y, _c, _kq, depth_, _f, _p = loaded(cache, trades)
    with_depth = {m for m, d in depth_.items() if any(s_ == "buy_yes" for _t, s_, _a, _b in d)}
    # Depth is loaded for SETTLED markets only, so coverage is counted over them.
    cov = {"settled_markets": len(_y), "settled_never_depth": len(set(_y) - with_depth),
           "settled_games": len({mk_[m]["game"] for m in _y}),
           "settled_games_with_depth": len({mk_[m]["game"] for m in with_depth if m in _y})}
    for w in (60, 300, 900, 3600):
        cov[f"settled_within_{w}s_of_E"] = sum(
            1 for m in with_depth if m in _y and any(
                s_ == "buy_yes" and abs(t_ - (mk_[m]["kick"] - ENTRY_LEAD)) <= w
                for t_, s_, _a, _b in depth_[m]))
    out(f"  depth coverage {cov}")
    res["posthoc_depth_coverage"] = cov
    sub = [r for r in E if "MB" in r]
    t = boot_games(sub, s_bias)
    out(f"  M1 at E on the maker subset  n {len(sub):,}  games {t[3]}  gap {iv(t)}")
    res["posthoc_M1_maker_subset"] = {"n": len(sub), "games": t[3], **_t(t)}
    hw = arms["MB"]["conditional"]
    if hw["lo"] is not None:
        out(f"  MB conditional interval half-width {(hw['hi'] - hw['lo']) / 2:.2f}pp")
        res["posthoc_MB_halfwidth_pp"] = (hw["hi"] - hw["lo"]) / 2

    out("\n== by stat (DESCRIPTIVE) ==")
    bs = {}
    for stat in SERIES.values():
        rs = [r for r in E if r["stat"] == stat]
        tt = boot_games([r for r in rs if "T" in r], s_taker)
        d = {"n": len(rs), "T": _t(tt)}
        msg = f"  {stat:<14} n {len(rs):,}  T {iv(tt)}"
        for arm in ("MB", "MM"):
            ra = [r for r in rs if arm in r]
            cd = boot_games(ra, s_cond(arm))
            d[arm] = {"placed": len(ra), "filled": sum(r[arm]["filled"] for r in ra),
                      "conditional": _t(cd)}
            msg += f"  {arm} {d[arm]['filled']}/{d[arm]['placed']} cond {iv(cd)}"
        bs[stat] = d
        out(msg)
    res["by_stat"] = bs

    out("\n== ticket sizes (DESCRIPTIVE) ==")
    tk = {}
    for c in TICKETS_DESCRIPTIVE:
        rr, _c, _f = build_rows(cache, trades, c)
        rr = [r for r in rr if "m" in r]
        tt = boot_games([r for r in rr if "T" in r], s_taker)
        d = {"T_n": sum("T" in r for r in rr), "T": _t(tt)}
        msg = f"  C={c:<5} T n {d['T_n']:,} net {iv(tt)}"
        for arm in ("MB", "MM"):
            ra = [r for r in rr if arm in r]
            cd = boot_games(ra, s_cond(arm))
            d[arm] = {"placed": len(ra), "filled": sum(r[arm]["filled"] for r in ra),
                      "conditional": _t(cd)}
            msg += f"  {arm} {d[arm]['filled']}/{d[arm]['placed']} cond {iv(cd)}"
        tk[str(c)] = d
        out(msg)
    res["tickets"] = tk

    res["generated_ts"] = time.time()
    if json_out:
        with open(json_out, "w") as f:
            json.dump(res, f, indent=1, default=str)
        with open(os.path.splitext(json_out)[0] + ".log", "w") as f:
            f.write("\n".join(lines) + "\n")
    return res


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    # No defaults: scratch paths are named by the caller, never written here as literals
    # (tests/test_storage_paths.py).
    ap.add_argument("--cache", required=True, help="c-19's scratch cache (venue_spread --extract)")
    ap.add_argument("--trades", required=True, help="scratch print store, created by --fetch")
    ap.add_argument("--fetch", action="store_true")
    ap.add_argument("--json-out")
    a = ap.parse_args()
    if a.fetch:
        return fetch(a.cache, a.trades)
    return run(a.cache, a.trades, a.json_out)


if __name__ == "__main__":
    main()
