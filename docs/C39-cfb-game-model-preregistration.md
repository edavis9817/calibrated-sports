# C39 — the game model, pointed at college. Pre-registration

Committed and pushed **before** `models/cfb_game.py` and
`research/cfb_game_forecast.py` exist and before any college rating, forecast
or score is computed. Nothing here is edited after the first run; a correction
is appended at the bottom, dated, with the reason.

Unit c-39 (track C), 2026-10-08. Branch `c-39-cfb-game-model`, cut from
`origin/c-37-yards-markets` (`e8ff867`).

## What was done before this file

- Read `models/game.py`, `models/season.py` (`run_elo`), `jobs/season_model.py`
  (`grid`, `game_losses`, `best_params`), `research/game_forecast.py` (c-28) and
  c-24's `research/ranking_calibration.py`.
- Counted, from `cfb.db` (`mode=ro`), what exists. **No rating, forecast, price
  or outcome was compared with anything.**
  - `cfb_games`, current rows: 49,115 games 2001-2026. Both sides FBS: 18,991
    (652-808 a season; 534 in 2020; 761 listed in 2026 of which weeks 1-5, 271
    games, are final). FBS against FCS: 2,467. 117 FBS teams in 2001, 138 in 2026.
    `start_ts` is null on none; `home_conference`/`away_conference` null on no
    FBS-FBS row. Neutral site: 868 of 889 FBS-FBS postseason games, 382 of
    18,102 regular. One FBS-FBS row carries an equal score (2024, 0-0).
    **Postseason rows carry `week = 1`**, so the NFL walk's (season, week)
    ordering would put every bowl in week 1.
  - `cfb_game_lines` (CFBD): a spread on 534-808 FBS-FBS games a season
    2013-2025 and 220 in 2026; **moneylines only from 2021** (Bovada; DraftKings
    from 2023; ESPN Bet from 2024). No row carries a capture time, and every row
    was fetched after its game (CLAUDE.md, c-15).
  - `cfb_odds_quotes` (the Odds API forward capture, timestamped): `h2h` on 261
    events, 11 books, fetched 2026-09-17 to 2026-10-07.
  - `cfb_exchange_closes` (the 2026-09-10..13 Kalshi probe): a two-sided
    moneyline close on 103 games.
- Started `research/c28_season_identity.py` on the unmodified tree to record
  the hash the NFL season model must still produce at the end of the unit.
- a-72 (the CFB export spine) is still running. This unit reads the FACTS store
  (`cfb.db`), which a-72 does not write, so it does not wait on it.

## The question, answered up front

**Will a margin-of-victory Elo beat a college price? Expected: no — it should
beat the three naive baselines against settlement and lose to the line.** It
reads final scores only: no recruiting, no transfer portal, no returning
production, no quarterback, no injuries. Public college ratings that add those
(SP+, FPI) are not known to beat the closing spread. What is NOT known in
advance is the SIZE of the loss relative to the NFL's, and that is the thing
this unit can measure. A loss to the price is not a failure of the unit; the
success condition is against settlement.

## The model (`models/cfb_game.py`)

A new module. `models/game.py`, `models/season.py`, `jobs/season_model.py` and
`research/game_forecast.py` are **not edited** — that, plus the identity hash,
is the proof c-28's NFL output does not move. The college module imports
`win_prob`, `MEAN`, `GameForecast` and `margin_sigma_mle` from `models.game`.

Per game, as of kickoff, with ratings `rh`, `ra`:

    p_home = win_prob(rh - ra + hfa * (1 - neutral_site))
    mult   = ln(min(|margin|, cap) + 1) * a / (0.001 * elo_diff_winner + a)     (MOV)
    mult   = 1                                                                   (plain Elo)
    delta  = k * mult * (result - p_home)

With `a = 2.2`, `cap = none`, `conf_w = 0`, `entry = 0` and no neutral site
this IS `models.game.elo_delta`, and a committed test asserts the college walk
then reproduces `models.season.run_elo` on a synthetic league.

Seven constants, all refitted; none is carried from the NFL:

| constant | meaning | grid (MOV model) |
|---|---|---|
| `k` | step | 20, 30, 40, 50, 60, 80, 100, 130, 160 |
| `hfa` | home advantage, Elo points; 0 on a neutral site | 0, 25, 40, 55, 70, 85, 100, 130 |
| `regress` | preseason regression toward the target | 0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.65, 0.8 |
| `a` | the multiplier's rating-gap damping (NFL 2.2) | 1.0, 2.2, 4.0, none (factor 1) |
| `cap` | the margin beyond which a blowout teaches nothing more | 14, 21, 28, 35, 45, none |
| `conf_w` | weight of the team's CONFERENCE mean in the regression target | 0, 0.5, 1 |
| `entry` | rating offset of a team first seen after the first season | 0, -100, -200, -300, -400 |

207,360 points. Plain Elo (the baseline) is fitted on its own grid — `k` 20,
30, 40, 50, 60, 80, 100, 130, 160, 200 and the same `hfa`, `regress`, `conf_w`,
`entry` — 9,600 points. (c-28's plain Elo sat on its K ceiling in 25 of 25
seasons, f-26; the ceiling here is 200.) **Every fitted constant that sits on
an open edge of its grid in any scored season is reported as such**, for both
models. An extension would be an addendum.

Fit: for season T, the grid point with the lowest mean log loss over decisive
FBS-FBS games in seasons 2002..T-1 (`jobs.season_model.best_params`'s rule).
2001 is burn-in: every team starts at 1500. Scored seasons are **2005-2025**
(at least three fit seasons). 2026 weeks 1-5 are forecast with the 2026 fit and
reported descriptively and in Part 2.

`sigma_m` per season: `models.game.margin_sigma_mle` on 2002..T-1, grid
10.0..30.0 step 0.1 (the NFL grid stops at 18). The total is a league-level
trailing distribution (256 games) carried so the object is complete; **it is
not scored** — the brief does not ask for it and c-28 showed it carries nothing.

### The four college differences — the rule for each

1. **Blowouts.** `cap` and `a` are fitted. The NFL form is the grid point
   (`a` 2.2, `cap` none), so "the NFL multiplier is right" is a reachable answer.
2. **Conference structure.** Three things, none of which fully fixes it:
   (i) the margin term moves information across conferences faster through the
   few games that connect them; (ii) the preseason target is
   `conf_w * conference_mean + (1 - conf_w) * 1500`, where the conference mean
   is the prior-season-final rating of the teams in that conference THIS season
   (membership from the season's own schedule rows, known before week 1), so a
   conference's level persists while its teams regress — fitted, and 0 is on
   the grid; (iii) it is **reported, not hidden**: Part 1 is cut by
   conference / non-conference game and by weeks 1-4 / 5+ / postseason.
   The rating is not recentred. Cross-conference level is identified only by
   non-conference and bowl games and nothing here changes that.
3. **Roster turnover.** `regress` is fitted on a grid reaching 0.8. The NFL
   fit is printed beside it.
4. **FBS against FCS. PRIMARY RULE: a game enters the walk and the scored
   population only if BOTH teams are FBS on that game's own row** (the division
   is the season's, not today's). An FBS-FCS game updates no rating and gets
   no forecast. Cost, stated: an FBS loss to an FCS team is informative and is
   ignored. **Sensitivity S1** (reported beside the primary, never substituted
   for it): every non-FBS opponent is one pooled pseudo-team `FCS` with its own
   rating, FBS-FCS games update both, scoring stays on the same FBS-FBS games.

Order: `(season, start_ts, game_id)`, never `week`. A completed game has both
scores, `completed = 1` and unequal points (college has had no ties since 1996;
an equal score is a non-game — one row).

## Part 1 — against settlement (PRIMARY)

Population: decisive FBS-FBS games 2005-2025, regular and postseason. Binary
outcome: home side won. Statistics are c-24's (`research.ranking_calibration`:
`corp`, `auc`, `Pop`), imported; the comparison loop is c-28's
(`research.game_forecast.compare`, `boot_many`), imported. Game blocks, 2,000
draws, seed 24.

