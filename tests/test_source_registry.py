"""a-22, a-30: Sources is derived from what the producers read, and the export refuses
what it does not declare.

Every guard here is shown firing on the other input before it is trusted: a check
that can only pass is the failure this unit exists to remove. a-30 adds the four
ways past a-22's scan that f-19 found (lower-case SQL, an f-string table, SQL in
store.py, a mapped table reaching a new kind), each planted and each caught, and a
behavioural check that the per-kind attribution matches what the export DOES.
"""
import ast
import json
import os
import sqlite3

import pytest

import config
from jobs import export_web as E
from jobs import source_registry as R
from tests.test_export_cfb_web import store  # noqa: F401  (shared fixture)
from tests.test_export_web import NOW, db  # noqa: F401  (shared fixture)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONTRACT_KINDS = E.CONTRACT["x-contract"]["kinds"]


@pytest.fixture
def registry(monkeypatch):
    """Edit the registry's tables, then re-derive; restored AND re-derived after."""
    yield monkeypatch
    monkeypatch.undo()
    R.refresh()


def _src(mod):
    with open(os.path.join(ROOT, *mod.split(".")) + ".py", encoding="utf-8") as f:
        return f.read()


def plant(tmp_path, monkeypatch, **texts):
    """Swap module sources for planted copies (module name with '_' for '.'), re-derive."""
    real = R._module_file
    paths = {}
    for key, text in texts.items():
        mod = key.replace("__", ".")
        p = tmp_path / f"planted_{key}.py"
        p.write_text(text, encoding="utf-8")
        paths[mod] = str(p)
    monkeypatch.setattr(R, "_module_file", lambda m: paths.get(m) or real(m))
    R.refresh()
    return R.PROBLEMS["nfl"]


# ------------------------------------------------------------------ the registry itself

def test_every_contract_kind_is_declared_and_nothing_else_is():
    statement = R.check_registry(CONTRACT_KINDS)
    assert f"({len(CONTRACT_KINDS)} nfl)" in statement and "0 gaps" in statement
    assert set(R.DECLARED["nfl"]) == set(CONTRACT_KINDS)
    assert R.PROBLEMS == {"nfl": [], "cfb": [], "mlb": []}


def test_a_contract_kind_with_no_declaration_is_refused(registry):
    registry.setitem(R.DECLARED, "nfl", {k: v for k, v in R.DECLARED["nfl"].items() if k != "team"})
    with pytest.raises(R.SourceRegistryError, match="team"):
        R.check_registry(CONTRACT_KINDS)


def test_an_unregistered_id_is_refused_by_the_named_guard_not_by_the_import(registry):
    """f-19's C2: a-22's _derive crashed with ValueError (list.index) at IMPORT, so the
    named branches were reachable only by monkeypatching after import. Now the derivation
    carries the id through and both named guards refuse it."""
    registry.setitem(R.KIND_EXTRA["nfl"], "sports", ("a.feed.nobody.registered",))
    R.refresh()                                   # the step that used to raise ValueError
    assert R.DECLARED["nfl"]["sports"] == ("a.feed.nobody.registered",)
    with pytest.raises(R.SourceRegistryError, match="a.feed.nobody.registered"):
        R.check_registry()
    with pytest.raises(R.SourceRegistryError, match="a.feed.nobody.registered"):
        R.require_declared(["sports"])


def test_a_source_nothing_reads_is_refused(registry):
    registry.setitem(R.SOURCES, "orphan.feed", dict(
        name="Orphan", sports=("nfl",), layer="FACTS", provides="-", used_for="-",
        last_read=("elsewhere", "nowhere.db")))
    with pytest.raises(R.SourceRegistryError, match="orphan.feed: registered but no kind reads it"):
        R.check_registry()


def test_narrowing_a_table_may_only_drop_sources(registry):
    registry.setitem(R.NARROW["nfl"], ("build_market", "outcomes"), ("nflverse.teams",))
    R.refresh()
    with pytest.raises(R.SourceRegistryError, match="not a subset"):
        R.check_registry()


