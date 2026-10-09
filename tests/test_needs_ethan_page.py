"""The generated NEEDS-ETHAN.md agrees with the answered ledger, or something fails (f-33).

Every test but the last two builds its own relay folder and its own git
repository under tmp_path. The last two read the real `_relay` and skip, naming
the folder, when it is not there (CI).
"""
import ast
import json
import os
import subprocess

import pytest

from relay import answered, items, needs_ethan as ne

CITE = {"type": "commit", "ref": "abc1234", "detail": "in main"}


def _report(relay, unit, whats, **flags):
    d = relay / "reports" / "json"
    d.mkdir(parents=True, exist_ok=True)
    (d / (unit + ".json")).write_text(json.dumps({
        "unit_id": unit, "track": unit[0],
        "needs_ethan": [dict({"what": w, "why": "because", "irreversible": False, "recommended": "do it"}, **flags)
                        for w in whats]}), encoding="utf-8")


def _g(repo, *args):
    r = subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", "-c",
                        "commit.gpgsign=false"] + list(args), capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


@pytest.fixture
def repo(tmp_path):
    """main holds a-1-first-branch; a-2-second-branch is not merged."""
    r = tmp_path / "repo"
    r.mkdir()
    _g(r, "init", "-q", "-b", "main")
    (r / "f").write_text("0")
    _g(r, "add", "f")
    _g(r, "commit", "-q", "-m", "root")
    _g(r, "branch", "a-1-first-branch")
    _g(r, "checkout", "-q", "-b", "a-2-second-branch")
    (r / "f").write_text("1")
    _g(r, "commit", "-q", "-am", "unmerged work")
    _g(r, "checkout", "-q", "main")
    return str(r)


@pytest.fixture
def relay(tmp_path, monkeypatch):
    monkeypatch.setattr(answered, "PINS", str(tmp_path / "pins.jsonl"))
    r = tmp_path / "_relay"
    _report(r, "a-1", ["Merge a-1-first-branch to origin/main",
                       "Rule on the structural red items in the staleness gate"])
    _report(r, "a-2", ["Merge a-2-second-branch to origin/main",
                       "Decide the slug form before any college URL is published"], irreversible=True)
    _report(r, "c-3", ["Choose the headline for the root page", "Pick the reserve for the forward capture"])
    return r


def _known(relay):
    return {it.id: it for it in items.load(str(relay))[0]}


def _ledger(relay):
    return str(relay / answered.LEDGER)


def _model(relay, repo, task=None):
    return ne.build(str(relay), repo, repo, task)


def _run(relay, repo, *more):
    return ne.main(["--relay", str(relay), "--main-repo", repo, "--web-repo", repo, "--no-tasks"] + list(more))


def _page(relay):
    return (relay / ne.DOC).read_text(encoding="utf-8")


def test_an_item_is_live_unless_something_outside_its_report_says_otherwise(relay, repo):
    known = _known(relay)
    answered.append(_ledger(relay), "answered", "c-3#0", "done", CITE, "f-99", None, known)
    answered.append(_ledger(relay), "answered", "c-3#1", "keep 40", {"type": "ethan", "ref": "said so"}, "ethan", None, known)
    cls = _model(relay, repo).cls
    assert cls == {"a-1#0": ne.MERGED,    # merge ask, branch in main, nobody recorded an answer
                   "a-1#1": ne.LIVE,      # nothing says otherwise
                   "a-2#0": ne.LIVE,      # merge ask, branch NOT in main
                   "a-2#1": ne.LIVE,
                   "c-3#0": ne.CITED,     # a unit closed it on a citation: never `answered`
                   "c-3#1": ne.ANSWERED}  # Ethan's own word


def test_a_reopened_item_is_live_again(relay, repo):
    known = _known(relay)
    e = answered.append(_ledger(relay), "answered", "c-3#0", "done", CITE, "f-99", None, known)
    assert _model(relay, repo).cls["c-3#0"] == ne.CITED
    answered.append(_ledger(relay), "reopened", "c-3#0", "it was not done", None, "ethan", e["entry"], known)
    assert _model(relay, repo).cls["c-3#0"] == ne.LIVE


def test_the_page_reads_back_as_the_classification_and_lists_every_item_once(relay, repo):
    answered.append(_ledger(relay), "answered", "c-3#0", "done", CITE, "f-99", None, _known(relay))
    m = _model(relay, repo)
    doc_cls, snap, appended = ne.parse(ne.render(m))
    assert doc_cls == m.cls and len(doc_cls) == 6
    assert snap["items"] == 6 and snap["reports"] == 3 and snap["ledger_head"] == "L0001"
    assert snap["counts"] == {"live": 4, "answered": 0, "merged": 1, "cited": 1}
    assert appended == 0


