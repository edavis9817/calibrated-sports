"""The settlement rule, once. Run: pytest -q tests/test_settlement.py

THE REGRESSION THIS FILE EXISTS FOR. The rule was written twice and the copies
disagreed on 3,272 defensive outcomes. `walkforward.corrected_result` judged
every stat on OFFENSIVE snaps, which voids a linebacker's realized zeros because
his offensive snap count is 0 in every game he plays. It was dormant only
because that module's STATS are receptions and rush attempts - dormancy is not
safety, and an offense-only branch in `settle_one` would have voided 3,263 real
defensive zeros, all in the direction that flatters the model.

No test in the suite caught that, because no test asserted the rule on a
defensive stat. `test_a_defender_who_played_settles_at_zero_not_void` is that
test, and `test_the_rule_exists_in_exactly_one_place` is what stops the copies
coming back.
"""
import ast
import pathlib

import pytest

from core import settlement as S

ROOT = pathlib.Path(__file__).resolve().parents[1]

# (team, offense_snaps, defense_snaps)
WR_PLAYED = ("BUF", 18, 0)
LB_PLAYED = ("BUF", 0, 41)
BENCHED = ("BUF", 0, 0)


# ------------------------------------------------------------------ the cases

def test_case_A_played_with_no_stat_row_is_a_realized_zero():
    """nflverse omits the ROW, not the player. Snaps say he was out there."""
    v, status, reason = S.settled_value(None, False, WR_PLAYED, "receptions")
    assert (v, reason) == (0.0, None)
    assert "0" in status


def test_case_B_zero_snaps_is_void_did_not_play():
    v, _s, reason = S.settled_value(None, False, BENCHED, "receptions")
    assert v is None and reason == S.DID_NOT_PLAY


def test_case_C1a_no_snap_row_but_he_plays_elsewhere_is_void_inactive():
    v, _s, reason = S.settled_value(None, False, None, "receptions",
                                    player_has_snaps=True, week_has_snaps=True)
    assert v is None and reason == S.INACTIVE


def test_case_C1b_a_player_who_appears_nowhere_stays_UNSETTLED():
    """OUR JOIN FAILED. 44 of these are a mapper defect - a backfill pointed a
    Cleveland receiver's outcomes at a 1981 linebacker. Recording 'void' there
    would launder a data defect into a fact about a player."""
    v, _s, reason = S.settled_value(None, False, None, "receptions",
                                    player_has_snaps=False, week_has_snaps=True)
    assert v is None and reason is None, "a join failure is not a void"


def test_case_C2_a_week_with_no_snap_data_stays_UNSETTLED():
    v, _s, reason = S.settled_value(None, False, None, "receptions",
                                    player_has_snaps=True, week_has_snaps=False)
    assert v is None and reason is None


def test_a_present_stat_row_always_wins():
    assert S.settled_value(3.0, True, None, "receptions") == (3.0, "value", None)
    assert S.settled_value(None, True, None, "receptions")[0] is None
    assert S.settled_value(None, True, None, "receptions")[2] is None


# ----------------------------------------------------------------- the phase

def test_a_defender_who_played_settles_at_zero_not_void():
    """THE 3,263-OUTCOME BUG, AS A TEST.

    A linebacker has 41 defensive snaps and 0 offensive ones. The offense-only
    rule reads 0 and voids him; the correct rule reads 41 and settles his
    tackles at a realized 0. Voiding here deletes exactly the zeros - the
    outcomes where the over LOST - which flatters any model being scored.
    """
    for stat in ("tackles_assists", "sacks"):
        v, _s, reason = S.settled_value(None, False, LB_PLAYED, stat)
        assert (v, reason) == (0.0, None), f"{stat} judged on the wrong phase"
        # and the offense-only reading, named so the regression is unmistakable
        assert S.snaps_played(LB_PLAYED, stat) == 41
        assert LB_PLAYED[1] == 0, "offense_snaps is what the buggy copy read"


def test_an_offensive_stat_is_still_judged_on_offensive_snaps():
    v, _s, reason = S.settled_value(None, False, WR_PLAYED, "receptions")
    assert (v, reason) == (0.0, None)
    # A receiver with only defensive snaps did not play his own phase.
    v2, _s2, r2 = S.settled_value(None, False, ("BUF", 0, 12), "receptions")
    assert v2 is None and r2 == S.DID_NOT_PLAY


