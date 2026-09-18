# F01 — the play-by-play archive, surveyed

TRACK F · cs-analytics · `C:\Users\Ethan Davis\code\cs-analytics`

Reproduce everything below with:

    python -m analytics.survey --scan            # 4 datasets, 81 files, ~10s
    python -m analytics.survey --report
    python -m analytics.survey --report --steps
    python -m analytics.survey --silent-zeros
    python -m analytics.survey --dead-ends
    python -m analytics.survey --column air_yards
    python -m analytics.survey --dataset weekly_stats --column targets

The survey covers **four** seasonal nflverse feeds, not one — 81 files,
2,564,753 rows:

| dataset | asset | seasons |
|---|---|---|
| `pbp` | `play_by_play_{season}.parquet` | 1999–2026 (28) |
| `weekly_stats` | `stats_player_week_{season}.parquet` | 1999–2026 (28) |
| `snap_counts` | `snap_counts_{season}.parquet` | 2012–2026 (15; 2012 is empty) |
| `participation` | `pbp_participation_{season}.parquet` | 2016–2025 (10) |

The play-by-play is the subject, but **the defect this survey exists to prevent
is not a play-by-play column** — 2003–08 `targets` lives in `stats_player_week`,
and a sweep reading only the PBP walks past its own worked example. The filename
patterns are read from `nflverse.DATASETS`, not repeated.

Measured 2026-09-17 against the mirror on disk. Nothing here is estimated.

---

## 1. Where the files are

    D:\calibrated-sports\data\raw\nflverse\<pull-date>\play_by_play_<season>.parquet

Resolved through `analytics.paths`, never as a literal. The mirror is
partitioned by **pull date**, not by season, because nflverse applies stat
corrections and the pull date is the data version that makes a correction
legible. `paths.pbp_files()` takes the newest pull carrying each season.

| | |
|---|---|
| Seasons | **1999–2026**, 28 files, no gaps |
| Plays | **1,282,384** |
| Games | **7,289** |
| Size on disk | **489.4 MB** (of a 676.8 MB nflverse mirror, 219 files) |
| Columns | **372**, identical count in every season file |
| Pull dates | 1999–2025 from **2026-09-09**; 2026 re-pulled daily, currently 2026-09-17 |
| 2026 content | **week 1 only** — 16 games, 2,756 plays |

The 1999–2025 files come from **one pull**, on 2026-09-09. The nflverse mirror
is exempt from raw rotation (`RAW_ROTATE_EXEMPT="nflverse"`), so retention will
not touch it — checked, not assumed. But all 219 shards are in `raw_shards`
state `local` with `uploaded_ts` null: **there is no off-machine copy.** It is
re-downloadable, so this is an inconvenience rather than a loss. Flagged for
track D rather than acted on.

Scanning all 28 seasons takes about 6 seconds and the coverage table is 10,416
rows. This asset is far cheaper to work with than its size suggests.

---

## 2. Coverage cliffs

**98 of 372 columns** carry a coverage anomaly: 62 have seasons where the
column is absent, 24 are materially thin without being absent, 12 move by more
than 40% season-over-season without ever going thin.

The detector scores every season against **the column's own reference level**
(the median of its top five seasons), not against an absolute share — most of
these columns are event flags that are legitimately 0 on 99% of plays.

It counts two kinds of empty separately, and the second is the dangerous one:

- **null** — the column is NULL. A mean skips it, a join drops it. Loud.
- **zero** — the column is `0.0` or `""`. A sum includes it and is wrong.
  Silent.
- **nan** — a float NaN. The worst of the three, because NaN is not NULL:
  `count()` counts it and `fill_null(0) != 0` is true for it. It inverted a
  verdict here before it was handled — §2.10.

One caveat on reading the `informative` count: for a column whose zero is a
real value — `score_differential` at a tie, `posteam_score` before anyone has
scored — `informative` is lower than coverage. The anomaly detector uses it
anyway, because a per-season *relative* comparison is unaffected by a constant
bias, and the alternative (nulls only) is what misses the silent cliffs
entirely. Where an absolute figure is quoted in §4, it is the non-null share.

### 2.1 The one that matters most: targets do not exist for 2003–2008

