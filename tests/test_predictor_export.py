"""The drafting predictor's published shape (f-02).

Every verdict is driven to each of its answers, and every refusal is shown
firing on a payload built to trip it - a shape check that has only ever been
seen to pass is asserting nothing. No test here reads the nflverse mirror or
writes outside `tmp_path`.
"""
import ast
import copy
import os
import re

import pytest

from analytics import drafting
from analytics import export as analytics_export
from analytics import predictor_export as px

TEAMS = sorted(drafting.FRANCHISES)


def _outcome(p=0.68, wf=(-0.056, -0.116, 0.021, 15), classes=(2002, 2022),
             bands=None, shift=0.0):
    teams = [{"team": t, "est": (i - 16) / 1000 + shift, "lo": (i - 16) / 1000 - 0.03,
              "hi": (i - 16) / 1000 + 0.03, "n": classes[1] - classes[0] + 1,
              "picks": 160 + i, "excludes_null": False}
             for i, t in enumerate(TEAMS)]
    walk = ({"note": "career AV is a snapshot"} if wf is None else
            {"r": wf[0], "lo": wf[1], "hi": wf[2], "targets": wf[3]})
    return {
        "classes": list(classes),
        "separation": {"between_sd": 0.02, "null_sd_mean": 0.021,
                       "null_sd_p95": 0.026, "p": p, "classes": 21, "picks": 5371},
        "null_exclusions": {"mean": 2.3, "p95": 5.0, "perms": 200},
        "environment": {"r": 0.15, "lo": -0.2, "hi": 0.47, "teams": 32},
        "walk_forward": walk,
        "teams_excluding_null": 1, "expected_by_chance": 1.6,
        "teams": teams,
        "bands": bands or [TEAMS[:20], TEAMS[20:]],
    }


def result(**over):
    r = {"pulled": "2026-09-09", "picks": 5371, "no_id_picks": 219,
         "outcomes": {"roster4": _outcome(), "bust": _outcome(p=0.16),
                      "snaps4": _outcome(classes=(2013, 2022)),
                      "w_av": _outcome(p=0.003, wf=None, classes=(2002, 2021))}}
    r["outcomes"].update(over)
    return r


def payload(**over):
    return px.build(result(**over), generated_at="2026-09-22T05:00:00Z")


# ---------------------------------------------------------------------------
# the real shape passes
# ---------------------------------------------------------------------------

def test_a_complete_payload_validates_against_contract_plus_proposal():
    p = px.validated(px.KEY, payload())
    assert len(p["values"]) == 64
    assert {v["slice"] for v in p["values"]} == {"roster4", "bust"}


def test_every_slice_the_predictor_defines_is_on_the_envelope():
    p = payload()
    assert list(p["slices"]) == list(px.SLICES)


def test_a_withheld_slice_is_null_with_reasons_not_absent():
    p = payload()
    for name in ("snaps4", "w_av"):
        s = p["slices"][name]
        assert s["status"] == "withheld" and s["withheld_reasons"]
        for k in ("sample", "separation", "chance", "record", "confound", "bands"):
            assert s[k] is None, (name, k)
        assert s["bands_reason"] == "withheld"
        assert not [v for v in p["values"] if v["slice"] == name]
    assert p["slices"]["w_av"]["withheld_reasons"] == ["licence_unresolved", "confounded"]


def test_a_missing_measurement_is_refused_not_dropped():
    r = result()
    del r["outcomes"]["w_av"]
    with pytest.raises(px.ContractError, match="w_av"):
        px.build(r)


def test_nothing_in_the_file_numbers_a_row_or_a_band():
    banned = re.compile(r"(^|_)(rank|ranking|position|place|index|order|band_no)(_|$)")

    def keys(o):
        if isinstance(o, dict):
            for k, v in o.items():
                yield k
                yield from keys(v)
        elif isinstance(o, list):
            for v in o:
                yield from keys(v)
    sep = copy.deepcopy(result())
    sep["outcomes"]["roster4"]["separation"]["p"] = 0.001
    for p in (payload(), px.build(sep)):
        assert not [k for k in keys(p) if banned.search(k)]


# ---------------------------------------------------------------------------
# verdicts reach every answer
# ---------------------------------------------------------------------------

def test_separation_verdict_takes_both_values():
    assert px.separation_verdict(0.049) == "separates"
    assert px.separation_verdict(0.05) == "does_not_separate"


@pytest.mark.parametrize("lo,hi,n,want", [
    (0.01, 0.2, 15, "forecasts"),
    (-0.2, -0.01, 15, "forecasts_inversely"),
    (-0.1, 0.1, 15, "no_better_than_chance"),
    (0.02, 0.25, 4, "not_readable"),       # snaps4's shape: excludes 0 on 4 targets
])
def test_record_verdict_takes_every_value(lo, hi, n, want):
    assert px.record_verdict(lo, hi, n) == want


def test_the_record_is_null_with_a_reason_when_never_scored():
    rec = px._record({"note": "career AV is a snapshot"})
    assert rec == {"score": None, "reason": "not_as_of"}
    assert px._record({"targets": 0, "r": None})["reason"] == "too_few_targets"


def test_a_published_unscored_slice_still_validates():
    r = result(roster4=_outcome(wf=None))
    p = px.validated(px.KEY, px.build(r))
    assert p["slices"]["roster4"]["record"] == {"score": None, "reason": "not_as_of"}


