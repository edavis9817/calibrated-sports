"""c-43 - the interval pieces, on synthetic ladders.

No store and no cache is opened. Each verdict function is driven to every one
of its answers, and the calibration statistic is shown reading BOTH ways on
planted populations, so a pass is not a statement about a placeholder.
"""
import ast
import json
import os

import numpy as np
import pytest

from research import interval_mass as im
from research import ladder_edges as le

SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "research", "interval_mass.py")


def rung(line, k, x=None, game="g1", gsis="p1", stat="receptions", spread=0.02, depth=None):
    r = {"mid": f"KXNFLREC-26SEP20AAABBB-{gsis}-{int(line + 0.5)}", "gsis": gsis, "stat": stat,
         "game": game, "week": 3, "line": line, "k": k, "bid": k - spread / 2, "ask": k + spread / 2,
         "depth": depth or {}, "m": 0.123}
    if x is not None:
        r["x"], r["y"] = x, float(x > line)
    return r


def ladder_rows(ks, x=None, first=1.5, **kw):
    return [rung(first + n, k, x, **kw) for n, k in enumerate(ks)]


# ---- the interval --------------------------------------------------------------

def test_clears_four_stays_under_eight_is_the_rungs_3p5_and_7p5():
    assert [im.hit(3.5, 7.5, x) for x in (3, 4, 5, 6, 7, 8)] == [0, 1, 1, 1, 1, 0]
    rows = ladder_rows([0.9, 0.8, 0.6, 0.45, 0.3, 0.2, 0.1], x=7)
    ivs = im.intervals(im.build(rows))
    assert len(ivs) == 7 * 6 // 2
    v = next(v for v in ivs if (v["i"], v["j"]) == (2, 6))        # lines 3.5 and 7.5
    assert v["w"] == 4 and v["p"] == pytest.approx(0.5) and v["h"] == 1.0
    assert sum(v["h"] for v in ivs if v["w"] == 1) == 1           # one unit cell holds the count


def test_bins_and_positions_cover_every_case():
    assert [im.bin_of(p) for p in (-0.01, 0.049, 0.05, 0.15, 0.25, 0.4, 0.5, 0.6, 0.99)] == \
        [0, 0, 1, 2, 3, 4, 5, 6, 6]
    assert im.position(None, 0, 1) == "no central rung"
    assert im.position(3, 1, 5) == "straddle"
    assert im.position(3, 5, 6) == "upper wing"
    assert im.position(3, 0, 1) == "lower wing"
    assert im.position(3, 3, 4) == "adjacent" and im.position(3, 2, 3) == "adjacent"


def test_every_registered_cell_is_present_even_when_empty():
    fam, pre = im.test_cells(im.intervals(im.build(ladder_rows([0.8, 0.5, 0.2], x=3))))
    assert len(fam) == 72 and len(pre) == 9
    assert set(pre) == set(im.PRE_DIRECTION)
    assert len(fam["receptions|w=1|all"]) == 2 and len(fam["rush_attempts|w=3|all"]) == 0


# ---- not a model unit, and the MDE reads no outcome -----------------------------

def test_load_drops_the_model_column_and_the_mde_path_drops_outcomes(tmp_path):
    rows = ladder_rows([0.8, 0.5, 0.2], x=3)
    cache = tmp_path / "rows.json"
    cache.write_text(json.dumps({"rows": rows, "census": {}, "fees": {"snapshot:KXNFLREC": ["quadratic", 1]}}))
    with_out, _ = im.load(str(cache), outcomes=True)
    assert all("m" not in r and "x" in r for r in with_out)
    without, _ = im.load(str(cache), outcomes=False)
    assert all(not ({"m", "x", "y"} & set(r)) for r in without)
    assert all(v["h"] is None for v in im.intervals(im.build(without)))


