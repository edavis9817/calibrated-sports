"""Outcome mapping tests. Run: pytest -q

The join key is the one thing here that cannot be wrong quietly. If two venues
produce different ids for the same claim, nothing errors - you simply never see
the cross-venue comparison, and the CLV you report is measured against your own
book. So these are mostly identity tests: same claim, same id.
"""
import time

import pytest

import config
import store
from core.outcomes import Stat, _fmt_line, is_push_possible, player_prop
from jobs import settle_outcomes
from venues import kalshi, polymarket
from venues.mapping import Unresolved


# --- line formatting: an unstable string forks the key space -----------------

def test_fmt_line_is_stable_across_equal_values():
    assert _fmt_line(5.5) == "5.5"
    assert _fmt_line(5.0) == "5"
    assert _fmt_line(5) == "5"
    assert _fmt_line(None) == "na"
    assert _fmt_line(-3.5) == "-3.5"
    assert _fmt_line(0.0) == "0"


def test_int_and_float_lines_produce_one_id():
    """5 and 5.0 are the same claim. If they hashed differently, one market's
    history would split in two and neither half would look wrong."""
    a = player_prop(2026, 1, "00-0000001", Stat.RECEPTIONS, 5)
    b = player_prop(2026, 1, "00-0000001", Stat.RECEPTIONS, 5.0)
    assert a.outcome_id == b.outcome_id
    assert "|5|" in a.key


def test_fmt_line_does_not_leak_float_repr():
    """str(x) gives '5.0' and carries binary-float noise; %g normalises both."""
    assert _fmt_line(99.50) == "99.5"
    assert _fmt_line(1e2) == "100"
    assert _fmt_line(0.1 + 0.2) == "0.3"        # not 0.30000000000000004


def test_outcome_id_is_16_hex_of_sha1():
    import hashlib
    o = player_prop(2026, 1, "00-0036355", Stat.RECEPTIONS, 5.5)
    assert o.outcome_id == hashlib.sha1(o.key.encode()).hexdigest()[:16]
    assert len(o.outcome_id) == 16


# --- push handling -----------------------------------------------------------

def test_push_possible_only_on_integer_lines_of_discrete_stats():
    assert is_push_possible(5.0, Stat.RECEPTIONS) is True
    assert is_push_possible(5, Stat.RECEPTIONS) is True
    assert is_push_possible(5.5, Stat.RECEPTIONS) is False
    assert is_push_possible(None, Stat.RECEPTIONS) is False


def test_yardage_is_not_treated_as_a_push_market():
    assert is_push_possible(50.0, Stat.RECEIVING_YARDS) is False
    assert is_push_possible(50.5, Stat.RECEIVING_YARDS) is False


def test_settlement_scores_a_push_as_a_push_not_a_win():
    """Scoring the mass exactly on the line as a win is how a backtest inflates
    its own hit rate on every integer-line prop."""
    assert settle_outcomes.resolve(6.0, 6.0, push_possible=True) == "push"
    assert settle_outcomes.resolve(6.0, 6.0, push_possible=False) == "over"
    assert settle_outcomes.resolve(7.0, 6.0, True) == "over"
    assert settle_outcomes.resolve(5.0, 6.0, True) == "under"
    assert settle_outcomes.resolve(None, 6.0, True) == "unsettled"


# --- the round trip ----------------------------------------------------------

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
        ("gsis_id", "display_name", "position", "last_season", "status",
         "ingested_ts"),
        [("00-0038543", "Jaxon Smith-Njigba", "WR", 2026, "ACT", now)])
    store.replace_rows(
        "player_alias", ("alias", "gsis_id", "source", "last_season"),
        [("jaxon smith njigba", "00-0038543", "display", 2026)])
    yield tmp_path


