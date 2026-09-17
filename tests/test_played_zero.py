"""Played-but-zero weeks reach the export. Run: pytest -q tests/test_played_zero.py

THE UPSTREAM FACT. nflverse writes NO ROW for a player who played and recorded
nothing - verified on the raw parquet, and the same gap that made
`jobs/settle_outcomes.py` call 13,182 realized zeros "unsettled". The export
inherits it: a week with snaps and no stat line simply has no `PeriodRow`, so
the 18-week frame cannot tell "played, did nothing" from "did not play".

WHAT MAY BE EMITTED, AND WHAT MAY NOT. Measured in scope (the 3,970 exported
players), seasons >= SNAP_FIRST_SEASON:

    offensive snaps > 0, no stat row    18,892   -> a PeriodRow of real zeros
    defensive snaps only, no stat row    1,070   -> NOT emitted
    zero snaps in both phases, no row     5,904   -> NEVER emitted

The last line is the trap. Emitting those would assert a player appeared in a
game he sat out - inventing a fact rather than recovering one - and it is the
larger of the two excluded groups. `test_a_week_he_sat_out_is_never_emitted`
is the guard.

Defence-only is excluded for a different reason: `PERIOD_KEYS` is offensive
vocabulary and `snaps` maps to OFFENSIVE snaps, so such a row would publish
snaps=0 with all-zero offence - on the page indistinguishable from sitting out,
which is worse than omitting it. Track A owns that call; it is recorded in
DECISIONS.md rather than buried here.

ZEROS, NOT NULLS. The contract is explicit: "null means unknown or not
collected, never zero". We know these values are zero - that is the whole
finding - so a null here would restate the bug in the export's own vocabulary.
"""
import os
import sqlite3

import pytest

import config
from jobs import export_web as E

NOW = 1_789_500_000.0

# (team, offense_snaps, defense_snaps, offense_pct, game_id)
def snap(team, off, dfn=0, pct=0.5, gid="g"):
    return (team, off, dfn, pct, gid)


def game(season, week, home, away, played=True):
    return {"game_id": f"{season}_{week:02d}_{away}_{home}", "season": season, "week": week,
            "game_type": "REG", "gameday": f"{season}-09-0{min(week, 9)}",
            "home_team": home, "away_team": away,
            "home_score": 20 if played else None, "away_score": 17 if played else None}


def gidx_for(*games):
    idx = {}
    for g in games:
        for t in (g["home_team"], g["away_team"]):
            idx[(g["season"], g["week"], t)] = g
    return idx


def stat_row(season, week, team):
    return {"season": season, "week": week, "season_type": "REG", "team": team}


# ------------------------------------------------------------- the pure rule

def test_a_week_he_played_with_no_stat_row_becomes_a_period():
    g = game(2024, 5, "BUF", "MIA")
    out = E.played_zero_periods(
        "00-A", [stat_row(2024, 1, "BUF")],
        {("00-A", 2024, 5): snap("BUF", 31, pct=0.62)}, gidx_for(g))
    assert len(out) == 1
    p = out[0]
    assert (p["season"], p["index"], p["season_type"]) == (2024, 5, "REG")
    assert p["team"] == "BUF" and p["opponent"] == "MIA" and p["home"] is True
    assert p["game_id"] == g["game_id"] and p["date"] == g["gameday"]


def test_the_stats_are_real_zeros_not_nulls():
    """The contract: null means unknown or not collected, NEVER zero. We know
    these are zero - a null would restate the upstream bug in our own file."""
    out = E.played_zero_periods(
        "00-A", [], {("00-A", 2024, 5): snap("BUF", 31, pct=0.62)},
        gidx_for(game(2024, 5, "BUF", "MIA")))
    stats = out[0]["stats"]
    assert set(stats) == set(E.PERIOD_KEYS), "a period row must carry the full key set"
    assert stats["snaps"] == 31, "snaps are the EVIDENCE he played - never zero here"
    assert stats["snap_share"] == 0.62
    for k in ("rec", "rec_yds", "targets", "rush_att", "pass_att", "fum_lost"):
        assert stats[k] == 0, f"{k} must be a known zero"
        assert stats[k] is not None


