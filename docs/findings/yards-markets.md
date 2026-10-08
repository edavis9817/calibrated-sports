# c-37 — a yardage model against the receiving-yards close

Pre-registration `docs/C37-yards-markets-preregistration.md` (4134f35, before the script
existed; addendum 1 at 08b701b, before any run). Script `research/yards_markets.py`.
One registered run, 2026-10-08: `research/results/yards_markets.log` and `.json`
(aggregates only). 58 intervals declared, 58 computed, 35 exclude zero. Zero credits;
`market_log.db` opened `mode=ro`.

**Scope, in the sentence:** NFL receiving yards, over side, regular season 2023-2025, US
book closes within 15 minutes of kickoff (no Pinnacle), one hurdle-gamma model fitted
walk-forward. **Rushing yards is not compared with any market here** - `outcome_close`
holds no rush-yards rows; the brief's "closes already on disk" is true of receiving
yards only.

## 1. The shape fits; the centre does not, where it matters

Shape fit first, on every played player-game of 2023-2025 (no price involved),
randomised PIT:

| | n | KS (bar 0.03) | PIT <= .10 / .25 / .50 / .75 / .90 / .95 | registered verdict |
|---|---|---|---|---|
| receiving yards, `Y` | 18,031 | 0.0083 | .100 / .243 / .495 / .754 / .907 / .953 | **fits** |
| rush yards (RB), `Y` | 4,761 | 0.0218 | .090 / .234 / .484 / .754 / .913 / .959 | **fits** |
| receiving, shipped ZIG moment-matched `S0` | 18,031 | 0.0300 | q25 .278 outside | does not |
| receiving, lognormal comparator | 18,031 | 0.0409 | q50 .467, q75 .719 outside | does not |
| receiving, Weibull comparator | 18,031 | 0.0128 | all inside | fits |

The fitted gamma shape is about 1.5 for receiving yards and 1.4 for rushing, rising with
the mean. Against naive distributions the model wins on CRPS in both stats: receiving
`Y - N-pos` -1.590 [-1.681, -1.506], `Y - N-own` -0.414 [-0.467, -0.362]; rushing
-2.011 [-2.203, -1.806] and -0.718 [-0.840, -0.603]. The ML shape beats the moment
shape on CRPS for receiving yards (`S1 - S0` -0.044 [-0.064, -0.024]) and not
detectably for rushing (-0.026 [-0.070, +0.018]).

**But on the 10,082 PRICED receiving-yards player-games the same model is too low**:
KS 0.079, PIT <= 0.50 on 42.3% of outcomes, hurdle mass 0.217 forecast against 0.152
realized. A player a book hangs a line on is a selected player - expected to play his
role that day - and a frame fitted on every played game does not know that. The
recalibrated centre `Y` is *further* off there than the unrecalibrated `S1` (PIT <= 0.50
at 45.2%): the recalibration was fitted on the whole frame and moved the wrong way for
the priced subset.

## 2. Registered success condition: NOT MET

On the bench-book rungs (`RB`, 18,666 rungs, 10,016 player-games, 814 games):

    Brier   N-half 0.2500   close 0.2498   Y 0.2666   S1 0.2657   S0 0.2690   N-prior 0.2799
    Y - N-half    +0.0166 [+0.0136, +0.0196]   (worse than a coin)
    Y - N-prior   -0.0133 [-0.0169, -0.0099]   (better than the prior-season hit rate)

Same signs in every season and on the one-claim-per-player-game population `RM`
(+0.0170, -0.0154). **The model beats the prior-season hit rate and loses to a constant
0.5.** By the registered rule that is "does not beat the naive baselines: fails against
N-half".

Why a coin is not naive here, measured post hoc: **99.5% of bench-book rungs close with
a de-vigged price inside [0.45, 0.55]** (sd 0.010). The book puts all of its information
into the LINE and prices it at even money, so "0.5 at the book's line" is the book's
forecast, not a baseline. The model's mean P(over) at those lines is 0.410 against a
realized 0.479, and the Platt slope of the model on the outcome is 0.037: its
disagreement with the line is almost pure error.

## 3. Secondary: the close

Registered primary statistic, `RB` pooled, model minus close:

    dDSC   -0.0002 [-0.0006, +0.0003]   MDE 0.0007  ->  "not distinguishable from the close"
    dAUC   -0.0096 [-0.0231, +0.0045]   MDE 0.0197  ->  "not distinguishable"
    dMCB   +0.0166 [+0.0138, +0.0195]
    Brier  +0.0168 [+0.0137, +0.0197]   (descriptive)

**The registered reading is empty and must not be quoted as parity.** The close's own
DSC on these rungs is 0.0004 and its AUC 0.515: a price that is 0.50 on every rung has
nothing to resolve, so no forecaster can be separated from it on resolution. The
statistic I registered was the wrong one for a market that moves the line - the same
mechanical fact c-33 hit, and I registered around it for the cross-book comparison and
not for the scoring. What the data does show:

- on Brier the model is worse than the close by 0.0168, **99% of it miscalibration**;
- where prices DO vary - `RA`, all books at every quoted line, 53,733 rungs, 58% of
  closes inside [0.45, 0.55] - the model resolves and orders worse: dDSC -0.0071
  [-0.0083, -0.0058] against a close DSC of 0.0145, dAUC -0.0337 [-0.0421, -0.0254];
- on `RM` (modal line, one claim per player-game) dAUC -0.0173 [-0.0331, -0.0019].

So: **the model loses to the receiving-yards close, 2023-2025**, as every market tested
so far has. In-sample isotonic recalibration (optimistic) would bring it to 0.2493
against the close's 0.2498 on `RB` - that is, back to a constant.

## 4. Shape against centre (c-30's lesson)

`RB` pooled, with the centre held fixed the better shape made the forecast **better**,
not worse: `S1 - S0` Brier -0.0033 [-0.0043, -0.0022], all of it MCB (-0.0033), DSC
unchanged. Recalibrating the centre on the frame made it slightly **worse**: `Y - S1`
Brier +0.0009 [+0.0001, +0.0017]. So c-30's amplification did not occur here; what did
is the other half of its lesson - the centre is what stands between this model and the
line, and no shape repairs it. Comparators, not adopted: Weibull -0.0014
[-0.0017, -0.0011] against `Y`, lognormal +0.0088 [+0.0076, +0.0100].

## 5. What this does not establish

- Nothing about rushing yards against a market. Its shape fit says the family is
  usable; that is all.
- Not that yards are a worse target than counts. The Brier gap to the close (+0.0168)
  is smaller than the count model's (+0.0195 to +0.0237, brief 023), but the two models
  differ, the populations differ, and neither clears the line.
- Not that a model given the line would fail. This model never sees the line; one that
  shrinks toward it is a different object and was not built.
- `p_bench` is one book on 12,432 of 18,666 rungs. Playoff games (2,810 rungs) are
  excluded, unlike c-24's P1.
- No cost, limit or execution is modelled anywhere.

## 6. For whoever scores a yards market next

Score the LINE, not the price: the model's median against the book's line, or the
model's P(over) at the line against 0.5. CORP resolution of the price at a book's own
main line is ~0 by construction and separates nobody.
