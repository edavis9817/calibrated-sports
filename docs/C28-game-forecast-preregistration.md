# C28 — the game forecast, scored. Pre-registration

Committed **before** the scoring script exists and before any game-forecast
number is computed. The script is `research/game_forecast.py`; it is written
after this file and must implement what is below. Nothing here is edited after
the first run; a correction is appended at the bottom, dated, with the reason.

Unit c-28 (track C), 2026-09-30. Branch `c-28-game-forecast`, cut from
`origin/c-27-decomposed-usage` (`0907c0b`, which carries c-24's scoring code,
`research/ranking_calibration.py`, not yet on `main`) with `origin/main`
(`a8c2e13`) merged in.

## What was done before this file

- Read `models/season.py`, `jobs/season_model.py`, c-24's
  `research/ranking_calibration.py`, `research/structural.py`'s `ladder_key`.
- Counted, from `market_log.db` (`mode=ro`), what exists — no forecast, no
  price and no outcome was compared with anything:
  - `nfl_games` (newest `data_version` per game): scores for every game
    1999-2025; `spread_line` / `total_line` on every game 1999-2025;
    `home_moneyline` from 2006 (220 of 267 in 2006, 194 in 2008, full from 2010).
  - 2026: weeks 1-3 scored, 48 games. `KXNFLGAME` lists 63 events (weeks 1-4);
    every market maps through `market_outcome` to an nflverse `game_id` and team.
    `KXNFLSPREAD` 1,698 markets, `KXNFLTOTAL` 1,258. `markets.result` is NULL on
    all of them, so settlement is from the final score (brief 022: 0 of 1,839
    Kalshi NFL settlements disagreed with the box score).
- Started `research/c28_season_identity.py` on the UNMODIFIED season model to
  record the hash the refactor must reproduce.

## The question, answered up front

**Can a margin-of-victory Elo plausibly beat a closing NFL game line? No.** It
reads final scores and nothing else: no injuries, no quarterback status, no
weather, no roster change between seasons beyond a regression to the mean. A
closing NFL side prices all of that and is among the most efficient prices in
sport. The expected finding is that the model loses to the close by a small,
detectable margin and beats the naive baselines. **A loss to the close is not a
failure of this unit.** The success condition is below and is against
settlement; beating the market is a separate, secondary question.

The TOTAL component is weaker still, by construction: Elo has no information
about scoring, so the total is a league-level as-of distribution with no
game-specific term (declared below). It is scored because the brief asks that
spread and total rungs be priced from one object; it is expected to lose to
every market total, and that result says nothing about the Elo.

## The forecast object (`models/game.py`)

Lifted out of `models/season.py`. `models.season.run_elo` and `win_prob` must
behave identically after the lift: `research/c28_season_identity.py` hashes
the full `jobs.season_model.compute()` output (division walk-forward rows,
fits, summary, tiebreak check, current forecast, a-55 projection rows, summary
and current) before and after, and the two hashes must be equal; a committed
test pins `run_elo` on a synthetic league to values captured from the
pre-lift code.