This is the same defect named in the brief, and it is **not** confined to a
legacy file. Measured on both the play-by-play and the maintained
`stats_player` release:

| | receiver named on completions | receiver named on **incompletions** |
|---|---|---|
| 1999–2002 | 100% | 79–81% |
| **2003–2008** | 100% | **0.5–0.9%** |
| 2009–2026 | 100% | 70–83% |

`stats_player_week_<season>.parquet`, `targets` column, league total:

    1999  16,881    2003     3    2009  17,552    2025  17,490
    2002  17,686    2004     5    2015  18,870    2026     941
                    2005     0
                    2006    67
                    2007    14
                    2008    17

**Six seasons of targets are zero in the live release, and the play-by-play
cannot reconstruct them** — the receiver simply is not recorded on an
incompletion in 2003–2008. Receptions are unaffected: 100% of completions carry
a receiver in all 28 seasons, and the PBP-derived reception count matches
`stats_player_week.receptions` exactly in every season checked.

Two consequences, both hard:

1. **Any targets-based analytic starts in 2009**, or 1999 with a documented
   six-season hole. There is no fix.
2. **Even where it works, a PBP target count is not the true target count.**
   17–30% of incompletions have no receiver. PBP-derived targets land at
   1.002–1.006× `stats_player_week.targets` league-wide, so the two agree — but
   that is because nflverse builds the weekly table the same way. Both
   undercount the real thing by whatever share of incompletions go unattributed.
   Quote targets as *recorded targets*, not as targets.

### 2.2 Onset cliffs — nothing before season *N*

| columns | absent | kind |
|---|---|---|
| `air_yards`, `pass_length`, `pass_location`, `yards_after_catch`, `cp`, `cpoe`, `xpass`, `pass_oe`, `air_epa`, `yac_epa`, all four `xyac_*`, and 16 `total_*_air/yac_*` accumulators — **36 columns** | **1999–2005** | null |
| `drive_real_start_time`, `no_huddle` | 1999–2002 | null / **zero** |
| `nfl_api_id`, `order_sequence`, `play_clock`, `play_type_nfl`, `stadium`, `start_time`, `time_of_day`, `weather`, `jersey_number` and the three `*_jersey_number`, `drive_yards_penalized` | 1999–2000 | null |

Air yards from 2006 confirms the known fact. The **2009 receiving** half of that
known fact is more precisely the receiver-attribution hole in §2.1: air yards
exist from 2006, but on an incompletion in 2006–2008 there is no receiver to
attribute them to, so *receiving* air yards effectively begin 2009.

`no_huddle` is a **zero** cliff — not null, just 0 — and it ramps for another
decade after it appears (0.000 in 2002 → 0.008 in 2003 → 0.029 in 2006 → 0.094
in 2013). Treat it as unusable before ~2010 rather than as a clean 2003 onset.

### 2.3 Mid-series holes — full, empty, full

| column | absent | kind |
|---|---|---|
| `qb_hit`, `qb_hit_1_player_id`, `qb_hit_1_player_name` | **2003–2005** | **zero** |
| `qb_hit_2_player_id/name` | 2003–2006 | null |
| `tackle_for_loss_1_player_id/name` | **2003–2007** | null |
| `special_teams_play` | 1999–2000, **2003–2004** | zero |
| `end_clock_time` | 1999–2000, **2003–2021** | null |

`qb_hit` is the sharpest trap on the board: **0.021 of plays in 2002, exactly
0.000 in 2003–2005, 0.047 in 2006**, and non-null throughout. A sum over
1999–2010 silently loses three seasons and understates the other four (1999–2002
runs at a third of the modern rate — see §2.5).

### 2.4 Live-end collapses

| column | 2023 | 2024 | 2025 | 2026 (wk1) |
|---|---|---|---|---|
| `tackle_with_assist` (share of plays) | 0.075 | 0.052 | **0.014** | **0.007** |
| `stats_player_week.def_tackles_with_assist` | 3,725 | 2,567 | **703** | 20 |
| `stats_player_week.def_tackle_assists` | 13,299 | 14,861 | **17,059** | 954 |
| `stats_player_week.def_tackles_solo` | 20,419 | 20,216 | 20,681 | 1,175 |

