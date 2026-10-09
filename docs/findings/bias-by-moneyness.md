# Does the over bias flip deep in the money?

Unit c-22 (track C), 2026-09-29. Findings only; nothing publishes from here.

- **Scope, so it travels with every number.**
  - Settled NFL player props, over side, regular season (weeks 1-18), 2023-2025.
  - Priced at the multiplicatively de-vigged close from the Odds API historical
    backfill.
  - Sportsbooks only. No exchange price is observed anywhere in this study.
- **Pre-registration.**
  - `docs/C22-bias-by-moneyness-preregistration.md`, committed and pushed at
    **`93b9d37`** before `research/bias_by_moneyness.py` existed.
  - The bench population's buckets were **already published** (a-36,
    `research/results/market_calibration.json`) and I read them first. So the bench
    table below is **SEEN, not a test**. The registered tests are on the all-books
    population.
- **Reproduce.** `LOGGER_DB=<market_log.db> python -m research.bias_by_moneyness --out F`,
  which takes about 30 s.
  - `market_log.db` is opened `mode=ro` only.
  - Aggregates are committed at `research/results/bias_by_moneyness.json`.
- **Reproduction gate: passed.**
  - The bench population reproduces R18's n (44,198), games (814) and estimate
    (-2.43pp) exactly.
  - The interval is -2.43 [-3.11, -1.77] against R18's [-3.13, -1.72]. It is the same
    game-block percentile bootstrap (2,000 draws) with a different RNG.
- **Method.**
  - Gap = realised over rate minus mean priced over rate, in pp. Negative means the
    over is overpriced.
  - Every interval is a game-block bootstrap, and each slice is drawn independently.
  - **T2 is one quantity over shared draws**, never two intervals differenced.

## Headline

**No. The over bias does not flip deep in the money, and it does not deepen there
either. It is a near-the-money phenomenon that fades toward both tails.**

On the registered population (A, every book, n 92,577, 814 games):

