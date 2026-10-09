"""c-48: the three figures and the reading of them.

Pure functions only - no store is opened."""
import csv
import json

import numpy as np
import pytest

from research import c37_figures as cf


def _rb(n_games=40, per=6, seed=0, slope=1.0):
    """Synthetic bench rungs whose outcome follows logit(Y) with a known slope."""
    rng = np.random.default_rng(seed)
    rows = []
    for g in range(n_games):
        for i in range(per):
            m = float(rng.uniform(0.2, 0.8))
            q = 1 / (1 + np.exp(-slope * np.log(m / (1 - m))))
            rows.append({"season": 2024, "game": f"g{g}", "gsis": f"p{i}", "line": 40.5 + i,
                         "p_bench": float(rng.normal(0.5, 0.01)), "n_bench": 1,
                         "Y": m, "S0": m, "S1": m, "y": float(rng.random() < q)})
    return rows


def test_concentration_bounds_are_inclusive_and_the_share_can_be_low():
    share, sd = cf.concentration([0.45, 0.55, 0.50, 0.4499, 0.5501])
    assert share == pytest.approx(0.6)
    assert sd == pytest.approx(np.std([0.45, 0.55, 0.50, 0.4499, 0.5501]))
    assert cf.concentration([0.30, 0.70, 0.62])[0] == 0.0       # the other answer


def test_platt_slope_recovers_a_planted_slope_and_tells_two_apart():
    one = cf.figures(_rb(n_games=400, slope=1.0))["slope"]
    none = cf.figures(_rb(n_games=400, slope=0.0))["slope"]
    assert abs(one - 1.0) < 0.15
    assert abs(none) < 0.15


def test_status_is_reproduced_inside_the_tolerance_and_moved_outside_it():
    assert cf.status(0.99475, 0.995) == "REPRODUCED"
    assert cf.status(0.989, 0.995) == "MOVED"
    assert cf.status(0.9955, 0.995) == "MOVED"                    # strict: the edge does not round to 0.995


def test_a_moved_figure_can_keep_or_lose_its_argument():
    iv = {"gap": {"hi": -0.05}, "slope": {"hi": 0.1}}
    fig = dict(cf.PUBLISHED, share=0.989, gap=-0.07)
    v = cf.verdicts(fig, iv)
    assert v["F1"]["status"] == "MOVED" and v["F1"]["argument_survives"]
    assert v["F2"]["status"] == "REPRODUCED" and v["F3"]["status"] == "REPRODUCED"
    v = cf.verdicts(dict(fig, share=0.80), {"gap": {"hi": 0.01}, "slope": {"hi": 0.9}})
    assert not v["F1"]["argument_survives"]
    assert not v["F2"]["argument_survives"] and not v["F3"]["argument_survives"]


def test_f2_needs_both_of_its_rows():
    iv = {"gap": {"hi": -0.05}, "slope": {"hi": 0.1}}
    v = cf.verdicts(dict(cf.PUBLISHED, over_rate=0.470, gap=-0.06), iv)
    assert v["rows"]["mean_model"]["status"] == "REPRODUCED"
    assert v["F2"]["status"] == "MOVED"


def test_population_check_names_what_moved():
    same = {k: cf.C37_COUNTS[k] for k in cf.C37_COUNTS}
    assert cf.population_check(same)["same_as_c37"]
    moved = cf.population_check(dict(same, RB=18700))
    assert not moved["same_as_c37"] and moved["differences"]["RB"] == {"c37": 18666, "now": 18700}


def test_the_interval_does_not_narrow_when_rows_are_duplicated_inside_their_game():
    rb = _rb()
    a = cf.boot(rb, draws=300, seed=1)
    b = cf.boot([dict(r, line=r["line"] + 100 * k) for r in rb for k in range(20)], draws=300, seed=1)
    for k in ("share", "mean_model", "gap"):
        assert b[k]["se"] == pytest.approx(a[k]["se"], rel=1e-9)   # games are the sample, not rungs


def test_boot_reads_its_constants_at_call_time(monkeypatch):
    rb = _rb(n_games=10)
    monkeypatch.setattr(cf, "SEED", 1)
    a = cf.boot(rb, draws=50)
    monkeypatch.setattr(cf, "SEED", 2)
    assert cf.boot(rb, draws=50)["share"] != a["share"] or cf.boot(rb, draws=50)["gap"] != a["gap"]


def test_other_markets_reads_the_committed_shares_and_refuses_a_missing_key(tmp_path):
    got = {o["market"]: o for o in cf.other_markets()}
    assert set(got) == {"receiving_yards", "receptions", "rush_attempts"}
    assert got["receptions"]["share"] < 0.5 < got["rush_attempts"]["share"] < 0.9 < got["receiving_yards"]["share"]
    d = tmp_path / "research" / "results"
    d.mkdir(parents=True)
    (d / "residual_given_line.json").write_text(json.dumps({"descriptive": {"RB": {"n": 1}}}))
    (d / "residual_two_markets.json").write_text(json.dumps({"descriptive": {}}))
    with pytest.raises(SystemExit):
        cf.other_markets(str(tmp_path))


def test_scratch_comparison_reports_absent_identical_and_different(tmp_path):
    rb = _rb(n_games=5)
    assert cf.compare_scratch(rb, None)["state"] == "not requested"
    assert cf.compare_scratch(rb, str(tmp_path / "gone.csv"))["state"] == "ABSENT"
    cols = ["season", "game", "gsis", "line", "y", "p_bench", "Y", "S0", "S1"]

    def write(rows, name):
        with open(tmp_path / name, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(cols)
            w.writerows([[r[c] for c in cols] for r in rows])
        return str(tmp_path / name)
    non_bench = dict(rb[0], game="other", p_bench=None)
    same = cf.compare_scratch(rb, write(rb + [non_bench], "same.csv"))
    assert same["identical_keys"] and same["max_abs_diff_Y"] == 0.0 and same["scratch_rows"] == len(rb) + 1
    changed = [dict(r) for r in rb[1:]]
    changed[0]["Y"] = changed[0]["Y"] + 0.25
    diff = cf.compare_scratch(rb, write(changed, "diff.csv"))
    assert not diff["identical_keys"] and diff["only_regenerated"] == 1
    assert diff["max_abs_diff_Y"] == pytest.approx(0.25)
