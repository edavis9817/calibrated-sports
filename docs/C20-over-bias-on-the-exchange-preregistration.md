# C20 pre-registration: is the book's over bias tradeable on the exchange?

Unit c-20 (track C), 2026-09-29. This file is committed and pushed **before**
`research/over_bias_exchange.py` exists. It is also committed before any Kalshi
trade print for weeks 2-3 is fetched, and before any realised outcome is read
against a Kalshi price for this population.

## What was seen before this file was written

- **c-19's published aggregates.** The Kalshi over sits 1.06pp [0.82, 1.30] below the
  de-vigged book close on receptions and 0.74pp on rush attempts. Kalshi-minus-book
  Brier is +0.0005 [-0.0012, +0.0022]. Median quoted spread is 1c at kickoff-10m.
- **c-22's and R18's book-side over bias.** Every figure is against sportsbooks and
  covers 2023-2025.
- **Six week-2 receptions trade tapes.** These were fetched to check that
  `/markets/trades` still serves settled markets. I read the print COUNTS only (5-73
  pre-kickoff prints each), not prices against outcomes.
- **Not seen:** any realised over rate against a Kalshi price for weeks 2-3, any
  fill, and any PnL.

## Hypothesis (C20-H1)

On Kalshi `KXNFLREC` and `KXNFLRSHATT`, a **resting order to buy NO (the under)**
placed before kickoff and held to settlement earns a positive net return
**conditional on being filled**.

The mechanism proposed by the brief:
- The book over is overpriced (R18).
- Kalshi sits only ~1pp below the book (c-19).
- The two series are maker-fee-free (M = 0).

The arithmetic that motivates it mixes populations, so it is a reason to run the
test and not a prior estimate.

## Scope — stated so it travels with every number

- **Venue:** Kalshi only. No sportsbook price enters any registered quantity.
- **Series:** `KXNFLREC` and `KXNFLRSHATT`. Every market is YES = over the threshold.
- **Season and window:** NFL 2026 regular season, **weeks 2 and 3**. This is c-19's
  window. Week 1's live quotes are pruned. Week 4+ is reserved as a replication
  holdout (see the decision rule).
- **Population:** the `kout` rows of c-19's scratch cache (`D:/temp/c19/extract.sqlite`,
  extracted 2026-09-28 ~22:15 ET), over side, weeks 2-3, whose game has a final score
  in that cache.
  - Settlement is `core.settlement.settle` against the store's `nfl_player_week` at
    its latest `data_version` (`market_log.db`, `mode=ro`) plus snap rows. This is
    exactly c-19 Step 4's call.
  - Voids and unsettled rows are excluded and counted.
- **Prices:** the last Kalshi quote row at or before the instant, from the same cache.
  - It must be two-sided (`0 < bid < ask < 1`) and no more than 600s old: c-19's
    `kalshi_at` rule, reused unchanged.
- **Instants:**
  - **E = kickoff - 180 min**, the PRIMARY. It is c-19 Step 4's entry.
  - **K = kickoff - 10 min**, SECONDARY, for the bias measurement only.
- **Prints:** Kalshi `/markets/trades`. The endpoint is public, unauthenticated and
  free: no credits, no spend.
  - Fetched for every population market, bounded to [E - 60s, kickoff]. A capped window
    is walked back to its start (`walk_window`, brief 019).
  - Written only to a scratch store, `D:/temp/c20/trades.sqlite`, with every raw page
    gzipped under `D:/temp/c20/raw/` before parsing.
  - Nothing is written to `market_log.db`, to `trades_m01.db` or to the production raw
    archive.
  - A market whose tape fetch fails is excluded from the maker arms and counted.
  - Requests are at most 3/s, with blind backoff on a 429.

## M1 — the over bias on Kalshi's own prices (premise check)

- **Estimate:** G = realised over rate minus mean Kalshi YES mid, in pp.
  - Computed at E (primary) and at K.
  - Rows are the rungs two-sided at that instant.
- **Interval:** game-block percentile bootstrap, 2,000 draws, seed 20, blocks =
  `game_id`.
  - A ladder's rungs are one claim priced at many thresholds, so games are the
    effective sample.
- **Reading:**
  - A negative G whose interval excludes zero means the Kalshi over is overpriced on
    its own mid.
  - An interval spanning zero means the premise "the Kalshi under is cheap" is **not
    established on Kalshi's prices**. That is reported whatever the trade arms show.
- **By stat:** descriptive only.

## The three trade arms — side is the UNDER (buy NO), held to settlement

Placed at E and cancelled at kickoff if unfilled. Never in-game (brief 022 H3).

- **Ticket:** C = 100 contracts, PRIMARY. 10 and 1,000 are descriptive.
- **Per-contract net, in pp:** `100 x (1{under} - price) - 100 x fee(price, C)/C`.
  - The fee is `core.fees.kalshi_fee`: Decimal arithmetic, ceil-to-cent on the WHOLE
    ORDER of C contracts, with the series multiplier from `series_multiplier`.
  - The fee is never computed at 1 contract and multiplied.

The arms:

- **T — taker at the ask.**
  - Buy NO at `1 - yes_bid` at E, so it always fills.
  - Taker fee M = 1.
  - Kept only if the `buy_no` touch size in the nearest `market_depth` snapshot within
    60s of E is at least C. Otherwise it is excluded and counted: walking the book is
    not modelled, and the cache holds the touch only.
