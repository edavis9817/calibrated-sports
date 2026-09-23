"""a-15: the current period's fixtures, and air yards / red-zone looks - staged.

Run: pytest -q tests/test_a15_fixtures_air_rz.py

Track B's A-B5 and A-B8 (docs/track-a-requests.md). What is pinned here:

  * THE SPREAD'S DIRECTION, AGAINST NAMED GAMES. c-14 shipped 21,378 CFB rows
    with the spread inverted because the tests asserted the transform, and the
    transform was what was wrong. So the direction test here asserts an outside
    fact - who was favoured - and does not mention the transform at all. Two
    games, the same favourite once at home and once away, so neither the
    identity-negated nor an absolute-value transform can pass both.
  * THE GATE. Both features build only with `--stage NAME --dest DIR`; the
    weekly refresh runs this exporter from the working clone's checked-out
    branch and uploads what it builds.
  * NULL IS NOT ZERO. Air yards 1999-2008 and red-zone targets 2003-2008 are not
    recorded; a game play-by-play has not reached is unknown. Each is shown
    producing the other answer where it IS recorded.
  * A RED-ZONE LOOK IS A SUBSET OF A LOOK. Counted on nflverse's own target and
    carry definition, so it can never exceed the Tgt / Car beside it.
"""
import io
import json
import os
import sqlite3

import polars as pl
import pytest
from jsonschema import Draft202012Validator

import config
import store
from jobs import export_web as E
from jobs import ingest_nflverse as I


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setattr(config, "RAW_DIR", str(tmp_path / "raw"))
    monkeypatch.setattr(E, "SLUG_DIR", str(tmp_path / "slugs"))   # never the committed registry
    store.init_db()
    return tmp_path


# --------------------------------------------------- the spread, named games

# The nfl_games rows as nflverse publishes them, copied from the raw archive
# (games.parquet, data_version 2026-09-09) - spread_line, total_line and the
# moneylines verbatim. What each test asserts is NOT in these rows: it is who
# the market favoured, which is public record.
DET_AT_KC_2023 = {   # 2023 opener, Thu 7 Sep. Chiefs favoured at home; Lions won 21-20.
    "game_id": "2023_01_DET_KC", "season": 2023, "week": 1, "game_type": "REG",
    "gameday": "2023-09-07", "kickoff_ts": 1694132400.0, "home_team": "KC", "away_team": "DET",
    "home_score": 20.0, "away_score": 21.0, "spread_line": 4.0, "total_line": 53.0,
    "home_moneyline": -198.0, "away_moneyline": 164.0}
SUPER_BOWL_LIX = {   # Chiefs favoured, Eagles the DESIGNATED home side; Eagles won 40-22.
    "game_id": "2024_22_KC_PHI", "season": 2024, "week": 22, "game_type": "SB",
    "gameday": "2025-02-09", "kickoff_ts": 1739143800.0, "home_team": "PHI", "away_team": "KC",
    "home_score": 40.0, "away_score": 22.0, "spread_line": -1.5, "total_line": 48.5,
    "home_moneyline": 100.0, "away_moneyline": -120.0}


def favourite(fixture):
    """Read a fixture the way the contract's description tells a reader to."""
    if fixture["spread"] is None or fixture["spread"] == 0:
        return None
    return fixture["home"] if fixture["spread"] > 0 else fixture["away"]


def fixtures_of(game):
    current = {"season": game["season"], "period": {"index": game["week"]}}
    return E.current_fixtures({game["game_id"]: game}, current)


def test_the_chiefs_are_the_favourite_at_home_against_detroit():
    (f,) = fixtures_of(DET_AT_KC_2023)
    assert (f["home"], f["away"]) == ("kc", "det")
    assert favourite(f) == "kc"


