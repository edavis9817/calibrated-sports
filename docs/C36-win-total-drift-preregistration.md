# C36 - what a season win total does between now and January: pre-registration

Unit c-36 (track C), written 2026-10-08, **before `research/win_total_drift.py` exists** and
before any fetched candle, any weekly change or any model number for this unit was read.
Base: `c-35-ladder-edges` (`a0f0354`), the tip of the stack that sits on c-34.

## Scope, stated so it travels with every number

Kalshi `KXNFLWINS-27{TEAM}-{k}` ("at least k wins"), 32 teams, NFL 2026, from the market's
open (2026-04-20) to 2026-10-08. **Four weeks of the season have been played**, so there are
four weekly transitions per team (128 team-weeks), not sixteen. The model is a-41/a-55's season
model (`models.season`, margin-of-victory Elo with per-simulation rating draws), at the
published constants, at states after weeks 0-4. Nothing here has settled and **nothing here
scores anyone against truth**; every test below is about whether a PRICE CHANGE is predictable,
which needs no settlement. `KXNFLWINSWEEK`, the division series and the conference champions
are out of scope. Nothing here is about sportsbooks.

c-25's published figures and `research/registers/c25_forward_calls.json` are not touched; this
unit does not even have those files in its tree (c-25 is on its own branch) and reads c-25's
preserved quotes with `git show`, for one validation only.

## What was read before this file was written

- The census: 1,348 season-future markets, **0 retention holds on any of them**, live quotes
  only from 2026-09-24 to 2026-10-08 (the 14-day window), 0 `market_depth` rows, 0
  `market_trades` rows, 0 results. So of the Tuesday instants below, S_3 and S_4 are on disk and
  **S_0, S_1 and S_2 exist only as candles** (c-25 preserved Wednesday 09-16 and 09-23 at 16:00
  UTC, which are this unit's S'_1 and S'_2, and Tuesday 09-29, which is S_3).
- A probe of two tickers (`KXNFLWINS-27MIA-5`, `-27KC-10`) on Kalshi's free, unauthenticated
  candlestick endpoint: daily candles exist from 2026-04-21 and carry `yes_bid`, `yes_ask`,
  `price`, `volume_fp`, `open_interest_fp`. I saw the first and last candle of each (MIA-5
  0.21/0.22 and KC-10 0.83/0.88 on 10-08) and one order book. No change was computed.
- The 2026 schedule: weeks 1-4 complete, 16 games each, no byes; week 5 starts 10-09 00:15 UTC.
- c-25's findings (the model is compressed relative to the market, slope 0.59/0.69/0.76 after
  weeks 1/2/3) and its descriptive movement line (d-market on gap +0.108 [-0.024, +0.285], n 64).
  That line is the same question as Q3 below on half the data without the controls; I have seen
  it and it is not evidence for or against what follows.
- A fetch of raw candles and order books was STARTED before this file was finished and is
  archived verbatim to `<STORAGE>/research_raw/c36/`; no fetched file was opened before this
  commit. Free endpoints, zero credits.

## Data

- **Candles** (`/series/KXNFLWINS/markets/{t}/candlesticks`): daily from 04-20 to 09-07, hourly
  from 09-07 00:00 UTC to the fetch hour. A rung's quote at instant T is the `yes_bid.close` /
  `yes_ask.close` of the hourly candle with `end_period_ts == T`. **Usable** = `0 < bid < ask <
  1` and `ask - bid <= 0.15` (an empty book quotes 0.01/0.99 and its mid is a meaningless 0.5).
  Mid = (bid + ask) / 2.
- **Validation of the candles, before anything is believed**: candle quote against the live
  `quotes` row at S_3, S_4 (market_log.db, `mode=ro`) and against c-25's preserved rows at S'_1,
  S'_2. Reported as the share of rungs whose bid and ask both agree to 1c and the median absolute
  mid difference. **If fewer than 90% agree at S_3/S_4 the unit stops and reports that.**
- **Order books** (`/markets/{t}/orderbook`), one snapshot per rung tonight, for Q4 only.
- **Fee**: `fee_type` and `fee_multiplier` from `/series/KXNFLWINS` fetched tonight, checked
  against `core.fees`; a mismatch stops Q4.

## Definitions

- **Instants** (UTC). S_k = the Tuesday 16:00 after week k: S_0 09-08, S_1 09-15, S_2 09-22,
  S_3 09-29, S_4 10-06. S'_k = S_k + 24 h. For a team's week-w game with kickoff K:
  PRE = K - 1 h, POSTa = K + 5 h, POSTb = K + 17 h. Every week-k game ends before S_k and no
  week-(k+1) game starts before S'_k + 8 h; the script asserts it.
- **Decided rung**: current whole wins >= k (1) or current + remaining < k (0), from the
  schedule. A decided rung enters a ladder at 0/1 and is never a "moving rung".
