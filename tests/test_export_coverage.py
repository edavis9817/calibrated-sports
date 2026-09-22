"""The sport-coverage feed: read-only, counts read not typed, nothing is null not zero.

Run: pytest -q tests/test_export_coverage.py

Every fixture store is INVENTED and lives under tmp_path; `config.STORAGE_DIR` is pinned
there, so no test can reach the production stores. Validation runs against the real
contract plus the real proposal, because validating against a copy would test the copy.
"""
import ast
import json
import os
import re
import sqlite3

import pytest

import config
from jobs import export_coverage as X
from jobs.export_web import CONTRACT

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SITE_SPORTS = os.path.join(os.path.dirname(REPO), "calibratedsports-web", "config", "sports")

V = "valid_from_ts REAL, valid_to_ts REAL, src_dataset TEXT"
DDL = {
    "market_log.db": {
        "nfl_player_week": "sport TEXT, gsis_id TEXT, season INT, week INT, season_type TEXT, "
                           "data_version TEXT, source TEXT, ingested_ts REAL",
        "nfl_games": "sport TEXT, game_id TEXT, data_version TEXT, season INT, kickoff_ts REAL, "
                     "spread_line REAL, total_line REAL, source TEXT, ingested_ts REAL",
        "nfl_snap_counts": "sport TEXT, pfr_player_id TEXT, game_id TEXT, data_version TEXT, "
                           "season INT, source TEXT, ingested_ts REAL",
        "quotes": "id INTEGER PRIMARY KEY, ts REAL, sport TEXT, venue TEXT, market_id TEXT, "
                  "source TEXT, ingest_ts REAL",
        "outcomes": "outcome_id TEXT, sport TEXT, season INT",
        "outcome_close": "outcome_id TEXT, kickoff_ts REAL, built_ts REAL",
        "markets": "venue TEXT, market_id TEXT, sport TEXT",
        "market_depth": "ts REAL, venue TEXT, market_id TEXT",
        "market_trades": "trade_id TEXT, venue TEXT, market_id TEXT, ts REAL, ingest_ts REAL",
    },
    "cfb.db": {
        "cfb_games": f"sport TEXT, game_id INT, season INT, start_ts REAL, {V}",
        "cfb_player_game_box": f"sport TEXT, game_id INT, athlete_id INT, season INT, {V}",
        "cfb_player_game_usage": f"sport TEXT, game_id INT, athlete_id INT, season INT, {V}",
        "cfb_game_rosters": f"sport TEXT, game_id INT, team_id INT, athlete_id INT, season INT, {V}",
        "cfb_rosters": f"sport TEXT, team_id INT, athlete_id INT, season INT, {V}",
        "cfb_teams": f"sport TEXT, team_id INT, season INT, {V}",
        "cfb_rankings": f"sport TEXT, season INT, season_type TEXT, week INT, poll TEXT, "
                        f"team_id INT, {V}",
        "cfb_cfbd_games": f"sport TEXT, game_id INT, season INT, start_ts REAL, {V}",
        "cfb_game_lines": f"sport TEXT, game_id INT, season INT, provider TEXT, start_ts REAL, {V}",
        "cfb_odds_quotes": "sport TEXT, event_id TEXT, commence_ts REAL, bookmaker TEXT, "
                           "fetched_ts REAL",
        "cfb_exchange_closes": f"sport TEXT, venue TEXT, market_id TEXT, kickoff_ts REAL, "
                               f"capture TEXT, {V}",
    },
    "feeds.db": {
        "injury_reports": f"sport TEXT, season INT, season_type TEXT, week INT, team TEXT, "
                          f"player_id TEXT, {V}",
        "venues": f"sport TEXT, season INT, venue_id INT, {V}",
        "weather_at_kickoff": f"sport TEXT, game_id INT, provider TEXT, kind TEXT, "
                              f"kickoff_ts REAL, {V}",
        "news_items": f"sport TEXT, feed TEXT, guid TEXT, published_ts REAL, {V}",
    },
}

SEP_2026 = 1789000000.0     # 2026-09-10
JAN_2026 = 1768000000.0     # 2026-01-09, the 2025 season


