"""Depth and executable-pricing tests. Run: pytest -q

The failure mode this guards is not a crash. It is a VWAP computed off the
wrong end of the book, which produces a number that looks entirely reasonable
and is the cost of trading against the worst prices available instead of the
best. Both venues order their ladders in ways that make this easy to get wrong,
and in opposite directions.
"""
import pytest

from venues.depth import (Depth, kalshi_buy_ladders, ladder_depth,
                          polymarket_buy_ladders)


# --- the Nacua case ----------------------------------------------------------

def test_the_touch_is_not_the_price():
    """Observed live: best ask 4c for ONE contract, then a wall. A backtest
    reading the touch believes it filled at 4c; 1000 contracts cost 33c."""
    book = [(0.04, 1), (0.05, 200), (0.06, 500), (0.98, 5000)]
    d = ladder_depth(book)

    assert d.touch_price == 0.04
    assert d.touch_size == 1
    assert d.vwap[100] == pytest.approx((1 * .04 + 99 * .05) / 100, abs=1e-6)
    # 1 + 200 + 500 = 701 cheap, then 299 at 0.98
    expected = (1 * .04 + 200 * .05 + 500 * .06 + 299 * .98) / 1000
    assert d.vwap[1000] == pytest.approx(expected, abs=1e-6)
    assert d.vwap[1000] > d.touch_price * 8
    assert d.slippage(1000) > 7.0


def test_size_at_touch_and_near_touch_are_distinguished():
    book = [(0.04, 1), (0.05, 200), (0.06, 500), (0.98, 5000)]
    d = ladder_depth(book)
    assert d.touch_size == 1
    assert d.size_within_1c == 201        # 0.04 and 0.05
    assert d.size_within_5c == 701        # through 0.06 (0.09 cutoff)
    assert d.total_size == 5701


def test_an_unfillable_stake_has_no_price_not_a_partial_one():
    """Reporting the cost of the contracts you COULD get, as though you got
    them all, is how a thin book starts looking tradeable."""
    d = ladder_depth([(0.10, 50)])
    assert d.vwap[100] is None
    assert d.filled[100] == 50
    assert d.slippage(100) is None


def test_an_empty_book_prices_nothing():
    d = ladder_depth([])
    assert d.touch_price is None
    assert all(v is None for v in d.vwap.values())
    assert d.n_levels == 0


def test_zero_size_levels_are_ignored():
    d = ladder_depth([(0.03, 0), (0.05, 100)])
    assert d.touch_price == 0.05


# --- ladder orientation, per venue -------------------------------------------

def test_kalshi_yes_ask_is_derived_from_the_no_bid():
    """Kalshi publishes two BID ladders and no asks. To buy YES you lift the
    people bidding for NO: a bid to buy NO at 0.61 is an offer to sell YES at
    0.39. Getting this backwards prices every trade against the wrong side."""
    ob = {"yes_dollars": [["0.30", "100"], ["0.38", "500"]],
          "no_dollars": [["0.55", "200"], ["0.61", "900"]]}
    buy_yes, buy_no = kalshi_buy_ladders(ob)

    assert buy_yes[0] == (pytest.approx(0.39), 900)     # 1 - 0.61
    assert buy_yes[1] == (pytest.approx(0.45), 200)     # 1 - 0.55
    assert buy_no[0] == (pytest.approx(0.62), 500)      # 1 - 0.38
    assert buy_no[1] == (pytest.approx(0.70), 100)


def test_kalshi_ladders_come_out_best_first():
    """Kalshi returns both ladders ASCENDING, so the best bid is the LAST
    entry. After inversion the buy ladder must be cheapest-first."""
    ob = {"yes_dollars": [["0.01", "10"], ["0.20", "20"], ["0.38", "30"]],
          "no_dollars": [["0.01", "40"], ["0.30", "50"], ["0.61", "60"]]}
    buy_yes, _ = kalshi_buy_ladders(ob)
    prices = [p for p, _ in buy_yes]
    assert prices == sorted(prices)
    assert prices[0] == pytest.approx(0.39)


def test_polymarket_bids_ascend_but_asks_descend():
    """The two sides use opposite conventions, verified live. Best bid is the
    max, best ask is the min."""
    book = {"bids": [{"price": "0.01", "size": "10"},
                     {"price": "0.58", "size": "40"}],
            "asks": [{"price": "0.99", "size": "20"},
                     {"price": "0.59", "size": "30"}]}
    buy_yes, buy_no = polymarket_buy_ladders(book)

    assert buy_yes[0] == (pytest.approx(0.59), 30)     # min of asks
    assert buy_no[0] == (pytest.approx(0.42), 40)      # 1 - max bid (0.58)


def test_the_two_venues_agree_on_an_identical_book():
    """A Kalshi book and a Polymarket book describing the same market must
    produce the same executable price - otherwise cross-venue comparison is
    comparing conventions rather than prices."""
    kalshi = kalshi_buy_ladders(
        {"yes_dollars": [["0.40", "500"]], "no_dollars": [["0.58", "300"]]})[0]
    poly = polymarket_buy_ladders(
        {"bids": [{"price": "0.40", "size": "500"}],
         "asks": [{"price": "0.42", "size": "300"}]})[0]
    assert ladder_depth(kalshi).touch_price == pytest.approx(
        ladder_depth(poly).touch_price)


# --- the ladder is monotone --------------------------------------------------

def test_vwap_never_improves_with_size():
    """Filling more can only ever cost the same or more. A ladder that
    improves with size means the book was sorted the wrong way."""
    book = [(0.10, 100), (0.12, 400), (0.20, 1000), (0.50, 10000)]
    d = ladder_depth(book)
    prices = [d.vwap[s] for s in (100, 500, 1000, 5000)]
    assert all(a <= b for a, b in zip(prices, prices[1:]))
    assert prices[0] == pytest.approx(0.10)


def test_a_deep_flat_book_barely_slips():
    """The control case: when size is genuinely there, executable price is the
    touch and the whole exercise costs nothing."""
    d = ladder_depth([(0.45, 50_000)])
    assert d.vwap[5000] == pytest.approx(0.45)
    assert d.slippage(5000) == pytest.approx(0.0)
