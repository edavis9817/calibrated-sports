# C13 - what has to be true for `/cfb` to come off the gate

Track C, unit c-13, 2026-09-23. ESTABLISH AND SPECIFY: nothing here publishes, flips a flag or
deletes a key. The web half is filed to track B as `docs/track-b-requests.md` section
"From track C - c-13"; the producer half is the P-list below.

Evidence (outside the repo, reproducible by the commands quoted): `D:/temp/c13-evidence/`.
Web code read at the **deployed** commit, `9884f96` (`/build.json`, built 2026-09-22T23:35Z).

---

## 1. What is in the bucket under `cfb/` (measured)

Listed with `list_objects_v2(Prefix="cfb")`, read-only, and fetched over HTTP from
`calibratedsports.com/data/...`:

| kind            | key                         | count | last modified (UTC)   |
|-----------------|-----------------------------|-------|-----------------------|
| sport_manifest  | `cfb/manifest.json`         | 1     | 2026-09-23 01:22      |
| player_index    | `cfb/players/index.json`    | 1     | 2026-09-23 01:22      |
| team            | `cfb/teams/<slug>.json`     | 138   | 2026-09-23 01:22      |

- **140 keys**, and not one key begins `cfb` without being under `cfb/`. The bucket set equals the
  local `WEB_EXPORT_DIR/cfb` set and the upload record's `cfb/` set. 140 of 140 are
  byte-identical over HTTP.
- `analytics/cfb`, `research/cfb`, `live/cfb` and `components/cfb` hold **0** keys.
- `sports.json` (generated 2026-09-15) lists `nfl` only.

## 2. What each `/cfb` route reads, and what it would find

Every CFB route tests `config.awaitingExport` **before** it reads anything, so today the site
requests exactly one CFB key: `cfb/manifest.json`. The Shell requests it client-side on `/cfb`
for the period crumb. With the flag off (rendered, see section 4):

| route                  | declared | reads                                        | finds         |
|------------------------|----------|----------------------------------------------|---------------|
| `/cfb`                 | yes      | `cfb/manifest.json`                          | yes           |
| `/cfb/players`         | yes      | `cfb/manifest.json`, then `cfb/players/index.json` (client) | yes, **0 players** |
| `/cfb/teams`           | yes      | `cfb/manifest.json`                          | yes, `season: null` on 138/138 |
| `/cfb/analytics`       | yes      | nothing (a mock; reads no key)               | -             |
| `/cfb/live`            | yes      | nothing: cfb declares no `live` feed         | -             |
| `/cfb/team/[slug]`     | **no**   | manifest + `cfb/teams/<slug>.json`           | would find all 138 |
| `/cfb/player/[slug]`   | **no**   | index, `cfb/players/<id>/summary.json`, market | none exist; index is empty |
| `/cfb/fantasy`         | **no**   | manifest (`scoring_presets`)                 | `{}`          |

**No key that a declared page requests is missing.** The gate is not a missing file. It is
what the pages SAY about the files that exist.

The reverse direction: **138 of 140 published keys (the team files) are read by no route**,
because `team` is not declared. That is a **missing page**, not dead weight. The files carry
schedule, splits and roster that the teams board cannot show. But the page must not be declared
on today's files: see P1 and P2. `cfb/players/index.json` is read and is empty on purpose
(finding C-5: the index is the page list). `sports.json` is read by no route on the site at all
(`sportsKey` is defined in `lib/keys.ts` and called nowhere), so its missing `cfb` entry is not
a gate item.

## 3. The runbook's warning: true, and it understated the problem

`docs/runbooks/cfb-publish.md` says that with the flag off, `/cfb` "renders the NFL SportHome,
which states '0 players.', the Kalshi between-slates copy, and links to `/cfb/fantasy` and
`/cfb/news`". Rendered against the published manifest with the flag off, **all three hold**.
Two corrections:

- **It is the shared `SportHome`, not an NFL one.** The component is sport-agnostic, as CI
  requires. What is NFL-shaped is its **copy and its card list**, which are hard-coded. So the
  fix is in one shared component, not in a CFB fork.
