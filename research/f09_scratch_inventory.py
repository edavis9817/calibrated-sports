"""f-09: inventory the working space units leave behind. READ-ONLY.

Walks every candidate directory beside the four repositories and under
D:\\temp, and reports per directory: what git thinks it is (registered
worktree, standalone clone, plain directory), size, file count, last write,
reparse points (NTFS junctions / symlinks) found and NOT followed, whether it
carries a .venv / node_modules / run_cfb_job.cmd, and - for git trees -
whether HEAD is on the remote and whether the tree is dirty.

It deletes nothing, writes nothing but its --out JSON, and never follows a
reparse point: b-09 had `--force` follow an NTFS junction into a real
node_modules, and a size that counts a junction's target twice is the same
defect read-only.

    py -3.12 research/f09_scratch_inventory.py --out D:/temp/f09/inventory.json
"""
from __future__ import annotations

import argparse
import json
import os
import re
import stat
import subprocess
import sys
import time
from pathlib import Path

CODE = Path(r"C:\Users\Ethan Davis\code")
TEMP = Path(r"D:\temp")
MAIN_REPOS = {"calibrated-sports", "calibratedsports-web", "cs-cfb", "cs-analytics", "_relay"}
# A unit-made directory under D:\temp is named for its unit (a03, c04-wt,
# f02_clone_bt2, pt-a11, wt-b08) or is a tool's scratch that units spawn.
UNIT_RE = re.compile(r"^(?:pt-|wt-)?[abcf]\d\d(?:[-_].*)?$|^[abcf]\d\d$")
TOOL_RE = re.compile(r"^(miniflare-|open-next-tmp|playwright|cfb_cap_|cfb_export|pytest-of-)")
REPARSE = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


