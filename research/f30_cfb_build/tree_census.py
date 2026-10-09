"""f-30: the export's conformance claim, counted over EVERY file instead of a sample a side.

The target reads its "missing / undeclared" totals from research/cfb_contract_diff.py, which
profiles the two player kinds on a seeded sample of files per side. A key carried by fewer
than one file in the sample size could be absent from every conclusion. This file removes
the sample, three ways:

  1. REPRODUCE  the target's tool, unmodified, at the baseline's seed and sample, on the
                staged tree -> the totals and file counts the BASELINE states. The baseline
                is a file named by --baseline; there is no default and no figure in this
                script (f-34). It names the two trees it describes by their manifests'
                generated_at, and a baseline for another tree is refused before anything
                is compared - that is a wrong argument, not a failed reproduction.
  2. FULL       the same tool with SAMPLE lifted above the file count -> the same four lists
                over every file. Run through the TARGET's code, so a difference is the sample.
  3. CENSUS     an independent walker (written here, sharing nothing with the tool) that
                counts, per kind and side, the files carrying each path. It prints the NFL
                paths in the sample's blind zone (carried by < 1 in 400 files) and what the
                college tree does with each - and checks the two full counts agree.
  4. PLANT      two synthetic trees where one NFL file in 2,000 carries a stat key college
                lacks: the sampled tool must be SEEN to miss it on some seeds and the full
                run must report it. A census that cannot report a missing key proves nothing.

Reads two directories of JSON. No database, no network; writes only --out.

    python research/f30_cfb_build/tree_census.py --src <the target's worktree> --nfl <NFL tree> \
        --cfb <staged tree> --baseline <baseline.json> --out <census.json>

The baseline, as the target published it for THESE two trees:

    {"source": "<who stated it, and where>",
     "trees": {"cfb": "<cfb/manifest.json generated_at>", "nfl": "<nfl/manifest.json generated_at>"},
     "seed": <int>, "sample": <int>,
     "totals": {"missing": n, "undeclared": n, "declared": n, "cfb_only": n},
     "files": {"cfb": {"player_summary": n, "player_season": n},
               "nfl": {"player_summary": n, "player_season": n}},
     "known": [{"list": "missing" | "undeclared", "path": "<exact path>",
                "source": "<who reported it, and where>"}]}          # optional

THE EXIT STATUS (f-36; the rule is research/f36_exit_status/PREREGISTRATION.md). Exit 0 means
all of: step 1 reproduced the baseline; every path the walker prints as MISSING, and every
missing or undeclared path either the walker or the tool's full run reports, is named in the
baseline's `known`; the walker and the tool differ on no path `known` does not name; and
`known` names nothing that was not found. Anything else is exit 1, with one `EXIT 1:` line
per reason, after the output is written (the decision is in the output, under "exit").
`known` has no default and no wildcard: a finding is acknowledged by its exact path, under
its own list, with a source. Exit 0 does NOT mean no FILLED-BUT-DECLARED line and no
blind-zone path; those are printed and do not move the status.
Exit 2: no --baseline. A malformed or wrong-tree baseline stops the run before step 4, exit 1.
"""
import argparse
import contextlib
import io
import json
import os
import sys
import tempfile
import time
from collections import Counter, defaultdict

TOTAL_KEYS = ("missing", "undeclared", "declared", "cfb_only")
KNOWN_LISTS = ("missing", "undeclared")
PLAYER_KINDS = ("player_summary", "player_season")
SIDES = ("nfl", "cfb")
out = lambda s="": print(s, flush=True)  # noqa: E731


def _count(v, where):
    if isinstance(v, bool) or not isinstance(v, int) or v < 0:
        raise SystemExit(f"baseline: {where} must be a count, got {v!r}")
    return v


def load_baseline(path):
    """-> the baseline, or SystemExit naming what it lacks. Nothing is filled in for the caller."""
    try:
        with open(path, encoding="utf-8") as f:
            b = json.load(f)
    except (OSError, ValueError) as e:
        raise SystemExit(f"baseline: cannot read {path}: {e}")
    if not isinstance(b, dict):
        raise SystemExit(f"baseline: {path} is not an object")
    for key in ("source", "trees", "seed", "sample", "totals", "files"):
        if key not in b:
            raise SystemExit(f"baseline: {path} has no {key!r}")
    if not (isinstance(b["source"], str) and b["source"].strip()):
        raise SystemExit("baseline: 'source' must say who stated these figures")
    for side in SIDES:
        if not (isinstance(b["trees"].get(side), str) and b["trees"][side]):
            raise SystemExit(f"baseline: trees.{side} must be that tree's manifest generated_at")
        for k in PLAYER_KINDS:
            if k not in b["files"].get(side, {}):
                raise SystemExit(f"baseline: no files.{side}.{k}")
            _count(b["files"][side][k], f"files.{side}.{k}")
    if set(b["totals"]) != set(TOTAL_KEYS):
        raise SystemExit(f"baseline: totals must carry exactly {list(TOTAL_KEYS)}, got {sorted(b['totals'])}")
    for k in TOTAL_KEYS:
        _count(b["totals"][k], f"totals.{k}")
    _count(b["seed"], "seed")
    if _count(b["sample"], "sample") < 1:
        raise SystemExit("baseline: sample must be at least 1")
    known_of(b)
    return b


