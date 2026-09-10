"""Capture order book depth as a VWAP ladder, continuously.

    python -m jobs.capture_depth --once
    python -m jobs.capture_depth --loop --every 60
    python -m jobs.capture_depth --report

WHAT IS STORED AND WHY NOT RAW L2
----------------------------------
Per snapshot per market, both sides: touch price and size, effective VWAP to
fill 100 / 500 / 1000 / 5000 contracts, and cumulative size within 1c and 5c of
the touch. That is ~14 numbers, against a raw book of 10-100 levels.

Raw L2 is kept only for an ALLOWLIST - current-week priority markets
(receptions, targets, rush attempts) and anything sitting in the paper ledger -
because those are the books a fill would actually be reconstructed against.
Storing raw for all 3,400 markets would be most of a gigabyte a day to record
that nobody was quoting them.

RATE LIMITS, measured 2026-09-10
---------------------------------
The BATCHED endpoint is not the rate-limited one. `/markets/orderbooks` with
100 repeated `tickers` params sustained 13.6 calls/s over 18 consecutive calls
with zero 429s, and returns FULL depth per book - 0.54 MB for all 1,743 mapped
markets. The ~5-call limit in CLAUDE.md belongs to `/candlesticks`, a different
endpoint entirely. So REST holds the whole slate comfortably and the WebSocket
delta path is not needed; see PART 4 note in the brief 007 commit.

Polymarket has no batched book endpoint, so its depth is captured for the
allowlist only - one call per token, which is why the allowlist exists.
"""
import argparse
import json
import sqlite3
import time

import httpx

import config
import store
from venues.depth import (DEFAULT_SIZES, kalshi_buy_ladders, ladder_depth,
                          polymarket_buy_ladders)

BATCH = 100                 # kalshi hard limit on repeated tickers params
KALSHI_CALLS_PER_S = 8.0    # measured 13.6/s clean; leave real headroom
POLY_CALLS_PER_S = 5.0

SCHEMA = """
CREATE TABLE IF NOT EXISTS market_depth (
    ts           REAL NOT NULL,
    venue        TEXT NOT NULL,
    market_id    TEXT NOT NULL,
    outcome_id   TEXT,
    side         TEXT NOT NULL,      -- buy_yes | buy_no
    touch_price  REAL,
    touch_size   REAL,
    n_levels     INTEGER,
    total_size   REAL,
    size_within_1c REAL,
    size_within_5c REAL,
    vwap_100     REAL,               -- NULL when the book cannot fill it
    vwap_500     REAL,
    vwap_1000    REAL,
    vwap_5000    REAL,
    filled_1000  REAL,               -- contracts actually available at 1000
    PRIMARY KEY (ts, venue, market_id, side)
);
CREATE INDEX IF NOT EXISTS ix_depth_market ON market_depth(venue, market_id, ts);
CREATE INDEX IF NOT EXISTS ix_depth_outcome ON market_depth(outcome_id, ts);
"""

PRIORITY_STATS = ("receptions", "targets", "rush_attempts")


def allowlist(con):
    """Markets whose RAW book is worth keeping, not just the VWAP ladder."""
    rows = con.execute(
        """SELECT DISTINCT mo.venue, mo.market_id
             FROM market_outcome mo
             JOIN outcomes o USING (outcome_id)
            WHERE o.stat IN (?, ?, ?) AND o.season = 2026
              AND o.week = (SELECT MIN(week) FROM outcomes
                             WHERE season = 2026 AND week IS NOT NULL)
            UNION
           SELECT DISTINCT venue, market_id FROM paper_ledger""",
        PRIORITY_STATS).fetchall()
    return {(v, m) for v, m in rows}


def targets(con, venue):
    return con.execute(
        """SELECT mo.market_id, mo.outcome_id FROM market_outcome mo
            WHERE mo.venue = ? AND mo.outcome_id IS NOT NULL""",
        (venue,)).fetchall()


