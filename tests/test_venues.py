"""Venue adapter tests. Run: pytest -q

These target the failures that return HTTP 200 and look like a quiet market:
a filter that matches the wrong universe, a batched request the API silently
answers with nothing, a price off by a factor of 100, and a dedupe rule that
makes an outage look like a flat price. Every one of them was live in this
package on 2026-09-09.
"""
import asyncio
import time

import pytest

import config
import store
from venues.base import mid_from
from venues.kalshi import KalshiClient, _top, is_football_series
from venues.polymarket import PolymarketClient


# --- the NFL filter ---------------------------------------------------------

def test_filter_rejects_inflation_and_accepts_pro_football():
    """The two cases the brief names. "inflation" contains "nfl", so a naive
    substring filter matches every CPI series on the exchange."""
    assert is_football_series("KXACPI", "US annual inflation", "Economics") is False
    assert is_football_series("KXNFLGAME-26SEP09SEA", "Pro Football Game Winner",
                              "Sports") is True


def test_filter_guards_on_category_and_ticker_independently():
    """Either guard alone is wrong, so neither may carry the decision alone."""
    # category alone would admit this; the ticker/title match must also fail it
    assert is_football_series("KXACPI", "US annual inflation", "Sports") is False
    # the ticker match alone would admit these; the category must fail them
    assert is_football_series("KXNFLVIEW", "NFL ratings", "Entertainment") is False
    assert is_football_series("KXTOPSEASONNFLX", "Top media by platform?",
                              "Entertainment") is False


def test_filter_rejects_netflix_and_college_football():
    """Live false positives from the real catalogue: NFLX contains "nfl", and
    KXNCAAFCONFLEAVE contains it across a word boundary (CO-NFL-EAVE)."""
    assert is_football_series("KXTOPSEASONNFLX", "Top media", "Sports") is False
    assert is_football_series("KXNCAAFCONFLEAVE", "College Football Conference Leave",
                              "Sports") is False
    assert is_football_series("KXNCAAFTOTALTD", "College Football Total Touchdowns",
                              "Sports") is False


def test_filter_accepts_the_trademark_avoidant_titles():
    """Kalshi says "Pro football", never "NFL", on most titles."""
    assert is_football_series("KXNFLWINS-MIA", "Pro football wins Miami", "Sports")
    assert is_football_series("KXNFL2Q", "Pro Football 2nd Quarter Winner", "Sports")
    assert is_football_series("KXLEADERNFLRUSHTDS", "NFL leader rushing TDs", "Sports")
    assert is_football_series("KXNFLPREPACKSGPSPREAD", "MVE NFL Pre Pack", "Sports")


# --- prices are dollars, not cents ------------------------------------------

def test_ladder_price_is_already_a_probability():
    """A Kalshi contract settles at $1, so the dollar price IS the probability.
    Dividing by 100 - as a cents-based reader would - puts every price two
    orders of magnitude low, which reads as a market nobody trades."""
    assert _top([["0.1800", "571.00"], ["0.1500", "74533.00"]]) == 0.18


def test_ladder_compares_numerically_not_lexicographically():
    """max() over the raw strings picks "0.9000" over "0.1000" by luck and
    "0.0900" over "0.1000" by mistake."""
    assert _top([["0.1000", "1"], ["0.0900", "1"]]) == 0.10


def test_ladder_tolerates_junk_levels():
    assert _top([]) is None
    assert _top(None) is None
    assert _top([[None, "1"], ["0.4200", "1"]]) == 0.42


def test_yes_ask_is_implied_by_the_best_no_bid():
    """One-sided ladders: a resting bid to buy NO at 0.81 is an offer to sell
    YES at 0.19. Reconciled against the live yes_ask_dollars on 2026-09-09."""
    yes_bid = _top([["0.1800", "22840.94"]])
    no_bid = _top([["0.8100", "1000.00"]])
    ask = round(1.0 - no_bid, 4)
    assert (yes_bid, ask) == (0.18, 0.81 and 0.19)
    assert mid_from(yes_bid, ask) == pytest.approx(0.185)


# --- the batched orderbook request ------------------------------------------

def run(coro):
    """Drive one coroutine to completion.

    Deliberately not pytest-asyncio: six tests do not justify a plugin the
    logger itself never imports, and the suite has to run identically under the
    venv and a bare interpreter.
    """
    return asyncio.run(coro)


class _RecordingClient:
    """Captures the params of every get_json call without any network."""

    def __init__(self, payload=None):
        self.calls = []
        self.payload = payload or {"orderbooks": []}

    async def get_json(self, url, params=None, archive_as=None, **kw):
        self.calls.append((url, params))
        return self.payload