@pytest.fixture
def stores(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "STORAGE_DIR", str(tmp_path))
    monkeypatch.delenv("WEB_EXPORT_DIR", raising=False)
    cons = {}
    for store in X.STORES:
        con = sqlite3.connect(tmp_path / store)
        for t, cols in DDL[store].items():
            con.execute(f"CREATE TABLE {t} ({cols})")
        for t in X.IGNORED[store]:
            if t not in DDL[store]:
                con.execute(f"CREATE TABLE {t} (sport TEXT)")
        cons[store] = con
    yield cons
    for c in cons.values():
        c.close()


def put(cons, store, table, **row):
    cons[store].execute(f"INSERT INTO {table} ({', '.join(row)}) "
                        f"VALUES ({', '.join('?' * len(row))})", list(row.values()))
    cons[store].commit()


def run(**kw):
    groups, stores = X.read_stores(log=lambda *_: None)
    return X.build(groups, stores, "2026-09-22T00:00:00Z", **kw)


def sport(obj, s):
    return next(x for x in obj["sports"] if x["sport"] == s)


# ---------------------------------------------------------------------------

def test_empty_stores_give_null_for_every_sport_and_validate(stores):
    obj = run()
    assert [s["sport"] for s in obj["sports"]] == list(X.SPORTS)
    for s in obj["sports"]:
        assert (s["stats"], s["odds"], s["context"]) == (None, None, None)
    assert X.validate(obj).startswith("coverage.json valid against")
    assert [s["store"] for s in obj["stores"]] == list(X.STORES)
    assert all(s["tables"] == s["classified"] for s in obj["stores"])


def test_a_null_can_become_a_value_from_the_same_pipeline(stores):
    """Falsifiable: MLB is null only because no store holds it. One row makes it not."""
    assert sport(run(), "mlb")["stats"] is None
    put(stores, "cfb.db", "cfb_games", sport="mlb", game_id=1, season=2026, start_ts=SEP_2026,
        valid_from_ts=SEP_2026)
    got = sport(run(), "mlb")
    assert got["stats"]["seasons"] == [2026]
    assert got["stats"]["sources"][0]["units"] == 1
    assert got["odds"] is None and got["context"] is None


def test_units_count_distinct_facts_not_versions(stores):
    for dv in ("v1", "v2"):
        put(stores, "market_log.db", "nfl_games", sport="nfl", game_id="2025_01_A_B",
            data_version=dv, season=2025, kickoff_ts=SEP_2026, spread_line=3.0,
            source="nflverse", ingested_ts=SEP_2026)
    nfl = sport(run(), "nfl")
    games = next(s for s in nfl["stats"]["sources"] if s["table"] == "nfl_games")
    assert (games["units"], games["rows"]) == (1, 2)
    lines = nfl["odds"]["sources"][0]
    assert (lines["table"], lines["units"]) == ("nfl_games", 1)


def test_superseded_versions_are_not_counted(stores):
    put(stores, "cfb.db", "cfb_teams", sport="cfb", team_id=1, season=2026,
        valid_from_ts=1.0, valid_to_ts=2.0)
    assert sport(run(), "cfb")["stats"] is None
    put(stores, "cfb.db", "cfb_teams", sport="cfb", team_id=1, season=2026, valid_from_ts=2.0)
    assert sport(run(), "cfb")["stats"]["sources"][0]["rows"] == 1


def test_seasons_list_gaps_and_the_event_time_rule(stores):
    for season in (2013, 2015):
        put(stores, "cfb.db", "cfb_game_lines", sport="cfb", game_id=season, season=season,
            provider="Bovada", start_ts=SEP_2026, valid_from_ts=SEP_2026)
    put(stores, "cfb.db", "cfb_odds_quotes", sport="cfb", event_id="e", commence_ts=JAN_2026,
        bookmaker="dk", fetched_ts=JAN_2026)
    odds = sport(run(), "cfb")["odds"]
    assert odds["seasons"] == [2013, 2015, 2025]   # a gap is listed, not spanned
    q = next(s for s in odds["sources"] if s["table"] == "cfb_odds_quotes")
    assert (q["seasons"], q["season_basis"]) == ([2025], "event_time")


