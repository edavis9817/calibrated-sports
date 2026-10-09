# c-21: is the live exchange price a fair exit?

**Scope of every figure here.** These are Kalshi `KXNFLREC` and `KXNFLRSHATT` markets, where the
YES leg is the over. The sample is NFL 2026 regular season, in-game, and covers **17 games**: week 2
Thursday (DET@BUF) and all 16 week-3 games. These are the only games whose in-game prop quotes the
logger captured at live-tier cadence. There are 1,592 settled rungs.

Every interval is a 2,000-draw game block bootstrap. Aggregates only; there are no per-bet rows and no
per-position paths (`BET_LIST_RESTRICTION`).

**Where things are.**

- Pre-registration: `docs/C21-live-exit-value-preregistration.md`, committed before any settlement
  was joined to an in-game quote.
- Script: `research/live_exit_value.py`.
- Output: `research/results/live_exit_value.{json,log}`.
- `market_log.db` was read `mode=ro` into a scratch extract.

**Kalshi is the only venue.** Polymarket's NFL markets close at kickoff, so no Polymarket price exists
to exit at during a game. No sportsbook in-game price is on disk either: the Odds API schedule ends at
T-10, and live polling is forbidden. "Hedging into a prediction market" in-game means hedging on
Kalshi, and nowhere else.

## Headline

1. **We did not detect miscalibration in the live price at this sample size, with one exception.**
   - The pre-registered over-reaction slope is **+0.036 [−0.055, +0.120]**. This is the slope of
     realised minus live mid against how far the live mid moved from its pre-game value.
     - The interval spans zero, and its point estimate leans toward *under*-reaction, not
       over-reaction.
     - MDE at 80% power is a slope of 0.13.
   - The pooled bias is −1.09pp [−3.65, +2.01], with an MDE of 4.1pp.
   - **The exception: at the end of Q3, the over is priced too high: −4.02pp [−6.92, −0.83].** The
     overpricing sits in rungs that are still 3 or more away from clearing: priced 0.110, realised
     0.045. It is one of four bias intervals, it rests on 17 games, and nothing is corrected for
     multiplicity. **It is a thing to re-measure, not to believe.**
2. **Exiting costs far more in-game than pre-game, and the cost grows through the game.**

   | | kickoff − 10 min | end of Q3 |
   |---|---|---|
   | half-spread + fee at the touch | **1.81c** | **7.85c** |
   | median half-spread | 0.5c | 3c |
   | 90th-percentile half-spread | 1.5c | 19.5c |

   - **The YES bid is where an over holder has to sell, and in-game it is nearly empty.** Its median
     touch is 20 contracts at the end of Q1, 2 at halftime and 1 at the end of Q3.
   - So at the end of Q3, **57% of books could not fill a 100-contract YES sale at all**.
3. **Exiting gives up EV at every checkpoint, and cuts variance by 57–90%.** These are two separate
   axes, and neither is a verdict.
   - The EV cost is almost entirely spread and fee.
   - The one place miscalibration enters is the end of Q3, where it cuts both ways. The mispricing
     favours an over holder who sells and costs an under holder who sells.

## Density: the precondition, checked first

The quotes table is **change-only with a 300 s heartbeat** (`QUOTE_DEDUPE`, `QUOTE_HEARTBEAT_SEC`).
So a gap between rows means "unchanged" if the market was polled, and "not polled" if it was not. A
checkpoint quote counts as fresh only if its newest row is at most 310 s old.

| | markets | games with any in-game quote | median rows per market, Q1 / Q2 / Q3 / Q4 |
|---|---|---|---|
| week 1 | 1,719 | **0**: pruned at 14 days | n/a |
| week 2 | 1,482 | 16 | **4 / 5 / 5 / 4** |
| week 3 | 1,502 | 16 | 66 / 102 / 55 / 22 |

- **Week 2's Sunday props were written about once every 10–12 minutes.** That is roughly 20 rows per
  market across four hours, so they were not on the 10 s live tier.
  - Week 2 Thursday was on the live tier, and so was every week-3 game.
  - `poll_log` shows `quotes:live` polling about 333 markets per cycle on 09-20, against 704 on 09-27.
  - **The cause is not determined.** `market_outcome.mapped_ts` is rewritten on every remap, and
    every row now reads 2026-09-29, so it cannot date the original mapping. This belongs to track A.
- The pre-registered game-level rule admitted 17 games and excluded 15. The rule: the median in-game
  gap between rows must be ≤ 60 s.
- On the live-tier games, the median gap between rows is 10.9 s in Q1–Q3 and 21 s in Q4.
- Checkpoint coverage worsens through the game:

  | | fresh and two-sided | one-sided | stale or none |
  |---|---|---|---|
  | end of Q1 | 83% | 7% | 10% |
  | halftime | 75% | 13% | 12% |
  | end of Q3 | 64% | 19% | 17% |

