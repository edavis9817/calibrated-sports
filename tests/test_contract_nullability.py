"""Which contract fields may be null, and — just as much — which may not.

Run: pytest -q tests/test_contract_nullability.py

Findings C-4 and C-7 (track C, 2026-09-18) are the same shape: a field typed
non-nullable that a second sport cannot answer honestly. `RosterEntry.games` is
an appearance count no public college source records, and
`ScheduleGame.opponent_abbr` is missing on 54,974 of the two sides across 46,296
games from 2004. Both are now `["...", "null"]`.

THE HALF THAT IS EASY TO GET WRONG IS THE OTHER HALF. `"games": {"type":
"integer"}` appears THREE times in the contract - `SeasonTotal`, `TeamSplit` and
`RosterEntry` - and only one of them was meant to change. An edit anchored on
that text alone would have widened all three, and a test that checked only the
field it meant to change would have passed while it happened. So the
non-nullable siblings are pinned here explicitly.
"""
import json
import os

import pytest
from jsonschema import Draft202012Validator

from jobs import export_web as E

CONTRACT = E.CONTRACT
DEFS = CONTRACT["$defs"]


def validator(name):
    return Draft202012Validator({"$ref": f"#/$defs/{name}", "$defs": DEFS})


def roster_row(**kw):
    base = {"season": 2026, "id": "00-0034857", "slug": "josh-allen", "name": "Josh Allen",
            "position": "QB", "games": 1, "snap_share": 1.0, "target_share": 0.0,
            "carry_share": 0.28, "has_page": True}
    base.update(kw)
    return base


def schedule_game(**kw):
    base = {"season": 2026, "index": 1, "label": "Week 1", "game_type": "REG",
            "game_id": "2026_01_BUF_HOU", "date": "2026-09-13", "kickoff_ts": 1789318800,
            "home": False, "opponent": "hou", "opponent_abbr": "HOU",
            "points_for": 36, "points_against": 31, "result": "W", "spread": 1.5,
            "total": 44.5, "coach": "A", "opponent_coach": "B"}
    base.update(kw)
    return base


# ------------------------------------------------------------------ C-4 and C-7

def test_roster_games_accepts_null():
    assert not list(validator("RosterEntry").iter_errors(roster_row(games=None)))


def test_roster_games_still_accepts_an_integer():
    """The other answer on the other input: NFL emits a real count and must keep
    validating. A type that accepted anything would pass the test above."""
    assert not list(validator("RosterEntry").iter_errors(roster_row(games=7)))


def test_roster_games_still_REFUSES_a_non_integer():
    """Nullable is not 'anything goes'. A string here would be a different
    defect wearing the fix's clothes."""
    assert list(validator("RosterEntry").iter_errors(roster_row(games="three")))
    assert list(validator("RosterEntry").iter_errors(roster_row(games=1.5)))


def test_opponent_abbr_accepts_null_and_still_accepts_a_string():
    v = validator("ScheduleGame")
    assert not list(v.iter_errors(schedule_game(opponent_abbr=None)))
    assert not list(v.iter_errors(schedule_game(opponent_abbr="HOU")))
    assert list(v.iter_errors(schedule_game(opponent_abbr=7)))


# --------------------------------------------- nullable is not the same as absent

@pytest.mark.parametrize("name,row,field", [
    ("RosterEntry", roster_row(), "games"),
    ("ScheduleGame", schedule_game(), "opponent_abbr"),
])
def test_the_field_is_still_REQUIRED(name, row, field):
    """An absent key and a null one are different statements: null says 'we
    looked and cannot answer', absent says nothing at all. Dropping the key
    would have been the easy way to satisfy C-4 and C-7, and it would have
    destroyed the distinction they exist to preserve."""
    assert field in DEFS[name]["required"]
    row.pop(field)
    assert list(validator(name).iter_errors(row)), f"{name} validated without {field}"


# ------------------------------------------- the look-alikes must NOT have moved

@pytest.mark.parametrize("name", ["SeasonTotal", "TeamSplit"])
def test_the_OTHER_games_fields_are_still_non_nullable(name):
    """`"games": {"type": "integer"}` appears three times and one changed. These
    two count games a player or team actually played, and the producer always
    knows that number - a null there would be a real loss of meaning, not an
    honest gap."""
    assert DEFS[name]["properties"]["games"]["type"] == "integer", (
        f"{name}.games was widened by accident - the C-4 edit hit the wrong definition")


def test_a_null_games_is_refused_by_the_season_and_split_shapes():
    """Asserting the type string is not the same as asserting the behaviour."""
    assert list(validator("SeasonTotal").iter_errors(
        {"season": 2026, "season_type": "REG", "games": None, "stats": {}}))
    assert list(validator("TeamSplit").iter_errors(
        {"season": 2026, "season_type": "REG", "games": None, "offense": {}, "defense": {}}))


# -------------------------------------------------- the vendored copy will drift

def test_the_canonical_contract_is_the_one_the_producer_loads():
    """If these ever diverge, every assertion above is about a document nothing
    reads. The web repo vendors a byte-identical copy and its CI diffs it."""
    with open(os.path.join(E.ROOT, "web", "contract", "v2", "contract.schema.json"),
              encoding="utf-8") as f:
        on_disk = json.load(f)
    assert on_disk["$defs"]["RosterEntry"]["properties"]["games"]["type"] == ["integer", "null"]
    assert on_disk is not CONTRACT          # loaded fresh, not the same object
    assert on_disk["$defs"] == DEFS         # and identical in content
