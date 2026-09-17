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