def test_orderbooks_uses_repeated_tickers_params_not_comma_joined():
    """`tickers=a,b,c` returns HTTP 200 with ONE EMPTY BOOK whose ticker is the
    joined string - the API does not split on commas. Repeated params return
    every book. This is the difference between a working logger and weeks of
    null mids that look like no liquidity."""
    client = KalshiClient.__new__(KalshiClient)
    rec = _RecordingClient()
    client.get_json = rec.get_json

    markets = [{"market_id": f"KXNFLGAME-T{i}"} for i in range(3)]
    run(KalshiClient.fetch_quotes(client, markets))

    (url, params), = rec.calls
    assert url.endswith("/markets/orderbooks")
    assert params == [("tickers", "KXNFLGAME-T0"),
                      ("tickers", "KXNFLGAME-T1"),
                      ("tickers", "KXNFLGAME-T2")]
    # and specifically NOT the comma form
    assert not isinstance(params, dict)
    assert all("," not in v for _, v in params)


def test_orderbooks_batches_at_the_api_limit():
    """150 tickers in one call returns 400, 200 returns 414. 100 is the ceiling."""
    client = KalshiClient.__new__(KalshiClient)
    rec = _RecordingClient()
    client.get_json = rec.get_json

    markets = [{"market_id": f"T{i}"} for i in range(250)]
    run(KalshiClient.fetch_quotes(client, markets))

    assert [len(p) for _, p in rec.calls] == [100, 100, 50]
    assert config.KALSHI_ORDERBOOK_BATCH == 100


def test_kalshi_quote_row_carries_a_usable_mid():
    payload = {"orderbooks": [{"ticker": "KXNFLGAME-X",
                               "orderbook_fp": {"yes_dollars": [["0.1800", "1"]],
                                                "no_dollars": [["0.8100", "1"]]}}]}
    client = KalshiClient.__new__(KalshiClient)
    client.get_json = _RecordingClient(payload).get_json

    rows = run(KalshiClient.fetch_quotes(
        client, [{"market_id": "KXNFLGAME-X", "market_type": "moneyline",
                  "event_id": "E", "subject": "NYG", "line": None}]))

    assert len(rows) == 1
    assert rows[0]["best_bid"] == 0.18
    assert rows[0]["best_ask"] == 0.19
    assert rows[0]["mid"] == pytest.approx(0.185)
    assert 0.0 < rows[0]["mid"] < 1.0        # a probability, not cents


# --- Polymarket discovery ---------------------------------------------------

class _PagingClient:
    """Serves pages of an events payload, and can 422 at a given offset the way
    gamma does past its pagination ceiling."""

    def __init__(self, pages, fail_at=None):
        self.pages, self.fail_at, self.calls = pages, fail_at, 0

    async def get_json(self, url, params=None, archive_as=None, soft_status=(), **kw):
        self.calls += 1
        offset = params["offset"]
        if self.fail_at is not None and offset >= self.fail_at:
            assert 422 in soft_status, "discovery must treat gamma's 422 as soft"
            return None
        return self.pages.get(offset, [])


def _event(slug, markets):
    return {"slug": slug, "ticker": slug, "markets": markets}


def _market(token, liquidity=500.0, bid=0.40, ask=0.42):
    return {"clobTokenIds": f'["{token}", "other"]', "active": True, "closed": False,
            "enableOrderBook": True, "acceptingOrders": True,
            "liquidityNum": liquidity, "bestBid": bid, "bestAsk": ask,
            "question": "Team wins?", "groupItemTitle": "Team",
            "volumeNum": 1000.0, "lastTradePrice": 0.41,
            "startDate": "2026-09-01T00:00:00Z", "endDate": "2026-09-30T00:00:00Z"}


def test_a_422_truncates_discovery_instead_of_killing_it():
    """Blind pagination 422s at offset 2100. Whatever was already collected is
    real data and must survive; the alternative is a venue that discovers
    nothing the day gamma moves its ceiling."""
    pages = {0: [_event("pro-football-a", [_market("t1")])],
             1: [_event("pro-football-b", [_market("t2")])]}
    client = PolymarketClient.__new__(PolymarketClient)
    client._snapshot, client._snapshot_ts = [], 0.0
    client.get_json = _PagingClient(pages, fail_at=1).get_json

    markets = run(PolymarketClient.list_markets(client))

    assert [m["market_id"] for m in markets] == ["t1"]


def test_discovery_pages_to_the_end_and_applies_the_liquidity_floor():
    pages = {0: [_event("pro-football-a", [_market("keep", liquidity=500.0),
                                           _market("dust", liquidity=1.0)])],
             1: []}
    client = PolymarketClient.__new__(PolymarketClient)
    client._snapshot, client._snapshot_ts = [], 0.0
    client.get_json = _PagingClient(pages).get_json

    markets = run(PolymarketClient.list_markets(client))

    assert [m["market_id"] for m in markets] == ["keep"]


