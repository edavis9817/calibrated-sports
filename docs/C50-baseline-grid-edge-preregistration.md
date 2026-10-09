# c-50 pre-registration - plain Elo's K grid, widened, and the weeks 1-4 sentence over seeds

Written and committed BEFORE any c-50 script, test or result exists and before any figure in this
unit is computed. Nothing below is edited after the first run; corrections go at the bottom, dated.

Branch `c-50-baseline-grid-edge`, cut from `origin/c-49-grid-fit-asof` (bed04d4). Sport: NFL.
Store: `market_log.db`, opened `mode=ro` through `jobs.season_model.market_log_ro` and nothing
else; only `nfl_games` and `nfl_teams` are read. Zero credits, no export, no publish. Nothing in
`models/`, `jobs/` or `research/game_forecast.py` is edited: the walk, the fit rule and the
scoring population are c-28's, imported.

## What is already known, and from where (read, not re-derived)

Written down because it bounds how blind this registration is.

- c-28 (`docs/findings/game-forecast.md`, script `research/game_forecast.py`): the plain-Elo
  baseline `elo_nomov` is fitted on the season model's grid `jobs.season_model.GRID` -
  K in {10, 15, 20, 25, 30, 40}, hfa in {0, 25, 50, 75}, regress in {0, 0.25, 1/3, 0.5, 0.6,
  0.75}, 144 points, the SAME grid the margin-of-victory model is fitted on. Published: dBrier
  model minus plain Elo -0.0027 over 6,743 decisive games 2001-2025; REG weeks 1-4
  -0.0008 [-0.0024, +0.0007].
- f-26 (`_relay/reports/json/f-26.json`): plain Elo's fitted K is 40, the grid maximum, in 25 of
  25 scored seasons. With K in {50, 60, 80, 100, 120} added, the fitted K is 40 or 50; headline
  -0.0027 [-0.0036, -0.0019], excluding zero in 10 of 10 seeds; REG weeks 1-4 registered grid
  -0.0008 [-0.0024, +0.0009], widened -0.0017 [-0.0031, -0.0004] on 1,547 games, ONE seed, with
  f-26's own bootstrap code (not c-28's).
- a-79 (`_relay/reports/json/a-79.json`): a weeks 1-4 figure of -0.0030 against plain Elo on the
  SYNTHETIC test league. That is a fixture figure. It is not re-run here, not compared with any
  store figure and not pooled with one; it is named once in the record as a different object.

So I know before writing this that the fitted K lands at 40-50 on a coarse widened grid and that
one seed excluded zero. I do not know the profile between 40 and 60, the hfa or regress edges,
or any second seed for the weeks 1-4 cut.

## Grids - fixed now