Per game, **as of kickoff** — ratings after every game that kicked off earlier
(run_elo's own ordering: season, scheduled week, kickoff, game_id), parameters
fitted on seasons 2000..T-1:

- `p_home_win` = `1 / (1 + 10^(-(d + hfa)/400))`, exactly the season model's.
- **Margin** (home minus away) ~ Normal(mu, sigma_m), with
  `mu = sigma_m * PhiInv(p_home_win)`, so P(margin > 0) = `p_home_win` and the
  moneyline and every spread rung come from ONE object. `sigma_m` is fitted per
  season T by maximum likelihood of observed margins on seasons 2000..T-1,
  using each game's own as-of `p_home_win` under season T's parameters; grid
  10.0 to 18.0 step 0.1.
- **Total** ~ Normal(mu_t, sigma_t), mu_t and sigma_t the mean and sd of the
  final totals of the 256 completed games immediately before this one (all
  types). No game-specific term.
- Methods: `prob_home_win()`, `prob_margin_over(L)` (home margin > L),
  `prob_team_by_over(team, L)`, `prob_total_over(L)`, `margin_mean()`,
  `total_mean()`, `sample(n, rng)`. Half-integer lines only are scored, so no
  push handling is needed; a whole-number line is excluded from scoring, not
  priced.

Declared limitations, not fixed: Normal margins do not carry NFL key numbers
(3, 7); margins and totals are independent in the object; ties are not
modelled (a tie is excluded from every scored population).

## Part 1 — against settlement, walk-forward (PRIMARY)

**Population.** Every game 2001-2025 with a decisive final score, regular and
postseason (the season model's rating walk includes both). 1999 is burn-in
(every rating 1500) and 2000 is the first fit season, so 2001 is the first
season that can be scored walk-forward — the brief's "1999 to 2025" is not
reachable for scoring. y = 1 if the home team won.

**Model.** MOV Elo `p_home_win`, parameters per season from `best_params`
(grid-search on game log loss, seasons 2000..T-1) — the season model's own
fit, imported, not copied.

**Baselines (all probabilities, all as-of, all with constants from seasons
2000..T-1 only):**

1. `home` — the home team always: p = the home-win rate over decisive games
   in seasons 2000..T-1.
2. `record` — the better record: season-to-date win percentage before kickoff
   (ties half). If home's is higher, p = q_T; lower, p = 1 - q_T; equal
   (including every week-1 game), p = the `home` rate. q_T = share of games in
   2000..T-1 with unequal records in which the better-record team won.
3. `elo_nomov` — the Elo favourite without margin of victory: the same rating
   walk with the multiplier fixed at 1, its own K/hfa/regress chosen on the same
   grid by the same walk-forward log-loss rule.

**Scoring, imported from c-24** (`research.ranking_calibration`: `brier`,
`auc`, `corp`, `Pop.boot`): Brier, CORP decomposition (MCB, DSC, UNC), AUC,
for the model and each baseline. Differences model − baseline bootstrapped
over **games** (one row per game, so each game is its own block), 2000 draws,
seed 24, `n_blocks` stated. Sensitivity (not the verdict): the same
differences blocked on season-week.

**THE SUCCESS CONDITION.** The model succeeds if the 95% interval of
Brier(model) − Brier(baseline) lies **below zero against all three**
baselines, pooled 2001-2025. Covering zero against any one is "no better than
<baseline>", reported as such. Cuts reported, not verdicts: REG only; per
season (sign and whether the interval excludes zero); weeks 1-4 vs later.

## Part 2 — against the closing price (SECONDARY, in this order)

Every comparison reports dBrier, dMCB, dDSC, dAUC (model − market) and the
within-line AUC difference where lines exist, game-block bootstrap, `n_blocks`
stated. **Beats the market** only where dBrier's interval lies below zero; a
model that is calibrated and ranks worse (dAUC below zero) is not useful and is
reported as such whatever dBrier says.

**2a. Kalshi `KXNFLGAME`, 2026 weeks 1-3 (the brief's first).** Close = last
`source='live'` quote with both bid and ask, `ts` strictly before kickoff and
no more than 30 minutes before it. Market p_home = mid_home / (mid_home +
mid_away) when both teams' books are two-sided; mid_home alone, or 1 − mid_away,
when only one is. No de-vig (an exchange mid is the probability). Model
constants fitted on 2000-2025; ratings as of kickoff.

**2b. Kalshi `KXNFLSPREAD` and `KXNFLTOTAL` rungs, same weeks, same close
rule, rung mid.** Spread rungs are per team: ladder_key from
`research.structural` keeps each team's rungs on its own curve and the two
teams' ladders are never pooled. Rung "TEAM wins by over L" is priced
`prob_team_by_over(team, L)`; total rung "over L" is `prob_total_over(L)`.
y from the final score. Within-line AUC strata = (series, team side for
spreads, line). A game with fewer than 5 blocks in any cell: not read.

**2c. The nflverse closing lines, 2006-2025 (added; not in the brief).** This
is where the market comparison has power, and it is registered here so it is
not chosen after seeing 2a:
- moneyline: `home_moneyline`/`away_moneyline`, American odds to implied
  probability, multiplicative de-vig (a book price carries a margin; this is
  not an exchange). Games with both moneylines and a decisive result.
- spread: mean-squared error of the actual margin against model `mu` versus
  against `spread_line` (positive = home favoured), 2001-2025, d(MSE)
  model − line with a game-block interval.
- total: the same with `total_mean()` against `total_line`.

## What is not claimed, whatever the numbers

- No bet, pick or recommendation. Hit rates for the site would be a fact about
  the forecast after settlement; nothing here says bet.
- 2026 Kalshi populations are 48 games at most: an interval that excludes
  zero on fewer than 5 blocks is not read (brief 020's rule), and 48 games of
  one venue cannot say anything about the venue's season.
- Cost analysis (fees from `/series` `fee_type`, never the PDF) runs only if a
  2a/2b dBrier interval lies below zero; otherwise it is recorded as not run.

## Multiplicity

Registered intervals: Part 1 = 3 baselines x (dBrier, dMCB, dDSC, dAUC) = 12;
Part 2 = 2a 4 + 2b 2 series x 5 + 2c 3 = 17. Total 29. No correction is
applied; the count is reported beside the results.
