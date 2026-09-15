"""Brief 020: the pure pieces of the Kalshi-vs-consensus test."""
import pytest

from research import consensus as C


def _ev(markets, key="dk", lu="2026-09-13T16:00:00Z"):
    return {"bookmakers": [{"key": key, "last_update": lu, "markets": markets}]}


def test_american_odds():
    assert C.american_to_prob(-110) == pytest.approx(110 / 210)
    assert C.american_to_prob(150) == pytest.approx(0.4)


def test_multiplicative_and_shin_both_sum_to_one_and_differ_off_centre():
    py, pn = C.american_to_prob(-300), C.american_to_prob(240)
    m = C.devig(py, pn, "multiplicative")
    s = C.devig(py, pn, "shin")
    assert 0.7 < m < 0.8 and 0.7 < s < 0.8
    assert s != pytest.approx(m, abs=1e-6)       # Shin loads margin on the longshot
    assert s > m


def test_spread_key_is_the_favourites_claim():
    ev = _ev([{"key": "spreads", "outcomes": [
        {"name": "Houston Texans", "price": -105, "point": 2.5},
        {"name": "Buffalo Bills", "price": -115, "point": -2.5}]}])
    (key, py, pn, book, lu), = C.book_pairs(ev)
    assert key == ("spread", "BUF", 2.5)
    assert py == pytest.approx(C.american_to_prob(-115))
    assert book == "dk" and lu is not None


def test_pickem_and_mismatched_points_are_skipped():
    ev = _ev([{"key": "spreads", "outcomes": [
        {"name": "Houston Texans", "price": -110, "point": 0.0},
        {"name": "Buffalo Bills", "price": -110, "point": 0.0}]},
        {"key": "totals", "outcomes": [
            {"name": "Over", "price": -110, "point": 44.5},
            {"name": "Under", "price": -110, "point": 45.5}]}])
    assert C.book_pairs(ev) == []


def test_totals_key():
    ev = _ev([{"key": "totals", "outcomes": [
        {"name": "Over", "price": -108, "point": 44.5},
        {"name": "Under", "price": -112, "point": 44.5}]}])
    assert C.book_pairs(ev)[0][0] == ("total", None, 44.5)


def test_consensus_requires_two_books_and_counts_the_drop():
    k1, k2 = ("total", None, 44.5), ("total", None, 47.5)
    pairs = [(k1, .52, .52, "a", 0), (k1, .50, .54, "b", 0), (k2, .5, .5, "a", 0)]
    ok, short = C.consensus(pairs, "multiplicative")
    assert set(ok) == {k1} and short == [k2]
    assert ok[k1][1] == 2
    assert ok[k1][0] == pytest.approx((0.5 + 0.5 / 1.04) / 2)


def test_kalshi_keys_match_book_keys():
    assert C.kalshi_key("KXNFLSPREAD-26SEP13BUFHOU-BUF3", "Buffalo wins by over 2.5 points", 2.5) \
        == ("spread", "BUF", 2.5)
    assert C.kalshi_key("KXNFLSPREAD-26SEP10SFLAR-LAR4", "Los Angeles R wins by over 3.5 points", 3.5) \
        == ("spread", "LA", 3.5)
    assert C.kalshi_key("KXNFLTOTAL-26SEP13BUFHOU-45", "Over 44.5 points scored", 44.5) \
        == ("total", None, 44.5)


def test_settlement_from_the_named_teams_perspective():
    # BUF (away) 36, HOU (home) 31
    assert C.settle(("spread", "BUF", 2.5), "HOU", "BUF", 31, 36) == 1.0
    assert C.settle(("spread", "BUF", 5.5), "HOU", "BUF", 31, 36) == 0.0
    assert C.settle(("spread", "HOU", 1.5), "HOU", "BUF", 31, 36) == 0.0
    assert C.settle(("total", None, 66.5), "HOU", "BUF", 31, 36) == 1.0
    assert C.settle(("total", None, 44.5), "HOU", "BUF", None, None) is None


def test_crossing_goes_toward_the_consensus():
    assert C.side_for(+0.03) == "no" and C.side_for(-0.03) == "yes"
    assert C.touch_price("yes", 0.40, 0.42) == 0.42
    assert C.touch_price("no", 0.40, 0.42) == pytest.approx(0.60)


def test_walk_close_closed_censored_and_stale():
    q = [(10, .55, .57), (20, .51, .53)]           # mids .56 (open), .52 (inside 2.5pp)
    assert C.walk_close(q, 0.50, 0.025, horizon=100, start=0) == (20, "closed")
    assert C.walk_close(q[:1], 0.50, 0.025, horizon=100, start=0) == (100, "censored")
    assert C.walk_close([(700, .50, .50)], 0.50, 0.025, horizon=5000, start=0) == (0, "went stale")


def test_lead_lag_slopes_have_the_right_sign_on_synthetic_data():
    # books close half of every gap by the next snapshot; kalshi never moves
    gaps = [0.01 * i for i in range(-5, 6)]
    dc = [0.5 * g for g in gaps]
    assert C.ols_slope(gaps, dc) == pytest.approx(0.5)
    assert C.ols_slope([-g for g in gaps], [0.0] * len(gaps)) == pytest.approx(0.0)


def test_bootstrap_resamples_games_not_rows():
    rows = [{"game": g, "v": v} for g, v in (("a", 1.0), ("b", -1.0), ("c", 0.5))]
    wide = C.boot(rows, C.mean_of("v"))
    dup = C.boot([dict(r) for r in rows for _ in range(20)], C.mean_of("v"))
    assert dup["games"] == 3
    assert (dup["hi"] - dup["lo"]) >= 0.9 * (wide["hi"] - wide["lo"])


def test_ttk_buckets():
    assert C.ttk_bucket(-1) == "in-game"
    assert C.ttk_bucket(30 * 60) == "0-1h"
    assert C.ttk_bucket(3 * 24 * 3600 + 1) == ">72h"