def git(cwd: Path, *args: str) -> str | None:
    try:
        r = subprocess.run(["git", "--no-optional-locks", "-C", str(cwd), *args], capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return r.stdout.strip() if r.returncode == 0 else None


def walk(root: Path) -> dict:
    size = files = 0
    newest = 0.0
    reparse: list[str] = []
    errors = 0
    gitdirs: set[str] = set()
    stack = [root]
    while stack:
        d = stack.pop()
        try:
            it = os.scandir(d)
        except OSError:
            errors += 1
            continue
        with it:
            for e in it:
                try:
                    st = e.stat(follow_symlinks=False)
                except OSError:
                    errors += 1
                    continue
                attrs = getattr(st, "st_file_attributes", 0)
                if attrs & REPARSE or e.is_symlink():
                    # Count the link, never its target.
                    try:
                        tgt = os.readlink(e.path)
                    except OSError:
                        tgt = "?"
                    reparse.append(f"{e.path} -> {tgt}")
                    continue
                if stat.S_ISDIR(st.st_mode) and e.name == ".git":
                    stack.append(Path(e.path))   # sized, but never counted as "recent use"
                    gitdirs.add(e.path)
                    continue
                if not any(e.path.startswith(g) for g in gitdirs):
                    newest = max(newest, st.st_mtime)
                if stat.S_ISDIR(st.st_mode):
                    stack.append(Path(e.path))
                else:
                    files += 1
                    size += st.st_size
    return {"bytes": size, "files": files, "newest_mtime": newest, "reparse": reparse, "errors": errors}


def git_facts(p: Path) -> dict | None:
    dotgit = p / ".git"
    if not dotgit.exists():
        return None
    kind = "worktree" if dotgit.is_file() else "clone"
    facts: dict = {"kind": kind}
    if kind == "worktree":
        facts["gitdir"] = dotgit.read_text().strip().removeprefix("gitdir: ")
    facts["remote"] = git(p, "remote", "get-url", "origin")
    facts["branch"] = git(p, "branch", "--show-current")
    facts["head"] = git(p, "rev-parse", "HEAD")
    porcelain = git(p, "status", "--porcelain", "--untracked-files=normal")
    facts["dirty"] = [l for l in (porcelain or "").splitlines()]
    # A scratch clone made with `git clone <local repo>` has a LOCAL origin, so
    # asking its own remote says nothing about GitHub. Ask the reference clone
    # of the GitHub repo instead, which was fetched at the start of the run.
    ref = REFERENCE.get(_repo_key(p, facts))
    on_remote = None
    if facts["head"] and ref:
        if git(ref, "cat-file", "-e", facts["head"]) is None:
            on_remote = "NOT-ON-REMOTE"
        else:
            br = git(ref, "branch", "-r", "--contains", facts["head"]) or ""
            names = [b.strip().split(" -> ")[0] for b in br.splitlines() if b.strip()]
            on_remote = ("on:" + ",".join(names[:3])) if names else "NOT-ON-REMOTE"
        if on_remote == "NOT-ON-REMOTE":
            remote_subjects = set((git(ref, "log", "--remotes", "--format=%s") or "").splitlines())
            local = []
            for h in (git(p, "rev-list", "HEAD", "--max-count=60") or "").splitlines():
                known = git(ref, "cat-file", "-e", h) is not None and bool(git(ref, "branch", "-r", "--contains", h))
                if known:
                    break
                local.append(git(p, "log", "-1", "--format=%s", h) or "")
            facts["unpushed_subjects"] = local
            if local and all(sj in remote_subjects for sj in local):
                on_remote = "SUPERSEDED"   # same subjects on a remote branch: rewritten, then pushed
    facts["head_on_remote"] = on_remote
    return facts


# One fetched clone per GitHub repository. calibrated-sports, cs-cfb and
# cs-analytics are three clones of ONE repository (LEDGER, 09-22).
REFERENCE = {"calibrated-sports": CODE / "cs-analytics", "calibratedsports-web": CODE / "calibratedsports-web"}
LONG_PREFIX = "\\\\?\\"   # the \\?\ that readlink puts on a junction target


def _repo_key(p: Path, facts: dict) -> str | None:
    where = (facts.get("remote") or "") + " " + (facts.get("gitdir") or "")
    if "calibratedsports-web" in where:
        return "calibratedsports-web"
    if facts.get("remote"):
        return "calibrated-sports"   # every other clone here descends from the one NFL repo
    return None


def registered_worktrees() -> dict[str, str]:
    """worktree path (lower, forward slashes) -> owning clone, across the four clones."""
    out = {}
    for repo in ("calibrated-sports", "calibratedsports-web", "cs-cfb", "cs-analytics"):
        for line in (git(CODE / repo, "worktree", "list", "--porcelain") or "").splitlines():
            if line.startswith("worktree "):
                out[line[9:].replace("\\", "/").lower()] = repo
    return out


def running_units() -> set[str]:
    units = set()
    for f in (CODE / "_relay" / "state").glob("track-*.json"):
        try:
            st = json.loads(f.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            continue
        if st.get("status") == "running" and st.get("unit"):
            units.add(st["unit"].replace("-", "").lower())   # a-10 -> a10
    return units


def candidates() -> list[tuple[str, Path]]:
    out = [("code", p) for p in sorted(CODE.iterdir()) if p.is_dir() and p.name not in MAIN_REPOS]
    for p in sorted(TEMP.iterdir()):
        if p.is_dir() and (UNIT_RE.match(p.name) or TOOL_RE.match(p.name)):
            out.append(("temp", p))
    return out


# Directories that are not scratch at all, found by reading the unit that made them.
NOTES = {"prod": "NOT SCRATCH - a-10's production clone (queue/units/a-10-production-checkout.md), in build"}


def verdict(r: dict) -> str:
    """The recommendation, most-cautious reason first. Nothing here acts on it."""
    trees = ([r["git"]] if r["git"] else []) + list(r["nested_worktrees"].values())
    if r["unit_running"]:
        return "LIVE - unit running now; do not touch"
    if r["name"] in NOTES:
        return NOTES[r["name"]]
    if r["idle_min"] is not None and r["idle_min"] < 60:
        return "LIVE? - written in the last hour; recheck in daylight"
    if any(t.get("head_on_remote") == "NOT-ON-REMOTE" for t in trees):
        return "KEEP - holds commits on no remote branch; push first"
    if any(t.get("dirty") for t in trees):
        return "LOOK - uncommitted changes; diff before removing"
    escaping = [x for x in r["reparse"]
                if not x.split(" -> ")[1].replace(LONG_PREFIX, "").lower().startswith(r["path"].lower())]
    how = "git worktree remove (no --force)" if (r["registered_worktree"] or r["nested_worktrees"]) else "delete"
    if escaping:
        return f"REMOVE after unlinking {len(escaping)} junction(s) with rmdir - then {how}"
    return f"REMOVE - {how}"


def markdown(rows: list[dict], by: dict[str, list]) -> str:
    stamp = time.strftime("%Y-%m-%d %H:%M %Z")
    out = [f"Generated {stamp} by `research/f09_scratch_inventory.py`. Sizes never follow a junction.", "",
           "| verdict | dirs | GB |", "|---|---:|---:|"]
    for k, rs in sorted(by.items()):
        out.append(f"| {k} | {len(rs)} | {sum(r['bytes'] for r in rs)/1e9:.2f} |")
    out += ["", "| directory | GB | files | idle min | git | branch | on remote | verdict |",
            "|---|---:|---:|---:|---|---|---|---|"]
    tool = re.compile(r"^(miniflare-|open-next-tmp|playwright|cfb_cap_)")
    grouped: dict[str, list] = {}
    for r in rows:
        m = tool.match(r["name"])
        if m and r["verdict"].startswith("REMOVE"):
            grouped.setdefault(m.group(1), []).append(r)
            continue
        trees = ([r["git"]] if r["git"] else []) + list(r["nested_worktrees"].values())
        kind = "+".join(sorted({t["kind"] for t in trees})) or "-"
        if r["nested_worktrees"]:
            kind += " (nested)"
        branch = ", ".join(t.get("branch") or "(detached)" for t in trees) or "-"
        remote = ", ".join((t.get("head_on_remote") or "?").split(",")[0] for t in trees) or "-"
        out.append(f"| `{r['path']}` | {r['bytes']/1e9:.2f} | {r['files']} | {r['idle_min']} | {kind} | {branch} | {remote} | {r['verdict']} |")
    for k, rs in grouped.items():
        out.append(f"| `{TEMP / k}*` ({len(rs)} dirs) | {sum(r['bytes'] for r in rs)/1e9:.3f} | "
                   f"{sum(r['files'] for r in rs)} | - | - | - | - | REMOVE - delete |")
    return "\n".join(out) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--markdown", help="also write the per-directory table here")
    args = ap.parse_args()

    for ref in REFERENCE.values():
        if git(ref, "fetch", "-q", "origin") is None:
            print(f"REFUSING: fetch failed in {ref}; cannot say what is pushed", file=sys.stderr)
            return 2
    wts = registered_worktrees()
    live_units = running_units()
    if not wts or not live_units:
        print(f"REFUSING: worktrees={len(wts)} running units={len(live_units)} - expected both non-zero",
              file=sys.stderr)
        return 2

    rows = []
    now = time.time()
    for where, p in candidates():
        t0 = time.time()
        w = walk(p)
        g = git_facts(p)
        key = str(p).replace("\\", "/").lower()
        nested = {wt: repo for wt, repo in wts.items() if wt.startswith(key + "/")}
        nested_facts = {wt: git_facts(Path(wt)) for wt in nested}
        unit = re.match(r"^(?:pt-|wt-)?([abcf])[-_]?(\d\d)", p.name.replace("calibratedsports-web-", "").replace("cs-analytics-", ""))
        unit = (unit.group(1) + unit.group(2)) if unit else None
        rows.append({
            "where": where, "path": str(p), "name": p.name,
            "bytes": w["bytes"], "files": w["files"],
            "idle_min": round((now - w["newest_mtime"]) / 60, 1) if w["newest_mtime"] else None,
            "reparse": w["reparse"], "walk_errors": w["errors"],
            "has_venv": (p / ".venv").exists(), "has_node_modules": (p / "node_modules").exists(),
            "has_run_cfb_job": (p / "run_cfb_job.cmd").exists(),
            "git": g, "walk_s": round(time.time() - t0, 1),
            "registered_worktree": key in wts, "worktree_of": wts.get(key),
            "nested_worktrees": {wt: {"repo": repo, **(nested_facts[wt] or {})} for wt, repo in nested.items()},
            "unit": unit, "unit_running": unit in live_units if unit else False,
        })
        rows[-1]["verdict"] = verdict(rows[-1])
        print(f"{p.name:48s} {w['bytes']/1e9:8.2f} GB {w['files']:8d} files "
              f"reparse={len(w['reparse'])} git={(g or {}).get('kind')} -> {rows[-1]['verdict']}", flush=True)

    if not rows:
        print("REFUSING: zero candidates found - the globs are wrong, not the disk clean", file=sys.stderr)
        return 2
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(rows, indent=1))
    print(f"{len(rows)} directories, {sum(r['bytes'] for r in rows)/1e9:.2f} GB total -> {args.out}")
    by: dict[str, list] = {}
    for r in rows:
        by.setdefault(r["verdict"].split(" - ")[0].split(" after")[0], []).append(r)
    for k, rs in sorted(by.items()):
        print(f"  {k:10s} {len(rs):4d} dirs {sum(r['bytes'] for r in rs)/1e9:7.2f} GB")
    if args.markdown:
        Path(args.markdown).write_text(markdown(rows, by), encoding="utf-8")
        print(f"table -> {args.markdown}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
