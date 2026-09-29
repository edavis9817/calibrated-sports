"""The stored cross-venue join (a-53). Run: pytest -q tests/test_cross_venue_join.py

Two defects, one shape: a join that failed as an EMPTY SAMPLE rather than an
error. Live Odds API props never reached `markets` / `market_outcome`, so every
study rebuilt the Kalshi-to-book join in memory; and Kalshi's spread subject was
reworded, so a prose parse returned None for every rung. These tests pin the
join in the store and the team on the ticker.
"""
import time
from datetime import datetime, timezone

import pytest

import config
import store
from jobs import map_markets
from research import consensus as C
from research import cross_venue_join as J
from venues import kalshi, oddsapi
from venues.mapping import Unresolved, team_abbr

EID = "8bd90781e17e6d97df3941063cedae1e"
KICK = datetime(2026, 9, 10, 0, 20, tzinfo=timezone.utc).timestamp()
JSN = "Jaxon Smith-Njigba"


# --- the Kalshi spread team comes from the ticker ------------------------------

def test_the_reworded_subject_is_what_broke_the_prose_parse():
    """The case the old parse failed on, so the tests below are known to be on it."""
    assert team_abbr("ARI Cardinals") is None
    assert team_abbr("CHI Bears") is None


def test_spread_team_reads_the_ticker_suffix():
    assert kalshi.spread_team("KXNFLSPREAD-26SEP28PHICHI-CHI28") == "CHI"
    assert kalshi.spread_team("KXNFLSPREAD-26SEP28PHICHI-PHI3") == "PHI"
    # Kalshi's codes fold to nflverse's.
    assert kalshi.spread_team("KXNFLSPREAD-26SEP10SFLAR-LAR4") == "LA"
    assert kalshi.spread_team("KXNFLSPREAD-26SEP14JACDEN-JAC7") == "JAX"


def test_spread_team_refuses_a_team_not_in_the_game():
    assert kalshi.spread_team("KXNFLSPREAD-26SEP28PHICHI-DAL3") is None
    assert kalshi.spread_team("KXNFLTOTAL-26SEP28PHICHI-45") is None
    assert kalshi.spread_team("KXNFLSPREAD-26SEP28PHICHI") is None
    assert kalshi.spread_team("") is None


def test_consensus_key_ignores_the_subject_text():
    new = "ARI Cardinals wins by over 2.5 points"
    assert C.kalshi_key("KXNFLSPREAD-26SEP27ARISF-ARI3", new, 2.5) == ("spread", "ARI", 2.5)
    assert C.kalshi_key("KXNFLSPREAD-26SEP27ARISF-ARI3", None, 2.5) == ("spread", "ARI", 2.5)
    # A subject naming a different team does not move the key: the ticker wins.
    assert C.kalshi_key("KXNFLSPREAD-26SEP27ARISF-SF3", new, 2.5) == ("spread", "SF", 2.5)


# --- the instrument id ----------------------------------------------------------

def test_prop_market_id_round_trips_and_carries_the_line():
    q = f"{EID}|player_receptions|{JSN}|Over"
    mid = oddsapi.prop_market_id(q, 6.5)
    assert mid == f"{q}|6.5"
    assert oddsapi.split_prop_market_id(mid) == (q, 6.5)
    assert oddsapi.split_prop_market_id(oddsapi.prop_market_id(q, 5)) == (q, 5.0)


def test_a_four_part_quote_id_is_not_an_instrument():
    with pytest.raises(ValueError):
        oddsapi.split_prop_market_id(f"{EID}|player_receptions|{JSN}|Over")
    with pytest.raises(Unresolved, match="no line"):
        oddsapi.map_market({"market_id": f"{EID}|player_receptions|{JSN}|Over"})


def test_book_only_reason_carries_the_claim():
    from core.outcomes import Side, Stat, player_prop
    o = player_prop(2026, 1, "00-0038543", Stat.RECEIVING_YARDS, 65.5, Side.OVER)
    r = oddsapi.book_only_reason(o)
    assert len(r) <= 200
    assert oddsapi.parse_book_only(r) == (o.outcome_id, o.key)
    assert oddsapi.parse_book_only("no player matches 'x'") is None


# --- the join, end to end --------------------------------------------------------

