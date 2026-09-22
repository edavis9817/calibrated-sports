# Calibrated Sports — working agreement

Read before proposing or writing code. This file exists so decisions survive
across sessions instead of being re-litigated or quietly violated. If a decision
is not written here or in `DECISIONS.md`, a future session will not know it.

## Agreement between two unchecked arguments is not evidence

Two reasons pointing the same way feel like corroboration. They are not. Nothing
makes either one evidence except measuring it, and the fact that they agree is
usually what stops anyone measuring either.

Earned three times in two days. The worked case is the bootstrap cache under
*Shared denominators*: it was defended both by "nothing published is a contrast
between subjects" and by "regenerating draws is ~99% of runtime". Both were
false, neither had been checked, and the agreement is what made it feel settled.

The check is the same one the proxy table asks: **which of these two did I
measure?** If the answer is neither, there is one argument, not two, and it is
an assumption.

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

**A text-parsed name column is never a key** (track C, A-C1; applies to every
track). In `cfbfastR_cfb_pbp`, `rusher_player_name` ran 0.37 of plays in 2024,
0.21 in 2025 and **0.000 in 2026** while `rush_player_id` held flat at
0.35-0.36; `sack_player_name` and `sack_players` went the same way. Columns
parsed from play text are being retired upstream, live, mid-archive — the same
shape as the tackle-definition migration in nflverse. Key and join on ids.
`cfb.pbp_scope.key_column()` refuses the name columns by name and by
`_player_name` shape.

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
- **There are no single-game player props ON KALSHI** - and that is a fact about the
  EXCHANGE, not about the sport. Measured 2026-09-19 (P1, 74 credits,
  `research/cfb_p1_markets.py`): US SPORTSBOOKS list 26 player-prop keys on college
  games, on 55 of 74 events in one week-4 slate - `player_receptions` on 31 events,
  `player_rush_yds` on 36, `player_pass_yds` on 39, anytime TD on 54. DraftKings and
  FanDuel list the most keys; betus, fanatics and lowvig list none. So "CFB has no
  player props" is true of Kalshi and false of the market. What has NOT changed: the
  appearance gap still bans hit rates and settlement, and the model loses to the close
  in every season (brief 023), so this reopens no betting case - it reopens the question
  of what CFB DATA is purchasable, at ~3,700 credits for a 5-market usage pull over a
  slate. 47,553 markets across 97 NCAAF series on Kalshi price none of it. Everything player-named is a SEASON leader
  future closing 2027-01-07 ("Nick Rinaldi records the most sacks in the SEC").
  What looks like a prop is team-level: `KXNCAAFTEAMRECYDS` is "Texas: 325+
  receiving yards", `KXNCAAFTEAMTD` is "Utah: 4+ rushing touchdowns".
- **So CFB is not addressable by this model ON KALSHI.** SCOPE CORRECTED 2026-09-21:
  the sentence used to read "CFB is not addressable by this model", which is a claim
  about the SPORT that nobody measured. What was measured is Kalshi's catalogue, and
  what it supports is: the validated edge is single-game player usage - targets, rush
  attempts, receptions - and KALSHI lists none of it. US sportsbooks DO (26 prop keys,
  55 of 74 events, P1 2026-09-19), so a books-priced CFB usage model is not refuted by
  anything in this section. What still refutes it is elsewhere and is not about
  listings: no appearance signal (`cfb.stats_and_usage_only`), and the model losing to
  the close in every NFL season (brief 023). Do not re-probe KALSHI for props.
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
- **VERDICT: NO-GO ON KALSHI FOR COLLEGE FOOTBALL.** SCOPE CORRECTED 2026-09-21:
  this read "NO-GO on college football" and was quoted onward as a fact about the
  sport. The probe measured ONE VENUE over 56 hours. What it establishes: on Kalshi
  there is nothing to point this model at - no single-game player props exist THERE,
  the derivatives already cohere to within a third of a point (Part 1), and a
  team-level effort needs a CFB facts layer that did not then exist. What has since
  changed, and what has not:
  - **A venue listing college player props at book density EXISTS** - the condition
    this bullet named as what "would change it". US sportsbooks list 26 prop keys on
    55 of 74 events (P1, 2026-09-19). That closes the listing question and opens a
    pricing one; it does not by itself make a model viable.
  - **The CFB facts layer now exists** (W07 track C): games, teams, rosters, box,
    usage, lines, rankings, venues, 2001-2026.
  - **The binding constraint is no longer listings; it is the APPEARANCE GAP.** No
    public source records whether a college player dressed, so a prop record cannot
    yield an honest hit rate at any price. See
    `docs/C03-cfb-prop-preregistration.md`, which names that gap as the gate.
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
- **`stats_player_week` has NO ROW for a player who played but recorded no
  stat.** Verified 2026-09-15 on the raw parquet: Calvin Ridley, De'Zhaun
  Stribling, Elijah Arroyo and Odell Beckham Jr. played 11-32 offensive snaps in
  week 1 and are absent from the file itself, not just the table. A missing row
  is therefore NOT "inactive" and NOT "unresolvable": with offensive snaps > 0
  (`nfl_snap_counts`, from 2013 - the table's own earliest season and
  `export_web.SNAP_FIRST_SEASON`; "2012" was wrong) the actual is 0 and the over LOST; with no snaps
  the book voids the prop. `jobs/settle_outcomes.py` still treats every missing
  row as unsettled, which drops exactly the zero outcomes and inflates the
  realized over rate - brief 021's 28 "no stat row" exclusions are this.
- **TARGETS ARE UNRECOVERABLE FOR 2003-2008. This is a permanent data
  limitation, not a deferred fix.** The earlier plan — null them now and rebuild
  from play-by-play `receiver_id` later — is withdrawn: track F measured the PBP
  and the receiver is named on **100% of completions but only 0.5-0.9% of
  incompletions** in those six seasons. A target is a pass *thrown at* a
  receiver, so reconstructing it needs exactly the half that is missing. PBP
  cannot rebuild this at any effort.
  - **The store holds ZEROS, not nulls, which is the dangerous part.** Measured
    2026-09-17: 2003-2008 carry ~17,200 rows per season with `targets` non-null
    on every one, summing to **3, 5, 0, 67, 14, 17** against ~17,000 in the
    seasons either side. A consumer therefore computes `target_share = 0` and
    renders a confident zero rather than a gap. Null at the READ boundary —
    invariant 2 forbids transforming on write, and the raw file is not wrong,
    it is empty.
  - Same shape as snap counts (2013+): a stat that does not exist before season
    N needs vocabulary the contract does not have. Filed to track B as A5, and
    one mechanism must serve both sports — see `docs/track-c-requests.md` C-A3.
- **THE SILENT-ZERO CLASS: a column that is present, populated and ZERO for a run
  of seasons. No null check can see it, and a mean over it returns a number that
  is wrong.** Targets are one row of this class, not a special case. Track F swept
  the nflverse release for it (`python -m analytics.survey --silent-zeros`,
  `docs/F01-pbp-survey.md` §2.9, 13 columns); track A swept the columns the SITE
  PUBLISHES, which is the different and smaller question. **Three published
  columns are affected, measured 2026-09-17 against `nfl_player_week`:**

      source column               published as   silently zero
      targets                     targets        2003-2008
      def_tackles_for_loss        def_tfl        2003-2011   <- longest in the archive
      def_qb_hits                 def_qb_hits    2003-2005

  `def_tackles_for_loss` is 1,796 player-weeks in 2002, **0 through nine
  seasons**, 1,843 in 2012, non-null throughout — so a career TFL total spanning
  that span silently drops nine years. The seven other columns track F found
  (`racr`, `receiving_air_yards`, `pacr`, `passing_air_yards`, `air_yards_share`,
  `receiving_yards_after_catch`, `def_tackles_for_loss_yards`) are in neither
  `STAT_MAP` nor `DEF_COLUMNS` and reach no page.
  - **`target_share` is a DIFFERENT shape and needs its own handling.** It is
    mostly NULL in the hole rather than zero — 60 non-null rows of 17,211 in
    2003, none at all in 2005 — so it degrades honestly except for a small
    residue that would render as a genuine usage share inside a gap.
  - **Test it at the level that is published, not at the source.** Effectively
    zero, never exactly zero: the league target counts are 3, 5, 0, 67, 14, 17,
    so an exact-zero test walks past five of the six seasons. Track F's first
    version did exactly that and missed the defect it was named after.
  - **The contract already requires the fix.** `Stats` is
    `{"type": ["number", "null"]}` and its description says *"null means unknown
    or not collected, never zero"* — so the export publishing 0 here violates the
    intent its own contract states, and no contract change is needed to correct
    it. What IS missing is vocabulary for the REASON, which is track B's A5.
- **QB passing facts are COMPLETE across all 28 seasons — the facts layer is not
  the blocker on QB props** (measured by track F, 2026-09-17). PBP passing
  reconciles to `stats_player_week` to the unit with no cliff anywhere,
  including the 2003 and 2005 seasons that break targets. So if `KXNFLPASSYDS`
  or `KXNFLPASSTDS` are ever logged, settlement and prop history need **no new
  facts work**; the whole cost is market logging and Odds API credits. That
  materially changes the calculus on tracking them, and belongs in any prop
  coverage report. Note this is a statement about DATA availability only — brief
  019's catalogue measured PASSYDS at a 5c spread, and 021/023 still say the
  model loses to the close.
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
  - **`def_tackles_with_assist` is being RECLASSIFIED UPSTREAM, so "solo" does not
    mean the same thing across seasons** (measured by track F, 2026-09-17). Its
    share of plays collapses 0.075 (2023) → 0.014 (2025) → 0.007 (2026) while
    plain `def_tackle_assists` rises 13,299 → 17,059. This is a definition change
    at the source, **not a missing feed**: total tackles are stable (37,443 →
    38,443) over the same span.
    - **Nothing published moves.** The `tackles_assists` market settles on the
      SUM of all three columns, and the sum is what is stable — which is the
      second reason the three-column decomposition above is the right one.
    - **But box-score SOLO falls 11% across those seasons on definition alone.**
      Any split, trend or comparison quoting solo across 2023-2026 is comparing
      two different definitions and will read as a real decline. Recorded before
      it bites a split, which is the only cheap moment to record it.
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

      Brier  model 0.1916   market 0.1676   naive 0.1916
      model - market   +0.0240 [+0.0089, +0.0373]

  RESTATED 2026-09-17 after the settlement fix. The population grew from 682 to
  706 on the common set (the played-with-no-stat-row outcomes now settle at 0
  instead of being dropped), and the figures above are the ones the site serves
  from `research/calibration.json`. THE VERDICT DID NOT MOVE: the interval still
  excludes zero and the new estimate sits inside the previously published one.
  What DID change is that **the model and the naive prior are now identical** -
  0.1916 against 0.1916, where the model was 0.0033 worse. It is not "no better
  than" a smoothed prior-season frequency; it is the same number.
  Log loss, and the intervals on model-naive and market-naive, were NOT
  re-derived and are removed rather than carried forward stale.

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

