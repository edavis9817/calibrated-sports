# Are low-threshold multiples underpriced?

Unit c-23 (track C), 2026-09-29. This is a findings unit, and it stopped at its own
gate. Nothing publishes from here.

## The headwind, first

**A two-leg multiple at a book has to clear about twice the single-leg cost in return
terms. In probability points it has to clear more than the single-leg bar, and much
more deep in the money.**

The brief's figure is "3.38c a side, so roughly 6.8pp before anything else". That is
right at the money in return terms, where both legs together give **−12.3%**. It is
wrong deep in the money, where Ethan's example lives:

- **Book cost there.** c-22 measured the book cost of an over leg at 4.6-6.2c, not 3.38c.
- **Joint mispricing needed to break even.** Realised P(both) minus the product of the
  de-vigged legs must exceed:
  - **6.19pp** in 0.60-0.70;
  - **8.59pp** in 0.70-0.80;
  - **10.55pp** in 0.80-1.00.
- **Return at fair, independent legs:** −12.9% to −13.6%.

The **exchange** route has no compounding vig. Each leg costs the taker fee `7p(1−p)`c,
which c-22 put at 1.0-1.6c deep in the money, plus half the spread. But two contracts
pay **additively**, so correlated singles **cannot collect a joint mispricing at all**.
What they earn is the sum of the legs' own edges, and c-22 measured those at zero. So
the reader's choice is:

- a book, for the multiplicative payoff, at the break-even above;
- an exchange, for singles whose edge is the thing c-22 found absent.

One correction to the brief: Kalshi **does** list combo series. See Finding 3.

- **Reproduce.** `python -m research.joint_headwind` reads only
  `research/results/bias_by_moneyness.json` (c-22, `8dd47bb`).
- **Pre-registration.** `docs/C23-the-joint-preregistration.md` at **`e0db8e3`**, committed
  and pushed before the script existed.

| bucket | n | p | c.ovr | leg gap pp [95%] (c-22) | 1-leg break-even pp | 1-leg return | 2-leg break-even pp | 2-leg return |
|---|---:|---:|---:|---|---:|---:|---:|---:|
| 0.00-0.20 | 2,433 | 0.177 | 1.30 | −0.94 [−2.50, +0.76] | 1.30 | −6.8% | 0.47 | −13.2% |
| 0.20-0.30 | 9,771 | 0.257 | 1.89 | −0.05 [−1.18, +1.08] | 1.89 | −6.9% | 1.01 | −13.2% |
| 0.30-0.40 | 10,447 | 0.350 | 2.50 | −1.17 [−2.32, −0.04] | 2.50 | −6.7% | 1.81 | −12.9% |
| 0.40-0.45 | 8,287 | 0.427 | 2.86 | −2.23 [−3.31, −1.15] | 2.86 | −6.3% | 2.53 | −12.2% |
| 0.45-0.50 | 16,119 | 0.482 | 3.25 | −3.15 [−4.13, −2.25] | 3.25 | −6.3% | 3.23 | −12.2% |
| 0.50-0.55 | 28,477 | 0.511 | 3.45 | −1.78 [−2.73, −0.82] | 3.45 | −6.3% | 3.65 | −12.3% |
| 0.55-0.60 | 6,884 | 0.571 | 3.80 | −2.50 [−3.73, −1.28] | 3.80 | −6.2% | 4.48 | −12.1% |
| 0.60-0.70 | 5,758 | 0.646 | 4.62 | −0.74 [−2.38, +0.70] | 4.62 | −6.7% | 6.19 | −12.9% |
| 0.70-0.80 | 3,858 | 0.740 | 5.59 | +0.14 [−1.58, +1.87] | 5.59 | −7.0% | 8.59 | −13.6% |
| 0.80-1.00 | 543 | 0.818 | 6.22 | −0.74 [−4.54, +2.77] | 6.22 | −7.1% | 10.55 | −13.6% |

- **How the columns are computed.**
  - Both legs sit in the same bucket at the bucket mean `p`, and are independent at fair.
  - The book prices the multiple at `(p+c)²`.
  - `c.ovr` is c-22's median book cost of the over, so this is not a shopped best price.
- **What the break-even columns are.** They are deterministic arithmetic on c-22's
  aggregates. The uncertainty lives in c-22's own intervals, which are reprinted here.
- **The bars differ by unit.** In pp the two-leg bar rises steeply with moneyness. In
  return terms it is flat at about 2× the single leg. The brief's 6.8 matches neither
  deep in the money.

## Finding 1: the gate fires, so the joint was not measured

The brief: *"If c-22 comes back with the deep in-the-money bucket at zero or negative,
say so and stop rather than looking for the joint to rescue it."*