def test_a_week_he_sat_out_is_never_emitted():
    """5,904 in-scope weeks have a snap row reading zero in both phases. A row
    for one of those asserts he appeared in a game he did not play."""
    out = E.played_zero_periods(
        "00-A", [], {("00-A", 2024, 5): snap("BUF", 0, dfn=0)},
        gidx_for(game(2024, 5, "BUF", "MIA")))
    assert out == []


def test_a_defence_only_week_is_not_emitted():
    """`snaps` is OFFENSIVE snaps and PERIOD_KEYS is offensive vocabulary, so
    this row would read snaps=0 with all-zero offence - on the page identical
    to sitting out."""
    out = E.played_zero_periods(
        "00-D", [], {("00-D", 2024, 5): snap("BUF", 0, dfn=44)},
        gidx_for(game(2024, 5, "BUF", "MIA")))
    assert out == []


def test_a_week_that_already_has_a_stat_row_is_not_duplicated():
    out = E.played_zero_periods(
        "00-A", [stat_row(2024, 5, "BUF")],
        {("00-A", 2024, 5): snap("BUF", 31)}, gidx_for(game(2024, 5, "BUF", "MIA")))
    assert out == []


def test_an_unplayed_game_emits_nothing():
    """A fixture with no final score has not happened. Snap rows should not
    exist for it, but the export must not depend on that being true."""
    out = E.played_zero_periods(
        "00-A", [], {("00-A", 2024, 5): snap("BUF", 31)},
        gidx_for(game(2024, 5, "BUF", "MIA", played=False)))
    assert out == []


def test_seasons_before_snap_coverage_emit_nothing():
    """Snap counts begin in 2013 (SNAP_FIRST_SEASON, and the table's own
    earliest season). Before that, played-zero cannot be told from did-not-play
    at all, so the frame degrades rather than guessing."""
    assert E.SNAP_FIRST_SEASON == 2013
    out = E.played_zero_periods(
        "00-A", [], {("00-A", 2012, 5): snap("SD", 31)},
        gidx_for(game(2012, 5, "SD", "OAK")))
    assert out == []


def test_a_snap_row_with_no_matching_game_is_skipped():
    """No team/week join means no opponent, no date, no home flag - every one of
    which PeriodRow requires. Skipped rather than emitted with nulls."""
    out = E.played_zero_periods(
        "00-A", [], {("00-A", 2024, 5): snap("BUF", 31)}, {})
    assert out == []


def test_the_players_own_team_decides_the_game_not_the_other_way_round():
    """A traded player's week belongs to whichever team the SNAP row names."""
    g1, g2 = game(2024, 5, "BUF", "MIA"), game(2024, 5, "NE", "NYJ")
    out = E.played_zero_periods(
        "00-A", [], {("00-A", 2024, 5): snap("NE", 22)}, gidx_for(g1, g2))
    assert out[0]["team"] == "NE" and out[0]["opponent"] == "NYJ"


@pytest.mark.parametrize("gtype,label", [("WC", "Wild Card"), ("DIV", "Divisional"),
                                         ("SB", "Super Bowl")])
def test_a_postseason_week_is_POST_not_REG(gtype, label):
    """THE REGRESSION. `game_index` carries every game type, so a hard-coded
    "REG" emitted 745 playoff games as regular season - season_type=REG beside
    label='Divisional'. `_totals` groups on (season, season_type), so each one
    landed in that season's REG total and in the per-game denominator.
    """
    g = game(2024, 19, "BUF", "KC")
    g["game_type"] = gtype
    out = E.played_zero_periods(
        "00-A", [], {("00-A", 2024, 19): snap("BUF", 31)}, gidx_for(g))
    assert out and out[0]["season_type"] == "POST", f"{gtype} emitted as REG"
    assert out[0]["label"] == label


