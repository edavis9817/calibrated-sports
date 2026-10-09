"""c-38: the cross-sport comparison's machinery (`research.cross_sport`).

Run: pytest -q tests/test_cross_sport.py

No test here opens a database. What is pinned: each sport's own interval is
c-24's game-block interval (so it is the one c-28 / c-39 published); the
between-sport draws are independent per sport; Holm; the matched reweighting;
and the verdict can return every answer it has.
"""
import numpy as np
import pytest

from research import cross_sport as X
from research import ranking_calibration as rc


def _rows(n, seed, skill=1.0, prefix="g"):
    rng = np.random.default_rng(seed)
    p = np.clip(0.5 + skill * rng.normal(0, 0.18, n), 0.02, 0.98)
    y = (rng.random(n) < p).astype(float)
    noisy = np.clip(0.5 + 0.6 * (p - 0.5) + rng.normal(0, 0.05, n), 0.02, 0.98)
    return [{"game": "%s%05d" % (prefix, i), "season": 2005 + i % 20, "week": 1 + i % 15,
             "regular": i % 17 != 0, "neutral": i % 11 == 0, "conference": i % 3 != 0,
             "y": float(y[i]), "model": float(p[i]), "home": 0.57,
             "record": float(np.clip(0.5 + 0.3 * (p[i] - 0.5), 0.02, 0.98)),
             "elo_nomov": float(noisy[i]), "arm": float(noisy[i]), "arm0": 0.5, "price": float(p[i])}
            for i in range(n)]


def test_own_interval_is_c24s_game_block_interval():
    rows = _rows(400, 1)
    tab = X.Tab(rows, X.COLS)
    est, dr = X.boot(tab, X.SEED_OWN, 300)
    got = X.interval(est["d_home"], dr["d_home"], tab.n)
    pop = rc.Pop("t", [dict(r, stat="ml", line=0.0, m=r["model"], k=r["home"]) for r in rows], "m", "k")
    want = pop.boot(lambda i: rc.brier(pop.m[i], pop.y[i]) - rc.brier(pop.k[i], pop.y[i]), draws=300)
    for k in ("est", "lo", "hi", "se"):
        assert got[k] == pytest.approx(want[k], abs=1e-12)


def test_between_sport_seeds_are_independent_of_each_other_and_of_the_own_seed():
    assert len({X.SEED_OWN, X.SEED_NFL, X.SEED_CFB}) == 3
    a, b = X.Tab(_rows(500, 2, prefix="a"), X.COLS), X.Tab(_rows(500, 2, prefix="b"), X.COLS)
    # identical data: one shared seed gives a zero-width difference (common random
    # numbers); the registered seeds do not
    _e, shared_a = X.boot(a, 7, 200)
    _e, shared_b = X.boot(b, 7, 200)
    assert np.abs(shared_a["d_home"] - shared_b["d_home"]).max() == 0.0
    _e, ia = X.boot(a, X.SEED_NFL, 200)
    _e, ib = X.boot(b, X.SEED_CFB, 200)
    assert (ia["d_home"] - ib["d_home"]).std() > 0.5 * ia["d_home"].std()


def test_between_sport_difference_sees_a_real_difference_and_not_an_absent_one():
    weak, weak2 = X.Tab(_rows(3000, 3, 0.5), X.COLS), X.Tab(_rows(3000, 4, 0.5), X.COLS)
    strong = X.Tab(_rows(3000, 5, 1.2), X.COLS)
    ew, dw = X.boot(weak, X.SEED_NFL, 400)
    for other, want in ((weak2, "contains 0"), (strong, "below")):
        eo, do = X.boot(other, X.SEED_CFB, 400)
        r = X.interval(eo["d_home"] - ew["d_home"], do["d_home"] - dw["d_home"], 3000)
        assert rc.sign(r) == want


def test_core_statistics():
    r = X.core({"model": 0.18, "home": 0.24, "record": 0.22, "elo_nomov": 0.19,
                "arm": 0.2, "arm0": 0.21, "price": 0.17})
    assert r["d_home"] == pytest.approx(-0.06) and r["s_home"] == pytest.approx(0.25)
    assert r["c1"] == pytest.approx(-0.02) and r["c2"] == pytest.approx(-0.03)
    assert r["arm_vs_arm0"] == pytest.approx(-0.01) and r["d_price"] == pytest.approx(0.01)
    assert "arm_vs_home" not in X.core({"model": 0.18, "home": 0.24, "record": 0.22, "elo_nomov": 0.19})


