# C25 — the season model meets a market (pre-registration)

Unit c-25, track C. Written and committed **before** `research/season_vs_market.py`
exists. The script implements this file; it does not extend it. Anything the script
does that is not written here is labelled post hoc in the findings.

## What was read before this file was written (disclosed)

Step 0's census only. It counted rows and read no prices against any model output.
The census ran on 2026-09-30 against `market_log.db` opened `mode=ro`, using a scratch
script. The script will repeat it and print the result.

| family | markets | quoted | two-sided at some instant | depth | trade prints |
|---|---|---|---|---|---|
| `KXNFLWINS` (final wins, "at least k") | 547 | 516 | 468 | 0 | 0 |
| `KXNFLWINSWEEK` (wins in first 4/8/12 weeks) | 728 | 728 | 539 | 0 | 0 |
| `KXNFLWINSTREAK` (any team, k+ in a row) | 9 | 9 | 9 | 0 | 0 |
| division winners (8 × 4) | 32 | 32 | 31 | 0 | 0 |
| `KXNFLAFCCHAMP` / `KXNFLNFCCHAMP` | 32 | 32 | 28 | 0 | 0 |

- **Span.** Every quote is `source='live'`, from 2026-09-16 02:18 UTC to 2026-09-30 03:11
  UTC. Nothing older survives the 14-day retention.
- **No outcomes.** No futures market has a `result`. The first to settle is
  `KXNFLWINSWEEK-26W4`, which closes 2026-10-13.

The census is not thin, so the unit proceeds.

## Fees, read from `/series` on 2026-09-30

`GET /trade-api/v2/series/{ticker}` returned `fee_type`, with `fee_multiplier` 1 on all
of them:

- **Maker-free:** `KXNFLWINS`, `KXNFLWINSWEEK` and `KXNFLWINSTREAK` are `quadratic`.
- **Makers pay:** the eight division series, `KXNFLAFCCHAMP` and `KXNFLNFCCHAMP` are
  `quadratic_with_maker_fees`.

This agrees with `core/fees.SERIES_M_PREFIX` and with the 09-14 `board_019.db` snapshot.
The raw responses are kept with the results.

## The limitation that governs everything below

**A season future settles once per team per season. Nothing here has settled.**

- **No scoring against the exchange tonight.** There is no outcome, so this unit cannot
  score model against exchange with CORP, AUC or any proper score. The brief's Step 1 asks
  for exactly that, and it is not possible before settlement.
- **Where settlement power exists.** Only the model's walk-forward over 2002–2025 has
  settled outcomes. There is no market price on disk for any of those seasons.

So this unit produces four things:

1. the model's own coverage and rung-level calibration at weeks 1–3 (walk-forward, settled);
2. a descriptive disagreement map between model and exchange at three named instants;
3. a **forward shortlist**, frozen with its as-of timestamp and committed, to be scored
   when the rungs settle;
4. the power that January scoring will have, computed now under the model.

## Objects

**The model** is the one the site publishes, reproduced state by state. It is a-41's
margin-of-victory Elo with a-55's per-simulation rating draws.

- **Code and constants.** It is built from `jobs.season_model` and `jobs.season_projection`,
  with the 2026 constants from the grid fitted on 2000–2025 and sigma from CRPS on 2001–2025.
- **Simulations.** 20,000 per state.
  - k = 3 uses the published seed `rng_for("proj-current", 2026)`, so it can reproduce the
    published file.
  - k = 1 and 2 use `rng_for("proj-current", 2026, k)`.
  - The division model follows the same rule with the tag `"cur-model"`.
- **Reproduction check.** At k = 3 the state equals the published one. The script must
  reproduce `projection.json`'s per-team means to within Monte Carlo error before it
  prints anything else, or it refuses.

Model prices per family:

- **`KXNFLWINS-27{T}-k`**: P(final whole wins >= k). A tie is not a win on this rung, so
  the model counts `R == 1` only (it simulates no ties, and 2026 has none so far).
- **`KXNFLWINSWEEK-26W{N}-{T}k`**: P(wins in games of week <= N is >= k).
  - These come from the same simulations' per-game result matrix, which the simulation
    already returns.
  - **Nothing about the simulation or its published output is changed.** It is a second
    query on the same array.
