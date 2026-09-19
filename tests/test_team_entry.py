"""The manifest's team listing, and the season summary the teams board reads.

Run: pytest -q tests/test_team_entry.py

Track B's A14 and track C's C-3 land on one object. `manifest.teams[]` was
`{slug, abbr, name}`, so a figure per team meant opening every team file on an
index page - the cost `counts` exists to remove - and the board shipped with its
figures marked rather than inventing them.

Two things here are NOT what was asked for, and both are tested because that is
where a faithful implementation would have been wrong:

  * `tied` - A14 asked for {games, cleared, missed}. The sport has ties, so that
    triple silently loses a drawn result.
  * `division` is NOT split into a bare region. The source stores "NFC West" as
    an atom; "West" is recorded nowhere, and inventing the decomposition would
    publish a structure the sport does not have.
"""
import json
import os

import pytest
from jsonschema import Draft202012Validator

from jobs import export_web as E

DEFS = E.CONTRACT["$defs"]


def validator(name):
    return Draft202012Validator({"$ref": f"#/$defs/{name}", "$defs": DEFS})


def summary(**kw):
    base = {"games": 1, "cleared": 1, "missed": 0, "tied": 0,
            "points_for": 36, "points_against": 31, "markets": 4}
    base.update(kw)
    return base


def entry(**kw):
    base = {"slug": "buf", "abbr": "BUF", "name": "Buffalo Bills",
            "conference": "AFC", "division": "AFC East", "classification": None,
            "season": summary()}
    base.update(kw)
    return base


# --------------------------------------------------------------- the shapes

def test_a_full_entry_validates():
    assert not list(validator("TeamEntry").iter_errors(entry()))


def test_every_grouping_field_may_be_null_and_none_may_be_ABSENT():
    """Nullable is not optional. A sport says what it has and says null for what
    it has not; an absent key says nothing at all."""
    v = validator("TeamEntry")
    assert not list(v.iter_errors(entry(conference=None, division=None, classification=None)))
    for field in ("conference", "division", "classification", "season"):
        bad = entry()
        bad.pop(field)
        assert list(v.iter_errors(bad)), f"{field} validated while absent"


def test_season_may_be_null_for_a_sport_that_publishes_no_figures():
    assert not list(validator("TeamEntry").iter_errors(entry(season=None)))


def test_the_entry_is_closed():
    assert list(validator("TeamEntry").iter_errors(entry(plays=63))), (
        "an unrequested field must fail the export until the contract moves")


def test_tied_is_required_even_though_it_was_not_requested():
    bad = summary()
    bad.pop("tied")
    assert list(validator("TeamSeasonSummary").iter_errors(bad))


def test_points_may_be_null_before_a_team_has_played():
    assert not list(validator("TeamSeasonSummary").iter_errors(
        summary(games=0, cleared=0, missed=0, tied=0, points_for=None, points_against=None)))


def test_membership_shape():
    v = validator("TeamMembership")
    assert not list(v.iter_errors(
        {"season": 2026, "conference": "AFC", "division": "AFC East", "classification": None}))
    assert list(v.iter_errors({"season": 2026}))


# ------------------------------------------------- what the producer emits

def _game(season, week, home, away, hs, as_, gtype="REG"):
    return {"game_id": f"{season}_{week:02d}_{away}_{home}", "season": season, "week": week,
            "game_type": gtype, "home_team": home, "away_team": away,
            "home_score": hs, "away_score": as_}


def test_the_summary_counts_wins_losses_AND_TIES():
    games = {
        "a": _game(2026, 1, "BUF", "MIA", 24, 20),   # BUF cleared
        "b": _game(2026, 2, "NE", "BUF", 30, 17),    # BUF missed
        "c": _game(2026, 3, "BUF", "NYJ", 21, 21),   # BUF tied
    }
    out = E.team_season_summaries(games, 2026, [], {})
    buf = out["BUF"]
    assert (buf["games"], buf["cleared"], buf["missed"], buf["tied"]) == (3, 1, 1, 1)
    assert buf["points_for"] == 24 + 17 + 21
    assert buf["points_against"] == 20 + 30 + 21


def test_cleared_missed_and_tied_ALWAYS_SUM_TO_GAMES():
    """The schema cannot express this and it is the whole reason `tied` exists.
    A {games, cleared, missed} triple would fail here on the drawn game."""
    games = {
        "a": _game(2026, 1, "BUF", "MIA", 24, 20),
        "b": _game(2026, 2, "NE", "BUF", 30, 17),
        "c": _game(2026, 3, "BUF", "NYJ", 21, 21),
    }
    for abbr, s in E.team_season_summaries(games, 2026, [], {}).items():
        assert s["cleared"] + s["missed"] + s["tied"] == s["games"], abbr


def test_an_unplayed_team_reports_null_points_not_zero():
    out = E.team_season_summaries({}, 2026, [], {})
    buf = out["BUF"]
    assert buf["games"] == 0
    assert buf["points_for"] is None and buf["points_against"] is None
    assert buf["cleared"] == buf["missed"] == buf["tied"] == 0


def test_unplayed_games_and_other_seasons_are_excluded():
    games = {
        "played": _game(2026, 1, "BUF", "MIA", 24, 20),
        "future": _game(2026, 2, "BUF", "NYJ", None, None),
        "lastyr": _game(2025, 1, "BUF", "MIA", 10, 7),
        "post": _game(2026, 19, "BUF", "KC", 27, 24, gtype="WC"),
    }
    assert E.team_season_summaries(games, 2026, [], {})["BUF"]["games"] == 1


def test_markets_counts_PRICED_PLAYERS_on_that_team():
    index = [{"id": "00-A", "team": "BUF"}, {"id": "00-B", "team": "BUF"},
             {"id": "00-C", "team": "MIA"}, {"id": "00-D", "team": None}]
    out = E.team_season_summaries({}, 2026, index, {"00-A": "k1", "00-C": "k2", "00-D": "k3"})
    assert out["BUF"]["markets"] == 1
    assert out["MIA"]["markets"] == 1
    assert out["NE"]["markets"] == 0


def test_every_published_team_gets_a_row_even_with_no_data():
    out = E.team_season_summaries({}, 2026, [], {})
    assert set(out) == set(E.TEAM_NAMES), "a listing needs a row per team, not per team with data"


# ------------------------------------------- division is published, not derived

def test_division_is_NOT_split_into_a_bare_region():
    """The source stores the conference inside the division. Publishing "West"
    would invent a decomposition it does not record."""
    src = open(os.path.join(E.ROOT, "jobs", "export_web.py"), encoding="utf-8").read()
    body = src.split("def load_team_groupings", 1)[1].split("\ndef ", 1)[0]
    assert ".split(" not in body, "the grouping loader must not decompose the source value"
    assert "team_division" in body and "team_conf" in body


def test_the_contract_carries_the_teams_ref_rather_than_an_inline_shape():
    with open(os.path.join(E.ROOT, "web", "contract", "v2", "contract.schema.json"),
              encoding="utf-8") as f:
        contract = json.load(f)
    teams = contract["$defs"]["SportManifest"]["properties"]["teams"]
    assert teams["items"] == {"$ref": "#/$defs/TeamEntry"}
    assert "memberships" in contract["$defs"]["TeamFile"]["required"]
