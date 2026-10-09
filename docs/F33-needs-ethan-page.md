# f-33 - `NEEDS-ETHAN.md` is generated from the reports plus the answered ledger

    git fetch origin                                 (here and in the web clone: ancestry is asked of origin/main)
    python -m relay.needs_ethan                      classify, print the counts, write nothing
    python -m relay.needs_ethan --write              overwrite <relay>/NEEDS-ETHAN.md in place
    python -m relay.needs_ethan check                0 agrees with the ledger, 1 DISAGREES, 3 agrees but stale

The rule is `F33-needs-ethan-page-prereg.md`, committed (`68907da`) before
`relay/needs_ethan.py` existed. Generated once, 2026-10-08 20:5x -0400.

## What the first run measured

596 `needs_ethan` items in 254 machine reports (595 in 253 when the rule was
written, about half an hour earlier - the count moved again while this was built).

| class | items |
|---|---|
| live | **496** |
| answered in the ledger (Ethan's own word) | 0 |
| overtaken by a merge (no ledger answer, every named branch in main by ancestry) | 7 |
| closed on a citation by a unit, nobody answering | 93 |

**496 is more than 300 and the rule was not tightened.** Live, in the order the
page prints them: 11 Weekly Refresh repoint, 33 flagged irreversible or spending
money, 67 in 32 repeated-ask groups, 28 merge asks not all in main, 357 others.

- All 110 ledger entries were recorded by f-28, so "answered in the ledger" is
  empty and all 93 closures are the relay closing its own questions: 77 on a
  commit for a merge ask, 7 on a decision file, 5 on a commit for a non-merge
  ask, 4 on a report or a measurement.
- All 7 `merged` items carry an f-28 ledger note that the ask had a second half
  with no evidence (a publish, a live-store run). The pre-registered rule still
  classes them `merged`; the page lists the note on each line.
- The registered clustering gives 538 clusters at 596 items (508 at 0.40, 570 at
  0.60). It is printed only through `cluster_sentence`, which attaches f-28's
  audits (7 of 43 false merges; 28 of 60 near-miss pairs the same question) and
  the sentence that it is not a count of distinct questions. The audits were not
  repeated on the 42 items raised since f-28's run.
- Weekly Refresh: `Get-ScheduledTask` read at generation shows it executing
  `code\calibrated-sports\.venv\Scripts\python.exe -m jobs.weekly_refresh` from
  `code\calibrated-sports` - the dev clone. The page lists **eleven** items, a
  hand-read list (post hoc, pinned by `what_sha`), raised 2026-09-22 through
  2026-10-08. f-28 said nine and did not list them.

## What the old page held that no report does

The old `NEEDS-ETHAN.md` had 603 `## ` blocks. 574 are items and are regenerated
from their reports. **29 are not in any machine report** and would have been
lost by a plain overwrite:

- 1 runner block: b-22's first run (2026-09-23 02:28, "Requeue b-22 once a-12
  is merged to producer main"). b-22 ran twice and `reports/json/b-22.json`
  holds only the second run, so the first run's ask has no item id and cannot
  be answered in the ledger. 4 of 255 units in `runlog.jsonl` ran more than
  once; b-22 is the only one whose earlier run raised an item.
- 28 blocks written by the overnight watch (notes and proposed units).

The generator carries every such block forward verbatim (section 5) and checks,
before writing, that they survive a regeneration unchanged. A page that does not
decode as UTF-8 is not rewritten.

## What the test fails on

`tests/test_needs_ethan_page.py::test_the_real_page_agrees_with_the_real_ledger`
parses the page on disk and fails when an item it calls live or merged has an
effective `answered` entry, when one it calls answered or cited has none, or
when a unit's closure is shown as Ethan's. It failed before the first
generation (the old page is not a generated one) and passes after. New reports
make the page stale (`check` exits 3); that does not fail the test.

**Consequence, taken knowingly:** answering an item with
`python -m relay.answered answer` turns that test red in every clone that holds
it until the page is regenerated. That is what the brief asked for; the cure is
one command.

## Not done here

- No item was answered and `ANSWERED.jsonl` was not written (same SHA-256
  before and after). The generator has no code path that appends to it.
- The Weekly Refresh task was not repointed.
- `run/relay.ps1` still appends raw blocks to the bottom of the page. They land
  below the end mark, `check` counts them as stale, and the next `--write` folds
  them in. Nothing regenerates the page on a schedule.
