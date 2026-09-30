"""One number per metric (a-36, audit N-02): the registry and the gate that
refuses an export when two served files disagree on a registered metric.

Runs without a store. The market file is built from the COMMITTED
research/results/market_calibration.json and the register from the committed
docs/hypotheses.json - the two real files the gate joins. research/calibration.json
needs model predictions no CI store holds, so it is a fixture shaped like the
real one; the full real-data gate is `python -m jobs.export_web --dry-run`.
"""
import copy
import json
import os

import pytest

from jobs import export_web as E
from jobs import metric_registry as M

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _calibration_fixture():
    """research/calibration.json as export_web.build_research builds it, with
    research.score.load replaced by rows in the week-1 state a-58 measured: every
    prediction priced only by a day-old Kalshi candle, so the market comparison
    is withdrawn and R10 rides along as history."""
    from research import score as SC
    from tests.test_score import week1_rows
    orig = SC.load
    SC.load = lambda *a, **k: (week1_rows(),)
    try:
        return E.build_research("2026-09-29T00:00:00Z")[M.SCORE]
    finally:
        SC.load = orig


@pytest.fixture
def files():
    with open(os.path.join(ROOT, "docs", "hypotheses.json"), encoding="utf-8") as f:
        hyp = json.load(f)
    return {M.MARKET: E.build_market_calibration("2026-09-26T00:00:00Z"),
            M.REGISTER: {"kind": "research.hypotheses", "hypotheses": hyp["hypotheses"]},
            M.SCORE: _calibration_fixture(),
            M.MANIFEST: _manifest_fixture(),
            **_season_fixture(),
            **analytics_fixture()}


def analytics_fixture(season_from=2006, season_to=2025):
    """The opportunity_residual metric files and the analytics index entry for
    each, shaped as analytics.export.build writes them (a-51). The real-data gate
    is `jobs.publish_preflight`, which runs analytics.export.gate on the tree it
    builds from the store."""
    out, entries = {}, []
    for mid in M.residual_metric_ids():
        basis = "weekly_stats" if mid.endswith("_tds") else "pbp"
        key = M.analytics_file(mid)
        out[key] = {"kind": "analytics.metric", "sport": "nfl", "metric": mid,
                    "basis": basis, "season_from": season_from, "season_to": season_to,
                    "requires": ["weekly_stats.position"], "values": []}
        entries.append({"metric": mid, "key": key, "season_from": season_from,
                        "season_to": season_to})
    out[M.ANALYTICS_INDEX] = {"kind": "analytics.index", "sport": "nfl", "metrics": entries}
    return out


def _season_fixture():
    """The division file built by the real `season_export.build` over the
    season tests' synthetic record (a-42), and the projection beside it built by
    `season_export.build_projection` over the projection tests' league (a-55)."""
    from jobs import season_export as X
    from tests.test_season_projection import both
    f = both()
    X.validated(f[X.KEY])
    X.validated_projection(f[X.PROJECTION_KEY])
    return {M.SEASON: f[X.KEY], M.PROJECTION: f[X.PROJECTION_KEY]}


def _manifest_fixture():
    """The two manifest blocks the a-46 metrics join: `denominators` from the real
    computation over a one-game slate, and the legacy `counts` beside it."""
    from jobs import denominators as D
    cfg = D.Participation("usage", "a target", ("targets",), ("REG",), "pid", "wk", "team")
    rows = [{"pid": "p", "season": 2026, "wk": 1, "team": "A", "season_type": "REG", "targets": 3}]
    games = [{"season": 2026, "period": 1, "final": True, "kickoff_ts": 1.0, "teams": ("A", "B")},
             {"season": 2026, "period": 2, "final": False, "kickoff_ts": 9.0, "teams": ("A", "B")}]
    cur = {"season": 2026, "period": {"index": 2, "label": "Week 2", "key": "2026-2"}}
    d = D.compute(cfg, rows, games, cur, {"p"}, {"p"}, 5.0)
    return {"counts": {"players": 1, "teams": 2, "market": 1, "games": 1, "rungs": 0},
            "denominators": d}


# ------------------------------------------------------------------ the registry

def test_ids_are_unique_and_every_path_parses():
    ids = [m["id"] for m in M.METRICS]
    assert len(ids) == len(set(ids))
    for m in M.METRICS:
        for loc in [m["source"], *m["copies"]]:
            assert M.parse(loc["path"]), loc
            assert loc["file"] in M.FILES


