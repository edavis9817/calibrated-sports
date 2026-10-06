# C34 — does known-before-kickoff information raise the prop model's resolution? Pre-registration

Committed and pushed **before** the script exists and before any feature is
joined to an outcome. The script is `research/known_before_kickoff.py`; it is
written after this file and must implement what is below. Nothing here is edited
after the first run; a correction is appended at the bottom, dated, with the
reason.

Unit c-34 (track C), 2026-10-06. Branch `c-34-known-before-kickoff`, cut from
`origin/c-33-soft-markets` (`2d84f8d`), the tip of the c-32 stack.

Scope, stated so it travels: **NFL** player props, **receptions and rush
attempts**, over side, c-24's two populations (P1: book close 2023-2025; P2:
Kalshi mid, 2026 weeks 2-3). Information sources: nflverse's official injury
report as held in `feeds.db`, and nflverse depth charts as archived under the
logger's raw tree. No other source, no other market, no other sport.

## What was done before this file

Looked at, with no outcome joined to any of it:

- `feeds.db` schema and `injury_reports` per season: row counts, the share with
  `upstream_asof_ts`, the status vocabularies, our ingestion times.
  - 2009-2024 carry `upstream_asof_ts` (nflverse `date_modified`) on every row
    (17 of 4,821 in 2009). **2025 and 2026 carry none.**
  - Every row of 2009-2025 was ingested by us between 2026-09-20 and 09-22. So
    for 2025 the only stamp we hold postdates the whole season.
  - 2026 is versioned by our own ingestion: captures at 09-20 03:45Z, 09-22
    04:58Z, 09-24 09:20Z, 09-28 21:04Z, then twice daily from 09-29.
- Depth chart parquets: 2001-2024 are **weekly-labelled with no timestamp**
  (`season, club_code, week, depth_team, depth_position, gsis_id`); 2025 and
  2026 carry a per-snapshot `dt` (221 and 218 distinct). We hold one copy of
  2025 (archived 2026-09-09) and 27 daily copies of 2026.
- c-24 / c-27 scratch inputs exist: `D:/temp/c24/wf_ledger.csv`,
  `D:/temp/c27/p2_rows.json` (2,513 rows with player ids, weeks 2 and 3).
- **One number was computed, and it involves no information feature**: the
  pre-run MDE proxy, the week-block SE of a model-versus-model difference on the
  two P1 subsets this unit can use, taken from c-27's stored predictions
  (decomposed − baseline, 500 draws, seed 34):

      P1 2023-24  n 10,010  44 weeks   dDSC SE 0.00023 -> MDE 0.00065   dAUC SE 0.0031 -> MDE 0.0088
      P1 2025     n  6,031  22 weeks   dDSC SE 0.00039 -> MDE 0.0011    dAUC SE 0.0036 -> MDE 0.0100

## The claim under test

c-27, c-30 and c-31 re-arranged inputs the model already had. The hypothesis
here: **the market out-orders the model because it knows things the model is not
fed, and feeding the model the injury report and the depth chart raises its
RESOLUTION (CORP DSC) and narrows the ordering gap to the market** (c-24: dAUC
−0.043 on P1).

Bound on what can be found, recorded now: on P1 the baseline's DSC is 0.0009 and
the close's is 0.0045 (c-24). A selection fact also bounds it: a P1 row exists
only where a book hung a line and the player played, so a player ruled Out is
never a row. The player's own status therefore reaches P1 only as
Questionable / Doubtful-but-played / limited practice. Teammate absence is not
selected away, which is one reason arm 3 is registered separately.

## Step 0 — the as-of audit (run first, over the full span, before any model)

Kickoffs come from `nfl_games.kickoff_ts` (newest `data_version`), REG only.

**Injury rows.** A row is PROVABLE for its team's game in (season, week) iff:

- seasons with `upstream_asof_ts`: it is non-null, `< kickoff`, and
  `> kickoff − 8 days`;
- 2025: never (no upstream stamp; our stamp is 2026-09-20) — **the season is
  excluded**;
