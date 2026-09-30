# C32 — does the model's disagreement with the opening number predict where the line closes. Pre-registration

This file is committed and pushed **before** `research/open_line_move.py` exists and before
any model number is compared with any price. The script implements what is written here and
does not extend it. Nothing in this file is edited after the first run. A correction goes at the
bottom, dated, with its reason.

Unit c-32 (track C), 2026-09-30. Branch `c-32-open-line-move`, cut from
`origin/c-31-team-total` (`5e75e60`), the previous unit's tip. It is the c-28 → c-31 stack.

## The question

Not "does the model beat the close" (c-28/c-30/c-31: it does not). Instead: **when the model
disagrees with the earliest price on disk, does the line then travel toward the model between
that price and the close?** Per game and per market:

    model_minus_open = model's implied number - market's implied number at the open
    close_minus_open = market's implied number at the close - at the open

The headline for each market is the OLS slope of `close_minus_open` on `model_minus_open`
(through an intercept) and the Pearson r, each with a 95% percentile interval from a
**game-block bootstrap** (one row per game per market, so resampling rows is resampling games;
2,000 draws, seed 24, c-24's `BOOT`/`SEED`). **A slope, not a win rate.**

## What was done before this file

I counted what exists and looked at lead times. I compared no model number with any price.

- **Kalshi is the only 2026 game-line venue with a quote history on disk.** The Odds API
  rows in `market_log.db` for 2026 are `game` event markers and player props only; there is
  no h2h/spread/total history. Polymarket was not examined and is not used.
- **Kalshi census**, mapped `KXNFLGAME`/`KXNFLSPREAD`/`KXNFLTOTAL` markets on 2026 REG games,
  earliest two-sided quote before kickoff and last two-sided quote within 30 min of it:

      week  earliest quote on disk           close (<=30 min pre-kick)   scored
      1     Kalshi CANDLE backfill, 900-2250h   2 of 32 ML, ~6% of rungs  yes
      2     live, 108.5h median lead            all                      yes
      3     live, 119.8h median lead            all                      yes
      4     live, 119.7h median lead            none (not played yet)     no

- **Week 1 has no close** (the live quotes are gone: `QUOTES_RETENTION_DAYS` = 14 on
  `ingest_ts`, earliest live KXNFL game-line quote on disk 09-16 04:31 UTC). **Week 4 has no
  close yet** (kicks off 10-01). **So the population is weeks 2 and 3: 32 games.** The brief's
  "about 60 games, weeks 1-4" is not what is on disk.
- **The open is not the opening number, for any game.**
  - Week 3's ~119.8h lead is the logger's discovery horizon (`KALSHI_CLOSE_HORIZON_DAYS` = 7,
    counted from the market's close_ts, which lands after the game), not Kalshi's listing time
    — week 1's markets have candles from June.
  - Week 2's ~108.5h is the retention edge, and **it moves forward in real time** as the
    logger prunes. The script records the run time and the realised lead of every open.
  - Both are "a price about four to five days out", and they are reported as that.
- 2026 pace rows (track F, `f_team_game_pace`) exist for week 1 only, so the total model's
  2026 team factors see week 1 and nothing after it. That is the model as it stands, not a
  choice made here.

## Definitions

**Information cut.** For each game, `cut = max(previous kickoff of home, of away) + 5h`, over
2026 games (the previous game has finished). No price earlier than the cut is used as the open,
so the model's inputs at the open are the inputs it has at kickoff for everything that is
team-specific. The script **asserts** that no game involving either team kicks off between the
cut and the game.

**Open**, per (game, series): `t0` = the earliest two-sided (`best_bid` and `best_ask` non-null)
`source='live'` quote at or after the cut on any of the game's markets in that series. Each rung's
open is its first two-sided quote in `[t0, t0 + 3600)`. **Minimum lead 24h**: a game whose `t0`
is less than 24h before kickoff is excluded from that series and counted. The lead
distribution is reported (p10/p50/p90, min, max).

**Close**, per rung: the last two-sided `source='live'` quote in `[kickoff - 1800, kickoff)` —
c-28's rule (`research.game_forecast.kalshi_closes`), with the quote read per rung.

**Eligible rung** (spread and total, at open and at close separately): half-point line,
`ask - bid <= 0.10`, `0.10 <= mid <= 0.90`.

**The market's implied number.**
- Moneyline: `p_home = h/(h+a)` from the two team markets' mids; one side only → that side
  (complemented for away). c-28's rule.
- Spread: home margin. A home rung at L is `P(M > L) = mid`; an away rung at L is
  `P(M > -L) = 1 - mid`. Each eligible rung gives `mu_j = x_j + sigma * PhiInv(P(M > x_j))`,
  and the implied mean is the `p(1-p)`-weighted mean of the `mu_j`. `sigma` = c-28's
  `sigma_m` for 2026. Same for totals with `sigma` = c-31's NO_WIND `s_e` for 2026.
  Fewer than 2 eligible rungs → no number.
- **Sensitivity (not a verdict):** the model-free median — linear interpolation of the
  eligible rungs' `P(M > x)` at 0.5, where the rungs bracket 0.5.

**The model's implied number, as of the open.**
- Moneyline: c-28's `p_home`. Spread: c-28's `mu_m` (home margin mean). Both from the
  season model's Elo walk, which reads only the two teams' results, so with the cut above they
  are identical as of the open and as of kickoff.
- **Total: c-31's NO_WIND arm, PRIMARY.** c-31's FULL model uses RECORDED game-day wind,
  which is not known four days out; using it at the open would be look-ahead. NO_WIND is
  c-31's own registered arm, walk-forward fitted on seasons before 2026. The factor states
  are rebuilt AS OF THE CUT (only team-games that kicked off at least 5h before it), so the
  league-mean window cannot see same-week games either. FULL with recorded wind is run as a
  flagged sensitivity and is never the verdict.
- The stacks, factor parameters, Elo parameters and sigma are c-28/c-31's walk-forward fits
  for season 2026 (fitted on 2000-2025), imported, not refitted.
- The script recomputes c-28's moneyline Part-1 Brier and **exits if it is not
  0.2205815236861795**, c-31's guard. Nothing of c-28, c-30 or c-31 is edited.

## Power, stated before the result

32 games per market at most. At 80% power and two-sided 5%, the smallest correlation
detectable is **r ≈ 0.48** (Fisher z: 2.80 / sqrt(29)). The slope MDE is reported as
2.8 × its bootstrap SE and **printed before the estimate**. My prior guess of its size, from an
assumed move sd of ~1-1.5 points against a model-open disagreement sd of ~3 points, is
**≈ 0.2 on the spread**. A null here is a null with no power.

The three markets are not three independent tests. The moneyline and the spread are one model
quantity (`p_home` and `mu_m` are monotone in each other) against two views of one market.
Three co-primary slopes, no multiplicity correction, reported as such.

## Measures

For each market (moneyline, spread, total):

1. **Primary:** slope and r of `close_minus_open` on `model_minus_open`, game bootstrap.
   A slope near 1 = the model sees what the market later sees; near 0 = its disagreement is
   noise.
2. Descriptive: n, lead distribution, sd of each quantity, mean |close_minus_open|.
3. **Selection control.** Games where the model disagrees most may be the most uncertain and
   move most for unrelated reasons. Terciles of `|model_minus_open|`; in each band: n, mean
   **unsigned** move `|close_minus_open|`, mean **signed** move toward the model
   `sign(model_minus_open) × close_minus_open`, and their ratio (the share of the band's
   movement that went the model's way). A band that moves more in both directions shows up as
   a larger unsigned move with an unchanged ratio. Spearman rank correlation as a robustness
   read on the whole-market relationship.
4. **The honest version: did the move beat the cost of taking the side at the open.** Per
   game, the pivot rung is the eligible open rung with mid closest to 0.5 (moneyline: the home
   market). Side = YES if the model's probability for that rung exceeds its open mid, else NO.
   - Signed move = `(close mid - open mid)` on that rung, times +1 for YES / -1 for NO.
   - Cost = the half-spread paid at the open (`ask - mid` for YES, `mid - bid` for NO) plus
     the Kalshi taker fee per contract at 100 contracts (`core.fees.fee_per_contract`, the
     series' own multiplier).
   - Report the share of games with signed move > cost (Wilson interval, games are the
     unit), and the mean of `signed move - cost` (game bootstrap).
   - A pivot rung with no close quote → the game drops out of this measure, counted.

## Verdict wording, per market

- Slope interval above zero: "between a ~4-5 day open and the close, the Kalshi line moved
  toward the model", with the slope.
- Contains zero: "no detectable relationship; this sample could detect only r ≥ ~0.48".
- Below zero: "the line moved away from the model".

**Whatever the sign: a line moving toward a position is closing-line value, not profit.** The
position still has to settle, and c-28/c-30/c-31 found the model loses to the close at
settlement. A positive slope here would say the model anticipates the market's movement; it
would not say that taking the model's side at the open pays.

## What this cannot say

- Nothing about 2023-2025: no open on disk for those seasons (only `outcome_close`).
- Nothing about the true opening number: the open here is 4-5 days out, set by the logger's
  horizon (week 3) or by retention (week 2).
- Nothing about sportsbooks: Kalshi is the only venue with a history.
- Nothing about wind forecasts: the total runs without wind.

## Data safety

`market_log.db` and `analytics.db` open `mode=ro`. The script writes only `--json-out`.