def test_holm():
    assert X.holm([0.01, 0.04, 0.03]) == pytest.approx([0.03, 0.06, 0.06])
    assert X.holm([0.5, 0.0001]) == pytest.approx([0.5, 0.0002])
    assert X.holm([0.9, 0.9]) == [1.0, 1.0]


def test_a_zero_variance_bootstrap_is_p_one_and_few_games_are_not_read():
    assert X.interval(0.3, np.full(50, 0.3), 100)["p"] == 1.0
    r = X.interval(0.3, np.linspace(0.2, 0.4, 50), 4)
    assert r["lo"] is None and r["p"] == 1.0 and rc.sign(r) == "not read"


def test_matched_to_its_own_shares_is_zero_and_to_other_shares_is_not():
    a = X.Tab(_rows(2000, 6, 0.6), X.COLS)
    b = X.Tab(_rows(2000, 7, 1.3), X.COLS)
    ea, da = X.boot(a, X.SEED_NFL, 50, bins=True)
    eb, db = X.boot(b, X.SEED_CFB, 50, bins=True)
    e, v = X.matched(ea, da, ea, da, "home")
    assert abs(e) < 1e-12 and np.abs(v).max() < 1e-12
    e2, _v = X.matched(ea, da, eb, db, "home")
    raw = eb["d_home"] - ea["d_home"]
    # the stronger league's raw gap is mostly its spread of forecasts: matching shrinks it
    assert raw < 0 and abs(e2) < abs(raw)


def test_matched_drops_a_draw_with_an_empty_bin_instead_of_inventing_it():
    est_n = {"share": np.array([0.5, 0.5] + [0.0] * 6), "d_home": -0.02}
    dr_n = {"share": np.array([[0.5, 0.5] + [0.0] * 6] * 2), "d_home": np.array([-0.02, -0.02])}
    dbin = np.array([-0.03, -0.05] + [np.nan] * 6)
    hole = np.array([-0.03, np.nan] + [np.nan] * 6)
    e, v = X.matched(est_n, dr_n, {"dbin_home": dbin}, {"dbin_home": np.array([dbin, hole])}, "home")
    assert e == pytest.approx(-0.02) and v[0] == pytest.approx(-0.02) and np.isnan(v[1])
    assert X.interval(e, v, 100)["draws"] == 1


def _all(sign="below", ok=True):
    tests = ("d_home", "d_record", "d_elo_nomov", "c1", "c2")
    return ({s: {t: sign for t in tests} for s in ("nfl", "cfb")},
            {s: {t: ok for t in tests} for s in ("nfl", "cfb")})


def test_verdict_reaches_every_answer():
    signs, hok = _all()
    assert X.verdict(signs, hok) == (True, [])
    signs["nfl"]["c2"] = "contains 0"
    success, fails = X.verdict(signs, hok)
    assert not success and fails == [("nfl", "c2", "contains 0", True)]
    assert X.one_sport_only(fails) == {"c2": "cfb"}          # holds in college only
    hok["cfb"]["d_home"] = False                              # interval below, Holm not
    success, fails = X.verdict(signs, hok)
    assert not success and ("cfb", "d_home", "below", False) in fails
    assert X.one_sport_only(fails) == {"c2": "cfb", "d_home": "nfl"}
    signs["cfb"]["c2"] = "above"                              # fails in both: not a one-sport finding
    assert "c2" not in X.one_sport_only(X.verdict(signs, hok)[1])


def test_cuts_and_bins_are_the_registered_ones():
    assert len(X.CUTS) == 8 and X.N_BINS == 8
    t = X.Tab([dict(r, model=p) for r, p in zip(_rows(4, 8), (0.5, 0.549, 0.55, 0.97))], X.COLS)
    by_p = dict(zip(t.p["model"].tolist(), t.bin.tolist()))
    assert by_p == {0.5: 0, 0.549: 0, 0.55: 1, 0.97: 7}
    low = X.Tab([dict(r, model=0.2) for r in _rows(2, 9)], X.COLS)      # favourite p is max(p, 1-p)
    assert set(low.bin.tolist()) == {6}


def test_the_table_refuses_a_duplicated_game():
    rows = _rows(3, 10)
    with pytest.raises(ValueError):
        X.Tab(rows + [rows[0]], X.COLS)
