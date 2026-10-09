"""a-75: a figure that may only be cited with a sentence is never served without it.

Run: pytest -q tests/test_required_sentences.py

Track F (f-26, f-27) returned five published game figures as "citable only with a
stated sentence attached". `jobs.required_sentences` attaches the sentence and
`require` refuses a file without it. These tests are keyed on each figure's own
`file|holder` path, read from the registry: a fourth sentence added to the carried
file is a new parameter here and fails until its builder attaches it.

a-77 closed the two places a-75 measured it had missed: `record.json`'s
`by_stage.weeks_1_4` (a sixth registry entry for the stage figure's second home) and
every matchup file's `numbers.{market}.record` copies, which print the figure's own
digits beside a pointer and so carry the owner's sentence, not the pointer alone.

a-79 made a moved figure a refusal. f-31 measured that a served estimate planted
+0.0090 away from the one its sentence was measured beside still passed the gate,
with the sentence worded against the moved number. The last section plants that
movement at every registered place and through the export's own build, and fails
if anything is published.

No test opens a live store: the forecast is a-63's synthetic league and the two
records are the real committed result files, as in tests/test_game_matchup.py.
"""
import copy

import pytest

from jobs import export_web as E
from jobs import game_export as X
from jobs import game_matchup as GM
from jobs import metric_registry as MR
from jobs import required_sentences as RS
from tests.test_game_export import NOW, _fake, synthetic_files
from tests.test_game_matchup import (_build, _matchups, measured,  # noqa: F401 - fixtures
                                     results_on_this_walk)

EARLY = (-0.0008, -0.0024, 0.0009)          # the weeks 1-4 figure f-27 attacked
LATE = (-0.003, -0.004, -0.002)
ALL = (-0.002, -0.003, -0.001)


def _stage(week, game_type="REG", early=EARLY):
    return X.season_stage(_fake({"weeks_1_4": early, "weeks_5_plus": LATE, "all": ALL}),
                          week, game_type)


def _forecast(week, game_type="REG"):
    """A forecast file whose stage is the chosen week's. Only the stage is read."""
    return {"kind": X.FORECAST_KIND, "season_stage": _stage(week, game_type)}


@pytest.fixture(scope="module")
def served(tmp_path_factory):
    """The four files as the export builds them, with the forecast's stage in week 3
    so the weeks 1-4 figure is the headline; `later` has it in `other`."""
    files = synthetic_files(tmp_path_factory.mktemp("a75"))
    files[X.FORECAST_KEY]["season_stage"] = _stage(3)
    return files


def _holders(files):
    """-> {figure id: [(key, holder)]} for every registered figure the files serve."""
    out = {}
    for key, payload in files.items():
        for fid, _path, blk, _e in RS.located(key, payload):
            out.setdefault(fid, []).append((key, blk))
    return out


# ------------------------------------------------------- the registry itself

def test_the_registry_is_the_five_figures_f27_returned_and_nothing_else():
    """Five figures at six places: the weeks 1-4 figure is published in the forecast
    and again in the record's `by_stage` (a-77), and f-27 named both."""
    assert set(RS.REQUIRED) == {
        "game/nfl/record.json|by_stage.weeks_1_4.vs.elo_nomov",
        "game/nfl/record_total.json|vs_close",
        "game/nfl/record_total.json|against_baselines[id=league]",
        "game/nfl/record_total.json|against_baselines[id=season_avg]",
        "game/nfl/record_spread.json|against_shape",
        "game/nfl/forecast.json|season_stage[stage=weeks_1_4]"}
    assert set(RS.REQUIRED) == set(RS.required())
    assert {e["kind"] for e in RS.required().values()} == set(RS.WORDING)