def test_every_item_shows_its_evidence_on_one_line(relay, repo):
    answered.append(_ledger(relay), "note", "a-2#1", "half done", CITE, "f-99", None, _known(relay))
    lines = ne.render(_model(relay, repo)).splitlines()
    ev = {l.split("raised by ")[1].split(" ")[0]: l for l in lines if l.startswith("  - evidence: raised by a-2")}
    merge_line = next(l for l in lines if "evidence" in l and "a-2-second-branch" in l)
    assert "raised by a-2 (track A)" in merge_line and "ledger: no answer" in merge_line
    assert "NOT in main by ancestry\u2020" in merge_line, "the ancestry caveat mark travels with the claim"
    assert any("ledger note L0001 (f-99)" in l and "not a merge ask" in l for l in lines)
    merged = next(l for l in lines if "evidence" in l and "a-1-first-branch" in l)
    assert "in main by ancestry" in merged and "NOT" not in merged
    assert ev, "no evidence line was found at all"
    assert sum(1 for l in lines if l.startswith("\u2020 ")) == 1, "the caveat itself is defined on the page"


def test_the_page_agrees_with_the_ledger_until_the_ledger_moves(relay, repo):
    assert _run(relay, repo, "--write") == 0
    doc_cls, _, _ = ne.parse(_page(relay))
    assert ne.disagreements(doc_cls, answered.verify(_ledger(relay))) == []
    assert _run(relay, repo, "check") == 0
    # the ledger answers an item the page calls live
    answered.append(_ledger(relay), "answered", "a-1#1", "ruled", CITE, "f-99", None, _known(relay))
    bad = ne.disagreements(doc_cls, answered.verify(_ledger(relay)))
    assert len(bad) == 1 and bad[0].startswith("a-1#1: the page says live, the ledger answers it")
    assert _run(relay, repo, "check") == 1
    assert _run(relay, repo, "--write") == 0 and _run(relay, repo, "check") == 0


def test_the_reverse_a_page_answer_the_ledger_no_longer_holds(relay, repo):
    known = _known(relay)
    e = answered.append(_ledger(relay), "answered", "c-3#0", "done", CITE, "f-99", None, known)
    assert _run(relay, repo, "--write") == 0 and _run(relay, repo, "check") == 0
    answered.append(_ledger(relay), "reopened", "c-3#0", "withdrawn", None, "ethan", e["entry"], known)
    bad = ne.disagreements(ne.parse(_page(relay))[0], answered.verify(_ledger(relay)))
    assert bad == ["c-3#0: the page says cited, the ledger has no effective answer"]
    assert _run(relay, repo, "check") == 1


def test_a_unit_closure_shown_as_ethans_answer_is_a_disagreement(relay, repo):
    answered.append(_ledger(relay), "answered", "c-3#0", "done", CITE, "f-99", None, _known(relay))
    entries = answered.verify(_ledger(relay))
    assert ne.disagreements({"c-3#0": ne.CITED}, entries) == []
    assert "recorded by f-99" in ne.disagreements({"c-3#0": ne.ANSWERED}, entries)[0]


def test_a_new_report_makes_the_page_stale_not_wrong(relay, repo):
    assert _run(relay, repo, "--write") == 0
    _report(relay, "f-4", ["Queue the follow-up unit"])
    doc_cls, _, appended = ne.parse(_page(relay))
    assert ne.disagreements(doc_cls, answered.verify(_ledger(relay))) == []
    assert ne.staleness(doc_cls, items.load(str(relay))[0], appended) == (["f-4#0"], [], 0)
    assert _run(relay, repo, "check") == 3


def test_runner_blocks_appended_below_the_end_mark_are_counted_not_classified(relay, repo):
    assert _run(relay, repo, "--write") == 0
    with open(relay / ne.DOC, "a", encoding="utf-8") as fh:
        fh.write("\n## 2026-10-08T21:00:00-04:00 - track F - f-4\n\n**What.** - **`a-1#1`** not an item line\n")
    doc_cls, _, appended = ne.parse(_page(relay))
    assert appended == 1 and len(doc_cls) == 6
    assert _run(relay, repo, "check") == 3


def test_generating_never_writes_the_ledger(relay, repo):
    answered.append(_ledger(relay), "answered", "c-3#0", "done", CITE, "f-99", None, _known(relay))
    before = open(_ledger(relay), "rb").read()
    listing = sorted(os.listdir(relay))
    assert _run(relay, repo, "--write") == 0 and _run(relay, repo, "check") == 0
    assert open(_ledger(relay), "rb").read() == before
    assert sorted(os.listdir(relay)) == sorted(listing + [ne.DOC]), "only the page may appear in the relay folder"


