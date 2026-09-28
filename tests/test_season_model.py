"""a-41: the season model - Elo, simulator, tiebreakers, walk-forward, published shape.

Run: pytest -q tests/test_season_model.py

Every verdict the file can carry is driven to each of its answers here, so the
published "beats the standings" is known to be a result the pipeline could
have reported the other way.
"""
import ast
import copy
import json
import math
import os
import sqlite3

import numpy as np
import pytest

from jobs import season_export as X
from jobs import season_model as J
from models import season as M


# ------------------------------------------------------------------ fixtures

def game(gid, season, week, home, away, hs, as_, gtype="REG", ts=None):
    return dict(game_id=gid, season=season, week=week, game_type=gtype,
                kickoff_ts=ts if ts is not None else season * 1e6 + week * 1e3,
                home=home, away=away, home_score=hs, away_score=as_, data_version="t")


def one_division(results):
    """A four-team double round robin, all played. `results` maps (home, away)
    -> 1.0 / 0.0 / 0.5, every ordered pair exactly once."""
    teams = ["A", "B", "C", "D"]
    games = [(h, a, 1, results[(h, a)]) for h in teams for a in teams if h != a]
    return M.Season(teams=teams, division={t: "X East" for t in teams},
                    conference={t: "X" for t in teams}, games=games)


def winner(season, seed=0, stats=None):
    R = M.simulate(season, np.full(season.G, 0.5), 1, np.random.default_rng(seed))
    tab = M.Tables(season, R)
    return M.division_winners(tab, np.random.default_rng(seed), stats)["X East"][0]


# ------------------------------------------------------------------ Elo

def test_elo_prediction_uses_only_earlier_games():
    g = [game("1", 2001, 1, "A", "B", 30, 0), game("2", 2001, 2, "A", "B", 0, 30),
         game("3", 2001, 3, "A", "B", 10, 7)]
    p = M.EloParams(20, 50, 0.5)
    pre1, _ = M.run_elo(g, p)
    g2 = copy.deepcopy(g)
    g2[2]["home_score"] = 0          # change only the LAST game
    pre2, _ = M.run_elo(g2, p)
    assert [q for _, q in pre1] == [q for _, q in pre2]
    assert pre1[0][1] == pytest.approx(float(M.win_prob(50)))


def test_snapshot_after_week_k_holds_weeks_up_to_k_only():
    g = [game("1", 2001, 1, "A", "B", 30, 0), game("2", 2001, 2, "A", "B", 30, 0),
         game("3", 2002, 1, "A", "B", 30, 0)]
    p = M.EloParams(20, 0, 0.5)
    _, s = M.run_elo(g, p, snapshot_at=[(2001, 0), (2001, 1), (2001, 2), (2002, 0),
                                         (2002, 1), (2003, 0)])
    assert s[(2001, 0)] == {}                              # nobody rated yet
    assert s[(2001, 1)]["A"] > 1500 and s[(2001, 2)]["A"] > s[(2001, 1)]["A"]
    # preseason 2002 is 2001's end regressed halfway to 1500
    assert s[(2002, 0)]["A"] == pytest.approx(1500 + 0.5 * (s[(2001, 2)]["A"] - 1500))
    # a season with no games yet: last ratings regressed once
    assert s[(2003, 0)]["A"] == pytest.approx(1500 + 0.5 * (s[(2002, 1)]["A"] - 1500))


def test_relocated_franchise_keeps_its_rating():
    g = [game("1", 2015, 1, "STL", "SF", 40, 0), game("2", 2016, 1, "LA", "SF", 0, 0)]
    _, s = M.run_elo(g, M.EloParams(20, 0, 0.0), snapshot_at=[(2016, 0)])
    assert s[(2016, 0)]["LA"] > 1500 and "STL" not in s[(2016, 0)]


def test_best_params_never_reads_the_season_it_forecasts():
    a, b = M.EloParams(10, 0, 0), M.EloParams(20, 0, 0)
    seasons = np.array([2000, 2001, 2002, 2003])
    losses = {a: (seasons, np.array([0.5, 0.5, 9.0, 9.0])),
              b: (seasons, np.array([0.6, 0.6, 0.0, 0.0]))}
    assert J.best_params(losses, 2002)[0] == a             # 2002-3 invisible
    assert J.best_params(losses, 2004)[0] == b             # ... until they are past


# ------------------------------------------------------------------ tiebreakers

