# F05 — what the analytics page says

TRACK F · cs-analytics · `C:\Users\Ethan Davis\code\cs-analytics`

    python -m analytics.claims
    python -m analytics.claims --metric usage_stability.within_lag1.target_share

**Every sentence below is generated, not written.** `CLAUDE.md` already holds
that no comparative claim on this site is hand-written, and the A10 extension
covers exported prose and not only rendered text — five captions written from
memory were wrong in both directions. Writing 87 sentences by hand would be that
defect with a larger surface. So `analytics/claims.py` holds the vocabulary as
data, takes the verdict from the interval, and assembles the sentence; this
document shows what it emits and is not the source of it. Re-run the command and
the copy regenerates.

## The rule the wording must not break

From track B's `rateVerdict`: an interval covering the null says **"cannot be
distinguished from X"**, never "is the same as X". Absence of evidence is not
evidence of absence, and copy is exactly where that conversion happens quietly.

`claims.BANNED` is the mechanical form — "is the same as", "identical to", "no
difference", "has no effect", "shows no", "proves", "is zero" and six more — and
`tests/test_analytics_claims.py` drives every sentence the module can emit past
it, plus every published sentence in the store.

Two smaller rules fell out of writing it, both now tested:

- **A descriptive metric is never told it is zero.** "Cannot be distinguished
  from zero target share" is true and absurd. A share's null is *a typical
  player*, and the vocabulary says so.
- **The stability families do not share wording.** `within_lag1` is persistence
  of a player's own weekly swings; `between` is how much of the variance
  separates players at all. Calling the second one persistence would have been
  a hand-written claim that merely happened to be assembled.

## Four verdicts, all reachable

`above`, `below`, `indistinguishable`, `insufficient`. A verdict value that
cannot occur is decoration — the falsifiability rule the research register
already carries — so all four are driven in test. **`insufficient` fires on 0 of
52,583 values today**, because the smallest published `n` is 8 against a
threshold of 5 (brief 020: an interval over fewer than five blocks is not read).
It is counted out loud anyway: a number that reads 0 today is what makes the day
it reads 12 visible.

## Page order

From `docs/F04-what-the-metrics-support.md`, which measured which metrics
support a statement at all.

### 1. Usage stability — 18 metrics, every one decisive

F02's finding, worded. The first three lines are the site's thesis in its own vocabulary: the level separates players, the week-to-week swing does not carry, and carry share is the exception.

**`usage_stability.between.carries`** — 1999-2026, current  
Share of the variance in carries that separates players rather than weeks - the metric separates players: +0.659 (95% interval +0.641 to +0.675, n=1101 players).

**`usage_stability.between.carry_share`** — 1999-2026, current  
Share of the variance in carry share that separates players rather than weeks - the metric separates players: +0.732 (95% interval +0.713 to +0.749, n=1101 players).

**`usage_stability.between.reception_share`** — 1999-2026, current  
Share of the variance in reception share that separates players rather than weeks - the metric separates players: +0.441 (95% interval +0.428 to +0.454, n=2135 players).

**`usage_stability.between.receptions`** — 1999-2026, current  
Share of the variance in receptions that separates players rather than weeks - the metric separates players: +0.422 (95% interval +0.408 to +0.437, n=2135 players).

**`usage_stability.between.target_share`** — 2009-2026, current  
Share of the variance in target share that separates players rather than weeks - the metric separates players: +0.550 (95% interval +0.533 to +0.566, n=1540 players).

**`usage_stability.between.targets`** — 2009-2026, current  
Share of the variance in targets that separates players rather than weeks - the metric separates players: +0.511 (95% interval +0.494 to +0.526, n=1540 players).

**`usage_stability.naive_lag1.carries`** — 1999-2026, current  
Raw week-to-week correlation of carries, not adjusted for who the player is - positive, but see the adjusted figure beside it: +0.696 (95% interval +0.681 to +0.710, n=1101 players).

**`usage_stability.naive_lag1.carry_share`** — 1999-2026, current  
Raw week-to-week correlation of carry share, not adjusted for who the player is - positive, but see the adjusted figure beside it: +0.799 (95% interval +0.786 to +0.811, n=1101 players).

