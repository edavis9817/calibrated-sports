"""Drafting strengths as a published shape, with its scoring record attached (f-02).

    python -m analytics.predictor_export --check              # measure, build, validate
    python -m analytics.predictor_export --write               # ... and write locally
    python -m analytics.predictor_export --from-json r.json    # reuse a saved measurement

A PREDICTOR SHIPS WITH ITS RECORD, IN THE SAME FILE, KEYED TO THE SAME SLICE.
Every stats site publishes a team number; publishing how that number did when it
was used to forecast what it could not see is the reason this one is worth
publishing. So `slices[s].record` sits beside `slices[s]`'s values, and when a
slice has never been scored the score is NULL WITH A REASON, never absent.

WHAT THE RECORD SAYS, today: on the two published slices a team's closed draft
record does not forecast its next class, and no team separates from shuffled
labels (F07). The file is shaped so that the pipeline COULD say otherwise -
every verdict below is computed from the figures and each enum value is
reachable (tests drive each one) - but on this data it does not. This unit is
therefore the write-up of a null, not a metric dressed up to look publishable.

WHAT IS WITHHELD, AND WHY IT IS PRESENT ANYWAY. `snaps4` and `w_av` are read
from Pro-Football-Reference-sourced columns whose redistribution terms have not
been read (F07), and `w_av` also separates teams on winning rather than drafting
(r = 0.82 with win share). Both appear under `slices` with status `withheld`,
every measured field null and the reasons named. A page can then say why a
number is missing; an absent slice is indistinguishable from one nobody built.

BANDS ARE NULL WHEREVER SEPARATION DOES NOT REJECT. The leader is a selected
maximum of 32 noisy numbers and its 31 contrasts are unadjusted, so bands split
roster4 and bust in two while the separation test cannot tell either from
shuffled labels. A band boundary there would be exactly the claim F07 refutes.

WHERE IT WRITES, AND WHERE IT NEVER GOES. Its own top-level prefix,
`predictors/`, under its own directory (`config.storage_path("predictor_export")`).
Never `WEB_EXPORT_DIR`, never `analytics/` - `analytics.export.sync` deletes
every key under `analytics/` its own build did not produce, so a predictor there
would be deleted on the next analytics run. It writes and never deletes, and it
imports nothing that uploads (asserted by AST in the tests): `upload()` deletes
by absence and this module does not go near it.

THE CONTRACT IS PROPOSED, NOT EDITED. The kind's `$defs` are in
`docs/proposals/F08-predictor.defs.json`, for track A. Validation here merges the
vendored contract's `$defs` with the proposal's, in memory; the contract file is
read and never written.
"""
import argparse
import json
import os
import re
import sys
import time

from analytics import drafting, gate
from analytics import export as analytics_export

PROPOSAL_PATH = os.path.join("docs", "proposals", "F08-predictor.defs.json")
OWNED_PREFIX = "predictors/"
SPORT = "nfl"
PREDICTOR = "drafting"
KEY = "%s%s/%s.json" % (OWNED_PREFIX, SPORT, PREDICTOR)
ALPHA = 0.05
MIN_READABLE = 5            # the five-block floor, brief 020 / 022
BLOCK_METHOD = "classblock%d"
RECORD_METHOD = "targetblock%d"