- **`KXNFLWINSTREAK-27-k`**: P(any team's longest in-season winning streak >= k). It is
  computed from the same matrix, with played games fixed. One league-wide claim gives one
  block: **descriptive only**.
- **Division `KXNFL{AFC,NFC}{EAST,..}-27-{T}`**: the a-41 division model as published,
  with fixed ratings, `rng_for("cur-model", 2026)` at k = 3 and 20,000 simulations.
  - Primary: that published object.
  - Secondary, labelled: division probabilities from the sigma-drawn simulations above.
    a-41's fixed ratings are the object the site shows, but a-55 measured them as
    overconfident.
- **Conference champion: not modelled.** The model has no playoff simulation. It is
  excluded and counted.

Team codes are folded Kalshi to nflverse: `JAC→JAX`, `LAR→LA`.

## As-of instants (fixed)

| state k | instant (UTC) | why |
|---|---|---|
| 1 | 2026-09-16 16:00 | after week 1's last game (09-15 00:15 kickoff), before week 2's first (09-18 00:15) |
| 2 | 2026-09-23 16:00 | after week 2's last (09-22 00:15), before week 3's first (09-25 00:15) |
| 3 | 2026-09-29 16:00 | after week 3's last (09-29 00:15), before week 4's first (10-02 00:15) |

The script asserts both conditions from `nfl_games`: every week-k game kicked off at
least 4 h before T_k, and no week-(k+1) game kicked off before T_k.

**The price** is the last `quotes` row per market with `T − 660 s <= ts <= T`. It must be
two-sided, meaning `0 < bid < ask < 1`. `mid = (bid + ask) / 2`, with no de-vig, because
this is an exchange.

**Decided rungs are excluded and counted.** A rung is decided when current wins >= k
(YES) or when current wins plus remaining games < k (NO).

## P0 — census (repeated by the script, printed)

As above, plus the rung ladder per team and the number of two-sided, undecided rungs at
each T_k.

## P1 — the model's own honesty at weeks 1–3 (settled, walk-forward 2002–2025)

This is the gate, reported before anything is compared.

1. **Coverage.**
   - The model reproduces a-55's walk-forward states k = 1, 2, 3 exactly: same RNG tag
     `("proj-walk", year, k, sigma)`, 1,000 simulations, and the per-season sigma a-55
     chose from earlier seasons.
   - The check: its RMSE at each k must equal `projection.json`'s `record.by_week` to
     4 dp, or the script refuses.
   - Then, per k: the realised share of final records inside the central 80% interval
     against the simulated mass inside it, with a season-block bootstrap interval
     (24 blocks, 2,000 draws).
   - **Gate.** If that interval lies wholly below zero at week k ("too narrow"), the week-k
     comparison is **uninterpretable**. It is reported as such, and no shortlist is drawn
     from that week.
2. **Rung-level calibration.** From the same simulations, P(W >= j) for j = 1..17, every
   team, every state k in {1,2,3}, with settled outcomes.
   - **Excluded:** rungs decided at the state, and rungs the model prices at exactly 0 or 1
     from undecided states (these are counted).
   - **Scoring:** c-24's machinery unchanged — Brier, the CORP decomposition (MCB, DSC,
     UNC, exact) and AUC. The bootstrap blocks on **season**, because 32 teams' final
     records in one season are zero-sum.
   - **Reference forecast:** the standings coin-flip, meaning wins so far plus a
     Binomial(remaining, 0.5).
   - **Reported:** Brier(model) − Brier(coin-flip), with its interval and n_blocks.
3. **The same for `WINSWEEK`-shaped claims.** P(wins through week N >= j) for
   N in {4, 8, 12} and states k < N, walk-forward.
4. **Reliability bands.** Bins are deciles of the model's forecast, per family.
   - `WINS` gets a band per state k. `WINSWEEK` bins are pooled over N.
   - Each band is the realised rate with a season-block 95% interval.
   - This interval is **the model's own stated uncertainty** used in P3.
   - For division rungs the band is a-41's published calibration record (10 bins, pooled
     over states, blocked on division-season), read from `division.json`, not recomputed.

