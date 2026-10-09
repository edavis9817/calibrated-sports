"""Generate `<relay>/NEEDS-ETHAN.md` from the machine reports plus the answered ledger (f-33).

    python -m relay.needs_ethan                 classify and print the counts; writes nothing
    python -m relay.needs_ethan --write         overwrite <relay>/NEEDS-ETHAN.md in place
    python -m relay.needs_ethan check           does the page on disk still agree with the ledger
                                                exit 0 agrees, 1 DISAGREES, 3 agrees but is stale

    --relay DIR       else RELAY_DIR, else ../_relay beside this checkout
    --main-repo DIR   the producer repository (default: this checkout)
    --web-repo DIR    the web repository (default: ../calibratedsports-web); refused if absent
    --no-tasks        do not read Task Scheduler for the Weekly Refresh task

`git fetch` both repositories first: ancestry is asked of `origin/main` as this
clone last saw it, and the page prints the two SHAs it used.

IT READS THE LEDGER AND NEVER WRITES IT. Nothing here calls
`relay.answered.append`; the only file opened for writing is NEEDS-ETHAN.md. It
answers no item: an item's class is derived, and `answered` is reachable only
through a ledger entry that is Ethan's own word.

THE RULE is `docs/F33-needs-ethan-page-prereg.md`, committed before this file.
An item is live unless something outside its own report says otherwise; the
first of these that holds is its class:

    answered   effective ledger entry is `answered` and is Ethan's own word
    cited      effective ledger entry is `answered`, recorded by a unit on a citation
    merged     no such entry; a merge ask whose every named branch is in main by ancestry
    live       none of the above

THE COUNTS. Any cluster count this prints goes through `cluster_sentence`, which
attaches f-28's two audit figures. Do not print one any other way.
"""
import argparse
import collections
import datetime as dt
import json
import os
import re
import subprocess
import sys
import time

from relay import answered, items as im

DOC = "NEEDS-ETHAN.md"
LIVE, ANSWERED, MERGED, CITED = "live", "answered", "merged", "cited"
CLASSES = (LIVE, ANSWERED, MERGED, CITED)
END = "<!-- f-33:end-of-generated -->"
CARRIED, END_CARRIED = "<!-- f-33:carried -->", "<!-- f-33:end-carried -->"
TRAILER = ("Anything below this line was appended by the relay runner (`run/relay.ps1`) after this page was "
           "generated and is not classified. Regenerating folds it in.")
LIVE_REPORT_ABOVE = 300

# f-28's audits of the registered clustering rule, measured once, by one reader,
# at 554 items / 504 clusters (docs/F28-answered-ledger.md). Constants, not
# recomputed here: nothing in this file can grade a cluster.
F28_AUDIT = {"items": 554, "reports": 238, "clusters": 504, "multi": 43, "false_merges": 7,
             "near_sampled": 60, "near_same": 28, "later_items": 559, "later_reports": 240}

# POST HOC, f-33, 2026-10-08: a hand-read list, not a similarity rule. {item id: what_sha}.
TASK_NAME = "CalibratedSports Weekly Refresh"
REPOINT = {
    "a-08#1": "2006810bc4bb", "a-14#0": "85c7120e896e", "a-15#2": "681a87201ab1", "a-28#0": "df4a84c5522a",
    "a-33#4": "96bd2fde33ac", "a-38#2": "4630aed4eb33", "a-67#6": "b41329641a1f", "a-69#3": "7a1849df98a1",
    "a-73#2": "5ad7c4134596", "f-21#2": "d714a89dd317", "f-25#4": "267515a3db33",
}
# Read by f-33 and judged a different ask (a merge that waits on the repoint; a publish run from prod).
REPOINT_READ_NOT_SAME = ("a-16#1", "a-51#0")
_REPOINT_CANDIDATE = (re.compile(r"weekly[ _]refresh"), re.compile(r"code[\\/]+prod|production clone"))
_SAME_ASK = re.compile(r"same ask as ([abcf]-\d{1,3}#\d+)", re.I)
_REF = re.compile(r"\b([abcf])-(\d{1,3})((?:-[a-z0-9]+)*)\b")
_ITEM_LINE = re.compile(r"^- \*\*`([abcf]-\d{1,3}#\d+)`\*\*")
_CLASS_MARK = re.compile(r"^<!-- f-33:class (\w+) -->$")
_SNAPSHOT = re.compile(r"^<!-- f-33:snapshot (\{.*\}) -->$")
_RUNNER_BLOCK = re.compile(r"^## \d{4}-\d\d-\d\dT\S+ - track \w - ([abcf]-\d{1,3})\b")
_BLOCK_WHAT = re.compile(r"\*\*What\.\*\* (.*?)\n\s*\n\*\*Why it stopped", re.S)

