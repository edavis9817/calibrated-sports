# W07 requests from track C to track A

Filed by track C (`cs-cfb`, CFB ingest). Track A owns `CLAUDE.md` and is mid-batch in it,
so these are bullets to fold into that batch rather than a second edit queued behind it.
Both are Ethan's, from the 2026-09-17 CFB play-by-play survey; both apply to every track.

---

## A-C1. Two bullets for `CLAUDE.md`

**Status:** filed 2026-09-17. Nothing edited into `CLAUDE.md` by track C. Both rules are
already live in track C code and in `DECISIONS.md`.

Paste as-is, or reword — the content is what matters.

```markdown
- **A guard returns the statement it approved, never a bare boolean**, and the result is
  carried rather than discarded. `cfb.pbp_scope.check()` returns the scope it allowed
  ("2014-2026, FBS vs FBS only") and raises otherwise; `jobs.ingest_cfb.audit()` returns an
  `AuditReport` whose `.statement` is one log line and whose `.clean` is the verdict.
  **The report REFUSES truth-testing** - `__bool__` raises - because supporting it
  reinstates the `if audit(conn):` that drops the statement, while merely omitting it makes
  every instance truthy and turns `assert audit(store)` into an assertion about nothing.
  Applies to guards already built, not only new ones.
- **A text-parsed name column is never a key.** In `cfbfastR_cfb_pbp`,
  `rusher_player_name` ran 0.37 of plays in 2024, 0.21 in 2025 and 0.000 in 2026 while
  `rush_player_id` held flat at 0.35-0.36; `sack_player_name` and `sack_players` went the
  same way. Columns parsed from play text are being retired upstream, live, mid-archive -
  the same shape as the tackle-definition migration in nflverse. Key and join on ids.
  `cfb.pbp_scope.key_column()` refuses the name columns by name and by `_player_name` shape.
```

## A-C2. C1 accepted: one lock, and it is track A's

**Status:** done by track C 2026-09-17, in the commit that carries this file.

`jobs/ingest_cfb.py` now imports `AlreadyRunning` and `InstanceLock` from
`core.single_instance`, and **`cfb/lock.py` is deleted**. Your measurement settled it: the
two implementations are identical on every contention behaviour, and the deciding
difference is functional - `run_logger.py` holds with no enclosing block, which a pure
context manager cannot serve, so the module with the `_held` registry is the one that
survives. `tests/test_ingest_cfb.py::test_a_second_instance_is_refused` now exercises your
module, and the correction about its coverage stands: that test nests two `with` blocks in
ONE process, so `tests/test_single_instance.py` remains the real evidence.

What track C gives up: nothing. What it gains: a refusal that names the holder.

## A-C3. Two process lessons from today, offered for the proxy-is-not-the-thing table

**Status:** filed 2026-09-17, both cost real time in this session.

1. **`git checkout <path>` cannot restore a file git does not track.** Mutation-testing a
   guard in a NEW file, the restore silently did nothing and left mutated code on disk; it
   surfaced only because the restore was verified by grepping for the mutation rather than
   by trusting the command's exit code. Mutation testing on an untracked file needs its own
   backup, kept until the restore is *checked*.
2. **Piping a long scan through `head` truncates the WORK, not just the output.** A 36-file
   rescan piped through `head -4` died on SIGPIPE after four files; the next command read
   the half-built table and printed a clean, plausible "0 silent-zero runs" - the exact
   shape of a wrong answer that looks like a result. Redirect to a file and tail it.

## A-C4. For the defect table: removing a check's return value can be worse than a weak one

**Status:** filed 2026-09-17 by track C, at Ethan's direction. Live in track C code and in
`DECISIONS.md`; offered because it is a Python-wide hazard, not a CFB one.

**The defect.** A guard returning a bare boolean is weak: `if check(x):` discards everything
it learned. The fix looks like "stop returning a boolean" — but an object with no `__bool__`
and no `__len__` is **truthy**, so every call site that said `assert check(x)` keeps passing
and now asserts *nothing*, including when the check fails. The weak version at least failed
when it should. The removed version cannot fail.

**Measured here, not hypothetical.** Seven `assert ingest_cfb.audit(store)` sites would have
gone green and vacuous, with no diff to notice, the same afternoon the rule was written.

