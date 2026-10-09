# C44 — the cross-sport question again, on the game total. Pre-registration

Committed and pushed **before** `research/cross_sport_total.py` exists and before
any total forecast, loss or comparison is computed in either sport. Nothing here
is edited after the first run; a correction is appended at the bottom, dated,
with the reason.

Unit c-44 (track C), 2026-10-08. Branch `c-44-cross-sport-total`, cut from
`origin/c-38-cross-sport` (`a998e08`). Unattended run: every choice below that
would normally be handed up is taken here, with its reason.

## What was done before this file

- Read c-38's pre-registration, script and findings; c-31's findings
  (`docs/findings/game-total.md`) and `models/game_total.py`; the loaders in
  `jobs/season_model.py`, `research/game_forecast.py`,
  `research/cfb_game_forecast.py`; f-30's machine report.
- Read the column names of `cfb_games` and `cfb_game_lines` (`pragma
  table_info`). A per-season count of non-null line totals was attempted and
  failed on a wrong column name; it returned nothing and was not retried.
- **No total, forecast, loss or comparison was computed.** Every number below
  is copied from a committed finding or is a stated assumption, and is labelled
  as which.

## The question

Does a total model built the same way in both sports beat the same baselines in
the same order in both — **at equal forecast strength**?

The null: it does not; whatever ordering appears in college is a product of
college's wider spread of game environments, as c-38 found for the winner
against the home baseline.

## "Built the same way": one code path, scores only

c-31's NFL total model reads plays from track F's play-by-play table and recorded
wind. College has neither on the same footing, so c-31's model **cannot be built
the same way in both sports and is not the model under test**. The model here is
the scores-only reduction of c-31's decomposition (c-31's own ablation says
points-per-play carries the result and plays add −0.73 of −11.18), and one
function builds it for both sports from `(game id, season, start time, home,
away, home points, away points)` and nothing else. This is the same footing as
c-38's Elo: final scores and nothing else. **c-31's result is not re-derived, and
nothing here is a statement about c-31's model.**

A game enters any state below only when its start time is at least **6 hours**
before the start of the game being forecast (as-of; a later kickoff on the same
day does not see a game that may still be running).

**The league baseline `B_league`** (the league-and-season mean): the mean total
of every population game of season T−1 and of season T so far. No constant.

**The team-pair baseline `B_pair`**: for each of the two teams, the mean total
of that team's own population games in season T−1 and season T so far; the
baseline is the mean of the two. A team with no such game takes `B_league`. No
shrinkage, no constant. (Declared reading of "team-pair mean": the mean of the
pair's own game totals, **not** the mean of the pair's previous meetings, which
in college is empty for most games.)

**The model.** With `L = B_league / 2` (league points per team-game), for each
team an as-of offence (points scored) and defence (points allowed):

    prior    = L + r * (team's season T-1 mean - L)      (L if no T-1 game)
    estimate = (sum over season T so far + k * prior) / (n + k)
    raw      = (off_home + def_away - L) + (off_away + def_home - L)
    mu       = a_T + b_T * raw

- `(k, r)`: one pair for offence and defence, chosen for season T as the grid
  point minimising the MSE of `raw` on population games of seasons 2002..T−1.
  Grid, fixed now: `k` in (1, 2, 4, 6, 8, 12, 16, 24, 32), `r` in
  (0, 0.2, 0.35, 0.5, 0.65, 0.8, 1.0). 63 points. Every season whose choice is
  a first or last grid value is reported.
- `(a_T, b_T)`: OLS of the total on `raw` over the same training games.
- Declared, not fixed: no home-field term, no neutral-site term, no weather, no
  overtime adjustment (a settled total includes overtime in both sports), no
  margin coupling (c-31 measured it at +0.09, containing zero).
- The model lives in `research/cross_sport_total.py`. **Nothing under `models/`
  is added or edited**, and no winner model or c-39 constant is read.

## Span and populations — the same on both sides, fixed now

- **First season loaded: 2001. Fit from: 2002. Scored: 2005-2025.** In both
  sports. College data starts in 2001, so the NFL's 1999-2000 seasons are
  **not loaded**: the NFL walk starts where the college one does.
- **NFL**: every scored game, REG and POST, newest `data_version`
  (`jobs.season_model.load`), teams through `models.game.franchise`. Ties are
  kept (a tie has a total).
- **College**: completed FBS-against-FBS games (c-39's registered primary rule,
  `research.cfb_game_forecast.load`). A game against a non-FBS side is not in
  the population and informs no state.
- A scored game with no `B_league` yet (none can occur from 2005) stops the run.

## The environment variable — one, fixed now

**`e = B_pair − B_league`, in points**: the pair baseline's own predicted
total, centred on the league-and-season mean. Centred because the two sports'
levels differ by about ten points and a raw level would have almost no common
support; **not** scaled by each sport's spread, because scaling would make the
two distributions alike by construction and the re-weighting would do nothing —
the spread is the thing being controlled for. It is not a price and not pace
(neither exists on the same footing in both sports).

Eight bins, fixed now: `(-inf, -6)`, `[-6, -4)`, `[-4, -2)`, `[-2, 0)`,
`[0, 2)`, `[2, 4)`, `[4, 6)`, `[6, +inf)`. Per-bin n, mean `e` and every `d` are
printed for both sports. A bootstrap draw in which the re-weighted sport has an
empty bin that the other sport weights is dropped and counted.

**Primary direction: college re-weighted to the NFL's bin shares** (c-38's
direction; the NFL's bins are the thin ones at the extremes, so the reverse is
the noisier). The reverse — NFL at college's shares — is registered as a
secondary family and reported.

