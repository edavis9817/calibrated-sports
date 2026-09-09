"""Core tests. Run: pytest -q

These target the things that would silently corrupt results rather than throw:
outcome_id stability, push handling, and whether the fitted families actually
reproduce the moments measured in research/.
"""
import numpy as np
import pytest

from core.distributions import (Empirical, NegativeBinomial, ZeroInflatedGamma,
                                american_to_prob, devig_two_way,
                                edge_after_fees, kalshi_fee)
from core.outcomes import (MarketType, Side, Sport, Stat, is_push_possible,
                           player_prop)


# --- outcome identity --------------------------------------------------------

def test_outcome_id_is_stable_and_readable():
    o = player_prop(2026, 1, "00-0036355", Stat.RECEPTIONS, 5.5)
    assert o.key == "nfl|2026|wk1|player_prop|00-0036355|receptions|5.5|over"
    assert o.outcome_id == player_prop(2026, 1, "00-0036355",
                                       Stat.RECEPTIONS, 5.5).outcome_id


def test_line_formatting_does_not_fork_history():
    """5.0 and 5 must produce the same id, or a float repr change silently
    splits one market's history into two."""
    a = player_prop(2026, 1, "p1", Stat.RECEPTIONS, 5.0)
    b = player_prop(2026, 1, "p1", Stat.RECEPTIONS, 5)
    assert a.outcome_id == b.outcome_id
    assert "|5|" in a.key


def test_opposite_side_round_trips():
    o = player_prop(2026, 1, "p1", Stat.RECEPTIONS, 5.5, Side.OVER)
    assert o.opposite().side is Side.UNDER
    assert o.opposite().opposite() == o


def test_sport_discriminator_changes_identity():
    a = player_prop(2026, 1, "p1", Stat.RECEPTIONS, 5.5, sport=Sport.NFL)
    b = player_prop(2026, 1, "p1", Stat.RECEPTIONS, 5.5, sport=Sport.MLB)
    assert a.outcome_id != b.outcome_id


def test_push_only_on_integer_lines_of_discrete_stats():
    assert is_push_possible(5.0, Stat.RECEPTIONS) is True
    assert is_push_possible(5.5, Stat.RECEPTIONS) is False
    assert is_push_possible(50.0, Stat.RECEIVING_YARDS) is False


# --- distributions reproduce the measured moments ----------------------------

@pytest.mark.parametrize("mean,vmr", [(3.75, 1.69), (12.14, 3.72)])
def test_negbin_recovers_its_moments(mean, vmr):
    d = NegativeBinomial(mean, vmr)
    s = d.sample(400_000, np.random.default_rng(0))
    assert s.mean() == pytest.approx(mean, rel=0.02)
    assert s.var() / s.mean() == pytest.approx(vmr, rel=0.05)
    assert d.mean() == pytest.approx(mean, rel=1e-9)


def test_negbin_is_overdispersed_vs_poisson():
    """Receptions var/mean is 1.69; a Poisson would put it at 1.0 and would
    therefore understate the tails a prop line lives in."""
    d = NegativeBinomial(3.75, 1.69)
    s = d.sample(200_000, np.random.default_rng(1))
    pois = np.random.default_rng(1).poisson(3.75, 200_000)
    assert s.var() > pois.var() * 1.5
    # and the over on a high line is materially more likely
    assert d.prob_over(7.5) > (pois > 7.5).mean() * 1.3


def test_zig_recovers_overall_moments_and_zero_mass():
    # receiving yards: mean 47.7, sd 37.0, P(0) 5.8%
    d = ZeroInflatedGamma.from_overall_moments(47.73, 36.96 ** 2, 0.058)
    s = d.sample(400_000, np.random.default_rng(2))
    assert s.mean() == pytest.approx(47.73, rel=0.03)
    assert s.std() == pytest.approx(36.96, rel=0.06)
    assert (s == 0).mean() == pytest.approx(0.058, abs=0.004)


def test_zig_is_right_skewed():
    d = ZeroInflatedGamma.from_overall_moments(47.73, 36.96 ** 2, 0.058)
    s = d.sample(200_000, np.random.default_rng(3))
    skew = ((s - s.mean()) ** 3).mean() / s.std() ** 3
    assert skew > 0.7


# --- push handling -----------------------------------------------------------

def test_push_conditioning_changes_the_price_materially():
    """On an integer line the mass sitting exactly on it is voided, so the
    conditional over is higher than the raw over. For receptions that gap is
    several probability points - large enough to flip a bet decision."""
    d = NegativeBinomial(3.75, 1.69)
    raw = d.prob_over(4.0, push=False)
    cond = d.prob_over(4.0, push=True)
    assert cond > raw
    assert cond - raw > 0.01
    assert d.prob_over(4.0, True) + d.prob_under(4.0, True) == pytest.approx(1.0)


def test_half_lines_have_no_push_mass():
    d = NegativeBinomial(3.75, 1.69)
    assert d.prob_over(5.5, push=True) == pytest.approx(d.prob_over(5.5, False))


# --- empirical / correlation -------------------------------------------------

def test_empirical_preserves_correlation_across_players():
    """The whole reason the simulator emits joint samples: two Empiricals
    built from the same sim run must retain their dependence. QB<->WR1 was
    0.423 in the data; a per-prop model would imply zero."""
    rng = np.random.default_rng(4)
    n = 100_000
    shared = rng.normal(size=n)                       # game passing volume
    qb = 250 + 60 * shared + 55 * rng.normal(size=n)
    wr = 60 + 20 * shared + 28 * rng.normal(size=n)
    assert np.corrcoef(qb, wr)[0, 1] == pytest.approx(0.42, abs=0.06)

    d_qb, d_wr = Empirical(qb), Empirical(wr)
    # marginals are correct...
    assert d_qb.prob_over(250) == pytest.approx((qb > 250).mean(), abs=0.01)
    # ...and the joint is recoverable from the aligned sample arrays
    joint = ((qb > 250) & (wr > 60)).mean()
    assert joint > d_qb.prob_over(250) * d_wr.prob_over(60) + 0.03


def test_empirical_reports_monte_carlo_error():
    d = Empirical(np.random.default_rng(5).normal(0, 1, 10_000))
    se = d.mc_stderr(0.0)
    assert 0.0045 < se < 0.0055          # ~0.5 pts at p=0.5, n=10k


# --- market maths ------------------------------------------------------------

def test_american_odds_conversion():
    assert american_to_prob(-110) == pytest.approx(0.5238, abs=1e-4)
    assert american_to_prob(+150) == pytest.approx(0.4000, abs=1e-4)


def test_devig_sums_to_one_and_removes_overround():
    po, pu = american_to_prob(-110), american_to_prob(-110)
    assert po + pu > 1.0
    a, b = devig_two_way(po, pu)
    assert a + b == pytest.approx(1.0)
    assert a == pytest.approx(0.5)


def test_kalshi_fee_peaks_at_the_middle_and_collapses_at_the_tails():
    """The structural reason tail mispricing is the cheapest to trade."""
    mid = kalshi_fee(0.50, 100)
    tail = kalshi_fee(0.07, 100)
    assert mid == pytest.approx(1.75, abs=0.01)
    assert tail < mid / 3
    assert kalshi_fee(0.50, 100, maker=True) < mid / 3


def test_no_edge_at_fair_value_after_fees():
    """Buying at exactly fair value must be negative EV once fees are paid -
    if this ever passes at zero, the fee model has been dropped somewhere."""
    assert edge_after_fees(0.50, 0.50) < 0
    assert edge_after_fees(0.60, 0.50) > 0
