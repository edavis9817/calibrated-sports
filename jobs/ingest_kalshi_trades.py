"""BRIEF M01 - Kalshi trade prints for the week-1 markets.

    python jobs/ingest_kalshi_trades.py --status
    python jobs/ingest_kalshi_trades.py --week 1
    python jobs/ingest_kalshi_trades.py --from-archive     # re-parse, 0 requests

`/markets/trades` is FREE and UNAUTHENTICATED, like `/candlesticks`. It is the
only record of what actually traded: quotes say what was offered and depth says
what was resting, and neither says whether anyone crossed.

RATE LIMITS ARE PER ENDPOINT ON KALSHI and this one is undocumented. The
candlesticks endpoint 429s after ~5 rapid calls while batched orderbooks
sustained 13.6/s, so nothing about one generalises to another. This job starts
deliberately slow, backs off blind on a 429 (Kalshi sends no Retry-After), and
keeps going rather than dying - a partial print history is still usable and is
labelled as partial.

IT SHARES A RATE LIMIT WITH THE RUNNING LOGGER. That is the real constraint,
not the wall clock. The logger is mid-season and its Kalshi budget matters more
than this job finishing quickly.

RAW FIRST. Every page is archived verbatim before a field is read, so a parser
fix re-runs from disk at zero requests - `--from-archive`.

WHAT A PRINT MEANS, which is the part worth getting right
`taker_side` is the side the TAKER bought. The maker took the other one:

    taker_side == "yes"  ->  a taker BOUGHT yes, so a resting NO bid was hit
    taker_side == "no"   ->  a taker BOUGHT no,  so a resting YES bid was hit

So a passive order to buy YES is filled by `taker_side == "no"` prints, and a
passive order to buy NO is filled by `taker_side == "yes"` prints. Getting this
backwards inverts every fill in the study and would still produce a plausible
fill rate.
"""
import argparse
import gzip
import json
import os
import sqlite3
import sys
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx

import config

ET = ZoneInfo("America/New_York")
VENUE = "kalshi_trades"

# WRITES TO ITS OWN DATABASE, NOT THE LOGGER'S. Two reasons, both learned here:
#
#   1. The logger owns `market_log.db` and is mid-season. A bulk ingest holding
#      write transactions against it is contention with the one process that
#      must not stall.
#   2. `store.archive_raw` opens its OWN connection to `config.DB_PATH`. With an
#      uncommitted INSERT open on another connection to the same file, that is a
#      self-deadlock: the first market archives fine, the second blocks on our
#      own transaction until busy_timeout. The symptom is a job that burns 0.8s
#      of CPU in two minutes and prints nothing.
#
# So `config.DB_PATH` is repointed in main() before `store` is imported - the
# cfb_probe pattern - and the live database is opened READ-ONLY, for targets.
# STORAGE LOCATION COMES FROM CONFIG, NEVER FROM A PATH LITERAL.
LIVE_DB = config.DB_PATH
M01_DB = os.path.join(os.path.dirname(os.path.abspath(config.DB_PATH)),
                      "trades_m01.db")

store = None            # bound in main(), after config.DB_PATH is repointed

# Deliberately slow. See the module docstring: this endpoint's limit is
# undocumented and the running logger shares the venue budget.
RPS = 4.0
MAX_PAGES = 20          # a week-1 prop with >20k prints does not exist
BACKOFF = [2, 5, 15, 45]

SCHEMA = """
CREATE TABLE IF NOT EXISTS market_trades (
    trade_id    TEXT PRIMARY KEY,
    venue       TEXT,
    market_id   TEXT,
    ts          REAL,
    yes_price   REAL,
    no_price    REAL,
    size        REAL,
    taker_side  TEXT,
    taker_book_side TEXT,
    is_block    INTEGER,
    ingest_ts   REAL
);
CREATE INDEX IF NOT EXISTS ix_trades_market_ts ON market_trades(venue, market_id, ts);

CREATE TABLE IF NOT EXISTS market_trades_fetch (
    venue     TEXT,
    market_id TEXT,
    fetched_ts REAL,
    pages     INTEGER,
    n_trades  INTEGER,
    status    INTEGER,
    note      TEXT,
    PRIMARY KEY (venue, market_id)
);
"""


def live_ro():
    """The logger's database, read-only. Never written by this job."""
    return sqlite3.connect(f"file:{LIVE_DB}?mode=ro", uri=True)


def conn():
    c = sqlite3.connect(M01_DB, timeout=60)
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA busy_timeout=60000")
    c.executescript(SCHEMA)
    return c


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


