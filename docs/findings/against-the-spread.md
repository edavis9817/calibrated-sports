# Against the spread: the key-number margin, scored (c-30)

Pre-registration: `docs/C30-against-the-spread-preregistration.md`, pushed at
`1bb405f` before `models/key_margin.py` or `research/against_the_spread.py`
existed. It has one addendum, covering the smoke runs, a post-hoc section and a d|CITL| caveat.

**Reproduce.** With `LOGGER_DB` pointing at `market_log.db` (opened `mode=ro`):

    python -m research.against_the_spread --json-out <scratch>/result.json     # ~3 min

Data: `nfl_games` data_version `2026-09-30`, `nfl_teams` `2026-09-16`. Every
figure moves when either is re-ingested; the Kalshi rows grow as weeks 4+ settle.

## Scope, stated so it travels with the numbers

- **Model:** c-28's margin-of-victory Elo `GameForecast`. It is untouched, and
  only the margin SHAPE is replaced. It reads final scores and nothing else.
- **Settlement and book price:** the nflverse `spread_line`, 2001-2025, and
  `home_spread_odds`/`away_spread_odds`, 2006-2025. This is a close of
  unrecorded provenance; see c-28.
- **Kalshi:** ONE venue, 2026 weeks 2-3, 32 games, 829 half-point rungs.

## The moneyline did not move

- The Part-1 model Brier is `0.2205815236861795`. That is c-28's recorded
  figure, **equal as a float**, and the script refuses to continue otherwise.
- sha256 over every Part-1 `p_home` is `8ee8002f…8a6e`.
- `max |P_E(M>0 | M≠0) − p_home|` over 6,758 games is 4.4e-16.
- `models/game.py` and `models/season.py` have no diff against c-28.

## The object

`models/key_margin.py`: `KeyMarginForecast(base, w, t)`.

- **Discretisation.** The base Normal is discretised on the integers, and each
  |k| from 1 to 24 is multiplied by a weight w_|k|. The weights are symmetric
  in sign.
- **Moneyline pin.** Each side of zero is renormalised to c-28's `p_home`. The
  tie mass is the training tie rate.
- **Fitting.** w is fitted walk-forward on seasons 2000..T-1 by penalised
  within-side maximum likelihood, with a ridge of 2·Σ(log w)², i.e. a prior sd
  of 0.5.
- **Arms:**
  - **E** is this object.
  - **N0** is c-28 exactly as built: continuous, with no push mass.
  - **N1** is E with w ≡ 1, i.e. the discretised Normal. It isolates the shape.

## The spike table: the deliverable

These are |margin| masses over all 6,758 scored games, 2001-2025. The Normal
and E columns are the walk-forward mean predicted mass. w is the final fit on
2000-2025, which is what a 2026 game uses.

| \|M\| | empirical | Wilson 95% | Normal (N1) | E | w |
|---|---|---|---|---|---|
| **3** | **0.1503** | [0.1420, 0.1591] | 0.0538 | **0.1559** | 2.75 |
| **7** | **0.0906** | [0.0839, 0.0976] | 0.0491 | **0.0924** | 1.79 |
| **10** | 0.0550 | [0.0499, 0.0607] | 0.0438 | 0.0571 | 1.22 |
| **14** | 0.0490 | [0.0441, 0.0544] | 0.0353 | 0.0467 | 1.35 |
| **6** | 0.0614 | [0.0559, 0.0674] | 0.0506 | 0.0534 | 1.17 |
| **4** | 0.0497 | [0.0448, 0.0552] | 0.0529 | 0.0470 | 0.91 |
| 0 (tie) | 0.0022 | [0.0013, 0.0037] | 0.0012 | 0.0012 | — |
| 1 | 0.0410 | | 0.0548 | 0.0393 | 0.74 |
| 2 | 0.0408 | | 0.0544 | 0.0366 | 0.74 |
| 5 | 0.0358 | | 0.0518 | 0.0339 | 0.69 |
| 9 | 0.0154 | | 0.0457 | 0.0185 | 0.35 |
| 12 | 0.0166 | | 0.0397 | 0.0167 | 0.42 |

(The full table, 0 to 21, is in the run log and `result.json`.)

- **The spike at 3 is 2.8× the Normal's mass.** 15.0% of games end on
  exactly ±3, which is closer to a seventh than to the brief's "roughly a sixth".
- **The spike at 7 is 1.8× the Normal's mass**, and 10 and 14 carry about 1.3×.
- **4 is NOT a spike in 2001-2025.** Its empirical mass, 0.0497, sits at or
  below the Normal's 0.0529, and its fitted weight is below 1 (0.91). The brief
  lists it with the key numbers; the data does not.
