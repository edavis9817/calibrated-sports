# C35 - the edges inside one player's own ladder: pre-registration

Unit c-35 (track C), written 2026-10-08, **before `research/ladder_edges.py` exists** and
before any outcome-bearing number for this unit was computed. Base: `c-34-known-before-kickoff`
(`0bb330d`).

## Scope, stated so it travels with every number

Kalshi `KXNFLREC` (receptions) and `KXNFLRSHATT` (rush attempts), the over side, NFL 2026
regular season **weeks 2, 3 and 4**, one instant per player-game: **kickoff - 180 minutes**
(c-19's entry instant, kept so the population is comparable with c-19 / c-24). A *ladder* is
every rung of one (player, stat, game). Week 1 is out (its quotes were pruned); week 5 is out
(not settled). The model is `models.baseline`, refit walk-forward exactly as c-19 step 4 did.
Nothing here is about sportsbooks except the Q1 reference table, which is labelled.

Brief 019 H1 (rung ORDER: 892 monotonicity episodes, none with ten contracts on both legs) is
**not re-run**. Monotonicity appears here only as a count of ladders the isotonic step touched.

## What was read before this file was written (no outcome, no model number)

- Mapped Kalshi over-rungs / ladders by week: wk2 1,482 / 243, wk3 1,502 / 248, wk4 1,589 / 263.
- All of those markets still have quote rows in `market_log.db` (a-60's retention hold covers
  weeks 2-3; week 4 is inside the 14-day window).
- `market_depth` exists for **107 of 1,482** week-2 markets, **1,297 of 1,502** week-3 markets
  and **119 of 1,589** week-4 markets. Question 3 therefore lives almost entirely on week 3.
- `nfl_player_week` holds 2026 weeks 1-4. `outcome_close` holds 25,402 settled book closes on
  these two stats for 2023-2025.
- The schema of `market_depth` (touch, `vwap_100/500/1000/5000`).

## Common definitions

- **Rung quote at entry**: the last `source='live'` Kalshi quote at or before entry, two-sided
  (`0 < bid < ask < 1`) and at most 600 s old (c-19's `kalshi_at`, unchanged). `k` = mid.
- **Settlement**: `core.settlement.settle`, as c-19 step 4. Void / unsettled rungs are dropped
  and counted. `X` = the settled actual.
- **Ladder survival**: rungs sorted by line; `S(L_i) = k_i`. Where the mids are not
  non-increasing in the line, a pool-adjacent-violators pass makes them so; the number of
  ladders touched and the mean absolute adjustment are reported. `S(-0.5) = 1`, `S(inf) = 0`.
- **Central rung**: the rung whose mid is nearest 0.5. A ladder has a central rung only if that
  mid is in [0.25, 0.75].
- **Position**: `nfl_player_week.position` on the player's own row for that game, else the
  snap-count row (as c-19). Groups: receptions-all, receptions-WR, receptions-TE,
  receptions-RB, rush-attempts-all.
- **Blocks**: every interval is a 2,000-draw block bootstrap over **games**. The brief asks for
  player-game blocks; a game contains its player-games and a slate shares a scoring
  environment (CLAUDE.md, week-1 CLV), so the game block is the coarser and more conservative
  of the two. The player-game-block interval is printed beside the primary Q2 test.
- **An interval on fewer than 5 games is not read.** MDE = 2.8 x bootstrap SE.
- **Multiplicity**: z = estimate / bootstrap SE; Benjamini-Hochberg at q = 0.10 within each
  question's registered family; survivors split by sign.
- **Fees**: `core.fees` (the `/series` `fee_type`, never the PDF): REC and RSHATT are
  `quadratic`, taker M = 1 at rate 0.07, maker M = 0. The order fee is computed on the whole
  order at the stated size and divided. The fee type is re-read from the `/series` snapshot
  in `board_019.db` and, if the free unauthenticated endpoint answers, live; a mismatch stops
  Q3.

## Q1 - the shape between the rungs

For each ladder with at least 3 usable rungs.

**Q1-A, unit cells (verdict-bearing).** For an integer count `x`, the implied mass
`q(x) = S(x - 0.5) - S(x + 0.5)` exists only where both bounding rungs are quoted (`x = 0`
needs only the 0.5 rung). Cells are indexed by offset `d = x - ceil(L_c)` from the central
line, `d` in -3..+3, plus two tail cells: everything below the lowest quoted rung and
everything above the highest. Statistic per (group, cell): `mean(1[X in cell]) - mean(q)`,
over ladders where the cell exists. 5 groups x 9 cells = **45 intervals**.

**Q1-B, dispersion and level in one number each (verdict-bearing).** With the ladder's cells
`j` (quoted rungs as boundaries, masses `q_j`, cumulative `F_j`), the mid-PIT of the realised
count is `t = F_{j-1} + q_j / 2` for the cell `X` fell in. Under a correct ladder
`E[t] = 1/2` and `Var(t) = (1 - sum q_j^3) / 12` exactly. Per group:

    level       M = mean(t - 1/2)                          > 0: realised above the ladder
    dispersion  D = mean(12 (t - 1/2)^2 - (1 - sum q_j^3))  > 0: ladder too NARROW (tails underpriced)
                                                           < 0: ladder too WIDE

5 groups x 2 = **10 intervals**. The variance identity is unit-tested by simulation.

**Q1-C, the realised reference (DESCRIPTIVE, no verdict).** The realised distribution of the
stat "for that player type": 2023-2025 settled book closes with `p_bench` in [0.40, 0.60],
typed by (stat, position that week, main line). For each Kalshi ladder whose central mid is in
[0.40, 0.60], the reference mass at offset `d` is the historical frequency of `X = x` among
player-games of the same (stat, position, line), needing n >= 30 or the cell is dropped and
counted. Reported per group and `d`: Kalshi implied mass, historical realised frequency, 2026
realised frequency, side by side. It is descriptive because the conditioner differs (a
de-vigged book line against an exchange mid), the seasons differ and the venue differs; it is
there because 3 weeks of outcomes is thin and 25,402 is not.

**Verdict rule, Q1.** "The ladder's implied shape departs from what happened" in a group iff
a Q1-A cell or the Q1-B dispersion survives BH over the 55 registered intervals with >= 5
games. Otherwise: "no detectable departure", quoted with the MDE of D and of the largest-|z|
cell. Where it departs, the table ranked by |z| is the answer to "where". A level departure
(M) is reported and is NOT a shape finding.

## Q2 - is the whole ladder shifted, and is that better than one rung

Model probability `m_i` per rung from the walk-forward baseline fit (c-19 step 4 procedure,
constants refit on seasons <= 2025). `m`, `k` clipped to [0.01, 0.99]. Ladders with >= 3 rungs.

- **Rung gap** `z_i = probit(m_i) - probit(k_i)`.
- **Ladder shift** `c = argmin_c sum_i (Phi(probit(k_i) + c) - m_i)^2` - the single shift of
  the market's own ladder that best reproduces the model's, fitted in probability space so a
  tail rung does not dominate.
- **Decomposition (descriptive, with intervals).** `SS_total = sum (m_i - k_i)^2`.
  Level share = `1 - SS_resid(shift) / SS_total`, pooled over ladders. A second fit adds a
  scale, `Phi(a + b probit(k_i))`; scale share = the further reduction; the rest is residual
  shape. Read: "mostly level" iff the pooled level-share interval lies above 0.5, "mostly not
  level" iff below, otherwise undetermined.
- **PRIMARY TEST.** Does the ladder-level disagreement order outcomes better than the rung's
  own gap? Using c-24's `research.ranking_calibration.wauc` (concordance within strata),
  strata = stat x market-mid decile (`floor(10 k)`), so the question is who orders the outcome
  among rungs the market prices alike:

      A_R = wauc(z_i, y)        A_L = wauc(c_ladder, y)        dA = A_L - A_R

  **dA interval above zero: ladder-level orders better. Below: rung-level orders better.
  Contains zero: no detectable difference, quoted with its MDE, which is printed BEFORE the
  estimate.** The MDE is 2.8 x the bootstrap SE and is not known at the time of writing; I
  could not compute it without the outcomes.
  **Override, fixed now:** if neither `A_R - 0.5` nor `A_L - 0.5` has an interval above zero,
  the disagreement carries no detectable information about the outcome at either level and
  the verdict is "a comparison of two nulls", whatever dA reads.
- **Secondary, same rule, reported beside it:**
  - leave-one-out: `c` refitted without rung `i` (does the REST of the ladder say anything
    about this rung);
  - one observation per ladder: Spearman of `c` with the ladder's mid-PIT `t` against
    Spearman of the central rung's `z` with `t`, and their difference;
  - player-game blocks instead of game blocks for dA.
- **Reproduction guard.** On weeks 2-3 the rung population should reproduce c-24's P2
  (n = 2,513, Brier(model) - Brier(Kalshi mid) = +0.0121). A difference is reported with its
  cause, not hidden; it does not stop the run.

Family for BH: dA, A_R - 0.5, A_L - 0.5, LOO dA, the Spearman pair and difference, x
(all, receptions, rush attempts) = **21 intervals**.

## Q3 - the cheapest rung to express a view

An execution question. **Every number here is an executable price from `market_depth`
(VWAP at size, the side being bought) plus the taker fee at that size. Nothing is priced at a
mid.** The mid is used for one thing only: to state the view.

- **Eligible rung**: usable quote at entry AND a `market_depth` snapshot for the side being
  bought at most 600 s before entry with a non-null VWAP at the size. Ladders with >= 3
  eligible rungs. Sizes: **100 contracts primary**, 500 secondary. Rungs without depth are not
  given a cost and are counted.
- **The view**: the whole ladder is mis-centred by `delta` probit units:
  `fair_i = Phi(probit(k_i) + delta)`. `delta > 0` buys YES at `vwap(buy_yes)`; `delta < 0`
  buys NO at `vwap(buy_no)`. Stated views: +/-0.10, **+/-0.25 primary**, +/-0.50.
- **Per rung**: net EV per contract `e_i = fair_side - price - fee(price, C) / C`; per dollar
  `e_i / price`; and the **break-even view** `delta*_i`, the smallest |delta| at which
  `e_i >= 0`. The break-even view is view-free and is the cost measure.
- **Fixed rules, named now, by the rung's mid**: nearest 0.50 (central), nearest 0.30,
  nearest 0.70, nearest 0.15, nearest 0.85. Reported per rule and side: median and mean
  `delta*`, mean `e` at each stated view, touch size, cost in cents (price + fee - mid).
- **How much the choice is worth (verdict-bearing).** Games are split in two by sorted
  `game_id` (alternating). The rule with the highest mean `e` (primary view, size 100) on one
  half is scored against the central rung on the other half, both ways; the cross-fitted mean
  difference `e(chosen rule) - e(central)` gets a game-block interval, separately for the
  up-view and the down-view. **Above zero: the choice of rung is worth that much. Contains
  zero: not shown to be worth anything, with the MDE.**
- **Descriptive only**: the per-ladder best rung minus the central rung, the mean rung and
  the worst rung. Best-of-n is >= 0 by construction, has no null, and is quoted as a
  magnitude only.
- **Realised, no view (descriptive, intervals shown)**: buying YES, and separately NO, at each
  rule's rung at the executable price and holding to settlement: mean `y - price - fee`.
  This is the only place Q3 reads an outcome.
- **No edge is claimed by Q3 under any result.** The view is hypothetical; c-24 and brief 023
  already say the model has none worth holding. Q3 reports the cost of holding one.

Family for BH: the two cross-fitted differences x two sizes = **4 intervals**. Everything else
in Q3 is descriptive.

## Stop rules

- Fewer than 10 games or fewer than 100 ladders at entry: Q1 and Q2 are not read.
- Fewer than 5 games with >= 3 eligible rungs at size 100: Q3 is not read at that size.
- The `/series` fee type disagreeing with `core.fees`: Q3 stops.
- `market_log.db` is opened `mode=ro` only, read into a scratch cache in one short session,
  and closed before any fitting other than the model refit (which opens it `mode=ro` itself).
- Credits: 0. Nothing is written to any store; nothing is published.

## What this cannot say, known now

- One entry instant per game: nothing about how a ladder's shape evolves toward kickoff.
- Three weeks. A cell departure smaller than its MDE is not ruled out.
- Position is the player's row for that game; the Q1-C reference uses book lines, not Kalshi.
- Q3 prices a one-crossing entry held to settlement. It does not simulate a resting order
  (maker, fee-free on these series) - briefs 018/019 and c-20 measured that path.
- The fee is computed at the VWAP; a multi-level fill is billed per fill, which can add a
  cent of rounding per level.

---

## Addendum 1 - written AFTER the first full run (2026-10-08), with the results in view

The first run used `research/ladder_edges.py` exactly as committed at `cc7a5dd`
("as first written"). Five things changed afterwards. None
changes a registered interval: the diff of the result JSON before and after touches only
the Q1-C reference column and the |z| ranking list, and adds keys.

- **(a) Bug fix toward the registered definition, Q1-C.** For the two `-all` groups the
  first run typed the 2023-25 reference by (stat, line) with positions pooled, where this
  file says (stat, position, line). Pooling let in 2023-25 rows with no position at all
  (played, no stat row, settled at 0), which moved mass out of the cells near the line.
  Now keyed on each ladder's own position in every group. Q1-C is descriptive; no verdict
  read it.
- **(b) Display rule.** The "largest |z|" list ranked an interval on 4 games first. It was
  never read (p = 1 in BH by the rule above) and it is now not ranked either.
- **(c) POST HOC table, outside the BH family: cells between consecutive quoted rungs.**
  Rush-attempt rungs sit 3 apart, which I had not looked at before registering, so no unit
  cell exists there and Q1-A covers rush attempts on its two tails only (48 registered
  Q1 intervals exist, not 55). The table indexes the cell between consecutive rungs from
  the central rung, for every group.
- **(d) Q3 per-dollar.** "Per dollar" is registered as a reported quantity and the first
  run computed it without printing it. It is now printed per rule, and a POST HOC cross-fit
  on EV per dollar staked is added at every stated view. It is outside the BH family and
  carries no verdict.
- **(e) POST HOC depth join for any Q1-A cell that survives BH.** The brief's rule is that
  no candidate is reported without its `market_depth` join. The registered Q1 has no
  executable arm, so one is added for survivors only: every leg at its VWAP at 100
  contracts plus the taker fee, realised net with a game-block interval.

Also changed, with no effect on any number: `Blocks.boot` reads `BOOT` at call time rather
than freezing it in a default argument (CLAUDE.md, the default-argument row).

**Replication target fixed now, before week 5 settles.** The one registered survivor is
receptions-RB, cell d=+1, realised BELOW implied. If this is re-run on weeks 5+, that cell
and that direction are the test; nothing else from this run is a hypothesis.