def targets(season, week, model_version=None):
    """The markets the 935 predictions point at. Exactly the M01 population.
    Read from the LIVE database, read-only."""
    from core import version_resolve
    from models import baseline
    mv, note = version_resolve.resolve(
        season, week, baseline.MODEL_VERSION, override=model_version,
        db_path=LIVE_DB)
    if note:
        print(f"  {note}")
    c = live_ro()
    return [r[0] for r in c.execute(
        "SELECT DISTINCT mo.market_id FROM predictions p "
        "JOIN outcomes o USING (outcome_id) "
        "JOIN market_outcome mo ON mo.outcome_id = p.outcome_id "
        "WHERE mo.venue='kalshi' AND o.season=? AND o.week=? "
        "AND p.model_version=? ORDER BY 1", (season, week, mv))]


PROP_SERIES = ("KXNFLREC", "KXNFLRSHATT")


def control_frame(season, week, model_version=None, series=PROP_SERIES):
    """BRIEF 018 ITEM 1 - every prop market in the SAME kalshi events as the
    predictions, whether or not a prediction touched it.

    The events come from the predictions only to fix the WINDOW - same games,
    same slate, same afternoon - so that the control differs from the
    model-selected population in market choice and nothing else. Which markets
    are returned inside those events involves no model output at all.
    """
    from core import version_resolve
    from models import baseline
    mv, note = version_resolve.resolve(
        season, week, baseline.MODEL_VERSION, override=model_version,
        db_path=LIVE_DB)
    if note:
        print(f"  {note}")
    c = live_ro()
    pred = [r[0] for r in c.execute(
        "SELECT mo.market_id FROM predictions p JOIN outcomes o USING (outcome_id) "
        "JOIN market_outcome mo ON mo.outcome_id = p.outcome_id "
        "WHERE mo.venue='kalshi' AND o.season=? AND o.week=? AND p.model_version=?",
        (season, week, mv))]
    events = {m.split("-")[1] for m in pred if len(m.split("-")) > 1}
    # BRIEF 019 ITEM 3: any series, not just the two props. The events still
    # come from the predictions ONLY to fix the window - same 14 games - so a
    # per-series capture comparison differs by series and nothing else.
    allm = []
    for s in series:
        allm += [m for (m,) in c.execute(
            "SELECT market_id FROM markets WHERE venue='kalshi' "
            "AND market_id >= ? AND market_id < ?", (s + "-", s + "."))]
    return sorted(m for m in allm
                  if len(m.split("-")) > 1 and m.split("-")[1] in events)


def fetch_one(client, ticker, window=None):
    """Every print for one ticker. Returns (pages, payloads, status, note).

    `window=(min_ts, max_ts)` bounds the tape. The endpoint pages NEWEST FIRST,
    so an unbounded fetch of a game-level market spends all MAX_PAGES on
    in-game prints and never reaches the entry->kickoff window a maker
    simulation reads - 22 of 28 KXNFLGAME tapes did exactly that (brief 019).
    """
    payloads, cursor, code, note = [], None, 200, ""
    for page in range(MAX_PAGES):
        params = {"ticker": ticker, "limit": 1000}
        if window:
            params["min_ts"], params["max_ts"] = int(window[0]), int(window[1])
        if cursor:
            params["cursor"] = cursor
        for attempt, wait in enumerate([0] + BACKOFF):
            if wait:
                time.sleep(wait)
            r = client.get(f"{config.KALSHI_BASE}/markets/trades", params=params)
            if r.status_code != 429:
                break
            note = f"429 x{attempt + 1}"
        code = r.status_code
        if code != 200:
            note = note or f"http {code}"
            break
        d = r.json()
        payloads.append(d)
        trades = d.get("trades") or []
        cursor = d.get("cursor") or ""
        if not cursor or not trades:
            break
    else:
        note = "hit MAX_PAGES"
    return len(payloads), payloads, code, note


def parse(c, ticker, payloads):
    rows, now = [], time.time()
    for d in payloads:
        for t in d.get("trades") or []:
            ts = _ts(t.get("created_time"))
            if ts is None or not t.get("trade_id"):
                continue
            rows.append((
                t["trade_id"], "kalshi", t.get("ticker") or ticker, ts,
                _f(t.get("yes_price_dollars")), _f(t.get("no_price_dollars")),
                _f(t.get("count_fp")), t.get("taker_side"),
                t.get("taker_book_side"), int(bool(t.get("is_block_trade"))),
                now))
    if rows:
        c.executemany(
            "INSERT INTO market_trades VALUES (?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(trade_id) DO NOTHING", rows)
    return len(rows)


