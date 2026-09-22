"""Collapse duplicated nflverse_versions rows and make the key unique.

    python -m jobs.migrate_nflverse_versions --db PATH                 # dry run
    python -m jobs.migrate_nflverse_versions --db PATH --raw-dir DIR   # + disk check
    python -m jobs.migrate_nflverse_versions --db PATH --apply

WHY. `season` is NULL for an all-season file (games, players, ngs_*, teams) and
is part of the primary key. SQLite treats NULLs in a primary key as distinct,
so `ON CONFLICT(dataset, season, data_version)` never fired for those files and
every same-day re-pull inserted another row. Measured 2026-09-22 (unit a-03)
read-only on the live store: 332 rows, 140 NULL-season, 27 keys duplicated, 71
surplus rows; 0 duplicates among the 192 seasoned rows. `store.record_version`
no longer relies on the key; this collapses what it already wrote.

WHAT IT DOES, in one transaction:
  1. every row that is not the NEWEST (highest rowid) for its
     (dataset, season, data_version) is COPIED to `nflverse_versions_superseded`
     with the rowid it lost to - nothing is dropped, it moves;
  2. those rows are removed from `nflverse_versions`;
  3. `ux_nflv_key` is created: UNIQUE on (dataset, IFNULL(season, -1),
     data_version), so a regression raises IntegrityError instead of quietly
     inserting. -1 is never a real season.

The newest row is the one to keep because it is the one whose sha matches the
bytes on disk: `archive_file` overwrites a day's file, so only the last pull
of a day is still in the archive. Checked on all 27 live duplicated keys;
`--raw-dir` re-checks it on whatever database it is pointed at.

`--db` has no default on purpose: this is a schema change, and the path it
runs against is a decision, not a config lookup. Idempotent - a second run
finds nothing to move and the index already present.
"""
import argparse
import hashlib
import os
import sqlite3
import sys
import time

COLS = ("dataset", "season", "data_version", "sha256", "bytes", "rel_path",
        "rows", "tier", "ingested_ts", "last_checked_ts")

SUPERSEDED_DDL = """
CREATE TABLE IF NOT EXISTS nflverse_versions_superseded (
    dataset       TEXT NOT NULL,
    season        INTEGER,
    data_version  TEXT NOT NULL,
    sha256        TEXT NOT NULL,
    bytes         INTEGER,
    rel_path      TEXT,
    rows          INTEGER,
    tier          TEXT,
    ingested_ts   REAL NOT NULL,
    last_checked_ts REAL,
    old_rowid     INTEGER NOT NULL,   -- its rowid in nflverse_versions
    kept_rowid    INTEGER NOT NULL,   -- the row that stayed for this key
    superseded_ts REAL NOT NULL
)"""

UNIQUE_DDL = ("CREATE UNIQUE INDEX IF NOT EXISTS ux_nflv_key ON "
              "nflverse_versions(dataset, IFNULL(season, -1), data_version)")

# Every row that is not the newest for its key, with the rowid that is.
SURPLUS = """
SELECT v.rowid, k.kept, {cols}
FROM nflverse_versions v
JOIN (SELECT dataset, IFNULL(season, -1) AS s, data_version, MAX(rowid) AS kept
      FROM nflverse_versions GROUP BY 1, 2, 3) k
  ON k.dataset = v.dataset AND k.s = IFNULL(v.season, -1)
 AND k.data_version = v.data_version
WHERE v.rowid <> k.kept
ORDER BY v.dataset, v.data_version, v.rowid
""".format(cols=", ".join(f"v.{c}" for c in COLS))


def plan(con):
    """-> (surplus rows, {(dataset, season): n}, index_present)."""
    surplus = con.execute(SURPLUS).fetchall()
    by = {}
    for r in surplus:
        k = (r[2], r[3])
        by[k] = by.get(k, 0) + 1
    present = con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='index' AND name='ux_nflv_key'"
    ).fetchone() is not None
    return surplus, by, present


def disk_check(con, raw_dir):
    """Kept rows (newest per key) whose sha256 does not match the archived
    bytes -> (checked, [mismatch...], [missing...]). Reads every file once."""
    rows = con.execute(
        "SELECT dataset, season, data_version, sha256, rel_path "
        "FROM nflverse_versions WHERE rowid IN (SELECT MAX(rowid) FROM "
        "nflverse_versions GROUP BY dataset, IFNULL(season, -1), data_version)"
    ).fetchall()
    bad, missing = [], []
    for ds, season, ver, sha, rel in rows:
        p = os.path.join(raw_dir, *(rel or "").split("/"))
        if not rel or not os.path.exists(p):
            missing.append((ds, season, ver))
            continue
        with open(p, "rb") as f:
            if hashlib.sha256(f.read()).hexdigest() != sha:
                bad.append((ds, season, ver))
    return len(rows), bad, missing


def migrate(db_path, apply=False, raw_dir=None):
    if not os.path.exists(db_path):
        raise FileNotFoundError(f"no database at {db_path} - refusing to create one")
    uri = f"file:{db_path}" + ("" if apply else "?mode=ro")
    con = sqlite3.connect(uri, uri=True, isolation_level=None)
    try:
        before = con.execute("SELECT COUNT(*) FROM nflverse_versions").fetchone()[0]
        surplus, by, present = plan(con)
        out = {"db": db_path, "applied": False, "rows_before": before,
               "surplus": len(surplus), "keys_with_surplus": len(
                   {(r[2], r[3], r[4]) for r in surplus}),
               "by_dataset": by, "index_present_before": present}
        if raw_dir:
            n, bad, missing = disk_check(con, raw_dir)
            out.update(kept_checked=n, kept_sha_mismatch=bad,
                       kept_file_missing=len(missing))
        if not apply:
            return out
        con.execute("BEGIN IMMEDIATE")
        try:
            con.execute(SUPERSEDED_DDL)
            now = time.time()
            con.executemany(
                f"INSERT INTO nflverse_versions_superseded "
                f"({', '.join(COLS)}, old_rowid, kept_rowid, superseded_ts) "
                f"VALUES ({', '.join('?' * (len(COLS) + 3))})",
                [tuple(r[2:]) + (r[0], r[1], now) for r in surplus])
            con.executemany("DELETE FROM nflverse_versions WHERE rowid = ?",
                            [(r[0],) for r in surplus])
            con.execute(UNIQUE_DDL)
            after = con.execute("SELECT COUNT(*) FROM nflverse_versions").fetchone()[0]
            if after != before - len(surplus):
                raise RuntimeError(f"row count {after} != {before} - {len(surplus)}")
            con.execute("COMMIT")
        except BaseException:
            con.execute("ROLLBACK")
            raise
        out.update(applied=True, rows_after=after, moved=len(surplus))
        return out
    finally:
        con.close()


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--db", required=True, help="the SQLite store to migrate")
    ap.add_argument("--apply", action="store_true",
                    help="write; without it this is a read-only dry run")
    ap.add_argument("--raw-dir", help="check kept rows' sha256 against the archive")
    a = ap.parse_args()
    r = migrate(a.db, apply=a.apply, raw_dir=a.raw_dir)
    for k, v in r.items():
        print(f"  {k:22s} {v}")
    if a.raw_dir and r.get("kept_sha_mismatch"):
        print("  WARNING: some kept rows do not match the bytes on disk")
    print("applied" if r["applied"] else "dry run - nothing written (pass --apply)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
