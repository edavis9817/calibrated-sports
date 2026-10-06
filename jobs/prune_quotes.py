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
from array import array

import config
import store

SOURCE = "prune_quotes"


def cutoff_ts(days: float = None, now: float = None) -> float:
    days = config.QUOTES_RETENTION_DAYS if days is None else days
    return (now or time.time()) - days * 86400


def _candidate_ids(c, cutoff, srcs, held_sql, held_args):
    """Ids of every prunable row, read WITHOUT the write lock.

    Two index-shaped branches rather than `COALESCE(ingest_ts, ts) < ?`: the
    COALESCE form cannot use `ix_quotes_ingest (source, ingest_ts)`, so it
    walks the whole table, and inside a DELETE it did that walk while HOLDING
    the write lock. Same rows, same rule: a row with an ingest_ts is aged on
    it, and a row from before the column existed is aged on ts.
    """
    ph = ",".join("?" for _ in srcs)
    ids = array("q")
    for age_sql in ("ingest_ts < ?", "ingest_ts IS NULL AND ts < ?"):
        for (i,) in c.execute(
                f"SELECT id FROM quotes WHERE source IN ({ph}) AND {age_sql} "
                f"{held_sql}", (*srcs, cutoff, *held_args)):
            ids.append(i)
    return ids


def delete_sql(marks: str, ph: str, held_sql: str) -> str:
    """One batch: these ids, and only those of them the rule still condemns.

    `+source` is not decoration. Written as a bare `source IN (...)` the planner
    takes `ix_quotes_ingest (source=?)` and walks EVERY live row to test it
    against the id list - measured on the live store 2026-10-06: 91 batches of
    500 held the write lock for 4.1 s each on average and 5.9 s at worst,
    376.9 s in all for 45,254 rows. The unary plus takes that index off the
    table for this term, COALESCE already cannot use one, and what is left is
    the primary key. tests/test_logger_survives_lock.py reads the plan.
    """
    return (f"DELETE FROM quotes WHERE id IN ({marks}) "
            f"AND COALESCE(ingest_ts, ts) < ? AND +source IN ({ph}) {held_sql}")


def run(days: float = None, dry_run: bool = False, vacuum: bool = None,
        batch: int = None, pause_s: float = None, max_seconds: float = None) -> dict:
    cutoff = cutoff_ts(days)
    vacuum = config.QUOTES_PRUNE_VACUUM if vacuum is None else vacuum
    batch = config.QUOTES_PRUNE_BATCH if batch is None else batch
    pause_s = config.QUOTES_PRUNE_PAUSE_S if pause_s is None else pause_s
    max_seconds = (config.QUOTES_PRUNE_MAX_SECONDS if max_seconds is None
                   else max_seconds)
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
    # DELETE.
    held_sql = ("AND NOT EXISTS (SELECT 1 FROM quote_retention_hold h "
                "WHERE h.venue = quotes.venue AND h.market_id = quotes.market_id "
                "AND h.until_ts > ?)")
    held_args = (time.time(),)

    # --- 1. READ. Nothing in this block takes the write lock, and the
    # connection is closed before the first delete so no read snapshot is held
    # open across the batches (an open reader pins the WAL).
    t_read = time.time()
    with store.db() as c:
        # Degrade, never die: this runs inside the logger's maintenance loop, so
        # a store predating the table must not take the loop down with it.
        if not c.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                         "AND name='quote_retention_hold'").fetchone():
            held_sql, held_args = "", ()
        ids = _candidate_ids(c, cutoff, srcs, held_sql, held_args)
        stale = len(ids)
        # Held = aged rows the read above did NOT return: they are exact
        # complements, so this is a subtraction. Counting them with their own
        # EXISTS walked every aged row a second time - 5.58M row lookups on the
        # live store - and the two counts below are answered from the index.
        aged = sum(c.execute(
            f"SELECT COUNT(*) FROM quotes WHERE source IN ({ph}) AND {a}",
            (*srcs, cutoff)).fetchone()[0]
            for a in ("ingest_ts < ?", "ingest_ts IS NULL AND ts < ?"))
        held = aged - stale
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
    read_s = time.time() - t_read

    # --- 2. DELETE, in small committed batches (a-67). One DELETE in one
    # transaction held the write lock for over a minute (61-84 s of stalled
    # polling beside five consecutive prunes on 2026-10-04) and the 30 s busy
    # timeout of every other writer expired inside it; that is what killed the
    # logger twice. A batch is `batch` rows by PRIMARY KEY, its own transaction,
    # and a pause after the commit so a waiting writer gets in.
    #
    # The id list only says WHERE to look. Every batch re-applies the whole
    # rule - source, age, hold - so a row that gained a hold, or that is not
    # what the read saw, is not deleted on the strength of a stale list.
    deleted, batches, max_batch_s, delete_s = 0, 0, 0.0, 0.0
    unfinished = 0
    if stale and not dry_run:
        t_del = time.time()
        conn = store._conn()
        try:
            for i in range(0, stale, batch):
                if max_seconds and time.time() - t_del > max_seconds:
                    unfinished = stale - i        # the next pass re-reads them
                    break
                chunk = ids[i:i + batch].tolist()
                marks = ",".join("?" for _ in chunk)
                t0 = time.time()
                cur = conn.execute(delete_sql(marks, ph, held_sql),
                                   (*chunk, cutoff, *srcs, *held_args))
                conn.commit()
                took = time.time() - t0
                deleted += cur.rowcount
                batches += 1
                max_batch_s = max(max_batch_s, took)
                if pause_s:
                    time.sleep(pause_s)
        finally:
            conn.close()
        delete_s = time.time() - t_del

    stats = {"deleted": deleted, "candidates": stale,
             "protected": protected, "held": held, "saved_by_ingest_ts": reparsed,
             "remaining": total - deleted,
             "cutoff": cutoff, "bytes_before": before, "bytes_after": before,
             "batches": batches, "batch_size": batch,
             "max_batch_s": round(max_batch_s, 3), "read_s": round(read_s, 2),
             "delete_s": round(delete_s, 2), "unfinished": unfinished}

    if vacuum and deleted and not dry_run:
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
        f"{stats['remaining']} remain; {batches} batches of <= {batch}, "
        f"longest write lock {stats['max_batch_s']}s, read {stats['read_s']}s"
        + (f"; {unfinished} left for the next pass" if unfinished else ""),
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
    n = s['candidates'] if args.dry_run else s['deleted']
    print(f"{verb} {n} quotes older than "
          f"{time.strftime('%Y-%m-%d', time.gmtime(s['cutoff']))}, "
          f"{s['remaining']} remain")
    if not args.dry_run:
        print(f"{s['batches']} batches of <= {s['batch_size']}, longest write "
              f"lock {s['max_batch_s']}s, total delete {s['delete_s']}s, "
              f"read {s['read_s']}s, {s['unfinished']} left for the next pass")
    if s["bytes_after"] != s["bytes_before"]:
        print(f"db {s['bytes_before']/1e6:.1f}MB -> {s['bytes_after']/1e6:.1f}MB")


if __name__ == "__main__":
    main()