Declared limit: this conditions on a baseline's forecast, built from the same
scores as the model. It is descriptive, not causal, and against `B_league` a
near-null inside a bin with `e` near zero is close to mechanical.

## PRIMARY — against settlement

Loss: squared error of the predicted total, points². Game blocks (one row per
game), 2,000 draws. Per-sport intervals use seed 24. **Between-sport draws are
independent per sport** (NFL seed 4401, college 4402, draw j with draw j) —
never a shared seed (`CLAUDE.md`, *Shared denominators*).

Per sport:

- `d_league = MSE(model) − MSE(B_league)`
- `d_pair = MSE(model) − MSE(B_pair)`
- `c = d_league − d_pair` (= `MSE(B_pair) − MSE(B_league)`)
- skill `s_b = 1 − MSE(model) / MSE(b)`, the scale-free form: points² are not
  the same unit in two sports whose totals have different variance.

College at the NFL's bin shares (every statistic recomputed from re-weighted
per-bin means inside every resample): `dm_league`, `dm_pair`, `cm`.

Between sports (college − NFL), each **twice**:

- raw: `D_b` (points²) and `Ds_b` (skill), b in (league, pair);
- re-weighted: `Dm_b` and `Dms_b` — college at the NFL's shares, minus the NFL.

**One Holm family at 0.05 over these 17 tests**: 6 per-sport (`d_league`,
`d_pair`, `c` × 2), 3 re-weighted college, 4 raw between, 4 re-weighted
between. p from z = estimate / bootstrap SE, two-sided normal; a zero-variance
bootstrap is p = 1; an interval on fewer than 5 games is not read.

### The claim and SUCCESS — both parts or the answer is no

**Claim:** the model beats `B_pair`, and `B_pair` beats `B_league`, in both
sports, and in college this survives re-weighting to the NFL's distribution of
`e`.

**Part 1.** `d_league`, `d_pair` and `c` each have an interval below zero and
are Holm-significant in the NFL, in college, **and in college at the NFL's bin
shares** (9 tests). A sign that holds raw in college and fails re-weighted is
reported as **"the college ordering is a product of the wider spread of game
environments"**, naming the test. A sign that holds in one sport only is
reported as **suspected of being a fitted artefact**, naming the sport.

**Part 2.** `Dm_league`, `Dm_pair`, `Dms_league`, `Dms_pair` are each reported
with interval and MDE **whether or not they exclude zero**, beside the raw
`D_b` / `Ds_b`. Reading, fixed now, per baseline and on the **skill** scale:

- raw excludes zero and re-weighted contains zero → "the size difference is
  accounted for by the spread of environments, to within the MDE";
- re-weighted excludes zero with the raw sign → "not accounted for by the
  spread alone";
- anything else → stated as "neither registered reading", with both signs.

If Part 1 fails, **the answer is that it does not transfer**, and the report
says so in its first sentence.

### MDE — written down before the outcome run, in two steps

