# TRACK C handoff — CFB ingest (W07)

Written 2026-09-17 ~05:45Z, before a context clear. **A fresh session needs no other
source than this file** (plus `CLAUDE.md`, which it must read anyway). Every figure
here was read from the store or the docs at the time of writing, not remembered.

Reports open with: `TRACK C · cs-cfb · C:\Users\Ethan Davis\code\cs-cfb`

---

## 0. Act on these first

1. **RESOLVED 2026-09-17 ~17:00Z: `D:\calibrated-sports\data\cfb\raw_archive\` holds all 115
   probe shards** (57 cfb_kalshi, 57 cfb_polymarket, 1 cfb_cfbd; 15,957,864,233 bytes), and all
   115 sha256 hashes match the logger's raw tree, compared file by file. Rotation to R2 from 2026-09-18
   00:00Z no longer leaves the store without a local copy. Do not touch the logger or its raw tree.
2. **RESOLVED 2026-09-17: `ODDS_API_KEY` is in `cs-cfb\.env`** (identical to the main clone's), and
   `ODDS_RESERVE=20000` was added, copied from the main clone. `cfb/oddsapi.py` refuses when
   `ODDS_RESERVE` is unset, because `config.ODDS_RESERVE` defaults to 40 (the NFL fallback).
3. **Nothing in phase 3 may spend Odds API credits except the approved forward bulk game-line
   capture (~3 credits a slate).** Historical pulls and prop probes wait for Ethan to approve a
   costed number.

---

## 1. Environment

| Item | Value |
|---|---|
| Clone | `C:\Users\Ethan Davis\code\cs-cfb` (W07 said `C:\cs-cfb`; this is where it is) — remote `git@github.com:edavis9817/calibrated-sports.git`, **public** |
| Venv | `.venv`, Python 3.12.10 (`py -3.12 -m venv .venv`, `pip install -r requirements.txt pytest`). Stays on C: until the next rebuild for another reason; then recreate on D: (Windows venvs hardcode paths, so it is a rebuild, not a move) |
| `.env` (gitignored) | `LOGGER_DB=D:/calibrated-sports/data/_track_c_unused.db` (throwaway), `LOGGER_RAW_DIR=D:/calibrated-sports/data/_track_c_unused_raw` (throwaway, a sibling of the live `raw\`), `CFBD_API_KEY` (added by Ethan, same key as the main clone), `ODDS_API_KEY` (added by Ethan, same key), `ODDS_RESERVE=20000` (added 2026-09-17, copies the main clone) |
| `STORAGE_DIR` | `D:\calibrated-sports\data` (derived from `LOGGER_DB`); every CFB path comes from `config.storage_path()` via `cfb/paths.py` |
| Temp | `%TEMP%` is `D:\temp` for Ethan's user. This session's shell still had the old value, so every command was run with `TEMP='D:\temp' TMP='D:\temp'`. pip cache `D:\caches\pip`, npm `D:\caches\npm` |
| Tests | `TEMP='D:\temp' TMP='D:\temp' .venv/Scripts/python.exe -m pytest -q -p no:cacheprovider` → **853 passed, 1 failed** before the last commit; the failure is Track A's (§8). CFB tests: `tests/test_ingest_cfb.py`, `tests/test_ingest_cfb_cfbd.py`, `tests/test_probe_promote.py` (77) |
| Never | open `market_log.db` read-write (open with `mode=ro` only); write `cfb_probe.db` (read-only, `mode=ro`); write under the logger's `D:\calibrated-sports\data\raw`; edit `jobs/export_web.py`, `contract.schema.json` or any shared NFL code path |

## 2. Commits (all pushed to `origin/main`)

| Commit | What |
|---|---|
| `28ed16e` | Phase 1: sportsdataverse facts ingest |
| `6f6e01a` | Phase 2: CFBD game lines and results under a shared-quota budget |
| `2425f93` | Canonical provider names, one scope limitation, `--cfbd-week latest`, `cfb_runs` temp logging |
| `5d27d53` | Probe promotion, per-game results allowed / aggregates guarded, `run_weekly_cfb.cmd`, `--log` |
| *(this commit)* | Closing-line correction to `cfb.stats_and_usage_only`, source-layering decision, this handoff |

## 3. What is in `cfb.db` (`D:\calibrated-sports\data\cfb.db`, 692 MB)

Every fact table is versioned per row by ingestion time: `valid_from_ts` / `valid_to_ts`,
plus `src_dataset`, `src_season`, `src_part`, `src_file_id` (→ `cfb_raw_files.file_id`),
`row_sha` (16 hex), `sport='cfb'`. Current view = `valid_to_ts IS NULL`.

| Table | Current rows | All versions | Seasons | Source |
|---|---:|---:|---|---|
| `cfb_games` | 49,115 | 49,115 | 2001–2026 | sportsdataverse `cfb_schedules` (CFBD+ESPN; division & conference per game) |
| `cfb_teams` | 17,939 | 17,939 | 2001–2026 | `espn_cfb_teams`, keyed (season, team_id) |
| `cfb_rosters` | 372,638 | 372,638 | 2004–2026 | `espn_cfb_rosters` |
| `cfb_game_rosters` | 2,892,630 | 2,892,630 | 2004–2026 | `espn_cfb_game_rosters` (starter flag; `did_not_play` deliberately NOT stored) |
| `cfb_player_game_box` | 645,777 | 645,777 | 2004–2026 | `espn_cfb_player_box`, one row per (game, athlete), categories merged |
| `cfb_player_game_usage` | 376,036 | 376,036 | 2004–2026 | `espn_cfb_adv_player_usage` (targets, rushes, touches, team denominators) |
| `cfb_player_xwalk` | 16,559 | 16,559 | — | nflverse `players.parquet`: ESPN athlete id → gsis_id |
| `cfb_game_lines` | 38,532 | 38,532 | 2013–2026 | CFBD `/lines`: 2013–2025 season scope (`src_part='both'`) + 2026 week 2 (`regular:w2`) |
| `cfb_cfbd_games` | 303 | 303 | 2026 | CFBD `/games` 2026 week 2 |
| `cfb_exchange_markets` | 47,553 | 49,223 | 2026 | probe promotion (1,670 rows re-versioned when the join moved to the full schedule) |
| `cfb_exchange_closes` | 33,461 | 33,461 | 2026 | probe promotion: last quote strictly before kickoff |

Control tables: `cfb_raw_files` 162 (manifest), `cfb_fetch_checks` 167, `cfb_http_log` 180,
`cfb_parse_log` 333, `cfb_measurements` 1,061, `cfb_limitations` 7, `cfb_runs` 5,
`cfbd_requests` 25.

## 4. What is in `cfb/raw` (`D:\calibrated-sports\data\cfb\raw`, 187 MB, 160 files)

| Source (repo / tag) | Files | Bytes |
|---|---:|---:|
| sportsdataverse `cfb_schedules` | 26 | 1.5 MB |
| sportsdataverse `espn_cfb_teams` | 26 | 3.2 MB |
| sportsdataverse `espn_cfb_rosters` | 23 | 37.8 MB |
| sportsdataverse `espn_cfb_game_rosters` | 23 | 121.8 MB |
| sportsdataverse `espn_cfb_player_box` | 23 | 10.5 MB |
| sportsdataverse `espn_cfb_adv_player_usage` | 23 | 16.4 MB |
| nflverse `players` | 1 | 3.4 MB |
| CFBD `lines` (gzipped JSON) | 14 | 11.2 MB (uncompressed bytes in manifest) |
| CFBD `games` | 1 | 0.2 MB |
| **External** (not under raw): `local:cfb_probe.db` markets + closes | 2 manifest rows | the 23.9 GB probe, read-only |

Layout: `raw/sportsdataverse/<tag>/<asset-stem>/<UTCfetch>-<content12>.parquet`,
`raw/cfbd/<endpoint>/<season>/<part>/<UTCfetch>-<content12>.json.gz`. `--audit` checks
manifest ↔ disk, parquet/JSON readability, and opens external sources read-only: last run
160/160, 0 unregistered, 0 missing, 0 unreadable, 2 external readable.

Other D: CFB paths: `cfb/cache` (in-flight `.part`, audit downloads, docs), `cfb/checkpoints`
(`ingest_cfb.lock`, `env.before-track-c.bak`), `cfb/logs` (`ingest_cfb.log` from `--log`,
backfill/rebuild/probe logs).

## 5. The job

```
python -m jobs.ingest_cfb                                   # status, 0 requests
python -m jobs.ingest_cfb --fetch --season 2026             # sportsdataverse, free
python -m jobs.ingest_cfb --parse | --rebuild [--dataset X] [--season S]   # replay archive, 0 requests
python -m jobs.ingest_cfb --audit
python -m jobs.ingest_cfb --cfbd-status                     # ledger + /info (unmetered)
python -m jobs.ingest_cfb --cfbd-lines 2013-2025            # 1 metered request per season
python -m jobs.ingest_cfb --cfbd-week 2026:3 | latest        # 2 metered requests
python -m jobs.ingest_cfb --promote-probe                   # probe -> cfb.db, 0 requests
python -m jobs.ingest_cfb --odds-free                       # Odds API /sports + NCAAF /events, 0 credits (verified per call)
python -m research.cfb_odds_coverage                        # events -> cfb_games join, coverage, costs; 0 requests
python -m jobs.ingest_cfb --odds-p1 --lock-wait 900         # P1: 1 credit/event, 74 LIFETIME, not before 2026-09-19 12:00Z
python -m jobs.ingest_cfb --odds-forward                    # one tick: 3 credits per kickoff hour, 45 per CFB week
python -m jobs.ingest_cfb --odds-week                       # this week's kickoff hours and what was captured, 0 requests
python -m jobs.ingest_cfb --odds-reparse                    # archive -> cfb_odds_* observation tables, 0 requests
run_cfb_job.cmd <flags>                                     # scheduler wrapper: cd to the repo, adds --log
python -m jobs.ingest_cfb ... --log                         # append output + exit code to cfb/logs/ingest_cfb.log
python -m research.cfb_sources_audit                        # reproduces every quoted source figure
```

Exit codes: 0 ok · 1 audit failure or crash · 2 another instance holds the lock · 3 CFBD
refused on budget (facts already done) · 4 a CFBD file refused at parse · 5 Odds API stopped
(non-200, unset reserve/key, or a documented-free call that the server billed).

Code: `cfb/{paths,sources,schema,normalize,versioning,fetch,lock,limitations,cfbd,cfbd_normalize,probe_promote,guards}.py`, `jobs/ingest_cfb.py`, `run_weekly_cfb.cmd`.

## 6. CFBD quota

- **Position:** server `remainingCalls` **979 of 1,000** for 2026-09 (resets 2026-10-01T00:00Z)
  at the last call (05:23Z). Ethan's note said 981 — that was before the last wrapper
  verification run, which spent 2. This job has spent **19** metered calls this month
  (13 lines backfill + 3×2 week-2 runs); **2** more were spent elsewhere (the probe era).
- **Ledger:** `cfb.db` table `cfbd_requests` — every call including `/info` and failures, with
  `metered`, `status`, `remaining`, `file_id`, `run_id`, `origin`
  (`EthanPC|C:\Users\Ethan Davis\code\cs-cfb|jobs.ingest_cfb`). `--cfbd-status` prints the
  server's `usedCalls` minus this job's calls as spend made elsewhere (the main clone, CBBD,
  the docs playground — the quota is shared with CBBD per collegefootballdata.com/terms).
- **The three brakes** (`cfb/cfbd.py`, `jobs/ingest_cfb.run_cfbd`):
  1. **`/info` pre-check before any metered call** (unmetered per CFBD's server code). A failed
     `/info` refuses the run — an unknown quota is not an infinite one. A plan that would leave
     fewer than `config.CFBD_RESERVE` (**100**) calls is refused before spending.
  2. **20 metered requests per run** (`MAX_REQUESTS_PER_RUN`); `--max-requests` can lower it,
     never raise it. The plan is built first and is finite.
  3. **`X-CallLimit-Remaining` re-read after every call; the run stops at the floor**, so a
     shared quota drained mid-run by another client stops it (tested).
  Also: only year/week-level `/games` and `/lines` URLs can be built — `gameId`, `id`, `team`
  raise. Non-2xx stops the run (CFBD refunds non-2xx).
- The old `jobs/ingest_cfbd.py` writes to `cfb_probe.db` and backs the published C01 record.
  Do not use it for new spend; do not modify it.

## 7. Limitations recorded (`cfb_limitations`, rewritten on every run; retired ids deleted)

Each cites `cfb_measurements` keys; figures below are the store's values at writing.

1. **`cfb.stats_and_usage_only`** (structural) — *College football supports statistics, usage
   and per-game results - not aggregate hit rates or settlement, and no book closing line.*
   - Why: no source records whether a college player appeared. `did_not_play` True on **0**
     rows across all 23 seasons measured; starter flags exist only from 2025 (**802 of 1,890**
     team-games have a full lineup in 2025, **106 of 156** so far in 2026, **0** in 2004–2024);
     CFBD has no snap/participation field; ESPN box lists only players with a stat; PFF sells
     snaps. A no-row game = did not dress OR played and recorded nothing.
   - Per-game display permitted: posted line, actual stat, cleared/missed for that game, only
     where a stat row exists; a game with no row shows as no record, never as missed.
   - Aggregate hit rates banned because the error direction depends on an untestable choice:
     missing-as-zero skews under; missing-dropped skews over.
   - No settlement.
   - **Closing line (corrected 2026-09-17):** no close from CFBD book lines, which carry no
     timestamps. Real exchange closes DO exist for the probe weekend (Kalshi **119** games,
     Polymarket **129**, median **56s**, max **310s** before kickoff) — exchange probabilities,
     never presented as a book line.
   - Enforced by `cfb.guards.scope_violations(schema.TABLES) == []` (aggregate-rate names and
     settlement/appearance names banned everywhere; cleared/missed columns only in tables keyed
     on `game_id`), with tests that the guard catches each banned shape.
2. **`cfb.exchange_probe_capture`** (provenance) — one weekend, non-production cadence.
   Game-market poll gap median **42.1s** Kalshi (p90 60.7s), **52.7s** Polymarket (p90 60.6s);
   game polls/hour **55.9 → 115.7** (Kalshi) after the second process started 2026-09-11 16:00Z;
   close age median 56s / max 310s; **987** Polymarket closes are empty 0/1 books (labelled
   `empty_0_1`, not priced); the probe's `sport` column says `nfl` on all 47,553 markets.
   Exchange probability ≠ book line: no spread without a scoring-distribution assumption,
   coverage skews to marquee games.
3. **`cfb.no_espn_betting_lines`** (provenance) — `espn_cfb_betting` is denied by name AND tag in
   `cfb/sources.py`: all **6,411** `odds_source='default'` rows carry spread 2.5 (**6,395** with
   total 55.5) — **6,276 of 6,277** games 2004–2011 and 0–20 a season since. Historical lines
   come from CFBD (2013+). `research/cfb_sources_audit.py` reproduces the counts.
4. **`cfb.lines_coverage`** (coverage) — 2013–2017 carry only consensus, numberfire,
   teamrankings; retail books from 2018. Opening spread present on **8,370 of 38,532** rows
   (78% absent), moneyline on **7,842** (80% absent). DraftKings arrives as two CFBD feeds
   ("DraftKings", "Draft Kings"): **176** games carry both; values disagree on **30** spreads and
   **24** totals; the fuller feed wins per game (`provider_raw` records which).
5. **`cfb.no_targets_in_box_score`** (coverage) — ESPN's receiving box has no targets; targets
   come from the usage file (play-by-play). Usage covers **463 of 699** box-score games in 2004,
   **956 of 958** in 2025. Targets thrown to unidentified players stay in `team_targets` and no
   player row: **2,530 of 44,993** in 2011 (~5%/season 2004–2014), **96 of 49,057** in 2019,
   **185 of 6,628** in 2026 so far.
6. **`cfb.team_identity_gaps`** (coverage) — the teams file misses ids its games reference:
   **2005: 64 of 298**, 2007: 15 of 320, 2023: 6 of 704, 2004: 5 of 289, 2026: 5 of 716.
7. **`cfb.current_season_rosters_partial`** (coverage) — 2026 roster file held **476** players on
   **4** teams (vs 26,327 on 234 for 2025) when fetched; it fills in upstream.

## 8. Items routed to Track A (and Track D)

| Item | Status |
|---|---|
| `tests/test_model_equivalence.py::test_all_three_versions_resolve_to_the_one_with_predictions` skips only when `LOGGER_DB` is unset, so a throwaway `LOGGER_DB` makes it FAIL | **Open** (Track A) |
| R2 rotation and `%TEMP%`: `r2.upload` uses `upload_file` on the shard in place; `r2.verify` streams `get_object` in 1 MB chunks into sha256; the only temp write is `rotate_raw --check`'s ~40-byte preflight; `r2.fetch` writes beside its destination and only tests call it. Both clones' `r2.py`/`rotate_raw.py` identical ignoring CRLF | **Closed** — rotation is not the 24.8 GB. Still unattributed; `cfb_runs` logs temp size per CFB run (all runs so far: no change, 0.01–0.07 GB) |
| "Logger restarted at 05:10Z" | **Withdrawn** — no restart. The 05:10:23Z banner (build `6c3d27edb126`, pid 2928) is the running process's own 2026-09-15 start: two midnight rollovers follow it in `logger.log` (lines 51,375 and 54,172), it matches pids 18988/2928 CreationDate 2026-09-15 01:10:23 EDT, no later banner. 18988 is the venv launcher, 2928 its interpreter child — one logger. Lesson: log lines carry no date |
| Task "CalibratedSports Weekly Refresh" returned **1** at 2026-09-16 09:00; a later run (18:45) completed | **Open, uninvestigated** (Track A/D) |
| CFB probe shards rotating to R2 from 2026-09-18 | **Accepted by Ethan** (no logger restart without the single-instance guard Track A is building). `raw_archive` copy not yet on disk — see §0 |
| Contract findings for a CFB manifest: conference per season and per game, a division level (fbs/fcs/ii/iii), variable game counts, no appearance signal, limitations needing a home the About/Sources page can render | **Open** (Track A owns the contract; Track C proposes, never edits) |

## 9. Weekly refresh — command line and the schtasks one-liner

`run_weekly_cfb.cmd` (committed, repo root):
```
@echo off
setlocal
cd /d "%~dp0"
".venv\Scripts\python.exe" -m jobs.ingest_cfb --fetch --season 2026 --cfbd-week latest --log
exit /b %ERRORLEVEL%
```
Verified end to end 2026-09-17 05:23Z: exit 0, resolved 2026 regular week 2, 2 CFBD calls
(content unchanged), log written. Each run costs ~2 CFBD calls and 7 GitHub API listings.
`--season 2026` and `CURRENT_SEASON` in `cfb/sources.py` both change before the 2027 season.

**schtasks, for an elevated Windows PowerShell — one line.** `--%` stops PowerShell parsing so
schtasks receives the `\"` escapes verbatim; `/RP *` prompts for the password and stores it so
the task runs whether or not Ethan is logged on:
```
schtasks.exe --% /Create /F /TN "CalibratedSports CFB Weekly Refresh" /SC WEEKLY /D TUE /ST 09:00 /TR "\"C:\Users\Ethan Davis\code\cs-cfb\run_weekly_cfb.cmd\"" /RU "ETHANPC\Ethan Davis" /RP * /RL HIGHEST
```
Logged-on-only variant (matches the NFL tasks): replace `/RP *` with `/IT`.

