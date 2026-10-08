# C42 - the counterfactual ledger on an uncertainty scale: pre-registration

Unit c-42, track C, NFL. Written and committed **before `research/counterfactual_uncertainty.py`
exists** and before any distance on the new scale, any standard error of any input, or any
share under the new scale has been computed. What had been read is listed in section 10.

Base: `origin/c-40-counterfactual-ledger` (`ac47f03`). c-40's pre-registration is
`docs/C40-counterfactual-preregistration.md` (`8ce15b1`); this document changes its section 4
(the distance) and nothing else it does not name.

## 1. The question

c-40 ranked the model's inputs by which one flips a lean with the smallest **proportional**
change, and found the player's own prior mean first on 66.1% of missed leans. A 50% change is a
different event for an input known to within 2% than for one known to within 40%. This unit
re-runs the counterfactual on the same leans, changing **only the ruler**: each input is moved in
units of **its own as-of standard error**. The null is that the ranking is the same.

## 2. Population - frozen

c-40's 477 leans: the rows of `research/results/counterfactual_ledger_rows.json` as committed at
`39ed11c` (ledger snapshot sha256 `760b93be...5306d`, 227 missed, 250 cleared, 31 games, 2026
weeks 3-4). No re-grading, no additions, no removals. The script refuses unless:

- the rows file holds exactly 477 rows with that ledger hash; and
- every row's fit components, rebuilt from the scratch facts copy as of the lean's `read_at` by
  c-40's own `components()` / `replica()`, equal the `as_of` values that file records (own mean,
  group mean, weight, total variance, prior games) to 1e-9. A row that does not match is a
  different fit, and the run stops rather than dropping it.

The model is not edited, not refitted, and `models/` is not touched. The flip target, the
threshold `T`, the allowed range of each input (x0.05 to x20, the `MIN_VMR` floor on the
variance) and the game blocks are c-40's, imported from its script.

## 3. The uncertainty of each input - defined before the run

Each is computed strictly as of the lean's `read_at`, from the same as-of rows the fit itself
read (`features._ASOF_JOIN`: regular-season games that kicked off before the instant, seasons
2025 and 2026).

| input | as-of standard error | defined when |
|---|---|---|
| `own_mean` | `sqrt(s^2 / n)`: `s^2` the sample variance (n - 1) of the player's own per-game values, `n` his prior games - the standard error of the number the fit uses, from its own sample | `n >= 2` and `s^2 > 0` |
| `group_mean` | `sqrt(v / N)`: `v` the sample variance (N - 1) of the per-player means the model averages (`positional_prior`'s `AVG(m)`: position and role, widened to the position where the model widened), `N` the number of them | `N >= 2`, `v > 0`, and the model had a group at all |
| `dispersion` | the standard deviation, over 1,000 bootstrap draws, of the model's own total predictive variance, where each draw resamples the player's `n` games with replacement and, independently, the group's `N` rows with replacement, and recomputes the variance by c-40's `replica()` with the weight, `n`, and every constant held. Seed: the first 8 bytes of sha256 of the `lean_id` | `n >= 2` and the bootstrap sd `> 0` |
| `weight` | **none - excluded by name** | - |
| `line` | **none - excluded by name** | - |

**`weight` is excluded.** `w = n / (n + 6)` times `TEAM_CHANGE_KEEP` and `COACH_CHANGE_KEEP`.
`n` is a count known exactly; the three constants are, in this project's own record, judgment
calls with no fitting script (CLAUDE.md, brief 023). No fit exists that yields a standard error
for it. Brief 023's bracket of alternative constants is a range chosen by hand, and using it
would be substituting a constant, which the brief forbids. The brief expected the weight's
uncertainty "from the fit"; there is no such fit.

**`line` is excluded.** A posted line is an exact number, not an estimate. The only as-of
quantity that could stand for its uncertainty is disagreement between the benchmark books about
where the line sits, and that was measured for availability before this commit (section 10):
**409 of the 477 leans have exactly one listed line across the three benchmark books** at the
read, 68 have two. A standard error that is zero on 85.7% of rows is not an uncertainty scale.

These exclusions are made for the stated reason and were fixed before any distance was computed.
They are not made because either input ranks low: under c-40 the line is the flipping input on
22.9% of missed leans, the second-ranked input.

**So k = 3 on the new scale, and the null share is 1/3.** The scale question can be asked of
three of the five inputs. For the line and the weight it cannot be asked on this data, and the
report says so in those words. No sixth input is added.

Stated biases of these standard errors, registered now so they are not discovered later:

- `own_mean`'s SE is sampling error only. It leaves out the season-to-season drift the model
  itself carries (`MEAN_DRIFT_VAR`, a full-sample constant, not as-of, so not used). The SE is
  therefore understated, `own_mean`'s distance overstated: **biased against `own_mean` ranking
  first.**
- `dispersion`'s SE holds the drift constant, the shrinkage constants and the weight fixed. It
  is a lower bound: **biased against `dispersion` ranking first.**
- `group_mean`'s SE treats the group's rows as independent; a player who changed teams appears
  twice. Slightly understated: biased against `group_mean`.
- All three assume a player's games are exchangeable across the two seasons.

## 4. Distance, flipping input

- **Distance** of an input on a row: the smallest `|x' - x| / SE` over allowed `x'` that reaches
  c-40's flip target, searched in **both directions separately** (c-40's `_scan`, one direction
  at a time) because the nearer direction in standard errors need not be the nearer one in log
  ratio. An input with no SE on a row, or that cannot reach the flip inside c-40's range, has no
  distance on that row.
