# The game forecast, scored (c-28)

Pre-registration: `docs/C28-game-forecast-preregistration.md`, pushed at `45abdce`
before `research/game_forecast.py` existed. Forecast object: `models/game.py`.

**Reproduce.** With `LOGGER_DB` pointing at `market_log.db` (opened `mode=ro`):

    python -m research.game_forecast --json-out <scratch>/result.json          # ~12 min
    python -m research.c28_season_identity <scratch>/after.json                # ~4 min, the lift check

Data: `nfl_games` data_version `2026-09-30`, `nfl_teams` `2026-09-16`. Every
figure below will move when either is re-ingested, and the Kalshi rows will grow
as weeks 4+ settle.

## Scope, stated so it travels with the numbers

- **Model:** the season model's margin-of-victory Elo (a-41), parameters refit
  per season on 2000..T-1. It reads final scores and nothing else.
- **Part 1** is settlement, 2001-2025, 6,743 decisive games, REG + POST. 1999 is
  burn-in and 2000 the first fit season, so the brief's "1999 to 2025" cannot be
  scored walk-forward.
- **Part 2a/2b** is ONE venue (Kalshi), ONE season, weeks **2 and 3 only** — 32
  games. Week 1 has no `source='live'` quote on any game-line series in the 24h
  before any kickoff (candle backfill only), so the registered close rule finds
  nothing there.
- **Part 2c** is the nflverse closing lines, a sportsbook close of unrecorded
  provenance, 2006-2025 for moneylines and 2001-2025 for spread and total.

## The lift

`models/game.py` now holds the constants, `win_prob`, and the per-game rating
update. `models.season` re-exports them and `run_elo` calls them. It gains an
optional `mov=` argument whose default is the old behaviour.

- **Full data.** `research/c28_season_identity.py` hashes everything
  `jobs.season_model.compute()` returns that is deterministic. That covers 3,304
  division rows, 13,216 projection rows, the fits, the summaries, the tiebreak
  check and the current forecasts.
  - Before the lift: `2a2ca2dc…0b16`. After: `2a2ca2dc…0b16`.
  - The two files are byte-identical (`cmp`).
- **Committed.** `tests/test_game_forecast.py` pins `run_elo` on a synthetic
  league to a digest captured from the pre-lift file. A second test shows that
  `mov=False` moves the digest, so the pin can see a change.

## Part 1 — against settlement (PRIMARY): the model beats all three baselines

These are game blocks: one row per game, `n_blocks` 6,743, 2,000 draws.

| model − | dBrier | dDSC | dAUC | dMCB |
|---|---|---|---|---|
| home (as-of home rate) | **−0.0255 [−0.0288, −0.0221]** | +0.0268 | +0.187 | +0.0013 † |
| better record | **−0.0162 [−0.0189, −0.0135]** | +0.0141 | +0.070 | −0.0021 [−0.0031, +0.0001] |
| Elo without MOV | **−0.0027 [−0.0035, −0.0018]** | +0.0025 [+0.0014, +0.0036] | +0.010 [+0.006, +0.014] | −0.0002 [−0.0008, +0.0005] |

The model's own figures are Brier 0.2206, which is MCB 0.0014 − DSC 0.0268 + UNC 0.2460, and AUC 0.687.

- **Registered verdict: SUCCESS.** All three dBrier intervals lie below zero.
- **Every gain is resolution, not calibration.** dMCB contains zero against the
  record and plain-Elo baselines.
- **The margin-of-victory term earns 0.0027 of Brier.** Against plain Elo it
  improves ordering (dAUC +0.010) and changes nothing in calibration.
- † **Distrust the dMCB interval against `home`.** Its percentile interval
  [+0.0016, +0.0026] **excludes its own estimate**. The in-sample PAV step is
  biased under resampling when one side is constant within a season. The
  difference is small either way, and it is not read.
- **The verdict survives every cut except one**, with one weak spot and one outlying season:
  - Season-week blocks (530 blocks): all three are still below zero.
  - REG only: all three are below zero.
  - REG weeks 5+: all three are below zero.
  - **REG weeks 1-4 against plain Elo is the exception:** −0.0008 [−0.0024, +0.0007], which
    contains zero. Early in a season, margin of victory has too few games to add anything.
  - Per season, the interval lies below zero in 22 of 25 seasons against home, 18 of 25 against
    record, and 7 of 25 against plain Elo. No season's interval lies above zero.
  - **2025 is the weakest season against record:** −0.0043, and its interval contains zero.

