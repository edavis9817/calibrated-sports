# C11 - MLB reads as current and stops at last season

Track C, relay unit c-11, 2026-09-22. Nothing published, nothing spent.

## What was measured tonight

**The store.** `D:/calibrated-sports/data/mlb.db`, opened `mode=ro` with
`PRAGMA query_only`. Current rows (`valid_to_ts IS NULL`):

| gametype | games | first | last |
|---|---|---|---|
| regular | 64,055 | 1999-04-04 | 2025-09-28 |
| wildcard | 73 | 2012-10-05 | 2025-10-02 |
| divisionseries | 437 | 1999-10-05 | 2025-10-11 |
| lcs | 308 | 1999-10-12 | 2025-10-20 |
| worldseries | 152 | 1999-10-23 | 2025-11-01 |
| playoff (tiebreakers) | 7 | 1999-10-04 | 2018-10-01 |
| allstar | 26 | 1999-07-13 | 2025-07-15 |

27 seasons, 1999-2025. `mlb_batting`, `mlb_pitching` and `mlb_team_games` all end on
**2025-11-01**. The newest raw file was fetched 2026-09-22 05:14Z (`mlb_raw_files`, the
2025 bundle). The store holds **no 2026 game**, while (per the LEDGER, 22 Sept - not checked here)
the 2026 regular season is in its final week. The store's own `mlb.no_current_season` limitation row already says: "A page
must not present the newest season held as the current one."

**What a reader sees today** (`https://calibratedsports.com`, `/build.json` = web
`9884f96`, built 2026-09-22T23:35Z; server HTML fetched with curl, not rendered):

- `/mlb` returns 200 and renders the generic ComingSoon page: "There is no ingest, no
  manifest and no record for MLB yet." No date, no season range, no Retrosheet.
- `/mlb/players`, `/mlb/teams`, `/data/mlb/manifest.json` and `/data/coverage.json` are 404.
  `/data/sports.json` lists NFL only.

So **today nothing reads as current, because nothing is published.** The staleness is
latent. The live page does say something false - "no ingest" has been untrue since c-03 -
but it is false in the safe direction: it claims less than we hold, never that we hold 2026.

**Does anything disclose the cutoff?** Not on any served page. Two unpublished places do:

1. The MLB manifest that `jobs/export_mlb_web.py` builds (c-03, on `main`) carries
   `current.season 2026`, `data_through {season 2025, index 305}`, `stale: true` and a
   `stale_reason`. `index` is a day-of-year ordinal, so as data it is right, but it is
   not a date a page can print. Rendered through today's `StaleBanner` it would read
   "Retrosheet is late: stats through 2025 · date 305 - ...", which frames an annual
   publisher as a late one (c-07 and c-09 filed this to track B). Also: `current.season`
   is the constant `CURRENT_SEASON = 2026`, typed, not read - it has to be edited each year.
2. The coverage feed (`jobs/export_coverage.py`, c-01) listed MLB `seasons 1999..2025` but
   `span: null` on every MLB table. It had the season range and not the date.

## What this unit changed: the cutoff is a field, read from the store

`CoverageCount.event_dates: {first, last} | null` - calendar dates, `YYYY-MM-DD`.

- Filled for the four MLB tables that record a game date (`mlb_games`,
  `mlb_team_games`, `mlb_batting`, `mlb_pitching`) from `MIN(date)`/`MAX(date)` over
  current rows, beside the same holding's `counted_at`. `mlb_player_teams` has no date and
  carries null.
- **A date, not a timestamp.** Retrosheet records the game's local date and no time.
  Filling `span` would mean inventing an instant, and a midnight-UTC instant renders as 31
  October in every US time zone - a wrong cutoff, produced by a correct-looking field.
- `span` and `event_dates` are never both set; `first <= last`; a value that is not a real
  `YYYYMMDD` date refuses the run, because dates are compared as text and a malformed one
  would win the MIN or MAX and be published as the cutoff.
- Tests: the cutoff moves when a later game is added and moves back when that game's
  version is closed, so it cannot be typed and cannot drift; every mutation of the new
  guards fails a test.
