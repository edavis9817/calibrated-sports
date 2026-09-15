# Calibrated Sports — working agreement

Read before proposing or writing code. This file exists so decisions survive
across sessions instead of being re-litigated or quietly violated. If a decision
is not written here or in `DECISIONS.md`, a future session will not know it.

## What this is

A pipeline that turns public NFL data into **probability distributions over
bettable outcomes**, compares them to **logged market prices**, and keeps an
**honest scoreboard** of whether it beats the close. The site, the content and
the betting are consumers of those three things.

The brand name is a promise. The calibration page ships early and stays honest,
including when it looks bad.

## Invariants — a change that violates one of these is the wrong change

1. **Venue specifics live only in `venues/`.** Core speaks normalized rows.
2. **Raw first.** Archive verbatim before parsing. Every derivation must be
   re-runnable from the archive. Never transform on write.
3. **Models emit distributions, never scalars.** Interface: `sample(n)`,
   `cdf(x)`, `prob_over(line)`, `mean()`. A model that cannot answer
   `prob_over` is not finished.
4. **One simulator, many queries.** Joint samples per game; every prop is a
   query against that array. Never one model per prop — correlation must be
   structural, not bolted on.
5. **Every feature is as-of**, keyed `(entity_id, as_of_ts)`, using no data
   timestamped after `as_of_ts`. Violating this silently invalidates every
   backtest.
6. **Facts and beliefs in separate stores.** Facts may be corrected;
   predictions are immutable and timestamped.
7. **`sport` discriminator on every entity** from row one.
8. **Every time-based policy keys on INGESTION time, never on event time.**
   `quotes.ts` is when a price existed; `quotes.ingest_ts` is when the row was
   written. Backfilled data is old by definition — a 2023 closing line lands
   with a `ts` two years in the past the second it is parsed — so a retention
   window on `ts` deletes purchased history on arrival. It did: `prune_quotes`
   destroyed brief 009's entire 806-credit Odds API pilot, 76 days of Kalshi
   candles and 17 days of Polymarket history before anyone looked. Two rules,
   both load-bearing and neither sufficient alone:
   (a) only sources in `QUOTES_PRUNE_SOURCES` are prunable at all — today just
   `live`; and (b) their age is measured on `ingest_ts`. `ingest_ts` is stamped
   by `store.write_quotes` from the wall clock and is never accepted from a
   caller, because a row that can name its own ingestion time can name one in
   the past. Guarded by `tests/test_backfill_full.py`.

## Polling tiers

Tiers key on **KICKOFF**, never on a venue's `close_ts`. Kalshi closes a player
prop at GAME END and Polymarket closes the same claim at KICKOFF, so one field
means two instants and the identical outcome runs at two cadences.
`store.kickoff_map()` is the join; `close_ts` is the fallback for unmapped
markets only.

    hot     15s   0 < secs_to_kick < HOT_WINDOW_MIN (240m)
    live    10s   -LIVE_WINDOW_MIN (240m) < secs_to_kick <= 0
    game    60s   secs_to_kick > 0 and within COLD_WINDOW_HOURS (24h)
    cold   600s   everything else, INCLUDING all post-live markets
    futures 300s  market_type == future

`game` is **future-side only**. Post-game prices converge to 0/1 and stay, so
600s is sufficient resolution for a converged market — and it keeps finished
games out of `DEPTH_TIERS`, where they would otherwise compete for the depth
cycle on the busiest night of the week.

Discovery runs **off the polling path** as a task. Kalshi's catalogue pass
averages 15.7s and peaks at 37.6s; awaited inline it stalled the venue. One
pass in flight at a time, and a failure keeps the previous catalogue rather
than emptying it. `LOOP_TICK` bounds the resolution of every cadence above.

The Odds API is **snapshot-scheduled and is not in the tiers** — see
`venues/oddsapi.py`. `jobs/tier_report.py --accept` checks all of this against
the real catalogue and schedule.

## The raw archive manifest

Every raw file on disk must have a `raw_shards` row. `archive_raw()` registers
on first write to a shard (once per shard per process — it is on the hot path
of every venue tick), and the logger audits the tree at startup, registering
and reporting anything it finds unregistered.

Shards are hashed when their **hour closes** (`store.seal_shards()`), not when
they rotate. Rotation's own hash is taken seven days later, so verifying the
upload against it proves the **transfer** and says nothing about the
**content**: a shard truncated by a bad append on day two would be faithfully
preserved to R2 and the only other copy deleted. Rotation refuses, loudly and
without deleting, when the sealed and current hashes disagree.

## The join key

`outcome_id` identifies a semantic claim independent of venue:
`(nfl, 2026, wk1, player=00-0036355, receptions, 5.5, over)`. A `market` is one
venue's instrument pointing at it. All cross-venue comparison, and all CLV
measured against a different book than the one you bet at, depends on this.

Player identity: nflverse `gsis_id`, crosswalked **at ingest**, never in
analysis code.

## Layers

`FACTS → FEATURES → BELIEFS → DECISIONS`. Each layer reads only the previous
layer's output. No layer calls another layer's internals.

## Jobs

Small, idempotent, incremental, independently runnable. `--week 3` moves
nothing but week 3. Watermark per source, `source_health` per job, and the
publish job **refuses to run on stale inputs**.

Retention runs **in-process**, not via cron. A policy that depends on someone
remembering a cron entry is the policy that already failed once. And see
invariant 8: what it deletes is bounded by source, and how old is measured on
ingestion time.

## Automation rule

> The system must survive three weeks of total neglect.

Two prior projects died otherwise: one needed manual weekly updates, one filled
its disk while unattended. Verified retention, disk alerting, ingestion that
degrades rather than dies, dead-man switch. If a human must touch it weekly, it
does not ship.

Current state: raw rotates to R2 at 7 days, quotes pruned at 14, archiving
suspends below 5 GB free, dead-man fires at 20 minutes globally and per venue.
Measured ~1.71 GB/day on a quiet midweek night — **the Sunday slate figure is
not yet measured and the retention arithmetic depends on it.**

## Venue facts — hard-won, do not rediscover

**Kalshi**
- `/series` returns ~13,900 series. Football tickers look like `KXNFLGAME`,
  `KXNFLWINS-MIA`, `KXNFL2Q`, `KXNFLAFCWEST`, `KXLEADERNFLRUSHTDS`,
  `KXNFLPREPACKSGPSPREAD`, `KXNFLFFH2H`, `category` = "Sports".
- **Titles say "Pro football", not "NFL"** — trademark avoidance. Match both.
- Discovery must go `/series` → filter → fetch by `series_ticker`. Walking
  `/markets` never reaches sports within any sane page cap.
- **Prices are DOLLARS, and a contract settles at $1, so the price IS the
  probability.** Do not divide by 100. Fields are `yes_bid_dollars` and
  `orderbook_fp.yes_dollars` / `no_dollars`, as strings.
- **`/markets/orderbooks?tickers=a,b,c` returns HTTP 200 with one empty book.**
  Use repeated params — `tickers=a&tickers=b`. Comma-joining fails silently,
  which reads as "no liquidity" rather than as an error.
- Props are natively threshold-format ("4+ receptions"), spreads around 1¢.
  `KXNFLREC` covers every threshold under one series; strikes are half-integer,
  so no push handling is needed on that path.
- Fee `0.07·C·P·(1−P)` taker, `0.0175` maker — cheapest at the tails.
- **Rate limits are PER ENDPOINT.** `/candlesticks` 429s after ~5 rapid calls;
  the batched `/markets/orderbooks` sustained 13.6 calls/s over 18 consecutive
  calls with zero 429s and returns full L2. Do not generalise one endpoint's
  limit to the venue.
