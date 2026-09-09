"""Resolve logged venue markets to outcome_ids.

    python -m jobs.map_markets                 # map everything, print coverage
    python -m jobs.map_markets --venue kalshi
    python -m jobs.map_markets --coverage      # report only, no re-mapping
    python -m jobs.map_markets --unmapped 20   # a sample to work from

EVERY market gets a market_outcome row. A market that could not be resolved is
recorded WITH ITS REASON, never dropped: a coverage number computed over the
markets you managed to parse is not a coverage number, and the unmapped list
grouped by reason is the queue that tells you which mapper to improve next.
"""
import argparse
import sqlite3
import time
from collections import Counter

import config
import store
from venues import kalshi, oddsapi, polymarket
from venues.mapping import Unresolved

MAPPERS = {
    "kalshi": kalshi.map_market,
    "polymarket": polymarket.map_market,
    "oddsapi": oddsapi.map_market,
}


def _rows(venue=None, limit=None):
    con = sqlite3.connect(f"file:{config.DB_PATH}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    q = "SELECT * FROM markets"
    args = []
    if venue:
        q += " WHERE venue = ?"
        args.append(venue)
    if limit:
        q += f" LIMIT {int(limit)}"
    try:
        return [dict(r) for r in con.execute(q, args)]
    finally:
        con.close()


def run(venue=None, limit=None) -> dict:
    stats = Counter()
    reasons = Counter()
    t0 = time.time()
    for row in _rows(venue, limit):
        v = row["venue"]
        # oddsapi venue names carry the book: "oddsapi:pinnacle"
        fn = MAPPERS.get(v.split(":")[0])
        if fn is None:
            store.record_mapping(v, row["market_id"],
                                 unmapped_reason="no mapper for venue")
            stats["no_mapper"] += 1
            continue
        try:
            outcome_id, method, conf = fn(row)
            store.record_mapping(v, row["market_id"], outcome_id, method, conf)
            stats[f"{v}:mapped"] += 1
        except Unresolved as e:
            store.record_mapping(v, row["market_id"],
                                 unmapped_reason=str(e)[:200])
            stats[f"{v}:unmapped"] += 1
            reasons[f"{v}: {str(e)[:70]}"] += 1
        except Exception as e:                      # a mapper bug, not bad data
            store.record_mapping(v, row["market_id"],
                                 unmapped_reason=f"MAPPER ERROR {type(e).__name__}: {e}"[:200])
            stats[f"{v}:error"] += 1
            reasons[f"{v}: MAPPER ERROR {type(e).__name__}: {e}"[:70]] += 1
    stats["elapsed"] = round(time.time() - t0, 1)
    store.record_health("mapping", stats.get("kalshi:error", 0) == 0,
                        ", ".join(f"{k}={v}" for k, v in sorted(stats.items())),
                        watermark=time.time())
    return {"stats": stats, "reasons": reasons}


def coverage():
    """Per venue and per market type, printed. The acceptance number."""
    con = sqlite3.connect(f"file:{config.DB_PATH}?mode=ro", uri=True)
    rows = con.execute("""
        SELECT m.venue, m.market_type,
               COUNT(*)                                   AS n,
               SUM(mo.outcome_id IS NOT NULL)             AS mapped
          FROM markets m
          LEFT JOIN market_outcome mo
            ON mo.venue = m.venue AND mo.market_id = m.market_id
         GROUP BY m.venue, m.market_type
         ORDER BY m.venue, n DESC
    """).fetchall()
    print(f"{'venue':<12} {'market_type':<12} {'markets':>8} {'mapped':>8} {'cov':>7}")
    tot = Counter()
    for venue, mtype, n, mapped in rows:
        mapped = mapped or 0
        tot[venue] += n
        tot[venue + ":m"] += mapped
        print(f"{venue:<12} {str(mtype):<12} {n:>8,} {mapped:>8,} "
              f"{mapped/n if n else 0:>7.1%}")
    print()
    for venue in sorted({v for v, _, _, _ in rows}):
        n, m = tot[venue], tot[venue + ":m"]
        print(f"{venue:<12} {'TOTAL':<12} {n:>8,} {m:>8,} {m/n if n else 0:>7.1%}")

    n_out = con.execute("SELECT COUNT(*) FROM outcomes").fetchone()[0]
    shared = con.execute("""
        SELECT COUNT(*) FROM (
            SELECT outcome_id FROM market_outcome
             WHERE outcome_id IS NOT NULL
             GROUP BY outcome_id HAVING COUNT(DISTINCT venue) > 1)
    """).fetchone()[0]
    print(f"\ndistinct outcomes: {n_out:,}")
    print(f"outcomes quoted on more than one venue: {shared:,}"
          "   <- the cross-venue join, which is the point")
    con.close()


def unmapped(n=20):
    con = sqlite3.connect(f"file:{config.DB_PATH}?mode=ro", uri=True)
    print("unmapped by reason:")
    for reason, c in con.execute("""
            SELECT unmapped_reason, COUNT(*) c FROM market_outcome
             WHERE outcome_id IS NULL GROUP BY 1 ORDER BY c DESC LIMIT 12"""):
        print(f"  {c:>6,}  {str(reason)[:96]}")
    print(f"\nsample of {n} unmapped markets:")
    for venue, mid, reason in con.execute("""
            SELECT mo.venue, mo.market_id, mo.unmapped_reason
              FROM market_outcome mo WHERE mo.outcome_id IS NULL
             ORDER BY RANDOM() LIMIT ?""", (n,)):
        title = con.execute("SELECT title FROM markets WHERE venue=? AND market_id=?",
                            (venue, mid)).fetchone()
        print(f"  {venue:<11} {str(title[0] if title else '')[:56]:<56} "
              f"{str(reason)[:60]}")
    con.close()


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--venue")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--coverage", action="store_true")
    ap.add_argument("--unmapped", type=int, nargs="?", const=20)
    args = ap.parse_args()

    store.init_db()
    if args.coverage:
        coverage()
        return
    if args.unmapped:
        unmapped(args.unmapped)
        return

    res = run(args.venue, args.limit)
    print(f"mapped in {res['stats']['elapsed']}s\n")
    coverage()
    print()
    for reason, c in res["reasons"].most_common(8):
        print(f"  {c:>6,}  {reason}")


if __name__ == "__main__":
    main()