- **REGISTERED** (c-28's): `S.GRID` as above. K endpoints 10 and 40.
- **Arm P, K widened (primary; the brief's question and f-26's):**
  K in {10, 15, 20, 25, 30, 35, 40, 45, 50, 55, 60, 65, 70, 75, 80, 100, 120} - step 5 from 10
  to 80, then 100 and 120. Endpoints 10 and 120. hfa and regress grids unchanged. 408 points.
- **Arm S, all three widened (specification sensitivity, descriptive):** arm P's K, hfa in
  {0, 25, 50, 75, 100}, regress in {0, 0.25, 1/3, 0.5, 0.6, 0.75, 0.9}. 595 points. Declared now
  because a baseline pinned in hfa or regress is the same defect in another dimension and I have
  not looked at either.
- The MODEL (margin-of-victory Elo) is NOT refitted on any widened grid. It stays on `S.GRID`,
  as published. Its own edge counts are reported (see below) and nothing is done about them here.
- Fit rule, unchanged: `S.best_params` - lowest mean log loss on seasons 2000..T-1, first point
  in grid order on a tie within 1e-12. Grid order is `itertools.product(K, hfa, regress)` with
  each list ascending.
- **One widened grid per arm, one run. The grid is not widened again after looking.**

## "Interior" - the rule, stated before looking

For a season T and a dimension, the fitted value is **interior** when it is neither the smallest
nor the largest value of that dimension's grid. For K additionally: the K profile
`min over (hfa, regress) of fit log loss` at the fitted K must be strictly lower than at both
neighbouring grid K values (true by construction of an argmin except on ties, and reported).

- **"Interior in most seasons"** = interior in K in **at least 20 of the 25** scored seasons
  2001-2025 (2026's fit is reported and not counted).
- If arm P's K is interior in fewer than 20: the finding is "plain Elo's K does not go interior
  on a grid to 120", a finding about the baseline's specification, and the comparisons below are
  still reported at arm P as "widened, not interior". No further widening.
- Reported per season, for REGISTERED, P and S: fitted K, hfa, regress, fit log loss, and which
  dimensions sit at an edge. Reported for the MODEL on `S.GRID`: the same, per season. hfa = 0
  and regress = 0 are natural lower bounds but still grid endpoints and are counted as edges,
  labelled "lower bound".

## Populations - c-28's, unchanged

- ALL: scored decisive games, 2001-2025, REG and POST (c-28's `pop1`; 6,743 on c-28's store).
- W14: REG, week <= 4. W5P: REG, week > 4. (c-28's cuts; a-63's `weeks_1_4`, `weeks_5_plus`.)
- Statistic everywhere: mean over games of (model - y)^2 minus (plain Elo - y)^2, y = 1 on a
  home win. Negative favours the model.

## Bootstrap and seeds - fixed now

- The resample is c-28's (`research.ranking_calibration.Pop.boot`): `default_rng(seed)`, each
  draw `rng.integers(0, G, G)` over game blocks, percentile 2.5 / 97.5. One game is one block.
- **2,000 draws per seed (c-28's `rc.BOOT`). Seed panel: the 200 integers 1..200.** Seed 24 is
  c-28's `rc.SEED` and is inside the panel; it is also reported by itself as "the published
  seed".
- **Pooled** = one percentile interval over all 400,000 draws of the panel (200 x 2,000), with
  its SE. This is the low-Monte-Carlo-error interval.
- Reported for every (population, grid) cell: the estimate; per seed lo / hi / sign (all 200
  kept in the JSON); min, median and max of lo and of hi across seeds; the share of seeds whose
  interval lies below zero, contains zero, lies above zero; the pooled interval and SE;
  |est| / (2.8 x pooled SE).
- **What seeds do and do not measure.** Varying the seed moves only the Monte Carlo error of the
  percentile bounds. It does not add games. A share of 100% says the bootstrap bound is stable,
  not that the effect is large against its sampling error; |est|/MDE is the figure for that and
  is printed beside every share.
- Descriptive block sensitivity, same panel, W14 and ALL only: season-week blocks (c-28's own
  sensitivity) and season blocks (25 blocks).

## Checks that must pass before any new figure is read

1. REGISTERED grid, seed 24, ALL: dBrier against plain Elo must equal c-28's published -0.0027
   at 4 dp, and plain Elo's fitted K must be 40 in 25 of 25 seasons (f-26's count). If either
   fails the run still completes, every figure is labelled "store moved since c-28" and the old
   value is printed beside the new.
2. The model's forecasts must be identical (max abs difference 0) whichever baseline grid is
   fitted - a baseline refit that moves the model is a bug, and the run raises.
3. The seed loop must reproduce `rc.Pop.boot` on seed 24 exactly (lo, hi to 1e-15) on W14,
   REGISTERED - my loop is checked against c-28's function, not assumed equal to it.
4. Counts: 6,743 / W14 1,547 expected; today's counts are printed beside them and a difference
   is reported, not hidden.

## Verdict words - fixed now

Threshold share: **0.95** (at least 190 of 200 seeds).

**The weeks 1-4 sentence** (a-63's "no better than plain Elo in weeks 1 to 4"), read at arm P,
game blocks:

- **CHANGED - "better than"**: the interval lies below zero in at least 190 of 200 seeds AND the
  pooled interval lies below zero.
- **CHANGED - "worse than"**: the same with "above zero". (Reachable, not expected.)
- **UNCHANGED - "no better than"**: the interval contains zero in at least 190 of 200 seeds AND
  the pooled interval contains zero.
- **SEED-DEPENDENT AT THIS POWER**: anything else. This verdict is then the published one;
  neither sentence is.
- A qualifier travels with CHANGED: if arm S, or season-week blocks at arm P, falls in a
  different one of these categories, the verdict reads "CHANGED at the K-widened grid;
  specification-dependent" (or "block-dependent") and the wording handed to track A says so.
  If arm P's K is not interior by the rule above, the verdict reads "... at a widened grid whose
  optimum is not interior".
- The same four categories are computed and printed for the REGISTERED grid, so the currently
  published sentence is itself tested for seed stability.

**c-28's headline against plain Elo at the widened grid** (ALL, arm P):

- **CONFIRMS f-26 ("unchanged")**: interval below zero in at least 190 of 200 seeds AND pooled
  below zero - the model still beats a plain Elo with an unpinned K.
- **CONTRADICTS f-26**: the pooled interval contains zero or lies above it.
- Otherwise **seed-dependent**. The estimate and pooled interval are reported beside c-28's
  published -0.0027 and f-26's -0.0027 [-0.0036, -0.0019]; neither is overwritten.
- The baselines `home` and `record` have no fitted grid and are not re-run.

## Multiplicity and reading

c-28 registered the cuts as "not verdicts", and weeks 1-4 is one of three cuts against three
baselines. This unit tests one cut against one baseline at two grids because a published sentence
rests on it; z = est / pooled SE and a Bonferroni p at k = 2 (the two stages) are printed, and
the verdict rule above is the registered one regardless.

A stronger baseline is not read as weakening c-28: the registered baseline was the weakest the
grid allowed. Whatever arm P shows, c-28's result against `home` and `record` is untouched.

## Not done here

No edit to `forecast.json`, `jobs/game_export.py`, any export, any page, c-28's or a-63's
findings prose, or `research/results/f27_required_sentences.json`. The wording each verdict
would need is handed to track A in the report. The c-39 college plain grid is not examined.

## Outputs

`research/c50_baseline_grid_edge/analyse.py`, `results/c50_baseline_grid_edge.json` and `.log`,
`docs/findings/c50-baseline-grid-edge.md`, `tests/test_c50_baseline_grid_edge.py` (the verdict
function driven to each of its answers; the seed loop against `rc.Pop.boot`).
