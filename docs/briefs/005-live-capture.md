# 005 — Live in-game capture (WebSocket) and futures

Not for opening week. Build after 001-004 are stable.

## Why polling is the wrong tool here

The REST logger now has a `live` tier at 10s, which is a stopgap and adequate
for a first look. It is not the right architecture:

- In-game prices move on every snap. 10s polling misses most of the movement
  and captures duplicates the rest of the time.
- **The Odds API must never be polled live.** Per-event calls bill credits; a
  10s loop over a slate would exhaust a 500-credit month in minutes. It is
  currently safe by construction — `due_snapshots()` only fires on the
  pre-kickoff schedule and returns empty once kickoff passes — and that
  guarantee must not be weakened.
- Kalshi and Polymarket both push over WebSocket for free. Kalshi's `ticker`
  and `trade` channels are public; `orderbook_delta` needs auth.

## Goal

A separate `jobs/live_capture.py` process that subscribes to WebSocket feeds
for games in progress, writes to its own high-volume table, and stops when the
games end.

## Contract

- Own table `live_ticks`, NOT the `quotes` table. Different volume profile,
  different retention, and mixing them makes the pre-game series unusable.
- Subscribe on kickoff, unsubscribe on final. Never subscribe to everything.
- Same normalized row shape, so downstream code doesn't branch on transport.
- Retention policy **written and tested before the first run**. This is the
  Praxis disk-full failure restated: an unbounded stream with an untested
  retention policy fills the disk while you are away.

## Futures

Already handled by the existing `futures` tier at 300s — futures barely move
intraday and this is the right cadence. Two things still missing:

- Discovery currently keys off game events. Season-long markets (division,
  conference, Super Bowl, win totals) need their own discovery path.
- They are the **fee-favourable** category: Kalshi's fee at P=0.10 is ~0.63¢
  versus ~1.75¢ at P=0.50, so longshot-priced futures cost under a probability
  point against 20-30% overround at a book. Worth their own analysis once
  logged.

## Invariants
#1 venue specifics in adapters · #2 raw first

## Acceptance
- Runs through a full game, reconnects automatically after a dropped socket
- `live_ticks` row count is non-trivial and timestamps are monotonic per market
- Retention job demonstrably deletes rows past the window, with a test
- Disk usage projected for a full Sunday slate, recorded in `DECISIONS.md`
- A test asserts the Odds API client returns no rows when handed in-game markets

## Out of scope
Order placement. Modelling live prices. Anything before 004 ships.
