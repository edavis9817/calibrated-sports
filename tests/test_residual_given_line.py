"""c-41: the residual-given-the-line statistics, on synthetic rows only.

No store is opened here: every test builds its own arrays. The registered run
is the first execution that forms a residual on real rows.
"""
import math

import numpy as np
import pytest

from research import residual_given_line as R


def _recs(n_games=120, per_game=12, slope=0.0, seed=0, dup=1):
    """Rungs whose over probability is 0.5 + slope * z(form_gap)."""
    rng = np.random.default_rng(seed)
    recs = []
    for g in range(n_games):
        season = R.SEASONS[g % 3]
        for i in range(per_game):
            x = rng.normal()
            y = float(rng.random() < 0.5 + slope * x)
            for d in range(dup):
                rec = {"oid": f"{g}-{i}-{d}", "gsis": f"p{g}-{i}", "event": f"g{g}",
                       "season": season, "line": 40.5 + d, "p": 0.5, "y": y,
                       "n_bench": 3 - d, "n_all": 5}
                rec.update(dict.fromkeys(R.CANDIDATES, None))
                rec.update(form_gap=x, log_line=float(rng.normal()))
                recs.append(rec)
    return recs


def _get(tests, kind, pop, cut, cand):
    return next(t for t in tests if (t["kind"], t["pop"], t["cut"], t["cand"])
                == (kind, pop, cut, cand))


def test_a_planted_slope_is_found_and_passes():
    tests, _ = R.analyse(_recs(n_games=300, slope=0.10), print)
    t = _get(tests, "coef", "RB", "pooled", "form_gap")
    assert 0.07 < t["est"] < 0.13 and t["p_holm"] < 0.05
    v = R.verdict(tests)
    assert v["form_gap"]["passes"] and not v["log_line"]["passes"]


def test_the_null_does_not_pass():
    tests, _ = R.analyse(_recs(n_games=300, slope=0.0, seed=3), print)
    assert not any(v["passes"] for v in R.verdict(tests).values())


def test_family_size_is_the_registered_one():
    tests, _ = R.analyse(_recs(), print)
    reg = [t for t in tests if t["registered"]]
    assert len(reg) == R.N_REGISTERED == 42 and len(tests) == 46
    assert all("p_holm" in t for t in reg)
    assert all("p_holm" not in t for t in tests if not t["registered"])


def test_duplicating_rungs_inside_a_game_does_not_narrow_the_interval():
    """Rungs of one player-game share one outcome: a game block must absorb it."""
    one, _ = R.analyse(_recs(seed=5, dup=1), print)
    many, _ = R.analyse(_recs(seed=5, dup=4), print)
    a = _get(one, "coef", "RB", "pooled", "form_gap")
    b = _get(many, "coef", "RB", "pooled", "form_gap")
    assert b["n"] == 4 * a["n"]
    assert b["se"] > 0.8 * a["se"]


def test_rm_keeps_one_rung_per_player_game_by_bench_count():
    recs = _recs(n_games=6, per_game=3, dup=3)
    rm = R.modal(recs)
    assert len(rm) == 18 and all(d["n_bench"] == 3 for d in rm)


def test_a_candidate_gets_no_credit_for_the_intercept():
    """Outcomes with an over bias and no slope: `close+a` improves, a candidate does not."""
    recs = _recs(n_games=300, seed=7)
    rng = np.random.default_rng(11)
    for d in recs:
        d["y"] = float(rng.random() < 0.40)
    seen = {}
    for d in recs:                                   # one outcome per player-game
        d["y"] = seen.setdefault((d["gsis"], d["event"]), d["y"])
    tests, _ = R.analyse(recs, print)
    assert _get(tests, "brier", "RB", "2024+2025", "close+a")["est"] < -0.005
    assert abs(_get(tests, "brier", "RB", "2024+2025", "form_gap")["est"]) < 0.002


def test_walk_forward_refuses_a_training_row_from_the_target_season(monkeypatch):
    rows = R.columns(_recs())
    q, fits = R.walk_forward(rows, ["form_gap"])
    assert set(fits) == {2024, 2025} and fits[2024]["n_train"] < fits[2025]["n_train"]
    assert np.isnan(q[rows["season"] == 2023]).all()
    assert np.isfinite(q[rows["season"] >= 2024]).all()


def test_features_refuse_a_game_at_or_after_kickoff():
    game = {"season": 2024, "k": 1000, "home": "A", "away": "B", "spread": 3.0, "total": 44.0}
    rung = {"line": 40.5, "p_bench": 0.5, "p_all": 0.52}
    with pytest.raises(R.LeakError):
        R.features(rung, game, [(1000, 2024, 50.0, "A", "WR")], 42.0, None)


def test_feature_definitions():
    game = {"season": 2024, "k": 1000, "home": "A", "away": "B", "spread": 3.0, "total": 44.0}
    rung = {"line": 40.0, "p_bench": 0.50, "p_all": 0.53}
    prior = [(10, 2023, 20.0, "A", "WR"), (20, 2024, 80.0, "A", "WR"), (30, 2024, 50.0, "A", "WR")]
    f = R.features(rung, game, prior, 45.0, None)
    ybar = (0.5 * 20 + 80 + 50) / 2.5
    assert f["form_gap"] == pytest.approx((ybar - 40) / 50)
    assert f["last_game"] == pytest.approx((50 - 40) / 50)
    assert f["book_gap"] == pytest.approx(0.03)
    assert f["line_pos"] == pytest.approx((40 - 45) / 55)
    assert f["team_total"] == pytest.approx(23.5)          # home, favoured by 3
    assert f["log_line"] == pytest.approx(math.log(40))
    away = R.features(rung, game, [(30, 2024, 50.0, "B", "WR")], None, None)
    assert away["team_total"] == pytest.approx(20.5) and away["form_gap"] is None
    assert away["line_pos"] is None
    moved = R.features(rung, game, [(30, 2024, 50.0, "C", "WR")], None, "B")
    assert moved["team_total"] == pytest.approx(20.5)      # roster fallback
    assert R.features(rung, game, [(30, 2024, 50.0, "C", "WR")], None, "D")["team_total"] is None


def test_missing_standardises_to_zero_and_winsorises():
    x = np.array([0.0, 1.0, 2.0, np.nan, 100.0])
    z = R.standardise(x, 1.0, 1.0)
    assert z[3] == 0.0 and z[4] == R.WINSOR and z[0] == -1.0


def test_holm_and_the_zero_variance_rule():
    assert R.holm([0.01, 0.04, 0.03]) == pytest.approx([0.03, 0.06, 0.06])
    t = R._summ(0.5, np.full(2000, 0.5) + 1e-17, 50, 100)
    assert t["p"] == 1.0                                   # ~1e-17 is not "a tight interval"
    assert R._summ(0.5, np.random.default_rng(0).normal(0.5, 0.1, 2000), 4, 100)["p"] == 1.0
