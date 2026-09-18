"""Track F's analytics export, validated against the vendored contract.

    python -m analytics.export --write
    python -m analytics.export --check          # validate without writing
    python -m analytics.export --keys

WHAT THIS IS NOT. It is not `jobs/export_web.py` and it does not touch it. That
file is track A's and track F is barred from it by `docs/W07-parallel-tracks.md`.
This writes its own keys, under its own prefix, from its own database, and
nothing here uploads: publishing is a separate decision made elsewhere.

TWO KINDS, BOTH ADDITIVE TO THE CONTRACT:

    analytics/{sport}/index.json        every metric, its range, where to fetch
    analytics/{sport}/{metric}.json     one metric's envelope and values

ANALYTICS IS A TOP-LEVEL PREFIX, AND THAT IS NOT COSMETIC. Track A, 2026-09-18:
`sync_keys(dest, research, ["research/"])` DELETES every local key under
`research/` that the research builder did not produce, and `build_research()`
produces exactly three files. Twelve market keys were deleted that way in one
night by a builder firing on data it does not produce.

`sync_keys`'s contract is "this builder owns this prefix", so the prefix has to
be one a single builder fills completely. Anything under a prefix another
builder owns is deleted on its next run - which is why this is NOT solved by
adding analytics to some other builder's wanted set. One incident is enough
evidence.

The first version of this module used `{sport}/analytics/`. That happened to be
safe - the owned prefixes are `nfl/market/`, `nfl/players/`, `nfl/teams/` and
`research/`, and nothing owns bare `nfl/` - but "happened to be safe" is a fact
about today's call sites, not a property of the key. A top-level prefix owned by
exactly one builder is the property.

THE GATE IS NOW EXPRESSED ON BOTH SIDES. `analytics.gate` refuses an estimate
without an interval and a sample count in the producer; `AnalyticValue` in the
contract makes `interval` non-nullable and `n` an integer >= 1, so a CONSUMER
refuses it too. That matters because a producer suite does not exercise the
site's validator - the same reason this module validates every file it writes
through one choke point (`_validated`) rather than trusting that it built them
correctly.

WHAT CANNOT BE EXPORTED, AND WHY IT IS REFUSED RATHER THAN COERCED. An estimate
whose interval is unbounded - a single block, so the bootstrap has nothing to
resample - has no JSON representation (`Infinity` is not JSON) and no meaning on
a page. It is dropped, counted, and the count is printed, because a number that
reads 0 most runs is what makes the run it reads 12 visible.
"""
import argparse
import json
import math
import os
import re
import sys
import time

from analytics import paths

CONTRACT_PATH = os.path.join("web", "contract", "v2", "contract.schema.json")
SPORT = "nfl"

# THE PREFIX THIS MODULE OWNS, and the only one it may write or delete under.
# `keys()` asserts every key it produces starts with it, and `sync()` deletes
# stale files only under it - a builder must own exactly the prefix it fills,
# no more and no less.
OWNED_PREFIX = "analytics/"
PREFIX = "%s%s" % (OWNED_PREFIX, SPORT)

_CONTRACT = None
_VALIDATORS = None


def contract():
    global _CONTRACT
    if _CONTRACT is None:
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, CONTRACT_PATH), encoding="utf-8") as f:
            _CONTRACT = json.load(f)
    return _CONTRACT


def validators():
    """One compiled validator per analytics kind, derived from the contract.

    Derived, not hand-listed: a second copy of the kind table here is exactly
    the drift the contract exists to close, and `jobs/export_web.py` builds its
    validators the same way.
    """
    global _VALIDATORS
    if _VALIDATORS is None:
        from jsonschema import Draft202012Validator
        c = contract()
        defs = c["$defs"]
        _VALIDATORS = {
            kind: Draft202012Validator({"$ref": "#/$defs/%s" % name, "$defs": defs})
            for kind, name in c["x-contract"]["kinds"].items()
            if kind.startswith("analytics.")}
    return _VALIDATORS


def kind_of(key):
    """The kind a key implies, from the contract's own key table."""
    for entry in contract()["x-contract"]["keys"]:
        if re.match(entry["pattern"], key):
            return entry["kind"]
    return None


class ContractError(AssertionError):
    """A file does not match the contract for the kind its key implies."""


def _validated(key, payload):
    """THE choke point. Nothing leaves this module without passing here."""
    kind = kind_of(key)
    if kind is None:
        raise ContractError("key %r matches no pattern in the contract" % key)
    if kind != payload.get("kind"):
        raise ContractError("key %r implies kind %r but the payload says %r"
                            % (key, kind, payload.get("kind")))
    errs = sorted(validators()[kind].iter_errors(payload), key=lambda e: e.path)
    if errs:
        where = "/".join(str(p) for p in errs[0].absolute_path) or "(root)"
        raise ContractError("%s does not match %s at %s: %s"
                            % (key, kind, where, errs[0].message[:200]))
    return payload


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _finite(*xs):
    return all(isinstance(x, (int, float)) and math.isfinite(x) for x in xs)


