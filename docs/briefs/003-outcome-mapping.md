# 003 — Outcome mapping and the player crosswalk

The unglamorous brief that everything cross-venue depends on. Skipping it is
the single most likely cause of a v2 rewrite.

## Goal
A `market -> outcome_id` table so a Kalshi ticker, a Polymarket token id and a
book's market id for the same claim resolve to one row, plus a player-name
crosswalk from each venue's naming to `gsis_id`.

## Contract
Uses `core.outcomes.Outcome` and `Market`. Mapping is:
1. deterministic where the venue exposes structure (parse the ticker)
2. fuzzy-matched with a confidence score where it only exposes a title
3. **manually confirmed** for anything below threshold, into a reviewed table

Unmapped markets are logged, never silently dropped. An unmapped market is a
market you cannot compare, and a silent drop looks identical to "no edge here."

## Invariants
#1 venue specifics in adapters · #7 sport discriminator

## Acceptance
- Round trip: build an `Outcome`, map three synthetic venue markets to it, and
  recover all three from `outcome_id`
- A name crosswalk test covering the usual failure cases: suffixes (Jr/III),
  punctuation, and two active players sharing a surname on one team
- Coverage report: % of logged NFL markets mapped, listing the unmapped
- Fuzzy matches below threshold land in a review queue, not in the table

## Out of scope
Automatic resolution of ambiguous matches. ~16 games a week is small enough
that manual confirmation is correct; guessing here corrupts the join key.