# Every slice the predictor defines, in a fixed order. Order is for the file's
# readability and carries no claim.
SLICES = {
    "roster4": {
        "label": "Roster weeks over the slot",
        "unit": ("share of regular-season weeks on any NFL roster (53 or reserve "
                 "lists) over a pick's first four seasons, minus the league's "
                 "expectation at the same pick slot, per pick"),
        "cannot_see": "quality and health: a 53rd man, an All-Pro and a player "
                      "on injured reserve all count one week",
        "direction": "higher_is_better",
        "withheld": [],
    },
    "bust": {
        "label": "Busts over the slot",
        "unit": ("share of picks on no NFL roster for a single regular-season "
                 "week in four seasons, minus the league's expectation at the "
                 "same pick slot, per pick"),
        "cannot_see": "anything above zero roster weeks",
        "direction": "lower_is_better",
        "withheld": [],
    },
    "snaps4": {
        "label": "Snaps over the slot",
        "unit": ("offensive plus defensive snaps over a pick's first four "
                 "seasons, relative to the class mean, minus the league's "
                 "expectation at the same pick slot, per pick"),
        "cannot_see": "special teams, and every class before 2013",
        "direction": "higher_is_better",
        "withheld": ["licence_unresolved"],
    },
    "w_av": {
        "label": "Career Approximate Value over the slot",
        "unit": ("Pro-Football-Reference weighted career AV relative to the "
                 "class mean, minus the league's expectation at the same pick "
                 "slot, per pick"),
        "cannot_see": "whether a team's AV comes from its picks or from its "
                      "winning, since AV is allocated from team points",
        "direction": "higher_is_better",
        "withheld": ["licence_unresolved", "confounded"],
    },
}

_VALIDATOR = None


def _root():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def proposal():
    with open(os.path.join(_root(), PROPOSAL_PATH), encoding="utf-8") as f:
        return json.load(f)


def validator():
    """The vendored contract's $defs plus the proposal's, merged in memory.

    A name collision REFUSES rather than letting one silently win: the proposal
    is meant to merge additively, and a collision means it cannot.
    """
    global _VALIDATOR
    if _VALIDATOR is None:
        from jsonschema import Draft202012Validator
        defs = dict(analytics_export.contract()["$defs"])
        prop = proposal()["$defs"]
        clash = sorted(set(defs) & set(prop))
        if clash:
            raise ContractError("proposal redefines contract $defs %s" % clash)
        defs.update(prop)
        _VALIDATOR = Draft202012Validator(
            {"$ref": "#/$defs/PredictorFile", "$defs": defs})
    return _VALIDATOR


class ContractError(AssertionError):
    """The payload does not match the proposed kind, or the gate refuses it."""


def kind_of(key):
    for entry in proposal()["x-contract-additions"]["keys"]:
        if re.match(entry["pattern"], key):
            return entry["kind"]
    return None


# ---------------------------------------------------------------------------
# verdicts - each computed from figures, each able to take every value
# ---------------------------------------------------------------------------

def separation_verdict(p, alpha=ALPHA):
    return "separates" if p < alpha else "does_not_separate"


def record_verdict(lo, hi, n, min_n=MIN_READABLE):
    if n < min_n:
        return "not_readable"
    if lo > 0:
        return "forecasts"
    if hi < 0:
        return "forecasts_inversely"
    return "no_better_than_chance"


def bands_block(bands, p, direction, means, alpha=ALPHA):
    """(bands, reason). Bands only where separation rejects; each band's leader
    is its best member in `direction`, members alphabetical."""
    if separation_verdict(p, alpha) != "separates":
        return None, "separation_not_rejected"
    sign = 1.0 if direction == "higher_is_better" else -1.0
    out = []
    for members in bands:
        lead = max(members, key=lambda t: sign * means[t])
        out.append({"leader": lead, "members": sorted(members)})
    return out, None


# ---------------------------------------------------------------------------
# build
# ---------------------------------------------------------------------------

def _withheld_slice(spec, classes):
    return {"status": "withheld", "withheld_reasons": list(spec["withheld"]),
            "label": spec["label"], "unit": spec["unit"],
            "cannot_see": spec["cannot_see"], "direction": spec["direction"],
            "null_value": 0.0, "season_from": classes[0],
            "season_to": classes[1], "sample": None, "separation": None,
            "chance": None, "record": None, "confound": None, "bands": None,
            "bands_reason": "withheld"}


def _record(wf):
    if wf.get("r") is None:
        reason = "not_as_of" if "note" in wf else "too_few_targets"
        return {"score": None, "reason": reason}
    return {"score": {
        "statistic": "walk_forward_pearson_r", "estimate": wf["r"],
        "interval": [wf["lo"], wf["hi"]], "n": int(wf["targets"]),
        "method": RECORD_METHOD % drafting.DRAWS,
        "lag_blocks": drafting.HORIZON, "min_readable_n": MIN_READABLE,
        "verdict": record_verdict(wf["lo"], wf["hi"], wf["targets"])},
        "reason": None}