def test_every_registered_figure_is_found_in_what_the_export_builds(served):
    found = _holders(served)
    assert set(found) == set(RS.REQUIRED)           # none unlocated: the gate walks all six
    assert RS.require(served) == ("sentence check: 6 figure(s) that need a sentence carry it, "
                                  "across 4 file(s)")
    assert RS.FIELD in MR.resolve(served[GM.RECORD_TOTAL_KEY], "vs_close")


# --------------------------------------- served without its sentence: refused

@pytest.mark.parametrize("fid", RS.REQUIRED)
def test_a_figure_served_without_its_sentence_is_refused(served, fid):
    files = copy.deepcopy(served)
    key, blk = _holders(files)[fid][0]
    figure = RS.required()[fid]["figure"]
    assert blk[RS.FIELD] is not None and blk[figure]["estimate"] is not None
    blk[RS.FIELD] = None
    with pytest.raises(RS.MissingSentence, match="served without its sentence"):
        RS.require(files)
    with pytest.raises(RS.MissingSentence, match="served without its sentence"):
        X.gate({key: files[key]})                   # the export's own gate, so no file writes
    E.validate_contract({key: files[key]})          # null is legal: only the gate above refuses it
    del blk[RS.FIELD]                               # the key's absence is the contract's to refuse
    with pytest.raises(E.ContractError):
        E.validate_contract({key: files[key]})


@pytest.mark.parametrize("fid", RS.REQUIRED)
def test_a_sentence_that_is_not_the_one_its_figures_word_is_refused(served, fid):
    files = copy.deepcopy(served)
    _key, blk = _holders(files)[fid][0]
    blk[RS.FIELD]["statement"] = "It is fine."
    with pytest.raises(RS.MissingSentence, match="not the one its figures word"):
        RS.require(files)


def test_a_sentence_on_a_figure_nobody_registered_is_refused(served):
    files = copy.deepcopy(served)
    donor = files[GM.RECORD_TOTAL_KEY]["vs_close"][RS.FIELD]
    assert files[GM.RECORD_SPREAD_KEY]["vs_close"][RS.FIELD] is None
    files[GM.RECORD_SPREAD_KEY]["vs_close"][RS.FIELD] = donor
    with pytest.raises(RS.MissingSentence, match="vs_close carries a sentence no registered"):
        RS.require(files)


def _with_fourth(monkeypatch, entry):
    data = copy.deepcopy(RS.carried())
    data["sentences"].append(entry)
    monkeypatch.setattr(RS, "carried", lambda: data)


def test_a_fourth_sentence_fails_until_its_builder_attaches_it(served, monkeypatch):
    """Registering a figure is one entry in the carried file. The file that serves
    it is refused from that moment, not when someone remembers the builder."""
    _with_fourth(monkeypatch, {
        "file": GM.RECORD_SPREAD_KEY, "holder": "vs_close", "figure": "d_brier",
        "kind": "at_mde_uncorrected",
        "measured_against": {"estimate": 0.0081, "interval": [0.0054, 0.0107]},
        "figures": {"mde_ratio": 2.11, "intervals_registered": 72, "correction": "Bonferroni",
                    "adjusted_p": 0.001, "alpha": 0.05}})
    with pytest.raises(RS.MissingSentence, match="record_spread.json: vs_close.d_brier is served"):
        RS.require(served)


def test_a_registered_path_that_does_not_resolve_is_a_failure_not_a_skip(served, monkeypatch):
    _with_fourth(monkeypatch, {
        "file": GM.RECORD_SPREAD_KEY, "holder": "against_baselines[id=league]",
        "figure": "d_brier", "kind": "recorded_wind",
        "measured_against": {"estimate": 0.0, "interval": None}, "figures": {}})
    with pytest.raises(RS.MissingSentence, match="not where the registry says"):
        RS.require(served)


def test_two_sentences_for_one_figure_are_refused(monkeypatch):
    _with_fourth(monkeypatch, copy.deepcopy(RS.carried()["sentences"][0]))
    with pytest.raises(ValueError, match="twice"):
        RS.required()


# ------------------------------------------- the stage figure, wherever it is

