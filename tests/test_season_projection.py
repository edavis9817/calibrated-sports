"""a-55: the projected final record - simulation, walk-forward, published shape.

Run: pytest -q tests/test_season_projection.py

Every verdict the file can carry is driven to each of its answers, and the
invariant that makes the page's numbers agree with each other - the headline
mean IS the record so far plus the sum of the per-game win probabilities - is
shown refusing a file where it does not hold.
"""
import copy
import functools

import numpy as np
import pytest

from jobs import season_export as X
from jobs import season_model as J
from jobs import season_projection as P
from jobs import metric_registry as MR
from models import season as M

TEAMS = list(MR.NFL_TEAMS)
DIVS = ["AFC East", "AFC North", "AFC South", "AFC West",
        "NFC East", "NFC North", "NFC South", "NFC West"]


def league(seasons=(2022, 2023, 2024, 2025, 2026), weeks=6, played_2026=2, seed=3):
    """A synthetic 32-team league: `weeks` random pairings a season, scores from
    fixed team strengths, 2026 played through `played_2026`."""
    rng = np.random.default_rng(seed)
    strength = {t: rng.normal(0, 6) for t in TEAMS}
    groupings = {t: (DIVS[i // 4][:3], DIVS[i // 4]) for i, t in enumerate(TEAMS)}
    games, gid = [], 0
    for y in seasons:
        for w in range(1, weeks + 1):
            perm = rng.permutation(TEAMS)
            for h, a in zip(perm[::2], perm[1::2]):
                gid += 1
                hs = as_ = None
                if y < 2026 or w <= played_2026:
                    m = strength[h] - strength[a] + 2 + rng.normal(0, 13)
                    hs, as_ = (24 + int(round(m / 2)), 24 - int(round(m / 2)))
                    if hs == as_:
                        hs += 3
                games.append(dict(game_id=str(gid), season=y, week=w, game_type="REG",
                                  kickoff_ts=y * 1e6 + w * 1e3 + gid, home=str(h), away=str(a),
                                  home_score=hs, away_score=as_, data_version="t"))
    return games, groupings


@functools.lru_cache(maxsize=1)
def make_world():
    """Built once per process: test_season_model and test_metric_registry
    publish the division file beside this projection too."""
    games, groupings = league()
    losses = J.game_losses(games, [M.EloParams(20.0, 50.0, 0.33), M.EloParams(30.0, 25.0, 0.5)])
    rows, sigma, crps_by = P.walk_forward(J, games, groupings, losses, n=200,
                                          seasons=[2024, 2025], fit_from=2023, log=lambda *a: None)
    cur = P.current(J, games, groupings, losses, crps_by, n=2000)
    return dict(games=games, groupings=groupings, losses=losses, rows=rows, sigma=sigma,
                crps_by=crps_by, cur=cur)


@pytest.fixture(scope="module")
def world():
    return make_world()


def fake_result(world):
    """The division tests' synthetic record, plus a real projection over the
    synthetic league (so the per-team registry rows resolve for all 32)."""
    from tests.test_season_model import _fake_result
    res = _fake_result()
    res["current"]["games_played"] = sum(g["home_score"] is not None for g in world["games"]
                                         if g["season"] == 2026)
    summ = P.summarise(world["rows"])
    # two synthetic seasons cannot be read; give the record readable blocks so
    # the file carries the verdict the real record does, and drive it both ways
    for b in summ["vs"]:
        summ["vs"][b].update(n_blocks=24, interval=[-1.0, -0.5], verdict="better_than")
    for lvl in summ["coverage"]:
        summ["coverage"][lvl].update(n_blocks=24, verdict="within")
    res.update(projection_summary=summ, projection_sigma=world["sigma"],
               projection_current=world["cur"], projection_rows=world["rows"],
               n_proj_walk=200)
    return res


def build(world, **kw):
    return X.build_projection(fake_result(world), generated_at="2026-09-29T00:00:00Z", **kw)


# ------------------------------------------------------------------ the simulation

def test_crps_matches_the_brute_force_definition():
    rng = np.random.default_rng(0)
    W = rng.integers(0, 10, size=(60, 3)).astype(float)
    a = np.array([3.0, 7.5, 0.0])
    brute = [np.abs(W[:, j] - a[j]).mean()
             - 0.5 * np.abs(W[:, j][:, None] - W[:, j][None, :]).mean() for j in range(3)]
    assert np.allclose(M.crps(W, a), brute)


def test_a_one_dimensional_call_is_unchanged_by_the_two_dimensional_path():
    """The division model's figures must not move because the projection added
    per-simulation probabilities to `simulate`."""
    s = M.Season(teams=["A", "B", "C", "D"], division={t: "X" for t in "ABCD"},
                 conference={t: "Y" for t in "ABCD"},
                 games=[("A", "B", 1, 1.0), ("C", "D", 1, None), ("A", "C", 2, None)])
    p = np.array([0.5, 0.3, 0.8])
    one = M.simulate(s, p, 500, np.random.default_rng(9))
    two = M.simulate(s, np.repeat(p[None, :], 500, axis=0), 500, np.random.default_rng(9))
    assert (one == two).all()


def test_sigma_zero_draws_the_estimate_and_sigma_spreads_it():
    s = M.Season(teams=["A", "B"], division={"A": "X", "B": "X"},
                 conference={"A": "Y", "B": "Y"}, games=[("A", "B", 1, None)])
    r = {"A": 1600.0, "B": 1400.0}
    d0 = M.rating_draws(s, r, 0.0, 100, np.random.default_rng(1))
    assert (d0 == np.array([1600.0, 1400.0])).all()
    d = M.rating_draws(s, r, 100.0, 20000, np.random.default_rng(1))
    assert abs(d[:, 0].std() - 100.0) < 3 and abs(d[:, 0].mean() - 1600.0) < 3


def test_rating_uncertainty_widens_the_final_record():
    """The reason for the one departure from the division model, on the other
    input: sigma > 0 produces a wider distribution than fixed ratings."""
    games, groupings = league(played_2026=1)
    s = J.season_at(games, groupings, 2026, 99)
    r = {t: 1500.0 + 40 * i for i, t in enumerate(TEAMS)}
    sd = []
    for sigma in (0.0, 120.0):
        rng = np.random.default_rng(4)
        R = M.simulate(s, M.p_home_draws(s, M.rating_draws(s, r, sigma, 4000, rng), 50.0),
                       4000, rng)
        sd.append(M.final_wins(s, R).std(axis=0).mean())
    assert sd[1] > sd[0]


def test_sigma_for_a_season_is_chosen_on_earlier_seasons_only(world):
    for y, v in world["sigma"].items():
        assert v["fit_seasons"][1] < y
        assert v["sigma"] in P.SIGMA_GRID
    # and the chosen sigma really is the argmin over the earlier seasons
    y = 2025
    fit = [2023, 2024]
    best = min(P.SIGMA_GRID, key=lambda sg: (np.mean([world["crps_by"][(f, sg)] for f in fit]), sg))
    assert world["sigma"][y]["sigma"] == best


def test_pace_is_undefined_before_a_team_plays_and_excluded(world):
    rows = world["rows"]
    assert any(r["pace"] is None for r in rows)
    s = P.summarise(rows)
    assert s["n_forecasts"] == sum(r["pace"] is not None for r in rows)


def test_a_three_and_oh_team_is_not_projected_to_go_unbeaten(world):
    """The brief's check, on the synthetic league: pace says every unbeaten team
    finishes unbeaten; the model does not."""
    unbeaten = [t for t in world["cur"]["teams"] if t["losses"] == 0 and t["games_played"] >= 2]
    assert unbeaten
    for t in unbeaten:
        assert t["mean"] < t["games_total"] - 0.5


# ------------------------------------------------------------------ verdicts reach every value

@pytest.mark.parametrize("iv,n,want", [([-2, -1], 24, "better_than"), ([-1, 1], 24, "no_better_than"),
                                       ([1, 2], 24, "worse_than"), ([-2, -1], 4, "not_readable")])
def test_comparison_verdict_reaches_every_value(iv, n, want):
    assert P.verdict({"interval": iv, "n_blocks": n}) == want


@pytest.mark.parametrize("pace,coin,want,show", [
    ("better_than", "better_than", "beats_baselines", "model"),
    ("better_than", "no_better_than", "no_better_than_a_baseline", "standings_coin_flip"),
    ("worse_than", "better_than", "worse_than_a_baseline", "standings_coin_flip"),
    ("not_readable", "better_than", "not_readable", "standings_coin_flip")])
def test_record_verdict_and_display_reach_every_value(pace, coin, want, show):
    v = X.proj_record_verdict({"pace": {"verdict": pace}, "standings_coin_flip": {"verdict": coin}})
    assert v == want and X.proj_display(v)["show"] == show


def test_coverage_verdict_reaches_every_value():
    def rows(hit, mass, seasons=8):
        return [{"season": 2000 + i, "in80": h, "mass80": mass}
                for i in range(seasons) for h in hit]
    assert P.coverage(rows([True, False], 0.5), "80")["verdict"] == "within"
    assert P.coverage(rows([True, False, False, False], 0.9), "80")["verdict"] == "too_narrow"
    assert P.coverage(rows([True], 0.6), "80")["verdict"] == "too_wide"
    assert P.coverage(rows([True], 0.6, seasons=3), "80")["verdict"] == "not_readable"


def test_team_statement_says_harder_easier_and_same():
    base = {"games_total": 17, "projection": {"mean": 11.83, "interval80": [9.0, 15.0],
                                               "interval80_mass": 0.871},
            "remaining": {"games": 14, "difficulty_rank": 3, "schedule_effect": -0.36}}
    assert "0.4 fewer wins" in X.team_statement(base, 32) and "3rd hardest" in X.team_statement(base, 32)
    base["remaining"]["schedule_effect"] = 0.42
    assert "0.4 more wins" in X.team_statement(base, 32)
    base["remaining"]["schedule_effect"] = 0.04
    assert "the same number of wins" in X.team_statement(base, 32)
    assert "9 to 15" in X.team_statement(base, 32) and "87%" in X.team_statement(base, 32)
    base["remaining"]["difficulty_rank"] = 1
    assert "are the hardest schedule of 32" in X.team_statement(base, 32)
    base["remaining"]["games"] = 0
    assert X.team_statement(base, 32).endswith("No games remain.")


# ------------------------------------------------------------------ the published shape

def test_payload_validates_and_the_keys_route_to_their_kinds(world):
    p = X.validated_projection(build(world))
    assert X.kind_of(X.PROJECTION_KEY) == "season_projection"
    assert X.kind_of(X.KEY) == "season_model"
    assert X.kind_of("season/nfl/other.json") is None
    assert len(p["teams"]) == 32 and p["display"]["show"] == "model"
    assert p["line"]["label"] == X.LINE_LABEL


def test_the_headline_is_the_sum_of_the_games(world):
    """One simulation behind every figure: the mean, the per-game p_win, the
    path's end and remaining.expected_wins agree - and a file where they do not
    is refused."""
    p = X.validated_projection(build(world))
    t = next(x for x in p["teams"] if x["schedule"])
    bad = copy.deepcopy(p)
    tb = next(x for x in bad["teams"] if x["team"] == t["team"])
    tb["schedule"][0]["p_win"] = round(min(1.0, tb["schedule"][0]["p_win"] + 0.2), 4)
    with pytest.raises(X.ContractError, match="sum\\(p_win\\)"):
        X.validated_projection(bad)
    bad = copy.deepcopy(p)
    bad["teams"][0]["projection"]["interval80"] = [bad["teams"][0]["projection"]["interval95"][0] - 1,
                                                  bad["teams"][0]["projection"]["interval80"][1]]
    with pytest.raises(X.ContractError):
        X.validated_projection(bad)


def test_the_contract_refuses_an_unknown_field_and_a_bad_verdict(world):
    p = build(world)
    bad = copy.deepcopy(p)
    bad["teams"][0]["pace"] = 17
    with pytest.raises(X.ContractError):
        X.validated_projection(bad)
    bad = copy.deepcopy(p)
    bad["record"]["verdict"] = "edge"
    with pytest.raises(X.ContractError):
        X.validated_projection(bad)


def test_difficulty_ranks_are_a_permutation(world):
    p = build(world)
    ranks = sorted(t["remaining"]["difficulty_rank"] for t in p["teams"])
    assert ranks == list(range(1, 33))
    # rank 1 is the lowest average-team win share
    by = {t["remaining"]["difficulty_rank"]: t["remaining"]["average_team_win_share"]
          for t in p["teams"]}
    assert all(by[i] <= by[i + 1] for i in range(1, 32))


def test_a_harder_schedule_has_a_negative_effect(world):
    """schedule_effect is read off the model's own ratings: opponents rated
    above average cost wins, and the sign follows the opponents' mean rating."""
    p = build(world)
    lo = min(p["teams"], key=lambda t: t["remaining"]["opponent_rating_mean"])
    hi = max(p["teams"], key=lambda t: t["remaining"]["opponent_rating_mean"])
    assert hi["remaining"]["schedule_effect"] < lo["remaining"]["schedule_effect"]


# ------------------------------------------------------------------ publishing

@pytest.fixture
def web_tree(tmp_path, monkeypatch):
    import config
    monkeypatch.setattr(config, "WEB_EXPORT_DIR", str(tmp_path / "web"))
    return tmp_path / "web"


def both(world=None):
    """Both season files, built by the real builders: the pair `publish_all` takes."""
    world = world or make_world()
    res = fake_result(world)
    return {X.KEY: X.build(res, generated_at="2026-09-29T00:00:00Z"),
            X.PROJECTION_KEY: X.build_projection(res, generated_at="2026-09-29T00:00:00Z")}


def test_publish_all_writes_both_files_and_passes_the_gate(world, web_tree):
    written, deleted, gate = X.publish_all(both(world), str(web_tree))
    assert (written, deleted) == (2, 0)
    assert (web_tree / "season" / "nfl" / "projection.json").is_file()
    assert (web_tree / "season" / "nfl" / "division.json").is_file()
    assert gate.startswith("metric gate: %d metrics" % len(MR.SEASON_METRICS))
    assert X.publish_all(both(world), str(web_tree))[:2] == (0, 0)


def test_the_single_file_publish_is_retired_and_raises(world, web_tree):
    with pytest.raises(TypeError, match="retired"):
        X.publish(both(world)[X.KEY], str(web_tree))


def test_publishing_the_division_alone_is_refused_by_the_gate(world, web_tree):
    """The projection's registered figures are in the season gate, so a publish
    without it cannot pass - and cannot delete the projection already served."""
    X.publish_all(both(world), str(web_tree))
    with pytest.raises(MR.MetricDisagreement, match="was not produced"):
        X.publish_all({X.KEY: both(world)[X.KEY]}, str(web_tree))
    assert (web_tree / "season" / "nfl" / "projection.json").is_file()


def test_a_missing_team_fails_the_gate(world, web_tree):
    f = both(world)
    f[X.PROJECTION_KEY]["teams"] = [t for t in f[X.PROJECTION_KEY]["teams"] if t["team"] != "BUF"]
    with pytest.raises((MR.MetricDisagreement, X.ContractError)):
        X.publish_all(f, str(web_tree))


def test_the_two_files_must_have_seen_the_same_games(world, web_tree):
    f = both(world)
    f[X.PROJECTION_KEY]["as_of"]["games_played"] += 1
    with pytest.raises(MR.MetricDisagreement, match="as_of.games_played"):
        X.publish_all(f, str(web_tree))


def test_every_projection_metric_resolves_on_a_built_file(world):
    f = both(world)
    rep = MR.check(f, MR.SEASON_METRICS)
    assert rep.clean, rep.statement
    ids = {m["id"] for m in MR.SEASON_METRICS}
    assert {"season.projection.BUF.mean", "season.projection.record.rmse.pace",
            "season.projection.sigma"} <= ids
    assert sum(i.startswith("season.projection.") for i in ids) == 4 * 32 + 14
