# The edges inside one player's own ladder - Kalshi REC / RSHATT, NFL 2026 weeks 2-4

Unit c-35 (track C), 2026-10-08. Findings only; nothing publishes from here.

- **Scope, stated so it travels with every number below.** Kalshi `KXNFLREC` and
  `KXNFLRSHATT`, the over side, NFL 2026 regular season **weeks 2, 3 and 4**, one instant per
  game: **kickoff - 180 minutes**. 725 ladders (one player, one stat, one game), 3,987 rungs,
  48 games. The model is `models.baseline`, refit walk-forward as c-19 did. **Question 3 is
  18 games, 16 of them week 3** (every week-3 game, and one game each from weeks 2 and 4),
  because that is where `market_depth` was captured.
- **Order of work.** `docs/C35-ladder-edges-preregistration.md` was pushed at `f1db87c`
  before `research/ladder_edges.py` existed. The script as first written is `cc7a5dd`; the
  first run used it. Addendum 1 (`d3f40be`) was written after that run and says so: one bug
  fix toward the registered definition, one display rule, three post hoc tables outside
  every BH family. No registered interval moved (the two result files were diffed).
- **Reproduce.** `LOGGER_DB=<market_log.db> python -m research.ladder_edges --cache
  <scratch>.json --extract` (store held 12 s `mode=ro`, then ~3 min of model refits), then
  the same command without `--extract`. Tests: `tests/test_ladder_edges.py`.
- **Not re-run:** brief 019's monotonicity race. The isotonic step touched **0 of 725**
  ladders at this instant - at kickoff - 180 min every ladder's mids are already ordered.

## The three answers, short

1. **The shape between the rungs is the shape receivers actually produce.** Kalshi's implied
   mass per catch-count, measured from the central rung, matches the realised distribution
   of 12,593 player-games (2023-25, typed by position and line) to within 1.5pp in every
   cell for receptions overall, and within 2.2pp in every position cell carrying at least 23
   ladders. Against 2026's own outcomes, 1 of 48 registered intervals survives correction, and
   the three seasons side with the ladder on that cell, not with the three weeks.
2. **The model and the ladder disagree almost entirely by level - and the level is the
   model's error.** One shift per ladder removes 95.8% of the squared disagreement. But the
   disagreement orders outcomes at neither level, so "ladder-level versus rung-level" is, by
   the rule fixed in advance, **a comparison of two nulls**.
3. **The central rung is the cheapest way to hold a level view per contract; the wings are
   cheaper per dollar only for a large view.** Cost on Kalshi is a hump in price, set by the
   fee, not a slope. Choosing the central rung over a random rung is worth about 2.9pp of a
   7.3pp expected value at the primary view.

Nothing here is an edge. The one statistical survivor costs 4.5c to trade against a 7.5pp
gap at the mid, on a 50-contract touch, and realised +0.40pp [-9.32, +7.97].

## Q1 - the shape between the rungs

691 ladders with at least 3 rungs (median 6; receptions rungs are 1 apart, **rush-attempt
rungs are 3 apart and a rush ladder has 3 rungs**). A central rung (mid in [0.25, 0.75])
exists on 690.

**Q1-B, one number for dispersion and one for level** (mid-PIT of the realised count under
the ladder; `D > 0` means the ladder is too narrow):

| group | ladders | level M | dispersion D |
|---|---:|---|---|
| receptions, all | 535 | +0.0008 [-0.0211, +0.0217] | **+0.0720 [+0.0008, +0.1408]** MDE 0.0998 |
| receptions, WR | 273 | +0.0086 [-0.0224, +0.0407] | +0.0618 [-0.0337, +0.1489] |
| receptions, TE | 136 | -0.0131 [-0.0640, +0.0401] | +0.1105 [-0.0132, +0.2360] |
| receptions, RB | 124 | -0.0017 [-0.0548, +0.0504] | +0.0562 [-0.0788, +0.1993] |
| rush attempts | 156 | -0.0069 [-0.0465, +0.0317] | +0.0179 [-0.1145, +0.1514] |

The level is right everywhere (every M interval contains zero, MDE 0.03-0.07). Every
dispersion estimate is positive, and the pooled receptions one excludes zero **before**
correction (z = 2.02) and does **not** survive BH. That is the direction to watch - a ladder
slightly too narrow - and it is not a finding.

**Q1-A, where** - implied mass against what happened, receptions (all), by the count's
offset `d` from the first count above the central line:

