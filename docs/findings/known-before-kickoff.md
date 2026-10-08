# Known-before-kickoff information: does it raise resolution?

Unit c-34 (track C), 2026-10-06. Findings only. Nothing publishes and nothing
tunes `models/baseline.py`. Credits spent: 0.

- **Scope, stated so it travels with every number.** NFL player props, over
  side, **receptions and rush attempts**. Information: nflverse's official
  injury report (`feeds.db`) and nflverse depth charts (raw archive). Scored on
  c-24's populations:
  - **P1**: the walk-forward rows against the de-vigged DK/FD/MGM **book
    close**. Injury arms: **2023-2024 regular season only**, 9,418 of 10,010
    rows, 36 season-weeks. 2025 is excluded (no provable as-of).
  - **P2**: 2026 weeks 2-3 against the **Kalshi mid** at kickoff − 180 min,
    2,420 (injuries) / 2,513 (depth) rungs, 30-31 games.
- **Order of work.** Pre-registration
  `docs/C34-known-before-kickoff-preregistration.md` pushed at **`32c1795`**
  before the script existed; addendum 0 at `774c798` (before the script);
  addendum 1 at `18fa62f` (after the as-of audit, before any fit). One smoke run
  at 20 draws preceded the registered 2,000-draw run; nothing was changed
  between them except the print path for the untested arm.
- **Scoring is c-24's, imported** (`research.ranking_calibration`: `corp`,
  `auc`, `wauc`, `brier`, `Pop`); the panel and P1 loader are c-27's, imported.
  A test asserts none is redefined.
- **Reproduce.** With `LOGGER_DB` pointing at `market_log.db` (opened `mode=ro`,
  closed before any fit; ~15 s):
  1. `python -m research.known_before_kickoff --audit --out-dir D:/temp/c34` (~35 s)
  2. `python -m research.known_before_kickoff --ledger D:/temp/c24/wf_ledger.csv --p2 D:/temp/c27/p2_rows.json --out-dir D:/temp/c34` (~11 min)

## Headline

**The injury report moves the stat and does not move the model's resolution.
Neither injury arm raises DSC, neither narrows the ordering gap to the close,
and both improve calibration. By the rule fixed in advance that is a FAILED
arm, twice. The depth-chart arm could not be tested on P1 at all.**

| arm | rows carrying any information | **dDSC (info − baseline)** | MDE | dAUC (info − baseline) | dMCB |
|---|---|---|---|---|---|
| 1 injury status, own and adjacent | 2,993 of 9,418 (31.8%) | **+0.00010 [−0.00022, +0.00040]** | 0.00044 | +0.0002 [−0.0037, +0.0034] | −0.0008 [−0.0015, −0.0000] |
| 3 teammate absence | 3,350 of 9,418 (35.6%) | **+0.00002 [−0.00017, +0.00019]** | 0.00026 | −0.0004 [−0.0032, +0.0022] | −0.0014 [−0.0021, −0.0008] |
| 1 + 3 together | 4,770 of 9,418 | +0.00007 [−0.00026, +0.00038] | 0.00044 | −0.0000 [−0.0041, +0.0036] | |
| 2 depth rank | **not tested** (stop rule) | | | | |

- **Registered verdict, arms 1 and 3: "no rise in resolution detected"**, and
  because dMCB is below zero in both, **"improved calibration and not
  resolution: FAILED this unit."** A null, not a refutation — but a tight one:
  the baseline's DSC on these rows is 0.0010 and the close's is 0.0052, so the
  gap is 0.0041 and the MDEs are 6% and 11% of it. An improvement worth having
  would have been seen.
- **The ordering gap does not narrow.** On these rows the baseline's AUC is
  0.5292 and the close's 0.5756 (gap −0.046, c-24's −0.043 on a subset). Arm 1
  closes +0.3% of it, arm 3 −0.9%; both intervals straddle zero with an MDE of
  ~0.004-0.005, about a tenth of the gap. With the information added the model
  still trails the close by dAUC −0.046 [−0.057, −0.035] (arm 1) and −0.047
  [−0.057, −0.036] (arm 3).