def _rows(ts, venue, market_id, outcome_id, buy_yes, buy_no):
    out = []
    for side, ladder in (("buy_yes", buy_yes), ("buy_no", buy_no)):
        d = ladder_depth(ladder, DEFAULT_SIZES)
        out.append((ts, venue, market_id, outcome_id, side, d.touch_price,
                    d.touch_size, d.n_levels, d.total_size, d.size_within_1c,
                    d.size_within_5c, d.vwap.get(100), d.vwap.get(500),
                    d.vwap.get(1000), d.vwap.get(5000), d.filled.get(1000)))
    return out


COLS = ("ts", "venue", "market_id", "outcome_id", "side", "touch_price",
        "touch_size", "n_levels", "total_size", "size_within_1c",
        "size_within_5c", "vwap_100", "vwap_500", "vwap_1000", "vwap_5000",
        "filled_1000")


def snapshot_kalshi(client, con, allow, ts):
    tgts = targets(con, "kalshi")
    by_id = dict(tgts)
    tickers = [t for t, _ in tgts]
    rows, raw_kept, interval = [], 0, 1.0 / KALSHI_CALLS_PER_S
    for i in range(0, len(tickers), BATCH):
        chunk = tickers[i:i + BATCH]
        t0 = time.time()
        try:
            r = client.get(f"{config.KALSHI_BASE}/markets/orderbooks",
                           params=[("tickers", t) for t in chunk])
            if r.status_code != 200:
                time.sleep(1.0)
                continue
            body = r.json()
        except Exception:
            time.sleep(1.0)
            continue
        keep = {}
        for o in body.get("orderbooks") or []:
            tk = o.get("ticker")
            if not tk:
                continue
            buy_yes, buy_no = kalshi_buy_ladders(o.get("orderbook_fp"))
            rows.extend(_rows(ts, "kalshi", tk, by_id.get(tk), buy_yes, buy_no))
            if ("kalshi", tk) in allow:
                keep[tk] = o
        if keep:
            # Raw L2, allowlist only. Archived rather than stored in SQLite:
            # it is a verbatim payload and belongs with the rest of the raw
            # archive, where invariant #2 already governs it.
            store.archive_raw("kalshi_depth", "orderbooks", keep)
            raw_kept += len(keep)
        time.sleep(max(0.0, interval - (time.time() - t0)))
    return rows, raw_kept


def snapshot_polymarket(client, con, allow, ts):
    """Allowlist only - there is no batched book endpoint, so this is one call
    per token and the whole slate would not fit inside any sane cadence."""
    tgts = [(m, o) for m, o in targets(con, "polymarket")
            if ("polymarket", m) in allow]
    rows, raw_kept, interval = [], 0, 1.0 / POLY_CALLS_PER_S
    for token, outcome_id in tgts:
        t0 = time.time()
        try:
            r = client.get(f"{config.POLY_CLOB}/book",
                           params={"token_id": token})
            if r.status_code != 200:
                continue
            book = r.json()
        except Exception:
            continue
        buy_yes, buy_no = polymarket_buy_ladders(book)
        rows.extend(_rows(ts, "polymarket", token, outcome_id, buy_yes, buy_no))
        store.archive_raw("polymarket_depth", "book", book)
        raw_kept += 1
        time.sleep(max(0.0, interval - (time.time() - t0)))
    return rows, raw_kept


def run_once():
    with store.db() as c:
        c.executescript(SCHEMA)
    con = sqlite3.connect(f"file:{config.DB_PATH}?mode=ro", uri=True)
    allow = allowlist(con)
    t0 = time.time()
    # ONE timestamp for the whole snapshot. Stamping each venue as it finished
    # meant a snapshot had two different ts values, and any query anchored on
    # MAX(ts) silently saw one venue and none of the other.
    ts = t0
    with httpx.Client(timeout=45, follow_redirects=True,
                      headers={"User-Agent": config.USER_AGENT}) as client:
        krows, kraw = snapshot_kalshi(client, con, allow, ts)
        prows, praw = snapshot_polymarket(client, con, allow, ts)
    con.close()
    n = store.replace_rows("market_depth", COLS, krows + prows, None)
    stats = {"kalshi_rows": len(krows), "poly_rows": len(prows),
             "raw_books_kept": kraw + praw, "allowlist": len(allow),
             "written": n, "elapsed": round(time.time() - t0, 1)}
    store.record_health("depth_capture", True,
                        ", ".join(f"{k}={v}" for k, v in stats.items()),
                        watermark=time.time())
    return stats