**`usage_stability.naive_lag1.reception_share`** — 1999-2026, current  
Raw week-to-week correlation of reception share, not adjusted for who the player is - positive, but see the adjusted figure beside it: +0.404 (95% interval +0.390 to +0.418, n=2135 players).

**`usage_stability.naive_lag1.receptions`** — 1999-2026, current  
Raw week-to-week correlation of receptions, not adjusted for who the player is - positive, but see the adjusted figure beside it: +0.381 (95% interval +0.367 to +0.397, n=2135 players).

**`usage_stability.naive_lag1.target_share`** — 2009-2026, current  
Raw week-to-week correlation of target share, not adjusted for who the player is - positive, but see the adjusted figure beside it: +0.532 (95% interval +0.514 to +0.549, n=1540 players).

**`usage_stability.naive_lag1.targets`** — 2009-2026, current  
Raw week-to-week correlation of targets, not adjusted for who the player is - positive, but see the adjusted figure beside it: +0.488 (95% interval +0.471 to +0.504, n=1540 players).

**`usage_stability.within_lag1.carries`** — 1999-2026, current  
Week-to-week persistence of a player's own swings in carries - this week's swing carries into next week: +0.114 (95% interval +0.094 to +0.135, n=1101 players).

**`usage_stability.within_lag1.carry_share`** — 1999-2026, current  
Week-to-week persistence of a player's own swings in carry share - this week's swing carries into next week: +0.237 (95% interval +0.216 to +0.258, n=1101 players).

**`usage_stability.within_lag1.reception_share`** — 1999-2026, current  
Week-to-week persistence of a player's own swings in reception share - this week's swing reverts by next week: -0.040 (95% interval -0.048 to -0.032, n=2135 players).

**`usage_stability.within_lag1.receptions`** — 1999-2026, current  
Week-to-week persistence of a player's own swings in receptions - this week's swing reverts by next week: -0.043 (95% interval -0.051 to -0.034, n=2135 players).

**`usage_stability.within_lag1.target_share`** — 2009-2026, current  
Week-to-week persistence of a player's own swings in target share - this week's swing reverts by next week: -0.014 (95% interval -0.024 to -0.003, n=1540 players).

**`usage_stability.within_lag1.targets`** — 2009-2026, current  
Week-to-week persistence of a player's own swings in targets - this week's swing reverts by next week: -0.015 (95% interval -0.026 to -0.004, n=1540 players).

### 2. Next Gen Stats stability — 54 metrics, 51 decisive

F03's finding, worded. The three `indistinguishable` rows are the honest ones and are content in their own right.

**`ngs_stability.between.passing.aggressiveness`** — 2016-2026, current  
Share of the variance in aggressiveness that separates players rather than weeks - the metric separates players: +0.176 (95% interval +0.147 to +0.204, n=96 players).

**`ngs_stability.between.passing.avg_air_distance`** — 2016-2026, current  
Share of the variance in avg air distance that separates players rather than weeks - the metric separates players: +0.249 (95% interval +0.214 to +0.283, n=96 players).

**`ngs_stability.between.passing.avg_air_yards_to_sticks`** — 2016-2026, current  
Share of the variance in avg air yards to sticks that separates players rather than weeks - the metric separates players: +0.191 (95% interval +0.163 to +0.222, n=96 players).

**`ngs_stability.between.passing.avg_time_to_throw`** — 2016-2026, current  
Share of the variance in avg time to throw that separates players rather than weeks - the metric separates players: +0.363 (95% interval +0.315 to +0.409, n=96 players).

**`ngs_stability.between.passing.completion_percentage_above_expectation`** — 2016-2026, current  
Share of the variance in completion percentage above expectation that separates players rather than weeks - the metric separates players: +0.168 (95% interval +0.144 to +0.191, n=96 players).

**`ngs_stability.between.passing.expected_completion_percentage`** — 2016-2026, current  
Share of the variance in expected completion percentage that separates players rather than weeks - the metric separates players: +0.216 (95% interval +0.188 to +0.246, n=96 players).

**`ngs_stability.between.passing.max_air_distance`** — 2016-2026, current  
Share of the variance in max air distance that separates players rather than weeks - the metric separates players: +0.176 (95% interval +0.151 to +0.201, n=96 players).

**`ngs_stability.between.receiving.avg_cushion`** — 2016-2026, current  
Share of the variance in avg cushion that separates players rather than weeks - the metric separates players: +0.211 (95% interval +0.191 to +0.231, n=326 players).

