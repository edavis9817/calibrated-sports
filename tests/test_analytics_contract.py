"""The analytics contract kinds, and the export that has to satisfy them.

THE POINT OF THIS FILE. Track F's structural rule - no analytic published
without an interval and a sample count - lived only in producer code. A
producer suite does not exercise the site's validator, so the rule was one
mistake away from being unenforced at the point it matters. `AnalyticValue`
puts it in the contract, which is what both sides compile from.
"""
import copy
import json
import os
import re

import pytest

from analytics import export

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture(scope="module")
def contract():
    with open(os.path.join(ROOT, export.CONTRACT_PATH), encoding="utf-8") as f:
        return json.load(f)


def _value(**kw):
    base = {"subject": "00-0036355", "slice": "", "estimate": 0.24,
            "interval": [0.19, 0.30], "n": 17, "rows": 17, "method": "block2000"}
    base.update(kw)
    return base


def _metric(**kw):
    base = {"schema_version": 2, "generated_at": "2026-09-18T00:00:00Z",
            "kind": "analytics.metric", "sport": "nfl", "metric": "a.b",
            "label": "L", "unit": "u", "subject_type": "player",
            "block": "game", "basis": "pbp", "slice_kind": "",
            "shares_denominator": "team", "season_from": 2009,
            "season_to": 2026, "range_note": "why", "availability": "current",
            "requires": ["pbp.receiver_player_id|incomplete_pass"],
            "values": [_value()]}
    base.update(kw)
    return base


def _errors(contract, name, payload):
    from jsonschema import Draft202012Validator
    v = Draft202012Validator({"$ref": "#/$defs/%s" % name,
                              "$defs": contract["$defs"]})
    return list(v.iter_errors(payload))


# =============================================================================
# the structural rule, now enforced consumer-side
# =============================================================================

def test_a_value_without_an_interval_is_refused(contract):
    bad = _value()
    del bad["interval"]
    assert _errors(contract, "AnalyticValue", bad)


def test_a_value_whose_interval_is_null_is_refused(contract):
    """Nullable would let the rule be satisfied by writing nothing."""
    assert _errors(contract, "AnalyticValue", _value(interval=None))


def test_a_value_without_a_sample_count_is_refused(contract):
    bad = _value()
    del bad["n"]
    assert _errors(contract, "AnalyticValue", bad)


def test_a_sample_count_of_zero_is_refused(contract):
    """Nothing measured is not a value."""
    assert _errors(contract, "AnalyticValue", _value(n=0))


def test_a_bare_extra_field_is_refused(contract):
    """Objects are closed on purpose: an additive field must fail until the
    contract is updated in the same commit, which regenerates the site types."""
    assert _errors(contract, "AnalyticValue", _value(confidence=0.9))


def test_a_one_element_interval_is_refused(contract):
    assert _errors(contract, "AnalyticValue", _value(interval=[0.2]))


def test_a_complete_value_passes(contract):
    assert _errors(contract, "AnalyticValue", _value()) == []


def test_a_null_estimate_with_a_real_interval_passes(contract):
    """The estimate may be unknown; the interval and the sample may not."""
    assert _errors(contract, "AnalyticValue", _value(estimate=None)) == []


# =============================================================================
# the usable range travels with the metric
# =============================================================================

@pytest.mark.parametrize("field", ["season_from", "season_to", "range_note",
                                   "availability", "shares_denominator",
                                   "slice_kind", "unit", "basis", "block"])
def test_a_metric_missing_any_self_description_is_refused(contract, field):
    bad = _metric()
    del bad[field]
    assert _errors(contract, "AnalyticMetricFile", bad), field


def test_availability_is_an_enum_not_free_text(contract):
    assert _errors(contract, "AnalyticMetricFile", _metric(availability="maybe"))
    assert _errors(contract, "AnalyticMetricFile", _metric(availability="current")) == []
    assert _errors(contract, "AnalyticMetricFile",
                   _metric(availability="historical")) == []


def test_shared_denominator_is_an_enum_and_null_is_allowed(contract):
    for ok in ("team", "league", "own", None):
        assert _errors(contract, "AnalyticMetricFile",
                       _metric(shares_denominator=ok)) == [], ok
    assert _errors(contract, "AnalyticMetricFile", _metric(shares_denominator="teem"))