def _h2h_case():
    # A and B finish 4-2, A swept B; C and D cannot reach 4 wins
    r = {}
    def series(x, y, x_wins):
        r[(x, y)] = 1.0 if x_wins >= 1 else 0.0
        r[(y, x)] = 0.0 if x_wins == 2 else 1.0
    series("A", "B", 2)
    series("A", "C", 1)
    series("A", "D", 1)
    series("B", "C", 2)
    series("B", "D", 2)
    series("C", "D", 1)
    return r


def test_head_to_head_decides_a_two_club_tie():
    s = one_division(_h2h_case())
    rec = M.standings(s)
    assert rec["A"][:2] == (4, 2) and rec["B"][:2] == (4, 2)
    stats = {}
    assert winner(s, stats=stats) == "A"
    assert stats == {"tied_at_top": 1, "head_to_head": 1}


def test_the_other_club_wins_when_head_to_head_flips():
    r = _h2h_case()
    r[("A", "B")], r[("B", "A")] = 0.0, 1.0                 # B sweeps A instead
    r[("A", "C")], r[("C", "A")] = 1.0, 0.0                 # A: 0 vs B, 2 vs C
    r[("A", "D")], r[("D", "A")] = 1.0, 0.0                 #    2 vs D -> 4-2
    r[("B", "C")], r[("C", "B")] = 1.0, 1.0                 # B: 2, 1, 1 -> 4-2
    r[("B", "D")], r[("D", "B")] = 1.0, 1.0
    s = one_division(r)
    rec = M.standings(s)
    assert rec["A"][:2] == rec["B"][:2] == (4, 2)
    assert winner(s) == "B"


def test_a_tie_nothing_separates_is_a_uniform_draw_and_is_counted():
    # a perfect cycle: every club 3-3, every head-to-head split
    r = {(h, a): 1.0 for h in "ABCD" for a in "ABCD" if h != a}
    s = one_division(r)
    wins, stats = set(), {}
    for seed in range(40):
        st = {}
        wins.add(winner(s, seed, st))
        for k, v in st.items():
            stats[k] = stats.get(k, 0) + v
    assert wins == {"A", "B", "C", "D"}
    assert stats["residual_draw"] == 40


def test_probabilities_sum_to_one_per_division():
    s = one_division({(h, a): 1.0 for h in "ABCD" for a in "ABCD" if h != a})
    s = M.Season(teams=s.teams, division=s.division, conference=s.conference,
                 games=[(h, a, w, None) for h, a, w, _ in s.games])
    p = M.division_probs(s, np.full(s.G, 0.6), 2000, np.random.default_rng(1))
    assert sum(p["X East"].values()) == pytest.approx(1.0)
    assert all(0.15 < v < 0.35 for v in p["X East"].values())


def test_leader_baseline_splits_between_co_leaders():
    r = _h2h_case()
    s = one_division(r)
    lp = M.leader_probs(s)["X East"]
    assert lp == {"A": 0.5, "B": 0.5, "C": 0.0, "D": 0.0}


def test_brier_is_the_multicategory_sum():
    assert M.brier({"A": 1.0, "B": 0.0}, "A") == 0.0
    assert M.brier({"A": 0.25, "B": 0.25, "C": 0.25, "D": 0.25}, "A") == pytest.approx(0.75)


# ------------------------------------------------------------------ verdicts reach every answer

@pytest.mark.parametrize("iv,n,want", [
    ([-0.3, -0.1], 192, "better_than"),
    ([0.1, 0.3], 192, "worse_than"),
    ([-0.1, 0.1], 192, "no_better_than"),
    ([-0.3, -0.1], 3, "not_readable"),
])
def test_comparison_verdict_reaches_every_value(iv, n, want):
    assert J.verdict({"interval": iv, "n_blocks": n}) == want


@pytest.mark.parametrize("leader,coin,want,show", [
    ("better_than", "better_than", "beats_standings", "model"),
    ("better_than", "no_better_than", "no_better_than_standings", "standings_coin_flip"),
    ("no_better_than", "better_than", "no_better_than_standings", "standings_coin_flip"),
    ("better_than", "worse_than", "worse_than_standings", "standings_coin_flip"),
    ("not_readable", "better_than", "not_readable", "standings_coin_flip"),
])
def test_record_verdict_and_display_reach_every_value(leader, coin, want, show):
    vs = {"standings_leader": {"verdict": leader}, "standings_coin_flip": {"verdict": coin}}
    assert X.record_verdict(vs) == want
    assert X.display_choice(want)["show"] == show


