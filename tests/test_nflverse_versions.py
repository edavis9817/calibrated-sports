"""The nflverse version ledger, and the NULL in its primary key (unit a-03).

`season` is NULL for an all-season file (games, players, ngs_*, teams). That
NULL is a sentinel the readers use - `season IS ?` - and it is also a primary
key column, where SQLite treats NULLs as distinct. So the old
ON CONFLICT(dataset, season, data_version) upsert never fired for those files
and every same-day re-pull inserted a row. On the live store (2026-09-22):
140 NULL-season rows, 27 keys duplicated, 71 surplus rows.

Every test that asserts the fix has a twin showing the old behaviour produced
the other answer, so none of them can pass against a writer that never had
the defect.
"""
import ast
import hashlib
import io
import os
import pathlib
import sqlite3

import pytest

import config
import store
from jobs import ingest_nflverse, migrate_nflverse_versions

REPO = pathlib.Path(__file__).resolve().parent.parent

OLD_UPSERT = """INSERT INTO nflverse_versions
     (dataset, season, data_version, sha256, bytes, rel_path, rows,
      tier, ingested_ts, last_checked_ts)
   VALUES (?,?,?,?,?,?,?,?,?,?)
   ON CONFLICT(dataset, season, data_version) DO UPDATE SET
     sha256=excluded.sha256, bytes=excluded.bytes,
     rel_path=excluded.rel_path,
     rows=COALESCE(excluded.rows, nflverse_versions.rows),
     last_checked_ts=excluded.last_checked_ts"""


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setattr(config, "RAW_DIR", str(tmp_path / "raw"))
    store.init_db()
    store.reset_disk_cache()
    yield tmp_path
    store.reset_disk_cache()


def _rows(dataset, version):
    with store.db() as c:
        return c.execute(
            "SELECT sha256, rows FROM nflverse_versions WHERE dataset=? "
            "AND data_version=? ORDER BY rowid", (dataset, version)).fetchall()


def _raw_insert(dataset, season, version, sha):
    """What the old writer did on a NULL season: a plain second INSERT."""
    with store.db() as c:
        c.execute(OLD_UPSERT, (dataset, season, version, sha, 1, "p", None,
                               "live", 0.0, 0.0))


# --- the defect, and the fix -------------------------------------------------

def test_the_old_upsert_duplicated_a_null_season_and_not_a_seasoned_one(env):
    """The discriminating twin. If this ever stops producing 2 rows, SQLite
    changed its NULL semantics and the tests below prove nothing."""
    for sha in ("a", "b"):
        _raw_insert("games", None, "2026-09-09", sha)
        _raw_insert("weekly_stats", 2026, "2026-09-09", sha)
    assert len(_rows("games", "2026-09-09")) == 2
    assert len(_rows("weekly_stats", "2026-09-09")) == 1


def test_record_version_keeps_one_row_per_null_season_key(env):
    store.record_version("games", None, "2026-09-09", "a", 1, "p", rows=7)
    store.record_version("games", None, "2026-09-09", "b", 1, "p", rows=None)
    assert _rows("games", "2026-09-09") == [("b", 7)]   # new sha, rows not nulled


def test_record_version_still_one_row_per_seasoned_key(env):
    store.record_version("weekly_stats", 2026, "2026-09-09", "a", 1, "p", rows=7)
    store.record_version("weekly_stats", 2026, "2026-09-09", "b", 1, "p", rows=9)
    store.record_version("weekly_stats", 2025, "2026-09-09", "c", 1, "p")
    assert _rows("weekly_stats", "2026-09-09") == [("b", 9), ("c", None)]


def test_on_existing_duplicates_every_reader_and_writer_uses_the_newest(env):
    """Stores that already hold duplicates keep working until migrated: the
    newest row answers, and only the newest row is written."""
    for sha in ("old", "mid", "new"):
        _raw_insert("games", None, "2026-09-09", sha)
    assert store.latest_version("games", None)[1] == "new"

    store.touch_version("games", None, "2026-09-09")
    with store.db() as c:
        touched = [r[0] for r in c.execute(
            "SELECT sha256 FROM nflverse_versions WHERE last_checked_ts > 0 "
            "ORDER BY rowid")]
    assert touched == ["new"]

    store.record_version("games", None, "2026-09-09", "newer", 1, "p")
    assert [r[0] for r in _rows("games", "2026-09-09")] == ["old", "mid", "newer"]


