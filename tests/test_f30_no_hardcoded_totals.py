"""f-34: no script in research/f30_cfb_build/ carries a tree total, and the census refuses to guess one.

f-30's tree_census.py kept a-72's totals and file counts as literals. a-78 restaged the tree
(14,327 player pages became 14,310, undeclared 1 became 0) and the script printed DOES NOT
REPRODUCE on a tree it had just verified - and exited 0. The comparison now takes its
baseline from --baseline. This file is what stops the literal coming back.

Three shapes are walked, by AST, over every .py in the directory:

  dict      a dict literal whose every value is a number (or a tuple / dict of numbers)
  compare   a comparison against a numeric literal of 1,000 or more
  text      a string - docstrings included - that spells a count with a thousands comma

A literal that is not a tree total is allowed BY NAME, with its reason, and an allowance
that no longer matches anything fails: the list cannot outlive what it excuses.

A guard asserts only over the shapes it walks. A total under 1,000 in a comparison, one
assembled from parts, or one passed as a bare argument is not seen here.
No store, no network.
"""
import ast
import importlib.util
import json
import os
import re
import subprocess
import sys

import pytest

HERE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "research", "f30_cfb_build")
FLOOR = 1000
THOUSANDS = re.compile(r"(?<![\w.])\d{1,3}(?:,\d{3})+(?![\w])")

# (file, shape, what) -> why it is not a tree total a verdict hangs on
ALLOWED = {
    ("tree_census.py", "dict", "{'targets': 1}"):
        "the synthetic plant's one stat row; no real tree is described",
    ("tree_census.py", "text", "2,000"):
        "the size of the synthetic plant tree the script builds itself",
    ("tree_census.py", "compare", "1234"):
        "which synthetic plant file carries the rare key",
    ("attack_c39_prices.py", "text", "9,652"):
        "docstring prose quoting c-39's published n for 2b; compared with nothing",
    ("slug_evidence.py", "compare", "10000"):
        "a sanity floor on the index read: its failure is an AssertionError, not a verdict. "
        "Listed by f-34 and left alone - a-78 ran this file unmodified and it must stay so",
    ("slug_evidence.py", "text", "14,327"):
        "docstring prose dating a-72's page count; compared with nothing",
    ("identity.py", "compare", "1000"):
        "a sanity floor on the NFL season-model row count; an AssertionError, not a verdict",
    ("part1_c39.py", "text", "9,600"):
        "docstring: the size of c-39's comparator grid, a property of the target's code",
    ("attack_c39_prices.py", "text", "9,822"):
        "a printed annotation beside the sign check (c-39's own n); compared with nothing",
}


def _numeric(node):
    if isinstance(node, ast.Constant):
        return isinstance(node.value, (int, float)) and not isinstance(node.value, bool)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
        return _numeric(node.operand)
    if isinstance(node, ast.Tuple):
        return bool(node.elts) and all(_numeric(e) for e in node.elts)
    if isinstance(node, ast.Dict):
        return bool(node.values) and all(_numeric(v) for v in node.values)
    return False


def findings(source, name):
    """-> sorted [(file, shape, what, line)] for every literal of the three shapes."""
    found, inner = [], set()
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict) and _numeric(node) and id(node) not in inner:
            inner.update(id(n) for n in ast.walk(node) if n is not node)
            found.append((name, "dict", ast.unparse(node), node.lineno))
        elif isinstance(node, ast.Compare):
            for side in [node.left, *node.comparators]:
                if isinstance(side, ast.Constant) and isinstance(side.value, (int, float)) \
                        and not isinstance(side.value, bool) and abs(side.value) >= FLOOR:
                    found.append((name, "compare", repr(side.value), node.lineno))
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            for m in THOUSANDS.findall(node.value):
                found.append((name, "text", m, node.lineno))
    return sorted(set(found))


def scripts():
    return sorted(f for f in os.listdir(HERE) if f.endswith(".py"))


def all_findings():
    out = []
    for f in scripts():
        with open(os.path.join(HERE, f), encoding="utf-8") as fh:
            out += findings(fh.read(), f)
    return out


