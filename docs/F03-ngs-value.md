# F03 — Next Gen Stats: half of it is the play-by-play, and the other half doesn't carry

TRACK F · cs-analytics · `C:\Users\Ethan Davis\code\cs-analytics`

    python -m analytics.ngs --build
    python -m analytics.ngs --redundancy
    python -m analytics.ngs --stability

Measured 2026-09-17. Availability was settled separately (`F01` §3b): NGS is
LIVE tier and carries the current season. **This is the value question.**

---

## The verdict

NGS is the most widely discussed advanced football data there is. Measured
against the play-by-play we already have:

- **11 of its 29 measurable columns are the play-by-play under another name**,
  correlating **0.96 to 1.0000** with a number already derivable.
- **18 columns have no play-by-play equivalent** — the tracking ones, which are
  the only ones that can add anything.
- **Not one of those 18 carries positive week-to-week signal within a player.**
  Sixteen are mean-reverting with intervals excluding zero; two contain zero.
- And they separate players **less well** than the play-by-play usage metrics
  do: between-player share 0.10–0.39, against 0.42–0.73 for plain usage.

So NGS differentiates least, which is what was expected of it, and the
measurement says so in both directions at once: what it repeats is redundant,
and what is genuinely new is weekly noise around a player trait that PBP
already captures more sharply.

---

## 1. Can the play-by-play already make this number?

Per player-week, NGS column against its play-by-play twin.

| family | column | n | r | verdict |
|---|---|---|---|---|
| receiving | receptions | 12,927 | **1.0000** | redundant |
| receiving | yards | 12,891 | 0.9999 | redundant |
| receiving | targets | 12,927 | 0.9972 | redundant |
| receiving | catch_percentage | 12,927 | 0.9968 | redundant |
| receiving | avg_yac | 12,883 | 0.9843 | near-duplicate |
| receiving | avg_intended_air_yards | 12,927 | 0.9761 | near-duplicate |
| rushing | rush_yards / avg_rush_yards | 5,349 | 0.9999 | redundant |
| rushing | rush_attempts | 5,349 | 0.9994 | redundant |
| passing | attempts | 5,317 | 0.9791 | near-duplicate |
| passing | avg_intended_air_yards | 5,317 | 0.9648 | near-duplicate |

`avg_intended_air_yards` at 0.976 is the interesting one: it is aDOT, which the
play-by-play has had since 2006 for **every** passer, against NGS's filtered
167. The 2.4% of variance it does not share is a definitional difference, not
extra information.

The 18 with no equivalent are the tracking columns: cushion, separation,
expected YAC, time to LOS, time to throw, aggressiveness, the
over-expected family, and `percent_share_of_intended_air_yards`.

---

## 2. Do the tracking columns carry within-player signal?

Same decomposition as `F02`. `n` is **players**.

