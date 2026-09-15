"""Brief 023 Part 2: book-versus-book arithmetic - orientation, pushes,
freshness, arbitrage and middle EV."""
import pytest

from research import bookvbook as B


def _q(book, kind, thr, price, lu=1000.0):
    return {"book": book, "kind": kind, "thr": thr, "price": price,
            "imp": B.american_to_prob(price), "lu": lu}


def test_odds_conversions():
    assert B.american_to_prob(-110) == pytest.approx(110 / 210)
    assert B.american_to_prob(150) == pytest.approx(0.4)
    assert B.decimal(150) == pytest.approx(2.5)
    assert B.decimal(-200) == pytest.approx(1.5)


def test_spread_orientation_is_on_home_margin():
    # home -3.5: wins iff home margin > 3.5 ; away +3.5: wins iff home margin < 3.5
    assert B.quote_kind("spreads", "Home FC", -3.5, "Home FC") == ("o", 3.5)
    assert B.quote_kind("spreads", "Away FC", 3.5, "Home FC") == ("u", 3.5)
    # home +3 (underdog): wins iff home margin > -3
    assert B.quote_kind("spreads", "Home FC", 3.0, "Home FC") == ("o", -3.0)
    assert B.quote_kind("totals", "Over", 44.5, "x") == ("o", 44.5)
    assert B.quote_kind("totals", "Under", 44.5, "x") == ("u", 44.5)


def test_integer_thresholds_push():
    assert B.pay(-110, "o", 3.0, 3) == 0.0
    assert B.pay(-110, "u", 3.0, 3) == 0.0
    assert B.pay(100, "o", 3.0, 4) == pytest.approx(1.0)
    assert B.pay(100, "u", 3.0, 4) == -1.0


def test_freshness_and_pair_window():
    a, b = _q("dk", "o", 3.5, -110, lu=1000), _q("fd", "u", 3.5, -110, lu=1200)
    assert B.comparable(a, b, snap=1300, fresh=600, window=300)
    assert not B.comparable(a, b, snap=1300, fresh=600, window=100)      # pair too far apart
    assert not B.comparable(a, b, snap=1700, fresh=600, window=300)      # a is 700s old
    assert not B.comparable(a, _q("dk", "u", 3.5, -110), snap=1100, fresh=600, window=300)  # same book
    assert B.comparable(a, _q("fd", "u", 3.5, -110, lu=None), snap=1100, fresh=None, window=None)


def test_arb_requires_implied_sum_below_one_and_different_books():
    qs = [_q("dk", "o", 44.5, 110), _q("fd", "u", 44.5, 105), _q("dk", "u", 44.5, -300)]
    arbs, _ = B.analyse_group(qs, snap=1100, fresh=600, window=300)
    a = arbs[44.5]
    assert a["arb"] and a["books"] == ("dk", "fd")
    assert 1 - a["sum"] == pytest.approx(1 - (100 / 210 + 100 / 205))
    no, _ = B.analyse_group([_q("dk", "o", 44.5, -110), _q("fd", "u", 44.5, -110)], 1100, 600, 300)
    assert not no[44.5]["arb"]


def test_middle_ev_on_a_toy_distribution():
    o, u = _q("dk", "o", 3.0, -110), _q("fd", "u", 3.5, -110)
    # X=3: o pushes (0), u wins (+0.909); X=10: o wins, u loses; X=0: o loses, u wins
    dist = {3: 0.2, 10: 0.4, 0: 0.4}
    ev, hit = B.middle_ev(o, u, dist)
    w = 100 / 110
    exp = (0.2 * (0 + w) + 0.4 * (w - 1) + 0.4 * (-1 + w)) / 2
    assert ev == pytest.approx(exp)
    assert hit == 0.0                       # 3 < X < 3.5 has no integer X
    ev2, hit2 = B.middle_ev(_q("dk", "o", 2.5, -110), _q("fd", "u", 3.5, -110), {3: 1.0})
    assert hit2 == 1.0 and ev2 == pytest.approx(w)


def test_middles_found_only_when_under_threshold_exceeds_over():
    qs = [_q("dk", "o", 3.0, -110), _q("fd", "u", 3.5, -110), _q("mgm", "u", 2.5, -110)]
    arbs, mids = B.analyse_group(qs, 1100, 600, 300, dist={3: 1.0})
    assert list(mids) == [(3.0, 3.5)] and not arbs


def test_corrected_settlement_rule():
    assert B.corrected_value(3.0, True, None, "receptions") == (3.0, "value")
    assert B.corrected_value(None, False, ("BUF", 18, 0), "receptions") == (0.0, "played, no row -> 0")
    assert B.corrected_value(None, False, ("BUF", 0, 0), "receptions") == (None, "did not play -> void")
    assert B.corrected_value(None, False, None, "receptions") == (None, "did not play -> void")
    # defensive props judge on DEFENSIVE snaps
    assert B.corrected_value(None, False, ("BUF", 0, 41), "sacks") == (0.0, "played, no row -> 0")
    assert B.corrected_value(None, False, ("BUF", 0, 41), "tackles_assists") == (0.0, "played, no row -> 0")
    assert B.corrected_value(None, False, ("BUF", 12, 0), "sacks") == (None, "did not play -> void")


def test_consensus_mode_ties_go_low():
    assert B.mode_threshold([3.5, 3.5, 3.0, 3.0, 2.5]) == 3.0
    assert B.mode_threshold([]) is None