def test_polymarket_quotes_batch_through_the_clob_not_the_events_payload():
    """The events payload is 6.9MB per page for three numbers per market; the
    CLOB reports the same top-of-book in 21KB per 250. Refreshing quotes off
    gamma would archive ~26GB/day, so the quote path must not touch it."""
    pages = {0: [_event("pro-football-a", [_market("t1"), _market("t2")])], 1: []}
    client = PolymarketClient.__new__(PolymarketClient)
    client._snapshot, client._snapshot_ts = [], 0.0
    paging = _PagingClient(pages)
    client.get_json = paging.get_json
    posts = []

    async def post_json(url, body, archive_as=None, **kw):
        posts.append((url, body))
        return {"t1": {"BUY": "0.40", "SELL": "0.42"},
                "t2": {"BUY": "0.10", "SELL": "0.11"}}

    client.post_json = post_json

    markets = run(PolymarketClient.list_markets(client))
    gamma_calls = paging.calls
    rows = run(PolymarketClient.fetch_quotes(client, markets))

    assert paging.calls == gamma_calls          # gamma untouched by the refresh
    url, body = posts[0]
    assert url.endswith("/prices")
    # two entries per market - a BUY (best bid) and a SELL (best offer)
    assert body == [{"token_id": "t1", "side": "BUY"},
                    {"token_id": "t1", "side": "SELL"},
                    {"token_id": "t2", "side": "BUY"},
                    {"token_id": "t2", "side": "SELL"}]
    assert rows[0]["best_bid"] == 0.40 and rows[0]["best_ask"] == 0.42
    assert rows[0]["mid"] == pytest.approx(0.41)


def test_polymarket_price_requests_stay_under_the_payload_ceiling():
    """600 entries returns "Payload exceeds the limit"; 500 is the ceiling and
    every market costs two entries."""
    client = PolymarketClient.__new__(PolymarketClient)
    posts = []

    async def post_json(url, body, archive_as=None, **kw):
        posts.append(body)
        return {}

    client.post_json = post_json
    run(PolymarketClient.fetch_quotes(
        client, [{"market_id": f"t{i}"} for i in range(600)]))

    assert [len(b) for b in posts] == [500, 500, 200]
    assert all(len(b) <= 500 for b in posts)
    assert config.POLY_PRICE_BATCH * 2 <= 500


def test_a_clob_failure_falls_back_to_the_last_gamma_prices():
    """A hole in the series is worse than a quote that is one discovery old and
    honestly timestamped."""
    pages = {0: [_event("pro-football-a", [_market("t1", bid=0.33, ask=0.35)])], 1: []}
    client = PolymarketClient.__new__(PolymarketClient)
    client._snapshot, client._snapshot_ts = [], 0.0
    client.get_json = _PagingClient(pages).get_json

    async def post_json(url, body, archive_as=None, **kw):
        raise RuntimeError("clob down")

    client.post_json = post_json

    markets = run(PolymarketClient.list_markets(client))
    rows = run(PolymarketClient.fetch_quotes(client, markets))

    assert rows[0]["best_bid"] == 0.33
    assert rows[0]["raw_ref"] == "polymarket/events"


# --- write-on-change and the heartbeat --------------------------------------

def _quote(ts, bid=0.40, ask=0.42, venue="kalshi", mid=None):
    return {"ts": ts, "sport": "nfl", "venue": venue, "market_id": "M1",
            "best_bid": bid, "best_ask": ask,
            "mid": mid if mid is not None else mid_from(bid, ask)}


@pytest.fixture(autouse=True)
def _isolated_store(tmp_path, monkeypatch):
    """Never let a test touch the live log - the logger is a long-lived process
    writing that file, and these tests append rows that are not observations."""
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setattr(config, "RAW_DIR", str(tmp_path / "raw"))
    store.init_db()
    store.reset_quote_state()
    yield
    store.reset_quote_state()


def test_an_unchanged_price_is_written_once_then_suppressed():
    now = time.time()
    assert store.should_write(_quote(now)) is True
    store.write_quotes([_quote(now)])
    assert store.should_write(_quote(now + 30)) is False


def test_the_heartbeat_fires_on_an_unchanged_market():
    """The point of the heartbeat: without it, "price held steady" and "logger
    was down" are the same absence in the quotes table, and a missing T-5min
    close is indistinguishable from a quiet one."""
    now = time.time()
    store.write_quotes([_quote(now)])

    assert store.should_write(_quote(now + config.QUOTE_HEARTBEAT_SEC - 1)) is False
    beat = _quote(now + config.QUOTE_HEARTBEAT_SEC + 1)
    assert store.should_write(beat) is True

    # and the heartbeat restarts the clock, so it fires on a period, not a burst
    store.write_quotes([beat])
    assert store.should_write(_quote(beat["ts"] + 30)) is False


def test_a_price_move_is_always_written():
    now = time.time()
    store.write_quotes([_quote(now)])
    assert store.should_write(_quote(now + 1, bid=0.41)) is True
    assert store.should_write(_quote(now + 1, ask=0.43)) is True


def test_markets_are_deduped_independently():
    now = time.time()
    store.write_quotes([_quote(now)])
    other = dict(_quote(now + 1), market_id="M2")
    assert store.should_write(other) is True


def test_the_odds_api_path_is_never_deduped():
    """Its rows are scheduled snapshots, not polls - each one is a deliberate
    capture and the T-10min one IS the CLV measurement."""
    now = time.time()
    row = _quote(now, venue="oddsapi:pinnacle", bid=None, ask=None, mid=0.52)
    store.write_quotes([row])
    assert store.should_write(_quote(now + 1, venue="oddsapi:pinnacle",
                                     bid=None, ask=None, mid=0.52)) is True
