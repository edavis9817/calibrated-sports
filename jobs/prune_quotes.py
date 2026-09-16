"""Prune quote rows older than the retention window.

    python -m jobs.prune_quotes                # keep QUOTES_RETENTION_DAYS
    python -m jobs.prune_quotes --days 30
    python -m jobs.prune_quotes --dry-run
    python -m jobs.prune_quotes --vacuum       # actually give the space back

This is the ONE table that may be deleted from, and only because it is derived:
every quote row was parsed out of an archived payload, so the raw archive - on
disk for RAW_ROTATE_DAYS, in R2 after that - can re-produce any window of it.
Deleting a quote row loses nothing that is not recoverable. That is not true of
anything under data/raw, which is why rotation moves shards and this deletes.

    quotes    = derived,  prunable,   recovery = re-parse the archive
    raw/      = source,   never cut,  rotated to R2 and manifested
    markets   = catalogue, tiny, kept
    poll_log  = the coverage record; kept, it is how gaps stay visible

RETENTION KEYS ON INGESTION TIME. `quotes.ts` is when the price EXISTED; a
backfilled row is old by definition, so a window on `ts` deletes paid history
the moment it lands. It did: this job destroyed brief 009's entire 806-credit
Odds API pilot, 76 days of Kalshi candles and 17 days of Polymarket history
before anyone noticed, because every one of those rows carries an event
timestamp from months or years ago. Two rules, and both are load-bearing:

    1. only sources in QUOTES_PRUNE_SOURCES are prunable at all; and
    2. their age is measured on `ingest_ts`, never on `ts`.

Rule 1 alone is not enough - it fails silently the first time someone adds a
source and forgets to list it. Rule 2 alone is not enough either - it would
still delete a paid backfill, just fourteen days later instead of instantly.

SQLite does not return deleted pages to the filesystem without a VACUUM, and a
VACUUM needs room for a full copy of the database plus an exclusive lock, so it
is opt-in rather than automatic. Without it the file stops growing but does not
shrink - which is usually the behaviour you want on a box that is still logging.
"""
import argparse
import os
import time

import config
import store

SOURCE = "prune_quotes"


def cutoff_ts(days: float = None, now: float = None) -> float:
    days = config.QUOTES_RETENTION_DAYS if days is None else days
    return (now or time.time()) - days * 86400


