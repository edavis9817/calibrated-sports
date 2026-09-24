# Web data contract — schema_version 2

> **THE CONTRACT IS `web/contract/v2/contract.schema.json`. THIS FILE IS PROSE
> ABOUT IT.**
>
> That file is executable and it is the only source of truth. `jobs/export_web.py`
> validates every file it writes against it, derives `SCHEMA_VERSION` and the
> key table from it, and the site generates `lib/schema.generated.ts` from a
> vendored copy — CI on both sides fails on drift. If this document and that
> schema ever disagree, the schema is right and this document is a bug.
>
> This page existed alone until 2026-09-15, and the arrangement failed exactly
> as you would predict: two hand transcriptions of one prose document. The site
> typed `identity.name` as `string` while the exporter emitted `null` — a 500 on
> the player page, and a browser-side throw that took out the whole 3,971-row
> players index while `curl` still reported 200. Making the contract executable
> found two more of the same shape within the hour (`RosterEntry.slug`,
> `seasons[].teams[]`).

The contract between `jobs/export_web.py` (this repo) and the site
(`calibratedsports-web`). It implements `calibratedsports-web/docs/site-architecture.md`
§1 (contracts) and §1.2 (R2, brought forward). §3 continues from here; its first
item — this executable contract — is done. Components tables, custom scoring,
client-side distribution sampling, incremental export and content-hash keys are
still to come.

v1 (static files in `public/data`, sport-less paths, fantasy points computed at
export) is retired. Changing a field's meaning or removing one bumps
`schema_version`.

**Adding an optional field does NOT bump `schema_version`, but it does fail the
build until the contract is updated in the same commit.** Every object in the
schema is closed (`additionalProperties: false`) on purpose: `headshot_url` was
an additive field, and under a permissive schema it would have reached the site
unannounced. Update the contract, sync, regenerate — one commit, both sides.

## What the export excludes, and says so

Two filters run, and **both report a count on every run**, zero included. A
silent filter is how a real player disappears without anyone noticing; a line
that reads `excluded 0` most weeks is what makes the week it reads `excluded 1`
visible the day it happens. Both also publish the affected ids in the manifest's
`unresolved_ids`, so an exclusion is visible on the site itself.

- **No resolvable name** — no `player_xwalk` row and no `player_name` in any stat
  row. A page titled by a bare source id is not a player page and a nameless row
  cannot be searched for, so the player is excluded rather than rendered.
  Currently 1: `00-0005532` (three 1999 rows for NO). Excluded players never
  enter the permanent slug registry.
- **A period row with no team** — dropped from that season's `teams` display
  list only. The period row keeps its null `team`, because that is the honest
  record. Currently 1 row of 478,812 (a 1999 game).

## Governing rules

1. **Sport is the first key segment and the first URL segment.** There is no
   un-prefixed player or team path.
2. **The data describes itself.** Every sport-specific label, format, stat group
   and scoring weight lives in that sport's manifest (data) or in
   `config/sports/{sport}.ts` (site). A site component never branches on a sport
   name; CI greps for it.
3. **No fantasy points are stored.** Player files carry stat components. The
   site computes points from `scoring_presets` in the manifest with ONE scoring
   function, so presets and a future custom scoring share a code path (§3.1).
4. **`period_type` replaces "week."** NFL periods are weeks. Another sport
   declares `game` or `date`, and nothing in the contract assumes a week.

## Storage

- **Bucket:** `config.WEB_R2_BUCKET` (`calibrated-sports-site`), which is separate
  from the logger's `calibrated-sports-raw`.
- **Credentials:** `WEB_R2_ACCESS_KEY_ID` / `WEB_R2_SECRET_ACCESS_KEY`, an R2
  token scoped to that bucket.
- **Local staging:** the export also writes a local mirror to
  `config.WEB_EXPORT_DIR` before upload.
- **No defaults.** None of these has a default; the export refuses to run rather
  than guess.

The site reads the bucket through a Worker R2 binding. Pages render at the edge
from the binding, and browser fetches go to the same-origin route
`/data/{key}`. The bucket is not public.

```
sports.json
{sport}/manifest.json
{sport}/players/index.json
{sport}/players/{id}/summary.json
{sport}/players/{id}/{season}.json
{sport}/teams/{slug}.json
{sport}/market/{id}/{period_key}.json
research/hypotheses.json
research/calibration.json
research/execution.json
```

- `id` is the sport's source id: nflverse `gsis_id` for NFL, e.g. `00-0036355`.
- `slug` is for URLs only. Keys use ids, and indexes map slug to id.
- `period_key` is `{season}-{index}`, e.g. `2026-2`.
- **Caching in v2:** the manifest, `sports.json` and indexes get
  `Cache-Control: public, max-age=60`. Everything else gets `max-age=300`.
  Immutable content-hash keys are §3.5.