def _games_parquet(n):
    import polars as pl
    buf = io.BytesIO()
    pl.DataFrame({"game_id": [f"2026_01_G{i}" for i in range(n)],
                  "season": [2026] * n, "week": [1] * n,
                  "gameday": ["2026-09-10"] * n}).write_parquet(buf)
    return buf.getvalue()


def test_rebuild_does_not_multiply_duplicates_and_records_the_disk_hash(env):
    """rebuild_from_archive used to call record_version once per ledger row,
    so each rebuild doubled a duplicated NULL-season key (the live store shows
    4 -> 8 -> 16 on games 2026-09-09). It also recorded the LEDGER's sha,
    which on a duplicated key can name bytes that were since overwritten."""
    data = _games_parquet(3)
    rel = store.archive_file("nflverse", "games.parquet", data, day="2026-09-09")
    disk = hashlib.sha256(data).hexdigest()
    with store.db() as c:
        for sha in ("stale-1", disk, "stale-2"):
            c.execute(OLD_UPSERT, ("games", None, "2026-09-09", sha, 1, rel,
                                   None, "live", 0.0, 0.0))

    s = ingest_nflverse.rebuild_from_archive(datasets=["games"])

    assert s["rebuilt"] == 1 and s["duplicate"] == 2
    assert [r[0] for r in _rows("games", "2026-09-09")] == \
        ["stale-1", disk, disk]            # no new row; newest now tells the truth
    assert store.latest_version("games", None)[1] == disk


# --- key_cols is gone, and stays gone -----------------------------------------

def _four_arg_calls(tree):
    return [n.lineno for n in ast.walk(tree)
            if isinstance(n, ast.Call)
            and getattr(n.func, "attr", getattr(n.func, "id", None)) == "replace_rows"
            and (len(n.args) != 3 or any(k.arg == "key_cols" for k in n.keywords)
                 or any(isinstance(a, ast.Starred) for a in n.args)
                 or any(k.arg is None for k in n.keywords))]


def test_the_call_site_check_can_fail():
    bad = ast.parse("store.replace_rows('t', cols, rows, None)\n"
                    "replace_rows('t', cols, rows, key_cols=('a',))\n"
                    "store.replace_rows(*args)\n")
    assert _four_arg_calls(bad) == [1, 2, 3]
    assert _four_arg_calls(ast.parse("store.replace_rows('t', c, r)")) == []


def test_no_call_site_passes_key_cols():
    """By AST, not grep: the removal comment in store.py names `key_cols` on
    purpose, and a text search cannot tell that from a live call. A 4-arg call
    is now a TypeError at runtime - this catches the ones no test reaches."""
    # This file is excluded by name: it makes one 4-arg call on purpose, to
    # show the TypeError, and the first run of this guard duly flagged it.
    files = [p for p in REPO.rglob("*.py")
             if not {".venv", "node_modules", ".git"} & set(p.parts)
             and p.resolve() != pathlib.Path(__file__).resolve()]
    assert len(files) > 50, f"walked only {len(files)} files"
    calls, bad = 0, {}
    for p in files:
        tree = ast.parse(p.read_text(encoding="utf-8"))
        calls += sum(1 for n in ast.walk(tree) if isinstance(n, ast.Call) and
                     getattr(n.func, "attr", getattr(n.func, "id", None)) == "replace_rows")
        if found := _four_arg_calls(tree):
            bad[str(p.relative_to(REPO))] = found
    assert calls >= 19, f"saw {calls} replace_rows calls - the walk is blind"
    assert bad == {}


def test_replace_rows_refuses_a_fourth_argument(env):
    with pytest.raises(TypeError):
        store.replace_rows("nfl_teams", ("team_abbr",), [], None)


# --- the migration ------------------------------------------------------------

# A writer check that has already passed. Every apply below names it, so no
# test reaches the real process/task probe - which on CI (no PowerShell) would
# refuse, and on the dev box would read this machine's live scheduler.
def READY(con):
    return migrate_nflverse_versions.WriterCheck()