def _published_slice(spec, o, perms, unresolved):
    sep, ne, env = o["separation"], o["null_exclusions"], o["environment"]
    means = {t["team"]: t["est"] for t in o["teams"]}
    bands, reason = bands_block(o["bands"], sep["p"], spec["direction"], means)
    return {
        "status": "published", "withheld_reasons": [],
        "label": spec["label"], "unit": spec["unit"],
        "cannot_see": spec["cannot_see"], "direction": spec["direction"],
        "null_value": 0.0,
        "season_from": o["classes"][0], "season_to": o["classes"][1],
        "sample": {"subjects": len(o["teams"]), "blocks": sep["classes"],
                   "rows": sep["picks"], "unresolved_rows": unresolved},
        "separation": {"between_sd": sep["between_sd"],
                       "null_sd_mean": sep["null_sd_mean"],
                       "null_sd_p95": sep["null_sd_p95"], "p": sep["p"],
                       "permutations": perms,
                       "verdict": separation_verdict(sep["p"])},
        "chance": {"excluding_null": int(o["teams_excluding_null"]),
                   "subjects": len(o["teams"]),
                   "nominal": o["expected_by_chance"],
                   "null_mean": ne["mean"], "null_p95": ne["p95"],
                   "permutations": ne["perms"]},
        "record": _record(o["walk_forward"]),
        "confound": {"against": "team regular-season win share",
                     "statistic": "pearson_r", "estimate": env["r"],
                     "interval": [env["lo"], env["hi"]], "n": env["teams"],
                     "method": "fisher_z95"},
        "bands": bands, "bands_reason": reason,
    }


def build(result, perms=None, sources=None, generated_at=None):
    """The predictor payload from a `drafting.measure()` result.

    `perms` is read inside, never frozen into the default: a default argument
    is evaluated once at import (see the default-argument row in CLAUDE.md).

    Refuses rather than emitting a partial file: a slice the result does not
    carry is an error, and so is a published slice with no values.
    """
    perms = drafting.PERMS if perms is None else perms
    missing = sorted(set(SLICES) - set(result["outcomes"]))
    if missing:
        raise ContractError("measurement carries no %s" % missing)
    slices, values = {}, []
    for name, spec in SLICES.items():
        o = result["outcomes"][name]
        if spec["withheld"]:
            slices[name] = _withheld_slice(spec, o["classes"])
            continue
        slices[name] = _published_slice(spec, o, perms, result["no_id_picks"])
        for t in o["teams"]:
            values.append({"subject": t["team"], "slice": name,
                           "estimate": t["est"], "interval": [t["lo"], t["hi"]],
                           "n": int(t["n"]), "rows": int(t["picks"]),
                           "method": BLOCK_METHOD % drafting.DRAWS})
        if not any(v["slice"] == name for v in values):
            raise ContractError("published slice %s has no values" % name)
    pub = [s for s in slices.values() if s["status"] == "published"]
    lo = min(s["season_from"] for s in pub)
    hi = max(s["season_to"] for s in pub)
    return {
        "schema_version": 2,
        "generated_at": generated_at or time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                                      time.gmtime()),
        "kind": "predictor", "sport": SPORT, "predictor": PREDICTOR,
        "label": "Drafting strengths", "subject_type": "team",
        "block": "draft class",
        "null_definition": ("a typical team: residuals are centred within each "
                            "draft class, so 0 is value realised exactly as the "
                            "league realises it from the same pick slots"),
        "alpha": ALPHA, "season_from": lo, "season_to": hi,
        "range_note": ("classes %d-%d: roster_weekly starts in %d, and a class "
                       "is scored only once its %d-season horizon has closed"
                       % (lo, hi, drafting.ROSTER_FIRST, drafting.HORIZON)),
        "availability": "historical",
        "sources": sources or result.get("sources") or
                   ["draft_picks@%s" % result["pulled"]],
        "slices": slices, "values": values,
    }


