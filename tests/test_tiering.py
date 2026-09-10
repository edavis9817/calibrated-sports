"""Brief 008: kickoff-based tiering, and discovery off the polling path.

Run: pytest -q

The bug these exist for is not a crash. It is a logger that looks completely
healthy while sampling the wrong markets at the wrong rate - which is
indistinguishable from a quiet slate until you go looking for the ticks.
"""
import asyncio
import time

import pytest

import config
import run_logger
from run_logger import tier_for

NOW = 1_800_000_000.0
HOUR = 3600.0


def mkt(venue="kalshi", market_id="m", market_type="prop", close_ts=None):
    return {"venue": venue, "market_id": market_id,
            "market_type": market_type, "close_ts": close_ts}


def at(secs_to_kick, **kw):
    """A market whose mapped game kicks off `secs_to_kick` from NOW."""
    m = mkt(**kw)
    return m, {(m["venue"], m["market_id"]): NOW + secs_to_kick}


# --- the tier boundaries -----------------------------------------------------

@pytest.mark.parametrize("secs,expect", [
    (25 * HOUR, "cold"),        # kickoff tomorrow+, nothing to be fast about
    (24 * HOUR, "game"),        # exactly a day out
    (5 * HOUR, "game"),
    (4 * HOUR, "game"),         # the window is EXCLUSIVE at its edge
    (4 * HOUR - 1, "hot"),      # ...and hot starts one second inside it
    (1 * HOUR, "hot"),
    (60, "hot"),
    (0, "live"),                # kickoff instant belongs to live
    (-60, "live"),
    (-3 * HOUR, "live"),
    (-4 * HOUR, "cold"),        # live window closes; the game is over
    (-20 * HOUR, "cold"),
])
def test_tier_boundaries_key_on_kickoff(secs, expect):
    m, kicks = at(secs)
    assert tier_for(m, kicks, NOW) == expect


def test_the_hot_window_is_wide_enough_for_the_operational_windows():
    """HOT_WINDOW_MIN was widened 120 -> 240 to catch cross-game line movement
    across a full 13-game slate. The windows that matter operationally are the
    T-90 inactive report and the final hour; both were already inside 120, and
    this asserts the widening did not somehow lose them."""
    for mins in (90, 60, 15, 1):
        m, kicks = at(mins * 60)
        assert tier_for(m, kicks, NOW) == "hot", f"T-{mins}min must be hot"


def test_the_past_side_of_game_does_not_exist():
    """A game that kicked off five hours ago is finished and its prices have
    converged to 0/1. Treating the window as symmetric put ~1,300 dead markets
    on the 60s tier all Sunday night, and dragged them into DEPTH_TIERS to
    compete for the 27s depth cycle on the busiest night of the week."""
    for secs in (-4 * HOUR, -6 * HOUR, -12 * HOUR, -23 * HOUR):
        m, kicks = at(secs)
        assert tier_for(m, kicks, NOW) == "cold"


def test_cold_is_reachable_at_all():
    """Midweek. No game inside 24 hours is the normal state Monday to Friday,
    and it is most of the calendar."""
    m, kicks = at(72 * HOUR)
    assert tier_for(m, kicks, NOW) == "cold"


def test_futures_short_circuit_before_any_clock():
    m, kicks = at(1.0, market_type="future")
    assert tier_for(m, kicks, NOW) == "futures"


# --- kickoff beats close_ts --------------------------------------------------

def test_kickoff_wins_over_the_venues_own_close():
    """THE bug. Kalshi closes a player prop at GAME END and Polymarket closes
    the same claim at KICKOFF, so one close_ts is two different instants. Live
    on the 2026-09-10 opener: mid-game Kalshi sat on the 60s `game` tier while
    the identical Polymarket claim correctly ran at 10s."""
    kalshi = mkt(venue="kalshi", close_ts=NOW + 3 * HOUR)   # closes at game END
    poly = mkt(venue="polymarket", close_ts=NOW)            # closes at kickoff
    kicks = {("kalshi", "m"): NOW - 600, ("polymarket", "m"): NOW - 600}

    assert tier_for(kalshi, kicks, NOW) == "live"
    assert tier_for(poly, kicks, NOW) == "live"
    # and without the map, the two disagree - which is the observed failure
    assert tier_for(kalshi, None, NOW) == "hot"
    assert tier_for(poly, None, NOW) == "live"