MergeState = collections.namedtuple("MergeState", "verdict found")  # found: [(unit, branch, sha, in_main)]


class Refused(Exception):
    pass


# ---------------------------------------------------------------- evidence
def _git(repo, *args):
    r = subprocess.run(["git", "-C", repo] + list(args), capture_output=True, text=True)
    return r.returncode, r.stdout.strip()


def repo_view(repo):
    """(repo, main ref, main sha, {unit: [(branch, sha)]}) or Refused."""
    rc, _ = _git(repo, "rev-parse", "--verify", "-q", "origin/main")
    prefix, main = ("refs/remotes/origin", "origin/main") if rc == 0 else ("refs/heads", "main")
    rc, sha = _git(repo, "rev-parse", main)
    if rc != 0:
        raise Refused("%s has no %s: not a repository this can ask about merges" % (repo, main))
    rc, out = _git(repo, "for-each-ref", "--format=%(refname:short) %(objectname)", prefix)
    by_unit = collections.defaultdict(list)
    for line in out.splitlines():
        name, tip = line.split()
        short = name.split("/", 1)[1] if name.startswith("origin/") else name
        m = re.match(r"([abcf])-?(\d{1,3})(?:-|$)", short)
        if m:
            by_unit["%s-%d" % (m.group(1), int(m.group(2)))].append((short, tip))
    if not by_unit:
        raise Refused("no unit branches under %s in %s - an empty ref list would read as 'nothing merged'" % (prefix, repo))
    return repo, main, sha, by_unit


def merge_states(its, repos):
    """{item id: MergeState} for merge-class items. `repos` is {"main": repo_view, "web": repo_view}."""
    cache, out = {}, {}

    def anc(key, sha):
        if (key, sha) not in cache:
            repo, main = repos[key][0], repos[key][1]
            cache[(key, sha)] = _git(repo, "merge-base", "--is-ancestor", sha, main)[0] == 0
        return cache[(key, sha)]

    for it in its:
        if not im.is_merge_class(it.what):
            continue
        named = ["%s-%d" % (m.group(1), int(m.group(2))) for m in _REF.finditer(it.what.lower())] or [it.unit]
        found = []
        for unit in dict.fromkeys(named):
            key = "web" if unit[0] == "b" else "main"
            for name, sha in repos[key][3].get(unit, []) or [(None, None)]:
                found.append((unit, name, sha, None if sha is None else anc(key, sha)))
        states = [f[3] for f in found]
        verdict = ("ALL-MERGED" if states and all(s is True for s in states) else
                   "NO-BRANCH" if all(s is None for s in states) else
                   "NONE-MERGED" if not any(s is True for s in states) else "SOME-MERGED")
        out[it.id] = MergeState(verdict, found)
    return out


def unmerged_tips(merge, repos, only=None):
    """POST HOC (f-28's block): {repo key: (n unmerged named branches, [tips that cover them])}."""
    out = {}
    for key in ("main", "web"):
        tips = {}
        for item, ms in merge.items():
            if only is not None and item not in only:
                continue
            for unit, name, sha, in_main in ms.found:
                if in_main is False and ("web" if unit[0] == "b" else "main") == key:
                    tips[name] = sha
        repo = repos[key][0]
        top = [n for n, s in tips.items()
               if not any(o != n and so != s and _git(repo, "merge-base", "--is-ancestor", s, so)[0] == 0
                          for o, so in tips.items())]
        out[key] = (len(tips), sorted(top, key=lambda n: im.unit_key(re.match(r"[abcf]-\d+", n).group(0))))
    return out


def probe_task(name=TASK_NAME):
    """What Task Scheduler runs for `name`, read now. None when it cannot be read."""
    cmd = ("$t = Get-ScheduledTask -TaskName '%s' -ErrorAction Stop; $t.Actions | ForEach-Object "
           "{ @{execute=$_.Execute; arguments=$_.Arguments; cwd=$_.WorkingDirectory; state=[string]$t.State} "
           "| ConvertTo-Json -Compress }" % name)
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", cmd],
                           capture_output=True, text=True, timeout=60)
        row = json.loads(r.stdout.strip().splitlines()[0])
    except (OSError, ValueError, IndexError, subprocess.TimeoutExpired):
        return None
    row["read_ts"] = int(time.time())
    return row


def task_runs_from_prod(task):
    """True / False from the task's own paths; None when there is no path to read."""
    where = " ".join(str(task.get(k) or "") for k in ("execute", "arguments", "cwd")).lower().replace("/", "\\")
    if not where.strip():
        return None
    return "\\code\\prod\\" in where


# ---------------------------------------------------------------- classify
def is_ethans_word(entry):
    return entry.get("by") == "ethan" or (entry.get("cite") or {}).get("type") == "ethan"


