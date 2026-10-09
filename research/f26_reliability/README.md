# f-26 — the standing reliability agent

Adversarial, read-only, re-runnable. Each run picks the claims that finished since
the last run, attacks them in a fixed order, and writes one verdict per claim.
**A run that finds nothing is a valid run** — it then has to name what it tried.

Nothing here writes to a store. The only file a run writes outside scratch is
`<relay>/state/f26-marker.json`.

## Run it

    PY=D:/calibrated-sports/venv-f/Scripts/python.exe        # the 3.12 venv; never the default interpreter
    RELAY="C:/Users/Ethan Davis/code/_relay"

    0. $PY research/f26_reliability/selfcheck.py
         -> every step driven to its FAILING answer on planted data. Exit 1 = a step cannot fail; stop.
    1. $PY research/f26_reliability/select.py "$RELAY" --needs-ethan
         -> the ranked targets, the units with no interval claim, and the needs_ethan repeat count
    2. for each target, top down, as far as the time allows:
         git worktree add --detach D:/temp/f26/<unit>src origin/<the unit's branch>
         run its adapter (below), or write one - an adapter is ~150 lines over f26lib
    3. write verdicts.json: {"run_id", "at", "verdicts": {unit: "<verdict>: <sentence>"}, "declined": {unit: why}}
       $PY research/f26_reliability/select.py "$RELAY" --record verdicts.json
    4. git worktree remove the scratch checkouts; report.

Adapters are run **as scripts, not with `-m`**: `research` must resolve to the
TARGET's package (its worktree goes first on `sys.path`), and each adapter refuses
if the target module resolved anywhere else.

## The marker

`<relay>/state/f26-marker.json` — `window_start`, `verdicts` (unit → who, which run,
what), `declined` (unit → why), `runs` (what each run saw, attacked, and ranked but
did not reach). `runs/` in this directory keeps a committed copy of each run's
verdict file so the record survives the relay folder.

A unit leaves the window **only** by receiving a verdict or a `declined` entry. A
unit that was ranked and not reached is still a target next run; nothing ages out.

## Target selection (the code is `select.py`; this is the same rule in words)

- **Window**: every machine report finished after `window_start` (fixed at f-23's
  finish, 2026-09-30T01:38 EDT — the last adversarial unit before this one) that has
  no verdict and is not itself an attack unit. **An attack unit is recognised by what it touched,
  not by its number** (f-31): a track-F unit whose `files_changed` touch this directory ran the agent. The
  number regex this replaced had to be extended by hand every run - it ranked f-27, the agent's own repair,
  as a target, and once widened to f-27/f-29 it also swallowed f-28, which is not an attack unit. The regex
  survives only for units that left no file here (f-21..f-23, f-30). `select.py` prints who it excluded and why.
- **Target**: a unit whose report states an estimate with an interval. No interval,
  no claim this checklist can attack; those units are listed, not ranked.
- **Rank**: +3 if a page will publish the claim (the unit's own files touch an export,
  the contract or a site route, or a unit that does names it); +1 if it names its MDE;
  +1 if its headline interval excludes zero. Newest first on ties.
- **A carrier is attacked through its source.** a-63 publishes c-28's numbers and adds
  none; the attack runs on c-28's code and the verdict is recorded against both.

**Known limit of the rank (run 1).** "Named by a unit that publishes" is a mention,
not a publication: a-70 names c-32 to say *no c-32 figure is in the file*, and c-32
scored +3 for it. The error runs in the safe direction (it over-ranks), so it is left
in; read the carrier's sentence before believing the +3.

## The attack checklist, in order

Stop at the first step that fails; that step is the finding.

1. **Reproduce.** The headline to the precision it was stated at, on today's store,
   with the target's own code from a detached worktree. An interval bound redrawn
   under a different seed is compared within 0.25 bootstrap SE, not by rounding.
   A figure that moved because an input was rebuilt is reported as *not reproducing*,
   with the input named — the verdict may still stand.
2. **Blocks.** One test and two descriptions. **Run 1's version of this step could not
   fail** (f-27): `duplication()` resampled with *this* file's bootstrap, so its ratio was
   1.000 by construction, and it printed `x1.000 (passes)` on 15 statistics. It now raises.
   - `duplication_through()` — **the test.** The claim's rows are handed to the **target's
     own** bootstrap function, then the same rows x5 with their block label kept, then a
     twin where every copy is its own block. Verdicts: `honours_blocks` (x1.0, twin
     ~1/sqrt 5); `row_bootstrap_one_per_unit` (narrows on copies, but rows == distinct
     games, so it is a game bootstrap by accident); **`NARROWS`** (narrows and rows >
     units — the published interval is too narrow by about sqrt(rows/units)).
     It needs `fn_name` and `units` (distinct games **counted by the attacker**) and
     refuses without them. Every claim an adapter attacks must have one:
     `require_through()` exits non-zero on a gap.
   - It does **not** test the block *label*. A target that called every rung its own game
     passes it. Compare `rows` with `units`, and run `alt_blocks()`.
   - `iid_contrast()` — descriptive, no pass/fail key: how much narrower an unblocked
     interval would be. ~1.00 means blocking is immaterial to that claim (one row a game).
   - `alt_blocks()` — the statistic under coarser blocks (week, season, kickoff slot).
     A result that needs the finest block to exclude zero is carried by dependence it
     ignored. **Fewer than 5 blocks is not read** — the primitive nulls `excludes_zero`.