def test_retention_is_rolling_only_for_prunable_sources(stores):
    for src in ("live", "oddsapi_historical"):
        put(stores, "market_log.db", "quotes", ts=SEP_2026, sport="nfl", venue="oddsapi:dk",
            market_id="m", source=src, ingest_ts=SEP_2026)
    put(stores, "market_log.db", "quotes", ts=SEP_2026, sport="nfl", venue="oddsapi:fd",
        market_id="m", source="live", ingest_ts=SEP_2026)
    got = {s["source"]: s for s in sport(run(prune_sources=("live",)), "nfl")["odds"]["sources"]}
    assert got["oddsapi live"]["retention"] == "rolling"
    assert got["oddsapi live"]["providers"] == 2
    assert got["oddsapi oddsapi_historical"]["retention"] == "kept"
    got = {s["source"]: s for s in sport(run(prune_sources=()), "nfl")["odds"]["sources"]}
    assert got["oddsapi live"]["retention"] == "kept"


def test_joined_sport_comes_from_the_join(stores):
    put(stores, "market_log.db", "markets", venue="kalshi", market_id="K1", sport="nfl")
    put(stores, "market_log.db", "market_depth", ts=SEP_2026, venue="kalshi", market_id="K1")
    put(stores, "market_log.db", "outcomes", outcome_id="o1", sport="nfl", season=2024)
    put(stores, "market_log.db", "outcome_close", outcome_id="o1", kickoff_ts=SEP_2026,
        built_ts=SEP_2026)
    tables = {s["table"] for s in sport(run(), "nfl")["odds"]["sources"]}
    assert tables == {"market_depth", "outcome_close"}
    assert "market_trades" not in tables              # empty table: omitted, not a 0


def test_an_unclassified_table_refuses(stores):
    stores["cfb.db"].execute("CREATE TABLE mlb_player_game (sport TEXT)")
    with pytest.raises(X.CoverageError, match="neither counted nor ignored.*mlb_player_game"):
        run()


def test_a_missing_store_refuses_rather_than_reporting_nothing(stores, tmp_path):
    stores["feeds.db"].close()
    os.remove(tmp_path / "feeds.db")
    with pytest.raises(X.CoverageError, match="store is missing"):
        run()


def test_an_undeclared_sport_value_refuses(stores):
    put(stores, "market_log.db", "player_xwalk", sport="xfl")
    with pytest.raises(X.CoverageError, match="xfl"):
        run()


def test_stores_are_opened_read_only(stores, tmp_path):
    con = X.connect_ro(str(tmp_path / "cfb.db"))
    with pytest.raises(sqlite3.OperationalError, match="readonly"):
        con.execute("INSERT INTO cfb_teams (sport) VALUES ('cfb')")
    con.close()


def test_refuses_to_write_into_the_published_directory(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "STORAGE_DIR", str(tmp_path))
    pub = tmp_path / "published"
    monkeypatch.setenv("WEB_EXPORT_DIR", str(pub))
    with pytest.raises(X.CoverageError, match="publishes"):
        X.refuse_published_dir(str(pub))
    with pytest.raises(X.CoverageError, match="publishes"):
        X.refuse_published_dir(str(tmp_path / "web_export"))
    X.refuse_published_dir(str(tmp_path / "elsewhere"))       # and allows anything else


def test_main_writes_only_where_told(stores, tmp_path):
    out = tmp_path / "out"
    assert X.main(["--out", str(out)]) == 0
    assert os.listdir(out) == ["coverage.json"]
    obj = json.loads((out / "coverage.json").read_text(encoding="utf-8"))
    assert obj["kind"] == "coverage" and obj["sport"] is None


# ---- the schema: a proposal until track A adopts it, and it must discriminate

def _valid_obj():
    count = {"store": "cfb.db", "table": "cfb_games", "source": "s", "grain": "game",
             "units": 1, "rows": 1, "providers": None, "seasons": [2026],
             "season_basis": "column", "span": None, "ingested_through": None,
             "retention": "kept", "counted_at": "2026-09-22T00:00:00Z"}
    return {"schema_version": 2, "generated_at": "2026-09-22T00:00:00Z", "kind": "coverage",
            "sport": None,
            "sports": [{"sport": "cfb", "stats": {"seasons": [2026], "sources": [count]},
                        "odds": None, "context": None}],
            "stores": [{"store": "cfb.db", "read_at": "2026-09-22T00:00:00Z", "tables": 1,
                        "classified": 1, "sports_seen": ["cfb"]}]}


