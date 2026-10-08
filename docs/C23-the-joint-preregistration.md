# C23 pre-registration: are low-threshold multiples underpriced?

Unit c-23 (track C), 2026-09-29. Written and committed **before** any script for
this unit exists.

## The gate the brief set, applied before anything else

The brief says: *"If c-22 comes back with the deep in-the-money bucket at zero or
negative, say so and stop rather than looking for the joint to rescue it."*

c-22 is committed on `origin/c-22-bias-by-moneyness` at `8dd47bb`. Its registered
T1 is the one this gate reads:

- **Deep ITM over (p >= 0.60), every book:** -0.41pp [-1.81, +0.93], n 10,159, 725 games.
- **Receptions only, the stat Ethan's example names:** -0.95 [-2.55, +0.58], n 4,040.
- **0.80-1.00, the zone a "7-average at 4+" leg lives in:** -0.74 [-4.54, +2.77], n 543.

All three are at zero with a negative centre. **The gate fires.** Decision taken
tonight, unattended: measurement items 1-4 of the brief (realised co-occurrence,
same-game vs cross-game, the week common factor, what breaks a deep ITM leg) are
**not run**. Running them after this gate is exactly the "joint rescues it" search
the brief forbids.

This is recorded as a decision so it can be reversed in daylight. What a reversal
would run is written at the end of this file, so it is registered now and not
invented after the fact.

## What this unit does measure

Only the headwind, which the brief requires in the report's first paragraph. It is
arithmetic on c-22's committed aggregates
(`research/results/bias_by_moneyness.json`, `price=all`) and reads no database.

For a two-leg multiple with both legs in bucket b:

- `p` is the bucket's mean de-vigged over price and `c` its median book cost of
  taking the over (`book_cost_over_pp`).
- A book prices the multiple as the product of the vigged legs, so the paid price is
  `(p + c)^2`.
- The **break-even joint mispricing**, in probability points, is `(p + c)^2 - p^2`.
  This is what realised P(both) minus `p^2` would have to exceed.
- The **expected return at fair, independent legs** is `p^2 / (p + c)^2 - 1`.
- The single-leg equivalents are `c` and `p / (p + c) - 1`.
- The exchange route is two separate contracts. Each costs the taker fee `7p(1-p)`c
  plus half the spread, and pays additively. So it **cannot collect a joint
  mispricing at all**: its expected value is the sum of the legs' edges, and c-22
  measured those at zero.

No interval is attached, because nothing here is estimated. It is a deterministic
function of committed aggregates, and those aggregates already carry their own
intervals in c-22.

## What a reversal would run (registered, not executed)

If Ethan overrides the gate, these are the registered tests. The script would be
`research/the_joint.py`, reading `market_log.db` with `mode=ro` only.

- **Population.** Settled over-side NFL props in 2023-2025 REG, at the multiplicatively
  de-vigged all-books close with p >= 0.60, one price per outcome (the c-22 population A
  filter). Legs from the same `outcome_id` family (the same player and stat on different
  lines) are excluded from pairing.
- **J1, same-game.** Across every pair of qualifying legs in one game: mean(1{both}) minus
  mean(p1·p2), in pp.
- **J2, cross-game.** The same quantity for pairs drawn from different games in the same
  week.
  - J1 and J2 are reported separately and never pooled.
- **Intervals.** Every interval is a week-block bootstrap (2,000 draws, seed 23), because
  cross-game pairs share a week. A game-block bootstrap would treat the common factor as
  independent, which is the thing under test.
- **J3, week common factor.** The between-week variance of the per-week mean residual
  (realised minus priced), against the variance a within-week permutation of the
  residuals produces.
- **J4, failure modes of a deep ITM leg.** Among missed legs, the share with:
  - the player's offensive snaps below half of his season median (an early exit);
  - the team's passer changing within the game;
  - the team leading by 17 or more in Q4.
  - Each is reported as a rate, with a Wilson interval over games.
- **Break-even.** Every J1 and J2 estimate is printed beside the break-even above for the
  same bucket.
