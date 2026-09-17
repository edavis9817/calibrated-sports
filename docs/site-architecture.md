# Calibrated Sports — site architecture

Single source of truth. Supersedes and consolidates the three separate briefs (multi-sport contracts,
scale and feel, data system) plus the decision log. Corrections from the audit are applied inline
rather than listed — what's written here is what to build.

**Save as `docs/site-architecture.md` in `calibratedsports-web`.**

Governing principle: **generalise the contracts, not the implementations.** Contracts are cheap to get
right now and painful later; implementations are the reverse.

---

## 0. Status

Live at `calibratedsports-web.ethanad17.workers.dev`, Workers static assets, one sport, Next.js static
export. **12,076 of the 20,000 asset cap used** — 60%, with one sport and a partial roster. That
measurement is why §2 is urgent rather than deferred.

Planned: 5–6 sports, ~15,000–20,000 players, several page types per sport, user-defined fantasy
scoring. Betting research is closed and parked.

---

## 1. Contracts

These three are cheap now and expensive after launch. **No custom domain until they're done** — once
`calibratedsports.com` serves a URL, that URL is owned forever.

### 1.1 URLs — sport first

```
/{sport}                  home / leaders
/{sport}/players          index + search
/{sport}/player/{slug}    detail
/{sport}/teams            index
/{sport}/team/{slug}      detail
/{sport}/fantasy          only where a scoring system exists
/{sport}/stats            leaderboards
/research                 cross-sport
```

Eight route patterns, not eight per sport. **The sport config declares which pages exist** and nav
renders from it — otherwise you ship an empty fantasy page for a sport with no fantasy scoring.

Slugs are unique only within a sport; the sport segment disambiguates. No global slug namespace.

### 1.2 Storage — sport as key prefix, on R2

```
r2://<bucket>/
  sports.json                            # which sports exist + manifest URLs
  {sport}/manifest.json                  # stat_definitions, period types, scoring presets, schema_version
  {sport}/players/{id}/summary.json      # first paint, career aggregates
  {sport}/players/{id}/{season}.json     # game logs, split by season
  {sport}/teams/{slug}.json
  {sport}/components/{season}.json       # powers every leaderboard
  {sport}/market/{id}/{week}.json        # distribution inputs
```

R2, not `public/data/`: a data refresh then never triggers a build, the asset count stays flat, and
the 25 MiB per-file ceiling stops mattering. Configure CORS for the site origin.

**Split game logs by season.** NFL is ~17 games; MLB is ~162. A ten-season baseball player is 1,620
rows and nobody needs it on first paint.

`calibrated-sports-raw` already exists for the logger's raw shards — the site data gets its **own**
bucket, don't reuse that one.

### 1.3 The export envelope — self-describing

**The site must never contain `if (sport === 'nfl')`.** Add a CI grep: `'nfl'` must not appear in any
component outside `config/sports/`.

`stat_definitions` lives **once per sport in `{sport}/manifest.json`** — not in every player file,
which would ship ~40 definitions 3,000 times:

```json
{
  "schema_version": "1",
  "sport": "nfl",
  "period_type": "week",
  "stat_definitions": {
    "rec":     { "label": "Rec", "format": "int", "group": "receiving", "higher_is_better": true },
    "rec_yds": { "label": "Yds", "format": "int", "group": "receiving", "higher_is_better": true }
  },
  "scoring_presets": { "ppr": {...}, "half": {...}, "standard": {...} }
}
```

Player files carry only keys:

```json
{
  "schema_version": "1", "generated_at": "...", "sport": "nfl",
  "identity": { "id": "...", "slug": "...", "name": "...", "position": "...", "team": "..." },
  "periods": [ { "season": 2026, "index": 2, "label": "Week 2", "opponent": "MIA",
                 "stats": { "rec": 6, "rec_yds": 84 } } ]
}
```

`period_type` is `week` for football, `game` or `date` for baseball — **there is no universal "week."**

The site validates `schema_version` on load and renders an explicit "data format changed" state. A
silent shape mismatch is what makes a site feel broken.

### 1.4 Identity