**`ngs_stability.between.receiving.avg_expected_yac`** — 2016-2026, current  
Share of the variance in avg expected yac that separates players rather than weeks - the metric separates players: +0.188 (95% interval +0.170 to +0.206, n=325 players).

**`ngs_stability.between.receiving.avg_separation`** — 2016-2026, current  
Share of the variance in avg separation that separates players rather than weeks - the metric separates players: +0.264 (95% interval +0.239 to +0.287, n=326 players).

**`ngs_stability.between.receiving.avg_yac_above_expectation`** — 2016-2026, current  
Share of the variance in avg yac above expectation that separates players rather than weeks - the metric separates players: +0.165 (95% interval +0.147 to +0.183, n=325 players).

**`ngs_stability.between.receiving.percent_share_of_intended_air_yards`** — 2016-2026, current  
Share of the variance in percent share of intended air yards that separates players rather than weeks - the metric separates players: +0.394 (95% interval +0.367 to +0.418, n=326 players).

**`ngs_stability.between.rushing.avg_time_to_los`** — 2016-2026, current  
Share of the variance in avg time to los that separates players rather than weeks - the metric separates players: +0.325 (95% interval +0.276 to +0.373, n=140 players).

**`ngs_stability.between.rushing.efficiency`** — 2016-2026, current  
Share of the variance in efficiency that separates players rather than weeks - the metric separates players: +0.096 (95% interval +0.085 to +0.142, n=140 players).

**`ngs_stability.between.rushing.expected_rush_yards`** — 2016-2026, current  
Share of the variance in expected rush yards that separates players rather than weeks - the metric separates players: +0.195 (95% interval +0.162 to +0.224, n=116 players).

**`ngs_stability.between.rushing.percent_attempts_gte_eight_defenders`** — 2016-2026, current  
Share of the variance in percent attempts gte eight defenders that separates players rather than weeks - the metric separates players: +0.256 (95% interval +0.220 to +0.288, n=140 players).

**`ngs_stability.between.rushing.rush_pct_over_expected`** — 2016-2026, current  
Share of the variance in rush pct over expected that separates players rather than weeks - the metric separates players: +0.124 (95% interval +0.105 to +0.143, n=116 players).

**`ngs_stability.between.rushing.rush_yards_over_expected_per_att`** — 2016-2026, current  
Share of the variance in rush yards over expected per att that separates players rather than weeks - the metric separates players: +0.126 (95% interval +0.104 to +0.146, n=116 players).

**`ngs_stability.naive_lag1.passing.aggressiveness`** — 2016-2026, current  
Raw week-to-week correlation of aggressiveness, not adjusted for who the player is - positive, but see the adjusted figure beside it: +0.123 (95% interval +0.081 to +0.160, n=96 players).

**`ngs_stability.naive_lag1.passing.avg_air_distance`** — 2016-2026, current  
Raw week-to-week correlation of avg air distance, not adjusted for who the player is - positive, but see the adjusted figure beside it: +0.217 (95% interval +0.168 to +0.257, n=96 players).

**`ngs_stability.naive_lag1.passing.avg_air_yards_to_sticks`** — 2016-2026, current  
Raw week-to-week correlation of avg air yards to sticks, not adjusted for who the player is - positive, but see the adjusted figure beside it: +0.145 (95% interval +0.104 to +0.186, n=96 players).

**`ngs_stability.naive_lag1.passing.avg_time_to_throw`** — 2016-2026, current  
Raw week-to-week correlation of avg time to throw, not adjusted for who the player is - positive, but see the adjusted figure beside it: +0.379 (95% interval +0.328 to +0.426, n=96 players).

**`ngs_stability.naive_lag1.passing.completion_percentage_above_expectation`** — 2016-2026, current  
Raw week-to-week correlation of completion percentage above expectation, not adjusted for who the player is - positive, but see the adjusted figure beside it: +0.099 (95% interval +0.066 to +0.131, n=96 players).

**`ngs_stability.naive_lag1.passing.expected_completion_percentage`** — 2016-2026, current  
Raw week-to-week correlation of expected completion percentage, not adjusted for who the player is - positive, but see the adjusted figure beside it: +0.166 (95% interval +0.129 to +0.206, n=96 players).