This is a **reclassification, not a missing feed**: as `with_assist` falls,
plain `assists` rises. The three-column total is roughly stable (37,443 →
37,644 → 38,443), but **box-score SOLO — `solo + with_assist` per CLAUDE.md —
falls 11% across two seasons** (24,144 → 22,783 → 21,384).

There is an earlier step of the same kind at **2011** (`with_assist` 0.101 →
0.047 of plays; league 4,758 → 2,220) and a partial reversal at 2021.

*Routed to track A.* The tackles+assists market settles on the sum, which is
stable, so nothing published moves. But CLAUDE.md's tackle identity is stated as
a fixed rule, and its two components are migrating under it; anything that
quotes box-score solo across 2023–2025 is comparing different definitions.
I have not changed any settlement code — that is track A's.

### 2.5 Ramps — never thin, still wrong

These clear every absent/thin threshold and are still unusable across the step.
`--report --steps` lists them all.

| column | step | before → after |
|---|---|---|
| `receiver_player_id` | 2003 | 0.375 → 0.218 (see §2.1) |
| `qb_hit` | 1999–2002 vs 2006+ | ~0.022 → ~0.047 |
| `shotgun` | continuous 1999→2023 | 0.09 → 0.55 |
| `temp` / `wind` | 2022 | 0.668 → **0.375** |
| `touchback` | 2011, 2025 | rule changes |
| `assist_tackle_2_*` | 1999–2010 thin vs 2021+ | 0.124 reference |

`shotgun` is real football, not a data cliff — shotgun usage genuinely rose.
Named here so nobody "fixes" it.

`temp`/`wind` is a data problem: coverage runs 0.70–0.80 across 1999–2019, then
0.65, 0.67, **0.38** (2022), 0.55, 0.64, 0.66, 0.61. The modern seasons are the
worst-covered. `temp` is null-for-dome (never 0), `wind` does record 0. Given
CLAUDE.md's finding that wind is the only situational factor surviving
Bonferroni, a wind feature built on the 2020s is running on two-thirds of the
slate at best. **This is also why the live weather feed is Open-Meteo and not
nflverse** — independent of the "filled after the game" point, the nflverse
column is not even complete after the game.

### 2.9 The silent-zero class — non-null, and zero, for a run of seasons

`python -m analytics.survey --silent-zeros`. This is the `qb_hit` shape, swept
for deliberately: a column that is **present, populated and exactly zero** for
consecutive seasons. No null check can see it. A mean returns a number and the
number is wrong. It has already shipped once on this site.

**15 columns across the four datasets.** `snap_counts` and `participation` are
clean - zero each, which is a result rather than a gap.

| dataset | column | zero seasons | rows in the run | non-null in the run |
|---|---|---|---|---|
| weekly | **`def_tackles_for_loss`** | **2003-2011** (9) | 1 | 1.000 |
| weekly | **`def_tackles_for_loss_yards`** | **2003-2011** (9) | 1 | 1.000 |
| weekly | **`racr`** | **2003-2008** (6) | 39 | 0.211 |
| weekly | **`receiving_air_yards`** | **2003-2008** (6) | 44 | 1.000 |
| weekly | **`receiving_yards_after_catch`** | **2000-2005** (6) | 0 | 1.000 |
| weekly | **`targets`** | **2003-2008** (6) | 54 | 1.000 |
| pbp | `no_huddle` | 1999-2002 (4) | 24 | 1.000 |
| pbp | `qb_hit` | 2003-2005 (3) | 1 | 0.969 |
| weekly | `air_yards_share` | 2006-2008 (3) | 41 | 1.000 |
| weekly | `def_qb_hits` | 2003-2005 (3) | 2 | 1.000 |
| weekly | `pacr` | 2003-2005 (3) | 2 | 0.037 |
| weekly | `passing_air_yards` | 2003-2005 (3) | 3 | 1.000 |
| pbp | `special_teams_play` | 2003-2004 (2) | 3 | 1.000 |
| weekly | `target_share` | 2007-2008 (2) | 31 | 0.031 |
| weekly | `wopr` | 2007-2008 (2) | 31 | 0.031 |

