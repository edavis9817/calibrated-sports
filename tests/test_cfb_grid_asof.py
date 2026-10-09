"""c-49: the `grid_fit` mask is as-of (docs/C49-grid-fit-asof-preregistration.md).

Run: pytest -q tests/test_cfb_grid_asof.py

No test here opens a database. The league is `test_cfb_game._league` with a
grid on which the mask BINDS (a = 0.5: 46 of 48 points go bad somewhere, and
the point 2002 chooses first goes bad in 2003) - on c-39's registered grid it
never does, so a test built there could not fail.
"""
import ast
import itertools
import os
import warnings

import numpy as np
import pytest

from models import cfb_game as C
from research import cfb_game_forecast as R
from tests.test_cfb_game import _league

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SEASONS = (2001, 2002, 2003, 2004)


def _grid(a=0.5):
    return [C.CfbParams(*v) for v in itertools.product(
        [40.0, 80.0, 160.0], [0.0, 70.0], [0.0, 0.4], [a], [21.0, None], [0.0, 1.0], [0.0])]


def _fit(games, plist):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")              # a bad point's ratings overflow; it is never chosen
        return C.grid_fit(games, plist)


def _whole_walk(first_bad):
    """The mask c-39 shipped: bad in ANY game of the walk is bad for every season."""
    return np.where(first_bad != C.NEVER, 0, C.NEVER)


def test_the_mask_binds_on_this_league():
    # every test below leans on this; if the league stops exercising the mask they assert nothing
    games, plist = _league(seasons=SEASONS), _grid()
    seasons, sums, counts, fb = _fit(games, plist)
    assert seasons == list(SEASONS)
    by = {int(s): int((fb == s).sum()) for s in np.unique(fb)}
    assert by == {2001: 32, 2002: 5, 2003: 9, C.NEVER: 2}
    chosen = C.best_params(plist, seasons, sums, counts, fb, 2001, 2002)[0]
    assert fb[plist.index(chosen)] == 2003               # clean when chosen, bad afterwards


def test_each_season_fit_is_the_truncated_refit():
    # THE as-of property: season T's fit from the whole walk is the fit from the games before T alone
    games, plist = _league(seasons=SEASONS), _grid()
    seasons, sums, counts, fb = _fit(games, plist)
    for year in (2002, 2003, 2004, 2005):
        past = [g for g in games if g["season"] < year]
        s1, u1, c1, f1 = _fit(past, plist)
        want = C.best_params(plist, s1, u1, c1, f1, 2001, year)
        got = C.best_params(plist, seasons, sums, counts, fb, 2001, year)
        assert got[0] == want[0] and got[2] == want[2] and got[1] == pytest.approx(want[1], abs=1e-12)
        # and the choice set is exactly the points the truncated walk left clean
        assert np.array_equal(fb >= year, f1 == C.NEVER)


def test_the_whole_walk_mask_would_have_chosen_differently():
    # the test above must be able to fail: the old rule picks another point for 2002
    games, plist = _league(seasons=SEASONS), _grid()
    seasons, sums, counts, fb = _fit(games, plist)
    new = C.best_params(plist, seasons, sums, counts, fb, 2001, 2002)[0]
    old = C.best_params(plist, seasons, sums, counts, _whole_walk(fb), 2001, 2002)[0]
    assert new != old


def _plant(games, params):
    """f-32's planted case in miniature: one game after every other, the lowest-rated team winning
    at the highest-rated, far enough apart that the denominator under `params` is <= 0 in it."""
    und = []
    _pre, r, _s = C.run(games, params, undefined=und)
    live = {t for g in games if g["season"] == SEASONS[-1] for t in (g["home"], g["away"])}
    hi, lo = max(live, key=lambda t: r[t]), min(live, key=lambda t: r[t])
    conf = C.conferences(games)
    return dict(games[0], game_id="planted", season=SEASONS[-1], week=99,
                start_ts=max(g["start_ts"] for g in games) + 1.0, home=hi, away=lo,
                home_conf=conf[(SEASONS[-1], hi)], away_conf=conf[(SEASONS[-1], lo)],
                neutral=0, home_score=0, away_score=1, fit=True), r[hi] - r[lo] + params.hfa


def _ordered_league():
    """`_league` with the scores rewritten so the lower-numbered team always wins: no upsets, so
    no point goes bad on its own, and the ratings spread far enough for a plant to be feasible."""
    games = _league(seasons=SEASONS)
    for g in games:
        h, a = int(g["home"][1:]), int(g["away"][1:])
        g["home_score"], g["away_score"] = (20 + 2 * abs(h - a), 10) if h < a else (10, 20 + 2 * abs(h - a))
    return games


