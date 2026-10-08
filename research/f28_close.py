"""Close what evidence closes, and nothing else (unit f-28).

    python -m research.f28_close <relay> --main-repo <clone> --web-repo <clone or mirror> [--apply]

Appends to `<relay>/ANSWERED.jsonl` through `relay.answered.append`. Without
`--apply` it prints what it would append. Re-running appends nothing new: an item
that already carries an f-28 entry of the same kind and citation is skipped.

WHAT "ANSWERED" MEANS HERE. The decision the item asked for has been TAKEN, and
something citable shows it. It does not mean it was taken the way the unit
recommended, and for a merge it does not mean the deploy that followed worked.

THREE RULES, fixed before the lists below were written:

  1. A merge ask closes when every branch it names is an ancestor of main AND
     the merge is the whole ask. "Merge order for X, Y" closes on the same
     evidence: once all of them are in main the order cannot be decided any more.
  2. A merge ask with a second half ("then run the publish", "then register the
     task") gets a NOTE for the merged half and stays open, unless the second
     half has a citation of its own.
  3. Everything else closes only on a named commit in main, a line of the relay's
     decision files, a later report, or a measurement this unit made and states.

Every git claim below is re-checked against the repositories at run time; an
entry whose evidence does not hold today is REFUSED, not appended.
"""
import os
import sys

from relay import answered, items as im
from research import f28_evidence as ev

BY = "f-28"

# Rule 1: whole-ask merges and merge-order questions, every named branch in main.
CLOSE_MERGED = """
a-08#0 a-10#1 a-16#1 a-20#0 a-22#0 a-29#0 a-30#0 a-31#0 a-32#1 a-33#0 a-34#0 a-35#0 a-36#0 a-37#1
a-42#0 a-48#0 a-53#1 a-54#0 a-56#0 a-59#2 a-60#1
b-01#1 b-02#2 b-03#0 b-05#1 b-07#0 b-08#3 b-09#3 b-15#0 b-16#2 b-19#1 b-20#0 b-22#0 b-25#0 b-26#0
b-27#0 b-28#0 b-31#2 b-34#0 b-38#0 b-39#0 b-45#0 b-47#0 b-48#1 b-50#2 b-52#0 b-53#0 b-54#0 b-55#0
b-56#0 b-76#0 b-84#0 b-87#2
c-07#2 f-03#0 f-04#1 f-05#0 f-18#0 f-19#0 f-21#0
""".split()

# Rule 1 where the text names fewer branches than the ask covers, or names one
# that must NOT be merged: (item, {branch: expected-in-main}, answer).
CLOSE_BRANCHES = [
    ("a-55#0", {"a-52-landing-fallback": True, "a-54-landing-archive-backfill": True,
                "a-55-team-projection-model": True}, "a-52, a-54 and a-55 are all in main."),
    ("b-40#0", {"b-38-firefox-knob": True, "b-40-fantasy-chips": True, "b-42-b23-rebased": True},
     "The fantasy line b-38, b-40, b-42 is all in main."),
    ("b-77#0", {"a-48-ledger-hash-chain": True, "b-77-receipts": True}, "a-48 and b-77 are both in main."),
    ("b-13#2", {"b-13-scaffolding": True}, "b-13-scaffolding is in main."),
    ("b-59#1", {"b-59-analytics-shelf": True, "b-57-fantasy-tools": True}, "b-59 and b-57 are both in main."),
    ("a-12#0", {"a-12-rebase-a05-contract": True, "a-05-coverage-predictor-kinds": False},
     "As asked: a-12 is in main and a-05 was left unmerged."),
    ("a-09#1", {"a-09-live-prices": True, "a-11-components-table": True, "a-12-rebase-a05-contract": True,
                "a-05-coverage-predictor-kinds": False},
     "a-09 and a-11 are in main; a-05 went in as its rebase a-12 (a-12#0's own text) and was left unmerged."),
    ("b-42#0", {"b-38-firefox-knob": True, "b-40-fantasy-chips": True, "b-42-b23-rebased": True,
                "b-23-ground-toggle-graphics": False},
     "As asked: b-38, b-40 and b-42 are in main and b-23 itself was not merged."),
]

