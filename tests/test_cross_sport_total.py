"""c-44: the cross-sport total comparison's machinery (`research.cross_sport_total`).

Run: pytest -q tests/test_cross_sport_total.py

No test here opens a database: every game is synthetic. What is pinned: the
model and both baselines read nothing at or after a game's own start; the two
baselines are what the pre-registration says, by hand; the re-weighting; the
between-sport draws are independent per sport; the MDE takes no outcome; the
verdict and the reading can return every answer they have; and the registered
family sizes.
"""
import inspect
import json

import numpy as np
import pytest

from research import cross_sport_total as X

DAY = 86400.0


def _games(seed, teams=16, spread=4.0, level=22.0, noise=9.0, prefix="g", first=2001, last=2025):
    rng = np.random.default_rng(seed)
    off, dfn = rng.normal(0, spread, teams), rng.normal(0, spread, teams)
    out = []
    for season in range(first, last + 1):
        off = 0.6 * off + rng.normal(0, 0.8 * spread, teams)
        dfn = 0.6 * dfn + rng.normal(0, 0.8 * spread, teams)
        t0 = (season - 1970) * 365.25 * DAY
        for week in range(1, 11):
            order = rng.permutation(teams)
            for j in range(0, teams, 2):
                h, a = int(order[j]), int(order[j + 1])
                hs = max(0, int(round(level + off[h] + dfn[a] + rng.normal(0, noise))))
                as_ = max(0, int(round(level + off[a] + dfn[h] + rng.normal(0, noise))))
                out.append({"game": "%s-%d-%02d-%02d" % (prefix, season, week, j), "season": season,
                            "week": week, "regular": week <= 9, "ts": t0 + week * 7 * DAY + (j % 3) * 3600.0,
                            "home": "T%d" % h, "away": "T%d" % a, "hs": hs, "as_": as_})
    return out


def _at(F, game):
    return int(np.flatnonzero(F["game"] == game)[0])


def test_a_forecast_does_not_read_its_own_game_or_anything_later():
    games = _games(1)
    F, _y = X.build(games)
    target = next(g for g in games if g["season"] == 2015 and g["week"] == 6)
    moved = [dict(g, hs=g["hs"] + 40, as_=g["as_"] + 40) if g["ts"] > target["ts"] - X.LAG else g
             for g in games if g["season"] <= 2015]
    moved += [g for g in games if g["season"] > 2015]
    F2, _y2 = X.build(moved)
    i, j = _at(F, target["game"]), _at(F2, target["game"])
    for k in ("mu", "league", "pair", "p_model", "p_pair", "s_model", "q", "sigma"):
        assert F[k][i] == F2[k][j], k
    # and the check discriminates: moving a game that started BEFORE the lag moves the forecast
    early = [dict(g, hs=g["hs"] + 40) if g["season"] == 2015 and g["week"] == 2 else g for g in games]
    F3, _ = X.build(early)
    assert F3["mu"][_at(F3, target["game"])] != F["mu"][i]


def test_the_two_baselines_by_hand():
    games = _games(2)
    F, _y = X.build(games)
    target = next(g for g in games if g["season"] == 2010 and g["week"] == 5)
    seen = [g for g in games if g["season"] in (2009, 2010) and g["ts"] <= target["ts"] - X.LAG]
    league = np.mean([g["hs"] + g["as_"] for g in seen])
    own = [np.mean([g["hs"] + g["as_"] for g in seen if t in (g["home"], g["away"])])
           for t in (target["home"], target["away"])]
    i = _at(F, target["game"])
    assert F["league"][i] == pytest.approx(league)
    assert F["pair"][i] == pytest.approx(np.mean(own))
    assert F["e"][i] == pytest.approx(np.mean(own) - league)


def test_bins_are_the_registered_eight_with_closed_lower_edges():
    b = np.searchsorted(X.BIN_EDGES, np.array([-9.0, -6.0, -4.1, -0.01, 0.0, 3.9, 6.0, 40.0]), side="right")
    assert b.tolist() == [0, 1, 1, 3, 4, 5, 7, 7] and X.N_BINS == 8


def test_reweighting_to_own_shares_is_the_raw_mean_and_to_other_shares_is_by_hand():
    F, y = X.build(_games(3))
    t = X.Tab(F, y)
    cnt, S = X.sums(t, np.arange(t.n))
    own = cnt / cnt.sum()
    assert X.means(cnt, S, own) == pytest.approx(X.means(cnt, S))
    w = np.zeros(X.N_BINS)
    w[3], w[4] = 0.25, 0.75
    want = 0.25 * t.L[t.bin == 3].mean(axis=0) + 0.75 * t.L[t.bin == 4].mean(axis=0)
    assert X.means(cnt, S, w) == pytest.approx(want)
    # a bin the weights need and the sample lacks is nan, never a silent zero
    cnt2, S2 = cnt.copy(), S.copy()
    cnt2[4], S2[4] = 0.0, 0.0
    assert np.isnan(X.means(cnt2, S2, w)).all()
    w0 = w.copy()
    w0[4], w0[3] = 0.0, 1.0
    assert not np.isnan(X.means(cnt2, S2, w0)).any()


def test_between_sport_draws_are_independent_per_sport():
    F, y = X.build(_games(4))
    t = X.Tab(F, y)
    a, b = X.stat(X.boot(t, X.SEED_NFL, 60)), X.stat(X.boot(t, X.SEED_NFL, 60))
    c = X.stat(X.boot(t, X.SEED_CFB, 60))
    assert np.ptp(a[1]["d_league"] - b[1]["d_league"]) == 0.0        # a shared seed: zero-width difference
    assert np.ptp(a[1]["d_league"] - c[1]["d_league"]) > 0.0
    assert X.SEED_NFL != X.SEED_CFB != X.SEED_OWN