3. **Leakage.** Scramble every input dated at or after a cutoff and assert that no
   earlier forecast moves. Every audit carries a **planted leak** (or a real one it is
   seen to fire on) so a clean result is not a blind check. **"Later forecasts moved" is
   not a plant** — it fires on a leak-free pipeline (selfcheck shows it), so it proves the
   scramble ran and nothing more. The plant that counts is one that makes an *earlier*
   forecast move: file one late game under week 1 and scramble from mid-season
   (`attack_c30.py`, `attack_c31.py`, `attack_c28.py` 3a). Parameter fits get their
   own audit — refit on a store truncated before each season — because the scramble
   is blind to a leak that leaves a grid argmin where it was (found on run 1).
4. **Specifications.** Count what the unit itself registered, Bonferroni on
   z = estimate / bootstrap SE over that count. The count comes from the target's result
   file through `registered_count()`, which **refuses** when it is absent (run 1 fell back
   to k = 2 silently); a zero-variance bootstrap or one on < 5 blocks enters at p = 1.
   Counting a specification the unit did *not* register is reading, not code. Then look for the specification nobody
   counted: a comparator held at a grid edge, a smoke run that disagreed, a second
   result file. Say whether the interval survives.
5. **MDE.** |estimate| / (2.8 × SE). *below* (< 0.8): a null the population could not
   have resolved. *at* (0.8–1.25): conditioned on having cleared the bar — a coin flip
   dressed as a result. *clear* otherwise. **This is not an independent step**: the ratio
   is |z| / 2.8, the same z step 4 used, so it cannot disagree with step 4 and a headline
   that excludes zero can only read *at* or *clear* (apart from |z| in 1.96-2.24). What can
   contradict the target is `mde_claim()` — the MDE the unit **stated** against 2.8 x the
   SE re-measured here.

Where the claim is a regression on a shared term (c-32: close−open on model−open),
add the null the claim actually needs — a permutation of the model across games and
the joint coefficient with the shared term held fixed. Zero is the wrong null there.

## Claims without an interval (run 3)

A census ("556 of 556 mapped before kickoff") has no interval, so the selector does not rank
it and steps 2, 4 and 5 do not apply. It is still attacked when a brief names it: re-count it
with the attacker's own query, then ask of each column it leans on **could this have read
otherwise at the moment it was read** - a timestamp standing in for an event, a first-seen
standing in for a listing, a condition that every row passes because nothing it tests has
happened yet. Plant the failing row in memory and show the count move.

**A hash that is identical before and after is evidence only if the unit could have moved it.**
Diff the files the hashed job imports first; if the unit touched none of them the hash is a
restatement of the diff. Then plant a change and show the hash moves.

## A null is attacked for its power, not only its arithmetic (run 4)

A null that reproduces can still be worded past what its test could see. Two checks, both from run 4:

- **Push a planted effect through the target's own pass rule.** c-41's rule required the out-of-sample gain to
  exceed its realized MDE; by construction that passes a true effect at the MDE about half the time. Its stated
  "rules out about 2 points per sd" was the rule's 9% point; the 80% point was 4.5-5. The plant also shows the
  rule CAN pass, which is what makes the null a null.
- **Ask what the perturbation could have reached.** c-34's "MDE is 6-11% of the gap" was arithmetic on an
  adjustment that, made perfect on every row it touches, closes 15% of that gap. Measure the ceiling before
  calling a null tight.

And for a positive: **find the null the claim needs.** c-40's 66.1% rejected a uniform null at p 2e-19 and sat at
the 62nd percentile of the label-permutation null; c-35's 95.8% was far from noise and equal to what another
player's ladder gives.

**Sub-agents (run 4).** Six targets were attacked by one sub-agent each, in parallel, from one written brief.
Every adapter was then re-run by the run itself with its own exit code, because a sub-agent's summary is a
report and not a measurement. Tell a sub-agent never to kill processes by image name: one ran
`taskkill /F /IM timeout.exe` and removed the time limit from two other adapters mid-run.

## Verdicts

- **citable** — survived every step that was run; the report names the steps.
- **citable only with a stated sentence** — the number stands and the sentence that
  must travel with it is written out.
- **not citable** — does not reproduce, or the interval does not survive.

A step that was **not run** is named in the verdict. A rows-only audit
(`attack_rows.py`) has run no leakage step and says so.

## needs_ethan, every run