## Envelope — every file

```json
{"schema_version": 2, "generated_at": "2026-09-15T18:00:00Z", "kind": "...", "sport": "nfl"}
```

- `sport` is `null` for `sports.json` and `research/*`.
- The site checks `schema_version === 2`, `kind` and `sport`. Any mismatch, a
  missing key or a parse failure renders the explicit "data format changed"
  state, naming the key and both versions.
- Conventions:
  - timestamps are ISO-8601 UTC; `*_ts` fields are unix seconds;
  - missing values are `null`, never 0;
  - probabilities and shares are in [0, 1].

## sports.json — kind `sports`

```json
{"schema_version": 2, "generated_at": "...", "kind": "sports", "sport": null,
 "sports": [{"sport": "nfl", "name": "NFL", "manifest": "nfl/manifest.json"}]}
```

## {sport}/manifest.json — kind `sport_manifest`

```json
{
  "schema_version": 2, "generated_at": "...", "kind": "sport_manifest", "sport": "nfl",
  "name": "NFL",
  "period_type": "week",
  "current": {
    "season": 2026,
    "period": {"index": 2, "label": "Week 2", "key": "2026-2"},
    "data_through": {"season": 2026, "index": 1},
    "source_version": "2026-09-15",
    "stale": false, "stale_reason": null
  },
  "seasons": [1999, "...", 2026],
  "stat_definitions": {
    "rec":     {"label": "Rec", "format": "int", "group": "receiving", "higher_is_better": true},
    "rec_yds": {"label": "Rec Yds", "format": "int", "group": "receiving", "higher_is_better": true},
    "snap_share": {"label": "Snap %", "format": "pct", "group": "usage", "higher_is_better": true}
  },
  "market_definitions": {
    "receptions": {"label": "Receptions", "stat": "rec"},
    "anytime_td": {"label": "Anytime TD", "stat": null,
                   "note": "a binary claim, while `td` is a count"}
  },
  "scoring_presets": {
    "ppr":      {"label": "PPR", "weights": {"rec": 1, "rec_yds": 0.1, "rec_td": 6, "rush_yds": 0.1, "rush_td": 6,
                                             "pass_yds": 0.04, "pass_td": 4, "int": -2, "fum_lost": -2, "two_pt": 2},
                 "bonuses": []},
    "half":     {"label": "Half PPR", "weights": {"rec": 0.5, "...": "otherwise as ppr"}, "bonuses": []},
    "standard": {"label": "Standard", "weights": {"rec": 0, "...": "otherwise as ppr"}, "bonuses": []}
  },
  "scoring_note": "Scored from the components present. fum_lost and two_pt are null in the NFL source table and score 0; against nflverse's own PPR the median difference is 0.00, p99 2.00.",
  "teams": [{"slug": "buf", "abbr": "BUF", "name": "Buffalo Bills",
             "conference": "AFC", "division": "AFC East", "classification": null,
             "season": {"games": 1, "cleared": 1, "missed": 0, "tied": 0,
                        "points_for": 36, "points_against": 31, "markets": 4}}],
  "counts": {"players": 3970, "teams": 32, "market": 0, "games": 7293, "rungs": 0},
  "unresolved_ids": [{"id": "...", "name": null, "reason": "not in player_xwalk"}]
}
```

- **`stat_definitions` is the ONLY place stat labels, formats and groups
  exist.** Every stat key used in any file of this sport must be defined here,
  and the export asserts it — over the shapes its guard walks, which is the part
  this sentence used to overstate. `stat_keys_used` reads period rows, season
  totals, career, team splits and scoring presets; it does **not** read
  `prop_history`, which names MARKETS rather than stats and is covered by
  `market_definitions` below. Demonstrated 2026-09-18: a planted nonsense key
  inside `prop_history` was accepted while the same key in `career.stats` raised.
- **`market_definitions` is the second vocabulary, and it is not optional.**
  `prop_history` names markets — `receptions`, `anytime_td` — and none of those
  8 names is a `stat_definitions` key, across 672 players and 25,529 records.
  Each entry carries `label` and a `stat` that points at a `stat_definitions`
  key **or is null** when no published stat is the same claim: `anytime_td` is
  binary where `td` is a count, and `sacks` / `tackles_assists` settle on columns
  published only inside team splits. A null is a measured state, not a gap —
  pointing a market at a near-miss key would publish a false equivalence.
- **`main_line_definition`** (a-24) is the rule that picks
  `prop_history.games[].line`, verbatim from the producer, for the Method page.
  Optional in the contract: a sport with no prop history has no main line.
- **`format`** is one of `int`, `dec1`, `dec2`, `pct` or `signed_dec1`.
- **`group`** is free text owned by the sport. The site orders groups from
  `config/sports/{sport}.ts`, not from here.