def classify(its, entries, merge):
    """{item id: class}. `merge` is {item id: MergeState} for merge-class items."""
    last, _ = answered.state(entries)
    out = {}
    for it in its:
        e = last.get(it.id)
        if e is not None and e["kind"] == "answered":
            out[it.id] = ANSWERED if is_ethans_word(e) else CITED
        elif it.id in merge and merge[it.id].verdict == "ALL-MERGED":
            out[it.id] = MERGED
        else:
            out[it.id] = LIVE
    return out


def cluster_sentence(n_clusters, n_items, at_040=None, at_060=None, scope="today's"):
    """The only way a cluster count is printed: with f-28's audits attached."""
    a = F28_AUDIT
    sens = " (%d at 0.40, %d at 0.60)" % (at_040, at_060) if at_040 is not None else ""
    return ("The registered text rule (idf cosine on `what` >= 0.50, committed in 3d97b5d before any pair was "
            "scored) groups %s %d items into **%d clusters**%s. **That is not a count of distinct questions.** "
            "f-28 audited the rule at %d items / %d clusters and it is wrong in both directions: **%d of %d** "
            "multi-item clusters were false merges, and **%d of %d** sampled near-miss pairs were the same "
            "question. One reader graded both audits, that reader wrote the rule, and neither audit has been "
            "repeated on the items raised since."
            % (scope, n_items, n_clusters, sens, a["items"], a["clusters"], a["false_merges"], a["multi"],
               a["near_same"], a["near_sampled"]))


Model = collections.namedtuple(
    "Model", "items n_reports entries cls merge repos task generated_ts newest_report clusters sens pins")


def build(relay, main_repo, web_repo, task="probe", now=None):
    its, n_reports = im.load(relay)
    entries = answered.verify(os.path.join(relay, answered.LEDGER))
    pins = answered.check_pins(entries, answered.read_pins())
    repos = {"main": repo_view(main_repo), "web": repo_view(web_repo)}
    merge = merge_states(its, repos)
    sims = im.similarities(its)
    clusters = im.cluster(its, im.THRESHOLD, sims)
    sens = tuple(len(im.cluster(its, t, sims)) for t in im.SENSITIVITY)
    files = [os.path.join(relay, "reports", "json", f) for f in os.listdir(os.path.join(relay, "reports", "json"))
             if f.endswith(".json")]
    newest = max(files, key=os.path.getmtime)
    return Model(its, n_reports, entries, classify(its, entries, merge), merge, repos,
                 probe_task() if task == "probe" else task, int(time.time() if now is None else now),
                 (os.path.basename(newest), os.path.getmtime(newest)), clusters, sens, pins)


# ---------------------------------------------------------------- render
def _when(ts, fmt="%Y-%m-%d"):
    return dt.datetime.fromtimestamp(ts).astimezone().strftime(fmt)


def _flat(text):
    return re.sub(r"\s+", " ", text or "").strip() or "(empty)"


def _merge_text(ms):
    parts = []
    for unit, name, sha, in_main in ms.found:
        if sha is None:
            parts.append("%s: no branch of that name on the remote" % unit)
        elif in_main:
            parts.append("`%s` %s in main by ancestry" % (name, sha[:7]))
        else:
            parts.append("`%s` %s NOT in main by ancestry†" % (name, sha[:7]))
    return "merge ask: " + "; ".join(parts)


def evidence_line(it, m, last, notes):
    e = last.get(it.id)
    bits = ["raised by %s (track %s) %s" % (it.unit, (it.track or "?").upper(), _when(it.raised_ts))]
    if e is not None and e["kind"] == "answered":
        bits.append("ledger: %s answered, recorded by %s, citing %s `%s` - \"%s\""
                    % (e["entry"], e["by"], e["cite"]["type"], e["cite"]["ref"], _flat(e["answer"])))
    elif e is not None:
        bits.append("ledger: %s REOPENED by %s - \"%s\"" % (e["entry"], e["by"], _flat(e["answer"])))
    else:
        bits.append("ledger: no answer")
    for n in notes.get(it.id, []):
        bits.append("ledger note %s (%s): \"%s\"" % (n["entry"], n["by"], _flat(n["answer"])))
    bits.append(_merge_text(m.merge[it.id]) if it.id in m.merge else "not a merge ask")
    return " · ".join(bits)


def _item(it, m, last, notes, why=False):
    flags = "".join(" **[%s]**" % f for f, on in (("IRREVERSIBLE", it.irreversible), ("SPENDS MONEY", it.spends_money)) if on)
    out = ["- **`%s`**%s %s" % (it.id, flags, _flat(it.what))]
    if m.cls[it.id] in (LIVE, MERGED):
        if why:
            out.append("  - why it stopped: %s" % _flat(it.why))
        out.append("  - would do: %s" % _flat(it.recommended))
    out.append("  - evidence: %s" % evidence_line(it, m, last, notes))
    return out


