"""c-36: the pure helpers of research/win_total_drift.py. No store, no network,
no file written - every input is built in the test."""
import numpy as np
import pytest

from research import win_total_drift as W


def test_usable_refuses_the_empty_book_and_the_wide_one():
    assert W.usable(0.42, 0.45)
    assert not W.usable(0.01, 0.99)          # empty book: mid would be a meaningless 0.5
    assert not W.usable(0.30, 0.50)          # 20c wide
    assert not W.usable(0.0, 0.05)
    assert not W.usable(0.95, 1.0)
    assert not W.usable(None, 0.5)
    assert W.usable(0.30, 0.45)              # exactly at the 15c cap


def test_pav_makes_a_non_increasing_ladder_and_leaves_a_monotone_one_alone():
    assert W.pav_decreasing([0.9, 0.7, 0.4]) == [0.9, 0.7, 0.4]
    got = W.pav_decreasing([0.9, 0.5, 0.6, 0.2])
    assert got == pytest.approx([0.9, 0.55, 0.55, 0.2])
    assert all(a >= b for a, b in zip(got, got[1:]))


def test_crossing_interpolates_and_refuses_a_ladder_that_never_crosses():
    assert W.crossing([(7, 0.8), (8, 0.6), (9, 0.4), (10, 0.2)]) == pytest.approx(8.5)
    assert W.crossing([(8, 0.6), (10, 0.2)]) == pytest.approx(8.5)     # across a missing rung
    assert W.crossing([(7, 0.9), (8, 0.8)]) is None
    assert W.crossing([(7, 0.4), (8, 0.3)]) is None
    assert W.crossing([(7, 0.4)]) is None
    # a decided rung (1.0) below a quoted ladder moves nothing
    assert W.crossing([(1, 1.0), (7, 0.8), (8, 0.6), (9, 0.4)]) == pytest.approx(8.5)
    # a higher number is a stronger team: shifting the ladder up shifts the crossing up
    assert W.crossing([(8, 0.8), (9, 0.6), (10, 0.4)]) > W.crossing([(7, 0.8), (8, 0.6), (9, 0.4)])


def test_decided():
    assert W.decided(3, 13, 3) == 1.0
    assert W.decided(0, 13, 14) == 0.0
    assert W.decided(2, 13, 9) is None


def _panel(beta, noise_shared=0.0, seed=1, teams=32, weeks=4):
    rng = np.random.default_rng(seed)
    rows = []
    for t in range(teams):
        for w in range(1, weeks + 1):
            gap = rng.normal(0, 1.0)
            lvl = rng.normal(0, 2.0)
            win = float(rng.random() < 0.5)
            e = rng.normal(0, 0.3)
            rows.append({"team": t, "week": w, "gap": gap, "lvl": lvl, "win": win,
                         "win_lvl": win * lvl,
                         "y": 0.1 * w + 0.6 * (win - 0.5) - 0.05 * lvl + beta * gap + e})
    return rows


def test_boot_ols_recovers_a_planted_beta_and_returns_zero_when_none_is_planted():
    X = ["gap", "win", "lvl", "win_lvl"]
    hit = W.boot_ols(_panel(0.30), "y", X, "week", "team", n_boot=400)["gap"]
    assert hit["lo"] < 0.30 < hi_(hit) and hit["lo"] > 0          # found, and excludes zero
    null = W.boot_ols(_panel(0.0), "y", X, "week", "team", n_boot=400)["gap"]
    assert null["lo"] < 0 < null["hi"]                             # the other answer
    assert hit["n"] == 128 and hit["n_blocks"] == 32
    assert hit["mde"] == pytest.approx(2.8 * hit["se"])


def hi_(d):
    return d["hi"]


def test_boot_ols_drops_rows_with_a_missing_value_and_says_how_many_it_kept():
    rows = _panel(0.2)
    rows[0]["gap"] = None
    rows[1]["y"] = None
    got = W.boot_ols(rows, "y", ["gap"], "week", "team", n_boot=200)["gap"]
    assert got["n"] == 126


