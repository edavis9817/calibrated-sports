"""Brief 021 Part A: scoring pieces, on synthetic numbers only."""
import math
import statistics

import pytest

from research import score as S


def test_brier_and_logloss():
    assert S.brier(0.7, 1) == pytest.approx(0.09)
    assert S.logloss(0.5, 0) == pytest.approx(math.log(2))
    assert S.logloss(0.0, 1) == pytest.approx(-math.log(S.EPS))     # clipped, finite


def test_naive_player_rate_is_laplace_over_games_with_an_opportunity():
    games = [(5, 7), (2, 4), (0, 0), (6, 8), (4, 5)]          # one game with no target
    p, src = S.naive_prob(games, pooled=(10, 100), line=4.5)
    assert src == "player" and p == pytest.approx((2 + 1) / (4 + 2))


def test_naive_falls_back_to_pooled_under_four_games():
    p, src = S.naive_prob([(5, 7), (1, 2)], pooled=(30, 98), line=4.5)
    assert src == "pooled" and p == pytest.approx(31 / 100)


def test_buckets_are_the_preregistered_edges():
    assert S.bucket(8, S.HISTORY) == "thin 0-8"
    assert S.bucket(9, S.HISTORY) == "medium 9-24"
    assert S.bucket(25, S.HISTORY) == "thick 25+"
    assert S.bucket(0.149, S.PRICE) == "<0.15"
    assert S.bucket(0.15, S.PRICE) == "0.15-0.35"
    assert S.bucket(0.65, S.PRICE) == "0.65-0.85"
    assert S.bucket(0.86, S.PRICE) == ">0.85"


def test_kalshi_rung_is_over_line_minus_half():
    mid = "KXNFLREC-26SEP13DALNYG-NYGOBECKHAM13-6"
    assert S.rung(mid) == 6 and S.rung(mid) - 0.5 == 5.5
    assert S.ladder_key(mid) == "KXNFLREC-26SEP13DALNYG-NYGOBECKHAM13"


def test_ladder_position_uses_rungs_listed_at_entry():
    base = "KXNFLREC-26SEP13DALNYG-NYGOBECKHAM13"
    listed = [f"{base}-{k}" for k in (3, 4, 5, 6)] + ["KXNFLREC-26SEP13DALNYG-OTHER1-9"]
    assert S.ladder_position(f"{base}-3", listed) == "edge"
    assert S.ladder_position(f"{base}-6", listed) == "edge"
    assert S.ladder_position(f"{base}-4", listed) == "interior"
    assert S.ladder_position(f"{base}-7", [f"{base}-3"]) == "edge"   # unlisted self counts


def test_bootstrap_blocks_on_games_and_duplication_does_not_narrow():
    rows = [{"game": g, "d": v} for g, v in (("a", .01), ("b", -.02), ("c", .03), ("d", 0))]
    one = S.boot_mean(rows, lambda r: r["d"])
    dup = S.boot_mean([dict(r) for r in rows for _ in range(25)], lambda r: r["d"])
    assert dup["games"] == 4 and dup["n"] == 100
    assert dup["sd"] == pytest.approx(one["sd"], rel=1e-9)


def test_cluster_se_matches_the_hand_formula():
    rows = [{"game": "a", "d": 1.0}, {"game": "a", "d": 1.0},
            {"game": "b", "d": -1.0}, {"game": "b", "d": -1.0}]
    # mean 0; cluster sums +2, -2; CR1 = sqrt(2/1 * 8)/4 = 1.0
    assert S.cluster_se(rows, lambda r: r["d"]) == pytest.approx(1.0)


def test_mde_and_games_needed_scale_with_root_games():
    assert S.mde(0.01) == pytest.approx(0.028, abs=1e-3)
    g = S.games_for_mde(0.01, 16, S.mde(0.01) / 2)          # halve the MDE
    assert g == pytest.approx(64)


def test_reliability_uses_wilson_and_flags_small_bins():
    ps = [0.05] * 40 + [0.95] * 10
    ys = [0] * 38 + [1] * 2 + [1] * 10
    table, ece = S.reliability(ps, ys)
    lo_bin = table[0]
    assert lo_bin["n"] == 40 and lo_bin["rate"] == pytest.approx(0.05)
    assert lo_bin["wilson"][0] > 0 and not lo_bin["excludes"]
    assert table[9]["n"] == 10 and table[9]["excludes"] is False   # n<30 never flagged
    assert ece == pytest.approx((40 * 0.0 + 10 * 0.05) / 50)


def test_reliability_flags_a_miscalibrated_bin():
    table, _ = S.reliability([0.45] * 100, [1] * 90 + [0] * 10)
    assert table[4]["excludes"] is True