def run(season, week, limit=None, frame=False, series=None):
    c = conn()
    tick = (control_frame(season, week, series=series or PROP_SERIES)
            if frame else targets(season, week))
    if limit:
        tick = tick[:limit]
    print(f"{len(tick)} markets, {RPS} req/s -> {M01_DB}")
    print(f"  archiving to {config.RAW_DIR}/{VENUE}")
    done = set(r[0] for r in c.execute(
        "SELECT market_id FROM market_trades_fetch WHERE venue='kalshi' "
        "AND status=200"))
    todo = [t for t in tick if t not in done]
    print(f"  {len(done)} already fetched, {len(todo)} to go")
    interval = 1.0 / RPS
    nxt = 0.0
    tot_tr = n429 = fails = 0
    with httpx.Client(timeout=45, headers={"User-Agent": config.USER_AGENT},
                      follow_redirects=True) as client:
        for i, ticker in enumerate(todo, 1):
            now = time.monotonic()
            if now < nxt:
                time.sleep(nxt - now)
            nxt = time.monotonic() + interval
            try:
                pages, payloads, code, note = fetch_one(client, ticker)
            except httpx.HTTPError as e:
                pages, payloads, code, note = 0, [], 0, f"{type(e).__name__}"
            # ARCHIVE BEFORE PARSING - and with NO transaction open, because
            # archive_raw uses its own connection to the same file.
            for d in payloads:
                store.archive_raw(VENUE, f"trades:{ticker}", d)
            n = parse(c, ticker, payloads) if payloads else 0
            tot_tr += n
            n429 += "429" in note
            fails += code != 200
            c.execute(
                "INSERT INTO market_trades_fetch VALUES (?,?,?,?,?,?,?) "
                "ON CONFLICT(venue, market_id) DO UPDATE SET "
                "fetched_ts=excluded.fetched_ts, pages=excluded.pages, "
                "n_trades=excluded.n_trades, status=excluded.status, "
                "note=excluded.note",
                ("kalshi", ticker, time.time(), pages, n, code, note))
            # Commit EVERY market. An open transaction here blocks the next
            # iteration's archive_raw, which is a different connection to this
            # same file. 935 small commits cost far less than one deadlock.
            c.commit()
            if i % 50 == 0 or i == len(todo):
                print(f"    {i}/{len(todo)}  trades={tot_tr:,}  429s={n429}  "
                      f"failed={fails}")
    c.commit()
    status(c)


def refetch_capped(c, windows_path):
    """Re-fetch every market whose tape hit MAX_PAGES, bounded to its own
    entry->kickoff window. Additive: prints already stored stay (ON CONFLICT
    DO NOTHING), and the simulation filters to the window anyway. A window that
    STILL hits the cap is recorded as such, not silently accepted."""
    with open(windows_path, encoding="utf-8") as f:
        windows = json.load(f)
    capped = [m for (m,) in c.execute(
        "SELECT market_id FROM market_trades_fetch WHERE venue='kalshi' "
        "AND note LIKE '%hit MAX_PAGES%'")]
    print(f"  {len(capped)} capped tapes to re-fetch inside entry->kickoff")
    with httpx.Client(timeout=45, headers={"User-Agent": config.USER_AGENT},
                      follow_redirects=True) as client:
        for ticker in capped:
            w = windows.get(ticker.split("-")[1])
            if not w:
                print(f"    {ticker}: NO WINDOW for its event - left capped")
                continue
            pages, n, code, note = walk_window(
                lambda win: _fetch_archived(c, client, ticker, win), w)
            c.execute(
                "UPDATE market_trades_fetch SET fetched_ts=?, pages=?, n_trades=?, "
                "status=?, note=? WHERE venue='kalshi' AND market_id=?",
                (time.time(), pages, n, code, note, ticker))
            c.commit()
            print(f"    {ticker:<34} {n:>6} prints in window  {note}")


def _fetch_archived(c, client, ticker, win):
    time.sleep(1.0 / RPS)
    pages, payloads, code, note = fetch_one(client, ticker, window=win)
    for d in payloads:
        store.archive_raw(VENUE, f"trades:{ticker}:window", d)
    n = parse(c, ticker, payloads) if payloads else 0
    tss = [_ts(t.get("created_time")) for d in payloads for t in d.get("trades") or []]
    tss = [t for t in tss if t is not None]
    return pages, n, code, note, (min(tss) if tss else None)


