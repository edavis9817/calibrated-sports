# Does a total model built the same way beat the same baselines in both sports (c-44)

Pre-registration: `docs/C44-cross-sport-total-preregistration.md`, pushed at
`c92f98e` before `research/cross_sport_total.py` existed. The pre-run MDE of
every test was committed at `62bba89`, before any loss was computed. One
registered run; nothing was re-run or changed after it. Run log and aggregates:
`research/results/cross_sport_total.log`, `research/results/cross_sport_total.json`.

**Reproduce** (about 20 seconds; both stores are opened `mode=ro`):

    LOGGER_DB=<market_log.db> python -m research.cross_sport_total \
        --json-out research/results/cross_sport_total.json --log-out research/results/cross_sport_total.log

Data: `nfl_games` data_version `2026-10-08`; `cfb.db` as read on 2026-10-08.
Every figure can move when either is re-ingested. `nfl_games` was re-ingested
(`2026-10-09`) between the registered run and a scratch re-run later in the same session;
the two logs differ in that version string and in no figure.

## Scope, stated so it travels with the numbers

- **Model:** a scores-only total, built by ONE function for both sports. Each
  team's as-of points scored and points allowed, shrunk toward last season and
  the league, summed for the two teams, then a walk-forward linear map. It reads
  final scores and nothing else: no plays, no weather, no price, no injuries.
  **It is not c-31's NFL total model** (which reads play-by-play and wind and
  cannot be built for college); nothing here is a statement about c-31's model.
- **Baselines:** the league-and-season mean total, and the team-pair mean (the
  mean of the two teams' own game totals, last season and this season so far).
- **Span:** scored 2005-2025 in both sports, both walks loaded from 2001 and
  fitted from 2002. NFL 5,698 games (REG and POST, ties kept). College 15,508
  completed FBS-against-FBS games.
- **Outcome:** the settled total. The loss is squared error, in points².
- **"Works"** means "beats two naive baselines". It does not mean "beats a
  price": the model loses to the one price on disk in each sport (last section).

## The answer

**Yes, by the registered rule: the model beats the pair mean, and the pair mean
beats the league mean, in the NFL, in college, and in college re-weighted to
the NFL's distribution of game environments.** All 9 Part-1 tests have
intervals below zero; 17 of 17 primary tests are significant after Holm.

Squared error, model minus baseline (points²), game blocks, 2,000 draws:

| | NFL | college | college at the NFL's environment shares |
|---|---|---|---|
| model − league mean | −9.14 [−11.34, −7.05] | −41.16 [−44.45, −37.61] | −18.36 [−20.71, −16.00] |
| model − pair mean | −1.73 [−2.56, −0.88] | −9.49 [−11.03, −7.87] | −8.07 [−9.82, −6.40] |
| pair mean − league mean | −7.41 [−9.51, −5.42] | −31.67 [−34.62, −28.60] | −10.29 [−11.91, −8.60] |

MSE levels: NFL model 184.6, pair 186.3, league 193.7. College model 284.1,
pair 293.6, league 325.2.

**The thinnest leg is the NFL model against the pair mean**: 1.7 points² on
186, under 1% of the baseline's error. It is significant in the primary family
(Holm p 0.0001) and in neither of two cuts (below).

## How much of the college advantage is the wider spread of environments

The environment variable, fixed before the run: `e` = the pair mean minus the
league mean, in points. Its sd is 2.83 in the NFL and 5.55 in college. **A game
6 or more points from the league mean is 3.4% of NFL games and 27.5% of college
games** — c-38's 2.3% against 25.7% for a 0.85 favourite, again.

College minus NFL, each reported twice. Skill is `1 − MSE(model)/MSE(baseline)`,
the scale-free form; points² are not one unit across two sports whose totals
have different variance (201 against 327).

| model − | raw, points² | re-weighted, points² | raw, skill | re-weighted, skill | registered MDE, skill (raw / re-weighted) |
|---|---|---|---|---|---|
| league mean | −32.02 [−36.22, −27.82] | −9.21 [−12.43, −6.13] | +0.0794 [+0.0649, +0.0937] | **+0.0134 [+0.0008, +0.0265]** | 0.0217 / 0.0187 |
| pair mean | −7.75 [−9.56, −5.93] | −6.33 [−8.32, −4.40] | +0.0230 [+0.0162, +0.0300] | **+0.0183 [+0.0110, +0.0258]** | 0.0094 / 0.0100 |

Skill levels: against the league mean 0.047 NFL and 0.127 college; against the
pair mean 0.009 and 0.032.

- **Against the league mean, 83% of the raw skill gap disappears at equal
  environment.** The registered reading of what is left is "not accounted for
  by the spread alone", and it is the weakest result in the primary family:
  Holm p 0.044, the estimate (0.0134) is **below its own MDE** (0.0187), and the
  interval's lower end is 0.0008. Two registered secondary views do not confirm
  a remainder: on the Brier scale the re-weighted difference is −0.0007
  [−0.0033, +0.0021], and with the re-weighting reversed it is +0.0325
  [−0.0022, +0.0713] (MDE 0.057, so that view could not have seen it).
  **Read: the size of the college advantage over the league mean is mostly the
  spread of environments; whether anything is left is not settled by this run.**
- **Against the pair mean, the gap is not the spread.** Only 20% of it
  disappears, and it excludes zero in all three views (skill +0.0183, Brier
  −0.0022 [−0.0036, −0.0008], reversed +0.0199 [+0.0085, +0.0311]). At the same
  environment, shrinking and splitting a team's scoring into offence and defence
  is worth more in college than in the NFL.
- Why that would be is **an inference and is not tested here**: a college team
  plays 12 games against an uneven schedule, so an unshrunk mean of its game
  totals is a noisier, more schedule-contaminated number than an NFL team's.
  c-38 found the same shape for the win-loss record.
- This conditions on a baseline's forecast, built from the same scores as the
  model. It is descriptive, not causal.

Per bin of `e` (model − league mean, points²; NFL | college): below −6:
−34.7 | −89.2; −6 to −4: −24.8 | −30.9; −4 to −2: −8.9 | −15.3; −2 to 0:
−1.1 | −8.2; 0 to 2: −3.5 | −8.9; 2 to 4: −10.9 | −25.0; 4 to 6: −28.0 | −31.7;
6 and over: −48.8 | −112.1.

**The two open-ended bins are not matched inside.** Their mean `e` is −6.9 and
+7.0 in the NFL against −8.5 and +9.2 in college. At the NFL's weights (1.4%
and 2.1%) those two bins contribute about −2.0 of the −9.2 points² re-weighted
league gap. That arithmetic is from the printed table, done after the run, and
is not a registered test.