| cell | n | Kalshi implied | 2026 realised | diff | 2023-25 realised, same position and line |
|---|---:|---:|---:|---|---:|
| below lowest rung | 535 | 0.2969 | 0.3028 | +0.0059 [-0.0233, +0.0375] | - |
| d = -3 | 88 | 0.1068 | 0.1136 | +0.0069 [-0.0501, +0.0740] | 0.1209 |
| d = -2 | 231 | 0.1548 | 0.1342 | -0.0206 [-0.0692, +0.0326] | 0.1686 |
| d = -1 | 370 | 0.2053 | 0.2243 | +0.0190 [-0.0283, +0.0695] | 0.1984 |
| d = 0 | 534 | 0.2035 | 0.1891 | -0.0144 [-0.0431, +0.0151] | 0.1956 |
| **d = +1** | 534 | 0.1338 | 0.0993 | **-0.0346 [-0.0610, -0.0074]** | 0.1308 |
| d = +2 | 499 | 0.0775 | 0.0902 | +0.0127 [-0.0116, +0.0383] | 0.0815 |
| **d = +3** | 396 | 0.0437 | 0.0657 | **+0.0220 [+0.0039, +0.0426]** | 0.0442 |
| above highest rung | 535 | 0.0363 | 0.0523 | +0.0161 [-0.0005, +0.0335] | - |

(The 2023-25 column is Q1-C: ladders whose central mid is in [0.40, 0.60], 460 of them, so
its `n` per cell is slightly smaller than the first column's. It is descriptive and has no
interval. Its conditioner is a de-vigged sportsbook line, not an exchange mid.)

- The 2026 pattern in receptions is one shape: **too much mass one catch above the line,
  too little three or more above it**. Two cells exclude zero uncorrected; neither survives.
- **The registered survivor is receptions-RB, d = +1**: implied 0.1396, realised 0.0645,
  -0.0751 [-0.1144, -0.0294], 124 ladders, 47 games, z = -3.47. 1 survivor of 48 at q = 0.10.
  By the rule, "the ladder's implied shape departs from what happened" there.
- **Why I do not believe it yet.** For the same position and lines, 2023-25 realised 0.1444
  in that cell. Kalshi implies 0.1413. The ladder agrees with three seasons to 0.3pp and
  disagrees with three weeks by 7.5pp. The same holds cell by cell for WR and TE: the largest
  Kalshi-versus-2023-25 gap in any position cell with at least 23 ladders is 2.2pp (TE,
  d = -3, n = 26). The one larger gap is RB d = -3, 5.0pp on 4 ladders.
- **Its depth join (post hoc, addendum 1e).** Selling that cell is two legs. Depth exists on
  both for 43 of the 124 ladders (17 games). Cost 4.52c over the mid against the 7.51pp gap;
  thinner-leg touch size p50 **50 contracts**; realised net **+0.40pp [-9.32, +7.97]**. Not an
  edge, and not reported as one.
- **Rush attempts have no cell finer than 3 carries.** Q1-A covers them on the two tails
  only (both within 1.1pp of implied, MDE 9pp). The post hoc rung-cell table: just below
  the central rung implied 0.2773 realised 0.2941; just above 0.2710 against 0.2549; both
  intervals contain zero at MDE 0.10.

**48 intervals were registered-and-estimable, not the 55 I wrote**, because I had not looked
at rush-attempt rung spacing before registering.

## Q2 - is the whole ladder shifted, and is that better than one rung

691 ladders, 3,935 rungs, 48 games. Weeks 2-3 reproduce c-24's P2 exactly - all 2,513 of its
rows, model probability included, to 4 dp - plus 88 rungs from PHI@CHI (Monday of week 3),
which had no final score when c-19 extracted. Brier(model) - Brier(Kalshi mid) on weeks 2-3
is +0.0122 here against c-24's +0.0121.

**The disagreement is level.**

| share of the squared rung disagreement | |
|---|---|
| removed by one shift per ladder | **0.958 [0.951, 0.964]** |
| further removed by adding a scale | 0.038 [0.033, 0.044] |
| left over (neither a shift nor a scale) | 0.004 [0.003, 0.005] |

