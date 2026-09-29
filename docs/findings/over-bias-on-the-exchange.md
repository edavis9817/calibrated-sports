# Is the book's over bias tradeable on the exchange?

Unit c-20 (track C), 2026-09-29. This is a findings document; nothing publishes from here.

- **Scope, so it travels with every number.**
  - Venue: Kalshi only. Series `KXNFLREC` and `KXNFLRSHATT`, where YES = over.
  - Window: NFL 2026 regular season, weeks 2-3, pre-kickoff. This is c-19's window
    and c-19's cache.
  - Trade: the **under** (buy NO), placed at kickoff - 180 min, cancelled at kickoff
    if unfilled, held to settlement.
  - No sportsbook price enters any number below.
- **Pre-registration.** `docs/C20-over-bias-on-the-exchange-preregistration.md` was
  committed and pushed at **`fefa959`**. At that point
  `research/over_bias_exchange.py` did not exist and no weeks 2-3 trade print had been
  fetched.
  - Afterwards: the fetcher was written and run, then the analysis.
  - One labelled POST-HOC block was added after the registered output had been read.
    It is marked as such below.
  - No registered rule was changed.
- **Reproduce.**
  - `python -m research.over_bias_exchange --fetch` pulls the free, unauthenticated
    Kalshi `/markets/trades` tape into a scratch store. That took 998 s for 2,984
    markets, 34,049 prints, 0 capped windows and one 429.
  - Then run `LOGGER_DB=<market_log.db> python -m research.over_bias_exchange --json-out F`
    with the 3.12 venv, which takes about 2 min.
  - `market_log.db` is opened `mode=ro`. The quotes and depth come from c-19's cache,
    `D:/temp/c19/extract.sqlite`, because week 2's live quotes began pruning from the
    store today.
  - Aggregates are committed at `research/results/over_bias_exchange.json` and `.log`.
- **Licence.** Aggregates and intervals only. No per-bet series and no equity curve.
- **Intervals.** Every interval is a game-block percentile bootstrap: 2,000 draws,
  seed 20, blocks = games. Each quantity is drawn independently. The selection gap is
  one quantity over shared draws.

## Headline

**By its pre-registered rule, the hypothesis is RETIRED.**

- A resting under bid at the NO touch, filled, lost **−0.60pp per contract
  [−9.18, +8.54]**. That is 220 fills from 1,228 orders across 16 games.
- The premise under it did not show up on Kalshi's own prices:
  - the Kalshi over is **not measurably overpriced against its own mid** in this
    window: **−0.40pp [−2.67, +2.07]**, 2,513 rungs, 31 games;
  - the taker route loses **−5.06pp [−10.06, +0.03]**.

**What the measurement cannot say.** It cannot rule out a small edge in either
direction:

- the primary interval is **±8.9pp** wide, and the M1 interval ±2.4pp;
- the brief's indicative 1.3pp would be invisible to both.

"Retired" here means the rule fired on a point estimate at or below zero. It does not
mean an edge of 1-2pp was shown not to exist.

## M1 — the over bias on Kalshi's own mid (the premise)

| instant | rungs | games | mean YES mid | realised over | gap pp [95%] |
|---|---:|---:|---:|---:|---|
| **E, kickoff − 180 min** | 2,513 | 31 | 0.3399 | 0.3359 | **−0.40 [−2.67, +2.07]** |
| K, kickoff − 10 min | 2,589 | 31 | 0.3396 | 0.3329 | −0.67 [−2.82, +1.67] |
| E, receptions (descriptive) | 2,203 | 31 | | | −0.32 [−2.95, +2.41] |
| E, rush attempts (descriptive) | 310 | 31 | | | −0.97 [−7.39, +5.85] |

- **The population reproduces c-19.**
  - Step 4's common set was 2,513 rungs over 31 games. This is the same 2,513.
  - It follows 106 exclusions in the week-3 game with no final score, 25 voids and 340
    rungs not two-sided or stale at E.
- **Reading.**
  - Both intervals span zero, so the premise "the Kalshi under is cheap" is **not
    established on Kalshi's own prices** in weeks 2-3.
  - The point estimates are negative, which is R18's sign, but far smaller than R18's
    −2.43pp.
  - These rungs are ladders. The mean YES mid of 0.34 says most sit away from 50c,
    where c-22 found the book-side bias fades.
  - So "R18's −2.43pp minus c-19's 1.06pp = 1.3pp" was never the right expectation
    for this population. **That subtraction mixed a near-the-money book population
    with an exchange ladder.**

## The three arms — under side, 100 contracts, placed at E, cancelled at kickoff

