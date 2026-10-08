# c-36 — what a season win total does between now and January

- **Pre-registration:** `docs/C36-win-total-drift-preregistration.md`, pushed at `3dc5735`
  before the script existed and before any fetched candle was read. Addendum 1 (`f850f46`)
  was written after the first run stopped at validation and before any result.
- **Scripts:** `research/win_total_drift.py` (registered), `research/c36_posthoc_reaction.py`
  (POST HOC), `research/c36_fetch.py` (the raw fetch), `research/c36_candle_diag_*.py`.
- **Results:** `research/results/win_total_drift.json`, `research/results/win_total_reaction.json`.
- **Raw:** `<STORAGE>/research_raw/c36/` — 544 candle files, 544 order books, 7 MB. Free,
  unauthenticated Kalshi endpoints. Zero credits.

## Scope, on every number below

Kalshi `KXNFLWINS-27{TEAM}-{k}`, 32 teams, NFL 2026, market open (2026-04-20) to 2026-10-08.
**Four weeks have been played: 128 team-weeks, not sixteen per team.** The model is a-41/a-55's
season model at states after weeks 0–4. Nothing has settled; nothing here says who is right
about any team. The order books are one snapshot taken 02:08–02:15 ET on Thursday 10-08.
`KXNFLWINSWEEK`, the division series and the conference champions are not covered.

## What still exists — the brief asked first

- **In the store:** live quotes for season futures from 2026-09-24 to 10-08 only. No retention
  hold covers any of the 1,348 futures markets. 0 depth rows, 0 trade prints, 0 results.
- **Only in c-25's preserved file:** Wednesday 09-16 and 09-23 at 16:00 UTC.
- **Only from candles:** every other instant before 09-24 — the whole preseason, weeks 1 and 2,
  and the Thursday game of week 3.
- **The candles reproduce the quotes.** Kalshi's free candlestick endpoint returns bid and ask
  for every rung back to the market's open. Against the live quotes: 319 of 320 rungs agree to
  1c on both sides at 09-29 16:00, 304 of 304 at 10-06 16:00. Against c-25's preserved rows:
  312 of 314 and 297 of 297.
- **So c-25's "no futures price older than 14 days exists or ever will" is true of the store and
  false of the path.** The price path of a season future is recoverable for free, at hourly
  resolution. What is not recoverable is depth: a candle carries no book.
- **One thing about the feed, found by the stop rule.** Kalshi emits no hourly candle for an
  hour in which nothing changed (a median 398 candles per rung over ~750 hours). The last
  candle stands until the next. Carried forward, it matched the live quote on 347 of 347 stale
  reads at five instants. My registered read assumed one candle per hour; addendum 1 fixes it.

## Q1 — how much it moves, and when

The "number" is the win count where a team's ladder crosses 0.5.

| week | mean \|change\| | median | p90 | max |
|---|---|---|---|---|
| 1 | 0.64 wins | 0.50 | 1.27 | 1.77 |
| 2 | 0.69 | 0.56 | 1.40 | 1.71 |
| 3 | 0.61 | 0.56 | 1.12 | 1.47 |
| 4 | 0.57 | 0.53 | 0.99 | 1.23 |

- **A result is worth about 0.6 wins of number.** After a win +0.60 [+0.50, +0.72]; after a loss
  −0.62 [−0.75, −0.50] (64 team-weeks each, team blocks).
- **By rung.** A rung within one win of the number moves a median 8.5pp a week (p90 17.4pp).
  Three or more wins away it moves 3.0pp (p90 8.5pp).
- **By team, over four weeks:** most moved LAC, CLE, PIT, NYG, CHI, ATL (3.5–4.4 wins of summed
  movement); least MIA (0.79), IND, DET, SEA, GB, NYJ. LAC's number fell 4.4 wins from the
  preseason; LV's rose 2.4. The correlation of the preseason number with the week-4 number is
  0.78.