def _gate_view(payload):
    """The payload's estimates in the long shape the gate reads: every
    {estimate, interval, n} object becomes {est, lo, hi, n}. The gate then
    refuses any estimate that arrived without an interval or a sample."""
    out = []

    def walk(o, path):
        if isinstance(o, dict):
            if "estimate" in o:
                iv = o.get("interval") or [None, None]
                row = {"est": o["estimate"], "n": o.get("n")}
                if iv[0] is not None:
                    row["lo"], row["hi"] = iv
                out.append((path, row))
            for k, v in o.items():
                walk(v, "%s.%s" % (path, k))
        elif isinstance(o, list):
            for i, v in enumerate(o):
                walk(v, "%s[%d]" % (path, i))
    walk(payload, "$")
    return out


def validated(key, payload):
    """THE choke point: key pattern, proposed contract, and both gate rules."""
    if kind_of(key) != payload.get("kind"):
        raise ContractError("key %r implies kind %r, payload says %r"
                            % (key, kind_of(key), payload.get("kind")))
    errs = sorted(validator().iter_errors(payload), key=lambda e: list(e.path))
    if errs:
        where = "/".join(str(p) for p in errs[0].absolute_path) or "(root)"
        raise ContractError("%s does not match PredictorFile at %s: %s"
                            % (key, where, errs[0].message[:200]))
    bad = gate.metric_violations(payload)
    for path, row in _gate_view(payload):
        bad += [(path,) + v[1:] for v in gate.payload_violations(row)]
        if row.get("n") is None or row["n"] < 1:
            bad.append((path, "n", "estimate has no sample count"))
    if bad:
        raise ContractError("gate refuses %s: %s" % (key, bad[:3]))
    return payload


def export_dir():
    """Track F's own directory. Never WEB_EXPORT_DIR: that is where track A's
    uploader looks, and nothing in this unit goes near it."""
    import config
    return config.storage_path("predictor_export")


def write(key, payload, root):
    """Write one key. Never deletes anything."""
    assert key.startswith(OWNED_PREFIX), key
    path = os.path.join(root, *key.split("/"))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(json.dumps(payload, indent=1) + "\n")
    return path


def summary(payload):
    """One line per slice, zero counts included."""
    lines = []
    for name, s in payload["slices"].items():
        if s["status"] == "withheld":
            lines.append("  %-8s WITHHELD (%s)" % (name, ", ".join(s["withheld_reasons"])))
            continue
        sc = s["record"]["score"]
        rec = ("%s r=%+.3f [%+.3f, %+.3f] n=%d" % (sc["verdict"], sc["estimate"],
               *sc["interval"], sc["n"]) if sc else "no score: " + s["record"]["reason"])
        lines.append("  %-8s separation %s p=%.4f | record %s | bands %s | "
                     "excl %d vs chance %.1f | values %d"
                     % (name, s["separation"]["verdict"], s["separation"]["p"], rec,
                        len(s["bands"]) if s["bands"] else s["bands_reason"],
                        s["chance"]["excluding_null"], s["chance"]["null_mean"],
                        sum(v["slice"] == name for v in payload["values"])))
    return lines


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--from-json", help="a saved `analytics.drafting --json` result")
    ap.add_argument("--check", action="store_true", help="build and validate only")
    ap.add_argument("--write", action="store_true", help="write under --out")
    ap.add_argument("--out", help="root directory (default: this track's own)")
    a = ap.parse_args(argv)
    if a.from_json:
        with open(a.from_json, encoding="utf-8") as f:
            result = json.load(f)
    else:
        result = drafting.measure()
    if result["picks"] < 1000:
        raise SystemExit("only %d picks read - refusing to export" % result["picks"])
    payload = validated(KEY, build(result))
    n = len(payload["values"])
    if n == 0:
        raise SystemExit("no values - an export that writes nothing is not an export")
    print("%s: %d values over %d slices, validated against the contract + %s"
          % (KEY, n, len(payload["slices"]), PROPOSAL_PATH))
    for line in summary(payload):
        print(line)
    if a.write:
        print("wrote " + write(KEY, payload, a.out or export_dir()))
    elif not a.check:
        ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