Per ladder the level share is p25 0.70, median 0.93. The fitted shift is **negative**:
median -0.19 probit, mean -0.234 (p10 -0.97, p90 +0.41). In probabilities: the model's mean
rung is 0.297, the market's 0.337, the realised rate 0.341. **The model sits about 4pp below
a market whose level is right** (Q1's M is zero). The fitted scale's median is 0.85.

**And the disagreement predicts nothing, at either level.** Concordance within strata of
stat x market-mid decile (c-24's `wauc`), minus 0.5:

| | all (3,935 rungs) | receptions | rush attempts |
|---|---|---|---|
| MDE of dA, stated first | 0.0240 | 0.0247 | 0.0211 |
| the rung's own gap | +0.0182 [-0.0352, +0.0733] | +0.0177 [-0.0367, +0.0748] | +0.0394 [-0.0414, +0.1176] |
| the ladder shift | +0.0155 [-0.0343, +0.0666] | +0.0145 [-0.0366, +0.0669] | +0.0519 [-0.0302, +0.1322] |
| **dA = ladder - rung** | **-0.0027 [-0.0194, +0.0143]** | -0.0031 [-0.0204, +0.0146] | +0.0125 [-0.0030, +0.0266] |
| dA, player-game blocks | -0.0027 [-0.0184, +0.0129] | -0.0031 [-0.0202, +0.0129] | +0.0125 [-0.0035, +0.0290] |
| rest of the ladder (leave-one-out) - rung | -0.0043 [-0.0214, +0.0132] | -0.0047 [-0.0224, +0.0132] | +0.0113 [-0.0093, +0.0294] |
| per ladder: Spearman(shift, PIT) | +0.071 [-0.009, +0.150] | +0.056 [-0.034, +0.144] | +0.129 [-0.029, +0.291] |
| per ladder: Spearman(central rung's gap, PIT) | +0.069 [-0.015, +0.148] | +0.055 [-0.035, +0.144] | +0.105 [-0.042, +0.253] |

**Verdict, by the override written before the run: a comparison of two nulls.** Neither the
rung's gap nor the ladder's shift has a concordance interval above 0.5, so whichever of the
two "orders better" is not a statement about anything. 0 of 21 registered intervals survive
BH.

What the table does say:

- The premise "the same disagreement across every rung is many observations of one claim" is
  half right. It IS one claim - 96% of it is a single number per ladder. It is not many
  observations: the rungs agree with each other because the model emits one distribution,
  not because they are independent looks. Averaging them adds no information the central
  rung's gap did not carry (Spearman difference +0.003 [-0.008, +0.012]).
- The point estimates are all positive and all small. A concordance edge under about 0.075
  for either score could not have been seen in three weeks.
- Rush attempts lean toward the ladder (dA +0.0125, interval just touching zero) on 3-rung
  ladders. Not read as a finding: its own A intervals contain zero.

## Q3 - the cheapest rung to express a view

Every price below is `market_depth` VWAP at the stated size on the side being bought, plus
the taker fee for the whole order at that size, divided. `/series` says `fee_type =
quadratic`, multiplier 1, for both series - read from the 09-14 snapshot and live on 10-08.
A "view" is that the whole ladder is mis-centred by `delta` probit units (0.25 is about
10pp at the money); the mid is used to state it and for nothing else.

**Coverage.** Of 3,987 usable rungs, **1,400 have a depth snapshot within 600 s before
entry** (week 2: 93, week 3: 1,223, week 4: 84). 242 ladders, **18 games**. The other 2,587
rungs are given no cost. Depth rows were a median 39 s old; the depth touch equals the
quoted ask to within 1c on 99.9% of rungs.

**Cost is a hump in price, and the fee is most of it** (buy YES, 100 contracts):

| YES price | rungs | all-in cost over mid | of which fee | break-even view (probit, p50) | touch size p50 |
|---|---:|---:|---:|---:|---:|
| < 0.10 | 394 | 1.44c | 0.42c | 0.123 | 9,428 |
| 0.10-0.20 | 200 | 1.98c | 0.94c | 0.078 | 3,111 |
| 0.20-0.35 | 205 | 2.51c | 1.40c | 0.071 | 1,898 |
| 0.35-0.50 | 178 | 2.43c | 1.71c | **0.056** | 3,742 |
| 0.50-0.65 | 149 | 2.51c | 1.70c | **0.057** | 2,041 |
| 0.65-0.80 | 144 | 2.65c | 1.35c | 0.074 | 458 |
| 0.80-0.90 | 99 | 2.12c | 0.85c | 0.079 | 585 |
| > 0.90 | 26 | 1.80c | 0.42c | 0.118 | 206 |

- In cents the middle is the most expensive rung; **in units of view it is the cheapest**,
  by a factor of two against either tail, because a cent buys the most movement where the
  density is highest. A deep in-the-money over costs 1.8-2.1c here, not c-22's 4.6-6.2c:
  that figure was a sportsbook's, and this is an exchange at kickoff - 3h.
- **Depth is lopsided.** The YES side of a cheap rung shows thousands at the touch. The NO
  side of the same kind of rung does not: buying NO above 0.65 meets a touch of **44-48
  contracts** (p50), and at 500 contracts its cost rises to 2.3-3.6c.

**The fixed rules** (one rung per ladder, chosen by its mid; up-view, buy YES, 100 contracts):

| rule | mean mid | cost | break-even view p50 | EV pp/contract at view 0.10 / 0.25 / 0.50 | EV per dollar staked, % |
|---|---:|---:|---:|---|---|
| central (nearest 0.50) | 0.491 | 2.42c | **0.057** | **+1.51 / +7.34 / +16.58** | +3.0 / +14.5 / +32.9 |
| nearest 0.30 | 0.290 | 2.58c | 0.069 | +0.87 / +6.33 / +15.93 | +2.7 / +20.4 / +51.9 |
| nearest 0.70 | 0.650 | 2.67c | 0.070 | +0.78 / +5.66 / +12.85 | +1.4 / +9.1 / +20.6 |
| nearest 0.15 | 0.169 | 2.26c | 0.080 | +0.32 / +4.60 / +12.77 | +2.0 / **+25.4 / +70.5** |
| nearest 0.85 | 0.725 | 2.40c | 0.073 | +0.50 / +4.52 / +10.27 | +0.9 / +7.1 / +16.0 |

(These are the value of being RIGHT about a view nobody here has shown they hold. The
down-view table is the mirror image and is in the result file.)

- **The registered test: how much is the choice worth?** The best fixed rule on each half of
  the games was scored against the central rung on the other half. On both halves, both
  sides and both sizes, **the best rule was the central rung**, so the cross-fitted
  difference is identically zero and the verdict by the rule is "not shown to be worth
  anything" - which here means **no rule beats the default, the default being the right
  rung**. 0 of 4 survive BH (a zero-variance interval is p = 1).
- **What a wrong choice costs (descriptive, >= 0 by construction).** At view 0.25, 100
  contracts, buying YES: the best rung beats the central by 0.10pp, **the mean rung by
  2.85pp and the worst by 6.01pp** per contract. Buying NO: 0.09 / 3.49 / 7.12pp. The best
  rung has a mid in 0.35-0.65 on 228 of 242 ladders.
- **Per dollar staked the answer turns over with the size of the view (post hoc).** At a
  0.10 view nothing beats the central rung per dollar (the one half that picked
  nearest-0.30 scored -0.46% of stake [-0.79, -0.18] against it on the other half). At 0.25 the cheapest rung is the
  out-of-the-money one: nearest-0.15 beats central by **+10.89% of stake [+9.81, +11.99]**
  cross-fitted, and by +37.61% at 0.50. On the down side (buy NO on a high rung) it is
  +2.41% [+0.91, +3.87] at 0.25 and +19.85% at 0.50 - and at 500 contracts the 0.25 figure
  is +0.19 [-1.89, +2.14], because that is the side with a 44-contract touch. This is
  leverage, not edge: the cheap rung pays more per dollar when the view is right and loses
  the whole stake more often when it is not.
- **Realised, no view.** Buying YES at each price bucket and holding: no bucket's interval
  excludes zero (the largest, YES below 0.10: -1.82pp [-3.68, +0.09]). The NO-side table
  shows three buckets excluding zero at -5.7 to -6.7pp; over and under are complements, so
  that is the YES side's noise plus two crossings, reported on one side here and not a
  finding.

## What this does not establish

- **Three weeks, one instant.** No cell departure under roughly 2.5-9pp (the cell's own
  MDE), and no concordance edge under about 0.075, could have been seen. "Matches three seasons" is a statement about
  the cells within three catches of the central line.
- **Q3 is one week.** 16 of the 18 games are week 3. The cost curve is that week's book at
  kickoff - 180 min, and says nothing about another lead time (brief 022's H3 map says
  props widen sharply in-game).
- **Q1-C compares across venues and seasons.** Its agreement is evidence the ladder's shape
  is plausible, not that Kalshi's ladder is calibrated in 2026.
- **The level share is partly mechanical.** One free parameter per ~6 rungs would absorb
  about a sixth of pure noise. 0.958 is far above that; the benchmark is arithmetic, not a
  measured null.
- **A multi-level fill is billed per fill.** The fee here is computed once at the VWAP,
  which can understate by up to a cent of rounding per level on an order that walks the
  book.
- **No resting order is simulated.** A maker pays no fee on these series, and the fee is
  most of the cost above. Briefs 018/019 and c-20 measured that path: an 18% fill rate and
  fills worth nothing.
- **Position is the player's row for that game.** 130 QB rungs and 6 FB rungs are in the
  `-all` groups and in no position group.

## Replication, fixed before week 5 settles

One cell, one direction: receptions-RB, d = +1, realised below implied. If weeks 5+ are
run, that is the hypothesis; the dispersion direction (D > 0 on receptions) is the second
thing to look at and is not one.
