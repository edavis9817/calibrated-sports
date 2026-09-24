"""a-22: Sources is generated from a registry, and the export refuses what it does not declare.

Every guard here is shown firing on the other input before it is trusted: a check
that can only pass is the failure this unit exists to remove.
"""
import ast
import json
import os
import re
import sqlite3

import pytest

import config
from jobs import export_web as E
from jobs import source_registry as R
from tests.test_export_web import NOW, db  # noqa: F401  (shared fixture)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXPORT_SRC = os.path.join(ROOT, "jobs", "export_web.py")
CONTRACT_KINDS = E.CONTRACT["x-contract"]["kinds"]
# Upper-case keywords only, so prose ("read from the archive") is never a table.
SQL_TABLE = re.compile(r"\b(?:FROM|JOIN)\s+([a-z_][a-z0-9_]*)\b")


def sql_tables(source):
    """Every table named after FROM/JOIN in a SELECT string literal, by AST.

    Adjacent literals are one Constant after parsing, so a query split across
    lines is read whole. Docstrings are skipped: they quote SQL to explain it.
    """
    tree = ast.parse(source)
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.ClassDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                docstrings.add(id(first.value))
    out = set()
    for node in ast.walk(tree):
        if (isinstance(node, ast.Constant) and isinstance(node.value, str)
                and id(node) not in docstrings and "SELECT" in node.value):
            out |= set(SQL_TABLE.findall(node.value))
    return out


def kind_table_names():
    return {t[0] if isinstance(t, tuple) else t for ts in R.KIND_TABLES.values() for t in ts}


# ------------------------------------------------------------------ the registry itself

def test_every_contract_kind_is_declared_and_nothing_else_is():
    statement = R.check_registry(CONTRACT_KINDS)
    assert f"{len(CONTRACT_KINDS)} kinds declared" in statement
    assert set(R.KIND_SOURCES) == set(CONTRACT_KINDS)


def test_a_contract_kind_with_no_declaration_is_refused(monkeypatch):
    monkeypatch.setattr(R, "KIND_SOURCES", {k: v for k, v in R.KIND_SOURCES.items() if k != "team"})
    with pytest.raises(R.SourceRegistryError, match="team"):
        R.check_registry(CONTRACT_KINDS)


def test_an_unregistered_id_is_refused(monkeypatch):
    monkeypatch.setitem(R.KIND_EXTRA, "sports", ("a.feed.nobody.registered",))
    with pytest.raises(R.SourceRegistryError, match="a.feed.nobody.registered"):
        R.check_registry()


def test_a_source_nothing_reads_is_refused(monkeypatch):
    # The inverse lie: a row on Sources for a feed no page draws from.
    monkeypatch.setitem(R.SOURCES, "orphan.feed", dict(
        name="Orphan", sports=("nfl",), layer="FACTS", provides="-", used_for="-",
        last_read=("elsewhere", "nowhere.db")))
    with pytest.raises(R.SourceRegistryError, match="orphan.feed: registered but no kind reads it"):
        R.check_registry()


def test_narrowing_a_table_may_only_drop_sources(monkeypatch):
    monkeypatch.setitem(R.KIND_TABLES, "market", (("outcomes", ("nflverse.teams",)),))
    with pytest.raises(R.SourceRegistryError, match="not a subset"):
        R.check_registry()


def test_every_layer_and_state_is_one_the_contract_allows():
    defs = E.CONTRACT["$defs"]
    assert set(R.LAYERS) == set(defs["SourceEntry"]["properties"]["layer"]["enum"])
    assert set(R.NOT_CONNECTED_STATES) == set(defs["NotConnectedEntry"]["properties"]["state"]["enum"])
    R.check_not_connected()


# ------------------------------------------------------------------ the SQL the export reads

def test_every_table_export_web_reads_is_attributed_to_a_source_and_a_kind():
    with open(EXPORT_SRC, encoding="utf-8") as f:
        tables = sql_tables(f.read())
    # Count against expectation, never trust an empty scan: fourteen tables today.
    assert len(tables) >= 10, f"the SQL scan found only {sorted(tables)}"
    unmapped = tables - set(R.TABLE_SOURCES)
    assert not unmapped, f"export_web reads tables no source is named for: {sorted(unmapped)}"
    unassigned = tables - kind_table_names()
    assert not unassigned, f"tables read by no declared kind: {sorted(unassigned)}"


def test_the_sql_scan_discriminates():
    planted = ('def load_new(con):\n'
               '    """SELECT * FROM a_docstring_table is prose, not a read."""\n'
               '    return con.execute("SELECT x FROM brand_new_feed f "\n'
               '                       "JOIN nfl_games g ON g.game_id = f.game_id").fetchall()\n')
    found = sql_tables(planted)
    assert found == {"brand_new_feed", "nfl_games"}
    assert "brand_new_feed" not in R.TABLE_SOURCES   # so the test above would fail on it


