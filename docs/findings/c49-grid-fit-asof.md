# c-49 - `grid_fit`'s mask is now as-of

Verdict, by the registered rule (`docs/C49-grid-fit-asof-preregistration.md`, 4ceaeca): **repaired**.

Scope of every figure here: `models/cfb_game.py` and `research/cfb_game_forecast.py` at c0b4307
against the base commit e88f114, on `cfb.db` as read on 2026-10-08 (18,991 FBS-FBS games, 15,508
forecasts in the 2005-2025 population, one store fingerprint across all eight walks), `grid_fit`
called 8,640 points at a time. Script `research/c49_grid_fit_asof/`, result
`research/c49_grid_fit_asof/results/c49_grid_fit_asof.json`. One run.

## What changed in the code

- `grid_fit` masks nothing. It returns `(seasons, sums, counts, first_bad)`; `first_bad[j]` is the
  season of the first game in which point j's multiplier denominator reached <= 0, else `NEVER`.
- `best_params(..., first_bad, fit_from, year)` refuses point j for season T only if
  `first_bad[j] < T`. Season T's choice set is what a walk over the games before T alone leaves
  clean - asserted in `tests/test_cfb_grid_asof.py` on a league where the mask binds.
- `run(..., undefined=[])` substitutes `grid_fit`'s denominator (1.0) and records the game instead
  of raising; without the argument it raises as before. `Walk` passes it, so a game after season T
  cannot remove T's forecasts by exception.
- Old call shapes raise (three-value unpack, `best_params` without `first_bad`).

## The planted case (f-32's, `GRID_MOV` with a = 1.0, 51,840 points)

One synthetic game a day after the last real one: team 2638 wins 1-0 at team 84, rating gap plus
home advantage 1,092 against 1,000 needed. The same planted store was given to both codes.

    code            compared          moved    fits changed        errors
    base (e88f114)  15,508 of 15,508  1,378    2005, 2006          0
    repaired        15,508 of 15,508  0        none of 22          0

- The control reproduces f-32's 1,378 exactly, so the detector fires.
- The repaired zero is not vacuous: every forecast was compared and no season was dropped.
- The plant is still seen by the repaired code: 5,274 points are marked `first_bad = 2026`, the
  choice set for 2027 falls 25,425 -> 20,151, and no choice set for 2005-2026 changes.
- Under the repaired code the points chosen for 2005 and 2006 each take ONE substituted game on the
  planted store - the synthetic game itself, after every forecast in the population.

## The no-change condition (registered grids, base code against repaired code, same store)

    grid    points    constants unchanged   forecasts moved    Brier base -> repaired
    MOV     207,360   22 of 22 seasons      0 of 15,508        0.181909 -> 0.181909
    plain     9,600   22 of 22 seasons      0 of 15,508        0.186073 -> 0.186073

- dBrier against home and against better record moves by 0.000000 (neither baseline reads
  `grid_fit`); against plain Elo it is -0.0042 before and after. Threshold 0.0015. **No published
  figure moves.** Secondary: the repaired fits equal the committed c-39 fits in all 22 seasons.
- 29,505 of 207,360 points go bad in some game. The repair returns 224 (2023-2026) to 9,097 (2005)
  of them to a season's choice set - f-32's range, reproduced. None of the returned points is
  chosen: every fitted point still has a = None.
- Substituted games under any chosen point on the registered grids: 0.

## What this does not establish

- It is a statement about the season-level mask and the scalar walk's exception. `conferences()`
  still reads a season's schedule rows whether played or not; f-32's label question is untouched.
- "No figure moves" is measured on today's store and c-39's grid. A different store or grid can
  choose a returned point; the repair is that such a choice is then made on past games only.
- The substitution rule (denominator 1.0 after a chosen point goes bad) has never fired on a
  registered-grid forecast, so its effect on forecast quality is unmeasured.
- c-39's price comparison, c-38 and c-45 were not re-run; they read the same fits and forecasts,
  which are bit-identical here, but that is an inference from 0 of 15,508 moved, not a re-run.
- The eligibility unit is the SEASON. A point that goes bad in week 3 of season T stays the
  chosen point for the rest of T, under the substitution.
