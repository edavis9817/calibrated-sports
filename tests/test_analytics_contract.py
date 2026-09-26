"""The analytics contract kinds, and the export that has to satisfy them.

THE POINT OF THIS FILE. Track F's structural rule - no analytic published
without an interval and a sample count - lived only in producer code. A
producer suite does not exercise the site's validator, so the rule was one
mistake away from being unenforced at the point it matters. `AnalyticValue`
puts it in the contract, which is what both sides compile from.
"""
import json
import os
import re
import sqlite3

import pytest

from analytics import export, paths

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# THE STORE GUARD, AT THE TOP RATHER THAN ONE LAYER DOWN. Everything else in
# this file is pure - schema documents and fixtures - and runs anywhere. Exactly
# one test reads the live analytics database, and without this it took CI and
# every store-less machine with it: `paths.connect(read_only=True)` raises at
# CONNECT on a machine with no store, and `export.build()` raises SystemExit on
# an empty one, which ends the pytest SESSION rather than failing a test.
#
# The assertion inside that test already said "an export that produces nothing
# cannot be checked", so the empty case was understood - the guard was just
# below the thing that fails. Same pattern as `tests/test_analytics_survey.py`.
HAS_METRICS = False
if os.path.isabs(str(paths.db_path())) and os.path.exists(paths.db_path()):
    try:
        HAS_METRICS = paths.connect(read_only=True).execute(
            "SELECT COUNT(*) FROM f_metrics").fetchone()[0] > 0
    except sqlite3.Error:
        HAS_METRICS = False

needs_metrics = pytest.mark.skipif(
    not HAS_METRICS,
    reason="no published metrics in analytics.db: run the analytics publishers")


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
    # a-37: the method block follows whatever values the case ends up with.
    base.setdefault("methods", export.methods_block(base["values"], base["block"]))
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

    assert kinds("analytics/nfl/index.json") == ["analytics.index"]
    assert kinds("analytics/nfl/usage_stability.within_lag1.target_share.json") \
        == ["analytics.metric"]
    assert kinds("analytics/nfl/pace.plays_per_game.json") == ["analytics.metric"]
    # and the OLD shape is no longer a key at all
    assert kinds("nfl/analytics/index.json") == []


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
        export._validated("analytics/nonsense/x/y.json", {"kind": "analytics.metric"})


def test_the_exporter_refuses_a_payload_whose_kind_contradicts_its_key():
    with pytest.raises(export.ContractError, match="implies kind"):
        export._validated("analytics/nfl/index.json", _metric())


def test_the_exporter_refuses_a_value_without_its_interval():
    bad = _metric()
    bad["values"] = [{"subject": "p", "slice": "", "estimate": 0.2,
                      "n": 9, "rows": 9, "method": "block2000"}]
    with pytest.raises(export.ContractError):
        export._validated("analytics/nfl/a.b.json", bad)


def test_the_exporter_accepts_a_complete_metric():
    assert export._validated("analytics/nfl/a.b.json", _metric())["metric"] == "a.b"


def test_an_unbounded_interval_is_dropped_not_coerced():
    """`Infinity` is not JSON and an unbounded interval means nothing on a
    page. The drop path is exercised here because the live store currently has
    none - a check that has never run is not a check."""
    assert export._finite(1.0, 2.0)
    assert not export._finite(float("-inf"), 2.0)
    assert not export._finite(float("nan"), 2.0)
    assert not export._finite(None, 2.0)


def test_the_exporter_defaults_away_from_the_site_export_dir():
    """It CAN write into `WEB_EXPORT_DIR` now - track A request F4 approved that
    path - but only when asked, and `own` stays the default. The original rule
    was "do not put unreviewed keys where the uploader looks"; what changed is
    that the keys are reviewed and the path has an owner for both halves, not
    that the rule was wrong."""
    import ast
    import inspect

    import config

    # BY AST, NOT BY GREP. The module's docstring names `WEB_EXPORT_DIR` on
    # purpose, to say why it is not used - and a text search cannot tell an
    # explanation from a live reference. CLAUDE.md records the same trap from
    # the other direction: a docstring explaining a removal quotes the old name
    # by design, so assert on the references.
    d = export.export_dir()
    assert d == config.storage_path("analytics_export")
    if config.WEB_EXPORT_DIR:
        assert os.path.abspath(d) != os.path.abspath(config.WEB_EXPORT_DIR)

    # `--dest web` is opt-in: the default path must not resolve to track A's
    # tree, checked by AST so a docstring mentioning the name cannot satisfy it.
    tree = ast.parse(inspect.getsource(export.main))
    defaults = [kw.value.value for node in ast.walk(tree)
                if isinstance(node, ast.Call)
                for kw in node.keywords
                if kw.arg == "default" and isinstance(kw.value, ast.Constant)]
    assert "own" in defaults, defaults


# =============================================================================
# a builder owns exactly the prefix it fills (track A's incident, 2026-09-18)
# =============================================================================

