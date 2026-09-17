# C02 — the CFB play-by-play feeds, surveyed before anything is ingested

Reproduce: `python -m research.cfb_pbp_survey --download --scan --report --silent-zeros`
(GitHub release assets, unmetered, **0 credits**). Measured 2026-09-17 on 36 season
files, 5,522,755 plays, 2.0 GB cached under `cfb/cache/pbp_survey/` — outside the
manifested raw archive, because a survey copy is not an ingest.

Nothing here is ingested. No table of plays, no derived metric, no page.

The machinery is Track F's: `analytics.survey.scan_season` measures each parquet and
`profiles` / `reference` / `anomalies` / `silent_zeros` read the rows back. This survey
supplies only what is CFB-specific — which assets exist, where they are cached, and
that the rows land in `cfb.db`. Counts are stored, never rates, per Track F's rule.

## 0. There are four CFB play-by-play feeds, not one

`sportsdataverse-data` carries `cfbfastR_cfb_pbp` (54 assets), `espn_cfb_pbp` (73),
`ncaa_mfb_pbp` (46), `ncaa_mfb_pbp_cfbfastr` (46) and `espn_cfb_model_pbp` (48). The
two surveyed here are the two full-schema seasonal feeds. **`espn_cfb_pbp` reaches
2004, nine seasons deeper than the 2014 start the brief assumed.** The other two are
an order of magnitude smaller (0.4–0.8 GB total) and are not surveyed yet.

## 1. Seasons, rows, columns

| feed | seasons | plays | games/season | columns |
|---|---|---:|---|---:|
| `cfbfastR_cfb_pbp` | 2014–2026 | 2,454,929 | 850–1,657 | 362, unchanged every season |
| `espn_cfb_pbp` | 2004–2026 | 3,067,826 | 463–956 | 491 → 500, **changing in six seasons** |

Shared column names: 123. cfbfastR-only 239, ESPN-only 378 — these are different
schemas over the same games, not one feed in two builds.

2026 is in progress: 334 games (cfbfastR) and 179 (ESPN) at the pull.

## 2. THE COVERAGE CLIFFS

Two kinds of empty, Track F's distinction, and only one is visible to a null check:
`null` is loud (a mean skips it), `zero` is silent (a mean returns a wrong number).

- `cfbfastR_cfb_pbp`: **90 columns with a cliff, 0 silent-zero runs.** Every gap in
  this feed is null, which is the safe kind.
- `espn_cfb_pbp`: **124 columns with a cliff and 14 silent-zero runs.**

### 2a. The silent zeros — ESPN only, and one of them is `touchdown`

Non-null on **100%** of rows and exactly 0.000 for a run of seasons:

| column | zero seasons | reference |
|---|---|---:|
| `touchdown` | 2005–2013 | 0.039 |
| `xp_attempt`, `xp_made` | 2004–2013 | 0.038 / 0.037 |
| `firstD_by_penalty` | 2004–2013 | 0.016 |
| `kneel_down` | 2004–2013 | 0.006 |
| `int_td`, `is_blocked_fg_turnover`, `is_blocked_punt_turnover` | 2005–2013 | 0.001 |
| `qb_hurry` | 2004–2006 **and 2014–2020** | 0.010 |
| `td_check` | 2014–2024 | 0.038 |
| `end_state_missing` | 2007–2011 | 0.005 |
| `era`, `gameSpreadAvailable`, `kickoff_oob` | 2004–2006 / 2004–2005 / 2018–2019 | — |

`SELECT SUM(touchdown)` over ESPN 2005–2013 returns a number, the query succeeds, and
the number is zero. This is Track F's `qb_hit` defect on a column nobody would think
to check. `qb_hurry` is worse-shaped: it is zero at both ends of a populated middle.

### 2b. The loud nulls worth knowing

- `cfbfastR.wallclock`: null 2014–2017, 0.998 from 2018. No real-time ordering before 2018.
- `espn.air_yards` / `air_yardsToEndzone`: absent 2004–2020, ~0.004 in 2021, 0.011 in
  2025 and **0.15 → 0.33 mid-2026** — a charting feed arriving live, not a stable column.