def report():
    con = sqlite3.connect(f"file:{config.DB_PATH}?mode=ro", uri=True)
    print("depth coverage")
    for venue, n, mk, snaps, lo, hi in con.execute(
            """SELECT venue, COUNT(*), COUNT(DISTINCT market_id),
                      COUNT(DISTINCT ts), MIN(ts), MAX(ts)
                 FROM market_depth GROUP BY venue"""):
        print(f"  {venue:<12} {n:>8,} rows  {mk:>5,} markets  {snaps:>4} snapshots"
              f"  span {(hi - lo) / 60:.1f} min")

    print("\ntouch vs executable size (kalshi, latest snapshot, buy_yes)")
    row = con.execute("SELECT MAX(ts) FROM market_depth").fetchone()
    if not row or row[0] is None:
        con.close()
        return
    ts = row[0]
    print(f"  {'stake':>7} {'fillable':>9} {'median slip':>12} {'p90 slip':>10}")
    for size in (100, 500, 1000, 5000):
        col = f"vwap_{size}"
        vals = [r[0] for r in con.execute(
            f"""SELECT {col} / touch_price - 1 FROM market_depth
                 WHERE ts = ? AND venue='kalshi' AND side='buy_yes'
                   AND {col} IS NOT NULL AND touch_price > 0""", (ts,))]
        total = con.execute(
            """SELECT COUNT(*) FROM market_depth WHERE ts=? AND venue='kalshi'
                AND side='buy_yes'""", (ts,)).fetchone()[0]
        vals.sort()
        if vals:
            med = vals[len(vals) // 2]
            p90 = vals[int(len(vals) * 0.9)]
            print(f"  {size:>7,} {len(vals)/total if total else 0:>8.1%} "
                  f"{med:>+12.1%} {p90:>+10.1%}")
        else:
            print(f"  {size:>7,} {'0.0%':>9} {'-':>12} {'-':>10}")

    print("\nmarkets where 1000-contract VWAP exceeds touch by >20%")
    rows = con.execute(
        """SELECT market_id, touch_price, touch_size, vwap_1000,
                  vwap_1000 / touch_price - 1 AS slip
             FROM market_depth
            WHERE ts = ? AND side = 'buy_yes' AND vwap_1000 IS NOT NULL
              AND touch_price > 0 AND vwap_1000 > touch_price * 1.2
            ORDER BY slip DESC LIMIT 8""", (ts,)).fetchall()
    print(f"  {len(rows)} shown; "
          + str(con.execute(
              """SELECT COUNT(*) FROM market_depth WHERE ts=? AND side='buy_yes'
                  AND vwap_1000 IS NOT NULL AND touch_price>0
                  AND vwap_1000 > touch_price*1.2""", (ts,)).fetchone()[0])
          + " total")
    for mk, tp, tsz, v, slip in rows:
        print(f"    {mk:<46} touch {tp:.2f} x{tsz:>7,.0f} -> vwap1000 "
              f"{v:.4f}  {slip:+.0%}")
    con.close()


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--loop", action="store_true")
    ap.add_argument("--every", type=float, default=60.0)
    ap.add_argument("--report", action="store_true")
    args = ap.parse_args()

    store.init_db()
    if args.report:
        report()
        return
    if args.loop:
        while True:
            s = run_once()
            print(" ".join(f"{k}={v}" for k, v in s.items()), flush=True)
            time.sleep(max(0.0, args.every - s["elapsed"]))
    else:
        s = run_once()
        print(" ".join(f"{k}={v}" for k, v in s.items()))
        print()
        report()


if __name__ == "__main__":
    main()
