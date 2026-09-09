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
remembering a cron entry is the policy that already failed once.

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
- **History is free and needs no auth**: `/series/{s}/markets/{t}/candlesticks`.
  `period_interval` is in MINUTES (1/60/1440). 90-day spans return, 365 is a
  400, so chunk. **429 after ~5 rapid requests**, no rate headers, no
  Retry-After. Candles carry `volume_fp` and `open_interest_fp`; `price` is
  `{}` when nothing traded, and bid/ask still exist there.
- **Live `volume` is CUMULATIVE, a candle's `volume_fp` is PER-PERIOD.**
  Summing both together produced a 6.7-billion-contract week.

**Substring traps in NFL filtering.** "i-NFL-ation" contains NFL, and so does
`KXNCAAFCO-NFL-EAVE` across a word boundary. The filter is
`(?<!I)NFL(?!X)` plus an NCAA/college exclusion plus a category guard.

**Polymarket**
- Blind pagination 422s past `offset≈2000`; the API names `/markets/keyset` for
  deeper paging. Use `/events` with `tag_slug=nfl` instead.
- Slugs look like `pro-football-2026-27-passing-yards-leader`.
- **`prices-history` carries NO volume and NO open interest** — `{t, p}` only,
  ~31 days. Neither does the live CLOB path. Polymarket cannot be bucketed on
  the same liquidity axis as Kalshi; spread is all it offers, and an empty book
  quotes 0/1 so its mid is a meaningless 0.500.
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