def test_duplicating_rows_inside_their_team_does_not_narrow_the_interval():
    """The block is the team: 20 copies of every row are not 20x the evidence."""
    rows = _panel(0.2, seed=3)
    a = W.boot_ols(rows, "y", ["gap"], "week", "team", n_boot=400, seed=5)["gap"]
    b = W.boot_ols(rows * 20, "y", ["gap"], "week", "team", n_boot=400, seed=5)["gap"]
    assert b["se"] == pytest.approx(a["se"], rel=0.25)


def test_a_shared_noisy_instant_manufactures_reversal_and_a_skipped_one_does_not():
    """T2a's design: jump and drift measured off ONE noisy price produce a
    negative slope with no overreaction in the truth. Two instants do not."""
    rng = np.random.default_rng(7)
    same, skip = [], []
    for t in range(32):
        for w in range(1, 5):
            pre = rng.normal(8.5, 2)
            true_jump = rng.normal(0, 0.5)
            true_drift = rng.normal(0, 0.2)            # a martingale: unrelated to the jump
            noise_a, noise_b = rng.normal(0, 0.4), rng.normal(0, 0.4)
            posta = pre + true_jump + noise_a
            postb = pre + true_jump + noise_b
            end = pre + true_jump + true_drift
            same.append({"team": t, "week": w, "jump": posta - pre, "drift": end - posta})
            skip.append({"team": t, "week": w, "jump": posta - pre, "drift": end - postb})
    s = W.boot_ols(same, "drift", ["jump"], "week", "team", n_boot=300)["jump"]
    k = W.boot_ols(skip, "drift", ["jump"], "week", "team", n_boot=300)["jump"]
    assert s["hi"] < 0                 # the artifact: "overreaction" from noise alone
    assert k["lo"] < 0 < k["hi"]       # the registered design does not see it


def test_bh_and_p_from_z():
    assert W.p_from_z(0.0) == pytest.approx(1.0)
    assert W.p_from_z(1.96) == pytest.approx(0.05, abs=0.001)
    assert W.p_from_z(None) == 1.0
    assert W.bh({"a": 0.001, "b": 0.5, "c": 0.9}) == {"a"}
    assert W.bh({"a": 0.2, "b": 0.5, "c": 0.9}) == set()
    assert W.bh({"a": 0.01, "b": 0.05, "c": 0.09}) == {"a", "b", "c"}


def test_book_vwap_lifts_the_other_sides_bids_best_first():
    no_bids = [(0.60, 50), (0.55, 100), (0.40, 1000)]       # YES offers at 0.40, 0.45, 0.60
    v, filled = W.book_vwap(no_bids, 50)
    assert v == pytest.approx(0.40) and filled == 50
    v, _ = W.book_vwap(no_bids, 100)
    assert v == pytest.approx((50 * 0.40 + 50 * 0.45) / 100)
    v, filled = W.book_vwap(no_bids, 5000)
    assert v is None and filled == 1150                      # cannot fill: no price, not a cheap one
    # walking the book never gets cheaper with size
    assert W.book_vwap(no_bids, 500)[0] > W.book_vwap(no_bids, 100)[0]


def test_tape_ladder_uses_decided_rungs_and_drops_unusable_quotes():
    T = 1000.0 * 3600
    candles = {
        "KXNFLWINS-27JAC-1": {"h": {T: (0.01, 0.99, None, 0, 0)}, "d": {}},     # decided, book ignored
        "KXNFLWINS-27JAC-8": {"h": {T: (0.60, 0.62, None, 0, 0)}, "d": {}},
        "KXNFLWINS-27JAC-9": {"h": {T: (0.38, 0.42, None, 0, 0)}, "d": {}},
        "KXNFLWINS-27JAC-10": {"h": {T: (0.01, 0.99, None, 0, 0)}, "d": {}},    # empty book: dropped
        "KXNFLWINS-27JAC-11": {"h": {T - 3600: (0.10, 0.12, None, 0, 0)}, "d": {}},   # 1h fallback
    }
    tape = W.Tape(candles)
    assert list(tape.by_team) == ["JAX"]                      # folded to the nflverse code
    lad = tape.ladder("JAX", T, lambda team, ts: (1, 13))
    assert lad == [(1, 1.0), (8, pytest.approx(0.61)), (9, pytest.approx(0.40)),
                   (11, pytest.approx(0.11))]
    assert tape.fallback == 1
    assert tape.number("JAX", T, lambda team, ts: (1, 13)) == pytest.approx(8 + 0.11 / 0.21)
