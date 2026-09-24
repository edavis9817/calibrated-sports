"""lab.run - the engine as a pure function (AUDIT 6.5, 6.10). pytest -q tests/test_lab_engine.py

Every test here builds its own universe in memory; nothing opens a store.
The tests 6.10 names are here: the holdout lock, bootstrap interval coverage
on synthetic data with a known ROI, and the verdict-chip rules. The as-of
guard and settlement parity need the universe builder and live in
test_lab_universe.py.
"""
import copy
import json

import numpy as np
import polars as pl
import pytest

from lab import catalogue, engine, run, strategy as S

MARKETS = {"prop": ["receptions", "rush_attempts"], "spread": ["spread"]}


def _row(season, week, subject, outcome, line=4.5, side="over", book="draftkings",
         american=-110, p=0.5, market="receptions", game=None, kick=None, **feat):
    game = game or "%d_%02d_%s" % (season, week, subject)
    r = {"bet_type": "prop", "market": market, "season": season, "week": week,
         "season_type": "REG", "game_id": game,
         "kickoff_ts": kick or float(season * 1e6 + week * 1e4),
         "subject": subject, "event_subject": subject,
         "claim": "%d|%d|%s|%s|%g" % (season, week, subject, market, line),
         "line": line, "side": side, "team": "AAA", "opp": "BBB", "book": book,
         "american": float(american), "p_devig": p, "book_hold": 0.045,
         "source": "oddsapi_close", "outcome": outcome, "actual": 0.0,
         "game.home": True, "game.dome": False, "player.position": "WR",
         "player.mean_l5": 4.0}
    r.update(feat)
    return r


def _universe(rows, seasons=(2023, 2025)):
    df = pl.DataFrame(rows, infer_schema_length=None)
    feats = catalogue.ranges(None, {"prop": seasons}, MARKETS,
                             derive=lambda con, m: (1999, 2026, "fake survey"))
    return {"rows": df, "meta": {"features": feats,
                                 "price_coverage": {"prop": seasons},
                                 "latest_complete_season": seasons[1],
                                 "settlement_fixed": True}}


def _strategy(**kw):
    s = {"schema": "lab.strategy/1", "sport": "nfl", "bet_type": "prop",
         "markets": ["receptions"],
         "seasons": {"from": 2023, "to": 2025, "season_type": "REG",
                     "weeks": {"from": 1, "to": 18}},
         "side": "over", "holdout": {"season": 2025, "revealed": False},
         "evaluation": {"resamples": 200, "null_draws": 100}}
    s.update(kw)
    return s


def _grid(rng, seasons=(2023, 2024, 2025), weeks=10, per_week=12, p_clear=0.5):
    rows = []
    for y in seasons:
        for w in range(1, weeks + 1):
            for i in range(per_week):
                out = "cleared" if rng.random() < p_clear else "missed"
                rows.append(_row(y, w, "P%02d" % i, out))
    return rows


# ------------------------------------------------------------------ strategy

def test_bench_books_are_the_calibration_benchmark():
    from research import calibration
    assert tuple(S.BENCH_BOOKS) == tuple(calibration.BENCH_BOOKS)


def test_shape_is_refused_before_anything_runs():
    with pytest.raises(S.Invalid):
        S.validate_shape(_strategy(side="sideways"))
    with pytest.raises(S.Invalid):
        S.validate_shape(dict(_strategy(), extra_field=1))


def test_what_the_data_cannot_support_is_refused_with_every_reason():
    u = _universe([_row(2023, 1, "A", "cleared")])
    feats = u["meta"]["features"]
    s = _strategy(timing="t_minus_3h", side="model_lean",
                  price={"source": "kalshi_mid"},
                  seasons={"from": 2023, "to": 2025, "season_type": "POST"},
                  conditions=[{"feature": "model.p", "op": ">", "value": 0.5},
                              {"feature": "nope.x", "op": ">", "value": 1}])
    with pytest.raises(S.Unsupported) as e:
        S.check(s, feats)
    text = " | ".join(e.value.reasons)
    for needle in ("t_minus_3h", "walk-forward", "kalshi_mid", "postseason",
                   "model.p", "nope.x"):
        assert needle in text, needle
    assert len(e.value.reasons) >= 6