def test_a_regular_season_week_is_still_REG():
    out = E.played_zero_periods(
        "00-A", [], {("00-A", 2024, 5): snap("BUF", 31)},
        gidx_for(game(2024, 5, "BUF", "MIA")))
    assert out[0]["season_type"] == "REG" and out[0]["label"] == "Week 5"


# ------------------------------------------------------------- end to end

import json                                                        # noqa: E402

import store                                                       # noqa: E402

GAMES = [
    # game_id, week, type, home, away, hs, as - all PLAYED
    ("2024_01_MIA_BUF", 1, "REG", "BUF", "MIA", 24, 20),
    ("2024_02_NE_BUF", 2, "REG", "BUF", "NE", 31, 17),
    ("2024_03_NYJ_BUF", 3, "REG", "BUF", "NYJ", 10, 7),
    # A playoff game he also played with no stat row. It must land as POST and
    # must NOT inflate the regular-season games count.
    ("2024_19_KC_BUF", 19, "DIV", "BUF", "KC", 27, 24),
]


@pytest.fixture
def store_db(tmp_path, monkeypatch):
    """A purpose-built store: one player, three PLAYED weeks.

        wk1  stat row, offensive usage   -> a normal period, and what puts him in scope
        wk2  snaps 41, NO stat row       -> must become a played-zero period
        wk3  snaps 0,  NO stat row       -> he sat out; must never appear

    Local rather than borrowed from test_export_web.py: pytest fixtures do not
    cross module boundaries, and hoisting that one into a conftest would move a
    fixture forty-odd existing tests depend on. This store is also sharper -
    three weeks chosen to exercise emit / refuse / refuse.

    SLUG_DIR is pinned like the other two. The slug registry is permanent and
    committed; a test appending to it would be a real defect, not a mess.
    """
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setattr(config, "RAW_DIR", str(tmp_path / "raw"))
    monkeypatch.setattr(E, "SLUG_DIR", str(tmp_path / "slugs"))
    store.init_db()
    c = sqlite3.connect(config.DB_PATH)
    for gid, wk, gtype, home, away, hs, as_ in GAMES:
        c.execute("INSERT INTO nfl_games (game_id, data_version, season, week, game_type, gameday, "
                  "kickoff_ts, home_team, away_team, home_score, away_score, source, ingested_ts) "
                  "VALUES (?,'v1',2024,?,?,?,?,?,?,?,?,'t',0)",
                  (gid, wk, gtype, f"2024-09-0{min(wk, 9)}", NOW - 5e7, home, away, hs, as_))
    c.execute("INSERT INTO nfl_player_week (gsis_id, season, week, season_type, data_version, "
              "player_name, position, team, opponent, receptions, targets, receiving_yards, "
              "source, ingested_ts) VALUES "
              "('00-A',2024,1,'REG','v1','Wide One','WR','BUF','MIA',5,7,63,'t',0)")
    c.execute("INSERT INTO player_xwalk (gsis_id, display_name, position, pfr_id) "
              "VALUES ('00-A','Wide One','WR','WideWi00')")
    for wk, gid, off, pct in ((2, "2024_02_NE_BUF", 41, 0.71), (3, "2024_03_NYJ_BUF", 0, 0.0),
                              (19, "2024_19_KC_BUF", 55, 0.90)):
        c.execute("INSERT INTO nfl_snap_counts (pfr_player_id, game_id, data_version, season, week, "
                  "player, team, offense_snaps, defense_snaps, offense_pct, source, ingested_ts) "
                  "VALUES ('WideWi00',?,'v1',2024,?,'Wide One','BUF',?,0,?,'t',0)", (gid, wk, off, pct))
    c.commit()
    c.close()
    return tmp_path


def exported(tmp_path):
    dest = str(tmp_path / "out")
    E.export(only=["players", "teams", "manifest"], now_ts=NOW, dest=dest)
    return {k: json.load(open(p, encoding="utf-8")) for k, p in E.local_keys(dest).items()}