**`ngs_stability.naive_lag1.passing.max_air_distance`** — 2016-2026, current  
Raw week-to-week correlation of max air distance, not adjusted for who the player is - positive, but see the adjusted figure beside it: +0.113 (95% interval +0.077 to +0.147, n=96 players).

**`ngs_stability.naive_lag1.receiving.avg_cushion`** — 2016-2026, current  
Raw week-to-week correlation of avg cushion, not adjusted for who the player is - positive, but see the adjusted figure beside it: +0.111 (95% interval +0.076 to +0.145, n=326 players).

**`ngs_stability.naive_lag1.receiving.avg_expected_yac`** — 2016-2026, current  
Raw week-to-week correlation of avg expected yac, not adjusted for who the player is - positive, but see the adjusted figure beside it: +0.110 (95% interval +0.081 to +0.139, n=325 players).

**`ngs_stability.naive_lag1.receiving.avg_separation`** — 2016-2026, current  
Raw week-to-week correlation of avg separation, not adjusted for who the player is - positive, but see the adjusted figure beside it: +0.202 (95% interval +0.170 to +0.231, n=326 players).

**`ngs_stability.naive_lag1.receiving.avg_yac_above_expectation`** — 2016-2026, current  
Raw week-to-week correlation of avg yac above expectation, not adjusted for who the player is - positive, but see the adjusted figure beside it: +0.090 (95% interval +0.062 to +0.119, n=325 players).

**`ngs_stability.naive_lag1.receiving.percent_share_of_intended_air_yards`** — 2016-2026, current  
Raw week-to-week correlation of percent share of intended air yards, not adjusted for who the player is - positive, but see the adjusted figure beside it: +0.332 (95% interval +0.299 to +0.363, n=326 players).

**`ngs_stability.naive_lag1.rushing.avg_time_to_los`** — 2016-2026, current  
Raw week-to-week correlation of avg time to los, not adjusted for who the player is - positive, but see the adjusted figure beside it: +0.312 (95% interval +0.245 to +0.377, n=140 players).

**`ngs_stability.naive_lag1.rushing.efficiency`** — 2016-2026, current  
Raw week-to-week correlation of efficiency, not adjusted for who the player is - positive, but see the adjusted figure beside it: +0.031 (95% interval +0.000 to +0.070, n=140 players).

**`ngs_stability.naive_lag1.rushing.expected_rush_yards`** — 2016-2026, current  
Raw week-to-week correlation of expected rush yards, not adjusted for who the player is - positive, but see the adjusted figure beside it: +0.147 (95% interval +0.086 to +0.200, n=116 players).

**`ngs_stability.naive_lag1.rushing.percent_attempts_gte_eight_defenders`** — 2016-2026, current  
Raw week-to-week correlation of percent attempts gte eight defenders, not adjusted for who the player is - positive, but see the adjusted figure beside it: +0.217 (95% interval +0.163 to +0.265, n=140 players).

**`ngs_stability.naive_lag1.rushing.rush_pct_over_expected`** — 2016-2026, current  
Raw week-to-week correlation of rush pct over expected, not adjusted for who the player is cannot be distinguished from zero raw correlation (+0.033; 95% interval -0.014 to +0.078, n=116 players).

**`ngs_stability.naive_lag1.rushing.rush_yards_over_expected_per_att`** — 2016-2026, current  
Raw week-to-week correlation of rush yards over expected per att, not adjusted for who the player is - positive, but see the adjusted figure beside it: +0.053 (95% interval +0.003 to +0.095, n=116 players).

**`ngs_stability.within_lag1.passing.aggressiveness`** — 2016-2026, current  
Week-to-week persistence of a player's own swings in aggressiveness - this week's swing reverts by next week: -0.062 (95% interval -0.095 to -0.030, n=96 players).

**`ngs_stability.within_lag1.passing.avg_air_distance`** — 2016-2026, current  
Week-to-week persistence of a player's own swings in avg air distance - this week's swing reverts by next week: -0.038 (95% interval -0.072 to -0.007, n=96 players).

**`ngs_stability.within_lag1.passing.avg_air_yards_to_sticks`** — 2016-2026, current  
Week-to-week persistence of a player's own swings in avg air yards to sticks - this week's swing reverts by next week: -0.050 (95% interval -0.083 to -0.016, n=96 players).

