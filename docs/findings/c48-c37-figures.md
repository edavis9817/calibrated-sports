# c-48 — c-37's three post-hoc figures, from a committed script

Pre-registered at `bea5737` (`docs/C48-c37-figures-preregistration.md`) before the script
existed. Script `research/c37_figures.py`, committed at `7aea03e` before its one run; output
`research/results/c37_figures.json` and `.log`. Zero credits, `market_log.db` opened
`mode=ro`. This is a reproduction, not a test: no null, no family.

**Scope of every figure below: NFL receiving-yards props, over side, regular season
2023-2025, the de-vigged DraftKings / FanDuel / BetMGM bench close about 14 minutes before
kickoff, at the book's own line.** Not "props", not rushing yards, not Kalshi.

## Result

All three reproduce to the digits c-37 published, on the population c-37 counted.

| | this run | 95% interval (814 games) | c-37 published | registered reading |
|---|---|---|---|---|
| F1 share of bench closes inside [0.45, 0.55] | 0.99475 | [0.99353, 0.99584] | 0.995 | reproduced |
| F1 sd of the bench close | 0.01012 | [0.00980, 0.01048] | 0.010 | reproduced |
| F2 mean model P(over), arm Y | 0.41003 | [0.40684, 0.41325] | 0.410 | reproduced |
| F2 realised over rate | 0.47927 | [0.46902, 0.48989] | 0.479 | reproduced |
| F3 Platt slope, arm Y | 0.03689 | [-0.04288, +0.11016] | 0.037 | reproduced |

Game-block bootstrap, 2,000 draws, seed 48. `mean(model - outcome)` is -0.0692
[-0.0808, -0.0584]; the Platt intercept is -0.0686.

- **Population: identical to c-37's.** 56,732 rungs read, 2,810 not regular season, 63 void,
  126 not in the frame, 53,733 scored; bench rungs 18,666 / 10,016 player-games / 814 games;
  by books {1: 12,432, 2: 4,998, 3: 1,236}. Every count matches c-37's run log.
- **The scratch file still exists and the regenerated panel is the same panel.**
  `D:/temp/c37/predictions.csv` (written 2026-10-08 07:05Z): 18,666 bench rows on each side,
  0 keys on one side only, largest absolute difference 0.0 in `Y`, `S0`, `S1`, `p_bench` and
  the outcome. The figures of record come from the regenerated panel, not from that file.
- **The Platt slope's interval contains zero.** c-37 published the point value alone. On 814
  games the slope is not distinguishable from zero and is well below 0.5; "almost no
  information" stands, and a positive slope is not established.

## The same share in other markets

Printed by the script on every run, read by key from the unit that measured each, not
recomputed here:

| market | share of bench closes inside [0.45, 0.55] | sd | bench rungs | measured by |
|---|---|---|---|---|
| receiving yards | 0.9947 | 0.0101 | 18,666 | this run, and c-41 (identical) |
| rush attempts | 0.7690 | 0.0419 | 4,581 | c-47 |
| receptions | 0.4013 | 0.0714 | 10,527 | c-47 |

The near-constant close is a property of the receiving-yards market, where the book moves
the line and leaves the price at even. It does not hold for receptions or rush attempts.

## Descriptive (declared before the run, not figures of record)

- F1 by season: 2023 0.9932 (n 5,261), 2024 0.9970 (6,294), 2025 0.9940 (7,111).
- F1 by books in the bench price: one book 0.9924 (sd 0.0110, n 12,432), two 0.9994
  (0.0085, n 4,998), three 1.0000 (0.0062, n 1,236).
- Mean P(over) by arm: S0 0.4513, S1 0.4365, Y 0.4100, Y-lognormal 0.3622, Y-weibull
  0.4194; prior-season hit rate 0.5037; bench close 0.5005. Platt slopes 0.031 to 0.038 on
  all five arms. The published 0.410 is arm Y, as the pre-registration assumed.

## What this does not establish

- **F1 had a committed computation before this unit.** c-41's
  `research/results/residual_given_line.json` already carried 0.994750 and sd 0.010122 for
  this population (seen before the tolerance was written, and said so in the
  pre-registration). What had no committed script was the model level and the Platt slope.
- **The wording in `c-37#1` is not what was measured.** It reads "0.50 +/- 0.01 on 99.5% of
  rungs". The measured statement is: inside [0.45, 0.55] on 99.5% of rungs, with sd 0.010.
  The share inside [0.49, 0.51] was not computed by c-37 or by this unit.
- Nothing about rushing yards (no 2023-2025 closes on disk), playoffs (2,810 rungs
  excluded), or any exchange.
- `p_bench` is one book's price on 12,432 of 18,666 rungs.
- The counts are those of `outcome_close` and the settlement rule as they stood on
  2026-10-08. The script prints every count and reports a moved population by name.
- Reproducing c-37's numbers says c-37's arithmetic was right. It does not re-examine
  c-37's model, its verdict or its registered statistics.