- **By the reading fixed before the run:** *the injury report and the depth
  chart, as these two sources carry them, are not what separates this model from
  the close on receptions and rush attempts* — with the depth chart half of that
  sentence resting on P2 and a descriptive run only (below).

## Step 0 — the as-of audit

REG team-games, cut = kickoff (kickoff − 180 min for 2026).

| season | team-games | injury report usable | depth chart usable (strict `dt`) |
|---|---:|---:|---:|
| 2009 | 512 | 1 (510 have no stamp) | 0 |
| 2010 | 512 | 491 | 0 |
| 2011-2020 | 512 each | 509-512 | 0 |
| 2021 | 544 | 543 | 0 |
| 2022 | 542 | 539 | 0 |
| 2023 | 544 | 543 | 0 |
| 2024 | 544 | 542 | 0 |
| 2025 | 544 | **0** | 544 |
| 2026 | 544 | 94 | 158 |

- **Injuries, 2010-2024: almost everything survives.** Where `date_modified`
  exists it sits a median 55 h before kickoff to 2020 and 47 h from 2021. Across
  2010-2024 only 7 team-games carry a row stamped at or after kickoff, 1 a row
  stamped more than 8 days early, 9 have no rows, and 20 (all 2010) no stamp.
- **Injuries, 2025: nothing survives.** No upstream stamp and our own stamp is
  2026-09-20. All 544 team-games, and so all 6,031 P1 rows of 2025, are out of
  the injury arms.
- **Injuries, 2026: provable by our own captures, and thin.** 94 team-games.
  For P2 the newest capture at or before the cut leads kickoff by a median 21 h
  (13-87 h). Week 2's was taken Saturday night; **week 3's is from Thursday
  09:20Z, before the Friday game designations exist** — so week 3 carries
  Wednesday's practice status and almost no Out / Questionable.
- **Depth charts, 2001-2024: no timestamp at all**, a week label only. Under
  the strict rule none is usable.
- **Depth charts, 2025: usable on upstream's word.** One archived copy, taken
  2026-09-09, after the season. `dt` is upstream's claim and cannot be checked
  against a contemporaneous copy.
- **Depth charts, 2026: the chart is stable, the player ids are not.** 27 daily
  copies; 4,994 past (copy, `dt`) snapshots re-read against the newest copy:
  **0** with a changed chart (team, position, slot, rank, espn id), 0 dropped, 0
  dated after their copy — and **3,224 with `gsis_id` rewritten**. Upstream
  back-fills ids into old snapshots, so a chart read today can name a player the
  same snapshot could not have joined on its own day. For a rookie or a new
  signing that is a small look-ahead in *who is listed*, not in *where*.
- **The same caveat covers every pre-2025 injury row:** we captured none of them
  before its game. "Provable" here means provable from upstream's stamp.

**Surviving player-games.**

| population | rows | injury-usable | depth-usable (strict) |
|---|---:|---:|---:|
| P1 2023 | 4,785 | 4,509 | 0 |
| P1 2024 | 5,225 | 4,909 | 0 |
| P1 2025 | 6,031 | 0 | 5,690 |
| P2 2026 wk 2-3 | 2,513 | 2,420 | 2,513 |

897 P1 rows are postseason (outside the REG audit) and 4 have no panel row.
1,479 P1 rows are positions the fit does not model (mostly quarterbacks' rush
attempts); they stay in the population with no adjustment.

**Stop rule.** Arms 1 and 3: 9,418 rows over 36 weeks — tested. **Arm 2: 5,690
rows over 18 weeks — NOT TESTED.** The rule asked for 20 weeks; I set it without
checking that four of 2025's 22 P1 weeks are postseason. It was my threshold and
it stands (addendum 1).

## The information is real at the stat level

Poisson fit on the screened regular-season population, seasons before T only
(T = 2024 shown: 23,665 receptions rows, 8,185 rush-attempt rows, 2013-2023).
Coefficients are log multipliers on the player's as-of expectation.