**State 2026-09-17 ~17:00Z:** Ethan created task `\CalibratedSports CFB Weekly` → `run_weekly_cfb.cmd`,
next run Tue 2026-09-22 09:00, never yet fired by the scheduler (LastTaskResult 267011). The wrapper
has an **uncommitted local edit** (not Track C's): it hardcodes the C: checkout, sets
`PYTHONIOENCODING=utf-8`, and redirects to `cfb\logs\weekly_cfb.log` INSTEAD of `--log`, so that log
has no `===== exit N` line; the exit code is only in the task's LastTaskResult. A manual run at
16:36Z exited 0 (CFBD remaining 977).
The `/TR` quoting was round-tripped: a throwaway non-elevated task created with exactly that
`/TR` stored the command as `"C:\Users\Ethan Davis\code\cs-cfb\run_weekly_cfb.cmd"` with a
Tuesday 09:00 trigger, and was deleted (confirmed). After creating the real task:
`schtasks /Run /TN "CalibratedSports CFB Weekly Refresh"`, then read the tail of
`D:\calibrated-sports\data\cfb\logs\ingest_cfb.log` (`===== <UTC> exit 0`).

## 10. Phase 3 — plan as it stands (nothing about it had reached disk before this file)

**Source layering (Ethan, settled — layers, not substitutes):**
- **CFBD** is kept for **2013–2019**, which the Odds API historical archive (from 2020-06-06)
  cannot reach at any price, and as the **free layer for 2020–2025**: buying those seasons at
  10× would be the most expensive way to add a timestamp to numbers already held.
- **The Odds API is the forward source** — bulk game lines, ~3 credits a slate, timestamped.
  **Ethan said start that capture now** (approved; blocked on `ODDS_API_KEY`).
- **Kalshi / Polymarket are exchange probabilities, not posted lines.** Free to poll, real
  timestamps, a different object: no spread without assuming a scoring distribution; coverage
  skews to marquee games. Never present them as a book line.
- On the site, CFBD lines are labelled **book consensus without a capture time** (Track B copy).
- Timestamped 2020–2025 history is bought **only against a specific pre-registered question**.

**Step 1 — probe promotion. DONE** (`5d27d53`). See §3 and §7.2.
- Promoted: `cfb_exchange_markets` (all 47,553; Kalshi 15,563 matched to a game, Polymarket
  20,201) and `cfb_exchange_closes` (33,461; last quote strictly before kickoff; **0** quotes on a
  kickoff second, so C01's "at or before" gives the same set). Kalshi book states at the close:
  13,040 two-sided, 486 ask-only, 121 bid-only; Polymarket 18,730 two-sided, 31 ask-only,
  66 bid-only, 987 empty 0/1.
- Left in `cfb_probe.db`: the 50.6M quote rows, all post-kickoff quotes, `poll_log`, its
  `raw_shards` manifest and `cfbd_games`; raw shards in the logger's raw tree (rotating, §0).
- Unmatched: Kalshi 8,534 non-game markets, 1,249 with no two-legged game-winner market naming
  the teams, 20 no game on date±1; Polymarket 1,940 season/futures, 12 no "A vs. B" title,
  34 no game on date±1 — some because C01's normaliser turns "St." into "state"
  ("St. Thomas" → "state thomas"). The normaliser is copied verbatim from
  `research/cfb_calibration.py` and a test keeps the copies identical; fix both or neither.

**Step 2 — coverage measurement. FREE PART DONE 2026-09-17 16:44Z.**
- Code: `cfb/oddsapi.py` (free endpoints only; a paid URL cannot be built), `oddsapi_requests`
  ledger in `cfb.db`, `--odds-free`, `research/cfb_odds_coverage.py`, tests
  `tests/test_oddsapi_cfb.py` (charge guard mutation-checked) and `tests/test_cfb_odds_coverage.py`.
- Ledger: `/sports` and `/events` both **x-requests-last 0**, remaining **44,546**, used 55,454 →
  **24,546 spendable above the 20,000 reserve**. Raw files 167, 168. NCAAF active.
- **88 events listed; 88 of 88 join `cfb_games`** after 6 explicit aliases (UMass, Southeastern
  Louisiana, Appalachian State, Nicholls State, Sam Houston State, Southern Mississippi), each checked
  against the schedule (same kickoff to the minute, same opponent). Kickoff agrees exactly on 86; one is
  30 min off (Oregon @ USC); one is 720 min off, a TBD placeholder (`start_time_tbd=1`), not a bad match.
- **2026 week 3: 74 listed of 311 scheduled — FBS/FBS 57 of 57, FBS/FCS 17 of 18** (missing: Wagner @
  California), **0 of 44 FCS/FCS, 0 of the D-II/D-III games.** Week 4: 14 listed, which only shows how
  far ahead `/events` lists (latest listed kickoff 09-26 23:45Z), not coverage.
- Week 3 as listed: **N = 74 events, 25 distinct 5-minute kickoff slots, 15 kickoff hours.**
- Still unknown: which PROP keys any book hangs on a CFB game. That needs P1.
- What the docs say (fetched 2026-09-17 into `cfb/cache/oddsapi_docs/`, quoted):
  - `GET /v4/sports` — "This endpoint does not count against the usage quota."
  - `GET /v4/sports/{sport}/events` — "Returns a list of in-play and pre-match events … Odds are
    not included in the response. This endpoint does not count against the usage quota."
  - `GET /v4/sports/{sport}/events/{eventId}/markets` — "A call to this endpoint costs 1 usage
    credit." It "only returns recently seen market keys for each bookmaker - it is not a
    comprehensive list … As an event's commence time approaches, this endpoint will return more
    market keys."
  - Rate limit: 429 with "try spacing out requests over several seconds"; no number published.
- **Consequence: the free endpoints cannot say which prop markets were listed on LAST weekend's
  games** — `/events` has no past games and no markets. Ethan's "coverage is partial" is
  therefore neither confirmed nor refuted yet.
- **The free part to run now (0 credits):**
  1. `GET /v4/sports?apiKey=…` — confirm `americanfootball_ncaaf` active; record
     `x-requests-remaining` and check `x-requests-last` is 0.
  2. `GET /v4/sports/americanfootball_ncaaf/events?apiKey=…` — count this weekend's events
     (2026 week 3), join to `cfb_games` by date and team names (the Odds API writes full names such as
     "Alabama Crimson Tide"; `cfb_teams.display_name` uses a similar school + mascot form -
     UNVERIFIED, measure the join rate and list every miss before trusting it), and report the
     count by division. This replaces the 120-game proxy in step 3.
  Archive both responses raw first; log calls with origin; spend 0.
- **The paid coverage probes, awaiting Ethan's approval:**
  - **P1 (recommended), this weekend:** `/events/{id}/markets` for every NCAAF event = **1 credit
    per event — 74 as listed 2026-09-17** (the proxy said ~120), run Saturday morning. Answers which prop
    keys each book lists per game.
  - **P2, last weekend:** historical events list (1 credit) + historical event odds on k sampled
    games requesting all 34 prop keys, ≤ 340 credits each; k = 2 → **≤ 681**.

**Step 3 — cost of the historical pull. OWED TO ETHAN; spend nothing until a number is approved.**
- Mechanics (docs, quoted):
  - Historical odds (bulk, featured markets): "cost = 10 x [number of markets specified] x
    [number of regions specified]" — **one call covers every event in the snapshot**. "available
    from June 6th 2020 … From September 2022, historical odds snapshots are available at 5 minute
    intervals."
  - Historical event odds (the only route to props): "cost = 10 x [number of unique markets
    returned] x [number of regions specified]" — **per event, per snapshot; billed on markets
    RETURNED**, so every prop figure is an upper bound. Props, alternates and period markets only
    "after 2023-05-03T05:30:00Z".
  - Historical events listing: "costs 1 from usage quota. If no events are found, it will not
    cost."
  - Live (non-historical) bulk odds: "cost = [number of markets specified] x [number of regions
    specified]" → h2h,spreads,totals × us = **3**.
  - The docs list 34 prop keys under one "NFL, NCAAF, CFL Player Props" heading, and "Coverage of
    player props is mainly limited to US sports and US bookmakers" — no per-sport breakdown.
