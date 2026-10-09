"""c-47: the two-market residual frame, on synthetic rows only. No store is opened."""
import numpy as np
import pytest

from research import residual_given_line as c41
from research import residual_two_markets as r2


def synth(n_games=120, per_game=8, effect=0.0, cand="team_total", seed=0, qb=False):
    """Player-games with two rungs each, one shared uniform per player-game."""
    rng = np.random.default_rng(seed)
    recs = []
    for g in range(n_games):
        season = r2.SEASONS[g % 3]
        for k in range(per_game):
            f = {c: float(rng.normal()) for c in r2.CANDIDATES}
            u = rng.random()
            for j, line in enumerate((3.5, 4.5)):
                p = 0.55 - 0.1 * j
                y = 1.0 if u < min(0.99, max(0.01, p + effect * f[cand])) else 0.0
                recs.append({"oid": f"{g}-{k}-{j}", "gsis": f"p{g}-{k}", "event": f"g{g}",
                             "season": season, "pos": "QB" if (qb and k == 0) else "RB",
                             "line": line, "p": p, "y": y, "n_bench": 3 - j, "n_all": 5, **f})
    return recs


def run(recs):
    prep = r2.prepare(recs, [r2.SEED, 0])
    r2.set_y(prep, [d["y"] for d in recs])
    return prep, r2.analyse_market(prep)


def test_one_market_is_42_registered_tests_and_qb_adds_six_descriptive():
    _, tests = run(synth())
    assert sum(t["registered"] for t in tests) == r2.N_PER_MARKET
    assert len(tests) == r2.N_PER_MARKET + 4
    _, tests = run(synth(qb=True))
    assert len(tests) == r2.N_PER_MARKET + 4 + 6
    assert 2 * r2.N_PER_MARKET == r2.N_REGISTERED
    assert r2.N_REGISTERED + 8 + 6 == r2.N_SPECS


def test_the_candidate_set_is_c41s_and_has_no_seventh():
    assert r2.CANDIDATES is c41.CANDIDATES and len(r2.CANDIDATES) == 6
    assert list(r2.MARKETS) == ["receptions", "rush_attempts"]
    assert "receiving_yards" not in r2.MARKETS


def test_rm_outcome_is_the_rb_outcome_of_the_same_rung():
    prep, _ = run(synth())
    rb, rm = prep["RB"], prep["RM"]
    assert len(rm["y"]) == rb["n_pg"]
    assert np.array_equal(rm["y"], rb["y"][rm["rb_index"]])
    assert np.allclose(rm["p"], rb["p"][rm["rb_index"]])


def test_a_planted_effect_is_found_and_a_null_is_not():
    _, tests = run(synth(n_games=300, effect=0.10))
    hit = r2.find(tests, None, "coef", "RB", "pooled", "team_total")
    miss = r2.find(tests, None, "coef", "RB", "pooled", "log_line")
    assert hit["p"] * r2.N_REGISTERED < 0.05 and hit["est"] > 0.05
    assert miss["p"] > 0.001


def _pooled(est, se):
    return {"est": est, "se": se, "bonf_lo": est - r2.Z_BONF * se, "bonf_hi": est + r2.Z_BONF * se}


def test_excluded_needs_the_interval_and_both_power_conditions():
    full = {d: 0.99 for d in r2.SIZES}
    # tight and powered: excluded at the smallest size
    assert r2.excluded_at(_pooled(0.0, 0.004), full)[0] == 0.020
    # the same interval with pre-run power under 0.80 at 0.020: not excluded there
    weak = {0.020: 0.68, 0.033: 0.99, 0.045: 0.99}
    assert r2.excluded_at(_pooled(0.0, 0.004), weak)[0] == 0.033
    # powered on paper, but the realized SE is too wide for 0.020 and 0.033
    assert r2.excluded_at(_pooled(0.0, 0.009), full)[0] == 0.045
    # an estimate near the size: nothing is excluded
    smallest, per = r2.excluded_at(_pooled(0.040, 0.004), full)
    assert smallest is None and not any(v["excluded"] for v in per.values())
    # no pre-run power on file is not permission
    assert r2.excluded_at(_pooled(0.0, 0.004), {})[0] is None


def _fake(market, est, se, p_holm, brier):
    tests = []
    for c in r2.CANDIDATES:
        for cut in ["pooled"] + [str(s) for s in r2.SEASONS]:
            tests.append({"market": market, "kind": "coef", "pop": "RB", "cut": cut, "cand": c,
                          "p_holm": p_holm, **_pooled(est, se)})
        tests.append({"market": market, "kind": "brier", "pop": "RB", "cut": "2024+2025",
                      "cand": c, **brier})
    return tests


def test_the_verdict_reaches_all_three_states():
    power = {m: {c: {f"{d:.3f}": {"coef_pooled_bound": 0.95, "detected": 0.5}
                     for d in r2.SIZES} for c in r2.CANDIDATES} for m in r2.MARKETS}
    nothing = {"est": 0.0001, "hi": 0.0003, "mde": 0.0002}
    gain = {"est": -0.0010, "hi": -0.0004, "mde": 0.0005}
    tests = _fake("receptions", 0.050, 0.005, 0.001, gain) \
        + _fake("rush_attempts", 0.000, 0.004, 1.0, nothing)
    v = r2.verdict(tests, power)
    assert v["receptions"]["form_gap"]["state"] == "detected"
    assert v["rush_attempts"]["form_gap"]["state"] == "excluded at 0.020"
    assert v["rush_attempts"]["form_gap"]["may_say_rules_out_0020"] is True
    tests = _fake("receptions", 0.030, 0.008, 0.5, nothing) \
        + _fake("rush_attempts", 0.000, 0.004, 1.0, nothing)
    v = r2.verdict(tests, power)
    assert v["receptions"]["form_gap"]["state"] == "unresolved"
    assert v["receptions"]["form_gap"]["may_say_rules_out_0020"] is False
    assert v["receptions"]["form_gap"]["unresolved_at"] == list(r2.SIZES)
    # the coefficient alone is not detection: condition 2 must hold too
    tests = _fake("receptions", 0.050, 0.005, 0.001, nothing) \
        + _fake("rush_attempts", 0.000, 0.004, 1.0, nothing)
    assert r2.verdict(tests, power)["receptions"]["form_gap"]["state"] == "unresolved"


def test_simulated_outcomes_share_one_draw_per_player_game_and_carry_the_effect():
    recs = synth(n_games=200)
    prep = r2.prepare(recs, [r2.SEED, 0])
    rng = np.random.default_rng(1)
    y = r2.simulate_y(prep, None, 0.0, rng)
    rb = prep["RB"]
    # the lower line (higher p) clears whenever the higher line does
    lo, hi = y[0::2], y[1::2]
    assert np.all(lo >= hi) and 0.4 < y.mean() < 0.6
    assert len(np.unique(rb["pg"])) == rb["n_pg"] == len(recs) // 2
    z = prep["cuts"][0][2]["z"]["team_total"][0]
    y = np.mean([r2.simulate_y(prep, "team_total", 0.10, rng) for _ in range(50)], axis=0)
    assert np.corrcoef(z, y - rb["p"])[0, 1] > 0.1


def test_unregistered_stat_column_is_refused():
    with pytest.raises(ValueError):
        r2.load_history(None, {}, "receiving_yards")