def known_of(baseline):
    """-> {(list, path): source} for the findings the baseline acknowledges. Absent key = none."""
    known = {}
    entries = baseline.get("known", [])
    if not isinstance(entries, list):
        raise SystemExit("baseline: 'known' must be a list of {list, path, source}")
    for i, e in enumerate(entries):
        if not isinstance(e, dict) or set(e) != {"list", "path", "source"}:
            raise SystemExit(f"baseline: known[{i}] must carry exactly list, path and source")
        if e["list"] not in KNOWN_LISTS:
            raise SystemExit(f"baseline: known[{i}].list must be one of {list(KNOWN_LISTS)}, got {e['list']!r}")
        for field in ("path", "source"):
            if not (isinstance(e[field], str) and e[field].strip()):
                raise SystemExit(f"baseline: known[{i}].{field} must say "
                                 + ("the exact path" if field == "path" else "who reported it, and where"))
        if (e["list"], e["path"]) in known:
            raise SystemExit(f"baseline: known names {e['list']} {e['path']} twice")
        known[(e["list"], e["path"])] = e["source"]
    return known


def exit_reasons(result, known):
    """-> (reasons, acknowledged). The process exits 1 iff `reasons` is not empty.

    Reads only what is written to --out, so the file and the status cannot disagree."""
    reasons, found = [], set()
    if not result["reproduce"]["ok"]:
        reasons.append("step 1 DOES NOT REPRODUCE the baseline")
    walker = {(lst, p) for k in PLAYER_KINDS for lst in KNOWN_LISTS for p in result["census"][k][lst]}
    tool = {(lst, p) for k in result["full"]["kinds"] for lst in KNOWN_LISTS
            for p in result["full"]["kinds"][k][lst]}
    found = walker | tool
    for lst, p in sorted(walker - set(known)):
        reasons.append(f"the census reports {lst.upper()} {p}, which the baseline's 'known' does not name")
    for lst, p in sorted(tool - walker - set(known)):
        reasons.append(f"the tool's full run reports {lst} {p}, which the baseline's 'known' does not name")
    known_paths = {p for _, p in known}
    for k in PLAYER_KINDS:
        c, f = result["census"][k], result["full"]["kinds"][k]
        differ = (set(c["missing"]) ^ set(f["missing"])) | (set(c["undeclared"]) ^ set(f["undeclared"]))
        if bool(c["agrees_with_tool"]) != (not differ):
            reasons.append(f"{k}: the result says agrees_with_tool {c['agrees_with_tool']} and its own lists say {not differ}")
        loose = sorted(differ - known_paths)
        if loose:
            reasons.append(f"{k}: the census and the tool's full run disagree on {loose}")
    for lst, p in sorted(set(known) - found):
        reasons.append(f"'known' names {lst} {p} ({known[(lst, p)]}) and neither the census nor the tool reports it")
    acknowledged = [{"list": lst, "path": p, "source": known[(lst, p)]} for lst, p in sorted(found & set(known))]
    return reasons, acknowledged


def tree_stamps(nfl, cfb):
    """-> {side: that tree's manifest generated_at}."""
    stamps = {}
    for side, root in (("nfl", nfl), ("cfb", cfb)):
        with open(os.path.join(root, side, "manifest.json"), encoding="utf-8") as f:
            stamps[side] = json.load(f)["generated_at"]
    return stamps


def require_same_trees(baseline, stamps):
    """A baseline for another tree is a wrong argument. It is refused, never scored."""
    off = [s for s in SIDES if baseline["trees"][s] != stamps[s]]
    if off:
        raise SystemExit("baseline: describes another tree - " + "; ".join(
            f"{s} baseline {baseline['trees'][s]}, on disk {stamps[s]}" for s in off)
            + f". Nothing was compared. Supply the baseline published for these trees ({baseline['source']} is not it).")