def test_kalshi_and_polymarket_agree_on_one_outcome_id(env):
    """THE acceptance test. Kalshi lists a 7+ threshold; Polymarket lists an
    O/U at 6.5. Same claim, so the same id - otherwise there is no cross-venue
    comparison and no CLV against a book you did not bet at."""
    k_id, k_method, _ = kalshi.map_market({
        "market_id": "KXNFLREC-26SEP09NESEA-SEAJSMITHNJIGBA1-7",
        "market_type": "prop",
        "title": "Jaxon Smith-Njigba: 7+ receptions",
        "subject": "Jaxon Smith-Njigba: 7+",
        "line": 6.5,
    })
    p_id, p_method, _ = polymarket.map_market({
        "market_id": "0xtoken",
        "market_type": "prop",
        "title": "Jaxon Smith-Njigba: Receptions O/U 6.5",
        "event_id": "nfl-ne-sea-2026-09-10-player-props",
    })

    assert k_id == p_id, f"{k_method} -> {k_id} vs {p_method} -> {p_id}"

    with store.db() as c:
        row = c.execute("SELECT key, entity_id, stat, line, side, week "
                        "FROM outcomes WHERE outcome_id=?", (k_id,)).fetchone()
    assert row[0] == "nfl|2026|wk1|player_prop|00-0038543|receptions|6.5|over"
    assert (row[1], row[2], row[3], row[4], row[5]) == (
        "00-0038543", "receptions", 6.5, "over", 1)


def test_the_venues_disagree_about_the_calendar_day_and_it_does_not_matter(env):
    """Kalshi dates the opener 09-09 (Eastern), Polymarket 09-10 (UTC). Keyed
    on the venue's own date, one game becomes two weeks - or fails outright."""
    from venues.mapping import game_for
    assert game_for("ne", "sea", "2026-09-09") == game_for("ne", "sea", "2026-09-10")


def test_threshold_and_ou_forms_are_the_same_claim(env):
    """A 4+ threshold pays on >= 4, which is the over on 3.5."""
    k_id, _, _ = kalshi.map_market({
        "market_id": "KXNFLREC-26SEP09NESEA-SEAJSMITHNJIGBA1-4",
        "market_type": "prop", "title": "Jaxon Smith-Njigba: 4+ receptions",
        "subject": "", "line": 3.5})
    p_id, _, _ = polymarket.map_market({
        "market_id": "0xtoken2", "market_type": "prop",
        "title": "Jaxon Smith-Njigba: Receptions O/U 3.5",
        "event_id": "nfl-ne-sea-2026-09-10-player-props"})
    assert k_id == p_id


# --- unmapped markets are recorded, never dropped ----------------------------

def test_an_unmappable_market_raises_with_a_reason(env):
    """Coverage computed over only the markets you managed to parse is not a
    coverage number. The reason is what makes the unmapped list a work queue."""
    with pytest.raises(Unresolved) as e:
        polymarket.map_market({
            "market_id": "0x", "market_type": "spread",
            "title": "1Q Spread: Ravens (-3.5)",
            "event_id": "nfl-bal-ind-2026-09-13"})
    assert "segment" in str(e.value).lower()

    with pytest.raises(Unresolved) as e2:
        kalshi.map_market({"market_id": "KXNFLWINS-SEA-9", "market_type": "future",
                           "title": "Seattle wins 9+ games", "subject": "",
                           "line": 8.5})
    assert "future" in str(e2.value).lower()


def test_an_unknown_player_is_unresolved_not_guessed(env):
    with pytest.raises(Unresolved) as e:
        polymarket.map_market({
            "market_id": "0x", "market_type": "prop",
            "title": "Notarealplayer Atall: Receptions O/U 4.5",
            "event_id": "nfl-ne-sea-2026-09-10-player-props"})
    assert "no player matches" in str(e.value)


def test_record_mapping_stores_the_reason(env):
    store.record_mapping("polymarket", "0xabc", unmapped_reason="segment market")
    with store.db() as c:
        row = c.execute("SELECT outcome_id, unmapped_reason FROM market_outcome "
                        "WHERE market_id='0xabc'").fetchone()
    assert row[0] is None and row[1] == "segment market"
