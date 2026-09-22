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

    r = migrate_nflverse_versions.migrate(db, apply=True)
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

    again = migrate_nflverse_versions.migrate(db, apply=True)
    assert (again["moved"], again["index_present_before"]) == (0, True)


def test_the_new_writer_works_on_a_migrated_store(env):
    """After migration the unique index exists; record_version must update,
    never trip it."""
    store.record_version("games", None, "2026-09-09", "a", 1, "p")
    migrate_nflverse_versions.migrate(config.DB_PATH, apply=True)
    store.record_version("games", None, "2026-09-09", "b", 1, "p")
    store.record_version("games", None, "2026-09-10", "c", 1, "p")
    assert _rows("games", "2026-09-09") == [("b", None)]


def test_migration_refuses_a_path_that_does_not_exist(tmp_path):
    with pytest.raises(FileNotFoundError):
        migrate_nflverse_versions.migrate(str(tmp_path / "nope.db"), apply=True)
    assert not os.path.exists(tmp_path / "nope.db")
