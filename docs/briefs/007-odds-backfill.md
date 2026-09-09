# Odds API historical backfill — spec

Status: approved in principle 2026-09-09. Blocked on brief 003 (`outcome_id`
mapping) and on a paid pilot. Do not spend the full budget before the pilot
passes.

## Why

Two goals, one dataset:

1. **Strategy backtesting.** Replay a decision rule across three seasons of
   real closing prices with settled outcomes, and report ROI, CLV, Brier and
   drawdown. Without historical prices you can measure whether the model is
   accurate; only prices tell you whether it is accurate *relative to the
   market*, which is where edge lives.
2. **Line lookup.** "What was Jefferson's receptions line in Week 8 2024, and
   did it go over" — the prop hit-rate feature for the public site, plus spread
   and total history per team. Per the editorial line in `CLAUDE.md`, hit-rate
   history is a fact and is publishable.

## Scope

| Class | Billing | 3 seasons |
|---|---|---|
| Featured (`h2h`, `spreads`, `totals`) | `markets × regions × 10` per slate snapshot | ~8–9k credits |
| Offensive props (receptions, rush attempts, reception yards) | `markets × regions × 10` per event-snapshot | ~26k credits |
| + tackles/assists | one more market | +~8k |
| + sacks | one more market | +~8k |

Total ≈ **51k credits** for 5 prop markets plus featured. Closing snapshot only
(T−10min equivalent). The full T−24h/T−3h/T−1h ladder roughly doubles it and is
not needed for a first backtest — the close is the benchmark.

Coverage limit: **player props only exist from 2023-05-03**, so props cover
2023, 2024, 2025. Featured markets go back further and can be extended cheaply.

Featured markets cluster into ~5 kickoff slots per week (Thu, Sun early, Sun
late, Sun night, Mon), so one snapshot per slot covers every game in it.

## Defensive markets — resolved 2026-09-09

Settlement data **exists and is already archived**. `stats_player_week_{season}.parquet`
carries ~150 columns including 20 defensive ones: `def_tackles_solo`,
`def_tackles_with_assist`, `def_tackle_assists`, `def_tackles_for_loss`,
`def_sacks`, `def_sack_yards`, `def_qb_hits`, `def_interceptions`,
`def_pass_defended` and more.

`nfl_player_week` projects only ~26 offensive columns, so the fix is widening
the ingest projection and re-running from `data/raw` — **no re-fetch needed**.
Brief 002 amendment.

**Ingest all 20 defensive columns.** It is free, already archived, and
interceptions are still required to settle anything and as a feature. Ingest
scope and purchase scope are separate decisions.

**Open trap:** three tackle columns exist and they are not interchangeable. The
sportsbook "Tackles + Assists" market is solo plus assists; which nflverse pair
reproduces it must be pinned against a real box score before any settlement
runs. The spread between combinations is 3–4 tackles per player-game — larger
than any line being bet into. Verify once, record the answer in `CLAUDE.md`.

### Which defensive markets to buy — decided 2026-09-09

**Buy: tackles+assists, and sacks. Skip: interceptions.**

- **Tackles** is a volume stat driven by defensive snap share and opponent play
  count — the category the research found forecastable.
- **Sacks** are noisy *as an outcome*, but the process behind them —
  pressure rate — is among the more persistent defensive metrics. Sacks are a
  noisy realization of a stable process. If books price sack props off recent
  sack totals while a model prices off pressure rate and opponent pass-block
  efficiency, that is a structural mismatch rather than a prediction contest,
  which is a better place to hold an edge.
- **Interceptions** fail on both counts: the outcome is noise *and* the process
  behind it (converting a pass defended into a pick) does not persist. Neither
  the realization nor its driver carries signal.

**Caveat on the market priority ranking.** The published ordering (targets .277,
rush attempts .254, receptions .234, receiving yards .145, TDs ~.05) is
out-of-sample R² on counts. R² is the wrong metric for a threshold market: a
0.5-line sack prop needs a calibrated probability that the count is ≥1, not an
accurate point estimate. The R² table systematically understates low-count
threshold markets. Do not use it alone to rule a threshold market out.

**Pressure data:** `ftn_charting_{season}.parquet` is already archived and
carries `was_pressure`. **Unresolved:** whether FTN charting refreshes in-season
or only after the postseason, as participation data does. If in-season, sack
props are live-modelable. If not, pressure is backtest-tier and a live sack
model needs a different pressure source. Resolve before building a live sack
model.

**Free research to run first:** tackle attribution is scored by the home
stadium's official scorer, and solo-versus-assist splits are believed to vary by
crew. Test it on data already on disk — compare each player's solo-tackle rate
home versus away across several seasons. If the effect is real and books price
off raw season averages, that is a structural edge rather than a predictive one.
Costs nothing; run before committing credits.

## Storage

Historical rows land in the **same `quotes` table** as live capture, keyed by
`outcome_id`, distinguished by a source marker (`oddsapi_historical`). One
query surface for backtest and live. Follows invariant 1 — venue specifics stay
in `venues/oddsapi.py`; core sees normalized rows.

Raw responses archive verbatim first, per invariant 2, so any re-derivation
runs from the archive rather than from a second paid pull.

## Pilot before spending

Run **one week of one season** (~16 events, ~500 credits) and confirm:

1. The historical events endpoint returns usable event IDs — and what that
   listing itself costs, which is not yet counted.
2. Props actually populate at the chosen snapshot timestamp rather than
   returning empty. Props post 48–72h before kickoff; a target earlier than
   that bills for nothing.
3. Player names in the response join to nflverse `gsis_id`. The single most
   likely silent failure across 855 games, and the same crosswalk brief 003
   builds.
4. Which books appear. 2023 coverage is thinner than today; if Pinnacle is
   absent the sharp reference for CLV disappears and the benchmark changes.
5. Real per-call cost, read from `x-requests-remaining` rather than estimated.
6. Whether tackles+assists and sacks are quoted at all in 2023, and by which
   books.

Fail any of these and the fix is cheap. Discovering them after 51k credits is
not.

## Open question

Whether the 100k allotment is monthly-recurring or one-time. At 51k this
matters more than it did at 35k. If recurring, also capture the T−24h snapshot
for line-movement history. If one-time, closes only, and consider dropping to
four prop markets.

## Sequencing

1. Brief 003 — `outcome_id` mapping and the `gsis_id` crosswalk.
2. Widen the nflverse projection to all defensive columns; rebuild from the
   archive; pin the tackle definition against a box score.
3. Run the free scorer-bias test.
4. Pilot against the mapping.
5. Spend the remainder in one pass.
6. Strategy replay harness (extends the brief 004 ledger into historical mode).

## Why this reverses an earlier recommendation

The initial call was to skip the backfill and forward-test only, on the grounds
that sportsbook lines are the wrong venue when the intent is to bet on Kalshi.
That objection is weak: Kalshi's NFL props barely predate 2025, so no
historical Kalshi book exists to backfill, and Kalshi arbs against the books —
a model that cannot beat a sharp sportsbook close will not beat Kalshi either.
The sportsbook close is the correct historical benchmark; discount whatever
edge it shows for Kalshi's fee (`0.07·C·P·(1−P)`) and thinner liquidity.