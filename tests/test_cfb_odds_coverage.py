"""The Odds API event -> cfb_games join in research/cfb_odds_coverage.py.

Run: pytest -q tests/test_cfb_odds_coverage.py

Fixtures are INVENTED games between real team names: no pairing, kickoff or id
here is a measurement.
"""
import sqlite3

from research import cfb_odds_coverage as cov

KICK = "2026-09-19T19:30:00Z"


def _conn():
    c = sqlite3.connect(":memory:")
    c.execute("CREATE TABLE cfb_teams (season, team_id, display_name, valid_to_ts)")
    c.execute("CREATE TABLE cfb_games (game_id, week, season_type, start_ts, home_id, away_id, "
              "home_team, away_team, home_division, away_division, season, valid_to_ts)")
    c.executemany("INSERT INTO cfb_teams VALUES (2026,?,?,NULL)",
                  [(113, "Massachusetts Minutemen"), (284, "Stonehill Skyhawks"),
                   (309, "Louisiana Ragin' Cajuns"), (2, "Auburn Tigers")])
    t = cov.iso_ts(KICK)
    c.executemany("INSERT INTO cfb_games VALUES (?,3,'regular',?,?,?,?,?,?,?,2026,NULL)",
                  [(1, t, 113, 284, "Massachusetts", "Stonehill", "fbs", "fcs"),
                   (2, t + 12 * 3600, 309, 2, "Louisiana", "Auburn", "fbs", "fbs")])
    return c


def _ev(home, away, when=KICK):
    return {"home_team": home, "away_team": away, "commence_time": when}


def test_norm_folds_accents_and_punctuation_without_fuzzing():
    assert cov.norm("Louisiana Ragin Cajuns") == cov.norm("Louisiana Ragin' Cajuns")
    assert cov.norm("San José State Spartans") == "san jose state spartans"
    assert cov.norm("UMass Minutemen") == "massachusetts minutemen"      # an alias, explicit
    assert cov.norm("Mass Minutemen") != cov.norm("Massachusetts Minutemen")


def test_join_is_unordered_tolerates_a_tbd_kickoff_and_lists_misses():
    events = [_ev("Stonehill Skyhawks", "UMass Minutemen"),                  # swapped + alias
              _ev("Louisiana Ragin Cajuns", "Auburn Tigers"),                # 12h off (TBD)
              _ev("Auburn Tigers", "Louisiana Ragin Cajuns", "2026-09-22T19:30:00Z"),  # >1 day
              _ev("Nowhere Nobodies", "Auburn Tigers")]
    _games, matched, missed, ambiguous, _fb = cov.match(_conn(), events, 2026)
    assert sorted(g[0] for _e, g in matched) == [1, 2]
    assert [e["home_team"] for e in missed] == ["Auburn Tigers", "Nowhere Nobodies"]
    assert ambiguous == []