def test_a_condition_outside_its_feature_range_is_refused():
    feats = catalogue.ranges(None, {"prop": (2023, 2025)}, MARKETS,
                             derive=lambda con, m: (2024, 2026, "starts 2024"))
    s = _strategy(conditions=[{"feature": "player.mean_l5", "op": ">", "value": 3}])
    with pytest.raises(S.Unsupported) as e:
        S.check(s, feats)
    assert "2024-2025" in str(e.value)
    ok = S.check(_strategy(seasons={"from": 2024, "to": 2025},
                           conditions=s["conditions"]), feats)
    assert ok.strategy["conditions"] == s["conditions"]


def test_approved_refuses_truth_testing():
    u = _universe([_row(2023, 1, "A", "cleared")])
    a = S.check(_strategy(), u["meta"]["features"])
    with pytest.raises(TypeError):
        bool(a)
    assert "prop over on receptions" in a.statement


def test_hash_ignores_the_name_and_not_the_reveal():
    a = _strategy(name="one")
    b = _strategy(name="two")
    c = _strategy(holdout={"season": 2025, "revealed": True})
    assert S.strategy_hash(a) == S.strategy_hash(b)
    assert S.strategy_hash(a) != S.strategy_hash(c)


def test_nearest_to_parses_both_forms():
    assert S.nearest_target("nearest_to(4.5)") == 4.5
    assert S.nearest_target({"nearest_to": 3}) == 3.0
    assert S.nearest_target("main") is None


# ------------------------------------------------------------------ the engine

def test_a_known_record_prices_and_settles_exactly():
    rows = [_row(2023, 1, "A", "cleared", american=+100),
            _row(2023, 1, "B", "missed"),
            _row(2023, 2, "A", "push", line=5.0),
            _row(2023, 2, "B", "void"),
            _row(2024, 1, "C", "cleared", american=-120)]
    r = run(_strategy(), _universe(rows))
    s = r["summary"]
    assert (s["bets"], s["cleared"], s["missed"], s["push"], s["void"]) == (5, 2, 1, 1, 1)
    # +1.00 (even money) + 0.8333 (-120) - 1 (loss) + 0 (push); void stakes nothing
    assert s["profit"] == pytest.approx(1.0 + 100 / 120 - 1.0, abs=1e-6)
    assert s["staked"] == 4
    assert s["hit_rate"] == pytest.approx(2 / 3, abs=1e-6)
    assert r["clv"]["value"] is None and "n/a" in r["clv"]["note"]
    assert r["publishable"] is True


def test_consensus_pays_the_median_posted_price_and_best_pays_the_max():
    rows = [_row(2023, 1, "A", "cleared", book=b, american=a)
            for b, a in (("draftkings", -130), ("fanduel", -110), ("betmgm", +105))]
    u = _universe(rows)
    med = run(_strategy(), u)["bet_list"][0]["price_decimal"]
    best = run(_strategy(price={"source": "best_close"}), u)["bet_list"][0]["price_decimal"]
    one = run(_strategy(price={"source": "book", "books": ["draftkings"]}), u)
    assert med == pytest.approx(engine.american_to_decimal(-110), abs=1e-4)
    assert best == pytest.approx(2.05, abs=1e-4)
    assert one["bet_list"][0]["price_decimal"] == pytest.approx(1 + 100 / 130, abs=1e-4)


def test_fair_price_when_posted_juice_is_off():
    rows = [_row(2023, 1, "A", "cleared", american=-150, p=0.55)]
    r = run(_strategy(price={"use_posted_juice": False}), _universe(rows))
    assert r["bet_list"][0]["price_decimal"] == pytest.approx(1 / 0.55, abs=1e-4)