- Contract: `coverage` is still a proposal (`docs/proposals/coverage.defs.json`, track
  C's) on `main`. The field is added there and filed to track A as **A-C12** with the
  exact diff.

## The wording - DRAFT, for Ethan to approve

Track C proposes; Ethan approves; track B renders. Nothing below is settled.

**Recommended section label:**

> **MLB · 1999–2025 · historical**

**Recommended line beneath it:**

> Box scores from Retrosheet, 1999 through 1 November 2025. The 2026 season is not here:
> Retrosheet publishes a season after it ends.

And Retrosheet's statement at the foot (c-07; the gate below).

**Every figure in it is read, none typed:**

| words | read from |
|---|---|
| 1999–2025 | `coverage` MLB `stats.seasons` (or the manifest's `seasons`) |
| 1 November 2025 | max `event_dates.last` over the MLB stats sources |
| 2026 | the manifest's `current.season` |
| historical | computed, see below |

**"historical" is a computed verdict, and it can come out the other way.** It is shown
when the season in progress is newer than the newest season held:
`manifest.current.season > max(seasons)`. Today 2026 > 2025, so it shows. When
Retrosheet's 2026 bundle lands (after the season ends; `2026csvs.zip` was 404 on
2026-09-22 per `mlb.no_current_season`) and is ingested,
`max(seasons)` becomes 2026 and the word drops; it returns when `current.season` moves
to 2027. Per the claims rule, this is a verdict function and lands with an entry in the
site's `tests/falsifiable.test.ts`.

Known imprecision, stated rather than hidden: `current.season` is a typed constant
(`CURRENT_SEASON` in `jobs/export_mlb_web.py`). If nobody bumps it in 2027 the label
silently stops saying "historical" the moment the 2026 bundle lands and never says it
again. The same once-a-year edit already exists for CFB (`cfb/sources.py`). A guard that
compares the constant against the calendar year at export time would close it; not built
here, because it is a producer change to a file c-07 also edits (merge order, below).

**Alternatives considered:**

- "MLB (through 2025)" - shorter, but a reader in September reads "through 2025" as a
  typo for 2026. The word "historical" is what stops the page reading as current.
- "MLB archive" - honest, but loses the range, which is the fact.
- Using `StaleBanner` as-is - rejected: it says the source is "late", and Retrosheet is
  not late; it publishes annually by design.

## The gate - and it outranks the wording

**No MLB page is live because the publish is gated, not because of staleness.** c-07 held
any MLB publish on two conditions. Checked tonight:

| condition | state on 2026-09-22 |
|---|---|
| A-C10 in the contract (`SportManifest.attribution`) | **NOT met.** No branch of the contract carries it: `origin/main`, `a-05`, `a-07`, `a-08`, `a-11` all 0 matches for "attribution". A-C10 itself lives only on the unmerged `c-07-mlb-attribution` branch. |
| Track B renders the statement | **Met in code and deployed.** `SourceAttribution` (`components/DataState.tsx`, `lib/attribution.ts`) is on web `main` at `9884f96`, which is what `/build.json` reports as live, and `config/sports/mlb.ts` names Retrosheet. It renders nothing today because no manifest carries the key. Not rendered or screenshotted in this unit. |

Two more things stand in front of any MLB page, independent of the wording:

- `config/sports/mlb.ts` has `comingSoon: true` and `pages: ["home"]` on web `main`.
- **c-07's `WEB_EXPORT_DIR` refusal is not on `main`.** On `origin/main`,
  `jobs/export_mlb_web.py` has no destination check, so the data half of c-07's gate is
  enforced only on the unmerged branch. Today the manifest would also fail validation
  as soon as it carried `attribution` (closed object), so a compliant MLB manifest cannot
  be produced at all until A-C10 lands.

So the order is: A-C10 into the contract → c-07 merged (producer emits attribution,
refuses the publish dir until then) → A-C12 with the coverage kind → track B renders the
label and the statement → Ethan flips `comingSoon`. Wording before the gate is copy for a
page that cannot exist yet.

## The gap: a current-season source - stated, not solved

Nothing is surveyed or spent here. c-03 refused MLB's Stats API on MLBAM's notice
("individual, non-commercial, non-bulk use"); the LEDGER (22 Sept) confirms that
closing monetisation does not unlock it. A source for in-season MLB box scores would
have to satisfy, all at once:

1. **Terms** that permit republishing derived stats on a public site: an explicit
   grant, or silence (the 22 Sept posture). Any "non-bulk", "personal use only" or
   "no redistribution" clause fails, whatever the revenue model.
2. **Attribution we can carry** - which needs the A-C10 field anyway.
3. **Game-level box lines** keyed per player per game, at the grain of `mlb_batting`
   and `mlb_pitching`, not season totals - otherwise the season folds (`mlb/totals.py`)
   cannot be continued and the 2026 rows would be a different kind of thing.
4. **A player id that crosswalks to Retrosheet's** at ingest (the Chadwick register,
   ODC-BY, is the known bridge; `mlb.retrosheet_ids_only`), so one player is one page
   across the seam.
5. **Reconcilable with Retrosheet afterwards.** When the season's Retrosheet bundle
   lands, the in-season rows are superseded; the versioned store supports that, but only
   if the source's box lines agree closely enough that a player's page does not jump.
   That agreement would have to be measured on a past season before anything ships.
6. **Free or already paid for** - no new spend without a pre-registered question.

No source meeting all six has been identified in this project; none was looked for
tonight. Until one is, "historical" is the honest word.
