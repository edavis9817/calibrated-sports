# f-28 - the answered ledger, and what the pile of `needs_ethan` items holds

Measured 2026-10-08 against `_relay/reports/json/` (238 reports, 554 items,
population hash `09a2f5708b599ecd`). The rule is in
`F28-answered-ledger-prereg.md`, committed before any pair was scored (`3d97b5d`).

    python -m research.f28_clusters <relay> --audit-dir <dir>      the count
    python -m research.f28_evidence <relay> --main-repo . --web-repo <web clone>
    python -m research.f28_close    <relay> --main-repo . --web-repo <web clone> [--apply]
    python -m relay.answered                                       what is open

## The pre-registered count, and why it should not be quoted as a deduplication

| | clusters |
|---|---|
| **registered rule, cosine >= 0.50** | **504** from 554 items |
| sensitivity 0.40 | 480 |
| sensitivity 0.60 | 535 |

43 clusters hold more than one item (36 pairs, 7 triples, 93 items); 461 items
stand alone. 5 of the 43 are chained (minimum internal cosine below 0.50).

**The rule fails in both directions, and the audits it registered say so.**

- False merges: I read all 43 multi-item clusters. **7 hold two items that no
  single answer settles** (16%): `Q:c-32#0`, `Q:a-64#2`, `Q:b-21#0`, `Q:a-14#1`,
  `Q:b-81#0`, `Q:a-10#3`, `Q:a-15#2`.
- Missed merges: of 134 cross-cluster pairs with cosine in [0.30, 0.50) I read
  the seeded sample of 60. **28 are the same question** (47%). Pairs below 0.30
  were not sampled at all.

So 504 is not an upper bound and not a lower bound. It is the output of a text
rule that separates "Merge order for b-52 and b-53" from "Merge order for b-52,
b-53 and b-54". One reader graded both audits, and that reader wrote the rule.
**No figure of the form "554 is really N" is supported by this unit.**

## What does reduce the pile: evidence, not text

122 of the 554 items are merge-class (a kind of ask). Asked of git:

| every named branch in main | some | none | no such branch on the remote |
|---|---|---|---|
| 74 | 11 | 26 | 11 |

POST HOC (added after the registered rule ran, and not a count of questions):
the unmerged branches those asks name are 27 in the producer repository, covered
by 13 tips, and 10 in the web repository, covered by 6. A branch whose commits
landed under another name (a rebase, a cherry-pick) still counts as unmerged
here.

`research/f28_close.py` then appended 110 cited entries to `ANSWERED.jsonl`: 93
close an item, 17 are notes that do not. After it: **461 open of 554.** "Answered"
means the decision was taken and something citable shows it - not that it went
the way the unit recommended, and not that a deploy after a merge worked. An item
with no entry is open; that is the default, not a finding that it is unanswered.

## The file

`<relay>/ANSWERED.jsonl`, described in its own header and in `relay/answered.py`.
Item id `<unit_id>#<index>`; entry id `L<seq>`; a correction is a new line naming
`supersedes`. Each line hashes the one before, so an in-place edit fails
`verify()`, which every read and append runs. The newest line has nothing after
it, so its hash is pinned in `relay/answered.pins.jsonl` (committed) and
`tests/test_answered_ledger.py` checks the live file against every pin.

What that does not cover: lines appended after the last pin can be rewritten by
someone who also recomputes every later `prev`. Pin after a batch of answers.
