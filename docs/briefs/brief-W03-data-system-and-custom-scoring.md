# Brief W03 — the data system, and how custom fantasy scoring works

Answers two questions at once, because they have the same answer: how the site stays fast across six
sports, and how a user-defined scoring system recomputes every fantasy number instantly.

**The principle, and everything below follows from it:**

> **Never store fantasy points. Store the stat components and compute points in the browser.**

Fantasy scoring is a dot product — `points = Σ (weight × stat)` — plus a handful of threshold bonuses.
If the browser has the components, any scoring system a user invents is a few microseconds of
arithmetic with no server round-trip. Precomputing points instead means storing one copy per scoring
system, and custom scoring becomes impossible by construction.

---

## 1. Three data shapes on R2

| shape | key | what it powers | rough size |
|---|---|---|---|
| **player detail** | `{sport}/players/{id}/{season}.json` | game logs, usage, charts | 5–40 KB |
| **player summary** | `{sport}/players/{id}/summary.json` | first paint, career aggregates | 3–8 KB |
| **components table** | `{sport}/components/{season}.json` | *every* leaderboard and ranking | 100–250 KB |
| **market distribution** | `{sport}/market/{id}/{week}.json` | the survival curve | ~1 KB |

No database in the serving path. Every one of these is a known key, immutable, edge-cached after first
hit. The Worker's only jobs are rendering the HTML shell with correct meta tags and fetching from R2.

## 2. Components, not points

Each player-game row carries the raw components for its sport, nothing derived:

```
nfl:  pass_yds pass_td int rush_yds rush_td rec rec_yds rec_td fum_lost two_pt ret_td
mlb:  ab h 2b 3b hr rbi r bb hbp sb cs  |  ip er h_a bb_a k w sv
```

The scoring system is data, not code:

```json
{ "name": "Home league", "weights": { "rec": 0.5, "rec_yds": 0.1, "rec_td": 6, "fum_lost": -2 },
  "bonuses": [ { "stat": "rec_yds", "at": 100, "points": 3 } ] }
```

The browser holds the user's scoring object (localStorage, wrapped in try/catch) and applies it on
render. Changing a weight re-renders from memory — no fetch, no rebuild, no server.

Ship the three standard presets and a custom editor. **The presets use the same code path as custom
scoring** — if PPR is special-cased anywhere, custom scoring will diverge from it in ways nobody
catches.

## 3. Distributions under custom scoring — the interesting part

F01's market-implied distribution is a copula over de-vigged ladders. That is too expensive to run
per-request and impossible to precompute for arbitrary scoring.

**So don't precompute the distribution — precompute its inputs and sample in the browser.**

Store per player-week: each stat's marginal CDF (the de-vigged ladder, ~10 points) plus the
correlation matrix between stats. For six stats that's ~140 numbers, about 1 KB.

In the browser: Cholesky the correlation (6×6, trivial), draw N correlated normals, push each through
its marginal's inverse CDF, apply the user's scoring function to each draw, take quantiles. At
N = 4,000 that's roughly 10 ms — imperceptible, and it re-runs on every weight change.

Two requirements:

- **Seed the RNG deterministically** (player id + week + scoring hash). Otherwise the same page shows
  slightly different numbers on each reload and the site looks unreliable.
- **State the sample count on the page.** A Monte Carlo quantile has its own error, and a site whose
  headline is calibration should say so.

This is what makes custom scoring genuinely free rather than a feature with an asterisk.

## 4. Leaderboards without a database

The hard case: "top 50 WRs under *my* scoring." That's a query across every player, and no
precomputed ranking can answer it.

**The components table solves it.** One file per sport-season holding every player's season and weekly
component totals, columnar:

```json
{ "ids":["ja","pn",...], "pos":["QB","WR",...], "team":["BUF","LAR",...],
  "rec":[0,106,...], "rec_yds":[0,1486,...], "rec_td":[0,6,...] }
```

3,000 players × ~15 stats ≈ 45,000 numbers — **100–250 KB gzipped**, fetched once, cached forever.
Every leaderboard, positional ranking and filter is then a client-side map-and-sort over typed arrays.
Instant, and it works under any scoring system the user defines.

Columnar beats row-objects here by a wide margin: repeated small integers in a column compress far
better than repeated key names, and it parses into typed arrays directly.

## 5. Incremental export — required, not an optimisation

MLB is daily and has ~2,000 active players. A naive weekly export rewrites every file every run; a
daily one rewrites 2,000 files a day forever.

Track a dirty set: a player is re-exported only when a game they appeared in changed. Hash each
output and skip the upload when the hash matches. Report per run: files considered, files changed,
files uploaded, bytes transferred.

## 6. Cache keys carry a content hash

`{sport}/players/{id}/{season}.{hash}.json`, with the current hash in the manifest. Data files get
`Cache-Control: public, max-age=31536000, immutable`; the **manifest** gets a short TTL and is the only
thing that ever needs revalidating. This is the whole cache-invalidation strategy — one small
mutable file, everything else permanent.

## 7. When you'd add D1 — and not before

Cloudflare D1 (SQLite at the edge) is the escape hatch for queries the static files can't answer:
cross-season aggregates, arbitrary multi-filter search, anything all-time.

**Don't add it in v1.** Sections 1–4 cover every page in the current plan, and a database in the
serving path adds a failure mode, a cold-start, and a size ceiling in exchange for nothing yet. Revisit
when a real page needs a query the components table can't serve.

SQLite on D: stays the source of truth for building. R2 is the read replica.

---

## Budgets — measure and report these

| target | budget |
|---|---|
| player page, first contentful paint | < 1.0 s |
| player page, data loaded | < 1.6 s cold, < 300 ms warm/prefetched |
| components table, gzipped | < 250 KB per sport-season |
| player season file, gzipped | < 40 KB |
| distribution resample on a weight change | < 30 ms |
| leaderboard re-sort under new scoring | < 50 ms for 3,000 players |
| cumulative layout shift, any page | < 0.05 |

## Acceptance

- No fantasy points stored anywhere in the export — components only, verified by grepping the schema
- Presets and custom scoring share one code path; no special-casing of PPR
- Distributions sampled client-side from marginals + correlation, with a deterministic seed
- Leaderboards computed from the components table, no per-scoring precomputation
- Components files columnar and parsed into typed arrays
- Incremental export with a dirty set; run report shows files changed vs uploaded
- Content-hash cache keys; manifest is the only short-TTL object
- Every budget above measured and reported

## Report back

1. Actual sizes against the budgets, per sport
2. Time to resample a distribution and to re-sort a 3,000-player leaderboard
3. Files changed vs uploaded on a typical weekly (NFL) and daily (MLB) refresh
4. Anything in the current schema that stores a derived value where it should store a component
