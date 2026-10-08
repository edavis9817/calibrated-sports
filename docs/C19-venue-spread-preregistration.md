# C19 — exchange mid against the book close: pre-registration

Committed **before** any venue-spread, lead/lag or score number is computed. The
script is `research/venue_spread.py`; it is written after this file and must
implement what is below. Nothing here is edited after the first run; a
correction is appended at the bottom, dated, with the reason.

Unit c-19 (track C), 2026-09-28. Base: `origin/main` at `6a0e612`.

## Inventory already done (counts only; no comparison computed)

- `market_log.db`, opened `mode=ro`. Live quotes are pruned at 14 days on
  `ingest_ts` (invariant 8), so the store holds `source='live'` rows from
  2026-09-15 00:16Z onward. **Week 1 Kalshi quotes are gone from the store**;
  the raw archive for them is in R2.
- Odds API live rows begin 2026-09-17 00:15Z: 34 events, 8-10 prop snapshots
  each (`config.ODDS_SNAPSHOTS_MIN` = 4320,1440,180,60,10). Book staleness
  `ts - source_ts`: p50 39s, p90 99s, p99 260s; 0.003% over 600s.
- Prop market keys present that map to the model's two stats:
  `player_receptions`, `player_receptions_alternate`, `player_rush_attempts`.
- **No live Odds API prop row has a `markets` or `market_outcome` row** (the
  live adapter writes quotes only; `map_markets` has only ever mapped the
  historical backfill). So no join between the exchange and the book exists in
  the store for 2026. The pairing below is computed in memory; nothing is
  written.
- `outcome_close` holds 2023-2025 only. `predictions` hold week 1 2026 only.
  `nfl_player_week` holds 2026 weeks 1-3; week 3 has one game without a final.

## Scope

