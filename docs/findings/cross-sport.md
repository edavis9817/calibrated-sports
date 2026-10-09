# Does the game model's advantage appear in both sports (c-38)

Pre-registration: `docs/C38-cross-sport-preregistration.md`, pushed at `dbd3d29`
before `research/cross_sport.py` existed. One registered run; nothing was
re-run or changed after it. Run log and aggregates:
`research/results/cross_sport.log`, `research/results/cross_sport.json`.

**Reproduce** (about 7 minutes; both stores are opened `mode=ro`):

    LOGGER_DB=<market_log.db> python -m research.cross_sport \
        --json-out research/results/cross_sport.json --log-out research/results/cross_sport.log

## Scope, stated so it travels with the numbers

- **Model:** one architecture, a margin-of-victory Elo that reads final scores
  and nothing else. NFL: c-28's (`models/game.py`, the season model's walk).
  College: c-39's port (`models/cfb_game.py`), registered primary rule,
  FBS-against-FBS games only. Constants are refit per season, walk-forward, in
  each sport by c-28 and c-39. **Nothing was refitted in this unit.**
- **Populations:** each sport's own registered one. NFL 2001-2025, 6,743
  decisive games, REG and POST. College 2005-2025, 15,508 decisive games.
  Both published Part 1 results reproduced to 4 dp before anything else was
  printed.
- **Outcome:** the home side won, against settlement. The price comparison is
  secondary and is **not comparable across the two sports** (last section).
- **"Works"** here means "beats three naive baselines". It does not mean
  "beats a price": in both sports the model loses to every price on disk.

## The answer

**Yes. The advantage over all three baselines has the same sign and the same
ordering in both sports** — the registered SUCCESS. 16 of 16 primary tests are
significant after Holm.

**Corrected by c-45 from f-31 run 4 (`docs/findings/c45-corrections.md`): the
"16 of 16" is not independent evidence, and the size claim below is a raw one.**
Once the reproduction gate passed (c-28's and c-39's published figures
reproduced), 6 of the 16 are gated to published values and the 4 ordering
contrasts cannot fail even at worst-case SE, so the registered success rule
could not have returned failure. The sign and ordering stand as a reproduction
of two published results side by side; they are not a test that could have come
out the other way.

dBrier, model minus baseline, game blocks, 2,000 draws:

| model − | NFL | college | college − NFL | registered MDE |
|---|---|---|---|---|
| home | −0.0255 [−0.0288, −0.0221] | −0.0615 [−0.0644, −0.0583] | **−0.0359 [−0.0404, −0.0314]** | 0.0065 |
| better record | −0.0162 [−0.0189, −0.0135] | −0.0380 [−0.0406, −0.0352] | **−0.0217 [−0.0255, −0.0179]** | 0.0055 |
| plain Elo | −0.0027 [−0.0035, −0.0018] | −0.0042 [−0.0049, −0.0034] | **−0.0015 [−0.0026, −0.0003]** | 0.0016 |

Ordering contrasts (the claim is both below zero in both sports):

| | NFL | college |
|---|---|---|
| c1 = d_home − d_record | −0.0093 [−0.0123, −0.0062] | −0.0235 [−0.0263, −0.0209] |
| c2 = d_record − d_plain | −0.0136 [−0.0162, −0.0109] | −0.0338 [−0.0364, −0.0311] |

On the skill scale, 1 − Brier(model)/Brier(baseline): home 0.104 NFL against
0.253 college; record 0.068 against 0.173; plain Elo 0.012 against 0.022. Each
difference excludes zero (+0.149, +0.104, +0.010).

- **The RAW advantage is larger in college on every baseline, on both scales,
  and the raw gap is not a same-strength comparison.** At equal forecast
  strength (college re-weighted to the NFL's distribution of favourite
  probability, next section) the dBrier gap is -0.0028 [-0.0071, +0.0012]
  against home (0.46 of its MDE: not detected, a null this population cannot
  resolve, not a measured zero), -0.0073 [-0.0115, -0.0029] against better
  record (1.21x its MDE, at the bar) and -0.0032 [-0.0046, -0.0018] against
  plain Elo (1.57x). **No re-weighted figure exists on the skill scale at all**,
  so "on both scales" has no equal-strength counterpart for one of its two
  scales; whether the skill-scale gap survives re-weighting is open.