def live_groups(m):
    """The live items in the pre-registered order: [(key, [Item] or [[Item]])]."""
    live = [it for it in m.items if m.cls[it.id] == LIVE]
    taken = set()

    def take(group):
        group = [it for it in group if it.id not in taken]
        taken.update(it.id for it in group)
        return group

    newest = lambda it: (-it.raised_ts, im.unit_key(it.unit), it.index)
    oldest = lambda it: (it.raised_ts, im.unit_key(it.unit), it.index)
    repoint = take(sorted((it for it in live if it.id in REPOINT), key=oldest))
    flagged = take(sorted((it for it in live if it.irreversible or it.spends_money), key=newest))
    # declared duplicates join the cluster of the item they name
    by_id = {it.id: it for it in m.items}
    groups = [list(g) for g in m.clusters]
    home = {it.id: n for n, g in enumerate(groups) for it in g}
    for it in m.items:
        d = _SAME_ASK.search(it.what)
        if d and d.group(1) in by_id and home[d.group(1)] != home[it.id]:
            src, dst = home[it.id], home[d.group(1)]
            groups[dst].extend(groups[src])
            for moved in groups[src]:
                home[moved.id] = dst
            groups[src] = []
    repeated = []
    for g in groups:
        g_live = [it for it in g if m.cls[it.id] == LIVE and it.id not in taken]
        if len(g_live) >= 2:
            repeated.append(sorted(g_live, key=oldest))
    repeated.sort(key=lambda g: oldest(g[0]))
    for g in repeated:
        take(g)
    merges = take(sorted((it for it in live if it.id in m.merge), key=newest))
    rest = take(sorted(live, key=newest))
    return [("repoint", repoint), ("flagged", flagged), ("repeated", repeated), ("merges", merges), ("rest", rest)]


def repoint_candidates(m):
    return [it.id for it in m.items
            if it.id not in REPOINT and it.id not in REPOINT_READ_NOT_SAME
            and all(p.search(it.what.lower()) for p in _REPOINT_CANDIDATE)]


