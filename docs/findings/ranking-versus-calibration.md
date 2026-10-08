# Ranking versus calibration: why the model loses on Brier

Unit c-24 (track C), 2026-09-29. Findings only. Nothing publishes from here. A
recalibration map fitted here is a diagnostic, not a release.

- **Scope, stated so it travels with every number.** NFL player props, the over
  side, receptions and rush attempts only. Three populations, each identical to
  the Brier verdict it diagnoses:
  - **P1** is brief 023's walk-forward model against the de-vigged DK/FD/MGM
    **book close** (`p_bench`), 2023-2025 regular season, 16,041 outcomes over
    852 games.
  - **P2** is c-19's weeks 2-3 2026 model against the **Kalshi mid** at
    kickoff − 180 min: 2,513 rungs, 31 games.
  - **P3** is brief 021's week 1 2026, 935 predictions. It is **model only**;
    see §5 for why.
- **Order of work.** The pre-registration
  (`docs/C24-ranking-versus-calibration-preregistration.md`) was committed at
  **`78ef152`** and pushed before `research/ranking_calibration.py` existed.
  Two things ran before that commit, and neither computed a metric:
  - The walk-forward ledger job was started.
  - A provenance check on week 1's quotes was run (§5).
- **Post-hoc material.** Two checks were added after the registered run. They
  are labelled post-hoc in the script, are not among the 49 intervals, and are
  quoted as context only (§4). Adding them left the registered output
  byte-identical (diffed).
- **Reproduce.**
  1. `python -m research.walkforward --ledger-out F`, about 41 minutes.
  2. `python -m research.ranking_calibration --ledger F --cache D:/temp/c19/extract.sqlite --rows R --build-rows`.
  3. The same command without `--build-rows`.
  - `market_log.db` is opened `mode=ro` throughout. The ledger and the cache
    hold per-outcome predictions and Odds API prices, so they stay on local
    scratch and are never committed.
- **Reproduction gate: passed.** Every population reproduces its published
  Brier difference to 4 dp:

  | population | n | Brier diff |
  |---|---:|---:|
  | P1 2023 | 4,785 | +0.0229 |
  | P1 2024 | 5,225 | +0.0237 |
  | P1 2025 | 6,031 | +0.0195 |
  | P2 | 2,513 | +0.0121 |

- **Licence.** Everything below is an aggregate or an interval
  (`BET_LIST_RESTRICTION`).
- **Method.**
  - Intervals are a game-block bootstrap: 2,000 draws, seed 24, percentile.
  - Every difference is **model minus market**.
  - The MDE is 2.8 × SE.
  - The decomposition is CORP (Dimitriadis, Gneiting & Jordan 2021), which uses
    isotonic regression and needs no bins: BS = MCB − DSC + UNC exactly.
  - MCB is miscalibration and DSC is discrimination, i.e. resolution.

## Headline

**The answer depends on the population, and it depends on it sharply.**

- **Against the book close (P1), the loss is mostly calibration.**
  - The model is badly **over-confident**. The Platt slope fitted on earlier
    seasons is **0.10 to 0.11**, so the model's log-odds spread is roughly ten
    times too wide.
  - 79-87% of the Brier gap is MCB.
  - Recalibrating out of sample, walk-forward, cuts the model's Brier by
    ~0.02. That brings it to within detection of the close in 3 of the 4
    registered R1 tests.
  - **But the close hardly beats a constant here either.** On these
    near-coin-flip main lines, the recalibrated model scores within 0.0007 of
    the training base rate. The close beats that base rate by 0.0021 in 2024,
    and in 2025 it does not beat it at all.
  - **Both** forecasters' ordering is weak (AUC 0.53 for the model, 0.57 for
    the market). **The model's is still significantly worse** in every season.
- **Against the Kalshi ladder (P2), the loss is ordering, not calibration.**
  - Both forecasters rank well (AUC 0.81 for the model, 0.83 for the market).
  - 83% of the gap is resolution. dMCB contains zero.
  - Recalibrating does not help.
- **So the brief's framing, "repairable miscalibration or blind", has no
  single answer.** The model is miscalibrated against the close and
  less-discriminating against the exchange. The ordering deficit is present in
  **every** population measured.
- **Nothing here supports "ordering matches the market, so a threshold
  strategy could work."** dAUC excludes zero below in all five registered
  populations.

## 1. Discrimination alone

