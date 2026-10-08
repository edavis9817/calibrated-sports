"""c-40 - the counterfactual ledger. Run: pytest -q tests/test_counterfactual_ledger.py

What these guard, in the order they would hurt:

- the REPLICA of the fit's arithmetic is the model's arithmetic. A counterfactual
  on a copy that has drifted from `models.baseline` describes a model nobody ran;
- a "minimum perturbation" reaches the target and nothing smaller does;
- the registered verdict can come out all three ways (CLAUDE.md, falsifiability);
- a game-block interval does not narrow when rows are duplicated inside a game.
"""
import math
import sqlite3
import time

import numpy as np
import pytest

import config
import store
from models import baseline
from research import counterfactual_ledger as CL


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setattr(config, "RAW_DIR", str(tmp_path / "raw"))
    monkeypatch.setattr(config, "STORAGE_DIR", str(tmp_path))
    store.init_db()
    now = time.time()
    past = now - 86400 * 400
    games = [("nfl", f"2025_{w:02d}_DET_GB", "v1", 2025, w, "2025-09-07",
              "GB", "DET", past + w * 86400 * 7, "test", now) for w in range(1, 11)]
    store.replace_rows(
        "nfl_games",
        ("sport", "game_id", "data_version", "season", "week", "gameday",
         "home_team", "away_team", "kickoff_ts", "source", "ingested_ts"), games)
    rows = []
    for p, (team, base) in enumerate((("DET", 4.0), ("DET", 2.0), ("GB", 5.0), ("GB", 1.0),
                                      ("DET", 6.0), ("GB", 3.0)), start=1):
        rows += [("nfl", f"00-000000{p}", 2025, w, "REG", "v1", f"WR {p}", "WR", team,
                  float(base + (w % 3)), "test", now) for w in range(1, 11)]
    store.replace_rows(
        "nfl_player_week",
        ("sport", "gsis_id", "season", "week", "season_type", "data_version",
         "player_name", "position", "team", "receptions", "source", "ingested_ts"), rows)
    yield now


@pytest.mark.parametrize("gsis,team", [("00-0000001", "DET"), ("00-0000001", "GB"),
                                       ("00-0000004", "GB"), ("00-0000005", "DET")])
def test_the_replica_is_the_model(env, gsis, team):
    """Mean, dispersion, weight and P(over) of the replica equal the fit's own,
    including with the team-change penalty fired."""
    con = sqlite3.connect(f"file:{config.DB_PATH}?mode=ro", uri=True)
    fit = baseline.fit_player_stat(con, gsis, "receptions", 2026, env, "WR", team,
                                   prior_seasons=(2025, 2026))
    c = CL.components(con, gsis, "receptions", 2026, env, "WR", team)
    con.close()
    for line in (0.5, 3.5, 6.5):
        assert CL.check_replica(c, fit, line) == fit.dist.prob_over(line)
    assert c["team_changed"] == (team == "GB" and gsis == "00-0000001")


def test_check_replica_raises_on_a_drifted_copy(env):
    """The gate can fail: a component that is not the model's is refused."""
    con = sqlite3.connect(f"file:{config.DB_PATH}?mode=ro", uri=True)
    fit = baseline.fit_player_stat(con, "00-0000001", "receptions", 2026, env, "WR", "DET",
                                   prior_seasons=(2025, 2026))
    c = CL.components(con, "00-0000001", "receptions", 2026, env, "WR", "DET")
    con.close()
    with pytest.raises(CL.GateError):
        CL.check_replica(dict(c, own_mean=c["own_mean"] * 1.01), fit, 3.5)
    with pytest.raises(CL.GateError):
        CL.check_replica(dict(c, weight=c["weight"] * 0.9), fit, 3.5)


C = {"stat": "receptions", "n": 12, "own_mean": 5.0, "own_var": 6.0, "group_mean": 3.5,
     "group_var": 5.0, "no_group": False, "weight": 12 / 18, "role": 1,
     "team_changed": False, "coach_changed": False}


def test_reached_discriminates_side_and_target():
    assert CL.reached(0.44, "over", 0.50, 4.0, "flip")
    assert not CL.reached(0.47, "over", 0.50, 4.0, "flip")
    assert CL.reached(0.47, "over", 0.50, 4.0, "withdraw")
    assert CL.reached(0.56, "under", 0.50, 4.0, "flip")
    assert not CL.reached(0.44, "under", 0.50, 4.0, "flip")
    assert not CL.reached(None, "over", 0.50, 4.0, "flip")


@pytest.mark.parametrize("side,line", [("over", 3.5), ("under", 5.5)])
def test_a_minimum_perturbation_reaches_the_flip_and_nothing_smaller_does(side, line):
    mkt = 0.50
    base = CL.p_over(C, line)
    assert (base - mkt) * (1 if side == "over" else -1) >= 0.04, "fixture must be a lean"
    pert = CL.min_perturbations(C, line, side, mkt, 4.0, "flip")
    kw = {"own_mean": "own_mean", "group_mean": "group_mean", "weight": "weight"}
    seen = 0
    for name, arg in kw.items():
        if pert[name] is None:
            continue
        seen += 1
        x, d = C[name], pert[name]["distance"]
        sign = 1 if pert[name]["value"] > x else -1
        assert CL.reached(CL.p_over(C, line, **{arg: x * math.exp(sign * d)}), side, mkt, 4.0, "flip")
        assert not CL.reached(CL.p_over(C, line, **{arg: x * math.exp(sign * (d - 1e-3))}),
                              side, mkt, 4.0, "flip")
        # and the other direction is no closer
        assert not CL.reached(CL.p_over(C, line, **{arg: x * math.exp(-sign * d * 0.999)}),
                              side, mkt, 4.0, "flip")
    assert seen >= 1
    ln = pert["line"]
    assert ln is not None and abs(ln["steps"]) >= 1
    assert CL.reached(CL.p_over(C, ln["value"]), side, mkt, 4.0, "flip")
    inner = line + (ln["steps"] - (1 if ln["steps"] > 0 else -1))
    assert inner == line or not CL.reached(CL.p_over(C, inner), side, mkt, 4.0, "flip")


