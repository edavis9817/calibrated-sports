# C40 - the counterfactual ledger: pre-registration

Unit c-40, track C, NFL. Written and committed **before `research/counterfactual_ledger.py`
exists** and before any model input of any ledgered lean has been reconstructed. What had been
read when this was written is listed in section 9, so the claim "before looking" can be checked.

The question (roadmap item 1): for every published lean that missed, which single input of the
model would have had to differ to put the model on the other side, and by how much.

## 1. The brief's input list does not describe this ledger's model

The brief names the inputs as "the rating, the home term, the pace/usage term, and the line
itself", and says "the inputs are the model's own". Those two statements disagree. Every lean in
`board/nfl/ledger.parquet` is a player prop (receptions, rush attempts) priced by
`models/baseline.fit_player_stat`, model version `baseline-usage-0.4+03571d4710af` on all 600
published leans. That model has no rating and no home term - they belong to the game model
(`models/game.py`), which writes nothing to this ledger.

Decision taken (unattended): the list below is the model's own, read off `fit_player_stat`. It is
closed by this commit and nothing is added after a result is seen.

## 2. Population

- Source: `board/nfl/ledger.parquet`, snapshotted at 2026-10-08T20:16Z, sha256
  `760b93be22fb1464df139457b011a67687ba1c70a013ba06f8e55a90d225306d`, read through
  `core.record.build_published`. The script takes the snapshot path as an argument and prints its
  hash; a different file is a different population and must be reported as such.
- Counts in that snapshot: 1,112 ledger events; 600 published leans; 496 graded (256 cleared,
  240 missed, 0 push); 16 void; 88 ungraded; 0 excluded as not pre-kickoff. Graded weeks: 2026
  weeks 3 and 4, 31 games. (a-70 read 1,040 events at 06:50Z the same day; the roadmap's 471 is
  an earlier graded count. Neither is used.)
- **Graded** = `build_published` gives the lean `result` in {cleared, missed, push}.
- **Missed** = `result == "missed"`. **Cleared** = `result == "cleared"`. Push, void and ungraded
  leans are outside every share and every test, and are counted.
- **Reconstruction gate.** The ledger stores the probability, not the inputs. Each lean's fit is
  rebuilt with the model's own code as of the lean's `read_at`, with the position and team the
  Board's read file recorded for that row. A lean enters the analysis only if the rebuilt
  P(over) at its line is within 0.0001 of the ledgered `model_p_over`. Leans that fail are
  counted, listed and excluded - a counterfactual on a fit that is not the published fit is not a
  counterfactual. If fewer than 80% of graded leans pass, the unit reports a partial and draws
  no verdict.
- The facts are read from tables copied out of `market_log.db` in one `mode=ro` session into a
  scratch file; the scratch copy may carry extra indexes. `models/` is not edited.

## 3. The inputs (k = 5, closed)

The model, per lean: `mean = w * own_mean + (1 - w) * group_mean`; a variance built from that
mean, the two dispersions, the sampling term and the drift term; a negative binomial; P(over) at
the line. The five named inputs:

| # | name | what it is in `fit_player_stat` | allowed range |
|---|---|---|---|
| 1 | `own_mean` | `prior.mean`, the player's own prior per-game usage | x0.05 to x20 |
| 2 | `group_mean` | `pos_prior["mean"]`, the position-and-role shrinkage target | x0.05 to x20 |
| 3 | `weight` | `w`, the weight on the player's own history after the team-change and coach-change penalties | (0, 1] |
| 4 | `dispersion` | the total predictive variance (`var_total`) | x0.05 to x20, never below `MIN_VMR * mean` |
| 5 | `line` | the threshold, moved in whole steps | 0.5 and up, at most 30 steps either way |

Each perturbation changes that one number and re-runs the model's own arithmetic with everything
else as read: moving `own_mean` also moves the player's variance-to-mean ratio because the code
divides by it; moving `weight` also moves the sampling term. When the line moves, the market's
P(over) is held at the ledgered value - the ledger does not carry the book's price at other
lines. That makes the line counterfactual "the same price hung at a different number", and it is
stated wherever the line is named as the flipping input.

The market's probability is not an input. It is the reference the lean is defined against, and
moving it by `gap_pp + T` flips every lean by construction.

## 4. Flip, distance, flipping input

- **Flip** (the registered target): the perturbed model would have published the opposite side.
  For an over lean, `100 * (p' - mkt_p_over) <= -T`; for an under lean, `>= +T`; `T` is the
  lean's own `lean_threshold_pp`.
- **Withdraw** (descriptive only, no test): the perturbed model would have published no lean,
  `|gap'| < T`.
