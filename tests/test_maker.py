"""M01: the fill simulation - side mapping, queue position, and what a fill was worth.

Run: pytest -q tests/test_maker.py

Synthetic. Two of these guard mistakes that would produce a plausible answer
rather than an error, which is the only kind worth writing a test for here:
reading `taker_side` backwards, and assuming front-of-queue.
"""
import pytest

from research import maker


def row(entry_bid=0.40, entry_ask=0.46, close_bid=0.50, close_ask=0.54,
        side="yes", q_yes=0.0, q_no=0.0, entry_ts=0.0, kickoff=1000.0, **kw):
    r = {"entry_bid": entry_bid, "entry_ask": entry_ask,
         "close_bid": close_bid, "close_ask": close_ask,
         "entry_mid": (entry_bid + entry_ask) / 2,
         "close_mid": (close_bid + close_ask) / 2,
         "side": side, "sgn": 1.0 if side == "yes" else -1.0,
         "entry_ts": entry_ts, "kickoff": kickoff,
         "market_id": "M", "game": "G1", "entity": "P", "stat": "receptions",
         # touch_size(buy_no) is the queue at the YES bid, and vice versa
         "touch_size": {"buy_yes": q_no, "buy_no": q_yes}}
    r.update(kw)
    return r


def prints(*specs):
    """(ts, taker_side, yes_price, size)"""
    return [{"ts": ts, "taker_side": tk, "yes_price": yp,
             "no_price": None if yp is None else round(1.0 - yp, 6),
             "size": sz} for ts, tk, yp, sz in specs]


# =============================================================================
# the side mapping, which inverts everything if wrong
# =============================================================================

def test_a_passive_yes_buy_rests_at_the_bid():
    assert maker.passive_price(row(side="yes")) == pytest.approx(0.40)


def test_a_passive_no_buy_rests_at_one_minus_the_ask():
    assert maker.passive_price(row(side="no")) == pytest.approx(0.54)


def test_a_yes_bid_is_filled_by_a_taker_buying_NO():
    """A taker buying no is selling yes into the bid ladder - that is the print
    that fills a passive yes buyer. Reading this backwards would fill us off
    the wrong half of the tape and still produce a plausible rate."""
    t = prints((1.0, "no", 0.40, 500))[0]
    assert maker.eligible(t, "yes", 0.40) is True
    assert maker.eligible(t, "no", 0.60) is False


def test_a_no_bid_is_filled_by_a_taker_buying_YES():
    t = prints((1.0, "yes", 0.46, 500))[0]
    assert maker.eligible(t, "no", 0.54) is True
    assert maker.eligible(t, "yes", 0.40) is False


def test_a_print_above_our_bid_does_not_fill_us():
    """A taker selling into a BETTER bid than ours never reaches our level."""
    t = prints((1.0, "no", 0.43, 5000))[0]
    assert maker.eligible(t, "yes", 0.40) is False


def test_a_print_through_our_bid_does_fill_us():
    t = prints((1.0, "no", 0.38, 100))[0]
    assert maker.eligible(t, "yes", 0.40) is True


# =============================================================================
# queue position - the easiest way to make this study lie
# =============================================================================

def test_we_sit_behind_the_size_already_resting():
    """200 ahead of us, 100 ticket. 250 of eligible volume is NOT enough."""
    r = row(side="yes", q_yes=200.0)
    s = maker.simulate(r, prints((10.0, "no", 0.40, 250)), ticket=100)
    assert s["queue_ahead"] == 200.0
    assert s["filled"] is False


def test_enough_volume_clears_the_queue_and_then_us():
    r = row(side="yes", q_yes=200.0)
    s = maker.simulate(r, prints((10.0, "no", 0.40, 250),
                                 (20.0, "no", 0.40, 60)), ticket=100)
    assert s["filled"] is True
    assert s["fill_ts"] == 20.0


def test_front_of_queue_would_have_doubled_the_fill_rate():
    """The optimistic modelling this study must not do, shown explicitly."""
    r = row(side="yes", q_yes=500.0)
    p = prints((10.0, "no", 0.40, 150))
    assert maker.simulate(r, p, ticket=100)["filled"] is False
    r0 = row(side="yes", q_yes=0.0)
    assert maker.simulate(r0, p, ticket=100)["filled"] is True


