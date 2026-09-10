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
    with store.db() as c:
        stale = c.execute(
            f"SELECT COUNT(*) FROM quotes WHERE ts < ? AND source IN ({ph})",
            (cutoff, *srcs)).fetchone()[0]
        total = c.execute("SELECT COUNT(*) FROM quotes").fetchone()[0]
        protected = c.execute(
            f"SELECT COUNT(*) FROM quotes WHERE ts < ? AND source NOT IN ({ph})",
            (cutoff, *srcs)).fetchone()[0]
        if stale and not dry_run:
            c.execute(f"DELETE FROM quotes WHERE ts < ? AND source IN ({ph})",
                      (cutoff, *srcs))

    stats = {"deleted": 0 if dry_run else stale, "candidates": stale,
             "protected": protected,
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
        f"protected) older than "
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
