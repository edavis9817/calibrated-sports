"""The reported metric must not be positive by construction. Run: pytest -q

THE BUG THESE EXIST FOR. `evaluate()` picks whichever side the model disagrees
with, so `gross_edge = model_p - market_p` is |model - market| and is positive
for ANY model. Averaged over tickets that had already been filtered to
`net_edge >= MIN_NET_EDGE`, it reported 13-18pp of "edge" from a plumbing
baseline. Two sessions of diagnostic work - depth, polarity, dispersion, model
staleness - were spent explaining a number that a random model reproduces.

A selected-sample statistic is a property of the filter.
"""
import random
import statistics

import pytest

from jobs.paper_trade import MIN_NET_EDGE, evaluate


def _book(mid, spread=0.02):
    return mid - spread / 2, mid + spread / 2


# --- the failure mode, demonstrated -----------------------------------------

def test_a_pure_noise_model_scores_a_large_positive_gross_edge():
    """The whole point. A model that knows NOTHING - its probabilities are
    independent of the market's - still averages a big positive `gross_edge`,
    because the side is chosen after the fact to make it positive."""
    rnd = random.Random(11)
    gross, signed = [], []
    for _ in range(4000):
        mid = rnd.uniform(0.15, 0.85)
        noise = min(max(rnd.gauss(mid, 0.15), 0.01), 0.99)   # zero skill
        ev = evaluate(noise, *_book(mid))
        gross.append(ev["gross_edge"])
        signed.append(ev["signed_edge"])

    assert statistics.fmean(gross) > 0.10, "noise scores >10pp of 'edge'"
    assert abs(statistics.fmean(signed)) < 0.01, "and zero signed bias"


def test_selecting_on_edge_inflates_it_further():
    """The second half of the artifact. Filtering to net_edge >= MIN_NET_EDGE
    keeps only the tail of a quantity that was already an absolute value."""
    rnd = random.Random(12)
    kept = []
    for _ in range(4000):
        mid = rnd.uniform(0.15, 0.85)
        noise = min(max(rnd.gauss(mid, 0.15), 0.01), 0.99)
        ev = evaluate(noise, *_book(mid))
        if ev["net_edge"] >= MIN_NET_EDGE:
            kept.append(ev["net_edge"])

    assert kept, "a noise model produces tickets"
    assert statistics.fmean(kept) > MIN_NET_EDGE, \
        "the surviving mean cannot be below the threshold it survived"


# --- what signed_edge guarantees --------------------------------------------

def test_signed_edge_is_on_a_fixed_axis_whichever_side_is_taken():
    """gross_edge flips its reference with the side; signed_edge does not. That
    is exactly what makes one averageable and the other not."""
    over = evaluate(0.70, *_book(0.50))     # model above market -> yes
    under = evaluate(0.30, *_book(0.50))    # model below market -> no

    assert over["side"] == "yes" and under["side"] == "no"
    assert over["gross_edge"] == pytest.approx(0.20)
    assert under["gross_edge"] == pytest.approx(0.20)    # both positive
    assert over["signed_edge"] == pytest.approx(+0.20)
    assert under["signed_edge"] == pytest.approx(-0.20)  # and this one is not


def test_a_calibrated_model_averages_zero_signed_edge():
    """The property that makes signed_edge worth reporting: a model that agrees
    with the market in distribution scores zero, and only a real disagreement
    moves it."""
    rnd = random.Random(13)
    vals = [evaluate(min(max(rnd.gauss(m, 0.10), 0.01), 0.99),
                     *_book(m))["signed_edge"]
            for m in (rnd.uniform(0.2, 0.8) for _ in range(5000))]
    assert abs(statistics.fmean(vals)) < 0.01


def test_a_biased_model_shows_the_bias_with_the_right_sign():
    """And a model that IS systematically low reports negative - which is what
    rush_attempts actually does at -12.7pp."""
    rnd = random.Random(14)
    vals = []
    for _ in range(3000):
        mid = rnd.uniform(0.25, 0.75)
        vals.append(evaluate(max(mid - 0.08, 0.01), *_book(mid))["signed_edge"])
    assert statistics.fmean(vals) == pytest.approx(-0.08, abs=0.005)


def test_gross_edge_is_never_negative_and_that_is_the_warning():
    """Documenting the trap rather than removing it: gross_edge and net_edge
    describe ONE ticket's economics and are correct for that. They are simply
    not averageable, and a reader who does not know that will average them."""
    rnd = random.Random(15)
    for _ in range(500):
        mid = rnd.uniform(0.05, 0.95)
        ev = evaluate(rnd.uniform(0.01, 0.99), *_book(mid))
        assert ev["gross_edge"] >= 0.0