- 2026: the version in force at the cut, `valid_from_ts <= cut < valid_to_ts`
  (our own ingestion stamps), cut = kickoff − 180 min (P2's pricing instant).

A **team-game** is USABLE iff it has at least one row and every row for that
(season, week, team) is provable. One unprovable row excludes the team-game,
because a row modified after kickoff may have been rewritten in place and its
pre-kickoff content is not recoverable. A team-week with no rows at all is
excluded and counted (a missing report cannot be told from a clean one). For
2026 the team-game is usable iff a capture taken at or before the cut holds at
least one row for that team-week; the capture's lead on kickoff is reported.

What this does NOT prove, and the audit says so: `date_modified` is upstream's
claim. We did not capture any 2009-2024 row before its game.

**Depth charts.** Strict rule (the brief's): the chart for a team-game is the
snapshot with the last `dt` **strictly before** the cut, and it must be within
8 days of kickoff. Only 2025 and 2026 have a `dt`, so **only 2025 (P1) and 2026
(P2) are testable under the strict rule; 2023-2024 are excluded**.
For 2026 the audit additionally checks our 27 daily copies: a snapshot's rows
for a past `dt` must be identical in every later copy (the "rewritten daily"
trap), and no `dt` may postdate the copy it sits in. For 2025 we hold one
post-season copy; `dt` is upstream's claim, as with injuries.

The audit prints, per season 2009-2026: team-games total / usable for each
source, the reason for each exclusion, and — for the screened training
population and for P1 and P2 — player-games surviving.

**Stop rule.** An arm is tested only if its surviving P1 rows are >= 3,000 and
span >= 20 season-weeks. If no arm clears, the audit table is the finding and
the unit stops there.

## Step 1 — how information enters the model

The c-24 ledger holds the baseline's probability, not its distribution, and no
book line exists before 2023 to fit a probability-level adjustment on. So the
information is fitted **at the stat level, on seasons before T**, and applied
to the baseline's own probability:

1. **Training rows** for test season T: REG player-games of seasons
   2013..T−1 (snap counts start 2013), restricted to seasons whose features are
   usable for that arm, the player having PLAYED (stat row, or offensive
   snaps > 0 with the stat = 0), with >= 4 as-of games and an as-of expectation
   E0 above the research screen (receptions 2.0, carries 6.0). Receptions:
   WR/TE/RB. Rush attempts: RB.
   E0 = shrunk as-of mean of the stat over the player's played games in seasons
   (T'−1, T') before kickoff, season T'−1 weighted 0.5 (c-27's declared
   weights), shrunk with k = 6 games toward the position's training mean.
2. **Poisson regression**, log link, offset log E0, intercept plus the arm's
   features, ridge 1e-6, fitted per stat. A feature with fewer than 200 non-zero
   training rows is dropped and listed. `walkforward`-style leak check: the fit
   refuses if any training season >= T.
3. **Multiplier** m = exp(beta · x), intercept excluded, clipped to [0.5, 2.0].
   A row with no non-zero feature has m = 1 and keeps the baseline probability
   bit for bit — so the comparison is the baseline against itself plus the
   information, and nothing else moves.
4. **Applying it.** v = the stat's training variance-to-mean ratio (mean within
   player-season, screened population, floor 1.05). Solve mu such that
   P(NB(mu, v) > line) = p_baseline; p_info = P(NB(mu · m, v) > line).
   Sensitivity: v × 0.5 and v × 2.

Test seasons and their training spans: T = 2023 (2013-2022), 2024 (2013-2023),
2025 (depth arm: 2013-2024), 2026 (injury arms: 2013-2024; depth arm:
2013-2025).

P1 rows outside the modelled positions (a QB's rush attempts, a FB's receptions)
get m = 1 and stay in the population; the count is reported.

### Shared definitions

- **Family** = the player's position on his row for the game (WR, TE, RB, QB).
- **As-of volume share**: weighted sum of the player's volume (targets for
  receptions, carries for rush attempts) over the weighted sum of his team's, on
  his played games in seasons (T−1, T) before kickoff.
- **As-of roster** of team X for game G: players whose most recent played game
  before kickoff was for X and within X's last 4 games, plus anyone on X's
  usable injury report for the week who has as-of history.
- **Usage rank**: rank of as-of mean volume among the as-of roster's same-family
  players.
- **Absent** = report status Out or Doubtful on a usable report.
  Only the report can make a player absent: IR and suspension are not in this
  source, so absence is understated and the unit says so.

### Arm 1 — injury status, own and adjacent

Own: `own_Q` (Questionable), `own_DO` (Doubtful or Out, and played), `own_P`
(Probable — training seasons to 2015 only), `own_LP` (limited practice),
`own_DNP` (did not practise), `own_listed_full` (on the report, full practice,
no status). Adjacent in usage rank within family: `above_absent`, `above_Q`,
`below_absent`, `below_Q`. 10 features.

### Arm 2 — depth chart rank

Depth rank = the player's best rank at his own family's positions on the
offense chart (2025+: min `pos_rank` over group `3WR 1TE` rows whose `pos_abb`
maps to the family; to 2024: min `depth_team` over `Offense` rows).
`rank2`, `rank3p`, `unlisted` (reference: rank 1); `promoted` / `demoted` against
the chart used for the team's previous game; `chart_above_usage` /
`chart_below_usage` (depth-rank bucket 1/2/3+ better / worse than usage-rank
bucket). 7 features.