def _load():
    spec = importlib.util.spec_from_file_location("f30_tree_census", os.path.join(HERE, "tree_census.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---- the scan ---------------------------------------------------------------------------
def test_the_scan_walks_every_script_in_the_directory():
    names = scripts()
    for want in ("tree_census.py", "slug_evidence.py", "identity.py", "part1_c39.py", "attack_c39_prices.py"):
        assert want in names, names
    for f in names:   # every one parses: a file the scan cannot read is a file it does not guard
        with open(os.path.join(HERE, f), encoding="utf-8") as fh:
            ast.parse(fh.read())


def test_no_tree_total_is_hard_coded_in_f30_cfb_build():
    bad = [x for x in all_findings() if x[:3] not in ALLOWED]
    assert not bad, "a figure is hard-coded; read it from an argument or from the target's own file:\n" + \
        "\n".join("  %s:%d  %s  %s" % (f, line, shape, what) for f, shape, what, line in bad)


def test_every_allowance_still_excuses_something():
    seen = {x[:3] for x in all_findings()}
    stale = sorted(k for k in ALLOWED if k not in seen)
    assert not stale, "allowance matches nothing now - delete it: %s" % stale


F30_ORIGINAL = '''
PUBLISHED = {"missing": 0, "undeclared": 1, "declared": 21, "cfb_only": 15}
EXPECT_FILES = {"cfb": {"player_summary": 14327, "player_season": 40563},
                "nfl": {"player_summary": 4002, "player_season": 19328}}
'''


@pytest.mark.parametrize("source,shape,what", [
    (F30_ORIGINAL, "dict", "{'missing': 0, 'undeclared': 1, 'declared': 21, 'cfb_only': 15}"),
    ('PUB = {"2a": (0.0107, 3768), "2c": (0.0321, 168)}', "dict", "{'2a': (0.0107, 3768), '2c': (0.0321, 168)}"),
    ("ok = got == 14310", "compare", "14310"),
    ("assert 40543 <= n", "compare", "40543"),
    ('print(f"{got:,} files (a-78 stated 14,310)")', "text", "14,310"),
    ('"""the 14,327 staged pages"""', "text", "14,327"),
])
def test_the_scan_fires_on_each_shape(source, shape, what):
    got = {(s, w) for _, s, w, _ in findings(source, "planted.py")}
    assert (shape, what) in got, got


def test_the_scan_fires_on_both_of_f30s_original_literals():
    got = [x for x in findings(F30_ORIGINAL, "planted.py") if x[1] == "dict"]
    assert len(got) == 2, got   # the totals and the file counts, each reported once (not once per nesting level)


@pytest.mark.parametrize("source", [
    'env = {"schema_version": 2, "generated_at": "x"}',      # not all numeric
    "tot = {k: 0 for k in TOTAL_KEYS}",                      # a comprehension states no figure
    "if len(both) >= 30: pass",                              # under the floor
    'out(f"{got:,} files (baseline {stated:,})")',           # a format spec is not a count
    "full = run_tool(D, nfl, cfb, seed, 10 ** 9)",           # not a comparison
    'x = "2026-10-08T23:45:55Z, 3.14, 1,5"',                 # no thousands group
])
def test_the_scan_is_quiet_on_code_that_states_no_total(source):
    assert findings(source, "planted.py") == []


# ---- the baseline -----------------------------------------------------------------------
def baseline(**over):
    b = {"source": "test", "trees": {"cfb": "T-cfb", "nfl": "T-nfl"}, "seed": 7, "sample": 50,
         "totals": {"missing": 0, "undeclared": 2, "declared": 5, "cfb_only": 3},
         "files": {"cfb": {"player_summary": 11, "player_season": 30},
                   "nfl": {"player_summary": 9, "player_season": 40}}}
    b.update(over)
    return b


def report(totals=None, cfb_summary=11):
    return {"totals": {"undeclared": 2, "declared": 5, "cfb_only": 3} if totals is None else totals,
            "kinds": {"player_summary": {"nfl_files": 9, "cfb_files": cfb_summary},
                      "player_season": {"nfl_files": 40, "cfb_files": 30}}}


def test_the_census_refuses_to_start_without_a_baseline(tmp_path):
    r = subprocess.run([sys.executable, os.path.join(HERE, "tree_census.py"), "--src", str(tmp_path),
                        "--nfl", str(tmp_path), "--cfb", str(tmp_path), "--out", str(tmp_path / "o.json")],
                       capture_output=True, text=True, cwd=str(tmp_path))
    assert r.returncode == 2, (r.returncode, r.stderr)
    assert "--baseline" in r.stderr
    assert not (tmp_path / "o.json").exists()


def test_the_baseline_argument_has_no_default():
    T = _load()
    src = open(os.path.join(HERE, "tree_census.py"), encoding="utf-8").read()
    calls = [n for n in ast.walk(ast.parse(src)) if isinstance(n, ast.Call)
             and getattr(n.func, "attr", "") == "add_argument"
             and n.args and getattr(n.args[0], "value", None) == "--baseline"]
    assert len(calls) == 1
    kw = {k.arg: k.value for k in calls[0].keywords}
    assert "default" not in kw and kw["required"].value is True
    assert not hasattr(T, "PUBLISHED") and not hasattr(T, "EXPECT_FILES")


def test_compare_returns_both_answers():
    T = _load()
    assert T.compare(report(), baseline()) == (True, [])            # an omitted zero total reads as 0
    ok, moved = T.compare(report(totals={"undeclared": 1, "declared": 5, "cfb_only": 3}), baseline())
    assert ok is False and moved == []
    ok, moved = T.compare(report(cfb_summary=10), baseline())
    assert ok is True and moved == [("cfb", "player_summary", 10, 11)]


def test_a_baseline_for_another_tree_is_refused_not_scored():
    T = _load()
    T.require_same_trees(baseline(), {"cfb": "T-cfb", "nfl": "T-nfl"})
    with pytest.raises(SystemExit) as e:
        T.require_same_trees(baseline(), {"cfb": "T-later", "nfl": "T-nfl"})
    msg = str(e.value)
    assert "another tree" in msg and "T-later" in msg and "Nothing was compared" in msg
    assert "REPRODUCE" not in msg


@pytest.mark.parametrize("damage,word", [
    (lambda b: b.pop("totals"), "totals"),
    (lambda b: b.pop("source"), "source"),
    (lambda b: b["totals"].pop("undeclared"), "totals"),
    (lambda b: b["totals"].update(extra=1), "totals"),
    (lambda b: b["files"]["cfb"].pop("player_season"), "files.cfb.player_season"),
    (lambda b: b["files"]["nfl"].update(player_summary="9"), "files.nfl.player_summary"),
    (lambda b: b["totals"].update(missing=True), "totals.missing"),
    (lambda b: b["trees"].pop("nfl"), "trees.nfl"),
    (lambda b: b.update(sample=0), "sample"),
])
def test_a_baseline_missing_a_part_is_refused_by_name(tmp_path, damage, word):
    T = _load()
    b = baseline()
    damage(b)
    p = tmp_path / "b.json"
    p.write_text(json.dumps(b), encoding="utf-8")
    with pytest.raises(SystemExit) as e:
        T.load_baseline(str(p))
    assert word in str(e.value), str(e.value)


def test_a_whole_baseline_loads_and_an_absent_file_does_not(tmp_path):
    T = _load()
    p = tmp_path / "b.json"
    p.write_text(json.dumps(baseline()), encoding="utf-8")
    assert T.load_baseline(str(p)) == baseline()
    with pytest.raises(SystemExit):
        T.load_baseline(str(tmp_path / "absent.json"))


def test_tree_stamps_reads_each_manifest(tmp_path):
    T = _load()
    for side, stamp in (("nfl", "T-nfl"), ("cfb", "T-cfb")):
        d = tmp_path / side / side
        d.mkdir(parents=True)
        (d / "manifest.json").write_text(json.dumps({"generated_at": stamp}), encoding="utf-8")
    assert T.tree_stamps(str(tmp_path / "nfl"), str(tmp_path / "cfb")) == {"nfl": "T-nfl", "cfb": "T-cfb"}
