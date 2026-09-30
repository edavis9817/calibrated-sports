"""The three Polymarket mapping faults a-59 found, fixed in a-60.

  1. spreads keyed on the book's sign, so "Seahawks (-2.5)" and Kalshi's
     "Seattle wins by over 2.5" were two outcome ids for one claim;
  2. the game moneyline, titled "Patriots vs. Seahawks", matched no shape;
  3. nothing re-mapped Polymarket weekly (tests/test_weekly_refresh.py) - and
     doing so must not add a Polymarket-only player line to the published
     prop history, so player props link only.

Each fix is tested for the pairing it creates AND for the pairing it must
refuse, because a mapping error and a real disagreement look identical in a
price and different only in a census.
"""
import time

import pytest

import config
import store
from venues import kalshi, polymarket
from venues.mapping import Unresolved

EVENT = "nfl-ne-sea-2026-09-10"          # Polymarket's UTC date for the 09-09 opener


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setattr(config, "RAW_DIR", str(tmp_path / "raw"))
    store.init_db()
    now = time.time()
    store.replace_rows(
        "nfl_games",
        ("sport", "game_id", "data_version", "season", "week", "gameday",
         "home_team", "away_team", "source", "ingested_ts"),
        [("nfl", "2026_01_NE_SEA", "2026-09-09", 2026, 1, "2026-09-09",
          "SEA", "NE", "test", now)])
    store.replace_rows(
        "player_xwalk",
        ("gsis_id", "display_name", "position", "last_season", "status", "ingested_ts"),
        [("00-0038543", "Jaxon Smith-Njigba", "WR", 2026, "ACT", now)])
    store.replace_rows(
        "player_alias", ("alias", "gsis_id", "source", "last_season"),
        [("jaxon smith njigba", "00-0038543", "display", 2026)])
    yield tmp_path


def _key(oid):
    with store.db() as c:
        return c.execute("SELECT key FROM outcomes WHERE outcome_id = ?", (oid,)).fetchone()[0]


def poly(title, event=EVENT, **kw):
    return polymarket.map_market({"market_id": "0xt", "market_type": None,
                                  "title": title, "event_id": event}, **kw)


# --- 1. the spread sign ------------------------------------------------------

def test_a_polymarket_spread_is_the_kalshi_claim_it_names(env):
    k_id, _, _ = kalshi.map_market({
        "market_id": "KXNFLSPREAD-26SEP09NESEA-SEA2", "market_type": "spread",
        "title": "Seattle wins by over 2.5 points?", "subject": "", "line": 2.5})
    p_id, method, _ = poly("Spread: Seahawks (-2.5)")
    assert method == "poly:spread"
    assert p_id == k_id
    assert _key(p_id) == "nfl|2026|wk1|spread|sea|na|2.5|over"


def test_the_underdog_form_is_the_negative_canonical_line(env):
    """'Patriots (+2.5)' is Patriots lose by less than 2.5: margin > -2.5. It is
    NOT the Seahawks claim and must not collide with it."""
    p_id, _, _ = poly("Spread: Patriots (+2.5)")
    assert _key(p_id) == "nfl|2026|wk1|spread|ne|na|-2.5|over"
    s_id, _, _ = poly("Spread: Seahawks (-2.5)")
    assert s_id != p_id


# --- 2. the head-to-head moneyline -------------------------------------------

def test_a_head_to_head_title_is_the_first_named_teams_moneyline(env):
    """The logged token is outcomes[0], which is the first-named team (61 of
    61 in the raw events payload, a-60). So it is THAT team's moneyline."""
    k_id, _, _ = kalshi.map_market({
        "market_id": "KXNFLGAME-26SEP09NESEA-NE", "market_type": "moneyline",
        "title": "New England at Seattle Winner?", "subject": "New England", "line": None})
    p_id, method, _ = poly("Patriots vs. Seahawks")
    assert method == "poly:head_to_head"
    assert p_id == k_id
    assert _key(p_id) == "nfl|2026|wk1|moneyline|ne|na|na|yes"


def test_a_head_to_head_naming_another_game_refuses(env):
    with pytest.raises(Unresolved, match="is not game"):
        poly("Patriots vs. Bills")


def test_a_colon_title_is_never_read_as_the_moneyline(env):
    """'X vs. Y: <something>' is a different claim about the game."""
    for t in ("Patriots vs. Seahawks: Highest Scoring Quarter - Q4",
              "Patriots vs. Seahawks: Both Teams to Score Points - 2Q"):
        with pytest.raises(Unresolved):
            poly(t)


def test_an_unknown_team_in_a_head_to_head_is_unresolved(env):
    with pytest.raises(Unresolved, match="unknown team"):
        poly("Patriots vs. Sharks")


# --- 3. player props link, they do not create --------------------------------

def test_a_polymarket_only_player_line_is_recorded_not_created(env):
    with pytest.raises(polymarket.PolyOnly) as e:
        poly("Jaxon Smith-Njigba: Receiving Yards O/U 70.5",
             event=EVENT + "-player-props")
    assert str(e.value).startswith("polymarket-only claim ")
    assert "receiving_yards|70.5|over" in str(e.value)
    with store.db() as c:
        assert c.execute("SELECT COUNT(*) FROM outcomes").fetchone()[0] == 0


def test_a_player_line_another_venue_created_links(env):
    k_id, _, _ = kalshi.map_market({
        "market_id": "KXNFLREC-26SEP09NESEA-SEAJSMITHNJIGBA1-7", "market_type": "prop",
        "title": "Jaxon Smith-Njigba: 7+ receptions", "subject": "", "line": 6.5})
    p_id, _, _ = poly("Jaxon Smith-Njigba: Receptions O/U 6.5",
                      event=EVENT + "-player-props")
    assert p_id == k_id


def test_creation_is_still_available_when_asked_for(env):
    p_id, _, _ = poly("Jaxon Smith-Njigba: Receiving Yards O/U 70.5",
                      event=EVENT + "-player-props", create_players=True)
    assert _key(p_id).endswith("receiving_yards|70.5|over")


def test_team_claims_are_created_without_a_flag(env):
    """Team and game outcomes never enter the player prop history."""
    p_id, _, _ = poly("Patriots vs. Seahawks: O/U 44.5")
    assert _key(p_id) == "nfl|2026|wk1|total|2026-01-ne-sea|na|44.5|over"
