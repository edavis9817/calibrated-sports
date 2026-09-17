"""W07 track C phase 1: the CFB ingest.

Run: pytest -q tests/test_ingest_cfb.py

Synthetic throughout, with a mocked HTTP transport: nothing here touches the
network, the real store or the NFL logger. Each test pins a behaviour that was
either measured on the real files or is the kind of failure that looks like
success - an unbounded loop, a silent drop, a column that is always False, a
version applied out of order.
"""
import io
import json
import os
import re
import sqlite3

import httpx
import polars as pl
import pytest

import config
from cfb import fetch, lock, normalize, paths, schema, sources, versioning
from jobs import ingest_cfb


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "STORAGE_DIR", str(tmp_path))
    monkeypatch.setattr(fetch, "DOWNLOAD_GAP_S", 0)
    paths.ensure_dirs()
    conn = ingest_cfb.connect()
    yield conn
    conn.close()


def _parquet(df: pl.DataFrame) -> bytes:
    buf = io.BytesIO()
    df.write_parquet(buf)
    return buf.getvalue()


# =============================================================================
# paths and the registry
# =============================================================================

def test_every_path_derives_from_storage_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "STORAGE_DIR", str(tmp_path))
    for p in (paths.db_path(), paths.raw_root(), paths.cache_root(), paths.checkpoints_root()):
        assert p.startswith(str(tmp_path))


def test_cfb_raw_is_outside_the_nfl_loggers_raw_tree(tmp_path, monkeypatch):
    """The logger adopts and rotates every file under its RAW_DIR. It did so to
    the CFB probe's 115 shards."""
    monkeypatch.setattr(config, "STORAGE_DIR", str(tmp_path))
    logger_raw = os.path.join(str(tmp_path), "raw")
    assert not os.path.abspath(paths.raw_root()).startswith(os.path.abspath(logger_raw) + os.sep)


def test_espn_betting_is_denied_by_name():
    with pytest.raises(ValueError, match="fabricates"):
        sources.get("espn_cfb_betting")


def test_espn_betting_is_denied_by_tag_under_any_name(monkeypatch):
    rogue = sources.Dataset("lines", sources.SDV_REPO, "espn_cfb_betting",
                            "cfb_betting_{season}.parquet", 2004, 2026, "cfb_games")
    monkeypatch.setitem(sources.DATASETS, "lines", rogue)
    with pytest.raises(ValueError, match="denied tag"):
        sources.get("lines")


def test_plan_is_finite_and_filters_seasons():
    full = sources.plan()
    assert 0 < len(full) < 200
    only = sources.plan(["player_box", "nfl_players"], {2026})
    assert [(d.name, s) for d, s in only] == [("player_box", 2026), ("nfl_players", None)]


def test_season_spec_parsing():
    assert ingest_cfb.parse_seasons("2026") == {2026}
    assert ingest_cfb.parse_seasons("2004-2006,2010") == {2004, 2005, 2006, 2010}
    assert ingest_cfb.parse_seasons("all") is None


# =============================================================================
# the schema: stats and usage, never settlement
# =============================================================================

def test_schema_has_no_settlement_or_hit_rate_shapes():
    """CFB has no appearance signal, so it cannot carry hit rates, prop history
    or settlement. If a table shaped like one appears, the design was broken."""
    banned = re.compile(r"settle|hit_?rate|cleared|missed|outcome|void|prop|over_under|"
                        r"did_not_play|played|appear", re.I)
    for table, (_key, cols) in schema.TABLES.items():
        assert not banned.search(table), table
        for c, _t in cols:
            assert not banned.search(c), f"{table}.{c}"


def test_every_fact_table_carries_sport_and_version_columns(store):
    for table in schema.TABLES:
        cols = {r[1] for r in store.execute(f"PRAGMA table_info({table})")}
        assert {"sport", "valid_from_ts", "valid_to_ts", "src_file_id", "row_sha"} <= cols


def test_no_derived_shares_or_model_outputs_are_stored():
    stored = {c for _k, cols in schema.TABLES.values() for c, _t in cols}
    for derived in ("target_share", "touch_share", "epa", "adjQBR", "qbr",
                    "yards_per_reception", "success_rate"):
        assert derived not in stored