- **Top of book is not a tradeable price.** Ladders are two ASCENDING BID
  ladders, no asks: the yes-ask is `1 - no_bid`. Observed 4c touch for ONE
  contract with a wall behind it; 1000-contract VWAP 0.333. 228 markets on one
  slate show >20% slippage from touch to 1000 contracts. Depth is
  unrecoverable after the fact - candlesticks carry price and volume, no book.
- **History is free and needs no auth**: `/series/{s}/markets/{t}/candlesticks`.
  `period_interval` is in MINUTES (1/60/1440). 90-day spans return, 365 is a
  400, so chunk. **429 after ~5 rapid requests**, no rate headers, no
  Retry-After. Candles carry `volume_fp` and `open_interest_fp`; `price` is
  `{}` when nothing traded, and bid/ask still exist there.
- **Live `volume` is CUMULATIVE, a candle's `volume_fp` is PER-PERIOD.**
  Summing both together produced a 6.7-billion-contract week.
- **Kalshi ladders are dense where they exist and absent where they do not.**
  `KXNFLREC` (receptions) and `KXNFLRSHATT` (rush attempts) carry a median of
  **7 thresholds per player-game**, max 12 - a genuine survival function, free.
  There is **no rushing-yards series and no anytime-TD series**; tickers
  matching "TD" are players named DMetcalf and DWashington.
- **Kalshi prices fantasy points directly**: `KXNFLFFPTS` quotes
  P(fantasy points > X) per player per game (one threshold each, 185 open on
  the 2026 wk1 slate), alongside `KXNFLFFWEEKTOP` and `KXNFLFFPLAYERHIGH`.
  `KXNFLFFH2H` appears in the series list but had **zero open markets**.
  FFPTS is the better consistency check anyway: same exchange, same game, and
  it needs no cross-player correlation term.
- **A Kalshi spread is not a bookmaker margin.** It is an exchange: the yes-book
  mid IS the probability. Applying Shin or multiplicative de-vig to it invents a
  correction for a margin that does not exist.

**Substring traps in NFL filtering.** "i-NFL-ation" contains NFL, and so does
`KXNCAAFCO-NFL-EAVE` across a word boundary. The filter is
`(?<!I)NFL(?!X)` plus an NCAA/college exclusion plus a category guard.

**College football (measured by `cfb_probe.py`, 2026-09-10 to 09-12, 56h)**
- **There are no single-game player props.** 47,553 markets across 97 NCAAF
  series on Kalshi and not one of them prices a player's receptions, carries,
  targets or yards for a game. Everything player-named is a SEASON leader
  future closing 2027-01-07 ("Nick Rinaldi records the most sacks in the SEC").
  What looks like a prop is team-level: `KXNCAAFTEAMRECYDS` is "Texas: 325+
  receiving yards", `KXNCAAFTEAMTD` is "Utah: 4+ rushing touchdowns".
- **So CFB is not addressable by this model.** The validated edge is single-game
  player usage — targets, rush attempts, receptions — and CFB lists none of it.
  A CFB expansion would be a different model (team totals, spreads, quarters),
  not this one pointed at a new sport. Do not re-probe for props.
- The depth that does exist is game-shaped: `KXNCAAFSPREAD` 2,822 markets,
  `KXNCAAFTOTAL` 2,232, `KXNCAAFTEAMTOTAL` 1,582, plus full quarter and
  first-half ladders. 20,801 of 47,553 markets close within 3 days of first
  being seen. Polymarket carries the same shapes, 20,587 markets.
- Volume is ~10x NFL for a sport we cannot trade: 50.6M quote rows and 23.9 GB
  in 56 hours, against 6.5 GB for the NFL logger's entire life. A `game`-tier
  poll over 9,080 markets takes a median 40.9s and a worst case 111.9s, so a
  60s cadence does not fit inside its own period and the loop runs saturated.
- **BOTH teams are quoted on the spread, on ALTERNATING rungs** - 79 of 126
  games. One series, one event, two interleaved half-curves. Pooling them into
  a single ladder is not a small error: it fits at rmse up to 0.51 and returns
  a median favourite margin of 32 points. Split by team they are complements,
  `S_A(-L) = 1 - S_B(L)` (half-integer lines, so no tie at -L), which turns a
  one-sided ladder from +1.5 up into a two-sided margin curve. Where only one
  team is quoted, ANY fit extrapolates across the whole left half and invents
  tens of points on a blowout - stratify on it, never pool it.
- **Kalshi derives its CFB period and team markets mechanically. There is no
  edge in the relations between them.** Measured 2026-09-13 on the 09-12 slate
  at a 10:00 ET pre-kickoff snapshot (`research/cfb_coherence.py`, signed
  deviations, unselected):

      teamRef - teamOpp = spread  (2-sided)  n=38  med -0.01  sd 0.30   0/38
      teamA + teamB = game total             n=59  med +0.17  sd 0.67   3/59
      Q1 + Q2 = 1H                           n=27  med -0.16  sd 0.59   0/27
      Q1..Q4 = game total                    n=23  med +0.79  sd 0.77   0/23

  The last column counts games whose deviation exceeds the summed bid-ask
  bands of its own legs. Quarter bands run ~10 points wide, so no quarter
  deviation of any plausible size is tradeable regardless of the estimate.
  Every one of the three that DID clear its band is a blowout: split on the
  favourite's margin, <=14 pts is 0/30 at sd 0.51 and 14-28 pts is 0/13 at sd
  0.31, while >28 pts is 3/16 at sd 1.02 with the median still at -0.07. The
  dispersion doubles and the centre does not move, which is a ladder pinned
  near zero on the underdog rather than a directional mispricing.
- **Quarter means are estimator-sensitive; game and team totals are not.** A
  probit-normal fit and a near model-free integration agree to 0.1 points on a
  game total and to 0.7 on the team-total relation, but differ by 1-3 points on
  every quarter relation, because a quarter's scoring is lumpy (mass at 0, 3,
  7, 10, 14) and its priced tail is fatter than normal. Any future CFB quarter
  claim must carry both estimators or it is measuring the family, not the
  market.
- **The college market is calibrated at about the same size as the NFL's, and
  one Saturday cannot say more than that.** Measured 2026-09-13 on 119 games
  with a close at their own kickoff (`research/cfb_calibration.py`, results
  from `jobs/ingest_cfbd.py`). Game total ECE 0.0315 (over side +2.71pp), game
  spread ECE 0.0223 (+0.75pp), against the NFL props' ECE 0.0043 on three
  seasons. **Every bucket's priced value sits inside its Wilson interval** -
  nothing here is distinguishable from correct pricing.
  - **The effective sample is GAMES, not rungs.** A 19-rung total ladder
    settles off ONE final score, so 119 games make ~2,000 rung-observations
    carrying nowhere near that much information. Wilson intervals are computed
    on distinct games throughout; on rungs they would be ~sqrt(19) too narrow.
    Collapsed to one observation per game - did the final clear the market's
    own implied mean? - it is 55.3% over, Wilson [0.4402, 0.6168], mean miss
    +1.55 points against a 15.01-point sd. That is the number to quote.
  - **No de-vig, on purpose.** An exchange mid IS the probability; there is no
    margin to remove and Shin would invent one. Partitions are normalised to
    sum to 1, which is a different operation and ~0.005 in size.
- **CFB Q1 is genuinely quiet and the market only half-knows it.** Realized
  mean Q1 is 10.06 points against a 13.51-point average regulation quarter -
  74% - while the market prices E[Q1]/E[game] at 0.2306 against a flat 0.2500
  and a realized 0.1834. The market has ~a third of the true first-quarter
  discount. The sign pattern across quarters is the mechanism's: it overprices
  the first quarter of each half (Q1 -2.19, Q3 -1.68) and underprices the
  second (Q2 +1.82, Q4 +1.09), which is scoring bunching before each break.
  **NOT ONE QUARTER CLEARS 1.5 SE** at n<=26, and four alternating signs is a
  1-in-8 coincidence, so this is a thing to re-measure, not to believe.