- **When.** The number moves 0.044 wins a day in the preseason (median 0.021) and 0.191 a day
  in season (median 0.090). Within a week the movement is the team's own game: 24 hours after
  kickoff the number has moved +0.56 after a win and −0.67 after a loss, against +0.60 / −0.62
  for the whole week. The four days before the next kickoff move it a mean 0.15 wins in
  absolute terms and 0.03 net.
- **The registered "share inside the 6-hour game window" is 0.54 [0.48, 0.62] and I do not
  trust it as a share.** For five hours after a game only 2–4 of a ladder's ~9 rungs carry a
  usable quote, so the number at kickoff + 5h is noisy, and that noise enters both halves of
  the ratio. The POST HOC path below is the better description.

## Q2 — does the market move more than it should

**No. On this month it moves less than the model and shows no reversal.**

- **T2a (registered, model-free): null.** Slope of the post-game drift on the game-window jump:
  −0.050 [−0.190, +0.061], z −0.75, MDE 0.18, 108 team-weeks. No overreaction detectable; a
  reversal smaller than about a fifth of the jump could not have been seen.
- **The artifact the design was built to avoid is real and large.** Measured from the same
  instant, the slope is −0.545 [−0.724, −0.339], z −5.6. That is a noisy post-game number
  entering the jump and the drift with opposite signs. Read alone it would have been
  "the market overreacts to results by half".
- **T2b (descriptive): the market moves less than the model per result.** A win moves the
  market number +0.60 and the model's +0.84; a loss −0.62 and −0.84. Slope of the market's
  weekly change on the model's: 0.72 [0.65, 0.80]; RMS ratio 0.84 [0.75, 0.92].
- **So the brief's direction is reversed.** If either side swings further on a result it is the
  model. That is not evidence the model overreacts: it starts from a compressed preseason view
  and has more to learn (next bullet).
- **Dispersion path.** SD across teams, market / model, in wins:

  | instant | market | model | slope of model on market |
  |---|---|---|---|
  | preseason | 2.17 | 1.27 | 0.47 [0.34, 0.64] |
  | after wk 1 | 2.22 | 1.56 | 0.59 [0.47, 0.75] |
  | after wk 2 | 2.37 | 1.86 | 0.70 [0.57, 0.85] |
  | after wk 3 | 2.34 | 2.02 | 0.76 [0.64, 0.90] |
  | after wk 4 | 2.38 | 2.23 | 0.85 [0.73, 0.98] |

  Rows 1–3 reproduce c-25's post hoc table (0.59 / 0.69 / 0.76) from candles alone. The market
  spread teams 2.2 wins apart before a game was played and has barely widened. The model
  started at 1.3 and has nearly caught up. The mean absolute gap went from 1.00 wins to 0.82.

## Q3 — does the model's gap at week k predict the move to week k+1

**Not detectably, once the market's own level is controlled. Without that control it looks
like a strong yes, which is the trap the brief named.**

| | beta on the gap | |
|---|---|---|
| **T3-P, registered primary** (week FE, result, level, result × level) | **+0.050 [−0.022, +0.132]**, z +1.27, MDE 0.110 | 128 team-weeks, 32 team blocks |
| same, game blocks | +0.050 [−0.020, +0.120] | 64 blocks |
| **T3-D, registered** (pre-game drift, no result in the window) | **+0.005 [−0.040, +0.045]**, z +0.23, MDE 0.062 | 126 team-weeks |
| without the level control | +0.143 [+0.084, +0.205], z +4.78 | not a test |
| move started at the gap's own instant | +0.071 [+0.004, +0.148] | not a test |

- **BH at q = 0.10 over the three registered tests: no survivor** (p 0.45 / 0.20 / 0.82).
- **The verdict, as registered:** not detectable at 128 team-weeks; MDE 0.11 wins of market
  move per win of disagreement per week. It is not "the model has no information".