# ------------------------------------------------------------------ the export gate

def test_require_declared_returns_what_it_approved():
    got = R.require_declared(["team", "market"])
    assert got["team"] == R.KIND_SOURCES["team"] and "kalshi.price_history" in got["market"]


def test_the_export_refuses_a_kind_whose_sources_are_undeclared(db, monkeypatch):  # noqa: F811
    dest = str(db / "out")
    monkeypatch.setattr(R, "KIND_SOURCES", {k: v for k, v in R.KIND_SOURCES.items() if k != "team"})
    with pytest.raises(R.SourceRegistryError, match="team"):
        E.export(only=["teams"], now_ts=NOW, dest=dest)
    assert not any(k.startswith("nfl/teams/") for k in E.local_keys(dest)), "wrote before refusing"


def test_the_export_passes_the_same_run_when_declared(db):  # noqa: F811
    dest = str(db / "out")
    E.export(only=["teams"], now_ts=NOW, dest=dest)
    assert any(k.startswith("nfl/teams/") for k in E.local_keys(dest))


# ------------------------------------------------------------------ the generated file

def _sources_file(db):  # noqa: F811
    dest = str(db / "out")
    s = E.export(only=["players", "teams", "manifest"], now_ts=NOW, dest=dest)
    with open(E.local_path(dest, "nfl/sources.json"), encoding="utf-8") as f:
        return s, json.load(f)


def test_sources_file_is_written_by_the_manifest_part_and_is_contract_valid(db):  # noqa: F811
    summary, obj = _sources_file(db)
    E.validate_contract({"nfl/sources.json": obj})
    assert obj["kind"] == "sources" and obj["sport"] == "nfl"
    assert summary["sources"]["listed"] == len(obj["sources"]) > 0
    assert set(obj["kinds"]) == set(CONTRACT_KINDS)


def test_the_three_rows_the_audit_found_false_are_generated_true(db):  # noqa: F811
    _, obj = _sources_file(db)
    rows = {r["source_id"]: r for r in obj["sources"]}
    # 1. The Odds API is listed, with what reads it.
    odds = rows["oddsapi"]
    assert odds["name"] == "The Odds API"
    assert "player_summary" in odds["read_by"] and "research.hypotheses" in odds["read_by"]
    assert "2023-2025" in odds["provides"] and "2026" in odds["provides"]
    # 2. Price history is retained, and the market file reads it.
    ph = rows["kalshi.price_history"]
    assert "market" in ph["read_by"] and "not retained" not in ph["provides"].lower()
    # 3. The schedule is the fixture source for teams.
    sched = rows["nflverse.schedule"]
    assert "team" in sched["read_by"] and "fixture" in sched["used_for"].lower()


def test_every_listed_source_serves_this_sport_and_every_read_is_listed(db):  # noqa: F811
    _, obj = _sources_file(db)
    listed = {r["source_id"] for r in obj["sources"]}
    for kind, ids in obj["kinds"].items():
        assert set(ids) <= listed, f"{kind} reads {set(ids) - listed}, which Sources omits"
    assert all("nfl" in R.SOURCES[i]["sports"] for i in listed)
    assert "retrosheet" not in listed   # registered, but not an NFL source


def test_cadence_is_read_from_the_logger_settings_at_build_time(db, monkeypatch):  # noqa: F811
    monkeypatch.setattr(config, "POLL_HOT", 7)
    monkeypatch.setattr(config, "QUOTES_RETENTION_DAYS", 3.0)
    _, obj = _sources_file(db)
    ph = next(r for r in obj["sources"] if r["source_id"] == "kalshi.price_history")
    assert "every 7s" in ph["provides"] and "kept 3 days" in ph["provides"]


def test_last_read_is_measured_and_a_null_says_why(db):  # noqa: F811
    c = sqlite3.connect(config.DB_PATH)
    c.execute("INSERT OR REPLACE INTO source_health (source, ok, detail, last_ok_ts, updated_ts) "
              "VALUES ('nflverse:games', 1, 't', ?, ?)", (NOW - 60, NOW - 60))
    c.commit()
    c.close()
    _, obj = _sources_file(db)
    rows = {r["source_id"]: r for r in obj["sources"]}
    assert rows["nflverse.schedule"]["last_read"] == E.iso(NOW - 60)
    # Nothing recorded for stats in this store: null, with the reason beside it.
    assert rows["nflverse.stats"]["last_read"] is None
    assert "no successful read" in rows["nflverse.stats"]["last_read_basis"]
    assert rows["nflverse.injuries"]["last_read"] is None
    assert "feeds.db" in rows["nflverse.injuries"]["last_read_basis"]


def test_not_connected_rides_the_same_file(db):  # noqa: F811
    _, obj = _sources_file(db)
    assert obj["not_connected"] == R.NOT_CONNECTED and len(obj["not_connected"]) > 0
