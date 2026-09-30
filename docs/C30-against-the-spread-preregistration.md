# C30 — against the spread, and the key numbers. Pre-registration

Committed and pushed **before** `research/against_the_spread.py` or the margin
model exists and before any cover, push or spike figure is computed from a
forecast. Nothing here is edited after the first run; corrections are appended
at the bottom, dated, with the reason.

Unit c-30 (track C), 2026-09-30. Branch `c-30-against-the-spread`, cut from
`origin/c-28-game-forecast` (`d1b259d`) as the unit directs.

## What was looked at before this file

- c-28's code (`models/game.py`, `research/game_forecast.py`), its findings and
  its logged run (`D:/temp/c28/run.log`, `result.json`).
- Counts only, from `market_log.db` (`mode=ro`), newest `data_version` per game:
  2001-2025 has 6,758 scored games with a `spread_line`. By |line|: 1,083 on
  exactly 3, 444 on exactly 7, 2,227 on other whole numbers, 3,004 on half
  points. `home_spread_odds`/`away_spread_odds` exist from 2006 (6,688 rows over
  all data versions; commonest pairs -110/-110, -105/-105, -108/-102).
  2026 scores exist for weeks 1-3 only; week 4 has none yet.
- **No margin histogram was drawn and no forecast was compared with anything.**

## The object

c-28's `GameForecast` is untouched. The new object wraps it and changes only
the SHAPE of the margin inside each side of zero:

    KeyMarginForecast(base: GameForecast, weights w_1..w_J, tie mass t)

- Support: integers k in [-60, 60].
- Base mass: the Normal of c-28 (same mu, same sigma_m) discretised,
  b_k = Phi((k + 0.5 - mu)/sigma) - Phi((k - 0.5 - mu)/sigma).
- Shape: b_k * w_|k|, with w_j free for j = 1..24 and w_j = 1 beyond.
  **Symmetric in sign**: a key number is a key number for either team; the
  direction is already in mu.
- **Sides are pinned to c-28's moneyline**:
  P(M = 0) = t;  P(M >= 1) = p_home * (1 - t);  P(M <= -1) = (1 - p_home) * (1 - t);
  each side's shape normalised within the side. So
  P(home wins | not a tie) = `p_home_win` exactly, and `prob_home_win()` returns
  the base object's float itself.
- **Fit, walk-forward.** For scoring season T: w and t are fitted on games in
  seasons 2000..T-1 only, mu computed from each training game's as-of
  `p_home_win` under season T's Elo parameters (the same convention c-28 used
  to fit sigma_m), sigma = c-28's `sigma_m[T]`. t = training tie frequency.
  w maximises the within-side log likelihood of the decisive training margins,
  sum log[ b_m w_|m| / sum_{same side} b w ], minus a ridge penalty
  2 * sum_j (log w_j)^2 (a prior sd of 0.5 on log w, pulling toward the Normal).
  L-BFGS on log w. Penalty and J are fixed here and not tuned.
- Arms scored:
  - **E** — the key-number margin above.
  - **N0** — c-28 exactly as built: P(M > L) = `prob_margin_over(L)`, and
    P(M = L) = 0 (a continuous Normal has no push mass).
  - **N1** — E with every w_j = 1: the discretised, side-pinned Normal. N1 is
    the mechanism control. E − N0 mixes two things (push mass on whole lines,
    and key-number shape); E − N1 is the key-number shape alone.

**The moneyline must not move.** The script asserts that the Part 1 model
Brier reproduces c-28's recorded `0.2205815236861795` exactly (from c-28's run
at `nfl_games` `2026-09-30`), prints a sha256 over the repr of every Part-1
`p_home`, and asserts that E's P(M>0)/(1−P(M=0)) equals `p_home` to 1e-12 on
every game. `models/game.py` and `models/season.py` are not edited.

## The spike table (the deliverable whatever the betting answer)

For |margin| j = 0..21, and called out for **3, 7, 10, 14, 6, 4**, over every
scored game 2001-2025:

- raw empirical frequency P(|M| = j), with a game-level Wilson interval;
- mean predicted mass under N1 (the Normal) and under E, walk-forward;
- the final fitted w_j (trained on 2000-2025, i.e. the object a 2026 game uses).

## Part 1 — against settlement, 2001-2025 (PRIMARY)

**Population.** Every scored game 2001-2025 with a `spread_line`. nflverse's
sign convention is positive when home is favoured (c-28 checked it by
correlation), so the home side covers iff margin > `spread_line`. A push
(margin == line) is excluded from cover scoring and scored separately below.