def test_close_ts_is_the_fallback_not_the_rule():
    """A market discovery found before the mapper caught up still gets tiered,
    just on the venue's own clock."""
    m = mkt(close_ts=NOW + 30 * 60)
    assert tier_for(m, {}, NOW) == "hot"
    assert tier_for(mkt(close_ts=None), {}, NOW) == "game"


def test_an_unmapped_market_does_not_inherit_another_markets_kickoff():
    m = mkt(market_id="unmapped", close_ts=NOW + 30 * HOUR)
    kicks = {("kalshi", "other"): NOW + 60}
    assert tier_for(m, kicks, NOW) == "cold"


# --- discovery must not stall the polling loop -------------------------------

class _SlowDiscoveryClient:
    """A venue whose catalogue pass takes far longer than a tier interval.

    Not hypothetical: kalshi discovery averages 15.7s and has peaked at 37.6s
    against a `live` tier that wants to fire every 10s, inline, on the primary
    trading venue, during a slate where thirteen games move at once.

    The FIRST pass returns immediately, because until one has landed there is
    no catalogue to poll and the question does not arise. It is the second and
    later passes - a running logger refreshing its catalogue - where blocking
    costs ticks.
    """
    name = "kalshi"

    def __init__(self, discovery_secs):
        self.discovery_secs = discovery_secs
        self.polls = []
        self.discoveries = 0

    def _catalogue(self):
        return [{"venue": self.name, "market_id": "m", "market_type": "prop",
                 "close_ts": time.time() + 60}]

    async def list_markets(self):
        self.discoveries += 1
        if self.discoveries > 1:
            await asyncio.sleep(self.discovery_secs)
        return self._catalogue()

    async def fetch_quotes(self, markets):
        self.polls.append(time.monotonic())
        return []


def _drive(client, monkeypatch, run_for, poll_hot=0):
    """Run venue_worker for `run_for` seconds and stop it.

    asyncio.run rather than pytest-asyncio: the convention list in CLAUDE.md is
    polars / httpx / pytest and one plugin is not worth widening it for two
    tests.
    """
    monkeypatch.setattr(run_logger, "DISCOVERY_EVERY", 0)
    monkeypatch.setattr(run_logger.store, "upsert_markets", lambda *_a: None)
    monkeypatch.setattr(run_logger.store, "log_poll", lambda *a, **k: None)
    monkeypatch.setattr(run_logger.store, "write_quotes", lambda *a, **k: 0)
    monkeypatch.setattr(run_logger.store, "kickoff_map", lambda *_a: {})
    monkeypatch.setattr(config, "POLL_HOT", poll_hot)
    monkeypatch.setattr(config, "POLL_GAME", poll_hot)
    monkeypatch.setattr(config, "POLL_COLD", poll_hot)
    monkeypatch.setattr(config, "POLL_LIVE", poll_hot)
    monkeypatch.setattr(config, "POLL_FUTURES", poll_hot)
    monkeypatch.setattr(config, "LOOP_TICK", 0.01)
    # A fresh Event per driver. run_logger._stop is created at import, so once
    # one asyncio.run() has awaited it, it is bound to that loop and the next
    # test's loop refuses it. Production has exactly one loop and never hits
    # this; the tests have one per call.
    monkeypatch.setattr(run_logger, "_stop", asyncio.Event())

    async def go():
        run_logger._stop.clear()
        task = asyncio.create_task(run_logger.venue_worker(client))
        await asyncio.sleep(run_for)
        seen = len(client.polls)
        run_logger._stop.set()
        try:
            await asyncio.wait_for(task, timeout=5)
        except asyncio.TimeoutError:
            task.cancel()
        run_logger._stop.clear()
        return seen
    return asyncio.run(go())


def test_discovery_does_not_block_quote_polling(monkeypatch):
    """The whole point of moving discovery off the polling path. While the
    catalogue pass is in flight the loop must keep polling the catalogue it
    already has - not sit on an await."""
    client = _SlowDiscoveryClient(discovery_secs=1.5)
    polls_during = _drive(client, monkeypatch, run_for=0.35)

    assert client.discoveries == 2, "first pass landed, second still in flight"
    assert polls_during >= 2, "polled WHILE the catalogue pass was in flight"