def test_limitations_are_recorded_with_the_appearance_gap_first_class(store):
    row = store.execute("SELECT severity, consequence, evidence_keys FROM cfb_limitations "
                        "WHERE id='cfb.no_appearance_signal'").fetchone()
    assert row[0] == "structural"
    assert "no hit rates" in row[1]
    assert "game_rosters.did_not_play_true_rows" in json.loads(row[2])


# =============================================================================
# normalisers
# =============================================================================

def _box(rows):
    cols = ["stat_1", "stat_2", "stat_3", "stat_4", "stat_5", "category", "athlete_id",
            "athlete_name", "team_id", "game_id", "season", "completions/passingAttempts",
            "passingYards", "passingTouchdowns", "interceptions", "receptions",
            "receivingYards", "receivingTouchdowns", "interceptionYards",
            "interceptionTouchdowns", "sacks"]
    return pl.DataFrame([{c: r.get(c) for c in cols} for r in rows],
                        schema={c: (pl.Int64 if c in ("athlete_id", "team_id", "game_id", "season")
                                    else pl.Utf8) for c in cols})


def _get(norm, **match):
    cols = schema.columns(norm.table)
    for r in norm.rows:
        d = dict(zip(cols, r))
        if all(d[k] == v for k, v in match.items()):
            return d
    raise AssertionError(f"no row {match}")


def test_passing_reads_both_layouts_in_one_file():
    """466 of 534 passing rows in 2026 carry (C/ATT, YDS, AVG, TD, INT) in
    stat_1..stat_5 instead of the named columns."""
    df = _box([
        dict(category="passing", athlete_id=1, athlete_name="Named", team_id=10, game_id=100,
             season=2026, **{"completions/passingAttempts": "20/31", "passingYards": "250",
                             "passingTouchdowns": "3", "interceptions": "1"}),
        dict(category="passing", athlete_id=2, athlete_name="Positional", team_id=11,
             game_id=100, season=2026, stat_1="11/17", stat_2="192", stat_3="11.3",
             stat_4="2", stat_5="0"),
    ])
    n = normalize.player_box(df, 2026)
    a, b = _get(n, athlete_id=1), _get(n, athlete_id=2)
    assert (a["pass_cmp"], a["pass_att"], a["pass_yds"], a["pass_td"], a["pass_int"]) == (20, 31, 250, 3, 1)
    assert (b["pass_cmp"], b["pass_att"], b["pass_yds"], b["pass_td"], b["pass_int"]) == (11, 17, 192, 2, 0)
    assert dict((k, v) for k, v, _ in n.measurements)["player_box.passing_rows_positional_layout"] == 1


def test_interceptions_thrown_and_made_are_told_apart_by_category():
    df = _box([
        dict(category="passing", athlete_id=1, team_id=10, game_id=100, season=2025,
             **{"completions/passingAttempts": "1/2", "passingYards": "5",
                "passingTouchdowns": "0", "interceptions": "2"}),
        dict(category="interceptions", athlete_id=1, team_id=10, game_id=100, season=2025,
             interceptions="1", interceptionYards="30", interceptionTouchdowns="1"),
    ])
    r = _get(normalize.player_box(df, 2025), athlete_id=1)
    assert (r["pass_int"], r["def_int"], r["def_int_yds"], r["def_int_td"]) == (2, 1, 30, 1)
    assert r["categories"] == "interceptions,passing"


def test_categories_merge_into_one_row_per_player_game():
    df = _box([
        dict(category="receiving", athlete_id=5, team_id=10, game_id=100, season=2025,
             receptions="4", receivingYards="61", receivingTouchdowns="1"),
        dict(category="defensive", athlete_id=5, team_id=10, game_id=100, season=2025,
             sacks="1.5"),
    ])
    n = normalize.player_box(df, 2025)
    assert len(n.rows) == 1
    r = _get(n, athlete_id=5)
    assert (r["rec"], r["rec_yds"], r["rec_td"], r["sacks"]) == (4, 61, 1, 1.5)


def test_a_repeated_category_row_refuses_the_file():
    row = dict(category="receiving", athlete_id=5, team_id=10, game_id=100, season=2025,
               receptions="4")
    with pytest.raises(normalize.RefusedFile):
        normalize.player_box(_box([row, dict(row, receptions="5")]), 2025)


