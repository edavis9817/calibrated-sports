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
    """Shaped like export_web.build_research's research/calibration.json, with the
    values a-36 measured on 2026-09-26 (n=935, 14 games)."""
    return {"kind": "research.calibration", "n": 935, "games": 14,
            "series": [{"name": "model", "ece": 0.0666, "bins": []},
                       {"name": "market", "ece": 0.0519, "bins": []}],
            "ece": {"model": 0.0666, "market": 0.0519},
            "brier": {"model": 0.1847, "market": 0.17, "naive": 0.1835,
                      "model_minus_market": {"estimate": 0.0147, "interval": [0.0009, 0.0268]},
                      "model_minus_naive": {"estimate": 0.0012, "interval": [-0.0098, 0.0136]}}}


@pytest.fixture
def files():
    with open(os.path.join(ROOT, "docs", "hypotheses.json"), encoding="utf-8") as f:
        hyp = json.load(f)
    return {M.MARKET: E.build_market_calibration("2026-09-26T00:00:00Z"),
            M.REGISTER: {"kind": "research.hypotheses", "hypotheses": hyp["hypotheses"]},
            M.SCORE: _calibration_fixture()}


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
    # and R10 is the one declared disagreement, reported rather than hidden
    assert rep.declared and all("R10" in d for d in rep.declared)


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
    r10 = next(h for h in files[M.REGISTER]["hypotheses"] if h["id"] == "R10")
    r10["n"] = files[M.SCORE]["n"]
    rep = M.check(files)
    assert not rep.clean and "stale declaration" in rep.statement


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
