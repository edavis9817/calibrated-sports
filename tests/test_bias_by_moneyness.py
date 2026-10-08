"""c-22: research/bias_by_moneyness.py - buckets, bootstrap, contrast, gate.

The study itself needs market_log.db; these pin the arithmetic on synthetic
rows so a bucket edge or a sign cannot move silently.
"""
import numpy as np
import pytest

from research import bias_by_moneyness as m


def test_buckets_partition_unit_interval_at_the_registered_edges():
    assert m.bucket_label(m.bucket_of(0.0)) == "0.00-0.20"
    assert m.bucket_label(m.bucket_of(0.5999)) == "0.55-0.60"
    assert m.bucket_label(m.bucket_of(0.60)) == "0.60-0.70"   # lower edge inclusive
    assert m.bucket_label(m.bucket_of(1.0)) == "0.80-1.00"
    assert len(m.EDGES) - 1 == 10
    with pytest.raises(ValueError):
        m.bucket_of(1.5)


def test_zones_match_the_preregistration():
    assert m.zone_of(0.3999).startswith("longshot")
    assert m.zone_of(0.40).startswith("near")
    assert m.zone_of(0.60).startswith("deep")


def test_fee_is_quadratic_and_falls_at_the_tails():
    assert m.fee(0.5) == pytest.approx(0.0175)
    assert m.fee(0.9) == pytest.approx(0.07 * 0.9 * 0.1)
    assert m.fee(0.9) < m.fee(0.7) < m.fee(0.5)


def _synthetic(n_games=200, per=20, bias=0.0, seed=1):
    rng = np.random.default_rng(seed)
    games, vals = [], []
    for g in range(n_games):
        p = 0.5
        hits = rng.random(per) < p + bias
        games += [g] * per
        vals += list(hits - p)
    return games, np.array(vals, float)


def test_boot_mean_estimate_is_the_plain_mean_and_the_interval_discriminates():
    g0, v0 = _synthetic(bias=0.0)
    b0 = m.boot_mean(g0, v0, "null")
    assert b0["est_pp"] == pytest.approx(100 * v0.mean())
    assert b0["lo_pp"] < 0 < b0["hi_pp"]
    g1, v1 = _synthetic(bias=-0.10)
    b1 = m.boot_mean(g1, v1, "biased")
    assert b1["hi_pp"] < 0                                  # the other answer is reachable


def test_boot_mean_flags_fewer_than_five_games_as_unreadable():
    b = m.boot_mean([1, 1, 2, 3, 4], np.zeros(5), "thin")
    assert b["games"] == 4 and b["readable"] is False


def test_block_bootstrap_does_not_narrow_when_rows_are_duplicated_within_game():
    g, v = _synthetic(n_games=60, per=5, bias=0.0, seed=3)
    b = m.boot_mean(g, v, "x")
    g20 = [x for x in g for _ in range(20)]
    v20 = np.repeat(v, 20)
    b20 = m.boot_mean(g20, v20, "x")
    assert (b20["hi_pp"] - b20["lo_pp"]) == pytest.approx(b["hi_pp"] - b["lo_pp"], rel=0.02)


def test_contrast_is_one_quantity_and_recovers_a_planted_difference():
    rng = np.random.default_rng(7)
    games, vals, deep, atm = [], [], [], []
    for gm in range(300):
        for _ in range(10):
            is_deep = rng.random() < 0.3
            p = 0.7 if is_deep else 0.5
            shift = 0.0 if is_deep else -0.10
            games.append(gm)
            vals.append(float(rng.random() < p + shift) - p)
            deep.append(is_deep)
            atm.append(not is_deep)
    c = m.boot_contrast(games, np.array(vals), np.array(deep), np.array(atm), "c")
    assert c["lo_pp"] > 0                                    # deep - atm = +10pp planted
    assert 5 < c["est_pp"] < 15
    assert c["n_a"] + c["n_b"] == 3000


def test_slice_seeds_differ_so_buckets_are_drawn_independently():
    assert m._seed("all|b3|gap") != m._seed("all|b4|gap")
    assert m._seed("all|b3|gap") == m._seed("all|b3|gap")


def test_gate_refuses_a_population_that_is_not_r18():
    rows = [{"game": g, "hit": 1, "p": 0.5} for g in range(10)]
    with pytest.raises(RuntimeError, match="reproduction gate FAILED"):
        m.gate(rows)