def test_the_chiefs_are_the_favourite_as_the_designated_away_side_in_super_bowl_lix():
    """The discriminating case: the same favourite, now AWAY. An inverted sign
    names Philadelphia here; an absolute value names Philadelphia too."""
    (f,) = fixtures_of(SUPER_BOWL_LIX)
    assert (f["home"], f["away"]) == ("phi", "kc")
    assert favourite(f) == "kc"


@pytest.mark.parametrize("game", [DET_AT_KC_2023, SUPER_BOWL_LIX])
def test_the_spread_names_the_same_favourite_as_the_moneyline_in_the_same_row(game):
    """A second, independent field in the same source, which nothing here
    transforms: the moneyline favourite carries the lower price."""
    (f,) = fixtures_of(game)
    ml_fav = game["home_team"] if game["home_moneyline"] < game["away_moneyline"] \
        else game["away_team"]
    assert favourite(f) == E.team_slug(ml_fav)


@pytest.mark.skipif(os.getenv("LOGGER_DB") is None,
                    reason="needs LOGGER_DB: a store holding nfl_games (read mode=ro)")
def test_across_every_game_in_the_store_the_spread_and_moneyline_agree():
    """Measured 2026-09-23: 5,306 agree, 21 disagree, and all 21 sit at |spread|
    = 1.0 on near-even moneylines (-103/-107). An inverted sign would disagree
    on ~5,300. So: none may disagree at 1.5 or wider, and most must agree."""
    path = os.getenv("LOGGER_DB").replace("\\", "/")
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        rows = con.execute(
            "SELECT g.spread_line, g.home_moneyline, g.away_moneyline FROM nfl_games g JOIN "
            "(SELECT game_id, MAX(data_version) dv FROM nfl_games GROUP BY game_id) v "
            "ON v.game_id = g.game_id AND v.dv = g.data_version WHERE g.spread_line IS NOT NULL "
            "AND g.spread_line != 0 AND g.home_moneyline IS NOT NULL "
            "AND g.away_moneyline IS NOT NULL AND g.home_moneyline != g.away_moneyline").fetchall()
    finally:
        con.close()
    assert len(rows) > 1000, f"only {len(rows)} games with both lines - nothing to check"
    wide = [r for r in rows if abs(E.fixture_spread(r[0])) >= 1.5
            and (E.fixture_spread(r[0]) > 0) != (r[1] < r[2])]
    agree = sum((E.fixture_spread(r[0]) > 0) == (r[1] < r[2]) for r in rows)
    assert not wide, wide[:5]
    assert agree / len(rows) > 0.99


def test_the_contract_states_the_sign_in_the_definition_itself():
    """In the $def, not in a comment a second implementer never reads."""
    d = E.CONTRACT["$defs"]["Fixture"]["properties"]["spread"]["description"]
    assert "HOME team's perspective" in d
    assert "positive means the home team is favoured" in d
    assert "NOT the sportsbook convention" in d


# ------------------------------------------------------------- the fixtures

def g(gid, season, week, home, away, ko, spread=None, total=None):
    return {"game_id": gid, "season": season, "week": week, "game_type": "REG",
            "gameday": None, "kickoff_ts": ko, "home_team": home, "away_team": away,
            "home_score": None, "away_score": None, "spread_line": spread, "total_line": total}


def test_fixtures_are_the_current_period_only_in_kickoff_order():
    games = {x["game_id"]: x for x in (
        g("b", 2026, 3, "GB", "ATL", 200.0, 5.5, 42.5),
        g("a", 2026, 3, "LV", "DEN", 100.0, -2.0, 40.0),
        g("c", 2026, 4, "KC", "BUF", 50.0),            # next week
        g("d", 2025, 3, "KC", "BUF", 50.0))}           # last season, same index
    out = E.current_fixtures(games, {"season": 2026, "period": {"index": 3}})
    assert [f["game_id"] for f in out] == ["a", "b"]
    assert out[0] == {"game_id": "a", "kickoff_ts": 100.0, "home": "lv", "away": "den",
                      "spread": -2.0, "total": 40.0}