NFL, 2026 regular season, Kalshi `KXNFLREC` and `KXNFLRSHATT` (outcome side
`over`; Kalshi YES "N+" = over N-0.5), against the Odds API `us` books.
**Pre-kickoff only**: book fetch time < kickoff (nflverse, via the Kalshi
outcome's `event_id`) and Kalshi quote time < kickoff. In-game is not built.

Team lines (`KXNFLSPREAD`/`KXNFLTOTAL`) are NOT re-measured by new code: brief
020's own pre-registered script, `research/consensus.py`, is re-run unchanged on
week 3, the only week whose raw `odds_bulk` archive is still on local disk.

## Step 1 — pairing (must pass before Step 2 is read)

- **Book price.** Per snapshot fetch (`quotes.ts`), per book, per (player,
  stat, line): both an Over and an Under row at that fetch. Main key preferred
  over the alternate key where a book quotes the same line on both. Implied
  probability is the stored `mid`; de-vig MULTIPLICATIVE via
  `jobs.backfill_oddsapi.devig_pair` (overround must lie in (1.00, 1.15); a
  violation drops the pair and is counted). Freshness: `ts - source_ts <= 600s`
  on both sides. Consensus = median across `BENCHMARK_BOOKS` (DraftKings,
  FanDuel, BetMGM) — the same definition as `outcome_close.p_bench`, which is
  what "the model trails the book close" is measured against. Secondary: median
  across every book (`p_all`).
- **Player.** Odds API name -> `gsis_id` through `venues.mapping.resolve_player`
  with its store reads redirected to a `mode=ro` connection. Unresolved and
  ambiguous names are counted, not guessed.
- **Game.** A book row pairs with the Kalshi outcome for the same
  (gsis, stat, line, over) whose kickoff is the first one after the fetch and
  within 80h of it. Every Odds API `event_id` must pair to exactly one nflverse
  game; the count that pair to more than one must be 0 or the step fails.
- **Kalshi price.** Last live quote at or before the book fetch time, both
  sides present and `0 < bid < ask < 1`. mid = (bid+ask)/2, NOT de-vigged.
  **Freshness: fetch - kalshi_ts <= 600s. This window is fixed; it is not
  widened if the sample is thin.**
- **Report:** matched N (lines, fits, games), the gap distribution
  (fetch - kalshi_ts and fetch - book source_ts), and drops by reason (no
  resolvable player, no Kalshi rung at that line, Kalshi one-sided, Kalshi
  stale > 600s, book stale, overround violation).
- **Thin rule, fixed now:** Step 1 FAILS if the matched set at the close has
  fewer than 10 games or fewer than 50 (player, stat) fits. A failure is the
  finding; Steps 2-4 do not run.

## Step 2 — the spread

- **The close** = per game, the last book snapshot with fetch < kickoff, and it
  must lie within 15 min of kickoff (as `outcome_close.lead_min <= 15`).
- spread = 100 x (Kalshi mid - p_bench), in pp, signed, one value per matched
  line at the close. Reported per stat (receptions, rush attempts): n lines,
  fits, games, p10/p25/p50/p75/p90, mean with a 2,000-draw game block
  bootstrap 95% interval, and the share of |spread| > 2.5pp. Also by
  time-to-kickoff over all snapshots (>24h, 6-24h, 1-6h, <1h), descriptive.
- **Fee (`docs/kalshi-fee-mechanics.md`, `core.fees`).** Treating p_bench as
  fair, the taker edge per contract of crossing toward the book:
  buy YES at ask if p_bench > ask, buy NO at 1 - bid if p_bench < bid,
  `edge = fair - price - fee_per_contract(price, C, 'taker', series M)`, at
  C = 10 and C = 100, at the touch. Report the share of lines with edge > 0 and
  the median positive edge. The touch is an upper bound on what 100 contracts
  cost; median touch size is reported beside it. Maker M = 0 on both series, so
  a resting order is fee-free — whether it FILLS is brief 018/019's question and
  is not re-asked here.
- Nothing per line is published: aggregates and intervals only
  (`BET_LIST_RESTRICTION`).

## Step 3 — who moves first

Over consecutive book snapshots k -> k+1 of one matched line, pre-kickoff:

    L1  slope of (book[k+1] - book[k]) on gap[k]      > 0: the books move toward Kalshi
    L2  slope of (kalshi[k+1] - kalshi[k]) on -gap[k] > 0: Kalshi moves toward the books

gap = Kalshi mid - p_bench. Both slopes are biased upward by noise in their own
k-term, so they are compared with each other, not with zero. Interval: game
block bootstrap. **Resolution is the book snapshot spacing** (72h/24h/3h/1h/10m
before kickoff); nothing faster than the gap between two book snapshots can be
resolved, and the report says so.

## Step 4 — the model against the exchange mid (registered hypothesis C19-H1)

**Question.** On the Kalshi rungs the model prices, is the model's Brier score
better than the Kalshi mid's?

- **Prior result, stated before running:** brief 021 already scored the stored
  week-1 predictions against the Kalshi mid (`research/score.py`): model worse,
  +0.0240 [+0.0089, +0.0373], n 706. This is therefore a REPLICATION on new
  weeks, not a first look.
- **Population.** Kalshi REC and RSHATT outcomes (side over), 2026 REG weeks 2
  and 3, whose game has a final score; settled through `core.settlement` against
  the newest `nfl_player_week` row with the snap-count rule (no stat row +
  offensive snaps > 0 -> actual 0; no snaps -> void, excluded). Common set: the
  Kalshi quote at the entry instant is two-sided and <= 600s old.
- **Entry instant.** kickoff - 180 min, per game (the T-180 book snapshot).
- **Model.** `models.baseline.fit_player_stat` exactly as
  `research/walkforward.py`'s `default` variant for T = 2026: module defaults,
  `MEAN_DRIFT_VAR` refit on (2024, 2025), `prior_seasons=(2025, 2026)`, as-of
  kickoff, `Constants.check()` enforced. Position and team from the player's own
  2026 row for that week, snap row as fallback (the walk-forward's precedent).
- **Metric.** Brier(model) - Brier(Kalshi mid), mean paired per-outcome
  difference, 2,000-draw game block bootstrap, fixed seed. MDE = 2.8 x SE.
  Secondary (three-way, same rows where p_bench exists at the T-180 snapshot):
  Brier(model) - Brier(book), Brier(Kalshi) - Brier(book). Per stat as a
  secondary line.
- **Decision rule.**
  - interval entirely below 0 -> "model better than the Kalshi mid on weeks 2-3".
    That is NOT an edge: it is a candidate needing (a) replication on week 4+,
    (b) a fee- and spread-inclusive executable test, before any other claim.
  - interval entirely above 0 -> "model worse than the Kalshi mid"; with brief
    021 that is two independent samples and the thin-rung hypothesis is retired.
  - interval straddles 0 -> "no better than the Kalshi mid", with the MDE.
  - fewer than 5 games in the common set -> not read.
- **Test count for this unit:** Step 2 mean spread x 2 stats = 2; Step 3
  L1, L2 = 2; Step 4 headline 1 + secondary 2 + per-stat 2 = 5. Total 9
  intervals.
