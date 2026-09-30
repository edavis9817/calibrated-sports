# The Kalshi ladder against the sportsbook close — NFL 2026 weeks 2-3, pre-kickoff

Unit c-19 (track C), 2026-09-28. Findings only; nothing publishes from here.

- **Scope, stated so it travels with every number below.** Kalshi `KXNFLREC`
  and `KXNFLRSHATT`, the over side, against the de-vigged DraftKings / FanDuel /
  BetMGM median from the Odds API `us` region. NFL 2026 regular season, weeks 2
  and 3 (book snapshots 2026-09-17 to 09-28). Pre-kickoff only. Team lines are
  NOT covered (see §5).
- **Order of work.** The pre-registration
  (`docs/C19-venue-spread-preregistration.md`) was committed at `ac605b7` and
  pushed before `research/venue_spread.py` existed. The inventory it lists
  (row counts, book staleness) was the only thing computed before it. No rule
  was changed after a result was seen.
- **Reproduce.** `python -m research.venue_spread --cache <scratch>.sqlite --extract`
  (about 8 minutes, `mode=ro`), then the same command without `--extract`. The
  cache holds Odds API prices, so it stays on local disk and is never committed.
- **Licence.** Everything below is an aggregate or an interval. No per-line
  series is published (`BET_LIST_RESTRICTION`).

## Honest prior, written down before the numbers

Brief 019 measured conditional maker CLV by series. It was **negative** on the
tight series and indistinguishable from zero on the wide ones (REC +0.24
[-0.31, +0.86]), with spread/capture correlation +0.90. Brief 021 had already
scored the model against the Kalshi mid on week 1 and found it worse. So the
prior on "the exchange is exploitable" was poor going in. Nothing below
changes that.

## 1. Pairing — PASSES the pre-registered thin rule

The store had **no join** between the two venues for 2026. The live Odds API
adapter writes quotes only. `map_markets` has only ever mapped the historical
backfill, so no live Odds API prop has a `market_outcome` row. The pairing here
is computed in memory, and nothing is written.

| stage | count |
|---|---:|
| book two-sided pairs, fresh (<= 600s) and in the overround band | 17,800 |
| book lines one-sided (the alternate ladders quote the over only) | 96,906 |
| book pairs dropped as stale | 1 |
| snapshot-lines with a benchmark consensus | 4,753 |
| ... no benchmark book quoted the line | 262 (dropped before this row) |
| ... player unresolved | 0 |
| ... player has no Kalshi ladder for the stat | 35 |
| ... Kalshi has the player but not this line | 576 |
| ... Kalshi rung not yet listed at the fetch | 429 |
| ... Kalshi quote older than 600s | 12 |
| **matched snapshot-lines** | **3,701** |
| Odds API events pairing to more than one nflverse game | **0** (required) |

- **Timestamp gaps.** Book age at fetch: p50 40s, p90 106s, p99 216s, max 430s.
  Kalshi, fetch minus the last quote row: p50 245s, p90 548s, max 580s on the
  matched set. Of every line that reached a Kalshi rung, 0.3% were over 600s
  (12 lines). The window was not widened.
- **The Kalshi gap measures time since the price last changed, not clock
  skew.** `store.write_quotes` keeps a row only when the price changes, plus a
  heartbeat every 300s (`QUOTE_HEARTBEAT_SEC`). So in the hot tier, a 245s-old
  row is a price re-read unchanged every 15s. Measured cadence of written rows
  on these markets: p50 313s in the last hour before kickoff, and 600s at 24-30h
  out.
- **At the close** (the last snapshot within 15 min of kickoff):

  | stat | lines | fits | games |
  |---|---:|---:|---:|
  | receptions | 259 | 227 | 20 |
  | rush attempts | 56 | 56 | 20 |
  | all | 315 | 283 | 20 |

  Rule: at least 10 games and at least 50 fits → **PASS**. **12 of the 32
  games have no close.** Their last prop snapshot landed 19.3-19.8 min before
  kickoff, outside the pre-registered 15-min rule, so they are out of Step 2 and
  still in Step 3.