def test_the_weeks_1_4_sentence_follows_the_figure_through_the_season():
    early, late, post = _stage(3), _stage(6), _stage(19, "WC")
    assert early["qualifier"]["kind"] == "comparator_at_grid_max"
    assert early["other"]["qualifier"] is None                  # weeks 5+ needs none
    assert late["qualifier"] is None
    assert late["other"]["stage"] == "weeks_1_4"
    assert late["other"]["qualifier"] == early["qualifier"]     # the same sentence, in `other`
    assert post["qualifier"] is None and post["other"] is None  # the figure is not served
    for week, gtype, n in ((3, "REG", 1), (6, "REG", 1), (19, "WC", 0)):
        fc = _forecast(week, gtype)
        assert len(RS.located(X.FORECAST_KEY, fc)) == n
        assert RS.require({X.FORECAST_KEY: fc}).startswith(f"sentence check: {n} figure")
    fc = _forecast(6)
    fc["season_stage"]["other"]["qualifier"] = None
    with pytest.raises(RS.MissingSentence, match=r"season_stage\.other\.vs_elo_nomov is served"):
        RS.require({X.FORECAST_KEY: fc})
    assert RS.require({X.FORECAST_KEY: {"kind": X.FORECAST_KIND, "season_stage": None}}) \
        .startswith("sentence check: 0 figure")


def test_every_matchup_carries_the_forecasts_stage_sentence_and_is_gated_on_it(
        measured, monkeypatch, results_on_this_walk):  # noqa: F811
    files, failed = _build(measured, monkeypatch)
    assert failed == []
    mus = _matchups(files)
    assert mus
    stage = files[X.FORECAST_KEY]["season_stage"]
    carrier = stage if stage["stage"] == RS.EARLY_STAGE else stage["other"]
    assert carrier["stage"] == RS.EARLY_STAGE and carrier["qualifier"] is not None
    for key, mu in mus.items():
        assert mu["season_stage"] == stage
        bad = copy.deepcopy(mu)
        blk = bad["season_stage"] if stage["stage"] == RS.EARLY_STAGE else bad["season_stage"]["other"]
        blk["qualifier"] = None
        with pytest.raises(RS.MissingSentence, match="served without its sentence"):
            RS.require({key: bad})
    # record_total 3, record_spread 1, the forecast 1, the record's by_stage 1; and each
    # matchup prints four: the stage figure and the total record's three (a-77)
    n = 6 + 4 * len(mus)
    assert RS.require(files).startswith(f"sentence check: {n} figure(s)")


# ------------------------------- a-77: the record's second copy of the stage figure

BY_STAGE = "game/nfl/record.json|by_stage.weeks_1_4.vs.elo_nomov"
GRID = ("Plain Elo's K was held at the top of the fitting grid (40) in 25 of 25 seasons; with K "
        "allowed to 120 the same comparison is -0.0017 [-0.0031, -0.0004], one seed.")


def _record(early=EARLY):
    """The record's `by_stage`, built by the export's own `stage_block`. Every baseline
    is given the SAME interval, so only the registry can tell plain Elo's apart."""
    def cmp(est, lo, hi):
        return {"corp_model": {"bs": 0.21}, "corp_comparator": {"bs": 0.22},
                "diffs": {"dBrier": {"est": est, "lo": lo, "hi": hi, "se": 0.001,
                                     "games": 500, "n": 500}}}
    m = {"n": {"weeks_1_4": 300, "weeks_5_plus": 600},
         "stages": {st: {b: cmp(*v) for b, _l, _d in X.BASELINES}
                    for st, v in (("weeks_1_4", early), ("weeks_5_plus", LATE))}}
    return {"kind": X.RECORD_KIND,
            "by_stage": {st: X.stage_block(m, st) for st in ("weeks_1_4", "weeks_5_plus")}}


