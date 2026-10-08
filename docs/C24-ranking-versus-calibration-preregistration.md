# C24 — ranking versus calibration: pre-registration

Committed **before** any discrimination, decomposition or recalibration number
is computed. The script is `research/ranking_calibration.py`; it is written
after this file and must implement what is below. Nothing here is edited after
the first run; a correction is appended at the bottom, dated, with the reason.

Unit c-24 (track C), 2026-09-29. Branch `c-24-ranking-versus-calibration`, cut
from `origin/c-19-venue-spread` (`abae7fa`) with `origin/main` (`865d6ba`)
merged in, because population P2 below is c-19's Step 4 set and its
construction lives in `research/venue_spread.py`, which is not on `main`.

## What was done before this file (no metric computed)

- `python -m research.walkforward --ledger-out D:/temp/c24/wf_ledger.csv` was
  STARTED before this file was committed. It writes the default variant's
  per-outcome predictions and prints no score. No row of it is read until this
  file is committed.
- `research.score.load()` was run on week 1 2026 to check reproducibility, and
  only quote provenance was printed: all 935 rows settle, and **every one of the
  935 Kalshi "entry mids" now comes from `backfill:kalshi_candles`, a median
  61,424 s (~17 h) before the prediction instant.** The live week-1 quotes were
  pruned at 14 days on `ingest_ts` (invariant 8). So brief 021's registered
  market comparison (n = 706, live mid at the entry instant) cannot be
  re-derived from the store. No Brier, AUC or calibration figure was computed.

## Populations — each identical to the Brier verdict it diagnoses