- **College really is lopsided, which is why one week is thin everywhere.**
  36% of games close with a favourite priced >=0.90 and 22% >=0.95, a range the
  NFL never reaches. Sub-0.15 underdogs went 1 for 45 against a 0.0613 price -
  the longshot direction - but Wilson is [0.0039, 0.1157] and contains it.
- **The CFBD results feed is metered at 1,000 requests per CALENDAR MONTH**,
  free tier, with no per-call weighting: one HTTP request is one unit whatever
  it returns. `/games?year=&week=` returns a whole week WITH line scores, so a
  season is ~15 requests and a per-game loop is ~800. **Never loop per game** -
  `tests/test_cfb_calibration.py` asserts the job can build only the one URL.
  Invariant 2 is a budget rule here: `--from-archive` re-derives every table
  from the gzipped shard at zero requests. A full historical backfill is the
  Starter Pack CSV download, not the API.
- **Team names differ between the feeds and the parenthetical is load-bearing.**
  107 of 120 games matched before an alias table, 120 of 120 after. Fold to
  ASCII first (CFBD writes "San Jose State" with an accent; strip-then-fold
  gives "jos"), treat a hyphen as a separator ("Louisiana-Monroe" is two
  words), and KEEP the bracketed qualifier - dropping it merges Anderson (IN)
  with Anderson (SC), Lincoln (MO) with Lincoln (PA), and Miami (FL) with
  Miami (OH). Match on date +/-1 day: a 23:00 ET Saturday kickoff in Hawaii is
  a Sunday in UTC.
- **VERDICT: NO-GO on college football.** Not because the market is sharp -
  because there is nothing to point a model at. No single-game player props
  exist, the derivatives already cohere to within a third of a point (Part 1),
  and a team-level effort needs a CFB facts layer that does not exist. What
  would change it: a venue listing college player props at NFL ladder density,
  or a Starter Pack backfill making a team model cheap to build. Neither is
  close.
- **The exchange's own coherence is exact at the mid.** Moneyline mid sums
  median 1.0000 (n=230); quarter-winner 3-ways median 0.99-1.01. Ask sums are
  1.02 two-way and 1.14-1.17 three-way, and NOT ONE partition of the 300 could
  be bought whole below 1.00. The bookmaker overround band of 1.00-1.15 does
  not apply to an exchange - a mid sum under 1.00 is the spread seen from the
  inside, not free money.

**Two processes writing one raw shard silently destroy it.** The storage-fix
restart on 2026-09-11 left two `cfb_probe.py` instances running — poll rate went
from 58 game polls/hour to 117, exactly double, from 12:00 EDT onward. Both
appended to the same hourly `.jsonl.gz`. 43 of 114 CFB shards fail `gunzip -t`,
and the first corrupt file is the UTC hour the second process started.
**CORRECTED 2026-09-15 (brief 022): the data is recoverable; the READER was
what failed.** `archive_raw` writes every record as its own gzip member, so a
torn append destroys only the member it tore. A streaming reader (`gunzip`,
`gzip.open`) stops at the first bad member - on `cfb_kalshi/2026-09-11/16`
(128 MB) it reads 6,937 lines and errors. Decoding member by member, each
CRC-checked by zlib, recovers 16,550 lines and 99.98% of the bytes, losing one
torn member. Across the 57 raw CFB shards the H2 sweep recovered 1,091,595
members (99.97% of bytes), dropping 117. The earlier "688 readable lines,
recovery nil" measured `gunzip`, not the archive. **Nothing detected this.**
`audit_shards()` checks that every file on disk has a manifest row; `seal_shards()`
hashes bytes. Neither opens the stream, so an unreadable archive passes both and
invariant 2 ("every derivation must be re-runnable from the archive") is
asserted but not verified. Two gaps to close: a single-instance lock on any
capture process, and a decompress check in the shard audit. The NFL archive was
scanned at the same time — 382 shards, 0 corrupt — because only ever one process
writes it, which is luck holding a guarantee up.

**Polymarket**
- Blind pagination 422s past `offset≈2000`; the API names `/markets/keyset` for
  deeper paging. Use `/events` with `tag_slug=nfl` instead.
- Slugs look like `pro-football-2026-27-passing-yards-leader`.
- **Bids ascend, asks DESCEND** in a CLOB book. Best bid is max, best ask is
  min. The two sides use opposite conventions.
- **`prices-history` carries NO volume and NO open interest** — `{t, p}` only,
  ~31 days. Neither does the live CLOB path. Polymarket cannot be bucketed on
  the same liquidity axis as Kalshi; spread is all it offers, and an empty book
  quotes 0/1 so its mid is a meaningless 0.500.
- **A pagination ceiling RAISES; it does not truncate.** Reversed 2026-09-10.
  A short catalogue is indistinguishable from a quiet day — the loop keeps
  succeeding, the dead-man stays green, and the gap surfaces months later as
  history nobody can re-derive. Discovery refuses at `POLY_MAX_OFFSET` rather
  than discovering the ceiling by getting a 422. If it ever fires the answer is
  `/markets/keyset` or a narrower tag, **never a bigger cap**.
- **Use `POST /clob/prices`, not gamma's inline `bestBid`/`bestAsk`.** Same
  top-of-book values, 235× fewer bytes, and materially fresher — gamma logged
  zero price changes across four minutes where CLOB logged 17–65 per 20s cycle.
  Gamma inline is the fallback only.

**The Odds API**
- Cost = markets × regions × **events** for props, alternates and period
  markets. Featured markets (`h2h`, `spreads`, `totals`) are a cheap bulk call
  covering the whole slate.
- **Books are free** — every bookmaker in a region comes with the call. There
  is no reason to pick one at ingest.
- Historical costs 10× and player props only go back to 2023-05-03.
- **MEASURED 2026-09-10**: historical event odds = 50 credits (5 markets × 1
  region × 10); the historical **events listing costs 1 credit and is not
  free**. Snapshots snap to a 5-minute grid. 3 seasons × 5 prop markets =
  43,163 credits.
- **Retention must never touch a backfill.** Backfilled rows carry the EVENT
  timestamp, so a window on `ts` deletes a 90-day history the moment it lands.
  `prune_quotes` prunes `source='live'` only AND measures age on `ingest_ts`.
  This silently destroyed an 806-credit pilot and 76 days of Kalshi candles
  before it was caught. See invariant 8.
- **A re-parse replaces its OWN derivation, never the whole event.** The
  archive holds two payloads for 2024 week 8 - the pilot bought it with
  `player_receptions_alternate`, the full backfill bought it again without.
  Deleting by event before the second parse threw the alternate ladder away,
  and had been wiping every prior featured snapshot besides the last: 527,352
  rows became 907,421 once it stopped. A full re-derive purges the source once
  and stays idempotent on (venue, market_id, ts).
- **Billing follows markets RETURNED, not requested.** `player_sacks` does not
  exist in 2023, so a 2023 event costs ~38 credits and a 2024-25 one costs 50.
- **17 books appear across 2023-2025**, not the 7 in a 2024 sample; several
  (pointsbetus, barstool, twinspires, wynnbet) have since exited.
- **`pointsbetus` quotes both sides at ~0.999** on ~99 pairs. The overround
  invariant is what catches it: two sides must sum to 1.00-1.15.
- **Pinnacle is ABSENT from 2024 us-region historical props.** Seven books
  quote: betmgm, betonlineag, betrivers, bovada, draftkings, fanduel,
  williamhill_us. The CLV benchmark cannot be Pinnacle for historical work.
- `player_receptions_alternate` is quoted by **fanduel only**; do not assume
  alternate-ladder coverage at scale.
