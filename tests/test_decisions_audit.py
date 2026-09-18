"""No decision row is ever lost. Run: pytest -q tests/test_decisions_audit.py

WHY IT IS A TEST AND NOT A HABIT. `DECISIONS.md` is append-only and three tracks
append to it, so every rebase is a chance to drop a row. On 2026-09-17 a
`--autostash` restore left conflict markers in the file, they were committed and
PUSHED, a second track rebased onto the broken file and added rows INSIDE the
conflict block, and the whole thing had to be merged by hand across three
versions. A row is the record of WHY a question was settled; losing one silently
means someone re-argues a closed question later with no trace of the first
answer.

WHY ENUMERATION, NOT A PATTERN. The loss was first "checked" by grepping for a
row using its COMMIT SUBJECT, which returned 2 of 3 - not because a row was gone
but because the subject read "removing a check's return value" while the table
row read "REMOVING ...". A pattern that fails to match and a row that does not
exist look identical and mean opposite things.
"""
import subprocess

import pytest

from jobs import audit_decisions as A


ROW = "| 2026-09-17 | **A decision** | because of a reason |"


# ------------------------------------------------------------ the identifier

def test_a_row_yields_an_identifier():
    assert A.identifier(ROW) == ("2026-09-17", "a decision")


def test_formatting_churn_does_not_change_identity():
    """Bold, backticks, case and spacing are presentation. If they changed the
    identifier, every reformat would read as a lost decision."""
    a = A.identifier("| 2026-09-17 | **A decision** | r |")
    b = A.identifier("| 2026-09-17 |   a   DECISION   | r |")
    c = A.identifier("| 2026-09-17 | `A decision`. | r |")
    assert a == b == c


def test_the_REASON_is_not_part_of_the_identity():
    """Reasons get edited - a figure restated after a re-run. Keying on the whole
    line would report those as losses and train everyone to ignore the check."""
    assert (A.identifier("| 2026-09-17 | D | first reason |")
            == A.identifier("| 2026-09-17 | D | a completely different reason |"))


def test_a_different_DECISION_is_a_different_row():
    """Discriminating: the normaliser must not collapse distinct decisions."""
    assert A.identifier("| 2026-09-17 | D one | r |") != A.identifier("| 2026-09-17 | D two | r |")


def test_the_date_is_part_of_the_identity():
    assert A.identifier("| 2026-09-16 | D | r |") != A.identifier("| 2026-09-17 | D | r |")


@pytest.mark.parametrize("line", [
    "",
    "# Decisions",
    "| date | decision | reason |",
    "|---|---|---|",
    "<<<<<<< Updated upstream",
    ">>>>>>> Stashed changes",
    "=======",
    "Append-only. One line per settled decision, newest last.",
])
def test_non_rows_are_not_rows(line):
    """Conflict markers and table furniture must never parse as decisions - the
    audit reads raw `git log -p` output, where all of these appear."""
    assert A.identifier(line) is None


# --------------------------------------------------------------- the diffing

def test_a_dropped_row_is_detected_and_NAMED():
    """The banned shape, driven directly. Without this the repo-level test below
    would pass against a checker that could never report anything."""
    history = A.rows_in("| 2026-09-17 | Kept | r |\n| 2026-09-17 | Dropped | r |\n")
    current = A.rows_in("| 2026-09-17 | Kept | r |\n")
    gone = sorted(k for k in history if k not in current)
    assert gone == [("2026-09-17", "dropped")]


def test_nothing_is_reported_when_nothing_is_missing():
    rows = "| 2026-09-17 | Kept | r |\n| 2026-09-17 | Also kept | r |\n"
    history, current = A.rows_in(rows), A.rows_in(rows)
    assert [k for k in history if k not in current] == []


def test_a_row_added_LATER_is_not_a_loss():
    """History is a subset of now, not the reverse. New decisions are the point."""
    history = A.rows_in("| 2026-09-17 | One | r |\n")
    current = A.rows_in("| 2026-09-17 | One | r |\n| 2026-09-18 | Two | r |\n")
    assert [k for k in history if k not in current] == []


# ----------------------------------------------------------------- the repo

def test_no_decision_has_ever_stopped_being_recorded():
    """The guard itself, over real history."""
    gone = A.missing()
    assert not gone, "rows lost from DECISIONS.md: " + "; ".join(
        f"{d} {t!r}" for (d, t), _line in gone)


def test_the_audit_actually_read_history():
    """Exit 0 is not a result. In a shallow clone `git log --all -p` returns
    little or nothing, and an empty history makes 'no losses' vacuous - the same
    reason ci.yml checks out with fetch-depth: 0."""
    hist = A.historical_rows()
    assert len(hist) > 50, f"only {len(hist)} historical rows found - shallow clone?"
    assert len(A.current_rows()) > 50


def test_it_runs_as_a_module_and_exits_zero_when_clean():
    r = subprocess.run(["python", "-m", "jobs.audit_decisions", "--check"],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "MISSING" in r.stdout