c-22's result, where every interval spans zero with a negative centre:

- **Deep ITM over, every book:** **−0.41pp [−1.81, +0.93]**.
- **Receptions** (Ethan's example): **−0.95 [−2.55, +0.58]**.
- **0.80-1.00** (the "7-average at 4+" zone): **−0.74 [−4.54, +2.77]** on 543 outcomes.

**So c-22 says the leg input does not have the sign this hypothesis needs. I stopped.**

- **Not run:** brief items 1-4 (co-occurrence, same-game vs cross-game, the week
  common factor, and failure-mode rates).
- **If Ethan overrides the gate:** they are registered in the pre-registration as
  J1-J4, with a week-block bootstrap, so a reversal runs a fixed plan rather than a
  search.
- **This departs from c-22's own recommendation.** c-22's report said "run c-23 as a
  measurement only". The brief's gate is Ethan's written instruction and a track's
  recommendation is not, so the brief wins. Both are quoted here, as the Authority
  rules require.

## Finding 2: "we hold a joint" is false. No game simulator exists

The brief's case rests on this: *"the game simulator emits joint samples per game... We
hold a joint."*

- **Only a docstring says so.** That claim appears in `core/distributions.py` (the module
  docstring and `Empirical`'s), and nowhere else. **Nothing in the repo constructs
  `core.distributions.Empirical`.** A search for `Empirical(` outside tests finds only
  `research/bookvbook.py`, which defines its own unrelated class of that name.
- **What the model actually is.** `models/baseline.py` fits each (player, stat) on its
  own, as an independent `NegativeBinomial` or `ZeroInflatedGamma`.
  - The only simulator in `models/` is `models/season.py`, which does team standings.
  - Invariant 4 is a design rule that has not been built.
- **Cross-game, the asymmetry would not exist even if it were built.** A per-game
  simulator treats different games as independent, which is exactly what a book's
  product-of-legs assumes. The only thing that could separate them cross-game is the
  week-level common factor (item 3), and no model here represents it.
- **Same-game, books do not price a same-game parlay as a product.** They price a
  same-game parlay with their own correlation adjustment. The brief's "product of its
  vigged legs" describes the cross-game case only.
  - This is `not_verified`: no book's SGP pricing is in the archive.

## Finding 3: the exchange does list combos

The brief says the exchange has *"no parlay instrument either"*. Our own records say
otherwise:

- **The board snapshot.** `board_019.db`, a Kalshi `/series` snapshot from 2026-09-14,
  opened `mode=ro`, lists three series:
  - `KXMVENFLMULTIGAME` ("MVE NFL Multi Game");
  - `KXMVENFLSINGLEGAME`;
  - `KXMVENFLMULTIGAMEEXTENDED`.
  - All three are in category Exotics.
- **The fee schedule.** `docs/kalshi-fee-mechanics.md` quotes Kalshi's schedule listing
  `KXMVE` as "Combos (excluding uncorrelated NFL combos): maker 2, taker 1".
- **The same snapshot held 0 open markets in those series.** Whether combos are quoted
  on request, how they are priced, and whether player props can be legs were **not
  checked**, because checking them means a live API read and this unit spent nothing.
- **So the choice is not strictly "book multiplier vs exchange singles".** An exchange
  multiplicative instrument exists, and nothing about its pricing is measured.

## Capacity

**We have no data on prop limits or on how fast a book restricts a winning prop account.**

- Nothing in `research/` or `docs/` measures it.
- The CLAUDE.md line "~$5 per $500 two-leg instance before the account is limited" (brief
  023 Part 2) has no measurement behind the "limited" half that I could find.
- I am not estimating one.

## What this does not establish

- **Not a measurement of the joint.** The same-game and cross-game co-occurrence was
  not computed. This unit cannot say whether a correlation effect exists, only that the
  gate the brief set was not passed and what it would have had to clear.
- **The gate reads c-22, and c-22 has its own scope.**
  - It is multiplicatively de-vigged with no Shin arm, and its deep tail is 90% Kambi
    (BetRivers/Unibet) lines, 2023-2024.
  - Ethan's example (a mean-7 receiver at 3.5) sits at p 0.85-0.94. That is a negative binomial at mean 7 or 8 with var/mean 1.2-1.69, from `core.distributions`, not a price. Only 543
    outcomes of any stat are priced ≥ 0.80, so the zone is barely in the archive.
  - The alternate ladders that would reach it are FanDuel-only
    (`player_receptions_alternate`).
- **The break-even assumes equal legs, bucket-mean p and median-book cost.** Line
  shopping lowers `c`, and this unit did not measure by how much.
- **Nothing about Kalshi combo pricing** (Finding 3).
