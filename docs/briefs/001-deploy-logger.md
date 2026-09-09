# 001 — Deploy the market logger

Priority: **now**. Market prices are perishable; Kalshi book history is not
backfillable. Every hour this isn't running is data that does not exist.

## Goal
`verify.py` passes against live endpoints, the venue parsers match observed
payloads, and `run_logger.py` runs continuously on a Render worker capturing
Kalshi, Polymarket and Odds API snapshots into SQLite plus the raw archive.

## Contract
Unchanged from the existing package. Venue adapters implement
`list_markets()` and `fetch_quotes()`; both return the normalized row shapes in
`store.py`.

## Invariants
- #1 venue specifics stay inside `ingest/venues/`
- #2 raw first — never parse before archiving

## Acceptance
- `python verify.py` reports OK for all three sources and writes payloads to
  `data/probe/`
- The measured credit cost of one Odds API per-event prop call is recorded in
  `DECISIONS.md`, with the extrapolated weekly full-slate cost
- Logger runs ≥6h without an unhandled exception
- `SELECT venue, COUNT(*), SUM(ok) FROM poll_log GROUP BY 1` shows non-zero
  successful polls for every enabled venue
- At least one row per venue has a non-null `mid`

## Out of scope
Postgres migration, the dedicated VPS, any modelling, the outcome mapping.
Fix parsers only as far as needed to capture — raw archive means an imperfect
parser is recoverable, a missing hour is not.

## Verified against the live API (2026-09-09)

The shapes below were guesses when this brief was written. They are now
measured; `venues/kalshi.py` and `venues/polymarket.py` carry the detail.

- `KALSHI_BASE` answers. Prices are **dollars**, not cents - and a contract
  settles at $1, so the dollar price is already the probability.
- The ladder is `orderbook_fp.yes_dollars` / `no_dollars`, string prices, and
  `ask_yes = 1 - best_no_bid` reconciles exactly with `yes_ask_dollars`.
- `/markets/orderbooks` needs **repeated** `tickers` params. Comma-joined
  returns 200 with one empty book. Batch ceiling is 100.
- Polymarket book levels are dicts with `price`; gamma also publishes
  `bestBid`/`bestAsk` inline, identical to the CLOB.
- The Odds API tier does include player props: one per-event call cost 16
  credits against a 100,000 credit balance.

## Retention (added 2026-09-09)

The capture path was complete but unbounded. Measured production rate is
**1.71 GB/day** (1.10 raw gzipped + 0.61 quotes) on a quiet midweek day.

- `jobs/rotate_raw.py` moves raw shards older than 7 days to Cloudflare R2:
  hash, upload, verify by reading the bytes back and hashing, delete local,
  mark remote. A failed verify keeps the local copy. `raw_shards` is the
  manifest; `--status` says where the archive lives, `--check` preflights the
  bucket.
- `jobs/prune_quotes.py` prunes `quotes` past 14 days. That table is derived,
  so the archive is the recovery path. `poll_log` and `markets` are kept.
- `store.disk_headroom_ok()` suspends raw archiving below `DISK_MIN_FREE_GB`
  and records it in `source_health`. Quote capture continues.
- `run_logger.watchdog()` is the dead-man switch: no successful poll from any
  venue in `DEADMAN_MIN` and it says so loudly and marks `liveness` unhealthy.
- Both jobs also run in-process hourly, so retention does not depend on a cron
  entry somebody has to remember to install.

Steady-state local footprint: 7.7 GB raw (7d) + 8.5 GB quotes (14d) = ~16 GB.

## Still open

- **R2 credentials are not set on this box.** Until they are, rotation is a
  no-op recorded as unhealthy and the archive grows locally - roughly 19 days
  of headroom before the disk floor suspends archiving. Set `R2_ACCOUNT_ID`,
  `R2_BUCKET`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, then run
  `python -m jobs.rotate_raw --check`.
- The rotation logic is tested against a fake S3. The wire path to real R2 is
  unexercised until that preflight runs.
- Nothing external watches `source_health` yet - the dead-man switch shouts
  into the log, which is enough to diagnose after the fact but not to page.
