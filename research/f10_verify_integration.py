"""f-10: verify the 2026-09-23 integration merge from the BRANCHES, not from the merges.

Sixteen branches reached main on 2026-09-23: fifteen by first-parent merges
between PRE and END, and f-06 inside f-07 (INDIRECT). For every one of them this script re-derives what
the branch contributed - `git diff merge-base..tip` - and then asks whether main
still carries it. It never reads a merge commit's resolution to decide that.

Per file touched by a branch:

    identical   main's blob == the branch tip's blob
    contained   blobs differ, but the branch's whole patch reverse-applies to
                main's index (every hunk the branch wrote is present, with its
                context); the difference is someone else's change, and the
                commits responsible are listed
    LOST?       the patch does not reverse-apply; the branch's added lines that
                main does not carry are printed, and a human decides
    deleted-ok  the branch deleted the file and main does not have it

Then the three union-merged ledgers (row / block level), the hand-resolved
contract (defs, kinds, keys, required == properties, metaschema), and a count
check that the run actually covered what it expected. A union merge of two
sides appending at one EOF keeps the shared opening separator ('', '---', '')
once and glues the second section to the first; that is reported as
SEPARATOR LOST and counts as a problem. Exits non-zero on any
LOST? or failed check, and on covering fewer merges than expected.

    python -m research.f10_verify_integration [--json out.json]

Read-only: it runs `git` plumbing and `git apply --cached --check`, which
writes nothing. Run it from a checkout whose index equals origin/main.
"""
from __future__ import annotations

import argparse
import collections
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

PRE = "88806df"          # main immediately before the first integration merge
END = "218b45d"          # main after the merges and their two post-merge fixes
MAIN = "origin/main"     # the tree being verified; --main overrides
EXPECTED_MERGES = 15     # 14 clean + a-11; the brief says "sixteen" - see report
LEDGERS = ["DECISIONS.md", "docs/track-a-requests.md", "docs/track-b-requests.md"]
CONTRACT = "web/contract/v2/contract.schema.json"

# Commits on main's first-parent line AFTER the merges, read in full by f-10 and judged
# deliberate. Each may explain only the lines it itself removed from that one path.
POST_MERGE_FIXES = {
    ("f-04-pfr-alias", "tests/test_pfr_alias.py"): "970a5d6",      # a-03 removed key_cols
    ("f-05-nfl-weather", "tests/test_nfl_weather.py"): "218b45d",  # freeze time.time in one test
    # f-06 arrived INSIDE f-07 (9577091), and f-07's own a6da078 rewrote its snaps4 prose/test
    ("f-06-drafting-scope", "analytics/predictor_export.py"): "a6da078",
    ("f-06-drafting-scope", "tests/test_predictor_export.py"): "a6da078",
}
# Branches merged into another branch rather than into main: (branch, the merge that carried it)
INDIRECT = {"f-06-drafting-scope": "9577091"}


def git(*args: str, check: bool = True, input: bytes | None = None) -> str:
    r = subprocess.run(["git", *args], capture_output=True, input=input)
    if check and r.returncode != 0:
        raise SystemExit(f"git {' '.join(args)} failed: {r.stderr.decode(errors='replace')}")
    return r.stdout.decode("utf-8", errors="replace")


def blob(rev: str, path: str) -> str | None:
    r = subprocess.run(["git", "show", f"{rev}:{path}"], capture_output=True)
    return r.stdout.decode("utf-8", errors="replace") if r.returncode == 0 else None


def merges() -> list[dict]:
    out = []
    for line in git("log", "--first-parent", "--merges", "--reverse",
                    "--format=%H %s", f"{PRE}..{END}").splitlines():
        sha, subj = line.split(" ", 1)
        m = re.search(r"origin/(\S+?)'", subj)
        name = m.group(1) if m else subj
        tip = git("rev-parse", f"{sha}^2").strip()
        base = git("merge-base", f"{sha}^1", f"{sha}^2").strip()
        remote = git("rev-parse", f"origin/{name}", check=False).strip()
        out.append(dict(branch=name, merge=sha, tip=tip, base=base,
                        tip_is_remote=(remote == tip)))
    return out


def added_lines(patch: str) -> list[str]:
    return [l[1:] for l in patch.splitlines()
            if l.startswith("+") and not l.startswith("+++")]


def added_blocks(patch: str) -> list[list[str]]:
    blocks, cur = [], []
    for l in patch.splitlines():
        if l.startswith("+") and not l.startswith("+++"):
            cur.append(l[1:])
        else:
            if cur:
                blocks.append(cur)
            cur = []
    if cur:
        blocks.append(cur)
    return blocks