`select.py --needs-ethan` counts the items raised by the window units no earlier run
has seen (the marker's `seen` list - so an item is counted by one run only) and, for
each, the most similar item raised by an earlier-finished unit (token-set Jaccard on
`what`, unit ids kept whole). **It is a text proxy.** It is printed at three
thresholds with every pair above 0.25, and the run reports the pairs it read by hand
as repeats separately from the proxy count. It cannot see a repeat worded differently,
so the hand count is a floor.

## Files

    f26lib.py        the primitives: reproduce, duplication_through (the blocks test), iid_contrast,
                     alt_blocks, seeds, multiplicity, registered_count, mde_ratio, mde_claim
    selfcheck.py     every step driven to its failing answer on planted data; run it first
    select.py        window, rank, needs_ethan repeats, marker
    attack_c28.py    c-28's moneyline record (published by a-63)   pipeline re-run, all five steps
    attack_c30.py    c-30's spread record (published by a-64)      pipeline re-run, all five steps
    attack_c31.py    c-31's total record (published by a-64)       pipeline re-run, all five steps
    attack_c32.py    c-32's line-move slope                        own script re-run + permutation null
    census_a73.py    a-73's mapping census (no interval)            own count on the live store, mode=ro; three
                     proxies measured (created_ts, first_seen vs open_ts, kickoffs still ahead); --plant
    attack_c39.py    c-39's college game model                      pipeline re-run, steps 1-3b; ~25 min, so give
                     the background job an explicit timeout - run 3's was stopped at the harness's 30 minutes
    tail_c39.py      c-39 steps 3c, 4, 5 from the recorded fits   valid only after attack_c39.py step 1 prints
                     `0 of 21 seasons differ`
    identity_c39.py  c-39's NFL season-model hash, and a plant that moves it
    attack_c37.py    c-37's yards result, from its scratch rows     arithmetic only, NO leakage step
    attack_c29.py    c-29's CLV record                              own script re-run, compared lean by lean
    receipts_blocks.py  the Receipts hit rate, from the Board ledger   per-lean interval against block intervals
    attack_c38.py    c-38's cross-sport contrast                    pipeline re-run (~7 min: give it a timeout) or cached
                     rows; carried-walk scramble with a misdated-game plant; an unregistered re-binning
    attack_c41.py    c-41's residual null                           own script re-run; a POWER PLANT - a synthetic
                     candidate of known size pushed through the target's own pass rule (--plant-reps, --plant-b)
    attack_c40.py    c-40's counterfactual ledger, from its rows    NO leakage step; the label-permutation null the
                     claim-about-misses needs; the 19 excluded leans bounded
    attack_c35.py    c-35's ladder edges, from its extract cache    NO leakage step; a measured null for the level share
    attack_c34.py    c-34's injury null                             three processes (--part a | blocks | b), b is the
                     pipeline; 'is the arm inert' and oracle / toward-the-close plants
    attack_c33.py    c-33's soft markets, from its archived extract the slope as a specification, not as sampling error
    sentences_a75.py a-75's attached sentences (no interval)        carried figures traced to F's own run files; the gate
                     and served_matches driven to both answers; where the figure is still served bare
    uptime_a74.py    a-74's poll-gap uptime (no interval)           own re-count on poll_log, mode=ro; the outages on
                     record; what 'up' hides when one venue is silent
    clusters_f28.py  f-28's cluster count (no interval)             replayed to the registered population hash; where a
                     cluster count is written
    attack_rows.py   any coefficient published with its rows       arithmetic only, NO leakage step;
                     --src/--boot hand the rows to the target's own bootstrap (without them the
                     blocks step prints NOT RUN, it does not pass)
    runs/            one verdict file per run

Only an adapter that hands rows to a target's bootstrap is named `attack_*.py`; `tests/test_f26_reliability.py`
requires `duplication_through` in every one. A census (`census_a73.py`), the second half of a split run
(`tail_c39.py`) and a re-draw whose target function is not runnable from here (`receipts_blocks.py`: the
Receipts interval is computed by the site, in TypeScript) are named otherwise, and their verdicts say the
through test was NOT RUN.

## What a clean result from each step does NOT cover (f-27, pinned in `selfcheck.py`)

| step | blind to | what covers it |
|---|---|---|
| 1 reproduce | a wrongly blocked interval when the unblocked one is within ~13% of its width (the 0.25 SE tolerance) | step 2 |
| 1 reproduce | a `PUBLISHED` constant typed into the adapter that is not the figure a page serves (run 1 attacked c-31's +0.0044; `record_total.json` serves +0.0040, a different comparison) | fetch the served file and compare, by hand today |
| 2 blocks | a block **label** that is not the dependence unit | `rows` vs `units`, `alt_blocks()` |
| 3 leakage | a fitted parameter whose grid argmin the scramble does not move | a truncated refit - only `attack_c28.py` 3c has one |
| 4 specifications | every specification the unit did not register | reading |
| 5 MDE | nothing step 4 did not already see | `mde_claim()` |
