# The interval between two rungs - Kalshi REC / RSHATT, NFL 2026 weeks 2-4

Unit c-43 (track C), 2026-10-08. Findings only; nothing publishes from here.

- **Scope, stated so it travels with every number below.** Kalshi `KXNFLREC` and
  `KXNFLRSHATT`, NFL 2026 regular season **weeks 2, 3 and 4**, one instant per game:
  **kickoff - 180 minutes**. c-35's population: 725 ladders, 3,987 rungs, 48 games, read from
  c-35's extract cache (`market_log.db` was not opened). An *interval* is any pair of rungs
  of one ladder: 11,145 of them on 709 ladders. **The cost arm is 18 games, 16 of them week
  3**, because that is where `market_depth` was captured.
- **This is the market's ladder, not ours.** No model is fitted or read; the cache's model
  column is dropped on load and a test asserts the script never touches it.
- **Order of work.** `docs/C43-interval-preregistration.md` was pushed at `dbf8b03` before
  `research/interval_mass.py` existed. The script as first written is `a62133d`. The MDE of
  every cell was computed with the outcome columns stripped and committed before the outcome
  run. The registered run happened **once**; specifications tried: **one**. Addendum 1 was
  written after it and says so: one post hoc sensitivity, outside the verdict.
- **Reproduce.** `python -m research.ladder_edges --cache <scratch> --extract` (c-35's), then
  `python -m research.interval_mass --cache <scratch> --mde --json-out <mde.json>`, then the
  same with `--mde-in <mde.json>` in place of `--mde`. Tests: `tests/test_interval_mass.py`.
  Aggregates: `research/results/interval_mass.{json,log}`, `interval_mde.{json,log}`.

## The three answers, short

1. **Coherent.** 0 of 11,145 intervals has a negative implied mass, 1 is exactly zero, and 0
   are crossed at the touch. The ladder always answers the question.
2. **Calibration: "a deviation survives correction below its MDE."** 6 of 46 readable cells
   survive BH at q = 0.10; **none exceeds its pre-run MDE, so by the registered rule none is
   called mispriced.** One of the six is a bootstrap artifact, and on an outcome-independent
   SE none survives (post hoc). What is there is one shape, the one c-35 already saw: mass
   near the centre of the ladder realised a few points less often than priced.
3. **The second leg roughly doubles the cost.** Two legs cost 4.36c over the mid at 100
   contracts against 2.30c for the nearest single rung: +2.06c [+1.94, +2.17]. Buying a
   width-1 interval costs 34% of what it is worth.

Nothing here is an edge. Every surviving cell's two-leg cost is at least as large as its gap
at the mid, or its realised net contains zero or is negative.

## Answer 1 - coherence

| | count | of |
|---|---:|---:|
| implied mass negative (mids inverted) | **0** | 11,145 intervals, 709 ladders |
| implied mass exactly zero | 1 | 11,145 |
| crossed at the touch (`ask_i < bid_j`) | **0** | 11,145 |
| touch SELL price <= 0 (the quotes cannot tell the mass from zero) | 311 (2.8%) | 11,145 |
| receptions ladders with >= 4 rungs whose unit masses are not unimodal | 20 (4.0%) | 500 |

- This agrees with c-35 (the isotonic step touched 0 of 725 ladders) and is the same instant.
  **It does not contradict brief 019 H1's 892 monotonicity episodes**: 95% of those were
  in-game and this is three hours before kickoff.
- The 311 are all thin intervals: 295 are width-1 receptions cells (10.1% of them), and 304
  sit in the `<0.05` mass bin, where 46.8% cannot be sold above zero at the touch. Above a
  mass of 0.10 the count is 0 of 9,147 on receptions.
- The interval's own quoted band at the touch (buy minus sell) is 3c at the median and 6c at
  p90 on receptions; 4-5c and 12-17.5c on rush attempts. That is the sum of two 1-2c spreads.

## Answer 2 - calibration of the interval, against settlement

72 cells were registered; **50 exist, 46 are readable** (>= 5 games). 8 are nominally
significant against 2.3 expected by chance - but the cells overlap heavily (a ladder's
intervals are all read off one realised count), so that ratio is not eight independent hits.

**The pre-run MDE is the resolution**: median 7.7pp, from 1.0pp (all width-1 receptions
cells pooled) to 50pp. The bootstrap SE at run time is 0.98x the pre-run one at the median
(p90 1.10), so treating ladders in a game as independent cost little.

Pooled by width (realised minus implied, pp, game-block interval; pre-run MDE):

| | n | implied | realised | diff | MDE |
|---|---:|---:|---:|---|---:|
| receptions w=1 | 2,933 | 0.1218 | 0.1176 | -0.42 [-1.00, +0.18] | 0.98 |
| receptions w=2 | 2,397 | 0.2460 | 0.2332 | -1.28 [-2.65, +0.08] | 2.13 |
| receptions w=3 | 1,862 | 0.3700 | 0.3416 | **-2.85 [-5.07, -0.81]** BH | 3.41 |
| receptions w=4 | 1,363 | 0.4898 | 0.4622 | -2.76 [-5.72, +0.17] | 4.78 |
| receptions w=5 | 950 | 0.6003 | 0.5768 | -2.35 [-6.31, +1.45] | 6.02 |
| receptions w=6 | 604 | 0.6938 | 0.6705 | -2.33 [-7.25, +2.73] | 7.10 |
| receptions w=7 | 335 | 0.7644 | 0.7403 | -2.41 [-9.04, +3.90] | 8.49 |
| rush attempts w=3 | 328 | 0.2773 | 0.2866 | +0.93 [-3.30, +5.16] | 5.32 |
| rush attempts w=6 | 156 | 0.5449 | 0.5385 | -0.64 [-8.69, +7.75] | 10.73 |

Every receptions width reads negative, by 2-3pp from width 3 up. Rush attempts show nothing
(MDE 5-11pp).

**The six BH survivors, each with its cost line** (100 contracts a leg; sold where realised
is below implied, bought where above):

| cell | diff, pp | pre-run MDE | exceeds it | two-leg cost | realised net, pp | thin-leg touch p50 |
|---|---|---:|---|---:|---|---:|
| rec w=1, 0.10-0.20 | -2.03 [-3.47, -0.62] | 2.07 | no | 4.69c | -3.56 [-6.27, -0.93] (495 pairs, 18 games) | 75 |
| rec w=2, 0.10-0.20 | +4.05 [+1.24, +6.75] | 4.45 | no | 3.88c | +0.19 [-4.39, +5.24] (194, 18) | 34 |
| rec w=2, 0.20-0.30 | -4.47 [-7.77, -1.19] | 4.79 | no | 4.48c | -1.75 [-6.87, +3.42] (208, 18) | 100 |
| rec w=3, 0.05-0.10 | -8.32 [-8.82, -7.88] | 17.09 | no | 3.76c | +5.34 [+3.79, +6.38] (**5 pairs, 5 games**) | 29 |
| rec w=3, 0.45-0.60 | -5.83 [-10.11, -1.37] | 6.21 | no | 4.57c | +0.44 [-5.93, +6.35] (230, 18) | 92 |
| rec w=3, all | -2.85 [-5.07, -0.81] | 3.41 | no | 4.24c | -3.39 [-7.21, +0.44] (669, 18) | 100 |

- **Verdict by the registered rule: a deviation survives correction below its MDE.** Not
  "mispriced": no survivor's size exceeds what the design could resolve. Not "calibrated":
  six cells did survive.
- **One survivor is an artifact, and the MDE condition is what caught it.** Receptions width
  3 priced 0.05-0.10 is 22 intervals on 18 games in which nothing hit. Every bootstrap
  resample of an all-zero cell also realises zero, so its bootstrap SE (0.24pp) is the
  spread of the prices, not of the outcome, and it reads z = -34 - the run's only Holm
  survivor. Under the ladder's own cells the SE is 6.1pp and z = -1.36. Its "realised net
  +5.34 [+3.79, +6.38]" is 5 pairs that all missed.
- **Post hoc (addendum 1), outside the verdict: on the outcome-independent SE, 0 of 46
  survive BH and 0 survive Holm.** Five cells stay nominally significant (p 0.006-0.020).
  The registered survivor count therefore depends on which SE is used; the statement that
  nothing is mispriced at this resolution does not.
- **The survivors are one shape, not six findings.** Width-3 intervals priced 0.45-0.60 are
  intervals around the centre of the ladder. c-35 reported the same sample as "too much mass
  one catch above the line" (d = +1: -3.5pp). These are overlapping summaries of that.
- No cost line shows a cell that pays: where the gap is 2-4.5pp the two legs cost 3.9-4.7c,
  and the one cell with a gap above its cost (w=3, 0.45-0.60: 5.83pp against 4.57c) realised
  +0.44pp [-5.93, +6.35] on the 18 games that have depth.

## The 2026 pre-check - the stated implication against the result

Stated before the run: c-35's +0.072 dispersion does **not** imply wide intervals are
under-priced. It implies intervals **straddling the central rung are over-priced by 1.5-3pp**
and wing intervals under-priced, so pooled wide widths should read negative.

| | predicted | implied | realised | diff, pp | pre-run MDE | reading |
|---|---|---:|---:|---|---:|---|
| S_2 | < 0 | 0.3748 | 0.3543 | -2.05 [-7.16, +3.36] | 7.16 | not detected |
| S_3 | < 0 | 0.5052 | 0.4509 | **-5.42 [-9.64, -0.76]** | 6.62 | confirmed in direction |
| S_4 | < 0 | 0.6010 | 0.5471 | **-5.39 [-9.50, -1.26]** | 6.55 | confirmed in direction |
| S_5 | < 0 | 0.6689 | 0.6409 | -2.80 [-6.84, +1.07] | 6.65 | not detected |
| S_6 | < 0 | 0.7221 | 0.6972 | -2.49 [-7.51, +2.30] | 7.05 | not detected |
| S_7 | < 0 | 0.7737 | 0.7438 | -2.99 [-9.64, +3.14] | 8.35 | not detected |
| **S_all** | < 0 | 0.6245 | 0.5881 | -3.64 [-7.62, +0.26] | 6.09 | **not detected** |
| **U** (upper wing) | > 0 | 0.0873 | 0.1017 | +1.44 [-0.31, +3.28] | 3.08 | **not detected** |
| Lo (lower wing) | > 0 | 0.1092 | 0.1373 | +2.81 [-3.43, +9.51] | 9.28 | not detected |

- **Reading by the rule fixed in advance: not detected, on both.** All nine point estimates
  carry the stated sign and none contradicts it. `S_all` is -3.64pp against a stated 1.5-3pp
  and an MDE of 6.09pp: the predicted effect was always below what three weeks could see,
  which the pre-registration said before the run.
- **This is not a replication of c-35.** It is the same 48 games re-aggregated. Agreement is
  arithmetic consistency between two summaries of one sample.
- So the brief's question has a direct answer: **in 2026 the place an interval could be
  mispriced is the centre, on the OVER-priced side, not wide intervals on the under-priced
  side.** And it is not established there either.

## Answer 3 - what the second leg costs

Every price is `market_depth` VWAP at the size on the side bought plus the taker fee on the
whole order (`quadratic`, M = 1), divided. Depth on both legs exists for **3,656 of 11,145**
intervals to buy and 3,939 to sell at 100 contracts (3,467 / 3,934 at 500), 18 games.

All tested widths, both series:

| | pairs | two-leg cost over mid | of which fee | nearest single rung | difference | break-even move, probit (two-leg / single) | thinner-leg touch p50 |
|---|---:|---:|---:|---:|---|---|---:|
| buy, 100 | 3,599 | **4.36c** | 2.04c | 2.30c | +2.06c [+1.94, +2.17] | 0.137 / 0.070 | 40 |
| sell, 100 | 3,874 | 4.37c | 2.12c | 2.33c | +2.04c [+1.89, +2.19] | 0.142 / 0.074 | 82 |
| buy, 500 | 3,417 | 5.11c | 2.04c | 2.59c | +2.52c [+2.32, +2.74] | 0.159 / 0.077 | 39 |
| sell, 500 | 3,869 | 5.00c | 2.12c | 2.68c | +2.32c [+2.09, +2.59] | 0.157 / 0.083 | 82 |

Buying the interval, 100 contracts a leg, receptions, by width:

| width | pairs | value at mid | two-leg | lower leg + upper leg | single rung | cost as share of value | capital posted per pair |
|---:|---:|---:|---:|---|---:|---:|---:|
| 1 | 987 | 0.126 | 4.30c | 2.09 + 2.21 | 1.86c | **34.1%** | 1.169 |
| 2 | 801 | 0.255 | 4.33c | 2.20 + 2.14 | 2.25c | 17.0% | 1.299 |
| 3 | 615 | 0.384 | 4.35c | 2.27 + 2.07 | 2.41c | 11.3% | 1.427 |
| 4 | 442 | 0.505 | 4.30c | 2.30 + 2.00 | 2.49c | 8.5% | 1.548 |
| 5 | 302 | 0.614 | 4.15c | 2.24 + 1.91 | 2.54c | 6.7% | 1.656 |
| 6 | 187 | 0.704 | 3.94c | 2.16 + 1.78 | 2.48c | 5.6% | 1.744 |
| 7 | 98 | 0.771 | 3.72c | 2.03 + 1.69 | 2.32c | 4.8% | 1.808 |

- **The cost of an interval does not depend on its width; its value does.** Two legs cost
  3.7-4.4c at every receptions width, so a narrow interval is the expensive one: 34% of
  value at width 1, 95% of value in the `<0.05` mass bin (3.24c on a 3.4c claim), 111% at
  500 contracts. The brief's example - width 4 - costs 4.30c on a 0.505 claim, 8.5%.
- **The second leg adds 1.4-2.4c on receptions**, i.e. the pair costs 1.6-2.3x the nearest
  single rung. In units of view the pair needs about twice the move to break even (0.137
  probit against 0.070).
- **Rush attempts are dearer**: 6.07c (width 3) and 6.87c (width 6) to buy, against 3.94c
  and 2.92c single; selling the width-6 interval is 9.35c at 100 and 12.21c at 500, where
  the thinner leg's touch is 11 contracts.
- **About half the cost is the fee** (2.0-2.1c of 4.4c), charged on each leg.
- **The pair is capital-heavy.** Buying posts both legs: 1 + value + cost per pair (1.17 for
  a 0.126 claim), of which 1 comes back in every outcome. Whether Kalshi nets that
  collateral across two rungs of one event is not known here.
- **100 contracts is not a touch fill.** The thinner leg shows 40 contracts at the touch at
  the median when buying (82 when selling); the VWAP walks the book, and a multi-level fill
  is billed per fill, which this prices once per leg.

## What this does not establish

- **"No book quotes this."** The roadmap's premise that a sportsbook does not quote an
  interval was not tested. This measures one exchange's ladder.
- **That our ladder answers it.** Nothing here concerns `models.baseline`. The market's
  ladder answers the question coherently; c-35 and brief 021/023 are the record on ours.
- **Calibration finer than the MDE.** A deviation under about 2pp on thin width-1 cells, or
  under 5-8pp on most width-3+ cells, could not have been seen in three weeks. "None is
  mispriced" means "none at that size".
- **Independence from c-35.** Same games, same settlements. The centre-is-over-priced shape
  appears in both because both are summaries of one sample; weeks 5+ are the test.
- **Another instant.** One read per game at kickoff - 180 min. Brief 022 H3 has in-game
  receptions spreads at 16c and touch sizes of 2.
- **The cost arm beyond week 3.** 16 of its 18 games are one week.
- **A resting order.** A maker pays no fee on these series and the fee is half the cost;
  briefs 018/019 and c-20 measured that path (18% fill rate, fills worth nothing).
- **The registered survivor count is SE-sensitive**: 6 on the bootstrap SE, 0 on the
  outcome-independent one (post hoc).

## Replication, fixed before week 5 settles

One quantity, one direction: receptions, intervals straddling the central rung (`S_all`),
realised **below** implied. c-35's target (RB, d = +1) stands unchanged beside it. Nothing
else from this run is a hypothesis.
