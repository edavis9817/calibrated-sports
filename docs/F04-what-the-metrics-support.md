# F04 — 85% of what track F measures says nothing about a given player

TRACK F · cs-analytics · `C:\Users\Ethan Davis\code\cs-analytics`

    python -m analytics.audit
    python -m analytics.audit --by-metric
    python -m analytics.audit --survivors

Measured 2026-09-18 over 87 published metrics and 52,583 values.

---

## The criterion, and why it is track B's

**The 95% interval excludes the null.** Exactly the test track B applied to the
prop record — 110 of 1,584 markets distinguishable from even money, 1,474 saying
nothing either way, **93.1% inconclusive**. Same arithmetic here so the two
records are comparable; only the null differs, and where it differs that is
stated rather than smoothed over.

`analytics.gate` already refuses a number without an interval and a sample
count. That makes a metric *publishable*. This is the next question: which of
them are distinguishable from saying nothing. Publishing all 87 undifferentiated
is noise.

---

## The headline, and it pairs with B's

**Per player, on the metrics with a genuine zero:**

| | |
|---|---|
| distinguishable from no game-script effect | **485 of 3,287** (14.8%) |
| saying nothing either way | **2,802** (**85.2% inconclusive**) |

| pipeline | criterion | decisive | inconclusive |
|---|---|---|---|
| Track B, prop record | interval excludes 0.5 | 110 / 1,584 (6.9%) | **93.1%** |
| **Track F, game-script elasticity** | interval excludes 0 | **485 / 3,287 (14.8%)** | **85.2%** |

Two pipelines, two sources, one criterion, the same answer in kind: **the large
majority of per-subject claims this project can make are claims it cannot
make.** Track F's number is better than track B's and both are damning of the
genre.

By usage kind, with the median interval width — the width is the reason:

| kind | players | decisive | share | median interval width |
|---|---|---|---|---|
| targets | 1,029 | 145 | 14.1% | 0.095 |
| receptions | 1,453 | 175 | 12.0% | 0.120 |
| carries | 805 | 165 | 20.5% | 0.186 |

A median receptions elasticity interval spans **0.120 of team share**. Typical
elasticities are a few hundredths. The interval is wider than the effect for
most players, and that is the finding: *"he's a garbage-time merchant"* is,
for 85% of players, not a statement the data supports.

### Carries separating best is corroboration, and it is worth saying so

Carries separate best at **20.5%**, against 14.1% for targets and 12.0% for
receptions. F02 found, from a completely different question, that **carry share
is the one usage metric whose week-to-week deviations persist** (+0.237
[+0.216, +0.258]) while target and reception share are mean-reverting.

Two questions, one answer: the run game's usage is more structured than the pass
game's, in the time dimension and in the script dimension alike. The mechanism is
the same in both — a backfield changes by decision and the decision holds, so
carry share carries more signal relative to its noise; targets move with
coverage and script and reset weekly.

**Two findings agreeing is worth more than either alone**, and it is the same
shape as track B's 110-of-1,584 standing beside their "0 of 17": neither number
is load-bearing on its own and together they describe one thing.

One distinction worth keeping, because conflating it would be the "agreement
between two unchecked arguments" trap wearing a better suit. **F02 and F04 are
independent QUESTIONS over the same facts, not independent data** — both read
`f_play_usage`. Track B's record and track F's are independent *pipelines* over
*different sources*, which is the stronger form. Both are real corroboration;
they are not the same strength, and the headline table above is the stronger
one.

---

## Three blocks, and they are not added together

### 1. Zero-null, per player — 485 / 3,287 (14.8%)

Game-script elasticity: a difference of two shares, so "no effect" is exactly
zero. Directly comparable to track B. This is the headline above.

### 2. Zero-null, league-wide — 69 / 72 (95.8%)

The stability correlations (`usage_stability.*`, `ngs_stability.*`). Each is one
value computed over 96–2,135 players, so it resolves almost by construction.
**This is a different question and is reported separately rather than pooled**;
pooling it with block 1 would lift the headline to 16.5% on the strength of
figures that were never about an individual.

The three that do *not* exclude zero are worth naming, because a league figure
failing this test is saying something:

| metric | estimate | interval | n |
|---|---|---|---|
| `ngs_stability.within_lag1.passing.avg_time_to_throw` | +0.0249 | [−0.0102, +0.0603] | 96 |
| `ngs_stability.within_lag1.rushing.avg_time_to_los` | −0.0149 | [−0.0562, +0.0303] | 140 |
| `ngs_stability.naive_lag1.rushing.rush_pct_over_expected` | +0.0331 | [−0.0140, +0.0783] | 116 |

Two of the three are the NGS columns F03 already flagged as the only ones whose
within-player figure contained zero. The audit reproduces that independently.

### 3. Reference-null, descriptive — 27,124 / 49,224 (55.1%)

Shares, quantiles, rates. **These have no meaningful zero** — every target-share
interval excludes 0, so testing against it returns ~100% and means nothing. A
figure like that placed beside track B's 93.1% would look like a finding and be
arithmetic.

The honest analogue of "even money" is the level at which the metric says
nothing *about this subject*: the population median. 55.1% of values are
distinguishable from the median subject.

**Read that as a ceiling, not an estimate.** Two reasons, both stated rather
than buried:

- The reference is treated as **fixed**. The population median carries its own
  uncertainty and this ignores it, so the count is generous.
- "Differs from the median player" is a **low bar** that mostly reflects
  population spread, not precision. A star receiver's target share differing
  from the median receiver's is nearly content-free.

The sharper question a leaderboard actually poses — can adjacent ranks be
separated — is **not answered here**.

Within block 3 the spread is itself informative:

| metric | values | decisive | share |
|---|---|---|---|
| `role.onfield_share` | 10,531 | 7,587 | 72.0% |
| `air_yards.quantiles.receiver` | 6,284 | 3,963 | 63.1% |
| `air_yards.bins.receiver` | 10,997 | 6,252 | 56.9% |
| `pace.seconds_per_play` | 32 | 18 | 56.2% |
| `role.touch_share` | 8,781 | 3,993 | 45.5% |
| `air_yards.quantiles.passer` | 900 | 169 | 18.8% |
| `pace.plays_per_game` | 32 | 3 | **9.4%** |
| `pace.plays_per_game.by_season` | 861 | 71 | **8.2%** |

**`pace.plays_per_game` is the one to act on: 3 of 32 teams distinguishable from
the median team.** F02 already said its intervals were six times wider than
`seconds_per_play`'s and that it remains partly a scoreboard measure. This
quantifies it — on 28 of 32 teams the metric cannot tell them from average. It
should not go on a page as a team figure.

---

## What goes on the analytics page first

On this evidence, in order:

1. **`usage_stability.*` — all 18, decisive, league-level.** F02's finding: target
   share deviations are mean-reverting (−0.014 [−0.024, −0.003]) while carry
   share persists (+0.237 [+0.216, +0.258]). Every value excludes zero at
   n = 1,101–2,135 players.
2. **`ngs_stability.*` — 51 of 54 decisive**, and the three that are not are
   informative in their own right. F03's finding: nothing NGS adds over the
   play-by-play carries week to week.
3. **The 485 named players whose game-script elasticity is decisive** — not the
   3,287. A per-player elasticity page that lists everyone is 85% noise; one
   that lists 485 with intervals is a finding.
4. **Air-yard shape, by receiver** — 56.9% of bin shares and 63.1% of quantiles
   separate. The weakest of the four and still the majority.

**Not on the page:** `pace.plays_per_game` in either form (9.4% / 8.2%), and no
per-player metric rendered as a ranked list without its interval, since between
44% and 91% of the rows would be indistinguishable from the row above.

---

## What this does not say

- **Not that the measurements are wrong.** An interval that includes the null is
  a correct statement about how much the data supports. The alternative is a
  point estimate that hides it, which is what every stats site publishes.
- **Not that 85% of the work was wasted.** The 485 are found *by* having
  measured 3,287, and knowing which 2,802 cannot be spoken about is the
  product.
- **Nothing about whether any of it is tradeable.** The record already holds
  that the model forecasts worse than the market in every season walk-forward.