## Secondary arms (registered; none enters the verdict)

- **Reversed re-weighting — the NFL at college's shares.** The NFL ordering
  survives it too: −19.70 [−27.88, −11.38] against the league mean, −2.39
  [−4.38, −0.36] against the pair mean (Holm p 0.037), pair − league −17.31
  [−25.82, −8.89]. No draw was dropped for an empty bin in either direction.
- **A common unit — Brier of "the total is above the league-and-season mean".**
  NFL model 0.2417, pair 0.2430, league 0.2497; college 0.2308, 0.2345, 0.2497.
  Model − league −0.0080 NFL, −0.0189 college; model − pair −0.0013 and −0.0037.
  All four exclude zero. Declared before the run: against a constant baseline
  the re-weighted null is close to mechanical.
- **Cuts** (40 intervals, 35 significant after Holm). The model beats the
  league mean in both sports in all five. **Against the pair mean it holds in
  college only in two cuts:** regular-season weeks 1-4 (NFL −1.28
  [−2.85, +0.21]) and 2005-2014 (NFL −1.15 [−2.17, −0.12], interval below zero,
  Holm p 0.10). The NFL's registered MDE on those cuts is 2.33 and 1.59, larger
  than the full-sample effect of 1.73, so **these cuts cannot tell "absent in
  the NFL" from "present and undetected"**.

## The fit

Chosen per season on earlier seasons only, 63 grid points. NFL: `k` 16 or 24,
`r` 0.35 or 0.5 (14 of 21 seasons at 16 / 0.35). College: `k` 8 or 12, `r` 0.2
or 0.35 (17 of 21 at 8 / 0.35). **No season's choice is on a grid edge in
either sport.** College wants less shrinkage per game than the NFL.

## MDE — registered before the run, and it held

The pre-run MDE came from `SE(d) ≈ 2σ·rms(m − b)/√n`, which reads the forecasts
and the training sd of totals and no forecast error. Against the run's own
2.8 × bootstrap SE: 3.11 registered / 3.08 run (NFL, league); 4.99 / 4.88
(college, league); 5.88 / 5.89 (between, league, points²); 0.0217 / 0.0207
(between, league, skill); 0.0187 / 0.0186 (re-weighted, league, skill). The
assumption-only figures in the pre-registration (about 5.1 points², 0.022
skill) were within 15%. The two re-weighted Brier differences had no formula
MDE; their run MDEs are 0.0039 and 0.0020.

## Against a price — separate, outside the verdict

> The NFL comparator is the nflverse total_line, a sportsbook close of
> unrecorded provenance. The college comparator is the CFBD total, median over
> providers — **not a timestamped close**: CFBD's last value per provider, read
> after the game, with no capture time. f-30's check (2026 weeks 3-5) was on the
> moneyline and c-15's (2026 week 3) on the spread; neither covers a total or a
> backfill season, so nothing on disk says when a college total was captured.
> The two figures are shown side by side, carry no between-sport interval, and
> enter no verdict.

Seasons 2013-2025 in both sports (every season in which at least 90% of the
college population carries a CFBD total; coverage is 9,822 of 9,822).

| comparison | games | MSE model / price | model − price | ratio |
|---|---|---|---|---|
| NFL, nflverse total_line | 3,562 | 185.6 / 176.0 | +9.59 [+7.15, +12.08] | 1.054 |
| college, CFBD total (not a timestamped close) | 9,822 | 284.8 / 268.3 | +16.52 [+13.63, +19.48] | 1.062 |

- **The model loses to the price in both sports.** No between-sport interval
  was computed, by registration, and the two ratios are not a measurement of
  which market is harder to beat.
- If any college value was captured in-game it favours the price.
- Checked after the run, not registered: CFBD totals in the store range from
  −1 to 90 across provider rows. Over the 13,010 games with a total in
  2013-2025, no game's median is below 25, 8 are above 85, and 2,463 rest on a
  single provider.
- **Not scored:** the 2026 Odds API totals, the only timestamped college totals
  on disk. Declared before the run: no NFL counterpart in this frame, three
  weeks of one season.

## What this cannot support

- **Any statement about a market.** Nothing here beats a price.
- **"The total transfers" for a model with more in it.** One architecture was
  tested, and it is a small one.
- **A cause for the pair-mean gap.** The schedule explanation is an inference.
- **A remainder against the league mean.** See above: at its MDE, and two of
  three views contain zero.
- **Anything about 2026.** Scored seasons end at 2025.

## Counts

74 registered intervals, as pre-registered: 17 primary (one Holm family, 17
significant), 7 reversed (6 significant), 8 Brier (7), 40 cuts (35), 2 price.
9 specifications. One model specification per sport; 63 grid points per sport
per season. `git diff origin/c-38-cross-sport -- models/ jobs/
research/cross_sport.py` is empty.