**`def_tackles_for_loss` is the longest run in the archive and was unknown.**
1,796 player-weeks in 2002, **0 in every season 2003-2011**, 1,843 in 2012, and
`nonnull` is 1.000 throughout. A career-TFL figure over that span silently
zeroes nine seasons.

Three things this sweep taught about how to write it:

- **"Effectively zero", not "exactly zero".** Required to be exactly 0, the
  first version returned two columns and **missed `targets`** - league counts
  for 2003-2008 are 3, 5, 0, 67, 14, 17, so five of the six seasons are not
  exactly zero. It now flags at or below 2% of the column's own reference and
  **prints the row count**, so a reader sees "54 rows in six seasons" rather
  than taking the word "zero" on trust.
- **A NULL run is excluded on purpose.** `air_yards` 1999-2005 is null, belongs
  to §2.2, and is loud. Including it here would bury the columns that actually
  need this sweep under thirty-six that do not.
- **NaN is neither, and it inverted a verdict.** See §2.10.

One fragment worth naming so nobody builds on it: `passing_air_yards` is not
zero in 1999-2002 - it carries ~350 of ~17,000 player-weeks, about 2%, and PBP
`pass_length` shows the same 1% fragment in 1999. A partially charted 2% season
is not a season. Air yards begin 2006.

### 2.10 NaN is not null, is not information, and inverted a verdict

Found 2026-09-17 by the dead-ends detector track C asked for (§2.11), and it is
a defect in **this survey's own measurement**, not in the data.

`stats_player_week.target_share` is targets divided by zero targets for
2003-2008, so it is **NaN on essentially every row**: 17,172 of 17,232 in 2003,
**17,355 of 17,355 in 2005**. NaN is not NULL - polars `count()` counts it and
`fill_null(0) != 0` is TRUE for it - so the first version scored those six
seasons as **99.7% informative** and flagged the other twenty-two as the
anomaly. The verdict came out exactly inverted.

`nonnull` and `informative` now both exclude NaN, and `nan_n` is stored beside
them so the fact stays visible rather than being folded into a category that
hides it. What moved:

- `target_share` and `wopr` join the silent-zero class at 2007-2008 (13 -> 15).
- `air_yards_share`'s reference falls from 0.997 to 0.240 - the earlier figure
  was NaN inflation.
- The 98 / 62 / 24 / 12 play-by-play anomaly counts in §2 are **unchanged**.
  The play-by-play carries no pure-NaN column; this is a derived-ratio problem
  and `stats_player_week` is where the derived ratios live.

### 2.11 Dead at both ends - the shape an edge-scanning detector cannot see

Track C found `qb_hurry` in the CFB archive zero for 2004-2006 **and**
2014-2020 around a populated middle, and observed that every cliff detector
written so far scans from an edge.

`anomalies` scores each season against the column's own median-of-top-five
reference and never asks where in the series the season sits, so it should
already flag both ends - but that is reasoning about a detector rather than
checking it. `survey.dead_ends()` names the shape explicitly and
`tests/test_analytics_survey.py` drives the exact `qb_hurry` profile through
it, asserting both runs are recovered and the seven covered seasons counted.

**The answer for the NFL archive: no column in any of the four feeds has this
shape.** `python -m analytics.survey --dead-ends` returns zero for pbp,
stats_player_week, snap_counts and participation, and a test pins it. The one
candidate was `target_share`, and it was the NaN artifact in §2.10 rather than
the data.

### 2.6 Sparse — populated in fewer than five seasons

| column | covers |
|---|---|
| `end_yard_line` | **2001–2002 only**, empty in the other 26 |
| `st_play_type` | 2001–2002 only |
| `kickoff_fair_catch` | 2023 (rule change, real) |
| `punt_blocked`, `safety_player_id`, `lateral_return` | scattered |

`end_yard_line` is the case that broke the first version of the detector: scored
against its max it made 26 seasons the finding, when the finding is the two.

### 2.7 Two columns are dead everywhere

`lateral_sack_player_id` and `lateral_sack_player_name` have **zero non-null
rows in all 28 seasons**. Nothing else in the file is entirely empty.

### 2.8 Two columns change dtype across seasons

    goal_to_go           1999-2002 Float64  2003-2019 Int32  2020 Float64
                         2021-2023 Int32    2024-2026 Float64
    xyac_median_yardage  1999-2005 Float64  2006-2026 Int32

