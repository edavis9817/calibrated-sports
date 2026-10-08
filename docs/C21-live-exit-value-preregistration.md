# C21 - is the live exchange price a fair exit? - pre-registration

Committed BEFORE any settlement was joined to an in-game quote. What had been seen
at commit time: the density step only (`python -m research.live_exit_value --density`),
which reads quotes and play-by-play and no outcome.

## Scope

Kalshi `KXNFLREC` and `KXNFLRSHATT`, the YES leg (= the over), NFL 2026 regular
season, in-game. Kalshi only: Polymarket NFL markets close at kickoff, so there is
no Polymarket in-game price to exit at, and no sportsbook in-game price is on disk
(the Odds API schedule ends at T-10 and live polling is forbidden).

## What the density step showed, and the population rule it forces

- The quotes table is **change-only with a 300 s heartbeat** (`QUOTE_DEDUPE`,
  `QUOTE_HEARTBEAT_SEC`). A gap between rows is "unchanged" when the market was
  polled, and "not polled" otherwise; the rows alone cannot say which, so staleness
  is judged against the heartbeat: **a checkpoint quote is fresh iff its newest
  row is <= 310 s old** (heartbeat + one live poll).
- Week 1 in-game quotes are gone (14-day retention on `ingest_ts`).
- Week 2 Sunday prop markets were written at roughly one row per 10-12 min
  (~20 rows per market over four hours): they were not on the 10 s live tier.
  Week 2 Thursday (DET@BUF) and all 16 week-3 games were.
- **Population rule (game level, decided on density alone):** a game is in the
  sample iff the median in-game inter-row gap across its REC/RSHATT markets is
  <= 60 s. Expected: week 2 TNF + 16 week-3 games = 17 games. Games failing it are
  counted, not analysed.

## Definitions

- **Checkpoints**: `end_q1`, `halftime`, `end_q3` - the midpoint between the last
  snap of the quarter and the first snap of the next (`time_of_day`, the snap wall
  clock), from the archived nflverse play-by-play.
- **Checkpoint quote**: newest row at or before the checkpoint, fresh (<= 310 s).
  Two-sided iff bid > 0 and ask < 1 and both present. mid = (bid + ask) / 2.
- **Pre-game price**: the same rule at kickoff - 10 min.
- **Settlement**: `core.settlement.settle` on the newest `nfl_player_week` and
  snap rows (c-19's call). Over/under only; void and unsettled counted and dropped.
- **State**: count `c` of the stat before the checkpoint from play-by-play
  (completed passes to the player; `rush_attempt` by the player; two-point tries
  excluded). Threshold `k = floor(line) + 1`. **Decided** iff `c >= k`. Pbp counts
  are reconciled to the settlement actual and the match rate is reported.
- **Blocks**: every interval is a 2,000-draw game block bootstrap. Rungs of one
  ladder and markets of one game are not independent.

## Question 1 - live calibration (undecided rungs only)

- **H1a (primary): over-reaction slope.** OLS slope of `(y - mid_t)` on
  `(mid_t - mid_pre)`, pooled across the three checkpoints. Negative = the live
  market over-reacts (moves too far); positive = under-reacts. Reading: the
  interval must exclude zero to say either.
- **H1b: mean bias** `mean(y - mid_t)` per checkpoint and pooled.
- Descriptive (not tests): calibration by price bucket x checkpoint; the canonical
  case - receptions, by remaining-needed `r = k - c` (1, 2, 3+) per checkpoint;
  decided rungs' price against their certain value (the settlement-lag effect of
  brief 022 H1, reported only so it is not mistaken for calibration).
- If H1a and H1b both span zero, question 1 is "no miscalibration detected at
  this n", reported with its minimum detectable effect, not as "calibrated".

## Question 2 - the cost of exiting

At each checkpoint and at kickoff - 10 min, on fresh quotes: half-spread
`(ask - bid) / 2`; taker fee per contract at a 100-contract order via
`core.fees.fee_per_contract` (0.07 C p(1-p), ceil to the cent on the order);
their sum. By checkpoint and by price bucket. Also: the share of fresh quotes that
are one-sided (a holder of the missing side cannot exit at any price) and the
share with no fresh quote; median touch size from `market_depth` within 60 s.

## Question 3 - exit against hold

A position is one settled market x one side (over holder, under holder), entered
at the pre-game price `e` (Kalshi's own mid at kickoff - 10 min; see below).
Exit at a checkpoint as a taker: over holder sells YES at `bid`; under holder
sells NO at `1 - ask`; fee at the exit price.

    P&L_hold = y - e                      (under: (1-y) - (1-e))
    P&L_exit = bid - fee - e              (under: (1-ask) - fee - (1-e))

- **EV cost** = `mean(P&L_hold - P&L_exit)`, decomposed exactly into a
  miscalibration term `mean(y - mid)` (sign-flipped for the under) and a cost term
  `mean(half-spread + fee)`. The entry price cancels from this difference.
- **Variance reduction** = `1 - Var(P&L_exit) / Var(P&L_hold)` across positions.
- The two are reported as **separate numbers on separate axes**. A variance
  reduction is not a profit; an EV cost is not a failure.
- Population: all positions with a fresh two-sided quote at the checkpoint
  (decided rungs included - a holder holds them too); undecided-only repeated.

**Entry price - a decision taken, not in the brief.** The brief says "the pre-game
book price". The EV cost is independent of the entry price (it cancels), and only
the variance ratio depends on it. Sportsbook prices match an exact Kalshi rung for
a minority of rungs (c-19), so the book entry would shrink the sample for a number
that does not affect the EV axis. Kalshi's own pre-game mid is used.

## Test count

H1a 1, H1b 4 (3 checkpoints + pooled), Q3 EV cost 3 checkpoints x 2 sides = 6.
11 intervals; nothing is corrected for multiplicity and nothing is read as a
finding off a descriptive cell.

## Licence

Aggregates and intervals only. No per-market row, no per-position path, no
equity curve (`BET_LIST_RESTRICTION`).
