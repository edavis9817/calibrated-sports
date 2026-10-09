"""a-63: the game forecast published with its settlement record.

Run: pytest -q tests/test_game_export.py

No test opens the live store: `measure()` is driven end to end over a synthetic
league in a throwaway SQLite file, with a three-point grid and 40 bootstrap
draws so the whole module runs in seconds. The live figures are the job's own
`--check` output (reports/a-63-check.txt), which reproduces c-28's to the digit.
"""
import copy
import json
import os
import sqlite3

import numpy as np
import pytest

import config
from jobs import export_web as E
from jobs import game_export as X
from jobs import metric_registry as MR
from jobs import season_model as S

NOW = 1_790_000_000.0            # 2026-09-21, after the synthetic 2026 week 1


def _db(path):
    """Seasons 1999-2026, 32 teams, eight REG weeks; 2026 has week 1 played
    and weeks 2-3 ahead of NOW. Home teams are a little better, so the model
    has something to find, and every game carries a two-way moneyline."""
    rng = np.random.default_rng(63)
    teams = ["T%02d" % i for i in range(32)]
    strength = {t: rng.normal(0, 4) for t in teams}
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE nfl_games (game_id, season, week, game_type, kickoff_ts, "
                "home_team, away_team, home_score, away_score, data_version, "
                "home_moneyline, away_moneyline)")
    con.execute("CREATE TABLE nfl_teams (team_abbr, team_conf, team_division, data_version)")
    for i, t in enumerate(teams):
        con.execute("INSERT INTO nfl_teams VALUES (?,?,?,?)",
                    (t, "AFC" if i < 16 else "NFC", "D%d" % (i // 4), "2026-09-16"))
    for season in range(1999, 2027):
        for week in range(1, 9):
            perm = rng.permutation(teams)
            for j in range(0, 32, 2):
                h, a = str(perm[j]), str(perm[j + 1])
                kick = NOW + ((season - 2026) * 52 + (week - 1)) * 604800.0 - 86400 + j * 60
                played = kick < NOW
                m = strength[h] - strength[a] + 2.5 + rng.normal(0, 13)
                hs, as_ = (int(max(0, round(21 + m / 2))), int(max(0, round(21 - m / 2)))) \
                    if played else (None, None)
                con.execute("INSERT INTO nfl_games VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                            ("%d_%02d_%s_%s" % (season, week, a, h), season, week, "REG",
                             kick, h, a, hs, as_, "2026-09-30", -130, 110))
    con.commit()
    con.close()


def synthetic_measure(tmp_dir):
    """`measure()` over the synthetic league, a three-point grid and 40 draws."""
    path = os.path.join(str(tmp_dir), "league.db")
    _db(path)
    mp = pytest.MonkeyPatch()
    mp.setattr(S, "market_log_ro", lambda: sqlite3.connect(path))
    mp.setattr(S, "grid", lambda: [S.M.EloParams(20.0, 50.0, 1 / 3),
                                   S.M.EloParams(30.0, 25.0, 0.5),
                                   S.M.EloParams(10.0, 75.0, 0.25)])
    try:
        return X.measure(log=lambda *_: None, draws=40)
    finally:
        mp.undo()


def synthetic_files(tmp_dir):
    """Both files, and a-64's spread and total records (read from the committed
    result files), as the metric registry's tests need them."""
    from jobs import game_matchup as GM
    m = synthetic_measure(tmp_dir)
    return {X.RECORD_KEY: X.envelope(X.RECORD_KIND, NOW, X.build_record(m)),
            X.FORECAST_KEY: X.envelope(X.FORECAST_KIND, NOW, X.build_forecast(m, NOW)),
            GM.RECORD_SPREAD_KEY: X.envelope(GM.MODEL_RECORD_KIND, NOW, GM.build_record_spread(
                GM.load_result(GM.C30_RESULT))),
            GM.RECORD_TOTAL_KEY: X.envelope(GM.MODEL_RECORD_KIND, NOW, GM.build_record_total(
                GM.load_result(GM.C31_RESULT)))}


@pytest.fixture(autouse=True)
def _no_matchups(monkeypatch):
    """a-63's tests cover the forecast and its record. The matchup step (a-64) reads
    the stores unless it is handed a context, so it is off here and driven over a
    synthetic context in tests/test_game_matchup.py."""
    monkeypatch.setattr(X, "add_matchups", lambda *a, **k: None)


@pytest.fixture(scope="module")
def measured(tmp_path_factory):
    return synthetic_measure(tmp_path_factory.mktemp("a63"))


@pytest.fixture
def files(measured):
    return {X.RECORD_KEY: X.envelope(X.RECORD_KIND, NOW, X.build_record(measured)),
            X.FORECAST_KEY: X.envelope(X.FORECAST_KIND, NOW, X.build_forecast(measured, NOW))}


# ------------------------------------------------------------------ the files

def test_both_files_pass_the_contract_the_source_gate_and_the_metric_gate(files):
    st = X.gate(files)
    assert "0 undeclared disagreements" in st and "agree with the record" in st
    assert len([m for m in MR.GAME_METRICS
                if MR._files_of(m) <= {X.RECORD_KEY, X.FORECAST_KEY}]) == 12


def test_the_contract_refuses_a_wrong_kind_and_an_extra_field(files):
    f = copy.deepcopy(files[X.RECORD_KEY])
    f["kind"] = "game.forecast"
    with pytest.raises(Exception):
        E.validate_contract({X.RECORD_KEY: f})
    g = copy.deepcopy(files[X.FORECAST_KEY])
    g["pick"] = "HOME"
    with pytest.raises(Exception):
        E.validate_contract({X.FORECAST_KEY: g})


def test_the_forecast_is_as_of_and_forecasts_only_games_not_yet_kicked_off(files, measured):
    fc = files[X.FORECAST_KEY]
    assert fc["season"] == 2026 and fc["week"] == 2 and len(fc["games"]) == 16
    assert fc["as_of"]["instant"] == X.iso(NOW)
    assert fc["as_of"]["results_through"] < fc["as_of"]["instant"]
    for g in fc["games"]:
        assert g["kickoff"] > fc["as_of"]["instant"]
        m = g["margin"]
        assert 0 < g["p_home_win"] < 1
        # P(margin > 0) is p_home_win: the mean's sign follows it
        assert (m["mean"] > 0) == (g["p_home_win"] > 0.5) or abs(g["p_home_win"] - 0.5) < 1e-3
        widths = [b["hi"] - b["lo"] for b in m["bands"]]
        assert [b["level"] for b in m["bands"]] == list(X.BANDS) and widths == sorted(widths)


def test_the_forecast_refuses_an_as_of_it_cannot_keep(measured):
    """A result that kicked off at or after the build instant is information
    the forecast would be claiming not to have had."""
    first = min(g["kickoff_ts"] for g in measured["games"]
                if g["season"] == 2026 and g["home_score"] is not None)
    with pytest.raises(RuntimeError, match="refusing an as-of"):
        X.build_forecast(measured, first)


def test_current_games_skips_kicked_off_games_and_takes_the_first_week_ahead():
    games = [dict(season=2026, week=w, kickoff_ts=k, home_score=None, game_type="REG",
                  game_id=str(k)) for w, k in ((3, 100.0), (3, 300.0), (4, 400.0), (5, 250.0))]
    week, gt, idx = X.current_games(games, 2026, 200.0)
    assert week == 3 and gt == "REG" and idx == [1]
    assert X.current_games(games, 2026, 1e9) == (None, None, [])


def test_no_game_ahead_is_an_empty_list_with_a_reason_not_a_failure(measured):
    body = X.build_forecast(measured, NOW + 10 * 604800)
    assert body["games"] == [] and body["season_stage"] is None and body["reason"]
    E.validate_contract({X.FORECAST_KEY: X.envelope(X.FORECAST_KIND, NOW, body)})


# ------------------------------------------------ the early-season sentence

def _fake(stages):
    """A measurement with chosen plain-Elo intervals per stage."""
    def cmp(est, lo, hi):
        return {"diffs": {"dBrier": {"est": est, "lo": lo, "hi": hi, "se": 0.001,
                                     "games": 500, "n": 500}}}
    return {"seasons": [2001, 2025], "n": {"all": 900, "weeks_1_4": 300, "weeks_5_plus": 600},
            "stages": {st: {"elo_nomov": cmp(*v)} for st, v in stages.items()}}


@pytest.mark.parametrize("early,word", [((-0.0008, -0.0024, 0.0007), "no better than"),
                                        ((-0.0030, -0.0040, -0.0010), "better than"),
                                        ((0.0030, 0.0010, 0.0040), "worse than")])
def test_the_week_4_sentence_is_worded_from_its_interval_and_can_come_out_otherwise(early, word):
    m = _fake({"weeks_1_4": early, "weeks_5_plus": (-0.003, -0.004, -0.002),
               "all": (-0.002, -0.003, -0.001)})
    st = X.season_stage(m, 4, "REG")
    assert st["stage"] == "weeks_1_4" and st["compared"] == word
    assert st["statement"].startswith(f"This is week 4. In weeks 1 to 4, this forecast has been {word} ")
    assert "(plain Elo)" in st["statement"] and "(Brier score)" in st["statement"]
    assert st["other"]["stage"] == "weeks_5_plus"


def test_week_5_quotes_the_later_record_and_the_postseason_quotes_all_games():
    m = _fake({"weeks_1_4": (-0.0008, -0.0024, 0.0007), "weeks_5_plus": (-0.003, -0.004, -0.002),
               "all": (-0.002, -0.003, -0.001)})
    st = X.season_stage(m, 5, "REG")
    assert st["stage"] == "weeks_5_plus" and st["statement"].startswith(
        "This is week 5. From week 5 on, this forecast has been better than")
    assert "In weeks 1 to 4 it has been no better than it" in st["statement"]
    post = X.season_stage(m, 19, "WC")
    assert post["stage"] == "all" and post["other"] is None


def test_no_pick_language_and_no_confidence_adjectives_in_any_string(files):
    banned = ("our play", "pick", "lock", "units", "confident", "confidence", "strong",
              "bet ", "value", "edge", "guarantee")
    strings = []

    def walk(o):
        if isinstance(o, dict):
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
        elif isinstance(o, str):
            strings.append(o.lower())
    for f in files.values():
        walk(f)
    hits = [(b, s) for s in strings for b in banned if b in s]
    assert hits == []


# --------------------------------------------- the withheld market comparison

def test_the_market_comparison_is_stored_with_figures_and_no_text(files):
    mc = files[X.RECORD_KEY]["market_comparison"]
    assert mc["withheld"]["display"] is False and mc["games"] > 0
    assert mc["d_brier"]["estimate"] is not None
    paths = []

    def walk(o, p):
        if isinstance(o, dict):
            for k, v in o.items():
                walk(v, p + [k])
        elif isinstance(o, str):
            paths.append(".".join(p))
    walk(mc, [])
    # metadata only: nothing a page could print as a sentence about the result
    assert sorted(paths) == ["benchmark.devig", "benchmark.source", "d_auc.verdict",
                             "d_brier.verdict", "d_discrimination.verdict",
                             "d_miscalibration.verdict", "withheld.decided_by",
                             "withheld.decided_on", "withheld.unit"]


def test_the_contract_will_not_carry_a_displayable_market_comparison(files):
    f = copy.deepcopy(files[X.RECORD_KEY])
    f["market_comparison"]["withheld"]["display"] = True
    with pytest.raises(Exception):
        E.validate_contract({X.RECORD_KEY: f})
    g = copy.deepcopy(files[X.RECORD_KEY])
    g["market_comparison"]["statement"] = "the model loses to the close"
    with pytest.raises(Exception):
        E.validate_contract({X.RECORD_KEY: g})


def test_the_market_comparison_is_not_in_the_metric_registry():
    """The registry is exported into the manifest; a registered path is one a
    page is invited to render."""
    locs = [m["source"]["path"] for m in MR.METRICS] + \
        [c["path"] for m in MR.METRICS for c in m["copies"]]
    assert not [p for p in locs if "market_comparison" in p]


# ----------------------------------------------------------- the pair check

def test_the_stage_check_refuses_a_forecast_that_disagrees_with_its_record(files):
    bad = copy.deepcopy(files)
    bad[X.FORECAST_KEY]["season_stage"]["vs_elo_nomov"]["estimate"] += 0.001
    with pytest.raises(RuntimeError, match="refusing both"):
        X.stage_agrees(bad)
    assert "agree" in X.stage_agrees(files)


def test_the_metric_gate_sees_the_record(files):
    bad = copy.deepcopy(files)
    del bad[X.RECORD_KEY]["baselines"][0]["d_brier"]["estimate"]
    with pytest.raises(Exception):
        MR.require(bad, MR.GAME_METRICS)


# ------------------------------------------------------ non-fatal publishing

def _publish(dest, monkeypatch, measured, **boom):
    monkeypatch.setattr(X, "measure", lambda **_: measured)
    for name in boom:
        def fail(*_a, _n=name, **_k):
            raise RuntimeError(f"planted failure in {_n}")
        monkeypatch.setattr(X, name, fail)
    logs = []
    out = X.publish(str(dest), now_ts=NOW, log=logs.append)
    return out, logs


def test_publish_writes_both_files_and_owns_no_prefix(tmp_path, monkeypatch, measured):
    (tmp_path / "game" / "nfl").mkdir(parents=True)
    stray = tmp_path / "game" / "nfl" / "old.json"
    stray.write_text("{}", encoding="utf-8")
    out, _ = _publish(tmp_path, monkeypatch, measured)
    assert out["failed"] == [] and out["written"] == 2
    assert stray.exists(), "the job must delete nothing"
    for key in (X.RECORD_KEY, X.FORECAST_KEY):
        assert json.loads((tmp_path / key).read_text(encoding="utf-8"))["kind"].startswith("game.")


def test_a_failing_forecast_keeps_its_previous_file_and_the_record_still_publishes(
        tmp_path, monkeypatch, measured):
    prev = tmp_path / X.FORECAST_KEY
    prev.parent.mkdir(parents=True)
    prev.write_text('{"previous": true}', encoding="utf-8")
    out, logs = _publish(tmp_path, monkeypatch, measured, build_forecast=True)
    assert [f["file"] for f in out["failed"]] == [X.FORECAST_KEY]
    assert prev.read_text(encoding="utf-8") == '{"previous": true}'
    assert (tmp_path / X.RECORD_KEY).exists()
    assert any("GAME FILE FAILED" in l for l in logs)


def test_a_failed_measurement_raises_nothing_and_writes_nothing(tmp_path, monkeypatch):
    def boom(**_):
        raise sqlite3.OperationalError("no such table: nfl_games")
    monkeypatch.setattr(X, "measure", boom)
    out = X.publish(str(tmp_path), now_ts=NOW, log=lambda *_: None)
    assert out["built"] == [] and out["written"] == 0 and out["failed"][0]["file"] == "*"
    assert not (tmp_path / "game").exists()


def test_the_cli_exit_code_reports_a_failed_file(tmp_path, monkeypatch, measured):
    monkeypatch.setattr(X, "measure", lambda **_: measured)
    monkeypatch.setattr(X, "build_record", lambda m: (_ for _ in ()).throw(RuntimeError("x")))
    monkeypatch.setattr(E, "assert_numeric_stack", lambda: True)
    assert X.main(["--write", "--dest", str(tmp_path), "--now", str(NOW)]) == 1
    assert (tmp_path / X.FORECAST_KEY).exists() and not (tmp_path / X.RECORD_KEY).exists()


def test_nothing_here_reads_kalshi_spreads_or_totals():
    src = open(X.__file__, encoding="utf-8").read()
    code = src.split('"""', 2)[2]                 # past the module docstring
    assert "KXNFLSPREAD" not in code and "KXNFLTOTAL" not in code and "quotes" not in code
