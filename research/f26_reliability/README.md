# f-26 — the standing reliability agent

Adversarial, read-only, re-runnable. Each run picks the claims that finished since
the last run, attacks them in a fixed order, and writes one verdict per claim.
**A run that finds nothing is a valid run** — it then has to name what it tried.

Nothing here writes to a store. The only file a run writes outside scratch is
`<relay>/state/f26-marker.json`.

## Run it

    PY=D:/calibrated-sports/venv-f/Scripts/python.exe        # the 3.12 venv; never the default interpreter
    RELAY="C:/Users/Ethan Davis/code/_relay"

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
  no verdict and is not itself an attack unit.
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
2. **Blocks.** Three separate things, because the first one alone proves nothing:
   - `duplication()` — rows ×5 inside their block under *this* file's bootstrap. Its
     ratio is 1.000 **by construction**; it documents what a correct interval does.
   - `duplication_through()` — the same rows handed to the **target's own** bootstrap
     function, and a twin where every copy is its own block, which must narrow by
     ~1/√5. This is the one that tests the target.
   - `alt_blocks()` — the statistic under coarser blocks (week, season, kickoff slot).
     A result that needs the finest block to exclude zero is carried by dependence it
     ignored. **Fewer than 5 blocks is not read**, whatever it excludes.
3. **Leakage.** Scramble every input dated at or after a cutoff and assert that no
   earlier forecast moves. Every audit carries a **planted leak** (or a real one it is
   seen to fire on) so a clean result is not a blind check. Parameter fits get their
   own audit — refit on a store truncated before each season — because the scramble
   is blind to a leak that leaves a grid argmin where it was (found on run 1).
4. **Specifications.** Count what the unit itself registered, Bonferroni on
   z = estimate / bootstrap SE over that count. Then look for the specification nobody
   counted: a comparator held at a grid edge, a smoke run that disagreed, a second
   result file. Say whether the interval survives.
5. **MDE.** |estimate| / (2.8 × SE). *below* (< 0.8): a null the population could not
   have resolved. *at* (0.8–1.25): conditioned on having cleared the bar — a coin flip
   dressed as a result. *clear* otherwise.

Where the claim is a regression on a shared term (c-32: close−open on model−open),
add the null the claim actually needs — a permutation of the model across games and
the joint coefficient with the shared term held fixed. Zero is the wrong null there.

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

    f26lib.py        the primitives: reproduce, duplication, duplication_through, alt_blocks,
                     seeds, multiplicity, mde_ratio
    select.py        window, rank, needs_ethan repeats, marker
    attack_c28.py    c-28's moneyline record (published by a-63)   pipeline re-run, all five steps
    attack_c30.py    c-30's spread record (published by a-64)      pipeline re-run, all five steps
    attack_c31.py    c-31's total record (published by a-64)       pipeline re-run, all five steps
    attack_c32.py    c-32's line-move slope                        own script re-run + permutation null
    attack_rows.py   any coefficient published with its rows       arithmetic only, NO leakage step
    runs/            one verdict file per run