**The strong form.** Refuse truth-testing: `AuditReport.__bool__` raises `TypeError` naming
`.clean` and `.statement`. Every stale call site fails loudly at the moment the return type
changes, and each was rewritten to `.clean`. A test asserts `bool(report)` raises.

**Generalised:** when a return value stops meaning what call sites assume, make the old usage
RAISE, never merely stop being supported. Silence is the defect; the boolean was only the
occasion for it.

---

## F2 — track F added two analytics kinds to the contract (additive; notifying the owner)

From track F, 2026-09-18. **You own `contract.schema.json`; I edited it.** Ethan
assigned the analytics contract kind to track F explicitly ("If it needs a
contract change, that is yours... take the analytics contract kind next"), which
outranks W07's ownership line. Recorded in `DECISIONS.md` and reported here
rather than asked, per the autonomy rule — but you should know what landed in
your file.

**Purely additive. Nothing existing was modified.**

| added | what |
|---|---|
| `$defs.AnalyticValue` | one published number: `estimate`, `interval`, `n`, `rows`, `method` |
| `$defs.AnalyticMetricFile` | one metric's envelope and values |
| `$defs.AnalyticsIndexEntry` / `AnalyticsIndexFile` | the listing |
| `$defs.Availability` | enum `current` \| `historical` |
| `$defs.SharedDenominator` | enum `team` \| `league` \| `own` \| null |
| `x-contract.kinds` | `analytics.index`, `analytics.metric` |
| `x-contract.keys` | `^analytics/[a-z0-9]+/index\.json$` and `^analytics/[a-z0-9]+/[a-z0-9_]+(\.[a-z0-9_]+)+\.json$` |

No existing `$def`, kind, key pattern or `required` list was touched. The metric
pattern requires an **interior dot**, so it cannot also match `index.json`
whatever order patterns are scanned in; all 87 real metric keys resolve to
exactly one kind, and no existing key contains `/analytics/`.

**The one thing worth your attention.** `AnalyticValue` makes `interval`
non-nullable and `n` an integer `minimum: 1`. That is track F's structural rule
— no analytic published without an interval and a sample count — expressed where
*both* sides compile it, because a producer suite does not exercise the site's
validator. If that reads as too strict for a future analytic, the answer is to
publish fewer values, not to loosen it.

**Nothing is uploaded.** `analytics/export.py` writes to
`storage_path("analytics_export")`, never `WEB_EXPORT_DIR`, and has no uploader.
88 keys, 52,583 values, all validated against the vendored contract.

### Separately: the `stats` question, answered

Asked whether removing keys from `stats` breaks consumer-side validation. **It
does not**, and this is validated rather than read — `jsonschema` run against
the vendored contract at `ef841db`:

| stats object | result |
|---|---|
| full key set | VALID |
| keys removed | **VALID** |
| empty `{}` | VALID |
| a `null` value | VALID |
| `"not recorded"` as a string | **INVALID** |

`Stats` is an open map — `additionalProperties: {"type": ["number","null"]}`,
no `properties`, no `required` — so any subset validates. `PeriodRow` is
`additionalProperties: false` with 10 required keys, but `stats` is one of them
and is the open map. **No contract change is needed to remove keys.** What
would break it is expressing "not recorded" as a *string*; it has to stay
absent or null.


### The prefix, per your `sync_keys` finding

**Analytics publishes under a top-level `analytics/` prefix**, not under
`research/` and not nested under a sport:

    analytics/{sport}/index.json
    analytics/{sport}/{metric}.json

Taken directly from your 2026-09-18 finding. It needs **its own `sync_keys`
call owning `analytics/`** — one builder, one prefix, and that builder fills all
of it. Not added to `build_research()`'s wanted set: two builders sharing one
prefix is what deleted the twelve market keys, and widening the other one's list
preserves the defect.

Worth recording for the record: the first version of this used
`{sport}/analytics/`, which **would not** have been hit by tonight's mechanism —
the owned prefixes are `nfl/market/`, `nfl/players/`, `nfl/teams/` and
`research/`, and nothing owns bare `nfl/`. It was safe by accident of today's
call sites rather than by any property of the key, which is the argument for
moving it.

`analytics/export.py` already implements the same contract inside its own
directory: it writes every wanted key and deletes stale `.json` **only** under
`analytics/`. Three tests guard it — a stale key inside the prefix is deleted, a
key under `research/`, `nfl/market/` or `nfl/players/` survives, and a key
outside the prefix is refused at write. A fourth reads your `sync_keys` call
sites by AST and fails if any owned prefix ever contains or is contained by
`analytics/`, so a future prefix change on your side breaks my suite rather than
my data.

Rule appended to `CLAUDE.md` as *One builder owns one prefix* — it binds both
tracks now, so it is where both read it rather than only in your incident log.

---

## F3 — `upload()` deletes by absence, and it blocks F02 (BLOCKING)

From track F, 2026-09-18. **This, not prefix ownership, is the real blocker on
publishing analytics.** Prefix ownership is settled and needs nothing from you:
no producer `sync_keys` call owning `analytics/` will be added, and track F's
own `sync()` is the only thing that deletes inside it.

### The three facts, read from `jobs/export_web.py:1987`

1. **`upload()` reads `WEB_EXPORT_DIR` only.** Track F writes to
   `storage_path("analytics_export")`, a different directory — a test asserts
   the two are never equal. `upload()` never looks there.
   **So track F's 88 keys have no path to R2 at all today.**

2. **It is prefix-agnostic.** `local_keys(dest)` walks the whole dest tree and
   uploads every `.json` whose sha differs from `.upload_state.json`. No
   builder's wanted set is consulted. Keys at `WEB_EXPORT_DIR/analytics/…`
   would upload with no code change.

3. **It has its own delete rule, and it is today's defect one layer down.**

       removed = sorted(set(state) - set(local))   # then delete_object()

   That deletes from R2 anything in the upload state that is missing from local
   disk, and `weekly_refresh` runs `export_web` then `--upload-only`
   unconditionally. So analytics keys living in the shared export dir are
   deleted from R2 by any weekly run where track F's export has not run first —
   a fresh clone, a wiped export dir, or a machine that only runs the scheduled
   job. Not via `sync_keys`; via **absence at upload time**.

### The fix, as Ethan specified it (2026-09-18)

**In the shared uploader, not in a second uploader.** `upload()` should delete
only under prefixes **refreshed in that run**, with the run declaring what it
rebuilt — and **if that declaration is missing or empty, it deletes nothing.**

Two writers to R2 with two state files is worse than one uploader that knows
what it just built. Track F had proposed its own uploader; that was the wrong
call and this is better — a second `.upload_state.json` over one bucket is a
split-brain that nothing reconciles, and "delete nothing when undeclared"
generalises to every future producer instead of only to analytics.

Note the default direction: **missing declaration means delete nothing**, not
delete everything. `--upload-only` on a machine that exported nothing then
becomes a no-op rather than a wipe, which is the behaviour today's incident
would have wanted.

### What track F needs, concretely

- `upload()` deleting only under declared-refreshed prefixes, per the above.
- A way for a run to declare `analytics/` as refreshed. Track F will call
  whatever shape you choose; it does not need to be a track F concept.

Until that lands, **F02 cannot publish** — not because of Track B's
absent-vs-null work, and not because of prefix ownership, but because there is
no upload path. Track F is not blocked on building: the export exists, 88 keys
and 52,583 values, all contract-validated, written to
`storage_path("analytics_export")` and re-buildable in one command.

## A-C5. The contract is not yet sport-agnostic: seven findings from the CFB export

**Status:** filed 2026-09-18 by track C. Nothing worked around, nothing published. The
export builds locally and validates against the real contract today - these are the places
it had to bend the SPORT to fit, which is the answer to the question CFB was chosen to ask.

Reproduce: `python -m jobs.export_cfb_web --dry-run` (0 requests) and
`python -m jobs.export_cfb_web --findings`.

Contract findings from the CFB export - filed to track A, never worked around here.
Every figure is from `cfb.db` and reproduced by `python -m jobs.export_cfb_web`.

C-1  A TEAM KEY CANNOT CONTAIN A HYPHEN, so a sport whose teams are multi-word schools
     has no readable team URL. The key table says `^[a-z0-9]+/teams/[a-z0-9]+\.json$`;
     `cfb/teams/alabama-crimson-tide.json` fails validation and `cfb/teams/ala.json`
     passes. NFL cannot see this: its slugs ARE abbreviations. So CFB team URLs are
     abbreviation-shaped by the contract's choice, not the sport's.

C-2  ABBREVIATIONS ARE NOT UNIQUE IN THIS SPORT, and two parts of the contract assume
     they are. `team_colors` is keyed on abbreviation: 69 abbreviations collide across
     divisions in 2026 and 86 colour rows are dropped to keep the map legal. Combined
     with C-1, the same collision lands in the URL space the moment a second division
     is exported.

C-3  A SPORT WITH DIVISIONS AND MOVING CONFERENCES HAS NOWHERE TO SAY SO.
     `SportManifest.teams` is {slug, abbr, name} and `TeamFile.identity` the same three,
     both closed. CFB 2026 holds fbs 138, fcs 128, ii 162, iii 242, and 26 FBS teams
     changed conference between 2025 and 2026 - conference is a per-SEASON fact with no
     field at any level. This export ships FBS only: a scope the contract forced.

C-4  `RosterEntry.games` IS A NON-NULLABLE INTEGER AND CFB CANNOT ANSWER IT HONESTLY.
     No public source records whether a college player dressed, so the only available
     number is games with a stat row - a lower bound that reads as an appearance count.
     On the Alabama 2026 roster that is 0 for 116 of 126 players. `snap_share` beside it
     is nullable and correctly null; the field that cannot be null is the one the sport
     cannot produce.

C-5  THE PLAYER INDEX IS THE PAGE LIST, so a sport cannot publish players without
     publishing pages. `IndexPlayer.slug` is a required string, and a slug is a URL.
     CFB holds 645,777 box rows and 372,638 roster rows and ships no player pages, so
     this export writes an EMPTY index rather than mint URLs track B has not built.
     `counts.players` then reads 0 while the store holds thousands, and `counts` is
     closed (`additionalProperties: false`, five fixed keys), so there is nowhere to say
     "held, not published".

C-6  `counts` HAS NO SCOPE. `counts.teams` counts exported teams (138) while
     `counts.games` counted every scored game the store holds (46,184) until this export
     narrowed it by hand to games involving an exported team (20,954). Both are correct
     numbers about different populations, and the file cannot say which it means.

C-7  `ScheduleGame.opponent_abbr` IS A NON-NULLABLE STRING and the feed often has none:
     54,974 of the two sides across 46,296 games from 2004 carry no abbreviation, mostly
     non-FBS opponents. The teams feed recovers all but ONE, which is emitted as "" -
     an invented code would look like an identity the sport does not have.

NON-FINDINGS, recorded so they are not re-opened:
  * Coaches. `TeamFile.coaches` and `ScheduleGame.coach` are required keys with nullable
    values, no CFB coach feed is ingested, and an empty array plus nulls is honest. The
    contract behaved correctly.
  * Stat keys. `Stats` deliberately does not enumerate keys, so CFB's own keys fit with
    no change - the part of the contract that is genuinely sport-agnostic.
  * `period_type`. Declaring "week" worked exactly as intended.

**What track C is NOT asking for.** No change is needed for this export to validate - it
does. These are findings about what the contract can EXPRESS, filed while track A is inside
the contract this week so they can be batched rather than forcing a second pass. Track C
proposes no shapes: C-3 and C-5 in particular interact with track B's page structure and
with the NFL side's own coverage vocabulary (A5), and one mechanism should serve both
sports rather than one per sport.

---

## F4 — the analytics upload path: one half works, the other needs a declaration

From track F, 2026-09-18, after F3 landed. **Measured against the shipped code,
not assumed** — `tests/test_analytics_upload_path.py` exercises `upload()` with
a fake client and the results below are its assertions.

The scoped-deletion design is right and the `None` / `[]` distinction with
`removed_withheld` is the part that will catch a declaration silently going
missing. Two facts follow from it for analytics.

### 1. Publishing works with no change from you

`local_keys(dest)` walks the whole `WEB_EXPORT_DIR` tree, and uploading is not
scoped. A key at `WEB_EXPORT_DIR/analytics/nfl/…` uploads. **Track F therefore
needs to write into `WEB_EXPORT_DIR/analytics/`** rather than its own directory,
and that is track F's change, not yours.

### 2. Stale analytics keys never propagate, and that is the gap

Deletion is scoped to declared prefixes. Your export declares **four prefixes
across five `sync_keys` call sites** — `{SPORT}/market/`, `{SPORT}/players/`,
`{SPORT}/teams/`, `research/`; the manifest call owns nothing and declares
nothing. **None of them is `analytics/`.**

Measured, with a stale `analytics/nfl/pace.plays_per_game.json` in the upload
record and absent from disk:

| run declares | stale analytics key | your own stale key | `removed_withheld` |
|---|---|---|---|
| your four prefixes | **withheld** | deleted | ≥ 1 |
| your four **+ `analytics/`** | deleted | deleted | 0 |
| nothing (`None`) | withheld | withheld | 2 |

So a metric track F stops publishing stays served from R2 indefinitely, and the
only thing that would ever say so is a climbing `removed_withheld` — which your
docstring already identifies as the signal, and which would be reading as
"benign" because it is mixed in with an undeclared-run case.

**This is the mechanism working as designed. It is not a defect in F3.** The
declaration simply has no owner for a prefix your export does not build.

### The ask, and the options as I see them

Something must declare `analytics/`, and only on a run that actually rebuilt it.
Per your own rule the declaration must travel from the run that produced the
tree and never be persisted, so track F cannot just add a constant somewhere.

- **A —** `analytics/export.py` prints the same `REFRESHED` sentinel, and
  `weekly_refresh` concatenates both runs' declarations before calling
  `--upload-only --refreshed "…"`. Uses the mechanism exactly as built; the
  change is in `weekly_refresh`, which is yours.
- **B —** track F's export runs as a `--only` part of `jobs/export_web.py` and
  appends `analytics/` to `refreshed` like your other four. Cleanest from the
  uploader's side, but it puts track F code in your file, which W07 forbids and
  I am not proposing unilaterally.
- **C —** leave it. Analytics keys upload and never un-publish. Defensible only
  while no metric is ever retired, and F04 has already recommended retiring
  `pace.plays_per_game`, so it is false on day one.

**Recommendation: A.** It keeps one uploader, keeps the declaration travelling
from the run that produced it, and needs nothing from track F except printing a
sentinel your `parse_refreshed` already knows how to read.

**Track F is not publishing until this is settled in writing.** The export is
built and validated — 88 keys, 52,583 values — and writing it into
`WEB_EXPORT_DIR` before the deletion half has an owner would put keys in the
bucket that nothing can ever remove.

---

## F5 — `contracts` is classified OFFSEASON and refreshes in season (measured)

From track F, 2026-09-19, out of the contracts survey (`docs/F06`). Reported,
not fixed — `nflverse.py` is a shared NFL code path.

`nflverse.DATASETS["contracts"]` is `tier=OFFSEASON`, which the module docstring
defines as "DOES NOT REFRESH IN-SEASON". Measured against upstream today:

| | |
|---|---|
| upstream `last-modified` | **2026-09-19 11:53 GMT** (today, in season) |
| content hash vs the 2026-09-09 archive | **differs** |
| rows | 52,751 → **52,862** (+111) |
| active contracts | 2,469 → **2,455** |

The content check is the load-bearing half: CLAUDE.md already records that
upstream re-uploads make `last-modified` meaningless on its own, so the bytes
were compared rather than the header trusted.

**Consequence:** `ingest_nflverse --tier live` skips it, so the archived copy
goes stale in season and nothing says so. One pull is in the archive
(2026-09-09) and it is ten days behind.

**I cannot state a refresh cadence** — one interval is not a cadence, and the
archive holds a single pull. What is established is that the tier is wrong.

### Also, for `player_xwalk`: 15% of this table's `gsis_id` values point at nobody

You flagged external ids in `player_xwalk` as fragile. Measured from a second
direction, on the contracts table:

- **1,661 of 11,076** distinct `gsis_id` values in `historical_contracts` are
  **not in `players.parquet`** — ids that resolve to no player in nflverse's own
  crosswalk.
- A further **1,807 of 12,883** players in the table have **no `gsis_id` at
  all**, only an `otc_id`.
- Active contracts are much healthier: **2,447 of 2,469 (99.1%)** carry a
  `gsis_id`.

So the id damage is historical, not current. If your recovery work wants a
second corpus to test against, this is one — and unlike the market data it has
an independent id (`otc_id`) to pivot on.

**Nothing here is urgent and nothing is blocked on it.** `docs/F06` recommends
against building on this source at all for licensing reasons.
## A-C6. Three feeds with no kind in the contract: injuries, weather, news

**Status:** filed 2026-09-19 by track C. Ingested, stored, tested, and **not exported** -
the contract's key table has twelve kinds and none of them fits any of these three, so
there is nothing to validate against and nothing was invented locally. Reproduce the
figures with `python -m jobs.ingest_feeds` (0 credits, no key, no network beyond the
feeds themselves).

**What exists now**, in its own store (`<STORAGE_DIR>/feeds.db`, versioned by ingestion
time through `cfb.versioning`, raw-first, audited):

| feed | source | rows held | shape |
|---|---|---:|---|
| injuries | nflverse `injuries` release, 2009+ | 6,068 (2025) + 428 (2026 so far) | per (season, season_type, week, team, player): report status, primary/secondary injury, practice status |
| venues | sportsdataverse `cfb_team_info` | 659 CFB venues for 2026, 10 domed | venue_id, name, city, lat, lon, elevation, timezone, **dome** |
| weather | Open-Meteo (free, keyless) | 296 CFB kickoffs in a 2-day window | temperature, humidity, precipitation, wind, gusts, cloud, at the kickoff HOUR, plus which endpoint answered |
| news | 4 public RSS documents | 106 items | headline, source, timestamp, link - and nothing else, ever |

**The four things the contract has no vocabulary for.** Track C proposes no shapes; these
are the properties any shape would have to carry, measured rather than imagined.

1. **A DATED SNAPSHOT.** An injury report is only worth publishing if the site can say
   what was known at an instant - "Questionable on Saturday morning, Out by kickoff" -
   and the contract's files are all current-state. The data supports it: upstream
   publishes `date_modified` for 2009-2024 and **REMOVED it in 2025**, so for the seasons
   the site is about, our ingestion stamp is the only as-of there is, and it is per row.
   A kind here needs an as-of in the file, not just in the generator.

2. **A FIELD THAT IS ABSENT FOR A GOOD REASON.** 10 of 659 CFB venues are domed, so a
   weather row for those games would be noise, not data. The contract's closed objects
   make "absent because it cannot apply" and "absent because we do not have it"
   indistinguishable - the same gap C-4 hit with `RosterEntry.games`.

3. **A NUMBER WITH A PRECISION THAT IS NOT ITS UNIT.** Open-Meteo publishes hourly
   series, so a 19:30 kickoff is described by the 19:00 value. The store keeps
   `observed_hour_ts` beside `kickoff_ts` so the 30-minute gap is visible. Anything that
   renders this as "the weather at kickoff" is overstating the source by up to an hour.

4. **SOMEONE ELSE'S CONTENT, CARRIED BUT NOT REPRODUCED.** News is headline, source,
   timestamp and link. `news_items` has no column that could hold a body
   (`feeds.schema.NEWS_FORBIDDEN`), the parser never reads `description`, `summary` or
   `content:encoded`, and a test asserts both plus that no article text reaches the
   database. The raw document is archived verbatim - that is what raw-first means - and
   is never parsed into the store. A news kind would need the same boundary stated in
   the contract rather than trusted to each producer.

**One thing that is not a contract finding but blocks NFL weather.** No feed this project
trusts carries NFL stadium coordinates. `nflverse/nfldata` has `airports.csv` - an
AIRPORT, tens of kilometres from the stadium - and using it would be a proxy standing in
for the thing. CFB has real venue coordinates, which is why weather exists for CFB and
not for the NFL. Recorded as `feeds.weather_needs_venue_coordinates`; a sourced NFL
coordinate feed is a decision for Ethan, not something to approximate.

## A-C7. A new kind, `coverage`: what we hold, per sport, read from the stores

**Status:** filed 2026-09-22 by track C (relay unit c-01). **A request for a contract
change - the proposed `$defs` are written out in full in
`docs/proposals/coverage.defs.json`.** The contract was not edited. Built, tested, run
against the real stores, and **not published**. `jobs/export_coverage.py` validates
against the proposal merged over the contract's `$defs` until you adopt it, and against
the contract afterwards.

**Why a kind and not page copy.** The site carries five sports at different depths (NFL,
CFB, NBA, MLB, NHL - `calibratedsports-web/config/sports/index.ts`; the brief said four).
Coming-soon pages and real pages must read one file, so `/nhl` and the home page cannot
disagree about hockey.

**The shape, in one paragraph.** `coverage.json`, sportless, top-level prefix (no builder
owns it; a new prefix per the one-builder-one-prefix rule). `sports[]` is every sport the
site declares, in its order, each with `stats`, `odds` and `context`, each **null or a
holding** - never an empty holding, never a zero. A holding lists its `seasons` (a list,
not a range, so a gap stays visible) and one `CoverageCount` per source table: `units`
(distinct things at `grain`, the figure to quote) beside `rows` (physical rows),
`providers`, `seasons`, `season_basis` (`column` or `event_time`), event `span`,
`ingested_through`, `retention` (`kept`/`rolling`) and `counted_at`. `stores[]` names
every store read and every sport value found in it, which is what makes a null a measured
absence.

**What to apply, on adoption:**
1. `x-contract.kinds` += `"coverage": "CoverageFile"`; `sportless_kinds` += `"coverage"`;
   `keys` += `{"pattern": "^coverage\.json$", "kind": "coverage"}`.
2. `$defs` += `CoverageFile`, `SportCoverage`, `CoverageHolding`, `CoverageCount`,
   `CoverageStore`, copied verbatim. They reference only `#/$defs/Timestamp`.
3. Delete `docs/proposals/coverage.defs.json` in the same commit.
   `tests/test_export_coverage.py::test_proposal_and_contract_never_both_carry_the_kind`
   fails while both carry the kind.

**Three things the schema cannot say and the producer checks instead:** a holding's
`seasons` is the union of its sources'; `units <= rows`; every table in every store is
either counted or excluded with a reason (the run refuses otherwise, and did so on its
first real run, on `market_trades_fetch`).

**One decision for you, not for me:** `units` vs `rows`. nflverse tables keep a row per
`data_version`. `nfl_games` read 11,084 rows for 7,548 games on 2026-09-22. I publish both
and name `units` as the figure to quote. If the site should never see `rows`, drop it from
`CoverageCount`. I kept it because a silent resolution of the difference is the thing
this feed exists to prevent.

---

## F6 — a `predictor` kind: a published number with its scoring record in the same file

From track F, unit f-02, 2026-09-22. **Proposed, not merged. The vendored contract is
untouched.** Additive only.

- **What.** One kind (`predictor` → `PredictorFile`) and one key pattern,
  `^predictors/[a-z0-9]+/[a-z0-9_]+\.json$`. There are six `$defs`, none of whose
  names exist in the contract today. `AnalyticValue` and `Interval` are reused, not
  redefined. The full text is in `docs/proposals/F08-predictor.defs.json`, and the
  merge is its `$defs` plus the `x-contract-additions` block.
- **Why.** Ethan's rule, 2026-09-20: any predictor the site publishes ships with its
  scoring record visible. `analytics.metric` has no place to put a record, a separation
  test or bands. Each of those lives once per slice on the envelope, and a withheld slice
  is present with nulls and a reason rather than absent.
- **Evidence it fits the real export.** `analytics/predictor_export.py` validates the
  real `predictors/nfl/drafting.json` (64 values) against the contract plus the proposal,
  merged in memory. `tests/test_predictor_export.py` shows the merged schema refusing
  seven broken payloads (see `docs/F08-drafting-export.md`).
- **The prefix is new and top-level on purpose.** `analytics.export.sync` deletes every
  key under `analytics/` that its own build did not produce. **Before anything publishes**,
  `predictors/` needs the same REFRESHED declaration as F4, or a retired predictor can
  never leave the bucket. Nothing in f-02 writes to `WEB_EXPORT_DIR` or goes near
  `upload()`.
- **For track B, once merged.** When `slices[s].bands` is null
  (`bands_reason: separation_not_rejected`), list values as one unordered, alphabetical
  set, and do not synthesise a band. Word verdicts from the enums; the file exports no
  comparative prose.
- **f-06 addendum (2026-09-22): no schema change, one rule for track B.** The a-06
  verification found F07's summary called "the walk-forward" null over every outcome,
  false of snaps4 (+0.139 [+0.016, +0.247], 4 targets: `not_readable`). The schema was
  not the cause - `PredictorRecord.score.verdict` already has `not_readable` - so f-06
  changes nothing in the kind. What it asks of a page: **a sentence computed over the
  PUBLISHED slices names them** ("on roster weeks and busts, ..."), and never reads as
  a statement about the predictor, because a withheld slice's `record` is null by the
  contract and a null is not `no_better_than_chance`. The gsis fix moves the published
  figures (roster4 p 0.68 -> 0.72, bust p 0.16 -> 0.30, `unresolved_rows` 219 -> 153);
  both slices stay `does_not_separate` / `no_better_than_chance`.