def _dup_db(path):
    con = sqlite3.connect(path)
    con.execute("""CREATE TABLE nflverse_versions (
        dataset TEXT NOT NULL, season INTEGER, data_version TEXT NOT NULL,
        sha256 TEXT NOT NULL, bytes INTEGER, rel_path TEXT, rows INTEGER,
        tier TEXT, ingested_ts REAL NOT NULL, last_checked_ts REAL,
        PRIMARY KEY (dataset, season, data_version))""")
    for sha in ("g1", "g2", "g3"):
        con.execute(OLD_UPSERT, ("games", None, "2026-09-09", sha, 1, "p", None,
                                 "live", 0.0, 0.0))
    con.execute(OLD_UPSERT, ("games", None, "2026-09-10", "g4", 1, "p", None,
                             "live", 0.0, 0.0))
    con.execute(OLD_UPSERT, ("weekly_stats", 2026, "2026-09-09", "w", 1, "p",
                             None, "live", 0.0, 0.0))
    con.commit()
    con.close()


def _all(path, sql):
    con = sqlite3.connect(path)
    try:
        return con.execute(sql).fetchall()
    finally:
        con.close()


def test_migration_dry_run_writes_nothing(tmp_path):
    db = str(tmp_path / "m.db")
    _dup_db(db)
    before = open(db, "rb").read()
    r = migrate_nflverse_versions.migrate(db)
    assert (r["applied"], r["surplus"], r["keys_with_surplus"]) == (False, 2, 1)
    assert open(db, "rb").read() == before


def test_migration_moves_surplus_keeps_newest_and_locks_the_key(tmp_path):
    db = str(tmp_path / "m.db")
    _dup_db(db)
    cols = "dataset, season, data_version, sha256"
    original = sorted(_all(db, f"SELECT {cols} FROM nflverse_versions"), key=repr)

    r = migrate_nflverse_versions.migrate(db, apply=True, writers=READY)
    assert (r["rows_before"], r["moved"], r["rows_after"]) == (5, 2, 3)

    kept = _all(db, f"SELECT {cols} FROM nflverse_versions ORDER BY rowid")
    assert [k[3] for k in kept] == ["g3", "g4", "w"]          # newest per key
    moved = _all(db, f"SELECT {cols}, kept_rowid FROM nflverse_versions_superseded")
    assert sorted(m[3] for m in moved) == ["g1", "g2"]
    assert {m[4] for m in moved} == {3}
    assert sorted(kept + [m[:4] for m in moved], key=repr) == original  # nothing lost

    con = sqlite3.connect(db)
    with pytest.raises(sqlite3.IntegrityError):
        con.execute(OLD_UPSERT, ("games", None, "2026-09-09", "again", 1, "p",
                                 None, "live", 0.0, 0.0))
    con.close()

    again = migrate_nflverse_versions.migrate(db, apply=True, writers=READY)
    assert (again["moved"], again["index_present_before"]) == (0, True)


def test_the_new_writer_works_on_a_migrated_store(env):
    """After migration the unique index exists; record_version must update,
    never trip it."""
    store.record_version("games", None, "2026-09-09", "a", 1, "p")
    migrate_nflverse_versions.migrate(config.DB_PATH, apply=True, writers=READY)
    store.record_version("games", None, "2026-09-09", "b", 1, "p")
    store.record_version("games", None, "2026-09-10", "c", 1, "p")
    assert _rows("games", "2026-09-09") == [("b", None)]


def test_migration_refuses_a_path_that_does_not_exist(tmp_path):
    with pytest.raises(FileNotFoundError):
        migrate_nflverse_versions.migrate(str(tmp_path / "nope.db"), apply=True)
    assert not os.path.exists(tmp_path / "nope.db")


# --- the precondition (unit a-07) ---------------------------------------------
# c-04: once ux_nflv_key exists, the OLD upsert raises on a second same-day
# NULL-season pull - after archive_file has overwritten the day's file. So
# --apply must refuse while any writer can still run the old upsert.

M = migrate_nflverse_versions