def test_a_wrong_fee_type_or_population_stops(tmp_path):
    cache = tmp_path / "rows.json"
    cache.write_text(json.dumps({"rows": ladder_rows([0.8, 0.5]), "census": {},
                                 "fees": {"live:KXNFLREC": ["quadratic_with_maker_fees", 1]}}))
    with pytest.raises(SystemExit):
        im.load(str(cache), outcomes=True)
    with pytest.raises(SystemExit):
        im.check_shape(im.build(ladder_rows([0.8, 0.5])))
    assert im.check_shape(im.build(ladder_rows([0.8, 0.5])), expect=(1, 2, 1)) == (1, 2, 1)


def test_the_script_reads_no_model_and_opens_no_store():
    tree = ast.parse(open(SRC).read())
    mods = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            mods |= {f"{node.module}.{a.name}" for a in node.names}
        elif isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant):
            assert node.slice.value != "m", "the model column is read"
        elif isinstance(node, ast.Attribute):
            assert node.attr not in ("connect", "ro", "extract", "DB_PATH"), node.attr
    assert not any(m.split(".")[0] in ("models", "sqlite3", "config") or "walkforward" in m
                   for m in mods), mods


# ---- the null simulation ---------------------------------------------------------

def test_null_sd_matches_the_binomial_on_independent_ladders():
    rows = []
    for n in range(400):
        rows += ladder_rows([0.7, 0.4], gsis=f"p{n}", game=f"g{n % 40}")
    ladders = im.build(rows)
    ivs = im.intervals(ladders)
    sd = im.null_sd(ladders, ivs, {"c": np.arange(len(ivs))}, sims=4000)["c"]
    assert sd == pytest.approx((0.3 * 0.7 / 400) ** 0.5, rel=0.08)


def test_null_sd_does_not_count_overlapping_intervals_of_one_ladder_as_independent():
    rows = []
    for n in range(200):
        rows += ladder_rows([0.9, 0.75, 0.55, 0.35, 0.2, 0.1], gsis=f"p{n}", game=f"g{n % 20}")
    ladders = im.build(rows)
    ivs = im.intervals(ladders)
    idx = np.array([n for n, v in enumerate(ivs) if v["w"] == 3])
    sd = im.null_sd(ladders, ivs, {"c": idx}, sims=4000)["c"]
    p = np.mean([ivs[n]["p"] for n in idx])
    assert sd > 1.2 * (p * (1 - p) / len(idx)) ** 0.5       # 3 overlapping intervals per ladder


# ---- the block bootstrap -----------------------------------------------------------

def test_duplicating_rows_inside_their_own_game_does_not_narrow_the_interval():
    rng = np.random.default_rng(1)
    v = rng.normal(0, 1, 300)
    g = [f"g{n % 15}" for n in range(300)]
    a = im.boot_mean(v, g, "t")
    b = im.boot_mean(np.tile(v, 20), g * 20, "t")
    assert b["se"] == pytest.approx(a["se"], rel=1e-9) and b["games"] == 15


def test_a_zero_variance_or_thin_interval_is_p_one_not_p_zero():
    r = im.boot_mean(np.full(60, 0.03), [f"g{n % 6}" for n in range(60)], "z")
    assert r["se"] < im.TOL and im.pval(r) == 1.0
    thin = im.boot_mean([1.0, 2.0, 3.0, 9.0], ["a", "a", "b", "c"], "thin")
    assert thin["games"] == 3 and im.pval(thin) == 1.0


def test_side_by_side_cells_do_not_share_draws():
    v, g = np.random.default_rng(2).normal(0, 1, 400), [f"g{n % 20}" for n in range(400)]
    assert im.boot_mean(v, g, "one")["se"] != im.boot_mean(v, g, "two")["se"]
    assert im.boot_mean(v, g, "one")["se"] == im.boot_mean(v, g, "one")["se"]      # and are reproducible


# ---- verdicts: every answer is reachable -------------------------------------------

def test_calibration_verdict_reaches_all_three():
    assert im.calibration_verdict([]) == "calibrated at this resolution"
    assert im.calibration_verdict([(-0.06, 0.04)]) == "mispriced"
    assert im.calibration_verdict([(0.02, 0.04), (0.01, None)]) == \
        "a deviation survives correction below its MDE"


