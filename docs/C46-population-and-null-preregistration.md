# C46 - the 19 leans c-40 dropped, and the null it never had: pre-registration

Unit c-46, track C, NFL. Written and committed **before `research/counterfactual_population.py`
exists**, before any of the 19 excluded leans has been re-fitted, and before any permutation has
been drawn by this unit. What had been read when this was written is in section 9.

Branch `c-46-population-and-null`, from `origin/c-45-wording-corrections` (9ae810d).

## 1. The question, and the answer that is expected

Does c-40's input ranking distinguish a missed lean from a cleared one, once the null is the
within-game label permutation f-31 used and the population is every graded lean the ledger holds?

The expected answer is no. f-31 (permutation p about 0.75 on the 477) and c-42 (a second distance
scale, same 477) both read the ranking as a property of the model's arithmetic. "No" is a complete
answer. This unit does not try to rescue 66.1%.

## 2. Population - stated first

Source: the c-40 snapshot of `board/nfl/ledger.parquet`, sha256
`760b93be22fb1464df139457b011a67687ba1c70a013ba06f8e55a90d225306d`, read through
`core.record.build_published`. 496 graded leans (256 cleared, 240 missed), 2026 weeks 3 and 4.
The script refuses any other hash. A later ledger is a different population and is not read.

- **P477** - the 477 rows of `research/results/counterfactual_ledger_rows.json` exactly as c-40
  committed them. Their flipping inputs are taken from that file and are NOT recomputed.
- **The 19** - the leans in `counterfactual_ledger.json` `gate.failed`. For each: week, market,
  side, result, game, `read_at`, and the size of the rebuild difference are reported.

### 2.1 One recovery rule, declared before any re-fit (R1)

c-40 rebuilt each fit on the facts store as it stood on 2026-10-08, with the model's as-of join,
which admits a player-week once its game has KICKED OFF (`g.kickoff_ts < as_of`). The store keeps
one `data_version` per day with an `ingested_ts`. A lean read after a kickoff but before that
game's rows were ingested was priced on fewer rows than a rebuild sees today.

**R1: rebuild the fit on facts restricted to rows with `ingested_ts <= read_at`**, in both
`nfl_player_week` and `nfl_games`, by shadowing those two tables with TEMP views on a `mode=ro`
connection to the scratch facts copy. Nothing else changes: same model code, same position and
team from the read file, same `components` / `check_replica` as c-40 (imported, not copied).

- A lean is **recovered** iff under R1 the replica check passes and the rebuilt P(over) is within
  0.0001 of the ledgered `model_p_over` - c-40's own gate and tolerance.
- R1 is the only rule tried. No second rule is fitted to whatever R1 leaves behind. A lean R1
  does not recover is **not recovered**, is listed, and is never replaced by another row.
- **Control, so R1 is not just a rule that happens to fit 19 rows:** all 477 are re-fitted under
  R1 as well and the count still within tolerance is reported. A 477 row that fails under R1
  stays in the population on c-40's committed values (those are the ones that match the ledger)
  and is counted as evidence against R1.
- `player_xwalk` carries no versions and is used as it stands.

### 2.2 The populations tested

- **P477** as above.
- **P496** - P477 plus the 19. A recovered lean enters on its R1 fit. An unrecovered lean enters
  on c-40's rebuild (today's facts), flagged `not_the_published_fit`.
- **Verdict population:** P496 if all 19 are recovered; otherwise **P477**, with P496 reported as
  a sensitivity arm only. (The brief: "if the 19 cannot be recovered, say so and stop at the 477".)
- **Bound, descriptive:** the smallest and largest value the own-mean contrast could take over
  every possible assignment of flipping inputs to the 19, computed without their fits. f-31
  published [-0.062, +0.015]; this is a replication of that bound, not a test.

Whether the exclusion is a property of the row or of when it was graded is answered from the
control and from `read_at` against kickoff and ingestion times, and is reported whichever way it
comes out - including "not established" if R1 recovers some and not others.

## 3. What is ranked

c-40's ranking, unchanged: five inputs (`own_mean`, `group_mean`, `weight`, `dispersion`,
`line`), the flip target, the proportional distance `|ln(x'/x)|`, ties to the earlier input. For
a newly entering lean the functions are c-40's own (`min_perturbations`, `flipping_input`),
imported. `models/` is not edited, nothing is refitted, no lean is re-graded, no price moves.

The line is ranked exactly as c-40 ranked it (the same price hung at another number). **Nothing
about the line's uncertainty is tested here** - it has none on this data, and that stays
unresolved. c-42's standard-error scale is not re-run: its 97.4% is a share over three inputs
against a 1/3 null and is a different number from the 66.1%, not a later reading of it.

## 4. The test - missed against cleared, never missed against a null share

Statistic, per input i, per slice, per population:
`d_i = share of missed rows whose flipping input is i  -  the same share among cleared rows`,
over rows that have a flipping input. No test against 1/5 is run: the model's structure violates
that null on its own, which is what c-45 corrected.

- **Null: the within-game label permutation.** Within each game the missed / cleared labels are
  shuffled among that game's rows, so each game keeps its own count of misses and every row keeps
  its flipping input. **10,000 draws, seed 46**, fixed now.
  `p = (1 + #{|d*| >= |d| - 1e-12}) / 10,001`, two-sided. The floor is 1/10,001; over the family
  below that is 0.008 after Holm, so a survivor is reachable.
