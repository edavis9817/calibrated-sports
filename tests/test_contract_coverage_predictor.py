"""Track A's half of unit a-05, redone as a-12: the `coverage` and `predictor`
kinds, adopted from the proposals as they stood on main on 2026-09-23 - which
carry c-11's `event_dates` and f-07's five predictor fields that a-05 predated.

Track C (A-C7) and track F (F6) proposed the shapes and test their producers.
These tests hold what the CONTRACT owner is answerable for:

- the record rule is in the schema, and is shown firing on both sides - a
  published slice without its scoring record does not validate, and neither
  does an unscored record that will not say why;
- no field the proposals carried was lost in adoption - c-11's and f-07's
  fields are named here, because a-05's first adoption predated them and
  would have dropped them silently;
- the upload's deletion semantics reach the two new keys the same way they
  reach every other prefix, with `None` and `[]` kept apart;
- the contract uses no keyword the site's type generator cannot read, so a
  re-sync on track B's side fails loudly for a named reason, not a surprise;
- nothing schedules either producer yet, so nothing can publish under a
  prefix that has no REFRESHED declaration threaded to the uploader.
"""
import ast
import copy
import json
import os

import pytest
from jsonschema import Draft202012Validator

from jobs import export_web as E
from jobs import export_coverage as X
from analytics import predictor_export as px

from tests.test_export_web import FakeS3, creds  # noqa: F401  (shared fixture)
from tests.test_predictor_export import payload as predictor_payload
from tests.test_export_coverage import _valid_obj as coverage_obj


def _v(kind):
    return E.contract_validators()[kind]


def _errors(kind, obj):
    return list(_v(kind).iter_errors(obj))


# ------------------------------------------------------------------ adoption

def test_both_kinds_are_in_the_contract_and_both_proposals_are_gone():
    x = E.CONTRACT["x-contract"]
    assert x["kinds"]["coverage"] == "CoverageFile"
    assert x["kinds"]["predictor"] == "PredictorFile"
    assert "coverage" in x["sportless_kinds"] and "predictor" not in x["sportless_kinds"]
    for name in ("coverage.defs.json", "F08-predictor.defs.json"):
        assert not os.path.exists(os.path.join(E.ROOT, "docs", "proposals", name)), name


@pytest.mark.parametrize("key,kind", [
    ("coverage.json", "coverage"),
    ("predictors/nfl/drafting.json", "predictor"),
])
def test_the_new_keys_route_to_their_kind(key, kind):
    assert E.kind_for_key(key)[0] == kind


@pytest.mark.parametrize("key", [
    "nfl/coverage.json",                 # coverage is sportless, one file
    "predictors/drafting.json",          # a predictor needs its sport
    "predictors/nfl/sub/drafting.json",
    "analytics/nfl/drafting.json",       # not under another builder's prefix
])
def test_near_miss_keys_route_nowhere(key):
    assert E.kind_for_key(key) == (None, None)


def test_coverage_is_now_validated_against_the_contract_not_a_proposal():
    assert X.validate(coverage_obj()) == "coverage.json valid against the contract"


# ------------------------------------------------------------------ the record rule

def _mixed():
    """A real build with ONE slice withheld. Since f-07 read Sports-Reference's
    terms nothing is withheld in production, so the withheld branch is driven
    through the producer's own path (`SLICES[...]["withheld"]`) rather than
    hand-built - the shape tested is the shape the producer writes."""
    saved = px.SLICES["snaps4"]
    px.SLICES["snaps4"] = dict(saved, withheld=["licence_unresolved"])
    try:
        return predictor_payload()
    finally:
        px.SLICES["snaps4"] = saved


def test_the_real_shape_validates():
    assert _errors("predictor", predictor_payload()) == []
    p = _mixed()
    assert _errors("predictor", p) == []
    statuses = {s["status"] for s in p["slices"].values()}
    assert statuses == {"published", "withheld"}, "the fixture must exercise both branches"


def test_both_values_policies_validate():
    for policy in ("show", "hide"):
        assert _errors("predictor", predictor_payload(policy)) == [], policy


