# C48 — a committed script for three figures c-37 published post hoc. Pre-registration

Committed and pushed **before** the script exists and before any of the three figures has
been recomputed by this unit. The script is `research/c37_figures.py`; it is written after
this file and must implement what is below. Nothing here is edited after the first run; a
correction is appended at the bottom, dated, labelled post hoc where it is, with what had
been seen.

Unit c-48 (track C), 2026-10-08. Branch `c-48-c37-figures`, cut from
`origin/c-47-two-markets` (`23b3271`). `market_log.db` is opened `mode=ro` only. No request
to any API is made. Zero credits. **No model is fitted that c-37 did not already fit**, and
c-37's pre-registration (`4134f35`), its findings doc and its verdict are not touched.

## What this is and is not

c-37's report carries one `verified_by` entry marked `measured_it` whose evidence reads
"post-hoc inline script over `D:/temp/c37/predictions.csv` (scratch, not committed). NOT
registered, and there is no committed script in `research/` for these three figures":

    99.5% of bench rungs close with p_bench in [0.45, 0.55]   (findings doc: sd 0.010)
    mean model P(over) 0.410 vs realized 0.479
    Platt slope 0.037

This unit is a **reproduction**, not a test of a hypothesis. It asks one thing of each
figure: does committed code, run against the store as it stands today, return the published
number? There is no null, no family and no multiple-comparison correction, because nothing
is being discovered. The intervals below are stated so the figures are citable with their
uncertainty, not so that anything can be called significant.

## What was done before this file

- Read `research/yards_markets.py` (committed at `e8ff867`), `research/ranking_calibration.py`
  (`platt_fit`), c-37's machine report and `docs/findings/yards-markets.md`.
- Listed `D:/temp/c37/`: `predictions.csv` (9,894,920 bytes, 2026-10-08 03:05) **still
  exists**. It was **not opened**. No figure was computed from it or from the store.