def metric_payload(con, metric):
    """(payload, dropped) for one metric. `dropped` counts unbounded intervals."""
    row = con.execute(
        "SELECT metric, label, unit, subject_type, block, basis, slice_kind, "
        "shares_denominator, season_from, season_to, range_note, availability, "
        "requires FROM f_metrics WHERE metric=?", (metric,)).fetchone()
    if not row:
        raise KeyError("no such metric: %r" % metric)
    (key, label, unit, subject_type, block, basis, slice_kind, denom,
     lo, hi, note, availability, requires) = row
    values, dropped = [], 0
    for subject, sl, est, a, b, n, rows, method in con.execute(
            "SELECT subject_id, slice, est, lo, hi, n, rows, method "
            "FROM f_metric_values WHERE metric=? ORDER BY subject_id, slice",
            (metric,)):
        if not _finite(a, b) or n is None or n < 1:
            dropped += 1
            continue
        if est is not None and not _finite(est):
            dropped += 1
            continue
        values.append({"subject": subject, "slice": sl or "",
                       "estimate": est, "interval": [a, b], "n": int(n),
                       "rows": int(rows) if rows is not None else None,
                       "method": method})
    payload = {
        "schema_version": 2, "generated_at": _now(), "kind": "analytics.metric",
        "sport": SPORT, "metric": key, "label": label, "unit": unit,
        "subject_type": subject_type, "block": block, "basis": basis,
        "slice_kind": slice_kind or "", "shares_denominator": denom,
        "season_from": int(lo), "season_to": int(hi), "range_note": note,
        "availability": availability,
        "requires": [r for r in (requires or "").split(";") if r and r != "-"],
        "values": values}
    return payload, dropped


def build(con):
    """{key: payload} for the index and every metric, all contract-validated."""
    out, dropped = {}, {}
    entries = []
    metrics = [r[0] for r in con.execute(
        "SELECT metric FROM f_metrics ORDER BY metric")]
    if not metrics:
        raise SystemExit("no metrics published - nothing to export. An export "
                         "that writes nothing and exits 0 is not an export.")
    for metric in metrics:
        payload, n_dropped = metric_payload(con, metric)
        key = "%s/%s.json" % (PREFIX, metric)
        out[key] = _validated(key, payload)
        if n_dropped:
            dropped[metric] = n_dropped
        entries.append({
            "metric": metric, "key": key, "label": payload["label"],
            "unit": payload["unit"], "subject_type": payload["subject_type"],
            "shares_denominator": payload["shares_denominator"],
            "season_from": payload["season_from"],
            "season_to": payload["season_to"],
            "range_note": payload["range_note"],
            "availability": payload["availability"],
            "value_count": len(payload["values"]),
            "subject_count": len({v["subject"] for v in payload["values"]})})
    index_key = "%s/index.json" % PREFIX
    out[index_key] = _validated(index_key, {
        "schema_version": 2, "generated_at": _now(), "kind": "analytics.index",
        "sport": SPORT, "source": paths.db_path(), "metrics": entries})
    return out, dropped


def export_dir():
    """Track F's own export directory, resolved through config - never a
    literal, and never `WEB_EXPORT_DIR`: that one is track A's and writing into
    it would put unreviewed keys where the site's uploader looks."""
    import config
    return config.storage_path("analytics_export")


def local_keys(root):
    """{key: path} for every .json already under this module's OWN prefix."""
    base = os.path.join(root, *OWNED_PREFIX.strip("/").split("/"))
    out = {}
    for dirpath, _dirs, files in os.walk(base):
        for name in files:
            if not name.endswith(".json"):
                continue
            path = os.path.join(dirpath, name)
            key = os.path.relpath(path, root).replace(os.sep, "/")
            out[key] = path
    return out


def sync(out, root=None, dry_run=False):
    """Write every wanted key; delete stale .json UNDER THIS PREFIX ONLY.

    Same contract as `jobs.export_web.sync_keys` and deliberately the same
    shape: this builder owns `analytics/` and nothing else, so a metric that
    stops being published stops being served, and nothing another builder owns
    is ever in reach. See the module docstring for the incident behind it.
    """
    root = root or export_dir()
    stale = [k for k in local_keys(root) if k not in out]
    written = 0
    for key, payload in out.items():
        assert key.startswith(OWNED_PREFIX), (
            "%r is outside the prefix this builder owns (%r)" % (key, OWNED_PREFIX))
        path = os.path.join(root, *key.split("/"))
        if not dry_run:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write(json.dumps(payload, indent=1, sort_keys=False) + "\n")
        written += 1
    for key in stale:
        if not dry_run:
            os.remove(os.path.join(root, *key.split("/")))
    return written, len(stale), root


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true",
                    help="build and validate without writing")
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--keys", action="store_true")
    a = ap.parse_args(argv)
    con = paths.connect(read_only=True)
    out, dropped = build(con)
    total_values = sum(len(p["values"]) for k, p in out.items()
                       if p["kind"] == "analytics.metric")
    if a.keys:
        for key in sorted(out):
            print("  %-64s %d bytes" % (key, len(json.dumps(out[key]))))
    print("%d keys, %d values, all validated against %s"
          % (len(out), total_values, CONTRACT_PATH))
    # Printed every run, zero included: a number that reads 0 most runs is what
    # makes the run it reads 12 visible.
    print("unbounded intervals dropped: %d across %d metrics%s"
          % (sum(dropped.values()), len(dropped),
             (" - " + ", ".join("%s:%d" % kv for kv in sorted(dropped.items())))
             if dropped else ""))
    if a.write:
        n, deleted, root = sync(out)
        print("wrote %d files, deleted %d stale, under %s/%s"
              % (n, deleted, root, OWNED_PREFIX))
    if not (a.check or a.write or a.keys):
        ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
