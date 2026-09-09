"""Healthcheck ping tests. Run: pytest -q

The ping exists to cover what a self-report cannot: a killed process, a wedged
loop, a dead box. That only works if the ping STOPS when capture stops - a
logger that keeps reassuring an external monitor while its own dead-man switch
is tripped is worse than no monitor at all, because it converts a loud failure
into a silent one.

So the tests that matter here are the negative ones.
"""
import asyncio
import time

import pytest

import config
import run_logger
import store


def run(coro):
    return asyncio.run(coro)


class FakeHTTP:
    """Records healthcheck pings. `status` drives the response code."""

    def __init__(self, status=200, boom=None):
        self.calls = []
        self.status = status
        self.boom = boom

    async def get(self, url, timeout=None):
        if self.boom:
            raise self.boom
        self.calls.append(url)

        class R:
            status_code = self.status
        return R()


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setattr(config, "RAW_DIR", str(tmp_path / "raw"))
    monkeypatch.setattr(config, "HEALTHCHECK_URL", "https://hc.example/ping/abc")
    monkeypatch.setattr(config, "DEADMAN_MIN", 20)
    store.init_db()
    yield tmp_path


def poll(venue, age_min, ok=True):
    """Write a poll_log row aged `age_min` minutes into the past."""
    store.log_poll(venue, "quotes:game", 10, 10, ok, None, 0.1)
    with store.db() as c:
        c.execute("UPDATE poll_log SET ts = ? WHERE rowid = (SELECT MAX(rowid) "
                  "FROM poll_log)", (time.time() - age_min * 60,))


# --- the negative cases: a stall must stop the pings -------------------------

def test_a_stall_stops_the_pings(env):
    """The acceptance case. Fresh polls ping; the same logger 40 minutes later
    with nothing new does not."""
    http = FakeHTTP()
    poll("kalshi", 0.5)
    poll("polymarket", 0.5)

    assert run(run_logger.watchdog_tick(http, {"kalshi", "polymarket"})) is True
    assert len(http.calls) == 1

    # simulate the stall: every successful poll recedes past DEADMAN_MIN
    with store.db() as c:
        c.execute("UPDATE poll_log SET ts = ts - ?", (40 * 60,))

    assert run(run_logger.watchdog_tick(http, {"kalshi", "polymarket"})) is False
    assert len(http.calls) == 1                      # no second ping
    assert store.health("liveness")[1] == 0


def test_one_silent_venue_also_suppresses_the_ping(env):
    """Partial capture is not capture. If polymarket has gone quiet while
    kalshi keeps the global timestamp fresh, the ping must still stop."""
    http = FakeHTTP()
    poll("kalshi", 0.5)
    poll("polymarket", 90)

    assert run(run_logger.watchdog_tick(http, {"kalshi", "polymarket"})) is False
    assert http.calls == []
    assert "STALE: polymarket" in store.health("liveness")[2]


def test_an_empty_poll_log_does_not_ping(env):
    """A logger that has never captured anything must not report healthy."""
    http = FakeHTTP()
    assert run(run_logger.watchdog_tick(http, {"kalshi"})) is False
    assert http.calls == []


def test_failed_polls_do_not_count_as_liveness(env):
    """poll_log rows with ok=0 are evidence of trying, not of capturing."""
    http = FakeHTTP()
    poll("kalshi", 0.5, ok=False)
    assert run(run_logger.watchdog_tick(http, {"kalshi"})) is False
    assert http.calls == []


# --- the positive case, and its guards ---------------------------------------

def test_a_healthy_cycle_pings(env):
    http = FakeHTTP()
    poll("kalshi", 0.5)
    poll("polymarket", 1.0)
    poll("oddsapi", 2.0)

    assert run(run_logger.watchdog_tick(http, {"kalshi", "polymarket", "oddsapi"})) is True
    assert http.calls == ["https://hc.example/ping/abc"]
    assert store.health("liveness")[1] == 1
    assert store.health("healthcheck")[1] == 1


def test_a_disabled_venue_does_not_suppress_pings_forever(env):
    """poll_log keeps history forever. A venue switched off last week would
    otherwise read as permanently stale and silence the monitor for good."""
    http = FakeHTTP()
    poll("kalshi", 0.5)
    poll("polymarket", 9999)          # disabled a week ago

    assert run(run_logger.watchdog_tick(http, {"kalshi"})) is True
    assert len(http.calls) == 1


def test_an_unset_url_is_inert_not_an_error(env, monkeypatch):
    monkeypatch.setattr(config, "HEALTHCHECK_URL", None)
    http = FakeHTTP()
    poll("kalshi", 0.5)

    assert run(run_logger.watchdog_tick(http, {"kalshi"})) is False
    assert http.calls == []
    assert store.health("liveness")[1] == 1       # still healthy, just unmonitored


def test_a_ping_failure_never_breaks_the_logger(env):
    """Monitoring being down is not capture being down."""
    http = FakeHTTP(boom=RuntimeError("dns is on fire"))
    poll("kalshi", 0.5)

    assert run(run_logger.watchdog_tick(http, {"kalshi"})) is False
    assert store.health("liveness")[1] == 1       # capture still reported healthy
    assert store.health("healthcheck")[1] == 0    # monitoring reported unhealthy


def test_a_rejected_ping_is_recorded_not_swallowed(env):
    http = FakeHTTP(status=404)
    poll("kalshi", 0.5)

    assert run(run_logger.watchdog_tick(http, {"kalshi"})) is False
    assert store.health("healthcheck")[1] == 0
    assert "404" in store.health("healthcheck")[2]
