# Track B requests — filed by other tracks, not built here

**Convention (Ethan, 2026-09-17, project-wide): one file per TARGET track,
`docs/track-<target>-requests.md`, with sections by SOURCE track.** So this file is
everything filed TO track B, whoever filed it; `track-a-requests.md` and
`track-c-requests.md` are the same shape for their targets.

Track A owns `contract.schema.json`; Track B consumes it and regenerates
`lib/schema.generated.ts`. Per `docs/W07-parallel-tracks.md`, an item belonging to another track is
**reported, not fixed**.

---

## 1. `manifest.seasons` — 2022 bye ambiguity needs a sibling key, not a shape change

**Status:** filed 2026-09-16, not built.

**The fact.** Bye weeks are derivable from `nfl_games` and the derivation is clean across 28 seasons
— 1999–2026, every team with exactly one empty REG week (31 teams before Houston, 32 after). Exactly
one season degrades: **2022**, where `BUF` has `[7, 17]` and `CIN` has `[10, 17]`, because their
week-17 game was cancelled. Those two are ambiguous and must be marked, not guessed.

**Why not change `seasons`.** The contract has
`"seasons": {"type": "array", "items": {"type": "integer"}}` under `additionalProperties: false`.
Converting it to an array of objects breaks three Track B call sites in `SportHome.tsx`:

    m.seasons.length          // tile value
    Math.min(...m.seasons)    // coverage range, first
    Math.max(...m.seasons)    // coverage range, last

**The request.** Add an additive sibling key carrying only the degraded seasons, leaving `seasons` an
`int[]`. Nothing above changes, and the flag hangs where a consumer can find it:

```json
"seasons_degraded": {
  "2022": { "teams": ["BUF", "CIN"], "reason": "week 17 cancelled; bye week not derivable" }
}
```

Additive fields fail the export until the contract is updated in the same commit — by design — so
this lands as one Track A contract change, after which Track B regenerates its types. Contract
changes stay serial: one at a time, Track A, Track B regenerates after.

---

## 3. URGENT — the Research copy must now read "identical to naive", not "no better than"

**Status:** filed 2026-09-17, live on the site now, not built.

The settlement re-run changed a published figure that the Research copy characterises in prose.
`research/calibration.json` is live and serves:

    Brier   model 0.1916   market 0.1676   naive 0.1916

**The model and the naive prior are now identical to four decimal places.** Before the re-run the
model was 0.1957 against naive 0.1924 — worse by 0.0033, which copy could fairly call "no better
than a smoothed prior-season frequency". That wording is now wrong in the direction that flatters
the model: it implies a gap that no longer exists. It is not "no better than"; it is **the same
number**.

The model-versus-market verdict is unchanged and still holds: model − market **+0.0240
[+0.0089, +0.0373]**, interval excluding zero, the new estimate sitting inside the previously
published interval.

Track A owns the figure and it is already published; Track B owns the copy that describes it.

---

## 4. `slotState`'s single `byeWeek` cannot express 1,051 player-seasons — and byes need no new export field

**Status:** filed 2026-09-17, measured, not built.

**The signature is wrong for traded players.** `lib/slotState.ts` declares:

    slotState(wk, byIndex, lastPlayed, support, byeWeek: number | null = null)

A bye is a property of a **team-season**, not of a player-season. Measured over the whole export:

| | n | share |
|---|---|---|
| player-seasons | 50,606 | |
| with more than one team | 1,313 | 2.6% |
| …whose teams' byes **differ** | **1,051** | 80% of those |
| …same bye week | 132 | |
| …at least one bye not derivable | 130 | |

For those 1,051 a single nullable integer cannot be correct for both halves of the season. The player's
team is already known per row — `PeriodRow.team`, the same field `lib/teamChange.ts` reads to draw the
trade separators — so the bye should be resolved per row from that team, not passed once per season.

Also note `SeasonFrame.tsx:172` calls `slotState(wk, byIndex, last, support)` and never passes
`byeWeek` at all. The parameter is declared and unused, so today it is inert rather than wrong.

**Byes require nothing new from Track A.** `TeamFile.schedule` already carries every game for all 28
exported seasons (486 rows for `sea.json`, 1999–2026), and the bye is simply the missing REG index:
Seattle 2024 reads `[1..9, 11..18]`, and week 10 is the bye. It is already published, already at
team-season granularity. What is missing is only that the player page never loads a team file —
`PlayerView` fetches `player_season` keys alone, though `teamKey()` exists in `lib/keys.ts`.

So the bye half is Track B wiring: fetch the team file(s) for the seasons shown and resolve the bye
per period row from that row's team. A traded player needs one fetch per team, which is the cost of
being correct for the 1,051.

**Played-zero is different and IS Track A work** — no `PeriodRow` exists for a week a player played
and recorded nothing, which is the same upstream gap as the settlement defect. **That half has now
landed** — see item 5.

**Per-season support flags** (item 1) should widen from "2022 bye ambiguity" to a general per-season
slot-support record, because the two gaps are independent and measured:

    both bye+zero   13 seasons   2013-2026 except 2022
    bye only        14 seasons   1999-2012   (snap counts start 2013)
    zero only        1 season    2022        (cancelled game, bye not derivable)
    neither          0

That reproduces `slotState.ts`'s own prose exactly, derived independently from the tables.

---

## 5. Played-zero rows now exist — `slotSupport()`'s `zero` reason string is stale copy on a live page

**Status:** filed 2026-09-17. Producer side built and tested; **not yet published** (see below).

`lib/slotState.ts` currently returns:

    zero: false,
    reasons: [..., "Played-but-zero needs snap counts the export does not carry yet."]

That sentence is now **false for seasons 2013 and later**. `jobs/export_web.played_zero_periods`
emits a real `PeriodRow` for every week a player had offensive snaps and no stat row — 15,175 rows
(REG 14,430, POST 745) across 3,900 season files and 1,506 players.

**No new field, and nothing new to render.** A played-zero week is an ordinary `PeriodRow` whose
stats are genuine zeros with a real `snaps` value, so `byIndex.has(wk)` is true and `slotState`
returns `filled`. A zero-valued bar draws at zero height, which *is* the "solid stub on the
baseline" the design already describes. So the `zero` slot state may not need to exist as a separate
case at all — worth deciding on Track B rather than assumed here.

It is also, deliberately, **indistinguishable from a genuine all-zero stat line**: both mean "played,
recorded nothing", which is one fact, not two.

