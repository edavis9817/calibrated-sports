# c-45 - six claims corrected to what their power supports

Unit c-45 (track C), 2026-10-08. **Wording only. No number in it was measured by c-45**: nothing was re-run, re-fitted, re-drawn or re-measured, no store was opened, and no result file, model or constant was touched. Every figure below is carried from **f-31 run 4** (`_relay/reports/json/f-31.json`, and the verdicts in `_relay/state/f26-marker.json`), which attacked these six units, retired none of them, reproduced every published number and found that "the failures are wording".

This file is the audit trail. Without it a corrected sentence is indistinguishable from drift. `tests/test_c45_corrections.py` pins every pair below: the corrected sentence must be in the file, the replaced one must not be, and both must be here.

**The rule applied to every correction:** weaken or qualify, never strengthen, and never replace a verdict with its opposite. A null worded past its power becomes "not detected" with the detectable size stated; it does not become "an effect is present". Where f-31's verdict does not give a correct wording, the text says the question is open and what would close it.

**Scope of f-31's figures, carried with them.** They are f-31's post hoc measurements about the *wording* of these claims, not new findings about injuries, misses, ladders or books. Its own limits (from its `not_established`): c-41's power table is 80 replications of one synthetic, season-stable candidate in one slot, an upper bound on the rule's power; c-34's 15% ceiling and c-40's permutation null were registered by nobody; c-38's "could not fail" arithmetic, c-34's ceiling, c-35's mismatched-ladder null and c-33's slope table were each computed once and not independently recomputed.

## c-41 - a null worded past its power

**f-31 run 4 finding (measured), verbatim:** c-41's null is worded past its power. Its pass rule requires the out-of-sample Brier gain to exceed its realized MDE, which passes a true effect at that size about half the time: measured 9% at 0.020 per sd, 41% at 0.033, 82-85% at 0.045-0.050. 'Rules out roughly 2 points per sd' is not supported, and team implied total is unresolved rather than refuted.

### `docs/findings/c41-residual-given-line.md`

- was:

  > ## The answer is no

  now:

  > ## None of the six passes the registered rule

- was:

  > What this rules out is an effect of about 2 points of over probability per standard deviation; it does not rule out one of 1.

  now:

  > **This null does not rule out an effect of 2 points of over probability per standard deviation.**

- added:

  > the rule passes 9% of the time at 0.020 per sd, 41% at 0.033 and 82-85% at 0.045-0.050.

- was:

  > **`team_total` is the nearest thing to a signal and it fails three ways.**

  now:

  > It did not pass, three ways - and it is unresolved, not refuted.**

- was:

  > **The only arm that improves on the close out of sample is the constant**

  now:

  > **The only arm whose out-of-sample improvement on the close exceeds its MDE is the constant**

### `CLAUDE.md`

- was:

  > **Given the line, nothing on a six-candidate list predicts the receiving-yards residual**

  now:

  > **Given the line, none of six candidates passed the registered rule on the receiving-yards residual - not detected, by a rule that reaches 80% detection only near 4.5-5 points per sd; team implied total is unresolved, not refuted**

- was:

  > an effect of ~2 points of over probability per sd is ruled out; 1 point is not.

  now:

  > **Scope of the null: it does NOT rule out 2 points of over probability per sd.**

- was:

  > The only arm that beats the close out of sample is the constant

  now:

  > The only arm whose out-of-sample gain on the close exceeds its MDE is the constant (1.16x, descriptive)

## c-34 - a "tight null" on a perturbation that could not have closed the gap

**f-31 run 4 finding (measured), verbatim:** c-34's 'tight null' is on a perturbation that could not have closed the gap. The injury arms move 32% / 36% of forecasts by a median 0.03 / 0.02; replacing those rows with the close's own price raises DSC by +0.0006, about 15% of the 0.0041 gap, which the test detects barely for arm 1 and not for arm 3. Arm 1's fall in miscalibration (p 0.048) does not survive correction.

c-34 has no `CLAUDE.md` entry (it never wrote one), so there is none to correct. The registered verdict ("improved calibration and not resolution: FAILED") is unchanged: the registered rule reads the interval as computed. What changed is the prose around it.

### `docs/findings/known-before-kickoff.md`

- was:

  > A null, not a refutation — but a tight one:

  now:

  > A null, not a refutation — and not a tight one.

- was:

  > An improvement worth having would have been seen.

  now:

  > replacing those same rows with the close's own price raises DSC by +0.0006, about 15% of the 0.0041 gap, which this test detects barely for arm 1 and not for arm 3.

- was:

  > and both improve calibration.

  now:

  > arm 3 lowers miscalibration and arm 1's fall does not survive correction.

