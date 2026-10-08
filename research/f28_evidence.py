"""Which merge and push asks does git already answer (unit f-28).

    python -m research.f28_evidence <relay> --main-repo <clone> --web-repo <clone or mirror>

For every merge-class `needs_ethan` item (`relay.items.is_merge_class`) this
names the unit branches its `what` text refers to - the raising unit's own when
it names none - and asks git, per branch, whether the tip is an ancestor of
main. Tracks a, c and f share one repository; track b is the web repository.

It PRINTS evidence. It closes nothing: a merge ask with a second half ("then run
the publish") is not answered by the merge, and reading that is a person's job.
`research/f28_closures.json` holds what was closed and why.

POST HOC, and labelled so wherever it is quoted: the last block counts how many
merges would settle every still-unmerged branch named by a merge ask (a branch
that is an ancestor of another named, unmerged branch needs no merge of its
own). It was added after the pre-registered text rule had run.
"""
import collections
import re
import subprocess
import sys

from relay import items as im

_REF = re.compile(r"\b([abcf])-(\d{1,3})((?:-[a-z0-9]+)*)\b")


def git(repo, *args):
    r = subprocess.run(["git", "-C", repo] + list(args), capture_output=True, text=True)
    return r.returncode, r.stdout.strip()


def branches(repo, prefix):
    rc, out = git(repo, "for-each-ref", "--format=%(refname:short) %(objectname)", prefix)
    if rc != 0 or not out:
        raise SystemExit("no refs under %s in %s" % (prefix, repo))
    by_unit = collections.defaultdict(list)
    for line in out.splitlines():
        name, sha = line.split()
        short = name.split("/", 1)[1] if name.startswith("origin/") else name
        m = re.match(r"([abcf])-?(\d{1,3})(?:-|$)", short)
        if m:
            by_unit["%s-%d" % (m.group(1), int(m.group(2)))].append((short, sha))
    return by_unit


def is_ancestor(repo, sha, main):
    return git(repo, "merge-base", "--is-ancestor", sha, main)[0] == 0


def evidence(relay, main_repo, web_repo):
    items, _ = im.load(relay)
    repos = {}
    for key, repo in (("main", main_repo), ("web", web_repo)):
        rc, _ = git(repo, "rev-parse", "--verify", "-q", "origin/main")
        prefix, main = ("refs/remotes/origin", "origin/main") if rc == 0 else ("refs/heads", "main")
        repos[key] = (repo, main, git(repo, "rev-parse", main)[1], branches(repo, prefix))
    rows = []
    for it in items:
        if not im.is_merge_class(it.what):
            continue
        named = ["%s-%d" % (m.group(1), int(m.group(2))) for m in _REF.finditer(it.what.lower())] or [it.unit]
        found = []
        for unit in dict.fromkeys(named):
            repo, main, main_sha, by_unit = repos["web" if unit[0] == "b" else "main"]
            for name, sha in by_unit.get(unit, []) or [(None, None)]:
                found.append((unit, name, sha, None if sha is None else is_ancestor(repo, sha, main)))
        rows.append((it, found))
    return rows, repos


def main(argv):
    relay = argv[1]
    main_repo = argv[argv.index("--main-repo") + 1]
    web_repo = argv[argv.index("--web-repo") + 1]
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    rows, repos = evidence(relay, main_repo, web_repo)
    if not rows:
        raise SystemExit("no merge-class items - refusing to print an empty table as a result")
    for key in ("main", "web"):
        print("%s repo main = %s" % (key, repos[key][2][:7]))
    tally = collections.Counter()
    unmerged = {}
    for it, found in rows:
        states = [f[3] for f in found]
        verdict = ("NO-BRANCH" if any(s is None for s in states) and not any(s is False for s in states)
                   and not any(s is True for s in states) else
                   "ALL-MERGED" if all(s is True for s in states) else
                   "NONE-MERGED" if not any(s is True for s in states) else "SOME-MERGED")
        tally[verdict] += 1
        print("\n%-8s %-11s %s" % (it.id, verdict, it.what))
        for unit, name, sha, anc in found:
            print("    %-6s %-40s %s %s" % (unit, name or "(no branch on the remote)", (sha or "")[:7],
                                           {True: "in main", False: "NOT in main", None: ""}[anc]))
            if anc is False:
                unmerged[(("web" if unit[0] == "b" else "main"), name)] = sha
    print("\n%d merge-class items: %s" % (len(rows), ", ".join("%s %d" % kv for kv in sorted(tally.items()))))
    print("\nPOST HOC (not pre-registered): unmerged branches named by merge asks, and the merges that cover them")
    for key in ("main", "web"):
        repo = repos[key][0]
        tips = {n: s for (k, n), s in unmerged.items() if k == key}
        maximal = [n for n, s in tips.items()
                   if not any(o != n and so != s and is_ancestor(repo, s, so) for o, so in tips.items())]
        print("  %s repo: %d unmerged named branches, covered by %d tips: %s"
              % (key, len(tips), len(maximal), ", ".join(sorted(maximal, key=lambda n: im.unit_key(re.match(r"[abcf]-\d+", n).group(0))))))


if __name__ == "__main__":
    main(sys.argv)
