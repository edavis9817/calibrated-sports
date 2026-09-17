"""No tracked file carries a merge conflict marker.

Run: pytest -q tests/test_no_conflict_markers.py

THE INCIDENT. On 2026-09-17 a `git rebase --autostash` failed to restore
cleanly, left conflict markers in `DECISIONS.md`, and the file was staged,
committed and PUSHED in that state. Three tracks write this repo, so autostash
restores across another track's commits routinely - it is not an exotic path.

WHY NOTHING CAUGHT IT. `DECISIONS.md` is prose. No test parses it, the suite ran
green on a clone containing the markers, and the export never reads it. The
damage was not missing content - nothing was lost, both sides survived - but a
malformed file published as the project's decision record. A failure with
nothing positioned to detect it is the class this whole file exists to close.

WHY IT KEYS ON THE ANGLE BRACKETS AND NOT ON `=======`. A row of equals signs is
legitimate Markdown - a setext heading rule - so matching it would fire on
ordinary prose. Git's own markers are `<<<<<<< ` and `>>>>>>> `, each seven
characters and a space, and those do not occur in normal text.

AND WHY THE PATTERNS ARE BUILT, NOT WRITTEN. A test that contains the literal
markers matches ITSELF, which is the same shape as a docstring quoting a deleted
function name and defeating a grep. They are constructed from characters here so
this file can never be its own violation.
"""
import subprocess

import pytest

# Built, never written literally - see the docstring.
OPEN_MARKER = "<" * 7 + " "
CLOSE_MARKER = ">" * 7 + " "


def tracked_files():
    r = subprocess.run(["git", "ls-files"], capture_output=True, text=True)
    assert r.returncode == 0, f"git ls-files failed: {r.stderr}"
    return [p for p in r.stdout.splitlines() if p.strip()]


def marker_lines(text):
    """[(lineno, line)] for every conflict marker. Pure, so the test below can
    drive it into the failing shape without touching the repo."""
    out = []
    for i, line in enumerate(text.splitlines(), 1):
        if line.startswith(OPEN_MARKER) or line.startswith(CLOSE_MARKER):
            out.append((i, line))
    return out


# ------------------------------------------------------------- it discriminates

def test_it_finds_a_marker_when_there_is_one():
    """Driven into the banned shape. Without this, a checker that always
    returned [] would pass the repo-wide test below and assert nothing."""
    text = f"a\n{OPEN_MARKER}HEAD\nb\n=======\nc\n{CLOSE_MARKER}stash\nd\n"
    found = marker_lines(text)
    assert [n for n, _ in found] == [2, 6], found


def test_it_does_not_fire_on_ordinary_markdown():
    """A setext heading rule is a row of equals signs and is not a conflict.
    Matching `=======` would have made this guard unusable on a docs-heavy repo."""
    assert marker_lines("Heading\n=======\n\ntext\n") == []


def test_it_does_not_fire_on_prose_mentioning_arrows():
    assert marker_lines("see a -> b, and x >> y\n") == []


# ------------------------------------------------------------------- the repo

def test_no_tracked_file_carries_a_conflict_marker():
    """The guard itself. `DECISIONS.md` was committed and pushed with three of
    them because nothing looked."""
    offenders = {}
    for path in tracked_files():
        try:
            with open(path, encoding="utf-8") as f:
                text = f.read()
        except (OSError, UnicodeDecodeError):
            continue                      # binary or unreadable: not our business
        found = marker_lines(text)
        if found:
            offenders[path] = found
    assert not offenders, "conflict markers in tracked files: " + "; ".join(
        f"{p} line {found[0][0]}" for p, found in offenders.items())


def test_the_scan_actually_read_something():
    """Exit 0 is not a result. If `git ls-files` returned nothing the test above
    would pass while checking no files at all."""
    files = tracked_files()
    assert len(files) > 100, f"only {len(files)} tracked files found - did the scan run?"
    assert any(p.endswith(".py") for p in files)
    assert any(p.endswith(".md") for p in files)
