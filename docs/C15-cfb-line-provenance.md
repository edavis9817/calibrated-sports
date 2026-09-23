# C15 — what the published CFB line actually is

Unit c-15, 2026-09-23. Every figure below is re-derived by

    python -m research.cfb_line_provenance --json out.json     # cfb.db read-only, 0 requests

against `cfb.db` as of 2026-09-23 (2013-2025 backfill fetched 2026-09-17, 2026 weeks 2-3,
Odds API forward capture for week 3). Figures move with every weekly ingest.

## The sentence a page can render

> **The CFB spread shown is the last line CollegeFootballData.com recorded for one source
> per game — its consensus line through 2022, and from 2023 whichever listed sportsbook
> sorts first by name — read by us after the game, with no capture time. It is not a
> verified closing line, and the source is not shown per game.**

That is what the store supports for every season that carries a line (2013-2026; 2004-2012 exported games carry none). The narrower
evidence below makes it *likely* that CFBD's per-book value is the book's last
pre-kickoff line, but that was measured on one week of one season and does not license
the word "close".

## 1. Which provider — read from the exporter, not assumed

`jobs/export_cfb_web.game_lines` orders `cfb_game_lines` by
`CASE provider WHEN 'consensus' THEN 0 ELSE 1 END, provider` and keeps the first row per
game. So the rule is **consensus if CFBD sent one, otherwise the alphabetically first
canonical provider name** — not first-arrived, not a named book, not an average. It is
deterministic, and it is still a silent provider mix, because CFBD stopped sending
`consensus`:

| season | published provider (games) |
|---|---|
| 2013-2020 | consensus ≥ 92% every season, residue numberfire/teamrankings/Caesars |
| 2021 | consensus 870, Bovada 12, William Hill (NJ) 5 |
| 2022 | consensus 1,244, William Hill (NJ) 185, Bovada 21, Caesars (CO) 9 |
| 2023 | Bovada 825, William Hill (NJ) 312, ESPN Bet 175, Caesars (CO) 52, consensus 29, DraftKings 20 |
| 2024 | Bovada 804, ESPN Bet 726, DraftKings 27 |
| 2025 | Bovada 934, ESPN Bet 656, DraftKings 7 |
| 2026 (wk 2-3) | Bovada 165, DraftKings 74 |

13,754 exported games carry a line (of 46,296 exported games). Consensus 8,544; Bovada
2,761; ESPN Bet 1,557; William Hill (NJ) 502; numberfire 191; DraftKings 128; others 71.
`numberfire` and `teamrankings` are projection sites, not books, and are published where
they sort first (197 games).

**Defect found, not fixed:** `spread` and `total` come from the same chosen row, so
**2,907 games publish `total = null` although another provider carries one** — 2,901 of
them a consensus row with no over/under, 2013-2016 (730 / 705 / 728 / 736). 15 games
likewise publish a null spread with another provider holding one. Fixing it changes
published totals, and an export publishes everything that changed, so it must wait for
c-14's republish (see the report). `tests/test_export_cfb_web.py::test_c15_*` pins the
current rule, including this defect, and was shown to fail under two mutations.

## 2. Magnitude

- **Every stored spread is on the half-point grid**: 38,640 of 38,640 current rows with a spread,
  every provider, including `consensus`. No derived or fractional values.
- **`consensus` is not simply a book median or mean.** On 2,541 games with consensus and
  ≥ 2 books beside it: equal to the book median 1,270 (50%), within half a point 2,350
  (92%), p90 0.5, max 31.25; equal to the mean 1,223. It behaves like a rounded
  aggregate whose inputs we do not hold. It is on the grid a book quotes, but it is not a
  price any one book offered.
- **Against independent pre-kickoff quotes (2026 week 3 only).** The Odds API forward
  capture (`--odds-forward`) holds books' spreads with fetch times. On 75 of the 119
  week-3 games carrying a CFBD line, the published number against the median of the
  books' last quote before kickoff: 50 exact, 69 within 0.5, **75 within 1.0**, mean
  signed difference −0.08. CFBD's own DraftKings row against the Odds API's DraftKings:
  71 of 75 exact, max 1.0.
- **Which instant CFBD holds — the discriminating form.** Agreement with the last quote
  proves nothing on a game whose line never moved, so restricted to games where the
  book's line moved inside our capture:

  | book | moved | = last pre-kick | = first seen | neither | = live line (and not last pre-kick) |
  |---|---|---|---|---|---|
  | DraftKings | 38 | 34 | 0 | 4 | 0 of 35 that moved post-kick |
  | Bovada | 54 | 50 | 1 | 3 | 0 of 49 that moved post-kick |

  CFBD's per-book value tracks the **last pre-kickoff** line, not the opener and never an
  in-game line. "Last pre-kickoff" is at our capture's resolution: a snapshot 5.6-7.6 min
  before the kickoff hour's first game, so up to ~68 min early for the hour's last game.
- `spread` differs from `spread_open` on 6,860 of the 8,556 rows carrying both, so it is
  not the opener either.

## 3. When it was captured — from the job, not the row

CFBD puts no timestamp on a line. The only knowable instant is when we read it
(`cfb_raw_files.fetched_ts` of the file each current row came from):

| scope | rows | fetched before kickoff | fetch − kickoff |
|---|---|---|---|
| 2013-2025 backfill | 38,325 | 0 | 240 days to 13 years |
| 2026 week 2 | 207 | 0 | 96.6 - 148.6 h |
| 2026 week 3 | 197 | 0 | 58.0 - 109.5 h |

This is the job's design, not an accident of timing: the weekly task
(`CalibratedSports CFB Weekly`, Tuesday 09:00 local = 13:00Z) runs
`--cfbd-week latest`, and `latest_completed_week` resolves the most recent week whose
**last game started ≥ 12 h ago**. So a line is always read at least 12 h after its
week's final kickoff and in practice 2.4 - 6.2 days after its own. **Not one CFB line in
the store was read before its game.** 2026 week 1 has no lines at all (never fetched);
week 4 will be read on 2026-09-29.

## What a close would need

A close is **not recoverable from CFBD** — not by timing the fetch either, because a
pre-kickoff CFBD fetch is still an untimestamped value of unknown age.

1. **Forward (2026 on): already built and already paid for.** `--odds-forward` (approved
   2026-09-17, 3 credits per kickoff hour, cap 75 per CFB week) captured 17 snapshots,
   51 credits, for week 3, every one 5.6-7.6 min before its hour's first kickoff. It
   holds 11 books with their own `last_update`. It covered 75 of 119 lined week-3 games.
   Using it as the published line is an export change plus a contract decision (a
   `line_ts` or basis field), not new spend. It stays "near-close", resolution one hour.
2. **Historical (2020-2025): Odds API historical bulk**, one call per distinct kickoff
   hour at `10 × markets × regions`: 1,643 kickoff hours over games carrying a line →
   **16,430 credits spreads-only, 49,290 for h2h + spreads + totals** (estimate: hours
   from `cfb_games.start_ts`, which carries TBD placeholders). Not proposed: the NFL has
   first claim on the shared pool and no pre-registered question asks for it. That the
   historical endpoint covers NCAAF back to 2020 was NOT checked.
3. **2013-2019: no timestamped source is known** (and 2004-2012 has no line at all). Those seasons can only ever carry
   "CFBD's last recorded line".
