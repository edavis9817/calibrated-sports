"""The C01 Part 1 estimator, tested on ladders whose answer is known.

Run: pytest -q tests/test_cfb_coherence.py

These tests do NOT touch the capture. Every fixture is synthetic, because the
point is to pin the estimator's behaviour rather than to re-measure Kalshi -
and because the capture is a disposable probe that will be deleted.

Two of these exist because the bug happened. `test_two_teams_are_not_one_ladder`
is brief C01's headline defect: 79 of 126 games quote BOTH teams on the spread,
on alternating rungs, and fitting them as a single ladder produced a median
favourite margin of 32 points. `test_sensitivity_rebinds_the_running_module`
is the sweep that reported four identical rows because it mutated a re-imported
copy of the module - a failure that looked exactly like perfect robustness.
"""
import math

import pytest

from research import cfb_coherence as cc


def ladder(lines, probs, spread=0.02):
    """Synthetic rungs in the shape `load_snapshot` produces."""
    return [{"market_id": f"KXNCAAFTOTAL-26SEP12AAABBB-{int(t)}",
             "line": t, "subject": f"Over {t}", "team": "AAA",
             "bid": max(0.0, p - spread / 2), "ask": min(1.0, p + spread / 2),
             "mid": p, "ts": 0.0, "spread": spread}
            for t, p in zip(lines, probs)]


def normal_ladder(mu, sigma, lines, **kw):
    n = cc.N01
    return ladder(lines, [1.0 - n.cdf((t - mu) / sigma) for t in lines], **kw)


# =============================================================================
# the fit recovers what it should
# =============================================================================

def test_fit_recovers_a_normal_ladder_exactly():
    """The estimator's whole claim: on the probit scale a normal ladder is a
    straight line, so mu comes back with no bias."""
    lines = [38.5, 44.5, 50.5, 56.5, 62.5, 68.5, 74.5]
    f = cc.fit_ladder(normal_ladder(54.0, 13.0, lines))
    assert f is not None
    assert f["mu"] == pytest.approx(54.0, abs=1e-6)
    assert f["sigma"] == pytest.approx(13.0, abs=1e-6)
    assert f["rmse"] < 1e-9


def test_a_flat_ladder_is_refused_rather_than_fitted():
    """Every rung at the same price carries no information about sigma. A
    zero or negative slope is not a distribution and must return None, not a
    number that happens to parse."""
    assert cc.fit_ladder(ladder([10.5, 20.5, 30.5, 40.5, 50.5, 60.5],
                                [0.5] * 6)) is None


def test_a_rising_ladder_is_refused():
    """S must not increase with the line. A ladder that does is stale or
    crossed, and fitting it yields a negative sigma."""
    assert cc.fit_ladder(ladder([10.5, 20.5, 30.5, 40.5, 50.5, 60.5],
                                [0.1, 0.2, 0.3, 0.5, 0.7, 0.9])) is None


def test_too_few_rungs_is_refused():
    assert cc.fit_ladder(normal_ladder(54.0, 13.0, [44.5, 50.5, 56.5])) is None


def test_wide_books_are_dropped_before_fitting():
    """An empty Kalshi book quotes 0/1 and prints a 0.500 mid. Left in, it is
    a rung that says the game is a coin flip at every line."""
    good = normal_ladder(54.0, 13.0, [38.5, 44.5, 50.5, 56.5, 62.5, 68.5])
    junk = ladder([80.5], [0.5], spread=0.9)
    assert len(cc._clean(good + junk, "mid")) == len(good)


# =============================================================================
# the defect that produced a 32-point favourite
# =============================================================================

