"""No decision row is ever lost. Run: python -m jobs.audit_decisions

    python -m jobs.audit_decisions            # report
    python -m jobs.audit_decisions --check    # exit 1 if any historical row is missing

WHY THIS EXISTS. `DECISIONS.md` is an append-only table and three tracks append to
it, often within the same hour. Every rebase is therefore a chance to drop a row,
and on 2026-09-17 that nearly happened twice: a `--autostash` restore left
conflict markers in the file, the markers were committed and PUSHED, a second
track then rebased onto the broken file and added rows INSIDE the conflict block,
and the resolution had to merge three versions by hand.

THE CONSEQUENCE OF LOSING ONE IS NOT COSMETIC. A row is the record of WHY a
question was settled. Lose it silently and someone re-argues a closed question
months later with no trace that it was ever answered - and the second answer may
differ from the first with nobody able to tell.

WHY NOT A GREP. Checking by pattern was tried and gave a misleading answer: a
search for a row using its COMMIT SUBJECT returned 2 of 3, because the subject
read "removing a check's return value" while the table row read "REMOVING ...".
A pattern that does not match and a row that does not exist look identical, and
they have very different consequences. This enumerates instead.

THE IDENTIFIER IS THE DECISION COLUMN, NOT THE WHOLE LINE. A row is
`| date | decision | reason |`, and the reason gets edited - a typo fix, a figure
restated after a re-run. Keying on the whole line would report those as losses.
The decision column is the claim itself, normalised (bold stripped, case folded,
whitespace collapsed, trailing punctuation dropped) so formatting churn does not
read as a missing decision.

HISTORY IS READ FROM `git log --all`, not from the current branch, so a row that
existed on any reachable commit must still be present. That is the property that
matters: not "did main change" but "did anything ever recorded stop being
recorded".
"""
import argparse
import re
import subprocess
import sys

PATH = "DECISIONS.md"
ROW = re.compile(r"^\|\s*(\d{4}-\d{2}-\d{2})\s*\|(.+?)\|", re.S)


def _git(*args):
    r = subprocess.run(["git", *args], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    return r.stdout if r.returncode == 0 else ""


def identifier(line):
    """A row's stable identity: (date, normalised decision). None if not a row."""
    m = ROW.match(line)
    if not m:
        return None
    date, decision = m.group(1), m.group(2)
    text = decision.replace("**", "").replace("`", "").strip()
    text = re.sub(r"\s+", " ", text).rstrip(" .:-").casefold()
    return (date, text) if text else None


def rows_in(text):
    out = {}
    for line in text.splitlines():
        ident = identifier(line)
        if ident:
            out.setdefault(ident, line.strip())
    return out


def historical_rows():
    """Every row that has EVER appeared, from every commit reachable anywhere.

    Read from `git log --all -p`, whose added lines carry a leading '+'. A
    conflict marker or a diff header can never parse as a row, so they drop out
    without special handling.
    """
    patch = _git("log", "--all", "-p", "--format=", "--", PATH)
    seen = {}
    for line in patch.splitlines():
        if not line.startswith("+") or line.startswith("+++"):
            continue
        ident = identifier(line[1:])
        if ident:
            seen.setdefault(ident, line[1:].strip())
    return seen


def current_rows():
    try:
        with open(PATH, encoding="utf-8") as f:
            return rows_in(f.read())
    except OSError:
        return {}


def missing():
    """[(identifier, the row as first written)] present in history, absent now."""
    now = current_rows()
    return sorted((k, v) for k, v in historical_rows().items() if k not in now)


def main(argv=None):
    ap = argparse.ArgumentParser(description="no DECISIONS.md row is ever lost")
    ap.add_argument("--check", action="store_true", help="exit 1 if any row is missing")
    args = ap.parse_args(argv)

    hist, now = historical_rows(), current_rows()
    gone = missing()

    # Exit 0 is not a result: a scan that read nothing would report "no losses".
    if not hist:
        print("FAILED: no history read for %s - is this a git repo with full history?" % PATH)
        return 1
    if not now:
        print("FAILED: %s has no rows" % PATH)
        return 1

    print(f"  rows ever recorded (git log --all): {len(hist)}")
    print(f"  rows on disk now:                   {len(now)}")
    print(f"  MISSING:                            {len(gone)}")
    for ident, line in gone:
        print(f"    LOST {ident[0]}: {line[:150]}")

    if gone:
        print("\n  A decision row is the record of WHY something was settled. Recover it "
              "from history rather than re-deciding:\n"
              "    git log --all -p -- DECISIONS.md | grep -n '<the decision text>'")
        return 1 if args.check else 0
    print("  no decision has ever stopped being recorded")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