def render(m, carried=()):
    last, notes = answered.state(m.entries)
    by_id = {it.id: it for it in m.items}
    n = collections.Counter(m.cls.values())
    head = m.entries[-1] if m.entries else None
    verdicts = collections.Counter(ms.verdict for ms in m.merge.values())
    groups = dict(live_groups(m))
    snap = {"generated_ts": m.generated_ts, "reports": m.n_reports, "items": len(m.items),
            "population": im.population_hash(m.items), "ledger_entries": len(m.entries),
            "ledger_head": head["entry"] if head else None, "ledger_head_sha": head["_line_sha"] if head else None,
            "main": m.repos["main"][2][:7], "web_main": m.repos["web"][2][:7],
            "counts": {c: n.get(c, 0) for c in CLASSES}}
    a = F28_AUDIT
    L = ["<!-- f-33:snapshot %s -->" % json.dumps(snap, sort_keys=True), "# Needs Ethan", ""]
    L += [
        "**A generated page. Do not edit it; regenerate it** (`python -m relay.needs_ethan --write`, repo "
        "calibrated-sports, branch f-33-needs-ethan-page). It answers nothing: the only place an item is recorded "
        "as answered is `ANSWERED.jsonl`, through `python -m relay.answered answer --item <id> ... --by ethan`.",
        "",
        "**Snapshot.** Generated %s from **%d machine reports** (newest `%s`, written %s) holding **%d "
        "`needs_ethan` items** (population hash `%s`); ledger `ANSWERED.jsonl` at %s (%d entries, head `%s`, %d "
        "pin checked); producer `origin/main` %s, web `origin/main` %s, each as last fetched."
        % (_when(m.generated_ts, "%Y-%m-%d %H:%M %z"), m.n_reports, m.newest_report[0],
           _when(m.newest_report[1], "%Y-%m-%d %H:%M"), len(m.items), snap["population"],
           snap["ledger_head"], len(m.entries), snap["ledger_head_sha"], m.pins, snap["main"], snap["web_main"]),
        "",
        "**This is a count that moves while you look at it.** It was %d items in %d reports at f-28's registered "
        "run and %d in %d about twenty minutes later; it is %d in %d here. `python -m relay.needs_ethan check` "
        "says whether this page is still current."
        % (a["items"], a["reports"], a["later_items"], a["later_reports"], len(m.items), m.n_reports),
        "",
        "| | items | what puts an item here |",
        "|---|---|---|",
        "| **1. Live** | **%d** | nothing outside its own report says it is settled |" % n[LIVE],
        "| 2. Answered in the ledger | %d | a ledger entry that is Ethan's own word |" % n[ANSWERED],
        "| 3. Overtaken by a merge | %d | a merge ask, every named branch in `main` by ancestry, no ledger answer |" % n[MERGED],
        "| 4. Closed on a citation, nobody answering | %d | a unit recorded it answered in the ledger, citing a commit, a report or a decision file |" % n[CITED],
        "| total | %d | |" % len(m.items),
        "| carried forward, not items | %d blocks | section 5: text on the previous page that no machine report holds |" % len(carried),
        "",
    ]
    if n[LIVE] > LIVE_REPORT_ABOVE:
        L += ["**%d live items is more than %d.** The rule was fixed before the generator ran "
              "(`docs/F33-needs-ethan-page-prereg.md`) and was not tightened to shorten this page."
              % (n[LIVE], LIVE_REPORT_ABOVE), ""]
    live_clusters = sum(1 for g in m.clusters if any(m.cls[it.id] == LIVE for it in g))
    L += [cluster_sentence(len(m.clusters), len(m.items), m.sens[0], m.sens[1]),
          "The %d live items fall in %d of those clusters, and the same caveat applies to that figure." % (n[LIVE], live_clusters),
          "",
          "**What shrinks the pile is evidence, not deduplication.** Of the %d merge-class items (a kind of ask), "
          "git says: every named branch in main %d, some %d, none %d, no branch of that name on the remote %d. "
          "And %d items are closed in the ledger without Ethan answering anything - section 4."
          % (len(m.merge), verdicts["ALL-MERGED"], verdicts["SOME-MERGED"], verdicts["NONE-MERGED"],
             verdicts["NO-BRANCH"], n[CITED]),
          "",
          "† **Ancestry is the only merge test on this page.** A branch whose commits reached `main` under "
          "another name - a rebase (a-05 landed as a-12) or a cherry-pick - reads NOT in main here, so a live "
          "merge ask may already be done. \"In main by ancestry\" is exact; \"NOT in main by ancestry\" is not "
          "proof the work is missing.",
          "",
          "Item ids are `<unit>#<index>`: the report `reports/json/<unit>.json` and the 0-based position in its "
          "`needs_ethan` array. Dates are when the unit's run was logged, in this machine's local time.",
          ""]

    # ---- 1. live
    L += ["## 1. Live - %d items" % n[LIVE], "<!-- f-33:class live -->", "",
          "Ordered by what can be acted on, not by unit and not by cluster size: the Weekly Refresh repoint; then "
          "what a report flagged irreversible or as spending money; then asks raised more than once; then merges "
          "not in main; then everything else, newest first. Each item appears once.", ""]
    rp = groups["repoint"]
    L += ["### 1.1 Repoint the Weekly Refresh task at the production clone - %d live items" % len(rp), ""]
    if rp:
        L += ["Raised **%s through %s** by %d units. One action - changing the task's Execute and WorkingDirectory "
              "- settles every item below, or the part of it still undone."
              % (_when(min(it.raised_ts for it in rp)), _when(max(it.raised_ts for it in rp)),
                 len({it.unit for it in rp})), ""]
    if m.task is None:
        L += ["**Task Scheduler was not read in this run**, so this page does not say where the task runs from.", ""]
    else:
        prod = task_runs_from_prod(m.task)
        where = ("**the production clone**" if prod else "**NOT the production clone - it still runs from the dev clone**"
                 if prod is False else "**a location this page could not read**")
        L += ["Task Scheduler, read %s: `%s` executes `%s %s` with working directory `%s` (state %s) - %s."
              % (_when(m.task["read_ts"], "%Y-%m-%d %H:%M"), TASK_NAME, m.task.get("execute") or "",
                 m.task.get("arguments") or "", m.task.get("cwd") or "(none)", m.task.get("state") or "?", where), ""]
    drift = [i for i, sha in REPOINT.items() if i not in by_id or by_id[i].what_sha != sha]
    gone_live = [i for i in REPOINT if i in by_id and m.cls[i] != LIVE]
    cands = repoint_candidates(m)
    L += ["POST HOC (f-33, 2026-10-08): this group is a hand-read list of %d item ids, not the registered "
          "clustering rule, which does not form it. f-28 reported \"nine items\" without listing them; f-33 read "
          "the candidates and lists %d. Three (a-08#1, a-33#4, f-21#2) name several scheduled tasks; the logger "
          "tasks have since moved, so the Weekly Refresh is what remains of them."
          % (len(REPOINT), len(REPOINT)), ""]
    if drift:
        L += ["**DRIFT:** %s no longer hash to the text f-33 read; re-read before trusting this group." % ", ".join(sorted(drift)), ""]
    if gone_live:
        L += ["On the list and no longer live (see their own sections): %s." % ", ".join(sorted(gone_live)), ""]
    if cands:
        L += ["**Not on the list, and mentioning both the Weekly Refresh and the production clone - unread:** %s."
              % ", ".join(cands), ""]
    for it in rp:
        L += _item(it, m, last, notes, why=True)
    L += [""]

    fl = groups["flagged"]
    L += ["### 1.2 Flagged irreversible or spending money by the report that raised them - %d" % len(fl), ""]
    for it in fl:
        L += _item(it, m, last, notes, why=True)
    L += [""]

    rep = groups["repeated"]
    L += ["### 1.3 Raised more than once - %d items in %d groups" % (sum(len(g) for g in rep), len(rep)), "",
          "Groups are the registered clusters holding two or more live items, longest-waiting first (plus any item "
          "declaring `same ask as <id>`). " + cluster_sentence(len(m.clusters), len(m.items)) +
          " Read each group as \"probably one question\", not as one.", ""]
    for g in rep:
        first, final = _when(g[0].raised_ts), _when(max(it.raised_ts for it in g))
        L += ["**%s, %d items**" % (first if first == final else "%s through %s" % (first, final), len(g)), ""]
        for it in g:
            L += _item(it, m, last, notes, why=True)
        L += [""]

    mg = groups["merges"]
    L += ["### 1.4 Merge asks whose branches are not all in main - %d" % len(mg), ""]
    tips = unmerged_tips(m.merge, m.repos, {it.id for it in m.items if m.cls[it.id] == LIVE})
    L += ["POST HOC (f-28's count, recomputed over the live merge asks): the branches these name that are not in "
          "main are **%d in the producer repository, covered by %d tips** (%s), and **%d in the web repository, "
          "covered by %d tips** (%s). A merge of a tip brings in every branch beneath it. † applies: a branch "
          "that landed as a rebase or cherry-pick is counted here as unmerged."
          % (tips["main"][0], len(tips["main"][1]), ", ".join("`%s`" % t for t in tips["main"][1]) or "none",
             tips["web"][0], len(tips["web"][1]), ", ".join("`%s`" % t for t in tips["web"][1]) or "none"), ""]
    for it in mg:
        L += _item(it, m, last, notes)
    L += [""]

    rest = groups["rest"]
    L += ["### 1.5 Everything else - %d, newest first" % len(rest), ""]
    day = None
    for it in rest:
        d = _when(it.raised_ts)
        if d != day:
            L += ["", "**%s**" % d, ""]
            day = d
        L += _item(it, m, last, notes)
    L += [""]

    # ---- 2. answered
    ans = [it for it in m.items if m.cls[it.id] == ANSWERED]
    L += ["## 2. Answered in the ledger - %d items" % len(ans), "<!-- f-33:class answered -->", "",
          "An item is here only when its ledger entry is Ethan's own word (recorded `--by ethan`, or citing type "
          "`ethan`).%s" % (" **None is.** Every one of the %d ledger entries was recorded by a unit; see section 4."
                           % len(m.entries) if not ans else ""), ""]
    for it in sorted(ans, key=lambda it: -last[it.id]["recorded_ts"]):
        L += _item(it, m, last, notes)
    L += [""]

    # ---- 3. merged
    mer = [it for it in m.items if m.cls[it.id] == MERGED]
    noted = [it for it in mer if notes.get(it.id)]
    L += ["## 3. Overtaken by a merge - %d items" % len(mer), "<!-- f-33:class merged -->", "",
          "Merge asks with no ledger answer whose every named branch is an ancestor of `main` today. Nobody "
          "recorded these as answered; git says the merge happened. **The merge is all that git shows.** %d of "
          "the %d carry a ledger note that the ask had a second half with no evidence behind it, and are listed "
          "first. Read the text of every one: an ask that says \"merge, then run X\" is here on the merge alone, "
          "and X may not have happened."
          % (len(noted), len(mer)), ""]
    for it in sorted(mer, key=lambda it: (not notes.get(it.id), -it.raised_ts)):
        L += _item(it, m, last, notes)
    L += [""]

    # ---- 4. cited
    cit = [it for it in m.items if m.cls[it.id] == CITED]
    L += ["## 4. Closed on a citation, nobody answering - %d items" % len(cit), "<!-- f-33:class cited -->", "",
          "**This is the relay closing its own questions.** Each item below is recorded `answered` in "
          "`ANSWERED.jsonl` by a unit, not by Ethan, on the strength of something citable. \"Answered\" there "
          "means the decision was taken and something shows it - not that it went the way the unit recommended, "
          "and for a merge not that the deploy after it worked. To withdraw one: `python -m relay.answered reopen "
          "--item <id> --answer \"why\" --supersedes <L-entry> --by ethan`.", ""]

    def kind(it):
        e = last[it.id]
        t = e["cite"]["type"]
        if t == "report":
            return 0
        if t == "commit":
            return 3 if it.id in m.merge else 1
        return 2
    titles = ["4.1 On a later report, or a measurement a unit made",
              "4.2 On a commit in main, for an ask that was not a merge",
              "4.3 On a line of a decision file (LEDGER.md, DECISIONS) - Ethan's word as the manager wrote it down, entered by a unit",
              "4.4 On a commit in main, for a merge ask"]
    for k, title in enumerate(titles):
        sub = sorted((it for it in cit if kind(it) == k), key=lambda it: (im.unit_key(it.unit), it.index))
        L += ["### %s - %d" % (title, len(sub)), ""]
        for it in sub:
            L += _item(it, m, last, notes)
        L += [""]

    # ---- 5. carried
    orphans = [b for kind, b in carried if kind == "runner"]
    others = [b for kind, b in carried if kind != "runner"]
    L += ["## 5. Carried forward verbatim - %d blocks that are not items" % len(carried), "",
          "Text that was on this page before it was regenerated and that no machine report holds. The generator "
          "does not classify it and will not drop it: overwriting was the only way to lose it.", "",
          "### 5.1 Raised by a run whose machine report was later overwritten - %d" % len(orphans), "",
          "A unit that runs twice keeps one `reports/json/<unit>.json`, so the first run's asks survive only "
          "here. **Live by the rule, and outside every count above:** with no report behind it such a block has "
          "no item id and cannot be answered in the ledger.", "", CARRIED]
    L += [b.rstrip("\n") + "\n" for b in orphans] + [END_CARRIED, "",
          "### 5.2 Everything else - %d" % len(others), "",
          "Written into the old page by something other than the runner (the overnight watch's notes and "
          "proposals). Not `needs_ethan` items, never counted as such.", "", CARRIED]
    L += [b.rstrip("\n") + "\n" for b in others] + [END_CARRIED, "", END, "", TRAILER, ""]
    return "\n".join(L)