def test_a_source_declared_for_a_sport_it_does_not_serve_is_refused(registry):
    registry.setitem(R.SPORT_TABLE_SOURCES["nfl"], "nfl_teams", ("retrosheet",))
    R.refresh()
    with pytest.raises(R.SourceRegistryError, match="retrosheet.*does not name nfl"):
        R.check_registry()


def test_every_layer_and_state_is_one_the_contract_allows():
    defs = E.CONTRACT["$defs"]
    assert set(R.LAYERS) == set(defs["SourceEntry"]["properties"]["layer"]["enum"])
    assert set(R.NOT_CONNECTED_STATES) == set(defs["NotConnectedEntry"]["properties"]["state"]["enum"])
    R.check_not_connected()


def test_sportless_kinds_are_read_off_the_contract():
    assert R.SPORTLESS == set(E.CONTRACT["x-contract"]["sportless_kinds"])


# ------------------------------------------------------------------ f-19's measured errors

def test_declarations_are_per_sport_and_match_each_sports_store():
    """f-19: cfb/manifest was listed as reading only Kalshi and mlb/manifest as reading
    nothing, because a-22 filtered the NFL declarations by `sports`."""
    assert R.DECLARED["cfb"]["sport_manifest"] == ("sportsdataverse.cfb",)
    assert R.DECLARED["cfb"]["team"] == ("sportsdataverse.cfb", "cfbd")
    assert R.DECLARED["cfb"]["player_index"] == ()       # an empty list, by decision
    for kind in ("sport_manifest", "team", "player_index", "player_summary", "player_season"):
        assert R.DECLARED["mlb"][kind] == ("retrosheet",), kind
    assert not any("kalshi" in i for ids in R.DECLARED["cfb"].values() for i in ids)


def test_player_index_declares_kalshi_because_has_market_does():
    """f-19: has_market is `gsis in market_keys`, and a-22 declared the index with
    nflverse only. Now inherited from the market kind, not retyped."""
    assert "kalshi.ladders" in R.DECLARED["nfl"]["player_index"]
    assert "kalshi.ladders" in R.DECLARED["nfl"]["player_summary"]


def test_pfr_alias_read_through_store_is_seen_and_attributed():
    found = R.scan("jobs.export_web")
    for fn in ("load_snaps", "snap_weeks", "load_phase_snaps"):
        assert "pfr_alias" in found[fn][0], fn           # via store.pfr_gsis
    assert "nflverse.draft_picks" in R.DECLARED["nfl"]["player_season"]


def test_espn_is_registered_and_read_by_the_live_page(db):  # noqa: F811
    assert "espn.scoreboard" in R.SOURCES
    _, obj = _sources_file(db)
    rows = {r["source_id"]: r for r in obj["sources"]}
    # a-33: with a-23 merged, the Live snapshot kind reads the scoreboard too. The
    # page read stays until the site's Live page reads the snapshot instead.
    assert rows["espn.scoreboard"]["read_by"] == ["live.snapshot", "page:live"]
    assert rows["espn.scoreboard"]["last_read"] is None
    assert "request time" in rows["espn.scoreboard"]["last_read_basis"]


def test_the_cfb_and_mlb_sources_files_list_their_own_stores():
    con = sqlite3.connect(":memory:")
    for sport, want, manifest in (("cfb", {"sportsdataverse.cfb", "cfbd"}, ["sportsdataverse.cfb"]),
                                  ("mlb", {"retrosheet"}, ["retrosheet"])):
        obj = R.build_sources(sport, E.iso(NOW), con, E.envelope)
        E.validate_contract({f"{sport}/sources.json": obj})
        assert {r["source_id"] for r in obj["sources"]} == want, sport
        assert obj["kinds"]["sport_manifest"] == manifest, sport


# ------------------------------------------------------------------ the scan