- **`player_anytime_td` is ONE-SIDED.** Every outcome is `{"name":"Yes",
  "description":"<player>"}` with no "No" - 136 of 136 on a probe event. It is
  not a partition either: several players score, and the listed set is not
  exhaustive. So the overround invariant cannot run on it and NO de-vig that
  normalises to 1 applies - not multiplicative, not Shin, not power. Any TD
  component must be ANCHORED to a count fitted elsewhere and labelled as such.
- **`player_rush_yds` is a one-book ladder.** betrivers hangs 11 thresholds;
  betmgm, bovada, draftkings and fanduel hang exactly one line each. The pooled
  "12-line ladder" is one book's ladder plus four point quotes - the same shape
  as `player_receptions_alternate`, which was declined for that reason.
- **Props post 48–72h before kickoff**, so snapshot targets earlier than that
  bill credits for empty responses.
- Never poll live. Snapshot on a schedule keyed to kickoff; the T−5 snapshot is
  the close and is the only one CLV strictly requires.

**nflverse**
- The `player_stats` release is **dead** — last asset update 2025-05-07. Use
  `stats_player`. Building against the dead one gives green health rows and
  data that silently stops at 2024.
- `contracts/historical_contracts.parquet`, not `contracts/contracts.parquet`.
- Participation refreshes **only after the postseason**. It carries `route`,
  `defense_man_zone_type`, `defense_coverage_type`, `was_pressure` and
  `offense_players` — all backtest-tier, never Sunday-tier. A feature reading
  one in live context must raise, not return nulls.

## Settled by evidence — do not re-open without new data

Scripts in `research/`. Verified 2026-09-09 against the maintained
`stats_player` release and replicated on 2025 as an unseen holdout.

- **Usage carries the signal; efficiency is noise.** Opponent defence and game
  script added ~0.000 OOS R² on top of prior usage.
- **Priority markets: targets, rush attempts, receptions.** Stable across both
  data sources, both windows and both pool definitions. Yards below them, TDs
  and QB props near zero. QB attempts is the one unstable market (0.233 → 0.048
  on the holdout, n=438).
- **Distribution families**: negative binomial for counts, zero-inflated gamma
  for yardage. Overdispersion, right skew and non-trivial zero mass all
  confirmed on the holdout.
- **The constants in `core/distributions.py` describe a SCREENED population**
  — the research filtered on prior expanding mean (receptions ≥2.0, targets
  ≥3.0, carries ≥6.0, receiving yards ≥25.0, ≥4 prior games). Unscreened,
  receptions measure 2.3–3.2 mean and 1.8–2.3 var/mean. They are test
  reference values, **not model priors**. Fit per-player.
- **Correlation**: QB↔WR1 ≈ 0.42 (0.442 on the holdout), WR1↔WR2 ≈ 0.02.
  Never size with a diagonal covariance.
- **Defence barely persists** (YoY r ≈ 0.2). Shrink hard toward league mean.
- **A coaching change zeroes team scheme history** (shotgun rate .79 → .12).
  Carry the incoming playcaller's prior, not the team's.
- **Player residuals don't persist** (r ≈ 0.09). No player-specific overrides.
- **Wind is the only situational factor** surviving multiple-comparison
  correction (30 tests, Bonferroni |t| > 3.14). Divisional, primetime, home/away
  for receivers and short rest are tested nulls.
- **Tackles are three columns, not one.** Verified against ESPN box scores
  (`research/tackle_definition.py`, three player-games):

      box-score SOLO      = def_tackles_solo + def_tackles_with_assist
      TACKLES + ASSISTS   = def_tackles_solo + def_tackles_with_assist
                          + def_tackle_assists

  `def_tackles_with_assist` is a tackle this player made that someone else
  assisted on — it scores as SOLO officially. Dropping it undercounts ~6% of
  defensive player-games by up to 3 tackles. Two reference cases are NOT enough
  to pin this: both Roquan Smith and Bobby Okereke had `with_assist` = 0, which
  leaves `solo + assists` looking correct when it is not. Tremaine Edmunds
  (2025 wk3, CHI) is the discriminating case.
- **nflverse `gametime` is US/Eastern.** Parse it with `ZoneInfo`, never as
  UTC - that puts kickoff 4-5 hours early, which is invisible in a schedule
  listing and silently closes any pre-kickoff window. September is EDT,
  January EST, so a fixed offset is wrong for half the postseason.
- **Shrink toward position AND role.** An RB1 and a third-string back share a
  position; shrinking to the bare position drags every starter down. Measured
  role means for carries: 14.25 / 7.06 / 3.62 / 1.90.
- **Push handling**: on integer lines price `P(X>L)/(1−P(X=L))`. For receptions
  at line 4 that is 0.385 versus 0.329 — larger than any edge being hunted.

- **The closing market is well calibrated, and the OVER is overpriced.**
  Measured 2026-09-10 on 183,669 settled player props, 2023–2025
  (`research/calibration.py`). Both sides together: Brier 0.2452, log loss
  0.6832, **ECE 0.0043** — the curve tracks the diagonal across every price
  bucket. The over side alone prices 0.4883 and realizes 0.4743: **−1.40pp,
  z = −5.9**, and it survives restriction to prices in [0.45, 0.55] (−1.26pp,
  z = −4.4, n = 30,440), so it is a pricing fact and not only line placement.
  Line placement is real too and shows separately — median margin −0.50
  against mean +2.74.
  - **It is mostly untradeable.** +0.0126/contract against a Kalshi taker fee
    of 0.0175 and half a 1.0675 overround at 0.0338. Clears a maker fill
    (0.0044) and nothing else. Do not treat it as an edge without a venue that
    quotes these props and a maker fill to show for it.
  - Strongest where books AGREE (dispersion <0.02: −1.78pp) and growing by
    season: 2023 **+0.60pp, not significant**; 2024 −1.67pp; 2025 −2.65pp.
  - By stat inside the band: sacks −7.2pp, rush attempts −2.4pp,
    tackles+assists −2.1pp, receiving yards −0.8pp, receptions −0.8pp.
- **Report every slice on ONE SIDE.** Over and under are exact complements, so
  a table covering both reads 0.5000 / 0.5000 in every slice, always. That
  looks like proof of perfect calibration and is arithmetic.
- **The `outcome_id` key is only as good as the week on it.** A team market's
  key is (season, week, team, side) and nothing else, so one wrong week merges
  eighteen different claims. Two ways it went wrong on 2026-09-10: stamping one
  season/week on every event in a payload, and resolving a January game by
  calendar year to the FOLLOWING season's identical matchup. Both are silent —
  the prices stay plausible. `--trust` in `research/calibration.py` counts
  outcomes whose markets span more than one game; it must stay at zero.

- **A market-implied fantasy distribution is calibrated in aggregate and fails
  in three specific places.** Built from de-vigged ladders, coupled with a
  Gaussian copula, scored on 9,822 settled player-games
  (`research/implied.py`). Quantile coverage: q50 0.482, q75 0.235, q90 0.101,
  q95 0.054 against 0.50 / 0.25 / 0.10 / 0.05 — the aggregate is good and
  **the aggregate is the least useful number in the study**. It fails at:
  - **running backs** — q90 0.140, q95 0.075. An RB's points are rushing
    yards, which arm B derives from a median-1-point attempt ladder times a
    fitted YPC. The weakest input path produces the worst tail.
  - **sparse ladders** — 1–2 points gives q90 0.139; 5+ points gives 0.113.
    Where the market pins only a point or two, the fitted family supplies the
    shape and the tail comes out too thin.
  - **single-book fits** — q90 0.161 against 0.100. A *noisy* book is safe
    (wide dispersion → q90 0.077, mildly underconfident); a *lone* book is
    not. `outcome_close.dispersion` is 0 when one book quoted, so single-book
    fits must be split out or they poison the "tight spread" stratum.
- **Within-player dependence is strong and must be modelled.** Rank
  correlation of receptions to receiving yards is +0.838 WR, +0.826 TE,
  +0.863 RB; carries to rushing yards +0.900 for RB. Independent marginals
  understate the joint upper corner, which is exactly the fantasy ceiling.
  QB rec↔yards is +0.300 — a QB reception is a trick play, not a workload.
