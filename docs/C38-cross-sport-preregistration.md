# C38 — does anything that works in one sport appear in the other. Pre-registration

Committed and pushed **before** `research/cross_sport.py` exists and before any
cross-sport statistic is computed. Nothing here is edited after the first run; a
correction is appended at the bottom, dated, with the reason.

Unit c-38 (track C), 2026-10-08. Branch `c-38-cross-sport`, cut from
`origin/c-39-cfb-game-model` (`a981315`).

## What was done before this file

- Read `models/game.py`, `models/cfb_game.py`, `models/season.py` (`run_elo`),
  `research/game_forecast.py` (c-28), `research/cfb_game_forecast.py` (c-39),
  `research/ranking_calibration.py` (c-24), c-28's findings and c-39's
  committed run log and result JSON.
- Started `research/c28_season_identity.py` on the unmodified tree (the hash
  the NFL season model must still produce at the end of the unit). Its result
  is not known at the time of writing.
- **No rating, forecast, score or comparison was computed.** Every number
  below is copied from c-28's and c-39's committed output.

## What is already published, and what it lets me say in advance

dBrier, model minus baseline, game blocks (c-28 `docs/findings/game-forecast.md`;
c-39 `research/results/cfb_game_forecast.log`):

| baseline | NFL 2001-2025, 6,743 games | college 2005-2025, 15,508 games | college − NFL |
|---|---|---|---|
| home | −0.0255 [−0.0288, −0.0221] | −0.0615 [−0.0644, −0.0583] | −0.0360 |
| better record | −0.0162 [−0.0189, −0.0135] | −0.0380 [−0.0406, −0.0352] | −0.0218 |
| plain Elo | −0.0027 [−0.0035, −0.0018] | −0.0042 [−0.0049, −0.0034] | −0.0015 |

So the SIGN in each sport is already known, and this unit cannot "discover"
it. What is not known, and is what this unit measures: the interval on the
between-sport difference; whether the ordering holds as a tested contrast and
not only as three point estimates; whether the size difference survives putting
the two sports on a common scale; and whether an advantage survives when the
constants are carried across with no fitting in the receiving sport.

**The MDE for the between-sport difference, stated before the run.** The two
populations share no game, so SE(diff) = sqrt(SE_nfl² + SE_cfb²). SEs are the
published ones (c-39's printed; c-28's from interval width / 3.92). MDE = 2.8 × SE.

| baseline | SE NFL | SE college | SE diff | **MDE of the difference** | published difference |
|---|---|---|---|---|---|
| home | 0.0017 | 0.0016 | 0.0023 | **0.0065** | −0.0360 |
| better record | 0.0014 | 0.0014 | 0.0020 | **0.0055** | −0.0218 |
| plain Elo | 0.0004 | 0.0004 | 0.0006 | **0.0016** | −0.0015 |

**Expected, in advance:** the home and record differences are 4-5× their MDE
and will exclude zero; the plain-Elo difference is at its MDE and may not. A
raw difference that excludes zero is NOT a failure of the claim — see *The
reading*.

On the skill scale (below), by the delta method from the same figures: MDE
about 0.027 (home), 0.024 (record), 0.008 (plain Elo), against published
differences of about 0.149, 0.104 and 0.010.

## Populations — each sport's own registered one, reproduced or the run stops

- **NFL**: c-28's Part 1 exactly — decisive games 2001-2025, REG and POST, the
  season model's walk and per-season fit (`research.game_forecast.Walk`,
  `jobs.season_model.grid` / `game_losses` / `best_params`), c-28's three
  baselines, all imported. **Nothing about the NFL model is refitted or
  changed.**
- **College**: c-39's Part 1 exactly — decisive FBS-FBS games 2005-2025, the
  registered primary rule (FBS-FBS only; **not** the pooled-FCS sensitivity S1),
  `research.cfb_game_forecast.Walk` over `GRID_MOV` / `GRID_PLAIN`, c-39's three
  baselines, all imported.
- **Reproduction gate.** Before any cross-sport figure is printed the script
  asserts, to 4 dp, NFL dBrier −0.0255 / −0.0162 / −0.0027 and college
  −0.0615 / −0.0380 / −0.0042. If one fails the script **stops** and that is
  the report (the NFL store was re-ingested since c-28: `nfl_games` 2026-09-30
  then, 2026-10-08 at c-39).

