# 002 — nflverse ingestion: mirror it, don't depend on it

## Why mirror rather than fetch on demand

Four reasons, in order of how badly each bites:

1. **Stat corrections rewrite history.** nflverse applies NFL stat corrections
   on Thursdays, retroactively changing already-published weeks. If you
   overwrite your copy, your history silently mutates — every backtest becomes
   unreproducible and you can no longer prove your Week 3 model used only Week 3
   information. This is invariant #5 and it is the real reason to mirror.
2. **The site cannot query GitHub release parquet at request time.** Obvious,
   but it means a serving layer, not just a cache.
3. **It's a volunteer project.** Schemas change, files get renamed, releases
   move. Fine for research, unacceptable as a hard runtime dependency for a
   product.
4. Bandwidth and rate limits.

Cost is not a consideration: the entire historical corpus is well under 1 GB
(pbp ~20 MB/season, participation ~4.6 MB, FTN ~0.6 MB, weekly stats ~0.7 MB,
snaps ~0.25 MB). Weekly in-season snapshots add ~25 MB. Market data is the
storage problem; nflverse is a rounding error.

## Three layers, distinct jobs

**Raw archive** — dated, immutable copies of exactly what nflverse published,
partitioned by pull date. Never overwritten. This is what makes a backtest
reproducible and lets you re-derive everything after a parser fix.

**Normalized store** — the queryable tables the model reads. Every row carries
`source`, `ingested_ts` and `data_version` (the pull date it came from), so
"what did we know on 2026-09-14?" is answerable with a WHERE clause.

**Serving layer** — precomputed aggregates for the site. The site must never run
a heavy query at request time.

## Tiers — the difference is load-bearing

**Live tier** (usable on a Sunday):
- play-by-play — nightly, ~15 min post-game
- snap counts (PFR) — 4x/day
- FTN charting — 4x/day (`is_motion`, `is_play_action`, `is_rpo`, `n_blitzers`,
  `is_drop`, `is_contested_ball`, `n_defense_box`)
- Next Gen Stats — nightly
- depth charts, rosters, schedules — daily

**Offseason tier** (backtest and research only):
- participation — refreshes ONLY after the postseason. Carries `route`,
  `defense_man_zone_type`, `defense_coverage_type`, `was_pressure`,
  `offense_players`

## Contract

Each job writes normalized rows carrying `sport, entity_id, event_ts, source,
ingested_ts, data_version`, and updates `source_health`. Player identity
resolves to nflverse `gsis_id` **at ingest**, never in analysis code.

Feature code must be able to ask which tier a field belongs to. Reading an
offseason-tier field in a live-tier context must raise, not return nulls —
otherwise you build features in April that are silently empty in October.

## Invariants
#2 raw first · #5 as-of · #7 sport discriminator

## Acceptance
- `python -m jobs.ingest_nflverse --season 2024` is idempotent: two runs
  produce identical row counts in the normalized store
- `--week 3` touches only week 3
- Two pulls of the same table on different dates produce two raw archive
  entries and are both recoverable
- A stat-correction test: re-ingest a week whose upstream values changed, and
  show the prior `data_version` is still queryable and unchanged
- Every job writes a `source_health` row on both success and failure
- Requesting a participation-derived field in live-tier context raises

## Out of scope
Feature engineering, the model, injuries (separate brief), PFF, the site.

---

## Built 2026-09-09

`nflverse.py` (registry + tiers) · `jobs/ingest_nflverse.py` · `queries.py` ·
`store.archive_file()` + `nflverse_versions` / `nfl_player_week` / `nfl_games` /
`nfl_snap_counts`.

Corrections to the file locations, all verified live:

- **weekly stats live in the `stats_player` release**, not `player_stats` -
  that one froze on 2025-05-07 with no 2025/2026 assets.
- **the missing-2019 file was an artifact of that frozen release.** No special
  case is needed on `stats_player`.
- schedules come from `schedules/games.parquet` rather than the raw github CSV:
  identical content, but a release asset goes through the same archive path.
- `contracts/historical_contracts.parquet` confirmed; `contracts.parquet` 404s.

Full backfill 1999-2026: 139 assets, **592MB**, 807,788 normalized rows
(475,629 player-weeks over 11,371 players; 7,548 games, 7,376 with closing
lines; 324,611 snap-count rows). Ran against the live database with the logger
up and took zero poll failures.

A new dated copy is written only when the bytes change, so the corpus stays at
that size and a stat correction is a new `data_version` rather than an
overwrite. The mirror is exempt from R2 rotation (`RAW_ROTATE_EXEMPT`).

The acceptance query:

    python -m queries --player "Amon-Ra St. Brown" --stat receptions --threshold 5
    python -m queries --player 00-0036355 --stat receptions --threshold 5 --as-of 2026-09-14

## Still open

- pbp, NGS, FTN and participation are archived and versioned but not flattened
  into SQLite - read them with polars off `data/raw/nflverse/`. Normalizing pbp
  is a deliberate deferral, not an oversight.
- The 2026 season had no pbp/snaps/FTN/weekly-stats asset when this was built
  (the opener was the following day). The first real live-tier run happens once
  nflverse publishes week 1.
- Serving layer (precomputed aggregates for the site) is not built.