def test_calibration_statement_can_say_all_bins_are_fine():
    bins = [{"bin": [0.0, 0.1], "n": 10, "forecast": 0.05, "realised": 0.05,
             "interval": [0.01, 0.1], "verdict": "within"}]
    assert X.calibration_statement(bins).startswith("Every one of 1")
    bins[0].update(realised=0.2, interval=[0.15, 0.25], verdict="realised_above")
    assert "overconfident" in X.calibration_statement(bins)


def test_block_bootstrap_does_not_narrow_when_rows_are_duplicated_inside_a_block():
    rng = np.random.default_rng(3)
    rows = [{"blk": b, "a": float(rng.normal()), "b": 0.0} for b in range(30)]
    one = J.block_bootstrap(rows, "a", "b", lambda r: r["blk"])
    many = J.block_bootstrap([dict(r) for r in rows for _ in range(20)], "a", "b",
                             lambda r: r["blk"])
    w1 = one["interval"][1] - one["interval"][0]
    w2 = many["interval"][1] - many["interval"][0]
    assert w2 == pytest.approx(w1, rel=0.05)


# ------------------------------------------------------------------ the published shape

def _fake_result():
    s = one_division({(h, a): 1.0 for h in "ABCD" for a in "ABCD" if h != a})
    s = M.Season(teams=s.teams, division=s.division, conference=s.conference,
                 games=[(h, a, w, None) for h, a, w, _ in s.games])
    n = 1000
    pm = M.division_probs(s, np.full(s.G, 0.6), n, np.random.default_rng(1))
    pc = M.division_probs(s, np.full(s.G, 0.5), n, np.random.default_rng(2))
    vs = {"estimate": -0.03, "interval": [-0.05, -0.01], "n_blocks": 192, "n_states": 3304,
          "se": 0.01, "verdict": "better_than",
          "by_season_blocks": {"estimate": -0.03, "interval": [-0.06, -0.01],
                               "n_blocks": 24, "n_states": 3304, "se": 0.01,
                               "verdict": "better_than"}}
    cal = [{"bin": [0.0, 0.1], "n": 5, "n_blocks": 3, "forecast": 0.05, "realised": 0.04,
            "interval": [0.0, 0.2]},
           {"bin": [0.1, 0.2], "n": 0, "n_blocks": 0, "forecast": None, "realised": None,
            "interval": None}]
    summ = {"brier": {"model": 0.41, "coinflip": 0.45, "leader": 0.55},
            "vs": {"leader": copy.deepcopy(vs), "coinflip": copy.deepcopy(vs)},
            "open_states": {"n_states": 10, "n_blocks": 5,
                            "brier": {"model": 0.5, "coinflip": 0.55, "leader": 0.6},
                            "vs": {"leader": dict(vs), "coinflip": dict(vs)}},
            "by_week": [{"after_week": 0, "n_division_seasons": 192, "model": 0.67,
                         "coinflip": 0.75, "leader": 0.75}],
            "calibration": {"model": cal, "coinflip": cal},
            "races": {"division_seasons": 192, "tied_at_top_final": 23, "margin_le_1_game": 74,
                      "open_after_week": {"8": 183}, "open_definition": "x"}}
    cur = {"season": 2026, "params": M.EloParams(20, 50, 0.5), "fit_log_loss": 0.63,
           "fit_games": 7017, "games_played": 3, "games_total": 12, "last_complete_week": 0,
           "last_game_ts": None, "ratings": {t: 1500.0 for t in s.teams}, "season_obj": s,
           "model": pm, "coinflip": pc, "leader": M.leader_probs(s),
           "record": M.standings(s), "n": n}
    return {"summary": summ, "current": cur,
            "fits": {2002: {"params": {"k": 20.0, "hfa": 50.0, "regress": 0.5},
                            "fit_seasons": [2000, 2001], "fit_games": 518}},
            "tiebreak": {"division_seasons": 192, "agree": 192, "tied_at_top": 23,
                         "tied_agree": 23, "residual_draws": 0},
            "versions": {"nfl_games": "2026-09-27", "nfl_teams": "2026-09-16"},
            "n_walk": 4000}


def test_payload_validates_and_the_key_routes_to_its_kind():
    p = X.validated(X.build(_fake_result(), generated_at="2026-09-27T00:00:00Z"))
    assert X.kind_of(X.KEY) == "season_model"
    assert p["display"]["show"] == "model"
    assert X.kind_of("season/nfl/division.json") == "season_model"
    assert X.kind_of("nfl/season/division.json") is None


