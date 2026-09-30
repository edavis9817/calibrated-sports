# c-25 — the season model meets a market for the first time

- **Pre-registration:** `docs/C25-season-model-meets-market-preregistration.md`, committed
  and pushed at `ecb3048` before the script existed.
- **Script:** `research/season_vs_market.py`.
- **Aggregates:** `research/results/season_vs_market.{json,log}`.
- **Frozen forward calls:** `research/registers/c25_forward_calls.json`.
- **Quote rows used:** `research/results/c25_quotes_at_T.json`, preserved because retention
  deletes them.
- **Fees:** the raw `/series` responses are in `research/results/c25_series_raw.json`.

## The limitation that leads

**Nothing in this comparison has settled.** A season future settles once per team per
season, and the first of these rungs (`KXNFLWINSWEEK-26W4`) closes 2026-10-13.

- **No score is possible yet.** No figure below scores the model against the exchange,
  because there is no truth to score either against. The brief's Step 1 asked for exactly
  that, and it cannot be done before settlement.
- **Where the settled evidence is.** It is the model's own walk-forward over 2002–2025, and
  there is no market price on disk for any of those seasons.
- **Everything market-side is descriptive:** a disagreement map, plus a forward list frozen
  so it can be scored later.
- **Scope of every market figure:**
  - Venue: Kalshi.
  - Series: `KXNFLWINS`, `KXNFLWINSWEEK`, the eight division series and `KXNFLWINSTREAK`.
  - Season: NFL 2026.
  - Instants: 2026-09-16, 09-23 and 09-29, each at 16:00 UTC.
  - Model: a-41/a-55's season model reproduced at states after weeks 1, 2 and 3.

## P0 — census: not thin

| family | markets | quoted | two-sided at some instant | depth | trade prints |
|---|---|---|---|---|---|
| `KXNFLWINS` | 547 | 516 | 468 | 0 | 0 |
| `KXNFLWINSWEEK` | 728 | 728 | 539 | 0 | 0 |
| division winners | 32 | 32 | 31 | 0 | 0 |
| `KXNFLWINSTREAK` | 9 | 9 | 9 | 0 | 0 |
| conference champion | 32 | 32 | 28 | 0 | 0 |

- **Span.** The quotes run 2026-09-16 02:18 to 2026-09-30 03:21 UTC, all `source='live'`.
  That is exactly the 14-day retention window, so **no futures price older than 14 days
  exists or ever will**.
- **No book, no tape.** There is no `market_depth` row and no `market_trades` print for any
  futures market.
- **Stale by construction.** Quote row counts in the log move between runs, because the
  logger is live.

## Fees, from `/series` (2026-09-30), not the PDF

| series | `fee_type` | maker pays? |
|---|---|---|
| `KXNFLWINS`, `KXNFLWINSWEEK`, `KXNFLWINSTREAK` | `quadratic` | no |
| eight division series, `KXNFLAFCCHAMP`, `KXNFLNFCCHAMP` | `quadratic_with_maker_fees` | yes |

`fee_multiplier` is 1 on all of them. This agrees with `core/fees.SERIES_M_PREFIX`.

- **Why this market's cost side is unusually good.** Each rung is held to settlement: one
  taker fee on entry, no settlement fee, and it settles once. At the typical prices here the
  fee is 1.1–1.8c per contract at 100 contracts.
- **The maker arm was not run.** WINS is maker-free, so a maker arm would be allowed. But
  with no depth snapshot and no trade print, there is no queue and no tape to simulate a
  fill against.

## P1 — the model's own honesty at weeks 1–3 (settled, walk-forward 2002–2025)

**Reproduction first.**

- The script reproduces a-55's published walk-forward exactly: RMSE 2.6943, 2.5473 and
  2.3783 after weeks 1, 2 and 3, and 80% coverage 0.8438, 0.8581 and 0.8698, all to 4 dp.
- The fitted constants equal the published ones: k 20, hfa 50, regress 0.5, sigma 100.

**Coverage gate: passes at every week.**

