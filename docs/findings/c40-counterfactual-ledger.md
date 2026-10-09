# c-40 - the counterfactual ledger

Pre-registration: `docs/C40-counterfactual-preregistration.md` (commit `8ce15b1`, before the
script existed). Script: `research/counterfactual_ledger.py`. Aggregates and every test:
`research/results/counterfactual_ledger.json`. Per-row output:
`research/results/counterfactual_ledger_rows.json`. Run log: `research/results/counterfactual_ledger.log`.

Scope of everything below: the NFL Board ledger as snapshotted 2026-10-08T20:16Z (sha256
`760b93be...5306d`), player props on receptions and rush attempts, model
`baseline-usage-0.4+03571d4710af`, graded leans of 2026 weeks 3 and 4, 31 games.

## 1. Verdict

**Concentrated, and structural** - the second of the three registered outcomes.

The player's own prior mean (`own_mean`) is the flipping input on 66.1% of missed leans, against
a null of 20%: +0.461 [+0.369, +0.557], Holm-adjusted p 2e-19 over the 80-test family, 3.39 times
its minimum detectable effect. It is also the flipping input on 68.0% of the leans that
**cleared**. The contrast, missed minus cleared, is -0.019 [-0.099, +0.067], adjusted p 1, 0.16
of its MDE.

So the concentration is real and is not detectably about missing. It is what this model's
arithmetic looks like: one number, weighted 0.73 at the median, carries most of the mean. The
ledger does not say which input was wrong when a lean missed. No input is detectably
over-represented among the misses: a difference in share under about 0.11-0.12 would not have
been seen on 31 games.

**Corrected by c-45 from f-31 run 4 (`docs/findings/c45-corrections.md`): the 66.1% is a
description of the model, not of misses.** `own_mean` is the flipping input on 67.1% of all 477
leans whatever the outcome, and with the missed/cleared label permuted within game 66.1% sits at
the 62nd percentile of its null (p about 0.75, drawn twice independently). The Holm p of 2e-19
rejects a 1/5 null that the model's structure violates on its own. Never cite the 66.1% without
the 68.0%.

## 2. Population

| | n |
|---|---|
| ledger events | 1,112 |
| published leans | 600 |
| graded | 496 (256 cleared, 240 missed, 0 push) |
| void / ungraded | 16 / 88 |
| graded leans whose fit rebuilds to the ledgered P(over) | 477 (96.2%) |
| analysed: missed / cleared | 227 / 250 |