- **`scoring_presets`** are weights plus threshold bonuses:
  `{"stat": "rec_yds", "at": 100, "points": 3}`. Their stat keys must exist in
  `stat_definitions`.
- **`current.stale`** is true when `data_through` lags the last completed period
  (the source is late), and `stale_reason` names the missing period.

## {sport}/players/index.json — kind `player_index`

The search and browse index. Aliases are included because they feed search
directly (§1.4).

```json
{"schema_version": 2, "generated_at": "...", "kind": "player_index", "sport": "nfl",
 "players": [
   {"id": "00-0036223", "slug": "jonathan-taylor", "name": "Jonathan Taylor",
    "position": "RB", "team": "IND", "first_season": 2020, "last_season": 2026,
    "aliases": ["j taylor"], "has_market": false}
 ]}
```

**Slugs** are lower-case, ASCII-folded and hyphenated from the display name, and
unique within the sport. **They are assigned once and recorded in a committed
registry, `web/slugs/{sport}.json` in this repo (id → slug).** The export only
ever appends to it: an existing entry is never changed.

When a registry is first seeded and namesakes collide:
- the **most regular-season career games** keeps the bare slug;
- ties go to the earliest `first_season`, then the lowest id;
- every other namesake gets `-{first_season}`, then `-{last 4 of id}` if that is
  still ambiguous.

After seeding, a new player gets the bare slug only if it is free, and otherwise
takes a suffix. Stability comes from the registry, not the rule, so a later
player overtaking on career games never moves a URL.

This replaces the first v2 rule, "earliest first_season keeps the bare slug",
before any URL was published. That rule gave `adrian-peterson` to the 2002
Bears back and suffixed the Hall of Famer.

v1 scope stands: players with offensive usage (3,971).

## {sport}/players/{id}/summary.json — kind `player_summary`

First paint. Identity, the season list, aggregates and the market pointer; no
game logs.

```json
{"schema_version": 2, "generated_at": "...", "kind": "player_summary", "sport": "nfl",
 "identity": {"id": "00-0036223", "slug": "jonathan-taylor", "name": "Jonathan Taylor",
              "position": "RB", "team": "IND",
              "ids": {"gsis": "...", "pfr": "...", "espn": "...", "sleeper": null, "yahoo": null, "pff": "..."},
              "aliases": ["j taylor"],
              "headshot_url": "https://static.www.nfl.com/image/upload/..."},
 "seasons": [{"season": 2025, "teams": ["IND"], "games": 17, "key": "nfl/players/00-0036223/2025.json"}],
 "season_totals": [{"season": 2025, "season_type": "REG", "games": 17,
                    "stats": {"rush_att": 0, "rush_yds": 0, "rec": 0, "snap_share_mean": 0.61}}],
 "career": {"season_type": "REG", "games": 85, "stats": {"rush_yds": 7696}},
 "market": {"key": "nfl/market/00-0036223/2026-2.json"}}
```

`market` is `null` when there is no current-period market.

`identity.headshot_url`: optional-in-meaning, always-present key; https URL hotlinked from the source (NFL: static.www.nfl.com); never stored in R2; null when unknown.

### `prop_history.games` and `prop_history.main` — the main line (a-24, P-player-03)

`stats[].rate` pools every ladder rung. `games` is one row per game per market on
the **main line** — the rule is `manifest.main_line_definition`, published verbatim
from `jobs/export_web.MAIN_LINE_DEFINITION`: the posted line whose de-vigged over
probability was closest to 0.5 at the close; sportsbook closes (DK/FD/MGM median,
else every book) where they exist, else the Kalshi then Polymarket mid in the last
hour before kickoff; never mixed within one game; quarter lines excluded; ties to
the lower line.

```json
"games": [{"stat": "receptions", "season": 2026, "week": 1, "date": "2026-09-13",
           "opponent": "BAL", "home": true, "line": 3.5, "p_over": 0.475,
           "provider": "kalshi", "books": null, "close_ts": 1789318306,
           "actual": 6, "result": "cleared"}],
"main": [{"stat": "receptions", "priority": true, "season": 2026,
          "last_10":     {"cleared": 5,  "n": 10, "rate": 0.5,    "interval": [0.2366, 0.7634]},
          "this_season": {"cleared": 1,  "n": 1,  "rate": 1.0,    "interval": [0.2065, 1.0]},
          "career":      {"cleared": 24, "n": 46, "rate": 0.5217, "interval": [0.3814, 0.6588]},
          "pushes": 0, "voids": 0}]
```

- `result` is `cleared | missed | push | void`. A push and a void count toward no
  rate; a player who played and recorded nothing settled at 0 and is `missed`.