def test_a_played_zero_week_reaches_the_exported_season_file(store_db):
    weeks = {p["index"]: p
             for p in exported(store_db)["nfl/players/00-A/2024.json"]["periods"]}
    assert 2 in weeks, "the played-zero week never reached the export - is the caller wired?"
    assert 3 not in weeks, "a week he sat out must never be emitted"
    wk2 = weeks[2]
    assert wk2["stats"]["snaps"] == 41 and wk2["stats"]["snap_share"] == 0.71
    assert wk2["stats"]["rec"] == 0 and wk2["stats"]["targets"] == 0
    assert wk2["opponent"] == "NE" and wk2["home"] is True and wk2["team"] == "BUF"


def test_the_games_count_includes_the_played_zero_week(store_db):
    """A CONSEQUENCE, asserted so it cannot change silently: `games` is the
    period count and PlayerView divides by it for per-game rates. Counting only
    weeks with a stat line flatters the average exactly as the offence-only
    snap rule flattered the model."""
    files = exported(store_db)
    summary = files["nfl/players/00-A/summary.json"]

    # TWO FIELDS NAMED `games`, AND THEY COUNT DIFFERENT THINGS. Conflating them
    # is what made the first version of this test assert 2 and fail at 3.
    #   season_totals[].games  grouped by (season, season_type) -> REG only
    #   seasons[].games        every period in that season FILE, postseason
    #                          included, and test_export_web pins it to
    #                          len(periods)
    reg = [t for t in summary["season_totals"]
           if t["season"] == 2024 and t["season_type"] == "REG"]
    assert reg and reg[0]["games"] == 2, "week 1 plus the played-zero week 2"

    entry = [s for s in summary["seasons"] if s["season"] == 2024][0]
    periods = files["nfl/players/00-A/2024.json"]["periods"]
    assert entry["games"] == len(periods) == 3, "REG 2 + the DIV week 19"

    # the zeros must not move the totals
    assert reg[0]["stats"]["rec"] == 5 and reg[0]["stats"]["rec_yds"] == 63


def test_a_postseason_played_zero_week_does_not_inflate_the_REG_count(store_db):
    """End to end: the playoff week must land as POST, and the regular-season
    games count - the denominator PlayerView divides by - must not move."""
    files = exported(store_db)
    summary = files["nfl/players/00-A/summary.json"]
    reg = [t for t in summary["season_totals"]
           if t["season"] == 2024 and t["season_type"] == "REG"][0]
    assert reg["games"] == 2, "a playoff game was counted as regular season"
    post = [t for t in summary["season_totals"]
            if t["season"] == 2024 and t["season_type"] == "POST"]
    assert post and post[0]["games"] == 1
    wk19 = [p for p in files["nfl/players/00-A/2024.json"]["periods"] if p["index"] == 19]
    assert wk19 and wk19[0]["season_type"] == "POST" and wk19[0]["label"] == "Divisional"


def test_no_emitted_row_contradicts_its_own_game(store_db):
    """A row whose label says 'Divisional' while its season_type says REG is
    self-contradictory, and 745 of them reached a measurement run."""
    files = exported(store_db)
    for p in files["nfl/players/00-A/2024.json"]["periods"]:
        post_label = p["label"] in ("Wild Card", "Divisional", "Conference", "Super Bowl")
        assert post_label == (p["season_type"] == "POST"), p


def test_the_exported_rows_still_satisfy_the_contract(store_db):
    """A synthetic row is still a PeriodRow: closed object, every required key."""
    files = exported(store_db)
    E.assert_stats_defined(files, files["nfl/manifest.json"]["stat_definitions"])
    for p in files["nfl/players/00-A/2024.json"]["periods"]:
        assert set(p) == {"season", "index", "label", "season_type", "game_id",
                          "date", "team", "opponent", "home", "stats"}
