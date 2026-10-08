# c-42 - the counterfactual ledger on an uncertainty scale

Pre-registration: `docs/C42-uncertainty-scale-preregistration.md` (commit `b69b376`, before the
script existed). Script: `research/counterfactual_uncertainty.py` (`90edc7d`, as first written).
Aggregates and every test: `research/results/counterfactual_uncertainty.json`. Per-row output:
`research/results/counterfactual_uncertainty_rows.json`. Run log:
`research/results/counterfactual_uncertainty.log`. One run; nothing changed after it.

Scope of everything below: c-40's 477 leans (NFL Board ledger snapshot 2026-10-08T20:16Z, sha256
`760b93be...5306d`; player props on receptions and rush attempts; model
`baseline-usage-0.4+03571d4710af`; 227 missed, 250 cleared; 2026 weeks 3-4, 31 games), and the
**three** of the model's five inputs that have an as-of standard error.

## 1. Verdict

**Same ranking** - both registered conditions hold.

Moved in units of its own as-of standard error, the player's own prior mean (`own_mean`) is the
flipping input on **97.4% of missed leans** (221 of 227), against a null of 1/3: +0.640
[+0.615, +0.658], Holm-adjusted p below 1e-300 over the 48-test family, 20 times its minimum
detectable effect. It is the input c-40 ranked first. c-40's ranking was not produced by the
proportional ruler.

**It is also the flipping input on 98.0% of the leans that cleared** (245 of 250). Missed minus
cleared is -0.006 [-0.036, +0.022], adjusted p 1, 0.16 of its MDE. A ranking that is identical on
misses and clears is not a diagnostic of a miss, on either scale. None of the 24 missed-against-
cleared tests reaches p < 0.05 unadjusted.

## 2. What the agreement is, and is not

The pre-registration wrote down, before the run, when `own_mean` would be the nearer of the two
means in standard errors: when `w * SE_own > (1 - w) * SE_group`. That inequality holds on **466
of the 468 rows** where both standard errors exist. `own_mean` is the flipping input on exactly
466 of the 468 rows where it has a standard error at all; the 11 rows that go to `group_mean` are
9 rows where `own_mean` has no standard error (5 with one prior game, 4 with a zero-variance
history) and 2 others.

So the result is the model's arithmetic a second time. The fit puts a median weight of 0.73 on a
number estimated from at most 21 games (median relative standard error 14%) and 0.27 on a number
averaged over a median 32 players (6.5%). The heavily weighted input is also the loosely known
one, so it is the nearest on either ruler. **The two scales agree about structure. This is not a
second, independent confirmation of anything about why a lean missed**, and c-40's reading -
concentrated and structural - stands unchanged.

## 3. Two of five inputs could not be put on this scale

The brief asked for all five inputs in units of their own as-of uncertainty. Two have none on
this data, and were excluded by name in the pre-registration, before any distance was computed:

- **`weight`**: `n / (n + 6)` times two penalties. `n` is exact and the constants are judgment
  calls with no fitting script; no fit yields a standard error.
- **`line`**: a posted line is exact. The only as-of stand-in is disagreement between the
  benchmark books, and 409 of the 477 leans have one listed line across all three.

So the scale question is answered for `own_mean`, `group_mean` and `dispersion`, with a null of
1/3. **For the line - c-40's second-ranked input, the flipping input on 22.9% of its missed
leans - the question cannot be asked.** Nothing here says where the line would rank on an
uncertainty scale.

## 4. Shares, all rows, on one input set

| input | uncertainty scale: missed / cleared | proportional scale, same three inputs: missed / cleared | c-40, five inputs: missed / cleared |
|---|---|---|---|
| own_mean | 0.974 / 0.980 | 0.846 / 0.840 | 0.661 / 0.680 |
| group_mean | 0.026 / 0.020 | 0.154 / 0.160 | 0.110 / 0.132 |
| dispersion | 0.000 / 0.000 | 0.000 / 0.000 | 0.000 / 0.000 |
| line | excluded | excluded | 0.229 / 0.184 |
| weight | excluded | excluded | 0.000 / 0.004 |

