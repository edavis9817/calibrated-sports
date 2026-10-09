# c-50 - the plain-Elo baseline's K grid, widened, and the weeks 1-4 sentence over seeds

Pre-registered at `bc7357b` (`docs/C50-baseline-grid-edge-preregistration.md`), script committed
at `ad624a2` before its one run. Script `research/c50_baseline_grid_edge/analyse.py`; output
`research/c50_baseline_grid_edge/results/c50_baseline_grid_edge.{json,log}`. NFL, `market_log.db`
opened `mode=ro`, `nfl_games` version 2026-10-09. Every figure below is a STORE figure. The
synthetic test league's -0.0030 (a-79) is a fixture figure, a different object, and was not
re-run, compared or pooled.

Scope of every sentence here: c-28's margin-of-victory Elo against c-28's plain Elo, 6,743
decisive NFL games 2001-2025, walk-forward, scored against the result. Nothing here concerns a
price; the model loses to every price it has been scored against.

## The grid, on the record

c-28's plain-Elo baseline (`elo_nomov`) was fitted on the season model's grid, the same 144
points the model itself is fitted on:

    K        10, 15, 20, 25, 30, 40          endpoints 10 and 40
    hfa      0, 25, 50, 75                   endpoints 0 and 75
    regress  0, 0.25, 1/3, 0.5, 0.6, 0.75    endpoints 0 and 0.75

On that grid plain Elo's fitted K is **40, the largest value offered, in 25 of 25 scored seasons**
(and for 2026). hfa is 50 in 25 of 25; regress is 1/3 in 16, 0.5 in 8, 0.25 in 1 - no endpoint.
The MODEL on the same grid has no pinned dimension: K 20 in 22 seasons and 25 in 3, hfa 50 in 25,
regress 0.5 / 0.6 / 1/3 in 19 / 5 / 1.

Widened grids, fixed before the run:

    arm P   K 10..80 step 5, then 100, 120 (17 values, endpoints 10 and 120); hfa, regress as above
    arm S   arm P's K; hfa 0..100 step 25; regress adds 0.9

**Plain Elo's K goes interior.** At arm P the fitted K is 45 in 14 seasons, 50 in 10 and 40 in 1 -
not an endpoint in 25 of 25 (rule: at least 20), and strictly below both neighbouring K values on
the fit profile in 25 of 25. Fitted regress moves with it, to 0.5 in 23 seasons: on the capped
grid a lower regress had been standing in for the K the grid did not offer. Arm S chooses the
same point as arm P in every season (hfa 50, regress never 0.9), so every arm S figure equals
arm P's.

The fit profile is flat at its floor. For the 2025 fit (mean log loss on 2000-2024, best hfa and
regress at each K): K 40 0.63689, K 45 0.63687, K 50 0.63688, K 60 0.63802, K 120 0.66194.

## c-28's headline at the widened grid - confirms f-26

dBrier, model minus plain Elo, all 6,743 games, 200 seeds x 2,000 draws, game blocks:

    grid         estimate   pooled interval        seeds below zero   |est|/MDE
    registered   -0.0027    [-0.0036, -0.0018]     200 of 200         2.11
    arm P        -0.0027    [-0.0035, -0.0019]     200 of 200         2.42

**CONFIRMS f-26 ("unchanged")** by the registered rule. c-28 published -0.0027; f-26 measured
-0.0027 [-0.0036, -0.0019] at its widened grid. Season-week blocks (530) and season blocks (25)
read the same. The `home` and `record` baselines have no fitted grid and were not re-run.

## The weeks 1-4 sentence - registered verdict: CHANGED, "better than", seed-stable

REG weeks 1-4, 1,547 games, same panel:

    grid         estimate   pooled interval        seeds: below / contains zero   |est|/MDE
    registered   -0.0008    [-0.0024, +0.0008]     0 / 200                        0.35
    arm P        -0.0017    [-0.0030, -0.0005]     200 / 0                        0.95