- A permutation distribution with sd below 1e-12 (an input that is never the flipping input) is
  `p = 1`. A slice with fewer than 5 games or an empty arm is `p = 1`.
- **Interval on every comparison:** game-block bootstrap, 2,000 draws, seed 46, 2.5 / 97.5
  percentiles - c-40's construction.
- **f-31's statistic, replicated and labelled descriptive:** where the observed missed share of
  `own_mean` sits in its permutation distribution (share of draws below it plus half the ties),
  with a Wilson 95% interval on that Monte Carlo proportion at 10,000 draws. On P477 f-31 read the
  62nd percentile twice.
- **Slices (c-40's eight, closed):** all; market receptions, rush attempts; side over, under; gap
  band 4-6, 6-8, 8+.
- **One Holm family: 5 inputs x 8 slices x 2 populations (P477, P496) = 80 tests** at 0.05. The
  two populations share 477 rows, so the family is conservative. Nothing else is tested.
- **Specifications tried** is reported as a count. One run is intended. Any re-run, and why, is
  logged in the findings.

**A limit of the registered null, stated now.** Rungs of one player's ladder share a flipping
input more often than chance and their results are correlated; shuffling rows within a game
breaks that, so the permutation p can be too small. For that reason the verdict also requires the
game-block interval to exclude zero.

## 5. Power - written down before the run

Pre-stated minimum detectable difference in share, 80% power, two-sided 5%. These are c-40's
committed figures (2.80 x its game-block bootstrap SE on the 477, `counterfactual_ledger.json`),
copied here before this unit computes anything:

| input, all-rows slice | pre-stated MDE |
|---|---|
| own_mean | **0.121** |
| group_mean | 0.111 |
| line | 0.112 |
| weight | 0.011 |
| dispersion | none - zero variance, cannot be called diagnostic |

For every other slice the pre-stated MDE is the `mde` of c-40's T2 test for that slice and input
in the committed file (12 of the 40 are null: zero variance or under 5 games). The same figure is
used for P477 and P496; 19 more rows would shrink it by about 2%, which is not claimed.

The run also reports the **realised** MDE, 2.80 x the sd of the permutation distribution. The
verdict uses the **larger** of the pre-stated and the realised figure. If the realised figure is
more than 1.5x or less than 1/1.5 of the pre-stated one, that is reported as a defect of the
pre-stated power, not smoothed over.

## 6. Verdict rule

Read on the all-rows slice of the verdict population (section 2.2), Holm-adjusted over all 80:

- **Diagnostic** - only if, for some input, the Holm-adjusted permutation p is below 0.05 **and**
  the game-block interval excludes zero **and** `|d|` exceeds the larger of its pre-stated and
  realised MDE.
- Anything else: **"not diagnostic, at this power"**, with the MDE printed beside it.

A sub-slice result, the P496 arm when it is not the verdict population, the percentile and the
bound change no verdict. Both verdicts are shown reachable by a test on planted rows.

## 7. Output

`research/results/counterfactual_population.json` (the 19, R1 and its control, every test, the
percentile, the bound, the verdict), `research/results/counterfactual_population_rows.json` (one
object per newly entering lean, c-40's row shape), a run log beside them, findings in
`docs/findings/c46-population-and-null.md`. No pick language; `cleared` / `missed` only.

## 8. Not done by this unit

No test of the line's uncertainty. No re-run of c-42. No edit to c-40's or c-42's documents, to
their result files, or to the c-40 / c-42 entries in `CLAUDE.md` (c-45 owns that prose). No model
change, refit, re-grade or price change. `market_log.db` is not opened at all: the facts come
from c-40's scratch copy, opened `mode=ro`. No credit spend, no publish, no merge. b-108 is
neither read nor cited.

## 9. What had been read before this commit

- `CLAUDE.md`, the relay ledger, the c-46 brief, `_relay/reports/json/f-31.json` and `c-40.json`.
- `research/counterfactual_ledger.py`, c-40's pre-registration and findings (with c-45's
  correction), and from `research/results/counterfactual_ledger.json`: `gate.failed`, the
  population block, and the all-rows T2 estimates, SEs and MDEs quoted in section 5.
- For the 19, joined to the ledger snapshot: week, market, side, result, `read_at`, game, player
  id, line, rebuilt and ledgered probability. Seen: all 19 are week 4; 16 receptions, 3 rush
  attempts; 13 missed, 6 cleared; 17 under, 2 over; 9 in one game; every `read_at` is at or after
  2026-10-02T01:04Z; differences 0.0001 to 0.0111.
- For the 477: the count of rows by week and hour of `read_at` (no flipping input, no result).
- The scratch facts copy: table definitions; rows per `data_version` with ingestion times for
  season 2026; four kickoff times. That is where R1 comes from: the first week-4 game kicked off
  2026-10-02T00:15Z and its rows were first ingested 2026-10-02T18:40Z.
- `models/features.py` lines 60-110 (the as-of join).

Not done before this commit: no fit rebuilt, under R1 or otherwise; no flipping input computed
for any of the 19; no permutation or bootstrap drawn; no share recomputed on any population.
