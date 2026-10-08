# C27 — does forecasting the factors separately raise resolution? Pre-registration

Committed **before** the script exists and before any decomposed-model number is
computed. The script is `research/decomposed_usage.py`; it is written after this
file and must implement what is below. Nothing here is edited after the first
run; a correction is appended at the bottom, dated, with the reason.

Unit c-27 (track C), 2026-09-30. Branch `c-27-decomposed-usage`, cut from
`origin/main` (`b851110`) with `origin/c-24-ranking-versus-calibration`
(`f1319b0`) merged in, because the scoring code this unit must import
(`research/ranking_calibration.py`) and P2's construction
(`research/venue_spread.py`) are not on `main`.

## What was done before this file

- Read c-24's findings, pre-registration and script; read `models/baseline.py`,
  `models/features.py`, `research/walkforward.py`.
- Confirmed the c-24 scratch inputs still exist: `D:/temp/c24/wf_ledger.csv`
  (25,370 rows, 16,041 with `p_bench`, every line x.5, so no push case) and
  `D:/temp/c24/rows.json` (P2 2,513 rows - **without player ids**).
- **One number was computed, and it involves no decomposed model**: the pre-run
  MDE proxy below, from the c-24 ledger (baseline model vs book close only):

      python -c "...rc.Pop(week-block rows).boot(dDSC(baseline - market), draws=500, seed=27)"
      week-block (66 weeks): dDSC -0.0037 [-0.0047, -0.0027], SE 0.00053 -> MDE 0.0015
      game-block (852 games): SE 0.00049 -> MDE 0.0014

## The claim under test

`models/baseline.py` fits one NegativeBinomial per (player, stat) on the observed
stat. The hypothesis: the stat is a product of sticky factors (team volume,
usage share) and a noisy one (catch rate), and fitting the product inherits the
noise. **Forecasting the factors separately and composing them by simulation
raises RESOLUTION (CORP DSC).** Not calibration, and not Brier.

A note recorded before any result, because it bounds what can be found: on P1
the *market's* DSC is 0.0045 (c-24 §2). Book main lines sit near each player's
median, so there is little resolution for anyone to have. A decomposed model
reaching the market's DSC would be a rise of ~0.0036 over the baseline's
0.0009; the pre-run MDE is ~0.0015.

A second note: the brief's mechanism (a noisy efficiency factor diluting the
signal) applies to **receptions only**. Rush attempts = team carries x carry
share has no efficiency factor. The rush arm is run and scored, but the
hypothesis does not predict a gain there, and per-stat results are reported.

## Step 0 — the premise check (before any model is fitted)

Training seasons **2013-2022** only (snap counts start 2013; the first test
season is 2023). REG games, newest `data_version`. A "played" player-game is a
`nfl_player_week` row OR an `nfl_snap_counts` row with offense_snaps > 0
(pfr -> gsis via `player_xwalk`); a played game with no stat row counts as 0.
Team-game totals are sums over the team's `nfl_player_week` rows.

Factors, each a per-game value within an entity-season:

- team: plays (= pass attempts + carries), pass attempts, team targets, team
  carries, rush rate (= carries / plays)
- player: target share (targets / team targets), carry share, snap share
  (offense_snaps / team offensive snaps, the team's max offense_snaps in that
  game), catch rate (receptions / targets, games with targets >= 1), yards per
  target, yards per carry (carries >= 1). Route share is **not available** in
  the store (no route column in any table) and is not measured.
- context: raw receptions, raw carries

Player population: WR/TE/RB for receiving factors, RB for carry factors;
player-seasons with >= 4 played games.

Two measures per factor:

- **r1**, lag-1 autocorrelation: Pearson correlation of f_t with f_{t+1} over
  consecutive played games within an entity-season, pooled.
- **w8**, the shrinkage-optimal weight on 8 games: method of moments,
  tau^2 = var(entity-season means) - mean(within variance / n), k = within
  variance / tau^2, w8 = 8 / (8 + k). (tau^2 <= 0 -> w8 = 0.)

