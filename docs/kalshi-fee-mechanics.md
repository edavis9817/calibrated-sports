# Kalshi fee mechanics — authoritative notes

Source: Kalshi fee schedule PDF, "Last updated and effective: July 7, 2026". 12 pages.

## Formulas

Taker (immediately matched orders):

    fee = roundup(M × 0.07 × C × P × (1−P))

Maker (resting orders, charged only when the resting order executes; no fee to cancel):

    fee = roundup(M × 0.0175 × C × P × (1−P))

- `P` = contract price in dollars (50¢ = 0.5)
- `C` = number of contracts **in the order** — the fee is charged on the whole order, not per contract
- `M` = per-series multiplier. **Taker default M = 1. Maker default M = 0.**
- Rounding: the schedule text says "rounds up such that the fee + positionCost is rounded to a
  centicent" ($0.0001). The published fee table contradicts this and is consistent with ceil-to-cent
  (e.g. 100 contracts @ $0.35 → raw $1.5925, table $1.60). See "Open question" below.

## Fee expressed in probability points

One contract settles at $1, so the fee as a fraction of notional is:

    taker cost = 7 · p · (1−p)  percentage points
    maker cost = 1.75 · p · (1−p)  percentage points  (only where M ≥ 1)

| price | taker fee (pp) |
|-------|----------------|
| 0.50  | 1.75 |
| 0.30  | 1.47 |
| 0.20  | 1.12 |
| 0.15  | 0.89 |
| 0.10  | 0.63 |
| 0.05  | 0.34 |

**The fee is a quadratic that peaks exactly where the liquidity is.** Tight, deep markets sit near
50¢ and carry the maximum fee; illiquid longshot rungs carry the least. The two costs — spread and
fee — are anti-correlated, which caps how much moving to deeper markets can help.

Crossing cost comparison:

| market | half-spread | fee | total per crossing |
|--------|-------------|-----|--------------------|
| player prop, 5–7¢ spread, p≈0.15 | 2.5–3.5pp | 0.89pp | **3.4–4.4pp** |
| game line, 1–2¢ spread, p≈0.50   | 0.5–1.0pp | 1.75pp | **2.3–2.8pp** |

Moving from props to game lines cuts the spread by ~70% but improves total crossing cost by only
~30%, because the fee moves against you.

## No settlement fee

Confirmed: no settlement fee, no membership fee, no ACH deposit/withdrawal fee.

**Consequence:** a position held to settlement pays one fee and crosses one spread. The
round-trip hurdle of ~7pp applies only to trades that are exited before settlement. For a
hold-to-settlement trade the hurdle is **~3.6pp, not ~7pp**. This halves the bar for the
inactive-report hypothesis, which is naturally a hold.

## No volume tiers on event contracts

The tiered bps schedule on the final page applies to **perpetual futures**, a separate product.
Event-contract fees are flat at 0.07 / 0.0175 regardless of 30-day volume. Scaling up does not
reduce the fee rate — there is no "get bigger and the economics improve" path.

## Per-series multipliers (Non-Standard Fees table)

Series listed with maker/taker multipliers. Relevant football entries, all **1 / 1**:

- `KXNFLGAME` — Professional Football Game
- `KXNCAAFGAME` — College Football Game
- `KXSB` — Super Bowl; all AFC/NFC division and conference series; all AP award series
- `KXMVE` — Combos (excluding uncorrelated NFL combos): **maker 2**, taker 1

Series listed with **0 / 0** are fee-free both sides (KXBTCY, KXDOED, KXGREENLAND, tech layoffs,
various geopolitical).

**No NFL player-prop series appears anywhere in the table.** Under the stated defaults an unlisted
series is taker M=1, **maker M=0** — i.e. resting orders on player props would be fee-free. This
needs verifying against the actual series tickers behind our 935 predictions before being relied on.

## Consequences for the codebase

1. `kalshi_fee(p, contracts=1)` in `jobs/paper_trade.py:76` bills the ceil-to-cent rounding to every
   contract. Overstatement is worst at extreme prices — ~1.1× at p=0.5, ~1.6× at p=0.1, ~2.9× at
   p=0.05 — averaging ~2.4× across the ledger. Every `net_edge` in `paper_ledger` is too pessimistic.
2. The fee function needs an `M` argument and a series→multiplier lookup, defaulting maker to 0.
3. This is correctness hygiene, **not a new result**. None of the six retired edge hypotheses rested
   on ledger `net_edge`; they were all measured on CLV, which is a price difference and carries no
   fee. Fixing the fee does not revive anything.

## Open question — cent vs centicent rounding

The prose and the table disagree. At 10-contract tickets the difference is ~0.1% of the fee and
immaterial; at 1-contract tickets it is up to 3×. Resolve empirically: place one small live order
and read the actual fee off the fills endpoint, rather than choosing between two documents.