- `n` is graded games. One claim per game, so the Wilson interval is on `n`.
- `provider` + `close_ts` are the provenance of the price that CHOSE the line.
- The ladder matrix (line × season, `k of n`) is `records`: one entry per
  (season, stat, line) carrying `cleared` of `n`. It is not duplicated.

## {sport}/players/{id}/{season}.json — kind `player_season`

```json
{"schema_version": 2, "generated_at": "...", "kind": "player_season", "sport": "nfl",
 "identity": {"id": "00-0036223", "slug": "jonathan-taylor", "name": "Jonathan Taylor"},
 "season": 2025,
 "periods": [
   {"season": 2025, "index": 3, "label": "Week 3", "season_type": "REG",
    "game_id": "2025_03_IND_TEN", "date": "2025-09-21", "team": "IND",
    "opponent": "TEN", "home": false,
    "stats": {"snaps": 49, "snap_share": 0.67, "targets": 3, "target_share": 0.09,
              "rec": 2, "rec_yds": 14, "rec_td": 0, "rush_att": 26, "rush_yds": 101,
              "rush_td": 1, "pass_att": 0, "pass_cmp": 0, "pass_yds": 0, "pass_td": 0,
              "int": 0, "fum_lost": null, "two_pt": null}}
 ]}
```

- Regular season and postseason are included, in chronological order.
- NFL postseason labels come from the game type: Wild Card, Divisional,
  Conference, Super Bowl.
- `snaps` and `snap_share` are null before 2013.

## {sport}/teams/{slug}.json — kind `team`

```json
{"schema_version": 2, "generated_at": "...", "kind": "team", "sport": "nfl",
 "identity": {"slug": "buf", "abbr": "BUF", "name": "Buffalo Bills"},
 "seasons": [1999, "...", 2026],
 "schedule": [{"season": 2026, "index": 1, "label": "Week 1", "game_type": "REG",
               "game_id": "2026_01_BUF_HOU", "date": "2026-09-13", "kickoff_ts": 1789318800,
               "home": false, "opponent": "hou", "opponent_abbr": "HOU",
               "points_for": 36, "points_against": 31, "result": "W",
               "spread": 1.5, "total": 44.5, "coach": "...", "opponent_coach": "..."}],
 "splits": [{"season": 2026, "season_type": "REG", "games": 1,
             "offense": {"points": 36, "pass_yds": 334, "rush_yds": 86, "...": "stat keys"},
             "defense": {"points_allowed": 31, "def_sacks": 3, "...": "stat keys"}}],
 "roster": [{"season": 2026, "id": "00-0034857", "slug": "josh-allen", "name": "Josh Allen",
             "position": "QB", "games": 1, "snap_share": 1.0, "target_share": 0.0,
             "carry_share": 0.2857, "has_page": true}],
 "coaches": [{"season": 2026, "head_coach": "Sean McDermott"}]}
```

- `opponent` is the opponent's team slug.
- `spread` is from this team's perspective; positive means this team is
  favoured.
- Team slugs are lower-case abbreviations of the current franchise:
  `lv`, `lac`, `la`.
- Every key in `offense` and `defense` must be defined in `stat_definitions`.
- **`roster[].games` is NULLABLE** (finding C-4). NFL answers it from snap counts and always emits a
  number. A sport with no appearance signal — no public college source records whether a player
  dressed — can only count games with a stat row, which is a lower bound that reads as an appearance
  count: 0 for 116 of 126 players on one real 2026 roster. The asymmetry is the argument. Every share
  beside it (`snap_share`, `target_share`, `carry_share`) was already nullable and degraded honestly;
  the one field that could not be null was the one the sport cannot produce.
- **`schedule[].opponent_abbr` is NULLABLE** (finding C-7). 54,974 of the two sides across 46,296
  college games from 2004 carry no abbreviation, mostly non-FBS opponents. Null rather than an
  invented code, which would look like an identity the sport does not have, and rather than an empty
  string, which renders as a gap that says nothing about why.
- **Both stay REQUIRED.** Nullable is not optional: the key is always present, and its value says
  whether the answer is known. An absent key and a null one are different statements.
- **`memberships` is this team's grouping per season**, and it is EMPTY for the NFL today. Membership
  is a per-season fact — 26 FBS teams changed conference between 2025 and 2026 — but `nfl_teams`
  carries one row per abbreviation with no season column, so no history exists in the store to
  publish. An empty array states that; an absent key would not. The **current** season's grouping is
  in the sport manifest, so a listing costs one fetch.

### `manifest.teams[]` carries the grouping and the current season (A14, C-3)

It was `{slug, abbr, name}`, which meant one figure per team cost 32 team-file reads on an index
page — the cost `counts` exists to remove. It now carries `conference`, `division`,
`classification` and a `season` summary.

