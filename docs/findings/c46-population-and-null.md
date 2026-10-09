# c-46 - the 19 leans c-40 dropped, and the within-game permutation null

Pre-registration: `docs/C46-population-and-null-preregistration.md` (commit `0bc9d74`, pushed
before the script existed). Script: `research/counterfactual_population.py`. Aggregates and every
test: `research/results/counterfactual_population.json`. The 19 rows:
`research/results/counterfactual_population_rows.json`. Both run logs:
`research/results/counterfactual_population.log`.

Scope of everything below: the NFL Board ledger snapshot c-40 used (sha256 `760b93be...5306d`,
2026-10-08T20:16Z), player props on receptions and rush attempts, model
`baseline-usage-0.4+03571d4710af`, the 496 graded leans of 2026 weeks 3 and 4, 31 games, c-40's
proportional-distance ranking over its five inputs. Nothing here is about a later ledger, another
distance scale, or the line's uncertainty.

## 1. Verdict

**Not diagnostic, at this power** - the registered outcome, and the expected one.

On all 496 graded leans, the player's own prior mean is the flipping input on 67.5% of missed
leans (162 of 240) and 68.8% of cleared ones (176 of 256). Missed minus cleared is
**-0.012 [-0.102, +0.076]**, within-game permutation p **0.785**, against a pre-stated minimum
detectable difference of **0.121** in share (realised 0.106). No other input does better: group
mean -0.021 [-0.103, +0.055], p 0.46, MDE 0.111; line +0.037 [-0.041, +0.113], p 0.35, MDE 0.112.

Across the 80-test family, **0 survive Holm and 0 reach p < 0.05 unadjusted**. The smallest p is
0.068 (the line, 8+ band, the 477); the largest estimate relative to its MDE is 0.64.

What this supports: on this ledger, c-40's ranking does not separate a missed lean from a cleared
one, and a difference in share smaller than about 0.12 would not have been seen. It does not show
the two are the same.

## 2. The 19

All 19 were recovered, by the one rule declared before any re-fit.

| | |
|---|---|
| week | 4 (all 19) |
| market | receptions 16, rush attempts 3 |
| side | under 17, over 2 |
| result | missed 13, cleared 6 |
| games | 7; nine of the 19 in `2026_04_DET_CAR` |
| c-40's reason | rebuilt P(over) not the ledgered one, off by 0.0001 to 0.0111 |
| read | every one at or after 2026-10-02T01:04Z, after week 4's first kickoff (00:15Z) |

**Why they did not rebuild.** The model's as-of join admits a player-week once its game has
kicked off. c-40 rebuilt on the store as it stood on 2026-10-08. Each of the 19 was read at a
moment when between 67 and 617 player-weeks from week-4 games already kicked off had not yet
been ingested, so the published fit stood on fewer rows than the rebuild saw.

**R1 - facts restricted to rows with `ingested_ts <= read_at`.** Under it all 19 rebuild to the
ledgered probability: largest difference 0.00004, inside the 4-decimal rounding of the ledger,
against 0.0001 to 0.0111 on today's facts.

**So the exclusion is a property of when the lean was READ, relative to ingestion** - not of the
row's market, side or result, and not of when it was graded. It lands in week 4 because week 3's
leans were read at moments when no kicked-off game was waiting to be ingested (0 of 194).

### 2.1 The control says R1 is not exact, and why

R1 was also applied to c-40's 477 rows:

| the 477 under R1 | n |
|---|---|
| read with nothing waiting to be ingested; inputs identical to c-40's | 446 |
| read with rows waiting; still within tolerance of the ledger | 19 (inputs differ from c-40's on 15) |
| read with rows waiting; **R1 moves them outside tolerance** | **12** |

Twelve rows that match the ledger on today's facts stop matching under R1 (off by 0.0002 to
0.0075). Seven were read 2026-09-29T16:09Z, five on 2026-10-05 at 12:19Z and 18:24Z. For the
seven, the weekly refresh log shows an nflverse ingest at 2026-09-29T13:00Z, three hours before
the read, while the rows carry `ingested_ts` 19:54Z the same day. The stamp is the LAST write of
that day's `data_version`, not the first, so R1 hides rows that were present. For the five on
10-05 no earlier ingest was found in that log; the same cause is inferred, not shown.

Per the registration the 12 stay in the population on c-40's committed values, which are the ones
that match the ledger. R1 is therefore right on all 19 it was needed for and wrong on 12 it was
not needed for. It is a recovery rule for these rows, not a general reconstruction of what the
Board knew.

A consequence outside this unit, for track A: a fit rebuilt later with the model's as-of join
reads rows the live fit did not have, whenever the read fell between a kickoff and that game's
ingestion. Here that was 50 of 496 graded leans by the stamp (an over-count, per the 12).

## 3. The two populations

