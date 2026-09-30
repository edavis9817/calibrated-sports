# The two-team total, scored (c-31)

**Pre-registration:** `docs/C31-game-total-preregistration.md`, pushed at
`1870535` before `models/game_total.py` or `research/game_total.py` existed. It
has one dated addendum, written after a 20-draw smoke run and before this run.

- **Object:** `models/game_total.py` (`GameTotalForecast`, which wraps c-28's
  `GameForecast`).
- **Script:** `research/game_total.py`.

**Reproduce:**

    LOGGER_DB=<market_log.db> python -m research.game_total --json-out <scratch>/result.json   # ~17 min

**Data behind every figure below:**
- `nfl_games` data_version `2026-09-30`;
- analytics.db `f_team_game_pace`, read mode=ro (track F, `situation='all'`);
- `raw/nflverse/2026-09-30/games.parquet` for `wind` and `roof`.

Every figure moves when any of these is rebuilt.

## Scope, stated so it travels with the numbers

- **Model:**
  - team plays × points per play, for two teams, each as-of and shrunk;
  - plus c-28's expected absolute margin, which is the game-script term;
  - plus the RECORDED game-day wind.
  - It has no injury, quarterback or weather-forecast information.
- **Part 1** is settlement, 2001-2025, REG + POST, **6,758 games**. That is every
  scored game in the span; none was dropped.
- **S1** is ONE venue (Kalshi) in ONE season, **weeks 2-3 only**: 608 rungs over
  32 games.
- **S2 and S3** are the nflverse closing total and its over/under prices,
  2006-2025. It is a sportsbook close whose provenance is not recorded.

## c-28 did not move

- **The moneyline Brier matches c-28 exactly.** The Part-1 moneyline Brier
  recomputed inside the run is `0.2205815236861795`, equal to c-28's recorded
  value as a float. The script refuses to score otherwise.
