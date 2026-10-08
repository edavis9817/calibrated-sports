# Decomposing usage: does it raise resolution?

Unit c-27 (track C), 2026-09-30. Findings only. Nothing publishes and nothing
tunes `models/baseline.py`.

- **Scope, stated so it travels with every number.** NFL player props, over
  side, receptions and rush attempts. c-24's two populations, identical rows:
  - **P1**: brief 023's walk-forward rows against the de-vigged DK/FD/MGM
    **book close**, 2023-2025 (postseason included, as in c-24), 16,041
    outcomes, 852 games, 66 season-weeks.
  - **P2**: c-19's weeks 2-3 2026 rows against the **Kalshi mid** at
    kickoff - 180 min, 2,513 rungs, 31 games.
- **Order of work.** Pre-registration `docs/C27-decomposed-usage-preregistration.md`
  committed and pushed at **`fc25f38`** before `research/decomposed_usage.py`
  existed. The only number computed before it was the pre-run MDE proxy, which
  involves no decomposed model (it is in the pre-registration).
- **Reproduction gate: passed.** P1 reproduces c-24 per season to 4 dp
  (+0.0229 / +0.0237 / +0.0195 on 4,785 / 5,225 / 6,031). P2 was rebuilt with
  player ids and matched **2,513 of 2,513** c-24 rows, baseline p unchanged on
  every one, Brier difference +0.0121.
- **Scoring is c-24's, imported** (`research.ranking_calibration`: `corp`,
  `auc`, `wauc`, `brier`, `Pop`). A test asserts none of them is redefined.
- **Reproduce.** With `LOGGER_DB` pointing at `market_log.db` (opened
  `mode=ro` only):
  1. `python -m research.decomposed_usage --premise` (Step 0, ~15 s)
  2. `python -m research.decomposed_usage --build-p2 --cache D:/temp/c19/extract.sqlite --c24-rows D:/temp/c24/rows.json --p2-out D:/temp/c27/p2_rows.json` (~5 min)
  3. `python -m research.decomposed_usage --ledger D:/temp/c24/wf_ledger.csv --p2 D:/temp/c27/p2_rows.json --out-dir D:/temp/c27` (~8 min)
  4. post-hoc: `... --posthoc-twin --out-dir D:/temp/c27` (~4 min)

## Headline

**By the registered rule, resolution rises. By any practical measure, it rises
by almost nothing, and on the population where the brief located the problem the
decomposed model still carries about a third of the market's resolution.**

- **Primary**, P1 pooled, week-block: dDSC(decomposed − baseline)
  **+0.0004 [+0.0001, +0.0008]**, MDE 0.0005. The interval excludes zero, so the
  registered verdict is **"resolution rises"**.
- **Size.** DSC goes 0.0009 → 0.0013. The market's is 0.0045. The decomposition
  closes **~11%** of the resolution gap to the close (dDSC vs market −0.0037 →
  −0.0033 [−0.0044, −0.0023]) and **~13%** of the ordering gap (dAUC vs market
  −0.043 → −0.038 [−0.048, −0.028]). The ordering deficit still excludes zero
  on every measure.
