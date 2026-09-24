"""Strength of schedule (`analytics.schedule`), against synthetic seasons.

Pure: every test builds its own season in memory, so nothing here needs a store
and nothing writes. Each estimator is shown recovering an effect that was
PLANTED, and shown returning the other answer where none was - an assertion that
can only come out one way is not measuring the estimator.
"""
import math
import re

import numpy as np
import polars as pl
import pytest

from analytics import schedule as S
from analytics.intervals import Estimate

TEAMS = list("ABCDEFGH")


def _round_robin(teams):
    """Circle method: every team plays every other once, len(teams)-1 weeks."""
    ts = list(teams)
    weeks = []
    for _w in range(len(ts) - 1):
        weeks.append([(ts[i], ts[-1 - i]) for i in range(len(ts) // 2)])
        ts = [ts[0]] + [ts[-1]] + ts[1:-1]
    return weeks


def season(eff=None, opp=None, noise=0.0, seed=1, zero_rows=False, weeks=None):
    """A synthetic season. `eff[D]` adds yards per target / carry to everyone
    facing D; `opp[D]` multiplies the opposing WR targets and RB carries."""
    eff, opp = eff or {}, opp or {}
    rng = np.random.default_rng(seed)
    rows = []
    sched = _round_robin(TEAMS)
    if weeks:
        sched = sched[:weeks]
    for w, games in enumerate(sched, start=1):
        for home, away in games:
            gid = "2030_%02d_%s_%s" % (w, away, home)
            for team, dteam in ((home, away), (away, home)):
                m = opp.get(dteam, 1.0)
                for slot, base_rate in (("1", 9.0), ("2", 7.0)):
                    t = int(round(6 * m))
                    y = t * (base_rate + eff.get(dteam, 0.0)) + noise * rng.normal()
                    rows.append((2030, w, "REG", gid, team + "WR" + slot, team,
                                 dteam, "WR", t, 0, y, 0.0))
                c = int(round(15 * m))
                ry = c * (4.0 + eff.get(dteam, 0.0)) + noise * rng.normal()
                rows.append((2030, w, "REG", gid, team + "RB", team, dteam, "RB",
                             0, c, 0.0, ry))
                if zero_rows:
                    # a WR who played and caught nothing: nflverse has NO ROW
                    # for him; here he is present as zeros, to be removed.
                    rows.append((2030, w, "REG", gid, team + "WR3", team, dteam,
                                 "WR", 0, 0, 0.0, 0.0))
    return pl.DataFrame(rows, orient="row", schema=[
        "season", "week", "season_type", "game_id", "player_id", "team",
        "opponent_team", "position_group", "targets", "carries",
        "receiving_yards", "rushing_yards"])


def _points(df, group, measure):
    so = S.Season(df, group)
    pt = getattr(so, measure)(np.ones((1, so.N)))[0]
    return {t: pt[i] for i, t in enumerate(so.teams)}


# =============================================================================
# the estimators recover what was planted, and nothing where nothing was
# =============================================================================

def test_efficiency_recovers_a_planted_defense_effect():
    got = _points(season(eff={"C": 3.0}), "wr", "efficiency")
    # EXACT, because the season is noiseless. Leave-one-out excludes the C game
    # from every baseline it is measured against, so C reads its full +3. A
    # baseline that INCLUDED the game reads 3 - 3/7 = 2.571 - that mutation was
    # run, and a tolerance of 0.75 let it pass, which is why this is exact.
    assert got["C"] == pytest.approx(3.0, abs=1e-9)
    # The price of leave-one-out, stated rather than hidden: C's +3 sits in the
    # baseline of every other game its opponents played, 1 game in 6, and every
    # other defense faced opponents who all played C. So every other defense
    # reads -3/6 on 6/7 of its rows = -3/7. Effects are RELATIVE to the rest of
    # the league, and a planted outlier costs the others E/(games - 1).
    assert all(v == pytest.approx(-3 / 7, abs=1e-9)
               for t, v in got.items() if t != "C")


def test_efficiency_reads_zero_when_no_defense_differs():
    got = _points(season(), "rb", "efficiency")
    assert all(abs(v) < 1e-9 for v in got.values())


def test_efficiency_is_relative_to_the_players_own_rate_not_to_the_league():
    # WR1 averages 9 a target and WR2 7. Every defense faced both, so a defense
    # that faced only the good one would be charged for him under a league
    # baseline; under his own it is not.
    df = season().filter(~((pl.col("opponent_team") == "D")
                           & (pl.col("player_id").str.ends_with("WR2"))))
    got = _points(df, "wr", "efficiency")
    assert abs(got["D"]) < 1e-9


def test_opportunity_recovers_a_planted_game_script_effect():
    got = _points(season(opp={"F": 1.5}), "rb", "opportunity")
    # round(15 * 1.5) = 22 carries against a baseline of exactly 15.
    assert got["F"] == pytest.approx(22 / 15 - 1, abs=1e-9)
    assert all(-0.1 < v < 0 for t, v in got.items() if t != "F")


def test_opportunity_and_efficiency_are_separate_measurements():
    # A defense that changes volume and not efficiency must show up in one
    # and not the other - the reason the two are not one rating.
    df = season(opp={"F": 1.5})
    assert _points(df, "rb", "opportunity")["F"] > 0.3
    assert abs(_points(df, "rb", "efficiency")["F"]) < 1e-9


def test_opportunity_is_immune_to_missing_zero_rows():
    # nflverse writes no row for a player who played and recorded nothing. The
    # group total must not care whether he is there as zeros or absent.
    with_zeros = season(opp={"B": 1.4}, zero_rows=True)
    without = with_zeros.filter(pl.col("targets") + pl.col("carries") > 0)
    a = _points(with_zeros, "wr", "opportunity")
    b = _points(without, "wr", "opportunity")
    assert a == pytest.approx(b)


# =============================================================================
# the interval
# =============================================================================

def test_scale_is_huge_at_two_games_and_modest_at_a_season():
    assert S.scale(1) == math.inf
    assert S.scale(2) == pytest.approx(12.706 / 1.95996 * math.sqrt(3), rel=1e-3)
    assert 1.1 < S.scale(17) < 1.2
    assert all(S.scale(n) > S.scale(n + 1) for n in range(2, 30))


def test_two_games_give_a_wide_interval_not_a_confident_one():
    df = season(eff={"C": 3.0}, noise=6.0, weeks=2)
    so = S.Season(df, "rb")
    raw = S.defense_effects(so, "efficiency", draws=400, method="raw")
    t = S.defense_effects(so, "efficiency", draws=400, method="t")
    widths = [(t[d].hi - t[d].lo) / (raw[d].hi - raw[d].lo)
              for d in t if raw[d].hi > raw[d].lo]
    assert widths and min(widths) > 5, widths
    assert all(e.n == 2 for e in t.values())


def test_one_game_is_unbounded_and_is_not_a_value():
    point = np.array([1.0])
    e = S._interval(1.0, point, 1, 5)
    assert e.lo == -math.inf and e.hi == math.inf


def test_each_defense_draws_independently_and_reproduces():
    a1 = S.weights(50, 20, "efficiency|wr|DAL|")
    a2 = S.weights(50, 20, "efficiency|wr|DAL|")
    b = S.weights(50, 20, "efficiency|wr|PHI|")
    assert np.array_equal(a1, a2)
    assert not np.allclose(a1, b)
    assert np.allclose(a1.sum(axis=1), 50)
    assert (a1 > 0).all()   # Dirichlet: no game ever drops out of a draw


# =============================================================================
# rest of season
# =============================================================================

def test_rest_of_season_combines_opponents_and_carries_the_interval():
    df = season(eff={"C": 3.0}, noise=4.0)
    so = S.Season(df, "wr")
    sched = {"A": [(9, "C"), (10, "D")], "B": [(9, "E"), (10, "F")]}
    rows = S.rest_of_season(so, "efficiency", sched, draws=400)
    slices = {(o, sl): e for o, sl, e in rows}
    assert set(slices) == {("A", "w09_C"), ("A", "w10_D"), ("A", ""),
                           ("B", "w09_E"), ("B", "w10_F"), ("B", "")}
    comb = slices[("A", "")]
    parts = [slices[("A", "w09_C")].est, slices[("A", "w10_D")].est]
    assert comb.est == pytest.approx(sum(parts) / 2)
    assert comb.lo < comb.est < comb.hi
    # n is the opponent-games the combination was measured on.
    assert comb.n == slices[("A", "w09_C")].n + slices[("A", "w10_D")].n


def test_rest_of_season_refuses_an_opponent_it_has_never_measured():
    so = S.Season(season(), "wr")
    with pytest.raises(AssertionError, match="not all in"):
        S.rest_of_season(so, "efficiency", {"A": [(9, "ZZ")]}, draws=50)


@pytest.mark.parametrize("e, words", [
    (Estimate(0.8, 0.2, 1.4, 17, "x"), "measurably easier"),
    (Estimate(-0.8, -1.4, -0.2, 17, "x"), "measurably harder"),
    (Estimate(0.3, -0.9, 1.5, 2, "x"), "not measurably easier or harder"),
    (Estimate(0.3, -math.inf, math.inf, 1, "x"), "not measurable yet"),
])
def test_the_verdict_can_return_every_answer(e, words):
    assert S.verdict(e) == words


# =============================================================================
# the registry
# =============================================================================

KEY = re.compile(r"^[a-z0-9_]+(\.[a-z0-9_]+)+$")   # the contract's metric key


@pytest.mark.parametrize("measure", ["efficiency", "opportunity"])
@pytest.mark.parametrize("group", sorted(S.GROUPS))
@pytest.mark.parametrize("rest", [False, True])
def test_every_metric_is_a_valid_registry_entry(measure, group, rest):
    m = S.metric_for(measure, group, rest=rest, live=2026)
    assert KEY.match(m.key), m.key
    assert m.subject_type == "team" and m.block == "game"
    if rest:
        assert m.floor_season == 2026   # values are the live season only
    # No rank, no numbering, no composite: the brief's one hard exclusion.
    assert not re.search(r"rank|rating|score|tier", m.key + m.label, re.I)


def test_an_unmeasured_opponent_makes_the_schedule_unmeasurable_not_nan():
    # H's defense has no WR row that clears the baseline floor, so its effect
    # is undefined. A schedule containing H is "not measurable yet" - it must
    # neither crash nor publish a NaN. This crashed the first live publish.
    df = season(noise=2.0).filter(~((pl.col("opponent_team") == "H")
                                    & (pl.col("position_group") == "WR")))
    so = S.Season(df, "wr")
    rows = S.rest_of_season(so, "efficiency", {"A": [(9, "H"), (10, "C")]},
                            draws=50)
    got = {sl: e for _o, sl, e in rows}
    assert got["w09_H"].est is None and got["w09_H"].n == 0
    assert S.verdict(got[""]) == "not measurable yet"
    assert got["w10_C"].est is not None
