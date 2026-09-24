# A20 — strength of schedule, measured

Unit a-20 (track A, 2026-09-24, unattended). Code: `analytics/schedule.py`.
Study: `research/schedule_strength.py`. Tests: `tests/test_analytics_schedule.py`.

## What is published (analytics.db registry, `analytics/nfl/schedule.*.json` via `analytics.export`; NOT uploaded)

| metric | subject | slice | range |
|---|---|---|---|
| `schedule.efficiency.{wr,te}` | defense | season | 2009-2026 (derived: `weekly_stats.targets` silent-zero 2003-08) |
| `schedule.efficiency.rb` | defense | season | 1999-2026 |
| `schedule.opportunity.{wr,te}` | defense | season | 2009-2026 |
| `schedule.opportunity.rb` | defense | season | 1999-2026 |
| `schedule.rest_of_season.{efficiency,opportunity}.{wr,te,rb}` | offense | `wWW_OPP` per remaining game, `''` = mean | 2026 only (floor: a remaining schedule exists only for the season in progress) |

Efficiency = yards per target (WR/TE) or per carry (RB) above each opposing player's own
leave-one-out season rate. Opportunity = the opposing position group's targets/carries over
that offense's own leave-one-out per-game average, minus 1. Interval: Dirichlet (Bayesian)
bootstrap over every game of the season, baselines recomputed on each draw, widened by
`schedule.scale(n)` = t(n-1)/1.96 * sqrt((n+1)/(n-1)). n = the defense's games.

Rest of season is published per OFFENSE, not per player: nothing in it depends on the player,
so per-player rows would be the team's number repeated ~5x with different Monte Carlo noise.
A player page resolves player -> team.

## As of 2026 week 2 (schedule pull 2026-09-24, stats pull 2026-09-23)

Combined rest-of-season verdicts, 32 offenses each:

| metric | not measurably easier or harder | not measurable yet | easier / harder |
|---|---|---|---|
| efficiency.wr | 20 | 12 | 0 |
| efficiency.te | 0 | 32 | 0 |
| efficiency.rb | 5 | 27 | 0 |
| opportunity.wr | 32 | 0 | 0 |
| opportunity.te | 32 | 0 | 0 |
| opportunity.rb | 32 | 0 | 0 |

"Not measurable yet" = at least one remaining opponent has < 2 measurable games (the
baseline floor `MIN_BASELINE` drops player-games whose other games carry < 4 targets / 8
carries). The export DROPS those values (unbounded interval), so on the site they are absent,
not stated.

## Does it persist? (`python -m research.schedule_strength`, 2009-2025, draws 1000)