- added:

  > **Arm 1's fall in miscalibration does not survive correction**

- was:

  > has can only ever buy MCB.

  now:

  > an inference from two correlations with no interval, not a measured limit.

- was:

  > **at the close, on main lines, the market has already used everything public.**

  now:

  > inferred, not measured, and not established by this unit

## c-38 - a success rule that could not have failed, and a bare size claim

**f-31 run 4 finding (measured), verbatim:** c-38's registered success rule could not have returned failure once its reproduction gate passed: 6 of the 16 are gated to published values and the 4 ordering contrasts cannot fail at worst-case SE. 'Larger in college on every baseline' appears without the re-weighted figure at findings :54, in the head sentence of its CLAUDE.md entry and in its relay findings[0]; there is no re-weighted skill-scale figure at all.

**The third bare copy is not in this repository.** f-31 names three places: `docs/findings/cross-sport.md:54`, the head sentence of the `CLAUDE.md` entry, and `findings[0]` of the relay record `_relay/reports/json/c-38.json` ("It is larger in college on all three."). The first two are corrected above. The relay record is the historical record and is not edited by anyone; whoever cites it must carry this sentence with it: *the raw college-minus-NFL gap is not a same-strength comparison; re-weighted to the NFL's distribution of favourite probability it is -0.0028 [-0.0071, +0.0012] against home, -0.0073 [-0.0115, -0.0029] against record and -0.0032 [-0.0046, -0.0018] against plain Elo, and no re-weighted skill-scale figure exists.*

**The missing re-weighted skill-scale figure was not computed here**, by instruction: the re-weighted cross-sport frame is c-44's. This unit adds the caveat, not the number.

### `docs/findings/cross-sport.md`

- added:

  > 6 of the 16 are gated to published values and the 4 ordering contrasts cannot fail even at worst-case SE, so the registered success rule could not have returned failure.

- was:

  > - **The advantage is larger in college on every baseline, on both scales.**

  now:

  > **The RAW advantage is larger in college on every baseline, on both scales, and the raw gap is not a same-strength comparison.**

- added:

  > the dBrier gap is -0.0028 [-0.0071, +0.0012] against home (0.46 of its MDE: not detected, a null this population cannot resolve, not a measured zero)

- added:

  > **No re-weighted figure exists on the skill scale at all**

- was:

  > ## Why it is bigger in college: mostly that college games are more lopsided

  now:

  > ## Why the raw gap is bigger in college: against home, mostly that college games are more lopsided

### `CLAUDE.md`

- was:

  > It is LARGER in college on every baseline - college minus NFL

  now:

  > The RAW gap is larger in college on every baseline - college minus NFL

- added:

  > and the raw gap is not a same-strength comparison: re-weighted to the NFL's distribution of favourite probability it is -0.0028 [-0.0071, +0.0012] against home

- added:

  > by a success rule that could not have returned failure once c-28's and c-39's published figures reproduced

- was:

  > **The size gap against `home` is the wider spread of college games, not a better model**

  now:

  > **At equal forecast strength the size gap against `home` is not detected: -0.0028 [-0.0071, +0.0012], 0.46 of its MDE - a null this population cannot resolve, not a measured zero**

## c-35 - a share that says nothing about this model, and an unsupported "no edge"

**f-31 run 4 finding (measured), verbatim:** c-35's 95.8% level share equals what a mismatched player's model ladder gives (0.959), so it says nothing about this model's shape. Its '+0.40pp' executable figure is post hoc and at 0.03 of its MDE, so 'nothing here is an edge' is unsupported; 'the shape receivers actually produce' rests on a descriptive column and contradicts the unit's own registered Q1 verdict.

c-35 has no `CLAUDE.md` entry, so there is none to correct. c-43's entry and findings cite c-35's population and were not touched. Also recorded by f-31 as **inferred** (read from the code, not measured): *c-35's fit is as-of kickoff while the price it is compared with is at kickoff minus 180 minutes, and position and team from the game's own stat row enter the fit. Both would flatter a model whose verdict is a null.*

### `docs/findings/ladder-edges.md`

- was:

  > **The shape between the rungs is the shape receivers actually produce.**

  now:

  > **By the registered rule the ladder's implied shape departs from what happened in one cell: receptions-RB, one catch above the line; 1 of 48 registered intervals survives correction, unreplicated.**

- was:

  > and the level is the model's error.**

  now:

  > and that share says nothing about this model.**

- added:

  > pairing each market ladder with ANOTHER player's model ladder gives 0.959 (f-31 run 4).

- was:

  > Nothing here is an edge.

  now:

  > No edge is reported here, and none is ruled out below the MDEs.

- added:

  > 0.03 of its 12.6pp MDE (f-31 run 4), so this run could not have detected an edge of that size or told one from none.

