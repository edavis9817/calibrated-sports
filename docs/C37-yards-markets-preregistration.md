# C37 — a yardage model against the book close. Pre-registration

Committed and pushed **before** the script exists and before any yardage forecast is
computed or scored. The script is `research/yards_markets.py`; it is written after this
file and must implement what is below. Nothing here is edited after the first run; a
correction is appended at the bottom, dated, with the reason.

Unit c-37 (track C), 2026-10-08. Branch `c-37-yards-markets`, cut from the tip of the
c-36 stack (`c-36-win-total-drift`, `2f4afa6`). `market_log.db` is opened `mode=ro`
only. No Odds API request is made.

## What was done before this file

- Read c-24's script (`research/ranking_calibration.py`), c-27's
  (`research/decomposed_usage.py`), `research/walkforward.py`, `models/baseline.py`,
  `models/features.py`, `core/distributions.py`, and the c-24 / c-27 / c-30 / c-33
  machine reports.
- **Counted what is on disk** (schema and row counts only, no price and no outcome
  was read):

      outcome_close JOIN outcomes, side = over, all with lead_min <= 15
      season  stat             rungs    p_bench present   games
      2023    receiving_yards  21,515    5,589            283
      2024    receiving_yards  23,389    6,744            285
      2025    receiving_yards  11,828    7,578            285
      rush_yards               0 rows in every season

- Timed the existing as-of fit (`models.baseline.fit_player_stat`): c-24's own
  walk-forward log shows 4,284 fits in 919 s on 20 workers.
- Read c-24's recorded standard errors from its scratch result
  (`D:/temp/c24/result.json`, count markets, model vs `p_bench`): pooled dDSC SE
  0.00051, dAUC SE 0.0055; per season dDSC SE 0.00086-0.00102.

No yardage distribution has been fitted, no yardage forecast computed, and no
receiving-yards close has been compared with an outcome.

## Two things the brief has wrong, found before the registration