def compare(rep, baseline):
    """-> (totals reproduce, [(side, kind, got, stated)] for every file count that differs)."""
    got = {**{k: 0 for k in TOTAL_KEYS}, **rep["totals"]}
    moved = [(side, k, rep["kinds"][k][side + "_files"], baseline["files"][side][k])
             for side in SIDES for k in PLAYER_KINDS
             if rep["kinds"][k][side + "_files"] != baseline["files"][side][k]]
    return got == baseline["totals"], moved


def run_tool(D, nfl, cfb, seed, sample):
    """a-72's main(), with its module-level SAMPLE set. -> the tool's own JSON report."""
    D.SAMPLE = sample
    fd, tmp = tempfile.mkstemp(suffix=".json", dir=os.environ.get("F30_TMP"))
    os.close(fd)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = D.main(["--nfl", nfl, "--cfb", cfb, "--json", tmp, "--seed", str(seed)])
    assert rc == 0, rc
    with open(tmp, encoding="utf-8") as f:
        rep = json.load(f)
    os.remove(tmp)
    assert rep["totals"] or rep["kinds"], "the tool wrote an empty report"
    return rep, buf.getvalue()


# ---- the independent walker -------------------------------------------------------------
def paths_in(obj, path, acc):
    """acc[path] = True if any occurrence in this file is non-empty, else False."""
    if isinstance(obj, dict):
        if not obj:
            acc.setdefault(path, False)
        for k, v in obj.items():
            paths_in(v, path + "." + k, acc)
    elif isinstance(obj, list):
        acc[path] = acc.get(path, False) or bool(obj)
        for v in obj:
            paths_in(v, path + "[]", acc)
    else:
        acc[path] = acc.get(path, False) or obj is not None


def census(root, sport):
    """-> {kind: {"files": n, "present": Counter, "filled": Counter}} over every file."""
    res = {k: {"files": 0, "present": Counter(), "filled": Counter()} for k in PLAYER_KINDS}
    base = os.path.join(root, sport, "players")
    for d in os.scandir(base):
        if not d.is_dir():
            continue
        for f in os.scandir(d.path):
            if not f.name.endswith(".json"):
                continue
            with open(f.path, encoding="utf-8") as fh:
                obj = json.load(fh)
            kind = obj.get("kind")
            if kind not in res:
                raise SystemExit(f"{f.path}: kind {kind!r} under a player directory")
            acc = {}
            paths_in(obj, kind, acc)
            r = res[kind]
            r["files"] += 1
            for p, filled in acc.items():
                r["present"][p] += 1
                r["filled"][p] += 1 if filled else 0
    return res


def is_stat(path):
    return ".stats." in path


