"""Backfill, liquidity and cross-venue tests. Run: pytest -q

The theme: a number that is missing must not be able to masquerade as a number
that is good. An empty book quotes 0/1 and has a midpoint; a venue with no
volume field still returns rows; a backfilled candle looks exactly like a
logged tick unless something marks it. Each of those is a way to end up
believing in a price nobody was ever quoting.
"""
import time

import pytest

import config
import store
from jobs import backfill_history as bf
from jobs import cross_venue, liquidity_map


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setattr(config, "RAW_DIR", str(tmp_path / "raw"))
    store.init_db()
    store.reset_quote_state()
    yield tmp_path
    store.reset_quote_state()


META = {"event_id": "2026_01_NE_SEA", "market_type": "prop",
        "subject": "X", "line": 3.5}


# --- Kalshi candles ----------------------------------------------------------

def test_kalshi_candles_are_dollars_not_cents():
    """A contract settles at $1, so the dollar price IS the probability. This
    is the brief 001 bug and it reads as 'nobody trades this' if reintroduced."""
    rows = bf.kalshi_rows("T", META, [{
        "end_period_ts": 1788994800,
        "yes_bid": {"close_dollars": "0.4400"},
        "yes_ask": {"close_dollars": "0.4600"},
        "price": {"close_dollars": "0.4500"},
        "volume_fp": "489576.79", "open_interest_fp": "2950583.47"}])
    r = rows[0]
    assert r["best_bid"] == 0.44 and r["best_ask"] == 0.46
    assert r["mid"] == pytest.approx(0.45)
    assert 0.0 < r["mid"] < 1.0
    assert r["volume"] == pytest.approx(489576.79)
    assert r["open_interest"] == pytest.approx(2950583.47)
    assert r["source"] == "backfill:kalshi_candles"


def test_a_candle_with_no_prints_still_carries_a_quote():
    """`price` is {} when nothing traded in the period. Bid and ask still
    exist, so the row is real - discarding it would punch a hole in exactly the
    quiet stretches a liquidity map is trying to measure."""
    rows = bf.kalshi_rows("T", META, [{
        "end_period_ts": 1788829200, "price": {},
        "yes_bid": {"close_dollars": "0.0100"},
        "yes_ask": {"close_dollars": "0.9900"},
        "volume_fp": "0.00", "open_interest_fp": "0.00"}])
    assert len(rows) == 1
    assert rows[0]["last"] is None          # no trade, not a zero
    assert rows[0]["best_bid"] == 0.01
    assert rows[0]["volume"] == 0.0


def test_candles_without_a_timestamp_are_dropped():
    assert bf.kalshi_rows("T", META, [{"yes_bid": {"close_dollars": "0.5"}}]) == []


# --- Polymarket history, and what it does not have ---------------------------

def test_polymarket_history_has_no_book_and_says_so():
    """prices-history returns {t, p} only. Recording a fabricated bid/ask, or a
    zero volume, would turn 'this venue does not tell us' into 'this venue told
    us zero' - and the liquidity map would believe it."""
    rows = bf.poly_rows("0xtok", META, [{"t": 1786320018, "p": 0.62}])
    r = rows[0]
    assert r["mid"] == 0.62 and r["last"] == 0.62
    assert r["best_bid"] is None and r["best_ask"] is None
    assert r["volume"] is None and r["open_interest"] is None
    assert r["source"] == "backfill:poly_history"


# --- the source marker and the dedupe bypass ---------------------------------

def test_backfilled_rows_are_distinguishable_from_logged_ones(env):
    now = time.time()
    store.write_quotes([{"ts": now, "sport": "nfl", "venue": "kalshi",
                         "market_id": "M", "best_bid": 0.4, "best_ask": 0.42,
                         "mid": 0.41}])
    store.write_quotes(bf.kalshi_rows("M", META, [{
        "end_period_ts": int(now) - 3600, "price": {},
        "yes_bid": {"close_dollars": "0.4000"},
        "yes_ask": {"close_dollars": "0.4200"},
        "volume_fp": "1", "open_interest_fp": "2"}]), dedupe=False)
    with store.db() as c:
        got = dict(c.execute("SELECT source, COUNT(*) FROM quotes "
                             "GROUP BY source").fetchall())
    assert got["live"] == 1
    assert got["backfill:kalshi_candles"] == 1


def test_dedupe_off_keeps_every_candle(env):
    """A history endpoint already returns one observation per period. The
    change-plus-heartbeat filter would drop the unchanged ones and leave gaps
    where the market was simply quiet."""
    now = int(time.time())
    candles = [{"end_period_ts": now - i * 3600, "price": {},
                "yes_bid": {"close_dollars": "0.4000"},
                "yes_ask": {"close_dollars": "0.4200"},
                "volume_fp": "0", "open_interest_fp": "0"} for i in range(10)]
    rows = bf.kalshi_rows("M", META, candles)

    assert store.write_quotes(rows, dedupe=False) == 10
    store.reset_quote_state()
    assert store.write_quotes(rows, dedupe=True) == 1     # what dedupe would do


# --- liquidity bucketing -----------------------------------------------------

def test_deep_needs_both_volume_and_a_tight_spread():
    """A market can trade heavily in a burst and be quoted wide the rest of the
    week; a tight quote on no volume is a maker talking to himself."""
    assert liquidity_map.bucket_for(50_000, 0.01) == "deep"
    assert liquidity_map.bucket_for(50_000, 0.09) == "thin"
    assert liquidity_map.bucket_for(10, 0.01) == "thin"


def test_one_axis_never_promotes_to_deep():
    """Polymarket has a spread and no volume. Half the evidence must not buy a
    full-confidence label."""
    assert liquidity_map.bucket_for(None, 0.005) == "medium"
    assert liquidity_map.bucket_for(None, 0.30) == "thin"
    assert liquidity_map.bucket_for(50_000, None) == "medium"


def test_no_evidence_is_unknown_not_thin():
    """Backfilled Polymarket rows have neither. `unknown` is a real answer and
    must not be read as 'fine' - or as 'bad', which would be equally invented."""
    assert liquidity_map.bucket_for(None, None) == "unknown"


def test_period_is_a_utc_week():
    assert liquidity_map.period_of(1788994800).startswith("2026-W")


# --- cross-venue classification ----------------------------------------------

def test_an_empty_book_is_named_before_it_is_called_a_mapping_error():
    """THE ordering bug. An empty CLOB book quotes 0/1, so its mid is 0.500 and
    it disagrees with everything by up to half a dollar while saying nothing.
    Checking 'is the gap big' before 'does the spread explain it' labelled 27
    of these as mapping errors and would have sent someone chasing a bug that
    does not exist."""
    assert cross_venue.classify(0.335, 0.010, 1.000) == "no book"
    assert cross_venue.classify(-0.315, 0.010, 1.000) == "no book"


def test_a_gap_inside_a_wide_spread_is_not_a_disagreement():
    assert cross_venue.classify(0.305, 0.04, 0.63) == "inside the spread"
    assert cross_venue.classify(0.10, 0.02, 0.30) == "inside the spread"


def test_a_large_gap_between_two_tight_books_is_suspect():
    """Only when no spread explains it does a gap implicate the mapping."""
    assert cross_venue.classify(0.40, 0.01, 0.01) == "suspect mapping"
    assert cross_venue.classify(0.03, 0.01, 0.01) == "real disagreement"


def test_a_wide_book_beats_a_real_disagreement_verdict():
    assert cross_venue.classify(0.09, 0.01, 0.06) == "wide book / stale"