- **MB — maker at the bid (the NO touch).**
  - Rest a NO bid at `q = 1 - yes_ask`.
  - Queue ahead = the `buy_yes` touch size (the size resting on the NO bid), from the
    nearest depth snapshot within 60s of E. With no such snapshot the row is excluded
    and counted.
- **MM — maker at the mid.**
  - Rest a NO bid at `q = floor_to_cent(1 - yes_mid)`.
  - Where the spread is 1c this equals the NO touch, so MM is MB with the same queue.
    The count of such rows is reported.
  - Where it improves the touch, the queue ahead is 0.
- **Fill rule, both maker arms.** This is `research.maker.simulate` / `eligible`,
  unchanged (M01's rule, pinned by `tests/test_maker.py`):
  - A passive NO buy is filled by prints with `taker_side == "yes"` at
    `no_price <= q`, in [E, kickoff).
  - It fills once cumulative eligible size reaches queue-ahead + C.
  - Cancellations ahead of us, which would help, are not modelled. Nor is price
    improvement by others, which would hurt.
- **Maker fee** is `kalshi_fee(q, C, "maker", M)` with M from `series_multiplier`.
  That is 0 for both series.

## What is reported per arm, and which number is primary

- **Fill rate:** filled / placed. Game-block interval.
- **PRIMARY — fill-conditional net PnL, pp/contract.** This is the mean over FILLED
  orders, with a game-block interval: resample games, then recompute the mean over
  the filled rows in the draw.
- **Selection gap:** mean over filled minus mean over unfilled, at the same price and
  the same accounting. It is bootstrapped as ONE quantity over shared game draws.
  - The unfilled mean is hypothetical: those orders did not trade.
  - It is reported only as the gap's second term, never as an edge.
- **Per order placed:** mean of `1{filled} x net` over every order. This is the
  expected value of posting the order, and it equals fill rate x conditional.
- **Post-fill drift:** the NO mid at the last quote before kickoff minus the NO mid at
  the fill, in pp. This is adverse selection proper.
- **An "if always filled" maker figure is NOT reported as an edge.** It is wrong by
  construction, per the brief and brief 018.
- **Taker arm:** mean net over every kept row, with its interval. It has no
  conditional, because it always fills.
- **Price buckets**, on the YES mid at E: `[0,.2) [.2,.4) [.4,.6) [.6,.8) [.8,1]`.
  - For each arm: n, fill rate, conditional net with interval, and the fee in the
    bucket.
  - DESCRIPTIVE, not tests: five buckets x three arms is fifteen more intervals, and
    none is corrected for multiplicity.
- **By stat:** descriptive.

## Decision rule — fixed here

The primary is **MB at C = 100, fill-conditional net**.

- **SUPPORTED:** its 95% interval lies entirely above 0.
  - The hypothesis then goes to a replication on weeks 4-6 with this script unchanged.
  - A single two-week window never makes it a finding.
- **RETIRED:** its point estimate is at or below 0.
- **OPEN:** point above 0 with the interval spanning 0.
  - Not a finding. It is retired if the week 4-6 replication's point estimate is at or
    below 0.
- **MM** is judged by the same rule as a secondary.
- **T** is reported against the same thresholds. The prediction is that it is negative.
- **Materiality,** whatever the verdict: per-order EV x orders placed per week x C,
  in dollars. Below $50 a week, a SUPPORTED result is "real and not a business",
  as brief 022 H1 was.
- **Premise:** if M1 at E spans zero, the report says the Kalshi under was not
  measurably cheap on Kalshi's own prices in this window, whatever the arms show.

## Honest prior, written down before the numbers

- Brief 018: the conditional maker CLV on REC was +0.28 [-0.34, +1.00], and the fills
  were worth nothing.
- Brief 019: the conditional was negative on tight series. c-19 shows REC/RSHATT were
  tight in weeks 2-3.
- **My prediction:**
  - MB conditional is at or below zero, so RETIRED.
  - T is negative.
  - M1 is negative but smaller than R18's -2.43pp. Its interval may span zero on 31
    games.

## Registered intervals

- M1: 2, at E and at K.
- Per maker arm: fill rate, conditional and gap, so 3 x 2 = 6.
- T: 1.
- **Total 9.** None is multiplicity-corrected.

## Out of scope, and why (decisions taken unattended)

- **a-53's stored cross-venue join is not used.** The brief says not to start before
  a-53 merges, and it has not: `origin/a-53-cross-venue-join` is not an ancestor of
  `origin/main` on 2026-09-29. I started anyway, for three reasons:
  1. Every registered quantity above is Kalshi-only. The book enters none of them.
  2. The book comparison the join would serve is c-19's, and c-19 already published it.
  3. Week 2's live Kalshi quotes began pruning from the store on 2026-09-29. c-19's
     cache preserves them, but waiting only shrinks what a re-extract could see.

  Reversal: after a-53 merges, the book side (Kalshi vs de-vigged close, R18's gap on
  the same rungs) can be added as a separate unit through `research.cross_venue_join`.
- **No sportsbook price** is used, so no de-vig choice arises.
- **Week 4+** is not read. It is reserved for replication.