## Part 2 — against the close (SECONDARY): no better than Kalshi; worse than the book close

**2a, KXNFLGAME.** Weeks 2-3 give 32 games. The close is a median 166s before
kickoff, and the moneyline mid sums lie in 0.99–1.01.

- dBrier is −0.0108 [−0.0444, +0.0215]. dAUC is +0.036 [−0.091, +0.163].
- All four intervals contain zero.
- **The MDE for dBrier is 0.047.** That is five times the model's entire gap
  to the book close in 2c. This population can detect nothing of the size that
  matters.

**2b, the ladders.** Each team's spread rungs form their own curve (`ladder_key`
guard), and the two sides are never pooled.

- **KXNFLSPREAD:** 829 rungs over 32 games.
  - dBrier is +0.0028 [−0.0138, +0.0195]. dAUC is −0.015 [−0.081, +0.053].
    Within-line dAUC is −0.027 [−0.135, +0.076].
  - All of them contain zero.
- **KXNFLTOTAL:** 608 rungs over 32 games.
  - dBrier is **+0.0237 [+0.0017, +0.0470], so worse**. dMCB is +0.0099, also worse.
  - Within-line dAUC is **−0.346 [−0.553, −0.114]**. This is the expected
    result: the total has no game-specific term, so within a line it cannot
    order games at all.

**2c, the nflverse closing lines.** This is where there is power.

- **Moneyline, 2006-2025, 5,281 games:**
  - Brier: model 0.2203 against the close 0.2111. dBrier is **+0.0092 [+0.0067, +0.0114]**.
  - **dMCB is −0.0003 [−0.0011, +0.0007]. The model is as well calibrated as
    the close.**
  - dDSC is −0.0094 [−0.0119, −0.0068]. dAUC is −0.031 [−0.038, −0.022].
  - **The whole of the loss is resolution.** The model is calibrated and orders
    worse, which is c-24's diagnosis of the prop model turned the other way
    round: that model was miscalibrated.
- **Margin:** MSE against model mu is 183.1 and against `spread_line` 174.3.
  dMSE is +8.86 [+6.73, +10.96]. MAE is 10.56 against 10.26.
  corr(mu, spread_line) is 0.854.
- **Total:** dMSE is +17.03 [+14.19, +19.96], as expected for a league-average
  total.

**Cost: not run.** No Kalshi dBrier interval lies below zero, which is the
registered condition.

**The pre-stated answer held.** A margin-of-victory Elo does not beat a closing
NFL line. It is calibrated, it clearly beats every naive baseline, and it loses
to the book close by 0.009 of Brier, all of it resolution.

## Registered interval count

The count is 32, and 18 of them exclude zero. No multiplicity correction was
applied. The pre-registration's text said 29, which was an arithmetic slip; see
its addendum.

## What this cannot support

- **Anything about Kalshi's season-long pricing.** The Kalshi evidence is 32
  games from two weeks.
- **A claim that the model "matches Kalshi".** "No better than" is not
  equality. The 2a MDE is 0.047.
- **Totals from this object.** The total is a placeholder distribution. It is
  priced only because the brief asks for one object.
- **The provenance of the nflverse close.** It is the line nflverse publishes,
  and no book or timestamp is recorded for it. Calling it "the close" is
  nflverse's labelling, not a measurement made here.
- **The Normal margin.** It carries no key numbers (3 and 7). Spread rungs next
  to a key number are mispriced by construction.

## Record of the baseline's grid (added by c-50, 2026-10-09; nothing above is re-worded)

The plain-Elo baseline (`elo_nomov`) was fitted on the season model's grid,
`jobs.season_model.GRID`: K in {10, 15, 20, 25, 30, 40}, hfa in {0, 25, 50, 75},
regress in {0, 0.25, 1/3, 0.5, 0.6, 0.75}. **Its fitted K is 40, the largest
value offered, in 25 of 25 scored seasons** (f-26 measured it; c-50 re-measured
it). hfa and regress are at no endpoint. With K offered to 120 the fitted K is
45 or 50 in 24 seasons; the headline against plain Elo is unchanged and the REG
weeks 1-4 cut reads differently. Figures, seeds and what they do not support:
`docs/findings/c50-baseline-grid-edge.md`.