- was:

  > Not an edge, and not reported as one.

  now:

  > Not reported as an edge; at 0.03 of its 12.6pp MDE (f-31 run 4) this join cannot tell an edge from none.

- was:

  > The level is right everywhere

  now:

  > No level error is detected anywhere

- was:

  > a market whose level is right**

  now:

  > a market whose level is not detectably off**

- was:

  > no rule beats the default, the default being the right rung

  now:

  > not a demonstration that the central rung is the right one.

- added:

  > the fit is as-of kickoff while the price it is compared with is at kickoff - 180 minutes, and position and team from the game's own stat row enter the fit.

## c-33 - the number moved with the ruler; the class contrast stands

**f-31 run 4 finding (measured), verbatim:** c-33's rush-yards disagreement of 2.81pp is 0.73pp and rank 6 of 8 under the registered slope. The slope's sampling error is immaterial (x1.05); the choice of slope moves the number. Spearman -0.48 over 8 keys has permutation p 0.24 and is +0.74 under the registered slope. The props-versus-game-lines class contrast survives correction over 372 intervals.

c-33 has no `CLAUDE.md` entry, so there is none to correct. **The props-versus-game-lines class contrast survives correction over 372 intervals and keeps its strength**; the three `KEPT` pins in `tests/test_c45_corrections.py` fail if it is softened.

### `docs/findings/soft-markets.md`

- was:

  > ## 3. Within props, the three proxies do NOT agree on an order

  now:

  > ## 3. Within props, no common order is shown, and the order depends on the slope chosen

- was:

  > **There is no common "softness" among these keys for a composite to find.**

  now:

  > **That -0.48 is not evidence of anything: over 8 keys its exact permutation p is 0.24, and under the registered slope it is +0.74**

- was:

  > - **Disagreement is highest on the yards keys** (rush 2.81, receiving 2.57)

  now:

  > **Under the addendum-1 slope, on 2026 weeks 3-4 (32 games), disagreement is highest on the yards keys** (rush 2.81, receiving 2.57)

- added:

  > under the registered slope rush yards is 0.73pp and rank 6 of 8

- was:

  > which is the check that the ladder slope is the right quantity

  now:

  > it is not a check of the yards slope.

- was:

  > which is where a forecast has the most room — nothing more.

  now:

  > Whether a forecast has more room there is untested

- was:

  > disagree with each other about 2-4x as much

  now:

  > disagree with each other about 1.1pp more

## c-40 - a description of the model, not of misses

**f-31 run 4 finding (measured), verbatim:** c-40's 66.1% is a description of the model, not of misses: own_mean is the flipping input on 67.1% of all 477 leans, and under a within-game label permutation 66.1% is at the 62nd percentile (p about 0.75, drawn twice independently). The 19 excluded leans are all week 4, which the unit did not report.

### `docs/findings/c40-counterfactual-ledger.md`

- was:

  > So the concentration is real and it is not about missing.

  now:

  > So the concentration is real and is not detectably about missing.

- was:

  > No input is over-represented among the misses.

  now:

  > No input is detectably over-represented among the misses: a difference in share under about 0.11-0.12 would not have been seen on 31 games.

- added:

  > `own_mean` is the flipping input on 67.1% of all 477 leans whatever the outcome, and with the missed/cleared label permuted within game 66.1% sits at the 62nd percentile of its null (p about 0.75, drawn twice independently).

- added:

  > **All 19 are week 4**

- was:

  > The same sentence is true, at the same rate, of the leans that cleared.

  now:

  > at a rate this sample does not distinguish from the misses'

### `CLAUDE.md`

- was:

  > is the player's own prior mean, on misses and clears alike**

  now:

  > a description of the model, not of misses; missed against cleared is a null at 0.16 of its MDE (0.12)**

- added:

  > The 19 excluded leans are all week 4.

## What was not changed

- No figure, table, interval, result JSON, script, model or constant. Every corrected sentence quotes numbers the unit already published or numbers f-31 measured.
- No registered verdict. c-34's "FAILED", c-35's "comparison of two nulls", c-38's registered SUCCESS, c-40's "concentrated, and structural" and c-41's "0 of 6 pass" all stand as registered; the prose that read more into them is what moved.
- c-42 and c-43. f-31 has not attacked either.
- Anything under `_relay/reports/`. The six units' relay reports still carry the original wording (c-41's `summary` and `next`, c-34's `findings[0]`, c-38's `findings[0]`, c-35's `findings[0]`, c-33's findings 3 and 5). They are the record of what was said at the time; this file is what to read beside them.
- Commit subjects that carry the old wording (c-35 `a0f0354`, c-33 `2d84f8d`). History is not rewritten.