def test_precheck_reading_reaches_all_four():
    def r(lo, hi, games=20):
        return {"lo": lo, "hi": hi, "games": games}
    assert im.precheck_reading(r(-0.05, -0.01), -1) == "confirmed in direction"
    assert im.precheck_reading(r(0.01, 0.05), -1) == "CONTRADICTED"
    assert im.precheck_reading(r(-0.02, 0.03), -1) == "not detected"
    assert im.precheck_reading(r(0.01, 0.05), +1) == "confirmed in direction"
    assert im.precheck_reading(r(-0.05, -0.01), +1) == "CONTRADICTED"
    assert im.precheck_reading(r(None, None), -1) == "not read"
    assert im.precheck_reading(r(0.01, 0.05, games=3), +1) == "not read"


def _population(true_scale, n_games=40, per_game=12, seed=0):
    """Ladders that all quote one survival curve; counts drawn from a curve whose
    spread is `true_scale` times the quoted one. scale 1 = a correct ladder."""
    from scipy.stats import norm
    rng = np.random.default_rng(seed)
    quoted = [0.93, 0.80, 0.60, 0.40, 0.22, 0.10, 0.04]
    true = list(norm.cdf(np.array(norm.ppf(quoted)) / true_scale))
    q = le.ladder_cells(None, true)
    rows = []
    for n in range(n_games * per_game):
        x = int(rng.choice(len(q), p=q)) + 1          # cell j -> a count just above rung j-1
        rows += ladder_rows(quoted, x=x, gsis=f"p{n}", game=f"g{n % n_games}")
    return im.build(rows)


def _run(ladders):
    ivs = im.intervals(ladders)
    mde = {}
    t = im.mde_table(ladders, ivs, out=lambda *_: None)
    mde.update(t["family"])
    mde.update({"pre|" + k: v for k, v in t["precheck"].items()})
    return im.calibration(ladders, ivs, mde, out=lambda *_: None)


def test_calibration_reads_a_correct_ladder_as_calibrated_and_a_narrow_one_as_not(monkeypatch):
    monkeypatch.setattr(im, "BOOT", 400)
    monkeypatch.setattr(im, "SIMS", 400)
    ok = _run(_population(1.0))
    assert ok["verdict"] == "calibrated at this resolution" and ok["n_bh"] == 0
    assert ok["precheck_reading"]["straddle"] == "not detected"
    narrow = _run(_population(1.6))                    # the ladder is far too narrow
    assert narrow["verdict"] == "mispriced" and narrow["n_bh"] > 0
    # the pre-registered implication, shown firing: straddling intervals over-priced
    assert narrow["precheck_reading"]["straddle"] == "confirmed in direction"
    assert narrow["precheck"]["S_all"]["est"] < 0
    wide = _run(_population(0.6))                      # too wide: the stated direction is contradicted
    assert wide["precheck_reading"]["straddle"] == "CONTRADICTED"


# ---- coherence -----------------------------------------------------------------------

def test_coherence_counts_an_inverted_and_a_crossed_pair():
    rows = ladder_rows([0.80, 0.50, 0.50, 0.58], x=2, spread=0.02)
    ladders = im.build(rows)
    res = im.coherence(ladders, im.intervals(ladders), out=lambda *_: None)
    t = res["total"]
    assert t["n"] == 6
    assert t["C1_negative"] == 2 and t["C2_zero"] == 1       # (1,3) and (2,3) negative; (1,2) zero
    assert t["C3_crossed_at_touch"] == 2                     # ask 0.51 < bid 0.57
    assert res["crossed_fee"] == {"no depth at 100 on a leg": 2}
    assert res["ladders_with_a_negative_interval"] == 1
    clean = im.build(ladder_rows([0.8, 0.5, 0.2], x=2))
    assert im.coherence(clean, im.intervals(clean), out=lambda *_: None)["total"]["C1_negative"] == 0


def test_unimodal():
    assert im.unimodal([0.1, 0.2, 0.3, 0.2, 0.1]) and im.unimodal([0.3, 0.2, 0.2, 0.1])
    assert not im.unimodal([0.1, 0.3, 0.2, 0.25, 0.1])