def test_rungs_on_one_player_week_are_one_bet_by_default():
    rows = [_row(2023, 1, "A", "cleared", line=l, p=p)
            for l, p in ((2.5, 0.8), (3.5, 0.62), (4.5, 0.49), (5.5, 0.3))]
    u = _universe(rows)
    one = run(_strategy(line_choice="all_rungs"), u)
    assert one["summary"]["bets"] == 1
    assert one["bet_list"][0]["line"] == 4.5          # the rung nearest 0.5
    every = run(_strategy(line_choice="all_rungs",
                          limits={"per_player_week": None}), u)
    assert every["summary"]["bets"] == 4
    near = run(_strategy(line_choice="nearest_to(2.4)",
                         limits={"per_player_week": None}), u)
    assert [b["line"] for b in near["bet_list"]] == [2.5]


def test_conditions_filter_and_a_null_feature_fails_the_condition():
    rows = [_row(2023, 1, "A", "cleared", **{"player.mean_l5": 6.0}),
            _row(2023, 1, "B", "missed", **{"player.mean_l5": 2.0}),
            _row(2023, 1, "C", "missed", **{"player.mean_l5": None})]
    r = run(_strategy(conditions=[{"feature": "player.mean_l5", "op": ">=",
                                   "value": 2.0}]), _universe(rows))
    assert sorted(b["subject"] for b in r["bet_list"]) == ["A", "B"]


def test_kelly_stakes_zero_off_model_lean_and_says_so():
    rows = [_row(2023, 1, "A", "cleared"), _row(2023, 2, "B", "missed")]
    r = run(_strategy(staking={"method": "kelly_fraction"}), _universe(rows))
    assert r["summary"]["staked"] == 0 and r["summary"]["roi"] is None
    assert r["verdict"] == engine.NO_BETS
    assert any("Kelly" in n for n in r["notes"])


def test_drawdown_and_losing_run():
    seq = ["cleared", "cleared", "missed", "missed", "push", "missed", "cleared"]
    rows = [_row(2023, w + 1, "A", o, american=+100) for w, o in enumerate(seq)]
    r = run(_strategy(), _universe(rows))
    assert r["drawdown"]["max_units"] == pytest.approx(3.0)
    assert r["drawdown"]["longest_losing_run"] == 3


def test_segments_under_100_bets_are_flagged_thin():
    rng = np.random.default_rng(1)
    r = run(_strategy(), _universe(_grid(rng)))
    seg = {s["key"]: s for s in r["segments"]["market"]}
    assert seg["receptions"]["bets"] == 240 and seg["receptions"]["thin"] is False
    assert all(s["thin"] == (s["bets"] < 100) for v in r["segments"].values() for s in v)


def test_result_is_json_and_deterministic():
    rng = np.random.default_rng(2)
    u = _universe(_grid(rng))
    a, b = run(_strategy(), u), run(_strategy(), u)
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


# ------------------------------------------------------------------ holdout lock

def test_the_holdout_season_reaches_no_figure_until_revealed():
    rng = np.random.default_rng(3)
    rows = _grid(rng)
    base = run(_strategy(), _universe(rows))
    # Rewrite every 2025 row into a spectacular winner. If ANY figure of an
    # unrevealed result reads the holdout, the two results differ.
    doctored = [dict(r, outcome="cleared", american=500.0) if r["season"] == 2025 else r
                for r in rows]
    locked = run(_strategy(), _universe(doctored))
    assert json.dumps(base, sort_keys=True) == json.dumps(locked, sort_keys=True)
    assert {b["season"] for b in locked["bet_list"]} == {2023, 2024}
    assert locked["holdout"]["result"] is None and locked["all_seasons"] is None

    shown = run(_strategy(holdout={"season": 2025, "revealed": True}), _universe(doctored))
    assert shown["holdout"]["result"]["cleared"] == 120
    assert shown["holdout"]["result"]["roi"] == pytest.approx(5.0)
    assert 2025 in [s["key"] for s in shown["by_season"]]
    # the in-sample tiles still exclude it after the reveal
    assert shown["summary"]["bets"] == base["summary"]["bets"]