Defense effect over weeks 1..k against weeks k+1..18, disjoint games and disjoint baselines.
Intervals: block bootstrap over seasons. SD(z) should be 1 if the interval is honest and the
effect stable; above 1 means the interval understates how far the rest of the season lands.

    seasons 2009-2025; draws 1000; intervals over seasons (block = season)
    grp measure       k  szn  defs  r(early, rest)             r(prior season, rest)      SD(z) raw                SD(z) t                  |z|>2 raw      t
    rb  efficiency    2   17   542  +0.011 [-0.058, +0.097]    +0.232 [+0.136, +0.323]    2.96 [2.65, 3.25]        0.77 [0.63, 0.89]         0.436  0.039
    rb  efficiency    4   17   544  +0.135 [+0.023, +0.244]    +0.220 [+0.132, +0.305]    1.33 [1.24, 1.41]        0.70 [0.65, 0.75]         0.138  0.017
    rb  efficiency    8   17   544  +0.253 [+0.172, +0.336]    +0.207 [+0.142, +0.277]    1.03 [0.97, 1.08]        0.75 [0.71, 0.79]         0.055  0.007
    rb  opportunity   2   17   544  +0.169 [+0.114, +0.229]    +0.312 [+0.240, +0.384]    2.76 [2.42, 3.17]        0.87 [0.71, 1.03]         0.387  0.033
    rb  opportunity   4   17   544  +0.277 [+0.222, +0.339]    +0.280 [+0.205, +0.364]    1.22 [1.14, 1.30]        0.66 [0.61, 0.70]         0.105  0.013
    rb  opportunity   8   17   544  +0.324 [+0.251, +0.386]    +0.235 [+0.151, +0.317]    1.15 [1.08, 1.22]        0.84 [0.78, 0.89]         0.099  0.015
    te  efficiency    2   17   477  +0.016 [-0.078, +0.102]    +0.108 [-0.001, +0.214]    2.67 [2.35, 3.02]        0.69 [0.51, 0.87]         0.395  0.019
    te  efficiency    4   17   544  +0.017 [-0.075, +0.104]    +0.089 [-0.003, +0.179]    1.21 [1.12, 1.31]        0.61 [0.56, 0.65]         0.101  0.007
    te  efficiency    8   17   544  +0.034 [-0.067, +0.134]    +0.097 [-0.014, +0.192]    1.03 [0.97, 1.10]        0.75 [0.70, 0.79]         0.044  0.006
    te  opportunity   2   17   544  -0.044 [-0.128, +0.046]    +0.126 [+0.040, +0.197]    2.67 [2.35, 3.01]        0.89 [0.72, 1.04]         0.348  0.048
    te  opportunity   4   17   544  +0.086 [-0.004, +0.192]    +0.103 [-0.007, +0.173]    1.15 [1.06, 1.23]        0.64 [0.58, 0.68]         0.088  0.013
    te  opportunity   8   17   544  +0.084 [-0.004, +0.168]    +0.077 [+0.011, +0.146]    1.10 [1.04, 1.17]        0.81 [0.76, 0.85]         0.072  0.022
    wr  efficiency    2   17   544  +0.074 [+0.004, +0.142]    +0.189 [+0.099, +0.278]    3.12 [2.81, 3.47]        0.98 [0.83, 1.14]         0.428  0.057
    wr  efficiency    4   17   544  +0.178 [+0.081, +0.277]    +0.180 [+0.086, +0.267]    1.13 [1.06, 1.21]        0.61 [0.56, 0.66]         0.077  0.006
    wr  efficiency    8   17   544  +0.190 [+0.121, +0.252]    +0.134 [+0.044, +0.224]    1.06 [1.01, 1.12]        0.78 [0.74, 0.81]         0.074  0.015
    wr  opportunity   2   17   544  +0.152 [+0.088, +0.216]    +0.053 [+0.047, +0.245]    2.82 [2.55, 3.10]        0.94 [0.73, 1.13]         0.383  0.043
    wr  opportunity   4   17   544  +0.269 [+0.200, +0.340]    +0.047 [+0.041, +0.210]    1.23 [1.16, 1.31]        0.67 [0.63, 0.70]         0.108  0.004
    wr  opportunity   8   17   544  +0.277 [+0.215, +0.344]    +0.055 [+0.049, +0.210]    1.11 [1.06, 1.15]        0.81 [0.77, 0.84]         0.070  0.017

Read:

- **The raw bootstrap is not usable early.** At k=2 SD(z) is 2.7-3.1 and 35-44% of defenses
  land outside their own interval; at k=4 it is still 1.1-1.3. A bootstrap over two games can
  only see the spread two games show. Measured on the live slate before the correction: LAC's
  WR efficiency read +6.1 [+5.5, +6.5] off two games.
- **The t-widened interval is conservative in all 18 cells** (SD(z) 0.61-0.98, |z| > 1.96 in
  0.4-5.7%). That measurement is what licenses publishing it. It errs wide, which is the
  direction the brief asked for.
- **Early-season efficiency says almost nothing about the rest of the season.** r at k=2 is
  0.01-0.07 across groups; by k=8 RB 0.25, WR 0.19, TE 0.03 (interval spans 0). Opportunity
  persists more (WR/RB 0.28-0.32 at k=8), consistent with CLAUDE.md "usage carries the
  signal; efficiency is noise".
- **For EFFICIENCY, last season's effect is no worse a predictor than this season's first
  weeks, and at k=2 it is better** (RB 0.23 vs 0.01; WR 0.19 vs 0.07; TE 0.11 vs 0.02, prior's
  interval touching 0). Not for WR opportunity, where two weeks beat last season (0.15 vs
  0.05) and keep beating it through k=8 (0.28 vs 0.06). The genre's number is not
  refuted by this measurement - it is simply weak, like everything else here: no correlation
  in the table exceeds 0.33, i.e. at most ~11% of the variance in a defense's rest-of-season
  effect is predictable from either source. TE efficiency is predictable from neither.
- r(early, rest) is attenuated by noise in BOTH halves; it is a lower bound on the persistence
  of the true effect, not the persistence itself.

## No shrinkage

The brief named `analytics/shrinkage.py`; it does not exist on any branch of the remote. The
fitted `k` lives in `research/shrinkage.py` and is a weight on a player's own usage mean,
fitted on player usage; it does not describe this shape and is not borrowed.
