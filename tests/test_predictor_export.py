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
             bands=None, shift=0.0, env=(0.15, -0.2, 0.47)):
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
        "environment": {"r": env[0], "lo": env[1], "hi": env[2], "teams": 32},
        "walk_forward": walk,
        "teams_excluding_null": 1, "expected_by_chance": 1.6,
        "teams": teams,
        "bands": bands or [TEAMS[:20], TEAMS[20:]],
    }


def result(**over):
    r = {"pulled": "2026-09-09", "picks": 5371, "no_id_picks": 219,
         "outcomes": {"roster4": _outcome(), "bust": _outcome(p=0.16),
                      "snaps4": _outcome(classes=(2013, 2022)),
                      "w_av": _outcome(p=0.003, wf=None, classes=(2002, 2021),
                                       env=(0.82, 0.65, 0.91))}}
    r["outcomes"].update(over)
    return r


def payload(policy="show", **over):
    return px.build(result(**over), generated_at="2026-09-22T05:00:00Z",
                    values_when_not_separating=policy)


def build(r, policy="show"):
    return px.build(r, values_when_not_separating=policy)


# ---------------------------------------------------------------------------
# the real shape passes
# ---------------------------------------------------------------------------

def test_a_complete_payload_validates_against_contract_plus_proposal():
    p = px.validated(px.KEY, payload())
    assert len(p["values"]) == 128
    assert {v["slice"] for v in p["values"]} == {"roster4", "bust", "snaps4", "w_av"}


def test_every_slice_the_predictor_defines_is_on_the_envelope():
    p = payload()
    assert list(p["slices"]) == list(px.SLICES)


def test_nothing_is_withheld_once_the_terms_are_read():
    """f-07: Sports-Reference's section 5 permits republishing with credit, so
    no slice carries `licence_unresolved` any more."""
    assert all(not spec["withheld"] for spec in px.SLICES.values())
    p = px.validated(px.KEY, payload())
    assert all(s["status"] == "published" for s in p["slices"].values())


def test_a_withheld_slice_is_null_with_reasons_not_absent(monkeypatch):
    """The shape still exists for the next source whose terms are unread."""
    for name in ("snaps4", "w_av"):
        monkeypatch.setitem(px.SLICES, name,
                            dict(px.SLICES[name], withheld=["licence_unresolved"]))
    p = px.validated(px.KEY, payload())
    for name in ("snaps4", "w_av"):
        s = p["slices"][name]
        assert s["status"] == "withheld" and s["withheld_reasons"]
        for k in ("sample", "separation", "chance", "record", "confound", "bands",
                  "reading", "statement"):
            assert s[k] is None, (name, k)
        assert s["bands_reason"] == "withheld" and s["values_reason"] == "withheld"
        assert not [v for v in p["values"] if v["slice"] == name]
    assert p["attribution"]["applies_to"] == ["bust", "roster4"]


def test_a_missing_measurement_is_refused_not_dropped():
    r = result()
    del r["outcomes"]["w_av"]
    with pytest.raises(px.ContractError, match="w_av"):
        build(r)


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
    for p in (payload(), build(sep)):
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


def test_the_export_and_the_measurement_read_an_interval_by_one_rule():
    for args in [(0.01, 0.2, 15), (-0.2, -0.01, 15), (-0.1, 0.1, 15),
                 (0.016, 0.247, 4), (-0.1, 0.1, 4)]:
        assert px.record_verdict(*args) == drafting.forecast_verdict(*args)


def test_snaps4_published_says_not_readable_not_null():
    """f-06 held snaps4 on its licence; f-07 read the terms and publishes it.
    What the file says about its record is `not_readable` - its interval
    EXCLUDES zero on 4 targets - and never `no_better_than_chance`, in the
    record AND in the slice's reading AND in its sentence. Built from its
    measured figures."""
    wf = {"r": 0.139, "lo": 0.016, "hi": 0.247, "targets": 4, "slope": 0.29}
    r = result(snaps4=_outcome(p=0.14, classes=(2013, 2022)))
    r["outcomes"]["snaps4"]["walk_forward"] = wf
    p = px.validated(px.KEY, build(r))
    s = p["slices"]["snaps4"]
    assert s["status"] == "published"
    assert s["record"]["score"]["verdict"] == "not_readable"
    assert s["record"]["score"]["interval"][0] > 0     # and it excludes zero
    assert s["reading"] == "not_readable"
    assert "not readable" in s["statement"] and "neither a null nor an edge" in s["statement"]
    assert "no better than chance" not in s["statement"]