- **The Brier improvement is mostly calibration, and it is not the result.**
  dBrier(decomposed − baseline) −0.0047 [−0.0062, −0.0032], of which dMCB is
  −0.0044 [−0.0056, −0.0030] and resolution +0.0004. The decomposed model still
  loses to the close by +0.0171 [+0.0139, +0.0204], and **81% of that is still
  miscalibration** (MCB 0.0171 vs the close's 0.0032).
- **The rise is not robust across cuts.** 2023 and 2024 exclude zero (lower
  bounds +0.00005 and +0.00003); 2025 does not (+0.0001 [−0.0007, +0.0009]).
  Receptions alone +0.0004 [−0.0000, +0.0009] and rush attempts alone +0.0001
  [−0.0006, +0.0010] **each contain zero**; only the pooled estimate clears.
- **Against the Kalshi ladder (P2)** DSC rises +0.0046 [+0.0002, +0.0090]
  (0.0614 → 0.0660, market 0.0715) — but the post-hoc control below shows
  **none of that is the decomposition**.

## Step 0 — the premise check (training seasons 2013-2022, REG)

Per-game lag-1 autocorrelation (r1) and the method-of-moments shrinkage weight
on 8 games (w8):

| factor | entities | obs | mean | r1 | k (games) | w8 | demeaned r1 / w8 |
|---|---:|---:|---:|---:|---:|---:|---|
| team plays | 320 | 5,182 | 61.4 | 0.108 | 11.9 | 0.403 | |
| team pass attempts | 320 | 5,182 | 34.8 | 0.171 | 6.1 | 0.567 | |
| team targets | 320 | 5,182 | 33.9 | 0.189 | 5.5 | 0.594 | |
| team carries | 320 | 5,182 | 26.6 | 0.144 | 8.6 | 0.481 | |
| team rush rate | 320 | 5,182 | 0.433 | 0.178 | 6.2 | 0.565 | |
| target share | 4,419 | 53,849 | 0.094 | **0.653** | 0.68 | **0.922** | 0.621 / 0.912 |
| snap share | 4,416 | 53,830 | 0.462 | 0.812 | 0.40 | 0.952 | 0.800 / 0.948 |
| catch rate | 4,043 | 39,780 | 0.658 | **0.070** | 7.0 | **0.534** | 0.033 / 0.445 |
| yards per target | 4,043 | 39,780 | 7.15 | 0.049 | 10.6 | 0.430 | 0.030 / 0.374 |
| receptions (raw) | 4,419 | 53,849 | 2.08 | 0.538 | 1.06 | 0.883 | 0.519 / 0.875 |
| targets (raw) | 4,419 | 53,849 | 3.20 | 0.621 | 0.78 | 0.912 | 0.588 / 0.900 |
| carry share (RB) | 1,275 | 14,779 | 0.287 | 0.772 | 0.52 | 0.939 | |
| yards per carry (RB) | 1,167 | 11,997 | 4.08 | 0.028 | 7.6 | 0.513 | |
| carries (raw, RB) | 1,275 | 14,779 | 7.62 | 0.684 | 0.73 | 0.917 | |

- **Registered rule: PREMISE WEAKENED.** Catch rate / target share: r1 ratio
  **0.107** (well under 0.5), w8 ratio **0.579** (over 0.5, under 0.75). The two
  measures disagree because w8 saturates: catch rate is near-noise game to game,
  but eight games of it still earn half weight. Demeaned by position-season the
  ratios are 0.052 and 0.488. The model was fitted, flagged.
- **The brief is wrong about team volume.** It calls team volume and usage
  share "sticky week to week". Usage share is (r1 0.65 target share, 0.77 carry
  share). **Team volume is not**: team targets r1 0.19, team carries 0.14, team
  plays 0.11 — the same order as catch rate's 0.07, and its w8 (0.48-0.59)
  brackets catch rate's 0.53. By the rule the brief proposed for catch rate,
  team volume is a noisy factor too. The sticky factor is share, and share is
  already most of what the raw stat carries (receptions r1 0.54, targets 0.62).
- **Route share is not measured**: no table in the store carries routes.
- **The first Step 0 run had a defect, fixed before any model was fitted.**
  `nfl_player_week` writes a relocated franchise's current code (LV, LAC, LA)
  and `nfl_games` writes the code of the season (OAK, SD, STL), so ~1,550
  player-weeks a season failed to map through 2019 and those teams' games were
  split. That run read team plays r1 0.719, an artefact. Folding the codes
  (`RELOCATED`) dropped unmapped rows from 29,314 to 1 and moved every team row
  above; the catch-rate rule was unchanged (0.106 / 0.578, WEAKENED). The
  broken run's log is kept at `D:/temp/c27/premise_run1_relocation_bug.log`.

## The registered results (24 intervals, 17 exclude zero)

Week-block on P1 (66 blocks, 22 per season), game-block on P2 (31).

| population | DSC dec / base / market | **dDSC dec − base** | dAUC dec − base | dMCB dec − base |
|---|---|---|---|---|
| P1 pooled | 0.0013 / 0.0009 / 0.0045 | **+0.0004 [+0.0001, +0.0008]** | +0.0055 [+0.0009, +0.0100] | −0.0044 [−0.0056, −0.0030] |
| P1 pooled, game-block | same | +0.0004 [+0.0001, +0.0008] | | |
| P1 2023 | 0.0017 / 0.0012 / 0.0042 | +0.0005 [+0.0000, +0.0014] | | |
| P1 2024 | 0.0013 / 0.0008 / 0.0057 | +0.0005 [+0.0000, +0.0014] | | |
| P1 2025 | 0.0019 / 0.0018 / 0.0050 | +0.0001 [−0.0007, +0.0009] | | |
| P1 receptions | 0.0015 / 0.0011 / 0.0055 | +0.0004 [−0.0000, +0.0009] | | |
| P1 rush attempts | 0.0018 / 0.0018 / 0.0028 | +0.0001 [−0.0006, +0.0010] | | |
| P2 (Kalshi) | 0.0660 / 0.0614 / 0.0715 | +0.0046 [+0.0002, +0.0090] | +0.0112 [−0.0005, +0.0231] | +0.0000 [−0.0013, +0.0012] |

Against the market:

| population | dDSC dec − market | dAUC dec − market | d_wAUC dec − market | dBrier dec − market |
|---|---|---|---|---|
| P1 pooled | −0.0033 [−0.0044, −0.0023] | −0.0378 [−0.0484, −0.0280] | −0.0460 [−0.0602, −0.0333] | +0.0171 [+0.0139, +0.0204] |
| P2 | −0.0055 [−0.0111, −0.0002] | −0.0157 [−0.0288, −0.0031] | −0.0289 [−0.0580, −0.0023] | +0.0075 [+0.0020, +0.0135] |

- **The ordering deficit narrows on P1**, and both registered measures say so:
  dAUC +0.0055 [+0.0009, +0.0100], d_wAUC +0.0053 [+0.0004, +0.0102]. On P2
  neither excludes zero.
- **P1 2023/2024 lower bounds print as +0.0000**; unrounded they are +0.00005
  and +0.00003. They pass the rule and should be read as touching zero.
- **The realised MDE is 0.0005, not the pre-run proxy's 0.0015.** The proxy was
  baseline − market; two versions of one model are far more correlated than a
  model and a market, so the difference is estimated three times more
  precisely. That precision is what lets a 0.0004 effect clear.

## Post-hoc — not registered: is it the decomposition?

The decomposed model differs from the baseline in more than decomposing. It
counts a played game with no stat row as a zero (nflverse writes no row for
those — CLAUDE.md), weights season T-1 at 0.5, and takes its role prior from a
snap rank. So the registered comparison cannot attribute a rise to the
decomposition. An **undecomposed twin**, added after the smoke run and not
among the 24, keeps all of that machinery and shrinks the raw stat directly:

| population | DSC dec / twin / base | dDSC dec − twin | dDSC twin − base |
|---|---|---|---|
| P1 pooled | 0.0013 / 0.0010 / 0.0009 | **+0.0003 [+0.0001, +0.0006]** | +0.0001 [−0.0002, +0.0004] |
| P1 receptions | 0.0015 / 0.0013 / 0.0011 | +0.0002 [−0.0001, +0.0005] | +0.0002 [−0.0002, +0.0006] |
| P1 rush attempts | 0.0018 / 0.0014 / 0.0018 | +0.0005 [−0.0001, +0.0013] | −0.0004 [−0.0011, +0.0002] |
| P2 | 0.0660 / 0.0661 / 0.0614 | **−0.0001 [−0.0024, +0.0025]** | **+0.0046 [+0.0003, +0.0086]** |

- **On P1, the small rise is mostly the decomposition** (+0.0003 of +0.0004).
- **On P2, it is none of the decomposition.** The whole +0.0046 is the shared
  machinery; decomposing adds −0.0001. The registered P2 secondary "resolution
  rises" is true and is not evidence for the hypothesis.
- **The brief's mechanism is not the one that shows.** The hypothesis is that
  separating a noisy catch rate helps receptions. On receptions the
  decomposition's own contribution is +0.0002 [−0.0001, +0.0005], null. The
  largest point estimate is on rush attempts (+0.0005), which has no noisy
  efficiency factor at all — its decomposition is team carries x carry share,
  with the opponent adjustment on team volume. Neither per-stat interval
  excludes zero.
- These are 8 further intervals chosen after the registered numbers were seen.
  Read them as a direction for a pre-registered follow-up, not as findings.

## What this means

- **"The model carries no information" is too strong, and the decomposition
  does not change the picture.** The decomposed model's P1 resolution, 0.0013,
  is about a third of the market's 0.0045. The market's own resolution on these
  main lines is small: book lines sit at the median, so there is little left to
  resolve for anyone. Per c-24 §4, the recalibrated baseline already scored
  within 0.0007 of a constant.
- **The decomposition is a better model and not a useful one.** It is better
  calibrated (MCB −0.0044), slightly better ordered (dAUC +0.0055) and
  marginally more resolving (+0.0004). Against the close, every one of those
  moves is an order of magnitude smaller than the remaining gap.
- **By the unit's own terms this is a pass on resolution that is too small to
  matter.** The report does not claim more. A later unit asking whether it
  clears cost would start from a forecaster that still orders 0.038 AUC worse
  than the price, and it should not be opened on this result.

## What this cannot support

- **One model specification.** Recency weight 0.5, snap-rank buckets, the
  method-of-moments constants and the beta-binomial share are declared, not
  tuned. No variant was bracketed; a differently built decomposition could do
  better or worse.
- **Constants fitted once on 2013-2022**, including for 2025 and 2026. The
  baseline refits its drift per season, so the two models do not see identical
  training information. The decomposed model sees *less* recent training data,
  which cuts against it.
- **Resolution is measured by in-sample PAV** (c-24's CORP). Its bias is shared
  by both forecasters in a difference; the per-forecaster DSC levels are not
  what to rely on.
- **24 registered intervals, no multiplicity correction.** 17 exclude zero;
  about 1.2 would under a global null. The primary is one test.
- **P2 is 31 games over 2 weeks**, and depends on the c-19 scratch cache;
  week 2's live quotes are leaving the store, so a re-run without the cache
  sees a smaller population.
- **Route share was not measured.** No routes are in the store.
- **Nothing here is about betting.**