def test_bands_are_null_unless_separation_rejects():
    p = payload()
    for name in ("roster4", "bust"):
        assert p["slices"][name]["bands"] is None
        assert p["slices"][name]["bands_reason"] == "separation_not_rejected"


def test_bands_when_separation_rejects_are_led_by_the_best_and_alphabetical():
    r = result()
    for name in ("roster4", "bust"):
        r["outcomes"][name]["separation"]["p"] = 0.001
        r["outcomes"][name]["bands"] = [list(reversed(TEAMS[:20])), TEAMS[20:]]
    p = px.validated(px.KEY, px.build(r))
    hi, lo = p["slices"]["roster4"]["bands"], p["slices"]["bust"]["bands"]
    assert all(b["members"] == sorted(b["members"]) for b in hi + lo)
    # est rises with the alphabet in the fixture: best-higher is the last
    # member, best-lower is the first.
    assert hi[0]["leader"] == TEAMS[19]
    assert lo[0]["leader"] == TEAMS[0]
    assert p["slices"]["roster4"]["bands_reason"] is None


def test_drafting_bands_lead_with_the_fewest_busts_when_lower_is_better():
    import numpy as np
    import polars as pl
    rng = np.random.default_rng(3)
    rows = [{"season": c, "pick": k + 1, "franchise": TEAMS[k % 32],
             "resid": rng.normal(0, 0.01) - (0.5 if TEAMS[k % 32] == "DAL" else 0)}
            for c in range(2002, 2014) for k in range(32 * 4)]
    f = pl.DataFrame(rows)
    assert drafting.bands(f, draws=300, higher_is_better=False)[0] == ["DAL"]
    assert "DAL" not in drafting.bands(f, draws=300)[0]


# ---------------------------------------------------------------------------
# refusals fire
# ---------------------------------------------------------------------------

def _refused(p):
    with pytest.raises(px.ContractError):
        px.validated(px.KEY, p)


def test_a_value_without_its_interval_is_refused():
    p = payload()
    del p["values"][0]["interval"]
    _refused(p)


def test_a_published_slice_without_its_record_is_refused():
    p = payload()
    del p["slices"]["roster4"]["record"]
    _refused(p)


def test_a_score_on_zero_targets_is_refused():
    p = payload()
    p["slices"]["roster4"]["record"]["score"]["n"] = 0
    _refused(p)


def test_an_unknown_withheld_reason_is_refused():
    p = payload()
    p["slices"]["w_av"]["withheld_reasons"] = ["we_would_rather_not"]
    _refused(p)


def test_a_band_with_a_number_is_refused():
    r = result()
    r["outcomes"]["roster4"]["separation"]["p"] = 0.001
    p = px.build(r)
    p["slices"]["roster4"]["bands"][0]["band"] = 1
    _refused(p)


def test_the_gate_refuses_an_envelope_without_its_range():
    p = payload()
    p["range_note"] = " "
    _refused(p)


def test_the_key_must_imply_the_kind():
    with pytest.raises(px.ContractError):
        px.validated("analytics/nfl/drafting.roster4.json", payload())


# ---------------------------------------------------------------------------
# the proposal merges additively and the prefix is owned by no one else
# ---------------------------------------------------------------------------

def test_the_proposal_collides_with_nothing_in_the_contract():
    c, prop = analytics_export.contract(), px.proposal()
    assert not set(prop["$defs"]) & set(c["$defs"])
    assert not set(prop["x-contract-additions"]["kinds"]) & set(c["x-contract"]["kinds"])


def test_no_existing_key_pattern_claims_the_predictor_key():
    for entry in analytics_export.contract()["x-contract"]["keys"]:
        assert not re.match(entry["pattern"], px.KEY), entry


def test_the_vendored_contract_is_not_edited():
    c = analytics_export.contract()
    assert "predictor" not in c["x-contract"]["kinds"]
    assert "PredictorFile" not in c["$defs"]


def test_the_prefix_is_top_level_and_outside_every_other_builder():
    assert px.OWNED_PREFIX == "predictors/" and px.KEY.startswith(px.OWNED_PREFIX)
    others = [analytics_export.OWNED_PREFIX, "research/", "nfl/", "cfb/"]
    for o in others:
        assert not px.KEY.startswith(o) and not o.startswith(px.OWNED_PREFIX)


def test_write_never_deletes(tmp_path):
    stale = tmp_path / "predictors" / "nfl" / "retired.json"
    stale.parent.mkdir(parents=True)
    stale.write_text("{}")
    path = px.write(px.KEY, px.validated(px.KEY, payload()), str(tmp_path))
    assert os.path.exists(path) and stale.exists()


def test_the_module_cannot_reach_an_uploader_or_the_site_export():
    src = open(px.__file__, encoding="utf-8").read()
    tree = ast.parse(src)
    mods = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            mods.add(node.module or "")
            mods |= {"%s.%s" % (node.module, a.name) for a in node.names}
    assert not [m for m in mods if re.search(r"r2|boto|httpx|requests|export_web|upload", m)]
    names = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    names |= {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    assert "WEB_EXPORT_DIR" not in names
    assert "web_export_dir" not in names and "upload" not in names