- **It is worse than three items.** The same render also states "The whole record for every
  player who has taken the field since 2004 - game logs, usage and season totals". That is false:
  CFB publishes no player. It states "7 pages sit under CFB", and 5 are declared. It shows "Markets
  priced - between slates" and "Ladder rungs - no ladders listed", which imply a slate that will
  come; for CFB none exists on the exchange (CLAUDE.md, Kalshi CFB).
- **`/cfb/players` is the bigger problem than `/cfb`.** Off the gate it reads "0 players in the
  record" and "Every other player in the record has a page". It also shows Offense / Defense /
  Special teams leader tabs for a sport that exports none of them.
- **"The only new reader-visible surface is the raw JSON" is slightly wrong.** `Shell.tsx`
  fetches `/data/{sport}/manifest.json` on the sport home without checking the flag. Since
  09-23 that returns 200, so `/cfb`'s address strip should now read `CFB / Week 3`. This
  comes from reading the code; the effect runs client-side and was not seen in a browser.

## 4. Links to undeclared routes: one line each

Found by rendering every CFB route with the flag off (probe below) and grepping every
`/${sport}/...` href in `components/` and `app/`:

1. **`/cfb/fantasy`** - `components/views/SportHome.tsx`, card 04 (`entries[3]`), rendered on
   `/cfb` once the flag is off. Declaring it would take `"fantasy"` in `pages`, and the page would
   have nothing to show: `scoring_presets` is `{}` and the page's only content is the presets. b-03
   declined it for that reason. **The fix is not to declare it but to stop linking it** (B-C13-1).
2. **`/cfb/news`** - `SportHome.tsx`, card 06, same condition. Declaring it would take `"news"` in
   `pages` plus a CFB source list for `news/feed`. It is not among v7's CFB pages. **Stop linking
   it** (B-C13-1).
3. **`/cfb/team/<display name>`** (e.g. `/cfb/team/East Carolina`) - `TeamView.tsx`
   `Schedule()`, only once `team` is declared. On Alabama 12 of 12 opponent links, and 0 of them
   resolve. This takes producer P2 (`opponent` must be a slug) **and** web B-C13-5 (link only an
   opponent that is in `manifest.teams`: non-FBS opponents have an abbreviation and no page).
4. **`/cfb/team/<slug>`** - the teams board rows. They are **not** links today: the board checks
   `pages.includes("team")`. Declaring the route takes `"team"` in `pages` after P1, P2 and P3.
5. **`/cfb/player/<slug>`** - `PlayersIndex.tsx` rows and the `TeamView` roster. **Not
   reachable**: the index is empty and the roster renders 0 player links. Nothing to declare.

## 5. Whose problem it is: BOTH, split as below

**Minimum to flip for home and teams:** B-C13-1 through B-C13-4, plus P5. Players stays gated by
B-C13-3. **To declare the team page as well:** P1, P2 and P3, then B-C13-5. P4 is quality, not a
gate.

### Producer (track C unless noted)

