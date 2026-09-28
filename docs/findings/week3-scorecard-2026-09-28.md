# Week 3 scorecard, 2026 — the Board's published leans (c-17)

**Every headline interval spans zero. One week of 176 graded leans cannot tell a model
that beats the close from one that trails it by 3pp. The result is consistent with the
standing verdict (the model loses to the book close), and it is also consistent with a
range of other verdicts, including a model no different from the close.**

Scope, carried with every number below: NFL 2026 week 3; the Board's published leans
under `baseline-usage-0.4+03571d4710af` (the LIVE fit, not the walk-forward refit that
produced the register); receptions and rush attempts; benchmark books DraftKings,
FanDuel and BetMGM from the Odds API forward capture. Read
`read-2026-09-28T224451Z.json` plus `board/nfl/ledger.csv` (380 rows) from the production
Board tree. The 16 leans on PHI@CHI (Monday night) were upcoming at the read and are
**not in any figure**.

Script `research/c17_week3_scorecard.py`; output
`research/results/c17_week3_scorecard.txt`. Intervals are 2,000-draw game block
bootstraps (`research.sweep.common.boot`, seed 22) over **14 games**. Any interval over
fewer than 5 games is printed and not read.

## Q1 — hit rate against the break-even of the published price

| | value |
|---|---|
| leans cleared | 95 of 176 = 0.540, game bootstrap [0.479, 0.605] |
| mean break-even of the published price (vig-inclusive) | 0.534 (n = 170; 6 leans carry no price) |
| **hit − break-even, bootstrapped as one quantity** | **+0.76pp [−5.96, +7.39]** — spans zero |

The published price is the median benchmark-book American price on the lean side at
publication. It is a quoted price, not a fill.

## Q2 — Brier(model) − Brier(close), register method

Over side, y = actual > line, pushes out, game block bootstrap: the same method as
`research/walkforward.py`. The close is the median multiplicative de-vig across the
three benchmark books at each book's last snapshot at or before kickoff.

| population | lead | n / games | model − close |
|---|---|---|---|
| **leans** (selected on disagreement) | ≤ 20 min | 166 / 14 | **+0.0269 [−0.0068, +0.0625]** |
| main line, unselected | ≤ 20 min | 188 / 14 | +0.0189 [−0.0087, +0.0459] |
| leans | ≤ 15 min (as registered) | 45 / 4 | not read (< 5 games) |

Levels (leans, lead ≤ 20): model 0.2779, close 0.2511, market at publication 0.2514.

By stat inside the leans: receptions +0.0092 [−0.0245, +0.0455] and rush attempts
**+0.0842 [+0.0166, +0.1586]**. That is one of four per-stat cells printed and the only
one that excludes zero. Read it as the register's "each stat loses independently", not
as a new finding.

**Against the register. This is the one comparison that carries information.** The
week-3 interval on leans contains every per-season point estimate the register
publishes: +0.0218 to +0.0272 as the brief quotes them, and +0.0195 to +0.0237 as
restated after the 2026-09-17 settlement fix. The week-3 point estimate (+0.0269) sits
inside 4 of the 5 published season intervals. **Week 3 is inside the register's range.
It neither confirms nor weakens the standing verdict.**

### Departures from the registered method, both forced

1. **`outcome_close` has no 2026 rows.** It is built from the historical backfill only.
   The close was therefore recomputed read-only from the forward capture in `quotes`,
   using the Board's own loader and de-vig (`jobs.board_read.load_quotes` / `ladder_at`,
   `core.board.market_prob`). As a check, the same code at the 09-27 06:02Z read
   reproduces the Board's published `mkt_p_over` on **380 of 380** upcoming rows exactly.
2. **Lead ≤ 20 min instead of ≤ 15.** The forward schedule snapshots at about T−20, so
   the registered cap admits only 4 games. At ≤ 15 the estimate is larger (+0.0417) and
   unreadable.

### A population difference that is not a departure

The leans are selected on |model − market| ≥ 4pp, which are the rows where model and
market disagree most. The unselected main-line set is the register's analogue. This
week that distinction barely exists: 168 of the 196 graded main-line rows lean.

## Q3 — calibration

The model's probability for the lean side, against how often the lean cleared:

| lean-side band | n | forecast | realised [Wilson] |
|---|---|---|---|
| 0.5–0.6 | 43 | 0.557 | 0.535 [0.389, 0.675] |
| 0.6–0.7 | 65 | 0.647 | 0.585 [0.463, 0.696] |
| 0.7–0.8 | 40 | 0.745 | 0.550 [0.398, 0.693] — forecast outside |
| 0.8–0.9 | 18 | 0.851 | 0.500 [0.290, 0.710] — forecast outside |
| 0.9–1.0 | 5 | 0.933 | 0.400 [0.118, 0.769] — forecast outside |

On the over side, the three lowest bands (0.0 to 0.3, n = 52) realise 0.44 to 0.60
against forecasts of 0.07 to 0.25, and the 0.9 to 1.0 band is empty (n = 0). The full
tables for both populations are in the output file.

The shape is overconfidence: the further the model is from the market, the less often
it is right. That matches brief 021's week-1 finding (too low on thin rungs). **The
Wilson intervals treat leans as independent. Leans share games and players, so the
effective n is lower and these intervals are too narrow.** Treat this as descriptive.

## Q4 — did leans that moved against us do worse?

The read reports 10 line moves and 2 lean changes across **all 198** published leans.
Among the 176 graded leans the flags cover **9**: 8 line moves and 1 lean change. Of the
8 line moves, 4 went against the lean and 4 toward it.

| | cleared |
|---|---|
| flagged | 4 of 9 = 0.444 [0.189, 0.733] |
| not flagged | 91 of 167 = 0.545 [0.469, 0.619] |
| flagged − not flagged, one bootstrapped contrast | −10.0pp [−46.4, +30.5] |

Nine leans can say nothing. The better-powered version uses every graded lean with a
close: did the market move against the lean between publication and kickoff?

| | cleared |
|---|---|
| close moved against the lean | 44 of 82 = 0.537 |
| close moved with it (1 exactly flat) | 46 of 84 = 0.548 |
| against − with, one bootstrapped contrast | −1.1pp [−10.6, +9.3] |

Mean drift toward the lean, publication to close: −0.03pp [−0.24, +0.19]. Median
|drift| is 1.12pp. **The close did not move toward our leans on average (no CLV), and
leans the market moved against did no measurably worse.** Nothing here supports a
change to how leans are published.

## What this cannot support

- Any statement that the model "worked" or "beat the market" this week.
- Any statement that week 3 was worse than the register. The point estimate is inside
  the register's range.
- Rush attempts "losing badly". That is one cell of four, with no multiple-comparison
  correction, over 39 leans.

## Public or internal

**Internal.** It is one week, every headline interval spans zero, and under the Claims
rule the only verdict a page could compute from it is "no better than". If a weekly
model scorecard is wanted on the site, it should be a generated Research entry whose
wording comes from the interval, not this scratch document. That decision is Ethan's.