- **THE SWEEP FOUND NOTHING THAT CLEARS THE BAR** (brief 022, `research/sweep/`,
  pre-registered at `ae3895b`, candidates frozen at `7e18ce3` before any CFB
  row was read). Search set NFL week 1; holdouts CFB (opened) and NFL week 2
  (does not exist yet - `python -m research.sweep.replicate_week2` refuses until
  every week-2 game has a final score). `python -m research.sweep.summarize`
  re-derives every count below.

      search tests registered 396 (328 estimable); nominal p<0.05: 230
      expected false positives at alpha=0.05: 16.4
      BH q=0.10 survivors 223 = 13 money-positive (all NFL H1)
                              + 83 significant on the LOSING side + 127 statistical
      replication records 122; invalid by construction 64; descriptive 589
      candidates 101 -> FINDINGS 0   (failed at bar 1: 36, bar 2: 42, bar 4: 23)

  - **H1, settlement/determination lag - the one thing that is real and it is
    tiny.** NFL receptions rungs already reached mid-game still offer YES at
    0.96-0.99: +1.81pp/contract [+1.24, +2.47] at 120s after the play, 10
    contracts depth-confirmed, persisting ~250s, on 16 of 247 decided markets
    over 8 games - about $17 for the week with capital locked a median 34 min.
    It passes bars 1, 3, 4 and 5 and fails ONLY bar 2: player props replicate
    only on week 2. Rush attempts: same sign but persists ~13s (a race).
    Totals, spreads and moneylines: nothing executable - once decided, nobody
    offers the true side.
    - **Final stats are look-ahead against the live feed.** Devaughn Vele 8+
      receptions traded at 0.99 YES after the whistle and settled NO on a final
      of 7 - one such loss (-99pp) erases ~55 of the wins. Any live version must
      decide on the feed traders see, not on nflverse.
    - Kalshi settlement agreed with the box score on **0 disagreements of 1,839
      NFL and 0 of 4,423 CFB** markets.
    - nflverse `time_of_day` is the SNAP, so a guard of 0s measures a play
      still running (NO@DET quoted 0.55 at the final snap). Use >= 60s.
    - **The CFB H1 run is invalid by construction.** My own pre-registered
      D = "first moneyline >= 0.97" lands a median 141 min before the moneyline
      closes, so spread/total "truth" from the final score was selection on the
      known winner (+15 to +33pp, flat across guards). Marked `invalid`, out of
      BH and out of bar 2.
  - **H2, order book imbalance - real, replicates, never pays.** In-game on
    team markets the next move goes toward the thin side (SPREAD 60s +0.30pp
    per unit I, replicated on CFB +0.15; TOTAL +0.34 / +0.19; GAME +0.55 /
    +0.32). Top-decile move minus half-spread and fee is negative in all 30 NFL
    and all 16 estimable CFB cells; the largest gross move is ~1.1pp against
    1.5-24pp of cost. Pre-kickoff slopes are resolution artifacts (95%+ of 10s
    lookups hit the same quote row). In-game prop "imbalance" is one stale
    1-2 contract order; its positive 10s slope was depth fresher than the quote.
  - **H3, the execution map - use it.** Median quoted spread by time to kickoff
    (>72h / 24-72h / 6-24h / 1-6h / 0-1h / in-game): REC 9/4/3/3/2/**16c**,
    RSHATT 6/13/10/9/9/**63c**, SPREAD and TOTAL 2/1/1/1/1/1c, GAME 1c. Touch
    size 1-6h -> in-game: REC 50 -> 2, RSHATT 3 -> 1, SPREAD 7,943 -> 89.
    **Cross game lines 1-6h before kickoff; never cross a prop in-game.**
    In-game and 24-72h / 6-24h widening replicate on CFB; hour-of-day
    contrasts do not (they are the in-game samples leaking into 09-16 ET).
    Spread / 60-min volatility, the maker's screen: CHAMP 3.88, WINSWEEK 2.76,
    RSHATT 2.51, WINS 2.13, DIVISION 1.18, REC 1.05, TOTAL 0.31, SPREAD 0.30,
    GAME 0.30.
  - **Open scan.** Price-path reversal (slopes -0.04 to -0.48) replicates on
    CFB for 8 of 9 team records, and every top-decile net of cost is negative -
    it is mid-quote bounce, with no one to trade against at the mid. Large prop
    prints carry information (REC +1.9pp continuation at 10 min) and lose
    1.8-2.9pp net. Key-number pricing of the unmatched rungs: Kalshi sits below
    a shifted historical margin distribution on far rungs (-0.4 to -0.9pp), but
    trading it loses on totals (-25.5pp) and the close moves against the model
    - model tails, not mispricing. C1/C2 constraints are the arithmetic of
    P(margin = 1) ~2.1%; the in-game C1 violation rate replicates on CFB
    (0.75% of instants) and 0 of 43 were fillable at 10 contracts. The four C01
    CFB relations, as tests: all null.
  - Not testable: `KXNFLRECYDS`, any team passing total and `KXMVE` are not
    tracked by the logger, so those cross-market constraints have 0 tests.
    **673 week-1 prop markets have no `market_outcome` mapping.**
  - **Grading lessons, encoded in `research/sweep/common.py`:** a
    percentile-bootstrap p floors at 1/draws and can never survive BH over
    hundreds of tests - use z = est / bootstrap SE; a zero-variance bootstrap is
    p = 1, not p = 0 (a 5-market constant was briefly the most significant test
    in the sweep); an interval on < 5 games enters BH at p = 1; a share of
    markets that is >= 0 by construction has no null and is descriptive; BH
    survivors must be split by sign, because most of them were "significant"
    losses. Registry records carry their compute time, so "mechanism before the
    holdout" is checked against the commit, not remembered.

- **THE MODEL LOSES TO THE SPORTSBOOK CLOSE IN EVERY SEASON, WALK-FORWARD**
  (brief 023 Part 1, `research/walkforward.py`, pre-registered at `fd0a57b`).
  Receptions and rush attempts, over side; every fitted constant refit on
  seasons <= T-1 (a `Constants` object refuses prediction if any fit season
  >= T); features strictly as-of. Close = `outcome_close.p_bench`, the
  de-vigged DK/FD/MGM median within 15 min of kickoff. Game block bootstrap:

      season  n      games  Brier model / close / naive   model - close           MDE
      2023    4,785  283    0.2695 / 0.2466 / 0.2777      +0.0229 [+0.0170,+0.0290]  0.0085
      2024    5,225  285    0.2687 / 0.2450 / 0.2833      +0.0237 [+0.0188,+0.0288]  0.0073
      2025    6,031  284    0.2648 / 0.2453 / 0.2844      +0.0195 [+0.0144,+0.0253]  0.0080

    RESTATED 2026-09-17, re-run after the settlement fix. The verdict is
    unchanged - every interval excludes zero, every estimate is 2.4-3.2x its
    MDE, and the summary reports `{'model worse*': 13}` across all 13 variants
    in all three seasons. The model still loses to the book close everywhere.

  - **Powered, and robust to everything we could not pin down.**
    `SHRINK_GAMES_VMR`, `TEAM_CHANGE_KEEP` and `COACH_CHANGE_KEEP` are
    judgment calls with no fitting script; k=6 was kept after a 2023-25 check;
    `MEAN_DRIFT_VAR` has no reproducible measurement (about 500 procedure
    variants failed to reproduce 8.56 / 1.12). All were bracketed with
    alternatives declared before the run - 13 variants per season, 60 intervals
    - and every one has the model worse with the interval excluding zero (range
    +0.0211 to +0.0295). Receptions and rush attempts both lose separately.
  - THAT DISTINCTION NO LONGER EXISTS (2026-09-17). The "corrected" arm settled
    played-with-no-stat-row at 0; `jobs/settle_outcomes.py` now does exactly
    that, so the re-run reports `0 moved to settled-at-0` in all three seasons
    and the two arms are the SAME computation - visible as
    `Brier(model) - Brier(p_all)` and its CORRECTED twin being byte-identical.
    The corrected figures it used to quote (+0.0231 / +0.0237 / +0.0195) are now
    simply the primary ones (+0.0229 / +0.0237 / +0.0195). The shift the
    correction was worth is therefore already inside the table above, measured
    at 0.0043 / 0.0028 / 0.0023 against the 0.002-0.004 this line predicted.
  - The model beats the naive prior-season hit rate by only 0.003-0.010.
  - **This closes the model-versus-close question.** The brief's rule was that
    beating neither end closes Part 1; the close is beaten by 0.022-0.027 against
    an MDE of ~0.008. The OPEN is not on disk (the backfill bought closes only),
    so "beats the open, not the close" is untested: one kickoff -48h snapshot is
    ~17,100 credits for 2 markets or ~42,750 for all 5 (spendable 24,582 on
    2026-09-15). Pilot one week (~320 credits) before any buy.
- **Book versus book: prop arbitrage is real, about 1pp, and not a business;
  every middle loses** (brief 023 Part 2, `research/bookvbook.py`,
  pre-registered at `fd0a57b`). Pre-kickoff, freshness-filtered (quote
  <= 600s old at the snapshot, pair <= 300s apart), no forecast.
  - **Before kickoff the clocks are fine.** Only 12 of 702 historical arbs
    vanish under the freshness filter (1.7%) and 0 of 12 live. The skew that
    ruined 020's in-game comparison is an in-game problem.
  - **Arbitrage** (best over and best under from different fresh books, vig
    included):

        class               hist rate   mean size    live 2026 wk1
        spreads             0.15%       +0.76pp      0
        totals              0           -            0
        receptions          2.53%       +1.09pp      1 arb (interval contains 0)
        rush attempts       1.71%       +1.12pp      1.77%, +0.92pp on 4 games
        tackles+assists     1.92%       +1.30pp      0.45%, interval contains 0

    Generated by slow retail and offshore books (bovada, betonlineag, betrivers)
    not moving a prop with the market. Fails replication; persistence cannot be
    measured on historical props (one close snapshot each), and live only 17%
    survive to a snapshot 3h later. **Ceiling: ~$5 per $500 two-leg instance
    before the account is limited.** Never annualise it.
  - **Middles lose everywhere** on the empirical distribution. Spreads and
    totals are half-point shading worth about one vig (-2.4 to -2.5pp); props
    -2 to -7pp. Live "positive" realized middles are two games' final scores.
  - Settling played-with-no-stat-row as 0 (by offensive or defensive snaps)
    makes every middle MORE negative; no sign flips.
  - Known defects: sacks quarter lines (0.25/0.75) were settled as plain
    thresholds; some prop subjects merge name spellings.
- **Inactives cannot be tested on disk** (brief 023 Part 3,
  `research/inactives.py`). No source timestamps an inactive announcement: the
  nflverse injuries data (16 columns) is the Wed-Fri report with no time field.
  The week-1 proxy (a player's own Kalshi ladder collapsing) found 0 events -
  4 of its 5 flags had played and merely had no stat row. **Kalshi pulled Brock
  Bowers' markets on the Saturday, 23.9h before kickoff**, on a Friday "Out":
  for known OUTs there is no game-day second-order window at all. Forward
  capture is specified, not built: `docs/briefs/023-inactives-capture-spec.md`.

**Anything quoted as a finding must have a committed script in `research/`.**
Numbers reached `CLAUDE.md` once without one; the reference then could not be
reproduced, and separating a data change from a methodology change cost a
session.

## The website (brief W02)

The site is `calibratedsports-web`, a Next 14 static export served as
**Cloudflare Workers static assets**, not Pages: Cloudflare routed the Git
connection to Workers Builds. It lives at
https://calibratedsports-web.ethanad17.workers.dev, and **there is no custom
domain by decision**.
- **Deploy.** Workers Builds runs `npm run build` and then `npx wrangler deploy`
  on every push to `main`.
- **`wrangler.jsonc`.** It is assets-only (`./out`, `not_found_handling`
  `404-page`). `workers_dev: true` is explicit, because workers.dev is the only
  address. `preview_urls: false` is explicit, because per-version preview URLs
  are public.
- **Static assets went 12,076 -> 36 when player and team pages moved to edge
  rendering** (site-architecture §2, live 2026-09-15). The v1 static export
  used 60% of the 20,000-asset cap with ONE sport; the OpenNext build never
  reads data, so the count no longer scales with players. Verified live: every
  `/nfl/...` route 200, undeclared pages and unknown players 404, and every v1
  URL 404 (a trailing slash redirects 308 first, then 404).
- **Two things killed the first OpenNext deploy, and both are now guarded.**
  - `prebuild` wrote `public/build.json`, but §2 deleted `public/data` and git
    does not track empty directories - so a CLEAN CLONE, which is what Workers
    Builds checks out, had no `public/` and the build died in 3s on ENOENT. Every
    local build passed because the working tree still had the directory. Fixed
    with `mkdirSync(..., {recursive: true})` AND a committed `public/.gitkeep`.
  - `.node-version` said 20; wrangler, miniflare and `@cloudflare/kv-asset-handler`
    all require **node >= 22**. Next 15 runs on 20, the Cloudflare tooling does
    not, and that is what runs at the deploy step.
  - **`npm run check:clean` is the guard:** clone the committed HEAD, `npm ci`,
    `npm run build`. CI runs the same job. A local build against the working
    tree cannot see a file the build needs that git does not track.
- **A contract type that lies is a production outage, and `curl` finds only
  half of it.** `lib/schema.ts` typed `Identity.name` and `IndexPlayer.name`
  as `string`; the exporter emits `null` for a player with no `player_xwalk`
  row. Exactly one of 3,971 exported players is one (`00-0005532`: three 1999
  rows for NO). TypeScript believed the type, so nothing was guarded:
  - `initials()` called `.trim()` on it and `/nfl/player/00-0005532` returned
    **500** - worse than the broken image the monogram fallback exists to
    prevent;
  - `fold()` called `.normalize()` on it inside `searchPlayers`, which runs
    over **every** player on **every** keystroke including the empty query, so
    that one row threw inside the whole 3,971-row players index.
  - **The index failure was invisible to a status check**: `/nfl/players`
    returned 200 because that view is a client shell and the throw happened in
    the browser. Verify a client view by what it renders, not by its code.
  - Fixed by typing both `string | null` and rendering every player name
    through `displayName()` (name, else slug). Roster rows were checked and are
    not affected: 32 team files, 1,117 rows, 0 null names.
  - The cause was that nothing validated the export against the site's own
    schema. **Closed by the executable contract below** - the fallback was only
    the symptom's fix.
- **THE CONTRACT IS `web/contract/v2/contract.schema.json`, AND IT IS
  EXECUTABLE.** `docs/web-schema.md` is prose *about* it. One document, two
  consumers: `jobs/export_web.py` validates every file it writes against it
  (in `sync_keys`, the single choke point) and DERIVES `SCHEMA_VERSION` and the
  key table from it; the site vendors a byte-identical copy and GENERATES
  `lib/schema.generated.ts` from it. Nothing about the shape of this data is
  hand-written twice any more.
  - **Making it executable found three lies the same afternoon**, all the same
    shape - a field typed non-null that is null in real data: `identity.name`
    (already a live 500), `RosterEntry.slug` (null in 63.5% of roster rows,
    never fired because the site dereferences it behind `has_page`) and
    `seasons[].teams[]` (null for one 1999 player). Checked against the real
    export: 22,927 files at 2.11 ms each.
  - **Objects are closed (`additionalProperties: false`) on purpose.** An
    ADDITIVE field fails the export until the contract is updated in the same
    commit, which is what regenerates the site's types. `headshot_url` was
    additive and went unannounced under the old arrangement.
  - Gates: `npm run check` fails on stale generated types; the web repo's
    `contract-in-sync` job curls the canonical file from this (public) repo and
    diffs the vendored copy; this repo's `ci.yml` runs the FULL suite.
- **CI runs the WHOLE suite, and the local numpy crash was never a reason not
  to.** `.github/workflows/ci.yml` is this repo's first workflow (2026-09-15).
  - **No count is pinned here on purpose.** This file said "751 tests" for two
    days while three tracks added to the suite; a total that every track
    invalidates weekly is a stale figure by construction, and re-typing today's
    number only restarts the clock. Run it if you need the number. Last
    measured 2026-09-17: 993 passed, 7 skipped, ~32s.
  - **Every skip states why, and there are now TWO skip conditions, not one.**
    This file previously called `LOGGER_DB is None` "this suite's only skip
    convention"; that stopped being true when track F landed
    `tests/test_analytics_survey.py`, which skips 5 on `no scanned
    analytics.db`. The convention that actually holds is the weaker one: a skip
    names the absent resource in its reason, so an absence never reads as a
    pass. Both current conditions are environment-absence, neither weakens an
    assertion.
  - **The dev box's default interpreter cannot run the suite; the suite is
    fine.** numpy there is a MINGW-W64 build on Python 3.14 that takes an
    access violation on import under pytest, killing 11 modules outright. That
    is one bad install. **A clean 3.12 venv on the same machine runs the whole
    suite in well under a minute** - `py -3.12 -m venv`,
    `pip install -r requirements.txt pytest`. Do not conclude from a crash on
    the default interpreter that anything is broken.
  - **IT IS NOT ONLY THE SUITE, AND IT DOES NOT ONLY FAIL ON IMPORT. It
    SEGFAULTS A PRODUCTION JOB AND TRUNCATES A PUBLISH.** Measured 2026-09-17:
    `python -m jobs.export_web` on the default interpreter dies with
    **exit 139, no Python exception and no traceback**, inside
    `build_research()` - the first part that makes numpy actually compute
    rather than merely import. `import core.distributions` succeeds there,
    which proves nothing; the same call under a 3.12 venv returns in 2.1s.
  - **The damage is what makes this severe: A TRUNCATED EXPORT LEAVES NO
    FILESYSTEM EVIDENCE.** `export()` runs market -> players -> teams ->
    research -> manifest, writing as it goes, and `write_if_changed` compares
    through `_canonical()`, WHICH STRIPS `generated_at`. So a part that never
    ran and a part that ran and produced identical content look the same on
    disk - same bytes, same mtime, nothing to notice. The crash left 4,856
    valid, contract-clean player and team files and the uploader pushed 4,910
    correct keys to R2 and reported success.
    - **Corrected, because the first diagnosis was wrong and the wrong reason
      is instructive.** The stale manifest mtime was read as proof the export
      had stopped early; a fully successful 3.12 re-run then reported
      `players [0,0] teams [0,0] research [0,0] manifest [0,0]` and wrote
      nothing at all. An untouched manifest is what a HEALTHY run produces too,
      so it was never evidence. The only real signals were the missing summary
      and the exit code - and the exit code had been masked by a pipe.
    - That is the argument for the preflight: there is no after-the-fact check
      that can distinguish a truncated export from a clean one, so the refusal
      has to happen before the first write.
  - **Run the export from the 3.12 venv, never the default interpreter**, and
    see the preflight guard in `jobs/export_web`: a job that can corrupt a
    published tree must refuse at the start rather than die in the middle.
  - **To reproduce CI locally, clone HEAD to a temp directory and run that venv
    against the clone.** The working tree has `.env` and a configured store, and
    it HIDES the two environment-dependent failures - the same shape as the
    Cloudflare `public/` ENOENT, where the working tree had a file git did not
    track.
  - **numpy and scipy were never in `requirements.txt`** despite
    `core/distributions.py` importing both. Nothing had ever installed from that
    file, because no CI existed; a fresh checkout could not import the model.
    Widening CI is what found it.
  - **The site stays lenient where the producer is strict**, deliberately:
    `validateEnvelope` checks the envelope and top-level keys only, so a file
    written before a field existed still renders, while the same file would be
    refused at export. Do not "fix" one to match the other.
- **Two export filters, and both print a count on every run** - zero included,
  because a number that reads 0 most weeks is what makes the week it reads 1
  visible. Both publish their ids in the manifest's `unresolved_ids`, so an
  exclusion is visible on the site rather than being a silent filter.
  - **No resolvable name** (no `player_xwalk` row, no `player_name` on any stat
    row): excluded from the export entirely, and never added to the permanent
    slug registry. Currently 1 of 3,971 - `00-0005532`, three 1999 rows for NO.
  - **A period row with no team**: dropped from that season's `teams` display
    list only; the period row keeps its null `team`, which is the honest
    record. Currently 1 row of 478,812.
- **`wrangler deploy --dry-run` output.** "Read N files" counts directories
  too: 16,113 entries against 12,077 real files.
- **Weekly refresh.** The task `CalibratedSports Weekly Refresh` runs
  `python -m jobs.weekly_refresh` at 09:00 Tue/Wed/Thu. It needs the PC on and
  the user logged on. It uploads to R2 and commits only `web/slugs`; it never
  pushes the site, so a data refresh cannot deploy code.

**v1 scope.** Team and player pages, and market-derived fantasy distributions.
**No login, paywall, betting recommendations or "best bets" - by decision, not
omission.** Brief 021 showed the model forecasts worse than the market, and
brief 023 confirmed it against the book close in every season.

- **The architecture is `calibratedsports-web/docs/site-architecture.md`.** It is
  worked in order: §1 contracts, §2 edge rendering, then §3 data system. §3
  (components tables, custom scoring, client-side distributions, incremental
  export, content-hash keys) is deliberately NOT started.
- **The contract is `docs/web-schema.md`, currently schema_version 2.**
  - **R2 was brought forward from §3:** the `calibrated-sports-site` bucket,
    separate from the logger's `calibrated-sports-raw`. The Worker reads it
    through the `SITE_DATA` binding, and browsers read the same keys at the
    same-origin `/data/{key}`. The bucket is not public.
  - **Keys are sport-first:** `sports.json`, `{sport}/manifest.json`,
    `{sport}/players/index.json`, `{sport}/players/{id}/summary.json` plus
    `{sport}/players/{id}/{season}.json`, `{sport}/teams/{slug}.json`,
    `{sport}/market/{id}/{season}-{index}.json`, `research/*`.
  - **`stat_definitions` and `scoring_presets` live ONLY in the sport
    manifest.** `period_type` replaces a hardcoded week.
  - **No fantasy points are stored.** The site scores components with one
    function for every preset.
  - Every file carries `schema_version`, `generated_at`, `kind` and `sport`; the
    site renders an explicit "data format changed" state on any mismatch.
  - **Config has no defaults:** `WEB_EXPORT_DIR`, `WEB_R2_BUCKET`,
    `WEB_R2_ACCESS_KEY_ID`, `WEB_R2_SECRET_ACCESS_KEY`, `WEB_SITE_URL`. The export
    refuses rather than guessing a path.
  - **The real v2 export is 22,927 keys, 94.7 MB,** including 18,907 season
    files. That alone is over the 20,000-asset cap, which is why R2 could not
    wait for §3.
- **Slugs are assigned once and recorded in `web/slugs/{sport}.json`,** which is
  committed and append-only.
  - When a registry is first seeded, the namesake with the most regular-season
    career games gets the bare slug. A later arrival only gets a bare slug if
    it is free.
  - The first v2 rule, "earliest first_season wins", was replaced before any URL
    was published: it gave `adrian-peterson` to the 2002 Bears back instead of
    the 184-game Hall of Famer.
  - `weekly_refresh` commits `web/slugs` (that pathspec only) whenever the
    export appends.
- **`player_xwalk` and `player_alias` now carry `sport`** (`TEXT NOT NULL DEFAULT
  'nfl'`). Invariant 7 had never been applied to them. Their primary keys stay
  sport-less until a second sport is ingested.
- **Only the manifest is read at build.**
  - Player and team routes are generated from `manifest.json` as real,
    indexable URLs.
  - Their HTML is a shell; all other data is fetched at runtime.
  - A data refresh needs no code change. A rebuild matters only when a player is
    added.
- **The file budget sets player scope.** Cloudflare caps a free site at 20,000
  files. That is 20,000 static assets on Workers, the same number as Pages'
  file cap, at 25 MiB each.
  - A static player route costs three files (`index.html`, `index.txt` and its
    JSON).
  - So v1 player pages cover only players with offensive usage: 3,971, for a
    build of ~12,100 files.
  - Defenders appear on team pages. Widening scope is an export filter plus the
    paid plan (100,000 files), not a redesign.
- **The weekly refresh is `python -m jobs.weekly_refresh` (v2)**, logged to
  `storage_path("logs", "weekly_refresh.log")`.
  - Steps: nflverse ingest, `map_markets --venue kalshi`, export, commit the slug
    registry if it changed, upload changed keys to R2, then check the live
    `/data/nfl/manifest.json`.
  - **It builds nothing and commits no data.**
  - A no-change export takes ~47s and writes 0 keys.
  - Until the bucket token exists, the upload logs "not configured" and exits
    0.
  - The scheduled task is DISABLED while the v2 site lands, because the v1 job
    would have written `public/data` and pushed. Re-enable it once §2 is live.
  - **`jobs/map_markets.py` must run before the market export.** Nothing else
    maps new Kalshi markets: week-2 props sat at 0 of 89 mapped until it ran.
  - **When nflverse is late,** the export still runs,
    `manifest.current.stale` names the missing week, the log WARNs, and the next
    scheduled run picks the data up.
  - Accepted debt: the PC must be on.
- **Market distributions carry their basis on every component.**
  - Receptions and rush attempts are MARKET (Kalshi ladders).
  - Yards are DERIVED.
  - TDs are ANCHORED.
  - They also carry an honest validation note: the method was validated on
    sportsbook ladders and the Kalshi arm never was.
- **Fantasy points omit fumbles lost and two-point conversions,** which
  `nfl_player_week` does not project. Against nflverse's own PPR the median
  difference is 0.00 and the p99 is 2.00 over 193,354 player-games.
- **`/build.json` names the commit a deployment was built from.** It reads
  `WORKERS_CI_COMMIT_SHA` on Workers Builds, falls back to `CF_PAGES_COMMIT_SHA`,
  then to `git rev-parse`. Check it after a push; a local `npm run build` passing
  is not evidence the deploy works.
- **Git history grows with the data.** The site repo commits ~132 MB of JSON, and
  roughly 30 MB changes weekly in season. Moving the data out of git is a later
  decision.

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
# Standing rules — Calibrated Sports

Append to `CLAUDE.md` in **both** repos. These were established by measurement or by error during the
W04/W05 build cycle. They apply without being restated, so proceed on them rather than asking.

## Evidence

- **A FINDING IS SCOPED TO WHAT WAS MEASURED.** A NO-GO measured on one venue is a fact about
  THAT VENUE; restating it as a fact about the sport is a generalisation nobody performed - and it
  is invisible, because the original measurement was correct and stays correct. Worked case: "VERDICT:
  NO-GO on college football" was measured on Kalshi over 56 hours, held for two weeks as a fact about
  college football, and was false of the market the whole time - US books list 26 player-prop keys
  (P1, 2026-09-19). The check is mechanical: name the venue, the season, the window and the model in
  the sentence itself, so the scope travels with the claim when someone quotes it.
- **Measure, don't estimate.** Any number asserted in a brief or in chat is unverified until a script
  reproduces it. Sizes, rates, counts, coverage — measure before acting. Briefs have been wrong about
  fee multiples, depth cost, retention windows, coverage limits and table budgets; every one was caught
  by measuring.
- **Verify the verifier.** When a check fails, establish which side is wrong before editing anything.
  Do not change working code to satisfy a broken assertion. Four failures in one round were the
  checker, not the page.
- **Grep case-INSENSITIVELY first; narrow only once you have hits.** `grep -i` to establish whether
  the thing exists at all, then tighten. Not a principle — a keystroke, because the principle already
  exists three rows down in the proxy table and did not fire three times in one session:
  - `removing a check` missed the row that reads "**REMOVING** a check's return value";
  - `Week 0` missed F01's week-0 finding, already tabled under different wording;
  - `guard returns the statement` missed the same rule living in `DECISIONS.md`.

  Each one produced a **wrong claim in a report** — "track C filed this and I never did it" — before a
  case-insensitive rerun corrected it. The failure is always in the same direction: a pattern that does
  not match and a thing that does not exist are indistinguishable, and they lead to opposite actions.
  Absence is the expensive answer to get wrong, so buy it with the cheap search first.
- **Exit 0 is not a result.** A script that reports nothing and succeeds is a FAILED script. Assert on
  the SHAPE of what you read before trusting what you print. Three of these in one night, each
  exiting clean and each telling me nothing: a `--tests-out` reader that printed `n/a` for all 28
  intervals because it assumed `est`/`lo`/`hi` key names the writer does not use; `curl -o /tmp/…`
  followed by a read of a path Git Bash had not written; `find -newermt "03:40"` against UTC
  timestamps on a machine in EDT, matching zero files four hours early. Check the count you got
  against the count you expected, and fail loudly when it is zero.
- **Assert correctness, not presence.** A test that a tab renders is worthless; assert its destination
  resolves. Use `satisfies`, not `as`. The gate typechecks everything, not the import graph.
- **A guard returns the statement it approved, never a bare boolean**, and the result is carried
  rather than discarded (track C, A-C1; applies to guards already built, not only new ones).
  `cfb.pbp_scope.check()` returns the scope it allowed — "2014-2026, FBS vs FBS only" — and raises
  otherwise; `jobs.ingest_cfb.audit()` returns an `AuditReport` whose `.statement` is one log line and
  whose `.clean` is the verdict. **The report REFUSES truth-testing** — `__bool__` raises — because
  supporting it reinstates the `if audit(conn):` that drops the statement, while merely omitting it
  makes every instance truthy and turns `assert audit(store)` into an assertion about nothing. See the
  proxy table's `__bool__` row for what that costs.
- **An assertion about a DEFINITION says nothing about its CALL SITES.** Extracting a duplicated rule
  deleted two functions and left two live calls to them in `research/bookvbook.py`, behind a suite of
  800 passing tests. The guard asserted the definition existed exactly once — but the risk was in the
  references, and an unresolved name is a runtime `NameError`, so `py_compile` passes and so does any
  test that never reaches the line. When you remove or rename something, assert on the callers, by
  AST rather than by grep: a docstring explaining the removal quotes the old name by design, and a
  text search cannot tell that from a live call.
- **Any assertion that can pass against a placeholder is asserting nothing.** Where a loading state
  exists, a second test asserts the subject is not it.
- **Route checks prove nothing about client-rendered content.** curl sees `Loading…`. Anything below
  the fold needs a render test.
- **A guard whose first run is all noise gets switched off.** A guard is only worth what it costs to
  keep, so the false-positive rate is part of whether it works, not a detail of it. `test_store_guards`
  matched any `.connect(...)` on its first run and flagged two tests calling
  `sqlite3.connect(tmp_path / "other.db")` — a temp file, no store in sight. The fix was to match the
  MODULE (`paths.connect`) rather than the method name. Precision first; a guard nobody trusts is
  deleted or ignored within the week, and then the class it was written for is unguarded AND believed
  to be covered.

### A PROXY IS NOT THE THING

**The class, and the check.** Name the thing under test, then confirm your assertion can only be
satisfied by *that thing* and not by a stand-in that resembles it. If some other state could produce
the same green, the assertion is measuring the stand-in.

Six incidents in one session, all the same shape. They are cross-referenced, not re-litigated:

| The stand-in | The thing | Where |
|---|---|---|
| `curl` | what the Worker serves a browser | a 403 came from Cloudflare's edge on the client's UA; the app returns only 404 |
| exit 0 | the result | a `--tests-out` reader printed `n/a` for 28 intervals on wrong key names, and exited clean |
| a `FunctionDef` exists once | nothing CALLS the deleted name | two live calls survived 800 green tests; an unresolved name is a runtime error |
| three `Loading…` placeholders | three rendered values | they compared equal to each other and to nothing real |
| `os.getenv("LOGGER_DB") is not None` | a store that resolves predictions | a throwaway path is set and empty: the skip misses and the test fails on absent data |
| `changed: 12` | files the export modified | it counts keys differing from *upload state*, not from the previous build |
| prose describing a manual check | a committed test | "a separate process was refused" in a handoff was a check run by hand during development; the committed test is nested `with` blocks in one process |
| `pytest -k "lock"` selecting nothing | the tests actually running | "42 deselected" reads like a pass; the filter matched no test name and verified nothing. Only the unfiltered run did |
| a registry record matching a published figure BY VALUE | that record's identity | R11's pointer was nearly resolved by scanning for `est ≈ 1.8125`; a second record carrying the same number is indistinguishable from the right one, and the scan would silently start returning it. Keyed on `(registry, family, name)` instead — verified unique at 1,171 records across all 8 registries |
| a guard verified BEFORE it was active | the guard | "no renormalisation" was checked while `.gitattributes` was still untracked — attributes apply only once git tracks the file, so the check ran at the one moment the rule could not fire and a clean `git status` proved nothing |
| **ANYTHING BETWEEN THE COMMAND AND `$?`** | **the command's exit code** | `$?` holds the status of the LAST thing that ran, which is rarely the thing you meant. Three instances, each a different carrier: (1) `cmd \| tail` reports `tail`'s status, so a SEGFAULTING export read as `exit 0` — proven, not assumed: `python -c "sys.exit(7)" \| tail` gives `$?=0` and `set -o pipefail` gives 7; (2) the same pipe inside the command written to DIAGNOSE the first, which is how thoroughly a masked exit code hides itself; (3) a bare `echo` interposed between a subshell and `echo "full exit=$?"`, which reported `full exit=0` while pytest had just failed 2 tests — caught only because the same script also grepped `FAILED`, so the wrong line and the right one were both on screen. The generalisation the first two rows missed: it is not about pipes. Capture into a variable on the very next line (`RC=$?`), or use `PIPESTATUS[0]`, and never let a convenience `echo` sit in between. `ci.yml` and `push_protocol.sh` do this correctly; ad-hoc diagnostics are where it keeps recurring |
| **a stale mtime** | **a part of the job that never ran** | a manifest 8 hours older than its siblings was read as proof the export had stopped early. `write_if_changed` compares through `_canonical()`, which strips `generated_at`, so a part that never ran is byte-identical to one that ran and produced the same content — and a fully successful re-run duly reported `manifest [0,0]`. The mtime could not distinguish the two states, so it was never evidence for either |
| **a total that reconciles** | **a total that is not double-counted** | NGS `ngs_receiving` carries a **week 0 row that IS the season total**: Ja'Marr Chase 2025 reads 185 targets at week 0 and weeks 1+ sum to exactly 185. Aggregate over all weeks and every figure doubles — and it is the SILENT-ZERO CLASS'S COUSIN, worse in one way. A zero cliff at least produces a number a reader might find odd; a silent double survives every sanity check anyone would apply, because the ratios, the rankings, the shares and the correlations are all unchanged and only the magnitudes move, by a factor that looks like nothing in particular. The reconciliation that catches it is exactly the one that looks redundant: does the part sum to the whole, or IS the whole sitting in the parts |
| **a non-NULL check** | **a value that is present** | NaN is not NULL. polars `count()` counts it and `fill_null(0) != 0` is TRUE for it, so a column of pure NaN scores as fully populated and fully informative. `stats_player_week.target_share` is targets over ZERO targets for 2003-2008 - NaN on 17,355 of 17,355 rows in 2005 - and the coverage survey read those six seasons as 99.7% informative and flagged the OTHER TWENTY-TWO as the anomaly. **A detector that inverts is worse than one that misses**: a miss leaves you where you started, an inversion hands you the opposite of the truth with a number attached. Count nan separately (`nan_n`) so it can never be folded into a category that hides it |
| **an interval that is valid on its own** | **an interval a reader may compare with the one beside it** | sharing bootstrap draws across subjects (common random numbers) changes no centre and no width, so each interval stays valid - and it correlates the Monte Carlo error BETWEEN subjects, which is exactly what someone reading two of them side by side consumes. Measured against the per-game correlation between the pair: variance of the eyeballed gap runs 1.571 at rho -0.9, 1.026 at 0, 0.952 at +0.6 relative to independent draws. Above 1 is anti-conservative. Teammates share a denominator and measure rho = -0.215, so the site's commonest side-by-side is the bad half. **Whatever readers will compare must be drawn independently**, and "nothing publishes a contrast" is a claim about the code, not about the product |
| **a command that returned** | **the work it actually did** | (track C, A-C3) `git checkout <path>` CANNOT restore a file git does not track: mutation-testing a guard in a new file, the restore silently did nothing and left mutated code on disk, surfacing only because someone grepped for the mutation instead of trusting the exit code. And piping a long scan through `head` truncates the WORK, not the output — a 36-file rescan died on SIGPIPE after four files and the next command read the half-built table and printed a clean, plausible "0 silent-zero runs". Redirect to a file and tail it; verify a restore by looking at the file |
| **an object with no `__bool__`** | **a check that can fail** | (track C, A-C4) a guard returning a bare boolean is weak — `if check(x):` discards what it learned — but REMOVING the return value is worse: an object with no `__bool__` and no `__len__` is TRUTHY, so every `assert check(x)` keeps passing and now asserts nothing, including when the check fails. Seven `assert ingest_cfb.audit(store)` sites would have gone green and vacuous with no diff to notice. The strong form REFUSES truth-testing (`__bool__` raises, naming `.clean` and `.statement`) so every stale call site fails loudly the moment the return type changes. Generalised: when a return value stops meaning what call sites assume, make the old usage RAISE, never merely stop being supported |
| **`$(git rev-parse origin/main)` read AFTER `git fetch`** | **whether the remote moved** | a freshness guard compared the post-fetch SHA against itself and could only ever print "unchanged". Capture it BEFORE the fetch. Written, and relied on, inside the very protocol step it was meant to protect |
| **a constant's value** | **the value in use** | (track C) a default argument is evaluated ONCE, at import, so `def plan_forward(..., cap=oddsapi.FORWARD_WEEKLY_CAP)` froze the cap at import: editing the constant changed what you READ and not what RAN, and no test could lower it by setting the attribute. Nothing observable distinguishes the two - the constant says 75, the function uses 45, and both are "the cap". Swept the repo with `research/default_arg_constants.py`: **14 cross-module and 83 same-module** constants sit in default-argument position. Two of the 14 were spend caps (`cfbd.MAX_REQUESTS_PER_RUN`, `oddsapi.P1_APPROVED_CREDITS`), both saved only by a second read inside the function; two are enum members and cannot drift; ten are research settings modules in frozen scripts. The fix is `arg=None` plus `arg = CONST if arg is None else arg`. **Same-module defaults bind identically** - "the constant and the default move in one diff" is not a defence, because a constant at the top of a long file and a default far below it are in one diff only if someone edits both; what differs is the odds of noticing, not the failure. So the guard has two parts: an allowlist for the cross-module 12 (each with a reason), and a CATEGORICAL rule over all 97 - a budget-shaped constant may not sit in a default argument in any module that CAN MAKE REQUESTS, where that capability is computed (the file imports an HTTP client, or imports a module that does) rather than listed, so a script that only reads a database is exempt by construction. The categorical half is the part that survives the allowlist going stale, and it is shown firing on a planted spender |

The tell is always the same: **the check passed and told me nothing.** A result that cannot
distinguish success from a plausible-looking absence has not been verified. Two habits close most of
it — compare the count you got against the count you expected and fail loudly at zero, and make every
assertion discriminate (show it returning the *other* answer on the other input) before trusting it.

## Data and figures

- **No unsourced figures.** Every number on the site is query-derived or visibly marked placeholder.
- **A figure the page itself contradicts is worse than a blank.** Suppress it; don't mark it.
- **Data leads code for ADDITIVE changes; code leads data for SUBTRACTIVE ones.** Adding a field
  cannot break an old reader — it ignores what it does not know — so publish the data, verify it is
  served, then ship the reader. Removing a field can, and does: the deployed reader is still running
  the old rule on the new data.
  - Learned by nearly getting it backwards, 2026-09-18. The per-row key-set change REMOVES keys from
    68% of stat slots, and the deployed site renders an absent key as "not recorded" — so publishing
    ahead of the deploy would have put **"Pass Yds — not recorded"** on receiver pages and silently
    dropped the 2003–08 targets columns. Both are false statements, produced by a correct change
    shipped in the wrong order.
  - **The gate is the DEPLOY, not the merge.** "The consumer has the code" and "the consumer is
    running the code" are different facts, and only the second one protects a reader. Check what is
    served, not what is pushed.
- **AN EXPORT PUBLISHES EVERYTHING THAT CHANGED. Two unrelated changes in one working tree are one
  publish, whether or not they belong together.** There is no "ship this fix but not that one" —
  `export()` writes every key whose content moved, and the uploader sends every key that differs.
  - 2026-09-18, a near-miss worth recording as luck rather than filing as a success. The prop-history
    interval correction and the per-row key-set change sat in one tree. They separated only because
    `prop_history` lives on `summary.json` and the key set on `{season}.json`, so the upload after the
    interval fix touched summaries alone. Nothing about the process arranged that; a change touching
    both files would have shipped the 68% key churn to a deployed reader that could not render it.
  - So the sequencing decision is made BEFORE the edit, not at publish time: if a correction must
    reach readers ahead of a larger change, the larger change does not enter the tree until the
    correction is out. Asking "can I split these at publish time" is already too late, and splitting
    by hand is how a partial export lands.
- **"Verified served" means AT LEAST FOUR samples, fetched over HTTP, compared against the local
  file.** One sample is a check that can pass while the claim is false; four is a claim.
  - Both halves matter. Fetch over HTTP because the local export directory is not what the reader
    gets — R2, the Worker and the edge all sit between. Compare against the local file because a 200
    with plausible JSON proves the route works, not that the right bytes are in the bucket.
  - This was reported as "verified served" off a single player twice on 2026-09-18. Both times the
    claim happened to be true, which is worse than being caught: a habit that survives by luck gets
    repeated.
- **Snapshot before any write that can move a published figure — everything the operation could
  touch, not only what you expect to move.** One `.tar` of the export directory and a dump of the
  table being written costs seconds and preserves the before/after permanently. The 2026-09-17
  settlement run captured only the three `research/*.json` files, so when brief 023's MIDDLES turned
  out to be settlement-sensitive there was no baseline left to diff against — and the surgical
  rollback was gone too, because the marker recorded `max(settled_ts)` while the writer's
  `ON CONFLICT` refreshed `settled_ts` on every row. A rollback marker records the KEY SET being
  added, never a timestamp the writer is free to rewrite.
- **Never approximate a historical field from a current one.** Backdating today's team onto past rows
  renders a wrong career while looking correct.
- **Components, not derived totals.** Store the parts; compute the aggregate at read time.
- **ABSENCE FAILS TOWARD KEEPING DATA. An empty or missing "wanted" set means "no information", never
  "nothing is wanted".** Any rule of the form *it is not in my list, so delete it* is a
  delete-everything instruction the moment the list arrives empty — and a list arrives empty for
  ordinary reasons: a part not in scope, a builder that does not exist, a caller that never said.
  - Two layers, same defect. `sync_keys(dest, wanted, prefixes)` deletes local keys under `prefixes`
    that the builder did not produce, so owning a prefix you do not fill deletes it all on an ordinary
    Tuesday — which is why track A owns nothing under `analytics/` and asserts it categorically
    (`tests/test_prefix_ownership.py`) rather than adding the call track F asked for.
    `upload()` did the same thing remotely: `set(state) - set(local)`, where `local_keys()` walks
    `WEB_EXPORT_DIR` alone, so every key another producer publishes computed as "removed". Worse
    remotely — there is no local copy to restore from.
  - The fix is a DECLARATION, passed and never persisted: the run that produced the tree names the
    prefixes it rebuilt, and deletion is scoped to those. Persisting it would let a stale copy
    authorise deletions for a run that never happened, which is this same defect wearing a fresh coat.
  - **Distinguish "nobody said" from "said nothing".** `None` and `[]` both withhold and mean
    different things, and a reader looking at a withheld count needs to know which. Report them apart.
  - **A safe default nobody teaches is a permanent leak.** Withholding is correct and silent, so the
    caller that must learn to declare is landed in the SAME unit as the default — otherwise every run
    withholds forever and nothing ever says so.
- **A ROW THAT COMES BACK WITH FEWER FIELDS POPULATED IS A SILENT DELETE.** What the newest release
  does not say, a whole-row write unsays — permanently, and nothing records that you used to know it.
  - **The mechanism is narrower than "a wholesale replace", and that wrong description hides it.**
    `store.replace_rows` is `INSERT OR REPLACE` row by row and **deletes nothing**, so a row the feed
    stops sending survives untouched: absence of a ROW is safe. The damage is a row still present with
    a column gone null — the write replaces the whole row, so a field that merely went quiet destroys
    the stored value. Grepping for `DELETE` finds nothing and the row count never moves.
  - Measured 2026-09-19 on `player_xwalk`, the identity table every join keys on: the 09-19 nflverse
    players release omitted `pfr_id` for 75 players and `espn_id` for 38 that the 09-17 release
    carried, and all 113 values were erased. The 09-14 and 09-18 parquets are ~11 KB smaller than
    their neighbours, so omission recurs — this is a property of the feed, not one bad day.
  - **The consequence CLASS is a silent drop; the measured impact of THIS incident was zero. Both
    halves are the finding.** `pfr_id` is the join key into `nfl_snap_counts`, and
    `jobs/settle_outcomes.py`, `models/features.py`, `research/shrinkage.py` and
    `research/walkforward.py` all join `player_xwalk` on it (verified by grep, not recalled), so a
    null there removes a player as a shortfall nobody counts rather than as a visible gap — the same
    shape as the missing-stat-row defect that inflated the realized over rate. But every one of the
    80 affected players has `last_season` **2010**, and `nfl_snap_counts` starts in 2013, so not one
    of the 113 lost values could ever have matched a snap row. Measured either side of the recovery:
    232 orphan rows before, 232 after, **0 moved**.
  - **The 232 figure was MISATTRIBUTED, and that is the transferable lesson.** 232 of 329,365 snap
    rows (0.07%) whose `pfr_player_id` matches no crosswalk row is a real, pre-existing population
    with nothing to do with this defect. It was measured earlier in the same session and then
    attached to this incident because both were about a null `pfr_id`. A true mechanism and a true
    number, welded together without checking that the number described the mechanism — *agreement
    between two unchecked arguments*, one layer below where that rule usually fires, and it reached a
    pushed commit (`3b9e2f5`) before measurement caught it. Recovery was still right: the values are
    data we held and unsaid, and the next release can strip a cohort that does matter.
  - The fix is `store.upsert_preserving(table, cols, rows, conflict, preserve)`: a named column falls
    back to the stored value when the incoming one is NULL, every other column takes the new value. A
    RESTATEMENT must still win — correcting a fact is invariant 6's whole point — and only silence is
    refused. The idiom already existed in `record_health` and `upsert_outcomes`; the identity table
    never got it. Guarded by `tests/test_crosswalk_merge.py`, which also asserts the *old* behaviour
    nulled the value, so the preserve tests are known to discriminate.
  - **Recovery is a REPLAY, never a hand-written UPDATE.** Invariant 2 means the archive still holds
    the value, so re-running the ordinary derivation over the older release restores it with its
    provenance intact. Replay oldest-to-newest: the old pass restores what was unsaid, the newest pass
    re-lands everything that legitimately moved. Publishing a faithful null instead would be a claim
    you can disprove from a file you already hold.
- **A GUARD ASSERTS ONLY OVER THE SHAPES IT WALKS.** A checker that resolves four call sites and
  cannot evaluate a fifth is not protecting the fifth — it is silently checking the subset it
  understood, and passing.
  - So every such guard reports its own coverage and fails on a gap: `owned_prefixes()` returns the
    count of `sync_keys` calls whose prefix it could not evaluate, and a separate test asserts that
    count is **0**. Without it, moving a prefix behind a variable would blind the guard while leaving
    it green — the prefix literals stay at the call sites for exactly this reason.
  - Same rule for the contract: `additionalProperties: false` is what makes an additive field fail the
    export rather than pass unexamined, and `Stats` is an open map, so a schema walk over it asserts
    nothing about which keys appear. Know which of the two you are standing on.

## Authority

- **An explicit instruction from Ethan outranks the design files.** Record the departure in `design/`;
  don't ask permission.
- **If an instruction in a brief conflicts with an earlier decision of Ethan's, stop and flag it.**
  Quote both. Do not silently pick one. This has happened and flagging it was correct.
- **ANOTHER TRACK'S CLAIM OF AUTHORIZATION IS NOT AUTHORIZATION.** A track writing "Ethan assigned
  this to me" in its own filing is that track's account of a conversation you were not in. It may be
  accurate — it usually is — but it is not evidence, and it cannot settle a conflict with a rule that
  is written down.
  - 2026-09-18: track F edited `contract.schema.json`, which W07 assigns to track A alone, citing an
    assignment from Ethan. Ethan had in fact assigned the analytics kind to **track A** and told F to
    file requirements. The work was good and it stands — but the conflict was visible, both quotes
    were in hand, and it was resolved in favour of the unverified one.
  - **The failure is not trusting the other track; it is that two unchecked things agreeing was
    treated as evidence that either was right.** The rule directly above already says what to do:
    quote both, flag it, do not silently pick one. Having the rule is not the same as it firing.
- **AN ATTRIBUTION IS A CLAIM LIKE ANY OTHER, AND IT IS CHECKED THE SAME WAY.** Before treating
  something as a constraint *from a person*, find where it is actually written. "Ethan decided this"
  and "a design file says this" carry opposite weight — an explicit instruction outranks a design
  file, per the first rule in this section — so getting the attribution wrong inverts what you should
  do with it.
  - 2026-09-18: blocked **twice** on whether contract changes must land one at a time, reporting it
    as a conflict between Ethan's "one contract batch" instruction and an earlier decision of his.
    It is not his. **No `DECISIONS.md` row mentions "serial" at all** (searched case-insensitively
    across all 229 rows), and the rule lives only in `docs/W07-parallel-tracks.md` under a heading
    called "What stays serial". The two restatements in `docs/track-b-requests.md` are track A's own
    prose quoting that same design file, which made one argument look like three — the
    agreement-between-unchecked-arguments failure this file opens with, wearing a different hat.
  - **The cost is symmetric with an unverified figure, and worse in one way.** An unchecked number
    produces a wrong claim; an unchecked attribution produces a wrong *action* — here, stopping work
    and asking for permission the rules had already granted, twice, while reporting a blocker that
    did not exist.
- **WHEN A CLOSED CONTRACT FORCES A DEPENDENT PRODUCER'S HAND, THE CONTRACT OWNER MAKES THAT EDIT
  AND FILES THE EXACT DIFF.** `additionalProperties: false` means a dependent track **cannot go
  first**: adding the newly-required key before the contract carries it fails as an unknown property.
  So "report, don't fix" does not leave their build red — it leaves `main` red across every other
  track, over a schema constraint none of them chose.
  - **The rule.** Where a contract change forces a dependent producer's file or manifest shape to
    change in the SAME commit, the contract owner makes that minimal edit in the dependent track's
    file and files **the exact diff, not a description**, so the owning track reviews what landed
    rather than discovering it from a failing build.
  - **The boundary is the forced lines and nothing else.** A test name left stale beside the edit, a
    comment the change made wrong, a decision about published URLs — all of it stays with the owning
    track. If a third thing needs changing, stop and report it rather than extending the exception.
  - **This is a structural exception with a stated boundary, not standing permission** to edit
    another track's files. It became a rule because it was granted case by case twice in one day for
    the identical structural reason — `market_definitions`, then `TeamEntry` and `memberships` — and
    will be required by every sport added after this one. A judgement call that recurs on a schedule
    is a protocol step that has not been written down yet.
- **Report anything changed that wasn't asked for, and why** — in every report, without being asked.

## Repo hygiene

- **In a public repo, name paths.** Never `git add -A`. There, staging is a publishing decision.
- **Design files and mock-data artefacts stay out of the public repo** unless committed deliberately
  with a README stating the numbers are mock.
- **Fix guards at the cause.** A guard that matches prose in generated files will fire again on the
  next vendor name.
- **The commit sequence, in full. Three tracks write this repo, so the remote moves while you work.**

      PRE=$(git rev-parse origin/main)   # BEFORE the fetch - see the warning below
      git fetch
      git rebase origin/main      # BEFORE the work is staged, not after it is rejected
      <run the suite>             # against the rebased tree - that is what will be pushed
      git commit                  # named paths, never -A
      git fetch                   # again: the remote may have moved during the suite run
      # if origin/main != $PRE: rebase onto it AND RE-RUN THE SUITE before pushing.
      # A rebase is a new tree. The suite result you are holding belongs to the old one.
      git push

  **THE SECOND FETCH HAS A SECOND HALF, AND OMITTING IT IS HOW THIS WAS VIOLATED**
  (2026-09-17, by the agent that wrote this section). The remote moved to another
  track's commit between the suite run and the commit. The rebase was done - and then
  the push went out with **no suite run against the combined tree**. Rebasing is the easy
  half to remember because it is what git forces; re-running is the half nothing prompts
  for. A green suite is evidence about a specific tree, and a rebase replaces that tree.
  (Verified clean afterwards, 1022 passed - which is luck, not process.)

  **And the freshness check must capture the SHA BEFORE fetching.** The guard written to
  catch exactly the above read `BEFORE=$(git rev-parse origin/main)` *after* `git fetch`,
  so it compared the post-fetch value with itself and could only ever report "unchanged".
  It printed reassurance from a check structurally incapable of firing - the same class as
  the `.gitattributes` guard verified before it was active, in a guard written to enforce
  this very protocol.

  The second fetch is not redundant. The suite takes minutes and another track can land in them.
  A rebase attempted at push time is a rebase attempted with staged work in the way, which is where
  it fails — `cannot rebase: You have unstaged changes` — and where the temptation to force is.
  **Rebase, never force.** A push rejection is the protocol working; the answer is to replay onto what
  arrived, not to overwrite it. Where another track has an uncommitted file in the shared tree,
  `--autostash` carries it through untouched — do not `git checkout --` a file you did not write.

  **This sequence lives here and only here.** Track C asked whether to copy it into their own notes
  and Ethan's answer was no. One copy: the settlement rule existed twice and the copies disagreed on
  3,272 outcomes.

- **On a conflict in an append-only log, each track's row wins in its OWN row** (Ethan, 2026-09-18).
  `DECISIONS.md` collided on two same-day rows during a rebase: track A had refined the wording of
  their own row while track F had updated its own. Neither side is "theirs" or "ours" wholesale —
  taking either half entire would have silently reverted the other track's refinement to a stale
  copy, which is the same class as overwriting a file you did not write. Resolve row by row, and
  keep the version written by whoever owns the row. The same applies to CLAUDE.md, where an earlier
  rebase collapsed three rows upstream: keep their generalisation, append only what is genuinely
  new.

## Design constraints

- **Team colour is identity only** — chips, hairlines. Never a chart fill or row background.
- **The accent gradient is chrome only.** Never on a mark that encodes a number.
  `--grad-from: oklch(0.555 0.180 267.5)` → `--grad-to: oklch(0.631 0.122 226.9)`. White on the cyan
  end is ~3.0:1: display type only.
- **Cleared/missed is non-valenced diverging**, hue-guarded. Green/red reimports the valence the
  vocabulary exists to remove.
- **Empty states differ by form, not hue alone.**
- **`cleared` / `missed`, never win / loss.** In code, labels and copy.
- **No charting library.** Hand-drawn SVG. Works at 400px.
- **One register per page** — dense reference or editorial, per `site-design-direction.md`. The player
  page is the one hybrid and its seam is an explicit section break.

## Tests

- **No test writes outside its own fixture — and neither does a diagnostic.** Every test that touches
  the store pins it — `DB_PATH`, `STORAGE_DIR`, `WEB_EXPORT_DIR` — to a `tmp_path`, and a subprocess
  gets it by env, not by monkeypatch. Autouse the fixture rather than per-test: the failure mode is
  the test that forgets. The word *test* is not the boundary: a throwaway probe run at a prompt
  inherits the live config just as readily, and one such probe left
  `<STORAGE_DIR>/locks/selftest_redirector.lock` in the production store because
  `acquire(name)` resolves through `config.storage_path`. If it writes anywhere, pin it or point it
  at a scratch path.
  This is not hypothetical. Adding a `source_health` write to `weekly_refresh.run()` gave seven
  previously write-free tests a real `record_health` call against the LIVE database, and the
  production `weekly_refresh` row read `ok=0` while the job was healthy. The tests were harmless
  unpinned right up until the code under them started writing.
- **Pin the root, not the leaf.** `config.STORAGE_DIR` is computed once at import from `DB_PATH`, so
  pinning `DB_PATH` alone leaves `storage_path()` pointing at the real store.
- **An empty environment variable is a SET variable.** `os.getenv("LOGGER_DB") is None` is the skip
  condition; `LOGGER_DB=` defeats it and runs a test that needs a store without one. Use `env -u`.
- **Never run the full suite against the live store while the logger is running.** Clone HEAD to a
  temp directory, overlay the working-tree changes, and run there — the working tree has `.env` and a
  configured store, and it hides exactly the environment-dependent failures CI exists to catch.
- **`mode=ro` means "cannot write the database", NOT "touches nothing"** (c-04 found it; a-07
  measured it, `research/wal_readonly_probe.py`). On a WAL store a `mode=ro` open CREATES `-wal`/`-shm`
  when absent and leaves them after close (a read-only connection cannot checkpoint), and an open read
  transaction PINS the WAL so the logger's checkpoint cannot truncate it while you hold it. The main
  file's bytes do not change and no write can succeed — that part holds. `immutable=1` touches no
  file but ignores the WAL and reads a stale database. So: keep read-only sessions on the live store
  short, and never cite the absence of `-wal`/`-shm` as evidence nothing opened it.

## Reporting

Every report closes with: what was as described and what wasn't; what the real data cannot support,
flagged rather than faked; what changed unasked; and any decision that belongs to Ethan rather than to
the agent — stated as options with a recommendation, not as a question without one.

## CFB facts store (W07 track C)

- **CFB publishes stats and usage, never hit rates, prop history or settlement.**
  There is no appearance signal for a college player - no snaps anywhere free,
  `did_not_play` False on every row 2004-2026, starter flags only from 2025.
  The limitation is a row in `cfb_limitations`, not a footnote; an About page
  renders it from the store and quotes figures from `cfb_measurements`.
- **Store:** `<STORAGE_DIR>/cfb.db`; raw at `<STORAGE_DIR>/cfb/raw`, outside the
  logger's RAW_DIR. `python -m jobs.ingest_cfb --fetch --season 2026` is the
  weekly refresh; `--parse` and `--rebuild` replay the archive at zero requests;
  `--audit` checks manifest against disk. One instance at a time (OS lock).
- **Versioning is per row by ingestion time** (`valid_from_ts`/`valid_to_ts`).
  A raw copy is kept only when the CONTENT hash moves - upstream re-uploads
  every season many times a day, so `updated_at` means nothing.
- **Never ingest `espn_cfb_betting`** - it fills missing lines with spread 2.5 /
  total 55.5. Denied in `cfb/sources.py`. Historical lines: CFBD `/lines`.
- **2026 passing rows use two layouts in one file**: 466 of 534 carry
  (C/ATT, YDS, AVG, TD, INT) in `stat_1..stat_5`. Order verified (AVG = YDS/ATT
  on all 466; TD before INT by mean).
- **ESPN's box score has no targets.** Targets come from the usage file, and
  targets thrown to unidentified players (~5% a season 2004-2014, <0.5% from
  2015) are in `team_targets` and no player's row.
- Figures: `python -m research.cfb_sources_audit`.
- **CFBD (phase 2) goes through `jobs.ingest_cfb` only.** `--cfbd-status` (/info,
  unmetered), `--cfbd-lines 2013-2025` (1 request/season), `--cfbd-week 2026:3`
  (2 requests). Guards: /info pre-check, `CFBD_RESERVE` floor, 20-request run cap,
  no gameId/id/team URL can be built, every call in `cfbd_requests` with `origin`.
  The old `jobs/ingest_cfbd.py` writes to `cfb_probe.db` and backs the C01 record;
  do not use it for new spend.
- **CFBD lines carry no timestamps** - `spread` is the provider's last value, not a
  kickoff close; opening values absent on 79% of rows, moneylines on 80%; retail
  books only from 2018; provider names pass through ('DraftKings' and 'Draft Kings').
- **Provider names are canonical in `cfb_game_lines.provider`** (map in
  `cfb.cfbd_normalize.PROVIDER_CANONICAL`); the CFBD string is `provider_raw`.
  DraftKings arrives as two feeds; the fuller one wins per game. A new book spelled
  two ways refuses the file - add it to the map and `--rebuild`, never fuzzy-match.
- **Weekly CFB refresh:** `python -m jobs.ingest_cfb --fetch --season 2026
  --cfbd-week latest` (~2 CFBD calls). `CURRENT_SEASON` in `cfb/sources.py` and the
  `--season` argument both change once a year, before the 2027 season.
- **A build line in `logger.log` is not evidence of a start today.** Log lines carry
  no date. Date a line by counting midnight rollovers after it, and confirm a start
  against the process's CreationDate.
- **`python -m jobs.ingest_cfb --promote-probe`** promotes `cfb_probe.db` (read-only)
  into `cfb_exchange_markets` / `cfb_exchange_closes`, idempotently. Its manifest rows
  are `external:` and `--audit` checks they open read-only.
- **C01's name normaliser turns "St." into "state"**, so St. Thomas becomes
  "state thomas" and does not join. It is copied verbatim into
  `cfb/probe_promote.py` (a test keeps the copies identical); fix both together
  if it is ever fixed.
- **The Odds API, measured from its docs (2026-09-17):** `/sports` and `/events` are
  free but `/events` lists only in-play and pre-match events - no past games, no
  markets. `/events/{id}/markets` costs 1 and returns only "recently seen" keys.
  Historical odds (bulk, featured markets) cost `10 x markets x regions` per call for
  the whole slate; historical EVENT odds (props) cost `10 x unique markets returned x
  regions` per event; historical events listing costs 1. So last weekend's prop
  coverage cannot be measured for free.
- **Scheduled weekly CFB refresh:** `run_weekly_cfb.cmd` (runs the job with `--log`;
  output and exit code in `<STORAGE_DIR>/cfb/logs/ingest_cfb.log`).
- **Track C state lives in `docs/TRACK-C-HANDOFF.md`** - read it before any CFB work.
- **CFB line sources are layers, not substitutes:** CFBD for 2013-2019 (unreachable elsewhere) and
  as the free 2020-2025 layer; the Odds API is the forward source (bulk game lines, ~3 credits a
  slate); Kalshi/Polymarket are exchange probabilities, never presented as a book line. CFBD lines
  are labelled book consensus without a capture time. Timestamped 2020-2025 history is bought only
  against a pre-registered question.
- **Odds API CFB spend exists in exactly two shapes** (`cfb/oddsapi.PAID`, params fixed): P1 event
  markets (74 credits LIFETIME, `--odds-p1`) and the forward bulk h2h/spreads/totals capture (3
  credits per kickoff hour, 75 per CFB week Tue 12:00Z-Tue 12:00Z, `--odds-forward` every 5 min).
  Kickoff hours come from the Odds API's `commence_time`, never `cfb_games.start_ts`. Anything
  else - historical, props at scale, option B or C - needs a new approved number, and B vs C is
  not proposed until P1's results AND Track A's NFL credit reconciliation are in: the NFL has
  first claim on the shared pool.
- **`config.ODDS_RESERVE` defaults to 40 - the NFL fallback.** A clone whose `.env` omits it
  silently inherits a floor of 40. `cfb/oddsapi.reserve()` reads the environment only and refuses
  when unset.
Disk before diagnosis. store.disk_headroom_ok() refuses below 5 GB free, so a full drive presents as unexplained test failures and refused archives, not as a disk error. On any run of unexplained failures in storage, archive or temp-using tests, check free space on the temp drive first. Temp is D:\temp; pip and npm caches are under D:\caches\. Tests should run with --basetemp pointed at a project path, never the default.
## Claims (W07)

- **No comparative claim on this site is hand-written.** Every verdict — "worse than", "tightens",
  "below the line", "doubles", "thinnest", "a small fraction" — is computed from the exported numbers
  at render time (`lib/claims`, `lib/verdict`), or it does not appear. Five captions written from
  memory were wrong in both directions ("four of five" when all five cleared; "roughly doubles" at
  1.6×): prose from memory, not a site flattering itself, so the fix is mechanical.
- **Wording comes from the interval.** Excludes zero → "worse than" / "better than"; includes it →
  "no better than". "Identical" only on a match at the precision the file publishes. **No interval,
  no verdict**: state the figures and stop ("well calibrated" was removed for exactly this).
- **Generating copy from data is a consistency check on the data.** Not a side benefit - a reason to
  keep generating even where a sentence would be quicker to type. A number can be wrong and look
  fine; a SENTENCE has to name the thing, and naming it is when two halves that disagree are forced
  to say so. Writing the analytics page copy found `ngs_stability` publishing a registry range of
  1999-2026 around values stamped 2016-2026: nothing about the numbers was wrong, both halves were
  internally consistent, and the file said two different things. It surfaced only because the
  sentence had to state a range. A hand-written caption would have stated whichever range the author
  remembered and the contradiction would have survived. Guarded now at `metrics.publish`, which
  refuses when the two differ.
- `tests/handwrittenClaims.test.ts` enforces it. Every comparative phrase in rendered text is computed
  or declared with a kind (definition, gated, record, violation). Declared violations are live debt,
  to be removed or computed, not permission.


## One builder owns one prefix, and owns exactly what it fills (track A's incident)

`sync_keys(dest, wanted, prefixes)` **deletes every local key under `prefixes` that is not in
`wanted`**. Its contract is therefore "this builder owns this prefix", and it is a delete
authorisation, not a hint.

2026-09-18: `sync_keys(dest, research, ["research/"])` ran with a `wanted` set that
`build_research()` fills with exactly three files, and **twelve market keys under `research/` were
deleted** — a builder firing on data it does not produce. One incident is enough evidence.

- **A prefix has exactly one builder, and that builder fills all of it.** Not most of it.
- **Do not fix a collision by widening another builder's `wanted` set.** Two builders sharing one
  prefix is the defect; adding keys to the other one's list preserves it and hides it.
- **A new data family gets a new TOP-LEVEL prefix with its own `sync_keys` call.** Nested under
  someone else's prefix is deletion waiting for their next run. Track F's analytics moved from
  `{sport}/analytics/` to `analytics/{sport}/` for this reason — the nested form happened to be
  safe, because the owned prefixes are `nfl/market/`, `nfl/players/`, `nfl/teams/` and `research/`
  and nothing owns bare `nfl/`, but "happened to be safe" is a fact about today's call sites and
  not a property of the key.
- **Assert it, don't remember it.** `tests/test_analytics_contract.py` reads the producer's own
  `sync_keys` call sites by AST and fails if any owned prefix contains, or is contained by, the
  analytics prefix; and it proves the delete path both deletes inside the prefix and cannot reach
  a key outside it. A destructive path that has never been seen to fire is not a guard.

## Shared denominators, and intervals read side by side (track F, Ethan)

**Any share-of-team figure has a shared denominator, and two teammates' shares are
therefore mechanically opposed.** Target share, carry share, snap share, route share, red-zone
share, any "% of team" a future analytic invents: if A's share goes up on a given game, B's goes
down, because they divide the same total. This is a property of the quantity, not of these five
metrics, and it applies to every analytic added later and to anything track B renders side by side.

Three consequences, in force without being restated:

- **Resample independently per subject.** Sharing bootstrap draws across subjects (common random
  numbers) leaves every interval individually valid and correlates the Monte Carlo error *between*
  them — which is exactly what a reader consumes from two intervals on one screen. For negatively
  correlated subjects that is **anti-conservative**: measured variance of the eyeballed gap runs
  1.571 at ρ = −0.9, 1.078 at −0.3, 1.026 at 0, and 0.952 at +0.6, relative to independent draws.
  Teammates measure ρ = −0.215 on a real slate, so the site's commonest comparison sits on the bad
  half. `analytics/crn_check.py`; the seed is derived from the subject's own id so this costs
  nothing in reproducibility.
- **Two intervals side by side are not a test.** Overlap is not "no difference" and separation is a
  stricter bar than a direct comparison. Where a genuine contrast between two subjects is wanted,
  bootstrap the CONTRAST as one quantity over shared blocks — the same rule brief 018 set for the
  selection gap — never by differencing two separately published intervals.
- **A share metric must say it is one.** `analytics.metrics.Metric.shares_denominator` names the
  denominator (`team`, `league`, `own`, or None), the registry refuses a share-shaped metric that
  leaves it unset, and it is exported so a page can carry the caveat rather than re-deriving it. A
  rule that lives only in prose is a rule the next analytic does not know about.
- **Where the classification is genuinely ambiguous, take the error that costs a sentence.**
  `usage_stability.*_share` is declared `team`, and `own` is defensible on the published shape — a
  correlation over 1,540 players is not itself a team-divided quantity. The tiebreaker is NOT an
  argument about what the field means, because both readings of that are sound. It is the
  asymmetry of being wrong: declaring `team` unnecessarily costs one sentence of caveat nobody
  needed, while declaring `own` wrongly OMITS that sentence from a comparison readers make anyway.
  The costs are not symmetric, so the decision does not rest on resolving the ambiguity.

**And the meta-rule, from how this one was nearly missed.** The caching was defended by two
arguments at once — that no contrast between subjects was published, and that regenerating draws
was ~99% of runtime. Both were wrong, and neither had been checked: the product's whole idiom is
side-by-side comparison, and the real cost was 22–208 seconds across 27,000 slices. **Agreement
between two unchecked arguments is not evidence.** Two reasons pointing the same way feel like
corroboration and are not; the only thing that makes either one evidence is measuring it.

## Claims — falsifiability (W07, Ethan)

- **A computed verdict must be capable of producing a different answer from the same pipeline.** A
  comparison whose only possible output is the one we are publishing is decoration, not a finding.
  Computing it is not enough: the data model must be able to express the other answer, and the code
  must be able to reach it.
- Found on the register: "none of them found an edge" was computed over a verdict enum with no
  edge-confirmed value, so it could not have come out otherwise. The fix is not to delete the finding
  but to make it falsifiable — add the reachable verdict, render the count ("17 registered, 0 reaching
  edge-confirmed"), a number that could have been non-zero.
- `tests/falsifiable.test.ts` drives every verdict function in `lib/claims` and `lib/verdict` to each of
  its answers. A new verdict function lands with its entry there, or it does not land.


- **It covers EXPORTED PROSE, not just rendered text** (track B, A10). The site renders these
  verbatim and cannot recompute them, so a comparison written into a string here is a hand-written
  claim one layer down:
  - `manifest.scoring_note` - "differs by median 0.00 and p99 0.00 points per game over 193,354
    player-games".
  - `market.validation.status` - "validated on sportsbook ladders, 2023-25 (q90 coverage 0.101
    against 0.100)".
  - `research.calibration.population`, `hypotheses[].why`, `source.method` - any comparative wording.

  Either export the figures as fields and let the site word them, or generate the string from the
  figures at export time, in code that can produce the other answer.

## Relay

Your brief is `C:\Users\Ethan Davis\code\_relay\tasks\<track>.md` — read it before
starting a unit. Track letters: a = calibrated-sports, b = calibratedsports-web,
c = cs-cfb, f = cs-analytics.

Before writing your report, move the existing one out of the way:

    mv _relay/reports/<track>.md _relay/archive/reports/<track>-$(date +%Y%m%d-%H%M).md

Then write the new report to `_relay/reports/<track>.md`. **That file is what gets
read. Terminal output is not.** A finding that exists only in the terminal did not
happen.

`_relay/LEDGER.md` is the append-only record of decisions taken — read it when a
question feels already settled, and say so rather than relitigating it. Do not
edit it; findings go in your report and are recorded from there.

### Work on a named branch off `origin/main`, never on local `main` (a-04, 2026-09-22)

`calibrated-sports`, `cs-cfb` and `cs-analytics` are **three clones of ONE repository**,
`github.com/edavis9817/calibrated-sports.git` - not three repos. Each track committing to its
own local `main` produced three divergent mains for one remote, with nothing pushed; a-04
integrated them. So, in all three clones:

    git fetch origin
    git checkout -b <unit-id>-<slug> origin/main

- **Every unit starts from that command.** Local `main` stays a clean mirror of `origin/main`
  and is never committed to; it only ever fast-forwards.
- **A unit's work reaches `origin/main` by pushing its branch and integrating it**, following
  the commit sequence under *Repo hygiene* (rebase or merge onto what arrived, re-run the suite
  on the combined tree, never force).
- **This rule lives in the committed `CLAUDE.md`, which is one file shared by all three
  clones.** A clone that has not fetched does not see it yet - which is one more reason to
  branch off `origin/main` rather than off whatever local `main` happens to hold.