def run(days: float = None, dry_run: bool = False, vacuum: bool = None) -> dict:
    cutoff = cutoff_ts(days)
    vacuum = config.QUOTES_PRUNE_VACUUM if vacuum is None else vacuum
    before = os.path.getsize(config.DB_PATH) if os.path.exists(config.DB_PATH) else 0

    # ONLY live capture is prunable. See config.QUOTES_PRUNE_SOURCES.
    srcs = config.QUOTES_PRUNE_SOURCES
    ph = ",".join("?" for _ in srcs)
    # COALESCE for rows written before the column existed. Those are live rows
    # whose ts and ingest_ts are the same instant anyway; a backfilled row from
    # that era would have been deleted long before this code ran.
    age = "COALESCE(ingest_ts, ts)"
    # Rows whose market is still being SHOWN somewhere are held back, however
    # old they are. jobs/export_web.py writes a row per published market and
    # renews it every run; an unrenewed hold expires and the row prunes
    # normally. NOT EXISTS rather than NOT IN or a LEFT JOIN: measured on a 4M
    # row store with 1M stale rows, NOT EXISTS 5.98s and LEFT JOIN 5.66s are a
    # wash while NOT IN builds a bloom filter and scans the hold table (13.02s)
    # - and NOT EXISTS is the only one of the three expressible directly in the
    # DELETE, which keeps this a single statement.
    held_sql = ("AND NOT EXISTS (SELECT 1 FROM quote_retention_hold h "
                "WHERE h.venue = quotes.venue AND h.market_id = quotes.market_id "
                "AND h.until_ts > ?)")
    held_args = (time.time(),)
    with store.db() as c:
        # Degrade, never die: this runs inside the logger's maintenance loop, so
        # a store predating the table must not take the loop down with it.
        if not c.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                         "AND name='quote_retention_hold'").fetchone():
            held_sql, held_args = "", ()
        stale = c.execute(
            f"SELECT COUNT(*) FROM quotes WHERE {age} < ? "
            f"AND source IN ({ph}) {held_sql}", (cutoff, *srcs, *held_args)).fetchone()[0]
        held = 0
        if held_sql:
            held = c.execute(
                f"SELECT COUNT(*) FROM quotes WHERE {age} < ? AND source IN ({ph}) "
                f"AND EXISTS (SELECT 1 FROM quote_retention_hold h "
                f"WHERE h.venue = quotes.venue AND h.market_id = quotes.market_id "
                f"AND h.until_ts > ?)", (cutoff, *srcs, *held_args)).fetchone()[0]
        total = c.execute("SELECT COUNT(*) FROM quotes").fetchone()[0]
        # Deliberately keyed on `ts`, not on age: this is the count of rows a
        # naive ts-keyed window WOULD have destroyed, which is the number worth
        # printing. It is the size of the crater rule 1 is standing in front of.
        protected = c.execute(
            f"SELECT COUNT(*) FROM quotes WHERE ts < ? "
            f"AND source NOT IN ({ph})", (cutoff, *srcs)).fetchone()[0]
        # How many rows rule 2 alone saves - live rows whose PRICE is older
        # than the window but which were written recently. Non-zero here means
        # something restored or re-parsed live capture, and a ts-keyed window
        # would have thrown it away again.
        reparsed = c.execute(
            f"SELECT COUNT(*) FROM quotes WHERE ts < ? AND {age} >= ? "
            f"AND source IN ({ph})", (cutoff, cutoff, *srcs)).fetchone()[0]
        if stale and not dry_run:
            c.execute(f"DELETE FROM quotes WHERE {age} < ? "
                      f"AND source IN ({ph}) {held_sql}", (cutoff, *srcs, *held_args))

    stats = {"deleted": 0 if dry_run else stale, "candidates": stale,
             "protected": protected, "held": held, "saved_by_ingest_ts": reparsed,
             "remaining": total - (0 if dry_run else stale),
             "cutoff": cutoff, "bytes_before": before, "bytes_after": before}

    if vacuum and stale and not dry_run:
        # Separate connection: VACUUM cannot run inside a transaction.
        conn = store._conn()
        try:
            conn.isolation_level = None
            conn.execute("VACUUM")
        finally:
            conn.close()
        stats["bytes_after"] = os.path.getsize(config.DB_PATH)

    store.record_health(
        SOURCE, True,
        f"pruned {stats['deleted']} live quotes ({protected} historical rows "
        f"protected, {held} held for the site) older than "
        f"{config.QUOTES_RETENTION_DAYS if days is None else days:g}d, "
        f"{stats['remaining']} remain",
        watermark=time.time())
    return stats


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--days", type=float, default=None,
                    help=f"retention window (default "
                         f"{config.QUOTES_RETENTION_DAYS:g})")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--vacuum", action="store_true",
                    help="reclaim the freed pages; needs room for a full copy")
    args = ap.parse_args()

    store.init_db()
    s = run(days=args.days, dry_run=args.dry_run,
            vacuum=True if args.vacuum else None)
    verb = "would delete" if args.dry_run else "deleted"
    print(f"{verb} {s['candidates']} quotes older than "
          f"{time.strftime('%Y-%m-%d', time.gmtime(s['cutoff']))}, "
          f"{s['remaining']} remain")
    if s["bytes_after"] != s["bytes_before"]:
        print(f"db {s['bytes_before']/1e6:.1f}MB -> {s['bytes_after']/1e6:.1f}MB")


if __name__ == "__main__":
    main()