def test_a_game_with_no_line_publishes_null_not_zero():
    (f,) = E.current_fixtures({"x": g("x", 2026, 3, "GB", "ATL", 1.0)},
                              {"season": 2026, "period": {"index": 3}})
    assert f["spread"] is None and f["total"] is None


def test_the_manifest_carries_fixtures_only_when_built_and_the_contract_accepts_both():
    v = E.contract_validators()["sport_manifest"]
    current = {"season": 2026, "period": {"index": 3, "label": "Week 3", "key": "2026-3"},
               "data_through": {"season": 2026, "index": 2}, "stale": False, "stale_reason": None}
    games = {"a": g("a", 2026, 3, "GB", "ATL", 1.0, 5.5, 42.5)}
    args = (games, current, [], {}, [], "2026-09-23", "note", "2026-09-23T00:00:00Z", 0, {}, {}, {})
    plain = E.build_manifest(*args)
    staged = E.build_manifest(*args, fixtures=E.current_fixtures(games, current))
    assert "fixtures" not in plain["current"]
    assert staged["current"]["fixtures"][0]["home"] == "gb"
    assert not list(v.iter_errors(plain))
    assert not list(v.iter_errors(staged))
    staged["current"]["fixtures"][0]["spread"] = "-5.5"      # a string is not a line
    assert list(v.iter_errors(staged))


# ------------------------------------------------------------------ the gate

@pytest.mark.parametrize("stage", E.STAGES)
def test_a_stage_without_dest_is_refused(stage):
    with pytest.raises(SystemExit):
        E.main(["--stage", stage])


def test_a_stage_with_upload_is_refused_even_with_dest(tmp_path):
    with pytest.raises(SystemExit):
        E.main(["--stage", "fixtures", "--dest", str(tmp_path / "out"), "--upload"])


def test_nothing_is_staged_by_default():
    """Publishing a feature is adding it to DEFAULT_STAGES. Until then the
    default export - what the weekly refresh runs - carries neither."""
    assert E.DEFAULT_STAGES == ()
    assert not set(E.AIR_RZ_STAT_DEFINITIONS) & set(E.STAT_DEFINITIONS)


def test_an_unknown_stage_is_refused_not_ignored(db):
    with pytest.raises(E.ConfigError):
        E.export(only=["manifest"], dest=str(db / "out"), stages=("fixture",))


# --------------------------------------------------------- red-zone looks

def pbp(*plays):
    base = {"game_id": "2025_01_A_B", "season": 2025, "week": 1, "season_type": "REG",
            "play_type": "pass", "two_point_attempt": 0, "receiver_player_id": None,
            "rusher_player_id": None, "yardline_100": 50, "posteam": "A"}
    return pl.DataFrame([{**base, **p} for p in plays],
                        schema_overrides={"receiver_player_id": pl.Utf8,
                                          "rusher_player_id": pl.Utf8, "play_type": pl.Utf8,
                                          "yardline_100": pl.Int64})


def looks_of(df):
    return {r["gsis_id"]: r for r in I.pbp_looks(df).iter_rows(named=True)}


def test_the_20_is_in_the_red_zone_and_the_21_is_not():
    out = looks_of(pbp({"receiver_player_id": "R", "yardline_100": 20},
                       {"receiver_player_id": "R", "yardline_100": 21},
                       {"rusher_player_id": "B", "play_type": "run", "yardline_100": 1},
                       {"rusher_player_id": "B", "play_type": "run", "yardline_100": 35}))
    assert (out["R"]["targets"], out["R"]["rz_targets"]) == (2, 1)
    assert (out["B"]["carries"], out["B"]["rz_carries"]) == (2, 1)


def test_two_point_attempts_are_neither_targets_nor_carries():
    out = looks_of(pbp({"receiver_player_id": "R", "yardline_100": 2, "two_point_attempt": 1},
                       {"receiver_player_id": "R", "yardline_100": 2}))
    assert (out["R"]["targets"], out["R"]["rz_targets"]) == (1, 1)