def test_the_phase_comes_from_the_stat_not_the_player():
    assert S.snaps_played(LB_PLAYED, "sacks") == 41
    assert S.snaps_played(LB_PLAYED, "receptions") == 0
    assert S.snaps_played(None, "sacks") is None


# ------------------------------------------------------------- end to end

def test_settle_collapses_the_cases_into_a_storable_result():
    assert S.settle(None, False, WR_PLAYED, "receptions", 0.5, False)[:3] == \
        (S.UNDER, 0.0, None)
    assert S.settle(None, False, BENCHED, "receptions", 0.5, False)[:3] == \
        (S.VOID, None, S.DID_NOT_PLAY)
    assert S.settle(None, False, None, "receptions", 0.5, False,
                    player_has_snaps=False, week_has_snaps=True)[:3] == \
        (S.UNSETTLED, None, None)
    assert S.settle(6.0, True, None, "receptions", 6.0, True)[:3] == \
        (S.PUSH, 6.0, None)


def test_unsettled_never_carries_a_void_reason():
    """The two are different claims: void says the venue returns the stake,
    unsettled says we do not know. A reason on an unsettled row would make a
    join failure look adjudicated."""
    for kwargs in ({"player_has_snaps": False, "week_has_snaps": True},
                   {"player_has_snaps": True, "week_has_snaps": False}):
        result, actual, reason, _s = S.settle(
            None, False, None, "receptions", 0.5, False, **kwargs)
        assert result == S.UNSETTLED and reason is None and actual is None


def test_void_reasons_are_exactly_the_two_the_evidence_supports():
    assert {S.DID_NOT_PLAY, S.INACTIVE} == {"did_not_play", "inactive"}


def test_resolve_still_scores_a_push_as_a_push():
    assert S.resolve(6.0, 6.0, True) == S.PUSH
    assert S.resolve(6.0, 6.0, False) == S.OVER
    assert S.resolve(7.0, 6.0, True) == S.OVER
    assert S.resolve(5.0, 6.0, True) == S.UNDER
    assert S.resolve(None, 6.0, True) == S.UNSETTLED


# -------------------------------------------------------- exactly one copy

def defines(path, name) -> bool:
    tree = ast.parse((ROOT / path).read_text(encoding="utf-8"))
    return any(isinstance(n, ast.FunctionDef) and n.name == name
               for n in ast.walk(tree))


def calls(path, name) -> bool:
    """Does this module CALL `name` as a bare function?

    Deleting a definition does not delete its callers, and nothing else catches
    that: an unresolved name is a runtime NameError, so `py_compile` passes and
    so does any test that never reaches the line. Extracting the rule left two
    live calls in research/bookvbook.py behind a green suite of 800 tests.
    """
    tree = ast.parse((ROOT / path).read_text(encoding="utf-8"))
    return any(isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
               and n.func.id == name for n in ast.walk(tree))


def sources():
    for p in sorted(ROOT.rglob("*.py")):
        rel = p.relative_to(ROOT)
        if set(rel.parts) & {".venv", "__pycache__", "node_modules", "ci-clone"}:
            continue
        yield rel


@pytest.mark.parametrize("name", ["corrected_value", "corrected_result"])
def test_no_module_still_calls_a_deleted_copy(name):
    """The definitions are gone; these assert the CALLS went with them."""
    offenders = [str(rel) for rel in sources() if calls(rel, name)]
    assert not offenders, (
        f"{name}() was deleted but is still called in: {offenders}")


@pytest.mark.parametrize("path,name", [
    ("research/bookvbook.py", "corrected_value"),
    ("research/walkforward.py", "corrected_result"),
    ("jobs/settle_outcomes.py", "resolve"),
])
def test_the_rule_exists_in_exactly_one_place(path, name):
    """The defect was duplication, so duplication is what the test forbids.

    Deleting a copy is not enough - the next session re-adds one unless
    something fails when it does.
    """
    assert not defines(path, name), (
        f"{path} defines {name}() again; the rule lives in core/settlement.py")


def test_the_settlers_import_the_shared_rule():
    for path in ("research/bookvbook.py", "research/walkforward.py",
                 "jobs/settle_outcomes.py"):
        src = (ROOT / path).read_text(encoding="utf-8")
        assert "core.settlement" in src or "from core import settlement" in src, \
            f"{path} does not use the shared rule"