def test_the_brief_minimum_is_registered():
    """The over-bias, ECE, Brier vs market, Brier vs naive and the walk-forward."""
    ids = {m["id"] for m in M.METRICS}
    for need in ("market.over_bias.estimate_pp", "market.over_bias.interval_pp",
                 "market.over_bias.n", "market.close.ece", "model.ece", "kalshi.ece",
                 "model.brier_minus_market", "model.brier_minus_naive",
                 "model.walkforward.brier_minus_close.2025"):
        assert need in ids, need


def test_the_headline_has_the_register_as_a_checked_copy():
    """R18 must be a COPY, not a second owner - otherwise nothing compares them."""
    m = next(m for m in M.METRICS if m["id"] == "market.over_bias.estimate_pp")
    assert m["source"] == {"file": M.MARKET, "path": "over_bias.estimate_pp"}
    assert {"file": M.REGISTER, "path": "hypotheses[id=R18].estimate"} in m["copies"]


def test_the_manifest_carries_the_registry_and_it_validates():
    block = M.manifest_block()
    assert block == M.METRICS and block is not M.METRICS
    schema = E.CONTRACT["$defs"]["SportManifest"]["properties"]["metrics"]
    assert schema["items"] == {"$ref": "#/$defs/MetricEntry"}


# ------------------------------------------------------------------ the path grammar

def test_resolve_key_index_and_select():
    doc = {"a": {"b": [10, 20]}, "rows": [{"id": "R1", "v": 1}, {"id": "R2", "v": 2}]}
    assert M.resolve(doc, "a.b[1]") == 20
    assert M.resolve(doc, "rows[id=R2].v") == 2


def test_a_selector_must_match_exactly_one():
    doc = {"rows": [{"id": "R1"}, {"id": "R1"}]}
    with pytest.raises(M.Unresolved, match="matched 2"):
        M.resolve(doc, "rows[id=R1]")
    with pytest.raises(M.Unresolved, match="matched 0"):
        M.resolve(doc, "rows[id=R9]")
    with pytest.raises(M.Unresolved):
        M.resolve(doc, "missing.key")


# ------------------------------------------------------------------ the gate

def test_the_committed_files_agree(files):
    rep = M.check(files)
    assert rep.clean, rep.statement
    # it resolved every location, not a subset it understood
    assert rep.checked == sum(1 + len(m["copies"]) for m in M.METRICS)
    # a-58 retired the last declared disagreement (R10 against the candle-priced
    # n=935): the file now carries R10 as a checked copy, so none remain
    assert rep.declared == []


def test_the_published_file_matches_the_register_figure(files):
    """44,198 outcomes over 814 games, -2.43 [-3.13, -1.72] - R18 as restated 09-24."""
    ob, pop = files[M.MARKET]["over_bias"], files[M.MARKET]["population"]
    assert (pop["n"], pop["games"]) == (44198, 814)
    assert (ob["estimate_pp"], ob["interval_pp"]) == (-2.43, [-3.13, -1.72])
    assert sum(b["n"] for b in files[M.MARKET]["buckets"]) == pop["n"]


def test_skewing_the_owner_fails_and_restoring_it_passes(files):
    """THE DISCRIMINATION TEST: the same gate, both answers."""
    ob = files[M.MARKET]["over_bias"]
    good = ob["estimate_pp"]
    ob["estimate_pp"] = round(good + 0.01, 2)
    rep = M.check(files)
    assert not rep.clean
    assert "market.over_bias.estimate_pp" in rep.statement and "R18" in rep.statement
    with pytest.raises(M.MetricDisagreement):
        M.require(files)
    ob["estimate_pp"] = good
    assert M.check(files).clean


def test_skewing_a_copy_fails(files):
    r18 = next(h for h in files[M.REGISTER]["hypotheses"] if h["id"] == "R18")
    r18["n"] = 43209                       # the pre-fix figure the site still printed
    rep = M.check(files)
    assert not rep.clean and "market.over_bias.n" in rep.statement


def test_disagreement_inside_one_file_fails(files):
    files[M.SCORE]["series"][0]["ece"] = 0.0714     # the DECISIONS.md figure Method cited
    rep = M.check(files)
    assert not rep.clean and "model.ece" in rep.statement


def test_a_declared_copy_that_agrees_is_a_stale_declaration(files):
    # No production metric carries a declaration since a-58, so the mechanism is
    # exercised on a declared copy built for the purpose - both answers.
    decl = M._m("t.n", "t", "predictions", 0, (M.SCORE, "market_comparison.registered.n"),
                [(M.REGISTER, "hypotheses[id=R10].n", "known to differ")])
    rep = M.check(files, [decl])
    assert not rep.clean and "stale declaration" in rep.statement
    r10 = next(h for h in files[M.REGISTER]["hypotheses"] if h["id"] == "R10")
    r10["n"] = 935
    rep = M.check(files, [decl])
    assert rep.clean and len(rep.declared) == 1