def test_usage_drops_rows_without_a_player_id_and_counts_them():
    df = pl.DataFrame({"game_id": [1, 1, 1], "season": [2025] * 3, "week": [1] * 3,
                       "pos_team_id": [9] * 3, "player_id": ["44", None, "45"],
                       "player_name": ["A", "Barika Kpeenu 15 Yd", "B"],
                       "position_group": ["RB"] * 3, "targets": [3, 1, 0],
                       "team_targets": [22] * 3})
    n = normalize.player_usage(df, 2025)
    assert len(n.rows) == 2
    assert n.dropped == {"null_athlete_id": 1}
    m = {k: (v, det) for k, v, det in n.measurements}
    assert m["player_usage.unattributed_targets"] == (1, "of 4 targets")
    r = _get(n, athlete_id=44)
    assert (r["targets"], r["team_targets"]) == (3, 22)


def test_game_rosters_measure_did_not_play_and_never_store_it():
    df = pl.DataFrame({"game_id": [1] * 3, "season": [2025] * 3, "week": [1] * 3,
                       "team_id": [7] * 3, "athlete_id": [1, 2, 3], "jersey": ["1", "2", "3"],
                       "starter": [True, False, False], "did_not_play": [False] * 3})
    n = normalize.game_rosters(df, 2025)
    m = {k: v for k, v, _ in n.measurements}
    assert m["game_rosters.did_not_play_true_rows"] == 0
    assert m["game_rosters.team_games_no_starters"] == 0
    assert "did_not_play" not in schema.columns("cfb_game_rosters")


def test_duplicate_keys_in_one_file_are_dropped_not_picked():
    df = pl.DataFrame({"season": [2025, 2025, 2025], "team_id": [1, 1, 2],
                       "abbreviation": ["A", "B", "C"]})
    n = normalize.teams(df, 2025)
    assert len(n.rows) == 1 and n.dropped == {"duplicate_key_rows": 2}


# =============================================================================
# versioning
# =============================================================================

def test_content_hash_ignores_order_but_not_values():
    a = pl.DataFrame({"x": [1, 2], "y": ["p", "q"]})
    assert versioning.content_sha256(a) == versioning.content_sha256(a.reverse().select("y", "x"))
    assert versioning.content_sha256(a) != versioning.content_sha256(a.with_columns(pl.lit(3).alias("x")))


def _team_rows(**abbr):
    cols = schema.columns("cfb_teams")
    out = []
    for tid, ab in abbr.items():
        d = dict.fromkeys(cols)
        d.update(season=2025, team_id=int(tid[1:]), abbreviation=ab)
        out.append(tuple(d[c] for c in cols))
    return out


def test_versions_are_row_level_idempotent_and_answer_as_of(store):
    v1 = _team_rows(t1="AUB", t2="ALA")
    assert versioning.apply(store, "teams", 2025, 1, 100.0, "cfb_teams", v1) == (2, 0, 0)
    assert versioning.apply(store, "teams", 2025, 1, 100.0, "cfb_teams", v1) == (0, 0, 2)
    v2 = _team_rows(t1="AUB", t2="BAMA", t3="UGA")          # one change, one new
    assert versioning.apply(store, "teams", 2025, 2, 200.0, "cfb_teams", v2) == (2, 1, 1)
    v3 = _team_rows(t1="AUB")                                  # two disappear
    assert versioning.apply(store, "teams", 2025, 3, 300.0, "cfb_teams", v3) == (0, 2, 1)

    def as_of(ts):
        clause, params = versioning.as_of_clause(ts)
        return dict(store.execute(f"SELECT team_id, abbreviation FROM cfb_teams WHERE {clause}",
                                  params).fetchall())
    assert as_of(150) == {1: "AUB", 2: "ALA"}
    assert as_of(250) == {1: "AUB", 2: "BAMA", 3: "UGA"}
    assert as_of(300) == {1: "AUB"}
    assert as_of(50) == {}