- Inputs measured from the store (proxy until step 2's free count): last weekend (2026 week 2)
  **120 games with book lines** (fbs/fbs 49, fbs/fcs 37, fcs/fcs 34; DraftKings 120, Bovada 87),
  in **31 distinct 5-minute kickoff slots** (15 kickoff hours). Kalshi-covered: 119 of the same
  games. Region `us`.
- **"NFL settings" as actually configured** (main clone `.env`): 16 prop/period markets
  (`player_receptions, player_rush_attempts, player_pass_attempts, player_reception_yds,
  player_rush_yds, player_pass_yds, player_pass_tds, player_anytime_td, player_tackles_assists,
  player_receptions_alternate, player_reception_yds_alternate, player_rush_yds_alternate,
  player_pass_yds_alternate, spreads_h1, totals_h1, team_totals_h1`) × 10 snapshots
  (`10080,4320,1440,720,360,180,90,45,20,5` min). Historical at those settings:
  **one close snapshot per game = 10 × 16 × 120 ≤ 19,200** — Ethan's 10–20k per FBS Saturday is
  **confirmed as an upper bound**; the full 10-snapshot schedule ≤ **192,000** (impossible).
- **The three options** (against **24,564** spendable above reserve; one close snapshot):

  | Option | Scope | Credits | Share |
  |---|---|---:|---:|
  | **A** | Timestamped game lines (h2h, spreads, totals) for all 120 games: 31 bulk snapshots × 30 | **930** (exact) | 3.8% |
  | **B** | A + 5 usage props (receptions, rush attempts, pass attempts, reception yds, rush yds) × 120 games, + ≤31 event listings | **≤ 6,961** | 28.3% |
  | **C** | 119 Kalshi-covered games × the 16 NFL-setting markets, + ≤30 listings | **≤ 19,070** | 77.6% |
  | kill | 119 games × all 34 prop keys | 40,460 | 165% |

  **Recomputed on the measured week-3 listing** (N = 74, 25 slots, 24,546 spendable;
  `python -m research.cfb_odds_coverage` prints it): P1 **74** · forward one bulk snapshot **3** ·
  forward per kickoff hour **45** · A **750** (3.1%) · B **≤ 4,475** (18.2%) · C **≤ 11,865** (48.3%) ·
  all 34 prop keys **≤ 25,160** (102.5%, more than is spendable).
  Recommendation given: run P1 (74) first to learn the returned-market count, then B.
  Note the source-layering rule now also applies: a historical pull for 2020–2025 needs a
  pre-registered question; last weekend (2026) is forward-season data.
- **P1 APPROVED 2026-09-17 (74 credits, Saturday morning). BUILT, not yet run.**
  `--odds-p1`: free `/events` refresh, then `/events/{id}/markets?regions=us` for every PRE-MATCH
  event, FBS/FBS first, then FBS/FCS, then the rest, then unjoined. The 74 is a LIFETIME total for
  `purpose='p1'` in `oddsapi_requests`; a rerun resumes and never exceeds it. Refuses before
  `P1_NOT_BEFORE` (2026-09-19T12:00Z). Every response kept verbatim (no content dedupe; a
  non-200 body is archived as `oddsapi_event_markets_error`), parsed to `cfb_odds_event_markets`.
  Stops at the first non-200 or a billed cost above 1.
- **B vs C is NOT to be proposed yet.** It waits on (1) P1's returned-market counts and (2) Track A's
  reconciliation of the NFL credit discrepancy: 296 measured over seven days against a config that
  implies 2,000+/week. The NFL has first claim on the shared pool; if its snapshot targets start
  firing it could take ~8,000/month, which makes C reckless, not just expensive.

**Step 4 — forward bulk game-line capture. APPROVED 2026-09-17: per kickoff hour, 45 credits a week
(~675 a season). BUILT; runs once the scheduled task exists (see below).**
- As built: `--odds-forward` is one tick, scheduled every 5 minutes via `run_cfb_job.cmd`. A kickoff
  hour = the UTC clock hour of the **Odds API's `commence_time`** (authoritative for timing - it is
  what books price against; our schedule had one game 30 min off and one TBD). The hour's snapshot
  fires in [first kickoff - 8 min, first kickoff - 1 min]. `cfb_odds_snapshots` holds one row per
  hour (primary key), so an hour is never bought twice; outcomes `captured`, `missed`,
  `skipped_weekly_cap`, `refused`, `error` (retried only if it cost 0), `charge_unknown`.
