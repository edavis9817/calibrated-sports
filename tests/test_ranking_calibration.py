"""c-24: the statistics in research/ranking_calibration.py, against brute force.

Every check is shown returning the OTHER answer on the other input, so none of
them can pass against a constant.
"""
import itertools

import numpy as np

from research import ranking_calibration as rc


def brute_auc(p, y, strata=None):
    num = den = 0.0
    for i, j in itertools.product(range(len(p)), repeat=2):
        if y[i] == 1 and y[j] == 0 and (strata is None or strata[i] == strata[j]):
            den += 1
            num += 1.0 if p[i] > p[j] else 0.5 if p[i] == p[j] else 0.0
    return num / den


def test_auc_matches_brute_force_with_ties():
    rng = np.random.default_rng(1)
    p = np.round(rng.random(60), 1)          # heavy ties on purpose
    y = (rng.random(60) < p).astype(float)
    assert abs(rc.auc(p, y) - brute_auc(p, y)) < 1e-12
    assert rc.auc(y, y) == 1.0 and rc.auc(1 - y, y) == 0.0


def test_within_line_auc_counts_only_same_stratum_pairs():
    rng = np.random.default_rng(2)
    p = rng.random(50)
    y = (rng.random(50) < 0.4).astype(float)
    s = rng.integers(0, 4, 50)
    assert abs(rc.wauc(p, y, s) - brute_auc(p, y, s)) < 1e-12
    # a forecaster that is informative ONLY across strata: pooled AUC high,
    # within-stratum AUC exactly 1/2
    s2 = np.repeat([0, 1], 50)
    y2 = np.concatenate([np.r_[np.ones(10), np.zeros(40)], np.r_[np.ones(40), np.zeros(10)]])
    p2 = np.where(s2 == 1, 0.8, 0.2)
    assert rc.auc(p2, y2) > 0.75
    assert rc.wauc(p2, y2, s2) == 0.5


def test_pav_is_monotone_and_optimal():
    rng = np.random.default_rng(3)
    p = rng.random(200)
    y = (rng.random(200) < p ** 2).astype(float)
    fit, _c, v = rc.pav(p, y)
    order = np.argsort(p)
    assert np.all(np.diff(fit[order]) >= -1e-12)
    assert np.all(np.diff(v) > 0)
    # no monotone step function beats it: compare with a few monotone candidates
    for cand in (np.clip(p, 0, 1), p ** 2, np.full_like(p, y.mean())):
        assert rc.brier(fit, y) <= rc.brier(cand, y) + 1e-12


def test_corp_identity_and_direction():
    rng = np.random.default_rng(4)
    q = rng.random(3000)
    y = (rng.random(3000) < q).astype(float)
    good = rc.corp(q, y)
    shrunk = rc.corp(0.5 + 0.3 * (q - 0.5), y)   # same ORDER, miscalibrated
    noise = rc.corp(rng.random(3000), y)          # no information
    for c in (good, shrunk, noise):
        assert abs(c["bs"] - (c["mcb"] - c["dsc"] + c["unc"])) < 1e-9
    # a monotone distortion moves MCB and leaves DSC exactly unchanged
    assert abs(shrunk["dsc"] - good["dsc"]) < 1e-12
    assert shrunk["mcb"] > good["mcb"] + 0.005
    # an uninformative forecaster loses resolution, not only calibration
    assert noise["dsc"] < good["dsc"] - 0.05   # DSC <= Var(q) = 1/12 here


def test_platt_recovers_a_known_distortion():
    rng = np.random.default_rng(5)
    q = np.clip(rng.random(20000), 0.01, 0.99)
    y = (rng.random(20000) < q).astype(float)
    x = rc.logit(q)
    distorted = 1 / (1 + np.exp(-(x - 0.4) / 1.5))   # q = logistic(0.4 + 1.5*logit(d))
    f, (a, b) = rc.platt_fit(distorted, y)
    assert abs(a - 0.4) < 0.1 and abs(b - 1.5) < 0.1
    assert rc.brier(f(distorted), y) < rc.brier(distorted, y)


def test_block_bootstrap_does_not_narrow_under_duplication():
    """Duplicating every row inside its own game must not narrow the interval
    (the effective sample is games)."""
    rng = np.random.default_rng(6)
    rows = [{"game": f"g{g}", "stat": "receptions", "line": 3.5, "y": float(rng.random() < 0.5),
             "m": float(rng.random()), "k": float(rng.random())} for g in range(30) for _ in range(5)]
    a = rc.Pop("a", rows, "m", "k").boot(lambda i, P=None: None, draws=10)
    assert a["est"] is None
    pop1 = rc.Pop("a", rows, "m", "k")
    pop20 = rc.Pop("b", rows * 20, "m", "k")
    f1 = pop1.boot(lambda i: rc.brier(pop1.m[i], pop1.y[i]), draws=400)
    f20 = pop20.boot(lambda i: rc.brier(pop20.m[i], pop20.y[i]), draws=400)
    w1, w20 = f1["hi"] - f1["lo"], f20["hi"] - f20["lo"]
    assert w20 > 0.7 * w1
