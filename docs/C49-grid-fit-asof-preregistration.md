# c-49 pre-registration - repair `grid_fit` so its mask is as-of

Written and committed BEFORE `models/cfb_game.py`, `research/cfb_game_forecast.py`, any test or
any c-49 script is changed or created, and before any figure in this unit is computed. Nothing
below is edited after the first run; corrections go at the bottom, dated.

Branch `c-49-grid-fit-asof`, cut from `origin/c-48-c37-figures` (e88f114). Inputs, read and not
re-derived: `origin/f-32-grid-fit-mask` (b498d2c) `research/f32_grid_mask/plant_mask.py`,
`mask_audit.py`, `PREREGISTRATION.md`, `runs/2026-10-08/plant_mask.json`; c-39's registered grid
(`research.cfb_game_forecast.GRID_MOV`, `GRID_PLAIN`). `cfb.db` is opened `mode=ro`;
`market_log.db` is not opened. Zero credits, no export, no publish.

## The defect, as read from the code

`models.cfb_game.grid_fit` accumulates ONE `bad` mask over every game it walks and applies
`sums[:, bad] = nan` to every season's row. `best_params(..., year=T)` therefore chooses from a
set that games of seasons >= T shaped. f-32 measured it harmless on the registered grid today
((a), not re-opened here) and showed the channel is real on an `a = 1.0` grid.

## The repair, fixed now

1. `grid_fit` stops masking. It returns `(seasons, sums, counts, first_bad)`: `first_bad[j]` is
   the SEASON of the first game in which grid point j's multiplier denominator reached <= 0, or
   `NEVER` (a sentinel above any season). `sums` keeps every point's summed log loss under the
   substitution `grid_fit` already performs (`den <= 0 -> 1.0`).
2. `best_params(params_list, seasons, sums, counts, first_bad, fit_from, year)` may choose point
   j for season T only if `first_bad[j] >= T` - the point has not gone bad in any game of a
   season before T. That is f-32's "season T's as-of choice set", and it is what a `grid_fit`
   call on the games of seasons < T alone would mask. A point eligible for T has had no
   substitution in any season < T, so its sums over the fit window are exact.
3. The return arity and the `best_params` signature both change, so a stale call site raises
   (`ValueError` on unpacking, `TypeError` on the call) instead of silently using unmasked sums.
4. The scalar walk. A point chosen as-of T can go bad in a game of season >= T, and
   `models.cfb_game.run` raises there today - so one future game would remove a season's
   forecasts by exception, the same channel in a different carrier. `run` gains an optional
   `undefined` list: when given, a game whose denominator is <= 0 takes `grid_fit`'s
   substitution and is appended to the list; when omitted, `run` raises exactly as now.
   `research.cfb_game_forecast.Walk` passes it and keeps the list per chosen point. This is the
   rule f-32 pre-registered for the same case ("computed with `grid_fit`'s own substitution
   (den <= 0 -> 1.0) and labelled as such"). The number of substituted games before the end of
   each scored season, under that season's chosen point, is REPORTED for both stores below; on
   the registered grid it is expected to be 0, and the repair does not depend on that.
5. No grid, constant, ordering, conference label or scoring rule changes. `GRID_MOV` and
   `GRID_PLAIN` are c-39's and are not edited. Nothing under `research/f32_grid_mask/` is
   edited; the planted case is copied to `research/c49_grid_fit_asof/` and cites its source.

## Seasons, grids, chunk - fixed now

- Seasons: every fit season 2005-2026 (22). Forecast population: FBS-FBS completed games of
  2005-2025 (f-32's 15,508 on its store; today's count is reported beside it).
- No-change run: the registered MOV grid (207,360 points) and the registered plain grid
  (`GRID_PLAIN`), each walked once by the code at the base commit (e88f114, from a detached
  worktree) and once by the repaired code, on the same `cfb.db` read in the same session.
- Planted run: `GRID_MOV` with `a = [1.0]` (51,840 points), MOV, f-32's case.
- Chunk: `grid_fit` is called on **8,640** grid points at a time and concatenated (f-32 measured
  bit-identical sums). At most two walks run at once. No process is killed by image name.
- One registered run of each. No re-fit of the published model, no new specification.

## Pass condition - the planted case (stated before any run)

The store is f-32's: today's games plus ONE synthetic game dated one day after the last real
game, the lowest-rated current-season team winning 1-0 at the highest-rated one, both under the
BASE code's 2005 fit on the `a = 1.0` grid (f-32: team 2638 at team 84). The synthetic game is
built once, from the base code, and the SAME planted store is given to both codes.

- **Control (the detector must fire):** under the base code, f-32 measured **1,378 of 15,508**
  earlier forecasts moving by more than 1e-12, with fits changed in 2005 and 2006. This unit
  expects to reproduce 1,378 and reports whatever today's store gives. If the control moves 0,
  the detector is BLIND and no pass can be claimed.
- **Pass:** under the repaired code, the count of earlier forecasts that move by more than
  1e-12 between the unplanted and the planted store is **0** - the 1,378 stop moving - AND the
  fits of all 22 seasons are equal between the two stores, AND every forecast in the population
  was compared (compared count equals the population; no season dropped by an exception). A
  zero that comes from an error or a skipped season is `not repaired`, not a pass.
- **The plant must still be seen:** the repaired `first_bad` on the planted store marks points
  that the unplanted store does not, all with `first_bad == 2026`; the choice set for 2027
  (`first_bad >= 2027`) shrinks by that count and no choice set for a season <= 2026 changes.
  Reported; if the plant marks nothing, the case does not exercise the repaired code and that
  is said.

## No-change condition (stated first, before the run)

On today's store with the registered grids, the repaired code must return **the same fitted
constants for 22 of 22 seasons** (MOV and plain, against the base code in the same session) and
move **no published dBrier by more than 0.0015**, f-32's binding threshold.

- dBrier movement is measured as: (i) change in the MOV model's Brier over the 2005-2025
  population - which IS the change in dBrier against `home` and against `better record`,
  because neither baseline reads `grid_fit`; (ii) change in (MOV Brier - plain-Elo Brier).
  Also reported: max |delta p| over every forecast, MOV and plain, and the count above 1e-12.
- Secondary, reported and not part of the rule: the repaired fits against the fits in the
  committed `research/results/cfb_game_forecast.json` (c-39's run, an earlier store).
- Also reported: per season, the size of the as-of choice set against the whole-walk one (f-32:
  224 to 9,097 points returned to the set).
- **If a constant or a published figure moves, the result is that value and its old value side
  by side.** Nothing committed under `research/results/` or `docs/findings/` is overwritten by
  this unit, whichever way it comes out.

## Verdict words - one of three, nothing else

- `repaired` - planted case 0 moved (pass condition in full) and 22 of 22 constants unchanged
  with no dBrier moved beyond 0.0015.
- `repaired with a moved figure` - planted case 0 moved, and some constant or dBrier moved; both
  values reported.
- `not repaired` - the planted case still moves, or its zero is vacuous.

## What this unit does not do

It does not re-open f-32's (a) harmless verdict, re-run c-39's price comparison, change the
grid, touch `cfb_games` labels, or hold the college publish: f-32 says do not wait on this and
the publish is Ethan's decision. `conferences()` still reads a season's schedule rows played or
not - that is c-39's stated design and f-32's separate label question, not this mask.
