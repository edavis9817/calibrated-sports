# C04 — MLB stats: where it lives, what it came from, and what the contract says (c-03)

Stats only. No odds, no props, no market data, no credits. Built 2026-09-22 by track C.

## 1. The source, and the line of its terms that permits this

**Retrosheet**, per-season CSV bundles (`https://www.retrosheet.org/downloads/{season}/{season}csvs.zip`).
Its notice (`https://www.retrosheet.org/notice.txt`, fetched 2026-09-22 and archived with the data):

> Recipients of Retrosheet data are free to make any desired use of the information, including (but
> not limited to) selling it, giving it away, or producing a commercial product based upon the data.
> Retrosheet has one requirement for any such transfer of data or product development, which is that
> the following statement must appear prominently:
>
> The information used here was obtained free of charge from and is copyrighted by Retrosheet.
> Interested parties may contact Retrosheet at "www.retrosheet.org".

**Refused: MLB's Stats API** (the obvious in-season feed). MLBAM's notice
(`http://gdx.mlb.com/components/copyright.txt`, fetched 2026-09-22):

> Only individual, non-commercial, non-bulk use of the Materials is permitted and any other use of the
> Materials is prohibited without prior written authorization from MLBAM.

An ingest is bulk use. `mlb.sources.check_url` refuses that host and every other one except Retrosheet.
The ingest also refuses to run if the fetched notice no longer contains the permission.

**Consequence: there is no current-season MLB data.** Retrosheet publishes a season after it ends.
`2026csvs.zip` returned 404 on 2026-09-22 and 2025 is the newest bundle. In-season stats need either a
licensed feed or written permission from MLBAM. That is a decision, not an engineering task.

## 2. Does this belong in `cs-cfb`? It belongs in `calibrated-sports`, and it is there

**`cs-cfb` is not a repo.** `git remote get-url origin` returns `edavis9817/calibrated-sports.git`
for `cs-cfb`, `cs-analytics` and `calibrated-sports` alike. W07 calls it "C — CFB ingest
(`calibrated-sports`, second clone)". So the brief's question is really two questions:

- **Which repo?** `calibrated-sports`. Every multi-sport mechanism lives there: the contract
  (`web/contract/v2`), its only validator (`jobs.export_web.validate_contract`), the append-only slug
  registry (`web/slugs/{sport}.json`), the R2 uploader, invariant 7's `sport` discriminator and the
  coverage feed. A separate MLB repo would have to vendor the contract and the validator. That is a
  second source of truth about the contract, which is the duplication the CFB export was built to
  avoid. CFB already lives in this repo on the same pattern: its own package (`cfb/`), its own store
  (`cfb.db`) and its own raw tree. MLB follows it with `mlb/`, `mlb.db` and `mlb/raw`. **Nothing needs
  to move.**
- **Which track?** The label "CFB ingest" was already wrong before this unit. c-02 built the NFL
  injury feed here, and c-01 reads every sport's stores. Track C is in practice "ingest for every
  sport except the NFL logger". Isolation between sports comes from the **store**, one SQLite file
  per sport. It does not come from the clone, so a fourth clone for MLB would add a writer without
  adding any isolation.

**Recommendation:** rename track C to "sport ingest (everything but the NFL logger)" and keep the
clone. No new repo.

**A conflict to flag, not settle.** `CLAUDE.md` § *Out of scope for v1* lists "a second sport" and
"MLB Toolbox migration". The first was overtaken by W07's CFB track. The second is a different act
from this unit: MLB Toolbox (mlbtb.com) is built on FanGraphs exports and shares no code or data
with this store. This unit migrates nothing from it, and FanGraphs-derived data could not enter a
public repo anyway. The line still reads as a standing exclusion and should be updated deliberately.

## 3. The spine

| table | grain | key | rows 1999-2025 |
|---|---|---|---|
| `mlb_games` | game | `game_id` | 65,058 |
| `mlb_team_games` | team-game line | `game_id, team, stattype` | 130,116 |
| `mlb_batting` | player-game batting line | `game_id, player_id, team, stattype` | 1,873,505 |
| `mlb_pitching` | player-game pitching line | `game_id, player_id, team, stattype` | 520,530 |
| `mlb_player_teams` | player-team-season (appearances by position) | `season, player_id, team` | 40,993 |

The grain is the game, as on the NFL spine, and **season totals are computed at read**
(`jobs.ingest_mlb.season_totals`). They are never stored. Every row is versioned by ingestion time
through `cfb.versioning.apply`, because Retrosheet re-publishes finished seasons: the 2024 bundle's
members are dated 2026-08-07.

Measured facts the design depends on:

- **`team` is in every player key.** Danny Jansen batted for TOR and BOS in one game, BOS202406260.
  The game was suspended on 06-26 and completed on 08-26, after he was traded. It is the only such
  line in 27 seasons.
- **Tiebreakers are filed as `playoff`, and MLB counts them as regular season.** Filtering on
  `regular` alone gives Matt Holliday's 2007 as 214 H / 135 RBI. Including the 2007-10-01 tiebreaker
  gives the official 216 / 137. There are 7 such games in 1999-2025. `REGULAR_SEASON = ("regular",
  "playoff")`.