def test_the_queue_for_a_yes_buy_is_the_buy_no_touch_size():
    """Buying NO consumes yes-bids, so touch_size(buy_no) IS the size resting
    at the best yes bid. Verified against quotes at build time; pinned here."""
    r = row(side="yes", q_yes=333.0, q_no=777.0)
    assert maker.queue_ahead(r, "yes") == 333.0
    assert maker.queue_ahead(r, "no") == 777.0


# =============================================================================
# the window
# =============================================================================

def test_prints_before_entry_or_after_kickoff_are_ignored():
    r = row(side="yes", q_yes=0.0, entry_ts=100.0, kickoff=200.0)
    s = maker.simulate(r, prints((50.0, "no", 0.40, 9999),
                                 (250.0, "no", 0.40, 9999)), ticket=100)
    assert s["n_prints"] == 0
    assert s["filled"] is False


def test_a_print_exactly_at_kickoff_is_excluded():
    r = row(side="yes", q_yes=0.0, entry_ts=0.0, kickoff=200.0)
    s = maker.simulate(r, prints((200.0, "no", 0.40, 9999)), ticket=100)
    assert s["filled"] is False


def test_a_market_that_never_prints_cannot_fill():
    s = maker.simulate(row(side="yes", q_yes=0.0), [], ticket=100)
    assert s["n_prints"] == 0 and s["filled"] is False


def test_no_depth_means_no_simulation_rather_than_a_guess():
    r = row(side="yes")
    r["touch_size"] = {}
    assert maker.simulate(r, prints((10.0, "no", 0.40, 999)), ticket=100) is None


# =============================================================================
# what a fill was worth
# =============================================================================

def test_maker_clv_is_the_close_mid_less_what_we_paid():
    r = row(side="yes", q_yes=0.0)
    s = maker.simulate(r, prints((10.0, "no", 0.40, 999)), ticket=100)
    gross = maker.maker_clv(r, s, net=False)
    assert gross == pytest.approx(0.52 - 0.40)


def test_maker_clv_beats_the_taker_price_by_the_spread():
    """The whole premise: same forecast, same close, better entry."""
    r = row(side="yes", q_yes=0.0)
    s = maker.simulate(r, prints((10.0, "no", 0.40, 999)), ticket=100)
    from research.clv import clv_one_crossing
    assert maker.maker_clv(r, s, net=False) - clv_one_crossing(r, basis="touch") \
        == pytest.approx(r["entry_ask"] - r["entry_bid"])


def test_the_maker_fee_is_charged_and_is_smaller_than_the_taker_fee():
    from core.distributions import kalshi_fee
    r = row(side="yes", q_yes=0.0)
    s = maker.simulate(r, prints((10.0, "no", 0.40, 999)), ticket=100)
    net = maker.maker_clv(r, s, net=True)
    gross = maker.maker_clv(r, s, net=False)
    assert gross - net == pytest.approx(
        float(kalshi_fee(0.40, 100, side="maker", multiplier=1)) / 100)
    assert (kalshi_fee(0.40, 100, side="maker", multiplier=1)
            < kalshi_fee(0.40, 100, side="taker"))


def test_no_side_clv_uses_the_complement_at_both_ends():
    r = row(side="no", q_no=0.0)
    s = maker.simulate(r, prints((10.0, "yes", 0.46, 999)), ticket=100)
    assert s["filled"] is True
    # paid 1-0.46 = 0.54 for no; close mid on the no side is 1-0.52 = 0.48
    assert maker.maker_clv(r, s, net=False) == pytest.approx(0.48 - 0.54)


def test_drift_after_fill_is_signed_on_our_side():
    """Positive means the market moved our way after trading with us."""
    r = row(side="yes", q_yes=0.0)
    s = maker.simulate(r, prints((10.0, "no", 0.40, 999)), ticket=100)
    assert maker.drift_after_fill(r, s, lambda m, t: 0.60) == pytest.approx(0.52 - 0.60)
    assert maker.drift_after_fill(r, s, lambda m, t: 0.40) == pytest.approx(0.52 - 0.40)


def test_drift_is_absent_for_an_unfilled_order():
    r = row(side="yes", q_yes=1e9)
    s = maker.simulate(r, prints((10.0, "no", 0.40, 1)), ticket=100)
    assert maker.drift_after_fill(r, s, lambda m, t: 0.5) is None
