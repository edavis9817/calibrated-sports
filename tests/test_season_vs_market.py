"""c-25: the pure pieces of research/season_vs_market.py. Each assertion is
shown producing the OTHER answer on the other input, so none can pass against
a constant."""
import numpy as np
import pytest

from models import season as M
from research import season_vs_market as S


def _season():
    teams = ["AAA", "BBB"]
    games = [("AAA", "BBB", 1, 0.5), ("BBB", "AAA", 2, 1.0), ("AAA", "BBB", 5, None)]
    return M.Season(teams=teams, division={t: "D" for t in teams},
                    conference={t: "C" for t in teams}, games=games)


def test_whole_wins_does_not_count_a_tie():
    s = _season()
    R = np.array([[0.5, 1.0, 1.0], [0.5, 1.0, 0.0]])
    W = S.whole_wins(s, R)
    # sim 0: AAA wins game 3 only; the week-1 tie is a win for nobody
    assert W[0, s.idx["AAA"]] == 1 and W[0, s.idx["BBB"]] == 1
    assert W[1, s.idx["AAA"]] == 0 and W[1, s.idx["BBB"]] == 2
    # the published half-win count differs exactly by the tie
    assert M.final_wins(s, R)[0, s.idx["AAA"]] == 1.5


def test_whole_wins_respects_the_week_cut():
    s = _season()
    R = np.array([[1.0, 0.0, 1.0]])
    assert S.whole_wins(s, R, max_week=4)[0, s.idx["AAA"]] == 2
    assert S.whole_wins(s, R)[0, s.idx["AAA"]] == 3
    assert list(S.games_by_team(s, 4)) == [2, 2]


def test_longest_streak_and_a_tie_breaks_it():
    res = np.array([[1, 1, 0.5, 1, 1, 1], [1, 1, 1, 1, 0, 1]], dtype=float)
    assert list(S.longest_streak(res)) == [3, 4]


def test_decided():
    assert S.decided(5, 3, 5) == 1
    assert S.decided(2, 2, 5) == 0
    assert S.decided(3, 2, 5) is None


def test_coinflip_survival():
    assert S.coinflip_survival(0, 2, 1) == pytest.approx(0.75)
    assert S.coinflip_survival(0, 2, 2) == pytest.approx(0.25)
    assert S.coinflip_survival(3, 2, 2) == 1.0
    assert S.coinflip_survival(0, 2, 3) == 0.0


def test_band_for_picks_the_bin_and_closes_the_last():
    bands = [{"bin": [0.0, 0.5], "interval": [0.1, 0.2]},
             {"bin": [0.5, 1.0], "interval": [0.6, 0.7]}]
    assert S.band_for(bands, 0.49)["interval"] == [0.1, 0.2]
    assert S.band_for(bands, 0.5)["interval"] == [0.6, 0.7]
    assert S.band_for(bands, 1.0)["interval"] == [0.6, 0.7]


def test_clears_uses_the_conservative_edge_and_the_fee():
    band = {"interval": [0.40, 0.46]}
    # buy YES at 0.30: edge at the band's LOW edge, 0.40 - 0.30 - fee
    ok, e = S.clears("yes", band, 0.29, 0.30)
    assert ok and e == pytest.approx(0.40 - 0.30 - 0.0147, abs=1e-9)
    # buy YES at 0.395: the band edge is 0.40, the fee (~1.67c) eats it
    ok, e = S.clears("yes", band, 0.39, 0.395)
    assert not ok and e < 0
    # buy NO when the bid is 0.60: pays 0.40, is worth 1 - 0.46 = 0.54 at the edge
    ok, e = S.clears("no", band, 0.60, 0.62)
    assert ok and e == pytest.approx(0.54 - 0.40 - 0.0168, abs=1e-9)
    ok, _ = S.clears("no", band, 0.47, 0.49)
    assert not ok


def test_crossing():
    assert S.crossing([(1, 0.9), (2, 0.7), (3, 0.3)]) == pytest.approx(2.5)
    assert S.crossing([(1, 0.9), (2, 0.8)]) is None


def test_reliability_interval_blocks_on_the_block():
    rng = np.random.default_rng(0)
    p = rng.random(4000)
    y = (rng.random(4000) < p).astype(float)
    blocks = list(np.repeat(np.arange(20), 200))
    b = S.reliability(p, y, blocks)
    assert len(b) == 10 and all(x["interval"][0] <= x["realised"] <= x["interval"][1] for x in b)
    # duplicating every row inside its own block must NOT narrow the interval
    b2 = S.reliability(np.repeat(p, 5), np.repeat(y, 5), list(np.repeat(blocks, 5)))
    w1 = np.mean([x["interval"][1] - x["interval"][0] for x in b])
    w2 = np.mean([x["interval"][1] - x["interval"][0] for x in b2])
    assert w2 > 0.8 * w1


def test_team_boot_refuses_to_manufacture_power():
    rows = [{"team": t, "g": g} for t, g in [("A", 1.0), ("B", -1.0), ("C", 0.5)] for _ in range(50)]
    r = S.team_boot(rows, lambda rs: float(np.mean([x["g"] for x in rs])))
    assert r["n_blocks"] == 3 and r["lo"] < 0 < r["hi"]