| arm | orders | fills | fill rate % | **fill-conditional net pp** | verdict |
|---|---:|---:|---|---|---|
| T, taker at the ask | 520 | (all) | — | −5.06 [−10.06, +0.03] (every row) | negative, as predicted |
| **MB, maker at the NO touch** | 1,228 | 220 | 17.9 [12.4, 24.1] | **−0.60 [−9.18, +8.54]** | **RETIRED** |
| MM, maker at the floored mid | 1,228 | 376 | 30.6 [22.0, 40.2] | +0.10 [−6.99, +6.34] | OPEN, meaning not a finding |

**Adverse selection** in the maker arms:

| | MB | MM |
|---|---|---|
| selection gap (filled − unfilled), one quantity | −1.22 [−10.08, +8.64] | +0.37 [−7.37, +7.68] |
| unfilled, hypothetical, at the same price | +0.62 [−2.77, +3.63] | −0.27 [−4.08, +3.01] |
| **post-fill drift** (NO mid at kickoff − at fill) | **−0.61 [−1.34, −0.15]** | **−0.41 [−0.88, −0.10]** |
| per order placed (fill rate × conditional) | −0.11 [−1.68, +1.40] | +0.03 [−1.98, +1.93] |
| materiality at 100 contracts, ~614 orders a week | −$66/week | +$19/week |

- **Adverse selection is visible, and only in the drift.**
  - After a fill, the NO mid moves against the filled order by 0.4-0.6pp before
    kickoff. Both intervals exclude zero.
  - That is the price confirming that the taker who hit the order knew something.
  - The settlement-based gap is too noisy to see it at all: ±9pp on a binary payoff
    over 16 games.
  - This matches brief 018's shape on week 1 (drift −0.35 [−0.79, +0.06]), now with an
    interval clear of zero.
- **MM is not a better strategy, and it is not a second, independent arm.**
  - 610 of its 1,228 orders sit at a 1c spread, where the floored mid IS the NO touch,
    so they are MB with the same queue.
  - It fills more (30.6%) only because on the other 618 it rests inside the spread
    with no queue ahead.
  - Under the rule "OPEN" means a point above zero with an interval spanning it. At
    +0.10 it is not distinguishable from MB and not a finding. Its registered
    retirement condition is the week 4-6 replication.
- **The queue is deep.** The median queue ahead at the NO touch was 3,000 contracts.
  The depth snapshot's touch price equalled the quoted ask on 1,225 of 1,228 orders.
  These books are not thin: c-19's premise correction holds.
- **The taker arm confirms the brief's arithmetic without needing it.**
  - The mean taker fee on these rows was 1.26pp at 100 contracts, computed on the whole
    order in Decimal.
  - The under was not cheap enough at the ask to pay for it.

### By YES-mid bucket at E — DESCRIPTIVE, 15 intervals, uncorrected

| YES mid | rungs | M1 gap | T net | T fee | MB fills (games) | MB cond | MM fills (games) | MM cond |
|---|---:|---|---|---:|---|---|---|---|
| 0.0-0.2 | 1,069 | +0.93 [−1.08, +2.95] | −6.92 [−15.22, −0.65] | 0.48 | 57 (13) | −4.95 [−17.04, +5.91] | 144 (16) | −2.92 [−9.04, +2.62] |
| 0.2-0.4 | 472 | +0.40 [−3.64, +4.82] | −17.71 [−27.55, −6.55] | 1.43 | 57 (15) | −9.42 [−22.67, +6.98] | 84 (16) | −4.06 [−14.64, +8.12] |
| 0.4-0.6 | 419 | −3.67 [−8.23, +0.92] | −0.10 [−5.87, +4.98] | 1.73 | 60 (14) | +3.20 [−10.58, +16.92] | 75 (15) | +1.52 [−11.52, +14.19] |
| 0.6-0.8 | 344 | +0.80 [−3.41, +5.07] | −5.79 [−14.99, +2.86] | 1.49 | 36 (12) | +6.14 [−7.78, +22.40] | 55 (14) | +3.82 [−5.03, +13.18] |
| 0.8-1.0 | 209 | −4.50 [−10.13, +0.99] | +2.19 [−7.93, +11.14] | 0.94 | 10 (5) | +27.40 [+8.60, +53.00] | 18 (7) | +26.39 [+6.55, +43.00] |