A multi-season scan must cast these or it will fail or silently upcast. No other
column moves.

---

## 3. What PBP cannot answer

Measured, not assumed.

**Who was on the field.** PBP names the passer, rusher, receiver, tacklers and
returner — the players who *touched* the ball. It does not name the other
twenty. `pbp_participation` does, and its limits are hard:

| | |
|---|---|
| Seasons on disk | **2016–2025**. 2026 is **absent** |
| Tier | offseason — refreshes only after the postseason, so it is never available for the current season |
| Schema | 20 columns 2016–2022, **26 columns 2023–2025** (adds `offense_names`, `offense_positions`, `offense_numbers` and defensive equivalents) |
| `offense_players` coverage | 0.91–0.92 for 2016–2022, **1.00** for 2023–2025 |
| `route` | ~18,200–19,600 rows/season in every era — pass plays only, ~40% |

There is also an **era trap** in `was_pressure`: 2016–2022 leave it NULL on run
plays (non-null on 19,107 of 48,434 rows in 2016); 2023–2025 fill it `False` on
every row (46,168 of 46,168 in 2023). A pressure rate over all rows reads 9.7%
for 2016 and 14.8% for 2023 while the per-dropback rate barely moves.

**Snaps.** Not in PBP at all. `snap_counts` starts at **2013** — the
`snap_counts_2012.parquet` file exists and has **0 rows**, so
`nflverse.DATASETS["snap_counts"].first_season = 2012` is off by one. CLAUDE.md
already records that "2012 was wrong"; this confirms it from the file itself.
*Routed to track A as a one-line correction; not fixed here, it is a shared
NFL code path.*

**Position and role.** PBP carries no position column of any kind — `posteam`
and friends are possession, not position. Every position, depth-chart role and
team-of-record comes from `players.parquet` / `roster_weekly`, and the usual
rule applies: never approximate a historical field from a current one.

**Anything charted.** Separation, route depth as run, time to throw before 2016,
coverage shell, blocking — none of it. Participation gives man/zone, coverage
type, pressure, box count and personnel for 2016–2025 offseason-only; NGS gives
the rest and is the next track F pass.

**Live weather.** `temp` and `wind` only, post-game, and at 38–66% coverage in
the 2020s (§2.5).

---

## 3a. QB passing is complete, all 28 seasons — *for track A*

Checked because track A is being asked whether Kalshi's `KXNFLPASSYDS` /
`KXNFLPASSTDS` are worth tracking, and the answer partly depends on whether the
facts side exists. It does, without a cliff anywhere:

| | min non-null share, worst season of 28 |
|---|---|
| `passer_player_id` | 0.389 |
| `passing_yards` | 0.215 (= its share of plays; complete on pass plays) |
| `pass_attempt`, `complete_pass`, `pass_touchdown`, `interception`, `sack` | 0.958 |

PBP-derived league totals match `stats_player_week` **exactly** in every season
checked, including the two where targets are zero:

    season   PBP passing yds / weekly    PBP pass TD / weekly
    1999       117,509 / 117,509            688 / 688
    2003       114,953 / 114,953            684 / 684
    2005       116,480 / 116,480            671 / 671
    2009       124,368 / 124,368            751 / 751
    2015       138,002 / 138,002            867 / 867
    2025       128,601 / 128,601            854 / 854

So if those series are ever logged, settlement and history need no new facts
work. This is the facts side only — it says nothing about market coverage,
subject counts or credit cost, which are track A's.

---

## 3b. Next Gen Stats — measured, not inherited

A note routed to this track attributed `was_pressure` and the offseason-tier
limits to NGS and concluded it should be treated as historical-only. **Those
limits belong to `pbp_participation`, which is a different release.** Measured
from the archive rather than reasoned from the participation result:

| | `pbp_participation` | **Next Gen Stats** |
|---|---|---|
| release | `pbp_participation` | `nextgen_stats` |
| tier in `nflverse.py` | OFFSEASON | **LIVE** |
| seasons on disk | 2016–2025 | **2016–2026** |
| current season | **absent** | **present — 2026 week 1, pulled 2026-09-17** |

So **NGS can serve a current-season page and participation cannot.** The
earlier conclusion does not carry across.