def test_the_records_by_stage_carries_the_grid_sentence_on_the_one_figure_that_needs_it():
    rec = _record()
    got = {(st, b): blk["qualifier"] for st, s in rec["by_stage"].items()
           for b, blk in s["vs"].items()}
    assert len(got) == 6                                        # two stages, three baselines
    carried = {k for k, q in got.items() if q is not None}
    assert carried == {("weeks_1_4", "elo_nomov")}
    q = got[("weeks_1_4", "elo_nomov")]
    assert q["statement"] == GRID                               # worded from the carried numbers
    assert q["kind"] == "comparator_at_grid_max" and q["served_matches"] is True
    assert _record(early=(-0.0011, -0.0024, 0.0009))["by_stage"]["weeks_1_4"]["vs"][
        "elo_nomov"]["qualifier"]["served_matches"] is False


def test_the_two_places_the_stage_figure_is_registered_carry_one_measurement():
    """Two registry entries, one measurement of F's. Nothing but this stops the copy
    in the carried file drifting from the entry it was copied from."""
    req = RS.required()
    a, b = req[BY_STAGE], req["game/nfl/forecast.json|season_stage[stage=weeks_1_4]"]
    for k in ("kind", "measured_against", "figures"):       # `figure` is each file's own field name
        assert a[k] == b[k], k
    assert _record()["by_stage"]["weeks_1_4"]["vs"]["elo_nomov"]["qualifier"] == \
        _stage(3)["qualifier"] == _stage(6)["other"]["qualifier"]


@pytest.mark.parametrize("week", [3, 6])
def test_the_stage_check_refuses_the_record_and_the_forecast_wording_one_figure_twice(week):
    files = {X.RECORD_KEY: _record(), X.FORECAST_KEY: _forecast(week)}
    assert X.stage_agrees(files).startswith("stage check: 2 stage figure(s) agree")
    drift = copy.deepcopy(files)
    drift[X.RECORD_KEY]["by_stage"]["weeks_1_4"]["vs"]["elo_nomov"]["qualifier"]["statement"] = "x"
    with pytest.raises(RuntimeError, match="different sentences for one figure"):
        X.stage_agrees(drift)
    bare = copy.deepcopy(files)
    bare[X.RECORD_KEY]["by_stage"]["weeks_1_4"]["vs"]["elo_nomov"]["qualifier"] = None
    with pytest.raises(RuntimeError, match="different sentences for one figure"):
        X.stage_agrees(bare)
    with pytest.raises(RS.MissingSentence,
                       match=r"record\.json: by_stage\.weeks_1_4\.vs\.elo_nomov\.d_brier is "
                             r"served without its sentence"):
        RS.require({X.RECORD_KEY: bare[X.RECORD_KEY]})


# ------------------------------------- a-77: the matchup's copies of record figures

COPIES = {      # where a matchup prints a registered record figure -> the figure's id
    "numbers.total.record.vs_close": "game/nfl/record_total.json|vs_close",
    "numbers.total.record.beats[id=league]":
        "game/nfl/record_total.json|against_baselines[id=league]",
    "numbers.total.record.beats[id=season_avg]":
        "game/nfl/record_total.json|against_baselines[id=season_avg]"}


@pytest.fixture
def matchups(measured, monkeypatch, results_on_this_walk):  # noqa: F811
    files, failed = _build(measured, monkeypatch)
    assert failed == []
    mus = _matchups(files)
    assert mus
    return files, mus