- **The plain-Elo size difference is at its MDE and is fragile.** It excludes
  zero in the primary family (Holm p 0.012 - which f-31 run 4 reads as its
  unadjusted p: it passes Holm only as the family's last step, fails Bonferroni
  over 16, and is 0.89 of its MDE). In the cuts family it is not
  significant after Holm in any of the eight cuts, and its interval contains
  zero in three of them. Read: the margin-of-victory term is worth about as
  much in both sports, perhaps slightly more in college.
- The between-sport draws are independent per sport (seeds 3801 / 3802). One
  shared seed would have been common random numbers between the two things
  being compared; a committed test shows that it produces a zero-width
  difference on identical data.

## Why the raw gap is bigger in college: against home, mostly that college games are more lopsided

The model's mean favourite probability is 0.637 in the NFL and 0.735 in
college. 2.3% of NFL games have a favourite at 0.85 or more; 25.7% of college
games do. Of the outcome uncertainty, the model resolves 10.9% in the NFL and
25.8% in college (DSC/UNC).

College re-weighted to the NFL's distribution of favourite probability (eight
bins), minus the NFL:

| model − | raw college − NFL | matched | registered reading |
|---|---|---|---|
| home | −0.0359 | −0.0028 [−0.0071, +0.0012] | accounted for by the spread, to within the MDE (0.0061) |
| better record | −0.0217 | −0.0073 [−0.0115, −0.0029] | not by the spread alone |
| plain Elo | −0.0015 | −0.0032 [−0.0046, −0.0018] | not by the spread alone |

- **Against home, 92% of the raw gap disappears at equal forecast strength** -
  a ratio of two point estimates with no interval of its own, and the matched
  figure is 0.46 of its MDE, so what remains is not detected rather than
  measured as zero (f-31 run 4).
  Declared before the run: against a constant baseline this is close to
  mechanical for two calibrated models, so it is evidence both are calibrated,
  not of anything deeper.
- **Against the record baseline a third of the gap remains.** A win-loss
  record carries less in college at the same forecast strength, which is what
  uneven schedules would produce. That mechanism is an inference; nothing here
  tests it.
- **Against plain Elo the matched gap is LARGER than the raw one.** The
  margin term's gain in college sits in the close games (favourite below 0.70:
  −0.006 to −0.007 in college against −0.002 to −0.005 in the NFL) and is near
  zero in the quarter of college games with a favourite at 0.85 or more
  (−0.0008). The raw figure averages those together.
- This conditions on the model's own forecast. It is descriptive. It cannot
  separate talent spread from schedule structure.

## Is the advantage a product of the per-sport fit? No

Each sport's 2026 constants carried into the other sport, nothing fitted in
the receiving sport:

| carried model − | NFL constants over college (arm N) | college constants over the NFL (arm R) |
|---|---|---|
| home | −0.0505 [−0.0524, −0.0485] | −0.0213 [−0.0259, −0.0165] |
| better record | −0.0270 [−0.0290, −0.0249] | −0.0120 [−0.0156, −0.0082] |
| carried plain Elo (like-for-like) | −0.0035 [−0.0041, −0.0030] | −0.0017 [−0.0029, −0.0005] |
| FITTED plain Elo (not like-for-like) | +0.0068 [+0.0053, +0.0083] | +0.0016 [−0.0005, +0.0037] |

- **Both carried models beat home, record and their own carried plain Elo**,
  each significant after Holm (family of 8). Registered reading for both arms:
  the advantage is not a product of the per-sport fit.