## P2 — disagreement map (descriptive; no outcome exists)

Per family, per T_k, over two-sided undecided rungs:

- **Size of the gap:** n, the median and p90 of |p_model − mid|, and the mean signed gap.
- **Ordering:** Spearman between p_model and mid, and the same within a team's ladder.
- **By team:** the signed gap at the rung whose mid is nearest 0.5, and the model's mean
  wins against the market's (the mean is computable only where the ladder is complete and
  two-sided; teams where it is not are counted).
- **Movement, labelled descriptive.**
  - Two slopes, per team, from T_1 → T_2 and T_2 → T_3:
    - Δmarket at the fixed rung, regressed on gap_{k};
    - Δmodel at the same rung, regressed on gap_{k}.
  - Both are bootstrapped over **team** blocks, and n_blocks is stated.
  - **This is not a test of who is right.** Game results move both.

## P3 — the forward shortlist (frozen at T_3)

This is drawn only from a family and week whose P1 gate passed. A rung at T_3 is
**shortlisted** when both of these hold:

- **(a) Disagreement.** `mid` lies outside the model's reliability band for the bin
  containing p_model, meaning the market price is outside the range the realised rate has
  historically taken when the model said this.
- **(b) Cost.** The trade in the model's direction clears its cost **at the band's
  conservative edge**. The taker fee is taken at 100 contracts, per `core/fees`, M = 1:
  - **Buy YES:** `band_lo − ask − fee(ask)/contract > 0`.
  - **Buy NO:** `(1 − band_hi) − (1 − bid) − fee(1 − bid)/contract > 0`.

Rungs meeting (a) but not (b) are listed separately as "disagrees, does not clear cost".

- **No maker arm.** The series is maker-free for `WINS`/`WINSWEEK`, but there is no depth
  snapshot and no trade print for any futures market, so a maker fill cannot be simulated.
  It is **not run**, and the reason is stated.
- **Size unverified.** Size at the touch cannot be checked (no `market_depth` rows), so
  "100 contracts at the touch" is an assumption, labelled as one.
- **Held to settlement.** One fee on entry, no settlement fee, and capital locked until
  the rung closes. The close date is printed per rung.
- **The frozen record.** The shortlist is written to
  `research/registers/c25_forward_calls.json` with:
  - `as_of_ts` = T_3;
  - the quote row's own `ts`;
  - bid, ask, mid, p_model, the band, and the direction;
  - the model state, sigma, constants and the commit;
  - the pre-registered scoring rule below.
- **Where it is not written.** `docs/hypotheses.json` is emitted by a track-A job and
  exported to the site, and nothing is published in this unit. The register file is this
  repo's own, committed and append-only.

## P4 — how the forward calls are scored, and the power they will have

Fixed now, run at settlement. Each run covers only the rungs that have settled by then:
`W4` in October, `W8`/`W12` in November, `WINS` and divisions in January.

- **Shortlist P&L.** Per contract, at the frozen executable price, net of the frozen fee.
  The mean is reported with a team-block bootstrap, and n_blocks is stated.
- **All T_3 rungs.** Brier(model) − Brier(mid), with a team-block bootstrap, per family.
- **Power, computed tonight under the model.**
  - Draw 2,000 complete seasons from the model's own T_3 simulations. Each draw is one
    joint truth for every rung.
  - For each draw, compute Brier(model) − Brier(mid) and the shortlist P&L.
  - The spread of those across draws is the sampling SD that settlement will face.
  - MDE = 2.8 × SD.
  - **This assumes the model is true.** It is the most favourable case for detecting that
    the market is wrong, so it is an **upper bound on power**.
- **The stated retirement condition.** If the settled shortlist P&L interval contains zero
  AND the MDE is larger than the mean edge claimed at T_3, the verdict is **"not
  detectable at n = 32 blocks"**, not "no edge".

## What would change the plan

Nothing is tuned after this commit. If the reproduction checks in the model definition or
in P1 fail, the script refuses. The unit then reports the failure rather than
re-parameterising.