- **Why the uncontrolled slope is not skill.** The model is compressed, so its gap is close to
  minus a multiple of the market's level. And the market's level does predict the move, with
  no model in the room: level −0.056 [−0.093, −0.016], result × level −0.045 [−0.095, −0.003].
  A strong team's win is less of a surprise and its loss more of one. Leave the level out and
  the gap inherits that. z goes from 4.8 to 1.3 when it is put back.
- **The cleanest version is the flattest.** Between Wednesday and an hour before kickoff
  nothing happens to the team, and the gap predicts 0.5% of itself.
- **Not stable by week:** +0.04, +0.13, −0.05, +0.13 for weeks 1–4 alone, none excluding zero.
- **The gap is persistent** (week-to-week r 0.93, SD 1.22 wins). Four weeks are closer to 32
  observations of one standing disagreement than to 128.
- **The tradeable version (registered table).** Each week, the model's side of the team's
  central rung, bought Wednesday at the candle touch and sold the next Tuesday at the opposite
  touch, taker fee both legs at 100 contracts:
  - mid to mid: **+2.57c [+0.62, +4.48]** per contract;
  - net: **−2.50c [−4.45, −0.57]**; the larger-gap half −2.79c [−5.06, −0.22];
  - mean fee for the two legs 3.41c.
  The mid does drift toward the model's side, by an amount T3-P attributes to the market's
  level rather than to the model, and it costs twice that to collect.
- **The brief said there is real power here.** There is more than c-32 had (one window, 32
  games) and less than the sentence implies: an MDE of 0.11 on a gap whose SD is 1.22 wins is
  about 0.13 wins of weekly drift, roughly 2pp on a central rung, against a 5c round trip. By
  week 18 the MDE should be near 0.05.

## POST HOC — how fast the market absorbs the team's own result

Not registered. Run after the registered output showed a 6-hour jump of +0.24 / −0.38 wins
against a weekly +0.60 / −0.62, which looked like a market still offering old prices after the
final whistle.

| hours after kickoff | after a win | after a loss | share of the 24h move done | usable rungs (median) |
|---|---|---|---|---|
| before | | | | 9 |
| 4 | +0.42 | −0.57 | 0.77 [0.46, 1.04] | 3 |
| 5 | +0.24 | −0.40 | 0.51 [0.23, 0.74] | 4 |
| 6 | +0.52 | −0.56 | 0.88 [0.63, 1.13] | 4 |
| 8 | +0.49 | −0.46 | 0.77 [0.58, 0.96] | 6 |
| 12 | +0.45 | −0.62 | 0.89 [0.77, 0.99] | 6 |
| 17 | +0.50 | −0.65 | 0.94 [0.84, 1.03] | 8 |
| 24 | +0.56 | −0.67 | 1 | 9 |

- **It is not a stale price. It is a missing book.** The path is not monotone because the
  ladder is two to four rungs for hours after a game; the quotes that remain have moved.
- **Priced at the touch, the obvious trade loses.** Central rung chosen before kickoff, the
  result's side bought after the game, sold at kickoff + 24h, 127 team-games, game blocks:

  | enter at | mid already moved | of the 24h move | entry spread (median) | round trip net |
  |---|---|---|---|---|
  | K+4h | 6.1c | 8.6c | 10c | −9.2c [−11.0, −7.6] |
  | K+5h | 6.7c | 8.6c | 5c | −8.2c [−10.0, −6.7] |
  | K+8h | 7.7c | 8.6c | 4c | −7.9c [−9.2, −6.6] |

  Negative in every week and positive on 5–13% of trades. About 2c of the move is left at
  K+4h and it sits inside a 10c spread.
- **What this does not test:** a resting order inside that spread. `KXNFLWINS` is
  `quadratic` (maker-free), and a 10c post-game spread is the one place on this market where a
  maker has room. There is no tape and no queue on disk to simulate a fill.

## Q4 — what it costs to hold one

- **Fee, from `/series` tonight:** `quadratic`, multiplier 1. Makers pay nothing; a taker pays
  1.75c at 0.50 and 1.12c at 0.20 or 0.80, per leg, at 100 contracts. Agrees with `core.fees`.