**Forecast.** f = P(M > L) / (1 − P(M = L)), the probability home covers given
no push. For half-point lines the denominator is 1.

**Strata** (by |L|): **on 3**, **on 7**, **other whole numbers**, **half
points**; and pooled.

**Registered measures per stratum, per arm:** Brier, CORP (MCB, DSC, UNC),
calibration-in-the-large CITL = mean(f) − mean(y). **Differences E − N0 and
E − N1**: dBrier, dMCB, d|CITL|; game-block bootstrap (one row per game, so
every game is its own block), 2,000 draws, seed 24, `n_blocks` stated.

**THE REGISTERED QUESTION: is E better calibrated at the key numbers than
the Normal?** Answered YES if the dMCB(E − N0) interval lies below zero in the
"on 3" stratum AND in the "on 7" stratum. Either one alone is reported as
"at 3 only" / "at 7 only". Covering zero is "no better calibrated than". The
same statement is made for E − N1 as the mechanism reading, not the verdict.

**Push-rate calibration (whole-number lines only).** Predicted P(M = L)
against the realised push rate, per stratum (on 3, on 7, other whole), for E
and N1 (N0 predicts 0 by construction and is shown as such). The difference
predicted − realised with a game-block interval.

**Expected, stated up front.** The model loses to the book close by 0.009
Brier on the moneyline, all of it resolution (c-28), so on every stratum both
arms are expected to be MISCALIBRATED against the cover outcome — they will
disagree with the line, and the line is right more often. The key-number
correction cannot fix that and is not expected to; what it can change is the
part of the error that is scoring arithmetic. A dMCB that covers zero here is
a plausible, honest answer.

## Part 2 — against the price (SECONDARY)

**2a. Kalshi `KXNFLSPREAD`, 2026 weeks 1-3**, c-28's close rule and its
`kalshi_closes` imported (last `source='live'` two-sided quote, strictly
before kickoff, within 30 minutes), half-point rungs only, each team's ladder
kept separate by `research.structural.ladder_key` (a ladder spanning two teams
raises). Rung "TEAM wins by over L" priced by each arm. dBrier, dMCB, dDSC,
dAUC and within-line dAUC for E − market and N0 − market, and dBrier E − N0.

**POWER, stated before the result.** c-28 measured this population at 829
rungs over 32 games with a dBrier SE of 0.0085, **MDE 0.0237**. The book close
beats the model by 0.009 on the moneyline. Nothing of the size that matters
can be detected here, and the expected reading is "no better than", which will
say nothing.

**2b. The book cover price, 2006-2025 (where the real reading is).** On games
with both spread odds: the de-vigged (multiplicative) home-cover probability
from `home_spread_odds`/`away_spread_odds`, pushes excluded. dBrier E − book
and N0 − book on cover, per stratum, game blocks.

**2c. What an honest positive would have to look like — registered now and
not relaxed after.** Flat 1-unit bets, 2006-2025, per arm (E, N0, N1): bet
home if f > b_home, away if (1 − f) > b_away, where b is the RAW implied
probability of that side's actual American odds (break-even including vig);
if both, the larger excess. A win pays the American odds, a loss is −1, a push
0. ROI = mean return per bet. **A positive requires ALL of:**

1. ROI on bets **away from the key numbers** — |L| not in
   {2.5, 3, 3.5, 6.5, 7, 7.5} — pooled 2006-2025, game-block interval
   **above zero**;
2. the same interval above zero in **each half separately**, 2006-2015 and
   2016-2025 (present in more than one season, as a replicate rather than a
   count);
3. for E, the away-from-keys ROI is not merely inherited from N1 (reported
   side by side).

ROI on key-number lines and pooled are reported too. **No positive is
expected.** If one appears, the next step is a depth check, not a cost unit
(019's spread arbitrage died on depth), and it is filed as `needs_ethan`.

## What is not claimed, whatever the numbers

- No bet, pick or recommendation. Nothing is published.
- A key-number correction that improves calibration is a fact about the SHAPE
  of the margin; it says nothing about beating the line.
- Kalshi figures are one venue, one season, two weeks.

## Multiplicity

Part 1: 4 strata + pooled = 5 cells × 2 comparisons (E−N0, E−N1) × 3 measures
(dBrier, dMCB, d|CITL|) = 30; push-rate: 3 strata × 2 arms = 6.
Part 2a: E−market 5 + N0−market 5 + E−N0 1 = 11. 2b: 5 cells × 2 arms = 10.
2c: 3 arms × (away-from-keys pooled, half 1, half 2, key lines, pooled) = 15.
**Total 72.** No correction is applied; the count is reported with the results.