def test_two_teams_are_not_one_ladder():
    """THE C01 BUG. Kalshi quotes both teams' spreads under one series on
    alternating rungs. Pooled, the two half-curves read as one ladder that
    decays far too slowly, and the fitted mean runs away."""
    fav = [{"market_id": f"KXNCAAFSPREAD-26SEP12AAABBB-AAA{int(t)}",
            "line": t, "subject": "", "team": "AAA", "bid": p - .01,
            "ask": p + .01, "mid": p, "ts": 0., "spread": .02}
           for t, p in zip([1.5, 9.5, 17.5, 25.5],
                           [.72, .52, .31, .15])]
    dog = [{"market_id": f"KXNCAAFSPREAD-26SEP12AAABBB-BBB{int(t)}",
            "line": t, "subject": "", "team": "BBB", "bid": p - .01,
            "ask": p + .01, "mid": p, "ts": 0., "spread": .02}
           for t, p in zip([1.5, 9.5, 17.5, 25.5],
                           [.24, .10, .03, .01])]
    sides = cc.split_by_team(fav + dog)
    assert set(sides) == {"AAA", "BBB"}
    assert len(sides["AAA"]) == 4 and len(sides["BBB"]) == 4

    pooled = cc.fit_ladder(fav + dog)
    two_sided = cc.fit_ladder(cc.margin_points(sides["AAA"], sides["BBB"], None))
    assert two_sided is not None
    # The pooled ladder either refuses outright or lands far from the honest
    # curve. Either way it must never be mistaken for the answer.
    assert pooled is None or abs(pooled["mu"] - two_sided["mu"]) > 3.0


def test_the_opponent_ladder_is_the_other_half_of_the_curve():
    """S_A(-L) = 1 - S_B(L), and the bid/ask flip when the side does: the
    complement of an ask is a bid."""
    dog = [{"market_id": "KXNCAAFSPREAD-26SEP12AAABBB-BBB10", "line": 9.5,
            "subject": "", "team": "BBB", "bid": 0.10, "ask": 0.14,
            "mid": 0.12, "ts": 0., "spread": 0.04}]
    got = cc.margin_points([], dog, None)
    assert len(got) == 1
    p = got[0]
    assert p["line"] == -9.5
    assert p["mid"] == pytest.approx(0.88)
    assert p["bid"] == pytest.approx(0.86)      # 1 - ask
    assert p["ask"] == pytest.approx(0.90)      # 1 - bid
    assert p["spread"] == pytest.approx(0.04)   # width is preserved


def test_the_moneyline_becomes_a_rung_at_a_half_point():
    """CFB plays overtime, so P(margin >= 1) is exactly P(win) and the
    moneyline is one more rung at t = 0.5."""
    ml = {"market_id": "KXNCAAFGAME-26SEP12AAABBB-AAA", "line": None,
          "subject": "AAA", "team": "AAA", "bid": 0.79, "ask": 0.81,
          "mid": 0.80, "ts": 0., "spread": 0.02}
    got = cc.margin_points([], [], ml)
    assert [(p["line"], p["mid"]) for p in got] == [(0.5, 0.80)]


# =============================================================================
# linearity is the whole method
# =============================================================================

def test_expectation_is_linear_so_coherent_ladders_deviate_by_zero():
    """Build two quarters and a half that agree by construction; the relation
    must return ~0. This is the null the coherence table is measured against."""
    lines_q = [2.5, 5.5, 8.5, 11.5, 14.5, 17.5, 20.5]
    lines_h = [8.5, 14.5, 20.5, 26.5, 32.5, 38.5, 44.5]
    g = {"key": "26SEP12AAABBB", "teams": {}, "money": {}, "qwin": {},
         "raw": {}, "fits": {
             "Q1": cc.fit_banded(normal_ladder(13.0, 7.0, lines_q)),
             "Q2": cc.fit_banded(normal_ladder(16.0, 8.0, lines_q)),
             "1H": cc.fit_banded(normal_ladder(29.0, 11.0, lines_h))}}
    rel = {r["name"]: r for r in cc.relations(g)}
    assert "Q1+Q2 = 1H" in rel
    assert abs(rel["Q1+Q2 = 1H"]["dev"]) < 1e-6


def test_a_planted_incoherence_is_recovered_at_its_planted_size():
    """Move the half three points off its own quarters and the relation must
    report three points, not two and not four."""
    lines_q = [2.5, 5.5, 8.5, 11.5, 14.5, 17.5, 20.5]
    lines_h = [8.5, 14.5, 20.5, 26.5, 32.5, 38.5, 44.5]
    g = {"key": "26SEP12AAABBB", "teams": {}, "money": {}, "qwin": {},
         "raw": {}, "fits": {
             "Q1": cc.fit_banded(normal_ladder(13.0, 7.0, lines_q)),
             "Q2": cc.fit_banded(normal_ladder(16.0, 8.0, lines_q)),
             "1H": cc.fit_banded(normal_ladder(26.0, 11.0, lines_h))}}
    rel = {r["name"]: r for r in cc.relations(g)}
    assert rel["Q1+Q2 = 1H"]["dev"] == pytest.approx(3.0, abs=1e-6)