def _published(p):
    return next(k for k, s in p["slices"].items() if s["status"] == "published")


def _withheld(p):
    return next(k for k, s in p["slices"].items() if s["status"] == "withheld")


def test_a_published_slice_with_a_null_record_is_refused():
    """THE rule. The proposal typed `record` null-or-object on every slice, so a
    published predictor with no scoring record was a valid file. It is not now."""
    p = predictor_payload()
    s = _published(p)
    p["slices"][s]["record"] = None
    assert _errors("predictor", p)


def test_the_null_record_rule_is_the_one_that_fired():
    """Show the refusal comes from the published-slice rule and not from something
    incidental: the SAME null record on a withheld slice is valid."""
    p = _mixed()
    w = _withheld(p)
    assert p["slices"][w]["record"] is None
    assert _errors("predictor", p) == []


def test_an_unscored_record_must_say_why():
    p = predictor_payload()
    s = _published(p)
    p["slices"][s]["record"] = {"score": None, "reason": None}
    assert _errors("predictor", p)
    p["slices"][s]["record"] = {"score": None, "reason": "not_as_of"}
    assert _errors("predictor", p) == []


def test_a_scored_record_may_not_also_carry_a_reason():
    p = predictor_payload()
    s = _published(p)
    rec = p["slices"][s]["record"]
    assert rec["score"] is not None and rec["reason"] is None
    rec["reason"] = "too_few_targets"
    assert _errors("predictor", p)


def test_a_withheld_slice_must_name_a_reason_and_carry_no_measurement():
    base = _mixed()
    w, s = _withheld(base), _published(base)
    for field in ("sample", "separation", "chance", "record", "confound",
                  "reading", "statement"):
        p = copy.deepcopy(base)
        p["slices"][w][field] = copy.deepcopy(base["slices"][s][field])
        assert _errors("predictor", p), f"a withheld slice leaked {field}"
    p = copy.deepcopy(base)
    p["slices"][w]["withheld_reasons"] = []
    assert _errors("predictor", p)
    p = copy.deepcopy(base)
    p["slices"][w]["values_reason"] = None
    assert _errors("predictor", p), "a withheld slice must say its values are withheld"


def test_a_published_slice_carries_its_reading_and_statement():
    """f-07's fields are 'null only when withheld'; a-12 puts that in the schema
    beside a-05's record rule rather than leaving it as a description."""
    base = predictor_payload()
    s = _published(base)
    assert base["slices"][s]["reading"] and base["slices"][s]["statement"]
    for field in ("reading", "statement"):
        p = copy.deepcopy(base)
        p["slices"][s][field] = None
        assert _errors("predictor", p), field
    p = copy.deepcopy(base)
    p["slices"][s]["values_reason"] = "withheld"
    assert _errors("predictor", p)


def test_attribution_is_null_or_a_complete_credit():
    """f-07 wrote `attribution` with `oneOf`; adopted as `anyOf` (disjoint
    null-or-object, identical documents). Shown still discriminating."""
    p = predictor_payload()
    assert p["attribution"] and _errors("predictor", p) == []
    q = copy.deepcopy(p)
    q["attribution"] = None
    assert _errors("predictor", q) == []
    q = copy.deepcopy(p)
    del q["attribution"]["statement"]
    assert _errors("predictor", q)
    q = copy.deepcopy(p)
    q["attribution"]["statement"] = ""
    assert _errors("predictor", q)


# ------------------------------------------------------------------ nothing the proposals carried was lost

# The fields a-05 predated. Each was on main in docs/proposals/* when a-12 ran;
# adopting a-05's snapshot would have dropped every one, and the producers that
# emit them would then fail validation against the contract they target.
LATE_FIELDS = {
    "CoverageCount": ["event_dates"],                                   # c-11
    "PredictorFile": ["attribution", "values_policy"],                  # f-07
    "PredictorSlice": ["reading", "statement", "values_reason"],        # f-07
}


