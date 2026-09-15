# Web data contract — schema_version 2

The contract between `jobs/export_web.py` (this repo) and the site
(`calibratedsports-web`). It implements `calibratedsports-web/docs/site-architecture.md`
§1 (contracts) and §1.2 (R2, brought forward). §3 (components tables, custom
scoring, client-side distribution sampling, incremental export and
content-hash keys) is NOT in v2 and will add to it.

v1 (static files in `public/data`, sport-less paths, fantasy points computed at
export) is retired. Changing a field's meaning or removing one bumps
`schema_version`; adding an optional field does not.

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
unique within the sport. When names collide, the player with the earliest
`first_season` keeps the bare slug (ties broken by id), and each other player
gets `-{first_season}`, then `-{last 4 of id}` if still ambiguous. A newcomer
therefore never renames an existing page. v1 scope stands: players with
offensive usage (3,971).

## {sport}/players/{id}/summary.json — kind `player_summary`

First paint. Identity, the season list, aggregates and the market pointer; no
game logs.

```json
{"schema_version": 2, "generated_at": "...", "kind": "player_summary", "sport": "nfl",
 "identity": {"id": "00-0036223", "slug": "jonathan-taylor", "name": "Jonathan Taylor",
              "position": "RB", "team": "IND",
              "ids": {"gsis": "...", "pfr": "...", "espn": "...", "sleeper": null, "yahoo": null, "pff": "..."},
              "aliases": ["j taylor"]},
 "seasons": [{"season": 2025, "teams": ["IND"], "games": 17, "key": "nfl/players/00-0036223/2025.json"}],
 "season_totals": [{"season": 2025, "season_type": "REG", "games": 17,
                    "stats": {"rush_att": 0, "rush_yds": 0, "rec": 0, "snap_share_mean": 0.61}}],
 "career": {"season_type": "REG", "games": 85, "stats": {"rush_yds": 7696}},
 "market": {"key": "nfl/market/00-0036223/2026-2.json"}}
```

`market` is `null` when there is no current-period market.

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