def test_an_older_file_cannot_be_applied_after_a_newer_one(store):
    versioning.apply(store, "teams", 2025, 2, 200.0, "cfb_teams", _team_rows(t1="AUB"))
    with pytest.raises(versioning.OutOfOrder):
        versioning.apply(store, "teams", 2025, 1, 100.0, "cfb_teams", _team_rows(t1="AU"))


def test_scopes_do_not_touch_each_other(store):
    versioning.apply(store, "teams", 2025, 1, 100.0, "cfb_teams", _team_rows(t1="AUB"))
    versioning.apply(store, "teams", 2024, 2, 100.0, "cfb_teams",
                     [tuple(2024 if c == "season" else v for c, v in
                            zip(schema.columns("cfb_teams"), _team_rows(t1="AUB")[0]))])
    versioning.rebuild_scope(store, "cfb_teams", "teams", 2024)
    assert store.execute("SELECT COUNT(*) FROM cfb_teams").fetchone()[0] == 1


# =============================================================================
# fetch: bounded, throttled, resumable - against a mocked GitHub
# =============================================================================

class FakeGitHub:
    def __init__(self):
        self.assets = {}          # name -> (bytes, updated_at)
        self.calls = []
        self.fail_listing = None

    def set(self, name, df, updated_at):
        self.assets[name] = (_parquet(df), updated_at)

    def handler(self, request: httpx.Request):
        url = str(request.url)
        self.calls.append(url)
        if "api.github.com" in url:
            if self.fail_listing:
                return httpx.Response(self.fail_listing, text="rate limited")
            return httpx.Response(200, headers={"x-ratelimit-remaining": "50"}, json={"assets": [
                {"name": n, "size": len(b), "updated_at": u,
                 "browser_download_url": f"https://dl.test/{n}"}
                for n, (b, u) in self.assets.items()]})
        name = url.rsplit("/", 1)[-1]
        return httpx.Response(200, content=self.assets[name][0])

    def client(self, conn):
        return fetch.Client(conn, httpx.Client(transport=httpx.MockTransport(self.handler)))


TEAMS = pl.DataFrame({"season": [2025, 2025], "team_id": [1, 2],
                      "abbreviation": ["AUB", "ALA"], "display_name": ["Auburn", "Alabama"]})


def test_fetch_keeps_a_raw_copy_only_when_content_changes(store):
    gh = FakeGitHub()
    plan = sources.plan(["teams"], {2025})
    gh.set("cfb_teams_2025.parquet", TEAMS, "2026-09-16T01:00:00Z")
    client = lambda: gh.client(store)       # one Client per run, as in production

    assert ingest_cfb.run_fetch(store, plan, client=client()) == {"new": 1}
    assert ingest_cfb.run_fetch(store, plan, client=client()) == {"skipped_same_remote": 1}

    gh.set("cfb_teams_2025.parquet", TEAMS, "2026-09-16T02:00:00Z")         # re-upload, same bytes
    assert ingest_cfb.run_fetch(store, plan, client=client()) == {"unchanged_bytes": 1}

    gh.set("cfb_teams_2025.parquet", TEAMS.reverse(), "2026-09-16T03:00:00Z")  # same content
    assert ingest_cfb.run_fetch(store, plan, client=client()) == {"unchanged_content": 1}

    fixed = TEAMS.with_columns(pl.Series("abbreviation", ["AUB", "BAMA"]))
    gh.set("cfb_teams_2025.parquet", fixed, "2026-09-16T04:00:00Z")
    assert ingest_cfb.run_fetch(store, plan, client=client()) == {"new": 1}

    assert store.execute("SELECT COUNT(*) FROM cfb_raw_files").fetchone()[0] == 2
    assert os.listdir(paths.cache_root()) == []        # no .part left behind

    totals = ingest_cfb.run_parse(store, plan)
    assert (totals["files"], totals["inserted"], totals["closed"]) == (2, 3, 1)
    assert dict(store.execute("SELECT team_id, abbreviation FROM cfb_teams "
                              "WHERE valid_to_ts IS NULL").fetchall()) == {1: "AUB", 2: "BAMA"}
    assert ingest_cfb.run_parse(store, plan)["files"] == 0     # nothing left to parse
    assert ingest_cfb.audit(store)