@pytest.mark.parametrize("name", sorted(LATE_FIELDS))
def test_the_fields_a05_predated_are_adopted_and_required(name):
    d = E.CONTRACT["$defs"][name]
    for field in LATE_FIELDS[name]:
        assert field in d["properties"], (name, field)
        assert field in d["required"], (name, field)


def test_the_confounded_bands_reason_is_adopted():
    """f-07 widened `bands_reason` with `confounded` (w_av's bands are null for
    that reason). a-05's enum lacked it."""
    enum = E.CONTRACT["$defs"]["PredictorSlice"]["properties"]["bands_reason"]["enum"]
    assert "confounded" in enum


def test_coverage_event_dates_validate_and_never_sit_beside_a_span():
    o = coverage_obj()
    src = o["sports"][0]["stats"]["sources"][0]
    assert "event_dates" in src
    src["span"], src["event_dates"] = None, {"first": "1999-04-05", "last": "2025-11-01"}
    assert _errors("coverage", o) == []
    src["event_dates"] = {"first": "1999-04-05"}
    assert _errors("coverage", o)


def test_a_published_slice_names_no_withheld_reason():
    p = predictor_payload()
    p["slices"][_published(p)]["withheld_reasons"] = ["confounded"]
    assert _errors("predictor", p)


def test_bands_reason_is_present_exactly_when_bands_are_null():
    p = predictor_payload()
    s = _published(p)
    assert p["slices"][s]["bands"] is None and p["slices"][s]["bands_reason"]
    p["slices"][s]["bands_reason"] = None
    assert _errors("predictor", p)


# ------------------------------------------------------------------ coverage: null, never zero

def test_a_sport_with_nothing_is_null_and_valid_and_empty_is_refused():
    o = coverage_obj()
    o["sports"].append({"sport": "nhl", "stats": None, "odds": None, "context": None})
    assert _errors("coverage", o) == []
    o["sports"][-1]["stats"] = {"seasons": [2026], "sources": []}
    assert _errors("coverage", o)


# ------------------------------------------------------------------ upload semantics reach the new keys

NEW_KEYS = ("coverage.json", "predictors/nfl/drafting.json")


def _stale_state(dest):
    """An upload record naming the two new keys, with neither on disk: the shape a
    retired predictor, or a coverage file that stopped being produced, leaves."""
    os.makedirs(dest, exist_ok=True)
    E.write_if_changed(E.local_path(dest, "sports.json"), {"kind": "x"})
    with open(os.path.join(dest, E.STATE_FILE), "w", encoding="utf-8") as f:
        json.dump({k: "0" * 64 for k in NEW_KEYS}, f)


@pytest.mark.parametrize("refreshed,deleted,declared", [
    (None, [], None),                                    # no declaration
    ([], [], []),                                        # declared, owns nothing
    (["nfl/players/", "analytics/"], [], None),          # declared, not these prefixes
    (["predictors/"], ["predictors/nfl/drafting.json"], None),
])
def test_absence_of_a_new_key_deletes_only_under_a_declared_prefix(
        tmp_path, creds, refreshed, deleted, declared):  # noqa: F811
    dest = str(tmp_path / "exp")
    _stale_state(dest)
    s3 = FakeS3(objects={k: b"{}" for k in NEW_KEYS})
    r = E.upload(dest=dest, client=s3, log=lambda *_: None, refreshed=refreshed)
    assert s3.deletes == deleted
    assert r["deleted"] == len(deleted)
    assert r["removed_withheld"] == len(NEW_KEYS) - len(deleted)
    # None and [] both withhold here, and must stay distinguishable in the report.
    if refreshed is None:
        assert r["declared_prefixes"] is None
    else:
        assert r["declared_prefixes"] == refreshed
    if deleted:
        assert "predictors/" not in r["withheld_prefixes"]
    else:
        assert "predictors/" in r["withheld_prefixes"]


# ------------------------------------------------------------------ the site can read it