def test_the_scan_reads_case_insensitively_and_reports_what_it_cannot_read():
    assert R.sql_tables("select a from brand_new_feed f join nfl_games g on 1")[0] == {
        "brand_new_feed", "nfl_games"}
    assert R.sql_tables("SELECT a FROM (SELECT b FROM inner_t) x")[0] == {"inner_t"}
    assert R.sql_tables("SELECT 1 FROM sqlite_master")[0] == set()
    # prose is not SQL: no SELECT
    assert R.sql_tables("read from the archive") == (set(), [])
    t, gaps = R.sql_tables(f"SELECT a FROM {R._HOLE} WHERE x")
    assert t == set() and gaps == ["the table name is interpolated"]
    assert R.sql_tables("SELECT a FROM ")[1] == [
        "FROM/JOIN ends the string: the table is outside the literal"]


def test_every_producers_scan_found_what_it_should():
    # Count against expectation, never trust an empty scan.
    counts = {s: len(R.scan(R.PRODUCERS[s])) for s in R.SPORTS}
    assert counts["nfl"] >= 15 and counts["cfb"] >= 8 and counts["mlb"] >= 1, counts
    # a-31: a side producer's functions are attributed under `module.function`;
    # the union of every scanned module must be exactly LOADERS - both ways.
    scanned = set(R.scan("jobs.export_web"))
    # a-32: the minimum is per module - the Board reads through ten functions, the
    # Lab universe through six. A bare ">= 10" held only while the Board was alone.
    expected = {"jobs.board_read": 10, "lab.universe": 6}
    assert set(R.SIDE_PRODUCERS["nfl"]) == set(expected)
    for mod in R.SIDE_PRODUCERS["nfl"]:
        side = R.scan(mod)
        assert len(side) >= expected[mod], (mod, side)
        scanned |= {f"{mod}.{fn}" for fn in side}
    assert scanned == set(R.LOADERS["nfl"])


LOADER = '\n\ndef load_f19_planted(con):\n    return con.execute({sql}).fetchall()\n'


@pytest.mark.parametrize("name, sql", [
    ("V1 lower-case SQL", '"select team, status from nfl_injury_week"'),
    ("V2 f-string table", 'f"SELECT team FROM {F19_T}"'),
    ("C4 upper-case, a-22's own case", '"SELECT team, status FROM nfl_injury_week"'),
])
def test_a_planted_export_web_read_is_refused(tmp_path, registry, name, sql):
    text = _src("jobs.export_web") + '\n\nF19_T = "nfl_injury_week"' + LOADER.format(sql=sql)
    problems = plant(tmp_path, registry, jobs__export_web=text)
    assert any("load_f19_planted" in p or "interpolated" in p for p in problems), (name, problems)
    with pytest.raises(R.SourceRegistryError):
        R.require_declared(["team"])          # the gate, not only the test, refuses


def test_v3_sql_held_in_store_and_called_from_export_web_is_refused(tmp_path, registry):
    store_text = _src("store") + ('\n\ndef f19_injuries(con):\n'
                                  '    return con.execute("SELECT team FROM nfl_injury_week").fetchall()\n')
    export_text = _src("jobs.export_web") + '\n\ndef load_f19(con):\n    return store.f19_injuries(con)\n'
    problems = plant(tmp_path, registry, store=store_text, jobs__export_web=export_text)
    assert any("load_f19" in p and "nfl_injury_week" in p for p in problems), problems


def test_v3_literal_store_sql_that_no_export_calls_is_not_an_export_read(tmp_path, registry):
    """f-19's V3 as planted appended an UNCALLED function to store.py. store.py is
    the whole logger's module and reads tables no export touches; requiring every
    one of them mapped would be a false declaration. Stated, so it is not assumed."""
    store_text = _src("store") + ('\n\ndef f19_injuries(con):\n'
                                  '    return con.execute("SELECT team FROM nfl_injury_week").fetchall()\n')
    assert plant(tmp_path, registry, store=store_text) == []


