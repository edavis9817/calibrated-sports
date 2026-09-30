# C31 — the total, built from two teams. Pre-registration

This file is committed and pushed **before** `models/game_total.py` and
`research/game_total.py` exist, and before any total forecast is computed. The
script implements what is written here and does not extend it. Nothing in this
file is edited after the first run. A correction goes at the bottom, dated, with
its reason.

Unit c-31 (track C), 2026-09-30. Branch `c-31-team-total`, cut from
`origin/c-28-game-forecast` (`d1b259d`) as the unit directs. It is not cut from
c-30: c-31 needs c-28's `GameForecast` and nothing from c-30's key-number
margin.

## What was done before this file

- I read `models/game.py`, `research/game_forecast.py`, c-28's findings, and the
  pre-registration and findings for c-30.
- I read `research/shrinkage.py`, `schedule_strength.py` and `stickiness.py`,
  and cs-analytics `analytics/spine.py` (`build_pace`).
- I counted what exists. No forecast, price or outcome was compared with
  anything.
  - **`f_team_game_pace`** (analytics.db, track F, `situation='all'`): one row
    per (game, offence) with `plays` = scrimmage run/pass plays and `drives` =
    distinct `fixed_drive` with a scrimmage play.
    - 1999-2025: 257-285 games per season, which is every game.
    - 2026: **16 games (week 1 only)**.
  - **nflverse `games.parquet`** (raw archive,
    `raw/nflverse/2026-09-30/games.parquet`) carries `wind` and `temp`, recorded
    for outdoor and open-roof games.
    - `wind` is non-null on 176-214 games per season 1999-2021.
    - **2022: 107 of 198 outdoor games.** 2023: 158. 2024: 182. 2025: 190.
      2026: 33.
    - `nfl_games` in the store does not carry `wind`, so it is read from the raw
      file (invariant 2).
  - **The feeds store has no NFL `weather_at_kickoff` rows**, only 296 CFB
    forecast rows. Filling it would take Open-Meteo archive calls. They are free
    and keyless, but they are writes to another track's store and new requests,
    so I did not make them. **Wind here is nflverse's recorded game-day wind, not
    a forecast** (see limitations).
  - **`total_line` is on every game 1999-2025. `over_odds`/`under_odds` are
    present from 2006** (217 in 2006, 194 in 2008, full from 2010).

## Why a game total should gain more from decomposition than c-27's prop did