- **`division` is published exactly as the source records it** and is never decomposed. nflverse's
  `team_division` already contains the conference — the eight values are `"AFC East"` through
  `"NFC West"`, checked rather than assumed — and no bare region is stored anywhere. Splitting it
  into `"West"` would publish a structure the sport does not record, and it would be the wrong shape
  for a sport whose groupings are conferences rather than conference-plus-region.
- **`classification` is null for the NFL.** It is the competitive tier, which this sport has not got.
- **`season` is components, not derived totals**: season sums, which the reader divides by `games`,
  and `cleared`/`missed`/**`tied`** rather than a record string, because integers cannot disagree
  with one another the way a string disagrees with its own parts.
- **`tied` was not requested and is required anyway.** `ScheduleGame.result` is already
  `W`/`L`/`T`/null, so a `{games, cleared, missed}` triple silently loses a drawn result:
  `cleared + missed` stops equalling `games` and nothing says why.
- **`points_for` / `points_against` are null before a team has played**, not 0 — no games is a
  different statement from no points.
- **`plays` is absent, not null.** It was requested and there is no source: the store holds no play
  count and no play-by-play table, so it needs exactly the ingest the request itself excluded. A
  permanently-null field would promise a figure that is never coming; approximating it from attempts
  and carries would publish an unsourced number that looks sourced.
- **`season.cumulative` is the season path the teams board draws (a-08).** One entry per
  regular-season week of the league's schedule, in order - 18 for 2026, taken from the schedule
  rather than hard-coded:

  ```json
  {"index": 2, "state": "played", "cleared": 2, "missed": 0, "tied": 0,
   "points_for": 77, "points_against": 62}
  {"index": 7, "state": "bye", "cleared": null, "missed": null, "tied": null,
   "points_for": null, "points_against": null}
  ```

  - **A played week carries the running total through that week. Every other state carries null
    in every value** - not zero, not the last value carried forward, and never a projection. A
    bye's value is knowable and is still null: it is not an observation, and the chart holds the
    line flat across it by reading `state`.
  - **`state` is one of `played`, `bye`, `unplayed`, `gap`, decided by the team page strip's own
    rule** (`TeamView.tsx` `slots()` over `lib/slotState.ts`), ported: the bye is the single
    regular-season week up to the team's last fixture with no fixture; two or more such weeks leave
    it unresolved and all are `gap`, as is any week past the last fixture. The strip's `off`/`live`
    split is one exported state, `unplayed` - it depends on the reader's clock, not the data.
  - **The last played entry equals the summary's own totals**, and the export refuses otherwise
    (`reconcile_path`). The axis ceiling is not published: it is the maximum over these arrays,
    and a separately published figure could disagree with them.
  - The contract types it as `TeamWeekPlayed | TeamWeekEmpty`, so a played week with a null or a
    bye with a zero fails the export.

## {sport}/market/{id}/{period_key}.json — kind `market`

Same content as v1's market file, with `sport`, `identity` and `period` added.
Distributions are still precomputed per preset in v2. §3.2 replaces them with
marginals plus a correlation matrix sampled in the browser, which is what makes
custom scoring possible. The basis labels (MARKET / DERIVED / ANCHORED) and the
validation note remain mandatory.

```json
{"schema_version": 2, "generated_at": "...", "kind": "market", "sport": "nfl",
 "identity": {"id": "...", "slug": "...", "name": "...", "position": "WR", "team": "buf"},
 "period": {"season": 2026, "index": 2, "label": "Week 2", "key": "2026-2"},
 "game_id": "...", "opponent": "det", "kickoff_ts": 0, "as_of": "...",
 "source": {"venue": "kalshi", "method": "...", "n_sims": 4000},
 "components": [{"stat": "rec", "basis": "MARKET", "rungs": []}],
 "game_lines": {"total": 51.5, "spread": 3.0, "source": "nflverse games"},
 "distributions": {"ppr": {"cdf": [], "thresholds": [], "quantiles": {}}},
 "validation": {"status": "...", "source": "..."}}
```

## {sport}/components/{season}.json — kind `components` (a-11, STAGED, not published)

The league-wide components table: one season, every in-scope player, one row per
player per period, in one file. It exists because a sortable leaderboard cannot
be assembled from per-player files (no route reads more than one summary), and
it is what the site's Analytics board, the players index's usage columns and
Fantasy's period leaderboard wait on.

```json
{"schema_version": 2, "generated_at": "...", "kind": "components", "sport": "nfl",
 "season": 2025,
 "columns": ["snaps", "snap_share", "targets", "target_share", "rec", "rec_yds", "rec_td",
             "rush_att", "rush_yds", "rush_td", "pass_att", "pass_cmp", "pass_yds",
             "pass_td", "int", "fum_lost", "two_pt", "ret_td", "team_targets", "team_snaps"],
 "players": [..., {"id": "00-0036223", "slug": "jonathan-taylor", "name": "Jonathan Taylor",
              "position": "RB"}, ...],
 "rows": [{"player": 264, "index": 3, "season_type": "REG", "team": "IND",
           "values": [45, 0.79, 4, 0.16, 3, 16, 0, 17, 102, 3, 0, 0, 0, 0, 0, 0, 0, 0, 25, 57]}]}
```

(The real 2025 week 3 row from the staged export; `player` 264 is his index in `players`.)

- **Player-period grain, not player-season.** A scoring preset's bonuses apply
  per period, so a season sum cannot be scored; the page sums rows itself.
  Nothing derived is stored: no totals, rates or points.
- **One file per season.** A modern season is ~1.1 MB raw, ~135 KB gzipped
  (measured 2026-09-22); all 28 seasons in one file would be 27.1 MB raw, 2.9 MB gzipped.
- **It is a projection of the `player_season` files**: same scope, same rows,
  same nulls. Verified on the real data at 209,283 rows, 0 differing values.
- **`values` is positional against `columns`.** An absent key in a season file
  is written 0, which is exact: the season file drops a key only when it is zero
  in every period, and a null keeps its key. A null stays null.
- **`team_targets` and `team_snaps` are the share denominators.** A share over
  any span is `sum(targets) / sum(team_targets)`, never a mean of per-period
  shares. `team_targets` is the team's targets over every stat row (reproduces
  nflverse's `target_share` on 375,672 of 375,711 rows); `team_snaps` is the max
  offensive snaps of any player in that team-game (equals the total implied by
  `offense_pct` in 7,165 of 7,188 team-games). Both are null where their part is.
- **Not in a default export.** `components` is in `OPTIONAL_PARTS`, built only by
  `--only components`, so `weekly_refresh` cannot publish it. Stage with
  `--dest`; moving it into `PARTS` is the publish decision.

## The extended profile — defence, special teams, identity (a-14, STAGED, not published)

Track B's A-B2, A-B3 and A-B4. Built only by `python -m jobs.export_web --extended --dest
<staging>`; `--extended` without `--dest` is refused, so the weekly refresh cannot produce it.
It adds no kind and no key pattern: it widens the player scope and adds fields to kinds that
exist. The default export is byte-identical with and without a-14 (23,293 files compared on one
store, 0 differing).

- **Scope.** Offensive usage (v1) OR any non-zero published defensive or special-teams stat in a
  regular-season week. 3,987 → 10,996 players on the staged store. The slugs are appended to a
  COPY of the registry beside the staging tree (`<dest>-staged-slugs/nfl.json`), never to
  `web/slugs/`.
- **Period stats** gain, where the season justifies them (the per-row key-set rule):
  the eleven `def_*` keys team splits already use; `defense_snaps`, `st_snaps`; `fg_att`,
  `fg_made`, `fg_missed`, `fg_blocked`, `fg_made_{0_19,20_29,30_39,40_49,50_59,60_plus}`,
  `fg_missed_{same bands}`, `pat_att`, `pat_made`, `punt_ret`, `punt_ret_yds`, `kick_ret`,
  `kick_ret_yds`. All defined in the manifest's `stat_definitions` (groups `usage`, `kicking`,
  `returns`; the `def_*` keys keep `team_defense`). Season totals and career sum them.
- **Null, not zero.**
  - Every kicking and return column is populated 1999-2026, the release's own first season.
  - The `def_*` runs already in `NOT_COLLECTED` apply (TFL 2003-11, QB hits 2003-05).
  - `defense_snaps` and `st_snaps` are null before 2013, and appear only for a player who
    played that phase somewhere in his career.
  - A stored NULL in an extended column publishes null, never 0. nflverse publishes none, so
    a NULL means the row was not re-derived.
- **Blocked kicks are not misses.** `fg_att = fg_made + fg_missed + fg_blocked`. The bands split
  made and missed only, so a band reads made / (made + missed), which is attempts less blocks.
- **`identity.jersey_number`, `identity.birth_date`, `seasons[].jersey_number`**, all optional
  and nullable.
  - The jersey is a one- or two-digit STRING, so `0` and `00` differ. It is the season's last
    roster week; null before 2002 and for 10 roster values carrying a letter (`69B`).
  - `birth_date` is ISO, from the players release, preserved like the external ids. Age is
    computed by the reader.
- **`RosterEntry.defense_snap_share`**, optional and nullable: the defensive twin of
  `snap_share`, computed the same way.
- **`market_definitions.sacks.stat` = `def_sacks`** under this profile (settlement reads that
  column). `tackles_assists` stays null: it is a sum of three keys.
- **Not built with `components`.** Which extended columns the league table carries is undecided.

## Staged features — fixtures, air yards, red-zone looks (a-15, STAGED, not published)

Track B's A-B5 and A-B8. Built only by `python -m jobs.export_web --stage fixtures --stage air_rz
--dest <staging>`; `--stage` without `--dest` is refused, like `--extended`, and a staged build
appends slugs only to the copy beside the tree. Publishing a feature is adding its name to
`DEFAULT_STAGES` in `jobs/export_web.py`. The default export is byte-identical with and without
a-15 (23,293 files compared on one store, 0 differing). Each stage composes with `--extended`.

**`fixtures` — `current.fixtures[]` on the sport manifest (A-B5).** Every game of
`current.period`, league-wide, in kickoff order: `{game_id, kickoff_ts, home, away, spread,
total}` (contract `$defs.Fixture`). `home`/`away` are team slugs. OPTIONAL: an absent key means
this producer does not publish fixtures; `[]` would mean a period with no games.

- **`spread` is the HOME team's expected margin: positive = home favoured.** Stated in the
  `$def`'s own description. This is NOT the sportsbook convention (favourite carries the minus).
- **NFL source convention, measured:** nflverse `spread_line` is already home-positive
  (2023_01_DET_KC +4.0 with KC -198 at home; Super Bowl LIX, PHI designated home, -1.5 with
  away KC -120), so NFL passes it through (`fixture_spread`). Across the store the spread and the
  moneyline name the same favourite in 5,306 games; the 21 that disagree all sit at |spread| = 1.0
  on near-even prices.
- **No other sport emits it.** CFBD's line is home-NEGATIVE (c-14), so a CFB emitter must negate
  and must establish that convention itself; nothing here assumes a second source shares
  nflverse's.
- For an unplayed game the line is the source's at export and can still move. It is not a close.

**`air_rz` — three period stat keys (A-B8)**, on player period rows, season totals and career,
and, with `--only components`, appended as the LAST three `columns` of the components table (no
existing column moves). Definitions, with the exact wording the page renders, ship in
`stat_definitions` under this stage only:

| key | label | what was counted | first season | null |
|---|---|---|---|---|
| `rec_air_yds` | Air Yds | nflverse `receiving_air_yards`: distance past the line of scrimmage of every pass thrown to him, caught or not. Can be negative | 2009 | 1999-2008 (1999-2002 PARTIAL: ~6% of a season, non-zero; 2003-2008 zero) |
| `rz_targets` | RZ Tgt | play-by-play targets with `yardline_100 <= 20` at the snap: play_type pass (or untyped) naming him as receiver, two-point attempts excluded | 1999 | 2003-2008 (incompletions name no receiver) |
| `rz_rush_att` | RZ Car | play-by-play carries with `yardline_100 <= 20`: play_type run / qb_kneel (or untyped) naming him as rusher, two-point attempts excluded | 1999 | none by season |

- **A red-zone look is a subset of a look.** The target and carry definitions reproduce
  `stats_player_week` per player-week (`research/redzone_looks.py`: at most 3 player-weeks off by
  one per season on targets and 1 on carries, outside targets 2003-2008). So `rz_targets <=
  targets` and `rz_rush_att <= rush_att` on every row (0 violations in 209,283 staged rows).
- **Null, not zero.** A period whose game play-by-play has not reached is null (the live
  season's pbp can lag its weekly stats), as is a row with no joinable game. A stored NULL
  `receiving_air_yards` (row not re-derived) is null. A covered game with no look is 0.
- Store: `nfl_player_week.receiving_air_yards`; `nfl_pbp_looks` (per player-week, derived from
  the archived pbp file; `ingest_nflverse --from-archive --dataset pbp` rebuilds it).

## research/*.json

The v1 kinds are unchanged (`research.hypotheses`, `research.calibration`,
`research.execution`), with `schema_version: 2` and `sport: null`. Research is
cross-sport and served at `/research`.

## live/{sport}/prices.json — kind `live.prices` (unit a-09)

Exchange prices for the live page, written by the **logger**, not by the export.
The Worker reads this from R2 instead of calling the exchange, which refused
every read from Cloudflare's egress (b-11: HTTP 429, 15 of 15).

- **Written by one process only.** The logger PUTs this one key straight to the
  site bucket. It never appears in `WEB_EXPORT_DIR` or in the upload record, and
  `export_web.upload()` refuses any declaration that reaches `live/`, never
  uploads a local `live/` file, and never deletes under it.
- **Every price carries `read_at`**, the start of the poll that read it - never
  later than the truth, and never the publish or upload time. `expected_every_s`
  is the cadence that market was being polled at, so an age can be judged:
  ten minutes is normal on the 600 s cold tier and a stall on the 10 s live one.
- **The file states its own staleness.** `stale_after` = `generated_at` +
  `heartbeat_s` + `publish_every_s` + 2 s. The producer writes when a newer read
  exists (at most every `publish_every_s`) and at least every `heartbeat_s`, so a
  `stale_after` in the past means the producer has stopped - whatever the prices
  say. What to render then is the Worker's decision.
- **Bounded.** Only `series` (the live page's `gameSeries`), capped at
  `LIVE_PRICES_MAX_MARKETS`; anything over the cap is counted in
  `counts.omitted`. No mid and no de-vig - the site derives the mid.
- `in_catalogue` is false once the market has left the exchange's open list;
  the price is then the last one read and will not update. Settled results are
  NOT in this file.
- **Not covered:** the featured game's candle path, which the Worker still
  reads from the exchange.
- **Off by default** (`LIVE_PRICES_ENABLED=0`). Measured write budget
  (`python -m research.live_prices_writes`, 09-10 to 09-22): 336-3,488 PUTs a
  day, mean 1,618, an upper bound; the ceiling is 5,760 a day at 15 s.

## coverage.json — kind `coverage` (A-C7, adopted a-05/a-12)

Sportless, one file. Every sport the site declares, each with `stats`, `odds` and
`context` - each a holding or `null`, never an empty holding and never a zero.
`stores[]` names what was read, which is what makes a null a measured absence.
Each source carries `span` (event instants) or `event_dates` (calendar dates,
A-C12, for a table with no time of day), never both. Producer:
`jobs/export_coverage.py` (track C). **In the contract, not published:** the
producer refuses `WEB_EXPORT_DIR`, and no scheduled job runs it. A-C11's
per-sport `status` is filed and NOT yet in the contract.

## predictors/{sport}/{predictor}.json — kind `predictor` (F6, adopted a-05/a-12)

A predictor and its scoring record in one file, keyed by slice. The schema
enforces the record rule: a `published` slice must carry `record` (an object,
never null), `reading` and `statement`; a record with `score: null` must give a
`reason`, and one with a score must not; a `withheld` slice names at least one
reason, carries no measured field, and has `reading` and `statement` null and
`values_reason: withheld`. The file carries `attribution` (the credit a source's
terms require; render its `statement` wherever a slice in `applies_to` is shown)
and `values_policy` (`show`/`hide`, Ethan's editorial call). Producer:
`analytics/predictor_export.py` (track F). **In the contract, not published:**
`predictors/` has no REFRESHED declaration threaded to the uploader yet, so a
retired predictor could never be deleted from R2.
`tests/test_contract_coverage_predictor.py` fails if a scheduled job runs
either producer before that exists.

## {sport}/sources.json — kind `sources` (a-22, audit S-04)

GENERATED from `jobs/source_registry.py`, never written. `sources[]` lists every
upstream source a file of this sport reads — `source_id`, `name`, `layer`
(FACTS, PRICES, BELIEFS, CONTEXT, HEADLINES), `provides`, `used_for`, `read_by`
(the kinds that read it) and `last_read`, which is measured at export time and is
null only with a `last_read_basis` saying why. `kinds` maps every contract kind
to the ids it reads for this sport, so a page can refuse a kind whose sources the
file does not list. `not_connected[]` is what the producer does not export, each
with a `state` — `not_ingested`, `ingested_not_exported`, `not_built` or
`declined` — and the `evidence` to check it against.

Written by the manifest part, which owns no prefix. **The gate:** `sync_keys`
refuses any file whose `(sport, kind)` has no declaration in the registry, and
that is the one write path for `export_web`, `export_cfb_web` and (from a-30)
`export_mlb_web`. Declarations are PER SPORT: `kinds` in `cfb/sources.json` is
the college producer's own, never the NFL's filtered by sport.

**What is derived and what is typed (a-30).** A kind's sources are derived from
the tables its producer functions read - scanned by AST, case-insensitively,
through calls into `store.py` and research modules - mapped through one
hand-written table -> source map per sport. A function that reads and is not
attributed to a kind, a table name the scan cannot read (an f-string hole), or an
unmapped table refuses the registry. At run time a sqlite authorizer on the
producer's connection records every table actually read, and `sync_keys` refuses
after an unmapped one. A kind built from another kind's output (the index's
`has_market`) inherits that kind's sources. Still typed by hand: reads of
committed result files, the producers that do not write through `sync_keys`
(analytics, predictor, coverage, live.prices), and `read_by` entries of the form
`page:<name>` - a source a page reads at request time through no file (ESPN's
scoreboard, today).

## Refresh

`python -m jobs.weekly_refresh` logs to
`config.storage_path("logs", "weekly_refresh.log")`:

1. nflverse ingest;
2. `map_markets --venue kalshi`;
3. export to `WEB_EXPORT_DIR`;
4. upload changed keys to R2, where changed means the content hash differs from
   the last upload record;
5. fetch `/data/{sport}/manifest.json` from the live site and confirm its
   `generated_at`.

**A data refresh commits nothing and triggers no build.** When nflverse is late,
the export still runs and sets `current.stale`.