# ---------------------------------------------------------------- read back, check
def parse(text):
    """({item id: class}, snapshot dict, n runner blocks below the end mark). Raises Refused on a page
    this did not generate - an unparsed page must not read as 'no disagreement'."""
    lines = text.splitlines()
    snap = _SNAPSHOT.match(lines[0]) if lines else None
    if snap is None or END not in lines:
        raise Refused("not a page this generator wrote: no f-33 snapshot line or no end mark")
    end = lines.index(END)
    stop = lines.index(CARRIED) if CARRIED in lines else end
    cls, current = {}, None
    for line in lines[:stop]:
        mk = _CLASS_MARK.match(line)
        if mk:
            current = mk.group(1)
            if current not in CLASSES:
                raise Refused("unknown class mark %r" % current)
            continue
        it = _ITEM_LINE.match(line)
        if it:
            if current is None:
                raise Refused("item %s appears before any section" % it.group(1))
            if it.group(1) in cls:
                raise Refused("item %s appears twice" % it.group(1))
            cls[it.group(1)] = current
    if not cls:
        raise Refused("the page lists no item at all")
    appended = sum(1 for line in lines[end:] if _RUNNER_BLOCK.match(line))
    return cls, json.loads(snap.group(1)), appended


def disagreements(doc_cls, entries):
    """Where the page and the ledger differ. Empty means they agree."""
    last, _ = answered.state(entries)
    out = []
    for item, c in sorted(doc_cls.items(), key=lambda kv: (im.unit_key(kv[0].split("#")[0]), kv[0])):
        e = last.get(item)
        closed = e is not None and e["kind"] == "answered"
        if c in (LIVE, MERGED) and closed:
            out.append("%s: the page says %s, the ledger answers it (%s)" % (item, c, e["entry"]))
        elif c in (ANSWERED, CITED) and not closed:
            out.append("%s: the page says %s, the ledger has no effective answer" % (item, c))
        elif closed and (c == ANSWERED) != is_ethans_word(e):
            out.append("%s: the page says %s, but %s was recorded by %s citing %s"
                       % (item, c, e["entry"], e["by"], e["cite"]["type"]))
    return out