def test_a_matchup_copy_printed_with_its_digits_carries_the_owners_sentence(matchups):
    files, mus = matchups
    tt = files[GM.RECORD_TOTAL_KEY]
    wind = tt["vs_close"]["qualifier"]["statement"]
    assert wind.startswith("Scored with each game's recorded wind")
    assert "+0.0056 [+0.0035, +0.0079]" in wind                 # the carried numbers
    for key, mu in mus.items():
        got = {path: (fid, blk) for fid, path, blk, _e in RS.located(key, mu)}
        stage = [p for p in got if p.startswith("season_stage")]
        assert len(stage) == 1 and set(got) - set(stage) == set(COPIES)     # the walk's coverage
        for path, fid in COPIES.items():
            assert got[path][0] == fid
            _file, holder = fid.split("|")
            owner = MR.resolve(tt, holder)
            blk = got[path][1]
            assert blk["d_brier"]["estimate"] is not None           # digits, not only a pointer
            assert blk["d_brier"] == owner["d_brier"]
            assert blk["qualifier"] is not None and blk["qualifier"] == owner["qualifier"]
        assert mu["numbers"]["total"]["record"]["vs_close"]["qualifier"]["statement"] == wind
        # the moneyline's and the spread's copies are of figures that need none
        for market in ("moneyline", "spread"):
            rec = mu["numbers"][market]["record"]
            assert rec["vs_close"]["qualifier"] is None
            assert all(b["qualifier"] is None for b in rec["beats"])


@pytest.mark.parametrize("path", sorted(COPIES))
def test_a_matchup_copy_served_without_its_sentence_is_refused(matchups, path):
    _files, mus = matchups
    for key, mu in mus.items():
        bad = copy.deepcopy(mu)
        blk = MR.resolve(bad, path)
        assert blk["qualifier"] is not None
        blk["qualifier"] = None
        with pytest.raises(RS.MissingSentence) as x:
            RS.require({key: bad})
        assert f"{key}: {path}.d_brier is served without its sentence" in str(x.value)
        with pytest.raises(RS.MissingSentence, match="served without its sentence"):
            X.gate({key: bad})                      # the export's own gate
        E.validate_contract({key: bad})             # null is legal; the gate is what refuses
        del blk["qualifier"]
        with pytest.raises(E.ContractError):
            E.validate_contract({key: bad})
        wrong = copy.deepcopy(mu)
        MR.resolve(wrong, path)["qualifier"]["statement"] = "It is fine."
        with pytest.raises(RS.MissingSentence, match="not the one its figures word"):
            RS.require({key: wrong})


def test_a_sentence_on_a_matchup_copy_nobody_registered_is_refused(matchups):
    _files, mus = matchups
    key, mu = next(iter(mus.items()))
    bad = copy.deepcopy(mu)
    bad["numbers"]["spread"]["record"]["vs_close"]["qualifier"] = copy.deepcopy(
        bad["numbers"]["total"]["record"]["vs_close"]["qualifier"])
    with pytest.raises(RS.MissingSentence,
                       match=r"numbers\.spread\.record\.vs_close carries a sentence no registered"):
        RS.require({key: bad})


def test_registering_a_record_figure_requires_it_in_the_matchup_copy_too(matchups, monkeypatch):
    """The copies are found from the matchup's own pointers, not from a list here: a
    figure registered tomorrow is refused bare in its copy from that moment."""
    _files, mus = matchups
    key, mu = next(iter(mus.items()))
    assert RS.require({key: mu}).startswith("sentence check: 4 figure(s)")
    _with_fourth(monkeypatch, {
        "file": GM.RECORD_SPREAD_KEY, "holder": "vs_close", "figure": "d_brier",
        "kind": "at_mde_uncorrected",
        "measured_against": {"estimate": 0.0081, "interval": [0.0054, 0.0107]},
        "figures": {"mde_ratio": 2.11, "intervals_registered": 72, "correction": "Bonferroni",
                    "adjusted_p": 0.001, "alpha": 0.05}})
    with pytest.raises(RS.MissingSentence,
                       match=r"numbers\.spread\.record\.vs_close\.d_brier is served without"):
        RS.require({key: mu})


def test_a_market_the_copy_walk_cannot_read_is_a_failure_not_a_skip(matchups):
    _files, mus = matchups
    key, mu = next(iter(mus.items()))
    bad = copy.deepcopy(mu)
    bad["numbers"]["first_half"] = copy.deepcopy(bad["numbers"]["total"])
    with pytest.raises(RS.MissingSentence, match="first_half: a market the sentence check"):
        RS.require({key: bad})