def test_default_holdout_is_the_latest_complete_season():
    u = _universe([_row(2023, 1, "A", "cleared"), _row(2025, 1, "B", "cleared")])
    s = _strategy()
    del s["holdout"]
    r = run(s, u)
    assert r["holdout"]["season"] == 2025 and r["holdout"]["revealed"] is False
    assert [b["season"] for b in r["bet_list"]] == [2023]


# ------------------------------------------------------------------ bootstrap

def _synthetic(rng, weeks=40, per_week=25, sd=0.12):
    """Even-money bets whose win probability shares a WEEK shock. E[p] = 0.5
    exactly (a symmetric shock, symmetrically clipped), so the true ROI is 0."""
    shock = np.clip(rng.normal(0, sd, weeks), -0.45, 0.45)
    wins = rng.random((weeks, per_week)) < (0.5 + shock)[:, None]
    profit = np.where(wins, 1.0, -1.0).ravel()
    blocks = np.repeat(np.arange(weeks), per_week)
    return profit, np.ones_like(profit), blocks


def test_week_block_bootstrap_covers_a_known_roi():
    rng = np.random.default_rng(20260924)
    reps, hit_block, hit_naive = 300, 0, 0
    for i in range(reps):
        profit, staked, blocks = _synthetic(rng)
        lo, hi, _se, _ = engine.block_bootstrap_roi(profit, staked, blocks, 400, i)
        hit_block += lo <= 0.0 <= hi
        lo, hi, _se, _ = engine.block_bootstrap_roi(profit, staked,
                                                    np.arange(len(profit)), 400, i)
        hit_naive += lo <= 0.0 <= hi
    block, naive = hit_block / reps, hit_naive / reps
    # 95% nominal. The week-block interval covers; resampling BETS, which
    # ignores the shared weekly shock, is far too narrow - the reason 6.5 says
    # resample weeks, and the discrimination that makes this test mean something.
    assert 0.88 <= block <= 0.99, block
    assert naive <= 0.85, naive


# ------------------------------------------------------------------ luck, variants

def test_luck_check_places_a_rule_among_random_draws():
    rng = np.random.default_rng(4)
    rows = _grid(rng, per_week=20)
    for r in rows:        # a feature that happens to pick winners in-sample
        r["player.mean_l5"] = 9.0 if r["outcome"] == "cleared" else 1.0
    r = run(_strategy(conditions=[{"feature": "player.mean_l5", "op": ">", "value": 5}],
                      limits={"per_player_week": None}), _universe(rows))
    assert r["luck"]["percentile"] == 100.0
    assert r["luck"]["draws"] == 100
    assert len(r["luck"]["band"]["p5"]) == len(r["luck"]["band"]["index"])


def test_luck_check_is_degenerate_when_the_rule_takes_everything():
    rng = np.random.default_rng(5)
    r = run(_strategy(), _universe(_grid(rng)))
    assert r["luck"]["percentile"] is None and "IS the rule" in r["luck"]["note"]


def test_expected_max_table_then_blom():
    assert [engine.expected_max(k) for k in range(1, 8)] == [0.0, 0.56, 0.85, 1.03,
                                                             1.16, 1.27, 1.35]
    # k >= 8 is Blom's approximation, as 6.5 specifies - which runs ~0.01 above
    # the exact E[max of 8 normals] (1.4236). Pin the formula, and its distance.
    import statistics
    blom = statistics.NormalDist().inv_cdf((8 - 0.375) / (8 + 0.25))
    assert engine.expected_max(8) == pytest.approx(blom)
    assert abs(engine.expected_max(8) - 1.4236) < 0.015
    assert engine.noise_best_roi(3, -0.045, 0.02) == pytest.approx(-0.045 + 0.017)