- **P1 — walk-forward against the book close** (brief 023 Part 1, the
  register's R15 family). `research/walkforward.py`, default variant, settled
  through `jobs.settle_outcomes` + `core.settlement`; rows with
  `outcome_close.p_bench` non-null (lead <= 15 min). Model = walk-forward
  `p_model`; market = `p_bench` (de-vigged DK/FD/MGM median). Per season 2023,
  2024, 2025, and pooled 2023-2025. **Reproduction check:** the per-season
  Brier(model) - Brier(p_bench) must equal the committed restatement
  (+0.0229 / +0.0237 / +0.0195 on n 4,785 / 5,225 / 6,031) to 4 dp, or P1 is
  reported as NOT the verdict's population and its results are not read.
- **P2 — weeks 2-3 2026 against the Kalshi mid** (c-19 Step 4). Built by
  `research.venue_spread` from the c-19 scratch cache
  (`D:/temp/c19/extract.sqlite`, extracted 2026-09-28, before week 2's live rows
  began to prune) plus `market_log.db` `mode=ro`. Model = walk-forward
  `default`, T = 2026, as-of kickoff; market = Kalshi mid at kickoff - 180 min.
  **Reproduction check:** n = 2,513 and Brier(model) - Brier(mid) = +0.0121 to
  4 dp, or P2 is not read. Secondary P2b: the 468 three-way rows, model and
  Kalshi each against the book `p_bench` at the snapshot nearest entry.
- **P3 — week 1 2026, model only.** The 935 settled `predictions` rows of the
  resolved model version. The market column is the stale candle above, so
  **every market-relative figure on P3 is descriptive and carries no verdict.**
  Model-only figures (AUC, within-line AUC, MCB, DSC) are reported.

## Metrics (model `m` and market `k` on identical rows)

Differences are always **model minus market**.

- **D1 AUC** — Mann-Whitney concordance of p against the settled outcome y,
  ties counted 1/2. dAUC < 0 means the model orders worse.
- **D2 Spearman** rank correlation of p with y (average ranks). d_rho.
- **D3 within-line AUC** — concordance counted only over (over, under) pairs
  sharing the same (stat, line); numerators and denominators summed across
  strata. This removes the trivial part of D1 on a ladder (a 7+ rung rarely
  clears, whoever prices it). d_wAUC.
- **D4 CORP decomposition** (Dimitriadis, Gneiting & Jordan 2021), bin-free:
  p_iso = PAV isotonic regression of y on p (ties pooled), ybar = mean y.
  MCB = BS(p) - BS(p_iso), DSC = BS(ybar) - BS(p_iso), UNC = BS(ybar), and
  BS = MCB - DSC + UNC exactly (asserted to 1e-9). dMCB > 0 = model worse
  calibrated; dDSC < 0 = model worse at resolution. dBS = dMCB - dDSC exactly.
  In-sample PAV flatters DSC for both forecasters; it is read as a difference.
- **D5 binned Murphy decomposition**, 10 equal-width bins: REL, RES, UNC and
  the residual BS - (REL - RES + UNC). Descriptive, no intervals.
- **D6 the oracle bound.** BS(p_iso) of the model — what a PERFECT in-sample
  monotone recalibration would score — against BS(market). Optimistic by
  construction (fitted on the rows it scores); descriptive. If even this is
  worse than the market, no monotone recalibration can close the gap on that
  population.
- **R recalibration, out of sample.** Two maps, each fitted on a TRAIN set and
  applied, frozen, to an EVAL set that shares no game with it:
  - Platt: y ~ logistic(a + b * logit(clip(p))), maximum likelihood.
  - Isotonic: PAV on train; applied by linear interpolation between the
    fitted block centres, flat beyond the ends; output clipped to
    [1e-4, 1 - 1e-4].
  **Split rules, stated before the numbers:**
  - R1 (P1, walk-forward, headline): fit on every P1 season < T, evaluate on
    T, for T = 2024 (train 2023) and T = 2025 (train 2023-2024). 2023 has no
    earlier season and is not evaluated here.
  - R2 (P2, headline): fit on ALL of P1 (2023-2025 book-close rows), evaluate
    on P2. Strictly earlier data, a different venue and a different line mix -
    so a failure here may be transport, which R3 checks.
  - R3 (P2, secondary): fit on week 2, evaluate on week 3.
  - R4 (P1 2023, secondary): two-fold cross-fit by game (fold = parity of
    CRC32 of game_id); each fold is scored by the map fitted on the other.
  Reported per split and map: Brier(recal model) - Brier(market) and
  Brier(recal model) - Brier(raw model), game-block bootstrap on the EVAL rows
  with the map frozen. Secondary, for fairness: the market recalibrated by the
  same split and map, and recal model - recal market.

## Intervals and verdict rules

- Game-block bootstrap, 2,000 draws, seed 24, percentile 2.5 / 97.5. The whole
  statistic (PAV included) is recomputed on each draw. An interval over fewer
  than 5 games is printed and not read.
- **Discrimination verdict** (per population, read on dAUC and dDSC):
  interval excludes zero below -> "model orders WORSE than the market";
  contains zero -> "no difference in ordering detected" (with the MDE,
  2.8 x SE, beside it); excludes zero above -> "model orders BETTER".
- **Classification** of the Brier loss: dMCB lo > 0 and dDSC not below zero ->
  "miscalibrated, not blind"; dDSC hi < 0 and dMCB interval containing zero ->
  "uninformative relative to the market, calibration not the problem";
  both -> "both". Shares dMCB / dBS and -dDSC / dBS are printed as point
  estimates.
- **Recalibration verdict** (R1 per season, R2): Brier(recal) - Brier(market)
  lo > 0 -> "the gap survives recalibration"; contains zero -> "recalibration
  closes the gap to within detection"; hi < 0 -> "recalibrated model beats the
  market" (a candidate, not an edge - it must replicate on an unseen week).
- Test count, declared: 5 discrimination/decomposition differences
  (dAUC, d_rho, d_wAUC, dMCB, dDSC) on each of P1-2023, P1-2024, P1-2025,
  P1-pooled and P2 = 25; P2b dAUC and dDSC for model-book and Kalshi-book = 4;
  recalibration R1 (2 seasons) + R2 + R3 + R4 = 5 splits x 2 maps x 2
  comparisons = 20. **49 intervals.** No multiplicity correction is applied to
  the verdicts; at alpha 0.05 about 2.5 would exclude zero under a global null.

## Rules

- `market_log.db` is opened `mode=ro` only; the c-19 resolver's store
  redirection is reused. The scratch ledger and cache stay on D:/temp and are
  never committed (they hold per-outcome predictions and Odds API prices).
- Output is aggregates and intervals only - no per-bet series, no equity
  curve (`BET_LIST_RESTRICTION`). Nothing publishes from this unit; a map
  fitted here is a diagnostic, not a release.
- Findings: `docs/findings/ranking-versus-calibration.md`.