- Cap: 45 per CFB week (Tuesday 12:00Z to Tuesday 12:00Z). If a week lists more than 15 kickoff
  hours, the hours with the most games are kept and the rest are recorded `skipped_weekly_cap`.
  Week 3 as listed at 2026-09-17 16:44Z: exactly **15 hours = 45**; first window 2026-09-17
  23:22-23:29Z (Pittsburgh v Syracuse 23:30Z).
- A quiet tick (listing <3h old, no kickoff within 40 min) takes no lock, writes nothing and makes
  no request - verified live 17:05Z. Near a kickoff it refreshes the free listing every tick (>4
  min old), so a moved kickoff is seen before the snapshot.
- Output: `cfb_odds_quotes` (event, book, book `last_update`, market, market `last_update`,
  outcome, american price, point, `fetched_ts`) and `cfb_odds_events`. A quote is a close only
  for a game whose `commence_ts` is after its `fetched_ts`; that is a query, not a column.
- Tests `tests/test_oddsapi_capture.py`: a simulated 17-hour week buys 15 and skips the two
  1-game hours; P1 stops at 74 across reruns; an overcharge stops and is charged to the week;
  identical responses in one second never overwrite. The weekly cap, P1 total, overcharge stop and
  no-overwrite were each disabled once and the tests failed.