The two populations differ in window (2001 vs 2005 start) because each is the
one its own unit registered. The common window is a registered cut, not the
primary.

## PRIMARY — against settlement

Game blocks (one row per game in both sports), 2,000 draws.

Per sport and baseline b in (home, record, elo_nomov):

- `d_b = Brier(model) − Brier(b)`. Seed 24 with c-24's draw sequence, so each
  interval is the one c-28 / c-39 published.
- **Ordering contrasts**, bootstrapped as one quantity on the same resamples:
  `c1 = d_home − d_record` and `c2 = d_record − d_elo_nomov`. The claimed
  ordering is `d_home < d_record < d_elo_nomov < 0`, i.e. c1 < 0 and c2 < 0.
- Skill: `s_b = 1 − Brier(model) / Brier(b)`. Printed with Brier, UNC, and the
  CORP DSC of the model and each baseline (point values).

Between sports, for each b:

- `D_b = d_b(college) − d_b(NFL)` and `Ds_b = s_b(college) − s_b(NFL)`.
- **Drawn independently per sport**: NFL seed 3801, college seed 3802, draw j
  paired with draw j. Seed 24 in both would feed one stream of uniforms to both
  sports' game orders — common random numbers between the two subjects being
  compared, which CLAUDE.md (*Shared denominators*) forbids for exactly this
  use. Percentile interval; SE = sd of the paired differences.

### The claim and SUCCESS

**Claim:** the model's advantage over the three baselines, against settlement,
has the same sign and the same ordering in both sports.

**SUCCESS** = in BOTH sports, all three `d_b` intervals lie below zero AND both
ordering contrasts' intervals lie below zero, each also significant after the
correction below. `D_b` and `Ds_b` are reported with their intervals **whether
or not they exclude zero**, beside the MDEs above.

**The honest failure.** If a `d_b` or a contrast holds in one sport and not the
other, the report says which sport, which baseline, and that **the one-sport
version is now suspected of being a fitted artefact**. That is the result the
roadmap asks for.

### Correction

One Holm family at 0.05 over the 16 primary tests: 6 `d_b`, 4 contrasts, 3
`D_b`, 3 `Ds_b`. p from z = estimate / bootstrap SE, two-sided normal (a
percentile-bootstrap p floors at 1/draws — brief 022's grading lesson); a
zero-variance bootstrap is p = 1. Cuts, the matched comparison and the transfer
arms are separate Holm families, named below. The count of every registered
interval and of every specification is printed.

## The reading — fixed now

Same sign and ordering says the advantage **appears** in both sports. It does
not say it is the same **size**, and a size difference is not by itself a
fitted artefact: college has more lopsided games, so there is more for any
rating to resolve. Three registered things separate those:

**M — matched on forecast strength.** Bin each game by the model's favourite
probability `f = max(p, 1 − p)`: [0.50, 0.55), [0.55, 0.60), ... [0.80, 0.85),
[0.85, 1.00] — eight bins. College `d_b` is recomputed with the NFL's bin
shares as weights (shares and bin means recomputed inside every resample;
independent seeds as above; a draw with an empty college bin is dropped and
counted). `Dm_b = d_b(college, NFL-weighted) − d_b(NFL)`, three tests, own Holm
family. Per-bin n, mean f and `d_b` are printed for both sports so the residual
mismatch inside the top bin is visible.
- `D_b` excludes zero and `Dm_b` contains zero → **"the size difference is
  accounted for by the wider spread of forecast strength in college, to within
  the MDE."**
- `Dm_b` excludes zero with `D_b`'s sign → **"not accounted for by the spread
  alone."**
- Declared: this conditions on the model's own forecast. Against a constant
  baseline two calibrated models at the same `f` must score alike, so for
  `home` a matched null is close to mechanical and is evidence of calibration,
  not of anything deeper. It is informative for `elo_nomov`. It is descriptive,
  not causal: it cannot separate talent spread from schedule structure.

**T — transfer, the direct test of "fitted".** An advantage that needs
refitting in each sport could be the fit. So, with nothing fitted in the
receiving sport:
- **Arm N (NFL → college).** c-28's 2026 NFL fit (k 20, hfa 50, regress 0.5,
  a 2.2, no cap, conf_w 0, entry 0, home advantage applied on neutral sites as
  the NFL walk does — c-39's arm N, unchanged) walked over the college games.
  Scored against college home, record, college-fitted plain Elo, and **arm N0**:
  the NFL's plain-Elo 2026 fit (k 40, hfa 50, regress 1/3) carried across the
  same way. N against N0 is the margin-of-victory term's gain with neither side
  fitted to college.
- **Arm R (college → NFL).** c-39's 2026 college fit (k 40, hfa 55, regress 0.4,
  a none, cap none, conf_w 1, entry −300) walked over the NFL games with
  `models.cfb_game.run`. Mapping, declared: teams through `models.game.franchise`;
  `start_ts` = kickoff; no neutral site; **conference = none**, so the regression
  target is 1500 and conf_w is inert (the NFL has no conference-strength
  structure of the college kind and I will not invent one from divisions);
  entry −300 applies to the one team that enters after the first season; tied
  games are skipped by that walk (college has none) and are not in c-28's
  decisive population anyway. Scored on c-28's population against NFL home,
  record, NFL-fitted plain Elo, and **arm R0**: college's plain-Elo 2026 fit
  (k 80, hfa 55, regress 0.3, conf_w 1, entry −300) carried the same way.
