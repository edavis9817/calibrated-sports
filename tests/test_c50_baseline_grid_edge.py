"""c-50: the seed panel reproduces c-28's bootstrap, and every registered verdict
word is reachable (docs/C50-baseline-grid-edge-preregistration.md)."""
import numpy as np
import pytest

from research import ranking_calibration as rc
from research.c50_baseline_grid_edge import panel as P


def _rows(n, shift, seed=3):
    rng = np.random.default_rng(seed)
    y = (rng.random(n) < 0.55).astype(float)
    k = np.clip(0.55 + rng.normal(0, 0.1, n), 0.05, 0.95)
    m = np.clip(k + shift * (y - 0.5) + rng.normal(0, 0.02, n), 0.05, 0.95)
    return y, m, k


@pytest.mark.parametrize("seed", [24, 7])
def test_the_panel_loop_is_pop_boot(seed):
    y, m, k = _rows(301, 0.02)
    games = ["g%04d" % (i * 7919 % 301) for i in range(301)]      # not in row order
    rows = [{"game": g, "stat": "ml", "line": 0.0, "y": float(a), "m": float(b), "k": float(c)}
            for g, a, b, c in zip(games, y, m, k)]
    pop = rc.Pop("t", rows, "m", "k")
    ref = pop.boot(lambda i: rc.brier(pop.m[i], pop.y[i]) - rc.brier(pop.k[i], pop.y[i]),
                   draws=200, seed=seed)
    d = (m - y) ** 2 - (k - y) ** 2
    got = P.panel(d, P.blocks_of(games), seeds=(seed,), draws=200)[0]
    assert got["est"] == pytest.approx(ref["est"], abs=1e-15)
    assert got["per_seed"][0]["lo"] == pytest.approx(ref["lo"], abs=1e-15)
    assert got["per_seed"][0]["hi"] == pytest.approx(ref["hi"], abs=1e-15)
    # one seed pooled is that seed
    assert got["pooled"]["lo"] == pytest.approx(ref["lo"], abs=1e-15)


def test_multi_row_blocks_follow_pop_boot_too():
    y, m, k = _rows(240, 0.02)
    games = ["w%02d" % (i % 30) for i in range(240)]
    rows = [{"game": g, "stat": "ml", "line": 0.0, "y": float(a), "m": float(b), "k": float(c)}
            for g, a, b, c in zip(games, y, m, k)]
    pop = rc.Pop("t", rows, "m", "k")
    ref = pop.boot(lambda i: rc.brier(pop.m[i], pop.y[i]) - rc.brier(pop.k[i], pop.y[i]),
                   draws=150, seed=5)
    got = P.panel((m - y) ** 2 - (k - y) ** 2, P.blocks_of(games), seeds=(5,), draws=150)[0]
    assert got["blocks"] == 30
    assert got["per_seed"][0]["lo"] == pytest.approx(ref["lo"], abs=1e-14)
    assert got["per_seed"][0]["hi"] == pytest.approx(ref["hi"], abs=1e-14)


def test_the_panel_reaches_all_four_categories():
    n = 400
    rng = np.random.default_rng(1)
    noise = rng.normal(0, 0.01, n)
    blocks = P.blocks_of(list(range(n)))
    se = noise.std() / np.sqrt(n)                               # the realised SE, not the nominal one
    cols = np.column_stack([noise - noise.mean() - 6 * se,      # far below
                            noise - noise.mean() + 6 * se,      # far above
                            noise - noise.mean(),               # centred
                            noise - noise.mean() - 1.96 * se])  # on the bound
    res = P.panel(cols, blocks, seeds=tuple(range(1, 41)), draws=300)
    assert [r["category"] for r in res[:3]] == [P.BELOW, P.ABOVE, P.CONTAINS]
    edge = res[3]
    assert edge["category"] == P.DEPENDS
    assert 0 < edge["count"][P.BELOW] < 40          # the seeds really disagree
    assert sum(edge["count"].values()) == 40
    assert edge["pooled"]["draws"] == 40 * 300


def test_category_needs_the_share_and_the_pooled_interval():
    full = {P.BELOW: 1.0, P.CONTAINS: 0.0, P.ABOVE: 0.0}
    assert P.category(full, -0.002, -0.001) == P.BELOW
    assert P.category(full, -0.002, 0.001) == P.DEPENDS         # pooled disagrees
    most = {P.BELOW: 0.94, P.CONTAINS: 0.06, P.ABOVE: 0.0}
    assert P.category(most, -0.002, -0.001) == P.DEPENDS        # 188 of 200 is not 190
    at = {P.BELOW: 0.95, P.CONTAINS: 0.05, P.ABOVE: 0.0}
    assert P.category(at, -0.002, -0.001) == P.BELOW
    none = {P.BELOW: 0.0, P.CONTAINS: 1.0, P.ABOVE: 0.0}
    assert P.category(none, -0.002, 0.001) == P.CONTAINS
    up = {P.BELOW: 0.0, P.CONTAINS: 0.0, P.ABOVE: 1.0}
    assert P.category(up, 0.001, 0.002) == P.ABOVE


def test_weeks_verdict_reaches_every_word():
    v = P.weeks_verdict
    assert v(P.DEPENDS, P.BELOW, P.BELOW, True) == {
        "verdict": "SEED-DEPENDENT AT THIS POWER", "sentence": None, "qualifiers": []}
    assert v(P.CONTAINS, P.BELOW, P.BELOW, False) == {
        "verdict": "UNCHANGED - no better than", "sentence": "no better than", "qualifiers": []}
    assert v(P.BELOW, P.BELOW, P.BELOW, True) == {
        "verdict": "CHANGED - better than", "sentence": "better than", "qualifiers": []}
    assert v(P.ABOVE, P.ABOVE, P.ABOVE, True)["verdict"] == "CHANGED - worse than"
    q = v(P.BELOW, P.CONTAINS, P.DEPENDS, False)["qualifiers"]
    assert q == ["specification-dependent", "block-dependent",
                 "at a widened grid whose optimum is not interior"]


def test_headline_verdict_reaches_every_word():
    assert P.headline_verdict(P.BELOW, P.BELOW) == "CONFIRMS f-26 (unchanged)"
    assert P.headline_verdict(P.DEPENDS, P.BELOW) == "seed-dependent"
    assert P.headline_verdict(P.DEPENDS, P.CONTAINS) == "CONTRADICTS f-26"
    assert P.headline_verdict(P.CONTAINS, P.CONTAINS) == "CONTRADICTS f-26"
    assert P.headline_verdict(P.ABOVE, P.ABOVE) == "CONTRADICTS f-26"


def test_edges_and_a_one_block_panel():
    assert [P.edges(v, [10.0, 40.0, 120.0]) for v in (10.0, 40.0, 120.0)] == ["min", "", "max"]
    with pytest.raises(ValueError):
        P.panel(np.zeros(3), P.blocks_of(["a", "a", "a"]), seeds=(1,), draws=5)
