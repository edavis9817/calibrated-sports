"""Brief 021 B2: halftime capture spends nothing unless switched on, polls only
inside the window, respects cadence / reserve / daily cap, and persists each
book's own clock."""
import asyncio
import sqlite3
from datetime import datetime

import pytest

import config
import store
from jobs import halftime_plan
from venues.base import RateLimiter
from venues.oddsapi import OddsApiClient

NOW = 1_790_000_000.0


class _Resp:
    def __init__(self, payload, cost=2, remaining=40_000):
        self.status_code = 200
        self.headers = {"x-requests-last": str(cost), "x-requests-remaining": str(remaining),
                        "x-requests-used": "1"}
        self._p = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._p


class _Http:
    def __init__(self, payload=None, cost=2, remaining=40_000):
        self.calls, self.payload, self.cost, self.remaining = [], payload or [], cost, remaining

    async def get(self, url, params=None):
        self.calls.append((url, dict(params or {})))
        return _Resp(self.payload, self.cost, self.remaining)


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setattr(config, "RAW_DIR", str(tmp_path / "raw"))
    monkeypatch.setattr(config, "ODDS_API_KEY", "test")
    monkeypatch.setattr(config, "ODDS_HALFTIME_ENABLED", True)
    monkeypatch.setattr(config, "ODDS_HALFTIME_FROM_MIN", 66)
    monkeypatch.setattr(config, "ODDS_HALFTIME_TO_MIN", 123)
    monkeypatch.setattr(config, "ODDS_HALFTIME_EVERY", 30)
    monkeypatch.setattr(config, "ODDS_HALFTIME_DAILY_CAP", 1200)
    monkeypatch.setattr(config, "ODDS_RESERVE", 20_000)
    store.init_db()


def _client(http, kick_min_ago):
    c = OddsApiClient(http, RateLimiter(1000))
    c.remember([{"event_id": "E1", "close_ts": NOW - kick_min_ago * 60},
                {"event_id": "E2", "close_ts": NOW + 3600}], now=NOW)
    return c


def test_disabled_makes_no_call_even_inside_the_window(env, monkeypatch):
    monkeypatch.setattr(config, "ODDS_HALFTIME_ENABLED", False)
    http = _Http()
    c = _client(http, 90)
    assert not c.halftime_active(NOW) and not c.halftime_due(NOW)
    assert asyncio.run(c.fetch_halftime(now=NOW)) == []
    assert http.calls == []


@pytest.mark.parametrize("mins,inside", [(65.9, False), (66, True), (123, True), (123.1, False)])
def test_window_edges(env, mins, inside):
    c = _client(_Http(), mins)
    assert (c.halftime_events(NOW) == ["E1"]) is inside


def test_one_bulk_call_for_spreads_and_totals_on_in_window_events_only(env):
    http = _Http()
    c = _client(http, 90)
    asyncio.run(c.fetch_halftime(now=NOW))
    (url, params), = http.calls
    assert url.endswith("/sports/americanfootball_nfl/odds")
    assert params["markets"] == "spreads,totals" and params["eventIds"] == "E1"


def test_cadence_is_respected(env):
    http = _Http()
    c = _client(http, 90)
    asyncio.run(c.fetch_halftime(now=NOW))
    assert not c.halftime_due(NOW + 10)
    assert c.halftime_due(NOW + 30)


def test_daily_cap_stops_spend(env, monkeypatch):
    monkeypatch.setattr(config, "ODDS_HALFTIME_DAILY_CAP", 4)
    http = _Http(cost=2)
    c = _client(http, 90)
    asyncio.run(c.fetch_halftime(now=NOW))
    asyncio.run(c.fetch_halftime(now=NOW + 30))
    asyncio.run(c.fetch_halftime(now=NOW + 60))
    assert len(http.calls) == 2


def test_reserve_stops_spend(env):
    http = _Http(remaining=19_999)
    c = _client(http, 90)
    asyncio.run(c.fetch_halftime(now=NOW))          # first call learns the balance
    assert not c.halftime_due(NOW + 30)


def test_a_started_game_is_not_forgotten_when_discovery_drops_it(env):
    c = _client(_Http(), 90)
    c.remember([{"event_id": "E2", "close_ts": NOW + 3600}], now=NOW)
    assert c.halftime_events(NOW) == ["E1"]


def test_every_row_carries_the_books_own_clock(env):
    c = OddsApiClient(_Http(), RateLimiter(1000))
    ev = [{"id": "E1", "bookmakers": [
        {"key": "dk", "last_update": "2026-09-13T17:00:00Z", "markets": [
            {"key": "spreads", "last_update": "2026-09-13T17:30:05Z", "outcomes": [
                {"name": "A", "price": -110, "point": -3.5}, {"name": "B", "price": -110, "point": 3.5}]},
            {"key": "totals", "outcomes": [
                {"name": "Over", "price": -110, "point": 44.5}, {"name": "Under", "price": -110, "point": 44.5}]}]}]}]
    rows = c._parse_events(ev, "game", tag="halftime")
    spread = [r for r in rows if "|spreads|" in r["market_id"]]
    total = [r for r in rows if "|totals|" in r["market_id"]]
    iso = lambda s: datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    assert {r["source_ts"] for r in spread} == {iso("2026-09-13T17:30:05Z")}
    assert {r["source_ts"] for r in total} == {iso("2026-09-13T17:00:00Z")}   # book fallback
    assert {r["raw_ref"] for r in rows} == {"oddsapi/halftime"}


def test_write_quotes_persists_source_ts(env):
    store.write_quotes([{"ts": NOW, "sport": "nfl", "venue": "oddsapi:dk", "market_id": "E1|spreads||A",
                         "market_type": "game", "mid": 0.52, "source_ts": NOW - 40}])
    con = sqlite3.connect(config.DB_PATH)
    assert con.execute("SELECT source_ts FROM quotes").fetchone()[0] == NOW - 40


def test_plan_merges_overlapping_windows_and_prices_per_call():
    kicks = [0, 0, 3900, 4 * 3600]           # two 1pm games, a 4:05 and a late game
    windows, minutes = halftime_plan.merged_minutes(kicks, 66, 123)
    assert len(windows) == 3 and minutes == pytest.approx(57 + 57 + 57 - 0)
    assert halftime_plan.credits_per_call("spreads,totals", "us") == 2
    c = halftime_plan.cost([0], 66, 123, 30, 2)
    assert c["calls"] == pytest.approx(114) and c["credits"] == pytest.approx(228)