@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setattr(config, "STORAGE_DIR", str(tmp_path))
    monkeypatch.setattr(config, "RAW_DIR", str(tmp_path / "raw"))
    store.init_db()
    now = time.time()
    store.replace_rows(
        "nfl_games",
        ("sport", "game_id", "data_version", "season", "week", "gameday",
         "home_team", "away_team", "kickoff_ts", "source", "ingested_ts"),
        [("nfl", "2026_01_NE_SEA", "2026-09-09", 2026, 1, "2026-09-09",
          "SEA", "NE", KICK, "test", now)])
    store.replace_rows(
        "player_xwalk",
        ("gsis_id", "display_name", "position", "last_season", "status", "ingested_ts"),
        [("00-0038543", JSN, "WR", 2026, "ACT", now)])
    store.replace_rows(
        "player_alias", ("alias", "gsis_id", "source", "last_season"),
        [("jaxon smith njigba", "00-0038543", "display", 2026)])
    # The Odds API discovery row: the title is built from the event's
    # structured away_team / home_team, and close_ts is its commence time.
    store.upsert_markets([{
        "venue": "oddsapi", "market_id": f"game:{EID}", "event_id": EID,
        "market_type": "game", "title": "New England Patriots @ Seattle Seahawks",
        "close_ts": KICK}])
    # The Kalshi rung "7+ receptions" = over 6.5.
    k_id, _, _ = kalshi.map_market({
        "market_id": "KXNFLREC-26SEP09NESEA-SEAJSMITHNJIGBA1-7",
        "market_type": "prop", "title": f"{JSN}: 7+ receptions",
        "subject": f"{JSN}: 7+", "line": 6.5})
    store.record_mapping("kalshi", "KXNFLREC-26SEP09NESEA-SEAJSMITHNJIGBA1-7",
                         k_id, "kalshi:test", 1.0)
    yield {"kalshi_outcome": k_id}


def _quote(book, mkey, side, line, ts, price=-110):
    return {"ts": ts, "sport": "nfl", "venue": f"oddsapi:{book}", "event_id": EID,
            "market_id": f"{EID}|{mkey}|{JSN}|{side}", "market_type": "prop",
            "subject": JSN, "line": line, "side": side, "mid": 0.52, "last": price,
            "raw_ref": "oddsapi/prop"}


def _write_slate(t0):
    rows = [
        _quote("draftkings", "player_receptions", "Over", 6.5, t0),
        _quote("draftkings", "player_receptions", "Under", 6.5, t0),
        _quote("draftkings", "player_receptions", "Over", 6.5, t0 + 3600),
        # The line moved: same four-part id, a different claim.
        _quote("draftkings", "player_receptions", "Over", 5.5, t0 + 7200),
        _quote("fanduel", "player_receptions_alternate", "Over", 6.5, t0 + 60),
        _quote("fanduel", "player_reception_yds", "Over", 65.5, t0 + 60),
        _quote("fanduel", "totals_h1", "Over", 23.5, t0 + 60),
    ]
    store.write_quotes(rows, dedupe=False)


def _mo(venue, market_id):
    with store.db() as c:
        return c.execute("SELECT outcome_id, unmapped_reason FROM market_outcome "
                         "WHERE venue=? AND market_id=?", (venue, market_id)).fetchone()


def test_a_book_line_and_the_kalshi_rung_land_on_one_outcome(env):
    """THE acceptance test. DraftKings' over 6.5 and FanDuel's alternate over 6.5
    are Kalshi's 7+, so all three share one outcome_id - stored, not rebuilt."""
    _write_slate(KICK - 10 * 3600)
    res = map_markets.run_book_props()
    q = f"{EID}|player_receptions|{JSN}|Over"
    dk = _mo("oddsapi:draftkings", oddsapi.prop_market_id(q, 6.5))
    fd = _mo("oddsapi:fanduel", oddsapi.prop_market_id(
        f"{EID}|player_receptions_alternate|{JSN}|Over", 6.5))
    assert dk[0] == env["kalshi_outcome"]
    assert fd[0] == env["kalshi_outcome"]
    assert res["census"]["linked"] == 2
    # A moved line is its own instrument, not an overwrite of the old one.
    assert _mo("oddsapi:draftkings", oddsapi.prop_market_id(q, 5.5)) is not None