def test_the_draw_sequence_is_c24s():
    F, y = X.build(_games(5))
    t = X.Tab(F, y)
    _e, dr = X.stat(X.boot(t, 24, 5))
    rng = np.random.default_rng(24)
    want = []
    for _ in range(5):
        i = rng.integers(0, t.n, t.n)
        want.append(t.L[i, 0].mean() - t.L[i, 1].mean())
    assert dr["d_league"] == pytest.approx(np.array(want))


def test_the_mde_takes_no_outcome_and_tracks_the_bootstrap():
    assert list(inspect.signature(X.formula_se).parameters) == ["F", "mask"]
    F, y = X.build(_games(6, teams=32))
    se = X.formula_se(F)
    t = X.Tab(F, y)
    est, dr = X.stat(X.boot(t, 24, 400))
    for name in ("d_league", "d_pair", "c"):
        assert 0.5 < se[name] / dr[name].std() < 2.0, name
    table = X.mde_table(se, X.formula_se(X.build(_games(7, spread=7.0, prefix="c"))[0]))
    assert sum(1 for k in table if not k.startswith(("R ", "B "))) == 17


def test_a_model_with_signal_beats_both_baselines_and_one_without_does_not():
    F, y = X.build(_games(8, teams=32, spread=5.0))
    est, _ = X.stat(X.boot(X.Tab(F, y), 24, 2))
    assert est["d_pair"] < 0 and est["c"] < 0 and est["d_league"] < 0
    F0, y0 = X.build(_games(9, teams=32, spread=0.0))        # no team effect at all
    est0, _ = X.stat(X.boot(X.Tab(F0, y0), 24, 2))
    assert est0["c"] > 0                                     # an unshrunk pair mean is worse than the league mean


@pytest.mark.parametrize("held,success,spread,lone", [
    ({a: dict.fromkeys(X.PART1, True) for a in ("nfl", "cfb", "cfb_at_nfl")}, True, [], {}),
    ({"nfl": dict.fromkeys(X.PART1, True), "cfb": dict.fromkeys(X.PART1, True),
      "cfb_at_nfl": {"d_league": True, "d_pair": False, "c": True}}, False, ["d_pair"], {}),
    ({"nfl": {"d_league": True, "d_pair": False, "c": True}, "cfb": dict.fromkeys(X.PART1, True),
      "cfb_at_nfl": dict.fromkeys(X.PART1, True)}, False, [], {"d_pair": "cfb"}),
    ({"nfl": dict.fromkeys(X.PART1, True), "cfb": {"d_league": True, "d_pair": True, "c": False},
      "cfb_at_nfl": {"d_league": True, "d_pair": True, "c": False}}, False, [], {"c": "nfl"}),
    ({a: dict.fromkeys(X.PART1, False) for a in ("nfl", "cfb", "cfb_at_nfl")}, False, [], {}),
])
def test_the_verdict_returns_every_answer(held, success, spread, lone):
    got = X.verdict(held)
    assert (got[0], got[2], got[3]) == (success, spread, lone)
    assert bool(got[1]) != success


def test_the_reading_returns_every_answer():
    assert "accounted for by the spread" in X.reading("above", "contains 0")
    assert X.reading("below", "below") == "not accounted for by the spread alone"
    for raw, rw in (("contains 0", "contains 0"), ("above", "below"), ("contains 0", "above"), ("not read", "below")):
        assert X.reading(raw, rw).startswith("neither registered reading")


def test_ok_needs_both_the_sign_and_holm():
    r = {"est": -1.0, "lo": -2.0, "hi": -0.1, "games": 100}
    assert not X.ok(dict(r, holm_significant=False)) and X.ok(dict(r, holm_significant=True))
    assert not X.ok({"est": -1.0, "lo": -2.0, "hi": 0.1, "games": 100, "holm_significant": True})


def test_a_whole_run_has_the_registered_family_sizes_and_mde_only_computes_no_loss(tmp_path, monkeypatch):
    nfl, cfb = _games(10, teams=16, spread=3.0), _games(11, teams=40, spread=7.0, level=27.0, prefix="c")
    rng = np.random.default_rng(0)
    line_c = {g["game"]: 54.0 + rng.normal() for g in cfb if g["season"] >= 2016}
    line_n = {g["game"]: 44.0 + rng.normal() for g in nfl}
    monkeypatch.setattr(X, "load_nfl", lambda out: (nfl, line_n, {"versions": "synthetic"}))
    monkeypatch.setattr(X, "load_cfb", lambda out: (cfb, line_c, {}))
    built = []
    monkeypatch.setattr(X, "Tab", lambda *a, **k: built.append(1) or _Tab(*a, **k))
    mde = tmp_path / "mde.json"
    assert X.main(["--mde-only", "--json-out", str(mde)]) == 0
    assert built == [] and "primary" not in json.loads(mde.read_text())
    full = tmp_path / "full.json"
    assert X.main(["--json-out", str(full), "--draws", "40"]) == 0
    res = json.loads(full.read_text())
    assert res["counts"]["families"] == {"primary": 17, "reverse": 7, "brier": 8, "cuts": 40, "price": 2}
    assert res["counts"]["registered_intervals"] == 74 and res["counts"]["specifications"] == 9
    assert res["price"]["seasons"] == list(range(2016, 2026))        # the coverage rule picked the seasons
    assert res["price"]["nfl"]["of"] < len(nfl)                      # and applied them to the NFL too
    assert "NOT A TIMESTAMPED CLOSE" in res["price"]["sentence"]
    assert set(res["primary"]["part2_reading"]) == {"league", "pair"}


_Tab = X.Tab