def test_only_one_discovery_is_in_flight_at_a_time(monkeypatch):
    """Firing a fresh catalogue pass every loop iteration because the last one
    has not returned is how a slow venue turns into a stampede."""
    client = _SlowDiscoveryClient(discovery_secs=1.5)
    _drive(client, monkeypatch, run_for=0.35)
    assert client.discoveries == 2, "one completed, one in flight, no stampede"


def test_a_failed_discovery_keeps_the_previous_catalogue(monkeypatch):
    """A venue that changes a response shape must not empty the catalogue and
    silently stop capturing - that is the exact failure the dead-man switch
    exists for, and it should not be reachable in the first place."""
    class _BreaksAfterFirst(_SlowDiscoveryClient):
        async def list_markets(self):
            self.discoveries += 1
            if self.discoveries > 1:
                raise RuntimeError("gamma changed the payload shape")
            return self._catalogue()

    client = _BreaksAfterFirst(discovery_secs=0)
    polls = _drive(client, monkeypatch, run_for=0.15)
    assert client.discoveries >= 2
    assert polls >= 1, "kept polling the catalogue it already had"


# --- depth capture is tiered the same way ------------------------------------

class _Rows(list):
    """A cursor-shaped list; targets() calls .fetchall() on what execute returns."""

    def fetchall(self):
        return list(self)


def test_depth_targets_drop_futures_and_finished_games(monkeypatch):
    """A full depth snapshot costs ~27s. Sampling every mapped market at one
    rate means the books actually in play on a 13-game Sunday get sampled every
    86s - and depth not captured live is gone, because candlesticks carry price
    and volume but no book."""
    import sqlite3
    from jobs import capture_depth

    rows = [("in_play", "o1"), ("kicks_soon", "o2"), ("tomorrow", "o3"),
            ("finished", "o4"), ("next_week", "o5")]
    kicks = {("kalshi", "in_play"): NOW - 600,        # live
             ("kalshi", "kicks_soon"): NOW + 1800,    # hot
             ("kalshi", "tomorrow"): NOW + 20 * HOUR,  # game
             ("kalshi", "finished"): NOW - 8 * HOUR,   # cold
             ("kalshi", "next_week"): NOW + 6 * 24 * HOUR}   # cold

    class _Con:
        """targets() calls .fetchall(); the fake has to as well."""
        def execute(self, *_a):
            return _Rows(rows)

    monkeypatch.setattr(config, "DEPTH_TIER_ENABLED", True)
    monkeypatch.setattr(config, "DEPTH_TIERS", ("hot", "live", "game"))

    kept = capture_depth.targets(_Con(), "kalshi", kicks, NOW)

    assert [m for m, _o in kept] == ["in_play", "kicks_soon", "tomorrow"]


def test_an_unmapped_market_keeps_its_depth_snapshot(monkeypatch):
    """Tiering is here to spend the cycle where it is worth spending, not to
    silently narrow what gets captured on the strength of a missing join."""
    from jobs import capture_depth

    class _Con:
        def execute(self, *_a):
            return _Rows([("mapped_cold", "o1"), ("unmapped", "o2")])

    monkeypatch.setattr(config, "DEPTH_TIER_ENABLED", True)
    kept = capture_depth.targets(
        _Con(), "kalshi", {("kalshi", "mapped_cold"): NOW - 9 * HOUR}, NOW)
    assert [m for m, _o in kept] == ["unmapped"]


def test_tiering_off_returns_everything(monkeypatch):
    from jobs import capture_depth

    class _Con:
        def execute(self, *_a):
            return _Rows([("a", "o1"), ("b", "o2")])

    monkeypatch.setattr(config, "DEPTH_TIER_ENABLED", False)
    assert len(capture_depth.targets(_Con(), "kalshi", {"x": 1}, NOW)) == 2


# --- the cadence table itself ------------------------------------------------

def test_every_tier_has_a_cadence_and_cold_is_the_slowest():
    """A tier with no cadence entry is a tier that never polls. `cold` covers
    most of the calendar, so if it is not the slowest the whole point is lost."""
    cadences = {"hot": config.POLL_HOT, "live": config.POLL_LIVE,
                "game": config.POLL_GAME, "cold": config.POLL_COLD,
                "futures": config.POLL_FUTURES}
    assert all(v > 0 for v in cadences.values())
    assert cadences["cold"] > cadences["game"] > cadences["hot"]
    assert cadences["live"] <= cadences["hot"]
    # The loop must wake more often than its fastest tier or the cadence is a
    # fiction.
    assert config.LOOP_TICK <= min(cadences.values())