| population | AUC model | AUC market | **dAUC** | d_rho | d_wAUC (within line) |
|---|---|---|---|---|---|
| P1 2023 | 0.529 | 0.566 | **−0.037 [−0.056, −0.017]** | −0.064 [−0.097, −0.029] | −0.051 [−0.072, −0.028] |
| P1 2024 | 0.519 | 0.577 | **−0.058 [−0.075, −0.041]** | −0.100 [−0.129, −0.071] | −0.056 [−0.078, −0.034] |
| P1 2025 | 0.539 | 0.574 | **−0.035 [−0.055, −0.016]** | −0.061 [−0.094, −0.027] | −0.048 [−0.071, −0.025] |
| P1 pooled | 0.530 | 0.573 | **−0.043 [−0.054, −0.033]** | −0.075 [−0.093, −0.057] | −0.051 [−0.064, −0.038] |
| P2 (Kalshi) | 0.807 | 0.834 | **−0.027 [−0.043, −0.011]** | −0.044 [−0.071, −0.018] | −0.037 [−0.068, −0.004] |

- **The model orders worse than the market in every population, on every
  registered discrimination measure.** That is 15 of 15 intervals, all
  excluding zero below.
- **P1's AUCs sit barely above 0.5 for both forecasters.**
  - The close's own AUC on these lines is 0.57.
  - The model's interval lower bounds are 0.502-0.524, so it carries some
    ordering information, but not much.
  - Book main lines are set near each player's median, and that is what makes
    them hard to order.
- **P2's AUCs are high because a ladder is easy to order.** A 7+ rung rarely
  clears, whoever prices it.
  - The within-line AUC (d_wAUC) removes that part. It drops both forecasters
    to 0.72 and 0.76.
  - The model still trails.
- **Model AUC at the book close lies 0.034-0.058 below the market's, and the
  MDE is ~0.015 pooled.** This is a powered deficit, not noise.

## 2. The Brier decomposition

CORP, in-sample:

| population | BS model | MCB model | DSC model | BS market | MCB market | DSC market | UNC |
|---|---|---|---|---|---|---|---|
| P1 2023 | 0.2695 | 0.0221 | 0.0012 | 0.2466 | 0.0021 | 0.0042 | 0.2487 |
| P1 2024 | 0.2687 | 0.0229 | 0.0008 | 0.2450 | 0.0041 | 0.0057 | 0.2465 |
| P1 2025 | 0.2648 | 0.0214 | 0.0018 | 0.2453 | 0.0050 | 0.0050 | 0.2453 |
| P1 pooled | 0.2675 | 0.0214 | 0.0009 | 0.2456 | 0.0032 | 0.0045 | 0.2469 |
| P2 | 0.1660 | 0.0043 | 0.0614 | 0.1538 | 0.0023 | 0.0715 | 0.2231 |

| population | **dMCB** | **dDSC** | share of dBS: calibration / resolution | registered classification |
|---|---|---|---|---|
| P1 2023 | +0.0200 [+0.0148, +0.0249] | −0.0029 [−0.0050, −0.0012] | 0.87 / 0.13 | both |
| P1 2024 | +0.0188 [+0.0137, +0.0236] | −0.0049 [−0.0070, −0.0037] | 0.79 / 0.21 | both |
| P1 2025 | +0.0164 [+0.0116, +0.0208] | −0.0032 [−0.0053, −0.0014] | 0.84 / 0.16 | both |
| P1 pooled | +0.0182 [+0.0154, +0.0211] | −0.0037 [−0.0047, −0.0028] | 0.83 / 0.17 | both |
| P2 | +0.0020 [−0.0002, +0.0045] | **−0.0101 [−0.0167, −0.0032]** | 0.17 / 0.83 | uninformative relative to the market, calibration not the problem |

- **Against the close, the model is miscalibrated first and less resolving
  second.** Both components exclude zero, which is why the registered label is
  "both". By size, calibration is ~5 times the larger component.
- **Against the exchange, calibration is fine.** The model's MCB of 0.0043 is
  close to the market's 0.0023, and the difference contains zero. **The whole
  loss is resolution.**
- **The registered P2 label reads harsher than the data.** "Uninformative
  relative to the market" is the rule's wording for "resolution below the
  market's, calibration not at fault".
  - The model's P2 DSC is 0.061, against the market's 0.072.
  - In absolute terms that is far from uninformative. It is *less* informative.
- **The binned Murphy decomposition (10 bins) agrees in every row**, with
  residuals ≤ 0.0012. On P1 pooled it gives model REL 0.0206 / RES 0.0007
  against market REL 0.0027 / RES 0.0037.
- **The oracle bound** is an in-sample isotonic model, so it is optimistic by
  construction. It asks whether even perfect monotone recalibration could
  close the gap:

  | population | oracle model Brier − market Brier |
  |---|---:|
  | P1 2023 | +0.0009 |
  | P1 2024 | +0.0008 |
  | P1 2025 | −0.0018 |
  | P2 | +0.0078 |

  - On P2, **no monotone recalibration can close the gap**, even with
    hindsight.
  - On P1, a perfectly recalibrated model would roughly tie the close.