def test_v4_a_mapped_table_read_by_a_new_function_must_be_attributed(tmp_path, registry):
    sql = '"SELECT line FROM outcomes WHERE entity_type = \'team\'"'
    text = _src("jobs.export_web") + LOADER.format(sql=sql)
    problems = plant(tmp_path, registry, jobs__export_web=text)
    assert any("load_f19_planted" in p and "not attributed" in p for p in problems), problems
    # Attributing it to a kind DERIVES that kind's new sources - nothing is retyped.
    registry.setitem(R.LOADERS["nfl"], "load_f19_planted", ("team",))
    R.refresh()
    assert R.PROBLEMS["nfl"] == []
    assert {"oddsapi", "polymarket"} <= set(R.DECLARED["nfl"]["team"])


def test_a_stale_loader_entry_is_refused(registry):
    registry.setitem(R.LOADERS["nfl"], "no_such_function", ("team",))
    R.refresh()
    with pytest.raises(R.SourceRegistryError, match="no_such_function.*stale"):
        R.check_registry()


# ------------------------------------------------------------------ the runtime ledger

def test_the_ledger_sees_reads_the_scan_cannot():
    con = sqlite3.connect(":memory:")
    con.execute("CREATE TABLE Mixed_Case (a)")
    con.execute("CREATE TABLE plain (a)")
    reads = R.watch(con, "nfl")
    t = "plain"
    con.execute("select a from mixed_case").fetchall()
    con.execute(f"SELECT a FROM {t}").fetchall()
    with R.quiet():
        con.execute("SELECT count(*) FROM plain").fetchall()   # still recorded once, above
    assert reads == {"mixed_case", "plain"}
    with pytest.raises(R.SourceRegistryError, match="mixed_case"):
        R.require_declared(["team"])
    R.watch(sqlite3.connect(":memory:"), "nfl")    # a fresh ledger for the next test


def test_the_export_refuses_after_an_unmapped_read_at_runtime(db, monkeypatch):  # noqa: F811
    real = E.load_headshots

    def reads_more(con):
        con.execute("select 1 from source_health").fetchall()   # lower-case, unmapped
        return real(con)

    monkeypatch.setattr(E, "load_headshots", reads_more)
    dest = str(db / "out")
    with pytest.raises(R.SourceRegistryError, match="source_health"):
        E.export(only=["players"], now_ts=NOW, dest=dest)
    assert not any(k.startswith("nfl/players/") for k in E.local_keys(dest)), "wrote before refusing"


def test_a_clean_export_reads_only_mapped_tables(db):  # noqa: F811
    E.export(only=["players", "teams", "components", "manifest"], now_ts=NOW, dest=str(db / "out"))
    reads = R._READS["nfl"]
    assert len(reads) >= 5, reads
    assert reads <= set(R.TABLE_SOURCES), reads - set(R.TABLE_SOURCES)
    assert "source_health" not in reads                     # last_read ran under quiet()


# ------------------------------------------------------------------ behaviour: data flow

def _canon(dest):
    out = {}
    for k, p in E.local_keys(dest).items():
        with open(p, encoding="utf-8") as f:
            out[k] = E._canonical(json.load(f))
    return out


def _moved_kinds(a, b):
    return {E.kind_for_key(k)[0] for k in set(a) | set(b) if a.get(k) != b.get(k)} - {"sources"}


PARTS_RUN = ["players", "teams", "components", "manifest"]


def _perturbed_export(db, base_db, t, where, monkeypatch):  # noqa: F811
    copy = str(db / f"perturbed_{t}_{len(where)}.db")
    # The backup API, not a file copy: the store is WAL, and a file copy misses
    # whatever has not been checkpointed.
    src, c = sqlite3.connect(base_db), sqlite3.connect(copy)
    src.backup(c)
    src.close()
    c.execute(f"DELETE FROM {t} {where}")
    c.commit()
    c.close()
    monkeypatch.setattr(config, "DB_PATH", copy)
    dest = str(db / f"out_{t}_{len(where)}")
    E.export(only=PARTS_RUN, now_ts=NOW, dest=dest)
    return _canon(dest)