## 2. The spread at the close: 100 x (Kalshi mid - book), pp

| | receptions | rush attempts |
|---|---:|---:|
| lines / games | 259 / 20 | 56 / 20 |
| p10 | -3.10 | -3.97 |
| p25 | -2.08 | -2.50 |
| **p50** | **-1.00** | **-0.38** |
| p75 | +0.04 | +0.95 |
| p90 | +0.97 | +2.07 |
| **mean [95%, game block]** | **-1.06 [-1.30, -0.82]** | **-0.74 [-1.24, -0.29]** |
| share with \|spread\| > 2.5pp | 0.193 | 0.321 |
| Kalshi quoted spread, median | 1c | 1c |

- **Direction.** Kalshi prices the over about 1pp below the de-vigged book
  close, and both intervals exclude zero. Mean Kalshi mid is 0.481 against a
  book 0.491.
- **By time to kickoff** (all matched snapshots, descriptive). The receptions
  median is -0.89 / -0.92 / -1.01 at 6-24h / 1-6h / <1h, and rush attempts is
  -0.81 / -0.69 / -0.82. The spread does not close into kickoff.
- **The fee** (`docs/kalshi-fee-mechanics.md`, `core.fees`; both series taker
  M=1, maker M=0). This treats the book as fair and crosses Kalshi toward it at
  the touch:
  - The book lies outside Kalshi's bid-ask on 196 of 259 receptions lines and
    43 of 56 rush lines.
  - After the taker fee, the edge is positive on **57 / 259 (22%) receptions
    lines, median +0.78pp, at 10 contracts**, and on 60 at 100 contracts.
  - For rush attempts it is positive on 20 / 56 (36%), median +0.99pp at 10
    contracts, and on 21 at 100.
  - Depth confirms the touch. The market_depth touch price equals the quote's
    on 408 of 424 snapshots within 60s. The touch on the side a taker buys is a
    median 9,685 contracts (receptions, n 86) and 1,373 (rush, n 20).
- **Why that is NOT an edge.** 70 of the 77 fee-positive lines buy YES, the
  over. The book close is already measured to **overprice the over by 2.43pp**
  (R18, -2.43 [-3.13, -1.72], 2023-2025). So "the book is fair" manufactures
  over-side edges out of the book's own known bias. Step 4's three-way scores
  the two venues directly on the same rows: Kalshi minus book Brier is
  **+0.0005 [-0.0012, +0.0022]**. Neither venue is distinguishable from the
  other at T-180.

## 3. Who moves first — the cadence cannot resolve it

Consecutive book snapshots of one matched line: 3,148 pairs over 32 games.
Snapshot spacing is p10 24 min, p50 90 min, p90 720 min.

| | slope [95%, game block] |
|---|---|
| L1, books move toward Kalshi | +0.096 [+0.077, +0.122] |
| L2, Kalshi moves toward the books | +0.089 [+0.065, +0.116] |
| **L1 - L2** | **+0.008 [-0.026, +0.045]** |

Each slope is biased upward by noise in its own snapshot-k term, so they are
read against each other. They are equal. **At a book snapshot spacing of about
90 minutes, neither venue can be said to lead.** Both close about a tenth of the
gap by the next snapshot. Anything faster than the gap between two book
snapshots is not resolvable from this data, and the Odds API is snapshot-
scheduled by design (`venues/oddsapi.py`), so this is the resolution there is.

## 4. C19-H1 — the model against the Kalshi mid: WORSE

Registered in the pre-registration before it was run. It is a replication, not
a first look: brief 021 scored the week-1 predictions against the Kalshi mid
(`research/score.py`) and found the model worse, +0.0240 [+0.0089, +0.0373].

- **Population.** Kalshi REC/RSHATT rungs, weeks 2-3: 2,984 outcomes.
  - Excluded: 106 in the one week-3 game without a final score, 25 voids, 271
    rungs not yet listed at entry, and 69 one-sided at entry.
  - **Common set: 2,513 rungs, 455 fits, 31 games.**
  - Entry is kickoff - 180 min. The model is the walk-forward `default` for
    T=2026 (drift refit on 2024→2025), as-of kickoff. There were 0 fit errors.

