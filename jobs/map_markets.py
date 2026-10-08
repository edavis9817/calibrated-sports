"""Resolve logged venue markets to outcome_ids.

    python -m jobs.map_markets                 # map everything, print coverage
    python -m jobs.map_markets --venue kalshi
    python -m jobs.map_markets --coverage      # report only, no re-mapping
    python -m jobs.map_markets --unmapped 20   # a sample to work from
    python -m jobs.map_markets --venue oddsapi # the live book-prop join (a-53)
    python -m jobs.map_markets --venue polymarket   # player props LINK only (a-60)
    python -m jobs.map_markets --pending       # only what is not mapped yet (a-68)

EVERY market gets a market_outcome row. A market that could not be resolved is
recorded WITH ITS REASON, never dropped: a coverage number computed over the
markets you managed to parse is not a coverage number, and the unmapped list
grouped by reason is the queue that tells you which mapper to improve next.

MAPPING RUNS ON THE LISTING'S CLOCK, NOT THE WEEK'S (a-68). The full pass above
ran only inside `jobs.weekly_refresh`, at 13:00Z on Tuesday, Wednesday and
Thursday. Kalshi lists the bulk of a week's player props at about 17:00Z on
THURSDAY - four hours after the last run of the week - and goes on listing
through Sunday, so most of a slate was first mapped the following Tuesday,
after every game it priced had been played: 95 of 1,482 week-2 props and 87 of
1,589 week-4 props had an outcome before their own kickoff. Week 3 was mapped
in time only because units happened to run the job by hand. Nothing was broken
in discovery, in the mapper or in the join, which is why no reason census ever
showed it: a market that has not been LOOKED AT has no reason row at all.
`run_pending` maps what has no row yet and is called by the logger on its own
timer (`config.MAP_PENDING_EVERY`), so the lag is minutes and needs no
scheduler entry to stay that way.
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


def map_row(row, create_poly_player_outcomes=False):
    """One `markets` row -> a `store.record_mappings` tuple plus its census key.

    The single copy of the per-row rule: the full pass and the pending pass
    both call it, so a market cannot resolve one way on Tuesday and another on
    Thursday. Returns ((venue, market_id, outcome_id, method, confidence,
    unmapped_reason), kind) with kind in mapped | unmapped | error | no_mapper.
    """
    v, mid = row["venue"], row["market_id"]
    # oddsapi venue names carry the book: "oddsapi:pinnacle"
    fn = MAPPERS.get(v.split(":")[0])
    if fn is None:
        return (v, mid, None, None, None, "no mapper for venue"), "no_mapper"
    try:
        if v == "polymarket":
            outcome_id, method, conf = fn(
                row, create_players=create_poly_player_outcomes)
        else:
            outcome_id, method, conf = fn(row)
        return (v, mid, outcome_id, method, conf, None), "mapped"
    except Unresolved as e:
        return (v, mid, None, None, None, str(e)[:200]), "unmapped"
    except Exception as e:                      # a mapper bug, not bad data
        return (v, mid, None, None, None,
                f"MAPPER ERROR {type(e).__name__}: {e}"[:200]), "error"


def run(venue=None, limit=None, create_book_outcomes=False,
        create_poly_player_outcomes=False) -> dict:
    stats = Counter()
    reasons = Counter()
    t0 = time.time()
    for row in _rows(venue, limit):
        v = row["venue"]
        if v.startswith("oddsapi:"):
            continue                # the book-prop join, batched below
        m, kind = map_row(row, create_poly_player_outcomes)
        store.record_mapping(*m)
        if kind == "no_mapper":
            stats["no_mapper"] += 1
            continue
        stats[f"{v}:{kind}"] += 1
        if kind == "unmapped":
            reasons[f"{v}: {m[5][:70]}"] += 1
        elif kind == "error":
            reasons[f"{v}: {m[5]}"[:70]] += 1
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


# --- the pending pass (a-68) --------------------------------------------------
# Kalshi BEFORE Polymarket BEFORE the books, for the reason the full pass runs
# them in that order: a Polymarket player line and a book line only ever LINK
# to an outcome an exchange rung created.
PENDING_VENUES = ("kalshi", "polymarket")
# An unmapped row is looked at again only when its reason says "the claim is
# known and its outcome does not exist YET" and the venue still lists it. Every
# other reason is a property of the market and re-reading it changes nothing.
RETRY_PREFIXES = (polymarket.PolyOnly.PREFIX,)
RETRY_LISTED_WITHIN = 86400.0
# Book lines are re-joined for games from a day ago to eight days out: the
# slate being priced now, not the whole season's event list.
BOOK_BEHIND, BOOK_AHEAD = 86400.0, 8 * 86400.0


def pending_rows(venues=None, now=None):
    """`markets` rows the mapper has not looked at, plus the link-only waits.

    No row in `market_outcome` at all is the state the weekly pass left most of
    a slate in until the following Tuesday. Venue order is preserved. A row
    being looked at AGAIN carries `_waiting` = 1.
    """
    venues = PENDING_VENUES if venues is None else venues
    now = time.time() if now is None else now
    con = sqlite3.connect(f"file:{config.DB_PATH}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    retry = " OR ".join("mo.unmapped_reason LIKE ?" for _ in RETRY_PREFIXES)
    try:
        out = []
        for v in venues:
            out += [dict(r) for r in con.execute(
                f"""SELECT m.*, mo.market_id IS NOT NULL AS _waiting
                      FROM markets m
                      LEFT JOIN market_outcome mo
                        ON mo.venue = m.venue AND mo.market_id = m.market_id
                     WHERE m.venue = ?
                       AND (mo.market_id IS NULL
                            OR (mo.outcome_id IS NULL AND m.last_seen >= ?
                                AND ({retry})))
                     ORDER BY m.market_id""",
                (v, now - RETRY_LISTED_WITHIN,
                 *(p + "%" for p in RETRY_PREFIXES)))]
        return out
    finally:
        con.close()


def run_pending(venues=None, book_props=True, now=None) -> dict:
    """Map what is not mapped yet. Idempotent; a second run with nothing new
    writes nothing.

    Returns a census: per venue how many rows were pending and how they
    resolved, and for the books how many links CHANGED. The weekly full pass
    still runs and still re-derives everything; this exists so a market listed
    on Thursday afternoon has an outcome on Thursday afternoon.
    """
    venues = PENDING_VENUES if venues is None else venues
    now = time.time() if now is None else now
    t0 = time.time()
    stats = Counter()
    for v in venues:
        rows = pending_rows((v,), now)
        stats[f"{v}:pending"] += len(rows)
        batch = []
        for row in rows:
            m, kind = map_row(row)
            if row.get("_waiting") and kind != "mapped":
                # still waiting: the stored row already says so, and rewriting
                # it every pass would be a write lock taken to change nothing
                stats[f"{v}:waiting"] += 1
                continue
            batch.append(m)
            stats[f"{v}:{kind}"] += 1
        # per venue, so the next venue's link-only lines see these outcomes
        store.record_mappings(batch)
    if book_props:
        for k, n in run_book_props_pending(now)["census"].items():
            stats[f"oddsapi_props:{k}"] += n
    stats["elapsed"] = round(time.time() - t0, 1)
    errors = sum(n for k, n in stats.items() if k.endswith(":error"))
    store.record_health("mapping_pending", errors == 0,
                        ", ".join(f"{k}={v}" for k, v in sorted(stats.items())),
                        watermark=now)
    return {"stats": stats}


def run_book_props_pending(now=None) -> dict:
    """The book-prop join for the slate in play, writing only what CHANGED.

    Linking only, never creating (see `run_book_props`). A line is written when
    it has no mapping row or when its outcome differs from the stored one -
    which is what happens when the exchange rung it links to is mapped after
    the book first quoted it. Unchanged lines are left alone, so a pass with
    nothing new holds no write lock; their `last_seen` is widened by the weekly
    full pass, not here.
    """
    now = time.time() if now is None else now
    con = sqlite3.connect(f"file:{config.DB_PATH}?mode=ro", uri=True)
    try:
        events = [r[0] for r in con.execute(
            "SELECT event_id FROM markets WHERE venue = 'oddsapi' "
            "AND market_type = 'game' AND close_ts BETWEEN ? AND ?",
            (now - BOOK_BEHIND, now + BOOK_AHEAD))]
        rows, census = oddsapi.derive_prop_markets(con, events)
        existing = {r[0] for r in con.execute(
            "SELECT outcome_id FROM outcomes WHERE entity_type = 'player'")}
        stored = {}
        for i in range(0, len(events), 400):
            chunk = events[i:i + 400]
            stored.update({(v, m): (1, o) for v, m, o in con.execute(
                "SELECT mo.venue, mo.market_id, mo.outcome_id FROM market_outcome mo "
                "JOIN markets m ON m.venue = mo.venue AND m.market_id = mo.market_id "
                "WHERE m.market_type = 'prop' AND m.event_id IN (%s) "
                "AND m.venue LIKE 'oddsapi:%%'" % ",".join("?" * len(chunk)), chunk)})
    finally:
        con.close()
    mappings, _new, mcensus = oddsapi.map_prop_rows(rows, existing, create=False)
    changed = [m for m in mappings if stored.get((m[0], m[1])) != (1, m[2])]
    keys = {(m[0], m[1]) for m in changed}
    store.upsert_prop_markets([r for r in rows if (r["venue"], r["market_id"]) in keys])
    store.record_mappings(changed)
    census.update(mcensus)
    census["changed"] = len(changed)
    census["newly_linked"] = sum(1 for m in changed if m[2] is not None)
    return {"census": census}


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
    ap.add_argument("--pending", action="store_true",
                    help="map only markets with no mapping row yet, then re-join "
                         "the book lines of the slate in play (a-68)")
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

    if args.pending:
        res = run_pending((args.venue,) if args.venue else None,
                          book_props=args.venue is None)
        print(", ".join(f"{k}={v}" for k, v in sorted(res["stats"].items())))
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