**Step 1, now, from stated assumptions.** For a per-game loss difference
`(y−m)² − (y−b)² = −2 (m−b) (y − (m+b)/2)`, SE(d) ≈ `2 σ δ / sqrt(n)` with σ
the residual sd of a total and δ the RMS of `m − b`. Check against a published
figure: c-31, NFL, model against league, dMSE −11.18 with SE 1.12 on 6,758
games; the formula with σ = 13.6 (c-31's MSE 184.9) and δ² = 11.18 gives 1.11.

| | assumption | SE(d_league) | SE(skill) |
|---|---|---|---|
| NFL, ~5,600 games 2005-2025 | σ 13.6, δ 3.3 (c-31's, a richer model: an upper guess for δ) | 1.20 | 0.006 |
| college, ~15,500 games | **σ 17, δ 5 — assumed, not measured** | 1.37 | 0.005 |

Between-sport MDE = 2.8 × sqrt(SE² + SE²): **about 5.1 points² on `D_league`
and about 0.022 on `Ds_league`**. `d_pair` and `c` have smaller δ and smaller
MDE; no figure is assumed for them here.

**Step 2, after the script exists and before any loss is computed:**
`--mde-only` prints, for every primary test, the MDE from the same formula
using the forecasts' own `m − b` and the training-window sd of totals — it
reads no forecast error and cannot see which forecast is better. Its output is
committed before the outcome run and is **the registered MDE of each test**. The
bootstrap's own 2.8 × SE is printed beside it afterwards.

## SECONDARY — registered, none enters the verdict

**R — the reverse re-weighting.** NFL at college's bin shares: `d_league`,
`d_pair`, `c`, and college minus that, on both scales. 7 tests, own Holm family.

**B — a common unit.** The event "the total is above `B_league`", Brier.
Model: `1 − Φ((B_league − mu) / s)` with `s` the model's training residual sd
for season T; pair: the same with `B_pair` and its own training residual sd;
league: the training share of games above their own `B_league` (a constant per
season). Per sport `dB_league`, `dB_pair`; between, raw and college-at-NFL-shares
for each. 8 tests, own Holm family. This is the arm that has c-38's shape (a
constant baseline, a Brier), so its re-weighted result against the league
constant is expected to be close to mechanical and is declared as such now.

**Cuts** (not verdicts; one Holm family): regular season only; regular weeks
1-4; regular weeks 5+; 2005-2014; 2015-2025. For each: `d_league`, `d_pair`
per sport, raw `D_b` and `Ds_b`. 5 × 8 = 40 intervals. A cut where a `d` is
below zero (Holm) in one sport only is listed by name.

## The price — separate, labelled, outside the success condition

- NFL: the nflverse `total_line`, a sportsbook close of unrecorded provenance.
- College: the CFBD `total`, median over providers, **not a timestamped close**
  — CFBD's last value per provider, read after the game, with no capture time.
- **The seasons are the same on both sides**: the seasons 2013-2025 in which
  at least 90% of the college population carries a CFBD total (a coverage count,
  no outcome), applied to both sports. If none qualifies the arm is not run.
- Per sport, `MSE(model) − MSE(price)` with its own game-block interval, and
  both MSEs. **No between-sport interval is computed and no verdict draws on
  it.**
- **f-30's check does not cover this figure, twice over.** It compared the CFBD
  *moneyline* with a timestamped pre-kickoff quote on 2026 weeks 3-5 (max
  difference 0.0657, no drift toward the result, the plant fires). It did not
  test the total, and it did not test 2021-25. c-15's 2026 week-3 comparison was
  on the spread. So for the college total in any backfill season there is **no
  evidence either way** about when the value was captured, and every mention
  carries "not a timestamped close".
- The 2026 Odds API totals (the only timestamped college totals on disk) are
  **not scored here**: there is no NFL counterpart in this frame and three
  weeks of one season is c-39's 168-game problem again. Declared as not done.

## Counts, stated before the run

Specifications: 1 primary (17 tests) + R (7) + B (8) + 5 cuts (40) + 2 price
intervals = **74 registered intervals** over 9 specifications; 4 skill levels
are printed in addition and are not tests. One model specification per sport;
63 grid points per sport per season, chosen on training seasons only.

## Must not

- No request, no credit spent. `market_log.db` and `cfb.db` opened `mode=ro`.
  The script writes only `--json-out` and `--log-out`.
- Nothing under `models/` is added or edited. `research/c28_season_identity.py`
  is not needed (no file it hashes is touched) and `git diff
  origin/c-38-cross-sport -- models/ jobs/ research/cross_sport.py` must be
  empty at the end.
- No college price is worded as a close, a closing line or a market consensus.
- Nothing is published, staged or minted. Everything printed is an aggregate or
  an interval; no game is named.
- One registered run. A smoke run on synthetic fixtures only.