- **P1 - EVERY PUBLISHED CFB SPREAD HAS THE WRONG SIGN.** `export_cfb_web` applies nflverse's
  rule, `spread if home else -spread`, which is right for nflverse (positive = home favoured).
  CFBD's convention is the opposite: in `cfb_game_lines`, corr(spread, home margin) = **-0.696**
  over 38,577 scored games, and the home moneyline agrees with "spread < 0 = home favoured" on
  7,710 of 7,969. The contract says positive = THIS team favoured. Across the 138 published team
  files, corr(published spread, this team's margin) = **-0.719** over **21,374** rows. Of 10,009
  rows the file marks "favoured", the team won 2,611. Alabama is published at -18.5 for a home
  game it was favoured in by 18.5. The site renders that as "+18.5".
  *Fix:* negate once at the CFB source (`spread_team = -spread if home else spread`).
  *Accept:* a test with a fixture CFBD row (home spread -7.0) asserts the home side exports +7.0
  and the away side -7.0; and a check over the exported tree asserts corr(spread, margin) > 0.
  **Exposure today:** raw JSON at `/data/cfb/teams/*.json` only. No page renders a CFB line.
  **FIXED IN CODE, c-14 (2026-09-23); NOT YET REPUBLISHED.** `export_cfb_web.team_spread`.
  The page is not a second sign error: `lib/format.spreadLine` renders the contract's
  positive-favoured number the sportsbook way (favourite carries the minus), so exported -18.5
  printing "+18.5" is one exporter error seen through a correct formatter; after the fix
  Alabama exports +18.5 and prints "−18.5". Over the same 138 files the staged re-export reads
  corr **+0.719** (21,378 rows with a line and a score: 21,374 flipped, 4 pick'ems unchanged,
  no other field moved). The served tree keeps the old sign until the 138 changed team keys are
  uploaded through the runbook.
- **P2 - `ScheduleGame.opponent` is a display name; the contract says slug** (c-12's finding,
  confirmed: 12 of 12 on Alabama).
  *Accept:* every `opponent` matches `^[a-z0-9][a-z0-9-]*$`, and every opponent that is an
  exported team equals that team's `slug`.
- **P3 - `opponent_abbr: ""` on one row; the contract now allows null and warns against `""`**
  (c-12). *Accept:* 0 rows with `""`.
- **P4 - `manifest.teams[].season` is null for 138/138**, so the teams board is 138 rows of
  marked dashes (417 `data-placeholder` cells rendered). The store can answer it (Alabama is 3-0
  on its own schedule). Since a-08 merged, a non-null `season` REQUIRES `cumulative`, one entry
  per week of the league's schedule. *Accept:* 138 non-null; the last played `cumulative` entry
  equals `season`'s totals (the producer's existing refusal); the board renders 0 "no season
  figures" rows.
- **P5 - freshness. A published CFB tree claims `stale: false` for ever.** `current.stale` is
  computed when the export runs, and nothing re-runs it: `export_cfb_web --dest web` is not in
  `weekly_refresh` (the runbook's "After the first publish" section). Week 4 kicks off 09-24 and
  has 285 games in the store with 0 scored. From ~09-28 the published manifest will still say
  "Week 3" and not stale. *Owner:* whoever owns `jobs/weekly_refresh.py` (track A). *Accept:* a
  `weekly_refresh` dry run after a week-4 ingest lists `cfb/manifest.json` as changed, with
  `current.period.label == "Week 4"`.
- **Not gate items:** `sports.json` without `cfb` (no route reads it; track A's to emit);
  `counts.players: 0` (correct under C-5, and handled by keeping players gated).

### Web (track B): see `docs/track-b-requests.md`, "From track C - c-13"

## 6. The five-minute flip, once the items land

1. `P5` has run once: `/data/cfb/manifest.json` `current.period.label` names the latest scored
   week (check against `cfb_games`, `mode=ro`).
2. On a web preview build with the flag off: render `/cfb`, `/cfb/teams`, `/cfb/players`,
   `/cfb/analytics`, `/cfb/live`. Every `/cfb/...` href passes `gatePage`, and no rendered text
   contains `0 players`, `exchange lists a ladder`, `between slates` or `with a page`.
3. `tests/routing.test.ts:36` asserts `cfb.awaitingExport === true` and the page set. The flip
   changes that test in the same commit, deliberately.
4. After deploy, fetch the five routes over HTTP: 200, and none contains "Export not published"
   except `/cfb/players` if B-C13-3 keeps it gated.

## How the render was measured

A vitest probe in a **detached scratch worktree** of the web repo at `9884f96`, never committed.
It mocks `config/sports/cfb` to `awaitingExport: false` and `lib/serverData` to return the
published manifest (byte-identical to the served one). It renders each route's page component,
plus `TeamView` on `alabama-crimson-tide.json` with `team` added to `pages`. The output is
`D:/temp/c13-evidence/render_probe.json`. It is a server render: client effects (the players
index fetch, the Shell crumb) do not run in it.