- **Read the CORP level intervals as differences.** The per-forecaster MCB and
  DSC intervals are not the quantity to rely on. Re-running PAV on a
  duplicated-game resample overfits, so on P2 the market's MCB interval
  [+0.0028, +0.0054] does not even contain its 0.0023 estimate. The
  model-minus-market differences share that bias on both sides, so they are
  the quantity to read.

## 3. Recalibrate and re-score, out of sample

**Split rules**, stated in the pre-registration before any number:

- **R1**: fit on the earlier P1 seasons, evaluate on season T (2024 ← 2023;
  2025 ← 2023+2024).
- **R2**: fit on all of P1, evaluate on P2.
- **R3**: fit on P2 week 2, evaluate on P2 week 3.
- **R4**: 2023, two-fold cross-fit by game (the parity of CRC32 of the game id).

No evaluation row shares a game with the rows its map was fitted on. The maps
are Platt (logistic on the logit) and isotonic (PAV, interpolated). The
intervals come from bootstrapping the evaluation games with the map frozen.

| split | map | Brier recal / raw / market | **recal − market** | recal − raw | verdict (registered rule) |
|---|---|---|---|---|---|
| R1 2024 | Platt (a −0.10, b 0.11) | 0.2468 / 0.2687 / 0.2450 | **+0.0018 [−0.0000, +0.0038]** | −0.0219 [−0.0263, −0.0175] | closes the gap to within detection |
| R1 2024 | isotonic | 0.2474 / 0.2687 / 0.2450 | **+0.0024 [+0.0006, +0.0044]** | −0.0212 [−0.0254, −0.0172] | gap survives |
| R1 2025 | Platt (a −0.15, b 0.10) | 0.2450 / 0.2648 / 0.2453 | **−0.0003 [−0.0025, +0.0019]** | −0.0198 [−0.0242, −0.0153] | closes the gap to within detection |
| R1 2025 | isotonic | 0.2460 / 0.2648 / 0.2453 | **+0.0007 [−0.0018, +0.0031]** | −0.0188 [−0.0228, −0.0147] | closes the gap to within detection |
| R2 (P1 → Kalshi) | Platt | 0.2118 / 0.1660 / 0.1538 | +0.0580 [+0.0497, +0.0660] | **+0.0459** (recal makes it WORSE) | gap survives |
| R2 (P1 → Kalshi) | isotonic | 0.2044 / 0.1660 / 0.1538 | +0.0506 [+0.0426, +0.0583] | +0.0385 | gap survives |
| R3 (wk2 → wk3) | Platt (a 0.09, b 0.87) | 0.1683 / 0.1704 / 0.1599 | +0.0084 [−0.0015, +0.0180] | −0.0021 [−0.0046, +0.0003] | closes the gap to within detection |
| R3 (wk2 → wk3) | isotonic | 0.1681 / 0.1704 / 0.1599 | +0.0082 [−0.0019, +0.0181] | −0.0023 [−0.0050, +0.0004] | closes the gap to within detection |
| R4 2023 | Platt | 0.2483 / 0.2695 / 0.2466 | +0.0016 [−0.0005, +0.0039] | −0.0213 [−0.0259, −0.0167] | (secondary) |
| R4 2023 | isotonic | 0.2502 / 0.2695 / 0.2466 | +0.0036 [+0.0008, +0.0065] | −0.0193 [−0.0232, −0.0153] | (secondary) |

- **Against the close, recalibration removes ~90% of the loss.** The remainder
  sits within 0.002 of the market, and the MDE is ~0.003. This is the finding
  with a fix attached, and **§4 says how little the fix is worth.**
- **A fair comparison recalibrates the market too, and then the model loses
  again.** The book close is itself miscalibrated (the over is overpriced,
  CLAUDE.md). Recalibrated model minus recalibrated market is +0.0042, +0.0050,
  +0.0033 and +0.0038 across R1, and +0.0028 and +0.0039 in R4, all excluding
  zero. The model closes the gap only to a market that has *not* been given the
  same repair.
- **R2 is a transport failure, and it was anticipated at registration.** A map
  fitted on book main lines (slope 0.11) flattens a ladder model into near
  uselessness. Calibration is population-specific. **No single recalibration
  serves both venues' line mixes.**
- **R3's "closes the gap" comes from low power, not from recalibration.**
  - On the same 15 week-3 games, the RAW gap is already
    +0.0105 [−0.0002, +0.0214] (post-hoc, §4).
  - Recalibration moved it by 0.0021, and that interval contains zero.
  - The fitted Platt slope is 0.87. On the ladder the model is only slightly
    over-confident, which is consistent with §2.

## 4. Post-hoc context, not registered

These checks were added after the registered run, because two registered
verdicts are easy to over-read without them. They are not among the 49
intervals.