| | |
|---|---|
| Brier model / Kalshi mid | 0.1660 / 0.1538 |
| **Brier(model) - Brier(Kalshi mid)** | **+0.0121 [+0.0049, +0.0198]**, MDE 0.0106 |
| receptions (n 2,203) | +0.0091 [+0.0011, +0.0172] |
| rush attempts (n 310) | +0.0334 [+0.0003, +0.0686] |
| log loss, model - Kalshi (descriptive) | +0.0349 |
| three-way rows (book within 30 min of entry), n | 468 |
| Brier model / Kalshi / book | 0.2644 / 0.2470 / 0.2465 |
| model - book | +0.0179 [+0.0014, +0.0356] |
| Kalshi - book | +0.0005 [-0.0012, +0.0022] |

**Decision under the pre-registered rule: model WORSE than the Kalshi mid.**
The interval lies entirely above zero, so the thin-rung hypothesis is retired
on two independent samples (week 1, brief 021; weeks 2-3, here). The hypothesis
was that a model unable to beat a sharp book might beat a thin exchange rung.

**Its premise was also false by weeks 2-3.** Across ALL REC/RSHATT rungs
(matched or not), the median Kalshi quoted spread was:

| | kickoff - 24h | kickoff - 3h | kickoff - 10 min |
|---|---:|---:|---:|
| receptions (n 2,132-2,376) | 4c | 2c | 1c |
| rush attempts (n 288-333) | 5c | 2c | 1c |

The brief's "REC at 12c, RSHATT at 14c" were the week-1 figures: brief 019's
Monday snapshot and its logged-history medians. Near kickoff in weeks 2-3,
these rungs were as tight as GAME's 1c and thousands of contracts deep. **This
exchange was not thin where the model priced it.**

## 5. Team lines: not measured — the pre-registered script no longer reads Kalshi

`research/consensus.py --week 3` (brief 020's frozen script, run unchanged)
finds **0 Kalshi SPREAD/TOTAL markets in 0 games** and then crashes on an empty
median.

- **Cause.** Kalshi's spread subject changed shape after week 1, from "Los
  Angeles R wins by over 9.5 points" to "ARI Cardinals wins by over 9.5 points".
  `venues.mapping.team_abbr("ARI Cardinals")` returns None, while
  `team_abbr("Los Angeles R")` returns LA.
- **Same class as the tackle migration.** A text-parsed label changed upstream
  and the join silently emptied. It is not patched here, because a patched
  frozen script is no longer the registered method.
- **Only week 3 is re-runnable at all.** Its raw `odds_bulk` is still on local
  disk. Week 2's has rotated to R2.

## 6. In-game — not built, and not tractable

- **The binding reason is not clock skew. There is no in-game book price for a
  prop.** The Odds API schedule ends at the T-10 snapshot, and polling live is
  forbidden (CLAUDE.md, The Odds API). An in-game prop comparison needs new
  spend.
- The Kalshi side is also the worst it gets in-game: REC is 16c and RSHATT 63c
  at a 1-2 contract touch (brief 022 H3).
- Team lines in-game have both prices, but brief 020 showed the book consensus
  there is "a clock, not a price".

## 7. What this cannot support

- **Two weeks, 20 games at the close, 31 in Step 4.** The Step 2 mean spreads
  sit about 9 SE (receptions) and 3 SE (rush attempts) from zero. But the
  effective sample is games, and one season could move them.
- **No statement about maker capture.** Resting orders are fee-free on these
  series (M=0), but whether they fill is brief 018/019's question and was not
  re-asked.
- **The Step 2 fee test assumes the book is fair.** The book's own over bias is
  why that assumption flatters the YES side (§2).
- **The data goes stale.** Live quotes prune at 14 days on `ingest_ts`, so week
  2's Kalshi and Odds API rows start leaving the store from 2026-09-29. A re-run
  after about 10-05 sees a smaller population. The scratch cache from this run
  preserves it locally.