**Per-season support.** `zero` resolves for 2013+ only (`SNAP_FIRST_SEASON`, and the table's own
earliest season — note CLAUDE.md's "from 2012" is wrong). Combined with the bye gap:

    both bye+zero   13 seasons   2013-2026 except 2022
    bye only        14 seasons   1999-2012
    zero only        1 season    2022
    neither          0

**One caveat Track B should know about**, because it changes a number already on the page: `games` is
the period count, and `PlayerView` divides by it for every per-game rate. Played-zero weeks grow that
denominator, so rates fall — brandon-lloyd 2014 12→14 games, 24.5→21.0 yds/g; jason-witten 2016
15→16, 44.9→42.1. Correct, but visible.

**Publication is gated on Ethan** — it moves a published figure on ~1,500 player pages, so the
measuring export ran to a scratch destination with a scratch slug registry and nothing was uploaded.

---

## 6. The model-versus-X verdict should cross the contract as an ENUM, not as a number to re-threshold

**Status:** filed 2026-09-17 by Track A as a contract proposal. Not built; wants Track B's agreement on
the wording map before the schema moves.

**The problem is item 3, generalised.** Item 3 is a copy fix: "no better than" had to become "identical
to naive" because a published figure moved. But the reason it *needed* fixing is structural — Track B's
`compareByLoss` receives two Brier numbers and decides, in TypeScript, whether the difference is worth
calling a difference. Track A's `hypotheses.json` generator now has to make that same call to write its
own prose. **That is the same boundary decided twice, in two languages, which is exactly the shape that
let the settlement rule disagree with itself on 3,272 outcomes.**

The tell is that item 3 is a *cross-track ticket at all*. A figure changed on Track A and a sentence
became false on Track B, with nothing connecting them but a human noticing.

**The proposal: one boundary decision, in Python, exported as data. One wording decision, in
TypeScript.** The producer emits the verdict alongside the numbers:

```json
"comparison": "worse" | "no_better" | "better"
```

- `worse` / `better` — the bootstrap interval on the difference **excludes zero**.
- `no_better` — it contains zero. Covers "identical to four decimals" and "different but not
  distinguishably so", because to a reader those are one claim.

Track A owns the boundary because Track A owns the bootstrap, the game-block resampling and the MDE.
Nothing about "does this interval exclude zero" is renderable knowledge, and re-deriving it from two
rounded means on the client cannot reproduce it — 0.1916 vs 0.1916 is not distinguishable from
0.1916 vs 0.1924 at four decimals, but the intervals differ.

Track B owns the sentence. `compareByLoss` keeps its job and loses its arithmetic: it maps the enum to
copy and picks register, tense and emphasis. A new wording ships without Track A; a new *threshold*
cannot ship without Track A, which is the point.

**Shared vectors pin the enum boundary only, never the wording.** A fixture of
`(est, lo, hi) → comparison` runs in both suites. It fixes where the boundary is and says nothing about
what either side says about it, so Track B can rewrite every string without touching a test.

**Contract mechanics.** `comparison` is additive under `additionalProperties: false`, so it fails the
export until the schema moves in the same commit — the usual serial dance: Track A changes the
contract, Track B regenerates. It belongs next to `model_minus_naive` in `brier`, which already landed.

Worth noting this makes item 3 self-correcting rather than closed: had `comparison` existed, the
settlement re-run would have flipped `worse` → `no_better` in the same write that moved the figure, and
the copy would have followed without a ticket.

---

## 7. A null needs a REASON — per-season stat coverage (this is A5, with the measurements attached)

**Status:** filed 2026-09-17 by Track A. The producer-side fix needs no contract change; this item is
only about rendering the reason.

**What is changing on the producer side, without asking Track B for anything.** Three published
columns are *present, populated and zero* for a run of seasons — the silent-zero class. Measured
against `nfl_player_week` on 2026-09-17:

| source column | published as | silently zero | note |
|---|---|---|---|
| `targets` | `targets` | 2003–2008 | unrecoverable; PBP names the receiver on 0.5–0.9% of incompletions |
| `def_tackles_for_loss` | `def_tfl` | **2003–2011** | 1,796 player-weeks in 2002, 0 for nine seasons, 1,843 in 2012 |
| `def_qb_hits` | `def_qb_hits` | 2003–2005 | |

These become `null`. **No contract change is required** — `Stats` is already
`{"type": ["number", "null"]}` and its own description reads *"null means unknown or not collected,
never zero."* The export was violating the intent the contract states; publishing null is the
contract being obeyed, not extended.

**What Track B will see.** `stats.targets` and `stats.def_tfl` go from `0` to `null` on affected
seasons, on both player season files and team splits. Anything rendering `0` today renders an empty
cell instead. **A career or multi-season total that spanned 2003–2011 was silently nine seasons
short on TFL and will now refuse to claim a number it does not have.**

**The request, and why null alone is not enough.** A null renders as "—", which is honest but says
*unknown*. These are not unknown: they are **not collected, and never will be**. The reader cannot
tell "this player had no targets" from "nobody recorded targets that year" from an empty cell, and
the second is a fact worth stating.

This is A5, which was filed as "per-season stat-coverage declaration" without measurements. The
measurements now exist, and they argue for the coverage record living in the **sport manifest**,
keyed by stat and season range, so one fetch covers every page:

```json
"stat_coverage": {
  "targets":   [{"from": 2003, "to": 2008, "reason": "not recorded by the source"}],
  "def_tfl":   [{"from": 2003, "to": 2011, "reason": "not recorded by the source"}],
  "def_qb_hits": [{"from": 2003, "to": 2005, "reason": "not recorded by the source"}]
}
```

**One mechanism, both gaps, both sports.** Snap counts begin in 2013 and CFB has the same shape
(`docs/track-c-requests.md` C-A3). Today `slotState.ts` hard-codes its own prose about which seasons
support which slot; that prose should read from this declaration instead, or it is the same fact
written twice — which is the defect that cost this session 3,272 outcomes.

Additive under `additionalProperties: false`, so it lands as one Track A contract change and Track B
regenerates. Track A will propose the exact shape rather than extend `SportManifest` ad hoc.

### 7a. Coverage of the existing treatment, checked against your code before publishing

Ethan's condition on publishing was that a null must render as *not recorded* rather than as zero, at
the season granularity each column needs. Track A read `calibratedsports-web` to check rather than
assume. **Published 2026-09-17.** Result: honest everywhere, explained in one place of four.

**The good news, and it is the load-bearing part: a null NEVER renders as zero.**
`lib/statFormat.ts:10` returns `DASH` before any format branch, so nothing plots or prints a null as
0. The failure mode Ethan was guarding against — a flat line at zero with no explanation — does not
occur anywhere.

**What the existing treatment covers:**

| value | window | where it renders | reads "not recorded"? |
|---|---|---|---|
| `target_share` | 2003–2008 | player usage frame | **yes** |
| `targets` | 2003–2008 | player game log, season totals, career | no — dash only |
| `def_tfl` | **2003–2011** | team splits table | no — dash only |
| `def_qb_hits` | 2003–2005 | team splits table | no — dash only |

**`PlayerView` is right and should be the model.** `PlayerView.tsx:422-430` derives `unrecorded` from
the rows themselves — every REG period null, or the summary's `<key>_mean` null — and names only the
season on screen. Its own comment explains that no start year is hard-coded "because the contract
carries none". That is exactly the per-column, per-season granularity needed, and it will light up for
these seasons with no change at all. It also already carries the 2012-vs-2013 fix.

**Three gaps, in priority order:**

1. **`TeamView` has no equivalent path.** `Splits` maps `v == null` to `DASH` and stops
   (`TeamView.tsx`, `perGame`). `def_tfl` is dashed for **nine consecutive seasons** on all 32 teams
   with nothing saying why — 396 values. This is the one worth building, and `PlayerView`'s rule ports
   directly: a column null in every row of a season is not recorded that season.
2. **`targets` is not in `usageKeys`** (`config/sports/nfl.ts:21` is `["snap_share",
   "target_share"]`), so the note misses it even on the player page, where its sibling
   `target_share` is covered. A reader sees "Tgt %" explained and "Tgt" dashed beside it.
3. **Career totals carry no season to attach a reason to.** 1,127 player pages now have a null career
   `targets` (line 686 renders `summary.career.stats`). This is the case the `stat_coverage`
   declaration above answers best: the career row can say "targets not recorded 2003–2008" from the
   manifest without inspecting rows.

**Producer-side counts, measured on the real export (23,206 files):** `targets` 44,518 values,
`target_share` 39,600, `def_tfl` 396, `def_qb_hits` 132 — 4,856 files touched. Every one was `0`
before and is `null` now, and a re-scan confirms **0 remaining zeros inside any run**.

---

## 8. `prop_history` is exported — the settled record, counted once, ordering supplied as data

**Status:** producer built and tested 2026-09-17. **Contract changed**, so Track B must regenerate
`lib/schema.generated.ts` before the types match.

**What landed.** `PlayerSummary` gains `prop_history`, which is `PropHistory | null`:

```json
"prop_history": {
  "stats":   [{"stat", "priority", "n", "games", "cleared", "rate", "interval"}],
  "records": [{"season", "stat", "line", "n", "cleared", "rate", "interval"}]
}
```

### `games` is the sample to quote, NOT `n` — and this is the one thing to get right

`n` counts **posted lines**. `games` counts the **independent events** behind them. They are not the
same number and the gap is large: a ladder prices several thresholds on one player-game, and every
rung settles off the *same* final stat line. Measured 2026-09-17 across the whole record:

| stat | markets | player-games | inflation |
|---|---|---|---|
| `receiving_yards` | 113,409 | 10,767 | **10.5x** |
| `receptions` | 41,945 | 10,293 | 4.1x |
| `sacks` | 15,238 | 4,765 | 3.2x |
| overall | 102,159 | 38,061 | 2.7x |

`rate` is `cleared / n`, which is the right reading of "how often did a posted line clear".
**`interval` is computed on `games`**, because that is what the uncertainty is about. On one real
player, receiving yards at n=1,046 rungs gives `[0.4327, 0.4930]`; the same record on its 53 games
gives `[0.3438, 0.6034]` — **4.3x wider**.

**This was shipped wrong first and is now corrected on the live export.** The first version built the
interval on `n`, which made every published interval up to 3.2x too narrow. Track F raised the
general shape — comparing two intervals side by side is anti-conservative when the subjects share a
denominator — and measuring it found the shared denominator was not across players (two players never
share a market; verified, 0 shared keys) but *within* one: the rungs of a ladder.

**So: if the page ever puts two players' rates side by side, the honest sample is `games`.** Quoting
`n` invites a reader to see a difference the data cannot support. Any aggregation Track B does should
sum `cleared` and `n` for the rate and sum `games` for the interval — never average rates, and never
re-derive an interval from `n`.

`null` for a player with no settled props — **not** an empty record, because "no history" and "a
history of nothing" are different statements. 672 of 3,970 exported players carry one.

**`stats` is already ordered and the ordering is DATA, not prose.** Priority markets first
(receptions, rush attempts, targets — the markets the research settled on), then everything else by
depth. Each entry carries a `priority` boolean and its full `n`.

**This is deliberate, and it is the W07 claims rule doing its job.** Ethan's instruction was to lead
with the priority markets and *not* suppress receiving yards, which has by far the deepest record —
"hiding the deepest data to flatter the model's own scope would be its own dishonesty" — and to say
that on the page rather than only in a report. But "receiving yards has the most data" is a
comparative claim, so under `tests/handwrittenClaims.test.ts` it cannot ship as a typed string. The
producer therefore exports the ordering and the counts; **the site words it from `n` and
`priority`.** Same split as item 6: one boundary decision in Python, one wording decision in
TypeScript.

**What it is, and is not.** A hit-rate record is a FACT and publishable under the editorial line —
"11-6 to the over" is research. Nothing here forecasts, and there is no pick.

**It needs no closing price, which is why it covers the current season.** Only the posted `line` and
the settled `result` are required, and `line` is non-null on 100% of settled rows in every season.
`outcome_close` has **zero** 2026 rows (it is written only by the historical backfill, newest close
2026-02-08), so anything comparing to the market stops at 2025 while this does not. If a
market-comparison layer is added later it must be labelled with its own coverage, not inherit this
one's.

**Each market is counted once, and this is the subtle part.** A settled prop is stored twice by the
producer — once as the over, once as the under — carrying the *same* market-level result. Counting
both leaves the rate correct and **doubles `n`**, so the interval comes out ~sqrt(2) too narrow and
the error survives review. `core/stats.hit_rate` refuses such a population outright
(`tests/test_hit_rate.py`). If Track B ever aggregates these records further, aggregate `cleared`
and `n` and recompute — **never average the rates**, and never re-derive from a source that carries
both sides.

**Missing on purpose: 681 players.** Every one is a defender (`tackles_assists` 561 players, `sacks`
370). They have settled props and no page, because defensive stats are not exported at player level
— which is your own §3 finding in `docs/audits/w07-track-b/README.md` (~770–990 defensive players
with a tackle each season, none exported). Ethan's sequencing: defensive stat groups is the real
follow-up, and it makes those pages worth existing rather than manufacturing pages to hold one
section. Until then those records are unreachable, accepted knowingly.

---

## 9. Period rows now carry per-player key sets — and `absent` is not `null`

**Status:** producer built and tested 2026-09-18. **Not yet published** — the blast radius is large
enough that Ethan sees the numbers first.

**What changes.** A period row used to carry one fixed key list for every player. It now carries the
keys that player's own production justifies:

> emit a key if ANY period has a non-zero value **or** a null for it; omit it only if it is zero in
> every period.

**Why.** The alternative was eleven defensive zeros on every receiver's page once defenders are
exported, and the receiving block on every linebacker's. The populations are mostly disjoint —
**759 players offensive-only, 6,856 defensive-only, 3,233 both** — and the played-zero path
*manufactured* those zeros (`{k: 0 for k in PERIOD_KEYS}`) rather than reading them from a source.
That is the silent-zero defect, self-inflicted.

**The blast radius is not small, and it is not confined to defenders.** Measured across the current
export, **19,201 of 19,201 season files** lose at least one key, a median of **11 of 15**, and
**67.9% of all key-slots disappear** (3,127,890 → 1,003,502):

| key | files where it is zero in every period |
|---|---|
| `ret_td` | 96% |
| `two_pt` | 92% |
| `int` / `pass_td` | 91% |
| `pass_yds` / `pass_cmp` | 87% |
| `pass_att` | 85% |
| `rush_td` | 82% |
| `fum_lost` | 75% |
| `rec_td` | 65% |
| `rush_yds` / `rush_att` | 53–55% |

Tables that currently render a column of zeros will find the key absent. `roleColumns` already derives
columns from the data, so this should mostly be an improvement rather than a break — but it is every
player page, not a subset.

### The one thing that needs a decision on your side: `absent` vs `null`

The site reads `p.stats[key] ?? null` and renders null as **"{stat} is not recorded for {season}"**.
That conflates two different facts:

- **`null`** — nobody recorded this stat that season. 2003–08 targets, 2003–2011 `def_tfl`. "Not
  recorded" is exactly right.
- **absent** — this player does not accumulate this stat. A receiver has no pass attempts. "Not
  recorded" is **false**; the honest rendering is to omit the column entirely.

With 68% of key-slots going absent, this stops being a corner case. **The producer preserves the
distinction** — a null key is still emitted with a null value, precisely so the silent-zero work
survives — but the site currently cannot tell them apart.

**Until it can, `snaps`, `snap_share` and `target_share` are exempt from the rule and always
emitted**, because they feed the usage frame where the "not recorded" note is rendered. Dropping
`target_share` for a receiver with genuinely zero targets would publish "Tgt % is not recorded",
which is a false statement produced by a correct-looking change. Once the site distinguishes absent
from null, that exemption can go.

---

## 10. The contract moved — four changes, and your CI goes red until you re-vendor

**Status:** landed 2026-09-18 by track A. **Nothing was blocked on your side before this and now
something is**, which is the reason this is filed rather than left to a red build to announce.

`calibratedsports-web/contract/v2/contract.schema.json` was **byte-identical** to canonical before
this change (`9d061b5767…`, 49,625 bytes), so `contract-in-sync` is green today and **this change is
what turns it red**. Re-vendor, then `npm run check` will tell you the generated types are stale.

### The four changes

| change | shape | who it is for |
|---|---|---|
| `market_definitions` **added and REQUIRED** on `SportManifest` | `{key: {label, stat, note?}}`, `stat` nullable | you — it is the labels for `prop_history` |
| team key pattern **widened** | `^[a-z0-9]+/teams/[a-z0-9][a-z0-9-]*\.json$` | track C (C-1); no NFL key changes |
| `RosterEntry.games` **now nullable** | `["integer", "null"]`, still required | track C (C-4) |
| `ScheduleGame.opponent_abbr` **now nullable** | `["string", "null"]`, still required | track C (C-7) |

### `market_definitions` is the one you asked for

You filed that `stat_definitions` has no keys for `receptions`, `receiving_yards` or `anytime_td`.
Measured on the real export: **all 8 market names carried by `prop_history` are absent from
`stat_definitions`**, across 672 players and 25,529 records — and nothing caught it, because
`stat_keys_used` never walked `prop_history`. A planted nonsense key there was accepted while the
same key in `career.stats` raised.

So `prop_history[].stat` now has a vocabulary. `stat` points at a `stat_definitions` key, **or is
null** where no published stat is the same claim, with a `note` saying why:

    receptions  -> rec          receiving_yards -> rec_yds     rush_attempts -> rush_att
    rush_yards  -> rush_yds     passing_yards   -> pass_yds
    sacks            -> null    "def_sacks is published only inside team splits"
    tackles_assists  -> null    "settles on the SUM of three columns, all team-splits only"
    anytime_td       -> null    "a binary claim, while `td` is a count"

**Do not fall back to a near-miss when `stat` is null.** `anytime_td` is not `td`; the nulls are
measured, not missing.

### Your build will break in exactly four places, and that is the mechanism working

`lib/schema.generated.ts` currently declares `opponent_abbr: string` and `games: number`. After
regeneration both carry `| null`, and `tsc` fails at:

- `components/views/TeamView.tsx:101` — the results-strip `title` interpolates `opponent_abbr`
- `components/views/TeamView.tsx:174` — the Opponent column's `sort` key
- `components/views/TeamView.tsx:176` — `` `${g.home ? "vs" : "@"} ${g.opponent_abbr}` ``
- `components/views/TeamView.tsx:272` — the roster `G` column renders and sorts `r.games` raw

That is **code leads data, enforced by the compiler rather than by anyone remembering**: NFL keeps
emitting non-null for both fields, so **no published figure moves**. Nothing renders differently
until a sport that cannot answer those fields publishes. Note the asymmetry the finding rests on —
`snap_share`, `target_share` and `carry_share` beside `games` were already nullable and already go
through handlers; `games` had none because it could not be null.

### Nothing else in this batch needs you

C-2, C-5 and C-6 were deferred. C-5 and C-6 want coverage-and-scope vocabulary, and **item 7's A5
`stat_coverage` is that vocabulary** — they should land with it rather than as a second mechanism.

### The data is already published, and that ordering was load-bearing

`market_definitions` is live in the served manifest as of 2026-09-19T01:29:18Z — one key uploaded,
bytes verified against the local export, all 8 entries present, `stat: null` on `anytime_td`,
`sacks` and `tackles_assists`. Five other keys were sampled and confirmed **unchanged**, so nothing
else moved.

That order was not a convention, it was the thing that keeps your site up. `REQUIRED_KEYS` is
GENERATED from the contract, and `validateEnvelope` (`lib/schema.ts:71`) does:

```ts
const missing = REQUIRED_KEYS[kind].filter((k) => !(k in o));
if (missing.length > 0) return { ok: false, reason: "shape", ..., missing };
```

The deployed build was generated from the OLD contract, so it does not know the key and ignores it —
additive changes are safe because this check only looks for what is ABSENT. But the moment you
re-vendor and regenerate, `REQUIRED_KEYS.sport_manifest` gains `market_definitions`, and from then on
the served manifest **must** carry it. It already does. Had you regenerated first, line 72 would have
fired and every page would have rendered "data format changed" on a missing required key.

### YOUR CI IS RED RIGHT NOW, and the fix is already sitting in your working tree

Measured 2026-09-19 from the **refs**, not from working copies:

| | sha256 (first 24) | bytes |
|---|---|---|
| `calibrated-sports` `origin/main` — canonical | `6e5d043e8c74bea4023b21d8` | 52,963 |
| `calibratedsports-web` `origin/main` — vendored | `9d061b576766fa6568971a4e` | 49,625 |

`contract-in-sync` curls the canonical file and byte-diffs your vendored copy, so it fails on its
next run for `main`. **You have already done the work**: your working tree carries uncommitted
modifications to BOTH `contract/v2/contract.schema.json` and `lib/schema.generated.ts`, and that
working copy hashes to `6e5d043e…` — the current canonical. It is re-vendored and regenerated
locally and not pushed.

**Why this is stated rather than inferred.** Track A cannot see your CI (the repo is not readable
unauthenticated from here), and "the mechanism must produce red" is not the same fact as "red was
observed". What IS observed is the two refs above. Track A's first check compared the two files on
DISK, which showed them identical and would have led to telling you the opposite — the working copy
standing in for what CI checks out. The tell was that `git log -- contract/v2/contract.schema.json`
carried no commit newer than `5bff8d5` ("Vendor the analytics kinds"), which predates track A's
contract change entirely, while the file on disk contained it.

Nothing is blocked on track A's side and no further contract change is queued ahead of you — push
when the design pass is at a natural stopping point.

### Two things in your own tests, found while checking the above

1. **`tests/schema.test.ts:16-20` will fail misleadingly on re-vendor.** The fixture hard-codes the
   manifest's top-level keys, and its own comment says it: *"Every top-level key
   REQUIRED_KEYS.sport_manifest lists, and no more - the list is generated from the contract, so a
   key added there must appear here or these tests fail for the wrong reason."* It needs
   `market_definitions: {}` added. Reported rather than fixed — it is your file.

2. **Additive tolerance is real but UNGUARDED, and that is the more important one.** Nothing in the
   `describe` block asserts that an unknown extra top-level key is accepted. Tighten line 71 to an
   exact key-set match and **every existing test still passes** — while silently breaking the rule
   that the whole publish order rests on. The property the producer relies on is currently a
   property of the implementation, not of the suite.

   A test shaped like the others would close it:

   ```ts
   it("accepts a key it has never heard of, which is why additive changes are safe", () => {
     expect(validateEnvelope({ ...manifest, some_future_field: 1 },
       "sport_manifest", "nfl/manifest.json", "nfl").ok).toBe(true);
   });
   ```

   Track A has the mirror of this on its side: the contract closes its objects
   (`additionalProperties: false`) so an additive field fails the EXPORT until the contract moves.
   The two halves are deliberate and opposite — strict at write, lenient at read — and only one of
   them is currently tested.

---

## 11. A14 accepted and built — with three deviations, one of which removes a figure

**Status:** built 2026-09-19, **not yet pushed** — it is held behind a cross-track decision, not
behind anything of yours. Ethan approved A14 as the highest-leverage item in track A's queue.

`manifest.teams[]` was `{slug, abbr, name}`. It now carries:

```json
{
  "slug": "buf", "abbr": "BUF", "name": "Buffalo Bills",
  "conference": "AFC", "division": "AFC East", "classification": null,
  "season": {"games": 1, "cleared": 1, "missed": 0, "tied": 0,
             "points_for": 36, "points_against": 31, "markets": 4}
}
```

Ranks need nothing, as you said: the site computes them from the 32 rows it already has.

### 1. `tied` is added, and you did not ask for it

`ScheduleGame.result` is already `W`/`L`/`T`/null — **this sport has ties**. A `{games, cleared,
missed}` triple silently loses a drawn result: `cleared + missed` stops equalling `games` and nothing
on the page says why. Implementing the requested shape faithfully would have published a record that
drops a result, so the third outcome is a field. A producer test asserts
`cleared + missed + tied == games` for every team, which the schema itself cannot express.

### 2. `division` is `"AFC East"` — exactly as you asked, and track A nearly got it wrong

Track C's C-3 (approved in the same round) wanted `conference` and `division` as separate fields, and
track A proposed splitting `"AFC East"` into `"AFC"` + `"East"`. **That was wrong and the source
settled it.** nflverse's `team_division` stores the grouping as ONE ATOM — the eight values are
`"AFC East"` through `"NFC West"`, checked rather than assumed — and no bare region is recorded
anywhere. Splitting it would publish a decomposition the sport does not have.

So `division` is the atom you asked for, `conference` is `team_conf` beside it, and your board groups
by `division` directly. Worth noting the tension, because your own A13 rule pointed the other way:
components rather than derived totals argues for splitting, and here there are no components in the
source to publish. The rule holds; the data has only one part.

`classification` is the competitive tier, null for this sport. It exists for C-3.

### 3. `plays` IS NOT EXPORTED, and this one costs you a cell

**There is no source for it.** The store has no play-count column in any table and no play-by-play
table at all — only `nfl_games`, `nfl_player_week` and `nfl_teams`. A team play count needs exactly
the play-by-play ingest that A14 itself excluded for neutral pass rate, EPA per play and pressure
rate: *"those need play-by-play context, they are a different ingest."* `plays` is in the same
position and the filing did not notice.

Two things track A deliberately did **not** do:

- **Not exported as a permanently-null field.** A key that is always null is a promise of a figure
  that is not coming, and it invites an empty column that never fills.
- **Not approximated** from attempts plus carries per team-game. That would produce a plausible
  number, indistinguishable on the page from a sourced one, and the standing rule is that every
  figure is query-derived or visibly marked. An approximation here is the worst case: it looks
  sourced.

So the board's plays-per-game cell stays marked. If it matters enough, the route is a play-by-play
ingest as its own unit — and that would also unlock neutral pass rate, EPA and pressure rate
together, which is the better shape for that decision than doing one of them.

### `markets` — CORRECTED. It counts markets, not players

**The earlier answer in this section was wrong and is retracted.** It said `markets` was "the number
of priced players on that team". Ethan's correction, 2026-09-19: *the card's label says markets and
its empty state says "No ladder" — both name the market, not the person. A player with two stats
priced is two markets.*

It now counts **distinct priced markets**: components with `basis == "MARKET"` and a non-empty
ladder. So a player with receptions and rush attempts priced contributes **2**, one with receptions
alone contributes 1. `rec_yds` is DERIVED and `td` is ANCHORED — neither was quoted, so neither is a
market — and a `rush_att` component carrying `rungs: []` because no ladder was listed contributes
nothing.

The first implementation could not have been right: it counted index entries against `market_keys`,
which is one file key per player and therefore structurally blind to a player carrying two ladders.

**And a naming collision you should know about, because I am not changing it.** `counts.market` in
the same manifest means something *different* — it is priced **players**, which is what
`PlayerView.tsx:340` reads for "N of 3,970 players priced this week", and the contract documents it
that way. So `counts.market` counts players and `teams[].season.markets` counts markets. Both are
correct for their own caller and the names do not say so. If you would rather one of them were
renamed, that is a contract change and it is track A's to make — say which.

### And `TeamFile.memberships`

C-3 adds a per-season membership history to the team file. **It ships EMPTY for this sport**:
`nfl_teams` holds one row per abbreviation with no season column, so there is no history in the store
to publish. An empty array says that; an absent key would not. The current season's grouping is in
the manifest, which is what your board reads.

---

## 2. `docs/site-architecture.md` is now canonical in `calibrated-sports`

**Status:** the file is in place here; the Track B half is not done.

`calibrated-sports/docs/site-architecture.md` is the canonical copy (336 lines), byte-identical to
what `calibratedsports-web/docs/site-architecture.md` held. Two things remain, both in the web repo:

1. Add it to the **`contract-in-sync`** job in `calibratedsports-web/.github/workflows/ci.yml`, which
   already curls the canonical contract from this (public) repo and diffs the vendored copy. The same
   treatment applies: curl `docs/site-architecture.md` from here, diff the vendored copy, fail on
   drift.
2. The web repo's copy becomes a **vendored** copy rather than a second original.

Track A did not edit that workflow or delete the web-repo file: it is Track B's, and W07 says an item
belonging to another track is reported rather than fixed.

---

## From track C — CFB team colours are a QUERY, not an ingest (filed 2026-09-17)

**Status:** nothing to build on the Track C side; filed so track B does not wait for a feed.

`cfb_teams` in `cfb.db` already carries `color`, `alternate_color` and `logo`, from the
sportsdataverse ESPN teams release, keyed `(season, team_id)` where `team_id` is the **ESPN team
id** — the same id space `cfb_games.home_id` / `away_id` use, so no name matching is involved.

Measured 2026-09-17 on season 2026, current rows only:

| classification | teams | `color` | `alternate_color` | `logo` |
|---|---:|---:|---:|---:|
| fbs | 138 | **138** | **138** | **138** |
| fcs | 128 | 124 | 105 | 128 |
| ii | 162 | 150 | 16 | 155 |
| iii | 242 | 233 | 104 | 237 |

**0** FBS team ids referenced by 2026 games are missing a `cfb_teams` row. Colours are stored as
six hex digits with no leading `#`.

Two things to hold to, both live constraints elsewhere on the site: conference membership moves, so
a colour row is per SEASON and must not be carried across seasons; and per the design rules team
colour is identity only — chips and hairlines, never a chart fill or a row background.

Alternate colour is materially thinner outside FBS (16 of 162 in D-II). Anything below FBS needs a
fallback, not an assumption.

---

## F1 — two players' intervals side by side need the shared-denominator caveat

From track F, 2026-09-17. Filed rather than built: this is a rendering rule and
track F does not do pages.

**The fact.** Any share-of-team figure — target share, carry share, snap share,
route share, any "% of team" — has a denominator two teammates divide. If A's
share is up in a game, B's is down, because they split the same total. Two such
intervals rendered side by side are therefore *not* two independent estimates,
and a reader eyeballing the gap between them is performing a comparison that
neither interval is advertising.

**What track F already did about it.** The bootstrap draws are independent per
subject (they were not, and shared draws measured **anti-conservative** at the
correlation teammates actually have — see `docs/F02-usage-stability.md`, "Reading
two players side by side", and `analytics/crn_check.py`). So the arithmetic is
clean. The remaining half is the rendering.

**What the export carries, so nothing has to be re-derived.** Every metric row in
`f_metrics` has a `shares_denominator` column:

| value | meaning | example |
|---|---|---|
| `team` | two subjects divide one total; the caveat applies | `role.touch_share`, `script_elasticity.*` |
| `own` | the denominator is the subject's own total; nothing is shared | `air_yards.bins.*` |
| `null` | not a share at all | `pace.seconds_per_play` |

Current split: **14 metrics `team`, 15 `own`, 4 null.** The registry refuses a
share-shaped metric that leaves it unset, so the field is never silently absent.

**The ask.** Where two `team` subjects are shown together, say that overlap is
not "no difference" and that separation is a stricter bar than a direct
comparison — and do not render a difference between two published intervals as
though it were a measured contrast. A genuine contrast has to be bootstrapped as
one quantity over shared blocks (the rule brief 018 set for the selection gap);
track F can publish one on request rather than track B differencing two.

**Not a contract change.** `shares_denominator` is on track F's own
`f_metrics`, not on `contract.schema.json`. If the analytics data is ever
exported through the web contract, this field should travel with it — that
*would* be a track A request, and it is noted here rather than filed, because
the export does not exist yet.

---

## F2 — the analytics row shape, banded rather than ranked

From track F, 2026-09-19. You reached the no-numbered-leaderboard conclusion
independently; this is the row shape that follows from it, with the bands
already computed so nothing has to be re-derived on your side.

### Why there is no rank

`docs/F04-what-the-metrics-support.md` measured how many subjects are
distinguishable from a typical one. Between **28% and 56%** of rows in any of
these metrics are not. A numbered list asserts an ordering between every
adjacent pair; the data supports an ordering between **bands**, not between
rows. F04 measured separation from the median and did **not** measure whether
adjacent ranks separate — so a rank would be a claim nobody has checked.

### The band IS the verdict, and it is already computed

Three bands, from `analytics/claims.py`, each from that subject's own interval
against the metric's null:

| band | meaning |
|---|---|
| `above` | the 95% interval clears the null on the high side |
| `indistinguishable` | the interval covers the null |
| `below` | the interval clears the null on the low side |
| `insufficient` | fewer than 5 blocks — the interval is not read at all |

`insufficient` fires on **0 of 52,583 values today** (smallest published `n` is
8 against a threshold of 5). It is in the enum because it is reachable, and the
count is worth rendering as a zero rather than omitting.

### Band sizes, measured

| metric | above | indistinguishable | below | total |
|---|---:|---:|---:|---:|
| `role.onfield_share` | 3,944 | 2,944 | 3,643 | 10,531 |
| `role.touch_share` | 2,162 | **4,788** | 1,831 | 8,781 |
| `air_yards.quantiles.receiver` | 1,823 | 2,321 | 2,140 | 6,284 |
| `script_elasticity.targets` | 593 | **1,905** | 589 | 3,087 |
| `script_elasticity.carries` | 679 | 1,143 | 593 | 2,415 |
| `air_yards.polarity.receiver` | 424 | 748 | 399 | 1,571 |
| `pace.seconds_per_play.by_season` | 135 | 583 | 143 | 861 |

**The middle band is the largest in every one of them.** That is the page, not
a caveat on it.

### The row

Every field is already in `f_metric_values` and `f_metrics`; nothing new has to
be produced.

```json
{
  "metric": "script_elasticity.targets",
  "subject": "00-0036355",
  "slice": "",
  "verdict": "above",
  "estimate": 0.0808,
  "interval": [0.0254, 0.1500],
  "n": 14,
  "null": 0.0,
  "sentence": "Share of his team's targets when trailing by 7+, minus the same share when leading by 7+ - used more when his team is trailing: +8.1 points of share (95% interval 2.5 to 15.0, n=14 games)."
}
```

- **`interval` and `n` are not optional.** The contract already enforces it —
  `AnalyticValue` makes `interval` non-nullable and `n` an integer `minimum: 1`
  — so a row without them cannot exist upstream. A row that renders without
  them re-creates a point estimate at the last hop.
- **`null` is on the row** because it differs by metric. An elasticity's null is
  zero; a share's null is the median subject. "Cannot be distinguished from
  zero target share" would be true and absurd.
- **`sentence` is generated**, from `analytics/claims.py`. Render it, or word it
  yourself in `lib/claims` from the fields — both satisfy the rule. What must
  not happen is transcribing it into a component, which makes it hand-written
  one layer down.

### Sort order, since it is not a rank

Sort within a band by `estimate`, and label the band. Do not number across
bands, and do not number within one — two rows inside `indistinguishable` are
by construction not distinguishable from each other.

If you want an ordering claim between two specific subjects, ask and track F
will bootstrap the contrast as one quantity over shared blocks. It is **not**
the difference of two published intervals; brief 018 set that rule for the
selection gap and it holds here.

---

## From track C — MLB: name Retrosheet, not `statsapi`, and render Retrosheet's statement (filed 2026-09-22, c-07)

**Status:** request, nothing edited in the web repo. It blocks any MLB publish. It does not block
anything that is live today, because `/mlb` is `comingSoon` with `pages: ["home"]`.

**1. `config/sports/mlb.ts`: `sourceName: "statsapi"` → `sourceName: "Retrosheet"`.**
- **Why.** `statsapi` is MLBAM's Stats API. c-03 **refused** it on MLBAM's own notice
  (http://gdx.mlb.com/components/copyright.txt, re-fetched by f-03): "Only individual,
  non-commercial, non-bulk use of the Materials is permitted". An ingest is bulk use. None of the
  data in `mlb.db` comes from MLBAM. `mlb.sources.check_url` refuses `statsapi.mlb.com` by name,
  and every row is from Retrosheet.
- **Where it shows.** `StaleBanner` (6 routes), `SourcesView` and `SportHome` (both the
  "record." line and "Coverage from the … record") all render `config.sourceName`. The site would
  name, as its data source, the one source this project refused on licence grounds.
- **Checked on:** web `origin/main` 3200177, by `git show`. f-03 found the same value on
  `b-integration` and local `main`.

**2. Render Retrosheet's statement on every `/mlb` page, from the manifest.**
- **Why.** Retrosheet's licence has one condition: its statement "must appear prominently". The
  site renders it nowhere (f-03: 0 site files mention Retrosheet).
- **What to render.** Once track A lands A-C10, the MLB manifest carries
  `attribution: {statement, source, source_url, terms_url}`. Render `statement` **verbatim**,
  including its two spaces and the quoted "www.retrosheet.org". Put it wherever the sport's pages
  show their source: a footer line on every route under `/mlb`, and the MLB row of `SourcesView`.
  Take it from the manifest rather than typing it into `mlb.ts`: a licence statement is data, and
  the producer pins it verbatim (`tests/test_ingest_mlb.py`).
- **`null` means no attribution required, and renders nothing.** A manifest **missing** the key
  should render a visible "attribution unknown" state, not nothing. That is the lenient-reader
  rule, applied so that a missing key reads as a gap.
- **The statement, for reference only** (the manifest is the source):
  `The information used here was obtained free of charge from and is copyrighted by Retrosheet.  Interested parties may contact Retrosheet at "www.retrosheet.org".`

**3. For when MLB leaves `comingSoon`: `StaleBanner` will say "Retrosheet is late", and that is
false.** The MLB manifest sets `stale: true` because the contract has no other way to say "this
source publishes after the season ends, by design" (c-03 finding M-2, with track A). Retrosheet
is not late. Its release schedule is annual. Do not show the stale banner for MLB until M-2 has a
contract answer. Otherwise the fix in item 1 makes the banner name the right source in a
sentence that is still wrong.
## From track C - c-09: the held sports' pages say "coming", and one figure is wrong (filed 2026-09-22)

Scope decision 2026-09-22: NBA and NHL are **held**. Measured on the live site
(`/build.json` commit `3200177`, built 2026-09-21T06:32Z) by fetching `/nba`, `/nhl`,
`/mlb` and reading the server-rendered text:

1. **Every coming-soon page says the sport is coming.** The eyebrow chip reads "Coming
   soon"; the section is headed "What it will lead with"; the note reads "The three below
   are what this page will lead with when it is built." For a held sport that is a claim
   about the future nothing backs. Recommended: drive the chip and the section from
   `coverage.json`'s `sports[].status` (A-C11) - `held` renders "Held" with `since` and
   `reason`, and drops "What it will lead with" entirely rather than drawing three empty
   cards for it. Until A-C11 lands there is no field to read; do not hard-code "held" into
   `nba.ts`/`nhl.ts` as a new hand-written status.
2. **`/nhl` shows "Season slots 82" and the NHL season now has 84 games.** The 2026-27
   NHL regular season is 84 games (CBA effective 2026-27; opens 2026-09-29 per the NHL
   season article and the NBC/TSN reports). `config/sports/nhl.ts` `seasonWeeks: 82` is
   the 2025-26 figure. The same `82` is on every web branch (`main`, `b-integration`,
   `b-03`, `b-07`, `b-08`). `nba.ts` 82 is still right for 2026-27.
3. **The shell's per-sport copy is NOT on the site**, so nothing there needs retracting -
   but note it for anyone porting it: `design/shell.html` `SOON.nhl` says "Eighty-two
   nights" and figure "Games each 82" (wrong from 2026-27, as above); `SOON.nba`,
   `SOON.mlb` and `SOON.nhl`'s other figures (30/82/2; 30/162/6; 32 teams, 4 divisions)
   are right. `SOON.ncaab` and `SOON.golf` figures were not checked.
4. **"1 of 5 sports on the rail have a build" sits under a rail of 7.** The count is
   computed from `SPORTS` (5); the rail renders 7 entries from `content/legacy/site.json`
   (NCAAB and GOLF as inert "Not yet live" cells). Golf is held (LEDGER 09-22); NCAAB has
   no decision. The two lists disagree and the page states the smaller one.
5. **`/mlb` says "There is no ingest ... for MLB yet"** - true of what is published, false
   of the project since c-03 (Retrosheet 1999-2025 ingested, not published). And MLB is
   in season. If MLB ever publishes through the current `StaleBanner`, the banner reads
   "`<sourceName>` is late: stats through 2025 · date 305 — Retrosheet publishes a season
   only after it ends; the newest season held is 2025.. Market data for No current-season
   data is current." (from the c-07 probe manifest and the component's template, not
   rendered). Three things wrong in it: "late" (the source is historical by design, M-2),
   the doubled full stop, and a market-data clause for a sport with no market data.
## From track C - c-11: MLB must not read as current; a label draft for when the gate opens (filed 2026-09-22)

**Nothing to build yet.** MLB stays `comingSoon` until A-C10 is in the contract and c-07
is merged (see `docs/C11-mlb-cutoff.md`, "The gate"). This is filed now so the label lands
in the same change that flips `comingSoon`, not after it.

- **The store stops at 2025-11-01** (measured c-11, `mlb.db` `mode=ro`) and holds no 2026
  game. A page that says "MLB" beside a live postseason reads as current.
- **Draft label, awaiting Ethan's approval:** "MLB · 1999–2025 · historical", and beneath
  it "Box scores from Retrosheet, 1999 through 1 November 2025. The 2026 season is not
  here: Retrosheet publishes a season after it ends."
- **Every figure is read**: the range from `seasons`, the date from the maximum
  `event_dates.last` over coverage's MLB stats sources (A-C12, a `YYYY-MM-DD` calendar
  date - format it as a date, never through `new Date()` into local time, which puts it
  on 31 October in any US zone), the season from `manifest.current.season`.
- **"historical" is a verdict**: `current.season > max(seasons)`. It belongs in
  `lib/claims` with a `tests/falsifiable.test.ts` entry that drives it both ways.
- **Not `StaleBanner`.** It words the cutoff as the source being "late", and prints
  `data_through` as "date 305". Already filed by c-07 (item 3) and c-09; restated because
  the label above replaces what the banner would otherwise say for MLB.
## f-07 — the drafting predictor, once a-05 carries the f-07 fields (2026-09-22)

- **Render `attribution.statement` wherever any slice in `attribution.applies_to` is
  shown.** Sports-Reference's terms make explicit credit the condition of republishing,
  and every drafting slice rests on PFR's draft table (not only w_av and snaps4).
- **Lead each slice with `reading` and render `statement` verbatim or not at all.** Do not
  compose a slice caption from `separation.verdict` alone. `w_av` separates
  (`separation.verdict: separates`), but its `reading` is `separates_confounded`, and its
  sentence carries the win-share confound and the missing forecast together. A page that
  shows "separates" without them publishes the one claim the file exists to prevent.
- **`values_reason` non-null means the slice's values are deliberately absent**
  (`values_policy: hide`, Ethan's call). Say so from the enum. It is not an empty state
  and not a failure.
- `bands_reason: confounded`: no ordering of any kind. List the values alphabetically,
  as for `separation_not_rejected`.
## 12. a-08 — the season path for the teams board, and why pace stays marked

**Status:** built by track A on branch `a-08-team-series`, **not merged, not exported, not
published.** When it merges, `contract-in-sync` goes red until you re-vendor: canonical on
`origin/main` today is `30df95a81c…` (57,570 bytes) and your vendored copy is byte-identical to it.

### What is added

`TeamSeasonSummary.cumulative`, **required**, on every `manifest.teams[].season`: one entry per
regular-season week of the league's schedule (18 for 2026), each either

```json
{"index": 2, "state": "played", "cleared": 2, "missed": 0, "tied": 0, "points_for": 77, "points_against": 62}
{"index": 5, "state": "bye", "cleared": null, "missed": null, "tied": null, "points_for": null, "points_against": null}
```

typed `TeamWeekPlayed | TeamWeekEmpty`. `state` is `played`, `bye`, `unplayed` or `gap`. Your
generator (origin/main 9884f96) emits the union cleanly; checked by running it on this contract.

- **Values are cumulative through the week, and null on every non-played week** - not zero, not
  carried forward, never projected. At a bye, hold the line flat by reading `state`.
- **The tabs map to `cleared`, `points_for`, `points_against`.** `missed` and `tied` travel so the
  running record sums to games played.
- **The axis ceiling is yours to compute**: the max over the 32 arrays for the selected metric. It
  is deliberately not published as its own figure, which could disagree with the arrays.
- **The last played entry equals `season`'s own totals**, and the producer refuses to export
  otherwise, so the end of a line cannot contradict the record printed beside it.

### The state rule is YOUR strip's, ported - and the two copies are the risk

`state` is decided by `TeamView.tsx` `slots()` over `lib/slotState.ts`, transcribed into
`jobs/export_web.path_states`. The strip's `off`/`live` split is one exported state, `unplayed`,
because it depends on the reader's clock. A producer test re-derives your strip's forms from each
exported team file's `schedule` and requires the manifest to agree; on the real 2026 store it agrees
32 of 32. **But that test transcribes your code as of 9884f96 - if the strip's rule changes, it
does not notice.** The cheap closure is on your side: have the strip read `season.cumulative[].state`
for the current season, or add a site test comparing `slots()` forms against it.

### Pace (and the splits table) is still not exportable

Unchanged from section 11's `plays` answer, re-checked 2026-09-22: the store holds `nfl_games`,
`nfl_player_week`, `nfl_snap_counts`, `nfl_teams`, `player_xwalk`, `player_alias`,
`player_headshot`, `nflverse_versions` and no play-by-play or play count. The shell's splits table
(points/game, plays/game, neutral pass rate, EPA/play off and def, pressure rate allowed, each ranked
of 32) is on the TEAM page, and five of its six figures need play-by-play. Points/game you already
have (`points_for / games`), and its rank you compute from the 32 rows.

**Where the play-by-play does exist is track F's store, not track A's.** `analytics.db` carries
`f_play_usage` (1999-2026, 1,365,453 rows, with `is_neutral` and `is_dropback` per player-role row).
Inferred, neither built nor measured: that may be enough for plays per game and neutral pass rate; it has no EPA or
pressure column. The producer does not read another track's store, so pace arrives - if it does -
as a track F analytic under its own `analytics/{sport}/` prefix, not in this manifest.

## From track C - c-13: what `/cfb` needs before `awaitingExport` comes off (filed 2026-09-23)

Full evidence: `docs/C13-cfb-gate.md`. Read at your deployed `9884f96`, and rendered there with
the flag mocked off, against the published CFB manifest (byte-identical to what
`/data/cfb/manifest.json` serves). **No key a declared CFB page requests is missing.** The gate
is what the pages say about the keys that exist. Each item carries the test that closes it.

1. **B-C13-1 - `SportHome` links only declared pages.** `components/views/SportHome.tsx`
   hard-codes seven cards. On `/cfb`, cards 04 (`/cfb/fantasy`) and 06 (`/cfb/news`) link to
   routes `gatePage` 404s, and the page says "7 pages sit under CFB" when 5 are declared.
   *Accept:* render `SportHome` with the cfb config and the published manifest. Every href
   starting `/cfb/` has `gatePage("cfb", page) !== null`, and the stated page count equals the
   number of cards rendered. The NFL render is unchanged (existing `sportHome.test.ts` green).
2. **B-C13-2 - `SportHome` copy that the CFB manifest contradicts.** Three things, all rendered:
   the headline "0 players.", with "The whole record for every player who has taken the field
   since 2004 - game logs, usage and season totals"; the exchange copy ("A market-implied
   distribution is published where the exchange lists a ladder... between slates"); and the
   "Markets priced" and "Ladder rungs" tiles. CFB publishes no players (`counts.players: 0`, the
   index is empty by design) and no market (`market_definitions: {}`; Kalshi lists no CFB props).
   Key the player sentences on `counts.players > 0` or `pages.includes("player")`, and the
   market block on a non-empty `market_definitions`, never on the sport's name.
   *Accept:* the cfb render contains none of `0 players`, `every player who has taken the field`,
   `exchange lists a ladder`, `between slates`, `Ladder rungs`. The nfl render still contains
   each one it contains today.
3. **B-C13-3 - `/cfb/players` must stay gated when home and teams flip.** Off the gate it renders
   "0 players in the record", "Every other player in the record has a page", and Offense /
   Defense / Special teams leader tabs. Recommended: make the gate per page
   (`awaitingExport: true | readonly PageType[]`, or a separate `awaitingPages`), so `players`
   keeps its awaiting view while `home` and `teams` read the manifest. Dropping `players` from
   `pages` is the alternative. It removes a v7 page, so it is Ethan's call rather than yours.
   *Accept:* with home and teams released, `/cfb/players` renders the awaiting view and makes 0
   reads, while `/cfb` and `/cfb/teams` each make 1.
4. **B-C13-4 - the teams board's copy assumes team pages.** The figure note reads "Teams 138 -
   with a page", and the lede reads "A team's own page carries its schedule..." and "Every
   franchise". CFB declares no `team` page, so both claims are false there.
   *Accept:* with `pages` lacking `team`, the rendered board contains neither `with a page` nor
   `own page`, and its rows have no `href` (they already have none).
5. **B-C13-5 - when `team` is declared: link an opponent only if it has a page.** `TeamView`
   `Schedule()` links any opponent with a non-empty `opponent_abbr`. Non-FBS opponents carry an
   abbreviation and no page. And today `opponent` is a display name (`/cfb/team/East Carolina`):
   12 of 12 links on Alabama, 0 of which resolve. Track C fixes the slug (P2 in C13). The guard
   is yours: link only when `opponent` is in `manifest.teams[].slug`.
   *Accept:* render `TeamView` for every published cfb team file. Every `/cfb/team/` href is a
   manifest slug. **Do not declare `team` before track C's P1**: every published CFB spread has
   the wrong sign (corr -0.719 with the team's own margin over 21,374 rows), and the page would
   render it.
6. **B-C13-6 - the flip changes a test on purpose.** `tests/routing.test.ts:36` asserts
   `cfb.awaitingExport === true` and the exact page set. *Accept:* the flip commit edits that
   assertion to the new gate. It is not deleted.
7. **B-C13-7 - decide whether the Shell may read an unpublished sport's manifest.**
   `components/Shell.tsx` fetches `/data/{sport}/manifest.json` on every sport home without
   checking `awaitingExport`. Since the 09-23 publish that returns 200 for cfb, so `/cfb`
   should now show `CFB / Week 3` in its strip while the page says "Export not published". This
   comes from reading the code; the effect is client-side and was not seen in a browser.
   *Accept:* either the fetch is skipped while awaiting (no request to
   `/data/cfb/manifest.json` from `/cfb` with the flag on), or the decision to keep it is recorded.

Not needed from you: nothing reads `sports.json` on the site (`sportsKey` has no call site), so
its missing `cfb` entry does not block the flip.