def test_every_kind_whose_output_moves_when_a_table_changes_declares_it(db, monkeypatch):  # noqa: F811
    """Checks LOADERS against what the code DOES. Delete a table's rows, re-export,
    and every kind whose files changed must declare a source that table carries. A
    table the export cannot run without (nfl_games, nfl_player_week) loses one row
    instead. A kind that moved without declaring it is an attribution the scan missed."""
    base_db = config.DB_PATH
    E.export(only=PARTS_RUN, now_ts=NOW, dest=str(db / "base"))
    base = _canon(str(db / "base"))
    reads = set(R._READS["nfl"])
    con = sqlite3.connect(base_db)
    populated = sorted(t for t in reads if con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0])
    con.close()
    assert len(populated) >= 4, populated
    checked = {}
    for t in populated:
        after = None
        for where in ("", f"WHERE rowid = (SELECT MAX(rowid) FROM {t})"):
            try:
                after = _perturbed_export(db, base_db, t, where, monkeypatch)
                break
            except Exception:  # noqa: BLE001 - try the smaller perturbation
                continue
        assert after is not None, f"no perturbation of {t} exported"
        moved = _moved_kinds(base, after)
        for kind in moved:
            assert set(R.DECLARED["nfl"][kind]) & set(R.TABLE_SOURCES[t]), (
                f"changing {t} moved {kind}, which declares none of {R.TABLE_SOURCES[t]}")
        checked[t] = sorted(moved)
    monkeypatch.setattr(config, "DB_PATH", base_db)
    # Discriminates: every populated table was perturbed, and the four the fixture
    # fills with player data each moved at least one kind - else nothing was tested.
    for t in ("nfl_games", "nfl_player_week", "nfl_snap_counts", "player_xwalk"):
        assert checked.get(t), (t, checked)
    return checked


def test_a_market_file_moves_the_index_summary_and_manifest_which_all_declare_kalshi(db):  # noqa: F811
    """has_market is not SQL - it is market_keys, off the market part or off disk. The
    only honest check is to add a market file and see which kinds move."""
    a = str(db / "a")
    summary = E.export(only=["players", "manifest"], now_ts=NOW, dest=a)
    pkey = summary["current"]["period"]["key"]
    b = str(db / "b")
    E.write_if_changed(E.local_path(b, f"nfl/market/00-A/{pkey}.json"), {
        "identity": {"team": "buf"},
        "components": [{"stat": "receptions", "basis": "MARKET", "rungs": [{"line": 3.5}]}]})
    E.export(only=["players", "manifest"], now_ts=NOW, dest=b)
    ca, cb = _canon(a), _canon(b)
    cb = {k: v for k, v in cb.items() if not k.startswith("nfl/market/")}
    moved = _moved_kinds(ca, cb)
    assert {"player_index", "player_summary", "sport_manifest"} <= moved, moved
    for kind in moved:
        assert "kalshi.ladders" in R.DECLARED["nfl"][kind], kind


# ------------------------------------------------------------------ the export gate

def test_require_declared_returns_what_it_approved():
    got = R.require_declared(["team", "market"])
    assert got[("nfl", "team")] == R.DECLARED["nfl"]["team"]
    assert "kalshi.price_history" in got[("nfl", "market")]


def test_the_gate_keys_on_the_files_sport(registry):
    files = {"cfb/manifest.json": {"kind": "sport_manifest", "sport": "cfb"}}
    assert R.require_declared(files) == {("cfb", "sport_manifest"): ("sportsdataverse.cfb",)}
    registry.setitem(R.DECLARED, "cfb", {})
    with pytest.raises(R.SourceRegistryError, match="cfb/sport_manifest: no source declaration"):
        R.require_declared(files)
    with pytest.raises(R.SourceRegistryError, match="'nba' has no declarations"):
        R.require_declared({"nba/manifest.json": {"kind": "sport_manifest", "sport": "nba"}})