- **E[distinct TD scorers] is sublinear in team TDs**: 2 → 1.82, 3 → 2.57,
  6 → 4.31 over 5,790 team-games. A TD anchor that treats a two-TD game as two
  scorers inflates every player's TD probability by ~10%.
- **The market's implied team total is an unbiased predictor of points**:
  actual = −0.24 + 1.019 × implied. nflverse `spread_line` is POSITIVE when the
  home team is favoured — checked by correlation (+0.391 vs −0.150), not
  assumed.

- **The longshot bias is a SACKS artifact and is not tradeable.** Retired as a
  finding 2026-09-12 (`research/longshot.py`). All 194 settled observations
  priced below 0.15 are `sacks`, `over` side, single-book, 1-2 line ladders.
  Every other stat has a de-vigged price FLOOR - receiving yards and receptions
  never below 0.165, rush attempts never below 0.338 - because a book hangs its
  line near the median outcome. There is no general longshot zone to be biased
  in. Above 0.15 the market prices 0.1673 against a realized 0.1671 (n=850) and
  0.1871 against 0.1869 (n=1,867).
  - Even where it lives it does not clear a Wilson interval: 0.125-0.150,
    n=167, priced 0.1388, realized 0.0898, CI [0.0552, 0.1429] - the priced
    value is inside. **Quote Wilson intervals, not normal-approximation SEs, on
    any bucket with n in the hundreds at a low rate.**
  - The 4.9pp residual is smaller than the 3.7pp-per-side raw vig it claims to
    have removed, so Shin under-removing the margin explains it as well as a
    market bias does, and price data alone cannot separate them.
- **Kalshi spreads are the binding constraint at longshot prices**, not fees.
  Mid in 0.05-0.20: median quoted spread 9.0c. Depth is fine (1,000 contracts
  slips 0.53c from touch) and fees are small (taker 0.84pp, maker 0.21pp). A
  sub-5pp edge does not survive crossing a 9c spread.

- **Week 1 CLV is not distinguishable from zero, and is deeply negative where
  money changes hands.** Measured 2026-09-14 on all 935 week-1 predictions,
  unselected (`research/clv.py`). Entry is one instant, Thu 09-10 15:03 ET;
  the close is the last quote STRICTLY BEFORE KICKOFF, which lands a median of
  1.0 minutes early. Block-bootstrapped over the 14 games:

      mid -> mid                    +0.62pp  [+0.13, +1.22]   n=624
      ask -> ask (what a taker pays) +0.32pp  [-0.01, +0.68]   n=624
      executable @1000 contracts   -13.52pp  [-17.06, -10.40] n=493

  The mid interval excludes zero and is the WEAKEST of the three to lean on.
  Between entry and kickoff the yes BID rises ~2.0c while the ask moves
  -0.2c - the book tightens almost entirely from below - so a mid collects
  half of a one-sided tightening whatever the model knew. On the ask leg
  alone the interval touches zero. The 14.14pp gap between mid and executable
  is the book crossed twice.
- **The fingerprint now NORMALISES line endings before hashing**, so it
  identifies a commit rather than a checkout (brief 018). Raw bytes made the
  same commit hash differently under different `autocrlf` settings, and
  verifying a historical version needed a search over 2^n conventions - which
  was the right diagnosis and the wrong fix. `fingerprint_matches` is retained
  ONLY for pre-018 versions, tries the normalised rule first, and must not
  grow. Equivalence chain: `679868a8549a == a307952813e6 == 03571d4710af`.
- **An IDENTITY symbol may change in place; nothing else may.** `_fingerprint`,
  `model_version` and `MODEL_VERSION` compute what the model is CALLED, so
  editing them cannot move a forecast - and normalising a hash means editing
  the hash function. A fee or prediction symbol changing in place still fails
  the equivalence assertion.
- **A fingerprint over raw file bytes identifies a CHECKOUT, not a commit.**
  `models/baseline._fingerprint()` hashes worktree bytes, and under
  `core.autocrlf=true` line endings are not a property of the commit -
  `core/distributions.py` was LF on disk before brief 016 and CRLF after. No
  single convention reproduces both fingerprints, so
  `core/model_equivalence.fingerprint_matches()` tries all of them.
- **Two model versions can be recorded EQUIVALENT, but only with machine-checked
  evidence.** `model_version_equivalence` holds
  `679868a8549a == a307952813e6` (brief 016 moved the fee out of a hashed file
  without changing a predicted quantity). The evidence is a structural AST
  comparison of every top-level construct in the hashed fileset: a construct
  that is not a fee symbol must exist in both and unparse identically, and
  ANY construct changed in place fails - including a fee one. Comparing
  unparsed ASTs ignores comments and formatting, which cannot move a
  prediction, and catches a flipped sign inside an untouched-looking function,
  which a diff of hunk headers would not. The objection to an equivalence
  table is that a human can assert into it; the assertion is the answer.
- **A resolver that cannot find predictions RAISES.** `core/version_resolve.py`
  follows equivalence links transitively and symmetrically, and refuses
  otherwise - naming the versions that DO have rows. This project has shipped
  four bugs whose symptom was a plausible empty or degenerate result (C01's
  gate sweep printing four identical rows, F01's Part 5 simulating one
  component, S01's one-crossing arm reproducing mid-to-mid, 016's series audit
  returning zero rows). A printed warning inside a twelve-minute run is a
  warning nobody reads. Override with `--model-version`, typed.
- **The Kalshi fee is charged on the WHOLE ORDER, and the defaults are taker
  M=1, maker M=0.** `fee = ceil_to_cent(M x rate x C x P x (1-P))`, rate 0.07
  taker and 0.0175 maker. `core/fees.py`, sourced from
  `docs/kalshi-fee-mechanics.md`; 42 vectors from the published table are
  pinned in `tests/test_fees.py`.
  - Calling it per contract bills the ceil-to-cent to EVERY contract: ~1.1x
    over at p=0.5, ~1.6x at p=0.1, ~2.9x at p=0.05. `contracts` now has NO
    DEFAULT, because there is no safe guess.
  - **Do the arithmetic in `Decimal`.** `0.07*100*0.1*0.9` is
    0.6300000000000001 in binary, and a ceil turns an exact $0.63 into $0.64 -
    on precisely the round prices that occur most.
  - **M = 0 means free, not one cent.** No NFL player-prop series appears in
    the schedule's Non-Standard Fees table, so under the published defaults a
    RESTING order on a prop costs nothing. Verified against our own tickers:
    all 935 week-1 predictions sit on `KXNFLREC` (689) and `KXNFLRSHATT` (246),
    both unlisted. Football entries that ARE listed are all 1/1; the only
    football multiplier that differs anywhere is `KXMVE` (maker 2), which we
    do not hold.
  - Rounding is ceil-to-CENT, which is what the published table does; the
    schedule's prose says centicent. Unresolved, and it only matters at
    1-contract tickets. One live fill settles it - change `QUANTUM` and
    nothing else.
- **`models/baseline._fingerprint()` hashes `core/distributions.py`, so a
  change to ANY code in that file bumps MODEL_VERSION** even when nothing the
  model predicts has changed. Moving the fee out of it did exactly that
  (`679868a8549a` -> `a307952813e6`) and silently emptied every research script
  that defaults to the current version. `store.latest_model_version()` now
  resolves and says out loud when it falls back. A derived version is still
  right - it is what stopped two builds both calling themselves
  "baseline-usage-0.2" - but a consumer asking to score last week wants the
  version the predictions were written under.
