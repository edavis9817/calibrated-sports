"""The market-implied distribution engine. Run: pytest -q

Every test here is a way the object could look right and be wrong. A survival
function that rises, a de-vig that fires on an exchange spread, a copula that
silently decouples - none of these raise. They produce a plausible number.
"""
import math

import pytest

from research.implied import (SCORINGS, _gamma_survival, _nb_survival,
                              fit_marginal, gaussian_copula_draw,
                              isotonic_decreasing, power_devig, shin_devig)


# --- de-vig ------------------------------------------------------------------

def test_devig_is_a_no_op_on_a_fair_pair():
    """No margin, no correction. A method that moves a fair price is inventing
    a margin that is not there - which is exactly what would happen if Shin
    were applied to a Kalshi exchange spread."""
    assert shin_devig(0.5, 0.5) == pytest.approx(0.5, abs=1e-6)
    assert power_devig(0.4, 0.6) == pytest.approx(0.4, abs=1e-6)


def test_devig_output_is_a_probability_and_the_pair_sums_to_one():
    for po, pu in ((0.5745, 0.4878), (0.1375, 0.8875), (0.92, 0.11)):
        s = shin_devig(po, pu)
        s_other = shin_devig(pu, po)
        assert 0 < s < 1
        assert s + s_other == pytest.approx(1.0, abs=1e-4)


def test_shin_discounts_the_longshot_harder_than_multiplicative():
    """THE reason multiplicative was rejected. It spreads the margin
    proportionally, which leaves the longshot too high - and the longshot end is
    where the calibration study found the market pricing 0.1375 against a
    realized 0.0674."""
    po, pu = 0.1375, 0.8875
    mult = po / (po + pu)
    assert shin_devig(po, pu) < mult
    assert power_devig(po, pu) < mult
    # and it must not overcorrect into nonsense
    assert shin_devig(po, pu) > 0.05


def test_devig_is_symmetric_at_the_favourite_end_too():
    """A correction that only fires on longshots would tilt every book."""
    po, pu = 0.8875, 0.1375
    assert shin_devig(po, pu) > po / (po + pu)


# --- monotonicity ------------------------------------------------------------

def test_a_survival_function_cannot_rise():
    """Pooled book quotes violate this routinely. An un-monotone survival curve
    implies a negative density somewhere, which surfaces later as a nonsense
    simulated draw rather than as an error."""
    out = isotonic_decreasing([1.5, 2.5, 3.5, 4.5], [0.8, 0.55, 0.60, 0.30])
    assert all(out[i] >= out[i + 1] - 1e-9 for i in range(len(out) - 1))
    assert out[1] == pytest.approx(out[2])          # the violating pair pooled
    assert out[0] == pytest.approx(0.8)             # clean points untouched


def test_an_already_monotone_ladder_is_returned_unchanged():
    ys = [0.9, 0.7, 0.4, 0.2]
    assert isotonic_decreasing([1.5, 2.5, 3.5, 4.5], ys) == pytest.approx(ys)


# --- the fitted marginal -----------------------------------------------------

def test_the_fast_nb_survival_matches_the_shipped_distribution():
    """The grid search reimplements the survival function for speed. If it
    drifts from core.distributions the whole engine is fitting a different
    object from the one the rest of the repo prices with."""
    from core.distributions import NegativeBinomial
    for mean, vmr, line in ((3.2, 1.6, 2.5), (0.8, 2.1, 0.5), (7.4, 1.3, 9.5)):
        assert _nb_survival(mean, vmr, line) == pytest.approx(
            NegativeBinomial(mean, vmr).prob_over(line), abs=1e-9)


def test_a_fit_recovers_the_distribution_that_generated_it():
    """Round trip: build survival points from a known negative binomial, fit
    them back, and the mean must return."""
    true_mean, true_vmr = 4.6, 1.8
    pts = [(l, _nb_survival(true_mean, true_vmr, l)) for l in (1.5, 2.5, 3.5, 4.5, 5.5, 6.5)]
    fit = fit_marginal("receptions", pts)
    assert fit["mean"] == pytest.approx(true_mean, rel=0.12)
    assert fit["n_points"] == 6
    assert not fit["assumed_shape"] and not fit["interpolated"]


def test_density_is_recorded_not_hidden():
    """A distribution fitted to one point is not the same object as one fitted
    to six, and the page has to be able to say so."""
    one = fit_marginal("receptions", [(3.5, 0.42)])
    assert one["n_points"] == 1
    assert one["assumed_shape"] and one["interpolated"]
    six = fit_marginal("receptions",
                       [(l, _nb_survival(4.0, 1.7, l)) for l in (1.5, 2.5, 3.5, 4.5, 5.5, 6.5)])
    assert not six["assumed_shape"]


def test_a_fit_is_monotone_in_the_threshold():
    fit = fit_marginal("receiving_yards", [(20.5, 0.72), (40.5, 0.44), (60.5, 0.20)])
    s = [_gamma_survival(fit["mean"], fit["shape"], x) for x in (10, 30, 50, 80, 120)]
    assert all(s[i] >= s[i + 1] for i in range(len(s) - 1))


def test_an_empty_ladder_yields_nothing_rather_than_a_default():
    assert fit_marginal("receptions", []) is None
    assert fit_marginal("receptions", [(None, None)]) is None


# --- coupling ----------------------------------------------------------------

def test_the_copula_actually_couples():
    """Independent marginals understate the joint upper tail, which is exactly
    the fantasy ceiling. If this ever returns uncorrelated draws the engine
    looks fine in the middle and is quietly wrong about a boom game."""
    import random
    rng = random.Random(3)
    pairs = [gaussian_copula_draw(0.8, rng) for _ in range(4000)]
    both_high = sum(1 for a, b in pairs if a > 0.9 and b > 0.9) / len(pairs)
    assert both_high > 0.045, "0.8 rank correlation should co-move the tails"
    indep = [(rng.random(), rng.random()) for _ in range(4000)]
    assert both_high > sum(1 for a, b in indep if a > 0.9 and b > 0.9) / len(indep)


def test_zero_correlation_reproduces_independence():
    import random
    rng = random.Random(4)
    pairs = [gaussian_copula_draw(0.0, rng) for _ in range(4000)]
    both = sum(1 for a, b in pairs if a > 0.9 and b > 0.9) / len(pairs)
    assert both == pytest.approx(0.01, abs=0.006)


def test_copula_draws_are_uniform_marginally():
    """A copula must not distort the marginals it couples."""
    import random
    rng = random.Random(5)
    xs = [gaussian_copula_draw(0.6, rng)[0] for _ in range(4000)]
    assert sum(xs) / len(xs) == pytest.approx(0.5, abs=0.02)
    assert sum(1 for x in xs if x < 0.25) / len(xs) == pytest.approx(0.25, abs=0.02)


# --- scoring -----------------------------------------------------------------

def test_the_three_scorings_differ_only_where_they_should():
    """PPR and standard disagree by exactly one point per reception. If they
    ever differ elsewhere, a scoring change has leaked into the yardage term."""
    for k in ("rec_yd", "rush_yd", "td"):
        assert SCORINGS["ppr"][k] == SCORINGS["standard"][k]
    assert SCORINGS["ppr"]["rec"] - SCORINGS["standard"]["rec"] == 1.0
    assert SCORINGS["half_ppr"]["rec"] == 0.5