def reverse_applies(patch: str) -> tuple[bool, str]:
    with tempfile.NamedTemporaryFile("wb", suffix=".patch", delete=False) as f:
        f.write(patch.encode("utf-8"))
        name = f.name
    r = subprocess.run(["git", "apply", "--cached", "--reverse", "--check", name],
                       capture_output=True)
    Path(name).unlink()
    return r.returncode == 0, r.stderr.decode(errors="replace").strip()


def check_file(m: dict, status: str, path: str, main_tree: dict) -> dict:
    res = dict(path=path, status=status)
    main_txt = blob(MAIN, path)
    if status == "D":
        res["verdict"] = "deleted-ok" if main_txt is None else "LOST?-deleted-file-back"
        return res
    tip_txt = blob(m["tip"], path)
    if main_txt is None:
        res["verdict"] = "LOST?-file-absent-on-main"
        return res
    if main_txt == tip_txt:
        res["verdict"] = "identical"
        return res
    patch = git("diff", "--no-renames", m["base"], m["tip"], "--", path)
    ok, err = reverse_applies(patch)
    # who else changed this file between the branch's base and main, excluding the branch itself
    others = git("log", "--format=%h %s", f"{m['base']}..{MAIN}", f"^{m['tip']}",
                 "--", path).splitlines()
    res["other_commits"] = others
    if ok:
        res["verdict"] = "contained"
        return res
    main_lines = collections.Counter(main_txt.splitlines())
    need = collections.Counter(added_lines(patch))
    missing = [l for l, n in need.items() if main_lines[l] < n]
    res["apply_error"] = err[:300]
    res["missing_added_lines"] = missing
    if not missing:
        res["verdict"] = "contained-by-lines"
        return res
    # JSON: a line that gained a trailing comma because another side appended after it
    if path.endswith(".json"):
        loose = collections.Counter(l.rstrip().rstrip(",") for l in main_txt.splitlines())
        if all(loose[l.rstrip().rstrip(",")] >= 1 for l in missing):
            res["verdict"] = "contained-mod-trailing-comma"
            return res
    # a post-merge fix commit: accepted ONLY if every missing line is exactly a line
    # that commit removed from this path, so an unrelated loss cannot hide behind it
    fix = POST_MERGE_FIXES.get((m["branch"], path))
    if fix:
        removed = {l[1:] for l in git("show", "--format=", fix, "--", path).splitlines()
                   if l.startswith("-") and not l.startswith("---")}
        if set(missing) <= removed:
            res["verdict"] = f"changed-later-by-{fix}"
            return res
    res["verdict"] = "LOST?"
    return res


def resurrected(ms: list[dict], path: str, main_cnt: collections.Counter) -> list[dict]:
    """Lines a branch DELETED from a ledger that main still carries more often than the tip."""
    out = []
    for m in ms:
        tip = blob(m["tip"], path)
        if tip is None:
            continue
        tip_cnt = collections.Counter(tip.splitlines())
        patch = git("diff", "--no-renames", m["base"], m["tip"], "--", path)
        for l in patch.splitlines():
            if l.startswith("-") and not l.startswith("---") and l[1:].strip():
                if main_cnt[l[1:]] > tip_cnt[l[1:]]:
                    out.append(dict(branch=m["branch"], line=l[1:120]))
    return out


def ledger_checks(ms: list[dict]) -> dict:
    out = {}
    revs = [PRE] + [m["tip"] for m in ms] + ["origin/a-05-coverage-predictor-kinds"]
    for path in LEDGERS:
        main_txt = blob(MAIN, path) or ""
        main_rows = main_txt.splitlines()
        # a "row" is any non-blank line; tables and bullets alike
        cnt = collections.Counter(l for l in main_rows if l.strip())
        dups = {l[:120]: n for l, n in cnt.items() if n > 1 and l.startswith("|")
                and not re.fullmatch(r"\|[\s\-|:]*\|?", l)}
        missing = {}
        for rev in revs:
            if rev == "origin/a-05-coverage-predictor-kinds":
                continue
            txt = blob(rev, path)
            if txt is None:
                continue
            for l in txt.splitlines():
                if l.strip() and l not in cnt:
                    missing.setdefault(l[:160], []).append(rev[:7])
        # contiguity: every block a branch appended is contiguous in main
        broken, glued = [], []
        for m in ms:
            patch = git("diff", "--no-renames", m["base"], m["tip"], "--", path)
            if not patch:
                continue
            for full in added_blocks(patch):
                # the separator lines ('' and '---') every section opens with are shared
                # context between two sides appending at one EOF; judge the BODY for
                # interleaving and report the separators on their own
                blk = list(full)
                while blk and blk[0].strip() in ("", "---"):
                    blk.pop(0)
                while blk and blk[-1].strip() in ("", "---"):
                    blk.pop()
                n = len(blk)
                if n < 2:
                    continue
                at = [i for i in range(len(main_rows) - n + 1) if main_rows[i:i + n] == blk]
                if not at:
                    broken.append(dict(branch=m["branch"], first=blk[0][:120], lines=n))
                    continue
                lead = full[:full.index(blk[0])]
                i = at[0]
                if lead and main_rows[max(0, i - len(lead)):i] != lead:
                    glued.append(dict(branch=m["branch"], heading=blk[0][:100], line=i + 1,
                                      branch_lead=lead,
                                      main_prev=main_rows[i - 1][:80] if i else None))
        out[path] = dict(rows_on_main=len(main_rows), duplicate_table_rows=dups,
                         lines_on_some_rev_not_on_main=missing,
                         non_contiguous_branch_blocks=broken,
                         separator_lost_before=glued,
                         removed_by_branch_still_on_main=resurrected(ms, path, cnt))
    return out