- The constants were fitted on seasons through 2025 of the OTHER sport, so no
  game of the receiving sport informed them. Eight tests, own Holm family.
- **Known in advance from c-39's published Briers:** arm N scored 0.1929 and
  college-fitted plain Elo 0.1861, so arm N is expected to LOSE to the fitted
  plain Elo. That comparison is a carried model against a fitted baseline and
  is reported as such; N against N0 is the like-for-like.
- Reading: a carried arm beating home, record and its own carried plain Elo →
  **"the advantage is not a product of the per-sport fit."** A carried arm
  failing against home or record → **"that advantage is suspected of being
  fitted."**

**Cuts** (not verdicts; one Holm family; for each: 3 `d_b` per sport, 3 `D_b`,
3 `Ds_b`):
1. common window, 2005-2025 in both sports;
2. regular season only (NFL `REG`; college `regular`);
3. regular-season weeks 1-4; 4. regular-season weeks 5+;
5. college non-neutral games only, against all NFL (the NFL walk has no neutral flag);
6. college conference games only, against all NFL;
7. 2005-2014; 8. 2015-2025.

Eight cuts × 12 = 96 intervals. A cut where a `d_b` is below zero in one sport
and not the other after Holm is listed by name.

## SECONDARY — against a price. Labelled: NOT COMPARABLE across sports

- NFL: c-28's 2c rule — the nflverse moneyline close, 2006-2025, normalised.
  Must reproduce +0.0092 on 5,281 games to 4 dp.
- College: c-39's 2a rule — the CFBD moneyline, median over providers,
  2021-2025. Must reproduce +0.0107 on 3,768 games to 4 dp.
- NFL again on 2021-2025 only, so the seasons at least match.
- College 2026, c-39's 2c rule (the timestamped Odds API pre-kickoff h2h), on
  whatever is on disk at run time; the capture grows every week, so this will
  not equal c-39's 168 games.

Each is printed with its own game-block interval, Brier of both sides and
Brier(model) / Brier(price). **No between-sport interval is computed for any
price comparison and no verdict draws on one.** The sentence that travels with
the table:

> The NFL comparator is the nflverse closing line, a sportsbook close of
> unrecorded provenance. The college comparator is CFBD's last value per
> provider, read after the game, with no capture time — not a close. Different
> books, different seasons (2006-2025 against 2021-2025), different base
> uncertainty; and if any college value is in-game it favours the price. The
> two losses are shown side by side and are not a measurement of which market
> is harder to beat.

## Must not

- The NFL model is not refitted. `models/game.py`, `models/season.py`,
  `jobs/season_model.py`, `research/game_forecast.py`, `models/cfb_game.py` and
  `research/cfb_game_forecast.py` are **not edited**. `research/c28_season_identity.py`
  is run before and after and both hashes are reported. The comparison value is
  the hash c-39 recorded on the same `nfl_games` data version; a hash that
  differs with the model files unchanged in git is a DATA move and is reported
  as that, with the data version.
- c-39's S1 is not adopted.
- No request is made, no credit is spent. `market_log.db` and `cfb.db` are
  opened `mode=ro`. The script writes only `--json-out` and `--log-out`.
- Everything printed is an aggregate or an interval; no game is named.
