"""Research holds on the prune (a-60): store.hold_quotes and jobs.hold_weeks.

The export holds what the site shows, on a rolling window. A week research
still needs - the only live-tier in-game week on disk - is held by
`jobs.hold_weeks` through `store.hold_quotes`, and two things must be true of
it: the hold keeps rows the prune would delete, and nothing that renews a hold
later (the export's weekly run) can cut it short.
"""
import sqlite3
import time

import pytest

import config
import store
from jobs import export_web as E
from jobs import hold_weeks, prune_quotes


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setattr(config, "RAW_DIR", str(tmp_path / "raw"))
    store.init_db()
    store.reset_quote_state()
    yield tmp_path
    store.reset_quote_state()


def _hold_row(venue, mid):
    with store.db() as c:
        return c.execute("SELECT until_ts, reason FROM quote_retention_hold "
                         "WHERE venue = ? AND market_id = ?", (venue, mid)).fetchone()


def _stale_quote(mid, venue="kalshi", days_ago=40):
    now = time.time()
    store.write_quotes([{"ts": now - days_ago * 86400, "sport": "nfl", "venue": venue,
                         "market_id": mid, "best_bid": 0.4, "best_ask": 0.42, "mid": 0.41}])
    with store.db() as c:
        c.execute("UPDATE quotes SET ingest_ts = ? WHERE venue = ? AND market_id = ?",
                  (now - days_ago * 86400, venue, mid))


def test_a_hold_extends_and_never_shortens(env):
    far, near = time.time() + 200 * 86400, time.time() + 10 * 86400
    assert store.hold_quotes([("kalshi", "A")], far, "research: keep week 3") == 1
    store.hold_quotes([("kalshi", "A")], near, "a shorter ask")
    until, reason = _hold_row("kalshi", "A")
    assert until == pytest.approx(far)
    assert reason == "research: keep week 3"       # the longest hold answers "why"

    later = far + 86400
    store.hold_quotes([("kalshi", "A")], later, "a longer ask")
    until, reason = _hold_row("kalshi", "A")
    assert until == pytest.approx(later) and reason == "a longer ask"


def test_a_hold_refuses_no_reason_and_no_end(env):
    with pytest.raises(ValueError):
        store.hold_quotes([("kalshi", "A")], time.time() + 86400, "  ")
    with pytest.raises(ValueError):
        store.hold_quotes([("kalshi", "A")], time.time() - 1, "already over")
    with pytest.raises(ValueError):
        store.hold_quotes([("kalshi", "A")], None, "forever")
    assert store.hold_quotes([], time.time() + 86400, "nothing") == 0


def test_the_export_renewal_cannot_clip_a_research_hold(env):
    """The export renews every published market to one retention window from
    now. Before a-58/a-60 that renewal REPLACED until_ts, so the next weekly
    export would have cut a 2027 research hold back to 14 days."""
    far = time.time() + 200 * 86400
    store.hold_quotes([("kalshi", "PUB")], far, "research")
    E.hold_published_markets({("kalshi", "PUB"), ("kalshi", "NEW")}, time.time())
    assert _hold_row("kalshi", "PUB")[0] == pytest.approx(far)
    new_until = _hold_row("kalshi", "NEW")[0]
    assert new_until == pytest.approx(time.time() + config.QUOTES_RETENTION_DAYS * 86400,
                                      abs=60)


def test_a_research_hold_survives_the_prune_and_its_neighbour_does_not(env):
    _stale_quote("held")
    _stale_quote("unheld")
    _stale_quote("held", venue="polymarket")        # same id, other venue: not held
    store.hold_quotes([("kalshi", "held")], time.time() + 86400, "research")
    stats = prune_quotes.run(days=14)
    assert stats["deleted"] == 2 and stats["held"] == 1
    with store.db() as c:
        left = c.execute("SELECT venue, market_id FROM quotes").fetchall()
    assert left == [("kalshi", "held")]


def test_ticker_date_and_the_slug_day_after():
    assert hold_weeks.ticker_date("2026-09-24") == "26SEP24"
    assert hold_weeks.ticker_date("2026-10-05") == "26OCT05"
    assert hold_weeks.day_after("2026-09-30") == "2026-10-01"
    m = hold_weeks.GAME_SLUG.match("nfl-sf-la-2026-09-11-player-props")
    assert m and m.group(1) == "2026-09-11"
    assert not hold_weeks.GAME_SLUG.match("pro-football-2026-27-week-3-receiving-yards-leader")


def test_week_selection_takes_the_week_and_nothing_either_side(env):
    with store.db() as c:
        c.executemany(
            "INSERT INTO nfl_games (sport, game_id, data_version, season, week, game_type, "
            "gameday, kickoff_ts, home_team, away_team, source, ingested_ts) "
            "VALUES ('nfl',?,'v',2026,?,'REG',?,?,?,?,'test',0)",
            [("2026_03_ATL_GB", 3, "2026-09-24", 1790295300.0, "GB", "ATL"),
             ("2026_03_PHI_CHI", 3, "2026-09-28", 1790640900.0, "CHI", "PHI"),
             ("2026_04_PIT_CLE", 4, "2026-10-01", 1790900100.0, "CLE", "PIT")])
        c.executemany(
            "INSERT INTO markets (venue, market_id, event_id, sport, market_type, title) "
            "VALUES (?,?,?,?,?,?)",
            [("kalshi", "KXNFLREC-26SEP24ATLGB-X", "e", "nfl", "prop", "t"),
             ("kalshi", "KXNFLGAME-26SEP28PHICHI-CHI", "e", "nfl", "moneyline", "t"),
             ("kalshi", "KXNFLREC-26OCT01PITCLE-X", "e", "nfl", "prop", "t"),       # week 4
             ("kalshi", "KXNFLPASSYDS-26SEP24ATLGB-X", "e", "nfl", "prop", "t"),   # not a held series
             # Thursday 20:15 ET is Friday in UTC: slugged the day after
             ("polymarket", "p1", "nfl-atl-gb-2026-09-25-player-props", "nfl", "prop", "t"),
             ("polymarket", "p2", "nfl-phi-chi-2026-09-29", "nfl", "spread", "t"),
             ("polymarket", "p3", "nfl-pit-cle-2026-10-02", "nfl", "spread", "t"),  # week 4
             ("polymarket", "p4", "pro-football-2026-27-week-3-receiving-yards-leader",
              "nfl", "prop", "t")])
    ro = sqlite3.connect(f"file:{config.DB_PATH}?mode=ro", uri=True)
    try:
        sel = hold_weeks.select(ro, 2026, 3)
    finally:
        ro.close()
    got = {(v, m) for (v, _), ids in sel.items() for m in ids}
    assert got == {("kalshi", "KXNFLREC-26SEP24ATLGB-X"),
                   ("kalshi", "KXNFLGAME-26SEP28PHICHI-CHI"),
                   ("polymarket", "p1"), ("polymarket", "p2")}


def test_a_week_with_no_schedule_refuses(env):
    ro = sqlite3.connect(f"file:{config.DB_PATH}?mode=ro", uri=True)
    try:
        with pytest.raises(SystemExit):
            hold_weeks.select(ro, 2026, 9)
    finally:
        ro.close()
