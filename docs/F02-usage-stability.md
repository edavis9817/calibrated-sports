# F02 — "His target share is trending" is noise

TRACK F · cs-analytics · `C:\Users\Ethan Davis\code\cs-analytics`

Written for the analytics page. Every figure below is re-derivable:

    python -m analytics.spine --build
    python -m analytics.stability --publish
    python -m analytics.stability                # the table
    python -m analytics.crn_check --synthetic    # the side-by-side caveat

Measured 2026-09-17 on 1,282,384 plays across 1999–2026.

---

## The finding

Week-to-week movement in a receiver's usage does not carry to next week. It is
very slightly **mean-reverting**, and the interval excludes zero.

Everything that looks like week-to-week predictability is a fact about *which
player he is*, not about *what he did last week*.

| usage metric | naive week-to-week | **within-player** | between-player | players | player-weeks |
|---|---|---|---|---|---|
| target share | +0.532 [+0.514, +0.549] | **−0.014 [−0.024, −0.003]** | 0.550 [0.533, 0.566] | 1,540 | 63,481 |
| targets | +0.488 [+0.471, +0.504] | **−0.015 [−0.026, −0.004]** | 0.511 [0.494, 0.526] | 1,540 | 63,481 |
| reception share | +0.404 [+0.390, +0.418] | **−0.040 [−0.048, −0.032]** | 0.441 [0.428, 0.454] | 2,135 | 95,522 |
| receptions | +0.381 [+0.367, +0.397] | **−0.043 [−0.051, −0.034]** | 0.422 [0.408, 0.437] | 2,135 | 95,522 |
| carry share | +0.799 [+0.786, +0.811] | **+0.237 [+0.216, +0.258]** | 0.732 [0.713, 0.749] | 1,101 | 44,459 |
| carries | +0.696 [+0.681, +0.710] | **+0.114 [+0.094, +0.135]** | 0.659 [0.641, 0.675] | 1,101 | 44,459 |

95% block-bootstrap intervals. **`n` is players, not player-weeks** — a player
contributes many weeks and many seasons and they are not independent of each
other, so the block is the player. Quoting 63,481 as the sample would make
every interval about six times too narrow.

**Read the first two numeric columns together.** The naive column is the one a
"trending" claim is implicitly appealing to: pool every consecutive pair of
weeks from every player and correlate them, and target share reads **+0.53**.
Subtract each player's own season average first — which is what "is he trending
*for him*" actually asks — and the same pairs read **−0.014**.

The gap between those two columns is the whole finding. It is not that usage is
unpredictable. It is that **all of the predictability is already in the
player's level, and none of it is in the direction he is moving.**

---

## The one exception, and it is worth naming

**Carry share persists: +0.237 [+0.216, +0.258].** A back whose workload jumped
last week is genuinely more likely to have a raised workload this week. Carries
(the raw count) persist too, at +0.114.

That is a real asymmetry between the run game and the pass game, and it is the
reason this table publishes six rows instead of one. A backfield changes by
decision — a committee resolves, a starter is benched — and the decision holds
for a while. Targets move with coverage, script and who else is on the field,
and those reset weekly.

If a "trending" claim is ever worth making, it is about a running back's
workload. It is not about a receiver's target share.

---

## What this refines in the research record

The record already holds **"usage carries the signal; efficiency is noise"**
(2026-09-09, opponent defence and game script added ~0.000 OOS R² on top of
prior usage) and **"player residuals don't persist" (r ≈ 0.09)**.

This does not contradict either. It splits the first one:

- **The LEVEL of usage carries the signal.** Between-player share is 0.42–0.73
  — most of the variance in weekly usage is which player you are looking at.
  A prior-usage feature works, and that is why.
- **The DEVIATION carries nothing**, for receivers. −0.014 to −0.043, intervals
  excluding zero, and on the wrong side of it.

So "usage is predictive" and "his usage is trending up" are not the same claim,
and only the first survives measurement. The record's r ≈ 0.09 for player
residuals was about *efficiency* residuals; this is the same shape one level
out, on volume.

---

## Ranges, and why they differ per row

Each row's range is **derived from the coverage survey, not typed**
(`analytics/metrics.py`), and the registry refuses a metric that will not say
its range:

| rows | range | why |
|---|---|---|
| receptions, carries and their shares | **1999–2026** | nothing binds them |
| targets, target share | **2009–2026** | a target is not attributable on an incompletion for 2003–2008 — the receiver is named on 0.6% of them against 82.7% either side, so the six seasons cannot be used and the arm starts after them |

See `docs/F01-pbp-survey.md` §2.1. The bound is a measurement, not a
convention: `python -m analytics.survey --column receiver_player_id --condition
incomplete_pass` prints the series it came from.

---

## Reading two players side by side

**Overlapping intervals do not mean "no difference", and non-overlapping
intervals are a stricter test than a direct comparison.** That is true of any
pair of independent intervals and it is not special to this table.

What *is* specific here, and was checked rather than assumed: the bootstrap
draws are **independent between subjects**.

An earlier version cached the resampling matrix by block count, so every
subject with the same number of games shared the same draws — common random
numbers. That changes no interval's centre and no interval's width, but it
correlates the Monte Carlo error *between* subjects, which is exactly what a
reader consumes when they put two intervals side by side.

`analytics/crn_check.py` measures the variance of the gap a reader eyeballs,
relative to independent draws, against the per-game correlation between the two
subjects:

    per-game correlation   -0.9   -0.6   -0.3    0.0   +0.3   +0.6   +0.9
    shared draws          1.571  1.226  1.078  1.026  0.981  0.952  0.980
    independent           1.000  1.000  1.000  1.000  1.000  1.000  1.000

Above 1 is **anti-conservative**: more noise in the comparison than the
intervals advertise, so two of them separate when they should not.

Two receivers on one team share a denominator — team targets — so their weekly
shares are mechanically opposed, and they measure **−0.215** on the 2024 slate.
That is the anti-conservative half of the curve, and teammates side by side is
precisely the comparison this site invites. At −0.9 the eyeballed gap carries
**57% more variance than it appears to**.

So the caching is gone. Every subject draws independently, seeded from its own
id so the figures still reproduce exactly. The measured cost of that was 22–208
seconds across all 27,000 published player-slices; the earlier comment claiming
it was ~99% of runtime was an estimate and it was wrong.

A per-subject column permutation was also measured — flat at 0.985–1.020 across
the whole range, so it would have worked. It is not used: two minutes does not
buy an extra mechanism to reason about.

---

## What this does not say

- **Not that usage is unpredictable.** The between-player column is the
  opposite claim, and it is large.
- **Not that a player's role never changes.** A lag-1 correlation over a season
  cannot see a mid-season role change that holds; it sees the week after the
  week it happened. A change-point measure would be a different study.
- **Not that this is tradeable.** It is a measurement of the data, not a
  forecast and not an edge. The record already holds that the model forecasts
  worse than the market in every season walk-forward (brief 023), and nothing
  here revises that.
- **Nothing about efficiency**, yards, or touchdowns. Six usage metrics only.
