"""a-72: diff a CFB export tree against an NFL one, key by key and path by path.

    python -m research.cfb_contract_diff --nfl <NFL tree> --cfb <CFB tree> [--json out.json]

Reads two directories of exported JSON. No database, no network, writes nothing but
`--json`. The question it answers is the one the templates ask: for every field the
first sport's files carry, does the second sport's file of the same kind carry it too -
and where it carries it empty, has it SAID so?

Four lists per kind, and each is a different statement:

    missing     a path the NFL files have and no CFB file has. A template reading it
                gets `undefined`, which is the failure this unit exists to remove.
    declared    present in CFB and null/empty in EVERY file, where NFL fills it, AND
                named in the CFB manifest's `absences`. An honest empty state.
    undeclared  the same emptiness with no `absences` entry. A null nobody explained.
    cfb_only    a path only CFB has. Harmless to a template; listed so it is known.

Stat maps are walked by key (`...stats.targets`), because a stat key the templates name
literally is a field in everything but the schema. The per-row key rule drops a key that
is zero all season, so a stat key is reported missing only when NO sampled file has it.

Every count is of FILES, and the sample size is printed beside it: a path 'present in
3 of 400' is not the same finding as one present in all of them.
"""
import argparse
import json
import os
import random
import statistics
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from jobs.export_web import kind_for_key                          # noqa: E402

PAGE_KINDS = ("sport_manifest", "team", "player_index", "player_summary", "player_season")
# Maps whose KEYS are data (an id, a stat key the manifest defines, a colour code):
# walking into them would compare one sport's vocabulary with another's.
OPAQUE = {"sport_manifest.stat_definitions", "sport_manifest.market_definitions",
          "sport_manifest.scoring_presets", "sport_manifest.team_colors",
          "player_summary.identity.ids"}
SAMPLE = 400


def keys_of(root, sport_prefixes):
    out = defaultdict(list)
    for base, _dirs, names in os.walk(root):
        for n in names:
            if not n.endswith(".json"):
                continue
            full = os.path.join(base, n)
            key = os.path.relpath(full, root).replace(os.sep, "/")
            if not key.startswith(sport_prefixes):
                continue
            try:
                kind, _ = kind_for_key(key)
            except Exception:
                kind = "(no contract kind)"
            out[kind].append((key, full))
    return out


def walk(obj, path, seen, top):
    """Record every path under `obj`. seen[path] = [present, empty] for THIS file."""
    if isinstance(obj, dict):
        if path in OPAQUE:
            seen[path][0] = 1
            seen[path][1] = 0 if obj else 1
            return
        if not obj and path != top:
            seen[path][0] = 1
            seen[path][1] = 1 if seen[path][1] is None else seen[path][1]
            return
        for k, v in obj.items():
            walk(v, f"{path}.{k}", seen, top)
    elif isinstance(obj, list):
        p = path + "[]"
        seen[path][0] = 1
        if not obj:
            seen[path][1] = 1 if seen[path][1] is None else seen[path][1]
        else:
            seen[path][1] = 0
        for v in obj:
            walk(v, p, seen, top)
    else:
        seen[path][0] = 1
        empty = obj is None
        # A path is empty in a file only if EVERY occurrence in that file is empty.
        seen[path][1] = int(empty) if seen[path][1] is None else (seen[path][1] and int(empty))


def profile(files, rng):
    sample = files if len(files) <= SAMPLE else rng.sample(files, SAMPLE)
    present, empty, sizes = Counter(), Counter(), []
    for key, full in sample:
        sizes.append(os.path.getsize(full))
        with open(full, encoding="utf-8") as f:
            obj = json.load(f)
        kind = obj.get("kind")
        seen = defaultdict(lambda: [0, None])
        walk(obj, kind, seen, kind)
        for path, (p, e) in seen.items():
            present[path] += p
            empty[path] += 1 if e else 0
    return {"n": len(sample), "of": len(files), "present": present, "empty": empty,
            "sizes": sizes}


def declared_paths(cfb_root):
    path = os.path.join(cfb_root, "cfb", "manifest.json")
    with open(path, encoding="utf-8") as f:
        m = json.load(f)
    return [a["path"] for a in m.get("absences", [])], "absences" in m


def is_declared(path, declared):
    """`player_season.periods[].stats.snaps` is covered by that exact entry, by a
    `stats.snaps` entry (a stat key wherever a Stats map carries it), or by an entry for
    any ancestor path (`team.coaches` covers `team.coaches[].season`)."""
    for d in declared:
        if path == d or path.startswith(d + ".") or path.startswith(d + "["):
            return True
        if d.startswith("stats.") and path.endswith("." + d):
            return True
        # offense/defense maps on a team split are Stats maps without the word `stats`
        if d.startswith("stats.") and path.rsplit(".", 1)[-1] == d[len("stats."):] and (
                ".offense." in path or ".defense." in path):
            return True
    return False