def test_the_schema_accepts_a_real_holding():
    X.validate(_valid_obj())


@pytest.mark.parametrize("breaks", [
    lambda o: o["sports"][0].update(stats={"seasons": [2026], "sources": []}),  # present, empty
    lambda o: o["sports"][0]["stats"]["sources"][0].update(units=0),          # zero-with-a-plan
    lambda o: o["sports"][0]["stats"]["sources"][0].update(units=2),          # units > rows
    lambda o: o["sports"][0]["stats"].update(seasons=[2025, 2026]),           # not the union
    lambda o: o["sports"][0].pop("odds"),                                     # absent != null
    lambda o: o["sports"][0]["stats"]["sources"][0].update(extra=1),          # closed object
    lambda o: o.update(stores=[]),                                            # nothing searched
])
def test_the_schema_refuses_what_it_exists_to_refuse(breaks):
    obj = _valid_obj()
    breaks(obj)
    with pytest.raises(X.CoverageError):
        X.validate(obj)


def test_proposal_and_contract_never_both_carry_the_kind():
    """On adoption the proposal is deleted; until then the contract must not have the kind,
    or two definitions of one shape would drift."""
    if X.contract_has_kind(CONTRACT):
        assert not os.path.exists(X.PROPOSAL_PATH), "adopted: delete docs/proposals/coverage.defs.json"
    else:
        prop = X.load_proposal()
        assert not set(prop["$defs"]) & set(CONTRACT["$defs"])
        add = prop["x-contract-additions"]
        assert add["kinds"] == {"coverage": "CoverageFile"}
        assert re.match(add["keys"][0]["pattern"], X.KEY)
        assert not any(re.match(k["pattern"], X.KEY) for k in CONTRACT["x-contract"]["keys"])


# ---- declarations that must match something outside this file

@pytest.mark.skipif(not os.path.isdir(SITE_SPORTS),
                    reason="calibratedsports-web is not checked out beside this repo")
def test_sports_match_the_sites_declaration():
    idx = open(os.path.join(SITE_SPORTS, "index.ts"), encoding="utf-8").read()
    names = re.search(r"SPORTS:\s*readonly SportConfig\[\]\s*=\s*\[([^\]]*)\]", idx).group(1)
    ids = []
    for name in [n.strip() for n in names.split(",") if n.strip()]:
        src = open(os.path.join(SITE_SPORTS, f"{name}.ts"), encoding="utf-8").read()
        ids.append(re.search(r'\bsport:\s*"([a-z0-9]+)"', src).group(1))
    assert tuple(ids) == X.SPORTS


def test_every_registered_query_runs_on_the_fixture(stores):
    """The fixture DDL is the registry's column list; a query naming a column the fixture
    lacks would fail here rather than first on the production store."""
    for store in X.STORES:
        for e in [e for e in X.REGISTRY if e["store"] == store]:
            stores[store].execute(e["sql"]).fetchall()
    assert {e["cls"] for e in X.REGISTRY} == set(X.CLASSES)


def test_no_publish_path():
    tree = ast.parse(open(X.__file__, encoding="utf-8").read())
    mods = {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    mods |= {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
    assert not mods & {"r2", "boto3", "botocore", "httpx", "requests", "store"}
    calls = {n.func.attr if isinstance(n.func, ast.Attribute) else getattr(n.func, "id", None)
             for n in ast.walk(tree) if isinstance(n, ast.Call)}
    assert not calls & {"upload", "put_object", "sync_keys"}


def test_season_rule():
    con = sqlite3.connect(":memory:")
    for ts, want in ((JAN_2026, 2025), (SEP_2026, 2026), (1772409600.0, 2026)):  # 2026-03-02
        assert con.execute(f"SELECT {X.season_of('?')}".replace("?", str(ts))).fetchone()[0] == want