# ------------------------------------ the sentences, and that they can differ

def test_the_three_served_sentences_are_f27s_worded_from_the_carried_figures():
    sp = GM.build_record_spread(GM.load_result(GM.C30_RESULT))
    tt = GM.build_record_total(GM.load_result(GM.C31_RESULT))
    assert tt["vs_close"]["qualifier"]["statement"] == (
        "Scored with each game's recorded wind, which no forecast made before kickoff has. "
        "With wind taken out of the model the same comparison is +0.0056 [+0.0035, +0.0079]: "
        "recorded wind moves it by 0.0016 [0.0004, 0.0027], which makes the published figure "
        "the favourable end.")
    base = {b["id"]: b["qualifier"]["statement"] for b in tt["against_baselines"]}
    assert "is -0.0065 [-0.0078, -0.0052]: recorded wind moves it by 0.0010, which" in base["league"]
    assert "is -0.0053 [-0.0067, -0.0040]: recorded wind moves it by 0.0010, which" in base["season_avg"]
    assert sp["against_shape"]["qualifier"]["statement"] == (
        "This estimate sits exactly on its own minimum detectable effect (ratio 1.00) and does "
        "not survive a Bonferroni correction over the 72 intervals the study registered "
        "(adjusted p 0.38).")
    assert _stage(3)["qualifier"]["statement"] == (
        "Plain Elo's K was held at the top of the fitting grid (40) in 25 of 25 seasons; with K "
        "allowed to 120 the same comparison is -0.0017 [-0.0031, -0.0004], one seed.")
    # the sentence is a footnote: the verdict words are still the interval's own
    assert sp["against_shape"]["compared"] == "worse than"
    assert sp["vs_close"]["qualifier"] is None


def test_the_served_figures_are_the_ones_the_sentences_were_measured_beside():
    """The two records are read from committed result files, so these hold exactly.
    If one goes False the figure moved and F's measurement no longer sits beside it."""
    sp = GM.build_record_spread(GM.load_result(GM.C30_RESULT))
    tt = GM.build_record_total(GM.load_result(GM.C31_RESULT))
    got = [sp["against_shape"]["qualifier"], tt["vs_close"]["qualifier"]] + [
        b["qualifier"] for b in tt["against_baselines"]]
    assert [q["served_matches"] for q in got] == [True, True, True, True]
    assert tt["vs_close"]["d_brier"]["estimate"] == 0.004
    assert tt["vs_close"]["d_brier"]["interval"] == [0.0019, 0.0063]
    # the estimate is compared, the interval is not: the export's own draw of the stage
    # figure is [-0.0024, +0.0007] and F's is [-0.0024, +0.0009], for the same -0.0008
    assert _stage(3)["qualifier"]["served_matches"] is True
    assert _stage(3, early=(-0.0008, -0.0024, 0.0007))["qualifier"]["served_matches"] is True
    assert _stage(3, early=(-0.0011, -0.0024, 0.0009))["qualifier"]["served_matches"] is False


def test_each_wording_can_come_out_the_other_way():
    wind = {"without_wind": {"estimate": 0.0056, "interval": [0.0035, 0.0079]},
            "wind_worth": {"estimate": 0.0016, "interval": None}}
    assert "the favourable end" in RS._wind(wind, {"estimate": 0.0040})
    assert "the unfavourable end" in RS._wind(wind, {"estimate": 0.0060})
    assert "where it was" in RS._wind(wind, {"estimate": 0.0056})
    mde = {"mde_ratio": 1.0, "intervals_registered": 72, "correction": "Bonferroni",
           "adjusted_p": 0.38, "alpha": 0.05}
    assert "sits exactly on" in RS._mde(mde, None) and "does not survive" in RS._mde(mde, None)
    clear = dict(mde, mde_ratio=1.47, adjusted_p=0.003)
    assert "is above" in RS._mde(clear, None) and " and survives a " in RS._mde(clear, None)
    assert "is below" in RS._mde(dict(mde, mde_ratio=0.46), None)
    grid = {"parameter": "K", "grid_max": 40, "seasons_at_max": 3, "seasons": 25,
            "allowed_to": 120, "alternative": {"estimate": 0.0, "interval": [-0.001, 0.001]},
            "seeds": 10}
    assert "in 3 of 25 seasons" in RS._grid(grid, None) and RS._grid(grid, None).endswith("10 seeds.")


