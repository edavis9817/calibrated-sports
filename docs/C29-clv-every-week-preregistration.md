# C29 — closing line value on every published lean: pre-registration

Committed **before** `research/clv_record.py` exists and before any CLV value is
computed. The script is written after this file and must implement what is below.
Nothing here is edited after the first run; a correction is appended at the
bottom, dated, with the reason.

Unit c-29 (track C), 2026-09-30. Branch `c-29-clv-every-week`, cut from
`origin/main` (`a8c2e13`) as the unit names. a-57 (`record/*` builders) and a-58
(`score.py`'s entry refusal) are **not on `main`**; nothing is merged from them.
Their rules are re-stated here and cited by branch and commit.

## What was done before this file (no CLV computed)

A coverage census over the Board ledger and `market_log.db` (`mode=ro`),
scratch scripts outside the repo, printing counts only:

- **The multi-week population is the Board ledger, not `predictions`.**
  `predictions` holds 2026 week 1 only (four model versions, all written
  2026-09-10/11). The published leans - the population the record page carries
  - live in `board/nfl/ledger.parquet`: 211 `published`, 194 `graded`, 6 `void`
  events; week 3 has 200 leans, week 4 has 11.
- **The leans are sportsbook-priced.** Each lean's `price` is the median of the
  lean-side American prices across the benchmark books (DraftKings, FanDuel,
  BetMGM) at the Board read, and its per-book prices are in the read file the
  ledger row names (`board/nfl/{season}/wk{week}/read-{iso}.json`). The close
  therefore comes from the same books: Odds API forward capture in `quotes`
  (`venue = 'oddsapi:<book>'`, `source = 'live'`). Kalshi is a different venue
  and a different claim on price and is **not** used here.
- Close coverage, week 3 (200 leans): a close quote on the lean's side at the
  lean's own line exists for **182**; **18** have none (13 of them because the
  book moved to another line, 5 pulled). Close books per lean: 3 on 94, 2 on
  53, 1 on 35. Same-book overlap entry-vs-close: 3 on 57, 2 on 70, 1 on 49,
  **0 on 6**.
- The Odds API NFL schedule captures each event at about T-12h, T-6h, T-3h,
  T-1.5h, T-45m, T-20m and sometimes T-5m. The last capture before kickoff lands
  p10 1.6 / p50 20.1 / p90 21.0 / max 21.7 **minutes** before kickoff: for most
  1pm games the latest pre-kickoff capture is the T-20m one.
- **Entry capture age is bimodal.** Measured as `read_at` minus the latest
  benchmark-book capture of that event at or before the read (NOT the per-book
  `read_at` in the read file, which is the book's own `last_update` - the time
  the price last CHANGED, not when it was seen): p10 1.03h, p50 2.32h, p90
  46.9h, max 47.2h over 616 (lean, book) pairs. At the 2026-09-26T19:19Z read,
  120 book-quotes on later games were 19-47 hours old at publication.
- Week 4's 11 leans are on games that have not kicked off; the last Odds API
  capture in the store is 2026-09-29T23:35Z.
- 8 of 211 ledgered prices lie in (-100, 100) - a median of American odds
  across an even-money straddle, not a price (a-57, `adf3773`).

## Population

Every `published` ledger event whose publication predates its kickoff under
a-57's rule (`core.record.pre_kickoff`, a-57 branch): both `read_at` and
`event_at` strictly before `kickoff_ts`. Leans failing it are excluded and
counted by id. Nothing else is dropped silently: every lean lands in exactly one
of `scored` or a named exclusion, and the counts are asserted to partition the
population.

## Definitions

Prices are probabilities. For book `b`, the **touch** probability of a side is
the vig-inclusive implied probability of that side's American price
(`core.board.american_to_prob`); the **fair** probability is its multiplicative
de-vig against the other side at the same book and line (`core.board.devig_mult`).

- **Entry.** The lean-side price of each benchmark book at the lean's line, as
  the read file row for the lean's `row_id` at the lean's `read_at` published
  it. The capture instant of that price is the book's latest Odds API prop
  capture of the event at or before `read_at`.
