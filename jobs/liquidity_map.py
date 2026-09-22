"""Per-market, per-week liquidity: volume, open interest, time-weighted spread.

    python -m jobs.liquidity_map                 # build the map
    python -m jobs.liquidity_map --report        # trend over the covered period
    python -m jobs.liquidity_map --strata        # what each bucket contains

WHY THIS IS NOT A FOOTNOTE
--------------------------
A backtest result that exists only in the thin bucket is a spread you could not
have crossed. Reporting one blended number lets the thin bucket - where the
model finds its biggest "edges", because nobody is quoting tightly enough to
contradict it - carry the headline. So the bucket is written onto the market
and every downstream query joins it. `quotes_stratified` exists so that
forgetting to stratify takes deliberate effort rather than being the default.

WHAT EACH VENUE ACTUALLY GIVES YOU (probed 2026-09-09)
------------------------------------------------------
  kalshi     volume_fp AND open_interest_fp on every candle and every live
             quote, plus a real bid/ask so spread is measurable.
  polymarket NOTHING. `prices-history` returns {t, p} only, and the live CLOB
             path records no volume either. Backfilled Polymarket rows have no
             spread at all - there is no book in a price series.

So Polymarket cannot be bucketed on the same axis as Kalshi, and averaging them
into one "liquidity" number would invent a measurement for one of them. Live
Polymarket rows get a spread-only bucket; backfilled ones get `unknown`, which
is a real answer and must not be read as "fine".
"""
import argparse
import sqlite3
import time
from datetime import datetime, timezone

import config
import store

# Thresholds are per market-week. Deliberately round numbers, set before
# looking at how they'd partition the results.
DEEP_VOLUME, DEEP_SPREAD = 10_000.0, 0.02
MED_VOLUME, MED_SPREAD = 1_000.0, 0.05

SCHEMA = """
CREATE TABLE IF NOT EXISTS market_liquidity (
    venue        TEXT NOT NULL,
    market_id    TEXT NOT NULL,
    period       TEXT NOT NULL,      -- ISO year-week, e.g. 2026-W37
    outcome_id   TEXT,
    n_quotes     INTEGER NOT NULL,
    volume       REAL,               -- summed over the period; NULL if unknown
    open_interest REAL,              -- last observed in the period
    tw_spread    REAL,               -- time-weighted mean ask-bid
    min_spread   REAL,
    bucket       TEXT NOT NULL,      -- deep | medium | thin | unknown
    first_ts     REAL,
    last_ts      REAL,
    built_ts     REAL NOT NULL,
    PRIMARY KEY (venue, market_id, period)
);
CREATE INDEX IF NOT EXISTS ix_liq_bucket ON market_liquidity(bucket, period);
CREATE INDEX IF NOT EXISTS ix_liq_outcome ON market_liquidity(outcome_id);

-- The stratification, made structural. Any downstream query that reads quotes
-- through this view carries the bucket with it and cannot silently blend a
-- deep market with one nobody was quoting.
DROP VIEW IF EXISTS quotes_stratified;
CREATE VIEW quotes_stratified AS
SELECT q.*, mo.outcome_id,
       COALESCE(l.bucket, 'unknown') AS liquidity_bucket,
       l.tw_spread, l.volume AS period_volume
  FROM quotes q
  LEFT JOIN market_outcome mo
    ON mo.venue = q.venue AND mo.market_id = q.market_id
  LEFT JOIN market_liquidity l
    ON l.venue = q.venue AND l.market_id = q.market_id
   AND l.period = strftime('%Y-W%W', q.ts, 'unixepoch');
"""