# Rule 2: the merge half is done, the rest of the ask has no citation.
NOTE_MERGED = {
    "a-21#1": "the publish", "a-28#1": "analytics.vacancy --publish", "a-33#1": "the 853-key publish",
    "a-40#0": "the gated publish", "f-04#0": "the build_pfr_alias run on the live store",
    "b-29#1": "the vacancy publish", "c-11#1": "keeping /mlb comingSoon",
}

TASKS = ("Get-ScheduledTask read by f-28 on 2026-10-08: Logger (boot/logon/watchdog), Board Tick and Live "
         "Snapshot execute from code\\prod\\calibrated-sports; Prod Sync from code\\prod; Weekly Refresh from "
         "code\\calibrated-sports (the dev clone).")

# Rule 3. commits: {sha: repo} that must be ancestors of main.
EXPLICIT = [
    ("answered", "a-68#0", "commit", "d9dd3ba", {"d9dd3ba": "main", "3ca1308": "main"},
     "Merged as d9dd3ba on 2026-10-08 12:35 -0400 and the logger restarted onto it.",
     "a-73's report measures the restart: logger.log '16:35:20Z build edb5db1b5167 pid 39236', prod clone at d9dd3ba."),
    ("answered", "a-69#1", "commit", "d9dd3ba", {"d9dd3ba": "main"},
     "Same ask as a-68#0: merged as d9dd3ba and the logger restarted at 16:35:20Z.",
     "a-73's report, verified_by entry on production at d9dd3ba with logger pid 39236."),
    ("answered", "a-03#1", "commit", "b1b8891", {"b1b8891": "main"}, "b1b8891 is in main.", ""),
    ("answered", "a-38#0", "commit", "9ea08ef", {"9ea08ef": "main"}, "9ea08ef is in origin/main.", ""),
    ("answered", "f-02#0", "commit", "df56f54", {"17b99c0": "main", "df56f54": "main"},
     "17b99c0 and df56f54 are both in origin/main.", ""),
    ("answered", "f-01#0", "commit", "17b99c0", {"17b99c0": "main"},
     "ac21ebf reached origin/main as its rebase 17b99c0.",
     "The rebase is f-02#0's own statement ('17b99c0 (f-01, rebased from ac21ebf)'); ac21ebf itself is not in main."),
    ("answered", "c-02#0", "commit", "9dc5094", {"9dc5094": "main"}, "9dc5094 is in origin/main.", ""),
    ("answered", "c-03#0", "commit", "ba81999", {"ba81999": "main", "9dc5094": "main"},
     "ba81999 and 9dc5094 are both in origin/main.", ""),
    ("answered", "a-50#0", "commit", "684d6a8", {"684d6a8": "main"},
     "684d6a8 is in origin/main and in the production clone.",
     "f-28 measured: git merge-base --is-ancestor 684d6a8 HEAD rc=0 in code\\prod\\calibrated-sports, HEAD d9dd3ba."),
    ("answered", "a-65#1", "commit", "7c4fdf3", {"7c4fdf3": "main", "cd0a901": "main"},
     "The fix unit ran as a-67: bookkeeping writes never raise, the prune deletes in batches.", ""),
    ("answered", "a-65#2", "commit", "7c4fdf3", {"7c4fdf3": "main"},
     "Decided by the fix: 7c4fdf3 makes raw books follow the current week.", ""),
    ("answered", "a-65#4", "commit", "a4d55ac", {"a4d55ac": "main"},
     "Done by a-67: the outage window was reconstructed from 1-minute candles under its own source.", ""),
    ("answered", "a-65#3", "report", "f-28", {}, "The three logger tasks run from code\\prod\\calibrated-sports.", TASKS),
    ("answered", "a-07#0", "commit", "eab7d4b", {"eab7d4b": "main"},
     "Decided: production runs from code\\prod (a-10 is in main; the logger, Board tick and Live snapshot run there).",
     TASKS + " The Weekly Refresh has NOT moved; that is a-73#2 and its cluster, still open."),
    ("answered", "a-23#0", "commit", "744e539", {"744e539": "main"},
     "a-23 is in main and a Live Snapshot task runs from the production clone.", TASKS),
    ("answered", "a-67#4", "report", "a-69", {},
     "check_fit.py was moved out of the production clone by a-69.",
     "a-69#5 states the move; f-28 measured git status in code\\prod\\calibrated-sports as clean main...origin/main."),
    ("answered", "a-01#2", "report", "f-28", {}, "D:/temp/a01 no longer exists.",
     "f-28 measured on 2026-10-08: ls -d D:/temp/a01 -> No such file or directory. Who removed it is not recorded."),
    ("answered", "f-24#1", "report", "f-25", {},
     "Queued and run as f-25 (rebuild the 2026 spine).", "_relay/reports/json/f-25.json; branch f-25-rebuild-2026-spine."),
    ("answered", "f-24#4", "ledger", "ROADMAP-2026-10-08.md", {},
     "Queued as f-28: 'Build the append-only answered list it recommended.'",
     "Wave 2, Track F. This file is that list; whether the relay adopts it is f-28's own open item."),
    ("answered", "b-65#0", "ledger", "DECISIONS-2026-09-28.md §R", {},
     "Ethan: keep it - the dashed line is meant to show a projection.", "W09's line is superseded for this chart."),
    ("answered", "c-01#1", "ledger", "LEDGER.md 'Settled - do not raise again'", {},
     "Ethan has decided: no rotation, and the inline PEM stays as it is.", ""),
    ("answered", "a-10#0", "ledger", "LEDGER.md 'Settled - do not raise again'", {},
     "Ethan has decided: no rotation, and the inline PEM stays as it is.", ""),
    ("answered", "a-11#0", "ledger", "LEDGER.md 'Settled - do not raise again'", {},
     "Ethan has decided: no rotation, and the inline PEM stays as it is.", ""),
    ("answered", "a-09#0", "ledger", "LEDGER.md 23 September", {},
     "Kalshi prices on Live: dropped. a-09 is withdrawn at Ethan's call, so the publisher is not enabled.",
     "The later question of game-winner prices from the snapshot job (a-23#1, b-41#0, b-61#1) is listed 'Still open' "
     "in DECISIONS-2026-09-26.md and is not closed by this."),
    ("answered", "c-06#1", "ledger", "LEDGER.md 22 September", {},
     "Terms posture settled 22 September: silence is acceptable; refuse only explicit prohibition.", ""),
    ("note", "b-43#1", "commit", "b0f01f8", {"b0f01f8": "web"},
     "b-43-reconciled is in web main. b-36-board-phase1's tip is not; whether its content landed another way was not checked.", ""),
    ("note", "a-31#1", "report", "f-28", {},
     "A Board Tick task is registered and runs from the production clone. The BOARD_EXPORT_DIR half was not read (.env).", TASKS),
    ("note", "a-38#1", "report", "f-28", {},
     "A Board Tick task is registered and runs from the production clone. The BOARD_EXPORT_DIR half was not read (.env).", TASKS),
    ("note", "a-67#5", "report", "f-28", {},
     "A Prod Sync task is registered. The Live Snapshot deferral fix is on a-69-prod-sync, which is not in main.", TASKS),
    ("note", "a-73#2", "report", "f-28", {},
     "NOT done as of 2026-10-08: the Weekly Refresh task still executes from code\\calibrated-sports.", TASKS),
]
PUSH_NOTE = ("LEDGER.md 22 September: 'Pushing a branch is now the default, not an escalation.' That answers whether it "
             "may be pushed. The branch is NOT on the remote today, so the commits may exist on one disk only: left open.")