- **Flipping input**: the smallest distance; ties to the earlier of `own_mean`, `group_mean`,
  `dispersion`.
- **The like-for-like comparator.** c-40's ranking is over five inputs. Its proportional
  flipping input is recomputed over the same three (from the distances already in its rows file,
  no new computation of any distance) so that the two scales are compared on one input set. Both
  the five-input and the three-input proportional shares are printed.

## 5. Tests

Per slice and per input i of the three:

- **T1:** share of missed rows (with a flipping input) whose flipping input is i, minus 1/3.
- **T2:** that share among missed rows minus the same share among cleared rows, bootstrapped as
  one quantity.

Slices: c-40's eight (all; market receptions, rush attempts; side over, under; gap band 4-6,
6-8, 8+). 8 x (3 + 3) = **48 tests, one Holm family** at 0.05. Game blocks, 2,000 draws, seed 42,
2.5 / 97.5 percentile intervals, two-sided normal p on estimate / bootstrap SE, zero-variance
bootstrap p = 1 (tested as `se < 1e-12`, c-40's corrected rule), fewer than 5 games p = 1.

**Specifications: one.** One definition of each standard error, one distance, one family. There
is no sensitivity arm. Anything else printed is labelled descriptive and enters no verdict. An
addendum after the first run is allowed only if committed, dated and labelled post hoc.

**MDE, written down before the run** (80% power, 2.80 x SE), from c-40's published bootstrap SEs
on this same population and these same blocks:

- T1, all rows: c-40's SE on `own_mean` was 0.0486 at a share of 0.661 and n = 227 - a design
  effect of 2.39 over binomial. At that design effect the T1 MDE is **0.136 at a share of 1/3,
  0.144 at 0.5 (the worst case), 0.115 at 0.8, 0.086 at 0.9**.
- T2, all rows: c-40's SE was 0.0430 (`own_mean`) and 0.0398 (`line`), MDE 0.121 and 0.112.
  At the worst case (a share of 0.5 in both arms) **0.128**.

Each test reports its realised MDE and estimate / MDE beside these. An estimate within 1.25x of
its MDE with adjusted p < 0.05 is reported as "at its MDE", not as a clean finding; an estimate
below its MDE with adjusted p above 0.05 is "not detected".

## 6. The verdict rule - both parts or the answer is no

Read on the all-rows slice, Holm-adjusted over the 48 tests.

**SAME RANKING** requires both:

1. the input with the largest share of missed rows under the uncertainty scale is `own_mean` -
   the input c-40 ranked first; and
2. its T1 estimate is above zero with adjusted p < 0.05 and a percentile interval excluding
   zero (a share above 1/3).

If (1) fails, the report states, in these words: **"c-40's 66.1% was scale-dependent"**, and
names the input that ranks first instead. If (1) holds and (2) fails, the report states that
`own_mean` ranks first on both scales but its share is not distinguishable from 1/3 on the
uncertainty scale, and the answer to the unit's question is **no**.

**The cleared-lean comparison**, reported whatever the verdict above: the first-ranked input's
share among cleared leans on the new scale, and its T2 (missed minus cleared) with interval and
adjusted p. If T2's interval contains zero, the report states plainly: **"a ranking that is
identical on misses and clears is not a diagnostic of a miss, on either scale."** If some input's
T2 is above zero with adjusted p < 0.05, that is reported as the first evidence the ledger says
something about misses, at its MDE ratio.

If both scales agree, **agreement is the result**. No third scale is tried.

## 7. A prediction, written before the run

Ignoring the variance channel, moving the fitted mean by `d` takes `d / w` on `own_mean` and
`d / (1 - w)` on `group_mean`, so in standard errors `own_mean` is the nearer of the two when
`w * SE_own > (1 - w) * SE_group`. Whether that holds on these rows has **not** been evaluated.
If it holds on nearly all of them, `own_mean` ranking first on this scale is, again, the model's
arithmetic - the same reading c-40 gave its own result - and the report will say that the
agreement of the two scales is agreement about structure, not a second independent confirmation
of anything about misses. The share of rows on which the inequality holds is printed.

## 8. Descriptive output (no test)

- Full order of the three inputs on each scale, missed and cleared.
- Row-level agreement: the share of rows whose three-input flipping input is the same on both
  scales.
- The ceiling in standard errors: the share of missed leans flipped by moving one input within
  1, 2, 3 and 5 of its own SEs, and the median SE distance per input.
- Counts of rows on which each input has no SE, and why.

## 9. Output, and what this unit does not do

`research/results/counterfactual_uncertainty.json` (aggregates, every test),
`research/results/counterfactual_uncertainty_rows.json` (per lean: each input's SE, SE distance,
flip value, direction, the flipping input on both scales), the run log, and
`docs/findings/c42-uncertainty-scale.md`. `cleared` / `missed` only; no pick language.

No page, no export edit, no model change, no refit, no merge, no publish. `market_log.db` is not
opened by this unit at all: the facts are read from c-40's scratch copy, `mode=ro`.

## 10. What had been read before this commit

c-40's pre-registration, script, tests, findings, run log and the all-rows SEs in its aggregates
file (for the MDEs above); one row of its per-row file (to learn its shape); `models/baseline.py`
and `models/features.py`; `jobs/board_read.py` and `core/board.main_line` (how a line is chosen).
Two availability counts over the 477 rows, taken to decide whether an uncertainty exists, not
how large it is: the number of distinct listed lines in each lean's read row (409 with one, 68
with two) and the number of prior games (5 rows with one game, so `own_mean` has no SE there).
No standard error, no distance on the new scale, and no share on the new scale had been
computed. c-40's proportional shares were known (they are the published result under test).