def test_an_input_that_cannot_flip_has_no_distance():
    """weight = 0 leaves the player's own mean with no say; a missing group has no
    group_mean. Absent is None, never a large number."""
    c0 = dict(C, weight=0.0, n=0, own_var=0.0, own_mean=0.0)
    pert = CL.min_perturbations(c0, 2.5, "over", 0.30, 4.0, "flip")
    assert pert["own_mean"] is None and pert["weight"] is None
    assert CL.min_perturbations(dict(C, no_group=True), 3.5, "over", 0.5, 4.0, "flip")["group_mean"] is None


def test_flipping_input_takes_the_smallest_and_breaks_ties_in_registered_order():
    none = {i: None for i in CL.INPUTS}
    assert CL.flipping_input(none) is None
    assert CL.flipping_input(dict(none, line={"distance": 0.2}, weight={"distance": 0.3})) == "line"
    assert CL.flipping_input(dict(none, line={"distance": 0.2}, own_mean={"distance": 0.2})) == "own_mean"


def test_holm_is_monotone_and_matches_a_hand_case():
    t = CL.holm([{"p": 0.01}, {"p": 0.04}, {"p": 0.03}, {"p": 0.5}])
    assert [round(x["p_holm"], 4) for x in t] == [0.04, 0.09, 0.09, 0.5]


def _t(test, inp, est, p_holm):
    return {"slice": "all", "test": test, "input": inp, "estimate": est, "p_holm": p_holm}


def test_the_verdict_can_come_out_all_three_ways():
    null = [_t(k, i, 0.0, 1.0) for k in ("T1", "T2") for i in CL.INPUTS]
    assert CL.verdict(null)["verdict"] == "spread_evenly"
    t1 = null + [_t("T1", "line", 0.3, 0.001)]
    assert CL.verdict(t1) == {"verdict": "concentrated_and_structural", "inputs": ["line"]}
    both = t1 + [_t("T2", "line", 0.1, 0.01)]
    assert CL.verdict(both) == {"verdict": "concentrated_and_about_the_misses", "inputs": ["line"]}
    # a share BELOW the null, however significant, is not concentration
    assert CL.verdict(null + [_t("T1", "weight", -0.19, 1e-9)])["verdict"] == "spread_evenly"
    # T2 on one input and T1 on another is not "about the misses"
    split = t1 + [_t("T2", "weight", 0.1, 0.01)]
    assert CL.verdict(split)["verdict"] == "concentrated_and_structural"
    # a sub-slice result changes nothing
    sub = null + [dict(_t("T1", "line", 0.3, 0.001), slice="side=over")]
    assert CL.verdict(sub)["verdict"] == "spread_evenly"


def _rows(mult=1):
    rng = np.random.default_rng(0)
    rows = []
    for g in range(30):
        tilt = rng.random()
        for n in range(12):
            res = "missed" if rng.random() < 0.5 else "cleared"
            inp = "line" if rng.random() < 0.3 + 0.4 * tilt else CL.INPUTS[int(rng.integers(0, 4))]
            rows += [{"game_id": f"g{g}", "result": res, "flipping_input": inp}] * mult
    return rows


def test_duplicating_rows_inside_a_game_does_not_narrow_the_interval():
    _, a = CL.slice_tests("all", _rows(1), np.random.default_rng(1))
    _, b = CL.slice_tests("all", _rows(20), np.random.default_rng(1))
    for x, y in zip(a, b):
        assert x["estimate"] == pytest.approx(y["estimate"])
        assert y["se"] == pytest.approx(x["se"], rel=1e-9)


def test_a_slice_with_under_five_games_enters_the_family_at_p_one():
    rows = [r for r in _rows() if r["game_id"] in ("g0", "g1", "g2")]
    _, t = CL.slice_tests("x", rows, np.random.default_rng(1))
    assert len(t) == 10 and all(x["p"] == 1.0 and x["lo"] is None for x in t)


def test_shares_ignore_rows_with_no_flip_and_sum_to_one():
    rows = [{"flipping_input": "line"}] * 3 + [{"flipping_input": None}] * 2 + [{"flipping_input": "weight"}]
    n, s, cnt = CL.shares(rows)
    assert n == 4 and s["line"] == 0.75 and sum(s.values()) == pytest.approx(1.0)


def test_ceiling_counts_what_no_input_reaches():
    far = {i: None for i in CL.INPUTS}
    rows = [{"flip": far}, {"flip": dict(far, line={"distance": math.log(1.2)})},
            {"flip": dict(far, weight={"distance": math.log(3.0)})}]
    c = CL.ceiling(rows)
    assert c["no_single_input_flip"] == 1
    assert [x["n_reached"] for x in c["caps"]] == [0, 1, 1, 1]
    assert c["per_input"]["line"]["n_can_flip"] == 1 and c["per_input"]["own_mean"]["n_cannot"] == 3
