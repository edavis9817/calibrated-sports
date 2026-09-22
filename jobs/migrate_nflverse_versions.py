"""Collapse duplicated nflverse_versions rows and make the key unique.

    python -m jobs.migrate_nflverse_versions --db PATH                 # dry run
    python -m jobs.migrate_nflverse_versions --db PATH --raw-dir DIR   # + disk check
    python -m jobs.migrate_nflverse_versions --db PATH --apply

Runbook: docs/runbooks/nflverse-versions-migration.md. Read its banner first.

WHY. `season` is NULL for an all-season file (games, players, ngs_*, teams) and
is part of the primary key. SQLite treats NULLs in a primary key as distinct,
so `ON CONFLICT(dataset, season, data_version)` never fired for those files and
every same-day re-pull inserted another row. `store.record_version` no longer
relies on the key (unit a-03); this collapses what the old writer already wrote.
THE SIZE OF THAT IS NOT QUOTED HERE ON PURPOSE: the old writer adds rows at every
same-day all-season pull (a-03 measured 71 surplus, c-04 measured 77 six hours
later), so any figure in this file is stale by the time it is read. The dry run
prints the current one; that is the only number to act on.

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
of a day is still in the archive. `--raw-dir` re-checks it on whatever
database it is pointed at.

THE PRECONDITION (unit a-07, found by c-04). Step 3 turns the OLD writer's
behaviour from "quietly insert a duplicate" into "raise IntegrityError" - and
the old `ingest_one` raises it AFTER `archive_file` has overwritten the day's
file, so the ledger's sha stops matching the disk. So `--apply` refuses unless
it can SEE that no writer will run the old upsert:

  * every scheduled task that runs a writer (the logger, weekly_refresh,
    ingest_nflverse) launches from a checkout whose `store.py` declares
    NFLV_WRITER_REV >= 2 - read from the file, never imported;
  * the running logger (if any) is ONE process whose `logger_start` health row
    carries `nflv_writer >= 2` - the revision it LOADED, not the one on disk;
  * no weekly_refresh or ingest_nflverse job is running at that moment;
  * the process list and the task list could both be read at all. A probe
    that fails is a refusal, never a pass.

What it cannot see is in the runbook's banner: a notebook or shell that imports
an old `store`, and ANY FUTURE checkout of a pre-a-03 branch into the
production tree. The index is permanent; the check is a moment.

`--db` has no default on purpose: this is a schema change, and the path it
runs against is a decision, not a config lookup. Idempotent - a second run
finds nothing to move and the index already present.
"""
import argparse
import ast
import hashlib
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
from dataclasses import dataclass, field

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

# The writer revision that is safe against ux_nflv_key. store.NFLV_WRITER_REV.
REQUIRED_REV = 2

# Command-line fragments that identify a process or a scheduled task that can
# write nflverse_versions. Every writer goes through jobs.ingest_nflverse, and
# the only things that call it are these (grep: record_version/touch_version).
LOGGER_MARK = "run_logger"
JOB_MARKS = ("weekly_refresh", "ingest_nflverse")
TASK_MARKS = ("start_logger.ps1", LOGGER_MARK) + JOB_MARKS


# ---- what the old writer left ----------------------------------------------

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


def census(con):
    """The figures a runbook must not hard-code, counted now."""
    g = con.execute(
        "SELECT season IS NULL, COUNT(*) FROM nflverse_versions "
        "GROUP BY dataset, IFNULL(season, -1), data_version").fetchall()
    null = [n for is_null, n in g if is_null]
    seasoned = [n for is_null, n in g if not is_null]
    return {"null_season_rows": sum(null), "null_season_keys": len(null),
            "null_season_dup_keys": sum(1 for n in null if n > 1),
            "null_season_surplus": sum(n - 1 for n in null),
            "seasoned_rows": sum(seasoned),
            "seasoned_surplus": sum(n - 1 for n in seasoned)}


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


# ---- the precondition: is any writer still on the old upsert? --------------

@dataclass
class WriterCheck:
    """The verdict and the statement that justifies it. Refuses truth-testing:
    `if check_writers(...)` would drop the statement, and a bare object is
    always truthy, so `assert check` would assert nothing. Use `.clean`."""
    problems: list = field(default_factory=list)
    notes: list = field(default_factory=list)

    @property
    def clean(self) -> bool:
        return not self.problems

    @property
    def statement(self) -> str:
        head = ("writers: every writer is on the fixed upsert" if self.clean
                else f"writers: REFUSE - {len(self.problems)} problem(s)")
        return "\n".join([head] + [f"  x {p}" for p in self.problems]
                         + [f"  - {n}" for n in self.notes])

    def __bool__(self):
        raise TypeError("WriterCheck has no truth value - read .clean for the "
                        "verdict and .statement for why")


class WritersNotReady(RuntimeError):
    def __init__(self, check):
        super().__init__(check.statement)
        self.check = check