- **The book, one overnight snapshot (544 rungs):** 128 decided, 297 two-sided, **119 undecided
  with a one-sided or empty book** (the tails).

  | mid | rungs | spread | touch YES / NO | all-in over mid, 100 | 500 | 1,000 |
  |---|---|---|---|---|---|---|
  | < 0.10 | 51 | 5c | 5 / 600 | 3.9c / 3.2c | 4.1 / 3.4 | 6.9 / 3.6 (NO fills on 35%) |
  | 0.10–0.25 | 45 | 4c | 6 / 174 | 5.1 / 3.5 | 5.9 / 4.0 | 8.0 / 4.5 |
  | 0.25–0.75 | 110 | 2c | 45 / 100 | 3.4 / 3.2 | 4.2 / 4.0 | 5.1 / 4.6 |
  | 0.75–0.90 | 37 | 5c | 5 / 300 | 4.3 / 3.7 | 5.6 / 4.0 | 6.4 / 5.0 |
  | > 0.90 | 50 | 5c | 5 / 600 | 4.0 / 3.4 | 4.1 / 3.5 | 4.0 (40%) / 5.4 |

  Costs are YES buy / NO buy, taker fee included, per contract.
- **Spread through the season (candles, Tuesdays 16:00 UTC):** within two wins of the number
  the median is 2c in the preseason and 1c after weeks 2–4; further out 4–7c early and 1–2c
  now. Tuesday afternoon is tighter than Thursday 2am.
- **Turnover.** Contracts traded per rung over the last seven days: median 461, p90 11,987,
  max 474,164; 36% of rungs traded nothing. Open interest at the money is a median 24,189.
- **Carry is the small part.** 102 days to the close (2027-01-18). At an ASSUMED 4% a year, a
  contract bought at 0.50 costs 0.56c of carry; at 0.80, 0.89c. Whether Kalshi pays interest on
  position collateral was not checked; if it does, carry is smaller still.
- **So the cost of a season future is the crossing, not the wait.**
  - Hold to settlement at the money, 100 contracts: one crossing 3.3c plus carry 0.6c — about
    **4c**, on a 50c stake, tied up 102 days.
  - A one-week round trip: two crossings — about **6.6c**, and capital back in a week.
  - A 3c edge held to January is under water after cost. The edge that breaks even is ~4pp on
    a central rung, which is 8% on the capital over 102 days.
  - The brief's point stands in a different form than it was put: the three months cost little
    in interest and a great deal in what cannot be learned — the position cannot be scored, and
    the edge cannot be recycled, until January.

## What the brief had right and wrong

- **Right:** the movement is observable now and the settlement is not. Every test here ran
  without a settled outcome.
- **Right:** the trap. The uncontrolled slope is +0.143 at z 4.8 and the controlled one +0.050
  at z 1.3.
- **Wrong: "sixteen weekly observations per team".** Four exist. The power argument is for
  January.
- **Wrong direction: "if the market swings further than a Bayesian update should".** The market
  moved 0.72 of the model's update per result and did not reverse.
- **Incomplete: "c-25's figures depend on quotes that prune at 14 days ... say which weeks are
  reconstructable only from candles".** They are reconstructable, to the cent, for every week
  back to April. The retention problem c-25 raised is real for depth and not for price.
- **Untested: "priced by far fewer people with far more time".** Tuesday spreads at the money
  are 1c, as tight as the game lines. The thin part is the wings and the hours after a game.

## What I would do next, in order

1. **Re-run this script unchanged after weeks 8 and 12.** T3-P's MDE falls with every week and
   the fetch is free. Do not add tests.
2. **Capture the post-game book.** Depth and trade prints for `KXNFLWINS` from kickoff to
   kickoff + 24h are the only thing that can price a maker inside the 10c post-game spread.
   That is track A's logger, and it is the one opening this unit found.
3. **Do not open a taker strategy on win totals.** Every executable number here is negative.