- The published sentence is seed-stable on its own grid: "no better than" in 200 of 200 seeds.
  On seed 24 (c-28's) it is -0.0008 [-0.0024, +0.0007], c-28's published interval exactly.
- At arm P the interval lies below zero in **200 of 200 seeds** (share containing zero 0.000);
  the upper bound runs -0.00057 to -0.00034 across seeds. On seed 24: -0.0017 [-0.0030, -0.0005].
  f-26's single-seed figure was -0.0017 [-0.0031, -0.0004] with its own bootstrap.
- No qualifier fires: arm S below; season-week blocks below, [-0.0031, -0.0004]; season blocks
  below, [-0.0033, -0.0003]; the optimum is interior.
- So f-26's flip is **not a seed artifact**. z = -2.67, p 0.0076, Bonferroni over the two stages
  0.015.

What 200 of 200 does and does not say: it says the bootstrap bound does not move with the seed.
It does not say the effect is large against its sampling error - the estimate is 0.95 of its MDE,
and weeks 1-4 is one of c-28's cuts, which c-28 registered as "not verdicts".

## Post hoc, and it changes how the flip reads

Not pre-registered; written after the registered run printed plain Elo's Brier as HIGHER at the
widened grid. `research/c50_baseline_grid_edge/posthoc.py`, `results/c50_posthoc.{json,log}`,
50 seeds x 2,000.

    stage          n       Brier: model / plain registered / plain arm P
    all            6,743   0.22058 / 0.22325 / 0.22330
    weeks 1-4      1,547   0.22903 / 0.22983 / 0.23078
    weeks 5+       4,909   0.21767 / 0.22084 / 0.22055

    plain Elo arm P minus plain Elo registered (positive = the unpinned baseline is worse)
    all         +0.00005 [-0.00043, +0.00052]   contains zero in 50 of 50
    weeks 1-4   +0.00095 [-0.00011, +0.00202]   contains zero in 50 of 50
    weeks 5+    -0.00029 [-0.00084, +0.00026]   contains zero in 50 of 50

- **Unpinning K did not produce a detectably stronger baseline out of sample**, in any stage.
  The in-sample fit improves by 0.00002 of log loss (the 2025 fit); out of sample the two plain Elos are not
  distinguishable (the null is at 0.07 of its MDE over all games - a difference under about
  0.0007 of Brier would not have been seen).
- **The weeks 1-4 gap widened because the baseline's own early-season score went up, not because
  anything about the model changed.** The model's forecasts are identical under every arm. A
  higher K with more regression is nominally worse in weeks 1-4 and nominally better from week 5.
- **The two sentences are not shown to differ from each other.** "No better than" (registered)
  and "better than" (arm P) differ by 0.00095 with an interval that contains zero. The difference
  between a result that excludes zero and one that does not is not itself established.

So the registered verdict stands as registered - the sentence changes at the K-widened grid, in
every seed - and what it supports is narrow: **"no better than plain Elo in weeks 1 to 4" holds
only against a plain Elo whose K was capped at 40; against the plain Elo its own fit rule chooses
(K 45-50) the model is better in weeks 1 to 4.** It does not support "the margin term helps early"
as a general statement, and it does not make the capped baseline the wrong one out of sample.

## What this does not establish

- Nothing about a price, the 2026 season, or college (c-39's plain grid was not examined).
- Not that K 45-50 is the right plain Elo: the profile is flat from 40 to 50 and the fit
  criterion is log loss on all weeks, not on weeks 1-4.
- Not that the MODEL's grid is adequate beyond "no fitted dimension is at an endpoint".
- Check 2 of the registration (the model does not move when the baseline grid does) holds by
  construction - the model is walked from its own losses - and the scripted check re-walks the
  same input; it cannot fail.
- The seed panel varies Monte Carlo error only. No additional games exist.