def rev_in_checkout(root):
    """NFLV_WRITER_REV declared by `root/store.py`, read as source (never
    imported - importing a checkout runs its config against this process's
    environment). 1 when the file exists and does not declare it: that is the
    pre-a-03 upsert. None when there is no store.py to read."""
    p = os.path.join(root, "store.py")
    if not os.path.exists(p):
        return None
    with open(p, encoding="utf-8") as f:
        tree = ast.parse(f.read())
    for node in tree.body:
        if (isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and node.targets[0].id == "NFLV_WRITER_REV"
                and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, int)):
            return node.value.value
    return 1


def _is_checkout(d):
    return (os.path.isfile(os.path.join(d, "store.py"))
            and os.path.isfile(os.path.join(d, "jobs", "ingest_nflverse.py")))


def _paths_in(text):
    """Absolute paths named in a task's command text - quoted or not. Drive
    paths are what Task Scheduler holds; POSIX ones are what the tests build
    on CI."""
    out = [q for q in re.findall(r'"([^"]+)"', text or "")]
    out += [t for t in re.split(r"\s+", re.sub(r'"[^"]*"', " ", text or "")) if t]
    return [p for p in out if re.match(r"^[A-Za-z]:[\\/]", p) or p.startswith("/")]


def checkout_of(task):
    """The repository checkout a scheduled task launches from, or None."""
    for cand in [task.get("workdir")] + _paths_in(task.get("execute")) \
            + _paths_in(task.get("args")):
        if not cand:
            continue
        d = cand if os.path.isdir(cand) else os.path.dirname(cand)
        while d and os.path.isdir(d):
            if _is_checkout(d):
                return os.path.normpath(d)
            parent = os.path.dirname(d)
            if parent == d:
                break
            d = parent
    return None


def _task_text(t):
    return " ".join(str(t.get(k) or "") for k in ("execute", "args", "workdir")).lower()


def logger_start(con):
    """-> (pid, nflv_writer rev or None, detail) from the logger's health row,
    or None if there is no row. A pre-a-07 logger writes no nflv_writer."""
    try:
        row = con.execute("SELECT detail FROM source_health "
                          "WHERE source='logger_start'").fetchone()
    except sqlite3.OperationalError:          # no source_health table at all
        return None
    if not row or not row[0]:
        return None
    pid = re.search(r"\bpid (\d+)\b", row[0])
    rev = re.search(r"\bnflv_writer (\d+)\b", row[0])
    return (int(pid.group(1)) if pid else None,
            int(rev.group(1)) if rev else None, row[0])


def check_writers(con, processes, tasks, rev_of=rev_in_checkout, locate=checkout_of):
    """Refuse unless every writer this machine can run is on REQUIRED_REV.

    `processes`: [{pid, ppid, cmd}] for python processes, or None if the
    process list could not be read. `tasks`: [{name, state, execute, args,
    workdir}] for scheduled tasks, or None. Both None-able on purpose: a probe
    that failed is reported as a refusal, never read as "nothing running".
    """
    chk = WriterCheck()
    if processes is None:
        chk.problems.append("could not read the process list - cannot tell "
                            "which writers are running")
    if tasks is None:
        chk.problems.append("could not read the scheduled tasks - cannot tell "
                            "which code the next scheduled writer will load")

    if tasks is not None:
        writers = [t for t in tasks if any(m in _task_text(t) for m in TASK_MARKS)]
        if not writers:
            chk.problems.append(
                "no scheduled task runs a writer (expected the logger and "
                "weekly_refresh) - if they were removed on purpose, this check "
                "has nothing to stand on; re-add them or change this rule")
        for t in writers:
            root = locate(t)
            if root is None:
                chk.problems.append(f"task '{t['name']}': cannot find the "
                                    f"checkout it launches from")
                continue
            rev = rev_of(root)
            if rev is None or rev < REQUIRED_REV:
                chk.problems.append(
                    f"task '{t['name']}' launches from {root}, whose store.py is "
                    f"writer rev {rev} (< {REQUIRED_REV}) - check out a branch "
                    f"containing a-03 there")
            else:
                chk.notes.append(f"task '{t['name']}' -> {root}: rev {rev}")

    if processes is not None:
        jobs = [p for p in processes
                if any(m in (p.get("cmd") or "") for m in JOB_MARKS)]
        for p in jobs:
            chk.problems.append(f"a writer job is running now (pid {p['pid']}: "
                                f"{p['cmd'].strip()[:80]}) - wait for it to exit")
        loggers = [p for p in processes if LOGGER_MARK in (p.get("cmd") or "")]
        # A venv interpreter is a launcher that spawns the real one with the
        # same command line; the real logger is the one no other logger parents.
        parents = {p.get("ppid") for p in loggers}
        live = [p for p in loggers if p["pid"] not in parents]
        started = logger_start(con)
        if len(live) > 1:
            chk.problems.append(f"{len(live)} loggers are running "
                                f"(pids {sorted(p['pid'] for p in live)})")
        elif len(live) == 1:
            pid = live[0]["pid"]
            if started is None:
                chk.problems.append(f"logger pid {pid} is running but there is "
                                    f"no logger_start row naming what it loaded")
            elif started[0] != pid:
                chk.problems.append(
                    f"logger pid {pid} is running but logger_start names pid "
                    f"{started[0]} - the row is not about this process")
            elif started[1] is None or started[1] < REQUIRED_REV:
                chk.problems.append(
                    f"the running logger (pid {pid}) loaded writer rev "
                    f"{started[1] or 'untagged (pre-a-07)'} - restart it from a "
                    f"checkout that has the fix")
            else:
                chk.notes.append(f"logger pid {pid}: loaded rev {started[1]}")
        else:
            chk.notes.append("no logger running - it will load its task's "
                             "checkout, checked above")
        other = [p for p in processes if p not in jobs and p not in loggers]
        if other:
            chk.notes.append(
                f"{len(other)} other python process(es) not classifiable "
                f"(editors, notebooks); one importing an old store is NOT "
                f"detectable - see the runbook banner")
    return chk


