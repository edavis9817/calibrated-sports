"""a-64: one file per matchup, the spread and total records, and the index.

Run: pytest -q tests/test_game_matchup.py

No test opens a live store. The measurement is a-63's synthetic league
(tests/test_game_export.py); the matchup's own reads - the schedule's lines and
venues, weekly player sums, team pace - are handed to `game_matchup.build` as a
synthetic context. The fitted constants are the real committed result files; where
a test needs the spread published, the c-30 fit's recorded base is set to the
synthetic walk's, because the weights are only valid around the walk they were
fitted on - and a separate test shows the refusal when it is not.
"""
import copy
import functools
import json

import pytest

from jobs import export_web as E
from jobs import game_export as X
from jobs import game_matchup as GM
from jobs import metric_registry as MR
from models import game as G
from tests.test_game_export import NOW, synthetic_measure


@pytest.fixture(scope="module")
def measured(tmp_path_factory):
    return synthetic_measure(tmp_path_factory.mktemp("a64"))


def _base(measured):
    return {X.RECORD_KEY: X.envelope(X.RECORD_KIND, NOW, X.build_record(measured)),
            X.FORECAST_KEY: X.envelope(X.FORECAST_KIND, NOW, X.build_forecast(measured, NOW))}


def _ctx(measured, neutral=None):
    games, year = measured["games"], measured["year"]
    lines, sched, units = {}, {}, {}
    for g in games:
        gid = g["game_id"]
        sched[gid] = {"game_id": gid, "gameday": _day(g["kickoff_ts"]),
                      "location": "Neutral" if gid == neutral else "Home", "div_game": 0,
                      "stadium_id": "XXX00", "stadium": f"{g['home']} Field", "roof": "outdoors",
                      "temp": None if g["home_score"] is None else 60.0,
                      "wind": None if g["home_score"] is None else 9.0}
        if g["season"] == year:
            lines[gid] = {"spread_line": 2.5, "total_line": 44.5, "home_moneyline": -130.0,
                          "away_moneyline": 110.0, "home_spread_odds": -110.0,
                          "away_spread_odds": -110.0, "over_odds": -105.0, "under_odds": -115.0,
                          "stadium": f"{g['home']} Field", "roof": "outdoors",
                          "data_version": "2026-09-30", "ingested_ts": NOW - 3600}
            if g["home_score"] is not None:
                for t, o in ((g["home"], g["away"]), (g["away"], g["home"])):
                    units[(g["week"], t)] = {"opp": o, "pass": 220.0, "rush": 110.0, "int": 1.0}
    return {"lines": lines, "sched": sched, "units": units, "rel": "synthetic"}


def _day(ts):
    import datetime as dt
    return dt.datetime.fromtimestamp(ts, dt.timezone.utc).date().isoformat()


def _pace(measured, drop=None):
    """62 plays for every team-game with a score; `drop` removes one game's rows."""
    out = {}
    for g in measured["games"]:
        if g["home_score"] is None or g["game_id"] == drop:
            continue
        out[(g["game_id"], G.franchise(g["home"]))] = 62.0
        out[(g["game_id"], G.franchise(g["away"]))] = 62.0
    return out


@pytest.fixture
def results_on_this_walk(monkeypatch, measured):
    """The committed results, with c-30's recorded base set to the synthetic walk's."""
    from research import game_forecast as GF
    year = measured["year"]
    params = measured["walk"].params(year).as_dict()
    sigma = GF.margin_sigmas(measured["games"], measured["walk"], [year])[year]
    real = GM.load_result

    def load(rel):
        d = copy.deepcopy(real(rel))
        if rel == GM.C30_RESULT:
            d["fits"][str(year)]["params"] = params
            d["fits"][str(year)]["sigma_m"] = sigma
        return d
    monkeypatch.setattr(GM, "load_result", load)


def _build(measured, monkeypatch, pace=None, neutral=None, pace_err=None):
    files = _base(measured)
    ctx = _ctx(measured, neutral)
    monkeypatch.setattr(GM, "build", functools.partial(
        GM.build, ctx=ctx, pace=_pace(measured) if pace is None else pace, pace_err=pace_err))
    failed = []
    X.add_matchups(measured, files, failed, NOW, log=lambda *_: None)
    return files, failed