for _item in ("a-06#0", "c-04#1", "c-05#3", "c-09#0", "f-09#2"):
    EXPLICIT.append(("note", _item, "ledger", "LEDGER.md 22 September", {}, PUSH_NOTE, ""))


def plan(relay, main_repo, web_repo):
    rows, repos = ev.evidence(relay, main_repo, web_repo)
    by_item = {it.id: (it, found) for it, found in rows}
    refused, out = [], []

    def tip(repo_key, branch):
        for shas in repos[repo_key][3].values():
            for name, sha in shas:
                if name == branch:
                    return sha
        return None

    def anc(repo_key, sha):
        repo, main = repos[repo_key][0], repos[repo_key][1]
        return ev.git(repo, "cat-file", "-e", sha + "^{commit}")[0] == 0 and ev.is_ancestor(repo, sha, main)

    def mains():
        return "main %s, web main %s" % (repos["main"][2][:7], repos["web"][2][:7])

    for item in CLOSE_MERGED:
        it, found = by_item[item]
        if not found or not all(f[3] is True for f in found):
            refused.append((item, "a named branch is not in main today"))
            continue
        detail = "; ".join("%s %s in main" % (f[1], f[2][:7]) for f in found) + " (" + mains() + ")"
        out.append(("answered", item, {"type": "commit", "ref": found[0][2][:7], "detail": detail},
                    "Merged: every branch the ask names is in main."))
    for item, want, answer in CLOSE_BRANCHES:
        got = []
        for branch, expected in want.items():
            key = "web" if branch.startswith("b-") else "main"
            sha = tip(key, branch)
            if sha is None or anc(key, sha) is not expected:
                refused.append((item, "%s: expected in-main=%s, not what git says today" % (branch, expected)))
                break
            got.append("%s %s %s" % (branch, sha[:7], "in main" if expected else "NOT in main"))
        else:
            out.append(("answered", item, {"type": "commit", "ref": got[0].split()[1],
                                           "detail": "; ".join(got) + " (" + mains() + ")"}, answer))
    for item, rest in NOTE_MERGED.items():
        it, found = by_item[item]
        if not found or not all(f[3] is True for f in found):
            refused.append((item, "a named branch is not in main today"))
            continue
        detail = "; ".join("%s %s in main" % (f[1], f[2][:7]) for f in found)
        out.append(("note", item, {"type": "commit", "ref": found[0][2][:7], "detail": detail},
                    "The merge half is done. No citation for %s, so the item stays open." % rest))
    for kind, item, ctype, ref, commits, answer, detail in EXPLICIT:
        bad = [s for s, key in commits.items() if not anc(key, s)]
        if bad:
            refused.append((item, "commit %s is not an ancestor of main today" % ", ".join(bad)))
            continue
        if commits:
            detail = (detail + " " if detail else "") + "Ancestors of main, checked: %s (%s)." % (", ".join(commits), mains())
        out.append((kind, item, {"type": ctype, "ref": ref, "detail": detail}, answer))
    return out, refused