| after week | realised in central 80% | simulated mass | diff (season-block 95%) | n_blocks | verdict |
|---|---|---|---|---|---|
| 1 | 0.8438 | 0.8619 | −0.0181 [−0.0412, +0.0041] | 24 | within |
| 2 | 0.8581 | 0.8638 | −0.0058 [−0.0310, +0.0189] | 24 | within |
| 3 | 0.8698 | 0.8645 | +0.0053 [−0.0185, +0.0298] | 24 | within |

- **The week-3 concern does not bite.** The brief worried that fixed ratings would
  understate uncertainty with 13 games left. The published projection draws ratings, and at
  week 3 its intervals cover.
- **The interval's nominal size.** The "central 80%" interval of a discrete win count holds
  about 86% of the simulated mass. That mass is the comparison, not 0.80.

**Rung-level calibration of the model** (whole wins, the Kalshi-shaped claim, c-24's CORP):

| rungs | n | seasons | Brier | MCB | DSC | UNC | model − coin-flip Brier | AUC model / coin-flip |
|---|---|---|---|---|---|---|---|---|
| WINS after wk 1 | 11,033 | 24 | 0.1064 | 0.0008 | 0.1443 | 0.2500 | −0.0170 [−0.0198, −0.0142] | 0.9305 / 0.9151 |
| WINS after wk 2 | 10,337 | 24 | 0.1068 | 0.0011 | 0.1443 | 0.2500 | −0.0187 [−0.0217, −0.0158] | 0.9298 / 0.9121 |
| WINS after wk 3 | 9,698 | 24 | 0.1062 | 0.0010 | 0.1447 | 0.2500 | −0.0196 [−0.0232, −0.0161] | 0.9308 / 0.9109 |
| WINSWEEK (N 4/8/12) | 37,582 | 24 | 0.1272 | 0.0004 | 0.1231 | 0.2500 | −0.0145 [−0.0177, −0.0113] | |

- **Rules applied:**
  - Rungs already decided at the state are excluded.
  - Rungs the model prices at exactly 0 or 1 from an undecided state are excluded: 1,692
    WINS and 200 WINSWEEK.
  - AUC is high for both forecasts because thresholds far from a team's record are easy. It
    does not discriminate here; the Brier difference does.
- **Reading.** The model's miscalibration is about 0.001 of Brier. It beats the standings
  coin-flip on every family, and every interval excludes zero with 24 season blocks.
- **Contrast with c-24.** This is the opposite of c-24's prop model, whose loss was 79–87%
  miscalibration.

## P2 — disagreement map (descriptive; no outcome exists)

The pool is two-sided, undecided rungs at T_k. The signed gap is model − mid. Intervals
are team-block bootstraps.

| family | k | usable | \|gap\| median / p90 | signed mean gap | Spearman(model, mid) | median spread |
|---|---|---|---|---|---|---|
| WINS | 1 | 314 | 0.074 / 0.228 | −0.008 [−0.048, +0.035] (32) | 0.915 | 4c |
| WINS | 2 | 297 | 0.060 / 0.188 | +0.002 [−0.032, +0.041] (32) | 0.932 | 4c |
| WINS | 3 | 320 | 0.065 / 0.181 | −0.000 [−0.034, +0.036] (32) | 0.945 | 1c |
| WINSWEEK | 1 | 492 | 0.060 / 0.232 | +0.005 [−0.028, +0.041] (32) | 0.908 | 10c |
| WINSWEEK | 2 | 160 | 0.093 / 0.329 | −0.027 [−0.068, +0.014] (32) | 0.701 | 10c |
| WINSWEEK | 3 | 363 | 0.058 / 0.164 | −0.005 [−0.031, +0.024] (32) | 0.950 | 10c |
| division | 1 | 30 | 0.062 / 0.167 | −0.006 [−0.041, +0.032] (30) | 0.799 | 1c |
| division | 2 | 31 | 0.089 / 0.214 | −0.003 [−0.044, +0.040] (31) | 0.858 | 1c |
| division | 3 | 31 | 0.081 / 0.229 | −0.000 [−0.046, +0.044] (31) | 0.817 | 1c |

- **No aggregate lean.** The model and the exchange agree on the ORDER of rungs (Spearman
  0.92–0.95 on WINS) and show no net lean in any family.
- **Rung by rung, the gap is not small.** The median absolute gap is 6–9pp, and the p90 is
  16–33pp. On the division series (a 1c market) the median gap is 6–9pp.
- **Ladder means could not be computed.** The "market mean wins" in the pre-registration
  needs a complete two-sided 17-rung ladder. **0 of 32 teams have one**, because the tails
  are unquoted.
- **Movement (descriptive, n 64, 32 team blocks).**
  - Δmarket on gap: +0.108 [−0.024, +0.285].
  - Δmodel on gap: −0.025 [−0.166, +0.134].
  - Neither excludes zero. **This is not a test of who is right.**
- **WINSTREAK is descriptive.** It is one league-wide claim, so one block: 6–7 usable rungs,
  and spreads of 21–28c. The one "decided" rung is 5+, which the model prices at 1.000. It
  has not actually settled; that is a labelling limitation of the script.

### POST HOC — the model is compressed relative to the market

This analysis was not pre-registered. It was added after the first run showed the
shortlist's direction splitting by team strength.

For each team, the win count at which the ladder crosses 0.5 was computed for the model and
for the market:

| k | teams | SD across teams, model / market | slope of model on market (team block) | \|gap\| median / max |
|---|---|---|---|---|
| 1 | 32 | 1.56 / 2.23 wins | 0.588 [0.461, 0.753] | 0.73 / 3.10 |
| 2 | 32 | 1.86 / 2.41 | 0.686 [0.554, 0.839] | 0.73 / 2.91 |
| 3 | 32 | 2.03 / 2.34 | 0.764 [0.628, 0.915] | 0.89 / 2.34 |

- **The market spreads teams further apart than the model does**, and the slope's interval
  excludes 1 at every week. The gap narrows as the season goes on.
- **Worked example: Miami (0-3).** The mids imply about 3.1 wins. The model gives 5.0.
- **Worked example: Cleveland.** The mids imply about 6.5 wins. The model gives 8.7.
- **The likely reason, not established.** The model reads scores alone and regresses every
  rating halfway to 1500 each preseason. The market prices rosters, quarterbacks and
  preseason information the model cannot see.
- **Both are consistent with P1.** Calibrated in aggregate is not the same as sharp, and
  P1 cannot say which view is right about **these** teams.
- **Only settlement separates them:** either the market knows something, or it is
  overconfident.

## P3 — the forward shortlist, frozen at 2026-09-29 16:00 UTC

- **Considered:** 714 two-sided, undecided rungs (WINS 320, WINSWEEK 363, division 31).
- **Shortlisted (pre-registered (a) and (b)):** 414 rungs, on all 32 teams. By family:
  WINS 234, WINSWEEK 158, division 22.
- **Disagrees but does not clear cost:** 182.

**The pre-registered filter did not filter, and that is itself the finding.**

- **Why it passes almost everything.** The walk-forward reliability bands are narrow
  (24 seasons, ~10,000 rungs per state). A 5pp disagreement lands outside them, and a
  1–2c fee does not stop it.
- **So the shortlist is effectively "every rung where the model and the exchange disagree
  by more than ~5pp".** It is not a selective list of strong calls.
- **Correlated, not independent.** The 414 rungs are 32 teams' ladders. The median gap is
  10.5pp.
- **Mostly a team-level view.** 16 teams carry calls in one direction only, which makes
  each of them effectively one bet on that team's strength.
- **Headed by the compression pattern.** The top of the list is Miami, Cleveland, the
  Rams, Baltimore and Dallas, which is the POST HOC finding above.
- **Assumptions, stated.** Size at the touch is unverified (no depth rows), so 100 contracts
  at the touch is an assumption. The maker arm is not run.
- **Capital lock.** WINS rungs close 2027-01-18. WINSWEEK W12 rungs close 2026-12-08, and
  W4/W8 earlier. Division rungs close 2027-01-25.

## P4 — the power January will have, under the model (an upper bound)

2,000 complete seasons were drawn from the model's own week-3 simulations. Each draw is
one joint truth for every rung.

| family | rungs | teams | E[Brier model − mid] | SD | MDE (2.8 SD) | model better in |
|---|---|---|---|---|---|---|
| WINS | 320 | 32 | −0.0128 | 0.0111 | 0.0310 | 88.0% of drawn seasons |
| WINSWEEK | 363 | 32 | −0.0107 | 0.0101 | 0.0283 | 85.5% |
| division | 31 | 31 | −0.0072 | 0.0247 | 0.0691 | 62.6% |
| shortlist P&L / contract | 414 | 32 | +0.0772 | 0.0550 | 0.1541 | positive in 92.5% |

**Even if the model were exactly right, one season could not show it.**

- **WINS:** the expected advantage is about 1.2 SD. The MDE of 0.031 Brier is 2.4× the
  effect the model itself predicts.
- **Shortlist P&L:** about 1.4 SD.
- **The rule this implies:** if the settled intervals contain zero in January, the verdict
  is **"not detectable at 32 team blocks"**, not "no edge". That rule was pre-registered.
- **The upper-bound assumption.** Every figure here assumes the model is the truth. If the
  market is the better forecaster, the same arithmetic runs against the model.

## What the brief had right and wrong

- **Right: "The prices are in the store. Nobody has looked."** It holds for 14 days of
  prices, and 714 rungs were two-sided and undecided at T_3.
- **Wrong: "Score the model against the exchange mid ... using exactly c-24's machinery".**
  It cannot be done: nothing has settled. c-24's machinery was used where settlement exists
  (P1, the model's walk-forward) and to compute power (P4).
- **Wrong: "use per-simulation rating draws ... at week 4 there are 13 games left".** The
  as-of state tonight is after week 3 (14 games left). Week 4 starts 2026-10-02.
- **Wrong: "b-88 drew a mean cumulative path, so paths may already be retained".**
  - They are not needed from the output. `models.season.simulate` already returns the
    per-game result matrix.
  - WINSWEEK and WINSTREAK are second queries on it, computed inside the research script.
  - The simulation and the published file are unchanged.
- **Incomplete: "`KXNFLAFC*` and `KXNFLNFC*` are conference futures".** They are two
  families:
  - the eight division series, which the division model prices directly (included);
  - `KXNFLAFCCHAMP`/`KXNFLNFCCHAMP`, which need a playoff simulation that does not exist
    (excluded, 32 markets).
- **The brief's premise that the model "measurably beats its baselines" holds** on the
  Kalshi-shaped rung claim as well. The whole-wins rungs beat the coin-flip at every state,
  and the model is calibrated to MCB ≈ 0.001.

## Two things found on the way

1. **The published `division.json` is not reproducible by its own code on the current
   store.**
   - `J.current()` on main, with identical ratings (to 0.1) and identical games, differs
     from the served file by up to 0.0033 (NYG).
   - That is within Monte Carlo error: the largest z is 0.82 against √2 × mc_se.
   - So it is a different random stream, for a reason not determined. It is not the
     nflverse data version (tested) and not a code difference between prod and main (same
     commits).
   - The script's check was changed after the first run, from "exact to 4 dp" to "within
     3·√2·mc_se". That change is disclosed at the check itself.
   - This belongs to track A.
2. **Retention makes season futures un-re-derivable.**
   - `prune_quotes` deletes `source='live'` quotes 14 days after ingestion. For a market
     that lives five months and settles once, that means the store holds a sliding 14-day
     window and never the path.
   - T_1's prices behind this report leave the store on about 2026-09-30. That is why the
     exact rows read are preserved in `research/results/c25_quotes_at_T.json`.
   - The recommendation (to track A and Ethan) is a downsampled hold for
     `market_type='future'`. At roughly one row per market per hour that is ~1,300 markets ×
     24 × 150 days ≈ 4.7M rows a season, against ~4.5M rows per 14 days kept today.