- `espn.pass_direction` / `rush_direction`: populated 2007–2013, empty 2014–2024, back
  from 2025. Present-and-empty for eleven seasons in the middle of the archive.
- Never populated in any season: 10 columns in cfbfastR, 13 in ESPN. Peak under 5% of
  rows: 97 and 121. Usable columns: **255 and 367**.

### 2c. A column dying live, and the id column that survives it

`cfbfastR.rusher_player_name` — 0.37 of plays in 2024, **0.21 in 2025, 0.000 in 2026**
— while `rush_player` / `rush_player_id` hold flat at 0.35–0.36 throughout. Same shape:
`sack_player_name` and `sack_players` fall to 0.000 in 2026 while `sack_player` and
`sack_taken_player` hold. The text-parsed name columns are being retired; the id columns
are the survivors. **Any CFB usage metric keys on the id columns.** Track F's tackle
migration, one archive over.

`target_player` moves 0.02 (2024) → 0.10–0.11 (2025–26). CFB targets are attributable
on about a tenth of plays at best — the college analogue of nflverse's 2003–08 targets,
except it is the CURRENT seasons that are thin.

## 3. THE 2022 ROW JUMP IS SCOPE, NOT COVERAGE

cfbfastR plays per season go 160k (2014–2021) → 252k (2022) → 293k (2025), and games
887 → 1,459 → 1,657. It is **FCS games entering the feed**, not better FBS coverage:

| season | fbs/fbs | fbs/fcs | fcs/fcs | fcs/ii |
|---|---:|---:|---:|---:|
| 2019 | 773 | 114 | 0 | 0 |
| 2021 | 770 | 116 | 0 | 0 |
| 2022 | 776 | 120 | **519** | 23 |
| 2025 | 807 | 126 | **669** | 37 |

FBS-vs-FBS is flat at 770–807 for twelve seasons. A per-season play count, or any
league-wide rate computed across 2021→2022, changes by 60% for a reason that has
nothing to do with football. **Stratify on division or restrict to FBS; never pool.**

Every pbp `game_id` joins `cfb_games`: 887/887 (2019), 1,459/1,459 (2022), 1,657/1,657
(2025). The id space is ESPN's, the one the store is already keyed on.

## 4. What is usable, and for what

Flat and full across every cfbfastR season: `game_id`, `drive_id`, `period`,
`offense_play`, `home`, `away`, `play_type` (1.00), `down` (0.98), `distance` (0.99),
`EPA` (0.93–0.95), `yards_gained` (0.70 — it is 0 on plays with no gain, so the
informative share is not the coverage), `pass` (0.36) and `rush` (0.38–0.40).

So a drive/play-level league history from 2014 is supportable on cfbfastR, and from
2004 on ESPN **if** every silent-zero column above is excluded by name. Player-level
usage is supportable only through the id columns, and only where `target_player`
allows — which is not most plays.

## 5. What this survey does NOT answer

- The other two feeds (`ncaa_mfb_pbp`, `ncaa_mfb_pbp_cfbfastr`) are unsurveyed.
- No cross-feed reconciliation: whether ESPN and cfbfastR agree on a shared game's
  play count, EPA or scoring has not been measured.
- Nothing about the appearance gap changes. A play-by-play feed still does not say who
  dressed, so `cfb.stats_and_usage_only` stands exactly as written.

## 6. Mismatch to report to Track F

`analytics.survey.run_scan` enumerates seasons through the nflverse registry and the
pull-date mirror layout, so it cannot scan a feed that lives anywhere else. Everything
below it — `scan_season`, `profiles`, `reference`, `anomalies`, `silent_zeros`, the
formatters — reused unchanged, and did the work here. **Suggested generalisation:
`run_scan` takes an iterable of `(dataset, season, path, pull)` and a connection,
leaving enumeration to the caller.** Until then this module duplicates ~30 lines of
loop, which is the seam to watch. No analytics were computed here: when CFB analytics
are built they go through `analytics/gate.py` and `analytics/intervals.py`.