- **At the entry instant the model had priced EVERY prop that existed**, so a
  control cannot differ in market selection. The intended frame was all 1,487
  `KXNFLREC`/`KXNFLRSHATT` markets in the predictions' 14 events, of which 552
  carried no prediction - but **542 of those 552 had no quote at all at 15:03
  Thursday**; Kalshi listed those rungs later in the week. Market selection
  cannot be the artifact because there was no market selection to make. What
  is testable is SIDE selection.
- **Conditional maker CLV falls with how much of the model is in it** (100
  contracts, same 14-game block bootstrap): model's own side +0.67pp
  [+0.05, +1.39]; both sides pooled +0.37pp [-0.20, +0.99]; the model's side
  FLIPPED +0.07pp [-0.69, +0.93]. Only the first excludes zero. Fill rate is
  flat across all three (18.0 / 20.0 / 18.1%), which is the check that the
  arms differ in side and nothing else.
- **The weekly product has an interior maximum and it is small.** `markets x
  fill rate x contracts x CLV`, measured rather than interpolated: the model
  arm peaks at **250 contracts, ~$113/week**; both-sides at 100 (~$46); flipped
  at 50 (~$14). Fill rate and conditional CLV both fall with size, so the
  product turns over - but every rung's CLV interval overlaps its neighbours,
  so the location is far less certain than the existence.
- **M01's 231 exclusions were NOT a depth-allowlist artifact** - that caveat
  was wrong. **229 of 231 have no BID at all at entry**: one-sided books, ask
  only. Depth exists for them. Their mid and spread are undefined, which is
  the characterisation; on the ask they are the expensive tail and they print
  a fifth as often (median 11 prints vs 51).
  - **The bias is measurable, not just a direction.** A no-bid book still
    supports a passive NO buy at `1 - ask`, so the same simulation runs on it:
    fill rate 6.55% against 18.04% on the simulable, giving 15.22% population
    wide. **The 18% is optimistic by 2.8pp.** Two effects oppose - no queue to
    clear helps, printing a fifth as often hurts more.
- **THE SPREAD WALL.** Entry spread over the 706 two-sided markets: p10 1.0c,
  p25 2.0c, median 5.0c, p75 10.0c, p90 13.0c, max 58.0c. The gate sits at 6c
  and 45% of two-sided markets are wider; counting the 229 one-sided books,
  **59% of the board is at or beyond the gate**. A taker pays half the spread
  each way, so a median book costs 2.50pp to enter before fees and before
  being wrong. This is the ceiling on any crossing strategy and it is a
  property of the venue, not of the model.
- **The maker path is starved, and the fills that happen are worth nothing.**
  Measured 2026-09-14 on 65,467 Kalshi trade prints for the 935 week-1 markets
  (`research/maker.py`, prints from `jobs/ingest_kalshi_trades.py`). A passive
  order resting at the touch from the prediction instant to kickoff, sitting
  BEHIND the size already at that level:

      fill rate                18.0%  [14.3, 21.5]   (704 simulated, 14 games)
      unconditional maker CLV  +3.91pp
      CONDITIONAL on a fill    +0.28pp [-0.34, +1.00]  - contains zero
      never filled             +4.80pp [+4.00, +5.74]
      selection gap            +4.52pp [+3.58, +5.79]  - excludes zero

  **Adverse selection did not show up as a loss on the fills; it showed up as
  the fills being worth nothing while the misses were worth everything.** The
  post-fill mid drift is -0.35pp [-0.79, +0.06]: directionally right, interval
  touches zero. Quote the SELECTION GAP, bootstrapped as one quantity - the
  difference of two separately quoted means carries more confidence than the
  data supports.
  - **Shrinking the ticket does not rescue it.** At 10 contracts the fill rate
    is only 21.6% and conditional CLV +0.47pp; at 1,000 it is 7.8% and
    -0.41pp. The binding constraint is the QUEUE, whose median is 203
    contracts - an order of magnitude larger than any ticket worth placing.
  - 11.6% of these markets had ZERO prints between entry and kickoff, and the
    median market had 11 prints of which **0 were eligible** to fill us: half
    of all volume trades on the other side of the book from where we rest.
- **`taker_side` is the side the TAKER bought, so a passive YES buy is filled
  by `taker_side == "no"` prints.** Reading it backwards fills you off the
  wrong half of the tape and still yields a plausible rate. `/markets/trades`
  is free and unauthenticated; 935 markets at 4 req/s drew zero 429s.
- **A fill rate and a "did this player trade at all" rate are different
  questions and must not share a line.** Per-prediction it is 18%; per
  (player, stat) fit with any rung filled it is 60%. Rungs within a fit are
  not independent, so the per-prediction rate gets a block bootstrap and only
  the fit-level rate gets a Wilson interval.
- **`kalshi_fee(p, 1)` overstates the fee ~2.4x.** The fee is charged on the
  WHOLE ORDER and rounded up to the next cent, so it must be computed at ticket
  size and divided. At p=0.40 a maker fee of 0.42c becomes 1.00c per contract,
  and the maker arm reads +3.23pp instead of +3.81pp. The rounding is one cent
  per order, not per contract.
- **S00's -13.52pp is a ROUND TRIP and overstates what a held ticket pays.**
  A position held to settlement crosses ONCE. Charging entry at the touch and
  valuing against the CLOSING MID gives -3.00pp [-3.80, -2.26], which is
  exactly mid-to-mid minus half the 7.22c entry spread - and that arithmetic is
  the check: a one-crossing arm that does not land there has a book-convention
  bug. The first version of it read the mid at entry and reproduced mid-to-mid
  exactly, which looks like agreement rather than like a bug.
- **Capacity: crossing cost falls with size, edge does not rise to meet it.**
  One crossing, entry VWAP at size against the closing mid, net of the taker
  fee: 100 contracts -5.55pp, 500 -6.74pp, 1,000 -8.60pp, 5,000 -16.67pp.
  Shrinking the ticket 50x buys back 11.13pp of slippage and nothing else - the
  signed edge is a property of the forecast, not of the stake - so the curve
  flattens toward a ceiling of -3.00pp, which is still negative. **`market_depth`
  stores 100/500/1000/5000 and NOT 300**; a VWAP over a discrete book is a step
  function, so 300 is bracketed rather than interpolated.
- **A positive mean CLV needs its mechanical term ruled out.** Decompose
  `mean(side x drift)` into `cov(side, drift)` plus `mean(side) x mean(drift)`:
  the second is just a directional lean multiplied by market-wide drift and can
  produce a "finding" from nothing. Here it is -0.115pp against a +0.731pp
  covariance, so the lean is not the cause - but the convention test above
  still is not passed, and both checks have to hold.
- **The effective sample is 136 (player, stat) fits over 14 games, not 935.**
  A ladder's rungs are one claim priced at ~6.9 thresholds and a slate shares
  a scoring environment. Intervals are block bootstraps over games; a
  normal-approximation interval on 935 would be roughly 3x too narrow.
  Guarded by `tests/test_clv.py`, which duplicates every row 20x inside its own
  game and asserts the interval does NOT narrow.
- **Lead time cannot be studied from a single entry instant.** All 935
  predictions were written in one run, so lead time is fully determined by
  kickoff: bimodal at 5.5h (the Thursday game) and 69.9-77.3h (Sunday), with
  nothing between. "48-72h vs >=72h" is Sunday's early games against its late
  ones - a time-slot contrast, not a decay curve. Answering decay needs
  SEVERAL entry times against the same kickoff.
- **The executable placebo is not zero-sum, and a test demanding that it were
  would be wrong.** Flipping the side must negate mid CLV exactly (asserted at
  0.0). At executable prices both sides pay to cross, so
  `clv_exec(yes) + clv_exec(no) == -(entry_eff_spread + close_eff_spread)` -
  that identity is the check, and it holds to 2.2e-16.
- **Liquidity cannot be stratified on this slate**: 910 of 935 predictions sit
  in `thin`, 24 in `medium`, 1 in `deep`.