- **Near the money is where the brief's mechanism would live, and it is where the fee
  peaks.**
  - At 0.4-0.6, M1 is −3.67 [−8.23, +0.92]: R18's sign and size, but spanning zero.
  - The taker fee there is 1.73pp, the highest of any bucket, and the taker nets
    −0.10.
  - The two maker arms point the right way, +3.20 and +1.52, with intervals of
    ±13-14pp.
  - This is the one place the idea is not contradicted. It is also the place the
    sample says least.
- **The 0.8-1.0 row is not a finding.**
  - Its maker intervals exclude zero, but they rest on **10 and 18 fills in 5 and 7
    games**.
  - They are one of fifteen uncorrected descriptive cells, so about one such cell is
    expected by chance.
  - It is a NO bought at ≤ 20c, a longshot under, where a handful of wins moves the
    mean by tens of points.
  - It is recorded so the week 4-6 replication can look for it. It is not to be
    quoted.

### By stat and by ticket size — DESCRIPTIVE

| | T net | MB fills / orders, cond | MM fills / orders, cond |
|---|---|---|---|
| receptions | −3.70 [−9.13, +1.94] | 188/1,079, −0.45 [−9.14, +9.25] | 326/1,079, +0.50 [−6.24, +7.73] |
| rush attempts | −14.42 [−26.05, −3.44] | 32/149, −1.50 [−24.77, +18.85] | 50/149, −2.48 [−20.44, +11.81] |
| C = 10 | −2.69 [−6.04, +0.35] (n 1,092) | 259/1,228, −0.11 [−8.77, +8.05] | 487/1,228, +0.03 [−5.56, +5.35] |
| C = 1,000 | −8.11 [−16.28, +0.54] (n 216) | 109/1,228, +0.70 [−12.00, +9.81] | 169/1,228, −0.56 [−10.56, +8.34] |

Shrinking the ticket raises the fill rate and moves the conditional net nowhere.
This is brief 018's result again: the binding constraint is the queue, not the size.

## POST-HOC, not registered: is the maker subset the same population?

- **Depth coverage limits the maker arms, not the 60s rule.**
  - Kalshi depth was captured for these series in **17 of the 31 settled week 2-3
    games**.
  - 1,570 of 2,853 settled markets have no depth snapshot at any time.
  - 1,264 have one within 60s of E. Widening the window to 300s, 900s or 3,600s gives
    the same 1,264, so it adds **zero** markets.
  - So the maker arms (and the taker arm, which also needs depth) run on 16 games, not
    31. The script prints this as `depth coverage`.
  - Why the depth cycle covered only some games is not established. I did not read
    the logger's depth scheduling.
- **On that subset, M1 is +0.49 [−2.48, +3.87]** (1,228 rungs, 16 games), against
  −0.40 on the whole population.
  - The subset is not visibly different.
  - It also carries no over bias to harvest, which is the plainest reason the arms
    find nothing.

## What this cannot support

- **Power.**
  - The primary interval's half-width is 8.86pp on 220 fills in 16 games. A true
    fill-conditional edge of 1-3pp would be read as nothing here.
  - The replication needs many more weeks with depth coverage, not a re-run of these
    two.
  - The registered rule retires on the sign of a point estimate, and I wrote that rule
    before I knew the interval would be this wide.
- **The fill model.**
  - Fills are M01's rule unchanged: behind the displayed queue, no cancellations ahead
    modelled, no price improvement by others modelled.
  - Our own order is assumed not to change anyone's behaviour.
  - At a median queue of 3,000 contracts, a fill needs 3,100 contracts of YES-taker
    flow at or through the level before kickoff.
  - The simulated fill is an estimate. Only real resting orders measure the true fill
    rate.
- **Weeks 2-3 only, 2026 only, one entry instant.**
  - Lead-time effects are not separable.
  - The K-instant bias figure is for the premise only; no trade was simulated at K.
- **No book comparison.** a-53's stored join is not merged. R18's gap on these same
  rungs was not measured, and the book-vs-Kalshi spread is c-19's figure, not
  re-derived here.
- **Staleness.** The population depends on c-19's scratch cache and on c-20's scratch
  trade store. Week 2's live quotes leave `market_log.db` from 2026-09-29, so a
  re-extract after about 10-05 sees a smaller population. The trade tape is re-fetchable
  for free.

## Registered intervals: 9, uncorrected

- 2 for M1: at E and at K. Neither excludes zero.
- 6 for the maker arms: MB and MM each carry a fill rate, a conditional and a
  selection gap. The fill rates exclude zero by construction; nothing else does.
- 1 for T. It spans zero at its upper end, +0.03.

The two drift intervals that exclude zero were **reported under the registered plan
but were not among the 9 counted**. Read them as supporting, not as tests.