def test_the_record_is_null_with_a_reason_when_never_scored():
    rec = px._record({"note": "career AV is a snapshot"})
    assert rec == {"score": None, "reason": "not_as_of"}
    assert px._record({"targets": 0, "r": None})["reason"] == "too_few_targets"


def test_a_published_unscored_slice_still_validates():
    r = result(roster4=_outcome(wf=None))
    p = px.validated(px.KEY, build(r))
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
    p = px.validated(px.KEY, build(r))
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
    p = build(r)
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
    # Adopted by track A (a-05, redone as a-12): the contract carries the kind
    # and the proposal file is gone, so there is one definition of the shape
    # and not two.
    c = analytics_export.contract()
    assert c["x-contract"]["kinds"]["predictor"] == "PredictorFile"
    assert not os.path.exists(os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(px.__file__))),
        "docs", "proposals", "F08-predictor.defs.json"))


def test_no_existing_key_pattern_claims_the_predictor_key():
    claims = [e["kind"] for e in analytics_export.contract()["x-contract"]["keys"]
              if re.match(e["pattern"], px.KEY)]
    assert claims == ["predictor"], claims


def test_the_vendored_contract_is_not_edited():
    c = analytics_export.contract()
    assert "predictor" in c["x-contract"]["kinds"]
    assert "PredictorFile" in c["$defs"]


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


# ---------------------------------------------------------------------------
# f-07: each slice's reading, in one breath; the values policy; the credit
# ---------------------------------------------------------------------------

def test_the_values_policy_has_no_default():
    """Whether per-team values show when nothing separates is Ethan's call."""
    for bad in (None, "", "sometimes"):
        with pytest.raises(px.ContractError, match="values_when_not_separating"):
            px.build(result(), values_when_not_separating=bad)


def test_both_values_policies_validate_and_differ_only_in_values():
    show = px.validated(px.KEY, payload("show"))
    hide = px.validated(px.KEY, payload("hide"))
    assert show["values_policy"] == "show" and hide["values_policy"] == "hide"
    assert len(show["values"]) == 128 and hide["values"] == []
    for name in px.SLICES:
        a, b = show["slices"][name], hide["slices"][name]
        assert a["values_reason"] is None
        assert b["values_reason"] in ("separation_not_rejected", "confounded")
        assert {k: v for k, v in a.items() if k != "values_reason"} == \
               {k: v for k, v in b.items() if k != "values_reason"}
    assert hide["slices"]["w_av"]["values_reason"] == "confounded"


def test_hide_still_publishes_a_slice_that_separates_on_what_it_names():
    r = result()
    r["outcomes"]["roster4"]["separation"]["p"] = 0.001
    p = px.validated(px.KEY, build(r, "hide"))
    assert p["slices"]["roster4"]["reading"] == "separates"
    assert p["slices"]["roster4"]["values_reason"] is None
    assert {v["slice"] for v in p["values"]} == {"roster4"}


@pytest.mark.parametrize("sep,score,confounded,want", [
    ("separates", None, False, "separates"),
    ("separates", None, True, "separates_confounded"),
    ("does_not_separate", {"verdict": "no_better_than_chance"}, False, "does_not_separate"),
    ("does_not_separate", None, False, "does_not_separate"),
    ("does_not_separate", {"verdict": "not_readable"}, False, "not_readable"),
    ("does_not_separate", {"verdict": "forecasts"}, False, "forecasts"),
    ("does_not_separate", {"verdict": "forecasts_inversely"}, False, "forecasts_inversely"),
])
def test_slice_reading_takes_every_value(sep, score, confounded, want):
    assert px.slice_reading(sep, {"score": score, "reason": None}, confounded) == want


