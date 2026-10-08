"""a-71: depth charts stored at change points, officials, and the releases that
are archived without a parser. Run: pytest -q tests/test_a71_releases.py"""
import io
import sqlite3

import polars as pl
import pytest

import config
import nflverse
import store
from jobs import ingest_nflverse


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setattr(config, "RAW_DIR", str(tmp_path / "raw"))
    store.init_db()
    store.reset_disk_cache()
    yield tmp_path
    store.reset_disk_cache()


def _parquet(rows):
    buf = io.BytesIO()
    pl.DataFrame(rows).write_parquet(buf)
    return buf.getvalue()


def _q(sql, args=()):
    c = sqlite3.connect(config.DB_PATH)
    try:
        return c.execute(sql, args).fetchall()
    finally:
        c.close()


DTS = ("2026-09-01T06:00:00Z", "2026-09-01T14:00:00Z", "2026-09-02T06:00:00Z")


def chart(dt, team, qb1, qb2, gsis=None):
    base = {"dt": dt, "team": team, "pos_grp_id": "21", "pos_grp": "3WR 1TE", "pos_id": "8",
            "pos_name": "Quarterback", "pos_abb": "QB", "pos_slot": 1}
    return [{**base, "pos_rank": 1, "espn_id": qb1, "player_name": "P" + qb1, "gsis_id": gsis},
            {**base, "pos_rank": 2, "espn_id": qb2, "player_name": "P" + qb2, "gsis_id": None}]


def depth_rows(det_last=("2", "1"), gsis=None):
    """DET's two quarterbacks swap at the third snapshot; CHI never changes."""
    out = []
    for i, dt in enumerate(DTS):
        out += chart(dt, "DET", *(det_last if i == 2 else ("1", "2")), gsis=gsis)
        out += chart(dt, "CHI", "7", "8", gsis=gsis)
    return out


def test_a_chart_is_stored_only_where_it_changed():
    _, _, rows = ingest_nflverse.normalize_depth_charts(_parquet(depth_rows()), "2026-09-02", season=2026)
    assert len(rows) == 6                       # DET dt1, CHI dt1, DET dt3 - two rows each
    assert _q("SELECT team, dt FROM nfl_depth_chart GROUP BY 1, 2 ORDER BY 1, 2") == [
        ("CHI", DTS[0]), ("DET", DTS[0]), ("DET", DTS[2])]
    # every snapshot is still on record, with what it held and what it changed
    assert _q("SELECT dt, teams, teams_changed, source_rows FROM nfl_depth_chart_snapshots ORDER BY dt") == [
        (DTS[0], 2, 2, 4), (DTS[1], 2, 0, 4), (DTS[2], 2, 1, 4)]
    # the chart in force at the middle snapshot is the newest stored one at or before it
    assert _q("SELECT espn_id FROM nfl_depth_chart WHERE team='DET' AND pos_rank=1 AND dt = "
              "(SELECT MAX(dt) FROM nfl_depth_chart WHERE team='DET' AND dt <= ?)", (DTS[1],)) == [("1",)]


def test_an_unchanged_file_stores_nothing_new_and_the_control_differs():
    """The change test discriminates: with no swap, DET's third snapshot is not stored."""
    _, _, rows = ingest_nflverse.normalize_depth_charts(
        _parquet(depth_rows(det_last=("1", "2"))), "2026-09-02", season=2026)
    assert len(rows) == 4
    assert _q("SELECT COUNT(*) FROM nfl_depth_chart WHERE dt=?", (DTS[2],)) == [(0,)]


def test_a_gsis_id_gone_quiet_is_kept_and_a_restated_one_wins():
    d = ingest_nflverse.normalize_depth_charts
    d(_parquet(depth_rows(gsis="00-0000001")), "2026-09-02", season=2026)
    d(_parquet(depth_rows(gsis=None)), "2026-09-03", season=2026)          # upstream unsays it
    assert _q("SELECT DISTINCT gsis_id FROM nfl_depth_chart WHERE pos_rank=1") == [("00-0000001",)]
    d(_parquet(depth_rows(gsis="00-0000009")), "2026-09-04", season=2026)  # upstream corrects it
    assert _q("SELECT DISTINCT gsis_id FROM nfl_depth_chart WHERE pos_rank=1") == [("00-0000009",)]
    assert _q("SELECT COUNT(*) FROM nfl_depth_chart") == [(6,)]            # three pulls, one copy


def test_the_weekly_layout_is_left_archived_and_says_so(capsys):
    legacy = _parquet([{"season": 2024, "club_code": "DET", "week": 1, "depth_team": "1",
                        "gsis_id": "00-0000001", "position": "QB", "depth_position": "QB"}])
    table, _, rows = ingest_nflverse.normalize_depth_charts(legacy, "2026-09-02", season=2024)
    assert table is None and rows == []
    assert "not parsed" in capsys.readouterr().out
    assert _q("SELECT COUNT(*) FROM nfl_depth_chart") == [(0,)]


def test_the_season_must_be_passed_because_the_file_does_not_carry_it():
    with pytest.raises(ValueError, match="season"):
        ingest_nflverse.normalize_depth_charts(_parquet(depth_rows()), "2026-09-02")


def test_officials_keep_the_league_game_id_and_skip_a_row_with_no_official():
    rows = [{"game_id": "2015091000", "game_key": "56503", "official_name": "A", "position": "Referee",
             "jersey_number": 1, "official_id": "25", "season": 2015, "season_type": "REG", "week": 1},
            {"game_id": "2015091000", "game_key": "56503", "official_name": "B", "position": "Umpire",
             "jersey_number": 2, "official_id": "", "season": 2015, "season_type": "REG", "week": 1}]
    table, cols, out = ingest_nflverse.normalize_officials(_parquet(rows), "2026-09-02")
    assert table == "nfl_officials" and len(out) == 1
    store.replace_rows(table, cols, out)
    assert _q("SELECT game_id, official_id, position, season FROM nfl_officials") == [
        ("2015091000", "25", "Referee", 2015)]


def test_what_is_parsed_and_what_is_only_archived():
    ds = nflverse.DATASETS
    parsed = {"depth_charts", "officials"}
    landed = {"pfr_adv_pass", "pfr_adv_rush", "pfr_adv_rec", "pfr_adv_def", "season_rosters",
              "player_stats_legacy"}
    assert all(ds[n].normalize and n in ingest_nflverse.NORMALIZERS for n in parsed)
    assert all(not ds[n].normalize and n not in ingest_nflverse.NORMALIZERS for n in landed)
    assert {ds[n].release for n in landed} == {"pfr_advstats", "rosters", "player_stats"}
    # the frozen release must never ride the logger's live-tier refresh
    assert ds["player_stats_legacy"].tier == nflverse.OFFSEASON
    assert ds["weekly_stats"].release == "stats_player"