def test_a_builder_asking_for_an_unregistered_sentence_raises():
    with pytest.raises(KeyError):
        RS.qualifier(GM.RECORD_SPREAD_KEY, "vs_close", {"estimate": 0.0, "interval": [0, 0]})


# ------------------------- a-79: a figure that moved from under its sentence: refused

PLANT = 0.0090          # f-31's planted movement of a served estimate
MOVED = "has moved and the sentence no longer describes it"


def _move(blk, e, by):
    """Move a served estimate and REBUILD its sentence beside it - what the builders
    do on a real export once the figure has moved, and the one path a-75's gate
    passed (f-31: 'served moved with qualifier REBUILT -> does NOT raise')."""
    fig = blk[e["figure"]]
    fig["estimate"] = round(fig["estimate"] + by, 4)
    blk[RS.FIELD] = RS.qualifier(e["file"], e["holder"], fig)
    return blk[RS.FIELD]


def test_the_planted_movement_is_the_contradiction_f31_measured(served):
    """What the refusal is for. Rebuilt beside a figure moved +0.0090, the sentence
    still gives wind's worth as 0.0016 against F's frozen no-wind figure - beside a
    figure now 0.0074 from it - and calls it the unfavourable end, where the figure
    F measured sat on the favourable one. If this stops holding, the plant below is
    no longer the case f-31 raised."""
    files = copy.deepcopy(served)
    vc = files[GM.RECORD_TOTAL_KEY]["vs_close"]
    e = RS.required()["game/nfl/record_total.json|vs_close"]
    assert vc["d_brier"]["estimate"] == 0.004
    assert vc[RS.FIELD]["statement"].endswith("the favourable end.")
    q = _move(vc, e, PLANT)
    assert vc["d_brier"]["estimate"] == 0.013 and q["served_matches"] is False
    assert "+0.0056 [+0.0035, +0.0079]" in q["statement"]
    assert "recorded wind moves it by 0.0016" in q["statement"]
    assert q["statement"].endswith("the unfavourable end.")
    assert round(0.013 - 0.0056, 4) == 0.0074           # the size it states is not the gap


@pytest.mark.parametrize("fid", RS.REQUIRED)
def test_a_figure_planted_away_from_its_sentence_is_refused(served, fid):
    files = copy.deepcopy(served)
    assert RS.require(files).startswith("sentence check: ")     # as built, it publishes
    key, blk = _holders(files)[fid][0]
    q = _move(blk, RS.required()[fid], PLANT)
    assert q["served_matches"] is False
    E.validate_contract({key: files[key]})          # legal in the contract: only the gate refuses
    with pytest.raises(RS.MissingSentence, match=MOVED):
        RS.require(files)
    with pytest.raises(RS.MissingSentence, match=MOVED):
        X.gate({key: files[key]})                   # the export's own gate, so no file writes