def test_rebuild_replays_the_archive_to_the_same_state(store):
    gh = FakeGitHub()
    plan = sources.plan(["teams"], {2025})
    gh.set("cfb_teams_2025.parquet", TEAMS, "u1")
    ingest_cfb.run_fetch(store, plan, client=gh.client(store))
    gh.set("cfb_teams_2025.parquet", TEAMS.with_columns(pl.lit("X").alias("abbreviation")), "u2")
    ingest_cfb.run_fetch(store, plan, client=gh.client(store))
    ingest_cfb.run_parse(store, plan)
    before = store.execute("SELECT team_id, abbreviation, valid_from_ts, valid_to_ts FROM cfb_teams "
                           "ORDER BY 1, 3").fetchall()
    n_http = len(gh.calls)
    ingest_cfb.run_parse(store, plan, rebuild=True)
    after = store.execute("SELECT team_id, abbreviation, valid_from_ts, valid_to_ts FROM cfb_teams "
                          "ORDER BY 1, 3").fetchall()
    assert before == after and len(gh.calls) == n_http


def test_a_rate_limit_stops_the_run_without_touching_the_next_file(store):
    gh = FakeGitHub()
    gh.fail_listing = 403
    plan = sources.plan(["teams", "games"], {2024, 2025})
    counts = ingest_cfb.run_fetch(store, plan, client=gh.client(store))
    assert counts == {"stopped_rate_limited": 1}
    assert len(gh.calls) == 1


def test_max_files_bounds_downloads(store):
    gh = FakeGitHub()
    for y in (2023, 2024, 2025):
        gh.set(f"cfb_teams_{y}.parquet", TEAMS.with_columns(pl.lit(y).alias("season")), "u")
    counts = ingest_cfb.run_fetch(store, sources.plan(["teams"], {2023, 2024, 2025}),
                                  max_files=2, client=gh.client(store))
    assert counts == {"new": 2, "deferred_by_max_files": 1}


def test_a_short_download_is_not_manifested(store):
    gh = FakeGitHub()
    gh.set("cfb_teams_2025.parquet", TEAMS, "u")
    real = gh.handler

    def short(request):
        r = real(request)
        if "dl.test" in str(request.url):
            return httpx.Response(200, content=r.content[:10])
        return r
    client = fetch.Client(store, httpx.Client(transport=httpx.MockTransport(short)))
    with pytest.raises(IOError):
        ingest_cfb.run_fetch(store, sources.plan(["teams"], {2025}), client=client)
    assert store.execute("SELECT COUNT(*) FROM cfb_raw_files").fetchone()[0] == 0
    assert os.listdir(paths.cache_root()) == []


def test_an_absent_season_is_recorded_not_invented(store):
    gh = FakeGitHub()
    counts = ingest_cfb.run_fetch(store, sources.plan(["teams"], {2025}), client=gh.client(store))
    assert counts == {"absent": 1}


def test_team_coverage_is_measured_across_datasets(store):
    cols = schema.columns("cfb_games")
    game = dict.fromkeys(cols)
    game.update(game_id=1, season=2005, home_id=10, away_id=11)
    versioning.apply(store, "games", 2005, 1, 100.0, "cfb_games", [tuple(game[c] for c in cols)])
    tcols = schema.columns("cfb_teams")
    team = dict.fromkeys(tcols)
    team.update(season=2005, team_id=10)
    versioning.apply(store, "teams", 2005, 2, 100.0, "cfb_teams", [tuple(team[c] for c in tcols)])
    assert ingest_cfb.measure_joins(store) == [(2005, 1, 2)]


def test_audit_reports_an_unregistered_raw_file(store):
    p = os.path.join(paths.raw_root(), "sportsdataverse", "x", "stray.parquet")
    os.makedirs(os.path.dirname(p))
    TEAMS.write_parquet(p)
    assert ingest_cfb.audit(store) is False


# =============================================================================
# single instance
# =============================================================================

def test_a_second_instance_is_refused(tmp_path):
    p = str(tmp_path / "ingest.lock")
    with lock.InstanceLock(p):
        with pytest.raises(lock.AlreadyRunning):
            with lock.InstanceLock(p):
                pass
    with lock.InstanceLock(p):          # released on exit
        pass