def sizes_line(sizes, of):
    if not sizes:
        return "0 files"
    mean = sum(sizes) / len(sizes)
    return (f"{of} files, sampled {len(sizes)}: median {int(statistics.median(sizes)):,} B, "
            f"max {max(sizes):,} B, est. total {mean * of / 1e6:,.1f} MB")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--nfl", required=True)
    ap.add_argument("--cfb", required=True)
    ap.add_argument("--json", default=None)
    ap.add_argument("--seed", type=int, default=72)
    a = ap.parse_args(argv)
    rng = random.Random(a.seed)

    nfl = keys_of(a.nfl, ("nfl/",))
    cfb = keys_of(a.cfb, ("cfb/",))
    if not nfl or not cfb:
        raise SystemExit(f"read {sum(map(len, nfl.values()))} NFL and "
                         f"{sum(map(len, cfb.values()))} CFB keys - one tree is empty or the "
                         "path is wrong; refusing to print a diff of nothing")
    report = {"families": {}, "kinds": {}}
    print("KEY FAMILIES UNDER THE SPORT PREFIX (files)")
    for kind in sorted(set(nfl) | set(cfb)):
        n, c = len(nfl.get(kind, [])), len(cfb.get(kind, []))
        mark = "" if n and c else ("   <- NFL only" if n else "   <- CFB only")
        print(f"  {kind:<18} nfl {n:>6}   cfb {c:>6}{mark}")
        report["families"][kind] = {"nfl": n, "cfb": c}

    declared, has_list = declared_paths(a.cfb)
    print(f"\nCFB manifest declares {len(declared)} absences"
          f"{'' if has_list else '  (NO `absences` KEY AT ALL)'}")
    totals = Counter()
    for kind in PAGE_KINDS:
        pn, pc = profile(nfl.get(kind, []), rng), profile(cfb.get(kind, []), rng)
        print(f"\n== {kind}")
        print(f"   nfl  {sizes_line(pn['sizes'], pn['of'])}")
        print(f"   cfb  {sizes_line(pc['sizes'], pc['of'])}")
        if not pn["n"] or not pc["n"]:
            print("   one side has no file of this kind - nothing to compare")
            report["kinds"][kind] = {"compared": False}
            continue
        absent = sorted(p for p in pn["present"] if p not in pc["present"])
        # A path below a declared-empty parent (`prop_history.games[].line` under a
        # declared-null `prop_history`) is that declaration, not a second finding.
        under = [p for p in absent if is_declared(p, declared)]
        missing = [p for p in absent if p not in under]
        cfb_only = sorted(p for p in pc["present"] if p not in pn["present"])
        both = [p for p in pn["present"] if p in pc["present"]]
        # empty in EVERY CFB file that has it, and filled in at least one NFL file
        dead = sorted(p for p in both
                      if pc["empty"][p] == pc["present"][p]
                      and pn["empty"][p] < pn["present"][p])
        dec = [p for p in dead if is_declared(p, declared)]
        undec = [p for p in dead if not is_declared(p, declared)]
        for title, rows, src in (("missing", missing, pn), ("undeclared", undec, pc),
                                 ("declared", dec, pc), ("cfb_only", cfb_only, pc)):
            extra = (f"  (+{len(under)} paths beneath a declared-empty parent, not listed)"
                     if title == "declared" and under else "")
            print(f"   {title}: {len(rows)}{extra}")
            for p in rows:
                print(f"      {p}   ({src['present'][p]} of {src['n']} "
                      f"{'nfl' if src is pn else 'cfb'} files)")
            totals[title] += len(rows)
        report["kinds"][kind] = {
            "compared": True, "nfl_files": pn["of"], "cfb_files": pc["of"],
            "nfl_sampled": pn["n"], "cfb_sampled": pc["n"],
            "paths_compared": len(both), "missing": missing, "undeclared": undec,
            "declared": dec, "under_declared_parent": under, "cfb_only": cfb_only,
            "cfb_median_bytes": int(statistics.median(pc["sizes"])),
            "cfb_max_bytes": max(pc["sizes"]),
            "nfl_median_bytes": int(statistics.median(pn["sizes"])),
            "nfl_max_bytes": max(pn["sizes"])}
    print(f"\nTOTAL over the five page kinds: missing {totals['missing']}  "
          f"undeclared {totals['undeclared']}  declared {totals['declared']}  "
          f"cfb_only {totals['cfb_only']}")
    report["totals"] = dict(totals)
    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