- **Market number m(t, T)**: the team's ladder of usable mids at T plus its decided rungs, made
  non-increasing in k by pool-adjacent-violators, linearly interpolated to the win count where
  it crosses 0.5 (c-25's `crossing`). This is the implied MEDIAN in "at least" units, one number
  per team per instant. A team-instant without a crossing is dropped and counted.
- **Model number f(t, k)**: the same crossing on the model's ladder P(whole wins >= r) from
  20,000 simulations at the state after week k (k = 0 is the preseason snapshot).
- **gap_k = f(t, k) - m(t, S_k)**, in wins.
- **Unit of observation: one team-week.** A ladder is one claim; rung-level figures are
  descriptive only. **Blocks: team** (32), 2,000 draws, week fixed effects in every regression.
  Two teams share a game each week; game-clustered SEs are printed beside the primary.
  z = estimate / bootstrap SE; MDE = 2.8 x SE; BH at q = 0.10 over the registered tests
  T2a, T3-P, T3-D (three). An interval on fewer than 5 blocks is not read.

## Q1 - how much does a rung move, and when (descriptive, no test)

- Weekly change S_k -> S_{k+1} of the market number (wins), by team and week; and of each
  usable undecided rung's mid (pp), by the rung's distance from the number at S_k
  (|k - m| in bands 0-1, 1-2, 2-3, 3+) and by week.
- **When**: for each team-week, the squared change of the market number split into the team's
  own game window (PRE -> POSTa, 6 hours) and the remaining ~162 hours; the share of the week's
  summed squared movement that falls in the game window, pooled, with a team-block interval.
- Preseason: mean absolute daily change of the central rung's mid, 04-21 to 09-07, against the
  in-season daily figure.
- Split by result: mean change of the number after a win and after a loss.

## Q2 - does the market move more than it should

Stated first: the model is not the ideal Bayesian, so "more than the model" is not "too much".
The registered TEST is therefore model-free.

- **T2a (registered, model-free).** If the market overreacts to a result, part of the jump comes
  back. Regress the post-game drift on the game-window jump, per team-week, weeks 1-4:

      drift = m(S_w) - m(POSTb)        jump = m(POSTa) - m(PRE)

  with week fixed effects. **Negative slope = overreaction, positive = underreaction, zero = a
  martingale.** The jump ends at POSTa and the drift starts at POSTb, twelve hours later, on a
  different quote: if both used one instant, bid-ask noise in that shared price would enter the
  two with opposite signs and manufacture a negative slope (the mirror of c-32's shared-open
  artifact). The same-instant version (drift from POSTa) is printed beside it, labelled
  artifact-prone, so the size of that artifact is visible.
- **T2b (descriptive, not in BH).** Weekly change of the market number against the weekly change
  of the model number over the same week: slope of d-market on d-model, the ratio of their RMS,
  and the same two split by win and loss. A slope above 1 says the market moves further per win
  of model update; c-25's compression (the market spreads teams wider) predicts above 1 without
  any overreaction, which is why this is not the test.
- **Dispersion path (descriptive).** SD across teams of m and of f at S_0..S_4 and the slope of
  f on m, extending c-25's post hoc table to five instants. Post hoc there, descriptive here.

## Q3 - does the model's disagreement at week k predict the move from k to k+1

**The trap, and how the two are separated - fixed here, not afterwards.** Ratings and prices
move on the same games, so "our update and the market's update correlate" is mechanical. Three
separate mechanical channels, each closed by construction:

1. *Same-week results.* The regressor is the gap at S_k, which contains no week-(k+1) result.
   The week-(k+1) result enters the regression directly (below), so the gap cannot proxy it.
2. *The market's own level.* The model is compressed relative to the market (c-25), so the gap
   is, to first order, minus a multiple of (m - 8.5): any tendency of strong teams' numbers to
   move differently from weak teams' would load on the gap with no model skill in it. The
   market's own level is therefore a control, **which makes the test deliberately one of the
   model's idiosyncratic view only** - the part of the gap a "shrink the market toward 8.5"
   rule would not have produced.
3. *Shared price noise.* gap_k uses m(S_k); the move starts at S'_k, 24 hours later, on a
   different quote. Noise in m(S_k) is not in the move.

- **T3-P (registered, PRIMARY).** For k = 0..3, team-week:

      m(S_{k+1}) - m(S'_k) = a_week + b1*win + b2*(m(S_k) - 8.5) + b3*win*(m(S_k) - 8.5)
                             + beta * gap_k + e

  `win` is the team's week-(k+1) result (1 / 0 / 0.5). The first four terms are "what the
  market's own prior explains": the week, the result, the level, and that a strong team's win
  is less of a surprise than a weak team's. **beta is the test**: wins of market move per win of
  model disagreement, over one week. beta = 0 is a market the model cannot predict; beta = 1 is
  a market that closes the whole gap in a week.
- **T3-D (registered).** The same without any result inside the window: the pre-game drift

      m(PRE_{k+1}) - m(S'_k) = a_week + b2*(m(S_k) - 8.5) + beta_D * gap_k + e

  Nothing happens to the team on the field between S'_k and PRE, so there is no result channel
  at all. It is the cleanest version and the least powerful (the window is 1-5 days of drift).
- **Reported beside them, not tests:** beta without the level control (so the size of channel 2
  is visible); beta with the move started at S_k instead of S'_k (channel 3); the leave-one-week
  -out range of beta; the gap's own week-to-week persistence.
- **Reading rule, fixed now.** Four weeks, 32 blocks. If T3-P's interval contains zero the
  verdict is "not detectable at 128 team-weeks; MDE x", never "the model has no information".
  If it excludes zero the verdict is a price-drift finding on one month, to be re-run as weeks
  accrue, and **not** an edge: an edge needs the cost of Q4 set against it, and a positive beta
  with c-25's unsettled gap says the market moved toward the model, not that the model is right.
- **If beta is positive, the tradeable version (registered as a table, not a test):** at S'_k
  buy the model's side of the team's central rung at the candle ask (YES) or 1 - bid (NO), sell
  at S_{k+1} at the opposite touch, taker fee both ways at 100 contracts; mean P&L per contract
  with a team-block interval. One round trip, candle touch prices, no depth: an upper bound on
  what a taker gets.

## Q4 - what it costs to hold one (descriptive)

- From tonight's books, per undecided rung with a two-sided book, by price band: touch spread,
  size at the touch, VWAP at 100 / 500 / 1,000 contracts on both sides (a YES buy lifts
  `1 - no_bid` levels; a NO buy lifts `1 - yes_bid` levels), and the taker fee from
  `core.fees` at that size. All-in cost over the mid per contract, one crossing (held to
  settlement) and two (a round trip).
- From the candles: the median usable spread by week and by distance from the number; daily
  volume and open interest per rung, as the measure of how long a size takes to trade.
- **Carry.** Days from tonight to the series close (2027-01-18). Capital tied per contract is
  the price paid. Against an alternative yielding r a year, carry = price x r x days / 365.
  r is shown at 4% and 0% - **4% is an assumption standing in for a T-bill-like alternative, not
  a measured rate**, and whether Kalshi pays interest on position collateral is not checked.
  The comparison the brief asks for is then arithmetic: the edge a hold-to-January needs to
  equal a one-week round trip of the same gross edge, both net.
- One book snapshot, taken around 02:00 ET on a Thursday. Depth at that hour is not depth on a
  Sunday; this is said wherever the figure appears.

## What would make me stop

- Candle validation under 90% at S_3/S_4.
- Fewer than 24 teams with a market number at any S_k (the test would be about a subset).
- The model's state-3 mean wins not within 0.051 of the published projection for every team
  (c-25's reproduction check, kept).

## Not claimed, whatever comes out

That the model is better or worse than the market at forecasting wins. That any drift found
here persists past week 4. That a taker can get the candle touch in size.

## Addendum 1 - 2026-10-08, after the first run STOPPED at validation and before any result

The first run (`e5ce5a7`) stopped at the candle validation and printed nothing after it. No
weekly change, no model number and no regression has been computed or seen. What the stop and
two diagnostics (`D:/temp/c36/diag*.py`, validation only) showed:

- **Kalshi does not emit an hourly candle for an hour in which nothing changed.** 205,182
  hourly candles over 544 rungs is a median of 398 per rung across ~750 hours. I registered "the
  hourly candle with `end_period_ts == T`" on the assumption of one candle per hour, taken from
  a probe of two busy rungs. That assumption was wrong.
- Where a candle does end exactly at T it agrees with the live quote: 303 of 304 at S_3, 180 of
  180 at S_4. The agreement rule was never in doubt. The script stopped on a bound I added in
  the script and did not register (`n < 300` rungs compared); it was a guess at coverage and it
  was wrong for the reason above.
- **Carrying the last candle forward reproduces the live quote.** At five instants inside the
  live window (S_3, S_4, S'_3, S'_4, Sat 10-03 20:00), every rung whose latest candle ended
  BEFORE T - 347 comparisons, up to 24 hours stale - agrees with the live quote to 1c on both
  sides. 347 of 347. A sixth instant chosen to be fast (Mon 09-28 06:00, ~2.5h after Sunday
  night football) agrees on 251 of 287, and the misses are all EXACT-hour candles (223 of 259):
  there the candle's hour-end close and the logger's last 300-second poll are different
  instants in a moving market, and the candle is the fresher of the two.

**Change, fixed here before the real run:**

1. A rung's quote at T is the **latest candle ending at or before T**, with no age cap, inside
   the archive of that period (hourly from 09-07 00:00, daily before). Before a rung's first
   candle there is no quote. The script counts how many reads were carried and how far.
2. The validation uses the same read (the split by staleness is the diagnostic above, not
   re-printed by the script) and keeps the registered
   stop rule exactly (under 90% agreement at S_3 or S_4 stops the unit). The unregistered
   `n < 300` bound is removed; the count compared is printed.
3. Nothing else changes: instants, the usable rule, the market number, every regression and
   every stop condition are as registered above.