- **c-28's two model files are unchanged.** `git diff origin/c-28-game-forecast
  -- models/game.py models/season.py` is empty.
- **The wrapper reads only the margin and edits nothing.** It uses `p_home` and
  `sigma_m`, and a committed test asserts the wrapped object is unchanged.

## Part 1 — against settlement (PRIMARY): beats both naive baselines, loses to the close

All figures are model − baseline over game blocks, 2,000 draws.

| baseline | dMSE of mean | ladder dBrier | dMCB | dDSC | dAUC | within-line dAUC |
|---|---|---|---|---|---|---|
| league (c-28) | **−11.18 [−13.26, −8.86]** | **−0.0075 [−0.0090, −0.0060]** | +0.0001 (contains 0) | +0.0075 | +0.021 | +0.071 |
| season averages | **−11.09 [−13.55, −8.65]** | **−0.0063 [−0.0078, −0.0048]** | −0.0004 | +0.0059 | +0.017 | +0.022 |
| closing total | **+5.84 [+3.99, +7.73]** | **+0.0044 [+0.0032, +0.0057]** | +0.0002 (contains 0) | −0.0042 | −0.012 | −0.028 |

- **Mean totals.** The model's MSE is 184.9, against 196.1 for the league
  constant, 196.0 for the season averages and **179.1 for the close**.
- **The registered verdict is SUCCESS.** Both ladder dBrier intervals against
  the naive baselines lie below zero.
- **The cuts agree:**
  - The verdict holds on REG only.
  - It holds on 2001-2012.
  - It holds on 2013-2025.
- **Per season** (league / season_avg / close):
  - the interval is below zero in 12 / 10 / 0 of 25 seasons;
  - it is above zero in 0 / 0 / **8**.
  - **No season's interval favours the model over the close.**
- **The loss to the close is all resolution.** dMCB against the close contains
  zero and dDSC is −0.0042. That is c-28's diagnosis again: the model is
  calibrated, and it orders games worse than the close.
- **P-c: over the closing total, the model is WORSE than a coin flip.**
  - dBrier is +0.0051 [+0.0032, +0.0070], almost all of it miscalibration
    (dMCB +0.0055).
  - Its dAUC of +0.012 [−0.002, +0.025] contains zero.
  - Where the model disagrees with the close, the disagreement is mostly noise,
    and a probability that acts on noise is miscalibrated around the line. That
    is c-30's mechanism on the spread, repeated on the total.

## The decomposition — the brief's mechanism was wrong; the result was large anyway

**The brief's reason was: "team volume is far more forecastable than one
player's share of it, so the noisy factor is a much smaller part of the product
here."** The data says that is backwards on both counts.

**D1, split-half persistence.** These are team-seasons 2001-2025, REG, weeks
1-8 against weeks 9+, n = 799.

| factor | r | 95% |
|---|---|---|
| off plays | +0.294 | [+0.229, +0.356] |
| def plays | +0.209 | [+0.142, +0.275] |
| **off points/play** | **+0.488** | [+0.433, +0.539] |
| def points/play | +0.325 | [+0.261, +0.385] |
| points per game | +0.530 | [+0.478, +0.578] |

**P1 (plays are stickier) FAILS.** Offensive efficiency persists better than
offensive volume. The walk-forward fit says the same: the plays shrinkage k
sits on the top of its grid (k = 16) in every season, while PPP settles at
k = 12. It wants more shrinkage than was allowed.

**D2, variance share.** Across 799 team-seasons, of var(log points per game):
- log plays carries **0.051**;
- log points per play carries **0.901**;
- 2·cov carries +0.047.

Team plays per game have a mean of 62.5 and an sd of **2.87**. **P2 HOLDS:**
between teams, the total is nearly all efficiency.

**D3, ablation** (dMSE, FULL − arm):

| arm | dMSE |
|---|---|
| PLAYS_ONLY (efficiency set to league) | **−8.14 [−9.98, −6.29]** |
| PPP_ONLY (plays set to league) | −0.73 [−1.10, −0.33] |
| NO_SCRIPT | +0.09 [−0.09, +0.26], contains 0 |
| NO_WIND | −1.71 [−2.67, −0.69] |

**P3 HOLDS.** Dropping efficiency costs 11× what dropping plays does.

- **Plays add something.** −0.73 excludes zero.
- **The game-script term adds nothing measurable to the mean.** With E|M| and
  its γ coupling removed, the MSE is unchanged. The script term is in the
  object because the brief requires the joint, and γ ≈ 0.11-0.18 couples
  blowouts to higher totals in `sample`. It does not earn its place in the mean.

**D5.** Ladder resolution gained over the league constant is dDSC **+0.0075
[+0.0060, +0.0091]**, about 19× c-27's +0.0004.

**The direction the brief predicted held.** The reason it held is not the
brief's: the total gained because team efficiency persists at the team level,
and a player's target share does not. The noisy factor is not small here. It is
90% of the variance, and it happens to be persistent.

## Wind — the folklore holds, and the close appears not to price all of it

- **D4(i), the full stack pooled 2001-2025:** **−0.244 [−0.320, −0.171]
  points per mph** of recorded wind. At 15 mph that is about −3.7 points.
- **D4(ii), OLS of (total − close) on wind:** **−0.174 [−0.245, −0.099]
  points per mph.**
- **Band means of (total − close)**, outdoor games with recorded wind:

| wind (mph) | n | total − close |
|---|---|---|
| 0-5 | 740 | +1.31 |
| 5-10 | 2,065 | +1.03 |
| 10-15 | 1,114 | −0.02 |
| 15-20 | 456 | −1.40 |
| 20+ | 165 | −0.88 |

**This is NOT evidence that the close misprices wind.**
- The wind here is the RECORDED game-day wind (nflverse/PFR).
- The close was set on a FORECAST.
- A forecast-error component correlates with recorded wind by construction, so
  some or all of D4(ii) can be forecast error rather than a mispricing. Declared
  before the run.
- Settling it needs historical kickoff forecasts, which this store does not hold
  for the NFL.

## Part 2 — against the price (SECONDARY)

**S1, Kalshi `KXNFLTOTAL`.** Power comes first: the dBrier bootstrap SE is
0.0093, so the **MDE is 0.026**. The population is 608 rungs over 32 games,
weeks 2-3 of 2026.

- **Model − Kalshi mid, dBrier: +0.0186 [+0.0012, +0.0370]. Worse.** Every
  other S1 interval contains zero.
- **Model − c-28's league total on the same rungs: −0.0051 [−0.0198, +0.0091].**
  This population cannot even see the improvement Part 1 measures over the
  league constant. The Part-1 gain of 0.0075 is well under this MDE.
- c-28 recorded +0.0237 against Kalshi; this model records +0.0186. **The two
  cannot be told apart on 32 games.**

**S2, the book close, 2006-2025** (5,216 games, pushes out, multiplicative de-vig):
- **dBrier is +0.0040 [+0.0019, +0.0063]. Worse.**
- dMCB is +0.0045. dDSC and dAUC contain zero.

**S3, the registered honest-positive test.** Flat bets at the actual prices,
where |model − de-vig| ≥ 0.03:

| span | bets | ROI |
|---|---|---|
| pooled | 3,723 | +0.2% [−2.8%, +3.4%] |
| 2006-2015 | 1,739 | +4.3% [−0.1%, +8.8%] |
| 2016-2025 | 1,984 | −3.4% [−7.8%, +1.0%] |

- **It is not an honest positive.**
- **The model leans to the over:** 59% of its bets are overs.
- The 20-draw smoke run showed both halves excluding zero, in opposite
  directions. With 2,000 draws, neither does. That is why smoke intervals were
  not read.

**Cost: not required.** No Kalshi dBrier interval lies below zero.

## Registered interval count

The count is **41, as pre-registered**, and **27 exclude zero**. No multiplicity
correction was applied.

Every interval that favours the model is against a NAIVE baseline, or is an
ablation or wind coefficient. **None favours the model over a market price.**

## What this cannot support

- **Any betting claim.** The model loses to the book close on the mean, the
  ladder, the over-at-the-close and the Kalshi rungs.
- **"The close under-prices wind."** See above: the wind is recorded, not
  forecast.
- **Anything about Kalshi's season-long pricing of totals.** The evidence is 32
  games.
- **The game-script mechanism in general.** What was measured is "E|M| from THIS
  Elo adds nothing to the mean". A better margin forecast, such as the spread
  itself, might.
- **The provenance of the nflverse total and odds.** Neither is recorded.