def _matchups(files):
    return {k: v for k, v in files.items() if k.startswith(GM.MATCHUP_DIR) and k != GM.INDEX_KEY}


# ------------------------------------------------------------------ the files

def test_every_file_passes_every_gate_and_the_index_lists_them_all(
        measured, monkeypatch, results_on_this_walk):
    files, failed = _build(measured, monkeypatch)
    assert failed == []
    mus = _matchups(files)
    assert len(mus) == 16
    assert {GM.RECORD_SPREAD_KEY, GM.RECORD_TOTAL_KEY, GM.INDEX_KEY} <= set(files)
    E.validate_contract(files)
    X.gate(files)
    idx = files[GM.INDEX_KEY]
    assert sorted(r["key"] for r in idx["games"]) == sorted(mus)


def test_the_numbers_are_the_models_beside_the_markets_and_their_difference(
        measured, monkeypatch, results_on_this_walk):
    files, _ = _build(measured, monkeypatch)
    fc = {g["game_id"]: g for g in files[X.FORECAST_KEY]["games"]}
    mkt = GM.devig(-130.0, 110.0)
    for mu in _matchups(files).values():
        ml = mu["numbers"]["moneyline"]
        assert ml["model"]["p_home_win"] == fc[mu["game_id"]]["p_home_win"]
        assert ml["market"]["p_home_win"] == round(mkt, 4)
        assert ml["difference"]["p_home_win"] == pytest.approx(
            ml["model"]["p_home_win"] - ml["market"]["p_home_win"], abs=1.5e-4)
        sp = mu["numbers"]["spread"]
        assert sp["model"]["reason"] is None and 0 < sp["model"]["p_home_covers"] < 1
        assert sp["model"]["p_push"] == 0.0               # 2.5 is a half-point line
        assert sp["difference"]["home_margin"] == pytest.approx(
            sp["model"]["home_margin_mean"] - 2.5, abs=0.11)
        tt = mu["numbers"]["total"]
        assert tt["model"]["reason"] is None and tt["model"]["mean"] > 0
        assert 0 < tt["model"]["p_over"] < 1
        # every number carries its record, and every record says it loses to the close
        for market in ("moneyline", "spread", "total"):
            rec = mu["numbers"][market]["record"]
            assert rec["file"] in files and rec["vs_close"]["display"] is GM.SHOW_CLOSE


def test_the_total_is_withheld_with_its_coverage_when_the_seasons_pace_is_incomplete(
        measured, monkeypatch, results_on_this_walk):
    played = [g for g in measured["games"] if g["season"] == measured["year"]
              and g["home_score"] is not None]
    files, failed = _build(measured, monkeypatch, pace=_pace(measured, drop=played[0]["game_id"]))
    assert failed == []
    for mu in _matchups(files).values():
        tt = mu["numbers"]["total"]
        assert tt["model"]["mean"] is None and tt["difference"]["total"] is None
        assert f"{2 * len(played) - 2} of the {2 * len(played)} team-games" in tt["model"]["reason"]
        assert mu["numbers"]["spread"]["model"]["reason"] is None   # the spread is unaffected
    for side in ("home", "away"):
        eff = next(iter(_matchups(files).values()))["teams"][side]["efficiency"]
        assert eff["reason"] is None or "team pace is built for" in eff["reason"]


def test_the_spread_is_withheld_when_c30s_fit_was_made_on_another_walk(measured, monkeypatch):
    """The synthetic walk's Elo constants are not the ones c-30 fitted around."""
    files, failed = _build(measured, monkeypatch)
    assert failed == []
    for mu in _matchups(files).values():
        sp = mu["numbers"]["spread"]["model"]
        assert sp["p_home_covers"] is None and "c-30's 2026 fit was made on Elo" in sp["reason"]


def test_a_fit_for_one_season_is_refused_for_another():
    _c30, _c31, problems = GM.frozen(2027, {"k": 20.0, "hfa": 50.0, "regress": 0.5}, 13.5)
    assert set(problems) == {"spread", "total"}
    _c30, _c31, problems = GM.frozen(2026, {"k": 20.0, "hfa": 50.0, "regress": 0.5}, 13.5)
    assert problems == {}


