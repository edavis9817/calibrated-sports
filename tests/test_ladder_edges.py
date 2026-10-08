"""c-35 - the ladder pieces, on synthetic data.

No store is opened: every function under test is pure or takes rows. The
concordance is c-24's and is asserted to be IMPORTED, never redefined. Each of
the three Q2 verdicts is driven to by a planted population, so the registered
test is shown returning the OTHER answer on the other input.
"""
import ast
import os

import numpy as np
import pytest
from scipy.stats import norm

from core.fees import kalshi_fee
from research import ladder_edges as le
from research import ranking_calibration as rc

SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "research", "ladder_edges.py")
MID = "KXNFLREC-26SEP20AAABBB-AAAPLAYER1-5"


@pytest.fixture(autouse=True)
def _few_draws(monkeypatch):
    monkeypatch.setattr(le, "BOOT", 300)


# ---- the ladder as a distribution ---------------------------------------------

def test_pav_leaves_a_monotone_ladder_alone_and_repairs_an_inverted_one():
    assert le.pav_decreasing([0.9, 0.6, 0.3]) == [0.9, 0.6, 0.3]
    fixed = le.pav_decreasing([0.9, 0.4, 0.5, 0.1])
    assert fixed == pytest.approx([0.9, 0.45, 0.45, 0.1])
    assert all(a >= b for a, b in zip(fixed, fixed[1:]))


def test_cells_partition_and_the_realised_count_lands_in_exactly_one():
    lines, surv = [1.5, 2.5, 4.5], [0.8, 0.5, 0.1]
    q = le.ladder_cells(lines, surv)
    assert q == pytest.approx([0.2, 0.3, 0.4, 0.1]) and sum(q) == pytest.approx(1.0)
    assert [le.cell_index(lines, x) for x in (0, 1, 2, 3, 4, 5, 9)] == [0, 0, 1, 2, 2, 3, 3]


def test_unit_cells_exist_only_where_both_bounding_rungs_are_quoted():
    u = le.unit_cells([0.5, 1.5, 3.5], [0.9, 0.7, 0.2])
    assert u == pytest.approx({0: 0.1, 1: 0.2})          # x=2 and x=3 span a missing rung
    assert 0 not in le.unit_cells([1.5, 2.5], [0.7, 0.4])  # no 0.5 rung, no P(X = 0)


def test_mid_pit_moments_match_the_identity_and_dispersion_discriminates():
    rng = np.random.default_rng(0)
    lines = [1.5, 2.5, 3.5, 4.5, 5.5]
    true = [0.85, 0.62, 0.40, 0.22, 0.10]
    qt = le.ladder_cells(lines, true)
    cells = rng.choice(len(qt), size=200_000, p=qt)

    def D(surv):
        q = le.ladder_cells(lines, surv)
        t = np.array([le.mid_pit(q, j) for j in range(len(q))])[cells]
        return t.mean() - 0.5, (12 * (t - 0.5) ** 2).mean() - 12 * le.pit_var(q)

    m, d = D(true)
    assert abs(m) < 0.003 and abs(d) < 0.01               # the correct ladder: both zero
    narrow = list(norm.cdf(2.0 * norm.ppf(true)))          # same centre, too confident
    wide = list(norm.cdf(0.5 * norm.ppf(true)))
    assert D(narrow)[1] > 0.05                             # too narrow reads positive
    assert D(wide)[1] < -0.05                              # too wide reads negative


# ---- the shift -----------------------------------------------------------------

def test_fit_shift_recovers_a_planted_level_shift_and_reads_it_as_level():
    k = np.array([0.85, 0.65, 0.45, 0.25, 0.10])
    m = norm.cdf(norm.ppf(k) + 0.4)
    c, ss = le.fit_shift(k, m)
    assert c == pytest.approx(0.4, abs=1e-3) and ss < 1e-9
    assert le.fit_shift(k, norm.cdf(norm.ppf(k) - 0.7))[0] == pytest.approx(-0.7, abs=1e-3)