Three things about NGS that do matter, all measured:

- **It is a qualifying-threshold leaderboard, not a full feed.** NGS receiving
  carries 120–132 players a season against ~500 with at least one target in the
  play-by-play — **23.6% to 29.2% coverage**. It can differentiate the players
  it covers and it cannot be a spine for anything that needs a roster.
- **Week 0 is the season total, and it sums to the weeks.** Ja'Marr Chase 2025:
  the week-0 row reads 185 targets and weeks 1+ sum to exactly 185. A naive
  aggregation over all weeks double-counts every season by precisely 2×.

  **This is the silent-zero class's cousin, and it is worse in one way.** A zero
  cliff at least produces a figure a reader might find odd. A silent double
  survives every sanity check anyone would apply: the ratios are unchanged, the
  rankings are unchanged, the shares are unchanged, the correlations are
  unchanged, and only the magnitudes move — by a factor that looks like nothing
  in particular. The reconciliation that catches it is exactly the one that
  looks redundant: does the part sum to the whole, or *is* the whole sitting in
  among the parts. Tabled in CLAUDE.md as its own row.
- **`player_gsis_id` is on all three files**, so it joins to the spine with no
  crosswalk.

Not yet measured: whether the NGS columns add anything over the play-by-play.
That is the next track F pass, and it is now a question about value rather than
about availability.

---

## 4. What this means for the five analytics

| # | analytic | usable range | binding constraint |
|---|---|---|---|
| 1 | Game-script elasticity | **1999–2026** | none. Worst non-null share in any season: `down` 0.836, `play_type` 0.912, `score_differential` 0.942, `wp` 0.994, `ydstogo`/`series` 1.000. **Carries and receptions only** — a targets version starts 2009 |
| 2 | Neutral-situation pace | **1999–2026** | `drive` 0.987, `fixed_drive`/`series` 1.000, `play_type` 0.912 at worst. `no_huddle` cannot be a filter before ~2010 (§2.2) |
| 3 | Down-and-distance role | **touches 1999–2026; on-field 2016–2025** | the honest version needs participation, which has no 2026 and never will in-season. This one splits into two different analytics and must say which it is |
| 4 | Air-yard distribution | **2006–2026 by passer; 2009–2026 by receiver** | §2.1. 20 seasons, not 28, and the receiver arm is 18 |
| 5 | Usage stability | **1999–2026 for carries/receptions; 2009–2026 for targets** | as #1 |

Nothing in the five is blocked. Three of the five lose seasons, and #3 splits.

---

## 5. The gate

`analytics/gate.py` + `tests/test_analytics_gate.py`, 28 tests, landed before
any analytic. No analytic is published without an interval and a sample count.

For a column `S` the gate requires `S_lo`, `S_hi`, and `S_n` or `n`. It runs
over a schema spec and over an export payload, and a child object may not
inherit its parent's `n` — that is how a figure ends up quoting a sample it was
not computed on.

`analytics/intervals.py` is the other half: `Estimate` cannot be constructed
without a sample count, `wilson` for proportions, `block_bootstrap` over games
for anything whose rows share one, `student_t` for means over independent units.
`n` is **blocks, not rows**. The bootstrap guard duplicates every row 20× inside
its own game and asserts the interval does not narrow.

The first thing the gate did was change this survey's schema: `f_pbp_columns`
stores `rows`, `nonnull` and `informative` as counts rather than a `null_rate`,
because a stored rate would have been refused — correctly, since the name does
not say what the denominator is.

---

## 6. Files added

| file | what |
|---|---|
| `analytics/__init__.py` | scope and the structural rule |
| `analytics/paths.py` | archive and database resolution; `market_log_ro()` |
| `analytics/gate.py` | the publication gate |
| `analytics/intervals.py` | `Estimate`, Wilson, block bootstrap, Student-t |
| `analytics/survey.py` | the scan, the coverage table, the anomaly report |
| `tests/test_analytics_gate.py` | 28 tests |
| `tests/test_analytics_survey.py` | 10 tests; 2 skip without a scanned store |
| `docs/F01-pbp-survey.md` | this |

New files only. No shared NFL code path, `jobs/export_web.py` or
`contract.schema.json` touched.