def test_no_outcome_is_created_by_default_and_the_claim_is_still_recorded(env):
    """Every settled player outcome enters the published prop history, so a
    book-only line must not create one. It is recorded with its full claim."""
    with store.db() as c:
        before = c.execute("SELECT COUNT(*) FROM outcomes").fetchone()[0]
    _write_slate(KICK - 10 * 3600)
    res = map_markets.run_book_props()
    with store.db() as c:
        after = c.execute("SELECT COUNT(*) FROM outcomes").fetchone()[0]
    assert after == before
    assert res["census"]["new_outcomes"] == 0
    yds = _mo("oddsapi:fanduel", oddsapi.prop_market_id(
        f"{EID}|player_reception_yds|{JSN}|Over", 65.5))
    assert yds[0] is None
    oid, key = oddsapi.parse_book_only(yds[1])
    assert key == "nfl|2026|wk1|player_prop|00-0038543|receiving_yards|65.5|over"
    # The under on the Kalshi line is book-only too: Kalshi lists the over.
    under = _mo("oddsapi:draftkings", oddsapi.prop_market_id(
        f"{EID}|player_receptions|{JSN}|Under", 6.5))
    assert oddsapi.parse_book_only(under[1])[1].endswith("|receptions|6.5|under")
    # A non-player key is unresolved with its reason, never dropped.
    h1 = _mo("oddsapi:fanduel", oddsapi.prop_market_id(
        f"{EID}|totals_h1|{JSN}|Over", 23.5))
    assert h1[0] is None and "not a player prop" in h1[1]


def test_create_is_what_creates_the_outcome(env):
    """Discriminates the test above: the same rows DO create outcomes when asked."""
    _write_slate(KICK - 10 * 3600)
    res = map_markets.run_book_props(create=True)
    assert res["census"]["new_outcomes"] >= 2          # yards over, receptions under, 5.5
    yds = _mo("oddsapi:fanduel", oddsapi.prop_market_id(
        f"{EID}|player_reception_yds|{JSN}|Over", 65.5))
    assert yds[0] is not None


def test_last_seen_is_the_last_quote_and_pruning_cannot_shrink_it(env):
    t0 = KICK - 10 * 3600
    _write_slate(t0)
    map_markets.run_book_props()
    mid = oddsapi.prop_market_id(f"{EID}|player_receptions|{JSN}|Over", 6.5)
    with store.db() as c:
        first, last, close = c.execute(
            "SELECT first_seen, last_seen, close_ts FROM markets WHERE venue=? AND market_id=?",
            ("oddsapi:draftkings", mid)).fetchone()
        assert (first, last, close) == (t0, t0 + 3600, KICK)
        # Retention deletes the later quote; a re-derive must not move last_seen back.
        c.execute("DELETE FROM quotes WHERE ts = ?", (t0 + 3600,))
    map_markets.run_book_props()
    with store.db() as c:
        assert c.execute("SELECT last_seen FROM markets WHERE venue=? AND market_id=?",
                         ("oddsapi:draftkings", mid)).fetchone()[0] == t0 + 3600


def test_the_census_reads_the_stored_join(env):
    _write_slate(KICK - 10 * 3600)
    map_markets.run_book_props()
    t, _ = J.triples(J.ro(), 2026)
    cells = J.coverage(t)
    assert cells[("receptions", 1)]["both"] == 1                # 6.5: book + Kalshi
    assert cells[("receptions", 1)]["book only"] == 1           # the moved 5.5
    assert cells[("receiving_yards", 1)]["book only"] == 1
    g = J.closes(J.ro())
    assert len(g) == 1 and g[0]["game_id"] == "2026_01_NE_SEA"
    assert g[0]["lead_nfl_min"] == pytest.approx((KICK - (KICK - 10 * 3600 + 7200)) / 60)


def test_bench_price_is_core_board_de_vig_through_the_join(env):
    """The join carries no price of its own: a study reads quotes through it and
    de-vigs with core.board, the Board's own function, unchanged."""
    from core import board as B
    t0 = KICK - 10 * 3600
    store.write_quotes([
        _quote("draftkings", "player_receptions", "Over", 6.5, t0, price=-120),
        _quote("draftkings", "player_receptions", "Under", 6.5, t0, price=100),
        _quote("betmgm", "player_receptions", "Over", 6.5, t0, price=-110),
        _quote("betmgm", "player_receptions", "Under", 6.5, t0, price=-110),
    ], dedupe=False)
    map_markets.run_book_props()
    p, n = J.bench_price(J.ro(), env["kalshi_outcome"], KICK)
    want = sorted([B.devig_mult(-120, 100), B.devig_mult(-110, -110)])
    assert n == 2
    assert p == pytest.approx(sum(want) / 2)
    assert J.bench_price(J.ro(), env["kalshi_outcome"], t0 - 1) == (None, 0)