| test | slice | gap pp [95%] | reading |
|---|---|---|---|
| T1 | deep ITM over, p ≥ 0.60 (n 10,159, 725 games) | −0.41 [−1.81, +0.93] | spans zero: **not a finding**. No flip. |
| T3 | longshot over, p < 0.40 (n 22,651, 807 games) | −0.67 [−1.64, +0.31] | spans zero: **not a finding** |
| — | near the money, 0.40-0.60 (n 59,767) | −2.29 [−3.06, −1.50] | excludes zero, negative (R18's bias) |
| T2 | gap(deep) − gap(near), one quantity | **+1.88 [+0.72, +2.99]** | **excludes zero** |

- **What the tests say.**
  - T2 is the one registered test that excludes zero. It says the deep in-the-money
    over is **less** overpriced than the near-the-money over.
  - T1 says it is not *under*priced. The interval centres near zero and its upper
    end is +0.93pp.
- **What the favourite-longshot bias predicts, and what happened.**
  - It predicts favourites underpriced and longshots overpriced.
  - Here neither tail departs from fair.
  - The brief's hypothesis — the deep ITM over is **cheap** — is not supported. It
    is priced about right.
  - A book-side FLB of about 1pp cannot be excluded: T1's interval reaches +0.93.

## R18's own population is near the money by construction, not only on average

The bench close is the median of DK/FD/MGM. It barely reaches the tails:

- **Of 44,198 outcomes:**
  - 770 (1.7%) are priced ≥ 0.60;
  - 1 is priced ≥ 0.70;
  - 0 are priced ≥ 0.80.
- **The mean priced probability of 0.4880 is not an average over a wide range.**
  91% of the population sits in 0.40-0.60.
- **Bench deep ITM (SEEN): −2.55 [−6.22, +1.01]** on 770 outcomes. It spans zero, and
  it is too wide to separate from either the near-the-money −2.40 or zero.
- **Bench T2: −0.16 [−3.82, +3.49].** The bench population cannot answer the
  question; it is too thin in the tail.

## Who prices the deep tail — post-hoc, descriptive, not registered

`D:/temp/c22/posthoc_books.txt`. For population A's p ≥ 0.60 zone:

- **Only 838 of the 10,159 outcomes carry a bench price.**
- **Book-quotes.**
  - Kambi books: `betrivers` 9,511 and `unibet_us` 4,381.
  - No other book exceeds 542. Kambi is 13,892 of 16,354 book-quotes (85%), and **9,184 of 10,159 outcomes (90%) were priced by Kambi books and nobody else.**
- **Single book:** 5,312 of 10,159 (52%) were priced by one book alone.
- **Seasons:** 4,541 in 2023, 5,240 in 2024 and **378 in 2025**. The deep tail
  thins out in 2025.

**So T1 is, in practice, a statement about Kambi's (BetRivers/Unibet)
over lines in 2023-2024.** It is not about "the market". Read it that way.

## Buckets, population A (registered), with the cost line in the same row

- **Columns.**
  - `c.ovr` and `c.und` are the median book cost of taking that side: raw implied
    price minus de-vigged price, in pp.
  - `net @book` is realised minus the raw price paid, on the side the gap favours.
    Here that is always the under, except where the gap is positive. It uses the
    median book's price, not a shopped best price.
  - `fee` is the Kalshi taker fee `7·p·(1−p)` c on the favoured side.
  - `net @exch` assumes an exchange at the book's de-vigged close with **zero
    spread**, so it is an upper bound.

| bucket | n | games | gap pp [95%] | reading | c.ovr | c.und | net @book | fee | net @exch (0 spread) |
|---|---:|---:|---|---|---:|---:|---|---:|---|
| 0.00-0.20 | 2,433 | 552 | −0.94 [−2.50, +0.76] | spans zero, not a finding | 1.30 | 6.00 | −4.89 [−6.59, −3.30] | 1.02 | −0.08 [−1.68, +1.54] |
| 0.20-0.30 | 9,771 | 790 | −0.05 [−1.18, +1.08] | spans zero, not a finding | 1.89 | 5.57 | −5.40 [−6.54, −4.35] | 1.33 | −1.28 [−2.44, −0.12] |
| 0.30-0.40 | 10,447 | 805 | −1.17 [−2.32, −0.04] | excludes zero, negative | 2.50 | 4.52 | −3.39 [−4.60, −2.19] | 1.59 | −0.42 [−1.54, +0.70] |
| 0.40-0.45 | 8,287 | 811 | −2.23 [−3.31, −1.15] | excludes zero, negative | 2.86 | 3.79 | −1.60 [−2.70, −0.58] | 1.71 | +0.52 [−0.59, +1.60] |
| 0.45-0.50 | 16,119 | 813 | −3.15 [−4.13, −2.25] | excludes zero, negative | 3.25 | 3.46 | −0.31 [−1.25, +0.67] | 1.75 | +1.41 [+0.41, +2.37] |
| 0.50-0.55 | 28,477 | 814 | −1.78 [−2.73, −0.82] | excludes zero, negative | 3.45 | 3.26 | −1.42 [−2.27, −0.46] | 1.75 | +0.03 [−0.87, +0.93] |
| 0.55-0.60 | 6,884 | 807 | −2.50 [−3.73, −1.28] | excludes zero, negative | 3.80 | 2.87 | −0.38 [−1.64, +0.89] | 1.71 | +0.78 [−0.49, +2.00] |
| 0.60-0.70 | 5,758 | 724 | −0.74 [−2.38, +0.70] | spans zero, not a finding | 4.62 | 2.51 | −1.78 [−3.18, −0.27] | 1.59 | −0.85 [−2.37, +0.73] |
| 0.70-0.80 | 3,858 | 531 | +0.14 [−1.58, +1.87] | spans zero, not a finding | 5.59 | 1.97 | −5.48 [−7.18, −3.83] | 1.34 | −1.20 [−2.82, +0.42] |
| 0.80-1.00 | 543 | 205 | −0.74 [−4.54, +2.77] | spans zero, not a finding | 6.22 | 1.37 | −0.63 [−4.18, +2.80] | 1.04 | −0.30 [−3.99, +3.37] |

**Where the sign changes.** Only in 0.70-0.80, at +0.14, and that interval spans zero.
**No bucket in the tails has an interval excluding zero in either direction.**

- **The cost line contradicts the brief's flat 3.38c.**
  - Under multiplicative de-vig the vig on a side scales with its price.
  - Taking the deep ITM over costs **4.6-6.2c** at a book, not 3.38c.
  - The book is most expensive exactly where the brief hoped the over was cheap.
  - The favourite side of a 0.2 over (the under at 0.8) costs 5.6-6.0c.
- **The exchange fee does fall away at the tails, as the brief said.** 1.0c at 0.80+,
  against 1.75c at the money. But there is no measured tail bias for it to reveal.
- **Net at a book, favoured side.**
  - It excludes zero on the **negative** side in 7 of 10 buckets.
  - It excludes zero positive in **none**.
- **Net at an exchange at zero spread.**
  - Excludes zero positive in one bucket: 0.45-0.50, +1.41 [+0.41, +2.37].
  - This is the favoured side chosen by the same data, so it is optimistic.
  - It excludes an exchange spread. CLAUDE.md measured a median of 5c on Kalshi
    REC/RSHATT, which a half-spread of 2.5c would erase.
  - Kalshi lists none of these book lines. **Not a finding about anything
    tradeable.**

## Splits (population A; bench in `research/results/bias_by_moneyness.json`)

**Deep ITM (p ≥ 0.60), by stat.**

- receiving_yards: −0.05 [−1.78, +1.66] (n 5,957).
- receptions: −0.95 [−2.55, +0.58] (n 4,040).
- Both span zero: **not findings**.
- rush_attempts (44), sacks (63) and tackles_assists (55) are THIN.
  - tackles_assists reads +13.54 [+1.59, +24.30], excluding zero positive, on 55
    outcomes.
  - That is **one of 111 intervals printed, with ~5–6 expected to exclude zero by
    chance.** It is not read as a finding.
  - It is the only positive-excluding deep ITM cell in either population.

**Deep ITM, by season.**

- 2023: −0.04 [−1.96, +1.78].
- 2024: −0.58 [−2.69, +1.39].
- 2025: −2.48 [−7.79, +2.63], on n 378.
- All three span zero, and **no season shows a flip.**

**Near the money, by season.** −0.65 [−1.95, +0.66] in 2023, −2.29 in 2024 and −3.89
in 2025.

- 2023 spans zero, and the bias grows each season.
- This matches the earlier CLAUDE.md record (2023 not significant, growing
  thereafter).
- **Pooled R18 is carried by 2024-2025.** That is not new, but it bears on c-23.

**Near the money, by stat.** Every stat excludes zero negative. Sacks is the largest:
−5.38 [−8.38, −2.34] on A and −6.40 on bench. That agrees with the earlier −7.2pp
sacks record.

- Sacks is the one cell where the favoured-side net at a zero-spread exchange
  excludes zero: +3.65 [+0.80, +6.74].
- Whether any exchange lists sacks was **not checked** in this unit. The side was chosen by the data, and no spread is included.

**Longshot zone.**

- receptions: −1.40 [−2.63, −0.17].
- 2025: −2.56 [−4.35, −0.74].
- Everything else there spans zero.

## What this does not establish

- **Not the market's tail.** Deep ITM on A is 52% single-book, and 90% of its
  outcomes are priced by Kambi books alone. On bench, the tail barely exists.
- **Not de-vig-robust.**
  - Multiplicative de-vig loads the margin in proportion to price.
  - Shin would move longshot and favourite prices in exactly the direction the
    favourite-longshot bias concerns.
  - A Shin arm was declared out of scope in the pre-registration and was not run.
    T1's "fair" could move under Shin.
- **Not exchange pricing.** Every exchange column is a hypothetical: a Kalshi price
  equal to the book's de-vigged close, at zero spread.
- **Not multiples (c-23).**
- **Not a recommendation.**
