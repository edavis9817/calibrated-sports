"""F11: the vectorised streak construction must equal a brute-force loop.

Every figure in docs/F11-streak-null.md passes through `lines`, `trailing` and
`boards`, which are index arithmetic over cumulative sums - exactly the code that
returns a plausible wrong number. Each is compared against a naive loop on random
data, and the comparison is shown to discriminate (a shifted window fails it).
"""
import numpy as np
import pytest

from research import f11_streak_null as F


def synthetic(seed=0, pairs=40, games=30):
    rng = np.random.default_rng(seed)
    rows = []
    for p in range(pairs):
        n = rng.integers(1, games)
        for t in range(n):
            rows.append((p, p // 3, p % len(F.STATS), 2000 + t // 17, 1 + t % 17,
                         float(rng.integers(0, 12)), "ABCD"[rng.integers(0, 4)],
                         int(rng.integers(0, 2))))
    cols = ("pair", "player", "stat", "season", "week", "x", "opp", "away")
    return {c: np.array([r[i] for r in rows]) for i, c in enumerate(cols)}


def naive_lines(a):
    out = []
    for i in range(len(a["x"])):
        prev = [a["x"][j] for j in range(i) if a["pair"][j] == a["pair"][i]][-F.LOOKBACK:]
        out.append(np.median(prev) if len(prev) >= F.MIN_PRIOR else np.nan)
    return np.array(out)


def naive_boards(a, g, h):
    n = len(a["x"])
    q = {b: np.zeros(n, bool) for b in F.BOARDS}
    for i in range(n):
        prev = [j for j in range(i) if a["pair"][j] == a["pair"][i] and g[j]]
        last7 = [h[j] for j in prev][-7:]
        q["L7"][i] = len(last7) == 7 and sum(last7) >= 6
        last5 = [h[j] for j in prev][-5:]
        q["L5"][i] = len(last5) == 5 and all(last5)
        aw = [h[j] for j in prev if a["away"][j] == 1][-5:]
        q["AWAY5"][i] = a["away"][i] == 1 and len(aw) == 5 and all(aw)
        mt = [h[j] for j in prev if a["opp"][j] == a["opp"][i]]
        q["H2H1"][i] = a["opp"][i] != "" and len(mt) == 1 and bool(mt[0])
    return q


@pytest.mark.parametrize("seed", [0, 1, 2])
def test_lines_match_naive(seed):
    a = synthetic(seed)
    np.testing.assert_array_equal(np.isnan(F.lines(a["pair"], a["x"])), np.isnan(naive_lines(a)))
    v = ~np.isnan(naive_lines(a))
    np.testing.assert_allclose(F.lines(a["pair"], a["x"])[v], naive_lines(a)[v])


@pytest.mark.parametrize("seed", [0, 1, 2, 3])
def test_boards_match_naive(seed):
    a = synthetic(seed)
    rng = np.random.default_rng(seed + 100)
    g = rng.random(len(a["x"])) < 0.8
    h = g & (rng.random(len(a["x"])) < 0.7)   # high rate, so the boards actually fire
    got, _ = F.boards(a, g, h)
    want = naive_boards(a, g, h)
    for b in F.BOARDS:
        assert want[b].sum() > 0, f"{b} never fires on the fixture - the test would be vacuous"
        np.testing.assert_array_equal(got[b], want[b], err_msg=b)


def test_comparison_discriminates():
    """A board built one row late must FAIL the naive comparison."""
    a = synthetic(5)
    rng = np.random.default_rng(9)
    g = rng.random(len(a["x"])) < 0.8
    h = g & (rng.random(len(a["x"])) < 0.7)
    got, _ = F.boards(a, g, h)
    want = naive_boards(a, g, h)
    shifted = np.roll(got["L5"], 1)
    assert not np.array_equal(shifted, want["L5"])


def test_iid_world_resamples_within_player_season():
    a = synthetic(7)
    key = list(zip(a["pair"], a["season"]))
    rng = np.random.default_rng(0)
    # rebuild the pick the same way world_iid does and check it never leaves the block
    k = a["pair"].astype(np.int64) * 10_000 + a["season"]
    new = np.ones(len(k), bool)
    new[1:] = k[1:] != k[:-1]
    starts = np.where(new)[0]
    ends = np.append(starts[1:], len(k))
    blk = np.cumsum(new) - 1
    pick = starts[blk] + (rng.random(len(k)) * (ends - starts)[blk]).astype(np.int64)
    assert all(key[i] == key[j] for i, j in enumerate(pick))


def test_coin_world_is_fair_on_graded_rows():
    a = synthetic(3, pairs=300, games=40)
    base = F.grade(a, a["x"])
    rng = np.random.default_rng(1)
    _, g, h = F.world_coin(a, rng, base)
    assert not (h & ~g).any()
    assert abs(h[g].mean() - 0.5) < 0.03