- **Kalshi's `/series` endpoint is the fee authority, not the PDF.** Each
  series row carries `fee_type`: `quadratic_with_maker_fees` charges makers,
  `quadratic` does not. 23 of 358 NFL series charge makers. The PDF's
  Non-Standard Fees table omitted five of them - **`KXNFLSPREAD`,
  `KXNFLTOTAL`, `KXNFLFIRSTTD`, `KXNFLANYTD`, `KXNFL2TD`** - and brief 016
  pinned SPREAD and TOTAL as maker-free on its word. Fixed in
  `core/fees.SERIES_M`. `KXNFLREC` and `KXNFLRSHATT` are genuinely
  `quadratic`, so every result from S01 through 018 stands.
- **Ladder monotonicity is violated on the touch, and none of it is money**
  (brief 019 H1, `research/structural.py --monotonicity`). Condition derived,
  not assumed: buy YES on the lower rung at `ask_i`, NO on the higher at
  `1 - bid_j`, payoff never below 1, so the arbitrage exists iff
  `ask_i < bid_j`. Over the logged history (09-09 to 09-14) across REC, RSHATT,
  SPREAD, TOTAL, WINS, WINSWEEK and WINSTREAK:
  - **892 episodes** (1,502 violating poll instants, 799 with both legs in the
    same poll). Gap at the start: median **1c**, p90 3c, max 12c.
  - **846 of 892 (95%) are in-game** - rungs of one ladder updating out of
    step after kickoff. 42 are season futures, 4 pre-game.
  - 232 survive taker fees on both legs; 215 / 227 / 232 at 10 / 50 / 100
    contracts with the rounded fee.
  - **Depth could be checked on only 17 of the 232, and none had even 10
    contracts at the touch on both legs - the thinner leg carried a median of
    1 contract, max 2.** The other 215 are unverifiable,
    not infeasible - 128 had no depth snapshot within 60s, 87 had one showing
    a different book, which in-game is expected.
  - Persistence is mostly one poll: 67% of fee-survivors were seen once
    (0 to ~60s), 11% lasted 1-5 min, 4% 5-30 min, max ~10 min. The poll floor
    is ~10s in-game, so anything faster was never seen.
  - Verdict on the brief's own standard: this is a latency race on a thin
    touch, not a business.
- **First-TD-scorer partitions are incomplete, so the sum test cannot run**
  (brief 019 H2, `research/structural.py --partition`). `KXNFLFIRSTTD` is the
  player partition; `KXNFLANYTD` and the `KXNFLTD` 1+/2+ ladder are not
  partitions. **16 of 18 listed events fail completeness**: 13 of 16 settled
  week-1 events had NO "No Touchdown" leg, SF@LA listed it TWICE (`-NONE` and
  `-LARNONE`, both "No Touchdown" - overlapping legs), and the open week-2
  DET@BUF event lists two D/STs and No Touchdown with no players. **ARI@LAC
  settled with no listed leg resolving yes** - the outcome that happened was
  not on the board. The two that pass on composition have no logged book
  (the series is untracked), so H2 was NOT tested.
  - Classify legs by `yes_sub_title`, not by ticker. A ticker regex caught
    `-LARNONE` only because the pattern happened to be end-anchored.
  - Settled markets quote 0/1, so a completeness check that also tests
    tradeability calls every settled event incomplete for the wrong reason.
    Composition only, and say tradeability is unknowable.
- **The NFL board, catalogued** (brief 019 item 0, `research/structural.py
  --catalogue`, snapshot `jobs/snapshot_kalshi_series.py` -> `board_019.db`,
  Mon 09-14 23:08 ET). 358 NFL series listed, 154 with open markets, 10,631
  open markets; 53 match the logger's allowlist. Of the 154: 70 partitions, 41
  ladders, 27 multi-binary, 16 standalone. **The tight books are the ones we
  do not model**: futures partitions (division, conference, MVP, awards) and
  game lines quote 1c (`KXNFLGAME`, `KXNFLSPREAD`), `KXNFLTOTAL` 2c. Untracked
  props run 4-12c (`KXNFLTD` ladder 4c on 58k volume, PASSYDS 5c, RECYDS 12c).
  Our two series are the widest on the board: REC 13c at snapshot (6c over
  logged history), RSHATT 60c at snapshot (14c history). Median prints per
  market: GAME at least 20,000 (22 of 28 tapes hit the fetch cap, so this is
  a floor, not a median), SPREAD 735, TOTAL 600, REC 40, RSHATT 14.
  - **The CLAUDE.md line "no anytime-TD series" is stale.** `KXNFLANYTD`
    exists (0 open at snapshot) and `KXNFLTD` is a 1+/2+/3+/4+ player ladder.
- **The trades tape pages NEWEST FIRST, so a capped fetch keeps the wrong
  end.** `MAX_PAGES=20` x 1000 was sized for props; 22 of 28 `KXNFLGAME` tapes
  and 2 `KXNFLSPREAD` tapes hit it, and every one of the 22 GAME tapes kept
  only in-game prints - the earliest retained print was after kickoff. A maker
  simulation reading entry->kickoff saw an EMPTY tape and called it "never
  filled", which is a plausible number. Fetch with `min_ts`/`max_ts` bounded to
  the window, and when a bounded pass still caps, walk `max_ts` back to the
  earliest print returned (`walk_window`). DAL@NYG needed 4 rounds, 62,619
  prints. A tape whose note says `hit MAX_PAGES` is not a tape.
- **Both-sides spread capture is negative on every tight series and zero on
  ours - and the fee explains that ordering as well as the spread does**
  (brief 019 H3, `research/structural.py --capture`). The 018 both-sides maker
  simulation per series, 100 contracts, same 14 events and entry instant,
  behind the queue, 14-game block bootstrap:

      series   spread  M   fill                 conditional            gap
      REC       7.0c   0   18.9% [15.2, 22.8]   +0.24 [-0.31, +0.86]   +3.92 [+3.21, +4.72]
      RSHATT    6.0c   0   25.5% [20.1, 33.3]   +0.85 [-0.45, +1.98]   +4.96 [+2.95, +7.15]
      SPREAD    2.0c   1   28.2% [23.9, 32.9]   -0.83 [-1.67, -0.29]   +1.84 [+1.09, +3.02]
      TOTAL     2.0c   1   18.8% [14.5, 24.2]   -0.50 [-0.94, -0.15]   +1.21 [+0.84, +1.72]
      GAME      1.0c   1   46.4% [39.3, 50.0]   -1.32 [-2.59, -0.37]   +2.65 [+0.86, +5.07]

  - The standing prediction (tighter captures worse) HELD as an ordering:
    corr(spread, conditional capture) = +0.90 over 5 series. **Five points is
    an ordering, not a test.**
  - **Spread and maker fee are perfectly collinear here.** Every tight series
    charges makers (`quadratic_with_maker_fees`), every wide one is free, so
    this sample cannot say which of the two did it. Adding back a maker fee of
    ~0.44pp at p~0.5 leaves SPREAD ~-0.4, TOTAL ~-0.1, GAME ~-0.9 gross -
    still at or below zero, but TOTAL's loss is mostly the fee.
  - Adverse selection is visible on the tight books as an actual LOSS on the
    fills, where on the props it was fills worth nothing. The selection gap
    excludes zero everywhere.
  - **The GAME row was first computed on truncated tapes** (see above): fill
    19.6%, conditional -1.17, never-filled +0.42, gap +1.59. On the complete
    tapes it is 46.4% / -1.32 / +1.34 / +2.65. The fill rate more than doubled
    and the sign did not move. The other four rows did not change.
  - Test count for brief 019: H1 1, H2 0 (not runnable), H3 5 x 4 intervals
    + 1 ordering = **22**.