- **Checkpoint player counts are exact.** Play-by-play counts of receptions and rush attempts
  reconciled to the settled actual on **1,592 of 1,592** rungs.

## Question 1: live calibration (undecided rungs)

| | estimate [95%] | n | reading |
|---|---|---|---|
| **H1a over-reaction slope, pooled** | **+0.036 [−0.055, +0.120]** | 3,410 / 17 games | spans zero; MDE 0.13 |
| H1b mean(y − mid), pooled | −1.09pp [−3.65, +2.01] | 3,532 | spans zero; MDE 4.1pp |
| H1b end of Q1 | +1.17 [−1.74, +4.69] | 1,318 | spans zero |
| H1b halftime | −1.10 [−3.53, +1.89] | 1,198 | spans zero |
| **H1b end of Q3** | **−4.02 [−6.92, −0.83]** | 1,016 | over priced too high |

**By how far the market moved since kickoff − 10 min (descriptive).** Each third is scored as
y − mid.

| third | mean move | y − mid |
|---|---|---|
| bottom | −18.8pp | −2.02 [−5.90, +2.57] |
| middle | −1.4pp | −0.98 [−2.93, +1.04] |
| top | +16.0pp | −0.33 [−3.67, +4.02] |

If the market over-reacted, the top third would realise below its mid and the bottom third above it.
Neither shows.

**The canonical case (descriptive): receptions, grouped by how many more are needed to clear.**

| | 1 more | 2 more | 3+ more |
|---|---|---|---|
| end of Q1 | 0.792 / 0.786 | 0.591 / 0.609 | 0.207 / 0.214 |
| halftime | 0.682 / 0.688 | 0.419 / 0.409 | 0.141 / 0.120 |
| end of Q3 | 0.509 / 0.524 | 0.261 / 0.249 | **0.110 / 0.045** (−6.50 [−9.26, −3.68]) |

Each cell is priced / realised. A receiver who is one catch short after the first quarter is priced at
0.79 and clears 0.79 of the time. The early-production case the brief was built around shows no
mispricing here.

**The end-of-Q3 cell is not a wide-book artefact.** That was the first hypothesis. A mid taken over a
wide book near 0 is pulled toward 0.5, which would make the over look overpriced. The check was run
post hoc, and the hypothesis did not survive:

- On **tight** books (half-spread ≤ 1c; 280 rungs), y − mid is −4.09 [−8.45, −0.24].
- On wide books, it is −3.99 [−7.12, −0.26].

**It is still not tradeable on what is measured.**

- Buying NO as a taker at the end of Q3 over all books returns −3.85 [−6.57, −1.13], net of fee.
- On tight books alone, the NO buyer's edge before fees is +3.45 [−0.39, +7.81]. That spans zero, and
  the fee has not yet been taken out of it.

The direction matches the book's pre-game over bias (CLAUDE.md, brief 012). That makes it plausible,
**not corroborated.**

**Decided rungs are almost never two-sided.** A decided rung is one where the player has already
cleared the line.

- At halftime, 198 such rungs are one-sided (102), stale (83) or unquoted (11), and only 2 are
  two-sided.
- The settlement-lag effect of brief 022 H1 therefore cannot be scored at the mid on this population.
- The pre-registered "decided rungs included" arm of Q3 is, in practice, the undecided arm.

## Question 2: the cost of exiting

All figures are per contract, for a 100-contract taker order, as measured from the live book.

| | kickoff − 10m | end Q1 | halftime | end Q3 |
|---|---|---|---|---|
| half-spread p50 / p90 | 0.5c / 1.5c | 2.0c / 10.0c | 2.0c / 14.0c | 3.0c / 19.5c |
| taker fee, mean | 1.03c | 0.95c | 0.84c | 0.71c |
| **half-spread + fee, mean** | **1.81 [1.73, 1.93]** | **4.73 [3.66, 5.97]** | **6.14 [4.98, 7.42]** | **7.85 [6.35, 9.36]** |
| YES-bid touch, p50 contracts | 224 | 20 | 2 | 1 |
| YES-bid touch under 100 contracts | 41% | 75% | 72% | 83% |
| **sell 100 YES: book cannot fill** | 2% | 29% | 40% | **57%** |
| sell 100 YES, where it fills: cost vs mid + fee | 2.34 [2.12, 2.62] | 7.46 [5.69, 9.83] | 8.52 [6.40, 11.33] | 9.53 [7.51, 12.20] |
| sell 100 NO, where it fills: cost vs mid + fee | 1.97 [1.88, 2.09] | 6.28 [4.99, 7.74] | 8.69 [6.62, 11.18] | 13.18 [10.30, 16.07] |

- **Cost rises with price toward the middle.** At the end of Q3, the cost at the touch runs 2.4c
  for rungs priced 0.0–0.1, 14.5c for 0.3–0.5 and 15.4c for 0.5–0.7.
