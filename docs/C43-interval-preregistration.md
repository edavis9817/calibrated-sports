# C43 - the interval between two rungs: pre-registration

Unit c-43 (track C), written 2026-10-08, **before `research/interval_mass.py` exists** and
before any outcome-bearing number for this unit was computed. Branch `c-43-interval-mass`,
cut from `origin/main` (`d9dd3ba`) with `origin/c-35-ladder-edges` (`a0f0354`) merged in,
because c-35's script and extract are what this unit reuses and they are not on `main`.

**This is not a model unit.** Nothing here fits a distribution to a player's outcomes and
nothing reads `models.baseline`. The object measured is the **market's** ladder. c-35's cache
carries a model probability per rung (`m`); this unit drops that key on load and a test
asserts the script never reads it.

## Scope, stated so it travels with every number

Kalshi `KXNFLREC` (receptions) and `KXNFLRSHATT` (rush attempts), NFL 2026 regular season
**weeks 2, 3 and 4**, one instant per game: **kickoff - 180 minutes** (c-35's instant,
unchanged, so the two are comparable). The population is c-35's: 725 ladders, 3,987 usable
rungs, 48 games. Venue: Kalshi only. Nothing here is about sportsbooks.

## The data, and what was read before this file was written

The rungs are read from the scratch cache written by c-35's committed extract
(`python -m research.ladder_edges --cache <scratch> --extract`, extracted
2026-10-08 05:40Z, sha256 `1c5f8b269ba2b9ec...`). **`market_log.db` is not opened by this
unit at all.** The cache is not committed (it holds per-market rows); it is re-creatable by
that command while the quotes are retained.

Read from that cache before writing this file - **no outcome field (`x`, `y`) and no model
field (`m`) was read**:

- 725 ladders, 3,987 rungs, 48 games (matches c-35's findings); `/series` fee type
  `quadratic`, multiplier 1, for both series.
- **Receptions**: 552 ladders, every line a half-integer, rungs 1 apart (2,933 adjacent
  gaps of 1.0 and one of 2.0). Rung pairs by width in catches: 1: 2,933 / 2: 2,397 /
  3: 1,862 / 4: 1,363 / 5: 950 / 6: 604 / 7: 335 / 8: 157 / 9: 45 / 10: 14 / 11: 1.
- **Rush attempts**: 173 ladders, rungs 3 apart, 156 ladders of 3 rungs. Pairs: width 3:
  328, width 6: 156.
- Quoted spread at this instant: median 1c on both series (deciles 1/1/1/1/1/2/2/3/3c on
  receptions).
- Pairs with a `market_depth` VWAP at 100 contracts on BOTH legs (YES on the lower rung, NO
  on the upper): receptions 987 / 801 / 615 / 442 / 302 / 187 / 98 for widths 1-7; rush
  attempts 112 / 55. At 500: 952 / 766 / 580 / 417 / 279 / 170 / 86 and 112 / 55.
- c-35's published findings (`docs/findings/ladder-edges.md`), which are outcome-bearing and
  which I have read. See *The 2026 pre-check* for what that does to independence.

## Definitions

- **Rung**: c-35's usable rung - the last `source='live'` Kalshi quote at or before entry,
  two-sided (`0 < bid < ask < 1`), at most 600 s old. `k` = mid. A rung states
  `P(X > line)`.
- **Interval**: any pair of usable rungs `i < j` of one ladder (one player, one stat, one
  game). It is the claim `L_i < X < L_j`, i.e. "at least `L_i + 0.5` and at most
  `L_j - 0.5`". **Width** `w = L_j - L_i`, the number of counts it contains. The brief's
  example, "clears four, stays under eight", is the rungs 3.5 and 7.5: width 4.
- **Implied mass** `p = k_i - k_j`, from the RAW mids. No isotonic repair is applied before
  the coherence count (repairing first would erase what is being counted); calibration uses
  the raw value too, so a negative mass enters its bin as it is quoted.
- **Realised** `h = 1[L_i < X < L_j]`, `X` the settled actual in the cache
  (`core.settlement.settle`, as c-35).
- **Widths tested**: receptions **1, 2, 3, 4, 5, 6, 7**; rush attempts **3, 6**. Receptions
  widths 8-11 (217 pairs) are counted in coherence and are in no test.
- **Bins on implied mass**, fixed: `<0.05`, `0.05-0.10`, `0.10-0.20`, `0.20-0.30`,
  `0.30-0.45`, `0.45-0.60`, `>=0.60`.
- **Central rung** (c-35's): the rung whose mid is nearest 0.5, if that mid is in
  [0.25, 0.75].
- **Blocks**: 2,000-draw bootstrap over **games**, seed 43. Intervals of one ladder overlap
  heavily and a ladder of `n` rungs yields `n(n-1)/2` of them off ONE realised count, so
  nothing here is read per interval. An interval on fewer than 5 games is not read and
  enters any correction at p = 1; so does a zero-variance bootstrap.
- **Multiplicity**: z = estimate / bootstrap SE, Benjamini-Hochberg at q = 0.10 (c-35's
  rule, kept for comparability), survivors split by sign. The Holm count at 0.05 is printed
  beside it and carries no verdict.
- **Specifications tried: one.** One instant, one bin list, one width list, one
  correction. The count is reported in the findings; any further run is an addendum, dated
  and labelled post hoc.

## Answer 1 - coherence (counts, no verdict word)

Over every interval of every ladder with at least 2 usable rungs, by width and by bin:

- **C1** implied mass negative (`k_i < k_j`): the mids are inverted.
- **C2** implied mass exactly zero.
- **C3** crossed at the touch: `ask_i < bid_j`. Buying YES on the lower rung and NO on the
  upper then costs less than the 1 it must pay (brief 019 H1's condition). Counted; and,
  where both legs have depth, whether it survives the taker fee at 100 contracts.
- **C4** the interval's own quoted band at the touch: buy at `ask_i - bid_j`, sell at
  `bid_i - ask_j`. Reported: the band's width in cents (p50, p90) and the share of intervals
  whose touch SELL price is `<= 0` - the quotes do not distinguish that interval's mass from
  zero.
- **C5** (receptions) ladders whose unit masses between consecutive rungs are not unimodal
  - descriptive only.

No verdict. A rate of zero is reported as a count with its denominator.

## Answer 2 - calibration of the interval, against settlement

**Statistic**: for a cell, `mean(h) - mean(p)` over its intervals. > 0: the interval
happened more often than priced.

**The verdict family (one family, registered now)**:

- every (stat, width, bin) cell: 7 widths x 7 bins for receptions, 2 x 7 for rush attempts
  = **63 cells**, of which those that exist are tested;
- every (stat, width) pooled over bins: **9**.

Up to **72 intervals**. A cell that does not exist is counted as not estimable, and the
number that do is reported (c-35 registered 55 and had 48; I expect well under 72 here,
because a width-1 interval cannot be priced above 0.45 and a width-6 one is rarely under
0.10).

**MDE, written down before the run.** The MDE of every cell is computed from the ladders
alone, with no outcome read: 2,000 simulated seasons in which each ladder's count is drawn
from that ladder's own implied cells (PAV-repaired, independent across ladders), the
statistic recomputed each time, `MDE = 2.8 x` its standard deviation. `--mde` does this with
`x` and `y` stripped from the rows before anything is computed, and its output,
`research/results/interval_mde.json`, **is committed before the outcome run**. Known bias,
stated now: the simulation treats ladders in one game as independent, so it understates the
SE where a game's players move together; the bootstrap SE at run time is printed beside it.

**Verdict rule** - one of exactly three, each reachable:

- **calibrated at this resolution**: no interval in the family survives BH. Quoted with the
  median and the largest pre-run MDE, because it is a statement about deviations that size.
- **mispriced**: at least one survives BH **and** its |estimate| exceeds its own pre-run
  MDE. Named cell by cell, with the cost line below printed beside each.
- **a deviation survives correction below its MDE**: at least one survives, none exceeds its
  MDE. Reported as that sentence; it is not "mispriced".

**Cost line for any survivor**: the two-leg executable cost (Answer 3's definition, 100
contracts a leg) over the intervals of that cell that have depth on both legs - bought if
realised > implied, sold otherwise - with the realised net per position and its game-block
interval, and the thinner leg's touch size. A survivor is a measurement; it is never
reported without that line and it is never called an edge.

## Answer 3 - what the second leg costs (numbers, no verdict)

Every price is `market_depth` VWAP at the stated size on the side being bought, plus the
taker fee on the whole order at that size, divided (`core.fees`; `/series` says
`quadratic`, taker M = 1). Nothing is priced at a mid; the mid states the value and nothing
else.

- **Minimum depth to be counted**: a leg counts only with a `market_depth` snapshot on the
  side being bought at most 600 s before entry AND a non-null VWAP at the size - the book on
  that side holds at least that many contracts. Sizes: **100 primary, 500 secondary**. Pairs
  without it get no cost and are counted. Coherence and calibration do NOT require depth
  (the quote is the object there); requiring it would cut 48 games to 18.
- **Buy the interval**: YES on the lower rung, NO on the upper. Pays 1, and 2 if `X` lands
  inside. Cost over mid `= (allin_yes_i - k_i) + (allin_no_j - (1 - k_j))`.
- **Sell the interval**: NO on the lower, YES on the upper. Pays 1 unless `X` lands inside.
- **The nearest single rung**: in the same ladder, at the same size, the one rung-side
  (YES at mid `k`, or NO at mid `1 - k`) with depth whose mid is nearest the value being
  bought (`p` for a buy, `1 - p` for a sell). Its cost over its own mid is the comparison.
- **Reported**, per stat, width, size and action: pairs with depth on both legs and games;
  mean and median two-leg cost in cents; the fee's share; each leg's cost separately; the
  nearest single rung's cost; the difference (two-leg minus single) with a game-block
  interval; cost as a share of the value bought; the break-even move in probit units,
  `probit(p + cost) - probit(p)`, beside the single rung's; capital posted per pair; the
  thinner leg's touch size. The same by implied-mass bin.
- **No test, no verdict, no family.** The difference carries an interval so its size can be
  read; it decides nothing.

## The 2026 pre-check - my stated implication, before the run

c-35 measured receptions ladders slightly too NARROW in 2026: dispersion D = +0.072
[+0.001, +0.141] pooled, not surviving correction.

**Does that imply interval mass is under-priced at wide widths? No - the opposite, for most
wide intervals.** A ladder that is too narrow puts too much mass near its centre and too
little in the wings. So:

- an interval that **straddles the central rung** (`i < c < j`) is **OVER-priced**:
  `realised - implied < 0`;
- an interval lying wholly in a wing is **under-priced**: `realised - implied > 0`;
- and because a wide interval on a ladder of 4-9 rungs almost always straddles the centre,
  the pooled wide-width cells should read **negative**, not positive.

Size, from D alone: on a normal PIT scale, 12 Var(PIT) = 1.072 corresponds to the true
spread being about 6.7% wider than the implied one (`Var(Phi(sZ)) = arcsin(s^2/(1+s^2))/2pi`
gives s = 1.067). A central interval priced at 0.38 / 0.68 / 0.87 / 0.95 should then
realise about 2.2 / 3.1 / 2.6 / 1.5pp less. **So the predicted effect is 1.5-3pp, negative,
largest for a straddling interval priced near 0.6-0.7.** I expect that to be below the MDE
of most cells; if it is, the honest reading is "not detectable", not "absent".

**Registered pre-check intervals (their own family, 9, directions fixed above):**
receptions, ladders with a central rung:

- `S_w`, straddling intervals of width w = 2..7 (6 intervals), and `S_all` pooled:
  predicted **< 0**;
- `U`, intervals starting at least two rungs above the central rung (`i >= c + 2`), all
  widths: predicted **> 0**;
- `Lo`, intervals ending at least two rungs below it (`j <= c - 2`), all widths: predicted
  **> 0** (expected thin: most ladders start one or two rungs below their centre).

**Reading, fixed now**: the implication is **confirmed in direction** iff `S_all`'s interval
lies below zero; **contradicted** iff it lies above zero; otherwise **not detected**, quoted
with its MDE. `U` is read the same way with the sign reversed. A result against the stated
direction is reported as a contradiction of this section, in those words.

**What this pre-check is not.** It is NOT a replication of c-35. The outcomes are the same
48 games c-35 used, re-aggregated, and I have read c-35's cell table (too much mass one
catch above the line, too little three or more above). Agreement here is arithmetic
consistency between two summaries of one sample. An out-of-sample test needs weeks 5+.

## Stop rules

- The cache does not hold 725 ladders, 3,987 rungs and 48 games, or its fee type is not
  `quadratic`: stop.
- Fewer than 5 games with depth on both legs at a size: Answer 3 is not read at that size.
- `market_log.db` is not opened. Credits: 0. Nothing is written to any store; nothing is
  published; no page is built.

## What this cannot say, known now

- One instant per game, three weeks, one venue. Nothing about another lead time: brief
  022's map says prop books widen sharply in-game.
- "Calibrated" can only mean "no deviation the size of the MDE"; the MDEs are in the
  committed file and are what the word is worth.
- The cost arm is 18 games, 16 of them week 3 (where depth was captured).
- The fee is computed once per leg at the VWAP; a fill that walks the book is billed per
  fill. Whether Kalshi nets collateral across two rungs of one event is not known to me and
  is not measured: capital posted is reported as the sum of the two legs.
- No resting order is simulated. A maker pays no fee on these series, and the fee is most of
  a taker's cost.
- Whether a sportsbook quotes such an interval ("no book quotes") is the roadmap's premise
  and is not tested here.

---

## Addendum 1 - written AFTER the registered run (2026-10-08), with the results in view

The registered run used `research/interval_mass.py` as committed before it (`a62133d`, plus
a guard against an empty denominator in the C5 print, which no real number touches). It ran
once. One thing was added afterwards. **It changes no registered interval and no verdict**:
the result JSON before and after is identical apart from one added key
(`calibration.posthoc_null_se`), and the log differs by three added lines.

- **POST HOC sensitivity, outside the verdict: z on the pre-run null SE.** The registered
  rule divides each estimate by its bootstrap SE. One surviving cell, receptions width 3
  priced 0.05-0.10, has 22 intervals on 18 games and **not one of them hit**. Every bootstrap
  resample of an all-zero cell also realises zero, so its bootstrap SE (0.24pp) measures
  only the spread of the prices, and the cell reads z = -34 - the only Holm survivor in the
  run. Under the ladder's own cells its SE is 6.1pp and z = -1.36. This is the
  zero-variance trap CLAUDE.md records from brief 022, one step removed: the variance is
  not zero, it is merely not the variance of the thing tested. The registered verdict rule
  already refuses to call this cell mispriced (8.3pp against a pre-run MDE of 17.1pp),
  which is what the MDE condition is for. The added table recomputes every p-value with the
  outcome-free SE and re-applies BH and Holm, so the reader can see how much of the
  registered survivor count depends on which SE is used.

Nothing else was changed: no width, bin, instant or correction. Specifications tried: one.