def test_the_module_cannot_append_to_the_ledger_or_open_anything_else_for_writing():
    tree = ast.parse(open(ne.__file__, encoding="utf-8").read())
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)]
    assert len(calls) > 50, "the walk found too few calls to be reading the module"
    appenders = [n for n in calls if isinstance(n.func, ast.Attribute) and n.func.attr == "append"
                 and isinstance(n.func.value, ast.Name) and n.func.value.id == "answered"]
    assert appenders == []
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)} | \
            {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    assert not names & {"remove", "unlink", "rename", "replace_file", "rmtree"}, "the page is overwritten in place"
    writers = []
    for fn in (n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)):
        for n in ast.walk(fn):
            if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "open" and len(n.args) > 1 \
                    and isinstance(n.args[1], ast.Constant) and set(str(n.args[1].value)) & set("wax+"):
                writers.append(fn.name)
    assert writers == ["write"]


RAW = ("# Needs Ethan\n\nAppended by the relay runners.\n\n"
       "## 2026-09-22T00:45:19-04:00 - track A - a-01\n\n**What.** Rule on the structural red items in the\n"
       "staleness gate\n\n**Why it stopped.** y\n\n**What it would have done.** z\n\n"
       "## 2026-09-23T02:28:43-04:00 - track C - c-3\n\n**What.** Requeue c-3 once a-1 is merged\n\n"
       "**Why it stopped.** first run, report since overwritten\n\n**What it would have done.** requeue\n\n"
       "## 2026-09-29T03:10-04:00 - overnight watch - proposed units (NOT queued)\n\nDrafted by the watch.\n\n"
       "## What the watch did not do\n\nIt did not push.\n")


def test_overwriting_carries_forward_what_no_report_holds(relay, repo):
    (relay / ne.DOC).write_text(RAW, encoding="utf-8")
    its = items.load(str(relay))[0]
    carried = ne.carried_blocks(RAW, its)
    assert [k for k, _ in carried] == ["runner", "other", "other"], "the a-01 block is an item and is regenerated"
    assert _run(relay, repo, "--write") == 0
    page = _page(relay)
    for text in ("Requeue c-3 once a-1 is merged", "first run, report since overwritten", "Drafted by the watch.",
                 "It did not push."):
        assert page.count(text) == 1
    assert "| carried forward, not items | 3 blocks |" in page
    assert len(ne.parse(page)[0]) == 6, "a carried block is never read as an item"
    # the runner appends below the end mark; one block is an item's, one is nobody's
    with open(relay / ne.DOC, "a", encoding="utf-8", newline="") as fh:
        fh.write("\r\n## 2026-10-08T21:00:00-04:00 - track C - c-3\r\n\r\n**What.** Choose the headline for the root page"
                 "\r\n\r\n**Why it stopped.** y\r\n\r\n**What it would have done.** z\r\n"
                 "\r\n## 2026-10-08T21:05-04:00 - overnight watch - a late note\r\n\r\nKeep me.\r\n")
    assert _run(relay, repo, "--write") == 0 and _run(relay, repo, "--write") == 0
    page = _page(relay)
    assert "| carried forward, not items | 4 blocks |" in page and page.count("Keep me.") == 1
    assert page.count("Requeue c-3 once a-1 is merged") == 1 and page.count("It did not push.") == 1
    late = ("other", "## 2026-10-08T21:05-04:00 - overnight watch - a late note\n\nKeep me.\n")
    assert sorted(ne.carried_blocks(page, its)) == sorted(carried + [late])
    # once a report holds the orphan's text it has an id, and stops being carried
    _report(relay, "c-3", ["Requeue c-3 once a-1 is merged"])
    assert _run(relay, repo, "--write") == 0
    assert "| carried forward, not items | 3 blocks |" in _page(relay)


def test_a_page_that_does_not_decode_is_not_rewritten(relay, repo):
    (relay / ne.DOC).write_bytes(b"# Needs Ethan\n\n## note \xff\xfe\n")
    with pytest.raises(UnicodeDecodeError):
        _run(relay, repo, "--write")
    assert (relay / ne.DOC).read_bytes() == b"# Needs Ethan\n\n## note \xff\xfe\n"


def test_a_page_this_did_not_write_is_refused_not_read_as_agreement(relay):
    with pytest.raises(ne.Refused):
        ne.parse("# Needs Ethan\n\n- **`a-1#0`** something\n")
    with pytest.raises(ne.Refused):
        ne.parse("")


def test_no_cluster_count_is_printed_without_its_audits(relay, repo):
    text = ne.render(_model(relay, repo))
    counted = [l for l in text.splitlines() if "clusters**" in l]
    assert len(counted) >= 2, "the page prints the cluster count in the header and in section 1.3"
    for line in counted:
        assert "7 of 43" in line and "28 of 60" in line and "not a count of distinct questions" in line
    assert "7 of 43" in ne.cluster_sentence(504, 554) and "**504 clusters**" in ne.cluster_sentence(504, 554)


