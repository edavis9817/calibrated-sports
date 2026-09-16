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
  "scoring_presets": {
    "ppr":      {"label": "PPR", "weights": {"rec": 1, "rec_yds": 0.1, "rec_td": 6, "rush_yds": 0.1, "rush_td": 6,
                                             "pass_yds": 0.04, "pass_td": 4, "int": -2, "fum_lost": -2, "two_pt": 2},
                 "bonuses": []},
    "half":     {"label": "Half PPR", "weights": {"rec": 0.5, "...": "otherwise as ppr"}, "bonuses": []},
    "standard": {"label": "Standard", "weights": {"rec": 0, "...": "otherwise as ppr"}, "bonuses": []}
  },
  "scoring_note": "Scored from the components present. fum_lost and two_pt are null in the NFL source table and score 0; against nflverse's own PPR the median difference is 0.00, p99 2.00.",
  "teams": [{"slug": "buf", "abbr": "BUF", "name": "Buffalo Bills"}],
  "counts": {"players": 3971, "teams": 32, "market": 11},
  "unresolved_ids": [{"id": "...", "name": null, "reason": "not in player_xwalk"}]
}
```

- **`stat_definitions` is the ONLY place stat labels, formats and groups
  exist.** Every stat key used in any file of this sport must be defined here,
  and the export asserts it.
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

## research/*.json

The v1 kinds are unchanged (`research.hypotheses`, `research.calibration`,
`research.execution`), with `schema_version: 2` and `sport: null`. Research is
cross-sport and served at `/research`.

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
