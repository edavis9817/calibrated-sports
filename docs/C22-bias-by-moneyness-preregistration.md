# C22 pre-registration: does the over bias flip deep in the money?

Unit c-22 (track C), 2026-09-29. Committed **before** `research/bias_by_moneyness.py`
exists and before any realised outcome is read for a population not already published.

## What was seen before this file was written, stated so the test is honest

1. **The R18 population's 0.05 buckets are already published.**
   - `research/results/market_calibration.json` (a-36) carries them, committed on `main`.
   - I read them while locating R18's method.
   - So the **bench** population (R18's own) is **not blind**. Its deep-in-the-money
     answer was visible: 0.60-0.65 reads -2.13 [-5.86, +1.67] on 701, 0.65-0.70 reads
     -7.36 on 68, and 0.70-0.75 holds 1 outcome.
   - It is re-run here because the brief asks for R18's population. It is labelled SEEN
     everywhere it appears and is not the registered test.
2. **The priced-probability distribution, with no outcome attached**, was counted for
   bucket design. Over side, regular season, settled:

       p decile        0.0  0.1   0.2   0.3    0.4    0.5    0.6   0.7   0.8
       bench (DK/FD/MGM) 4  324   810  1,986 15,375 24,929   769     1     0
       all books         5 2,428 9,771 10,447 24,406 35,361 5,758 3,858  543

   - This is the covariate, not the answer, and it is why the registered test is on
     the **all-books** population.
   - The bench close does not reach deep in the money. R18 is a near-the-money result
     by construction, not only on average.

## Populations

- **B (bench, SEEN).** Exactly R18's population:
  - `research.calibration._register_population("bench")`
  - over side, weeks 1-18, 2023-2025, settled, carrying `p_bench`
  - `p_bench` is the median multiplicatively de-vigged close across DK/FD/MGM.
- **A (all books, REGISTERED).** The same, with `price="all"`: `p_all`, the median
  de-vigged close across every book quoting at the closing snapshot.
  - The same filter, the same de-vig (multiplicative, `prob_devig`) and the same
    settlement.
  - This is R18's own `--price all` arm, not a new population.

## Method, identical to R18's

- **Estimate.** Realised over rate minus mean priced over rate, in pp.
- **Interval.**
  - A game-block percentile bootstrap: 2,000 draws, blocks = `outcomes.event_id`.
  - Implemented in numpy with seed 22.
  - The reproduction gate below proves it is the same computation as
    `calibration._block_gap`.
- **Reproduction gate.** Both conditions must hold, or the script refuses:
  - population B must reproduce R18's n (44,198), games (814) and estimate
    (-2.43pp) exactly;
  - its interval must land within 0.15pp of [-3.13, -1.72] on each end (a
    different RNG).

## Buckets, fixed here

- **Buckets** on the over's priced probability `p` (lower edge inclusive):

      [0, .20) [.20, .30) [.30, .40) [.40, .45) [.45, .50) [.50, .55) [.55, .60) [.60, .70) [.70, .80) [.80, 1]

- **Zones** for the stat and season splits. The coarse split exists so the splits do
  not thin into noise.

      longshot over  p < 0.40
      near the money 0.40 <= p < 0.60
      deep ITM over  p >= 0.60

- **Reading rules**, stated before any number exists:
  - **Games floor.** An interval over fewer than 5 games is printed and **not read**
    (house rule, brief 020).
  - **Size flag.** A bucket with n < 100 is flagged thin.
  - **Zero.** A bucket whose interval spans zero is **not a finding**, and the
    report says so in those words.

## The registered tests (population A)

1. **T1: deep in the money.** The gap for zone `p >= 0.60`.
   - The favourite-longshot bias predicts > 0 (the favourite is underpriced).
   - R18's sign predicts < 0.
   - "The sign flips" is claimed only if the interval excludes zero on the
     positive side.
2. **T2: the contrast.** Gap(p >= 0.60) minus gap(0.40 <= p < 0.60), bootstrapped as
   **one** quantity over shared game draws. Two separately published intervals are
   never differenced.
3. **T3: the longshot zone.** The gap for zone `p < 0.40`.
   - The favourite-longshot bias predicts < 0 here: the longshot over is overpriced.
4. **The favourite framing, derived and not separately tested.**
   - An over priced at p is the complement of the under at 1-p, so the over table
     already contains every favourite.
   - For p < 0.5 the favourite is the under, and its gap is minus the over's gap.
   - Reported as a derived column, not as a new test.

**Splits.**

- Each zone crossed with stat (5) and with season (3), on both populations.
- Each is printed with its interval and read by the same rules.
- A zone-level sign that holds in one season only is reported as a fluctuation, not
  a bias.

## The cost line, in the same table

Per bucket, in pp of payout per contract:

- **Book cost of taking a side.**
  - Median, across the same books that price the population, of the closing
    `mid - prob_devig` for that side.
  - `quotes.mid` is the vig-inclusive implied probability.
  - The side taken is the one the bias favours: the over if the gap is > 0, the
    under otherwise.
  - Printed for both sides. The brief's 3.38c is a flat approximation; under
    multiplicative de-vig the cost scales with p, so it is measured, not assumed.
- **Net at a book.**
  - Per outcome: `hit_side - raw_side`.
  - Bootstrapped by game over the bucket.
  - The price is the median book's raw price, not a shopped best price.
- **Exchange fee.** Taker `0.07 p (1-p)` per contract at the side's de-vigged price.
  - Continuous. The per-order ceil-to-cent is at most 1c / C and is ignored at 100+
    contracts.
- **Net at an exchange.** Per outcome: `hit_side - p_side - fee(p_side)`, bootstrapped
  by game.
  - This assumes an exchange quoting at the book's de-vigged close with **zero
    spread**.
  - It is therefore an upper bound on what the exchange pays. No exchange spread is
    measured on this population (Kalshi does not list these book props at these
    lines), and the CLAUDE.md spread wall (median 5c on REC/RSHATT) is quoted as
    context only.

## Intervals registered

- **Buckets.** 10 buckets × 2 populations × (gap, net-book, net-exchange) = 60
  intervals.
- **Zones.** 3 zones × (5 stats + 3 seasons) × 2 populations = 48 gap intervals.
- **Tests.** T1-T3 on A.

With about 110 intervals, about 5 exclude zero at alpha 0.05 by chance alone. Say so in
the findings.

## Out of scope

- Multiples (c-23).
- Any recommendation.
- Shin or power de-vig. Multiplicative de-vig is known to leave longshots looking
  overpriced; a Shin arm would be a different, post-hoc question and is not run here.
