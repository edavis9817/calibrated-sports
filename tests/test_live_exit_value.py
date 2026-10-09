"""c-21: the pure parts of research/live_exit_value.py.

Each rule is shown returning BOTH answers, so a green test cannot be a rule that
only ever says one thing.
"""
import json
import os

import pytest

from research import live_exit_value as lev


def test_quote_at_fresh_stale_none_and_one_sided():
    rows = [(100.0, 0.40, 0.44), (200.0, 0.0, 0.30), (300.0, 0.50, 1.0)]
    assert lev.quote_at(rows, 50.0)[0] == "none"
    assert lev.quote_at(rows, 150.0)[0] == "ok"
    st, b, a, age = lev.quote_at(rows, 150.0)
    assert (b, a, age) == (0.40, 0.44, 50.0)
    # a bid of zero or an ask of one is not a two-sided book
    assert lev.quote_at(rows, 250.0)[0] == "one_sided"
    assert lev.quote_at(rows, 350.0)[0] == "one_sided"
    # older than the 300 s heartbeat + one poll: the market was not polled
    assert lev.quote_at(rows, 300.0 + lev.HEARTBEAT_FRESH + 1)[0] == "stale"
    assert lev.quote_at(rows, 300.0 + lev.HEARTBEAT_FRESH - 1)[0] == "one_sided"


def test_depth_at_takes_the_named_side_and_the_nearest_snapshot():
    d = [(100.0, "buy_no", 0.6, 20.0, None), (105.0, "buy_yes", 0.45, 400.0, 0.47),
         (130.0, "buy_no", 0.6, 5.0, 0.62)]
    assert lev.depth_at(d, 110.0, "buy_no") == (20.0, None)     # nearest of the buy_no rows
    assert lev.depth_at(d, 125.0, "buy_no") == (5.0, 0.62)
    assert lev.depth_at(d, 110.0, "buy_yes") == (400.0, 0.47)
    assert lev.depth_at(d, 110.0 + lev.DEPTH_WINDOW + 60, "buy_no") is None


@pytest.mark.parametrize("y", [0.0, 1.0])
def test_ev_cost_decomposes_exactly_and_entry_cancels(y):
    r = {"y": y, "bid": 0.30, "ask": 0.36, "mid": 0.33, "hs": 0.03,
         "fee_y": 0.0147, "fee_n": 0.0161, "e": 0.40}
    for side, (fh, fe, fmis, fcost) in lev.LEGS.items():
        assert fh(r) - fe(r) == pytest.approx(fmis(r) + fcost(r), abs=1e-12)
        r2 = dict(r, e=0.10)
        assert fh(r2) - fe(r2) == pytest.approx(fh(r) - fe(r), abs=1e-12), side


def test_live_games_rule_admits_dense_and_refuses_sparse():
    games = {"G1": {"week": 3}, "G2": {"week": 2}}
    mk = {"a": {"game": "G1"}, "b": {"game": "G2"}}
    clocks = {"G1": {"start": 0.0, "end": 3600.0}, "G2": {"start": 0.0, "end": 3600.0}}
    kq = {"a": [(t, 0.4, 0.5) for t in range(0, 3600, 10)],
          "b": [(t, 0.4, 0.5) for t in range(0, 3600, 600)]}
    live, excluded = lev.live_games_rule(games, mk, kq, clocks)
    assert live == {"G1"}
    assert excluded == {"week 2": 1}


def test_boot_is_a_game_block_bootstrap():
    rows = [{"game": g, "v": v} for g, vs in (("A", [0.0] * 50), ("B", [1.0] * 50)) for v in vs]
    b = lev.boot(rows, lev.mean_of(lambda r: r["v"]), draws=400)
    assert b["est"] == 0.5 and b["games"] == 2
    # two blocks: resampling can draw A,A or B,B, so the interval must reach both ends
    assert b["lo"] == 0.0 and b["hi"] == 1.0
    # duplicating rows inside a game must not narrow it (the unit is the game)
    b2 = lev.boot(rows * 20, lev.mean_of(lambda r: r["v"]), draws=400)
    assert (b2["lo"], b2["hi"]) == (b["lo"], b["hi"])


def test_slope_sign_discriminates():
    up = [{"x": x, "y": 2 * x} for x in range(5)]
    down = [{"x": x, "y": -x} for x in range(5)]
    f = lev.slope_of(lambda r: r["x"], lambda r: r["y"])
    assert f(up) == pytest.approx(2.0) and f(down) == pytest.approx(-1.0)


def test_committed_results_are_aggregates_only():
    path = os.path.join(os.path.dirname(__file__), "..", "research", "results", "live_exit_value.json")
    if not os.path.exists(path):
        pytest.skip("no committed research/results/live_exit_value.json")
    text = open(path, encoding="utf-8").read()
    json.loads(text)
    # no ticker, no game id, no player id: BET_LIST_RESTRICTION
    assert "KXNFLREC-" not in text and "KXNFLRSHATT-" not in text
    assert "2026_0" not in text
    assert "00-00" not in text
