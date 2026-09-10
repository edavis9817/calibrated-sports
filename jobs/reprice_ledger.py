"""Reprice the paper ledger at executable size instead of top-of-book mid.

    python -m jobs.reprice_ledger --stake 100
    python -m jobs.reprice_ledger --stake 1000 --report

THE HONESTY CHECK
-----------------
Every ticket in the ledger was priced at the mid of the touch. The touch is a
price for whatever size happens to be resting there - sometimes one contract.
This recomputes each ticket against the VWAP of actually filling the stake, and
reports edge before and after, by liquidity bucket.

A ticket whose edge disappears at executable size was never a bet. Expect the
thin bucket to be wiped out; that is the finding, not a failure of the job.

The fee moves too, and not by a rounding amount: the Kalshi taker fee is
0.07*C*P*(1-P), so a worse fill price changes what the fee costs as well as
what the contract costs. Both are recomputed, never just the price.
"""
import argparse
import sqlite3
import time

import config
import store
from core.distributions import edge_after_fees, kalshi_fee

SIDE_TO_DEPTH = {"yes": "buy_yes", "no": "buy_no"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS ledger_reprice (
    ticket_id      INTEGER NOT NULL,
    stake          INTEGER NOT NULL,
    depth_ts       REAL,
    touch_price    REAL,
    exec_price     REAL,          -- VWAP to fill `stake`; NULL if unfillable
    fillable       REAL,          -- contracts actually available
    model_prob     REAL,
    edge_before    REAL,          -- at the mid, as originally booked
    edge_after     REAL,          -- at the executable price
    fee_before     REAL,
    fee_after      REAL,
    bucket         TEXT,
    repriced_ts    REAL NOT NULL,
    PRIMARY KEY (ticket_id, stake)
);
CREATE INDEX IF NOT EXISTS ix_reprice_bucket ON ledger_reprice(bucket, stake);
"""


def nearest_depth(con, venue, market_id, side, entry_ts):
    """The depth snapshot closest in time to the ticket's entry.

    Closest, not latest: once depth capture runs alongside the logger these are
    the same thing, but tickets written before capture started can only be
    repriced against a later book. The chosen ts is stored on the row so the
    gap is visible rather than assumed away.
    """
    return con.execute(
        """SELECT ts, touch_price, vwap_100, vwap_500, vwap_1000, vwap_5000,
                  filled_1000, total_size
             FROM market_depth
            WHERE venue = ? AND market_id = ? AND side = ?
            ORDER BY ABS(ts - ?) LIMIT 1""",
        (venue, market_id, SIDE_TO_DEPTH.get(side, side), entry_ts)).fetchone()


def exec_price_for(row, stake):
    """VWAP at the requested stake, and how much was actually available."""
    if row is None:
        return None, None, None
    ts, touch, v100, v500, v1000, v5000, filled1000, total = row
    by_size = {100: v100, 500: v500, 1000: v1000, 5000: v5000}
    # Use the smallest ladder rung at or above the stake we care about.
    for size in sorted(by_size):
        if size >= stake:
            return ts, touch, by_size[size]
    return ts, touch, by_size[max(by_size)]


def run(stake=100):
    with store.db() as c:
        c.executescript(SCHEMA)
    con = sqlite3.connect(f"file:{config.DB_PATH}?mode=ro", uri=True)
    tickets = con.execute(
        """SELECT l.ticket_id, l.venue, l.market_id, l.side, l.model_prob,
                  l.market_prob, l.net_edge, l.fee, l.entry_ts,
                  COALESCE(liq.bucket, 'unknown')
             FROM paper_ledger l
             LEFT JOIN market_liquidity liq
               ON liq.venue = l.venue AND liq.market_id = l.market_id
              AND liq.period = strftime('%Y-W%W', l.entry_ts, 'unixepoch')
             JOIN predictions p USING (prediction_id)
            WHERE p.model_version = (SELECT MAX(model_version) FROM predictions)
        """).fetchall()

    now = time.time()
    out, missing = [], 0
    for (tid, venue, market_id, side, model_prob, market_prob, edge_before,
         fee_before, entry_ts, bucket) in tickets:
        row = nearest_depth(con, venue, market_id, side, entry_ts)
        depth_ts, touch, exec_p = exec_price_for(row, stake)
        if row is None:
            missing += 1
        fillable = row[6] if row else None
        if exec_p is None:
            # The book cannot fill this stake at any price. That is not a bad
            # edge, it is no trade - recorded as NULL rather than as a number.
            edge_after, fee_after = None, None
        else:
            edge_after = edge_after_fees(model_prob, exec_p, 1)
            fee_after = kalshi_fee(exec_p, 1)
        out.append((tid, stake, depth_ts, touch, exec_p, fillable, model_prob,
                    edge_before, edge_after, fee_before, fee_after, bucket, now))
    con.close()

    cols = ("ticket_id", "stake", "depth_ts", "touch_price", "exec_price",
            "fillable", "model_prob", "edge_before", "edge_after", "fee_before",
            "fee_after", "bucket", "repriced_ts")
    n = store.replace_rows("ledger_reprice", cols, out, None)
    store.record_health("reprice_ledger", True,
                        f"{n} tickets repriced at stake {stake}, "
                        f"{missing} with no depth snapshot", watermark=now)
    return {"tickets": n, "stake": stake, "no_depth": missing}


def report(stake=100):
    con = sqlite3.connect(f"file:{config.DB_PATH}?mode=ro", uri=True)
    print(f"ledger repriced at executable size = {stake} contracts\n")
    print(f"  {'bucket':<9} {'tickets':>8} {'edge before':>12} {'edge after':>11} "
          f"{'change':>9} {'survive':>8} {'unfillable':>11}")
    tot = [0, 0.0, 0.0, 0, 0]
    for bucket, n, before, after, survive, unfill in con.execute(
            """SELECT bucket, COUNT(*), AVG(edge_before),
                      AVG(edge_after),
                      SUM(CASE WHEN edge_after > 0 THEN 1 ELSE 0 END),
                      SUM(CASE WHEN edge_after IS NULL THEN 1 ELSE 0 END)
                 FROM ledger_reprice WHERE stake = ?
                GROUP BY bucket
                ORDER BY CASE bucket WHEN 'deep' THEN 1 WHEN 'medium' THEN 2
                         WHEN 'thin' THEN 3 ELSE 4 END""", (stake,)):
        after = after if after is not None else float("nan")
        chg = after - before
        print(f"  {bucket:<9} {n:>8,} {before:>+12.4f} {after:>+11.4f} "
              f"{chg:>+9.4f} {survive/n:>8.1%} {unfill/n:>11.1%}")
        tot[0] += n
        tot[1] += before * n
        tot[3] += survive
        tot[4] += unfill
    if tot[0]:
        row = con.execute(
            "SELECT AVG(edge_before), AVG(edge_after) FROM ledger_reprice "
            "WHERE stake = ?", (stake,)).fetchone()
        print(f"  {'ALL':<9} {tot[0]:>8,} {row[0]:>+12.4f} "
              f"{(row[1] or 0):>+11.4f} {((row[1] or 0) - row[0]):>+9.4f} "
              f"{tot[3]/tot[0]:>8.1%} {tot[4]/tot[0]:>11.1%}")

    print()
    print("  worst repricings")
    print(f"    {'ticket':>7} {'bucket':<8} {'touch':>7} {'exec':>7} "
          f"{'before':>8} {'after':>8}")
    for tid, bucket, touch, ex, b, a in con.execute(
            """SELECT ticket_id, bucket, touch_price, exec_price, edge_before,
                      edge_after FROM ledger_reprice
                WHERE stake = ? AND edge_after IS NOT NULL
                ORDER BY (edge_after - edge_before) ASC LIMIT 6""", (stake,)):
        print(f"    {tid:>7} {bucket:<8} {touch:>7.3f} {ex:>7.3f} "
              f"{b:>+8.3f} {a:>+8.3f}")
    con.close()


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--stake", type=int, default=100)
    ap.add_argument("--report", action="store_true")
    args = ap.parse_args()
    store.init_db()
    if not args.report:
        s = run(args.stake)
        print(" ".join(f"{k}={v}" for k, v in s.items()))
        print()
    report(args.stake)


if __name__ == "__main__":
    main()