# ------------------------------------------------------------------- as-of

def test_nothing_in_a_matchup_reads_a_game_at_or_after_the_build_instant(
        measured, monkeypatch, results_on_this_walk):
    files, _ = _build(measured, monkeypatch)
    kick = {g["game_id"]: g["kickoff_ts"] for g in measured["games"]}
    for mu in _matchups(files).values():
        for side in ("home", "away"):
            t = mu["teams"][side]
            assert t["results"] and all(kick[r["game_id"]] < NOW for r in t["results"])
            assert [p["after_week"] for p in t["rating"]["path"]] == list(range(mu["week"]))
        for m in mu["head_to_head"]["meetings"]:
            assert kick[m["game_id"]] < NOW
        for c in mu["head_to_head"]["common_opponents"]:
            assert all(kick[r["game_id"]] < NOW for r in c["home_team"] + c["away_team"])


def test_head_to_head_counts_add_up_newest_first_and_state_where_the_record_starts(
        measured, monkeypatch, results_on_this_walk):
    files, _ = _build(measured, monkeypatch)
    for mu in _matchups(files).values():
        h = mu["head_to_head"]
        a = h["all_time"]
        assert a["games"] == len(h["meetings"]) == a["home_side_wins"] + a["away_side_wins"] + a["ties"]
        assert h["recent"]["games"] == min(len(h["meetings"]), GM.RECENT_MEETINGS)
        seasons = [m["season"] for m in h["meetings"]]
        assert seasons == sorted(seasons, reverse=True)
        assert h["record_starts"] == 1999


# -------------------------------------------------------------- the copies

def test_a_matchup_whose_copy_differs_from_its_owner_is_refused(
        measured, monkeypatch, results_on_this_walk):
    files, _ = _build(measured, monkeypatch)
    key, mu = next(iter(_matchups(files).items()))
    assert "agrees" in X.matchup_agrees(key, mu, files)
    for mutate, why in (
            (lambda m: m["numbers"]["moneyline"]["model"].__setitem__("p_home_win", 0.123),
             "p_home_win differs"),
            (lambda m: m["numbers"]["spread"]["record"]["vs_close"]["d_brier"].__setitem__(
                "estimate", -0.5), "spread: vs_close differs"),
            (lambda m: m["numbers"]["total"]["record"]["beats"].pop(), "total: beats differs"),
            (lambda m: m.__setitem__("season_stage", None), "season_stage differs")):
        bad = copy.deepcopy(mu)
        mutate(bad)
        with pytest.raises(RuntimeError, match=why):
            X.matchup_agrees(key, bad, files)


def test_a_matchup_that_fails_is_left_out_of_the_index_and_the_rest_publish(
        measured, monkeypatch, results_on_this_walk):
    victim = sorted(g["game_id"] for g in X.build_forecast(measured, NOW)["games"])[0]
    real = GM.numbers

    def numbers(g, *a, **k):
        if g["game_id"] == victim:
            raise RuntimeError("planted")
        return real(g, *a, **k)
    monkeypatch.setattr(GM, "numbers", numbers)
    files, failed = _build(measured, monkeypatch)
    assert [f["file"] for f in failed] == [f"{GM.MATCHUP_DIR}{victim}.json"]
    assert len(_matchups(files)) == 15
    assert victim not in {r["game_id"] for r in files[GM.INDEX_KEY]["games"]}


# ------------------------------------------------------------ the situation

def test_rest_short_week_and_bye_come_from_the_schedule():
    def g(gid, week, day):
        import datetime as dt
        ts = dt.datetime.fromisoformat(day + "T17:00:00+00:00").timestamp()
        return {"game_id": gid, "season": 2026, "week": week, "kickoff_ts": ts, "gameday": day,
                "home": "AAA", "away": gid[-3:]}
    games = [g("a_BBB", 1, "2026-09-13"), g("b_CCC", 2, "2026-09-17"),
             g("c_DDD", 4, "2026-10-04")]
    assert GM.rest_of(games, "AAA", games[0], 2026)["reason"] == "first game of the season"
    r = GM.rest_of(games, "AAA", games[1], 2026)
    assert (r["days"], r["short_week"], r["off_bye"]) == (4, True, False)
    r = GM.rest_of(games, "AAA", games[2], 2026)
    assert (r["days"], r["short_week"], r["off_bye"]) == (17, False, True)