- **The parts sum to the whole on every batting component, in every one of 130,116 team-games.**
  Two pitching components do not, and they differ in one direction only. Team ER is lower than the
  sum of individual ER in 634 team-games and never higher. That is consistent with the team
  earned-run rule (the reason is inferred, the direction is measured). `p_noout` is lower at team
  level in 20,349 and never higher, so it is not an additive component. Neither may be summed across
  pitchers and presented as the team figure.
- **Blank is NULL.** Across all 27 seasons there are 0 NULL cells in any of the 96 stat and flag
  columns of the three line tables, and 0 games without a box score. The rule is there for the day a restatement introduces one. A season total over a column
  unknown in some game is NULL, not a partial sum.
- Every line in every season is `stattype = 'value'`. The `official`/`lower`/`upper` lines
  Retrosheet uses for Negro League data do not occur in 1999-2025.
- Franchise codes move: MON→WAS in 2005, FLO→MIA in 2012, OAK→ATH in 2025. Each code is its own team,
  as the contract's `team_colors` note already expects.

`python -m jobs.ingest_mlb --reconcile 1999-2025` re-derives all of the above.

## 4. Does the contract admit a second sport's stats without reshaping? Yes to validation, no to fit

`jobs.export_mlb_web` built every kind a sport ships from real MLB rows (2024-2025: 4,757 files).
All of them pass `validate_contract`, track A's own choke point. The validator was checked on the
same files: it refused an unknown field and a wrong type. **No reshaping is needed for MLB stats to
validate.** Validating is not the same as fitting, though. Seven findings for track A, in
`python -m jobs.export_mlb_web --findings`:

- **M-1: a period is not unique for a player, and the contract does not say whether it must be.**
  With a date index, doubleheaders collide 13,630 times in 7,855 of 35,641 player-seasons (22%).
  With a team-game index there are 205 collisions in 156 player-seasons. `game_id` is the only
  unique key, and it is nullable.
- **M-2: there is no honest `current` for a source that is historical by design.** `stale: true` is
  the only available statement, and it means "late".
- **M-3: there is no attribution field.** Retrosheet's one condition has nowhere to go, because
  every object is closed.
- **M-4: `position` is one string, but two-way players exist.** Ohtani 2025 has 18 games carrying
  both a batting and a pitching line. Per-group game counts travel as stat keys, which works.
- **M-5: the source carries no team names**, only codes, while the contract requires a name.
- **M-6: the roster carries three football share fields** that are always null here, and has no
  field for MLB's own usage shares.
- **M-7: `markets` is a required integer.** A stats-only sport says 0.

Non-findings: `Stats` is an open map, and MLB's `b_`/`p_` keys fit, two-way rows included. The key
patterns resolve. `result` T and `tied` hold MLB's 7 regular-season ties (1999-2016).

## 5. What is not built

- Fielding (per position per game) and play-by-play (`plays.csv`, ~108 MB per season uncompressed)
  are archived and not parsed.
- There is no MLBAM/bbref crosswalk. The Chadwick register (ODC-BY) is the candidate source. Its
  terms were not read tonight.
- There is no league or division, because the bundle does not carry them.
- There is no scheduled refresh. A new bundle appears once a year, after the season, so this is a
  yearly `--fetch <season>`, and `mlb.sources.LAST_PUBLISHED` moves only after that fetch succeeds.
- **Nothing is published.** There is no upload path, and the probe writes only to a directory you
  name.

## 6. c-07 corrections (2026-09-22), after f-03's verification

- **The attribution was in no product artefact, and the limitation row said it was.** f-03 counted
  Retrosheet's statement in 0 of 4,757 exported files. Where it stands now:
  - The export writes `mlb/NOTICE.txt`, verbatim. That is 1 file in a 4,758-file tree, and **still
    0 of the 4,757 JSON files**, because the contract has no field for the statement (A-C10, filed
    to track A with the exact diff).
  - The export refuses to write into `WEB_EXPORT_DIR` until the manifest carries the statement.
  - `mlb.attribution_required` now says the condition is **not yet met** and that no MLB data may
    be published. A test pins the row to the contract's real state, so it fails when the field
    lands.
  - The site half (name Retrosheet, not `statsapi`; render the statement) is filed to track B.
- **One aggregation rule.** The rule is: NULL is contagious, and a tiebreaker counts as regular
  season. It now lives only in `mlb/totals.py`. `season_totals` and `team_records` select lines in
  SQL and fold them in Python, and the export folds through the same functions. On the real store,
  `research/c07_totals_equivalence.py` compared the new code against c-03's SQL, kept verbatim as
  an oracle:
  - 1,282,132 season-total cells over 27 seasons: 0 differ.
  - 810 team-seasons: 0 differ.
  - 100,484 export cells for 2024-2025: 0 differ. That is f-03's 90,116 plus the 6 pitching credit
    flags.
  - **No cell in 1999-2025 is NULL.** So on real data the null branch of the rule has never fired;
    it is exercised by fixtures only.
- **Reads are read-only.** Status, `--totals` and `--audit` open `mlb.db` with `mode=ro` plus
  `query_only`, and refuse a missing store rather than create one. Run against the real store,
  they left `mlb.db` byte-identical and every `recorded_ts` unchanged. A limitation's
  `recorded_ts` now moves only when its text changes.