def test_a_kneel_is_a_carry_and_an_untyped_play_still_counts():
    """nflverse counts kneels as carries; 1999-2000 leave ~200 plays a season
    untyped. Dropping either broke the reconciliation against Car / Tgt."""
    out = looks_of(pbp({"rusher_player_id": "Q", "play_type": "qb_kneel", "yardline_100": 70},
                       {"rusher_player_id": "Q", "play_type": None, "yardline_100": 5},
                       {"receiver_player_id": "R", "play_type": None, "yardline_100": 5}))
    assert (out["Q"]["carries"], out["Q"]["rz_carries"]) == (2, 1)
    assert (out["R"]["targets"], out["R"]["rz_targets"]) == (1, 1)


def test_a_sack_or_scramble_naming_nobody_counts_for_nobody():
    out = looks_of(pbp({"play_type": "pass"}, {"receiver_player_id": "R", "yardline_100": 9}))
    assert list(out) == ["R"]


def test_a_look_with_no_yardline_is_counted_as_a_look_and_in_neither_rz_count():
    out = looks_of(pbp({"receiver_player_id": "R", "yardline_100": None}))
    assert (out["R"]["targets"], out["R"]["rz_targets"], out["R"]["no_yardline"]) == (1, 0, 1)


def test_the_normalizer_writes_the_looks_table(db):
    buf = io.BytesIO()
    pbp({"receiver_player_id": "R", "yardline_100": 3}).write_parquet(buf)
    table, cols, rows = I.normalize_pbp(buf.getvalue(), "2026-09-23")
    assert table == "nfl_pbp_looks"
    assert store.replace_rows(table, cols, rows) == 1
    con = sqlite3.connect(config.DB_PATH)
    looks, covered = E.load_looks(con)
    con.close()
    assert looks == {("R", 2025, 1, "REG"): (1, 0)}
    assert covered == {"2025_01_A_B"}


# ------------------------------------------------ what the export publishes

def row(season, **cols):
    return {"season": season, "week": 1, "receiving_air_yards": None, **cols}


def inputs(covered=("G",), looks=None):
    return E.AirRzInputs(looks or {}, set(covered))


def test_air_yards_are_null_where_not_recorded_and_a_number_where_they_are():
    a = inputs()
    for season in (1999, 2002, 2003, 2008):          # partial, then zero-run
        assert a.values(row(season, receiving_air_yards=55.0), "P", "G", "REG")[
            "rec_air_yds"] is None, season
    assert a.values(row(2009, receiving_air_yards=55.0), "P", "G", "REG")["rec_air_yds"] == 55
    assert a.values(row(2009, receiving_air_yards=0.0), "P", "G", "REG")["rec_air_yds"] == 0


def test_a_stored_null_air_yards_stays_null():
    """A row written before the column was ingested: unknown, never 0."""
    assert inputs().values(row(2020), "P", "G", "REG")["rec_air_yds"] is None


def test_a_covered_game_with_no_looks_row_is_a_recorded_zero():
    v = inputs().values(row(2020), "P", "G", "REG")
    assert (v["rz_targets"], v["rz_rush_att"]) == (0, 0)


def test_a_game_play_by_play_has_not_reached_is_null_not_zero():
    v = inputs(covered=()).values(row(2020), "P", "G", "REG")
    assert (v["rz_targets"], v["rz_rush_att"]) == (None, None)
    v = inputs().values(row(2020), "P", None, "REG")          # no joinable game
    assert (v["rz_targets"], v["rz_rush_att"]) == (None, None)