- **Exit capacity is asymmetric.** The under holder sells into the NO bids, where the median touch
  is 340–450 contracts. The over holder sells into YES bids, which are nearly empty.
- The "fills" cost rows are conditional on the book filling, which makes them optimistic about the
  over holder's real cost.

The "cannot fill" row is measured as a share of fresh two-sided books with a depth snapshot within 60 s.

## Question 3: exit against hold

- A **position** is one settled rung on one side, entered at Kalshi's mid at kickoff − 10 min.
- The **exit** is a taker sale at the touch.
- **EV cost** = mean(hold P&L − exit P&L). It is the sum of a miscalibration term and a cost term,
  and that identity is asserted in `tests/test_live_exit_value.py`.
- **Variance reduction** = 1 − Var(exit P&L) / Var(hold P&L), taken across positions.

| checkpoint | side | EV cost (pp) | = miscalibration | + spread & fee | variance reduction |
|---|---|---|---|---|---|
| end Q1 | over | +5.88 [+2.63, +9.64] | +1.31 [−1.68, +4.82] | +4.57 | **89.5% [86.9, 91.7]** |
| end Q1 | under | +3.36 [−0.31, +6.56] | −1.31 | +4.68 | 88.7% [86.0, 91.0] |
| halftime | over | +4.96 [+2.21, +8.07] | −0.99 [−3.46, +2.06] | +5.96 | 77.2% [73.8, 80.4] |
| halftime | under | +7.13 [+3.87, +9.96] | +0.99 | +6.14 | 72.6% [68.5, 76.4] |
| end Q3 | over | +3.29 [+0.75, +6.06] | **−4.46 [−7.46, −1.12]** | +7.75 | 71.8% [66.9, 76.3] |
| end Q3 | under | **+12.56 [+8.24, +16.70]** | **+4.46** | +8.10 | 57.2% [49.5, 64.0] |

These rows are undecided rungs, n = 971–1,286, over 17 games. The "all" arm is identical to within 2
rungs.

**Reading it without the framing slipping.**

- **The variance reduction is a service.** Selling after Q1 removes about 90% of the remaining P&L
  variance, and selling at the end of Q3 removes 57–72%. That is risk transferred to the buyer. It
  is not profit.
- **The EV cost is the price of that service, not a failure.** Before Q3 it is essentially the
  spread plus the fee (4.6–6.1pp), and the miscalibration terms are indistinguishable from zero.
- **At the end of Q3 the price is asymmetric.**
  - An over holder sells into an over that is priced too high, which recovers 4.5pp of the 7.8pp
    cost.
  - An under holder pays both the cost and the mispricing: **12.6pp** to exit.
  - All of this is conditional on the end-of-Q3 cell being real (see Question 1).
  - It also assumes a 100-contract over exit that the book usually cannot fill.
- **The entry price does not appear in the EV cost.** It cancels, and the test proves it.

## What the Live page would show, if this holds

It does not hold yet. Question 1 found no general miscalibration. The one cell that did show some is
post-hoc-adjacent: it was pre-registered as one of four bias intervals, but the explanation is post
hoc. On the brief's rule, that is not enough to brief a web unit.

If a replication on weeks 4 onward confirms the end-of-Q3 cell, the page would show three things:

1. The live Kalshi mid beside a fair value derived from game state. For example, "3+ receptions
   still needed after three quarters realises about 0.05 where the market prices about 0.11".
2. What each holder would actually receive:
   - the over holder: `bid − fee`, with the touch size, because it is usually under 100 contracts;
   - the under holder: `1 − ask − fee`.
3. The exit cost stated separately from any fair-value gap, and never summed into a "value" number.

**Recommendation.** Do not brief the web unit. Re-measure the end-of-Q3 cell as a registered
replication once more weeks with live-tier capture exist.

## What this cannot support

- **Seventeen games.** The MDE is 4.1pp for the pooled bias and a slope of 0.13 for over-reaction.
  Over-reaction smaller than that could not have been seen.
- **One week, effectively.** Sixteen of the 17 games are week 3. Week-2 Sunday was not captured at
  live cadence, and week 1's in-game quotes are pruned.
- **Checkpoints only.** The only moments measured are the end of Q1, halftime and the end of Q3.
  - There is no Q4 and no intra-quarter timing.
  - There are no reactions to single plays.
  - A "3 catches in the first quarter" state is measured at the end of the quarter, not at the moment
    of the third catch.
- **Selection by freshness.** Books that went one-sided or stale are excluded from the calibration
  and cost figures, and that share rises to 36% at the end of Q3. The excluded books are the thin
  ones, so the in-game cost figures are **optimistic**.
- **The entry is Kalshi's own pre-game mid, not a sportsbook price.** This decision is recorded in
  the pre-registration. The EV cost is independent of entry. The variance ratio is not.
