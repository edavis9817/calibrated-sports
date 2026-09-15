# Brief 022 — the sweep: pre-registration

Committed before any Part 2 or Part 3 number is computed. Nothing below may be
changed after a result is seen; additions go in `022-candidates.md`, which has
its own commit, and which must exist in `HEAD` before any holdout is opened.

## Populations and fences

- **Search set: NFL week 1.** Kalshi events whose ticker date is `26SEP09`,
  `26SEP10`, `26SEP13` or `26SEP14`, at any timestamp (their post-game and
  settlement quotes are week 1). Series without a dated event (season wins,
  weekly wins, conference/division futures) are fenced by
  `ts < 2026-09-15 02:00 ET`, which is after week-1 MNF and before any week-2
  game. `research/sweep/common.py` is the only place these fences live.
- **Holdout A: NFL week 2** (`26SEP17`, `26SEP20`, `26SEP21`). **It does not
  exist yet.** Its first game is Thu 2026-09-17 and it settles after Mon 09-21.
  It is not read by anything in this brief. `research/sweep/replicate_week2.py`
  is committed with the candidates and refuses to run until week-2 MNF has a
  final score in `nfl_games`.
- **Holdout B: college football** (`cfb_probe.db`, raw `cfb_kalshi`, 09-10 to
  09-12). Used two ways, declared now:
  1. **Replication** of pre-registered microstructure hypotheses (H1, H2, H3)
     and of any Part 3 candidate whose mechanism is about market structure
     rather than football. Opened only after `022-candidates.md` is in `HEAD`;
     `common.require_committed()` enforces it.
  2. **A population in its own right** for H1-H3 and the constraint checks
     (Part 3, last axis). Those runs are SEARCH tests, counted in the BH family.
     A candidate born on CFB can replicate only on week 2.
  43 of 114 CFB raw shards are unreadable (brief C01). Every CFB result states
  the readable fraction.

## Statistics

- **Unit of independence: the game.** Every interval is a block bootstrap over
  games (2,000 draws, fixed seed). CFB blocks are CFB games.
- **p-values** are two-sided normal on `z = estimate / SE`, SE = sd of the game
  block bootstrap. A percentile-bootstrap p-value floors at ~1/2000 and could
  never survive BH over hundreds of tests, which would make the null automatic
  instead of earned.
- **Every interval is registered** (`common.Registry`), with role `search`,
  `replication` or `descriptive`. `descriptive` is reserved for quantities with
  no null (a median spread is positive by construction); these are reported
  but are NOT tests and are not in BH. Contrasts between them are tests.
- **Benjamini-Hochberg at q = 0.10 over all `search` tests from Parts 2 and 3.**
  Replication tests are reported beside it, not inside it. The expected number
  of false positives at nominal alpha = 0.05 is stated as `0.05 x m`.
- An interval resampled over **fewer than 5 games is not read** (brief 020).
- Wilson intervals on every rate; never the normal approximation.

## Part 2 — mechanism-first hypotheses

### H1 — determination lag
*Mechanism (from the brief):* nobody watches a decided market, and capital tied
up until settlement costs something. Other side: resting liquidity nobody
cancelled, and holders who will not pay the fee to exit.

- **Determination time D.**
  - Counting props (`KXNFLREC` receptions, `KXNFLRSHATT` rush attempts), rung
    "k+": YES is determined at the wall-clock `time_of_day` of the play on
    which the player's cumulative count reaches k (nflverse play-by-play, week
    1). NO is determined at the last play of the game.
  - `KXNFLTOTAL` over L: YES at the play where combined score first exceeds L;
    NO at the last play.
  - `KXNFLSPREAD`, `KXNFLGAME`: at the last play.
  - A player-stat whose play-by-play count disagrees with `nfl_player_week` is
    excluded and counted.
- **Guard G** for replay review and feed delay: every measure is reported at
  D+0, D+60s, D+120s, D+300s. **G = 120s is primary.**
- **Measures from D+G until the market stops quoting:**
  - `|mid - truth|`;
  - executable net per contract = `1 - ask(truth side) - fee(ask, C)/C` at
    C = 10 and 100, with the touch size required to cover C;
  - persistence = time until that net is <= 0 or quoting stops (lower bound, at
    poll resolution);
  - the settlement delay from D to Kalshi's `result`.
- **Risk.** Kalshi's settled `result` / `expiration_value` against the nflverse
  box score: count the disagreements. If settlement data is not on disk it is
  fetched from Kalshi's free public endpoint, archived raw first, and flagged.
- **Tests (search):**
  - mean executable net at G=120s, C=10, by series x determination kind (YES
    mid-game / at game end);
  - the same at C=100;
  - the share of determined markets with net > 0.
- **CFB:** post-game determination only (no play clock on disk). D = the time
  the game's Kalshi moneyline first quotes >= 0.97 on one side with CFBD
  confirming the final. Same measures. This is H1's replication.

### H2 — order book imbalance
*Mechanism:* resting size is information about who wants to trade; a thin side
is consumed first, so the next move is toward it. Other side: the maker on the
thin side, who is slow to re-quote.

