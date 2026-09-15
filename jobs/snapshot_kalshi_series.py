"""BRIEF 019 ITEM 0 - one snapshot of every NFL series Kalshi lists.

    python jobs/snapshot_kalshi_series.py            # fetch, archive, parse
    python jobs/snapshot_kalshi_series.py --from-archive
    python jobs/snapshot_kalshi_series.py --status

WHY A SNAPSHOT AND NOT HISTORY. The logger polls an allowlist of 18 series
(`config.KALSHI_SERIES_ALLOW`) because all ~350 NFL series carry ~16,700 open
markets and would fill the disk in a day. So for the other ~340 series there
is no history on disk at all. The only honest way to put them in a catalogue is
one current listing per series - which gives market counts, structure, touch
spread and cumulative volume, and CANNOT give prints per market or anything
about persistence. Every number from this job is labelled a snapshot for that
reason.

ONE CALL PER SERIES. `/events?series_ticker=&with_nested_markets=true` returns
the events with their markets inline AND each event's `mutually_exclusive`
flag, which is Kalshi's own statement of whether an event is a partition. That
flag says AT MOST one leg resolves yes; it does not say the legs are
exhaustive, so a partition test still has to check for the no-outcome leg.

Free and unauthenticated. It shares the venue rate budget with the running
logger, so it runs slow and backs off blind on 429.

RAW FIRST, and into its OWN database - see `jobs/ingest_kalshi_trades.py` for
the self-deadlock that motivates repointing `config.DB_PATH` before `store` is
imported. STORAGE LOCATION COMES FROM CONFIG, NEVER FROM A PATH LITERAL.
"""
import argparse
import gzip
import json
import os
import re
import sqlite3
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx

import config

LIVE_DB = config.DB_PATH
SNAP_DB = os.path.join(os.path.dirname(os.path.abspath(config.DB_PATH)),
                       "board_019.db")
VENUE = "kalshi_series"
RPS = 2.0
MAX_PAGES = 10
BACKOFF = [2, 5, 15, 45]
NFL = re.compile(r"(?<!I)NFL(?!X)")

store = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS series (
    ticker TEXT PRIMARY KEY, title TEXT, category TEXT, frequency TEXT,
    fee_type TEXT, fee_multiplier REAL, tracked INTEGER, snapshot_ts REAL
);
CREATE TABLE IF NOT EXISTS events (
    event_ticker TEXT PRIMARY KEY, series TEXT, title TEXT,
    mutually_exclusive INTEGER, n_markets INTEGER, snapshot_ts REAL
);
CREATE TABLE IF NOT EXISTS markets (
    ticker TEXT PRIMARY KEY, series TEXT, event_ticker TEXT, title TEXT,
    yes_sub_title TEXT, floor_strike REAL, cap_strike REAL, strike_type TEXT,
    yes_bid REAL, yes_ask REAL, volume REAL, open_interest REAL,
    status TEXT, close_ts REAL, snapshot_ts REAL
);
CREATE TABLE IF NOT EXISTS fetch_log (
    series TEXT PRIMARY KEY, status INTEGER, pages INTEGER, n_events INTEGER,
    n_markets INTEGER, note TEXT, fetched_ts REAL
);
"""


def conn():
    c = sqlite3.connect(SNAP_DB, timeout=60)
    c.execute("PRAGMA journal_mode=WAL")
    c.executescript(SCHEMA)
    return c


def _f(v):
    try:
        return None if v in (None, "") else float(v)
    except (TypeError, ValueError):
        return None


def _ts(v):
    from datetime import datetime
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def nfl_series(payload):
    """NFL series from a `/series` payload. Same filter as discovery: the
    `(?<!I)NFL(?!X)` pattern, minus anything collegiate."""
    out = []
    for s in payload.get("series") or []:
        tk = s.get("ticker") or ""
        if NFL.search(tk) and "NCAA" not in tk:
            out.append(s)
    return out


def latest_series_payload():
    root = os.path.join(config.RAW_DIR, VENUE)
    best = None
    for day in sorted(os.listdir(root)) if os.path.isdir(root) else []:
        d = os.path.join(root, day)
        for fn in sorted(os.listdir(d)):
            if not fn.endswith(".jsonl.gz"):
                continue
            with gzip.open(os.path.join(d, fn), "rt", encoding="utf-8") as f:
                for line in f:
                    rec = json.loads(line)
                    if rec.get("endpoint") == "series:all":
                        best = rec
    if best is None:
        raise SystemExit("no archived /series payload - fetch one first")
    return best["payload"], best["ts"]


def tracked(ticker):
    for pattern, _mt, _h in config.KALSHI_SERIES_ALLOW:
        if pattern.endswith("*") and ticker.startswith(pattern[:-1]):
            return True
        if ticker == pattern:
            return True
    return False


def parse(c, series_ticker, payloads, snap_ts):
    ne = nm = 0
    for d in payloads:
        for e in d.get("events") or []:
            mk = e.get("markets") or []
            c.execute("INSERT OR REPLACE INTO events VALUES (?,?,?,?,?,?)",
                      (e.get("event_ticker"), series_ticker, e.get("title"),
                       int(bool(e.get("mutually_exclusive"))), len(mk), snap_ts))
            ne += 1
            for m in mk:
                c.execute(
                    "INSERT OR REPLACE INTO markets VALUES "
                    "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (m.get("ticker"), series_ticker, e.get("event_ticker"),
                     m.get("title"), m.get("yes_sub_title"),
                     _f(m.get("floor_strike")), _f(m.get("cap_strike")),
                     m.get("strike_type"),
                     _f(m.get("yes_bid_dollars")), _f(m.get("yes_ask_dollars")),
                     _f(m.get("volume_fp", m.get("volume"))),
                     _f(m.get("open_interest_fp", m.get("open_interest"))),
                     m.get("status"), _ts(m.get("close_time")), snap_ts))
                nm += 1
    return ne, nm


def fetch(limit=None):
    c = conn()
    payload, _ = latest_series_payload()
    nfl = nfl_series(payload)
    now = time.time()
    for s in nfl:
        c.execute("INSERT OR REPLACE INTO series VALUES (?,?,?,?,?,?,?,?)",
                  (s["ticker"], s.get("title"), s.get("category"),
                   s.get("frequency"), s.get("fee_type"),
                   _f(s.get("fee_multiplier")), int(tracked(s["ticker"])), now))
    c.commit()
    todo = nfl[:limit] if limit else nfl
    print(f"{len(nfl)} NFL series listed, {sum(tracked(s['ticker']) for s in nfl)} "
          f"tracked by the logger; snapshotting {len(todo)} -> {SNAP_DB}")
    nxt = 0.0
    with httpx.Client(timeout=60, headers={"User-Agent": config.USER_AGENT},
                      follow_redirects=True) as client:
        for i, s in enumerate(todo, 1):
            tk = s["ticker"]
            payloads, cursor, code, note = [], None, 200, ""
            for _page in range(MAX_PAGES):
                wait = nxt - time.monotonic()
                if wait > 0:
                    time.sleep(wait)
                nxt = time.monotonic() + 1.0 / RPS
                params = {"series_ticker": tk, "status": "open",
                          "with_nested_markets": "true", "limit": 200}
                if cursor:
                    params["cursor"] = cursor
                for attempt, back in enumerate([0] + BACKOFF):
                    if back:
                        time.sleep(back)
                    r = client.get(f"{config.KALSHI_BASE}/events", params=params)
                    if r.status_code != 429:
                        break
                    note = f"429 x{attempt + 1}"
                code = r.status_code
                if code != 200:
                    note = note or f"http {code}"
                    break
                d = r.json()
                store.archive_raw(VENUE, f"events:{tk}", d)      # RAW FIRST
                payloads.append(d)
                cursor = d.get("cursor") or ""
                if not cursor or not d.get("events"):
                    break
            ne, nm = parse(c, tk, payloads, time.time())
            c.execute("INSERT OR REPLACE INTO fetch_log VALUES (?,?,?,?,?,?,?)",
                      (tk, code, len(payloads), ne, nm, note, time.time()))
            c.commit()
            if i % 50 == 0 or i == len(todo):
                print(f"  {i}/{len(todo)}")
    status(c)


def from_archive():
    c = conn()
    root = os.path.join(config.RAW_DIR, VENUE)
    n = 0
    for day in sorted(os.listdir(root)):
        d = os.path.join(root, day)
        for fn in sorted(os.listdir(d)):
            with gzip.open(os.path.join(d, fn), "rt", encoding="utf-8") as f:
                for line in f:
                    rec = json.loads(line)
                    ep = rec.get("endpoint", "")
                    if ep.startswith("events:"):
                        parse(c, ep.split(":", 1)[1], [rec["payload"]], rec["ts"])
                        n += 1
    c.commit()
    print(f"re-parsed {n} event pages, 0 requests")
    status(c)


def status(c=None):
    c = c or conn()
    s = c.execute("SELECT COUNT(*), SUM(tracked) FROM series").fetchone()
    f = c.execute("SELECT COUNT(*), SUM(status=200), SUM(n_markets), "
                  "SUM(n_markets>0) FROM fetch_log").fetchone()
    print(f"  series listed {s[0]} (tracked {s[1]}), fetched {f[0]} "
          f"({f[1]} ok), with open markets {f[3]}, open markets {f[2]}")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--from-archive", action="store_true")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--limit", type=int)
    a = ap.parse_args()
    config.DB_PATH = SNAP_DB              # REPOINT BEFORE store IS TOUCHED
    global store
    import store as _store
    store = _store
    if a.status:
        status()
    elif a.from_archive:
        from_archive()
    else:
        fetch(a.limit)


if __name__ == "__main__":
    main()
