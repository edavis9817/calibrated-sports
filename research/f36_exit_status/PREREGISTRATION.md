# f-36 — pre-registration: what `tree_census.py`'s exit status reports

Written and committed before any script or test is edited. Base: `origin/f-35-reliability-run5`
@ ee7a9c8. Nothing below has been run under the new rule yet; the only things read so far are
f-34's committed logs and result files.

## What is known before the rule is written

f-34 left `tree_census.py` exiting 0 whenever step 1 reproduced. On a-72's tree
(`D:/temp/a72/new`, generated 2026-10-08T07:28:10Z) that is exit 0 beside five printed
`MISSING` lines and `agrees with the tool's full run ... False` on `player_summary`.

**Which of the two I am looking at, said before running.** The brief calls the five id keys a
"declared, known absence". They are known. They are not declared, in the only sense the
census gives that word: a-72's `cfb/manifest.json` carries 34 `absences` entries and none
covers `player_summary.identity.ids`, which is exactly why the walker prints them as MISSING
and not as "beneath a declared absence". What exists is f-30's report naming them
(`research/f30_cfb_build/README.md`, "a-72's '86 to 0' excludes one map by construction").
a-78 then declared them, and on a-78's tree the same walker prints `59 beneath a declared
absence + 0 MISSING`.

So on a-72's tree the tree is the side with the gap, and a rule that goes red on it with no
further information is working. A rule that stays green on it with no further information
is the defect this unit exists to remove (a printed finding beside exit 0). The "must still
pass" case is therefore met by giving the caller a way to say *known, and here is who said
so* - by name, in the baseline - and not by making MISSING tolerable.

a-72's tool also totals `undeclared 1` on that tree. f-34's `census-a72.json` names it:
`sport_manifest.unresolved_ids[].name`, a kind the walker does not cover. It is in the
tool's full run, so the rule sees it.

## The rule

After the census is written, the process exits 1 if ANY of these holds, and prints one
`EXIT 1:` line per reason. It exits 0 only if none holds. Exit 2 stays what it is today:
an argument that cannot be used (no `--baseline`).

A **finding** is a `(list, path)` pair, `list` being `missing` or `undeclared`, drawn from
both sources: the independent walker (the two player kinds) and the target's tool with the
sample lifted (all five page kinds).

A baseline may carry `known`: a list of `{"list", "path", "source"}`. `source` is required
and must say who reported the finding and where. There is no default and no wildcard; a
path is acknowledged only by its exact string under its own list.

| # | case | exit |
|---|---|---|
| 1 | step 1 does not reproduce the baseline (totals or file counts) | 1 |
| 2 | the walker prints a MISSING path that `known` does not name | 1 |
| 3 | the walker or the tool's full run reports an undeclared path that `known` does not name | 1 |
| 4 | the tool's full run reports a missing path that `known` does not name | 1 |
| 5 | walker and tool disagree on a player kind, on any path `known` does not name | 1 |
| 6 | `known` names a finding that neither source reports (an acknowledgement that excuses nothing) | 1 |
| 7 | the written result's own `agrees_with_tool` flag contradicts its lists | 1 |
| 8 | a `known` entry with no `source`, or a `list` other than the two | refused at load, exit 1, nothing compared |

Case 6 is there because an acknowledgement list that can outlive what it excuses becomes a
permanent green; it is the same rule f-34's test applies to its own allowances.

The decision is a function of the result that is written to `--out` plus the baseline's
`known`, so the file a reader opens and the status the process returned cannot differ.

## What must fail, and how each is shown

By doctoring, each as a real process whose exit code is captured on the next line:

- **a doctored MISSING count** - a tree in which one first-sport key is absent from college
  with no `absences` entry. Expect exit 1, reason 2.
- **a doctored agreement flag** - a tree where the walker and the tool differ (a key under a
  map the tool treats as opaque). Expect exit 1, reason 5.
- **a doctored census / undeclared** - a college key present and empty in every file with no
  `absences` entry. Expect exit 1, reason 3.
- **step 1** - a baseline with one total moved. Expect exit 1, reason 1 (f-34's case, re-run).
- **a stale acknowledgement** - a clean tree whose baseline names a finding. Expect exit 1, reason 6.
- On the pure function, a written result with its lists or its flag edited by hand: every
  reason reachable, and the clean result returning no reason.

Small synthetic trees carry the doctoring so each case costs seconds; they go through
`main()` and the target's real `cfb_contract_diff` (a git archive of a-78's commit). The two
real trees are run as well.

## What must pass, and what I predict

| run | prediction |
|---|---|
| a-78's tree, f-34's `baseline-a78.json` unchanged | exit 0. 0 MISSING, undeclared 0, agreement True on both kinds. |
| a-72's tree, f-34's `baseline-a72.json` unchanged | **exit 1**, reasons 2, 3 and 5. This is the predicted consequence of the rule, not a regression: that baseline names nothing. |
| a-72's tree, a baseline that names the five id keys (source f-30) and `sport_manifest.unresolved_ids[].name` (source a-72's own stated total) | **exit 0**, with all five MISSING lines and `False` still printed, and a line saying they are acknowledged and by whom. |
| a-72's tree, the same baseline naming four of the five | exit 1, reason 2, naming the fifth. |

## Verdict words

- **`exit status repaired`** - every must-fail case exits 1 for the stated reason, a-78's tree
  exits 0 on its unchanged baseline, and a-72's tree exits 0 once its baseline names the six
  known findings.
- **`repaired but turns a declared absence red`** - any must-fail case holds but a path that
  IS under an `absences` entry in the tree's own manifest produces a non-zero exit (a-78's
  tree going red on its unchanged baseline is this), or a-72's tree cannot be made green by
  naming its known findings. Reported, and the rule is then not left on the default path.
- **`left as designed, with the reason`** - the rule cannot be made to fail correctly.

Nothing else. a-72's tree exiting 1 on a baseline that names nothing is not, by itself, the
second verdict: nothing under one of that tree's `absences` entries is involved.

## Outside this rule, said now so it is not read into the result later

- The walker also prints `FILLED-BUT-DECLARED` (seven lines on both trees) and blind-zone
  paths. The brief names four must-fail cases and these are not among them; they stay
  printed and do not move the exit status. Exit 0 will not mean "no FILLED-BUT-DECLARED".
- Steps 2-5 of the unit (the six sibling figures, the widened test, the stale manifest) carry
  no verdict word here. Each is reported as what was run and what happened.
- Decision taken unattended: acknowledgement lives in the baseline file, not in a flag such
  as `--allow-missing`. A flag would make the tolerant path a default somebody types once; a
  named entry with a source is a statement that can be read and can go stale loudly.