def test_a_complete_metric_passes(contract):
    assert _errors(contract, "AnalyticMetricFile", _metric()) == []


# =============================================================================
# keys resolve to exactly one kind
# =============================================================================

def test_the_index_key_and_a_metric_key_do_not_collide(contract):
    """The metric pattern requires an interior dot, so it cannot also match
    index.json whatever order the patterns are scanned in."""
    pats = [(re.compile(e["pattern"]), e["kind"])
            for e in contract["x-contract"]["keys"]]

    def kinds(key):
        return [k for rx, k in pats if rx.match(key)]

    assert kinds("nfl/analytics/index.json") == ["analytics.index"]
    assert kinds("nfl/analytics/usage_stability.within_lag1.target_share.json") \
        == ["analytics.metric"]
    assert kinds("nfl/analytics/pace.plays_per_game.json") == ["analytics.metric"]


def test_every_analytics_kind_has_a_def_and_a_key_pattern(contract):
    x = contract["x-contract"]
    pattern_kinds = {e["kind"] for e in x["keys"]}
    for kind, name in x["kinds"].items():
        assert name in contract["$defs"], kind
        assert kind in pattern_kinds, kind
    assert {"analytics.index", "analytics.metric"} <= set(x["kinds"])


def test_the_analytics_kinds_are_not_sportless(contract):
    """They carry a sport and their keys are sport-prefixed; listing them as
    sportless would make the producer resolve no sport for them."""
    assert not ({"analytics.index", "analytics.metric"}
                & set(contract["x-contract"]["sportless_kinds"]))


def test_the_contract_is_valid_json_schema(contract):
    from jsonschema import Draft202012Validator
    Draft202012Validator.check_schema(contract)


# =============================================================================
# the exporter's choke point
# =============================================================================

def test_the_exporter_refuses_a_key_matching_no_pattern():
    with pytest.raises(export.ContractError, match="matches no pattern"):
        export._validated("nfl/nonsense/x.json", {"kind": "analytics.metric"})


def test_the_exporter_refuses_a_payload_whose_kind_contradicts_its_key():
    with pytest.raises(export.ContractError, match="implies kind"):
        export._validated("nfl/analytics/index.json", _metric())


def test_the_exporter_refuses_a_value_without_its_interval():
    bad = _metric()
    bad["values"] = [{"subject": "p", "slice": "", "estimate": 0.2,
                      "n": 9, "rows": 9, "method": "block2000"}]
    with pytest.raises(export.ContractError):
        export._validated("nfl/analytics/a.b.json", bad)


def test_the_exporter_accepts_a_complete_metric():
    assert export._validated("nfl/analytics/a.b.json", _metric())["metric"] == "a.b"


def test_an_unbounded_interval_is_dropped_not_coerced():
    """`Infinity` is not JSON and an unbounded interval means nothing on a
    page. The drop path is exercised here because the live store currently has
    none - a check that has never run is not a check."""
    assert export._finite(1.0, 2.0)
    assert not export._finite(float("-inf"), 2.0)
    assert not export._finite(float("nan"), 2.0)
    assert not export._finite(None, 2.0)


def test_the_exporter_writes_nowhere_near_the_site_export_dir():
    """`WEB_EXPORT_DIR` is track A's and its uploader reads it. Track F writing
    there would put unreviewed keys where the site looks."""
    import ast
    import inspect

    import config

    # BY AST, NOT BY GREP. The module's docstring names `WEB_EXPORT_DIR` on
    # purpose, to say why it is not used - and a text search cannot tell an
    # explanation from a live reference. CLAUDE.md records the same trap from
    # the other direction: a docstring explaining a removal quotes the old name
    # by design, so assert on the references.
    tree = ast.parse(inspect.getsource(export))
    referenced = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    referenced |= {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    assert "WEB_EXPORT_DIR" not in referenced
    assert not {r for r in referenced if "export_web" in r}

    d = export.export_dir()
    assert d == config.storage_path("analytics_export")
    if config.WEB_EXPORT_DIR:
        assert os.path.abspath(d) != os.path.abspath(config.WEB_EXPORT_DIR)
