"""f-30: a-72's conformance claim, counted over EVERY file instead of 400 a side.

a-72 reads "missing 0, undeclared 1" from research/cfb_contract_diff.py, which profiles the
two player kinds on a seeded sample of 400 files per side (14,327 / 40,563 college,
4,002 / 19,328 NFL). Its own report says a key carried by fewer than ~1 in 400 files could
be absent from every conclusion. This file removes the sample, three ways:

  1. REPRODUCE  a-72's tool, unmodified, seed 72, on the staged tree -> its published totals.
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

    python research/f30_cfb_build/tree_census.py --src <a-72 worktree> \
        --nfl D:/calibrated-sports/data/web_export --cfb D:/temp/a72/new --out D:/temp/f30/census.json
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

PUBLISHED = {"missing": 0, "undeclared": 1, "declared": 21, "cfb_only": 15}
PLAYER_KINDS = ("player_summary", "player_season")
EXPECT_FILES = {"cfb": {"player_summary": 14327, "player_season": 40563},
                "nfl": {"player_summary": 4002, "player_season": 19328}}
out = lambda s="": print(s, flush=True)  # noqa: E731


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
    a = ap.parse_args()
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

    out("\n== 1. REPRODUCE a-72's totals with its own tool, seed 72, SAMPLE 400")
    rep, _ = run_tool(D, a.nfl, a.cfb, 72, 400)
    ok = rep["totals"] == PUBLISHED or {**{k: 0 for k in PUBLISHED}, **rep["totals"]} == PUBLISHED
    out(f"   tool says {rep['totals']}; published {PUBLISHED} -> {'REPRODUCES' if ok else 'DOES NOT REPRODUCE'}")
    for side in ("nfl", "cfb"):
        for k in PLAYER_KINDS:
            got = rep["kinds"][k][side + "_files"]
            out(f"   {side} {k}: {got:,} files (a-72 stated {EXPECT_FILES[side][k]:,})"
                f"{'' if got == EXPECT_FILES[side][k] else '   <- MOVED: the tree is not the one a-72 measured'}")
    result["reproduce"] = {"totals": rep["totals"], "ok": ok}

    out("\n== 2. FULL: the same tool, sample lifted, every file")
    full, _ = run_tool(D, a.nfl, a.cfb, 72, 10 ** 9)
    tot = {**{k: 0 for k in PUBLISHED}, **full["totals"]}
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
        floor_n, floor_c = n["files"] / 400.0, c["files"] / 400.0
        blind = sorted(p for p, v in n["present"].items() if v < floor_n)
        blind_c = sorted(p for p, v in c["present"].items() if v < floor_c)
        out(f"   NFL paths carried by < 1 in 400 files (< {floor_n:.0f} files): {len(blind)} "
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
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=1)
    out(f"\nwrote {a.out} ({time.time() - t0:.0f}s)")


if __name__ == "__main__":
    main()
