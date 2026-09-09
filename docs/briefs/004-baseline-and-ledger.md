# 004 — A deliberately naive model, and the scoreboard around it

The most important sequencing decision in the project: **build the scoreboard
before the player.**

Ship a model you know is mediocre, end to end, inside a working measurement
system. A naive baseline that runs the whole pipeline is worth more than a good
model with no measurement — it proves the plumbing, and it gives you the number
every future improvement is measured against. It also means a real calibration
curve exists in October rather than January.

## Goal
1. `models/baseline.py` — projects each stat from the player's trailing
   expanding mean, emitting `NegativeBinomial` / `ZeroInflatedGamma` fitted to
   the moments in `research/`. No cleverness. This is the control.
2. `eval/ledger.py` — immutable forecast records written BEFORE resolution
3. `eval/score.py` — Brier, log loss, calibration curve, and CLV

## Contract
```
record_forecast(outcome_id, ts, model_version, feature_hash,
                fair_prob, market_price, venue, intended_action) -> id
score_settled(as_of) -> updates rows with closing_prob, result, clv, brier
```
Rows are append-only. A forecast is never edited after the fact — that is the
whole point.

**CLV is measured against the sharp close**, not against the price you took.

## Invariants
#3 distributions not scalars · #5 as-of features · #6 facts and beliefs separate

## Acceptance
- Baseline emits a `Distribution` for every supported stat; a test asserts
  `prob_over` exists and integrates sanely
- Ledger rows are immutable: an update attempt raises
- Feeding known outcomes to a perfectly calibrated synthetic model produces a
  calibration curve on the diagonal within tolerance
- A deliberately overconfident synthetic model is visibly off-diagonal
- CLV computes correctly on a hand-built fixture with known close prices
- Integration test: ingest -> baseline -> record -> settle -> score, end to end

## Out of scope
The usage model, the simulator, reallocation, any attempt to be accurate.
Accuracy is brief 005. This brief is about being *measurable*.