def test_the_contract_refuses_an_unknown_field_and_a_bad_verdict():
    p = X.build(_fake_result(), generated_at="2026-09-27T00:00:00Z")
    bad = copy.deepcopy(p)
    bad["divisions"][0]["teams"][0]["surprise"] = 1
    with pytest.raises(X.ContractError):
        X.validated(bad)
    bad = copy.deepcopy(p)
    bad["record"]["verdict"] = "edge"
    with pytest.raises(X.ContractError):
        X.validated(bad)


def test_probabilities_that_do_not_sum_to_one_are_refused():
    p = X.build(_fake_result(), generated_at="2026-09-27T00:00:00Z")
    p["divisions"][0]["teams"][0]["p"] = round(p["divisions"][0]["teams"][0]["p"] + 0.05, 4)
    with pytest.raises(X.ContractError, match="sums to"):
        X.validated(p)


def test_a_record_that_does_not_beat_the_standings_switches_the_display():
    res = _fake_result()
    res["summary"]["vs"]["coinflip"]["interval"] = [-0.02, 0.01]
    res["summary"]["vs"]["coinflip"]["verdict"] = "no_better_than"
    p = X.validated(X.build(res, generated_at="2026-09-27T00:00:00Z"))
    assert p["record"]["verdict"] == "no_better_than_standings"
    assert p["display"]["show"] == "standings_coin_flip"
    assert p["record"]["statement"].endswith("No better than the standings carried forward.")


def test_write_stays_inside_its_prefix(tmp_path):
    p = X.build(_fake_result(), generated_at="2026-09-27T00:00:00Z")
    path = X.write(p, root=str(tmp_path))
    assert os.path.relpath(path, tmp_path).replace("\\", "/") == "season/nfl/division.json"
    with pytest.raises(AssertionError):
        X.write(p, root=str(tmp_path), key="nfl/teams/x.json")


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# a-42: `season_export.publish` writes through `export_web.sync_keys`, so the
# a-41 rule "imports nothing from export_web" became unsatisfiable by design.
# What it protected was "cannot upload", and that is asserted directly now: the
# ONLY export_web names these modules touch are the three below, none of which
# reaches the bucket. Checked by AST, so a docstring naming `upload` passes and
# a call to it fails.
EXPORT_WEB_ALLOWED = {"sync_keys", "require_setting", "REFRESHED_SENTINEL"}


def export_web_uses(src):
    """(attribute names used on an `export_web` alias, forbidden imports)."""
    tree = ast.parse(src)
    aliases, bad = set(), set()
    for n in ast.walk(tree):
        if isinstance(n, (ast.Import, ast.ImportFrom)):
            module = getattr(n, "module", None) or ""
            for a in n.names:
                if a.name.split(".")[-1] in ("r2", "boto3", "store") or                         module.split(".")[-1] in ("r2", "boto3", "store"):
                    bad.add(a.name)
                if a.name.split(".")[-1] == "export_web":
                    aliases.add(a.asname or a.name)
                elif module.endswith("export_web"):
                    bad.add(a.name)            # `from jobs.export_web import upload` etc.
    used = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)
            and isinstance(n.value, ast.Name) and n.value.id in aliases}
    return used, bad


def test_neither_module_can_upload_or_write_the_logger_store():
    for mod in ("jobs/season_model.py", "jobs/season_export.py", "models/season.py"):
        used, bad = export_web_uses(open(os.path.join(ROOT, mod), encoding="utf-8").read())
        assert not bad, (mod, bad)
        assert used <= EXPORT_WEB_ALLOWED, (mod, used - EXPORT_WEB_ALLOWED)
    src = open(os.path.join(ROOT, "jobs", "season_model.py"), encoding="utf-8").read()
    assert src.count("sqlite3.connect(") == 1 and "?mode=ro" in src


def test_the_upload_guard_fires_on_a_planted_upload():
    """The other answer on the other input: the guard is known to discriminate."""
    used, bad = export_web_uses("from jobs import export_web as E\nE.upload(dry_run=False)\n")
    assert "upload" in used - EXPORT_WEB_ALLOWED
    used, bad = export_web_uses("from jobs.export_web import upload\n")
    assert bad == {"upload"}
    used, bad = export_web_uses("import boto3\n")
    assert bad == {"boto3"}
    assert export_web_uses('"""E.upload is never called"""'+'\n') == (set(), set())