def test_a_game_after_every_forecast_moves_no_earlier_fit_or_forecast():
    games = _ordered_league()
    plist = [C.CfbParams(*v) for v in itertools.product(
        [20.0, 40.0], [0.0, 70.0], [0.0, 0.4], [0.3, 1.2], [21.0, None], [0.0, 1.0], [0.0])]
    w0 = R.Walk(games, plist, mov=True)
    assert (w0.first_bad == C.NEVER).all()               # nothing is bad until the plant
    target = w0.params(2003)                             # Walk fits from R.FIT_FROM = 2002
    j = plist.index(target)
    assert target.a == 1.2
    fake, gap = _plant(games, target)
    assert gap >= 1000.0 * target.a                      # the plant is feasible
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        w1 = R.Walk(games + [fake], plist, mov=True)
    assert w1.first_bad[j] == SEASONS[-1]                # the plant IS seen ...
    assert 0 < (w1.first_bad == SEASONS[-1]).sum() < len(plist)
    assert (w1.first_bad >= 2005).sum() < (w0.first_bad >= 2005).sum()   # ... by the season after it
    for year in SEASONS[2:]:
        assert w1.params(year) == w0.params(year)
    idx = [i for i, g in enumerate(games) if g["season"] > 2002]
    assert len(idx) == 120 and all(w1.p(i) == w0.p(i) for i in idx)
    # the plant's game was substituted under the point it broke, and recorded - not raised
    assert w1.undefined[target] == [(len(games), SEASONS[-1])] and w0.undefined[target] == []
    # and the old rule fails this same case: the planted store loses the point for 2003
    old0 = C.best_params(plist, w0.seasons, w0.sums, w0.counts, _whole_walk(w0.first_bad), 2002, 2003)[0]
    old1 = C.best_params(plist, w1.seasons, w1.sums, w1.counts, _whole_walk(w1.first_bad), 2002, 2003)[0]
    assert old0 == target and old1 != target


def test_scalar_walk_substitutes_only_when_asked_and_matches_the_grid():
    games, plist = _league(seasons=SEASONS), _grid()
    seasons, sums, counts, fb = _fit(games, plist)
    j = int(np.flatnonzero(fb == 2003)[0])
    with pytest.raises(ValueError):
        C.run(games, plist[j])                           # the default still refuses, loudly
    und = []
    pre = C.run(games, plist[j], undefined=und)[0]
    assert und and min(s for _i, s in und) == 2003 == fb[j]
    for s_i, s in enumerate(seasons):                    # through and after the substitution
        want = sum(C.log_loss(p, 1.0 if games[i]["home_score"] > games[i]["away_score"] else 0.0)
                   for i, p in pre if games[i]["season"] == s)
        assert sums[s_i, j] == pytest.approx(want, rel=1e-9)
    clean = int(np.flatnonzero(fb == C.NEVER)[0])
    und = []
    assert C.run(games, plist[clean], undefined=und)[0] == C.run(games, plist[clean])[0] and und == []


def test_stale_call_sites_raise():
    games, plist = _league(), _grid(a=None)
    with pytest.raises(ValueError):
        seasons, sums, counts = C.grid_fit(games, plist)             # the three-value unpack
    seasons, sums, counts, fb = C.grid_fit(games, plist)
    with pytest.raises(TypeError):
        C.best_params(plist, seasons, sums, counts, 2001, 2003)      # the call without first_bad
    assert not np.isnan(sums).any()                                  # nothing is masked in the sums


def test_no_point_defined_raises_rather_than_choosing_a_bad_one():
    games, plist = _league(seasons=SEASONS), _grid(a=0.3)            # all 48 points bad in 2001
    seasons, sums, counts, fb = _fit(games, plist)
    assert (fb == 2001).all()
    with pytest.raises(ValueError, match="no grid point is defined"):
        C.best_params(plist, seasons, sums, counts, fb, 2001, 2002)


def _calls(attr):
    """Every `C.<attr>(...)` call outside this file, track F's directory and the environment."""
    out = []
    for base, dirs, files in os.walk(ROOT):
        dirs[:] = [d for d in dirs if d not in (".venv", ".git", "node_modules", "f32_grid_mask")]
        for name in files:
            if not name.endswith(".py") or name == os.path.basename(__file__):
                continue
            path = os.path.join(base, name)
            with open(path, encoding="utf-8") as f:
                tree = ast.parse(f.read())
            parents = {c: n for n in ast.walk(tree) for c in ast.iter_child_nodes(n)}
            for node in ast.walk(tree):
                if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                        and node.func.attr == attr and isinstance(node.func.value, ast.Name)
                        and node.func.value.id == "C"):
                    out.append((os.path.relpath(path, ROOT), node, parents.get(node)))
    return out


def test_every_caller_uses_the_new_shape():
    # by AST over the CALLERS, not the definition: a stale reference is a runtime error, not a test failure
    fits, bests = _calls("grid_fit"), _calls("best_params")
    assert len(fits) >= 2 and len(bests) >= 2                        # the walk reached real call sites
    for path, _node, parent in fits:
        if isinstance(parent, ast.Assign) and isinstance(parent.targets[0], ast.Tuple):
            assert len(parent.targets[0].elts) == 4, path
    for path, node, _parent in bests:
        assert len(node.args) == 7, path