| family | column | **within-player lag-1** | naive lag-1 | between-player | players |
|---|---|---|---|---|---|
| receiving | avg_cushion | **−0.109 [−0.135, −0.082]** | +0.111 | 0.211 | 326 |
| receiving | avg_separation | **−0.068 [−0.093, −0.043]** | +0.202 | 0.264 | 326 |
| receiving | avg_expected_yac | **−0.084 [−0.107, −0.061]** | +0.110 | 0.188 | 325 |
| receiving | avg_yac_above_expectation | **−0.083 [−0.112, −0.053]** | +0.090 | 0.165 | 325 |
| receiving | percent_share_of_intended_air_yards | **−0.074 [−0.096, −0.051]** | +0.332 | 0.394 | 326 |
| rushing | efficiency | **−0.055 [−0.129, −0.032]** | +0.031 | 0.096 | 140 |
| rushing | percent_attempts_gte_eight_defenders | **−0.059 [−0.100, −0.018]** | +0.217 | 0.256 | 140 |
| rushing | avg_time_to_los | −0.015 [−0.056, +0.030] | +0.312 | 0.325 | 140 |
| rushing | expected_rush_yards | **−0.046 [−0.094, −0.002]** | +0.147 | 0.195 | 116 |
| rushing | rush_yards_over_expected_per_att | **−0.094 [−0.133, −0.057]** | +0.053 | 0.126 | 116 |
| rushing | rush_pct_over_expected | **−0.105 [−0.149, −0.063]** | +0.033 | 0.124 | 116 |
| passing | avg_time_to_throw | +0.025 [−0.010, +0.060] | +0.379 | 0.363 | 96 |
| passing | aggressiveness | **−0.062 [−0.095, −0.030]** | +0.123 | 0.176 | 96 |
| passing | avg_air_yards_to_sticks | **−0.050 [−0.083, −0.016]** | +0.145 | 0.191 | 96 |
| passing | expected_completion_percentage | **−0.067 [−0.094, −0.037]** | +0.166 | 0.216 | 96 |
| passing | completion_percentage_above_expectation | **−0.079 [−0.106, −0.052]** | +0.099 | 0.168 | 96 |
| passing | avg_air_distance | **−0.038 [−0.072, −0.007]** | +0.217 | 0.249 | 96 |
| passing | max_air_distance | **−0.072 [−0.101, −0.043]** | +0.113 | 0.176 | 96 |

**The naive column is doing the same work it did in F02.** Every one of these
reads positive pooled — separation +0.20, time to throw +0.38, intended-air-yard
share +0.33 — and every one of those is the player, not the week.

**The between-player column is the honest caveat, and it does not rescue them.**
A near-zero within-player figure would still leave a column useful as a *level*
if it separated players well — a QB's release time is a real trait, and
`avg_time_to_throw` at 0.363 is the best case here. But the play-by-play's own
usage metrics separate players at **0.42–0.73**. Every NGS tracking column is
below the weakest of them. They are neither more persistent nor more
discriminating.

---

## 3. What NGS costs you to use

Two coverage facts, both measured, both larger than expected.

**It is a qualifying-threshold leaderboard, twice over.** 120–132 receivers a
season against ~500 with at least one target in the play-by-play — **23.6% to
29.2%**. And then the *weeks within a covered player's season are filtered
again*: **1,166 of 1,325 receiving player-seasons are missing at least one
week.**

That second one was found by the week-0 reconciliation refusing to pass.

**Week 0 is the season total.** Aggregate over all weeks and every figure
doubles — the silent-double class, which survives every sanity check a reader
would apply because ratios, rankings and correlations are all unchanged and only
magnitudes move.

`assert_week_zero_reconciles` was written as an equality and **refused the build
at 1,166 of 1,325 receiving rows**. The refusal was correct and the reason was
the second coverage fact above: a player below the weekly threshold has no row
for that week while his targets still count in the season total. The invariant
is `week0 >= sum(weeks)`, and a week-0 figure *smaller* than its own weeks still
refuses — that would mean the trap had changed shape.

Because weeks go missing inside a season, the lag-1 pairing only ever uses
**consecutive calendar weeks**, so a filtered-out week breaks a pair rather than
silently spanning two.

---

## 4. Where this leaves NGS

**Not worth building on, and worth having said so with numbers.** The brief
predicted it would differentiate least because it is free, static and widely
discussed. That was a prediction about the market; this is a measurement of the
data, and they agree — which is the one case where agreement means something,
because one of the two was checked.

What would change it:

- **A within-player question NGS can answer that PBP cannot**, measured the same
  way. Nothing in these 18 columns is it.
- **Level-based use, not trend-based.** `avg_time_to_throw` (between 0.363) and
  `percent_share_of_intended_air_yards` (0.394) are genuine traits. They are
  still below every PBP usage metric, so they would have to earn their place
  against those, not against nothing.
- **A different NGS product.** These three files are leaderboards. The
  underlying tracking data is not what nflverse ships.

Nothing here is published to the site. The 54 values sit in `f_metric_values`
under `ngs_stability.*` so the claim is queryable rather than remembered.