# =============================================================================
# the band, and the cross-check
# =============================================================================

def test_the_band_widens_with_the_book_and_never_inverts():
    """`band` is what crossing the spread costs in points. A wider book must
    cost more, and E_bid can never exceed E_ask."""
    lines = [38.5, 44.5, 50.5, 56.5, 62.5, 68.5, 74.5]
    tight = cc.fit_banded(normal_ladder(54.0, 13.0, lines, spread=0.01))
    wide = cc.fit_banded(normal_ladder(54.0, 13.0, lines, spread=0.10))
    assert tight["e_lo"] <= tight["e_hi"]
    assert wide["e_lo"] <= wide["e_hi"]
    assert wide["band"] > tight["band"]


def test_the_model_free_estimator_agrees_on_a_well_covered_ladder():
    """`np_mean` exists to catch the normal fit being wrong about shape. On a
    genuinely normal ladder that starts low, the two must agree closely - if
    they did not, disagreement elsewhere would carry no information."""
    lines = [x + 0.5 for x in range(2, 46, 3)]
    pts = normal_ladder(22.0, 8.0, lines)
    fit = cc.fit_ladder(pts)
    e, unc, tail = cc.np_mean(pts)
    assert abs(e - fit["mu"]) < 1.0
    assert tail < 0.05


def test_monotonicity_counts_rungs_that_rise():
    pts = ladder([10.5, 20.5, 30.5, 40.5], [0.9, 0.95, 0.5, 0.6])
    bad, total = cc.monotonicity(pts)
    assert (bad, total) == (2, 4)


def test_isotonic_projection_is_non_increasing_and_preserves_length():
    rungs = [(1.0, 0.9), (2.0, 0.95), (3.0, 0.5), (4.0, 0.6), (5.0, 0.2)]
    out = cc._isotonic_decreasing(rungs)
    assert len(out) == len(rungs)
    assert [t for t, _ in out] == [t for t, _ in rungs]
    ss = [s for _, s in out]
    assert all(ss[i] >= ss[i + 1] - 1e-12 for i in range(len(ss) - 1))


# =============================================================================
# the sweep that lied
# =============================================================================

def test_sensitivity_rebinds_the_running_module():
    """`report_sensitivity` mutates MAX_SPREAD to prove the gate does not pick
    the answer. It originally did that through `import research.cfb_coherence`,
    which under `python research/cfb_coherence.py` is a SECOND module object -
    so the sweep changed nothing and printed four identical rows, which reads
    as flawless robustness. Assert the module rebinds its own global."""
    import inspect
    src = inspect.getsource(cc.report_sensitivity)
    # Match CODE, not prose: the comment above the fix quotes the broken line
    # on purpose, and a plain substring search cannot tell the explanation from
    # the bug. `tests/test_storage_paths.py` documents the same trap.
    code = [ln.split("#", 1)[0].strip() for ln in src.splitlines()]
    assert "global MAX_SPREAD" in code
    assert not any(ln.startswith("import research.cfb_coherence")
                   or ln.startswith("from research.cfb_coherence")
                   for ln in code)

    keep = cc.MAX_SPREAD
    try:
        cc.MAX_SPREAD = 0.001
        wide = ladder([10.5, 20.5, 30.5, 40.5, 50.5, 60.5],
                      [.9, .8, .6, .4, .2, .1], spread=0.05)
        assert cc._clean(wide, "mid") == []      # the gate really is consulted
    finally:
        cc.MAX_SPREAD = keep


def test_storage_comes_from_config():
    """Same rule as everywhere else: the capture's location is derived, never
    typed. Guarded generally by tests/test_storage_paths.py."""
    import os
    import config
    assert os.path.dirname(os.path.abspath(cc.CFB_DB)) == \
        os.path.dirname(os.path.abspath(config.DB_PATH))