- **Found while testing:** `archive_cfbd` wrote the file with `os.replace` BEFORE checking the
  manifest, so two identical responses in one second overwrote the first on disk, then failed the
  insert. Harmless for content-deduped sources; not for paid responses kept verbatim. The path is
  now made unique before any write.
- **Scheduled tasks: NOT created by Track C** (tasks are Ethan's). One-liners, non-elevated,
  logged-on only like the NFL tasks:
  ```
  schtasks.exe --% /Create /F /TN "CalibratedSports CFB Odds Forward" /SC MINUTE /MO 5 /TR "\"C:\Users\Ethan Davis\code\cs-cfb\run_cfb_job.cmd\" --odds-forward" /IT
  schtasks.exe --% /Create /F /TN "CalibratedSports CFB Odds P1" /SC ONCE /SD 2026/09/19 /ST 09:00 /TR "\"C:\Users\Ethan Davis\code\cs-cfb\run_cfb_job.cmd\" --odds-p1 --lock-wait 900" /IT
  ```
  `/SD` is yyyy/mm/dd on this machine (`09/19/2026` is refused). The `/TR` form was round-tripped
  2026-09-17 through a throwaway task (dated 2030, deleted, deletion confirmed): execute stored as
  `"C:\Users\Ethan Davis\code\cs-cfb\run_cfb_job.cmd"`, args passed through, logon Interactive.
  Check: `python -m jobs.ingest_cfb --odds-week` and the tail of `cfb\logs\ingest_cfb.log`.
- *(The pre-build plan follows, kept for the record; the "As built" bullets above supersede it.)*
- One call = `GET /v4/sports/americanfootball_ncaaf/odds?regions=us&markets=h2h,spreads,totals`
  = **3 credits** for every listed game, each bookmaker with its own `last_update`.
- Build it in the same shape as CFBD: new files only (e.g. `cfb/oddsapi.py`, a
  `cfb_odds_lines` fact table, an `oddsapi_requests` ledger in `cfb.db` with origin); raw first
  (gzipped response under `cfb/raw/oddsapi/...`); brakes = read `x-requests-remaining` from the
  free `/sports` call first and refuse if the call would take the pool below `config.ODDS_RESERVE`
  (the main clone's `.env` sets it; Ethan's figure is 24,564 spendable above it), a per-run cap of
  1 bulk call, stop on non-2xx. Join events to `cfb_games` by date + names. Do not touch
  `venues/oddsapi.py` (shared NFL code).
- **Decision for Ethan when scheduling:** "~3 credits a slate" = one snapshot, which is a close
  only for games kicking off soon after it. One snapshot per Saturday kickoff hour is ~15 calls
  = ~45 credits a week and gives every game a near-kickoff line. Recommendation: per kickoff
  hour; build the one-call job so either schedule works.

## 11. Decisions taken, with the why

**Ethan's decisions (do not re-open):** CFB ships statistics, usage and per-game results — no
aggregate hit rates, no settlement (appearance gap). Never ingest `espn_cfb_betting`. CFBD for
historical lines. CFB game lines only for Odds API spend (props only against an approved number).
Reuse the main clone's CFBD key. Keep the venv on C: until a rebuild. Delete the pre-migration
backup after phase 2 verified (done). Do not restart the logger for the CFB shards; let rotation
run. Promote the probe (reversed "no migration"). Per-game display permitted, aggregates banned.
Source layering (§10). Forward Odds API bulk capture approved. Normalise provider names.

**Mine:**
- **sportsdataverse releases as the spine, keyed on ESPN athlete id** — free, unmetered, per-game
  box + usage 2004–2026; CFBD athlete ids ARE ESPN ids (20,342 of 22,465 CFBD 2023 roster ids in
  ESPN's, 20,210 same last name), so one id serves both; nflverse `players.espn_id` links to gsis.
- **`cfb_schedules` over `espn_cfb_schedules`** — only it carries division and conference per game
  (3,831 games in 2025 vs 958).
- **Teams keyed (season, team_id)** — conference membership moves between seasons.
- **A new raw copy only when the CONTENT hash moves** — upstream re-uploads every season many
  times a day (player_box_2004 `updated_at` 2026-09-16T09:41Z); `updated_at` is no signal.
- **Row-level versioning by ingestion time (SCD2)** — file-level versions would store each season
  once per re-upload; invariants 5 and 8 key "what did we know when" on ingestion time.
- **`src_file_id` + 16-char `row_sha`** instead of path + sha1 per row — the store went 1.23 GB →
  635 MB; rebuilt from the archive and verified identical in all seven tables before deleting the
  backup.
- **`src_part` scopes below the season** — otherwise applying week 3 closes week 2's rows; a
  season scope and a week scope that could hold the same game are refused at parse.
- **Components, not derived values** — no shares, averages, longs, QBR, EPA, CFBD elo or win
  probability (someone else's model output); shares are recomputed from counts.
- **`did_not_play` not stored** — False on every row; an always-False column reads as "everyone
  played".
- **Passing parsed from both layouts** — 466 of 534 2026 passing rows put (C/ATT, YDS, AVG, TD,
  INT) in `stat_1..stat_5`; order verified (AVG = YDS/ATT on all 466; TD before INT by mean).
- **Duplicate keys: drop both, count them; a repeated (game, athlete, category) refuses the file**
  — there is no honest single value, and a row filter would hide a structural error.
- **OS byte-range lock, not a PID file** — a stale PID survives a crash, and on Windows
  `os.kill(pid, 0)` TERMINATES the process. Two writers corrupted 43 probe shards on 09-11.
- **GitHub: one listing per tag, refuse ≥100 inline assets, 403/429 stops the run** — a short
  asset list reads as "season missing", and 60 requests/hour unauthenticated is the budget.
- **Measurements recomputed per parse; limitations as rows citing them; retired ids deleted** — a
  figure on the About page must be a query, and a stale entry would still render.
- **CFBD: season scope `seasonType=both` for history, week scope in-season** — 1 call per season
  including postseason (measured 26–66 postseason games a season).
- **CFBD tables separate from sportsdataverse tables** — two sources' claims are compared by
  query, never merged on write. Measured agreement: every CFBD line game and week-2 result joins
  `cfb_games` with identical ids; 0 score disagreements; kickoff and names identical on 302/302.
- **`spread` side measured, not assumed** — 0 of 38,532 away-side, 152 uncheckable.
- **Old `jobs/ingest_cfbd.py` left untouched** — it reproduces the published C01 record from
  `cfb_probe.db`.
- **Provider names canonical + `provider_raw`; fuller DraftKings feed wins** — the two feeds are
  not duplicates; an unmapped book spelled two ways refuses the file; a split across files is
  measured (`cfbd_lines.provider_name_splits`, must be 0). Never fuzzy-match.
- **`--cfbd-week latest` from the stored schedule, 12h settle** — zero requests, reaches the
  postseason.
- **Facts before CFBD in a run; exit codes 3/4 after the free work** — a CFBD refusal must not
  cost the week its stats.
- **`cfb_runs` temp-size logging** — one before/after data point per run for the unattributed
  24.8 GB.
- **`--log` + a 5-line wrapper instead of a long `/TR`** — `/TR` is limited to 261 characters and
  the command had come through truncated twice; the wrapper hardcodes no drive path.
- **Probe: markets + closes promoted, ticks left** — the close is the price that matters; 50.6M
  rows of saturated-cadence ticks do not belong in the production store.
- **Close = strictly before kickoff** — a quote stamped at the kickoff second may be in-play;
  measured 0 such quotes, so it changes nothing against C01.
- **Probe join on the full-season schedule** — the probe listed later weeks; kickoff agreement
  with CFBD re-measured every run.
- **C01 normaliser copied, not imported** — production must not depend on a research script; a
  test keeps them identical.
- **Probe manifest rows `external:`, extraction hash instead of hashing 23.9 GB** — the archive is
  never written again; `--audit` opens it read-only.
- **`book_state` labels** — Polymarket quotes an empty book as 0/1 whose mid means nothing.
- **Guard design** — aggregate-rate and settlement/appearance names banned everywhere; per-game
  grade columns allowed only in tables keyed on `game_id`; the guard is tested against each
  banned shape (a guard never seen to fail is not a guard).
- **Two-direction skew statement** — "skews to the under" is true only for missing-as-zero.
- **Closing-line correction** — exchange closes for the probe weekend are genuine closes; CFBD
  book lines are not.

## 12. Standing lessons from this session (also in memory)

- A limitation that breaks the build cannot be forgotten; keep encoding limits as failing tests
  and refusals.
- `logger.log` lines carry no date: date a line by counting midnight rollovers after it and check
  process CreationDate before claiming a restart.
- Verify an agent's claim before relaying it (the "nothing from the 09-12 slate" claim was wrong:
  81 of 86 week-2 FBS-home games were present).
- Measure before merging: "Draft Kings" looked like a typo and was a second feed.
