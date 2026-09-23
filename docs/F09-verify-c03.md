# F09 — verifying c-03's MLB ingest against what it claims

Track F, unit f-03, 2026-09-22. This is a cross-track check: c-03 (track C) built it, and F re-derived the
figures before reading C's code. The store was opened with `mode=ro` and `PRAGMA query_only` only.
Figures are re-derived by `python -m research.f09_verify_c03 D:/calibrated-sports/data/mlb.db` (stdlib, read-only; the default path is `config.storage_path("mlb.db")`). The one check
that script cannot reproduce is marked as such below.

**What was checked.** c-03 is commit `ba81999`. When this unit started it sat on the `cs-cfb`
clone's **local** `main` only. It reached `origin/main` during the unit, through track A's a-04
integration (`e882a45`). `git diff ba81999 origin/main` over `mlb/`, `jobs/ingest_mlb.py`,
`jobs/export_mlb_web.py` and `tests/test_ingest_mlb.py` is empty, so what was verified is what
landed. The code was read as `git show ba81999:<path>`, and it was imported from the `cs-cfb` clone
into a scratch 3.12 venv to time it.
The store is `D:/calibrated-sports/data/mlb.db`, 656,621,568 bytes, mtime 2026-09-22 01:15.

## Verdict in one paragraph

- **The licence reading is right**, and the quote is verbatim.
- **The data claim is right**, and it holds up under checks c-03 did not run.
- **The "computed at read" claim is true of the store.** It is **not** what the product would do:
  the export writes season and career totals into every `summary.json`, so pages would read stored
  totals and never wait on SQL.
- **The attribution the licence requires is in no product artefact.** None of the 4,757 exported
  files carries it. c-03's own limitation row says the export does carry it, and that is false.
  c-03 filed the underlying gap honestly as M-3, so this is an overstatement in one row, not a
  concealed defect. Nothing has been published, so nothing is in breach **today**.
- **One new cross-track finding:** the site already names MLB's source as `statsapi`, the source
  c-03 refused.

---

## 1. Licence, checked first because everything else depends on it

### What the terms actually say

I fetched `https://www.retrosheet.org/notice.txt` today (HTTP 200, 971 bytes):

> Recipients of Retrosheet data are free to make any desired use of the information, including (but
> not limited to) selling it, giving it away, or producing a commercial product based upon the
> data. Retrosheet has one requirement for any such transfer of data or product development, which
> is that the following statement must appear prominently:
>
> The information used here was obtained free of charge from and is copyrighted by Retrosheet.
> Interested parties may contact Retrosheet at "www.retrosheet.org".

**c-03's quote is exact.** `mlb.sources.ATTRIBUTION` is contained in the fetched notice after
whitespace normalisation (checked by code, `True`).

The notice also says that Retrosheet "makes no guarantees of accuracy" and that "all information is
subject to corrections". That is consistent with c-03 versioning rows by ingestion time.

**The MLBAM refusal is also right.** I fetched `http://gdx.mlb.com/components/copyright.txt`
(HTTP 200). Its second sentence reads: *"Only individual, non-commercial, non-bulk use of the
Materials is permitted and any other use of the Materials is prohibited without prior written
authorization from MLBAM."* That matches c-03.

**I did not check** c-03's claim that FanGraphs and Baseball-Reference prohibit automated
retrieval. c-03 marked it `not_verified` and so do I.

### Is the attribution actually present where the terms require it? No

The terms require the statement to "appear prominently" on the transfer or the product. The table
lists every place the statement or the obligation exists:

| where | carries the statement? | is it product? |
|---|---|---|
| `mlb/sources.py` `ATTRIBUTION` constant and docstring | yes, verbatim | no, it is code |
| `mlb.db` `mlb_limitations` row `mlb.attribution_required` | it names the obligation | no, it is the store |
| `docs/C04-mlb-stats.md`, and c-03's report | yes | no |
| **the export**: `jobs/export_mlb_web.py` probe, 4,757 files in `D:/temp/c03/probe` | **no: 0 of 4,757** (`grep -rl "copyrighted by Retrosheet"`) | **yes** |
| the site, `calibratedsports-web` (`b-integration` and `origin/main`) | **no:** `git grep -i retrosheet` returns nothing | **yes** |

**So the licence is currently honoured only in code, docs and a store row. That is acceptable
only because nothing is published.** The first MLB publish would be unlicensed as the code stands.

That gap is structural, and c-03 said so itself (M-3). Every contract object is closed
(`additionalProperties: false`), and the export's own probe showed that adding an `attribution`
key is refused. **The export cannot carry the statement until track A adds a field.**