def test_the_prefix_this_builder_owns_is_the_top_level_analytics_prefix():
    """Store-free: the prefix is a property of the module, not of the data."""
    assert export.OWNED_PREFIX == "analytics/"
    assert export.PREFIX.startswith(export.OWNED_PREFIX)


@needs_metrics
def test_every_key_this_builder_produces_is_under_the_prefix_it_owns():
    """`sync_keys`' contract is "this builder owns this prefix". A key inside a
    prefix someone ELSE owns is deleted on their next run - which is how twelve
    market keys were lost under `research/`."""
    con = paths.connect(read_only=True)
    out, _dropped = export.build(con)
    assert out, "an export that produces nothing cannot be checked"
    assert all(k.startswith(export.OWNED_PREFIX) for k in out)


def test_sync_deletes_a_stale_key_under_its_own_prefix(tmp_path):
    root = str(tmp_path)
    stale = tmp_path / "analytics" / "nfl" / "gone.metric.json"
    stale.parent.mkdir(parents=True)
    stale.write_text("{}", encoding="utf-8")
    written, deleted, _ = export.sync({"analytics/nfl/a.b.json": _metric()},
                                      root=root)
    assert (written, deleted) == (1, 1)
    assert not stale.exists()
    assert (tmp_path / "analytics" / "nfl" / "a.b.json").exists()


def test_sync_CANNOT_REACH_A_KEY_OUTSIDE_ITS_PREFIX(tmp_path):
    """The whole point. A file under another builder's prefix must survive,
    however stale it looks from here."""
    root = str(tmp_path)
    others = ("research/calibration.json", "nfl/market/x.json",
              "nfl/players/index.json")
    for other in others:
        f = tmp_path.joinpath(*other.split("/"))
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("{}", encoding="utf-8")
    export.sync({"analytics/nfl/a.b.json": _metric()}, root=root)
    for other in others:
        assert tmp_path.joinpath(*other.split("/")).exists(), other


def test_sync_refuses_to_write_a_key_outside_its_prefix(tmp_path):
    with pytest.raises(AssertionError, match="outside the prefix"):
        export.sync({"research/analytics/a.b.json": _metric()},
                    root=str(tmp_path))


def test_the_analytics_prefix_is_owned_by_no_other_builder():
    """Read the producer's own call sites rather than trusting a comment: no
    existing `sync_keys` prefix may contain or be contained by `analytics/`."""
    import ast
    import inspect

    from jobs import export_web
    tree = ast.parse(inspect.getsource(export_web))
    owned = []
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call)
                and getattr(node.func, "id", "") == "sync_keys"
                and len(node.args) >= 3 and isinstance(node.args[2], ast.List)):
            for elt in node.args[2].elts:
                if isinstance(elt, ast.Constant):
                    owned.append(elt.value)
                elif isinstance(elt, ast.JoinedStr):       # f"{SPORT}/market/"
                    owned.append("".join(
                        v.value if isinstance(v, ast.Constant) else "*"
                        for v in elt.values))
    assert owned, "found no sync_keys prefixes - the check read nothing"
    for prefix in owned:
        assert not prefix.startswith(export.OWNED_PREFIX), prefix
        assert not export.OWNED_PREFIX.startswith(prefix), prefix


# =============================================================================
# the REFRESHED sentinel (track A request F4, approved 2026-09-18)
# =============================================================================

def test_the_sentinel_is_the_one_track_A_parses():
    """Not a string this module invented. Both halves of the sentinel live in
    `jobs/export_web.py` so they cannot drift; a third copy here would be the
    drift surface that arrangement exists to close."""
    from jobs.export_web import REFRESHED_SENTINEL, parse_refreshed
    line = export.refreshed_sentinel()
    assert line.startswith(REFRESHED_SENTINEL)
    stdout = "\n".join(["noise", line, ""])
    assert parse_refreshed(stdout) == [export.OWNED_PREFIX]


def test_the_sentinel_declares_exactly_the_prefix_this_builder_owns():
    """Declaring a prefix is authorising deletion under it. Declaring more than
    it fills is the `sync_keys` incident with extra steps."""
    from jobs.export_web import parse_refreshed
    declared = parse_refreshed(export.refreshed_sentinel())
    assert declared == ["analytics/"]
    assert declared == [export.OWNED_PREFIX]


def test_the_sentinel_is_printed_only_on_a_write_to_the_published_tree():
    """A --check must not authorise deletions: nothing was rebuilt."""
    import ast
    import inspect
    tree = ast.parse(inspect.getsource(export.main))
    calls = [n for n in ast.walk(tree)
             if isinstance(n, ast.Call)
             and getattr(n.func, "id", "") == "print"
             and any(isinstance(arg, ast.Call)
                     and getattr(arg.func, "id", "") == "refreshed_sentinel"
                     for arg in n.args)]
    assert len(calls) == 1, "the sentinel should be printed from exactly one place"
    src = inspect.getsource(export.main)
    assert 'a.dest == "web"' in src