def test_a_missing_file_is_a_failure_not_a_skip(files):
    del files[M.SCORE]
    rep = M.check(files)
    assert not rep.clean and "was not produced" in rep.statement


def test_the_report_refuses_truth_testing(files):
    with pytest.raises(TypeError):
        bool(M.check(files))


def test_rounding_is_at_the_published_precision(files):
    """-2.434 and -2.43 are one figure at 2dp; -2.44 is not."""
    ob = files[M.MARKET]["over_bias"]
    ob["estimate_pp"] = -2.4312
    assert M.check(files).clean
    ob["estimate_pp"] = -2.436
    assert not M.check(files).clean


# ------------------------------------------------------------------ the export

def test_export_refuses_before_writing_anything(files, tmp_path, monkeypatch):
    """The gate runs before the first write: a skewed research build leaves the
    destination empty rather than half-published."""
    bad = copy.deepcopy(files)
    bad[M.MARKET]["over_bias"]["estimate_pp"] = -1.40
    monkeypatch.setattr(E, "build_research", lambda generated_at: bad)
    dest = tmp_path / "dest"
    dest.mkdir()
    with pytest.raises(M.MetricDisagreement, match="market.over_bias.estimate_pp"):
        E.export(only=["research"], dest=str(dest), log=lambda *a: None)
    assert list(dest.iterdir()) == []


def test_the_market_file_validates_against_the_contract(files):
    E.validate_contract({M.MARKET: files[M.MARKET]})


def test_the_withdrawn_calibration_file_validates_and_carries_r10_as_history(files):
    """a-58. The file keeps the model-only reliability, withdraws every market
    figure, and carries the register's R10 figure - which must AGREE with R10,
    since it is a copy, and must say it cannot be re-derived."""
    cal = files[M.SCORE]
    E.validate_contract({M.SCORE: cal})
    assert [s["name"] for s in cal["series"]] == ["model"]
    assert cal["brier"]["market"] is None and cal["brier"]["model_minus_market"] is None
    assert cal["ece"]["market"] is None
    mc = cal["market_comparison"]
    assert mc["status"] == "withdrawn" and mc["scorable"] == 0
    assert mc["registered"]["re_derivable"] is False
    r10 = next(h for h in files[M.REGISTER]["hypotheses"] if h["id"] == "R10")
    assert (mc["registered"]["estimate"], mc["registered"]["n"]) == (r10["estimate"], r10["n"])
    rep = M.check(files)
    assert rep.clean, rep.statement
    assert not [d for d in rep.declared if "brier_minus_market" in d]


def test_a_registered_copy_that_drifts_from_r10_fails_the_gate(files):
    """The copy is checked, not decorative: edit it and the gate refuses."""
    files[M.SCORE]["market_comparison"]["registered"]["estimate"] = 0.0147
    rep = M.check(files)
    assert not rep.clean and "model.brier_minus_market" in rep.statement


# ------------------------------------------------------------------ the season gate (a-42)

def test_every_season_metric_is_in_the_season_gate_and_nowhere_else():
    ids = {m["id"] for m in M.SEASON_METRICS}
    assert ids and all(i.startswith(("season.division.", "season.projection.")) for i in ids)
    assert not ids & {m["id"] for m in M.MANIFEST_METRICS + M.RESEARCH_METRICS}
    assert ids <= {m["id"] for m in M.manifest_block()}, "the manifest must carry them"


def test_the_season_gate_resolves_its_file_and_refuses_a_missing_one(files):
    rep = M.check({M.SEASON: files[M.SEASON], M.PROJECTION: files[M.PROJECTION]},
                  M.SEASON_METRICS)
    assert rep.clean, rep.statement
    assert rep.checked == len(M.SEASON_METRICS) + sum(len(m["copies"]) for m in M.SEASON_METRICS)
    rep = M.check({}, M.SEASON_METRICS)
    assert not rep.clean and "was not produced" in rep.statement


def test_the_low_tail_metric_reads_the_lowest_bin(files):
    """The figure a-41 asked the page to print beside the bars."""
    cal = files[M.SEASON]["record"]["calibration"][0]
    assert cal["bin"][0] == 0.0
    got = {m["id"]: M.resolve(files[M.SEASON], m["source"]["path"]) for m in M.SEASON_METRICS
           if m["source"]["file"] == M.SEASON}
    assert got["season.division.calibration.low_tail.forecast"] == cal["forecast"]
    assert got["season.division.calibration.low_tail.realised"] == cal["realised"]