def test_red_zone_targets_2003_2008_are_null_and_carries_are_not():
    a = inputs(looks={("P", 2005, 1, "REG"): (2, 3), ("P", 2009, 1, "REG"): (2, 3)})
    v = a.values(row(2005), "P", "G", "REG")
    assert (v["rz_targets"], v["rz_rush_att"]) == (None, 3)
    v = a.values(row(2009), "P", "G", "REG")
    assert (v["rz_targets"], v["rz_rush_att"]) == (2, 3)


def per(season, **stats):
    return {"season": season, "stats": stats}


def test_a_total_over_an_unrecorded_period_is_null_and_a_clean_one_is_a_sum():
    clean = [per(2010, rec_air_yds=40, rz_targets=1), per(2011, rec_air_yds=-3, rz_targets=2)]
    t = E._totals(clean, airz=True)
    assert (t["rec_air_yds"], t["rz_targets"]) == (37, 3)
    spans = clean + [per(2008, rec_air_yds=None, rz_targets=None)]
    t = E._totals(spans, airz=True)
    assert (t["rec_air_yds"], t["rz_targets"]) == (None, None)


def test_default_totals_carry_no_air_rz_key():
    t = E._totals([per(2010, rec_air_yds=40, rz_targets=1)])
    assert "rec_air_yds" not in t and "rz_targets" not in t


def test_the_components_table_appends_the_columns_and_moves_no_existing_one():
    files = {
        "nfl/players/P/summary.json": {"kind": "player_summary", "identity": {
            "id": "P", "slug": "p", "name": "P", "position": "WR"}},
        "nfl/players/P/2020.json": {"kind": "player_season", "identity": {"id": "P"},
                                    "season": 2020, "periods": [{
                                        "index": 1, "season_type": "REG", "team": "A",
                                        "stats": {"snaps": None, "snap_share": None,
                                                  "target_share": None, "rec_air_yds": 12,
                                                  "rz_targets": None}}]}}
    plain, _ = E.build_components(files, [], {}, {}, "2026-09-23T00:00:00Z")
    staged, _ = E.build_components(files, [], {}, {}, "2026-09-23T00:00:00Z", airz=True)
    p, s = plain["nfl/components/2020.json"], staged["nfl/components/2020.json"]
    assert s["columns"][:len(p["columns"])] == p["columns"]
    assert s["columns"][len(p["columns"]):] == list(E.AIR_RZ_KEYS)
    vals = dict(zip(s["columns"], s["rows"][0]["values"]))
    # present -> value; null -> null; absent (all-zero season) -> 0
    assert (vals["rec_air_yds"], vals["rz_targets"], vals["rz_rush_att"]) == (12, None, 0)


def test_every_air_rz_key_is_defined_with_the_wording_the_page_renders():
    for k in E.AIR_RZ_KEYS:
        d = E.AIR_RZ_STAT_DEFINITIONS[k]
        assert d["description"], k
        assert not list(Draft202012Validator(
            {"$ref": "#/$defs/StatDefinition", "$defs": E.CONTRACT["$defs"]}).iter_errors(d)), k
    assert "inside the opponent's 20-yard line" in E.AIR_RZ_STAT_DEFINITIONS["rz_targets"]["description"]
    assert "inside the opponent's 20-yard line" in E.AIR_RZ_STAT_DEFINITIONS["rz_rush_att"]["description"]


def test_partial_collection_is_its_own_table_so_track_Fs_zero_sweep_still_matches():
    """Their sweep finds zero-runs; 1999-2002 is ~6% and non-zero, which a zero
    sweep cannot see. Folding it into NOT_COLLECTED would fail their anti-drift
    test for a reason that is not drift."""
    assert E.NOT_COLLECTED["receiving_air_yards"] == ((2003, 2008),)
    assert E.PARTIAL_COLLECTION["receiving_air_yards"] == ((1999, 2002),)
    assert not E.collected("receiving_air_yards", 2001)
    assert E.collected("receiving_air_yards", 2009)
    assert E.collected("rz_rush_att", 2005)
    assert not E.collected("rz_targets", 2005)