1. **There are no rushing-yards closes on disk for 2023-2025.** The brief says "the
   2023 to 2025 closes are already on disk" for both markets. `outcome_close` holds
   receiving yards and nothing for rush yards; c-33's 2.81-point rush-yards
   disagreement is from its LIVE panel (2026 weeks 3-4), not from the closes. The 2026
   live prop rows exist in `markets`/`quotes` but are not mapped to `outcomes` (17
   rush-yards outcome rows in 2026), and mapping book subjects to `gsis_id` is ingest
   work that CLAUDE.md forbids in analysis code ("crosswalked at ingest, never in
   analysis code"). So:
   - **receiving yards**: shape fit AND scoring against the close, 2023-2025;
   - **rushing yards**: shape fit and distributional scoring against naive baselines
     only. **No rushing-yards comparison with any market is made in this unit**, and the
     report must not word a conclusion as covering "the two markets".
2. **The existing frame is too slow to reuse call-for-call.** A receiving-yards frame
   needs an as-of fit for every played player-game in the training seasons as well as
   the priced ones (roughly 60,000 fits); at the measured rate that is hours. The
   walk-forward frame is therefore re-implemented in memory on c-27's `Panel`
   (imported), with the count model's rules kept: only games with kickoff strictly
   before this kickoff, seasons `(T-1, T)`, constants fitted on seasons `< T` only,
   played-with-no-stat-row is a zero, no-snaps is a void.

## The model

Notation: a player-game's yards `X` is an integer and can be negative.

**Hurdle.** `pi0 = P(X <= 0)`. Negative and zero yards are one event: every line is
`>= 0.5`, so nothing priced distinguishes them, and a gamma has no support there.

**Positive part.** `X | X > 0` is a discretised Gamma with mean `mu+` and shape
`kappa`: integer `k >= 1` carries mass `G(k + 0.5) - G(k - 0.5)` (`G(0.5)` folded into
`k = 1`). So `P(X > L) = (1 - pi0)(1 - G(L))` on a half line, and on an integer line the
push-conditional `P(X > L | X != L)` is used, with pushes dropped from scoring as in
`research/walkforward.py`.

**Why a hurdle gamma, stated before any fit.** (i) Yards in a game with a touch are a
sum of a few right-skewed gains: strictly positive, right-skewed, lighter-tailed than a
product process. c-33 measured CV 0.83-0.88 including zeros; a gamma with shape between
about 1.3 and 2.5 spans that, with a closed-form CDF, so every line is a query against
one fit (invariants 3 and 4). (ii) It is the family the project already settled on for
yardage ("zero-inflated gamma for yardage", confirmed on the 2025 holdout). (iii) A
lognormal's upper tail is heavier than a sum of gains warrants and it puts its mode too
far left; a Weibull is close to a gamma at these shapes. Both are fitted as
**comparators** (below). **The primary does not switch family if a comparator fits
better** - that would be choosing the family on the test seasons.

**Centre, fitted walk-forward (no hand-set constant).** For target season `T`, on
training seasons `2016..T-1`, per position (WR, TE, RB for receiving; RB for rushing):

- as-of window for a player-game with kickoff `k` in season `S`: the player's played
  games with kickoff `< k` in seasons `S-1` and `S`, weight 0.5 on season `S-1`
  (`decomposed_usage.PREV_W`, imported); `n` = sum of weights, `ybar` = weighted mean
  yards, `z` = weighted share of games with yards `<= 0`;
- role bucket as c-27 defines it (rank by as-of weighted offensive snaps among
  same-position teammates, capped at 3; no history -> bucket 2);
- prior tables `m0[pos|bucket]`, `z0[pos|bucket]`: training-season means of yards and
  of the `<= 0` indicator per played game;
- shrinkage strengths `k_y[pos]`, `k_z[pos]` by c-27's method of moments
  (`decomposed_usage.mom`, imported) on training player-seasons;
- `mu_hat = (n*ybar + k_y*m0) / (n + k_y)`, `pi_hat = (n*z + k_z*z0) / (n + k_z)`,
  `mu+_hat = mu_hat / (1 - pi_hat)`.

**Declared limitation.** The prior tables for target `T` are fitted on `2016..T-1` and
the same tables produce the as-of features of the training rows in `T-3..T-1` that fit
the shape and recalibration parameters below. Those training rows therefore see priors
fitted on a span containing their own season. Nothing from season `T` or later enters
anything used to predict `T` (the script refuses otherwise), but training residuals are
slightly optimistic, which biases the fitted shape toward over-confidence. It is one
direction and it is stated.

**Arms. One centre, three shapes, so shape and centre can be told apart (c-30).**

| arm | centre | dispersion |
|---|---|---|
| `S0` moment ZIG | `mu_hat`, `pi_hat` | `core.distributions.ZeroInflatedGamma.from_overall_moments`, variance = the player's as-of variance shrunk to the position/bucket variance with weight `n/(n+12)` (`baseline.SHRINK_GAMES_VMR`) plus the posterior variance of the mean. The shipped family, moment-matched |
| `S1` ML shape | `mu_hat`, `pi_hat` (same as S0) | `log kappa = c0 + c1 log mu+_hat`, `(c0, c1)` by maximum likelihood on training rows `T-3..T-1` with `X > 0` |
| `Y` **primary** | recalibrated: `log mu+ = b0 + b1 log mu+_hat`, `logit pi0 = a0 + a1 logit pi_hat` | `log kappa = c0 + c1 log mu+`, all six parameters by maximum likelihood on training rows `T-3..T-1` |

`S1 - S0` is the effect of the shape with the centre held fixed. `Y - S1` is the effect
of the centre with the family held fixed. Comparators `Y-lognormal` and `Y-weibull`:
`Y`'s centre and hurdle, the positive part replaced, one dispersion line `c0 + c1 log
mu+` each, same training rows.

## Part 1 — the shape fit, reported BEFORE any scoring against a price

Population `F_T`: every played player-game of season `T` (2023, 2024, 2025) at the
stat's positions, regardless of whether it was priced; also split at as-of `n >= 4`.
For receiving yards additionally the **priced** subset (player-games with at least one
rung). Both stats. Per arm and comparator:

- randomised PIT (uniform within each integer's mass, and within the hurdle mass):
  10-bin histogram, the largest absolute bin deviation from 0.10, and the KS distance
  to uniform;
- coverage: share of outcomes at or below the model's q10, q25, q50, q75, q90, q95;
- hurdle calibration: mean `pi0` against the realized share `<= 0`;
- mean log score of the discretised pmf, and CRPS (1-yard grid, 0..500);
- CRPS and log score against two **naive distributions**: `N-pos` (the position/bucket
  empirical distribution of the training seasons) and `N-own` (the player's own as-of
  window empirical distribution, players with `n >= 4` only). Differences with a
  2,000-draw game-block bootstrap.

**Registered shape verdict**, on `F` pooled 2023-2025, per stat, for `Y`: the gamma
hurdle **fits** if the PIT KS distance is `<= 0.03` and every one of the six coverage
figures is within 0.02 of nominal; otherwise it **does not fit**, and the report says
where (which quantile, which direction). The same two numbers are printed for `S0`,
`S1` and both comparators. This verdict is about shape plus centre together on the
continuous outcome; it is not a statement about any price.

## Part 2 — receiving yards against the book close

**Rows.** `outcome_close JOIN outcomes`, `stat = 'receiving_yards'`, `side = 'over'`,
`lead_min <= 15`, seasons 2023-2025 (the same query shape as
`walkforward.load_outcomes`). Settlement in memory from the `Panel` by the shared rule
(`core/settlement.py`: a stat row settles; no row with offensive snaps `> 0` settles at
0; no row and no snaps is void; `actual == line` is a push and is dropped). **Checked,
not assumed**: a seeded sample of 2,000 rows per season is also settled through
`jobs.settle_outcomes.settle_one` + `core.settlement.settle`, and the run refuses if
any row disagrees.

**How "books move the line" is handled: distributions, not prices.** On a yards market
two books on different lines are quoting two different claims, so no price is ever
converted from one line to another here. The model emits a distribution, and each rung
`(player, game, line)` is a claim that distribution answers natively; the close at a
rung is the de-vigged median of the books quoting **that exact line**
(`outcome_close.p_bench` for DraftKings / FanDuel / BetMGM, `p_all` for every book). No
slope, no interpolation, no kappa.

That choice has a cost, stated now: a player the books disagree on has more rungs, so
the rung population over-weights disagreement, and a rung may rest on one book. So
three populations, and they are never pooled:

- **`RB`** (primary): rungs with `p_bench` present. Per season and pooled - the shape of
  c-24's P1.
- **`RA`** (secondary): rungs with `p_all` present.
- **`RM`** (one claim per player-game): the modal line - the rung with the largest
  `n_all`; ties go to the rung nearest the `n_all`-weighted mean line, then to the
  lower line - scored against `p_all` at that line. The books on other lines are
  dropped, not converted. Reported with the share of player-games whose modal rung is a
  single book, and split on it (CLAUDE.md: a lone book is not a consensus).

**Scoring.** `research.ranking_calibration` imported, never copied: `Pop`,
`discriminate`, `corp`, `auc`, `wauc`, `brier`. Game-block bootstrap, 2,000 draws,
seed 24 (c-24's). MDE = 2.8 x bootstrap SE.

**Naive baselines.** `N-half` = 0.5. `N-prior` = the player's own Laplace-smoothed
prior-season hit rate at the line, `research.score.naive_prob` imported, opportunity =
targets (the rule briefs 021 and 023 used). `S0` is reported beside them as "the
shipped family, moment-matched" - it is an arm, not a naive baseline.

### Verdict rules

**Registered success condition** (the one this unit is run for): on `RB` pooled,
`Brier(Y) - Brier(N-half)` AND `Brier(Y) - Brier(N-prior)` both have intervals below
zero, and both differences are negative on `RM`. Both -> "beats the naive baselines".
One or neither -> "does not beat the naive baselines", naming which.

**Secondary, stated separately: the close.** On `RB` pooled, CORP:

- **primary statistic** `dDSC = DSC(Y) - DSC(p_bench)`. Interval below zero -> "resolves
  worse than the close". Contains zero -> "not distinguishable from the close at MDE x"
  (never "matches"). Above zero -> "a candidate, not an edge": it would need the
  per-season signs, `RA`, `RM` and a cost model before being read as anything.
- **ordering** `dAUC = AUC(Y) - AUC(p_bench)`, same three readings, and within-line
  `d_wAUC`. c-24: calibration alone is worth nothing, so ordering is read beside DSC and
  a Brier result is never read without both.
- `dMCB` and the Brier difference are reported and are not the verdict.

**MDE, stated before the result.** From c-24's recorded SEs on the count markets, same
games and the same blocking: I expect pooled `dDSC` SE about 0.0005 -> **MDE about
0.0015**; per season about 0.003; pooled `dAUC` MDE about 0.015; pooled Brier-difference
MDE about 0.004. The run prints the realized MDE for every interval; if the realized
pooled `dDSC` MDE exceeds 0.003 (twice the expectation) the secondary question is
reported as **under-powered**, whatever the interval says. For scale: on the count
markets the close's own DSC is 0.0045, so an MDE of 0.0015 is a third of everything
there is to resolve.

**The expectation, recorded so it can be held against the result:** the model loses to
the close on DSC and on AUC; it beats `N-half` narrowly or not at all (c-24: on main
lines the close itself barely beats a constant); it beats `N-prior`.

### The shape/centre decomposition against the close (c-30's lesson)

On `RB` pooled, with intervals: `Brier`, `MCB`, `DSC`, `AUC` of `S0`, `S1`, `Y`, and the
differences `S1 - S0` (shape, centre fixed) and `Y - S1` (centre, family fixed) on
Brier, MCB and DSC. If `S1 - S0` is positive on MCB while Part 1 shows S1's shape fits
better than S0's, the report says so in those words: the better shape made the forecast
worse, and the centre is why.

## Tests declared

Part 1: per stat, CRPS differences `Y - N-pos`, `Y - N-own`, `Y - S0`, `Y - S1`, `S1 -
S0`, pooled = 5 x 2 stats = **10** intervals. Part 2: `discriminate(Y vs p_bench)` on
`RB` x (3 seasons + pooled) = 4 x 5 = 20; on `RA` pooled and `RM` pooled = 10; Brier
differences `Y - N-half`, `Y - N-prior` on `RB` x 4 and on `RM` = 10; decomposition
(`S1 - S0`, `Y - S1`) x (Brier, MCB, DSC) on `RB` pooled = 6; `Y-lognormal - Y` and
`Y-weibull - Y` Brier on `RB` pooled = 2. **58 intervals.** Nothing is corrected for
multiplicity; the count is stated so a reader can.

## What a smoke run may and may not touch

The script may be run for bugs with target season **2022** (no closes exist for it, so
only Part 1 can execute, and 2022 is not a test season) and on the synthetic fixtures
in `tests/test_yards_markets.py`. The first execution of Part 2 on real rows is the
registered run. If it crashes, the crash is fixed and reported; if a definition above
turns out to be broken, it is corrected in an addendum here **before** the corrected
figure is reported, with what had been seen.

## What this cannot show

- Nothing about rushing yards against any market (no closes on disk).
- Nothing about 2026, the open, or any price other than a close within 15 minutes of
  kickoff from US books (no Pinnacle).
- Nothing about tradeability: no cost, no limit, no execution is modelled.
- `p_bench` on a yards rung is often one book's price, not a three-book median; the
  share is reported.

Everything printed is an aggregate or an interval. Per-outcome predictions go to a
scratch directory and are never committed.