PROBE_PS = r"""
$ErrorActionPreference = 'Stop'
$p = @(Get-CimInstance Win32_Process -Filter "Name like 'python%'" | ForEach-Object {
  [pscustomobject]@{pid=[int]$_.ProcessId; ppid=[int]$_.ParentProcessId;
                    cmd=[string]$_.CommandLine} })
$t = @(Get-ScheduledTask | ForEach-Object { $k = $_; foreach ($a in $k.Actions) {
  [pscustomobject]@{name=[string]$k.TaskName; state=[string]$k.State;
                    execute=[string]$a.Execute; args=[string]$a.Arguments;
                    workdir=[string]$a.WorkingDirectory} } })
ConvertTo-Json -Compress -Depth 4 @{processes=$p; tasks=$t}
"""


def probe_system(run=subprocess.run):
    """-> (processes, tasks) read from Windows; either is None if unreadable."""
    try:
        r = run(["powershell", "-NoProfile", "-NonInteractive", "-Command", PROBE_PS],
                capture_output=True, text=True, timeout=120)
        if r.returncode != 0:
            return None, None
        d = json.loads(r.stdout)
    except (OSError, ValueError, subprocess.SubprocessError):
        return None, None
    def as_list(x):                      # ConvertTo-Json flattens a 1-item array
        return [] if x is None else (x if isinstance(x, list) else [x])
    return as_list(d.get("processes")), as_list(d.get("tasks"))


def system_writers(con):
    return check_writers(con, *probe_system())


# ---- the migration ---------------------------------------------------------

def migrate(db_path, apply=False, raw_dir=None, writers=None):
    """`writers(con) -> WriterCheck`. With `apply`, defaults to probing this
    machine and REFUSES (WritersNotReady) unless the check is clean. On a dry
    run it is reported when given and never blocks."""
    if not os.path.exists(db_path):
        raise FileNotFoundError(f"no database at {db_path} - refusing to create one")
    if apply and writers is None:
        writers = system_writers
    uri = f"file:{db_path}" + ("" if apply else "?mode=ro")
    con = sqlite3.connect(uri, uri=True, isolation_level=None)
    try:
        before = con.execute("SELECT COUNT(*) FROM nflverse_versions").fetchone()[0]
        surplus, by, present = plan(con)
        out = {"db": db_path, "applied": False, "rows_before": before,
               **census(con),
               "surplus": len(surplus), "keys_with_surplus": len(
                   {(r[2], r[3], r[4]) for r in surplus}),
               "by_dataset": by, "index_present_before": present}
        if raw_dir:
            n, bad, missing = disk_check(con, raw_dir)
            out.update(kept_checked=n, kept_sha_mismatch=bad,
                       kept_file_missing=len(missing))
        check = writers(con) if writers else None
        if check is not None:
            out["writers_clean"] = check.clean
            out["writers"] = check.statement
        if not apply:
            return out
        if not check.clean:
            raise WritersNotReady(check)
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
    try:
        r = migrate(a.db, apply=a.apply, raw_dir=a.raw_dir, writers=system_writers)
    except WritersNotReady as e:
        print(e.check.statement)
        print("REFUSED - nothing written. See docs/runbooks/nflverse-versions-migration.md")
        return 3
    statement = r.pop("writers", None)
    for k, v in r.items():
        print(f"  {k:22s} {v}")
    if statement:
        print(statement)
    if a.raw_dir and r.get("kept_sha_mismatch"):
        print("  WARNING: some kept rows do not match the bytes on disk")
    print("applied" if r["applied"] else "dry run - nothing written (pass --apply)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
