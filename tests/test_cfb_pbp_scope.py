"""The 2022 play-by-play scope change, and parsed name columns, as guards.

Run: pytest -q tests/test_cfb_pbp_scope.py

FCS entered `cfbfastR_cfb_pbp` in 2022: 887 games become 1,459 while FBS vs FBS stays
flat at 770-807. Any pooled league-wide rate therefore moves ~60% for a reason that is
not football. A note would be forgotten, so pooling raises instead - the same shape as
`cfb.guards.scope_violations`, and, like it, the guard is shown failing.
"""
import pytest

from cfb import pbp_scope
from cfb.pbp_scope import FBS_VS_FBS, ScopeError


def test_pooling_across_2022_raises_and_the_message_carries_the_measurement():
    with pytest.raises(ScopeError) as ei:
        pbp_scope.check(range(2019, 2026))
    msg = str(ei.value)
    assert "2019-2025" in msg and "519" in msg and "770" in msg and "776" in msg


@pytest.mark.parametrize("seasons", [range(2014, 2022), range(2022, 2027), [2021], [2022]])
def test_one_side_of_the_change_is_fine(seasons):
    assert "scope change" in pbp_scope.check(seasons)


def test_the_fbs_restriction_is_the_ordinary_way_through():
    out = pbp_scope.check(range(2014, 2027), division_scope=FBS_VS_FBS)
    assert "FBS vs FBS only" in out
    assert pbp_scope.sql_filter("g") == "g.home_division = 'fbs' AND g.away_division = 'fbs'"


def test_a_declaration_is_repeated_back_and_an_empty_one_is_not_a_declaration():
    out = pbp_scope.check([2021, 2022], declared="counting FCS on purpose: opponent quality")
    assert "scope change declared: counting FCS on purpose" in out
    for empty in (None, "", "   ", 1):
        with pytest.raises(ScopeError):
            pbp_scope.check([2021, 2022], declared=empty)


def test_an_empty_season_set_is_refused():
    with pytest.raises(ScopeError):
        pbp_scope.check([])


def test_spans_change_is_about_crossing_not_about_containing_2022():
    assert pbp_scope.spans_change([2021, 2022])
    assert pbp_scope.spans_change([2019, 2025])
    assert not pbp_scope.spans_change([2022, 2025])
    assert not pbp_scope.spans_change([2014, 2021])


@pytest.mark.parametrize("col", ["rusher_player_name", "sack_players", "passer_player_name",
                                 "punt_returner_player_name", "tackle_player_name"])
def test_a_parsed_name_column_is_never_a_key(col):
    with pytest.raises(ScopeError, match="never a key"):
        pbp_scope.key_column(col)


@pytest.mark.parametrize("col", ["rush_player_id", "reception_player_id", "game_id",
                                 "target_player_id", "athlete_id"])
def test_an_id_column_passes(col):
    assert pbp_scope.key_column(col) == col