def staleness(doc_cls, its, appended=0):
    now = {it.id for it in its}
    return sorted(now - set(doc_cls)), sorted(set(doc_cls) - now), appended


def read_page(path):
    """The page as text, "" when absent. Strict UTF-8: a page that does not decode is not rewritten."""
    if not os.path.exists(path):
        return ""
    with open(path, encoding="utf-8-sig", newline="") as fh:
        return fh.read().replace("\r\n", "\n")


def carried_blocks(text, its):
    """[(kind, block)] to carry onto the next page: every `## ` block of the existing page
    that the machine reports do not hold. A runner block whose (unit, what) is an item is
    regenerated from its report and dropped here; everything else is kept, in file order.
    Absence fails toward keeping: a block this cannot read is carried, never discarded."""
    if not text.strip():
        return []
    lines = text.split("\n")
    if lines and _SNAPSHOT.match(lines[0]) and END in lines:      # a page this generator wrote
        src, inside = [], False
        for line in lines[:lines.index(END)]:
            if line == CARRIED:
                inside = True
            elif line == END_CARRIED:
                inside = False
            elif inside:
                src.append(line)
        tail = "\n".join(lines[lines.index(END) + 1:]).replace(TRAILER, "", 1)
        source = "\n".join(src) + "\n" + tail
    else:
        first = next((n for n, l in enumerate(lines) if l.startswith("## ")), len(lines))
        source = "\n".join(lines[first:])       # the raw log's preamble is the runner's boilerplate
    have = collections.defaultdict(set)
    for it in its:
        have[im.unit_key(it.unit)].add(_flat(it.what))
    out = []
    for block in re.split(r"(?m)^(?=## )", source):
        if not block.strip():
            continue
        block = block.rstrip("\n") + "\n"
        head = _RUNNER_BLOCK.match(block)
        what = _BLOCK_WHAT.search(block)
        if head and what:
            if _flat(what.group(1)) in have[im.unit_key(head.group(1))]:
                continue
            out.append(("runner", block))
        else:
            out.append(("other", block))
    return out