**`ngs_stability.within_lag1.passing.avg_time_to_throw`** — 2016-2026, current  
Week-to-week persistence of a player's own swings in avg time to throw cannot be distinguished from zero week-to-week persistence (+0.025; 95% interval -0.010 to +0.060, n=96 players).

**`ngs_stability.within_lag1.passing.completion_percentage_above_expectation`** — 2016-2026, current  
Week-to-week persistence of a player's own swings in completion percentage above expectation - this week's swing reverts by next week: -0.079 (95% interval -0.106 to -0.052, n=96 players).

**`ngs_stability.within_lag1.passing.expected_completion_percentage`** — 2016-2026, current  
Week-to-week persistence of a player's own swings in expected completion percentage - this week's swing reverts by next week: -0.067 (95% interval -0.094 to -0.037, n=96 players).

**`ngs_stability.within_lag1.passing.max_air_distance`** — 2016-2026, current  
Week-to-week persistence of a player's own swings in max air distance - this week's swing reverts by next week: -0.072 (95% interval -0.101 to -0.043, n=96 players).

**`ngs_stability.within_lag1.receiving.avg_cushion`** — 2016-2026, current  
Week-to-week persistence of a player's own swings in avg cushion - this week's swing reverts by next week: -0.109 (95% interval -0.135 to -0.082, n=326 players).

**`ngs_stability.within_lag1.receiving.avg_expected_yac`** — 2016-2026, current  
Week-to-week persistence of a player's own swings in avg expected yac - this week's swing reverts by next week: -0.084 (95% interval -0.107 to -0.061, n=325 players).

**`ngs_stability.within_lag1.receiving.avg_separation`** — 2016-2026, current  
Week-to-week persistence of a player's own swings in avg separation - this week's swing reverts by next week: -0.068 (95% interval -0.093 to -0.043, n=326 players).

**`ngs_stability.within_lag1.receiving.avg_yac_above_expectation`** — 2016-2026, current  
Week-to-week persistence of a player's own swings in avg yac above expectation - this week's swing reverts by next week: -0.083 (95% interval -0.112 to -0.053, n=325 players).

**`ngs_stability.within_lag1.receiving.percent_share_of_intended_air_yards`** — 2016-2026, current  
Week-to-week persistence of a player's own swings in percent share of intended air yards - this week's swing reverts by next week: -0.074 (95% interval -0.096 to -0.051, n=326 players).

**`ngs_stability.within_lag1.rushing.avg_time_to_los`** — 2016-2026, current  
Week-to-week persistence of a player's own swings in avg time to los cannot be distinguished from zero week-to-week persistence (-0.015; 95% interval -0.056 to +0.030, n=140 players).

**`ngs_stability.within_lag1.rushing.efficiency`** — 2016-2026, current  
Week-to-week persistence of a player's own swings in efficiency - this week's swing reverts by next week: -0.055 (95% interval -0.129 to -0.032, n=140 players).

**`ngs_stability.within_lag1.rushing.expected_rush_yards`** — 2016-2026, current  
Week-to-week persistence of a player's own swings in expected rush yards - this week's swing reverts by next week: -0.046 (95% interval -0.094 to -0.002, n=116 players).

**`ngs_stability.within_lag1.rushing.percent_attempts_gte_eight_defenders`** — 2016-2026, current  
Week-to-week persistence of a player's own swings in percent attempts gte eight defenders - this week's swing reverts by next week: -0.059 (95% interval -0.100 to -0.018, n=140 players).

**`ngs_stability.within_lag1.rushing.rush_pct_over_expected`** — 2016-2026, current  
Week-to-week persistence of a player's own swings in rush pct over expected - this week's swing reverts by next week: -0.105 (95% interval -0.149 to -0.063, n=116 players).

**`ngs_stability.within_lag1.rushing.rush_yards_over_expected_per_att`** — 2016-2026, current  
Week-to-week persistence of a player's own swings in rush yards over expected per att - this week's swing reverts by next week: -0.094 (95% interval -0.133 to -0.057, n=116 players).

### 3. Game-script elasticity — 485 of 3,287 players

The metric-level line is the population claim; the per-player sentence is generated the same way from that player's own interval. A page listing all 3,287 is 85% noise, so it lists the 485.