- **Close.** Each benchmark book's quote at the lean's line at that book's
  **latest Odds API capture of the event strictly before kickoff**, with the
  assertion `capture_ts < kickoff_ts` - the same rule `research/clv.py` applies
  on Kalshi, which exists because one field means kickoff on one venue and game
  end on another. A book whose latest pre-kickoff capture no longer lists the
  claim contributes nothing (`jobs.board_read.ladder_at`'s rule): it has pulled
  the player.
- **Same book, same line, same side, both ends.** CLV is computed per book
  over the books quoting the lean side at the lean's line at BOTH ends, then
  averaged within the lean with equal book weight. A median across a changing
  book set would move with composition, not price.

    CLV_touch(b) = touch_close(b, side) - touch_entry(b, side)
    CLV_mid(b)   = fair_close(b, side)  - fair_entry(b, side)
    CLV_held(b)  = fair_close(b, side)  - touch_entry(b, side)

  `CLV_touch` is the **headline**: the price paid at entry against the price the
  same book asked for the same side at the close, vig included at both ends -
  what the industry calls beating the closing line. `CLV_mid` counts value no
  bet could have taken (no book offers the de-vigged price) and is reported
  beneath it, never above. `CLV_held` is the expected value per unit of
  probability of a bet placed at entry and held, if the close is fair; it
  carries the hold and is expected to be negative for an unskilled bettor by
  about half the hold.

  All three are signed, in percentage points, positive = the lean got a better
  price than the close. The mid and held arms need a two-way quote at both ends
  from that book; the touch arm needs only the lean side.

## The entry rule (the a-58 analog), and the exclusions

a-58 (`d43ad1d`, branch `a-58-calibration-population`) made `score.py` refuse a
non-live or stale entry quote rather than substitute a candle. The book analog:

- `ENTRY_SOURCE = 'live'` - forward capture only; a backfill row is not a price
  at the read.
- `ENTRY_MAX_AGE = 6 h` - a book-quote whose capture is more than 6 hours before
  `read_at` is **refused**, not re-priced. Six hours is the widest spacing the
  forward schedule itself uses inside the last half-day (T-12h to T-6h), so any
  quote within it was the latest scheduled observation; a quote older than that
  was published off a capture the schedule had already superseded or never
  refreshed. The refusal is per book; a lean with no admissible entry book is
  excluded.

Exclusion reasons, each published as a count and by lean id:

    not published before kickoff (a-57 rule)
    pending: game not yet kicked off
    void (the bet is refunded; CLV has no money meaning)
    no read-file row for the lean
    entry refused: every entry book stale (> 6 h) or non-live
    no close at the lean's line (line moved or player pulled)
    no book on both ends (entry and close books disjoint)

A void lean is excluded from the headline because the book refunds it; it is
counted, not dropped. The **unrestricted** touch CLV (stale entries admitted)
is reported beside the headline as a sensitivity row, so the effect of the
entry rule is visible rather than absorbed.

## Statistics

- Unit of observation: the lean. Mean of per-lean CLV.
- **Interval: percentile bootstrap resampling GAMES with replacement**, 10,000
  draws, seed 20260930. A game is a block because a player's leans and every
  lean on one game share the game's news and scoring environment. A per-lean
  interval is never computed.
- **An interval on fewer than 5 games is not read**, whatever it excludes
  (brief 020's rule); the stratum prints its point estimate and `n_games`.
- **Power.** SE is the bootstrap standard deviation of the mean. MDE at 80%
  power, two-sided alpha 0.05, is `2.80 x SE`. The pre-data expectation, from
  S00's week-1 Kalshi ask->ask interval (+0.32pp [-0.01, +0.68], 14 games,
  SE ~0.18pp) is an MDE of order **0.5pp**; the book props here have wider
  prices and fewer games per lean, so 0.5-1.5pp is expected. The measured MDE
  is what is published.
- **Verdict wording, computed:** interval excludes zero above -> "the leans beat
  the close"; excludes zero below -> "the close beat the leans"; includes zero
  -> "no measurable CLV; an effect smaller than MDE could not have been seen".
  With fewer than 5 games -> no verdict.
- **Mechanical term.** Decompose `mean(sgn x drift)` into `cov` and
  `mean(sgn) x mean(drift)` on the fair over probability (sgn = +1 over, -1
  under; drift = fair_close_over - fair_entry_over). The leans are 74% unders
  on week 3 (148 of 200), so market-wide drift toward the over or under would
  masquerade as CLV. Both terms are published.

## Strata (all on the touch arm, then the mid arm)

week; market (receptions / rush_attempts); side (over / under); lead time from
read to kickoff (< 6h, 6-24h, 24-48h, >= 48h); entry depth, which for a book
is the number of benchmark books quoting the lean side at entry (1 / 2 / 3);
books on both ends (1 / 2 / 3); entry capture age (<= 1h, 1-6h, and the
refused > 6h as the sensitivity stratum); close lag (<= 5 min, > 5 min); band
(4-6, 6-8, 8+). **Test count** is printed: every stratum cell is an interval;
none is a finding unless it clears 5 games and the multiple-comparison
disclaimer is carried.

## How CLV can be gamed - stated before the number exists

1. **Enter earlier.** Prices drift more over longer horizons, so an early entry
   has more room for CLV in either direction and carries more risk; a
   positive CLV concentrated at long lead is a lead-time effect until the
   short-lead stratum agrees. Published as the lead-time stratum.
2. **Enter on stale rungs.** A quote captured hours before the read can differ
   from what the book was offering at the read; CLV against it is value no
   order could have taken. Refused above 6h and shown as its own sensitivity
   row.
3. **Mid instead of touch.** Counts the vig as value. The touch leads.
4. **Drop the calls with no close.** The exclusions are counted and listed.
5. **Pick the side after the drift.** Not possible here: the side is the
   ledger's, fixed at publication.

A positive CLV is still not a claim the leans win; CLV measures price, not
outcome. On markets this project has shown to be efficient, **a CLV near zero
is the expected result and is not a failure**.

## Output

- `research/results/clv_record.json` - the full result, committed.
- `record/nfl/clv.json` in a-57's envelope (`schema_version`, `generated_at`,
  `kind: "record.clv"`, `sport`) with `tier: "published"` and its source
  declared: entry from the Board tree (ledger + the read file it names), close
  from `market_log.db` Odds API live quotes. One tier. No figure from research
  or backtest. Written only to a scratch `--dest`; **nothing is published** and
  the contract carries no `record.clv` kind yet (that is track A's).

`research/clv.py` (S00, Kalshi `predictions`) gains a per-week census: for every
week in `predictions`, how many predictions have a live Kalshi entry quote
within a-58's age limit and a live close strictly before kickoff. Its S00
definitions are not changed.
