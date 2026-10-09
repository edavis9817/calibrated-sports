"""c-37: the yardage distribution and the walk-forward guards.

Pure functions only - no store is opened."""
import numpy as np
import pytest

from research import yards_markets as ym


def _arm(fam="gamma", pi0=0.1, mup=40.0, disp=1.5, n=1):
    return {"fam": fam, "pi0": np.full(n, pi0), "mup": np.full(n, mup), "disp": np.full(n, disp)}


@pytest.mark.parametrize("fam,disp", [("gamma", 1.5), ("lognormal", 0.8), ("weibull", 1.2)])
def test_pmf_sums_to_one_and_mean_is_the_declared_mean(fam, disp):
    y = np.arange(1, 4000, dtype=float)
    m = ym.pmf_pos(fam, 40.0, disp, y)
    assert abs(m.sum() - 1.0) < 1e-6
    # the discretised mean is the continuous mean to within the rounding of a yard
    assert abs((m * y).sum() - 40.0) < 0.6


def test_half_line_is_the_continuous_survival_and_matches_the_shipped_class():
    from core.distributions import ZeroInflatedGamma
    d = ZeroInflatedGamma(40.0, 40.0 ** 2 / 1.5, 0.1)
    a = _arm()
    for line in (0.5, 24.5, 61.5, 120.5):
        p = ym.prob_over(a, np.array([line]), np.array([False]))[0]
        assert abs(p - d.prob_over(line)) < 1e-9


def test_integer_line_push_conditional_and_no_push_flag_differ():
    a = _arm()
    L = np.array([50.0])
    over = 1 - ym.cdf_int(a, L)[0]
    at = ym.cdf_int(a, L)[0] - ym.cdf_int(a, L - 1)[0]
    p_push = ym.prob_over(a, L, np.array([True]))[0]
    p_flat = ym.prob_over(a, L, np.array([False]))[0]
    assert abs(p_push - over / (1 - at)) < 1e-9
    assert abs(p_flat - (over + at)) < 1e-9          # actual == line settles OVER without the flag
    assert p_push != p_flat


def test_pit_is_uniform_when_the_model_is_true_and_is_not_when_it_is_wrong():
    rng = np.random.default_rng(1)
    n = 20000
    a = _arm(n=n)
    x = np.round(rng.gamma(1.5, 40.0 / 1.5, n))
    x[rng.random(n) < 0.1] = 0
    # the simulated truth is rounded-gamma, whose zero mass is pi0 + (1-pi0) G(0.5)
    a["pi0"] = np.full(n, 0.1 + 0.9 * ym.pos_cdf("gamma", 40.0, 1.5, 0.5))
    u = np.sort(ym.pit(a, x, np.random.default_rng(2)))
    ks = np.max(np.abs(np.arange(1, n + 1) / n - u))
    assert ks < 0.02
    wrong = _arm(mup=60.0, n=n)
    u2 = np.sort(ym.pit(wrong, x, np.random.default_rng(2)))
    assert np.max(np.abs(np.arange(1, n + 1) / n - u2)) > 0.08   # the check can fail


def test_crps_is_zero_for_a_point_mass_on_the_outcome_and_grows_with_distance():
    F = (np.arange(ym.GRID + 1)[None, :] >= 30).astype(float)
    assert ym.crps_from_cdf(F, np.array([30.0]))[0] == 0.0
    assert ym.crps_from_cdf(F, np.array([40.0]))[0] == 10.0


def test_tables_and_shapes_refuse_a_training_row_from_the_target_season():
    rows = [{"season": s, "gsis": "a", "pos": "WR", "pb": "WR|1", "y": 10.0, "kick": float(s),
             "n": 1.0, "ybar": 10.0, "z": 0.0, "pvar": 1.0} for s in (2022, 2023)]
    with pytest.raises(ym.LeakError):
        ym.fit_tables(rows, 2023)
    with pytest.raises(ym.LeakError):
        ym.fit_shapes(rows, {}, 2023)
    with pytest.raises(ym.LeakError):
        ym.fit_tables([], 2023)


def test_modal_rung_takes_most_books_then_nearest_the_weighted_mean_then_lower():
    def r(line, n):
        return {"gsis": "a", "game": "g", "line": line, "n_all": n, "p_all": 0.5}
    assert ym.modal_rungs([r(49.5, 1), r(50.5, 3), r(52.5, 2)])[0]["line"] == 50.5
    # tie on books: weighted mean line is 51.0 -> 50.5 and 51.5 equidistant -> lower
    assert ym.modal_rungs([r(50.5, 2), r(51.5, 2)])[0]["line"] == 50.5
    assert ym.modal_rungs([r(50.5, 2), r(51.5, 2), r(55.5, 1)])[0]["line"] == 51.5
    assert ym.modal_rungs([dict(r(50.5, 2), p_all=None)]) == []


def test_boot_mean_does_not_narrow_when_rows_are_duplicated_inside_their_game():
    rng = np.random.default_rng(3)
    g = np.repeat(np.arange(60), 5)
    d = rng.normal(0, 1, 300) + np.repeat(rng.normal(0, 1, 60), 5)
    a = ym.boot_mean(d, g)
    b = ym.boot_mean(np.tile(d, 20), np.tile(g, 20))
    assert abs(a["se"] - b["se"]) < 1e-9