# ------------------------------------------------------------------ publishing (a-42)

@pytest.fixture
def web_tree(tmp_path, monkeypatch):
    import config
    monkeypatch.setattr(config, "WEB_EXPORT_DIR", str(tmp_path / "web"))
    return tmp_path / "web"


def test_publish_writes_season_into_the_web_tree_and_passes_the_metric_gate(web_tree):
    p = X.build(_fake_result(), generated_at="2026-09-27T00:00:00Z")
    written, deleted, gate = X.publish(p, str(web_tree))
    assert (written, deleted) == (1, 0)
    assert (web_tree / "season" / "nfl" / "division.json").is_file()
    from jobs import metric_registry as MR
    assert gate.startswith("metric gate: %d metrics" % len(MR.SEASON_METRICS))
    assert len(MR.SEASON_METRICS) >= 10
    # an unchanged file is not rewritten: generated_at alone does not count
    p2 = X.build(_fake_result(), generated_at="2026-09-28T00:00:00Z")
    assert X.publish(p2, str(web_tree))[:2] == (0, 0)


def test_publish_owns_season_and_nothing_else(web_tree):
    stale = web_tree / "season" / "nfl" / "retired.json"
    other = web_tree / "nfl" / "teams" / "x.json"
    for f in (stale, other):
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("{}")
    written, deleted, _ = X.publish(X.build(_fake_result(), generated_at="2026-09-27T00:00:00Z"),
                                    str(web_tree))
    assert deleted == 1 and not stale.exists()
    assert other.exists(), "publish deleted a key outside season/"


def test_publish_refuses_before_writing_when_a_registered_figure_is_missing(web_tree):
    """The low-tail bin is a registered metric; a file whose lowest bin is empty
    has no figure there, and the gate refuses BEFORE sync_keys touches disk."""
    res = _fake_result()
    res["summary"]["calibration"]["model"][0] = dict(res["summary"]["calibration"]["model"][1])
    p = X.build(res, generated_at="2026-09-27T00:00:00Z")
    from jobs import metric_registry as MR
    with pytest.raises(MR.MetricDisagreement, match="low_tail"):
        X.publish(p, str(web_tree))
    assert not web_tree.exists() or not any(web_tree.rglob("*.json"))


def test_season_prefix_reaches_no_other_builder():
    """`season/` is top-level: no owned prefix of the site export, and neither
    `analytics/` nor `lab/`, contains it or is contained by it."""
    from tests import test_prefix_ownership as P
    owned, dynamic = P.owned_prefixes()
    assert owned and dynamic == 0
    others = owned + ["analytics/", "lab/", "board/", "live/"]
    for o in others:
        assert not (o.startswith(X.OWNED_PREFIX) or X.OWNED_PREFIX.startswith(o)), o


def test_the_published_file_carries_both_honesty_requirements():
    """a-41's two, which the Teams page is instructed to show: the tail
    overconfidence (the calibration statement names the off bins) and the
    display switch (driven both ways above)."""
    res = _fake_result()
    res["summary"]["calibration"]["model"][0].update(
        forecast=0.019, realised=0.032, interval=[0.022, 0.044])
    p = X.validated(X.build(res, generated_at="2026-09-27T00:00:00Z"))
    st = p["record"]["calibration_statement"]
    assert "0.0%-10.0% forecast 1.9%, realised 3.2%" in st and "overconfident" in st
    assert p["record"]["calibration"][0]["verdict"] == "realised_above"
    assert p["display"]["show"] == "model" and p["display"]["reason"]


# ------------------------------------------------------------------ against the real schedule

@pytest.mark.skipif(os.getenv("LOGGER_DB") is None,
                    reason="needs LOGGER_DB: a store holding nfl_games (read mode=ro)")
def test_tiebreakers_reproduce_every_real_division_winner():
    uri = "file:" + os.getenv("LOGGER_DB").replace("\\", "/") + "?mode=ro"
    con = sqlite3.connect(uri, uri=True)
    try:
        games, groupings, _ = J.load(con)
    finally:
        con.close()
    tb = J.tiebreak_check(games, groupings)
    assert tb["division_seasons"] == 192
    assert tb["agree"] == 192, tb["disagreements"]
    assert tb["tied_at_top"] >= 20 and tb["tied_agree"] == tb["tied_at_top"]