def test_the_neutral_site_note_appears_only_at_a_neutral_site(
        measured, monkeypatch, results_on_this_walk):
    target = X.build_forecast(measured, NOW)["games"][0]["game_id"]
    files, _ = _build(measured, monkeypatch, neutral=target)
    for key, mu in _matchups(files).items():
        neutral = mu["game_id"] == target
        assert mu["situation"]["venue"]["neutral_site"] is neutral
        assert bool(mu["model_notes"]) is neutral


def test_wind_carries_its_effect_size_and_the_total_names_the_wind_it_assumed(
        measured, monkeypatch, results_on_this_walk):
    files, _ = _build(measured, monkeypatch)
    w = next(iter(_matchups(files).values()))["situation"]["weather"]
    assert w["forecast_wind_mph"] is None and w["forecast_reason"]
    assert w["wind_effect_on_total"]["per_mph"]["estimate"] == pytest.approx(-0.2443, abs=1e-4)
    assert w["total_assumes_wind_mph"] == 9.0      # the synthetic league's recorded wind


# --------------------------------------------------------------- the records

def test_the_records_carry_the_committed_figures_and_the_loss_to_the_close():
    sp = GM.build_record_spread(GM.load_result(GM.C30_RESULT))
    tt = GM.build_record_total(GM.load_result(GM.C31_RESULT))
    assert sp["vs_close"]["d_brier"]["estimate"] == 0.0081 and sp["vs_close"]["compared"] == "worse than"
    assert sp["population"]["games"] == 6580 and sp["against_baselines"] == []
    assert [p["line"] for p in sp["push_rates"]] == [3, 7]
    assert tt["vs_close"]["d_brier"]["estimate"] == 0.004 and tt["vs_close"]["compared"] == "worse than"
    assert {b["id"]: b["compared"] for b in tt["against_baselines"]} == {
        "league": "better than", "season_avg": "better than"}
    assert tt["population"]["games"] == 6758


def test_the_close_switch_is_one_constant_and_reaches_every_copy(
        measured, monkeypatch, results_on_this_walk):
    monkeypatch.setattr(GM, "SHOW_CLOSE", False)
    files, failed = _build(measured, monkeypatch)
    assert failed == []
    assert files[GM.RECORD_SPREAD_KEY]["vs_close"]["display"] is False
    for mu in _matchups(files).values():
        assert {mu["numbers"][m]["record"]["vs_close"]["display"]
                for m in ("moneyline", "spread", "total")} == {False}


def test_no_pick_language_in_any_string(measured, monkeypatch, results_on_this_walk):
    files, _ = _build(measured, monkeypatch)
    banned = ("our play", "pick", "lock", "units", "confident", "confidence", "strong",
              "bet ", "value", "edge", "guarantee", "take the", "appealing", "lean")
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
    for k, f in files.items():
        if k.startswith(GM.MATCHUP_DIR) or k in (GM.RECORD_SPREAD_KEY, GM.RECORD_TOTAL_KEY):
            walk(f)
    assert strings and [(b, s) for s in strings for b in banned if b in s] == []


def test_the_new_records_are_in_the_game_gate():
    files = {m["source"]["file"] for m in MR.GAME_METRICS}
    assert {GM.RECORD_SPREAD_KEY, GM.RECORD_TOTAL_KEY} <= files
    paths = [m["source"]["path"] for m in MR.METRICS if m["source"]["file"] == GM.RECORD_TOTAL_KEY]
    assert "vs_close.d_brier.estimate" in paths


def test_the_matchup_file_is_json_serialisable_and_round_trips(
        measured, monkeypatch, results_on_this_walk):
    files, _ = _build(measured, monkeypatch)
    for f in files.values():
        assert json.loads(json.dumps(f)) == f