# ---- cost ----------------------------------------------------------------------------

def _depth(k, slip=0.01):
    return {"buy_yes": {"age": 30, "touch": k + 0.01, "size": 400, "100": k + slip, "500": k + 2 * slip,
                        "1000": None, "total": 900},
            "buy_no": {"age": 30, "touch": 1 - k + 0.01, "size": 60, "100": 1 - k + slip, "500": None,
                       "1000": None, "total": 300}}


def test_two_leg_cost_is_the_sum_of_its_legs_and_the_500_leg_without_depth_is_not_priced():
    ks = [0.85, 0.6, 0.35, 0.15]
    rows = [rung(1.5 + n, k, x=3, depth=_depth(k)) for n, k in enumerate(ks)]
    l = im.build(rows)[0]
    buy = im.pair_cost(l, 1, 3, 100, "buy")
    yes = le.rung_exec(0.6, 0.61, 100, rows[1]["mid"], "yes")
    no = le.rung_exec(0.15, 0.86, 100, rows[3]["mid"], "no")
    assert buy["cost"] == pytest.approx(yes["cost"] + no["cost"])
    assert buy["capital"] == pytest.approx(yes["allin"] + no["allin"])
    assert buy["cost"] > yes["cost"] > 0 and buy["thin"] == 60
    assert im.pair_cost(l, 1, 3, 500, "buy") is None           # no NO-side depth at 500
    sell = im.pair_cost(l, 1, 3, 100, "sell")
    assert sell["lower_leg"] == pytest.approx(le.rung_exec(0.6, 0.41, 100, rows[1]["mid"], "no")["cost"])


def test_nearest_single_picks_the_side_whose_mid_is_nearest_the_value():
    ks = [0.85, 0.6, 0.35, 0.15]
    l = im.build([rung(1.5 + n, k, x=3, depth=_depth(k)) for n, k in enumerate(ks)])[0]
    assert im.nearest_single(l, 0.45, 100)["side_mid"] == pytest.approx(0.40)   # NO on the 0.60 rung
    assert im.nearest_single(l, 0.84, 100)["side_mid"] == pytest.approx(0.85)
    assert im.nearest_single(l, 0.45, 1000) is None


def test_priced_pairs_net_pays_two_on_a_hit_when_bought_and_zero_when_sold():
    ks = [0.85, 0.6, 0.35, 0.15]
    ladders = im.build([rung(1.5 + n, k, x=3, depth=_depth(k)) for n, k in enumerate(ks)])
    ivs = im.intervals(ladders)
    v = [x for x in ivs if (x["i"], x["j"]) == (1, 2)]          # 2.5 < X < 3.5, X = 3: a hit
    b = im.priced_pairs(ladders, v, 100, "buy")[0]
    s = im.priced_pairs(ladders, v, 100, "sell")[0]
    assert b["net"] == pytest.approx(2 - b["capital"]) and s["net"] == pytest.approx(0 - s["capital"])
    assert b["value"] == pytest.approx(0.25) and s["value"] == pytest.approx(0.75)
    assert im.break_even(0.25, 0.04) > 0 and im.break_even(0.0, 0.04) is None


# ---- addendum 1: the all-zero cell ---------------------------------------------------

def test_a_cell_in_which_nothing_hit_survives_on_the_bootstrap_se_and_not_on_the_null_se():
    games = [f"g{n}" for n in range(20)]
    p = np.linspace(0.06, 0.10, 20)
    r = im.boot_mean(0.0 - p, games, "zero-hit")              # 20 intervals, none hit
    r.update(realised=0.0, mde_prerun=2.8 * 0.06)             # ~ sqrt(.08 * .92 / 20)
    assert im.pval(r) < 1e-6                                   # the bootstrap calls it certain
    res = im.posthoc_null_se(["cell"], [r], [0], out=lambda *_: None)
    assert res["n_bh"] == 0 and res["all_zero_cells"] == ["cell"]
    assert res["smallest_p"][0][0] > 0.1