- `I = (bid_size - ask_size) / (bid_size + ask_size)` at the touch, YES book.
- **NFL:** `market_depth` touch sizes (yes bid size = `buy_no` touch size, yes
  ask size = `buy_yes` touch size), at depth-capture instants.
- **Future mid change** at t+10s, t+60s, t+300s from `quotes` as-of.
- **Tests (search):** OLS slope of the mid change on I, per series (REC,
  RSHATT, SPREAD, TOTAL, GAME) x tier (pre-kickoff, in-game) x horizon = 30.
- **Economic test (search):** mean signed move in the top decile of |I|,
  compared with the half-spread plus the taker fee at that price, per series x
  tier x horizon.
- **CFB replication:** raw `/markets` payloads carry `yes_bid_size_fp` /
  `yes_ask_size_fp` per poll. The same slopes apply on the CFB spread, total,
  team total and moneyline series. The 10s horizon is not measurable at CFB poll
  cadence (median game-tier sweep 40.9s) and is reported as such.

### H3 — spread and depth lifecycle
*Mechanism:* market makers widen when they are absent (overnight) and when
information risk is high (just before kickoff, in-game). Not an edge; an
execution map.

- **Descriptive:** median quoted spread and touch size by series x
  time-to-kickoff bucket (>72h, 24-72h, 6-24h, 1-6h, 0-1h, in-game), x hour of
  day ET, x day of week.
- **Tests (search), per series, as contrasts in mean spread (game bootstrap):**
  - each time-to-kickoff bucket against 1-6h;
  - 00-08 ET against 09-16 ET, and 17-23 ET against 09-16 ET;
  - Sunday against weekdays.
- **Ranking (descriptive):** median spread / realised mid volatility (sd of
  60-minute mid changes) per series.
- **CFB:** the same contrasts on CFB series, counted as search (population).

## Part 3 — the open scan (week 1 only; every test counted)

1. **Cross-market constraints**, only where both legs are on disk.
   - `KXNFLGAME` against the lowest `KXNFLSPREAD` rung.
   - P(A wins by over x) + P(B wins by over x) <= 1 at the lowest common rung.
   - `KXNFLWINSWEEK` against `KXNFLGAME` if they are the same claim.
   - Signed deviation at the mid, the count exceeding the summed bid-ask bands,
     and the executable arbitrage after fees.
   - Receiving-yard ladders against team passing totals, player ladders against
     the game total, and `KXMVE` combos against their legs: checked for
     presence on disk. **The logger does not track `KXNFLRECYDS`, any team
     passing total or `KXMVE`**, so these are expected to be reported NOT
     TESTABLE, with zero tests counted.
2. **Key-number ladder pricing (the unmatched rungs).**
   - Book-implied margin distribution: the empirical nflverse margin
     distribution (1999-2025) of games whose closing `spread_line` is within
     +-1 of the consensus main line, re-centred so that P(cover main line)
     equals the de-vigged consensus. Totals are analogous.
   - Price every Kalshi rung. Tests: mean signed gap by distance from the main
     line (0-3, 3-7, 7-10, 10+ points) x series, plus an executable arm for
     gaps above 2.5pp.
   - This arm carries interpolation error; the exact-match arm (020) did not.
3. **Price path.** Slope of the mid change over (t, t+h) on the mid change over
   (t-h, t), h in {1 min, 10 min, 1 hr}, per series x tier = 30 tests, plus the
   economics of the top decile.
4. **Time and calendar.** Close-price residual (outcome - Kalshi last pre-kick
   mid) by kickoff slot (Wed/Thu/Mon primetime, Sun 1pm, Sun late, SNF), by
   primetime vs afternoon, by home vs away (the subject team or the player's
   team), and by favourite vs underdog (team markets, closing mid > 0.5), per
   market family (props, team).
5. **Liquidity events.** The first print in each market whose size is >= the
   series' 99th percentile print size. Mid change from t-60s to t+60s, t+10min
   and t+60min, signed by taker direction (continuation > 0), per series x
   horizon = 15 tests.
6. **CFB population.** H1-H3 as above, plus the four C01 constraints re-run as
   tests (they were measured descriptively before).

## Part 4 — the five bars (numeric)

A candidate is a **finding** only if all five hold.

1. **Mechanism before the holdout.** Its mechanism, naming the other side,
   appears in `022-candidates.md` at a commit that precedes the first commit or
   run touching its holdout.
2. **Replicates.** The same sign, with the 95% interval excluding zero, on week 2
   or on CFB (CFB only for microstructure mechanisms).
3. **Survives BH** at q = 0.10 against all search tests from Parts 2 and 3.
4. **Effect exceeds cost at real size.**
   - **Crossing effects:** an executable net per contract > 0 after the actual
     touch or VWAP price and the fee at order size, at >= 10 contracts that the
     recorded depth supports.
   - **Mid-price effects:** a gross move > 3.6pp on props, > 2.5pp on team
     markets.
5. **Persists.** The median persistence of the actionable condition is
   >= 60 seconds at the logger's poll resolution (lower bound). Anything that
   needs to be under a minute is a latency race and fails.