- Test features are strict (`dt` before the cut): P1 2025 and P2.
- **Training** features for T = 2025 can only come from the weekly-labelled
  2013-2024 charts. They are joined **lagged one week** (the week w−1 chart for
  the week w game; no chart for a team's first game), on the inference — not
  proof — that a chart labelled week w−1 describes a time before week w. This
  touches the coefficients only; no test row uses a label-dated chart. T = 2026
  adds the 2025 `dt` charts to training.
- **Slot** (`pos_slot`) exists only in the 2025+ format, so it has no training
  data for P1 and is **not modelled**; the brief asked for it and this is a
  limit of the archive.
- Descriptive only, **not read for the verdict**: the same arm on P1 2023-24
  with lagged-label charts as the test feature.

### Arm 3 — teammate absence (vacated share)

`vac_same_new`: summed as-of volume share of absent same-family teammates who
played the team's previous game. `vac_same_old`: the same for absent teammates
who did not. `vac_other`: absent teammates of the other skill families
(WR/TE/RB). `qb1_absent`: the team's as-of leader in pass attempts is absent.
4 features (continuous shares, one flag).

## Populations and scoring — c-24's, imported

- **P1**: `decomposed_usage.load_p1_rows` (asserted identical to
  `ranking_calibration.load_p1`). The c-24 reproduction check (n and
  Brier(model) − Brier(close) per season to 4 dp) runs first on the full
  population; an arm's rows are then the subset whose team-game is usable.
- **P2**: c-27's 2,513 rows with ids; an arm's rows are the usable subset.
- `research.ranking_calibration` is imported: `corp`, `auc`, `wauc`, `brier`,
  `Pop`. Nothing is copied.
- Week-block bootstrap on P1 (blocks = season-week), game-block on P2 (two
  weeks cannot be blocked by week). 2,000 draws, seed 34, percentile.

**Primary, one per arm — the verdict is resolution, not Brier:**
dDSC = DSC(baseline + arm) − DSC(baseline) on the arm's surviving P1 rows,
pooled. Arms 1 and 3: P1 2023-24. Arm 2: P1 2025.

- lo > 0: **"resolution rises"**.
- contains 0: **"no rise in resolution detected"**, with its realised MDE
  (2.8 × SE). A null, not a refutation.
- hi < 0: **"resolution falls"**.
- An arm with dMCB < 0 and dDSC not excluding zero above is reported as
  **"improved calibration and not resolution: FAILED this unit"**, and the
  report does not lead with Brier.

Pre-run MDE: ~0.00065 (arms 1, 3), ~0.0011 (arm 2), before exclusions.

**The extra condition — does it close any of the ordering gap to the market?**
Per arm, on the same rows: dAUC and d_wAUC of (baseline + arm) − baseline.
"Narrows" iff the dAUC interval's lo > 0; reported with the share of the
baseline's AUC gap to the close it closes. Pre-run MDE ~0.009-0.010, i.e. about
a fifth of the −0.043 gap: a smaller narrowing cannot be seen here.

**Unit-level reading, fixed now.** If no arm's dAUC narrows the gap, the report
says, in these words and with this scope: *the injury report and the depth
chart, as these two sources carry them, are not what separates this model from
the close on receptions and rush attempts.* It does not say information is not
what is missing.

**Secondary intervals:**

- per arm (3): dAUC, d_wAUC, dMCB, dBrier vs baseline; dDSC, dAUC vs market —
  6 each = 18
- per arm, per stat (receptions, rush attempts) dDSC vs baseline = 6
- arms 1 and 3 per season (2023, 2024) dDSC = 4
- combined arm 1+3 on P1 2023-24: dDSC, dAUC vs baseline = 2
- P2, game-block: each arm dDSC and dAUC vs baseline (6); all three arms
  together dDSC and dAUC (2) = 8
- v sensitivity on each primary (× 0.5, × 2) = 6

**Test count: 3 primaries + 44 secondary = 47 intervals.** No multiplicity
correction; ~2.4 would exclude zero under a global null, and ~0.15 of the three
primaries.

**Descriptive, no interval read:** the coefficient table with training counts;
the share of P1/P2 rows with any non-zero feature per arm (how much information
there is to add at all); out-of-sample actual / E0 by vacated-share bucket on
the test seasons (the stat-level mechanism, independent of any line); arm 2 on
P1 2023-24 with lagged-label charts.

## Rules

- `market_log.db` and `feeds.db` are opened `mode=ro` only, in short sessions:
  the panel is read once and the connections closed before any fitting.
- The raw depth-chart parquets are read, never written.
- Per-outcome rows stay on `D:/temp/c34` and are never committed. Output is
  aggregates and intervals only.
- Nothing publishes, nothing tunes `models/baseline.py`, no credits are spent.
- Findings: `docs/findings/known-before-kickoff.md`.
