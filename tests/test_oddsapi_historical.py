"""Odds API historical pilot tests. Run: pytest -q

These bytes cost credits, so the failures worth guarding are the ones that make
you buy the same data twice: a name that does not join, a side that means the
opposite of what the schema says, and a re-parse that appends instead of
replacing.
"""
import time

import pytest

import config
import store
from core.outcomes import Stat, is_push_possible
from jobs.backfill_oddsapi import MARKET_STAT, american_to_prob
from venues.mapping import Unresolved, norm_name, resolve_player


# --- price semantics ---------------------------------------------------------

def test_american_odds_convert_the_way_the_book_means_them():
    """Hand-checked against the pilot's own raw row: AJ Brown Over 5.5 at -135
    on draftkings implied 0.5745."""
    assert american_to_prob(-135) == pytest.approx(0.574468, abs=1e-6)
    assert american_to_prob(105) == pytest.approx(0.487805, abs=1e-6)
    assert american_to_prob(-110) == pytest.approx(0.523810, abs=1e-6)
    assert american_to_prob(100) == pytest.approx(0.5)
    assert american_to_prob(None) is None
    assert american_to_prob("nonsense") is None


def test_a_favourite_is_more_likely_than_an_underdog():
    """Sign convention: negative is the favourite. Getting this backwards
    inverts every probability in the dataset and still looks like a number."""
    assert american_to_prob(-200) > 0.5
    assert american_to_prob(+200) < 0.5
    # Symmetric odds are a no-vig pair and must sum to exactly 1. This is the
    # clean statement of the convention: -200 is 0.667, +200 is 0.333.
    assert american_to_prob(-200) + american_to_prob(200) == pytest.approx(1.0)
    assert american_to_prob(-200) == pytest.approx(2 / 3, abs=1e-9)


def test_the_two_sides_of_one_line_sum_above_one():
    """THE polarity check. Over and Under of the same line must sum to slightly
    more than 1 - that excess is the vig. A sum near 1.0 means no vig (wrong),
    and a sum far from 1 means the sides are mismatched."""
    over, under = american_to_prob(-135), american_to_prob(105)
    total = over + under
    assert 1.0 < total < 1.15
    assert total == pytest.approx(1.0623, abs=1e-3)


# --- the join, which is the expensive thing to get wrong ---------------------

def test_initials_normalize_the_same_with_and_without_periods():
    """nflverse writes "A.J. Brown", the sportsbook writes "AJ Brown". Before
    this they were different keys and it cost 66 outcomes on one player - the
    largest single name failure in the pilot."""
    assert norm_name("A.J. Brown") == norm_name("AJ Brown") == "aj brown"
    assert norm_name("T.J. Watt") == norm_name("TJ Watt") == "tj watt"
    assert norm_name("C.J. Stroud") == norm_name("CJ Stroud")


def test_a_lone_middle_initial_is_not_swallowed():
    """Only RUNS of single letters collapse. Otherwise "Robert L Jones" would
    become "robertl jones" and stop matching itself."""
    assert norm_name("Robert L Jones") == "robert l jones"
    assert norm_name("Amon-Ra St. Brown") == "amon ra st brown"


def test_suffixes_and_punctuation_still_fold():
    assert norm_name("Odell Beckham Jr.") == "odell beckham"
    assert norm_name("Wan'Dale Robinson") == "wan dale robinson"


# --- market mapping ----------------------------------------------------------

def test_the_alternate_ladder_is_the_same_stat_as_the_main_line():
    """player_receptions_alternate quotes a ladder of thresholds on the SAME
    claim. Mapping it to a different stat would fork one market into two."""
    assert MARKET_STAT["player_receptions_alternate"] is Stat.RECEPTIONS
    assert MARKET_STAT["player_receptions"] is Stat.RECEPTIONS


def test_every_pilot_market_maps_to_a_stat():
    from jobs.backfill_oddsapi import PILOT_MARKETS
    for m in PILOT_MARKETS:
        assert MARKET_STAT.get(m) is not None, m


def test_tackles_can_push_but_yardage_cannot():
    """Tackles are a discrete count, so an integer line can land exactly on it.
    Yardage lines are half-points and the stat is treated as continuous."""
    assert is_push_possible(5.0, Stat.TACKLES_ASSISTS) is True
    assert is_push_possible(5.5, Stat.TACKLES_ASSISTS) is False
    assert is_push_possible(50.0, Stat.RECEIVING_YARDS) is False


# --- ambiguity is broken on facts, never on similarity -----------------------

@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setattr(config, "RAW_DIR", str(tmp_path / "raw"))
    store.init_db()
    now = time.time()
    # Two real same-name players from the pilot: a MIN corner and a SEA tackle.
    store.replace_rows(
        "player_xwalk",
        ("gsis_id", "display_name", "position", "last_season", "status",
         "ingested_ts"),
        [("00-0035236", "Byron Murphy", "CB", 2026, "ACT", now),
         ("00-0039309", "Byron Murphy II", "DT", 2026, "ACT", now)], None)
    store.replace_rows(
        "player_alias", ("alias", "gsis_id", "source", "last_season"),
        [("byron murphy", "00-0035236", "display", 2026),
         ("byron murphy ii", "00-0039309", "display", 2026),
         ("byron murphy", "00-0039309", "short", 2026)], None)
    store.replace_rows(
        "nfl_player_week",
        ("sport", "gsis_id", "season", "week", "season_type", "data_version",
         "team", "source", "ingested_ts"),
        [("nfl", "00-0035236", 2024, 8, "REG", "v1", "MIN", "t", now),
         ("nfl", "00-0039309", 2024, 8, "REG", "v1", "SEA", "t", now)], None)
    yield tmp_path


def test_the_game_teams_separate_two_players_of_the_same_name(env):
    """A prop belongs to ONE game, so the player is on one of two rosters. That
    is the strongest fact available and it resolves pairs nothing else can."""
    gsis, method, conf = resolve_player("Byron Murphy", 2024, teams=("MIN", "LA"))
    assert gsis == "00-0035236"
    assert method.endswith("+team") and conf == 1.0

    gsis2, _, _ = resolve_player("Byron Murphy", 2024, teams=("SEA", "BUF"))
    assert gsis2 == "00-0039309"


def test_without_the_teams_it_refuses_rather_than_guesses(env):
    """Both played in 2024, so no other fact separates them. Unresolved is the
    correct answer - picking one would silently price the wrong player."""
    with pytest.raises(Unresolved) as e:
        resolve_player("Byron Murphy", 2024)
    assert "ambiguous" in str(e.value)


def test_teams_that_match_neither_player_do_not_force_a_pick(env):
    with pytest.raises(Unresolved):
        resolve_player("Byron Murphy", 2024, teams=("DAL", "PHI"))