| feature | receptions | rush attempts |
|---|---|---|
| own: Questionable | −0.080 (se 0.018) | −0.120 (0.019) |
| own: limited practice | −0.055 (0.017) | +0.074 (0.017) |
| own: did not practise | −0.085 (0.025) | dropped (143 rows) |
| player above him absent | **+0.193** (0.022) | **+0.481** (0.018) |
| player above him Questionable | +0.039 (0.020) | +0.279 (0.019) |
| player below him absent | +0.035 (0.016) | +0.029 (0.016) |
| vacated same-position share, newly absent (per unit share) | **+0.640** (0.093) | **+0.687** (0.041) |
| vacated same-position share, already absent | +0.436 (0.084) | +0.519 (0.037) |
| vacated share, other positions | +0.181 (0.049) | +0.350 (0.286) |
| team's QB1 absent | −0.046 (0.017) | +0.002 (0.016) |

- A running back whose immediate superior is Out or Doubtful carries **~62%
  more** (e^0.481); a receiver in the same position catches ~21% more. This is
  the mechanism the brief named and it is plainly there.
- `own: Doubtful or Out` was dropped (5 and 1 training rows): a player with that
  status who plays is too rare to fit, which is the selection the
  pre-registration predicted.
- **Out of sample it is weaker than in sample.** Actual / expected on the 2023-24
  test seasons, by newly vacated same-position share: receptions 0.998 at zero
  (n 4,032), 1.081 (n 181), 1.072 (n 98), 1.010 above 0.20 (n 51); rush attempts
  1.008 (n 1,498), 0.932 (n 18), 1.003 (n 20), **1.407** above 0.20 (n 47). No
  intervals were registered for this table; the cells are small.

## Why it does not show up as resolution — post hoc, not registered

Computed after the run from the stored per-row predictions; no interval.

| arm, rows moved | n | mean baseline | mean with info | mean close | realized |
|---|---:|---:|---:|---:|---:|
| arm 1, moved up | 1,515 | 0.371 | 0.432 | 0.496 | 0.485 |
| arm 1, moved down | 1,478 | 0.449 | 0.408 | 0.499 | 0.451 |
| arm 3, moved up | 3,167 | 0.394 | 0.427 | 0.496 | 0.475 |
| unmoved (arm 3) | 6,068 | 0.419 | 0.419 | 0.498 | 0.482 |

- **The shift points at where the close already is.** Correlation of the shift
  with (close − baseline): **+0.31** (arm 1), **+0.21** (arm 3). Correlation of
  the shift with (outcome − close): **−0.003** and **−0.015**. The information
  explains part of why the model disagrees with the close and none of what the
  close gets wrong.
- **A P1 row is a line the book chose.** When a teammate is ruled out the book
  moves the *line*, and the over at the new line is again roughly a coin flip
  (mean close 0.496 on the moved rows). A model that did not know reads the
  raised line as a likely under; told, it moves back toward 0.5. That repairs a
  level error — calibration — and cannot order overs against unders at lines the
  market has already centred. On this population, information the market also
  has can only ever buy MCB.
- This is my explanation of a measured null, not a measured mechanism. What is
  measured is the two correlations and the table.

## P2 — the Kalshi ladder (game-block, 30-31 games)

| arm | rows moved | dDSC (info − baseline) | dAUC |
|---|---:|---|---|
| 1 injury status | 420 of 2,420 | −0.0004 [−0.0017, +0.0008] | −0.0018 [−0.0047, +0.0010] |
| 2 depth rank | 1,190 of 2,513 | +0.0006 [−0.0009, +0.0017] | +0.0009 [−0.0017, +0.0038] |
| 3 teammate absence | 401 of 2,420 | +0.0004 [−0.0006, +0.0017] | +0.0017 [−0.0011, +0.0048] |
| all three | 1,532 of 2,420 | +0.0006 [−0.0011, +0.0025] | +0.0016 [−0.0024, +0.0058] |

