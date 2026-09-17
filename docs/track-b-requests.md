# Track B requests — filed by Track A, not built here

Track A owns `contract.schema.json`; Track B consumes it and regenerates
`lib/schema.generated.ts`. Per `docs/W07-parallel-tracks.md`, an item belonging to another track is
**reported, not fixed**. This file is where Track A files them.

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
(`docs/track-c-requests.md` C3). Today `slotState.ts` hard-codes its own prose about which seasons
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

## CFB team colours are a QUERY, not an ingest — filed by Track C, 2026-09-17

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