def contract_checks(ms: list[dict]) -> dict:
    by = {m["branch"]: m for m in ms}
    main_c = json.loads(blob(MAIN, CONTRACT))
    res = {}
    for b in ("a-09-live-prices", "a-11-components-table"):
        side = json.loads(blob(by[b]["tip"], CONTRACT))
        base = json.loads(blob(by[b]["base"], CONTRACT))
        new_defs = sorted(set(side["$defs"]) - set(base["$defs"]))
        changed_defs = sorted(k for k in set(side["$defs"]) & set(base["$defs"])
                              if side["$defs"][k] != base["$defs"][k])
        absent = [d for d in side["$defs"] if d not in main_c["$defs"]]
        differ = [d for d in side["$defs"]
                  if d in main_c["$defs"] and main_c["$defs"][d] != side["$defs"][d]
                  and side["$defs"][d] != base["$defs"].get(d)]
        xc_s, xc_b, xc_m = side["x-contract"], base["x-contract"], main_c["x-contract"]
        new_kinds = [k for k in xc_s["kinds"] if k not in xc_b["kinds"]]
        kinds_missing = [k for k in new_kinds if k not in xc_m["kinds"]]
        keys_s = xc_s["keys"]
        keys_b = xc_b["keys"]
        new_keys = ({k: v for k, v in keys_s.items() if keys_b.get(k) != v}
                    if isinstance(keys_s, dict) else [k for k in keys_s if k not in keys_b])
        keys_m = xc_m["keys"]
        if isinstance(new_keys, dict):
            keys_missing = {k: v for k, v in new_keys.items() if keys_m.get(k) != v}
        else:
            keys_missing = [k for k in new_keys if k not in keys_m]
        top_new = sorted(set(side) - set(base))
        top_changed = sorted(k for k in set(side) & set(base)
                             if k not in ("$defs", "x-contract") and side[k] != base[k])
        xc_other = sorted(k for k in set(xc_s) if k not in ("kinds", "keys")
                          and xc_s.get(k) != xc_b.get(k))
        res[b] = dict(new_defs=new_defs, changed_defs=changed_defs,
                      defs_absent_on_main=absent, branch_defs_differing_on_main=differ,
                      new_kinds=new_kinds, kinds_missing_on_main=kinds_missing,
                      new_keys=new_keys, keys_missing_on_main=keys_missing,
                      top_level_new=top_new, top_level_changed=top_changed,
                      x_contract_other_changed=xc_other,
                      x_contract_other_on_main_equal={k: xc_m.get(k) == xc_s.get(k)
                                                      for k in xc_other})
    shapes = {}
    for d in ("LivePricesFile", "ComponentsFile"):
        s = main_c["$defs"].get(d)
        if s is None:
            shapes[d] = "ABSENT"
            continue
        props = set(s.get("properties", {}))
        req = s.get("required", [])
        shapes[d] = dict(required_eq_properties=(set(req) == props and len(req) == len(props)),
                         additionalProperties=s.get("additionalProperties"),
                         n_props=len(props))
    res["file_shapes"] = shapes
    try:
        import jsonschema
        cls = jsonschema.validators.validator_for(main_c)
        cls.check_schema(main_c)
        # every $ref must resolve
        refs = set(re.findall(r'"\$ref":\s*"#/\$defs/([^"]+)"', json.dumps(main_c)))
        res["metaschema"] = dict(valid=True, validator=cls.__name__,
                                 unresolved_refs=sorted(refs - set(main_c["$defs"])))
    except ImportError:
        res["metaschema"] = dict(valid=None, error="jsonschema not installed")
    except Exception as e:  # SchemaError
        res["metaschema"] = dict(valid=False, error=str(e)[:500])
    return res


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json")
    ap.add_argument("--main", default=MAIN, help="rev to verify against (default origin/main)")
    a = ap.parse_args()
    globals()["MAIN"] = a.main
    if subprocess.run(["git", "diff", "--cached", "--quiet", MAIN]).returncode != 0:
        raise SystemExit("index is not origin/main - the reverse-apply check would test the wrong tree")
    ms = merges()
    for name, via in INDIRECT.items():
        tip = git("rev-parse", f"{via}^2").strip()
        ms.append(dict(branch=name, merge=via, tip=tip, base=git("merge-base", tip, PRE).strip(),
                       tip_is_remote=(git("rev-parse", f"origin/{name}", check=False).strip() == tip)))
    if len(ms) != EXPECTED_MERGES + len(INDIRECT):
        print(f"!! expected {EXPECTED_MERGES + len(INDIRECT)} branches, found {len(ms)}")
    bad = 0
    report = dict(merges=[])
    for m in ms:
        files = [l.split("\t", 1) for l in
                 git("diff", "--no-renames", "--name-status", m["base"], m["tip"]).splitlines()]
        rows = [check_file(m, s, p, {}) for s, p in files]
        tally = collections.Counter(r["verdict"] for r in rows)
        bad += sum(n for v, n in tally.items() if v.startswith("LOST"))
        print(f"\n== {m['branch']}  tip {m['tip'][:7]}  base {m['base'][:7]}  "
              f"tip==origin:{m['tip_is_remote']}  files {len(rows)}  {dict(tally)}")
        for r in rows:
            if r["verdict"] != "identical":
                print(f"   {r['verdict']:<20} {r['status']} {r['path']}")
                for c in r.get("other_commits", []):
                    print(f"        by {c[:110]}")
                for l in r.get("missing_added_lines", [])[:10]:
                    print(f"        MISSING: {l[:140]}")
        report["merges"].append(dict(m, files=rows, tally=dict(tally)))
    if sum(len(r["files"]) for r in report["merges"]) == 0:
        raise SystemExit("zero files examined - the run verified nothing")
    report["ledgers"] = ledger_checks(ms)
    print("\n== ledgers")
    for p, r in report["ledgers"].items():
        print(f"   {p}: lines {r['rows_on_main']}, dup rows {len(r['duplicate_table_rows'])}, "
              f"lines on some rev not on main {len(r['lines_on_some_rev_not_on_main'])}, "
              f"non-contiguous branch blocks {len(r['non_contiguous_branch_blocks'])}, "
              f"separators lost {len(r['separator_lost_before'])}, "
              f"branch deletions undone {len(r['removed_by_branch_still_on_main'])}")
        for l, revs in r["lines_on_some_rev_not_on_main"].items():
            print(f"      not-on-main [{','.join(sorted(set(revs)))}] {l[:130]}")
        for l, n in r["duplicate_table_rows"].items():
            print(f"      DUP x{n}: {l}")
        for b in r["non_contiguous_branch_blocks"]:
            print(f"      SPLIT {b}")
        for g in r["separator_lost_before"]:
            print(f"      SEPARATOR LOST line {g['line']} [{g['branch']}] {g['heading'][:70]!r}"
                  f" branch opened with {g['branch_lead']}; main line above: {g['main_prev']!r}")
        for g in r["removed_by_branch_still_on_main"]:
            print(f"      RESURRECTED [{g['branch']}] {g['line']}")
        bad += len(r["removed_by_branch_still_on_main"]) + len(r["separator_lost_before"])
        bad += len(r["duplicate_table_rows"]) + len(r["non_contiguous_branch_blocks"])
    report["contract"] = contract_checks(ms)
    print("\n== contract")
    print(json.dumps(report["contract"], indent=1))
    c = report["contract"]
    for b in ("a-09-live-prices", "a-11-components-table"):
        x = c[b]
        bad += len(x["defs_absent_on_main"]) + len(x["kinds_missing_on_main"]) + len(x["keys_missing_on_main"])
        if not x["new_kinds"] or not x["new_defs"]:
            print(f"!! {b}: no new kind/def detected - the check would pass vacuously")
            bad += 1
    for d, s in c["file_shapes"].items():
        if s == "ABSENT" or not s["required_eq_properties"] or s["additionalProperties"] is not False:
            bad += 1
    if c["metaschema"].get("valid") is not True or c["metaschema"].get("unresolved_refs"):
        bad += 1
    if a.json:
        Path(a.json).write_text(json.dumps(report, indent=1, default=str))
    n_exp = EXPECTED_MERGES + len(INDIRECT)
    print(f"\nbranches {len(ms)} (expected {n_exp}); problems {bad}")
    return 1 if bad or len(ms) != n_exp else 0


if __name__ == "__main__":
    sys.exit(main())