- Every interval contains zero. MDEs are 0.0016-0.0026 on a baseline DSC of
  ~0.061 (c-27), so P2 could only have seen an effect several times the size of
  P1's bound. Two weeks, and week 3's injury information is a Wednesday practice
  report.
- A ladder is not centred by the market the way a main line is, so the argument
  above does not apply to P2; it is simply underpowered.

## Depth charts — what there is, none of it a verdict

- **P1 2025, strict `dt`, DESCRIPTIVE (stop rule: not tested):** dDSC −0.00024
  [−0.00052, +0.00014], dAUC −0.0004 [−0.0028, +0.0018], dMCB −0.0006 [−0.0011,
  +0.0001]. 5,690 rows, 18 weeks. Coefficients were trained on 2013-2024
  label-dated charts lagged a week.
- **P1 2023-24, lagged-label charts as the test feature, DESCRIPTIVE:** dDSC
  −0.00010 [−0.00039, +0.00012]; dAUC **−0.0028 [−0.0051, −0.0005]** — ordering
  got slightly worse.
- The stat-level fit: rank 3 or lower −0.128 on receptions (se 0.027), chart
  above usage +0.061 (0.008), chart below usage −0.052 (0.018); for rush
  attempts unlisted −0.149 (0.017), chart above usage +0.100 (0.012), chart
  below usage −0.111 (0.016). The chart knows something the usage history does
  not; it did not turn into ordering on either descriptive run.
- **Slot was not modelled.** `pos_slot` exists only from 2025, so there is no
  earlier season to fit it on.

## The registered intervals

36 computed, 7 exclude zero: arm 1 dMCB; arm 3 dMCB and dBrier; and for both
arms dDSC and dAUC against the market (the model still loses). None of the 7 is
a gain in resolution or ordering. No multiplicity correction; ~1.8 expected
under a global null.

Per stat and season, dDSC (info − baseline): arm 1 receptions +0.00008
[−0.00026, +0.00037], rush attempts −0.00010 [−0.00120, +0.00113], 2023
+0.00027 [−0.00018, +0.00098], 2024 −0.00026 [−0.00067, +0.00021]; arm 3
receptions +0.00016 [−0.00015, +0.00036], rush attempts −0.00025 [−0.00095,
+0.00049], 2023 +0.00001, 2024 +0.00004. Dispersion sensitivity (v × 0.5, × 2):
all four intervals contain zero.

## What this does not establish

- **Nothing about 2025** for injuries, and nothing registered about depth
  charts on P1.
- **Nothing about information the market does not have yet.** Every P1 row is
  scored at the close, 15 minutes before kickoff, when the Friday report is two
  days old. Whether the report beats a line that has *not yet moved* — the open,
  or the hour after a designation — is a different question, is the one a bettor
  would care about, and is not answered here. No prop open for 2023-25 is on
  disk (brief 023: the backfill bought closes only); c-32's opens are Kalshi
  game lines, 2026 weeks 2-3.
- **Nothing about absences the report does not carry**: injured reserve,
  suspensions, and game-day inactives announced 90 minutes before kickoff.
- **Nothing about yardage, touchdowns or quarterback props.**
- The method adds information as a multiplier on the baseline's implied mean. A
  model rebuilt around the information might use it better; this unit did not
  build one.
- The dispersion the multiplier is applied through (v = 1.12 for receptions,
  2.30 for rush attempts) is my estimate, not the baseline's own.

## What it points at next

c-27, c-30, c-31 rearranged what the model had; this unit added something it
did not have and landed in the same place. The common factor is the scoreboard,
not the inputs: **at the close, on main lines, the market has already used
everything public.** Two things would be different in kind:

1. **Score before the line has moved.** The same features against the *open*,
   with the open-to-close move as the outcome (c-32's frame). That needs prop
   opens, which are not held for any season.
2. **Information with a clock on it**: the 90-minute inactive list against the
   in-between prices. `docs/briefs/023-inactives-capture-spec.md` specified the
   capture and it was never built.