**Where c-03 overstated.** The limitation row's `consequence` text reads *"Any export of MLB data
carries the statement."* That describes an intention, not the export, and the same row goes on to
say the contract has no field for it. A reader of the store would take the first sentence as a fact
about the product, and that sentence is false today.

**New finding, belongs to track B.** `calibratedsports-web/config/sports/mlb.ts` declares
`sourceName: "statsapi"`. That is MLB's Stats API, the source c-03 refused on its non-bulk terms.
`sourceName` is rendered as the data source in `StaleBanner` ("{sourceName} is late"),
`SourcesView` and `SportHome` ("Coverage from the {sourceName} record").

The config is `comingSoon: true` with `pages: ["home"]`, so this is not user-facing on any data page
yet. But it is wrong in two ways at once:
- it credits the refused source;
- it does not credit the licensed one.

The probe manifest also sets `stale: true`, so the banner would say "statsapi is late". That is two
false statements in one line: the wrong source, and "late" for data that is historical by design
(c-03's M-2).

## 2. The data, re-derived before reading c-03's counts

Every figure below is c-03's figure as well. Nothing disagreed.

| table | rows | current (`valid_to_ts IS NULL`) | seasons |
|---|---|---|---|
| `mlb_games` | 65,058 | 65,058 | 1999–2025, 27 distinct |
| `mlb_team_games` | 130,116 | 130,116 | 1999–2025 |
| `mlb_batting` | 1,873,505 | 1,873,505 | 1999–2025 |
| `mlb_pitching` | 520,530 | 520,530 | 1999–2025 |
| `mlb_player_teams` | 40,993 | 40,993 | 1999–2025 |

The raw manifest holds 27 `retrosheet_csv` bundles (263,392,325 bytes) and one notice. That matches
c-03's "28 files, 263 MB".

### Is any season short? No, and the checks are stronger than c-03's

- **Regular-season games are 2,426–2,431 in every full season.** 2020 is 898, which is right for the
  60-game season with two cancelled games.
- **Every season has 30 teams.** Each team plays **161–163** games (regular plus tiebreaker). A 161 is
  a rainout never made up; a 163 is a Game 163. In 2020 the range is 58–60.
- **A partial load would show as one or more teams well below 161. None does.**
- **Every game has exactly 2 team lines** (65,058 of 65,058).
- **No game lacks a batting line, and no game lacks a pitching line** (0 and 0). No team-game id is
  missing from `mlb_games`, and no game is missing from the team-game table.
- **Batting lines and distinct batters per season are flat:**
  - ~69.4k–73.1k lines a season, apart from 2020 at 28,413;
  - batters rise gently from 1,209 (1999) to 1,471 (2025), with the 2021 jump to 1,509.
  - Pitching lines run 17.5k–22.0k. None of it looks like a truncated file.
- **The postseason shape is right in every era:**
  - wildcard 0 before 2012, 2 in 2012–2021, 18 in 2020, and 8–11 from 2022;
  - no All-Star Game in 2020;
  - 7 `playoff`-typed tiebreakers: CIN 1999, COL 2007, CHA 2008, MIN 2009, TEX 2013, and CHN and
    LAN 2018.

### Spot checks, run independently of c-03

These use `season_totals` from `ba81999`, run read-only:
- **Raleigh 2025:** 60 HR, 125 RBI.
- **Judge 2024:** 58 HR, 144 RBI.
- **Holliday 2007:** 216 H, 137 RBI, including the tiebreaker.
- **Ohtani 2025:** 55 HR, 102 RBI, 20 SB.
- **Bonds 2001:** 73 HR, 137 RBI. c-03 did not check this one.
- **Pujols career 1999–2025:** 703 HR.
- **Team records:** LAN 2024 98-64, CHA 2024 41-121, LAN 2025 93-69.

**Could not check properly:** like c-03, I compared these against published figures **from
memory**, not against a fetched reference. They are well-known numbers, but this is the same weakness
c-03 declared, repeated rather than fixed.

### What the data cannot yet show: versioning is built but never exercised

c-03 claims the store is "versioned by ingestion time". The columns exist
(`valid_from_ts`/`valid_to_ts`), and the machinery is `cfb.versioning.apply`, which is shared with
CFB. **But every row in all five tables is current: no row has ever been closed.** Each season has
been loaded exactly once. So the versioning is proven by c-03's tests and by CFB's use of the same
module. On MLB data it has never fired, and nothing about the real store can confirm it works here.
This is not a defect, but "versioned" should be read as "able to version".

## 3. The shape: "season totals computed at read"

### True of the store

`mlb.db` has no totals table; the full table list was checked. `jobs.ingest_mlb.season_totals` is a
`GROUP BY player_id` over game lines. Its NULL guard, `CASE WHEN COUNT(c) = COUNT(*) THEN SUM(c)
END`, does what c-03 says: a total over a game with an unknown value comes out NULL, not as a
partial sum.

### Not what the product does, and that is the more useful fact

`jobs/export_mlb_web.build()` does not call `season_totals`. It loads every line for the exported
seasons into Python, re-aggregates them there, and writes `season_totals` and `career` into
`{sport}/players/{id}/summary.json`.

**So the site never runs a totals query.** It reads totals materialised at export time, the same
way it reads NFL totals. The brief's worry, that pages cannot wait on seconds, does not arise for any
page this design would serve.

- The cost lands on the **export**, once a run.
- **The two paths agree.** For 2024 and 2025, every player's regular-season totals in the probe
  `summary.json` files equal `season_totals()`: **0 mismatches over 90,116 compared cells** (batting
  27,626 + 27,930, pitching 17,100 + 17,460).
- They are still **two implementations of one rule** (the NULL propagation is written twice). Any
  future edit has to move both.
- **Not reproduced by the committed script**, because it needs the `ba81999` modules.
- **The comparison was not mutation-tested.** It compared non-zero cells and printed its counts, but
  I did not plant a mismatch to show that it fires.

### What a live totals query costs on the real table

Measured on this machine (warm cache unless stated):

| query | seconds |
|---|---|
| one season, all batters (2025, 1,470 rows) | **8.8 cold**, 0.64–0.75 warm |
| one season, all pitchers | 1.7 cold, 0.21 warm |
| one player, one season | **0.55–0.57**, and the same for a player with no rows |
| one player's career, 27 per-season calls | **15.5–17.5** (four runs) |
| team records, one season | 1.6 cold, 0.05 warm |

**The plan explains it.** Every `season_totals` call is `SCAN mlb_batting`, a full table scan:
- The only secondary index, `ix_mlb_*_current`, is on `(src_dataset, src_season, valid_to_ts)`. The
  query filters on `season`, not on `src_season`, so the index is never used.
- The primary key leads with `sport`, so there is no access path on `player_id` or `game_id` either.
- As a result, a lookup for one player costs the same as one for all players.

(An `EXISTS` subquery keyed on `game_id` alone was the first thing I tried. It did not finish in two
minutes and I stopped it.)

**Is it a finding?** Not for pages, which never issue this query. It is a finding for anything that
would:
- **For a player page or an API reading the store live, 0.5s a season and ~16–18s a career is too slow.**
- It is harmless for a weekly export.

The fix is an index on `(season, player_id)` for the current rows, or a query that also filters on
`src_season`. It is not needed until something reads live.

### A "read" path that writes

`jobs.ingest_mlb.connect()` runs the full DDL, sets `journal_mode=WAL`, and does an `INSERT OR
REPLACE` of every `mlb_limitations` row with `recorded_ts = time.time()`. `--totals`, `--audit` and
the no-argument status path all go through `connect()`. So **looking at a season total writes to the
store**.

This is the same shape as track A's a-01 finding on `map_markets --coverage`. It has one extra
consequence: `mlb_limitations.recorded_ts` is not when a limitation was recorded, but when someone
last connected.

- The export uses a separate `ro()` (`mode=ro`) and does not write.
- This conclusion comes from reading the code. I did not run `--totals` against the real store,
  precisely because it would write.

## Where c-03 was right, where it overstated, where I could not check

**Right**, re-derived rather than repeated:
- the quote of the terms, and the attribution text, verbatim;
- the MLBAM refusal;
- the season range, all five row counts and the raw file count;
- no partial load;
- totals are not stored in the store;
- the NULL rule;
- tiebreakers counted as regular season (7 games);
- every spot check it listed.

**Overstated:**
- **The limitation row's "Any export of MLB data carries the statement."** 0 of 4,757 exported files
  do. c-03's M-3 names the reason, but the row states the intention as a fact.
- **"Season totals computed at read."** True of `mlb.db`, but the export materialises them. The claim
  as written makes the design sound slower than the product would be, and it hides that the rule is
  implemented twice.
- **"Versioned by ingestion time."** The structure is in place, but it has never been exercised on
  MLB rows.

**Could not check:**
- published figures against a fetched reference; I used memory, as c-03 did;
- the FanGraphs and Baseball-Reference terms (not fetched);
- the export-vs-SQL agreement from the committed script. The script restates the SQL rather than
  importing it, because it was written before c-03 reached `origin/main`;
- whether `cfb.versioning.apply` closes MLB rows correctly on a re-publish, because no re-publish has
  happened;
- c-03's full-suite run (1,413 passed). I did not re-run the suite.