def test_a_pure_scale_disagreement_is_not_absorbed_by_the_shift():
    k = np.array([0.85, 0.65, 0.50, 0.35, 0.15])
    m = norm.cdf(2.0 * norm.ppf(k))
    ss0 = float(((m - k) ** 2).sum())
    c, ss1 = le.fit_shift(k, m)
    _a, b, ss2 = le.fit_shift_scale(k, m)
    assert abs(c) < 0.05 and ss1 / ss0 > 0.9               # level share near zero
    assert b == pytest.approx(2.0, abs=0.02) and ss2 / ss0 < 1e-4


# ---- executable cost -----------------------------------------------------------

def test_rung_exec_bills_the_whole_order_fee_and_breaks_even_exactly_at_its_view():
    ex = le.rung_exec(0.50, 0.51, 100, MID, "yes")
    assert ex["fee"] == pytest.approx(float(kalshi_fee(0.51, 100, "taker", 1)) / 100)
    assert ex["cost"] == pytest.approx(0.01 + ex["fee"])
    assert le.view_ev(0.50, ex["allin"], ex["be"], "yes") == pytest.approx(0.0, abs=1e-9)
    assert le.view_ev(0.50, ex["allin"], ex["be"] + 0.1, "yes") > 0
    assert le.view_ev(0.50, ex["allin"], 0.0, "yes") == pytest.approx(-ex["cost"])


def test_the_no_side_is_priced_against_its_own_mid_and_a_missing_vwap_has_no_cost():
    ex = le.rung_exec(0.80, 0.22, 100, MID, "no")          # NO mid 0.20, bought at 0.22
    assert ex["cost"] == pytest.approx(0.02 + ex["fee"]) and ex["be"] > 0
    assert le.view_ev(0.80, ex["allin"], ex["be"], "no") == pytest.approx(0.0, abs=1e-9)
    assert le.rung_exec(0.80, None, 100, MID, "no") is None
    assert le.rung_exec(0.99, 1.0, 100, MID, "yes") is None     # a price of 1 cannot be held
    assert le.rung_exec(0.99, 0.995, 100, MID, "yes")["allin"] < 1


# ---- grading -------------------------------------------------------------------

def test_duplicating_rows_inside_a_game_does_not_narrow_the_interval():
    rng = np.random.default_rng(1)
    games = [f"g{i}" for i in range(20) for _ in range(6)]
    x = rng.normal(0, 1, len(games)) + np.repeat(rng.normal(0, 1, 20), 6)
    a = le.Blocks(games).boot(le.mean_diff(x, np.zeros(len(x))))
    b = le.Blocks(games * 20).boot(le.mean_diff(np.tile(x, 20), np.zeros(len(x) * 20)))
    assert b["se"] == pytest.approx(a["se"], rel=0.02)
    assert b["games"] == 20


def test_pval_refuses_thin_and_zero_variance_intervals_and_bh_splits_correctly():
    assert le.pval({"est": 1.0, "se": 0.1, "games": 4}) == 1.0
    assert le.pval({"est": 1.0, "se": 0.0, "games": 40}) == 1.0
    assert le.pval({"est": 0.3, "se": 0.1, "games": 40}) < 0.01
    assert le.bh([0.001, 0.5, 0.04, 0.9], q=0.10) == {0, 2}
    assert le.bh([0.2, 0.5], q=0.10) == set()


def test_the_concordance_is_c24s_and_the_store_is_only_ever_opened_read_only():
    tree = ast.parse(open(SRC, encoding="utf-8").read())
    defs = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    calls = [n for n in ast.walk(defs["wauc"]) if isinstance(n, ast.Attribute) and n.attr == "wauc"]
    assert calls, "ladder_edges.wauc must delegate to research.ranking_calibration.wauc"
    p, y, s = np.array([.1, .9, .4, .6]), np.array([0., 1., 0., 1.]), np.array([0, 0, 1, 1])
    assert le.wauc(p, y, s) == rc.wauc(p, y, s) == 1.0
    connects = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
                and isinstance(n.func, ast.Attribute) and n.func.attr == "connect"]
    assert len(connects) == 1                               # the one inside ro()
    inside = [n for n in ast.walk(defs["ro"]) if n is connects[0]]
    assert inside and "mode=ro" in ast.unparse(connects[0])


# ---- Q2 reaches each of its verdicts -------------------------------------------

