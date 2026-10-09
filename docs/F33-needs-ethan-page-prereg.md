# f-33 - pre-registration: which `needs_ethan` items the generated page calls live

Written and committed BEFORE `relay/needs_ethan.py` exists. The commit that adds
this file is the evidence of order.

## What I had seen when this was written

- `python -m relay.answered summary` on 2026-10-08: 595 items in 253 reports, 93
  answered, 110 ledger entries (all 110 recorded `by` f-28; 82 answered entries
  cite a commit, 7 a decision file, 4 a report). 126 items are merge-class.
- The `what`, `why` and `recommended` text of the 55 items that mention the
  weekly refresh anywhere, and of 13 candidates for the Weekly Refresh topic.
- `Get-ScheduledTask`: `CalibratedSports Weekly Refresh` executes
  `code\calibrated-sports\.venv\Scripts\python.exe -m jobs.weekly_refresh` with
  working directory `code\calibrated-sports` - the dev clone.
- NOT seen: how many items any rule below leaves live. No classification had run.

## The rule, as the brief fixed it

**An item is live unless something outside its own report says otherwise.**
Three things can say otherwise. They are tested in this order and an item takes
the first that holds, so the four classes partition the pile:

| class | holds when |
|---|---|
| `answered` | the item's effective ledger entry is `answered` AND it is Ethan's own word: `by` is `ethan`, or the citation type is `ethan` |
| `cited` | the item's effective ledger entry is `answered` and it is NOT Ethan's own word - a unit closed it on a commit, a report or a line of a decision file |
| `merged` | no effective `answered` entry; the item is merge-class (`relay.items.is_merge_class`) and `research.f28_evidence` finds every branch it names an ancestor of `main` today |
| `live` | none of the above |

- "Effective" is `relay.answered.state`: the last entry nothing supersedes,
  notes ignored. A `reopened` item is live.
- `cited` is the brief's (c), "closed on a citation with nobody answering". It
  is its own section, flagged, and is never folded into `answered`. No new
  `cited` item is inferred from reading reports: the only source is a ledger
  entry, because `relay.answered` infers nothing from silence and neither does
  this.
- `merged` is the brief's (b). A merge ask with a second half ("then run the
  publish") still classes as `merged` under the rule as written; where the
  ledger carries a note on it, the note is printed on the item's line and those
  items are listed first in the section. That is a known weakness of the rule,
  stated here rather than patched after seeing the count.
- A merge-class item where some or no named branch is in `main`, or where no
  branch of that name is on the remote, is `live`.
- Ancestry is the only merge test. A branch whose commits landed under another
  name (a rebase - a-05 landed as a-12 - or a cherry-pick) reads as unmerged,
  so `live` can hold merge asks that are in fact done. The page says so beside
  every not-in-main claim.

**If this leaves more than 300 live items the number is reported. The rule is
not tightened to shorten the page.**

## Order of the live section

Fixed here; an item appears once, in the first group it qualifies for.

1. The Weekly Refresh repoint topic (below).
2. Items their report flagged `irreversible` or `spends_money`, newest first.
3. Asks raised more than once: registered clusters (`relay.items.cluster`,
   threshold 0.50, unchanged) holding two or more live items, longest-waiting
   first. Also any item whose `what` declares `same ask as <item id>`.
4. Merge asks whose branches are not all in `main`, newest first.
5. Everything else, newest first.

## The one grouping that is not the registered rule - POST HOC, f-33, 2026-10-08

The registered text rule does not form the Weekly Refresh cluster (its largest
cluster is a triple). f-28 reported "nine items" and did not list them. The
topic on this page is therefore a HAND-READ LIST of item ids, made by f-33 after
reading the candidates above, pinned with each item's `what_sha`:

    a-08#1 a-14#0 a-15#2 a-28#0 a-33#4 a-38#2 a-67#6 a-69#3 a-73#2 f-21#2 f-25#4

Eleven, not nine. The test for membership was f-28's own definition of one
question: repointing the Weekly Refresh task at the production clone, once,
would settle the item or the part of it still undone (a-08#1, a-33#4 and f-21#2
name several tasks; the logger tasks have since moved). It is not a similarity
rule, changes no cluster count, and is labelled post hoc wherever it is shown.
Items that mention both the Weekly Refresh and the production clone and are not
on the list are counted on the page as unlisted candidates, so the list cannot
silently fall behind.

## What the test checks

The generated page carries each item's class in a machine-readable comment. The
check recomputes the ledger's answered set and fails when an item the page calls
`live` or `merged` has an effective `answered` entry, or an item the page calls
`answered` or `cited` has none. Items raised after the page was generated make
it STALE, which is reported separately and is not a disagreement with the ledger.