@pytest.mark.parametrize("fid", RS.REQUIRED)
def test_the_refusal_is_of_a_moved_estimate_and_of_nothing_smaller(served, fid):
    """The gate reads the flag, so it refuses what the flag calls a move: the estimate
    at four decimals. A change below that and a redrawn interval both publish."""
    e = RS.required()[fid]
    files = copy.deepcopy(served)
    key, blk = _holders(files)[fid][0]
    fig = blk[e["figure"]]
    fig["estimate"] += 0.00004
    fig["interval"] = [-0.5, 0.5]
    blk[RS.FIELD] = RS.qualifier(e["file"], e["holder"], fig)
    assert blk[RS.FIELD]["served_matches"] is True
    RS.require({key: files[key]})
    _move(blk, e, 0.0001)                           # the smallest move the file can print
    with pytest.raises(RS.MissingSentence, match=MOVED):
        RS.require({key: files[key]})
    _move(blk, e, -0.0001)                          # and back: it is the value, not the edit
    RS.require({key: files[key]})


def _tree(root):
    return {str(p.relative_to(root)).replace("\\", "/"): p.read_bytes()
            for p in root.rglob("*.json")}


def test_the_export_writes_neither_file_when_the_stage_figure_has_moved(
        measured, monkeypatch, tmp_path):       # noqa: F811
    """Through `publish`, the function the weekly refresh runs, with the real builders
    and the real gate. The stage figure is re-measured on the store every export; a
    movement must write nothing and leave the previous files, whose figure and
    sentence still agree, exactly where they were."""
    monkeypatch.setattr(X, "add_matchups", lambda *a, **k: None)    # reads the stores
    state = {"m": measured}
    monkeypatch.setattr(X, "measure", lambda **_k: state["m"])
    quiet = lambda *_: None                                         # noqa: E731
    first = X.publish(str(tmp_path), now_ts=NOW, log=quiet)
    assert first["built"] == [X.FORECAST_KEY, X.RECORD_KEY] and first["failed"] == []
    assert first["written"] == 2
    before = _tree(tmp_path)
    assert set(before) == {X.FORECAST_KEY, X.RECORD_KEY}

    moved = dict(measured, stages=copy.deepcopy(measured["stages"]))
    moved["stages"]["weeks_1_4"]["elo_nomov"]["diffs"]["dBrier"]["est"] += PLANT
    state["m"] = moved
    files, failed = X.build(NOW + 3600, log=quiet)
    assert files == {}
    assert {f["file"] for f in failed} == {X.RECORD_KEY, X.FORECAST_KEY}
    assert all(MOVED in f["error"] for f in failed)
    second = X.publish(str(tmp_path), now_ts=NOW + 3600, log=quiet)
    assert second["built"] == [] and second["written"] == 0 and len(second["failed"]) == 2
    assert _tree(tmp_path) == before                    # the previous files stand, untouched


def test_a_moved_total_record_takes_its_matchup_copies_down_with_it(
        measured, monkeypatch, results_on_this_walk):       # noqa: F811
    """Through `add_matchups` on the real committed results, c-31's close figure
    planted +0.0090. The total record is refused, and so is every matchup, each of
    which prints that figure with its own digits; the index lists none of them."""
    files, failed = _build(measured, monkeypatch)
    assert failed == [] and GM.RECORD_TOTAL_KEY in files and len(_matchups(files)) == 16
    load = GM.load_result

    def planted(rel):
        d = copy.deepcopy(load(rel))
        if rel == GM.C31_RESULT:
            d["part2"]["S2 book"]["diffs"]["dBrier"]["est"] += PLANT
        return d
    monkeypatch.setattr(GM, "load_result", planted)
    files, failed = _build(measured, monkeypatch)
    assert GM.RECORD_TOTAL_KEY not in files and _matchups(files) == {}
    errors = {f["file"]: f["error"] for f in failed}
    assert MOVED in errors[GM.RECORD_TOTAL_KEY]
    mus = [k for k in errors if k.startswith(GM.MATCHUP_DIR)]
    assert len(mus) == 16 and all(MOVED in errors[k] for k in mus)
    assert files[GM.INDEX_KEY]["games"] == []
    assert GM.RECORD_SPREAD_KEY in files                 # the figure that did not move publishes