def test_the_export_refuses_a_kind_whose_sources_are_undeclared(db, registry):  # noqa: F811
    dest = str(db / "out")
    registry.setitem(R.DECLARED, "nfl", {k: v for k, v in R.DECLARED["nfl"].items() if k != "team"})
    with pytest.raises(R.SourceRegistryError, match="team"):
        E.export(only=["teams"], now_ts=NOW, dest=dest)
    assert not any(k.startswith("nfl/teams/") for k in E.local_keys(dest)), "wrote before refusing"


def test_the_export_passes_the_same_run_when_declared(db):  # noqa: F811
    dest = str(db / "out")
    E.export(only=["teams"], now_ts=NOW, dest=dest)
    assert any(k.startswith("nfl/teams/") for k in E.local_keys(dest))


def test_every_producer_writes_through_sync_keys():
    """f-19: export_mlb_web wrote around sync_keys, so the gate never ran for MLB. By
    AST: each producer's export() calls sync_keys and calls no write_if_changed."""
    for mod in ("jobs.export_web", "jobs.export_cfb_web", "jobs.export_mlb_web"):
        fn = next(n for n in ast.parse(_src(mod)).body
                  if isinstance(n, ast.FunctionDef) and n.name == "export")
        called = {c.func.id for c in ast.walk(fn)
                  if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)}
        assert "sync_keys" in called, mod
        assert "write_if_changed" not in called, mod
        assert any(isinstance(c, ast.Attribute) and c.attr == "watch" for c in ast.walk(fn)) or \
            mod == "jobs.export_web", mod


def test_the_mlb_export_is_refused_when_its_kind_is_undeclared(tmp_path, registry):
    from jobs import export_mlb_web as M
    from tests import test_ingest_mlb as T
    registry.setattr(config, "STORAGE_DIR", str(tmp_path))
    con = T.J.connect()
    T._fetch(con, T.bundle())
    con.close()
    out = str(tmp_path / "probe")
    M.export(out, [T.SEASON], verbose=False)             # declared: writes
    assert os.path.exists(os.path.join(out, "mlb", "manifest.json"))
    assert R._READS["mlb"] <= set(R.SPORT_TABLE_SOURCES["mlb"]) and R._READS["mlb"]
    registry.setitem(R.DECLARED, "mlb", {k: v for k, v in R.DECLARED["mlb"].items()
                                         if k != "team"})
    out2 = str(tmp_path / "probe2")
    with pytest.raises(R.SourceRegistryError, match="mlb/team"):
        M.export(out2, [T.SEASON], verbose=False)
    assert not os.path.exists(os.path.join(out2, "mlb", "manifest.json"))


def test_the_cfb_export_reads_only_mapped_tables(tmp_path, store):  # noqa: F811
    from jobs import export_cfb_web as C
    files, _ = C.export(str(tmp_path / "out"), verbose=False)
    assert files and R._READS["cfb"], "the CFB export read nothing - the ledger is not on"
    assert R._READS["cfb"] <= set(R.SPORT_TABLE_SOURCES["cfb"]), R._READS["cfb"]


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
    odds = rows["oddsapi"]
    assert odds["name"] == "The Odds API"
    assert "player_summary" in odds["read_by"] and "research.hypotheses" in odds["read_by"]
    assert "2023-2025" in odds["provides"] and "2026" in odds["provides"]
    ph = rows["kalshi.price_history"]
    assert "market" in ph["read_by"] and "not retained" not in ph["provides"].lower()
    sched = rows["nflverse.schedule"]
    assert "team" in sched["read_by"] and "fixture" in sched["used_for"].lower()
    # and f-19's: the index lists Kalshi
    assert "player_index" in rows["kalshi.ladders"]["read_by"]


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
    assert rows["nflverse.stats"]["last_read"] is None
    assert "no successful read" in rows["nflverse.stats"]["last_read_basis"]
    assert rows["nflverse.injuries"]["last_read"] is None
    assert "feeds.db" in rows["nflverse.injuries"]["last_read_basis"]


def test_not_connected_rides_the_same_file(db):  # noqa: F811
    _, obj = _sources_file(db)
    assert obj["not_connected"] == R.NOT_CONNECTED and len(obj["not_connected"]) > 0