def test_the_snapshot_names_the_report_count_and_the_moving_count(relay, repo):
    text = ne.render(_model(relay, repo))
    assert "from **3 machine reports**" in text and "holding **6 `needs_ethan` items**" in text
    assert "554 items in 238 reports" in text and "559 in 240" in text


def test_the_task_verdict_can_come_out_either_way():
    dev = {"execute": r"C:\Users\x\code\calibrated-sports\.venv\Scripts\python.exe", "arguments": "-m jobs.weekly_refresh",
           "cwd": r"C:\Users\x\code\calibrated-sports", "state": "Ready", "read_ts": 1791500000}
    prod = dict(dev, execute=r"C:\Users\x\code\prod\calibrated-sports\run_weekly.cmd", cwd="")
    assert ne.task_runs_from_prod(dev) is False and ne.task_runs_from_prod(prod) is True
    assert ne.task_runs_from_prod({"execute": "", "arguments": None, "cwd": None}) is None


def test_the_page_says_where_the_task_runs_or_that_it_was_not_read(relay, repo):
    dev = {"execute": r"C:\c\code\calibrated-sports\python.exe", "arguments": "-m jobs.weekly_refresh",
           "cwd": r"C:\c\code\calibrated-sports", "state": "Ready", "read_ts": 1791500000}
    assert "NOT the production clone" in ne.render(_model(relay, repo, dev))
    moved = ne.render(_model(relay, repo, dict(dev, execute=r"C:\c\code\prod\calibrated-sports\run.cmd", cwd="")))
    assert "NOT the production clone" not in moved and "**the production clone**" in moved
    assert "Task Scheduler was not read in this run" in ne.render(_model(relay, repo, None))


def test_live_order_puts_the_repoint_first_then_flagged_and_uses_each_item_once(relay, repo, monkeypatch):
    its = {it.id: it for it in items.load(str(relay))[0]}
    monkeypatch.setattr(ne, "REPOINT", {"c-3#1": its["c-3#1"].what_sha})
    m = _model(relay, repo)
    groups = ne.live_groups(m)
    assert [k for k, _ in groups] == ["repoint", "flagged", "repeated", "merges", "rest"]
    got = dict(groups)
    assert [it.id for it in got["repoint"]] == ["c-3#1"]
    assert [it.id for it in got["flagged"]] == ["a-2#0", "a-2#1"], "flagged outranks the merge group"
    assert got["merges"] == [] and sorted(it.id for it in got["rest"]) == ["a-1#1", "c-3#0"]
    text = ne.render(m)
    assert text.index("`c-3#1`") < text.index("`a-2#0`") < min(text.index("`a-1#1`"), text.index("`c-3#0`"))


def test_a_declared_same_ask_joins_the_item_it_names(relay, repo):
    _report(relay, "f-4", ["Same ask as c-3#0: settle the front page wording"])
    repeated = dict(ne.live_groups(_model(relay, repo)))["repeated"]
    assert [[it.id for it in g] for g in repeated] == [["c-3#0", "f-4#0"]]


def test_a_drifted_pin_is_said_on_the_page(relay, repo, monkeypatch):
    monkeypatch.setattr(ne, "REPOINT", {"c-3#1": "000000000000"})
    assert "**DRIFT:** c-3#1" in ne.render(_model(relay, repo))


# ---------------------------------------------------------------- the real relay
def _real_relay():
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for cand in (os.environ.get("RELAY_DIR"), os.path.join(os.path.dirname(here), "_relay")):
        if cand and os.path.isdir(os.path.join(cand, "reports", "json")):
            return cand
    return None


REAL = _real_relay()
needs_relay = pytest.mark.skipif(REAL is None, reason="no _relay folder beside this checkout and no RELAY_DIR")


@needs_relay
def test_the_real_page_agrees_with_the_real_ledger():
    """Fails when an item is answered (or reopened) and the page was not regenerated:
    run `python -m relay.needs_ethan --write`. New reports make it stale, which does not fail."""
    with open(os.path.join(REAL, ne.DOC), encoding="utf-8") as fh:
        doc_cls, snap, _ = ne.parse(fh.read())
    assert len(doc_cls) == snap["items"] >= 595
    bad = ne.disagreements(doc_cls, answered.verify(os.path.join(REAL, answered.LEDGER)))
    assert bad == [], "NEEDS-ETHAN.md disagrees with ANSWERED.jsonl; regenerate it"


@needs_relay
def test_the_hand_read_repoint_list_still_points_at_the_text_that_was_read():
    now = {it.id: it.what_sha for it in items.load(REAL)[0]}
    assert len(ne.REPOINT) == 11
    assert {i: now.get(i) for i in ne.REPOINT} == ne.REPOINT