MAX_ROUNDS = 10


def walk_window(fetch, window):
    """Page a (min_ts, max_ts) window to its START. The tape is newest first,
    so a pass that hits MAX_PAGES has the kickoff end and is missing the entry
    end - which is where a resting order would fill first. Each capped pass
    moves max_ts back to the earliest print it returned (inclusive, since
    several prints share a second; ON CONFLICT drops the overlap) and goes
    again. `fetch(win) -> (pages, n, status, note, earliest_ts)`."""
    lo, hi = window
    pages = n = rounds = 0
    code, note = 200, ""
    while rounds < MAX_ROUNDS:
        rounds += 1
        p, k, code, note, earliest = fetch((lo, hi))
        pages += p
        n += k
        if code != 200 or note != "hit MAX_PAGES" or earliest is None \
                or earliest <= lo or earliest >= hi:
            break
        hi = earliest
    capped = code == 200 and note == "hit MAX_PAGES"
    return pages, n, code, (f"window {int(lo)}-{int(window[1])} rounds={rounds}"
                            + (" STILL hit MAX_PAGES" if capped else
                               f" {note}" if note else ""))


def from_archive(c):
    root = os.path.join(config.RAW_DIR, VENUE)
    if not os.path.isdir(root):
        raise SystemExit(f"no archive at {root}")
    n = files = bad = 0
    for day in sorted(os.listdir(root)):
        d = os.path.join(root, day)
        if not os.path.isdir(d):
            continue
        for fn in sorted(os.listdir(d)):
            if not fn.endswith(".jsonl.gz"):
                continue
            files += 1
            try:
                with gzip.open(os.path.join(d, fn), "rt", encoding="utf-8") as f:
                    for line in f:
                        rec = json.loads(line)
                        ep = rec.get("endpoint", "")
                        tk = ep.split(":", 1)[1] if ":" in ep else ""
                        n += parse(c, tk, [rec.get("payload") or {}])
            except (OSError, EOFError, json.JSONDecodeError) as e:
                bad += 1
                print(f"  UNREADABLE {day}/{fn}: {type(e).__name__}")
    c.commit()
    print(f"  re-parsed {n:,} prints from {files} shards, {bad} unreadable, 0 requests")


def status(c):
    f = c.execute("SELECT COUNT(*), SUM(status=200), SUM(n_trades), "
                  "SUM(n_trades=0) FROM market_trades_fetch "
                  "WHERE venue='kalshi'").fetchone()
    print(f"\n  markets fetched   {f[0] or 0}  ({f[1] or 0} ok)")
    print(f"  prints stored     {c.execute('SELECT COUNT(*) FROM market_trades').fetchone()[0]:,}")
    print(f"  markets with ZERO prints {f[3] or 0}")
    bad = c.execute("SELECT market_id, status, note FROM market_trades_fetch "
                    "WHERE venue='kalshi' AND status<>200 LIMIT 5").fetchall()
    for b in bad:
        print(f"    FAILED {b[0]} status={b[1]} {b[2]}")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--season", type=int, default=2026)
    ap.add_argument("--week", type=int, default=1)
    ap.add_argument("--limit", type=int)
    ap.add_argument("--frame", action="store_true",
                    help="brief 018: every prop market in the predictions' "
                         "events, not just the predicted ones")
    ap.add_argument("--series", help="brief 019: comma-separated series for --frame, "
                    "e.g. KXNFLSPREAD,KXNFLTOTAL,KXNFLGAME")
    ap.add_argument("--refetch-capped", metavar="WINDOWS_JSON",
                    help="brief 019: re-fetch tapes that hit MAX_PAGES inside "
                         "{event: [entry_ts, kickoff_ts]}")
    ap.add_argument("--from-archive", action="store_true")
    ap.add_argument("--status", action="store_true")
    a = ap.parse_args()

    # REPOINT BEFORE store IS TOUCHED.
    config.DB_PATH = M01_DB
    global store
    import store as _store
    store = _store

    c = conn()
    if a.status:
        status(c)
    elif a.refetch_capped:
        refetch_capped(c, a.refetch_capped)
        status(c)
    elif a.from_archive:
        from_archive(c)
        status(c)
    else:
        run(a.season, a.week, a.limit, a.frame,
            tuple(x.strip() for x in a.series.split(",")) if a.series else None)


if __name__ == "__main__":
    main()