def _population(kind, seed=2, games=30, per_game=8):
    """kind: 'ladder' - the truth is the market shifted per ladder, the model
    sees the shift plus rung noise; 'rung' - the truth disagrees with the market
    by a sign that ALTERNATES rung to rung, so it has no level in it; 'null' - the market is right."""
    rng = np.random.default_rng(seed)
    lines = [1.5, 2.5, 3.5, 4.5, 5.5, 6.5]
    rows = []
    for g in range(games):
        for p in range(per_game):
            a = rng.normal(0, 0.4)
            step = 1.0 if kind == "rung" else 0.8        # steep enough that the alternation stays monotone
            zk = np.array([a - step * (L - 4.0) for L in lines])
            if kind == "ladder":
                u = rng.normal(0, 0.6)
                zt, zm = zk + u, zk + u + rng.normal(0, 0.8, len(lines))
            elif kind == "rung":
                v = rng.choice([-1.0, 1.0])
                tilt = 0.45 * v * np.array([(-1.0) ** i for i in range(len(lines))])
                zt = zk + tilt
                zm = zt.copy()
            else:
                zt, zm = zk, zk + rng.normal(0, 0.6) + rng.normal(0, 0.5, len(lines))
            assert np.all(np.diff(zt) < 0)
            y = rng.normal() < zt                            # one latent draw: a coherent ladder
            x = 1 + int(y.sum())
            for L, k, m, yy in zip(lines, norm.cdf(zk), norm.cdf(zm), y):
                rows.append({"mid": MID, "oid": f"{g}-{p}-{L}", "gsis": f"p{g}-{p}", "stat": "receptions",
                             "line": L, "game": f"g{g:02d}", "week": 2, "pos": "WR", "y": float(yy),
                             "x": float(x), "bid": k - 0.01, "ask": k + 0.01, "k": float(k),
                             "depth": {}, "m": float(m)})
    return rows


def _q2(kind, **kw):
    ladders = le.build_ladders(_population(kind, **kw))
    le.q1(ladders, [], lambda *_: None)                      # sets each ladder's mid-PIT
    return le.q2(ladders, lambda *_: None)


def test_q2_says_ladder_level_when_the_truth_is_a_ladder_shift_seen_through_rung_noise():
    r = _q2("ladder")
    assert r["ordering"]["all"]["verdict"].startswith("ladder-level disagreement orders")
    assert r["ordering"]["all"]["dA"]["lo"] > 0
    assert r["decomposition"] in ("mostly not level", "undetermined")   # rung noise dominates the gap


def test_q2_says_rung_level_when_the_disagreement_alternates_and_has_no_level_in_it():
    r = _q2("rung", games=40, per_game=10)
    assert r["ordering"]["all"]["verdict"].startswith("the rung's own gap orders")
    assert r["level_share"]["est"] < 0.2


def test_q2_says_two_nulls_when_the_market_is_right():
    r = _q2("null")
    assert r["ordering"]["all"]["verdict"].startswith("a comparison of two nulls")


# ---- Q3 on a planted book -------------------------------------------------------

def test_q3_prices_only_rungs_with_depth_and_the_best_of_n_gap_is_never_negative():
    rows = _population("null", games=12, per_game=4)
    for i, r in enumerate(rows):
        if r["game"] != "g00":                              # one game has no depth at all
            wide = 0.005 if abs(r["k"] - 0.5) < 0.2 else 0.03
            r["depth"] = {"buy_yes": {"age": 30, "touch": r["k"] + wide, "size": 500,
                                      "100": min(r["k"] + wide, 0.999), "500": None},
                          "buy_no": {"age": 30, "touch": 1 - r["k"] + wide, "size": 500,
                                     "100": min(1 - r["k"] + wide, 0.999), "500": None}}
    res = le.q3(le.build_ladders(rows), lambda *_: None)
    yes = res["100"]["yes"]
    assert yes["read"] and yes["games"] == 11               # the depthless game is not priced
    assert res["500"]["yes"]["read"] is False               # no VWAP at 500 anywhere: not read
    assert min(yes["best_minus_pp"].values()) >= 0
    assert yes["rules"]["central"]["be_p50"] < yes["rules"]["near 0.15"]["be_p50"]
