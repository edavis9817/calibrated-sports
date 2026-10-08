# f-32 pre-registration - the `grid_fit` mask that lets a season see its own future

Written and committed BEFORE any figure in this unit was produced. Nothing below is
edited after the first measurement; corrections go at the bottom, dated.

Target: `models/cfb_game.py::grid_fit` at `origin/c-39-cfb-game-model`, read-only, run
from a detached worktree against `cfb.db` opened `mode=ro`. `grid_fit` accumulates one
`bad` mask over every game it walks and applies `sums[:, bad] = nan` to every season's
row, so `best_params(..., year=T)` chooses from a set that games in seasons >= T shaped.

## Scope stated in advance

- f-29's "harmless on 2006" is evidence about 2006 and about nothing else. It says
  nothing about the other 20 scored seasons (2005, 2007-2025).
- If this run does not cover all 21 scored seasons, the report names the seasons it
  covered and the verdict is scoped to those seasons only.
- The settlement comparison itself (f-30) is not re-litigated. Its published figures are
  the reference: home -0.0615 [-0.0644, -0.0583], better record -0.0380
  [-0.0406, -0.0352], plain Elo -0.0042 [-0.0049, -0.0034], n 15,508.

## Definitions

- **Season T's as-of choice set**: grid points whose multiplier denominator has not
  reached <= 0 in any game of a season < T. **Removed by the future**: points in that
  set that the whole-walk mask removes. Reported for T = 2005..2025 (and 2026, the
  current-season fit, separately), as a count and as a share of the 207,360-point grid
  and of the as-of set.
- **Truncated refit for T**: `C.grid_fit` on games with season < T, then
  `C.best_params(grid, ..., FIT_FROM, T)`. MOV grid and plain grid both. "Constants
  differ" means the returned `CfbParams` is not equal to the full walk's for T.
- An instrumented single walk (this unit's own copy of the loop, recording the first
  season each point goes bad and leaving sums unmasked) may be used to derive all 21
  truncated choices at once. It counts only if (i) its masked sums equal `C.grid_fit`'s
  on the fit seasons and (ii) genuine `C.grid_fit` truncated refits, for every season
  that finishes tonight, agree with it. Seasons verified only by the instrumented walk
  are reported as such, separately from seasons verified by a genuine truncated refit.
- If a truncated fit chooses a point that the scalar walk later refuses (raises in
  `multiplier`), that IS "constants differ"; the season's forecasts are then computed
  with `grid_fit`'s own substitution (den <= 0 -> 1.0) and labelled as such.

## The materiality threshold - fixed now

Change in a published dBrier (as-of/truncated fits minus the published walk), per arm:

    arm            published interval       width = MATERIAL beyond this
    home           [-0.0644, -0.0583]       0.0061
    better record  [-0.0406, -0.0352]       0.0054
    plain Elo      [-0.0049, -0.0034]       0.0015

A move larger than the arm's own interval width in ANY arm is material. Additionally,
any move >= 0.00005 (visible at the published 4 dp) is reported as "visible, not
material". The binding number is **0.0015** (the plain-Elo arm). It is stated in the
report whether or not it is reached.

## The verdict - one of three

- **(a) harmless** - no covered season's truncated refit changes a chosen constant, AND
  the full-pipeline scramble leaves every pre-cutoff forecast unmoved, AND the plant
  moves them (the detector is shown to fire).
- **(b) material** - at least one season's constants change AND a published settlement
  dBrier moves by more than its own interval width (table above).
- **(c) material but bounded** - constants change and no figure moves beyond its width.

If the scramble's plant does not fire, the scramble is reported as BLIND and cannot
support (a); the verdict then rests on the truncated refits alone and says so.
"Harmless" is a statement about the published figures on today's store and the
registered grid. It is not a statement that the mask is correct code: independent of the
verdict, the report states whether the mask CAN change a choice (on any grid), because a
defect that did not bind this time is still a defect for track C.

## The full-pipeline scramble

Scores of every completed game with start_ts >= cutoff are scrambled (f-29's rule: swap
and perturb, no ties). The genuine pipeline - `CF.Walk(games, grid(GRID_MOV), mov=True)`,
i.e. `grid_fit` + `best_params` + the scalar walk - is re-run on the scrambled store.
Detector: number of forecasts with kickoff < cutoff that move by > 1e-12, and number of
seasons T (preseason before the cutoff) whose fitted params change. Expected under no
leakage: 0 and 0. Cutoff: the median Part-1 kickoff (one cutoff minimum; more if time).

Plants (each must move pre-cutoff forecasts, or the detector is blind to that channel):
- **P1, rating channel**: one late-2019 game re-dated before the 2019 opener.
- **P2, mask channel**: a future-only change (games at/after the cutoff only) that flips
  the `bad` bit of a point an earlier season would otherwise choose. On the registered
  grid this may be impossible if every chosen point has a = None (the mask cannot touch
  a = None). If so it is run on a declared sub-grid (the registered grid without
  a = None) and reported as a demonstration that the channel exists, NOT as a statement
  about the published figures. If no future-only change can be constructed that flips a
  choice even there, that is reported as the result.

## Conference / division as-of

Columns under test: `cfb_games.home_conference`, `away_conference`, `home_division`,
`away_division` (what `CF.load` reads). Test, fixed now - a label is the SEASON'S OWN
if these read correctly on the store's current rows (`valid_to_ts IS NULL`):

    Nebraska      2010 Big 12        2011 Big Ten
    Texas A&M     2011 Big 12        2012 SEC
    Utah          2010 Mountain West 2011 Pac-12
    TCU           2011 Mountain West 2012 Big 12
    Maryland      2013 ACC           2014 Big Ten
    Texas         2023 Big 12        2024 SEC
    division: Appalachian State 2007 fcs; James Madison 2021 fcs / 2022 fbs;
              Massachusetts 2011 fcs / 2012 fbs

All correct -> "the labels are season-vintage". Any today's-label on an old game ->
leakage, named with the count of affected FBS-FBS games. Separately: whether a label was
KNOWN BEFORE the season (preseason-knowable) versus restated later is answered from row
versions (`valid_from_ts`) and the ingest source, or reported as "the store cannot tell
us", with the reason.

## What is not done

No change to `grid_fit` or to any c-39 file. No re-run of the price comparison. No
write to any store. No publish, stage or mint.