def _checkout(root, rev):
    """A fake repository checkout whose store.py declares `rev` (None = the
    pre-a-03 file, which declares nothing)."""
    (root / "jobs").mkdir(parents=True)
    (root / "jobs" / "ingest_nflverse.py").write_text("")
    body = "" if rev is None else f"NFLV_WRITER_REV = {rev}\n"
    (root / "store.py").write_text("import os\n" + body)
    (root / ".venv" / "Scripts").mkdir(parents=True)
    return root


def _health_db(path, detail):
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE source_health (source TEXT PRIMARY KEY, ok INT, "
                "detail TEXT, watermark REAL, last_ok_ts REAL, last_fail_ts REAL, "
                "updated_ts REAL)")
    if detail is not None:
        con.execute("INSERT INTO source_health VALUES ('logger_start',1,?,0,0,0,0)",
                    (detail,))
    con.commit()
    return con


def _tasks(root):
    return [
        {"name": "Logger (logon)", "state": "Ready", "execute": "powershell.exe",
         "args": f'-NoProfile -File "{os.path.join(root, "start_logger.ps1")}"',
         "workdir": ""},
        {"name": "Weekly Refresh", "state": "Ready",
         "execute": os.path.join(root, ".venv", "Scripts", "python.exe"),
         "args": "-m jobs.weekly_refresh", "workdir": str(root)},
        {"name": "Unrelated", "state": "Ready", "execute": "notepad.exe",
         "args": "", "workdir": ""},
    ]


# The venv launcher (pid 10) spawns the real interpreter (pid 11) with the
# same command line; logger_start records the real one's pid.
LOGGER = [{"pid": 10, "ppid": 1, "cmd": '"x\\python.exe" "run_logger.py"'},
          {"pid": 11, "ppid": 10, "cmd": '"x\\python.exe" "run_logger.py"'}]


def _good(tmp_path):
    root = _checkout(tmp_path / "repo", 2)
    con = _health_db(tmp_path / "h.db", "build abc pid 11 nflv_writer 2")
    return root, con


def test_writer_check_passes_when_everything_is_on_the_fix(tmp_path):
    root, con = _good(tmp_path)
    chk = M.check_writers(con, LOGGER + [{"pid": 50, "ppid": 1, "cmd": "jupyter"}],
                          _tasks(root))
    assert chk.clean, chk.statement
    assert "logger pid 11: loaded rev 2" in chk.statement
    assert "1 other python process" in chk.statement     # reported, not refused


@pytest.mark.parametrize("case, expect", [
    ("old_checkout", "writer rev 1"),
    ("untagged_logger", "untagged (pre-a-07)"),
    ("old_tagged_logger", "loaded writer rev 1"),
    ("pid_mismatch", "logger_start names pid 99"),
    ("no_health_row", "no logger_start row"),
    ("two_loggers", "2 loggers are running"),
    ("job_running", "a writer job is running now"),
    ("no_processes", "could not read the process list"),
    ("no_tasks", "could not read the scheduled tasks"),
    ("no_writer_tasks", "no scheduled task runs a writer"),
    ("no_checkout", "cannot find the checkout"),
])
def test_writer_check_refuses_each_way_a_writer_can_be_old(tmp_path, case, expect):
    """Each refusal is reached from the passing baseline by changing ONE thing,
    so the test above and these together show the check discriminates."""
    root, con = _good(tmp_path)
    procs, tasks = list(LOGGER), _tasks(root)
    if case == "old_checkout":
        tasks = _tasks(_checkout(tmp_path / "old", None))
    elif case == "untagged_logger":
        con = _health_db(tmp_path / "u.db", "build abc pid 11")
    elif case == "old_tagged_logger":
        con = _health_db(tmp_path / "o.db", "build abc pid 11 nflv_writer 1")
    elif case == "pid_mismatch":
        con = _health_db(tmp_path / "p.db", "build abc pid 99 nflv_writer 2")
    elif case == "no_health_row":
        con = _health_db(tmp_path / "n.db", None)
    elif case == "two_loggers":
        procs.append({"pid": 20, "ppid": 1, "cmd": "python run_logger.py"})
    elif case == "job_running":
        procs.append({"pid": 30, "ppid": 1, "cmd": "python -m jobs.weekly_refresh"})
    elif case == "no_processes":
        procs = None
    elif case == "no_tasks":
        tasks = None
    elif case == "no_writer_tasks":
        tasks = [t for t in tasks if t["name"] == "Unrelated"]
    elif case == "no_checkout":
        tasks = [dict(t, workdir="", execute="python.exe",
                      args=f'-File "{os.path.join(tmp_path, "nowhere", "start_logger.ps1")}"')
                 for t in tasks[:1]]
    chk = M.check_writers(con, procs, tasks)
    assert not chk.clean
    assert expect in chk.statement, chk.statement


