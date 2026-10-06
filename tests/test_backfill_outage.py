"""The outage backfill writes RECONSTRUCTED rows, and only those (unit a-67).

A candle is not a tick. What is pinned: the rows carry their own source, the
prune cannot touch them, a re-run does not double them, nothing outside the
window or of another source is written or deleted, and the market set is the
hot and live tiers of the window - shown excluding a market on each side.
"""
import time

import pytest

import config
import store
from jobs import backfill_outage as bo
from jobs import prune_quotes

T0 = 1_791_000_000.0          # window start
T1 = T0 + 30 * 60             # window end


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setattr(config, "STORAGE_DIR", str(tmp_path))
    monkeypatch.setattr(config, "RAW_DIR", str(tmp_path / "raw"))
    store.init_db()
    store.reset_disk_cache()
    store.reset_quote_state()
    store.upsert_markets([
        {"venue": "kalshi", "market_id": f"KXNFLREC-{x}", "event_id": "E",
         "sport": "nfl", "market_type": "prop", "subject": "p", "line": 4.5,
         "title": "t"} for x in ("HOT", "LIVE", "NEXTWEEK", "OVER", "NOKICK")])
    yield tmp_path
    store.reset_disk_cache()
    store.reset_quote_state()


KICKOFFS = {
    ("kalshi", "KXNFLREC-HOT"): T1 + 60 * 60,                  # kicks an hour after
    ("kalshi", "KXNFLREC-LIVE"): T0 - 60 * 60,                 # an hour into its game
    ("kalshi", "KXNFLREC-NEXTWEEK"): T1 + 7 * 86400,
    ("kalshi", "KXNFLREC-OVER"): T0 - config.LIVE_WINDOW_MIN * 60 - 60,
    ("polymarket", "KXNFLREC-HOT"): T1 + 60,                   # another venue's id
}


def candle(end_ts, bid, ask, vol="3.00"):
    return {"end_period_ts": int(end_ts), "yes_bid": {"close_dollars": str(bid)},
            "yes_ask": {"close_dollars": str(ask)}, "price": {},
            "volume_fp": vol, "open_interest_fp": "10.00"}


class FakeResponse:
    status_code = 200

    def __init__(self, body):
        self._body = body

    def json(self):
        return self._body


class FakeClient:
    """Every market: one candle before the gap, three inside, one after."""

    def __init__(self):
        self.calls = []

    def get(self, url, params=None):
        self.calls.append((url, dict(params)))
        return FakeResponse({"candlesticks": [
            candle(T0 - 60, 0.40, 0.44), candle(T0 + 60, 0.41, 0.45),
            candle(T0 + 120, 0.42, 0.46), candle(T1 - 60, 0.43, 0.47),
            candle(T1 + 60, 0.50, 0.54)]})


class NoWait:
    def wait(self): pass
    def ok(self): pass
    def throttled(self): pass


def run(**kw):
    client = FakeClient()
    s = bo.run(T0, T1, client=client, pacer=NoWait(), kickoffs=KICKOFFS, **kw)
    return s, client


def rows(where="1=1", args=()):
    with store.db() as c:
        return c.execute(f"SELECT market_id, ts, source, best_bid, volume, ingest_ts "
                         f"FROM quotes WHERE {where} ORDER BY market_id, ts", args).fetchall()


def test_the_market_set_is_the_hot_and_live_tiers_of_the_window(env):
    got = [t for t, _ in bo.targets(T0, T1, KICKOFFS)]
    assert got == ["KXNFLREC-HOT", "KXNFLREC-LIVE"]


def test_only_candles_inside_the_gap_land_and_they_say_what_they_are(env):
    s, client = run()
    assert s["markets"] == 2 and s["with_rows"] == 2 and s["rows"] == 6
    got = rows()
    assert len(got) == 6
    assert {r[2] for r in got} == {"reconstructed:kalshi_candles_1m"}
    assert all(T0 < r[1] < T1 for r in got)
    assert all(r[5] is not None and r[5] > time.time() - 60 for r in got)   # ingest_ts is NOW
    assert len(client.calls) == 2
    assert all(p["period_interval"] == 1 for _u, p in client.calls)


def test_a_rerun_replaces_its_own_rows_and_touches_no_others(env):
    store.write_quotes([
        {"ts": T0 + 90, "sport": "nfl", "venue": "kalshi", "market_id": "KXNFLREC-HOT",
         "side": "yes", "best_bid": 0.1, "best_ask": 0.2, "mid": 0.15, "source": src}
        for src in ("live", "backfill:kalshi_candles")], dedupe=False)
    run()
    run()
    assert len(rows("source = ?", (bo.SOURCE,))) == 6
    assert len(rows("source = 'live'")) == 1
    assert len(rows("source = 'backfill:kalshi_candles'")) == 1


def test_the_prune_cannot_touch_reconstructed_rows(env):
    """Their ts is the outage's and can be made as old as you like; what keeps
    them is the source. A LIVE row of the same age goes, so the prune is shown
    deleting something in this very store."""
    run()
    old = time.time() - 60 * 86400
    with store.db() as c:
        c.execute("UPDATE quotes SET ts = ?, ingest_ts = ?", (old, old))
        c.execute("INSERT INTO quotes (ts, venue, market_id, source, ingest_ts) "
                  "VALUES (?, 'kalshi', 'KXNFLREC-HOT', 'live', ?)", (old, old))
    s = prune_quotes.run(pause_s=0)
    assert s["deleted"] == 1
    assert len(rows("source = ?", (bo.SOURCE,))) == 6
    assert bo.SOURCE not in config.QUOTES_PRUNE_SOURCES


def test_dry_run_fetches_and_writes_nothing(env):
    s, client = run(dry_run=True)
    assert s["markets"] == 2 and s["rows"] == 0
    assert client.calls == [] and rows() == []


def test_the_window_must_run_forwards(env):
    with pytest.raises(ValueError):
        bo.run(T1, T0, kickoffs=KICKOFFS)
    assert bo.parse_utc("2026-10-04T14:50:22Z") == 1791125422.0