def plant(D, tmp_root):
    """One NFL file in 2,000 carries `stats.f30_rare`; no college file does."""
    nfl, cfb = os.path.join(tmp_root, "nfl_tree"), os.path.join(tmp_root, "cfb_tree")
    env = {"schema_version": 2, "generated_at": "x"}
    for root, sport in ((nfl, "nfl"), (cfb, "cfb")):
        for i in range(2000):
            d = os.path.join(root, sport, "players", f"p{i}")
            os.makedirs(d, exist_ok=True)
            stats = {"targets": 1}
            if sport == "nfl" and i == 1234:
                stats["f30_rare"] = 3
            with open(os.path.join(d, "2020.json"), "w", encoding="utf-8") as f:
                json.dump({**env, "kind": "player_season", "sport": sport,
                           "periods": [{"stats": stats}]}, f)
        with open(os.path.join(root, sport, "manifest.json"), "w", encoding="utf-8") as f:
            json.dump({**env, "kind": "sport_manifest", "sport": sport, "absences": []}, f)
    want = "player_season.periods[].stats.f30_rare"
    seen = []
    for seed in range(20):
        rep, _ = run_tool(D, nfl, cfb, seed, 400)
        seen.append(want in rep["kinds"]["player_season"]["missing"])
    full, _ = run_tool(D, nfl, cfb, 72, 10 ** 9)
    c = census(nfl, "nfl")["player_season"]["present"][want]
    return seen, want in full["kinds"]["player_season"]["missing"], c


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True)
    ap.add_argument("--nfl", required=True)
    ap.add_argument("--cfb", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--baseline", required=True,
                    help="JSON: the totals and file counts the target published for THESE trees. No default.")
    a = ap.parse_args()
    base = load_baseline(a.baseline)
    stamps = tree_stamps(a.nfl, a.cfb)
    require_same_trees(base, stamps)
    out(f"baseline: {os.path.abspath(a.baseline)}\n   source: {base['source']}\n   trees: {stamps} (match)")
    src = os.path.abspath(a.src)
    sys.path.insert(0, src)
    os.chdir(src)
    from research import cfb_contract_diff as D
    assert os.path.abspath(D.__file__).startswith(src), D.__file__
    t0 = time.time()
    result = {}

    out("== 4. PLANT (run first: a census that cannot report a missing key proves nothing)")
    with tempfile.TemporaryDirectory(dir=os.environ.get("F30_TMP")) as tmp:
        seen, full_sees, n = plant(D, tmp)
    out(f"   one NFL file of 2,000 carries the key (independent census counts {n}); a-72's tool at "
        f"SAMPLE=400 reports it missing on {sum(seen)} of 20 seeds; with the sample lifted: "
        f"{'REPORTS IT' if full_sees else 'MISSES IT'}")
    if not full_sees or n != 1 or all(seen):
        raise SystemExit("the plant did not behave: the full run must report it, the census must "
                         "count 1, and the sample must be seen to miss it at least once")
    result["plant"] = {"sampled_reports_on_seeds_of_20": sum(seen), "full_reports": full_sees}

    out(f"\n== 1. REPRODUCE the baseline's totals with the target's own tool, seed {base['seed']}, SAMPLE {base['sample']}")
    rep, _ = run_tool(D, a.nfl, a.cfb, base["seed"], base["sample"])
    totals_ok, moved = compare(rep, base)
    ok = totals_ok and not moved
    out(f"   tool says {rep['totals']}; baseline {base['totals']} -> {'REPRODUCES' if totals_ok else 'DOES NOT REPRODUCE'}")
    for side in SIDES:
        for k in PLAYER_KINDS:
            got = rep["kinds"][k][side + "_files"]
            out(f"   {side} {k}: {got:,} files (baseline {base['files'][side][k]:,})"
                f"{'' if got == base['files'][side][k] else '   <- DIFFERS: the tree changed under the stamp the baseline names'}")
    out(f"   step 1 -> {'REPRODUCES' if ok else 'DOES NOT REPRODUCE'}")
    result["baseline"] = {**base, "path": os.path.abspath(a.baseline)}
    result["reproduce"] = {"totals": rep["totals"], "ok": ok, "totals_ok": totals_ok,
                           "files_differ": [list(m) for m in moved]}

    out("\n== 2. FULL: the same tool, sample lifted, every file")
    full, _ = run_tool(D, a.nfl, a.cfb, base["seed"], 10 ** 9)
    tot = {**{k: 0 for k in TOTAL_KEYS}, **full["totals"]}
    out(f"   tool says {tot}")
    for k in D.PAGE_KINDS:
        fk, sk = full["kinds"][k], rep["kinds"][k]
        assert fk["nfl_sampled"] == fk["nfl_files"] and fk["cfb_sampled"] == fk["cfb_files"], (k, fk)
        for lst in ("missing", "undeclared", "declared", "under_declared_parent", "cfb_only"):
            gained = sorted(set(fk[lst]) - set(sk[lst]))
            lost = sorted(set(sk[lst]) - set(fk[lst]))
            if gained or lost:
                out(f"   {k}.{lst}: sample {len(sk[lst])} -> full {len(fk[lst])}")
                for p in gained:
                    out(f"      + {p}")
                for p in lost:
                    out(f"      - {p}")
    result["full"] = {"totals": tot, "kinds": {k: {l: full["kinds"][k][l] for l in
                      ("missing", "undeclared", "declared", "under_declared_parent", "cfb_only")}
                      for k in D.PAGE_KINDS}}

    out("\n== 3. CENSUS: independent walker, every player file")
    declared, _ = D.declared_paths(a.cfb)
    cn, cc = census(a.nfl, "nfl"), census(a.cfb, "cfb")
    result["census"] = {}
    for k in PLAYER_KINDS:
        n, c = cn[k], cc[k]
        out(f"\n   {k}: nfl {n['files']:,} files, {len(n['present'])} paths ({sum(map(is_stat, n['present']))} stat); "
            f"cfb {c['files']:,} files, {len(c['present'])} paths ({sum(map(is_stat, c['present']))} stat)")
        assert n["files"] == full["kinds"][k]["nfl_files"] and c["files"] == full["kinds"][k]["cfb_files"]
        floor_n, floor_c = n["files"] / float(base["sample"]), c["files"] / float(base["sample"])
        blind = sorted(p for p, v in n["present"].items() if v < floor_n)
        blind_c = sorted(p for p, v in c["present"].items() if v < floor_c)
        out(f"   NFL paths carried by < 1 in {base['sample']} files (< {floor_n:.0f} files): {len(blind)} "
            f"({sum(map(is_stat, blind))} stat); college paths under the same bar: {len(blind_c)}")
        absent = sorted(p for p in n["present"] if p not in c["present"])
        und = [p for p in absent if D.is_declared(p, declared)]
        miss = [p for p in absent if p not in und]
        out(f"   NFL paths no college file carries: {len(absent)} = {len(und)} beneath a declared absence + {len(miss)} MISSING")
        for p in miss:
            out(f"      MISSING {p}   (nfl {n['present'][p]:,} of {n['files']:,} files, filled in {n['filled'][p]:,})")
        for p in blind:
            st = ("college carries it in %d files" % c["present"][p]) if p in c["present"] else (
                "DECLARED absent" if D.is_declared(p, declared) else "MISSING from college")
            out(f"      blind-zone {p}   nfl {n['present'][p]} files -> {st}")
        dead = sorted(p for p in n["present"] if p in c["present"] and c["filled"][p] == 0 and n["filled"][p] > 0)
        dundec = [p for p in dead if not D.is_declared(p, declared)]
        out(f"   present in college and empty in EVERY college file while the NFL fills it: {len(dead)}, "
            f"of which undeclared {len(dundec)}")
        for p in dundec:
            out(f"      UNDECLARED {p}   (cfb {c['present'][p]:,} files, all empty)")
        # a declared absence that some file fills is a declaration that is false
        false_dec = sorted(p for p in c["present"] if D.is_declared(p, declared) and c["filled"][p] > 0)
        out(f"   college paths named by a declared absence yet FILLED in some file: {len(false_dec)}")
        for p in false_dec:
            out(f"      FILLED-BUT-DECLARED {p}   filled in {c['filled'][p]:,} of {c['present'][p]:,} files")
        # agreement with the target's own full run. The tool treats an empty dict as present-and-empty
        # and has opaque maps; compare on the two lists that decide the claim.
        tm, tu = set(full["kinds"][k]["missing"]), set(full["kinds"][k]["undeclared"])
        agree = tm == set(miss) and tu == set(dundec)
        out(f"   agrees with the tool's full run on missing and undeclared: {agree}"
            + ("" if agree else f"  tool-only {sorted((tm | tu) - set(miss) - set(dundec))}  census-only {sorted((set(miss) | set(dundec)) - tm - tu)}"))
        skeys = lambda r: {p.rsplit(".stats.", 1)[1] for p in r["present"] if is_stat(p)}  # noqa: E731
        kn, kc = skeys(n), skeys(c)
        out(f"   distinct stat keys: nfl {len(kn)}, cfb {len(kc)}; nfl-only {len(kn - kc)}: {sorted(kn - kc)}")
        out(f"                       cfb-only {len(kc - kn)}: {sorted(kc - kn)}")
        result["census"][k] = {
            "nfl_files": n["files"], "cfb_files": c["files"], "missing": miss, "undeclared": dundec,
            "beneath_declared": und, "blind_zone_nfl": {p: n["present"][p] for p in blind},
            "blind_zone_cfb": {p: c["present"][p] for p in blind_c}, "false_declarations": false_dec,
            "nfl_only_stat_keys": sorted(kn - kc), "cfb_only_stat_keys": sorted(kc - kn),
            "agrees_with_tool": agree,
            "nfl_stat_files": {p: n["present"][p] for p in n["present"] if is_stat(p)},
            "cfb_stat_files": {p: [c["present"][p], c["filled"][p]] for p in c["present"] if is_stat(p)}}
    reasons, acknowledged = exit_reasons(result, known_of(base))
    result["exit"] = {"code": 1 if reasons else 0, "reasons": reasons, "acknowledged": acknowledged}
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=1)
    out(f"\nwrote {a.out} ({time.time() - t0:.0f}s)")
    out("\n== EXIT")
    for e in acknowledged:
        out(f"   acknowledged by the baseline: {e['list']} {e['path']}   ({e['source']})")
    for r in reasons:
        out(f"   EXIT 1: {r}")
    if reasons:
        raise SystemExit(1)
    out("   EXIT 0: step 1 reproduces; no missing or undeclared path outside 'known'; "
        "census and tool differ on no path outside 'known'; 'known' names nothing unfound")


if __name__ == "__main__":
    main()
