"""c-30: the key-number margin keeps c-28's moneyline and changes only the shape."""
import numpy as np
import pytest
from scipy.stats import norm

from models import key_margin as KM
from models.game import GameForecast

K = KM._K


def _base(p=0.62, sigma=13.5):
    return GameForecast(game_id="g", home="H", away="A", as_of="kickoff",
                        p_home=p, sigma_m=sigma, mu_t=44.0, sigma_t=13.0)


def _spiky():
    w = np.ones(KM.J)
    w[2], w[6] = 2.8, 1.8          # |3| and |7|
    return w


def test_pmf_sums_to_one_and_pins_the_moneyline():
    rng = np.random.default_rng(0)
    p = rng.uniform(0.05, 0.95, 200)
    mu = 13.5 * norm.ppf(p)
    P = KM.pmf(p, mu, np.full(200, 13.5), _spiky(), 0.004)
    assert np.allclose(P.sum(axis=1), 1.0, atol=1e-12)
    cond = P[:, K >= 1].sum(axis=1) / (1 - P[:, K == 0].sum(axis=1))
    assert np.max(np.abs(cond - p)) < 1e-12
    assert np.allclose(P[:, K == 0].ravel(), 0.004)


def test_unit_weights_are_the_discretised_normal_within_each_side():
    p, mu, s = 0.6, 13.5 * norm.ppf(0.6), 13.5
    P = KM.pmf([p], [mu], [s], np.ones(KM.J), 0.0)[0]
    b = KM.base_mass([mu], [s])[0]
    pos = K >= 1
    assert np.allclose(P[pos] / P[pos].sum(), b[pos] / b[pos].sum(), atol=1e-14)


def test_spiky_weights_move_mass_to_the_key_number_and_the_unit_ones_do_not():
    p, mu, s = 0.6, 13.5 * norm.ppf(0.6), 13.5
    Pk = KM.pmf([p], [mu], [s], _spiky(), 0.0)[0]
    Pn = KM.pmf([p], [mu], [s], np.ones(KM.J), 0.0)[0]
    assert Pk[K == 3][0] > 2 * Pn[K == 3][0]
    assert Pk[K == 4][0] < Pn[K == 4][0]


def test_fit_recovers_a_planted_spike_and_finds_none_when_none_is_planted():
    rng = np.random.default_rng(1)
    n = 4000
    mu = rng.normal(2, 5, n)
    s = np.full(n, 13.5)
    p = norm.sf(0, mu, s)
    for w, want in ((_spiky(), True), (np.ones(KM.J), False)):
        P = KM.pmf(p, mu, s, w, 0.0)
        cdf = P.cumsum(axis=1)
        m = K[(cdf < rng.random((n, 1))).sum(axis=1)]
        wf, info = KM.fit_weights(mu, s, m)
        assert info["n"] == int((m != 0).sum())
        if want:
            assert wf[2] > 2.2 and wf[6] > 1.4
        else:
            assert 0.8 < wf[2] < 1.25 and 0.8 < wf[6] < 1.25


def test_the_penalty_pulls_toward_the_normal_on_little_data():
    wf, _ = KM.fit_weights([0.0, 0.0], [13.5, 13.5], [3, -3])
    assert np.all(np.abs(np.log(wf)) < 0.5)
    assert wf[2] > 1.0                      # and still leans the right way


def test_object_keeps_the_base_moneyline_and_voids_a_push():
    base = _base()
    f = KM.KeyMarginForecast(base, tuple(_spiky()), 0.003)
    assert f.prob_home_win() == base.p_home
    assert f.prob_margin_eq(2.5) == 0.0
    assert f.prob_cover(2.5) == pytest.approx(f.prob_margin_over(2.5))
    assert f.prob_cover(3) == pytest.approx(f.prob_margin_over(3) / (1 - f.prob_margin_eq(3)))
    # a push at 3 is large under the spike, so conditioning on no push matters there
    flat = KM.KeyMarginForecast(base, tuple(np.ones(KM.J)), 0.003)
    assert f.prob_margin_eq(3) > 2 * flat.prob_margin_eq(3)
    # the away rung is the other tail, never the home rung reused
    assert f.prob_team_by_over("A", 3.5) == pytest.approx(float(f._pmf[K < -3.5].sum()))
    assert f.prob_team_by_over("A", 3.5) + f.prob_team_by_over("H", -3.5) == pytest.approx(1.0)
    with pytest.raises(ValueError):
        f.prob_team_by_over("X", 3.5)


def test_cover_probs_matches_the_object():
    base = _base(0.7)
    w, t = _spiky(), 0.002
    f = KM.KeyMarginForecast(base, tuple(w), t)
    over, eq = KM.cover_probs([base.p_home], [base.mu_m], [base.sigma_m], w, t, [7.0])
    assert over[0] == pytest.approx(f.prob_margin_over(7.0), abs=1e-14)
    assert eq[0] == pytest.approx(f.prob_margin_eq(7), abs=1e-14)


def test_stratum_rule():
    from research.against_the_spread import stratum
    assert [stratum(x) for x in (3, -3, 7.0, -7, 4, -10, 2.5, -6.5)] == [
        "on 3", "on 3", "on 7", "on 7", "other whole", "other whole", "half point", "half point"]