| split | constant (training base rate) Brier | Platt model − constant | market − constant | raw model − market on the same eval rows |
|---|---|---|---|---|
| R1 2024 | 0.2471 | −0.0003 [−0.0009, +0.0003] | −0.0021 [−0.0042, −0.0001] | +0.0237 [+0.0185, +0.0290] |
| R1 2025 | 0.2457 | −0.0007 [−0.0013, −0.0001] | −0.0004 [−0.0026, +0.0018] | +0.0195 [+0.0138, +0.0252] |
| R2 | 0.2348 | −0.0230 | −0.0810 | +0.0121 [+0.0046, +0.0201] |
| R3 | 0.2275 | −0.0592 | −0.0676 | +0.0105 [−0.0002, +0.0214] |

- **On book-close main lines, the recalibrated model is within 0.0007 of a
  constant forecast.** In 2025 the book close does not beat that constant
  either (−0.0004, interval contains zero).
- **So "recalibration closes the gap to the close" means something narrow.**
  It means the model stops paying for over-confidence on a population where
  there is almost nothing to resolve. It does **not** mean the model's ordering
  is worth anything there: its DSC is 0.0008-0.0018.
- **That is the repair, and it is worth close to nothing as a strategy input.**

## 5. Week 1 (brief 021) cannot be re-derived, and what the site serves for it is a different comparison

- **Why it cannot be re-derived.** The registered week-1 comparison (n = 706)
  used the Kalshi mid at the prediction instant, Thursday 09-10 15:03 ET. Those
  live quotes were pruned at 14 days on `ingest_ts` (invariant 8).
- **What `research.score.load()` finds instead.** It now falls back, for **all
  935 rows**, to `backfill:kalshi_candles` rows a median **61,424 s (~17 h)
  before entry**, measured.
- **The site serves that comparison.** It serves `research/calibration.json`,
  generated 2026-09-26T18:28:46Z: model − market **+0.0147 [+0.0009, +0.0268]
  on n = 935**.
  - I fetched it 4 of 4 times, and it equals the local export.
  - That is a comparison against a 17-hour-stale candle, not against the
    registered live mid (+0.0240 on n = 706).
  - The file's `population` text calls it the "common set n=935".
  - Filed below for track A. The exporter is theirs.
- **Model-only figures on week 1** (the model does not depend on the market
  column):
  - AUC 0.775 [0.734, 0.817]
  - within-line AUC 0.658 [0.555, 0.750]
  - MCB 0.0082, DSC 0.0546
  - The same shape as P2: calibration is modest and resolution is substantial.
  - The stale candle's AUC is 0.814. It is descriptive and carries no verdict.

## 6. What this means for c-22 / c-23, and for the register

- **The ordering deficit is real, powered and present in every population.** A
  strategy built on "ordering plus a threshold" inherits a forecaster that
  orders worse than the price it trades against.
  - At the close, the deficit is 0.03-0.06 AUC.
  - On the exchange ladder, it is 0.027 AUC.
  - This input goes to c-22 and c-23, and it cuts against them.
- **"The model loses to the close" overstates how blind the model is, and
  understates how little either side knows.** The register's Brier gap against
  the close (+0.0195 to +0.0237) is ~80% over-confidence. The honest public
  sentence is roughly this:

  > On book main lines the model is badly over-confident; corrected for that,
  > it and the close are both barely better than a coin weighted to the base
  > rate, and the close still ranks outcomes better.

  That sentence is a register decision, and not this unit's to publish.
- **Against the exchange ladder, calibration is not the problem.** The
  exchange simply ranks better.

## 7. What this cannot support

- **Test count.** 49 intervals were registered and 49 were computed. 36
  exclude zero, against ~2.5 expected under a global null. No multiplicity
  correction was applied to the verdicts.
- **Weeks 2-3 are 31 games, and R3 has 15.** R3 cannot distinguish a small
  recalibration gain from none.
- **One walk-forward default variant.** The 12 bracketing variants of brief 023
  were not decomposed. Each loses on Brier, but whether each loses the same
  *way* is not measured.
- **P1 is the book close within 15 minutes of kickoff, main lines.** The open
  is not on disk (brief 023). Whether the model's ordering deficit is smaller
  against an earlier, softer price is untested.
- **The per-forecaster CORP level intervals are biased by resampled-PAV
  overfit** (§2). Read the differences.
- **Staleness.**
  - P2 depends on the c-19 scratch cache. Week 2's live rows start leaving the
    store today (2026-09-29), so a re-run without the cache sees a smaller
    population.
  - The P1 ledger is re-derivable from the store at any time. It takes about
    41 minutes.
- **Nothing here tunes the model.** The Platt and isotonic maps were fitted as
  diagnostics and are not saved anywhere.