- **Distance** of an input on a row: the smallest `|ln(x' / x)|` over allowed `x'` that reaches
  the flip. A proportional scale is used because it is the only one the five inputs share without
  a per-input constant chosen by hand. An input whose as-of value is 0, or that cannot reach the
  flip inside its allowed range, has no distance on that row.
- **Flipping input** of a row: the input with the smallest distance. Ties go to the earlier row
  of the table in section 3.
- **No single-input flip**: no input has a distance. Reported as a count and a share of missed
  rows, at the full allowed range and also at distance caps of ln 1.10, ln 1.25, ln 1.5 and ln 2
  (a 10%, 25%, 50% and 100% proportional change). That curve is the ceiling on the idea.

## 5. The claim and the tests

**Claim (the brief's):** the flipping input is concentrated - one input is the flipping input on
materially more than 1/5 of missed rows.

**A second claim this registration adds, and why.** The distance scale and the model's
arithmetic favour some inputs mechanically: the line moves the whole distribution while
`own_mean` moves only the `w` share of the mean, and the line moves in whole steps. So a share
above 1/5 can be produced by the model's structure alone and would then be equally true of the
leans that cleared. A share that says something about *missing* must differ between missed and
cleared rows. The same computation is therefore run on cleared rows (the input that would have
flipped a cleared lean to the losing side), and the contrast is tested.

- **T1, per input i:** share of missed rows (with a flipping input) whose flipping input is i,
  minus 1/5.
- **T2, per input i:** that share among missed rows minus the same share among cleared rows,
  bootstrapped as one quantity.
- **Blocks:** games. 2,000 bootstrap draws resampling games with replacement, seed 40. Interval:
  2.5 and 97.5 percentiles. p-value: two-sided normal on estimate / bootstrap SE; a zero-variance
  bootstrap is p = 1; a test on fewer than 5 games is p = 1.
- **Omnibus:** a Pearson chi-square of the five missed-row counts against uniform is printed for
  reference. Rows of one game are not independent, so it is not used for the verdict.

**Cuts (registered, closed):** all rows; by market (receptions, rush attempts); by side (over,
under); by gap band (4-6, 6-8, 8+). Eight slices x (5 T1 + 5 T2) = **80 tests, one Holm family**
at 0.05. No other cut is tested. Anything else printed is labelled descriptive.

**Power.** Every test reports its minimum detectable effect at 80% power (2.80 x bootstrap SE)
and the ratio estimate / MDE. A ratio near 1 with an adjusted p above 0.05 is reported as not
detected, never as a finding.

## 6. Verdict rule

Read on the all-rows slice, with Holm-adjusted p over all 80 tests:

1. **Concentrated, and about the misses**: some input has T1 > 0 with adjusted p < 0.05 **and**
   T2 > 0 with adjusted p < 0.05. That input is over-represented when the lean missed.
2. **Concentrated, and structural**: some input passes T1 and none passes both. The flipping
   input is concentrated on missed and cleared rows alike; it describes the model's arithmetic
   and says nothing about why a lean missed.
3. **Spread evenly**: no input passes T1. The ledger says the misses are noise.

A sub-slice result is reported with its adjusted p and changes no verdict.

Three stated limits, registered now: two graded weeks and 31 games is a small block count, and a
game-block interval is narrower than a week-block one (`core.record` refuses a week-block
interval at two weeks; the brief asks for game blocks, so game blocks are used and the caveat
travels with every interval). A flipping input is a statement about the model's arithmetic, not
about the truth - "the line would have had to be one higher" does not say the line was wrong.
And rungs are not claims: one player can carry several leans across reads; `lean_id` is the row.

## 7. Output

`research/results/counterfactual_ledger.json` (aggregates, every test) and
`research/results/counterfactual_ledger_rows.json` (one object per graded lean: ids, market,
line, side, result, actual, the as-of value of each input, each input's flip value and distance,
the flipping input, the withdraw equivalents). No pick language; `cleared` / `missed` only.
Findings in `docs/findings/c40-counterfactual-ledger.md`.

## 8. Not done by this unit

No page, no model change, no refit, no merge, no publish, no weekly refresh.

## 9. What had been read before this commit

`core/record.py`, `core/board.py` (head), `jobs/board_read.py` (`fresh_rows`, `model_prob`,
`resolve_player`), `models/baseline.py`, `models/features.py`; the ledger's column names, event
counts, the cleared / missed / void / ungraded counts by market, the model version, the line
fractions, and the number of graded games - all from `build_published`. No fit had been rebuilt,
no input value read, and no outcome compared with any input.