def test_confounded_needs_the_mechanism_and_the_measurement():
    """A correlation with winning alone is not a confound - real drafting skill
    would produce one - and a named mechanism the data does not show is not
    one either."""
    strong = {"interval": [0.65, 0.91]}
    weak = {"interval": [-0.2, 0.47]}
    assert px.is_confounded(px.SLICES["w_av"], strong)
    assert not px.is_confounded(px.SLICES["w_av"], weak)
    assert not px.is_confounded(px.SLICES["roster4"], strong)


def test_w_av_separates_but_says_confound_and_no_forecast_in_one_sentence():
    s = px.validated(px.KEY, payload())["slices"]["w_av"]
    assert s["separation"]["verdict"] == "separates"
    assert s["reading"] == "separates_confounded"
    st = s["statement"]
    assert st.count(". ") == 0 and st.endswith(".")          # one sentence
    assert "separate" in st and "not on drafting" in st
    assert "win share" in st and "team points" in st
    assert "no forecast" in st
    assert s["bands"] is None and s["bands_reason"] == "confounded"


def test_w_av_without_its_confound_would_read_separates_and_band():
    """The other answer is reachable: the same pipeline, with the confound
    interval covering zero, publishes `separates` and bands."""
    r = result(w_av=_outcome(p=0.003, wf=None, classes=(2002, 2021)))
    s = px.validated(px.KEY, build(r))["slices"]["w_av"]
    assert s["reading"] == "separates" and s["bands"]


def test_every_published_statement_names_separation_confound_and_forecast():
    for policy in px.VALUES_POLICIES:
        p = px.validated(px.KEY, payload(policy))
        for name, s in p["slices"].items():
            st = s["statement"]
            assert "shuffled labels" in st, name
            assert "win share" in st, name
            assert "forecast" in st, name
            assert st.startswith(px.SLICES[name]["label"] + ":")


def test_statements_are_generated_and_change_with_the_figures():
    base = payload()["slices"]["roster4"]["statement"]
    r = result()
    r["outcomes"]["roster4"]["separation"]["p"] = 0.001
    moved = build(r)["slices"]["roster4"]["statement"]
    assert "do not separate" in base and "do not separate" not in moved
    assert "(p 0.680)" in base and "(p 0.001)" in moved
    r = result(roster4=_outcome(wf=(0.2, 0.05, 0.35, 15)))
    assert "forecast its next class, r +0.200" in build(r)["slices"]["roster4"]["statement"]


def test_the_file_credits_sports_reference_for_every_published_slice():
    from analytics import pfr_terms
    p = px.validated(px.KEY, payload())
    a = p["attribution"]
    assert a["applies_to"] == sorted(px.SLICES)
    assert "Pro-Football-Reference" in a["statement"] and "Sports Reference" in a["statement"]
    assert a["terms_url"] == pfr_terms.TERMS_URL and a["terms_read"] == pfr_terms.TERMS_READ


def test_a_file_without_its_credit_is_refused():
    p = payload()
    del p["attribution"]
    _refused(p)
    p = payload()
    p["attribution"]["statement"] = ""
    _refused(p)


def test_nothing_published_is_a_per_pick_or_per_player_row():
    """Sports-Reference 5(i): no data store that substitutes for theirs. The
    file carries team aggregates only - no player, pick or PFR identifier."""
    import json as _json
    p = px.validated(px.KEY, payload())
    assert {v["subject"] for v in p["values"]} <= set(drafting.FRANCHISES)
    assert len(p["values"]) <= 32 * len(px.SLICES)
    gsis = re.compile(r"\b00-\d{7}\b")
    pfr = re.compile(r"\b[A-Z][A-Za-z.]{3}[A-Z]\w\d{2}\b")
    # the patterns are shown to fire before their silence is trusted
    assert gsis.search('"00-0036355"') and pfr.search('"JackLa00"')
    text = _json.dumps(p)
    assert not gsis.search(text) and not pfr.search(text)
