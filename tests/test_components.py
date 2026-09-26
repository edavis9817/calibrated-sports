"""The components table (a-11): one league-wide file per season, a projection of the
player season files, with the two share denominators beside the parts.

Run: pytest -q tests/test_components.py

Every store here is the shared invented fixture under tmp_path; the export writes
to a tmp `dest`, never WEB_EXPORT_DIR.
"""
import json
import sqlite3

import pytest

import config
from jobs import export_web as E

from tests.test_export_web import NOW, db  # noqa: F401  (shared fixture)


def _export(dest, only):
    E.export(only=only, now_ts=NOW, dest=str(dest), log=lambda *a: None)
    return {k: json.load(open(p, encoding="utf-8")) for k, p in E.local_keys(str(dest)).items()}


def _rows(files, season):
    """{(player id, index): {column: value}} for one season's table."""
    t = files[f"nfl/components/{season}.json"]
    return {(t["players"][r["player"]]["id"], r["index"]): dict(zip(t["columns"], r["values"]))
            for r in t["rows"]}


# ------------------------------------------------------------------ routing

def test_the_key_routes_to_its_kind_and_to_nothing_else():
    hits = [k["kind"] for k in E.CONTRACT["x-contract"]["keys"]
            if __import__("re").match(k["pattern"], "nfl/components/2025.json")]
    assert hits == ["components"]
    # and it does not swallow a neighbour's shape
    assert E.kind_for_key("nfl/components/index.json")[0] is None
    assert E.kind_for_key("nfl/players/00-A/2025.json")[0] == "player_season"


# ------------------------------------------------------------------ staged, not published

def test_a_default_export_builds_it(db, tmp_path, monkeypatch):
    """Moving it into PARTS was the publish decision, taken in 4b7e86b (2026-09-24).
    Since then weekly_refresh, which exports with only=None, produces these keys -
    shown both ways, default and named, so the presence is not one path's accident.

    Was `test_a_default_export_does_not_build_it`, which asserted the staged state
    and went stale on that commit (a-26 fixed it; f-14, f-17, a-20, a-21 saw it red).

    The research part reads committed research outputs and model predictions this
    fixture does not have; it is stubbed so `only=None` - the real default - runs."""
    monkeypatch.setattr(E, "build_research", lambda generated_at: {})
    # a-36: the metric gate refuses a research build that produced no registered
    # file (a gate that cannot find a value has checked nothing). The stub above
    # produces none by design, so the gate is stubbed with it; the gate itself is
    # tested in tests/test_metric_registry.py.
    monkeypatch.setattr(E.metric_registry, "require",
                        lambda files: type("G", (), {"statement": "stubbed", "declared": []})())
    assert "components" in E.PARTS and "components" not in E.OPTIONAL_PARTS
    default = _export(tmp_path / "a", None)
    assert sorted(k for k in default if "/components/" in k) == [
        "nfl/components/2025.json", "nfl/components/2026.json"]
    named = _export(tmp_path / "b", ["components"])
    assert sorted(k for k in named if "/components/" in k) == [
        "nfl/components/2025.json", "nfl/components/2026.json"]


def test_a_staging_dest_cannot_be_paired_with_an_upload():
    for flag in ("--upload", "--upload-only"):
        with pytest.raises(SystemExit):
            E.main(["--only", "components", "--dest", "x", flag])


# ------------------------------------------------------------------ it is a projection

def test_every_row_is_the_player_season_file_with_absent_keys_as_zero(db, tmp_path):
    files = _export(tmp_path / "o", ["players", "components"])
    E.validate_contract(files)
    seen = 0
    for season in (2025, 2026):
        table = _rows(files, season)
        periods = {(o["identity"]["id"], p["index"]): p["stats"]
                   for k, o in files.items() if o["kind"] == "player_season" and o["season"] == season
                   for p in o["periods"]}
        assert set(table) == set(periods) and periods
        for key, stats in periods.items():
            for col in E.PERIOD_KEYS:
                assert table[key][col] == stats.get(col, 0), (key, col)
            seen += 1
    assert seen == 5            # 00-A x3, 00-S1, 00-S2: the count expected, not "some"