Baselines, constants as-of (2002..T-1):
- **home** — the prior-seasons rate at which the home side won, separately for
  neutral and non-neutral games (the flag is known before kickoff).
- **record** — c-28's rule: the side with the better season-to-date record
  wins at the prior-seasons rate `q`; equal or no record falls back to `home`.
  A record counts every completed game the team played that season, any
  opponent — that is what a record is.
- **elo_nomov** — the same walk with the multiplier fixed at 1, own fit.

Reported for each: Brier, the CORP decomposition (MCB, DSC, UNC) of model and
baseline, AUC, and dBrier / dMCB / dDSC / dAUC with intervals and the MDE
(2.8 x bootstrap SE).

**SUCCESS = dBrier's interval lies below zero against all three.** Anything
else is reported as not all three.

Not verdicts: season-week blocks; the cuts in (2.iii); per-season counts; S1;
and **arm N — the NFL constants carried across unchanged** (c-28's 2026 fit for
k / hfa / regress, `a` 2.2, no cap, `conf_w` 0, `entry` 0, home advantage
applied on neutral sites as the NFL walk does). dBrier(college fit - arm N) is
what refitting bought. The constants table (NFL beside college: the 2026 fit
and the range over scored seasons) is printed whatever it shows.

## Part 2 — against a price, only where one is on disk

Four exist. Each is scored on its own population and labelled for what it is.

- **2a — CFBD moneyline, 2021-2025.** Per provider with both sides, American
  odds to implied, kept if the two sum to 1.00-1.15 (drops counted), normalised
  to 1; the comparator is the MEDIAN over providers. dBrier and CORP as Part 1.
  **Label: the provider's last value, read after the game, no capture time —
  not a timestamped close.** If any of these values is in-game it favours the
  price, not the model.
- **2b — CFBD spread, 2013-2025.** Line = median over providers. Sign
  convention is asserted in-run, not assumed: corr(-spread, home margin) must
  be positive or the run stops.
  (i) dMSE, model `mu` against the line. (ii) cover: `m = P(margin > -spread)`
  against `k = 0.5`, pushes dropped, dBrier. (iii) the hit rate of the side
  the model prefers, game-block interval, at |mu + spread| >= 0, 3 and 7
  points, against 0.5 and against **0.5238**, the break-even at -110.
- **2c — the Odds API forward capture, 2026.** Per book, the last `h2h` quote
  fetched strictly before `commence_ts` and no more than 6 hours before it;
  same de-vig and median. Events joined with `cfb.oddsapi_join.match`.
- **2d — the Kalshi probe, one weekend (2026-09-10..13).** The two-sided
  moneyline close; an exchange mid is the probability, no de-vig; both sides
  present are normalised. One slate; read only as one slate.

An interval over fewer than 5 games is not read. No multiplicity correction;
the count of registered intervals and how many exclude zero is printed.

**"Beats a price" has one meaning here:** 2a's or 2c's dBrier interval below
zero, or a 2b(iii) hit-rate interval wholly above 0.5238. If any does, a cost
and limits analysis is REQUIRED before it is called anything and is not in this
unit. Otherwise none is run.

## Which of the two the result supports — fixed now

- **"The competition was the problem; college is softer"** is supported only if
  the model beats a price as defined above.
- **"The method is the limit"** is supported if 2a's dBrier interval lies
  above zero AND no 2b(iii) interval lies above 0.5.
- Anything between is reported as neither, with the figures.

The college gap to the moneyline is printed beside c-28's NFL gap (+0.0092
against the nflverse moneyline close), absolute and as Brier(model) /
Brier(price). **A smaller college gap is not by itself evidence of a softer
market**: the two sports have different base uncertainty, and the college price
here is not a timestamped close. That sentence travels with the comparison.

## Invariants

`market_log.db` is opened only by `research/c28_season_identity.py` and the
NFL-constants helper, `mode=ro`. `cfb.db` is opened `mode=ro`. The script
writes only its `--json-out` and log. No request is made and no credit is
spent. Everything printed is an aggregate or an interval; no game is named as
a pick.