- **6 is a mild spike**, at 1.2×.
- **The troughs are as large as the spikes.** 9 is at 0.35× and 12 at 0.42×.
- **E reproduces every spike to within its Wilson interval except 6.** At 6 E
  gives 0.0534 against the interval [0.0559, 0.0674]; it under-fits because the
  ridge pulls toward the Normal. **The tie mass is under-predicted by both arms:**
  0.0012 against 0.0022. The walk-forward tie rate is trained on earlier seasons,
  and ties rose: 5 in 4,256 games over 2000-2015 (0.12%) against 10 in 2,761
  over 2016-2025 (0.36%). Those are small counts. The 2001 fit has t = 0,
  because 2000 had no tie.

**Push rates:** predicted P(M = L) against the realised rate, for whole-number
lines.

| line | n | realised | E | N1 (Normal) | N0 |
|---|---|---|---|---|---|
| on 3 | 1,083 | 0.0905 [0.0748, 0.1091] | 0.0862, diff −0.0043 [−0.0219, +0.0116] | 0.0294, diff **−0.0611 [−0.0786, −0.0453]** | 0 |
| on 7 | 444 | 0.0563 [0.0384, 0.0818] | 0.0560, diff −0.0003 [−0.0228, +0.0201] | 0.0291, diff **−0.0272 [−0.0496, −0.0069]** | 0 |
| other whole | 2,227 | 0.0247 | 0.0247, diff −0.0000 [−0.0063, +0.0064] | 0.0289, diff +0.0042 [−0.0021, +0.0105] | 0 |

**The shape is right.** E's push rates are calibrated on every stratum. The
Normal misses a third of the pushes on 3 and half of them on 7.

## Part 1: cover against settlement (PRIMARY)

- **Population:** 6,580 games with a line and no push, 2001-2025.
- **Strata:** 985 games on 3, 419 on 7, 2,172 on other whole numbers, and 3,004 on half points.
- **Method:** game blocks, 2,000 draws.

The first three columns are E − N0, the registered comparison. The last column
is E − N1, the mechanism reading.

| stratum | dBrier | dMCB | CITL E / N0 / N1 | dMCB E − N1 |
|---|---|---|---|---|
| **on 3** | **+0.0010 [+0.0006, +0.0014]** | **+0.0010 [+0.0005, +0.0014]** | +0.053 / +0.052 / +0.054 | +0.0008 [−0.0003, +0.0017] |
| on 7 | +0.0028 [−0.0008, +0.0063] | +0.0026 [−0.0011, +0.0060] | −0.014 / −0.002 / +0.002 | +0.0023 [−0.0018, +0.0064] |
| other whole | +0.0008 [−0.0002, +0.0019] | +0.0008 [−0.0001, +0.0019] | −0.006 / +0.002 / +0.005 | +0.0008 [−0.0003, +0.0020] |
| half point | +0.0007 [−0.0004, +0.0017] | +0.0008 [−0.0003, +0.0017] | +0.010 / +0.017 / +0.020 | +0.0009 [−0.0003, +0.0020] |
| pooled | **+0.0009 [+0.0003, +0.0016]** | **+0.0008 [+0.0003, +0.0015]** | +0.010 / +0.016 / +0.019 | **+0.0009 [+0.0002, +0.0016]** |

**Registered verdict: NO.** The key-number margin is **no better calibrated than
the Normal at the key numbers**, against the cover outcome:

- **On 3 it is WORSE.** dMCB is +0.0010, and the interval excludes zero on the
  wrong side. This holds in both season halves: 2001-2012 gives
  +0.0010 [+0.0001, +0.0017] and 2013-2025 gives +0.0010 [+0.0003, +0.0016].
- **On 7 it is no better.** The interval contains zero.
- **Pooled, it is slightly worse.**

Every arm's AUC against the cover is 0.503-0.514. None of them orders covers
better than chance, and the unconditional cover forecast is dominated by how the
model's mean disagrees with the line.

### Why a better shape scores worse (post-hoc; not registered, not counted)

- **The correct shape amplifies the model's disagreement with the line, and the
  line is the better mean.**
  - Conditioning on "no push" removes the spike at the line. The trough-and-spike
    structure on either side of it then converts a small gap between mu and L
    into a larger departure from 0.5.
  - **E's forecasts are more dispersed than N0's in every stratum.** The sd of f
    on 3 is 0.089 against 0.084, on 7 0.104 against 0.087, and pooled 0.096
    against 0.089.
  - c-28 showed the model's mean loses to the line, all of it resolution. So
    more confidence in the model's disagreement is more miscalibration. The shape
    is right (see the push rates) and it makes the forecast worse, because the
    thing it sharpens is wrong.