def test_values_are_positional_and_columns_are_defined_stats(db, tmp_path):
    files = _export(tmp_path / "o", ["components"])
    t = files["nfl/components/2026.json"]
    assert t["columns"] == list(E.COMPONENT_COLUMNS)
    assert all(len(r["values"]) == len(t["columns"]) for r in t["rows"])
    assert set(t["columns"]) <= set(E.STAT_DEFINITIONS)


def test_no_derived_total_or_points_is_stored(db, tmp_path):
    files = _export(tmp_path / "o", ["components"])
    for key, t in files.items():
        if "/components/" in key:
            assert not {c for c in t["columns"] if "point" in c or c == "td"}


# ------------------------------------------------------------------ the denominators

def test_team_targets_count_every_row_and_reach_a_player_with_none(db, tmp_path):
    """2025 wk1 BUF: 00-A 9 targets, 00-S1 (a quarterback) none. The QB's season
    file has NO `targets` key - zero all season - and his row must still carry the
    team's 9. This is the bug the first staged run had: keyed off the trimmed
    stats, it published null there."""
    t = _rows(_export(tmp_path / "o", ["components"]), 2025)
    assert t[("00-A", 1)]["team_targets"] == 9
    assert t[("00-S1", 1)]["targets"] == 0 and t[("00-S1", 1)]["team_targets"] == 9
    # 2026 wk1 BUF: 00-A 8 + 00-S2 1 + the out-of-scope 00-D's 0
    t26 = _rows(_export(tmp_path / "p", ["components"]), 2026)
    assert t26[("00-A", 1)]["team_targets"] == 9 and t26[("00-S2", 1)]["team_targets"] == 9


def test_team_snaps_is_the_max_over_all_snap_rows_not_only_crosswalked_ones(db, tmp_path):
    c = sqlite3.connect(config.DB_PATH)
    c.execute("INSERT INTO nfl_snap_counts (pfr_player_id, game_id, data_version, season, week, "
              "player, team, offense_snaps, offense_pct, source, ingested_ts) VALUES "
              "('LineMa00','2026_01_BUF_HOU','v1',2026,1,'Line Man','BUF',62,1.0,'t',0)")
    c.execute("UPDATE nfl_snap_counts SET team='BUF' WHERE pfr_player_id='WideWi00'")
    c.commit()
    c.close()
    t = _rows(_export(tmp_path / "o", ["components"]), 2026)
    assert t[("00-A", 1)]["snaps"] == 54 and t[("00-A", 1)]["team_snaps"] == 62


def test_a_season_the_source_did_not_collect_stays_null_in_part_and_whole(db, tmp_path):
    c = sqlite3.connect(config.DB_PATH)
    c.execute("INSERT INTO nfl_player_week (gsis_id, season, week, season_type, data_version, "
              "player_name, position, team, opponent, receptions, targets, receiving_yards, "
              "carries, source, ingested_ts) VALUES "
              "('00-A',2005,3,'REG','v1','Wide One','WR','BUF','MIA',4,0,50,0,'t',0)")
    c.commit()
    c.close()
    t = _rows(_export(tmp_path / "o", ["components"]), 2005)
    row = t[("00-A", 3)]
    assert row["rec"] == 4
    assert row["targets"] is None and row["team_targets"] is None     # null, never 0


def test_an_absent_usage_key_raises_rather_than_being_filled():
    files = {
        "nfl/players/00-X/summary.json": {"kind": "player_summary", "identity": {
            "id": "00-X", "slug": "x", "name": "X", "position": "WR"}},
        "nfl/players/00-X/2025.json": {"kind": "player_season", "identity": {"id": "00-X"},
                                       "season": 2025, "periods": [{
                                           "index": 1, "season_type": "REG", "team": "BUF",
                                           "stats": {"snap_share": None, "target_share": None}}]},
    }
    with pytest.raises(AssertionError, match="usage key 'snaps'"):
        E.build_components(files, [], {}, {}, "2026-09-22T00:00:00Z")
    files["nfl/players/00-X/2025.json"]["periods"][0]["stats"]["snaps"] = None
    out, census = E.build_components(files, [], {}, {}, "2026-09-22T00:00:00Z")
    assert census["rows"] == 1