**`script_elasticity.carries`** — 1999-2026, current  
Game-script elasticity: 1272 of 2415 players distinguishable from the typical one; the other 1143 say nothing either way.

**`script_elasticity.receptions`** — 1999-2026, current  
Game-script elasticity: 1414 of 4359 players distinguishable from the typical one; the other 2945 say nothing either way.

**`script_elasticity.targets`** — 2009-2026, current  
Game-script elasticity: 1182 of 3087 players distinguishable from the typical one; the other 1905 say nothing either way.

### 4. Air-yard shape

The weakest of the four that ship, and still a majority on the receiver side.

**`air_yards.bins.passer`** — 2006-2026, current  
Air-yard distribution: 489 of 1575 players distinguishable from the typical one; the other 1086 say nothing either way.

**`air_yards.bins.receiver`** — 2009-2026, current  
Air-yard distribution: 6252 of 10997 players distinguishable from the typical one; the other 4745 say nothing either way.

**`air_yards.polarity.passer`** — 2006-2026, current  
Air-yard distribution: 95 of 225 players distinguishable from the typical one; the other 130 say nothing either way.

**`air_yards.polarity.receiver`** — 2009-2026, current  
Air-yard distribution: 823 of 1571 players distinguishable from the typical one; the other 748 say nothing either way.

**`air_yards.quantiles.passer`** — 2006-2026, current  
Air-yard distribution: 169 of 900 players distinguishable from the typical one; the other 731 say nothing either way.

**`air_yards.quantiles.receiver`** — 2009-2026, current  
Air-yard distribution: 3963 of 6284 players distinguishable from the typical one; the other 2321 say nothing either way.

### Measured, and not published

`pace.plays_per_game` separates 3 of 32 teams from the median. It does not go on a page as a team figure, and the line saying so is content. `role.*` and `pace.seconds_per_play` are held for a different reason: they are per-subject metrics whose page shape is a ranked list, and whether adjacent ranks separate is not yet measured.

**`pace.plays_per_game`** — 1999-2026, current  
Pace: 3 of 32 teams distinguishable from the typical one; the other 29 say nothing either way.

**`pace.plays_per_game.by_season`** — 1999-2026, current  
Pace: 71 of 861 teams distinguishable from the typical one; the other 790 say nothing either way.

**`pace.seconds_per_play`** — 1999-2026, current  
Pace: 18 of 32 teams distinguishable from the typical one; the other 14 say nothing either way.

**`pace.seconds_per_play.by_season`** — 1999-2026, current  
Pace: 278 of 861 teams distinguishable from the typical one; the other 583 say nothing either way.

**`role.onfield_share`** — 2016-2025, historical  
Down-and-distance role: 7587 of 10531 players distinguishable from the typical one; the other 2944 say nothing either way.

**`role.touch_share`** — 1999-2026, current  
Down-and-distance role: 3993 of 8781 players distinguishable from the typical one; the other 4788 say nothing either way.

---

## What this leaves for track B

The sentences above are ready to render. What is **not** settled here, and is
track B's call:

- **How an `indistinguishable` row is displayed.** It is content, not an
  omission — "we measured this and it says nothing" is the site's thesis — but
  it should not look like a failed load. The design rule already holds that
  empty states differ by form, not hue alone.
- **Whether a per-player metric is ever a ranked list.** F04 did not answer
  whether adjacent ranks separate, only whether a subject separates from the
  median. Between 44% and 91% of rows in such a list would be
  indistinguishable from the row above, so a ranking needs that measurement
  first or it needs to not be a ranking.
- **Where the sentence is assembled.** Track F generates it here so the copy
  could be reviewed; the fields behind it — verdict, estimate, interval, n,
  null — are all in `f_metric_values` and `f_metrics` and track B may prefer to
  word it in `lib/claims` against its own vocabulary. Either is consistent with
  the rule, as long as it is computed. What must not happen is the sentence
  being transcribed into a component.

## What this does not say

- **Not that the 2,802 indistinguishable players lack a game-script effect.**
  They have an interval that contains zero. That is the whole point of the
  wording.
- **Nothing about tradeability.** The record holds that the model forecasts
  worse than the market in every season walk-forward.
- **Nothing is published.** F3 blocks the upload path; none of this reaches R2.