`player_xwalk` and `player_alias` need a sport discriminator (the CLAUDE.md invariant should cover it
— verify, don't assume). Ids collide across sports; the key prefix keeps that harmless.

Player aliases feed the search index directly — `CMC`, `SGA`, `ARSB` are how people actually search,
and no fuzzy match on a full name gets you there.

### 1.5 Per-sport config, not per-sport components

One file per sport — `config/sports/nfl.ts`: display name, stat groups and order, default sort, which
page types exist, scoring systems, and the prominence threshold for §3.3. Components read the config.

---

## 2. Rendering model — edge-rendered, not statically exported

**Urgent, not deferred.** The live deploy uses 12,076 of 20,000 assets with one sport. Six sports is
15,000–20,000 player pages before history, team pages or code chunks. And Next.js `output: 'export'`
**cannot** do runtime dynamic routes — it requires `generateStaticParams` enumerating every player at
build time. Static export isn't a tight fit needing tuning; it's structurally wrong for this site.

**Render player and team pages on demand at the edge.** Static assets become code, fonts and images
only — a few hundred files, constant regardless of how many sports or players exist. Build time stops
scaling with the data.

Path: keep the Next.js app, the brand tokens, the chart primitives and `assertNotSigned()`; swap
`output: 'export'` for the **OpenNext Cloudflare adapter** on Workers. Runtime dynamic routes, real
indexable URLs, per-page meta tags, plus Cron Triggers for the refreshes and a runtime for a login
later.

**The exit, decided in advance:** OpenNext is a community adapter tracking a fast-moving framework. If
a Next.js upgrade breaks it you are blocked on someone else's release cycle. If it fights for more
than about a day, fall back to a thin Worker rendering from templates against the same R2 JSON — the
pages are tables, charts and a hero, so most component work survives. **Nothing in §1 or §3 depends on
Next.js being the renderer.** That is the insurance.

---

## 3. Data system

> **Never store fantasy points. Store stat components and compute points in the browser.**

Scoring is a dot product — `Σ (weight × stat)` — plus threshold bonuses. With components in the
browser, any user-defined scoring is microseconds of arithmetic and no round-trip. Precomputing points
means one stored copy per scoring system, which makes custom scoring impossible by construction. It is
also *less* data than the alternative.

### 3.1 Components

```
nfl:  pass_yds pass_td int rush_yds rush_td rec rec_yds rec_td fum_lost two_pt ret_td
mlb:  ab h 2b 3b hr rbi r bb hbp sb cs  |  ip er h_a bb_a k w sv
```

Scoring is data, not code:

```json
{ "name": "Home league",
  "weights": { "rec": 0.5, "rec_yds": 0.1, "rec_td": 6, "fum_lost": -2 },
  "bonuses": [ { "stat": "rec_yds", "at": 100, "points": 3 } ] }
```

Held in `localStorage` (wrapped in try/catch — it throws in private windows). Changing a weight
re-renders from memory.

**Presets use the same code path as custom scoring.** If PPR is special-cased anywhere, custom scoring
will diverge from it in ways nobody catches.

### 3.2 Distributions under custom scoring

F01's copula is too expensive per-request and impossible to precompute for arbitrary scoring. So
**don't precompute the distribution — precompute its inputs.**

Store per player-week: each stat's marginal CDF (the de-vigged ladder, ~10 points) plus the
correlation matrix. Six stats ≈ 140 numbers, ~1 KB.

In the browser: Cholesky the correlation, draw N correlated normals, push each through its marginal's
inverse CDF, apply the scoring function, take quantiles. Re-runs on every weight change.

- **Seed the RNG deterministically** (player id + week + scoring hash). Otherwise the same page shows
  different numbers on reload and the site looks unreliable.
- **State the sample count on the page.** A Monte Carlo quantile carries its own error, and a site
  whose headline is calibration should say so.
- **Known limitation, disclose it:** a Gaussian copula fitted on marginals reproduces the centre
  better than the tails. F01 measured running-back q90 calibration at 0.140–0.152. Moving the sampling
  to the browser inherits that rather than fixing it — and tails are what fantasy users care about.
- **Measure the resample time.** The "~10 ms at N = 4,000" figure is an estimate, not a measurement.

### 3.3 Leaderboards without a database

"Top 50 WRs under *my* scoring" is a query across every player that no precomputed ranking answers.

One columnar components file per sport-season:

```json
{ "ids":["ja","pn"], "pos":["QB","WR"], "team":["BUF","LAR"],
  "rank":[0.91,0.88], "rec":[0,106], "rec_yds":[0,1486], "rec_td":[0,6] }
```

Columnar beats row-objects here: repeated small integers compress far better than repeated key names,
and it parses straight into typed arrays. Every leaderboard, positional ranking and filter is a
client-side map-and-sort.

**Relevant players only.** 100–250 KB assumes ~3,000 players — **CFB has 10,000+ and blows that
budget**. The table carries players above a per-sport prominence cut (snap share, games played, roster
status) from `config/sports/*`; full per-player data stays available at its own key. Measure and
report the size per sport.

### 3.4 Incremental export

Required, not an optimisation: MLB is daily with ~2,000 active players, so a naive export rewrites
everything every night forever.

Track a dirty set — a player is re-exported only when a game they appeared in changed — and hash each
output, skipping unchanged uploads. Report per run: considered, changed, uploaded, bytes.

### 3.5 Caching

`{sport}/players/{id}/{season}.{hash}.json`, hash in the manifest. Data files get
`Cache-Control: public, max-age=31536000, immutable`; the **manifest** is the only short-TTL object
and the only thing that ever revalidates.

### 3.6 No database in the serving path

Sections 3.1–3.3 cover every planned page. SQLite on D: stays the build-side source of truth; R2 is
the read replica.

*Trigger to revisit:* any page needing "all players matching filters X, Y, Z across seasons" — a
comparison tool or filter builder caps out the components table. That is when D1 earns its cold start.
Not before.

---

## 4. Interaction

What makes a large site feel seamless — none of it is animation.

**⌘K search over everything.** With 20,000 players the navigation problem is *finding one*, and no
menu solves it. Fuzzy index over name, aliases, team, position. Current sport's index eagerly (~40 KB),
the rest lazily on first open. Extend beyond players to teams, research findings and stat pages — a
universal jump, so the nav menu can stay small forever.

Three things the prototype proved necessary: **normalise diacritics** (`doncic` must find Dončić),
**weight surnames** above scattered subsequence matches, and **carry a prominence rank** — "jackson"
matches forty people and alphabetical order is useless.

**Search is necessary but not sufficient.** It serves people who know what they want. Browsable entry
points — position leaderboards, rosters, trending — serve first-time traffic, which is most of it.

**Prefetch on intent.** Fetch a player's JSON on hover or focus. By the time the click lands the data
is there. Nearly free; it is the difference between "website" and "app".

**Persistent shell.** Client-side routing; the rail, header and search never repaint. Most of the
feeling of smoothness is the *absence of a flash*, not the presence of a transition.

**Skeletons sized to the real layout.** Every page fetches data, so every page has a loading state.
Matching the final dimensions gives zero layout shift; a mis-sized skeleton is worse than none.

**Motion budget** (from the working prototype): 220 ms `cubic-bezier(0.22,0.61,0.24,1)` for view
changes, 140–180 ms for the command palette, 160 ms for hover states, nothing over 250 ms inside the
app. `transform` and `opacity` only, so everything runs on the compositor — animating `width`, `top`
or `height` forces layout and janks on phones. `prefers-reduced-motion` disables motion, not just
shortens it.

Heavy GSAP/WebGL work belongs on the landing hero, seen once. Motion on a page someone visits forty
times is charming on visit one and irritating by visit three.

### Budgets — measure and report

| target | budget |
|---|---|
| player page, first contentful paint | < 1.0 s |
| player page, data loaded | < 1.6 s cold, < 300 ms prefetched |
| components table, gzipped | < 250 KB per sport-season |
| player season file, gzipped | < 40 KB |
| distribution resample on weight change | < 30 ms |
| leaderboard re-sort, 3,000 players | < 50 ms |
| cumulative layout shift, any page | < 0.05 |
| static asset count | independent of player count |

---

## 5. Decision log — confidence and what would overturn each

**High confidence, no tradeoff:**

- *Components not points* — custom scoring is mathematically impossible otherwise, and it's less data.
  Overturned only if scoring becomes non-linear beyond weights plus threshold bonuses.
- *Sport-first URLs* — free now; the alternative is a redirect on every indexed URL. Overturned only
  by never adding a second sport.
- *Content-hash keys, one mutable manifest* — reduces cache invalidation to one file.
- *Incremental export* — forced by MLB's cadence.

**Right, with a named cost:**

- *R2 over the repo* — adds a deploy target, CORS, and a failure mode a repo file can't have. Mild
  over-engineering at one sport, clearly right by three.
- *Client-side copula sampling* — inherits the copula's tail weakness; the timing figure is unverified.
- *⌘K as primary navigation* — necessary, not sufficient (see §4).
- *No database* — §3.6 names the trigger.

**The real risk:** OpenNext. Exit decided in §2.

---

## 6. Deliberately undecided

- **Login and paywall.** No content behind them yet; the research found no edge to gate. Workers gives
  a runtime when there is.
- **The second sport's ingestion.** nflverse and Statcast share nothing — write each concretely, no
  shared loader.
- **Any shared model abstraction.** Not until two of them work.
- **Refresh cadence unification.** NFL weekly, MLB daily — two schedules, not one configurable one.

---

## 7. Acceptance

- Routes are `/{sport}/...`; no un-prefixed player or team URL exists
- CI grep: `'nfl'` appears nowhere in components outside `config/sports/`
- `stat_definitions` in the sport manifest only; player files carry keys
- JSON served from R2 with CORS; a data refresh triggers no build
- Player and team pages render at the edge; **static asset count independent of player count** — report it
- No fantasy points anywhere in the export; presets and custom share one code path
- Distributions sampled client-side from marginals + correlation, deterministically seeded
- Components table carries relevant players only; size reported per sport
- Incremental export reports considered / changed / uploaded
- Every budget in §4 measured and reported
- Sport config declares its page set; no empty page reachable from nav