# Keywords `calibratedsports-web/scripts/generate-schema-types.mjs` handles
# (`known` + `RUNTIME_ONLY`), as of web origin/main 9884f96 (re-read 2026-09-23), PLUS the six
# a-05 files to track B as a one-line addition to RUNTIME_ONLY. Each of the six
# constrains values at runtime and says nothing about a TypeScript type. `oneOf`
# is deliberately NOT here: the proposal used it, the generator throws on it, and
# every use was a disjoint null-or-object that `anyOf` states identically.
GENERATOR_KEYWORDS = {
    "$ref", "const", "enum", "anyOf", "type", "items", "prefixItems", "properties",
    "required", "additionalProperties", "$schema", "$id", "$defs", "x-contract",
    "allOf", "if", "then", "else", "not", "minimum", "maximum", "minItems", "maxItems",
    "pattern", "description", "$comment", "title", "default", "examples",
}
FILED_TO_B = {"uniqueItems", "minLength", "exclusiveMinimum", "exclusiveMaximum",
              "minProperties", "propertyNames"}


def _keywords(schema, out):
    """Every keyword used, walking only positions that hold schemas - a property
    NAME inside `properties` is not a keyword."""
    if isinstance(schema, list):
        for s in schema:
            _keywords(s, out)
        return out
    if not isinstance(schema, dict):
        return out
    out |= set(schema)
    for k in ("properties", "$defs"):
        for v in (schema.get(k) or {}).values():
            _keywords(v, out)
    for k in ("items", "additionalProperties", "if", "then", "else", "not", "propertyNames"):
        if isinstance(schema.get(k), dict):
            _keywords(schema[k], out)
    for k in ("anyOf", "allOf", "oneOf", "prefixItems"):
        if k in schema:
            _keywords(schema[k], out)
    return out


def test_the_contract_uses_no_keyword_the_site_generator_cannot_read():
    used = _keywords({k: v for k, v in E.CONTRACT.items() if k != "x-contract"}, set())
    assert used - GENERATOR_KEYWORDS - FILED_TO_B == set()
    assert "oneOf" not in used


def test_the_keyword_walk_can_fire():
    planted = copy.deepcopy(E.CONTRACT)
    planted["$defs"]["PredictorRecord"]["properties"]["score"] = {"oneOf": [{"type": "null"}]}
    assert "oneOf" in _keywords(planted, set())


SITE_GENERATOR = os.path.join(os.path.dirname(E.ROOT), "calibratedsports-web", "scripts",
                              "generate-schema-types.mjs")


@pytest.mark.skipif(not os.path.exists(SITE_GENERATOR),
                    reason="calibratedsports-web is not checked out beside this repo")
def test_report_whether_the_site_generator_has_taken_the_filed_keywords():
    """Informational, never red: the web repo is track B's and moves on its own
    schedule. Records which of the filed keywords its generator still lacks."""
    src = open(SITE_GENERATOR, encoding="utf-8").read()
    missing = sorted(k for k in FILED_TO_B if '"%s"' % k not in src)
    print("site generator still lacks: %s" % (missing or "nothing"))


# ------------------------------------------------------------------ nothing publishes yet

def test_no_scheduled_job_runs_either_producer():
    """`predictors/` has no REFRESHED declaration threaded to the uploader, and
    `coverage.json` refuses WEB_EXPORT_DIR by design. So the only safe number of
    scheduled runs of either is zero. Wiring one into weekly_refresh must come
    with its declaration passed through `concat_declarations`, and this test must
    be replaced by one asserting that - not deleted."""
    tree = ast.parse(open(os.path.join(E.ROOT, "jobs", "weekly_refresh.py"), encoding="utf-8").read())
    strings = {n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)}
    mods = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.ImportFrom):
            mods |= {"%s.%s" % (n.module, a.name) for a in n.names}
        elif isinstance(n, ast.Import):
            mods |= {a.name for a in n.names}
    for producer in ("analytics.predictor_export", "jobs.export_coverage"):
        assert producer not in strings and producer not in mods, producer
    assert "analytics.export" in strings, "the walk must see the producer it DOES run"