- **Re-centre every arm on the line itself (mu = `spread_line`) and the shape
  helps where it can be seen.** These are CITL figures, the mean forecast minus
  the realised cover rate, with game blocks and 1,000 draws:
  - Half-point lines, home favoured, n 1,984: E@L is +0.0006 [−0.0214, +0.0242]
    and N1@L is **+0.0274 [+0.0051, +0.0511]**. The Normal is off, and the key
    shape removes it.
  - **Home favoured by exactly 3 covers only 0.443 [0.402, 0.484], and no shape
    reproduces it.** E@L gives 0.501 (CITL +0.058, which excludes zero) and
    N1@L gives 0.512. That is a fact about how books hang −3, not about the
    margin shape: a symmetric margin centred on 3 cannot say 44%. It is also the
    source of the +0.05 CITL every arm shows on 3 in Part 1.
  - On 7 and on other whole numbers, every line-centred interval contains zero.

## Part 2: against the price (SECONDARY)

### 2a. Kalshi KXNFLSPREAD, 2026 weeks 2-3

**Power, stated before the result:** c-28 measured an MDE of 0.0237 on this
population. The population is 829 rungs over 32 games, and each team's ladder is
kept separate (a ladder spanning two teams raises).

| | dBrier | dMCB | dAUC |
|---|---|---|---|
| E − market | +0.0013 [−0.0158, +0.0190] | +0.0006 [−0.0071, +0.0058] | −0.011 [−0.077, +0.058] |
| N0 − market | +0.0028 [−0.0138, +0.0195] | +0.0014 [−0.0057, +0.0082] | −0.015 [−0.081, +0.053] |
| E − N0 | −0.0015 [−0.0039, +0.0010] | −0.0008 [−0.0041, +0.0017] | +0.003 [−0.001, +0.009] |

**All intervals contain zero, and the population can detect nothing that
matters.** The direction of E − N0 on the ladders favours E. It is not read.

### 2b. The book cover price, 2006-2025

The book price is de-vigged multiplicatively; there are 5,162 games.

- **Both arms lose to the book in every stratum.** E − book, pooled, is
  +0.0081 [+0.0054, +0.0107]; N0 − book is +0.0072 [+0.0047, +0.0096].
- **On 3 and on 7, E loses by more than N0 on the point estimate**; the difference itself was not bootstrapped. On 3 it is +0.0082 against
  +0.0071, and on 7 +0.0123 against +0.0088.

### 2c. The registered honest-positive test: flat bets at the actual price

| arm | away from keys, pooled | 2006-2015 | 2016-2025 | key lines | pooled |
|---|---|---|---|---|---|
| E | −2.2% [−6.1, +1.5] (2,490) | −0.4% [−5.5, +5.2] | −4.2% [−9.5, +1.1] | −0.1% [−4.0, +4.0] | −1.2% [−3.8, +1.6] |
| N0 | −2.4% [−6.1, +1.4] | +0.3% [−4.9, +5.8] | −5.4% [−11.0, +0.0] | −1.5% [−5.3, +2.7] | −2.0% [−4.8, +0.9] |
| N1 | −3.2% [−7.1, +0.8] | −0.6% [−6.0, +5.0] | **−6.0% [−11.3, −0.5]** | −2.2% [−6.3, +1.9] | −2.7% [−5.5, +0.1] |

**No honest positive.** Not one away-from-keys interval lies above zero, in any
arm or in either half. **No cost unit and no depth check are warranted.**

## Registered interval count

There are 72 registered intervals, and 21 of them exclude zero. No multiplicity
correction was applied. **None of the 21 favours any model over the market.**

- **9 in 2b:** the model loses to the book cover price.
- **6 in Part 1:** E is worse calibrated than N0 or N1, on 3 and pooled.
- **3 in Part 1 are d|CITL|, and they are not read.** That statistic is a
  difference of absolute values and its percentile interval is unreliable (see
  the addendum).
- **2 in the push-rate test:** the Normal under-predicts pushes on 3 and on 7.
  These are the only two that favour E's shape.
- **1 in 2c:** N1 loses money on 2016-2025 away from the keys.

## What this cannot support

- **"The key-number margin beats the line"** or any betting claim. Every arm
  loses to the book close.
- **"Key numbers do not matter."** They matter a great deal to the SHAPE, as the
  push rates show. What fails is using a better shape on a worse mean.
- **Kalshi anything.** It is 32 games of one venue in one season, and the MDE is
  five times the gap that matters.
- **The 0.443 cover rate of home −3 favourites as an edge.** It is post-hoc, it
  was not registered, and it was not priced against the juice those lines
  carry. 2c's "key lines" cell does price them, and it is not positive.