def period_of(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-W%W")


def bucket_for(volume, tw_spread) -> str:
    """Classify a market-week.

    Volume and spread must BOTH be good for the deep bucket: a market can be
    heavily traded in a burst and quoted two cents wide the rest of the week,
    and a tight quote on no volume is a market maker talking to himself.
    """
    if volume is None and tw_spread is None:
        return "unknown"
    if volume is not None and tw_spread is not None:
        if volume >= DEEP_VOLUME and tw_spread <= DEEP_SPREAD:
            return "deep"
        if volume >= MED_VOLUME and tw_spread <= MED_SPREAD:
            return "medium"
        return "thin"
    # Only one axis available - Polymarket live has a spread and no volume.
    # Never promote to `deep` on half the evidence.
    if tw_spread is not None:
        return "medium" if tw_spread <= DEEP_SPREAD else "thin"
    return "medium" if volume >= MED_VOLUME else "thin"


def build():
    with store.db() as c:
        c.executescript(SCHEMA)

    con = sqlite3.connect(f"file:{config.DB_PATH}?mode=ro", uri=True)
    rows = con.execute(
        """SELECT q.venue, q.market_id, q.ts, q.best_bid, q.best_ask,
                  q.volume, q.open_interest, mo.outcome_id, q.source
             FROM quotes q
             LEFT JOIN market_outcome mo
               ON mo.venue = q.venue AND mo.market_id = q.market_id
            ORDER BY q.venue, q.market_id, q.ts""").fetchall()
    con.close()

    acc, out = {}, []
    for venue, market_id, ts, bid, ask, vol, oi, outcome_id, source in rows:
        key = (venue, market_id, period_of(ts))
        a = acc.setdefault(key, {"n": 0, "vol_sum": None, "vol_lo": None,
                                 "vol_hi": None, "oi": None,
                                 "sw": 0.0, "dt": 0.0, "prev_ts": None,
                                 "prev_spread": None, "min_spread": None,
                                 "first": ts, "last": ts,
                                 "outcome_id": outcome_id})
        a["n"] += 1
        a["last"] = ts
        a["outcome_id"] = a["outcome_id"] or outcome_id
        # TWO VOLUME SEMANTICS, and mixing them produced a 6.7-BILLION-contract
        # week before this was caught. A candle's `volume_fp` is the volume IN
        # that period, so periods add. A live quote's `volume` is the market's
        # cumulative total to date, so summing 700k of them counts the same
        # contracts 700k times. Cumulative series contribute their RANGE.
        if vol is not None:
            if str(source or "").startswith("backfill"):
                a["vol_sum"] = (a["vol_sum"] or 0.0) + vol
            else:
                a["vol_lo"] = vol if a["vol_lo"] is None else min(a["vol_lo"], vol)
                a["vol_hi"] = vol if a["vol_hi"] is None else max(a["vol_hi"], vol)
        if oi is not None:
            a["oi"] = oi
        spread = (ask - bid) if bid is not None and ask is not None else None
        if spread is not None:
            a["min_spread"] = (spread if a["min_spread"] is None
                               else min(a["min_spread"], spread))
        # Time-weight by how long the PREVIOUS quote stood. A market quoted
        # tight for a minute and wide for an hour is a wide market; a simple
        # mean over rows would call it tight.
        if a["prev_ts"] is not None and a["prev_spread"] is not None:
            dt = max(ts - a["prev_ts"], 0.0)
            a["sw"] += a["prev_spread"] * dt
            a["dt"] += dt
        a["prev_ts"], a["prev_spread"] = ts, spread

    now = time.time()
    for (venue, market_id, period), a in acc.items():
        tw = (a["sw"] / a["dt"]) if a["dt"] > 0 else a["min_spread"]
        vol = a["vol_sum"]
        if a["vol_hi"] is not None:
            traded = a["vol_hi"] - a["vol_lo"]
            vol = (vol or 0.0) + traded
        out.append((venue, market_id, period, a["outcome_id"], a["n"],
                    vol, a["oi"], tw, a["min_spread"],
                    bucket_for(vol, tw), a["first"], a["last"], now))

    cols = ("venue", "market_id", "period", "outcome_id", "n_quotes", "volume",
            "open_interest", "tw_spread", "min_spread", "bucket", "first_ts",
            "last_ts", "built_ts")
    store.replace_rows("market_liquidity", cols, out)
    store.record_health("liquidity_map", True,
                        f"{len(out)} market-weeks classified", watermark=now)
    return {"market_weeks": len(out), "quotes_scanned": len(rows)}


def report():
    con = sqlite3.connect(f"file:{config.DB_PATH}?mode=ro", uri=True)
    print("liquidity by venue and bucket")
    print(f"  {'venue':<12} {'bucket':<9} {'mkt-wks':>8} {'quotes':>10} "
          f"{'med volume':>12} {'med spread':>11}")
    for venue, bucket, n, q in con.execute(
            """SELECT venue, bucket, COUNT(*), SUM(n_quotes)
                 FROM market_liquidity GROUP BY 1,2
                ORDER BY venue, CASE bucket WHEN 'deep' THEN 1 WHEN 'medium'
                       THEN 2 WHEN 'thin' THEN 3 ELSE 4 END"""):
        med = con.execute(
            """SELECT volume, tw_spread FROM market_liquidity
                WHERE venue=? AND bucket=? ORDER BY volume LIMIT 1
                OFFSET (SELECT COUNT(*)/2 FROM market_liquidity
                         WHERE venue=? AND bucket=?)""",
            (venue, bucket, venue, bucket)).fetchone() or (None, None)
        v = f"{med[0]:,.0f}" if med[0] is not None else "-"
        s = f"{med[1]:.4f}" if med[1] is not None else "-"
        print(f"  {venue:<12} {bucket:<9} {n:>8,} {q:>10,} {v:>12} {s:>11}")

    print("\ntrend over the covered period (kalshi, which has volume)")
    print(f"  {'period':<10} {'markets':>8} {'quotes':>10} {'volume':>14} "
          f"{'open interest':>15} {'tw spread':>10}")
    for row in con.execute(
            """SELECT period, COUNT(*), SUM(n_quotes), SUM(volume),
                      SUM(open_interest), AVG(tw_spread)
                 FROM market_liquidity WHERE venue='kalshi'
                GROUP BY period ORDER BY period"""):
        p, n, q, v, oi, sp = row
        print(f"  {p:<10} {n:>8,} {q:>10,} {(v or 0):>14,.0f} "
              f"{(oi or 0):>15,.0f} {(sp or 0):>10.4f}")
    con.close()


def strata():
    """What lives in each bucket - the number that makes the honesty check real."""
    con = sqlite3.connect(f"file:{config.DB_PATH}?mode=ro", uri=True)
    print("paper ledger stratified by the liquidity of the market traded")
    print(f"  {'bucket':<9} {'tickets':>8} {'avg net edge':>13} "
          f"{'avg spread':>11} {'share':>7}")
    rows = con.execute(
        """SELECT COALESCE(l.bucket, 'unknown'), COUNT(*), AVG(pl.net_edge),
                  AVG(pl.spread)
             FROM paper_ledger pl
             LEFT JOIN market_liquidity l
               ON l.venue = pl.venue AND l.market_id = pl.market_id
              AND l.period = strftime('%Y-W%W', pl.entry_ts, 'unixepoch')
            GROUP BY 1
            ORDER BY CASE COALESCE(l.bucket,'unknown') WHEN 'deep' THEN 1
                     WHEN 'medium' THEN 2 WHEN 'thin' THEN 3 ELSE 4 END""").fetchall()
    total = sum(r[1] for r in rows) or 1
    for bucket, n, edge, spread in rows:
        print(f"  {bucket:<9} {n:>8,} {edge:>+13.4f} {spread:>11.4f} "
              f"{n/total:>7.1%}")
    print()
    print("  Read this before believing any edge number: a result that lives")
    print("  in `thin` is a spread you could not have crossed.")
    con.close()


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--strata", action="store_true")
    args = ap.parse_args()
    store.init_db()
    if args.report:
        report()
    elif args.strata:
        strata()
    else:
        s = build()
        print(" ".join(f"{k}={v:,}" for k, v in s.items()))
        print()
        report()


if __name__ == "__main__":
    main()