- **Kalshi's team lines do not lag the retail book consensus by more than it
  costs to cross, and where they seem to, it is three bets** (brief 020,
  `research/consensus.py`, pre-registered at `72d05e7`). Week 1, KXNFLSPREAD
  and KXNFLTOTAL against 13 `us` books (no Pinnacle), exact lines only,
  moneyline excluded (books void on a tie; Kalshi has no tie leg).
  - Matching: 125 of 768 Kalshi rungs ever meet an exact book line - books
    hang the main line and a half-point, Kalshi hangs a ladder. 1,060
    observations (945 pre-kickoff, 115 in-game), 16 games.
  - Gap (Kalshi mid - consensus): median -0.32pp, p10 -1.86, p90 +1.42; mean
    -0.19pp [-0.39, +0.01]. **6.2% beyond 2.5pp overall, 2.2% pre-kickoff (21 of 945),
    0% beyond 72h and 0% in the last hour.** Multiplicative and Shin agree to
    a median 0.006pp near 50c - no material de-vig disagreement on these lines.
  - **Lead-lag: books lead, weakly.** Share of the gap closed by the next
    snapshot (~70 min): Kalshi toward books 0.05 [0.03, 0.10], books toward
    Kalshi 0.01 [-0.00, 0.02]. Same-interval change correlation +0.24; books
    one interval earlier +0.09, later -0.02. Nothing below the ~70-min
    snapshot spacing is resolvable.
  - **Pre-kickoff gaps above 2.5pp are PERSISTENT, not a latency race** - 84%
    still open at the next snapshot ~80 min later, depth ~150k contracts at
    the touch - **and there are only three of them**: BAL -3.5, WAS@PHI over
    44.5, CHI@CAR over 47.5. Each is the minority half-point that 2-7 books
    left up after the market moved to the key number, at 2.5-3.2pp, i.e. at
    the cost line. All three bought YES and all three won: +51.5pp/contract
    on 2-3 games, which is a coin flip landing, not an edge. **CLV to Kalshi's
    own close was -0.9pp** - the close moved AWAY from the books.
  - **An interval over <5 games is not read, whatever it excludes.** A block
    bootstrap over three identical wins returns a tight interval that
    "excludes zero"; 15 of the 17 zero-excluding intervals were that. The
    script now labels them. The 2.0pp curve point (51 triggers, 11 games)
    reads +23.65pp [-19.60, +47.07].
  - **In-game the consensus is a clock, not a price.** 39% of in-game
    observations exceed 2.5pp, but books on ONE line disagree by median 2.2pp,
    p90 6.7pp, max ~35pp (one live, one stale), and book lag p90 doubles to
    235s. In-game PnL +7.17pp [-9.76, +20.56] on 12 games contains zero, and
    no in-game gap can be attributed to Kalshi.
  - Verdict on the brief's own terms: null. Pre-kickoff gaps that clear cost
    are too rare to trade and did not beat Kalshi's close; in-game ones are
    measurement. 33 pre-registered intervals, 31 estimable, 2 readable ones
    exclude zero (both L2).

- **THE MODEL IS WORSE THAN THE MARKET, AND POWERED TO SAY SO** (brief 021
  Part A, `research/score.py`, pre-registered at `93ac88d`). Week 1, 935
  predictions: 907 settle (28 players with no stat row), 682 on the common set
  where Kalshi was two-sided at entry, 131 fits, 14 games. Market = Kalshi mid,
  no de-vig. Naive = the player's own Laplace-smoothed 2025 hit rate.

      Brier  model 0.1957   market 0.1682   naive 0.1924
      model - market   +0.0275 [+0.0117, +0.0410]   log loss +0.0747 [+0.0266, +0.1168]
      model - naive    +0.0032 [-0.0057, +0.0121]
      market - naive   -0.0242 [-0.0364, -0.0112]

  - **Minimum detectable Brier difference at 80% power is 0.021** (bootstrap
    SE 0.0075; cluster-robust 0.0213). The observed gap is above it: this is a
    powered result, not noise. A model EDGE smaller than ~0.02 could not have
    been seen in one week, and 0.005 would take ~244 games (~1 season), 0.002
    ~5.6 seasons.
  - **The model does not beat a smoothed 2025 frequency.** Everything the
    usage fit adds over "how often did he clear this last year" is invisible.
  - Calibration: ECE model 0.0764, market 0.0387. The model is too LOW on thin
    rungs (0.0-0.1 bin forecasts 0.049, realizes 0.122 [0.078, 0.184]; 0.1-0.2
    forecasts 0.147, realizes 0.252) and too HIGH near 0.75 (0.750 vs 0.581).
    No market bin with n>=30 excludes its forecast.
  - **Subsets cannot rescue it.** 19 estimable cells (series, prior games,
    price bucket, ladder position), 12 exclude zero and ALL 12 say the model
    is worse; none favours it. "Books contributing to the fit" is not
    definable - the fit has no book input. Thick history (25+ games) is empty:
    `prior_games` maxes at 18.
  - Consequence, per the brief's own logic: no subset of players, teams or
    situations can be positive EV off this forecast. Every execution study from
    S00 to 020 was measuring a forecast that loses to the price it trades
    against.
  - The multi-season version (Odds API closes in `outcome_close`, ~816 games,
    MDE ~0.003) is scoped, not started. Blockers: shrinkage constants were fitted
    on 2023-25 so a backtest there is in-sample; position/role come from the
    current crosswalk, not as-of.
- **Halftime capture is built and OFF** (brief 021 Part B). `ODDS_HALFTIME_*`
  in `config.py`, bulk `spreads,totals` every 30s only inside
  kickoff+66..+123 min, per UTC-day credit cap from `x-requests-last`, and
  `quotes.source_ts` = each book's own `last_update` on every Odds API row.
  - **Halftime is not where the median says.** Over 1,086 REG games 2022-25,
    last Q2 play lands p5 76 / p50 86 / p95 98 min after SCHEDULED kickoff,
    first Q3 play p5 90 / p50 101 / p95 113, halftime a steady 14 min. A window
    on the median covers the whole halftime+-10 in 2% of games; p5-p95 covers
    90%. `jobs/halftime_plan.py` re-derives it.
  - Cost at 2 credits/call: ~1,224 credits for a full week, ~2,448 to the end
    of September, against 24,582 spendable above the 20,000 reserve (2026-09-15).
  - `/sports` reports the balance for 0 credits - read it there, not by
    spending.
- **The logger now restarts at LOGON, not at boot.** Task
  `CalibratedSports Logger (logon)` calls `start_logger.ps1`; run by hand it
  refuses a duplicate (verified). An AT-STARTUP task needs an elevated shell and
  was refused - after an unattended Windows Update reboot nothing starts until
  someone logs in.

**Anything quoted as a finding must have a committed script in `research/`.**
Numbers reached `CLAUDE.md` once without one; the reference then could not be
reproduced, and separating a data change from a methodology change cost a
session.

## Out of scope for v1

WR/CB matchup modelling · PFF or SIS data · a second sport · order placement ·
LLM feature extraction · MLB Toolbox migration · monetization.

## Public / private

Public repo: ingestion, schema, simulator skeleton, CLV and calibration
harness, research scripts. Private `edge/`: fitted parameters, feature set,
selection thresholds, sizing, execution.

PFF data, if ever purchased, never leaves `edge/`. Their terms bar public
display and derivative works, and a public repo depending on licensed data is
not reproducible — which defeats the point of it being public.

## Editorial line

Hit-rate history is a **fact** and is publishable: "Jefferson is 11-6 to the
over" is research. "Bet Jefferson over tonight" is a pick and is not published.
Model-vs-market disagreements publish **only after settlement**.

## Conventions

Python 3.12, `polars` for analysis, `httpx` for I/O, `pytest`. All timestamps
UTC unix seconds named `*_ts`. No secrets in the repo; `.env`, `*.pem` and
`*.key` gitignored. Windows dev machine — no `&&` in PowerShell, use `$HOME`
not `~`.