def test_variants_sentence_carries_its_number():
    rng = np.random.default_rng(6)
    rows = _grid(rng, per_week=20)
    for i, row in enumerate(rows):
        row["game.home"] = i % 2 == 0
    r = run(_strategy(conditions=[{"feature": "game.home", "op": "==", "value": True}],
                      limits={"per_player_week": None}), _universe(rows),
            variants_tried=5)
    v = r["variants"]
    assert v["k"] == 5 and v["expected_max_z"] == 1.16
    assert v["best_roi_from_noise"] == pytest.approx(
        v["null_mean_roi"] + v["roi_se"] * 1.16, abs=1e-5)
    assert ("%+.1f%%" % (100 * v["best_roi_from_noise"])) in v["sentence"]


# ------------------------------------------------------------------ verdict chips

@pytest.mark.parametrize("args,chip", [
    ((-0.2, -0.01, 50, False, None, 10), engine.LOSES),
    ((-0.05, 0.03, 50, False, None, 10), engine.NO_EVIDENCE),
    ((-0.05, 0.03, 97, False, None, 10), engine.UNCLEAR),
    ((-0.05, 0.03, None, False, None, 10), engine.UNCLEAR),
    ((0.01, 0.09, 99, False, None, 10), engine.FORWARD_TEST),
    ((0.01, 0.09, 99, True, 0.04, 10), engine.SURVIVED),
    ((0.01, 0.09, 99, True, -0.02, 10), engine.FAILED_HOLDOUT),
    ((0.01, 0.09, 99, True, None, 10), engine.FAILED_HOLDOUT),
    ((None, None, None, False, None, 0), engine.NO_BETS),
])
def test_verdict_chip_rules(args, chip):
    assert engine.verdict(*args) == chip


def test_every_verdict_is_reachable():
    """Falsifiability: each chip is produced by some input, so none of them is
    decoration (CLAUDE.md, 'A computed verdict must be capable of producing a
    different answer')."""
    reached = {engine.verdict(-1, -0.5, 50, False, None, 1),
               engine.verdict(-1, 1, 50, False, None, 1),
               engine.verdict(-1, 1, 99, False, None, 1),
               engine.verdict(0.1, 1, 99, False, None, 1),
               engine.verdict(0.1, 1, 99, True, 0.5, 1),
               engine.verdict(0.1, 1, 99, True, -0.5, 1),
               engine.verdict(None, None, None, False, None, 0)}
    assert reached == set(engine.VERDICTS)


def test_a_rule_that_always_loses_gets_loses_end_to_end():
    rows = [_row(y, w, "P%d" % i, "missed") for y in (2023, 2024)
            for w in range(1, 9) for i in range(5)]
    r = run(_strategy(limits={"per_player_week": None}), _universe(rows))
    assert r["verdict"] == engine.LOSES and r["summary"]["roi_hi"] < 0


def test_unfixed_universe_results_are_not_publishable():
    u = _universe([_row(2023, 1, "A", "cleared")])
    u["meta"]["settlement_fixed"] = False
    r = run(_strategy(), u)
    assert r["publishable"] is False and any("UNFIXED" in n for n in r["notes"])


def test_the_engine_module_does_no_io():
    """Pure means pure: no store, no network, no filesystem in the engine."""
    import ast
    import inspect
    tree = ast.parse(inspect.getsource(engine))
    names = {a.name for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom))
             for a in n.names} | {n.module for n in ast.walk(tree)
                                  if isinstance(n, ast.ImportFrom) and n.module}
    for banned in ("sqlite3", "store", "config", "httpx", "os", "open"):
        assert banned not in names, banned
    calls = {n.func.id for n in ast.walk(tree)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    assert "open" not in calls