c-27 split a player prop into team volume × player share. The split raised
resolution by only +0.0004, because the forecastable factor (team volume) was
the small part of the variance. The noisy factor (one player's share of it) was
the large part.

**The brief's reasoning** is that a game total has no player-share factor, so
the noisy part should be smaller. It reads the product as team plays (sticky)
times points per play (less sticky), for two teams. It adds that the total is a
mean over about 125 plays and two offences, where a prop is one player on about
a quarter of one team's plays.

**My prediction, stated before measuring, disagrees with the MECHANISM and
agrees with the DIRECTION.**

- **Direction.** The model should gain far more than +0.0004 of resolution over
  the league constant. The league constant has no game-specific term (c-28's
  within-line dAUC on KXNFLTOTAL was −0.346), so almost any team-level
  information adds resolution.
- **Mechanism.** I expect the gain to come mostly from **points per play**, not
  from plays.
  - Team offensive plays per game vary across teams by only a few plays (about
    60-68).
  - Scoring per play varies by a factor of close to two between the best and
    worst offences.
  - So even if plays are stickier, efficiency carries most of the between-team
    variance in points.
- **If this is right, the brief's "the noisy factor is a much smaller part of
  the product here" is wrong.** The decomposition would help because team
  efficiency is persistent at the TEAM level, which a player's share of targets
  is not. That is a different reason from the one the brief gives.

Three registered predictions make this testable (D1-D3 below):

- **P1.** The split-half persistence of plays exceeds that of points per play.
- **P2.** Points per play carries the larger share of the between-team-season
  variance in log points per game.
- **P3.** The PPP-only ablation loses less to FULL than the plays-only ablation
  does.

If P1 holds while P2 and P3 fail, the brief's mechanism is right and mine is
wrong.

## The forecast object (`models/game_total.py`)

`GameTotalForecast` wraps c-28's `GameForecast` and **does not modify it**. The
wrapped `GameForecast` is used for the margin only. Its `mu_t`/`sigma_t` are
ignored by the new object and stay exactly as c-28 built them. It exposes
`sample(n, rng)` (joint margin and total), `cdf(x)`, `prob_total_over(L)`,
`mean()` / `total_mean()`, and `base` for every margin question.

### Team factors, as of kickoff

Every input comes from team-games that kicked off strictly before this game,
ordered by (kickoff_ts, game_id).

For team X there are four factors:

- `off_plays`: X's offensive plays per game;
- `def_plays`: opponent plays per game against X;
- `off_ppp`: X's points scored per X offensive play;
- `def_ppp`: points allowed per opponent play.

"Points" are the team's final score, so defensive and special-teams scores are
inside the offence's points. That is declared, not fixed.

**League means.** `L_plays` and `L_ppp` are taken over the 512 team-games (256
games) immediately before kickoff. That is the same window c-28 uses for its
total. `L_ppp` = Σpoints / Σplays over the window.

**A team's prior for season T.**

- Plays: `prior = L + r · (mean over X's season-(T−1) games − L)`.
- PPP: the same form, with Σpts / Σplays over season T−1.
- No season T−1 games: `prior = L`.

**Shrunk estimate over X's current-season games before this one:**

- plays: `(Σ v + k · prior) / (n + k)`
- ppp: `(Σ pts + k · L_plays · prior) / (Σ plays + k · L_plays)`

This is `research/shrinkage.py`'s form, `w = n / (n + k)`, with k fitted out of
sample rather than set by feel. It is reused by method, not imported; that
script is player-specific.

**Team expected points against opponent Y:**

- `plays_X = off_plays_X + def_plays_Y − L_plays`
- `ppp_X = off_ppp_X + def_ppp_Y − L_ppp`
- `pts_X = plays_X · ppp_X`
- `raw = pts_home + pts_away`

**(k, r) per factor pair**, fitted walk-forward for each season T on team-games
in seasons 2000..T−1:

- the plays pair (`k_p`, `r_p`) minimises the squared error of `plays_X` against
  X's realised plays;
- the PPP pair (`k_q`, `r_q`) minimises the plays-weighted squared error of
  `ppp_X` against realised points / plays;
- grid: k ∈ {2, 4, 6, 8, 12, 16}, r ∈ {0.2, 0.35, 0.5, 0.65, 0.8}.

A team-game with no pace row cannot contribute plays or PPP, so its points are
not used either. This applies to 2026 weeks 2-3; see limitations.

### Game script and wind, as a mean stack

`E|M|` is the expected absolute home margin under c-28's
`Normal(mu_m, sigma_m)`:

    sigma·sqrt(2/pi)·exp(−mu²/2sigma²) + mu·(1 − 2·Phi(−mu/sigma))

It uses c-28's `p_home` and `sigma_m` exactly as built. **The total therefore
takes c-28's margin distribution as an input.**

Wind variables:

- `wind_out` = nflverse `wind` (mph) when `roof` ∈ {outdoors, open} and wind is
  non-null, else 0;
- `dome` = roof ∈ {dome, closed};
- `wind_na` = roof ∈ {outdoors, open, ''} with null wind.

**Mean**, OLS, refit for each season T on every scored game (REG + POST) in
2000..T−1:

    total = a + b·raw + c·E|M| + d·wind_out + e·dome + f·wind_na + resid

**Joint with the margin.** On the same training games:

- regress the stack residual on `(|M_realised| − E|M|)` to get γ;
- `s_e` = the SD of what remains (ddof = number of fitted terms).

Then:

- `Total | M ~ Normal(mu + γ·(|M| − E|M|), s_e)`, with `M ~ c-28's Normal`.
- The marginal `P(T > L)` integrates M out by 40-node Gauss-Hermite. The object
  answers `prob_total_over` from exactly the distribution `sample` draws from.

**Declared, not fixed:**

- The conditional total is Normal, so it carries no total key numbers.
- Home and away points are not constrained to be non-negative.
- There is no home-field term, because it cancels in a sum.
- There is no temperature, precipitation or injury term.
- There is no opponent adjustment of the factors beyond the additive
  offence + defence − league form.

### Ablations (the decomposition, as arms)

Each arm is refit through its own stack by the same walk-forward rule:

- **PLAYS_ONLY:** `ppp_X = L_ppp` for both teams.
- **PPP_ONLY:** `plays_X = L_plays`.
- **NO_SCRIPT:** c = 0 and γ = 0.
- **NO_WIND:** d = e = f = 0, with the wind columns dropped from the stack.

## Baselines (pre-registered; every constant from seasons 2000..T−1)

1. **`league`**: c-28's total exactly, `research.game_forecast.total_params`
   imported. It is the mean and sd of the 256 games before kickoff.
2. **`season_avg`**: the two teams' season averages. Mean =
   `(PF_H + PA_H + PF_A + PA_A) / 2`, from season-to-date per-game averages
   before kickoff.
   - A team with no current-season game uses its full previous season.
   - With neither, it uses the league window mean.
   - sd = SD(total − mean) over training games. No recalibration: it is the
     naive number.
3. **`close`**: nflverse `total_line`, Normal with sd = SD(total − total_line)
   over training games. It is a sportsbook close of unrecorded provenance.

## Part 1 — against settlement, 2001-2025 (PRIMARY)

**Population.** Every scored game 2001-2025, REG + POST, with a model forecast.
1999 is burn-in and 2000 the first fit season, as in c-28. Totals do not need a
decisive result, so ties are included.

**Scoring**, imported from c-24 (`research.ranking_calibration`) and c-28
(`compare`, `mse_compare`, `boot_many`):

- 2,000 draws, seed 24, game blocks, `n_blocks` stated.
- Every difference is model − baseline.

**P-a, point.** dMSE of the mean total, against each of the three baselines.
**3 intervals.**

**P-b, ladder.** One row per (game, L) with L ∈ {33.5, 36.5, 39.5, 42.5, 45.5,
48.5, 51.5, 54.5} and y = 1[total > L]. This is CORP blocked on game: dBrier,
dMCB, dDSC, dAUC, and within-line dAUC (strata = L), against each baseline.
**15 intervals.**

**P-c, over the closing total.** One row per game with a `total_line`.

- A half-point line gives `y = 1[total > line]`.
- A whole-number line excludes pushes, and the model prices it with continuity
  correction: `P(T > L+0.5) / (P(T > L+0.5) + P(T < L−0.5))`.
- The comparator is `close`, which is 0.5 on every row by construction.
- dBrier, dMCB, dDSC, dAUC. **4 intervals.**

**THE SUCCESS CONDITION.** The model succeeds if the ladder dBrier interval
lies **below zero against both `league` and `season_avg`**.

- Covering zero against either is "no better than <baseline>".
- **Against `close` the pre-stated expectation is that the model LOSES.** The
  book's total prices weather forecasts, injuries, quarterback status and
  totals-specific information this model does not have.
- A loss to the close is not a failure of this unit. A WIN against the close
  would be treated as a probable defect and hunted before it is reported.

Cuts, reported and not verdicts: REG only; per season (the sign and whether the
interval excludes zero); 2001-2012 against 2013-2025.

## Diagnostics — the decomposition (registered, descriptive except D3 and D4)

**D1, persistence.** Across team-seasons 2001-2025 (REG only, teams with at
least 4 games in each half), the correlation between the weeks 1-8 mean and the
weeks 9+ mean. This is split-half, on disjoint games. It is computed for
`off_plays`, `def_plays`, `off_ppp`, `def_ppp` and points per game, with Fisher-z
95% intervals. **P1:** off_plays r > off_ppp r.

**D2, variance share.** Across team-seasons 2001-2025 (REG),
`log(PPG) = log(plays/g) + log(ppp)` exactly. The share is var(component) /
var(log PPG), with the covariance term reported separately. **P2:** the share
of log ppp exceeds the share of log plays.

**D3, ablation.** dMSE FULL − arm, for PLAYS_ONLY, PPP_ONLY, NO_SCRIPT and
NO_WIND. Each is registered with its interval. **P3:** FULL − PPP_ONLY is
closer to zero than FULL − PLAYS_ONLY. **4 intervals.**

**D4, wind.**

- (i) The pooled 2001-2025 OLS coefficient `d` on `wind_out`, in the full
  stack, in points per mph, with a game-block bootstrap.
- (ii) The coefficient on `wind_out` in OLS of (total − total_line) on
  (wind_out, dome, wind_na). **This asks whether the close already prices
  wind.**
- The folklore to test: wind lowers totals, and the close under-prices it.
- **2 intervals.**

**D5, resolution against c-27.** Model DSC − league DSC on the ladder (the dDSC
in P-b), set beside c-27's +0.0004. Descriptive.

## Part 2 — against the price (SECONDARY). Power first, then the result

**S1, Kalshi `KXNFLTOTAL`**, 2026 weeks 1-3. This uses c-28's close rule
exactly: the last `source='live'` two-sided quote strictly before kickoff and
at most 1,800s before it, with the mid as the price. Only half-point lines are
used.

- I print the MDE for dBrier before any difference is read.
- Model − Kalshi mid: dBrier, dMCB, dDSC, dAUC and within-line dAUC.
- Model − c-28's league total on the same rungs: dBrier.
- **6 intervals.** c-28 found 608 rungs over 32 games (weeks 2-3; week 1 has
  no live close).

**S2, the book close**, 2006-2025, games with both `over_odds` and
`under_odds`. The comparator is the multiplicative de-vig of the two American
prices; pushes are excluded as in P-c. dBrier, dMCB, dDSC, dAUC. **4
intervals.**

**S3, the registered honest-positive test.**

- Where the model's P(over the close) and the de-vigged P differ by at least
  0.03, bet 1 unit on the model's side at the actual American odds. A push
  returns the stake.
- Report ROI with a game-block interval: pooled, 2006-2015, and 2016-2025.
- It is an honest positive only if all three intervals lie above zero.
- **3 intervals.**

**Cost gate.** As in c-28, a cost analysis is required only if a Kalshi dBrier
interval lies below zero. It is not run here in any case, and it would be
filed.

**Registered interval count: 22 + 4 + 2 + 6 + 4 + 3 = 41.** There is no
multiplicity correction; the count is stated.

## Invariants the script asserts

- **market_log.db** is opened `mode=ro` through `jobs.season_model.market_log_ro`.
  analytics.db is opened `mode=ro`, and the games parquet is read from the raw
  archive.
- **c-28's moneyline and margin must not move.**
  - The Part-1 moneyline Brier must equal c-28's recorded `0.2205815236861795`
    exactly, or the script exits.
  - `models/game.py` is not edited: `git diff origin/c-28-game-forecast --
    models/game.py models/season.py` is empty at commit.
  - A committed test asserts that the wrapper leaves the wrapped object's
    `p_home`, `mu_m` and `prob_team_by_over` unchanged.
- **Every feature is as-of.** A committed test builds a synthetic league and
  asserts that changing a game's own result, or any later game's, does not move
  that game's forecast.

## Limitations, declared before the run

- **Wind is the recorded game-day wind (nflverse/PFR), not a pre-game
  forecast.**
  - Its measured effect is therefore an upper bound on what a forecast-driven
    model gets. For D4(ii), the close was set on a forecast, so an apparent
    close under-pricing of wind may be the forecast error rather than a
    mispricing.
  - 2022 carries wind on only 107 of 198 outdoor games.
- **2026 forecasts** see pace rows for week 1 only. The Kalshi week-3 forecasts
  therefore lack week-2 plays and points. This is a data gap, not look-ahead.
- **Kalshi evidence** is one venue, one season, weeks 2-3, about 32 games.
  Whatever it says is stated with its MDE and is not generalised.