| own-mean share | missed | cleared | missed - cleared | permutation p |
|---|---|---|---|---|
| the 477 (c-40's) | 0.661 (150/227) | 0.680 (170/250) | -0.019 [-0.102, +0.075] | 0.738 |
| all 496 | 0.675 (162/240) | 0.688 (176/256) | -0.012 [-0.102, +0.076] | 0.785 |

Of the 19, own mean is the flipping input on 18 (12 missed, 6 cleared) and group mean on 1
(missed). Over all 496 leans whatever the outcome it is own mean on 338 (68.1%).

f-31's bound on what the 19 could do to the contrast, [-0.062, +0.015], reproduces exactly
([-0.0625, +0.0151]). The realised value, -0.012, is inside it.

All rows, all 496:

| input | missed | cleared | missed - cleared | perm p | MDE pre-stated / realised |
|---|---|---|---|---|---|
| own_mean | 0.675 | 0.688 | -0.012 [-0.102, +0.076] | 0.785 | 0.121 / 0.106 |
| group_mean | 0.108 | 0.129 | -0.021 [-0.103, +0.055] | 0.460 | 0.111 / 0.077 |
| weight | 0.000 | 0.004 | -0.004 [-0.013, +0.000] | 1.000 | 0.011 / 0.011 |
| dispersion | 0.000 | 0.000 | zero variance, p = 1 | | none |
| line | 0.217 | 0.180 | +0.037 [-0.041, +0.113] | 0.346 | 0.112 / 0.096 |

Intervals are game-block bootstraps (2,000 draws, 31 games). Every Holm-adjusted p is 1.

## 4. The null

Within each game the missed / cleared labels were shuffled among that game's rows, 10,000 draws,
seed 46, fixed before the run. On the 477 the own-mean contrast has p 0.738; f-31 drew 0.746 and
0.739 with its own code.

**Where 66.1% sits.** By the registered definition (draws below, plus half the ties) the observed
missed share on the 477 is at the **58.5th percentile** of its null, Monte Carlo interval
[57.6, 59.5]. Counting ties as below it is 62.6% - f-31's "62nd percentile". The share is a count
out of 227, so ties are 8% of draws; both readings say the same thing. On all 496 the 67.5% sits
at the 56.4th percentile [55.5, 57.4] (60.6% counting ties). The at-or-below figure was not in the
registration; it was added to the printout after run 1 to reconcile with f-31.

## 5. Power, and where the pre-stated figure was wrong

The pre-stated MDEs were c-40's bootstrap figures, copied into the registration before the run.
On the all-rows slice the realised permutation MDE is 0.69 to 1.06 times the pre-stated one, and
the verdict uses the larger.

**In 7 of the 50 tests with both figures the realised MDE is outside 1/1.5 to 1.5 of the
pre-stated one**, all in the two small gap bands (4-6 and 6-8, 35 to 68 rows): the realised
figure is 0.41 to 0.66 of the pre-stated. A bootstrap SE on 14 missed rows and a permutation sd
on the same rows are not the same quantity, and the registration treated them as if they were.
No verdict depends on those slices. They are reported as too thin to read, at either figure.

## 6. Departures, and specifications tried

- **Runs: 2.** Run 1 rebuilt 496 fits (700 s) and produced every test. Run 2 read the cached fits
  and added the at-or-below percentile to the output. All 80 tests and the verdict are identical
  between the two (compared field by field).
- **One edit between the tests and run 1:** the permutation and the bootstrap were given separate
  random streams, because a shared stream made the bootstrap picks depend on the row count (a
  test on duplicated rows caught it). Made before any real row was read.
- **One recovery rule was tried.** No second rule was fitted to the 12 control rows.
- The 477's flipping inputs are c-40's committed values, not recomputed. The 19 were ranked by
  c-40's own functions, imported.
- `CLAUDE.md` and c-40's and c-42's documents are untouched, as the brief required. Section 8
  holds the entry this unit would add.

## 7. Not established

- **That missed and cleared leans have the same ranking.** A difference under about 0.12 in
  own-mean share (0.11 on the line and group mean) would not have been seen on 31 games.
- **Anything about the line's uncertainty.** The line is ranked as c-40 ranked it - the same price
  at another number. Where it would rank on an uncertainty scale is still unknown and was not
  attempted; it needs per-book lines kept at each read, which is a track A capture decision.
- **c-42's standard-error scale on the 496.** Not re-run. Its 97.4% is over three inputs against
  a 1/3 null and is a different number from the 67.5% here.
- **That R1 reconstructs what the Board knew in general.** It fails on 12 of the 477. For five of
  those the cause is inferred.
- **That the 12 rows' published fits are the ones c-40 analysed.** They match the ledger to
  0.0001 on today's facts; their inputs could still differ slightly from the published fit's.
  The same holds for the 15 rows whose R1 inputs differ from c-40's while both match the ledger.
- **Week structure.** Two graded weeks; every interval is a game-block interval and is narrower
  than a week-block reading would allow. The permutation shuffles rows, not ladders, so its p can
  be too small; with every p above 0.06 that does not bear on the verdict here.
- **Staleness.** 496 is one snapshot by hash. Weeks 5 onward change every share.
- **Why a lean missed.** Untouched by this unit, as by c-40.

## 8. The entry this unit would add to `CLAUDE.md` (not added - c-45 owns that prose)

> **c-46 (NFL Board ledger snapshot 2026-10-08, weeks 3-4, 31 games, all 496 graded leans,
> c-40's proportional ranking):** the 19 leans c-40 excluded were read after a week-4 kickoff
> and before that game's rows were ingested; rebuilt on facts with `ingested_ts <= read_at` all
> 19 match the ledger. With them in, own mean is the flipping input on 67.5% of missed and 68.8%
> of cleared leans: -0.012 [-0.102, +0.076], within-game permutation p 0.785 (10,000 draws), MDE
> 0.121 stated before the run. 0 of 80 tests reach p < 0.05 unadjusted. Not diagnostic, at this
> power. `ingested_ts` is the last write of a day's `data_version`, not the first: the same rule
> breaks 12 of c-40's 477, so it is a recovery rule for those 19 and not a general as-of.