def test_no_logger_running_is_not_a_refusal_the_checkout_is(tmp_path):
    root, con = _good(tmp_path)
    assert M.check_writers(con, [], _tasks(root)).clean
    old = _tasks(_checkout(tmp_path / "old", None))
    assert not M.check_writers(con, [], old).clean


def test_writer_check_refuses_truth_testing(tmp_path):
    root, con = _good(tmp_path)
    chk = M.check_writers(con, LOGGER, _tasks(root))
    with pytest.raises(TypeError):
        bool(chk)
    with pytest.raises(TypeError):
        assert chk


def test_rev_is_read_from_the_file_and_absence_means_old(tmp_path):
    assert M.rev_in_checkout(_checkout(tmp_path / "a", 2)) == 2
    assert M.rev_in_checkout(_checkout(tmp_path / "b", None)) == 1
    assert M.rev_in_checkout(tmp_path / "empty") is None
    # This checkout: the real store declares the revision the check requires.
    assert M.rev_in_checkout(str(REPO)) == store.NFLV_WRITER_REV >= M.REQUIRED_REV


def test_checkout_is_found_from_each_task_shape(tmp_path):
    root = _checkout(tmp_path / "repo", 2)
    for t in _tasks(root)[:2]:
        assert M.checkout_of(t) == os.path.normpath(root)
    assert M.checkout_of(_tasks(root)[2]) is None


def test_the_logger_tag_round_trips(tmp_path):
    import run_logger
    detail = run_logger.logger_start_detail("abc123", 4242)
    con = _health_db(tmp_path / "h.db", detail)
    assert M.logger_start(con) == (4242, store.NFLV_WRITER_REV, detail)


def test_apply_refuses_and_writes_nothing_when_writers_are_not_ready(tmp_path):
    db = str(tmp_path / "m.db")
    _dup_db(db)
    before = open(db, "rb").read()
    bad = M.WriterCheck(problems=["a writer is old"])
    with pytest.raises(M.WritersNotReady, match="a writer is old"):
        M.migrate(db, apply=True, writers=lambda con: bad)
    assert open(db, "rb").read() == before


def test_apply_with_no_check_given_probes_and_a_failed_probe_refuses(tmp_path, monkeypatch):
    """The default is the real probe, and a probe that cannot read the machine
    must refuse - never read as "nothing is running"."""
    db = str(tmp_path / "m.db")
    _dup_db(db)
    before = open(db, "rb").read()
    monkeypatch.setattr(M, "probe_system", lambda: (None, None))
    with pytest.raises(M.WritersNotReady, match="could not read the process list"):
        M.migrate(db, apply=True)
    assert open(db, "rb").read() == before


def test_the_probe_parses_powershells_flattened_single_item_arrays():
    class R:
        returncode = 0
        stdout = ('{"processes": {"pid": 5, "ppid": 1, "cmd": "run_logger.py"},'
                  ' "tasks": null}')
    procs, tasks = M.probe_system(run=lambda *a, **k: R)
    assert procs == [{"pid": 5, "ppid": 1, "cmd": "run_logger.py"}] and tasks == []

    class Fail:
        returncode = 1
        stdout = ""
    assert M.probe_system(run=lambda *a, **k: Fail) == (None, None)


def test_the_dry_run_counts_what_the_runbook_must_not_quote(tmp_path):
    db = str(tmp_path / "m.db")
    _dup_db(db)
    r = M.migrate(db)
    assert (r["null_season_rows"], r["null_season_keys"], r["null_season_dup_keys"],
            r["null_season_surplus"], r["seasoned_rows"], r["seasoned_surplus"]) \
        == (4, 2, 1, 2, 1, 0)
    assert "writers" not in r            # a dry run given no check probes nothing