Secondary: the same with the factor demeaned by position-season (removes
position mix, which inflates catch rate's apparent persistence).

**Rule, fixed now.** Receptions premise:
- **REFUTED** if r1(catch rate) >= 0.75 x r1(target share) **or**
  w8(catch rate) >= 0.75 x w8(target share). Then STOP: report the table, say
  the hypothesis is refuted before the model, fit nothing.
- **HOLDS** if both ratios are < 0.5.
- **WEAKENED** otherwise: proceed, and say so beside every result.

Rush attempts has no noisy factor; Step 0 reports carry share against team
carries descriptively and does not gate the rush arm.

## Step 1 — the decomposed model

    receptions    = team targets x target share x catch rate
    rush attempts = team carries x carry share

Constants are fitted ONCE on 2013-2022 by method of moments and used for every
test season (strictly before 2023, so walk-forward holds for every T; the
`walkforward.Constants` leak check is applied with fit seasons 2013-2022).

For an outcome (player, stat, game G, kickoff k, season T), the as-of window is
seasons T-1 and T, games with kickoff < k (the baseline's window). Games in
T-1 carry weight **0.5**, games in T weight **1.0** for team volume, opponent
and share (declared, not tuned); catch rate uses weight 1.0 throughout.

- **Team volume** (the predicted game's team, from the player's own row for G,
  exactly as `walkforward` resolves it): weighted mean of that team's per-game
  volume, shrunk toward the as-of league mean with `k_team` games. Posterior
  variance sigma_w^2 / (n_eff + k_team).
- **Opponent** (the other team in G, from `nfl_games`): weighted mean of volume
  it ALLOWED, divided by the as-of league mean, shrunk toward 1 with `k_opp`.
  Multiplies the team mean. This is where schedule adjustment lives; it is not
  applied to efficiency.
- **Share**: sum(player volume) / sum(team volume) over the player's played
  as-of games (weighted), in any team; shrunk toward a prior share for
  (position group, role bucket) with `k_share` games. Role bucket = rank of the
  player's as-of offensive snaps among as-of teammates at the same position on
  the predicted team: 1, 2, 3+; no as-of games -> bucket 2. Prior shares = the
  2013-2022 mean player-season share by (position, snap rank within
  team-position-season). Posterior variance sigma_w^2 / (n_eff + k_share).
- **Game-to-game share dispersion**: beta-binomial, rho = 1/(phi+1) by method
  of moments on 2013-2022.
- **Catch rate**: Beta posterior. Prior mean = 2013-2022 position catch rate,
  prior strength a0+b0 = c(1-c)/tau_c^2 - 1 with tau_c^2 from player-seasons
  with >= 20 targets; plus the player's as-of receptions and targets.
- **Team volume game-to-game**: negative binomial with vmr_team (within
  team-season variance / mean, 2013-2022), plus the posterior variance of the
  team mean.

Composition, per (player, stat, game), 20,000 draws, seed = CRC32 of
`gsis|stat|game_id`: V ~ NB(mu_team x opp, var); s ~ Normal(s_hat, post var)
clipped to [0.002, 0.9]; s_g ~ Beta(s phi, (1-s) phi); X ~ Binomial(V, s_g);
receptions add c ~ Beta(posterior), R ~ Binomial(X, c). p = P(stat > line).
Every row of each population gets a decomposed probability; a row with no
as-of history at any level uses the top of the hierarchy (prior share, league
volume, position catch rate). Coverage is reported and must be 100%.

The population, the settlement, the walk-forward and the scoring code are
**not changed**: y, p_bench and the baseline's p are read from c-24's inputs.

## Populations — c-24's own

- **P1**: the c-24 ledger rows with `p_bench` (16,041; 2023-2025). The c-24
  reproduction check (n and Brier(baseline) - Brier(p_bench) per season to 4 dp)
  is re-run first; a failed season is not read.
- **P2**: c-24's 2,513 Kalshi rows. `rows.json` has no player id, so the rows
  are rebuilt with `venue_spread.step4(rows_out=...)` and matched to `rows.json`
  on (game, stat, line, y, baseline p, Kalshi mid). Only matched rows are
  scored; the match count is reported, and P2 is not read unless all 2,513
  match.

## Scoring — c-24's code, imported

`research.ranking_calibration` is imported: `corp`, `auc`, `wauc`, `brier`,
`Pop`. Nothing is copied.

- **Primary (the unit's verdict):** on P1 pooled,
  dDSC = DSC(decomposed) - DSC(baseline), CORP, **week-block bootstrap**
  (blocks = season-week, 66 blocks), 2,000 draws, seed 27, percentile.
  - lo > 0: **"resolution rises"** - the claim passes.
  - contains 0: **"no rise in resolution detected"**, reported with its MDE
    (2.8 x SE); a null, not a refutation.
  - hi < 0: **"resolution falls"**.
  - Whatever Brier does: a decomposed model with dMCB < 0 and dDSC not
    excluding zero above is reported, in these words, as **"improved
    calibration and not resolution: FAILED this unit"**, and the report does
    not lead with Brier.
- **Pre-run MDE: ~0.0015** (week-block proxy above). The realized MDE is
  printed beside the primary.
- **Secondary** (week-block on P1 cuts; game-block on P2, because P2 spans 2
  weeks and a 2-block bootstrap is not read):
  - dDSC decomposed - baseline: P1 2023, 2024, 2025; P1 pooled per stat
    (receptions, rush attempts); P2. (6)
  - dDSC decomposed - market: P1 pooled, P2. (2)
  - ordering vs the market narrows: dAUC and d_wAUC of (decomposed - baseline)
    on P1 pooled and P2. (4)
  - ordering vs the market: dAUC and d_wAUC of (decomposed - market) on P1
    pooled and P2. (4)
  - dMCB decomposed - baseline on P1 pooled and P2. (2)
  - dBrier decomposed - baseline, and decomposed - market, on P1 pooled and
    P2. (4)
  - P1 pooled primary re-read with a game-block bootstrap, for comparability
    with c-24. (1)
- **Test count: 1 primary + 23 secondary = 24 intervals.** No multiplicity
  correction; ~1.2 would exclude zero under a global null.

## Rules

- `market_log.db` is opened `mode=ro` only (`LOGGER_DB` set to it, every
  connection `?mode=ro`; `venue_spread`'s store redirection reused for P2).
- The ledger, the P2 rows and the decomposed predictions stay on `D:/temp/c27`
  and are never committed. Output is aggregates and intervals only.
- Nothing publishes and nothing tunes `models/baseline.py`. The season model is
  not touched.
- Findings: `docs/findings/decomposed-usage.md`.