def write(path, text):
    """In place: truncate and write. Never delete-and-recreate, never rename over."""
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def main(argv=None):
    p = argparse.ArgumentParser(prog="python -m relay.needs_ethan", description=__doc__.split("\n\n")[0])
    p.add_argument("command", nargs="?", default="generate", choices=("generate", "check"))
    p.add_argument("--relay")
    p.add_argument("--main-repo")
    p.add_argument("--web-repo")
    p.add_argument("--write", action="store_true")
    p.add_argument("--no-tasks", action="store_true")
    a = p.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    relay = answered.relay_dir(a.relay)
    path = os.path.join(relay, DOC)
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    try:
        if a.command == "check":
            with open(path, encoding="utf-8") as fh:
                doc_cls, snap, appended = parse(fh.read())
            its, n_reports = im.load(relay)
            entries = answered.verify(os.path.join(relay, answered.LEDGER))
            bad = disagreements(doc_cls, entries)
            new, gone, appended = staleness(doc_cls, its, appended)
            print("page: %d items from %d reports, ledger at %s; now: %d items in %d reports, ledger at %s"
                  % (len(doc_cls), snap["reports"], snap["ledger_head"], len(its), n_reports,
                     entries[-1]["entry"] if entries else None))
            for line in bad:
                print("DISAGREES " + line)
            if new or gone or appended:
                print("STALE: %d items raised since (%s), %d on the page no longer in the reports, %d runner blocks appended"
                      % (len(new), ", ".join(new[:12]) + (" ..." if len(new) > 12 else ""), len(gone), appended))
            if not bad and not (new or gone or appended):
                print("agrees with the ledger and is current")
            return 1 if bad else 3 if (new or gone or appended) else 0
        web = a.web_repo or os.path.join(os.path.dirname(here), "calibratedsports-web")
        if not os.path.isdir(web):
            raise Refused("no web repository at %s: pass --web-repo" % web)
        m = build(relay, a.main_repo or here, web, None if a.no_tasks else "probe")
        carried = carried_blocks(read_page(path), m.items)
        text = render(m, carried)
        if sorted(carried_blocks(text, m.items)) != sorted(carried):
            raise Refused("the carried blocks do not survive a regeneration unchanged; nothing written")
        doc_cls, _, _ = parse(text)
        if doc_cls != m.cls:
            raise Refused("the rendered page does not read back as the classification that produced it")
        n = collections.Counter(m.cls.values())
        print("%d items in %d reports: %s" % (len(m.items), m.n_reports, ", ".join("%s %d" % (c, n[c]) for c in CLASSES)))
        for key, group in live_groups(m):
            print("  live/%-8s %d" % (key, sum(len(g) for g in group) if key == "repeated" else len(group)))
        print("  carried forward: %d blocks no machine report holds (%d runner, %d other)"
              % (len(carried), sum(1 for k, _ in carried if k == "runner"), sum(1 for k, _ in carried if k != "runner")))
        if a.write:
            write(path, text)
            print("wrote %s (%d lines)" % (path, text.count("\n") + 1))
        else:
            print("nothing written (pass --write)")
        return 0
    except answered.LedgerBroken as exc:
        print("LEDGER BROKEN: %s" % exc, file=sys.stderr)
        return 2
    except Refused as exc:
        print("REFUSED: %s" % exc, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