- Read two COMMITTED result files, by key:
  `research/results/residual_given_line.json` (c-41) already holds, for the same receiving
  yards bench population, `share_p_in_45_55` 0.994750, `sd_p` 0.010122, `over_rate`
  0.479267, n 18,666. `research/results/residual_two_markets.json` (c-47) holds 0.401254
  (receptions) and 0.769046 (rush attempts). So the first figure and the realised rate
  have, in fact, a committed computation already (c-41's `descriptive` block); the mean
  model P(over) and the Platt slope do not, because c-41 and c-47 build no model.
  **These values were seen before this file was written, and the tolerance below was set
  knowing them.** That is why the tolerance is tied to c-37's published digits and not to
  a band chosen around a number.

## Population

c-37's `RB`, rebuilt through c-37's own committed functions, imported and never copied:

    market        NFL receiving yards, OVER side          <- every figure here is about THIS market
    seasons       2023, 2024, 2025, regular season only (playoff rungs dropped: c-37 counted 2,810)
    close         outcome_close.p_bench not null, lead_min <= 15
                  (de-vigged DK / FD / MGM median; a SINGLE book on 12,432 of 18,666 rungs in c-37)
    settlement    jobs.settle_outcomes.settle_one; over / under only (void dropped)
    frame         the player-game is in c-37's frame (WR / TE / RB with a panel row)
    model         c-37's arm `Y` - the hurdle gamma with recalibrated centre, walk-forward,
                  priors on seasons < T, shapes on T-3..T-1 - `yards_markets.prob_over` at the
                  book's own line

c-37's run counted: 56,732 rungs read; 2,810 not regular season; 63 void; 126 not in the
frame; 53,733 scored (2023 20,114 / 2024 22,508 / 2025 11,111); **RB 18,666 rungs, 10,016
player-games, 814 games**; RB by `n_bench` {1: 12,432, 2: 4,998, 3: 1,236}.

**Four ways this population moves**, all from c-37's own `not_established`, restated so a
later reader does not have to find them: (1) playoffs are excluded; (2) `p_bench` is one
book's price on about two thirds of rungs, so "the close" is often not a consensus; (3) the
counts depend on `outcome_close` as built on the day of the run; (4) a settlement change
moves every figure. The script prints every count above on every run, and a count that
differs from c-37's is reported as a moved population, not hidden inside a figure.

## The three figures, defined

c-37 did not write down the arithmetic. The definitions below are this unit's reading of
its sentences, fixed here before the run. Where a reading was a choice, it is marked.

**F1 — close-price concentration.** `mean(0.45 <= p_bench <= 0.55)` over RB, bounds
inclusive, with `sd(p_bench)` (population sd, `ddof = 0`). Published: 0.995, sd 0.010.

**F2 — the model's level.** `mean(Y)` over RB against `mean(y)` over RB, where `Y` is arm
Y's P(over) at the rung's line and `y` is 1 when the over cleared. Published: 0.410 against
0.479. *Choice:* c-37 says "the model" without naming an arm; `Y` is its registered primary
arm. The script also prints `mean` for S0, S1, Y-lognormal and Y-weibull, descriptively. If
`Y` does not return 0.410 and another arm does, that is reported as c-37 having quoted a
different arm, and F2 is **not** counted as reproduced.

**F3 — the Platt slope.** `b` from `research.ranking_calibration.platt_fit(Y, y)` over RB
pooled, fitted in sample: the slope of a logistic regression of the outcome on
`logit(Y)`, `Y` clipped to [1e-4, 1 - 1e-4] by that function. Published: 0.037. *Choice:*
pooled over the three seasons and in sample, because c-37 gives one number and no split.
The intercept `a` is printed beside it.

## Tolerance — written before the run

A figure is **REPRODUCED** when the regenerated value rounds to the digits c-37 published:

    F1 share            |x - 0.995| < 0.0005
    F1 sd               |x - 0.010| < 0.0005
    F2 mean model P     |x - 0.410| < 0.0005
    F2 realised rate    |x - 0.479| < 0.0005
    F3 Platt slope      |x - 0.037| < 0.0005

F1 and F2 are each reproduced only when both of their rows are. A figure that misses is
**MOVED**, and for a moved figure the script states whether c-37's *argument* survives, by
rules also fixed here:

    F1  the registered close statistic is empty for want of price variation
        -> survives while the share is >= 0.90
    F2  the model sits below the realised rate at the book's line
        -> survives while the 95% interval of mean(Y - y) is wholly below zero
    F3  the model's disagreement with the line carries almost no information
        -> survives while the 95% interval of the slope is wholly below 0.5

Either outcome is reported with the number. A figure that reproduces is citable; a figure
that moves is citable at its new value. A match is not the goal.

**Population check, separate from the figures.** RB must count 18,666 rungs / 10,016
player-games / 814 games to be "the same population as c-37". If it does not, every figure
is labelled as measured on a moved population and the differences in the census are
printed; the figures are still reported.

## Intervals

Game-block bootstrap (resample games with replacement, all rungs of a drawn game together),
2,000 draws, seed 48, percentile 2.5 / 97.5, on: F1 share, F1 sd, `mean(Y)`, `mean(y)`,
`mean(Y - y)`, the Platt slope. One set of draws for all six, because they are six
quantities on one subject and none is compared with another subject. The effective sample
is 814 games, not 18,666 rungs. Draws and seed are read inside the function, not bound as
default arguments.

## Descriptive, declared now, not figures of record

- F1 share and sd by season and by `n_bench` (1 / 2 / 3 books).
- `mean` P(over) for the four other arms, the `half` and `prior` baselines, and `p_bench`.
- **The two other markets, printed beside F1 on every run so the venue fact cannot travel:**
  c-47's committed 0.4013 (receptions) and 0.7690 (rush attempts), and c-41's committed
  0.9947 (receiving yards), each READ from its committed result file by key - not
  recomputed here, labelled with the unit that measured it, and the script refuses if a key
  is absent. F1 is a fact about **NFL receiving-yards props at US sportsbook bench closes,
  2023-2025 regular season**. It is not a fact about "props", and it is not a fact about
  Kalshi.

## The scratch file

`D:/temp/c37/predictions.csv` is not an input. The figures of record come from the panel
regenerated by committed code. If the scratch file exists when the script runs with
`--scratch`, the regenerated RB is compared with it row for row on
`(season, game, gsis, line)` - counts of keys only in one side, and the largest absolute
difference in `Y`, `p_bench` and `y` on the shared keys - and that comparison is reported as
evidence about whether c-37's run and this one saw the same panel. If the file is gone, that
is reported as the finding it is and the three figures stand on the regenerated panel alone.

## Output

`research/results/c37_figures.json` and `.log`: aggregates and intervals only, every count,
the store's path and the commit. No per-outcome row is written anywhere but `--out-dir`
scratch (BET_LIST_RESTRICTION).

## Not in scope

No new model, no line-aware model, no change to c-37's verdict, no `CLAUDE.md` edit. Whether
the venue fact enters `CLAUDE.md` is `c-37#1`, Ethan's call; this unit only makes the figure
citable. The `boot_mean` default-argument fix in `research/yards_markets.py` is a separate
commit and changes no number: `draws` stays `rc.BOOT` (2,000) and `seed` stays `rc.SEED` (24).