def main(argv):
    relay = argv[1]
    main_repo = argv[argv.index("--main-repo") + 1]
    web_repo = argv[argv.index("--web-repo") + 1]
    apply = "--apply" in argv
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    entries_planned, refused = plan(relay, main_repo, web_repo)
    seen = [e[1] + e[0] for e in entries_planned]
    if len(seen) != len(set(seen)):
        raise SystemExit("an item is listed twice with the same kind: %s" % sorted(x for x in seen if seen.count(x) > 1))
    its, _ = im.load(relay)
    known = {it.id: it for it in its}
    path = os.path.join(relay, answered.LEDGER)
    have = {(e["kind"], e["item"], e["cite"]["ref"]) for e in answered.verify(path) if e.get("by") == BY and e.get("cite")}
    wrote = skipped = 0
    for kind, item, cite, answer in entries_planned:
        if (kind, item, cite["ref"]) in have:
            skipped += 1
            continue
        if apply:
            e = answered.append(path, kind, item, answer, cite, BY, None, known)
            print("%s %-8s %-8s %s:%s" % (e["entry"], kind, item, cite["type"], cite["ref"]))
        else:
            print("would append %-8s %-8s %s:%s  %s" % (kind, item, cite["type"], cite["ref"], answer))
        wrote += 1
    for item, why in refused:
        print("REFUSED %-8s %s" % (item, why))
    n_ans = sum(1 for e in entries_planned if e[0] == "answered")
    print("%s %d (%d answered, %d notes planned), skipped %d already there, refused %d"
          % ("appended" if apply else "would append", wrote, n_ans, len(entries_planned) - n_ans, skipped, len(refused)))
    return 1 if refused else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