The middle column is c-40's own distances restricted to the three inputs - no new distance. The
order is the same on both scales, for missed and for cleared: `own_mean`, `group_mean`,
`dispersion`. Row by row the three-input flipping input is the same on 409 of 477 leans (85.7%);
66 move from `group_mean` to `own_mean`, 2 the other way.

| test, all rows | estimate | interval | adj p | realised MDE | registered MDE |
|---|---|---|---|---|---|
| T1 own_mean (share - 1/3) | +0.640 | [+0.615, +0.658] | < 1e-300 | 0.032 | 0.086 at a share of 0.9 |
| T1 group_mean | -0.307 | [-0.325, -0.282] | 7e-159 | 0.032 | 0.136 at 1/3 |
| T1 dispersion | -0.333 | zero variance | 1 | - | - |
| T2 own_mean (missed - cleared) | -0.006 | [-0.036, +0.022] | 1 | 0.041 | 0.128 worst case |
| T2 group_mean | +0.006 | [-0.022, +0.036] | 1 | 0.041 | 0.128 worst case |
| T2 dispersion | 0 | zero variance | 1 | - | - |

Game-block bootstrap, 2,000 draws, 31 games. 10 of 48 tests survive Holm: `own_mean` and
`group_mean` on T1 in the five slices where the share varies. In the other three (side over, gap
bands 4-6 and 6-8) `own_mean` is the flipping input on every row, so the bootstrap has no
variance and the tests enter at p = 1 as registered. The realised MDEs are smaller than the
registered ones because the share landed near 1, where a share has little variance; the T2 test
could have seen a missed-against-cleared difference of 0.04 in share and saw 0.006.

The largest T2 anywhere: `group_mean` on rush attempts, +0.063 [+0.000, +0.153], unadjusted p
0.10, 0.58 of its MDE - not detected.

## 5. The ceiling, in standard errors (descriptive)

| one input moved within | missed leans flipped | cleared leans flipped |
|---|---|---|
| 1 SE | 7 (3.1%) | 1 (0.4%) |
| 2 SE | 59 (26.0%) | 51 (20.4%) |
| 3 SE | 107 (47.1%) | 108 (43.2%) |
| 5 SE | 155 (68.3%) | 176 (70.4%) |

The median missed lean needs its nearest input moved 3.1 of its own standard errors; the median
cleared lean, 3.4. Per input, on missed leans: `own_mean` 3.1 SE, `group_mean` 15.1 SE,
`dispersion` 21.1 SE (and the variance can reach a flip on only 60 of 227 at all). These are
sampling standard errors only - see the first limit below - so "3 standard errors" understates
how uncertain the player's mean really is and is not a significance statement about any lean.

## 6. Limits registered in advance

- `own_mean`'s SE is sampling error only; it leaves out the season-to-season drift the model
  carries as a constant. Understated SE, overstated distance: biased against `own_mean`, which
  ranked first anyway.
- `dispersion`'s SE holds the drift constant, the shrinkage constants and the weight fixed. It is
  a lower bound: biased against `dispersion`. Its last place is the weak half of the ranking.
- `group_mean`'s SE treats the group's rows as independent.
- Two graded weeks, 31 games, game blocks - c-40's caveat, unchanged.

## 7. Departures

- **The brief asked for five inputs; three were measured.** Section 3. Registered before the run.
- **One test fixture was corrected before the run.** The first synthetic fixture for the verdict
  test assigned rows to games cyclically, so every block had the same mix and the bootstrap had
  almost no variance; games are now assigned at random. The script was not changed.
- Otherwise the script ran once as first committed.

## 8. Not established

- Why a lean missed. Unchanged from c-40.
- Where the line or the weight would rank on an uncertainty scale.
- That `own_mean` would rank first under a standard error that included drift for every input,
  or under any ruler other than these two.
- Anything about leans outside these 477, or weeks after week 4.
