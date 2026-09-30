"""Resolve logged venue markets to outcome_ids.

    python -m jobs.map_markets                 # map everything, print coverage
    python -m jobs.map_markets --venue kalshi
    python -m jobs.map_markets --coverage      # report only, no re-mapping
    python -m jobs.map_markets --unmapped 20   # a sample to work from
    python -m jobs.map_markets --venue oddsapi # the live book-prop join (a-53)
    python -m jobs.map_markets --venue polymarket   # player props LINK only (a-60)

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


def run(venue=None, limit=None, create_book_outcomes=False,
        create_poly_player_outcomes=False) -> dict:
    stats = Counter()
    reasons = Counter()
    t0 = time.time()
    for row in _rows(venue, limit):
        v = row["venue"]
        if v.startswith("oddsapi:"):
            continue                # the book-prop join, batched below
        # oddsapi venue names carry the book: "oddsapi:pinnacle"
        fn = MAPPERS.get(v.split(":")[0])
        if fn is None:
            store.record_mapping(v, row["market_id"],
                                 unmapped_reason="no mapper for venue")
            stats["no_mapper"] += 1
            continue
        try:
            if v == "polymarket":
                outcome_id, method, conf = fn(
                    row, create_players=create_poly_player_outcomes)
            else:
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
    if venue in (None, "oddsapi") and not limit:
        # AFTER the exchanges, so a Kalshi rung's outcome exists before the
        # book line for the same claim looks for it.
        book = run_book_props(create=create_book_outcomes)
        for k, n in book["census"].items():
            stats[f"oddsapi_props:{k}"] += n
        reasons.update(book["reasons"])
    stats["elapsed"] = round(time.time() - t0, 1)
    store.record_health("mapping", stats.get("kalshi:error", 0) == 0,
                        ", ".join(f"{k}={v}" for k, v in sorted(stats.items())),
                        watermark=time.time())
    return {"stats": stats, "reasons": reasons}


def run_book_props(create=False, event_ids=None) -> dict:
    """The live Odds API prop join (a-53): derive one `markets` row per (book,
    prop, line) from the quote log, then link each to its outcome.

    Linking only, by default. A book line whose claim has no `outcomes` row -
    most of all a stat no exchange lists - is recorded as `book-only claim
    <outcome_id>` rather than created, because every player outcome that gets
    settled enters the prop history `jobs/export_web.py` publishes. Creating
    them (`create=True`, `--create-book-outcomes`) is a decision about what the
    site shows, and it is not this job's to take.
    """
    con = sqlite3.connect(f"file:{config.DB_PATH}?mode=ro", uri=True)
    try:
        rows, census = oddsapi.derive_prop_markets(con, event_ids)
        existing = {r[0] for r in con.execute(
            "SELECT outcome_id FROM outcomes WHERE entity_type = 'player'")}
    finally:
        con.close()
    mappings, new, mcensus = oddsapi.map_prop_rows(rows, existing, create)
    store.upsert_prop_markets(rows)
    if create and new:
        store.upsert_outcomes(new)
    store.record_mappings(mappings)
    census.update(mcensus)
    reasons = Counter()
    for m in mappings:
        if m[5] and not m[5].startswith(oddsapi.BookOnly.PREFIX):
            reasons[f"oddsapi props: {m[5][:70]}"] += 1
    ok = census["rows"] == 0 or census["linked"] + census["book_only"] > 0
    store.record_health("mapping_oddsapi_props", ok,
                        ", ".join(f"{k}={v}" for k, v in sorted(census.items())),
                        watermark=time.time())
    return {"census": census, "reasons": reasons}


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
    ap.add_argument("--create-book-outcomes", action="store_true",
                    help="create outcomes for book-only prop lines; these then "
                         "settle into the PUBLISHED prop history (a-53)")
    ap.add_argument("--create-poly-player-outcomes", action="store_true",
                    help="create outcomes for Polymarket-only player lines; these "
                         "then settle into the PUBLISHED prop history (a-60)")
    args = ap.parse_args()

    store.init_db()
    if args.coverage:
        coverage()
        return
    if args.unmapped:
        unmapped(args.unmapped)
        return

    res = run(args.venue, args.limit, args.create_book_outcomes,
              args.create_poly_player_outcomes)
    print(f"mapped in {res['stats']['elapsed']}s\n")
    coverage()
    print()
    for reason, c in res["reasons"].most_common(8):
        print(f"  {c:>6,}  {reason}")


if __name__ == "__main__":
    main()