- **Fitting is worth 0.0110 Brier in college and 0.0042 in the NFL**, about a
  sixth of each fitted model's advantage over home: Brier 0.1929 carried
  against 0.1819 fitted in college; 0.2248 against 0.2206 in the NFL.
- **A carried margin-of-victory model does not beat a plain Elo fitted to the
  receiving sport.** It loses in college (expected in advance from c-39's
  published Briers) and is indistinguishable in the NFL. The margin term is
  worth less than getting the constants right.
- Arm R's mapping was declared before the run: no conference in the NFL, so
  the conference weight is inert, and the entry offset applies to one team.

## The one place it appears in one sport only

Eight registered cuts, 96 intervals, 85 significant after Holm.

- **Regular-season weeks 1-4, against plain Elo.** College −0.0028
  [−0.0040, −0.0016]; NFL −0.0008 [−0.0024, +0.0007], which contains zero
  (c-28 already reported this).
- **This does not show the early-season margin gain is a college artefact.**
  The between-sport difference on that cut is −0.0020 [−0.0040, +0.0001] and
  contains zero, and the NFL's MDE there (0.0022) is about the size of the
  college effect. The NFL cut cannot tell "absent" from "the same and
  undetected".
- Every other cut has all three baselines below zero in both sports: common
  window 2005-2025, regular season only, weeks 5+, college non-neutral only,
  college conference games only, 2005-2014, 2015-2025.

## Against a price — shown side by side, NOT comparable

> The NFL comparator is the nflverse closing line, a sportsbook close of
> unrecorded provenance. The college comparator is CFBD's last value per
> provider, read after the game, with no capture time — not a close. Different
> books, different seasons (2006-2025 against 2021-2025), different base
> uncertainty; and if any college value is in-game it favours the price. The
> two losses are shown side by side and are not a measurement of which market
> is harder to beat.

| comparison | games | Brier model / price | dBrier | ratio |
|---|---|---|---|---|
| NFL, nflverse moneyline close, 2006-2025 | 5,281 | 0.2203 / 0.2111 | +0.0092 [+0.0067, +0.0114] | 1.043 |
| NFL, same close, 2021-2025 only | 1,420 | 0.2241 / 0.2115 | +0.0126 [+0.0079, +0.0170] | 1.060 |
| college, CFBD last value, 2021-2025 | 3,768 | 0.1960 / 0.1853 | +0.0107 [+0.0070, +0.0144] | 1.058 |
| college, Odds API pre-kickoff h2h, 2026 | 168 | 0.1745 / 0.1424 | +0.0321 [+0.0147, +0.0495] | 1.225 |

- **The model loses to the price in both sports.** No between-sport interval
  was computed, by registration.
- **"Loses by more in college" does not survive matching the seasons.** The
  +0.0107 against +0.0092 that prompted the question compares 2021-2025 with
  2006-2025. On 2021-2025 the NFL figure is +0.0126, and the two ratios are
  1.060 and 1.058. On this evidence the two losses are about the same size.
  The comparators still differ, so this is not a finding that the markets are
  equally hard.
- In both sports the loss is resolution, not calibration (dMCB −0.0003 and
  +0.0004; dDSC −0.0094 and −0.0102).
- The 2026 college figure is the only timestamped college price. It is 168
  games in three weeks, early in a season, and has no NFL counterpart here.

## The NFL model did not move

`research/c28_season_identity.py`, run before any work and after the last code
commit: sha256 `12b91365…49dd` both times, which is the value c-39 recorded.
No NFL-model file is touched by this branch.

**That is not the hash c-28 published** (`2a2ca2dc…0b16`). The model files
have no diff since c-28's lift commit (`d1b259d`); the store was re-ingested
(`nfl_games` data version 2026-09-30 at c-28, 2026-10-08 now). The hash moved
with the data, between c-28 and c-39, not with the model.

## Counts

133 registered intervals: 16 primary (one Holm family), 6 skill levels, 3
matched, 8 transfer, 96 cuts, 4 price. 16 specifications: the primary, the
matched comparison, two transfer arms, eight cuts, four price arms.
