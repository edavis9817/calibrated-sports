# What nflverse and the other free feeds expose that this project does not use (a-64)

Measured 2026-09-30. The release list, asset names, per-release year ranges, parquet sizes
and last-update dates come from `GET api.github.com/repos/nflverse/nflverse-data/releases`
(1 call, unauthenticated, free). Row counts and season spans for the single-file datasets
come from downloading the file to a scratch directory (`D:/temp/a64`) and reading it. Nothing
here was ingested, and no file was written to any store.

"In the store" is what `nflverse_versions` in `market_log.db` records as archived (read
`mode=ro`), plus `feeds.db` and track F's `analytics.db`. "Parsed" means some committed code
reads it into a table (case-insensitive grep over `analytics/ jobs/ models/ research/ lab/
cfb/ feeds/`, tests and `nflverse.py` excluded).

**Every item below is free: GitHub release assets, no key, no account, no API credits.** The
cost of each one is engineering time, and the storage figures are the parquet sizes summed
over every season.

## A. Not fetched at all

| dataset (release) | seasons | size | what it holds | ingest cost | what it would feed on a matchup |
|---|---|---|---|---|---|
| `stats_team` (`stats_team_week_{season}`) | 1999-2026, updated 2026-09-30 | 8.5 MB, all four variants | 138 columns per team-game: passing/rushing/receiving yards, first downs, **passing_epa, rushing_epa**, CPOE, sacks suffered, fumbles by type, defensive columns, special teams. 96 rows for 2026 weeks 1-3 | small: one file per season, same shape as `stats_player`, which is already ingested | "offence and defence by unit" from the source instead of summed from player rows; EPA per play by unit |
| `pfr_advstats` (`advstats_week_{pass,rush,rec,def}_{season}` and season files) | 2018-2026 | 2.9 MB | drops, bad throws, times pressured / blitzed / hurried / hit, broken tackles, yards before and after contact | small per file, but **Pro-Football-Reference-derived**: the terms question already open for PFR columns (LEDGER 22 Sept) applies | pass-protection and pass-rush pressure by team, which the play-by-play cannot give |
| `officials` | 2015-2026, 22,012 rows | 0.2 MB | every official per game, position and id | trivial | the referee crew, and its history |
| `espn_data` (`qbr_week_level`) | 2006-2026, 10,805 rows | 0.4 MB | ESPN QBR per quarterback-game, with points added and EPA splits | trivial, but it is ESPN's metric: terms unchecked | quarterback quality beside the team rating |
| `trades` | 2002-2026, 4,975 rows | 0.1 MB | trades, with picks and players | trivial | little for a game page |
| `combine` | 2000-2026, 8,968 rows | 0.4 MB | combine measurements | trivial | nothing for a game page |
| `misc` (`pfr_rosters`), `players_components` (`otc_players`) | - | 1.0 / 3.6 MB | PFR rosters; OverTheCap players | **do not**: OTC prohibits reuse in writing (LEDGER 22 Sept), PFR as above | - |

## B. Archived raw and parsed by nothing

| dataset | seasons in the release | in our archive | what it holds | cost to use |
|---|---|---|---|---|
| `depth_charts` | 2001-2026, 15.9 MB | 47 files, latest 2026-09-30 | 2026 is **585,690 rows**: a timestamped (`dt`) daily snapshot per player with position group, slot and rank | a parser and a table; the as-of question is the work, since the file is rewritten daily and a depth chart is only meaningful at a date |
| `ftn_charting` | 2022-2026, 2.3 MB | 16 files | FTN's charted play attributes | a parser; FTN's terms unchecked |

## C. Held in another store and not read by any export

| dataset | where | coverage | note |
|---|---|---|---|
| `injuries` | `feeds.db` `injury_reports` (`jobs/ingest_feeds.py`) | 2009-2026, 91,679 rows; 2026 through week 4 | the official Wed-Fri report. **It has no capture time from 2025** (`feeds_limitations`): the file dropped `date_modified`, so as-of is our ingestion stamp only |
| NFL weather forecast | `jobs/ingest_feeds.py --nfl-weather` (f-05) | **0 NFL rows** in `weather_at_kickoff`; the 296 rows are CFB | the code exists and uses sourced stadium points; it has never been run for the NFL. Open-Meteo is free and keyless |
| stadium coordinates | `feeds/nfl_stadium_points.csv` (committed) | 48 points | used by a-64 for travel distance. **No time zone and no country**, so "time zones crossed" and "international venue" publish null. Wikidata carries both (P421, P17) on the same items; adding them is a change to `research/nfl_stadium_coords.py`, not a new source |

## D. Already ingested and used

Schedules (`nfl_games`), weekly player stats (`nfl_player_week`), snap counts, weekly
rosters, players, teams, play-by-play (`analytics.db`, track F), Next Gen Stats (track F),
participation (track F, `analytics/team_units.py`; the release is 2016-2025 and refreshes
only after the postseason), contracts and draft picks.

**Track F's team pace is stale for 2026.** `f_team_game_pace` holds 2026 week 1 only (32
team-games); `f_spine_build` last ran 2026-09-17. The a-64 total and every team's
points-per-play figures are withheld until it covers the season, because c-31's factors drop
a team-game with no pace row.

## Recommendation, if Ethan picks from this

1. **`stats_team`**: the largest gain for the least work. It is the same release family
   as `stats_player`, already ingested and reconciled; it gives unit-level EPA per play for
   both sides of every matchup; and it covers 1999 onward. a-64 measured that summed
   player passing and rushing yards equal its team totals on all six PIT/CLE games of 2026
   weeks 1-3, and that fumbles lost do NOT (CLE week 3: 1 against 0), so a-64 publishes
   yards and interceptions and leaves fumbles out.
2. **Run the NFL weather capture that already exists** (`--nfl-weather`), and add P421/P17
   to the stadium points. That closes three of the four nulls on the situation block at no
   new source.
3. **`officials`**: trivial and free, but nothing measured says a crew moves a total. Hold
   it until a question needs it.
4. **Not recommended now:** `pfr_advstats` and `espn_data`, until the PFR and ESPN terms
   questions are answered; `depth_charts`, until someone owns its as-of problem.
