"""f-36: what research/f30_cfb_build/tree_census.py's exit status reports.

f-34 left it reporting step 1 only: on a-72's tree the script exited 0 beside five printed
MISSING lines and `agrees ... False`. The rule (research/f36_exit_status/PREREGISTRATION.md,
committed before the script changed) is that a finding fails the run unless the baseline's
`known` names it, by exact path, under its own list, with a source.

These tests drive `exit_reasons` to every answer on a written result edited by hand - a
doctored MISSING count, a doctored census, a doctored agreement flag - and `known_of` to
every refusal. The same cases as real processes, on real and synthetic trees, are
research/f36_exit_status/doctor.py and the logs beside it; they need the target's worktree
and are not run here. No store, no network.
"""
import ast
import importlib.util
import json
import os

import pytest

HERE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "research", "f30_cfb_build")
PAGE_KINDS = ("sport_manifest", "team", "player_index", "player_summary", "player_season")
IDS = "player_summary.identity.ids.gsis"
FIVE = ["player_summary.identity.ids." + i for i in ("gsis", "pff", "pfr", "sleeper", "yahoo")]
UNRESOLVED = "sport_manifest.unresolved_ids[].name"


def _load():
    spec = importlib.util.spec_from_file_location("f30_tree_census", os.path.join(HERE, "tree_census.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def baseline(**over):
    b = {"source": "test", "trees": {"cfb": "T-cfb", "nfl": "T-nfl"}, "seed": 7, "sample": 50,
         "totals": {"missing": 0, "undeclared": 2, "declared": 5, "cfb_only": 3},
         "files": {"cfb": {"player_summary": 11, "player_season": 30},
                   "nfl": {"player_summary": 9, "player_season": 40}}}
    b.update(over)
    return b


def written(**over):
    """A written census with nothing in it: step 1 reproduced, no finding, both kinds agree."""
    r = {"reproduce": {"ok": True},
         "full": {"kinds": {k: {"missing": [], "undeclared": []} for k in PAGE_KINDS}},
         "census": {k: {"missing": [], "undeclared": [], "agrees_with_tool": True}
                    for k in ("player_summary", "player_season")}}
    for path, value in over.items():
        node, keys = r, path.split("/")
        for k in keys[:-1]:
            node = node[k]
        node[keys[-1]] = value
    return r


def test_a_clean_census_gives_no_reason_to_fail():
    assert _load().exit_reasons(written(), {}) == ([], [])


@pytest.mark.parametrize("over,known,word", [
    ({"reproduce/ok": False}, {}, "step 1 DOES NOT REPRODUCE"),
    # a doctored MISSING count, seen by both sources
    ({"census/player_summary/missing": [IDS], "full/kinds/player_summary/missing": [IDS]}, {},
     "the census reports MISSING " + IDS),
    # a doctored census: an undeclared path the walker sees
    ({"census/player_season/undeclared": ["player_season.periods[].stats.x"],
      "full/kinds/player_season/undeclared": ["player_season.periods[].stats.x"]}, {},
     "the census reports UNDECLARED player_season.periods[].stats.x"),
    # kinds the walker does not cover
    ({"full/kinds/sport_manifest/undeclared": [UNRESOLVED]}, {}, "the tool's full run reports undeclared " + UNRESOLVED),
    ({"full/kinds/team/missing": ["team.city"]}, {}, "the tool's full run reports missing team.city"),
    # a doctored agreement flag, both ways round
    ({"census/player_summary/agrees_with_tool": False}, {}, "agrees_with_tool False and its own lists say True"),
    ({"census/player_summary/missing": [IDS], "census/player_summary/agrees_with_tool": True},
     {("missing", IDS): "f-30"}, "agrees_with_tool True and its own lists say False"),
    # the two sources differ, and nothing names the path
    ({"census/player_summary/missing": [IDS], "census/player_summary/agrees_with_tool": False}, {},
     "the census and the tool's full run disagree on"),
    # an acknowledgement that excuses nothing
    ({}, {("missing", IDS): "f-30"}, "'known' names missing " + IDS),
    # the right path under the wrong list is not an acknowledgement
    ({"census/player_summary/missing": [IDS], "full/kinds/player_summary/missing": [IDS]},
     {("undeclared", IDS): "f-30"}, "the census reports MISSING " + IDS),
])
def test_every_registered_case_is_a_reason_to_fail(over, known, word):
    reasons, _ = _load().exit_reasons(written(**over), known)
    assert any(word in r for r in reasons), reasons


def test_a_known_finding_is_acknowledged_and_does_not_fail():
    """a-72's shape: the walker sees five id keys the tool cannot, and the baseline names them."""
    T = _load()
    result = written(**{"census/player_summary/missing": FIVE, "census/player_summary/agrees_with_tool": False,
                        "full/kinds/sport_manifest/undeclared": [UNRESOLVED]})
    known = {("missing", p): "f-30" for p in FIVE}
    known[("undeclared", UNRESOLVED)] = "a-72"
    reasons, acknowledged = T.exit_reasons(result, known)
    assert reasons == [] and len(acknowledged) == 6
    assert {(e["list"], e["path"]) for e in acknowledged} == set(known)
    # naming four of the five is not naming five
    del known[("missing", FIVE[4])]
    reasons, acknowledged = T.exit_reasons(result, known)
    assert len(acknowledged) == 5
    assert any("MISSING " + FIVE[4] in r for r in reasons)
    assert any("disagree" in r and FIVE[4] in r for r in reasons)
    # and naming nothing fails on all six
    reasons, acknowledged = T.exit_reasons(result, {})
    assert acknowledged == [] and sum("MISSING" in r for r in reasons) == 5
    assert any("undeclared " + UNRESOLVED in r for r in reasons)


def test_a_disagreement_is_never_the_only_reason():
    """Reported by f-36: every path the two sources differ on is a finding of exactly one of
    them, so it is either unacknowledged (and fails as a finding) or named. The disagreement
    line tells the reader the sources differ; it cannot fail a run nothing else fails."""
    T = _load()
    for lst in ("missing", "undeclared"):
        for side in ("census/player_summary/", "full/kinds/player_summary/"):
            over = {side + lst: [IDS], "census/player_summary/agrees_with_tool": False}
            reasons, _ = T.exit_reasons(written(**over), {})
            assert len(reasons) == 2 and sum("disagree" in r for r in reasons) == 1, reasons
            assert T.exit_reasons(written(**over), {(lst, IDS): "x"})[0] == []


@pytest.mark.parametrize("known,word", [
    ([{"list": "missing", "path": IDS}], "known[0]"),                                  # no source
    ([{"list": "missing", "path": IDS, "source": " "}], "known[0].source"),
    ([{"list": "declared", "path": IDS, "source": "f-30"}], "known[0].list"),
    ([{"list": "missing", "path": "", "source": "f-30"}], "known[0].path"),
    ([{"list": "missing", "path": IDS, "source": "f-30", "note": "x"}], "known[0]"),
    ([{"list": "missing", "path": IDS, "source": "f-30"}] * 2, "twice"),
    ({"missing": [IDS]}, "'known' must be a list"),
    (["player_summary.*"], "known[0]"),                                                # no bare string, no wildcard
])
def test_a_malformed_acknowledgement_is_refused_at_load(tmp_path, known, word):
    p = tmp_path / "b.json"
    p.write_text(json.dumps(baseline(known=known)), encoding="utf-8")
    with pytest.raises(SystemExit) as e:
        _load().load_baseline(str(p))
    assert word in str(e.value), str(e.value)


def test_a_wildcard_path_acknowledges_only_its_own_literal_string():
    reasons, acknowledged = _load().exit_reasons(
        written(**{"census/player_summary/missing": [IDS], "full/kinds/player_summary/missing": [IDS]}),
        {("missing", "player_summary.identity.ids.*"): "x"})
    assert acknowledged == [] and len(reasons) == 2      # the finding, and the entry that excuses nothing


def test_known_is_optional_and_loads_by_list_and_path(tmp_path):
    T = _load()
    assert T.known_of(baseline()) == {}
    p = tmp_path / "b.json"
    p.write_text(json.dumps(baseline(known=[{"list": "missing", "path": IDS, "source": "f-30, README"}])), encoding="utf-8")
    assert T.known_of(T.load_baseline(str(p))) == {("missing", IDS): "f-30, README"}


def test_the_status_is_decided_from_the_written_result_and_nothing_else():
    """main() writes result['exit'] before it exits, and its last exit hangs on exit_reasons alone."""
    with open(os.path.join(HERE, "tree_census.py"), encoding="utf-8") as f:
        main = next(n for n in ast.parse(f.read()).body if isinstance(n, ast.FunctionDef) and n.name == "main")
    exits = [n for n in ast.walk(main) if isinstance(n, ast.Raise) and "SystemExit" in ast.unparse(n)]
    last = max(exits, key=lambda n: n.lineno)
    assert ast.unparse(last) == "raise SystemExit(1)"
    guard = next(n for n in ast.walk(main) if isinstance(n, ast.If) and last in n.body)
    assert ast.unparse(guard.test) == "reasons"
    dump = next(n for n in ast.walk(main) if isinstance(n, ast.Call) and ast.unparse(n.func) == "json.dump")
    assign = next(n for n in ast.walk(main) if isinstance(n, ast.Assign) and ast.unparse(n.targets[0]) == "result['exit']")
    assert assign.lineno < dump.lineno < last.lineno
    # nothing after the census can leave with status 0 by another door
    late = [n for n in exits if n.lineno > dump.lineno]
    assert late == [last], [ast.unparse(n) for n in late]