The brief quoted 1,040 rows (a-70's read at 06:50Z) and the roadmap 471; both are earlier reads
of a file that is appended to. The 477 analysed leans cover 233 players.

Nineteen graded leans (13 missed, 6 cleared) rebuild to a probability 0.0001 to 0.0111 away from
the ledgered one and are excluded and listed in the aggregates file. **All 19 are week 4**, which
this unit did not report until f-31 run 4 found it; by f-31's bound they could move the
missed-minus-cleared contrast only within [-0.062, +0.015]. The cause was not
established; facts restated since the read would produce exactly this and so would a position or
team that differs from the read file's.

On all 477, the replica of the fit's arithmetic matched the model's own mean, dispersion, weight
and P(over) - the script raises otherwise, and did not.

## 3. Shares, all rows

| input | missed | cleared | T1: missed - 1/5 | T2: missed - cleared |
|---|---|---|---|---|
| own_mean | 0.661 | 0.680 | +0.461 [+0.369, +0.557], adj p 2e-19 | -0.019 [-0.099, +0.067], adj p 1 |
| group_mean | 0.110 | 0.132 | -0.090 [-0.142, -0.034], adj p 0.079 | -0.022 [-0.098, +0.054], adj p 1 |
| weight | 0.000 | 0.004 | -0.200, zero variance, p 1 | -0.004 [-0.013, +0.000], adj p 1 |
| dispersion | 0.000 | 0.000 | -0.200, zero variance, p 1 | 0, zero variance, p 1 |
| line | 0.229 | 0.184 | +0.029 [-0.034, +0.088], adj p 1 | +0.045 [-0.035, +0.121], adj p 1 |

Intervals are game-block bootstraps, 2,000 draws, 31 games.

Across the 80 tests, 8 survive Holm at 0.05: `own_mean` on T1 in every one of the eight slices.
**No T2 survives, and none of the 40 T2 tests reaches p < 0.05 even unadjusted** (smallest
0.065, the line on receptions). The largest positive T2 estimate is the line in the 8+ band, +0.088
[-0.010, +0.181], 0.64 of its MDE - not detected.

Power, so the null is not over-read: the T2 test on `own_mean` could detect a difference of 0.12
in share; on the line, 0.11. A real difference smaller than that between missed and cleared
leans would not have been seen on 31 games.

`weight` and `dispersion` are never the flipping input on a missed lean. That is a property of
the scale and the model, not a discovery: the weight can flip only 19 of 227 missed leans
anywhere in (0, 1], and the variance only 60 of 227 inside x0.05 to x20.

## 4. The ceiling

At the registered range, **0 of 227** missed leans have no single-input flip. That zero carries
little: the range allows a twenty-fold change. The curve is the number to read.

| proportional change allowed on one input | missed leans flipped | share |
|---|---|---|
| 10% | 3 | 0.013 |
| 25% | 51 | 0.225 |
| 50% | 120 | 0.529 |
| 100% | 172 | 0.758 |
| x20 | 227 | 1.000 |

The median missed lean needs its nearest input moved by 45%; the median cleared lean, 49%. The
cleared curve is the same shape (0, 49, 130, 195 of 250). Median change needed, per input, on
missed leans: own mean 50%, line 67% (one whole step at a low line), group mean 118%.

The reason is the size of the disagreements, not the misses. 378 of the 477 leans sit in the 8+
band and the median absolute gap is 14.67 points. A lean that far from the market is not one
small input error away from the other side.

## 5. What a page could and could not say

Each row of the per-row file carries the lean, its side, cleared or missed, the actual, the
as-of value of each input, and per input the value that would have flipped it and the
proportional distance, with null where the input cannot reach it. That renders.

What it supports is a description of the model: "this lean stood on the player's own average;
it would have taken a 50% lower average to put the model on the other side". What it does not
support is a diagnosis of the miss. The same sentence is true of the leans that cleared, at a
rate this sample does not distinguish from the misses' (68.0% against 66.1%; a gap under about
0.12 would not have been seen).

A line counterfactual holds the market's probability at its ledgered value - the same price at a
different number - because the ledger does not carry the book's price at other lines.

## 6. Departures and corrections

- **The input list is the model's, not the brief's.** The brief named a rating, a home term, a
  pace/usage term and the line. The model that wrote every lean in this ledger has no rating and
  no home term. Registered before the script existed (pre-registration, section 1).
- **A registered rule was mis-implemented in the first run and corrected.** "A zero-variance
  bootstrap is p = 1" was coded as `se == 0`; a share that is 0 in every draw has a bootstrap sd
  near 1e-17, so 19 such tests entered the Holm family at p = 0. The second run (same rows, same
  seed, no refit) applies the rule as registered. Every estimate is unchanged, 19 p-values
  changed, the verdict is the same, and both run logs are in the log file.
- The second claim (T2, missed against cleared) was added by the pre-registration, not by the
  brief. Without it this unit would have reported the brief's success condition as met.

## 7. Not established

- Why a lean missed. This unit shows the flipping input does not distinguish misses from clears;
  it does not show the misses are noise in any wider sense.
- Anything about the truth of an input. A flip value is arithmetic on the model, not a claim that
  the player's average or the line was wrong by that amount.
- Any other distance scale. The proportional scale favours inputs that move the whole mean; a
  scale in units of each input's own uncertainty could rank them differently and was not run.
- More than two graded weeks. Game blocks were used as the brief asked; `core.record` refuses a
  week-block interval at two weeks, so these intervals are narrower than a week-block reading
  would allow.
- The 19 excluded leans, and why they do not rebuild. All 19 are week 4.
