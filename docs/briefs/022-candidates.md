# Brief 022 - candidates and mechanisms (frozen before any holdout)

Committed BEFORE any CFB row is read and before week 2 exists. `research/sweep/common.py`
refuses every CFB accessor, and the week-2 population, until this file is in HEAD.

**Selection rule, applied mechanically:** a candidate is a SEARCH test whose 95% game-block
interval excludes zero on >= 5 games AND whose direction implies a trade or an execution
choice. Net-of-cost tests are not separate candidates: each is the bar-4 evidence of the
candidate it belongs to. Tests that are significant only on the losing side (every H2
economic cell, every price-path and liquidity net-of-cost cell) are listed as evidence of no
edge, not as candidates. A candidate with no credible mechanism is listed with
`other side: none` and fails bar 1 by construction.

**Reclassification, applied under the pre-registered rule:** the 64 H1 'share of markets
with net > 0' records are >= 0 by construction and are `descriptive`, not tests.

Replication targets: `cfb` for market-structure mechanisms on team markets; `week2` for
player props (CFB has none), trade-tape events (CFB has no tape) and football-specific
distributions. The replication test named in each row is the registry record the CFB or
week-2 run must produce; bar 2 is graded against exactly that record.

## Part 2 - H1 determination lag (15 candidates)

| id | search test: est [95%] p, games | other side | replication | cost basis -> value | persistence |
|---|---|---|---|---|---|
| H1 KXNFLREC yes_midgame G0 C10 | +19.747 [+16.270, +22.966] p=3.9e-30, 14g | **none** | week2 (player props and a play clock: CFB has neither) | mean executable net per contract after fee, depth-confirmed >= order s -> +19.75 | 0.0 |
| H1 KXNFLREC yes_midgame G0 C100 | +23.584 [+16.549, +27.747] p=3.9e-16, 12g | **none** | week2 (player props and a play clock: CFB has neither) | mean executable net per contract after fee, depth-confirmed >= order s -> +23.58 | 0.0 |
| H1 KXNFLREC yes_midgame G60 C10 | +1.400 [+1.080, +1.909] p=3.5e-11, 6g | market makers / NO-side lottery holders who do not cancel resting YES asks when the rung is reached | week2 (player props and a play clock: CFB has neither) | mean executable net per contract after fee, depth-confirmed >= order s -> +1.40 | 250.0 |
| H1 KXNFLREC yes_midgame G120 C10 | +1.813 [+1.238, +2.471] p=6.6e-08, 8g | market makers / NO-side lottery holders who do not cancel resting YES asks when the rung is reached | week2 (player props and a play clock: CFB has neither) | mean executable net per contract after fee, depth-confirmed >= order s -> +1.81 | 250.0 |
| H1 KXNFLREC yes_midgame G300 C10 | +1.812 [+1.421, +2.279] p=2.1e-17, 7g | market makers / NO-side lottery holders who do not cancel resting YES asks when the rung is reached | week2 (player props and a play clock: CFB has neither) | mean executable net per contract after fee, depth-confirmed >= order s -> +1.81 | 250.0 |
| H1 KXNFLREC yes_midgame G300 C100 | +1.511 [+1.046, +1.976] p=7.3e-09, 6g | market makers / NO-side lottery holders who do not cancel resting YES asks when the rung is reached | week2 (player props and a play clock: CFB has neither) | mean executable net per contract after fee, depth-confirmed >= order s -> +1.51 | 250.0 |
| H1 KXNFLREC at_end G0 C10 | +4.816 [+1.895, +11.017] p=4.8e-02, 8g | **none** | week2 (player props and a play clock: CFB has neither) | mean executable net per contract after fee, depth-confirmed >= order s -> +4.82 | 0.0 |
| H1 KXNFLREC at_end G60 C10 | +5.207 [+1.125, +15.644] p=2.1e-01, 6g | traders pricing off the live stat feed before corrections | week2 (player props and a play clock: CFB has neither) | mean executable net per contract after fee, depth-confirmed >= order s -> +5.21 | None |
| H1 KXNFLREC at_end G300 C10 | +13.488 [+1.200, +36.618] p=1.9e-01, 5g | traders pricing off the live stat feed before corrections | week2 (player props and a play clock: CFB has neither) | mean executable net per contract after fee, depth-confirmed >= order s -> +13.49 | None |
| H1 KXNFLRSHATT yes_midgame G0 C10 | +5.531 [+1.400, +10.144] p=1.7e-02, 6g | **none** | week2 (player props and a play clock: CFB has neither) | mean executable net per contract after fee, depth-confirmed >= order s -> +5.53 | 0.0 |
| H1 KXNFLRSHATT yes_midgame G60 C10 | +1.157 [+0.900, +1.620] p=3.5e-10, 5g | market makers / NO-side lottery holders who do not cancel resting YES asks when the rung is reached | week2 (player props and a play clock: CFB has neither) | mean executable net per contract after fee, depth-confirmed >= order s -> +1.16 | 13.0 |
| H1 KXNFLRSHATT yes_midgame G120 C10 | +1.440 [+1.038, +2.025] p=1.7e-07, 7g | market makers / NO-side lottery holders who do not cancel resting YES asks when the rung is reached | week2 (player props and a play clock: CFB has neither) | mean executable net per contract after fee, depth-confirmed >= order s -> +1.44 | 13.0 |
| H1 KXNFLRSHATT yes_midgame G300 C10 | +1.200 [+0.900, +1.800] p=7.4e-06, 6g | market makers / NO-side lottery holders who do not cancel resting YES asks when the rung is reached | week2 (player props and a play clock: CFB has neither) | mean executable net per contract after fee, depth-confirmed >= order s -> +1.20 | 13.0 |
| H1 KXNFLTOTAL yes_midgame G0 C10 | +6.748 [+3.738, +9.498] p=8.2e-06, 11g | **none** | week2 (player props and a play clock: CFB has neither) | mean executable net per contract after fee, depth-confirmed >= order s -> +6.75 | 0.0 |
| H1 KXNFLTOTAL yes_midgame G0 C100 | +5.943 [+3.060, +10.038] p=1.5e-03, 11g | **none** | week2 (player props and a play clock: CFB has neither) | mean executable net per contract after fee, depth-confirmed >= order s -> +5.94 | 0.0 |

Mechanisms (verbatim, shared by the ids listed):

- **6 ids** (H1 KXNFLREC yes_midgame G0 C10 ... H1 KXNFLTOTAL yes_midgame G0 C100): None credible: nflverse time_of_day is the SNAP, so at G=0 the play is still running and the price has not yet had a chance to move - a latency race by construction.
- **7 ids** (H1 KXNFLREC yes_midgame G60 C10 ... H1 KXNFLRSHATT yes_midgame G300 C10): Stale resting YES asks at 0.96-0.99 on rungs already reached: the k-th catch/carry happens and nobody cancels. The 1-4c left is too small for attentive makers against 20-35 min of capital lock, the fee and correction risk. RISK THE SCREEN CANNOT SEE: determination uses FINAL nflverse stats, but a trader acts on the LIVE feed. KXNFLREC-26SEP13NODET-NODVELE14-8 traded at 0.99 YES after the whistle (live feed apparently 8) and settled NO on a final of 7 - one such loss (-99pp) erases ~55 of these +1.8pp wins.
- **2 ids** (H1 KXNFLREC at_end G60 C10 ... H1 KXNFLREC at_end G300 C10): Post-whistle prices on an at-end REC rung: driven by the live-stat vs final-stat disagreement (the Vele market) - dispute risk, not free money. RISK THE SCREEN CANNOT SEE: determination uses FINAL nflverse stats, but a trader acts on the LIVE feed. KXNFLREC-26SEP13NODET-NODVELE14-8 traded at 0.99 YES after the whistle (live feed apparently 8) and settled NO on a final of 7 - one such loss (-99pp) erases ~55 of these +1.8pp wins.

## Part 2 - H2 imbalance / H3 lifecycle (53 candidates)

| id | search test: est [95%] p, games | other side | replication | cost basis -> value | persistence |
|---|---|---|---|---|---|
| H2 KXNFLREC pre 10s | -0.004 [-0.005, -0.003] p=2.1e-08, 15g | **none** | none (no mechanism) | top-decile |I| signed move minus half-spread minus taker fee at 100 co -> -4.18 | 10.0 |
| H2 KXNFLREC pre 300s | +0.064 [+0.054, +0.075] p=1.1e-32, 15g | **none** | none (no mechanism) | top-decile |I| signed move minus half-spread minus taker fee at 100 co -> -4.09 | 300.0 |
| H2 KXNFLREC in-game 10s | +0.094 [+0.050, +0.138] p=2.1e-05, 15g | **none** | week2 (no CFB props) | top-decile |I| signed move minus half-spread minus taker fee at 100 co -> -7.14 | 10.0 |
| H2 KXNFLREC in-game 60s | -0.233 [-0.425, -0.074] p=1.1e-02, 15g | **none** | week2 (no CFB props) | top-decile |I| signed move minus half-spread minus taker fee at 100 co -> -7.48 | 60.0 |
| H2 KXNFLREC in-game 300s | -0.543 [-0.930, -0.238] p=1.8e-03, 15g | **none** | week2 (no CFB props) | top-decile |I| signed move minus half-spread minus taker fee at 100 co -> -8.15 | 300.0 |
| H2 KXNFLRSHATT pre 10s | -0.004 [-0.007, -0.000] p=2.7e-02, 14g | **none** | none (no mechanism) | top-decile |I| signed move minus half-spread minus taker fee at 100 co -> -8.51 | 10.0 |
| H2 KXNFLRSHATT pre 300s | +0.068 [+0.050, +0.089] p=1.5e-11, 14g | **none** | none (no mechanism) | top-decile |I| signed move minus half-spread minus taker fee at 100 co -> -8.41 | 300.0 |
| H2 KXNFLRSHATT in-game 10s | +0.180 [+0.102, +0.272] p=3.8e-05, 14g | **none** | week2 (no CFB props) | top-decile |I| signed move minus half-spread minus taker fee at 100 co -> -21.27 | 10.0 |
| H2 KXNFLRSHATT in-game 60s | -0.923 [-1.555, -0.467] p=1.1e-03, 14g | **none** | week2 (no CFB props) | top-decile |I| signed move minus half-spread minus taker fee at 100 co -> -22.97 | 60.0 |
| H2 KXNFLRSHATT in-game 300s | -1.856 [-2.849, -1.146] p=2.3e-05, 14g | **none** | week2 (no CFB props) | top-decile |I| signed move minus half-spread minus taker fee at 100 co -> -24.08 | 300.0 |
| H2 KXNFLSPREAD pre 10s | -0.001 [-0.001, -0.000] p=4.7e-10, 15g | **none** | none (no mechanism) | top-decile |I| signed move minus half-spread minus taker fee at 100 co -> -1.93 | 10.0 |
| H2 KXNFLSPREAD pre 60s | +0.002 [+0.001, +0.002] p=8.9e-21, 15g | **none** | none (no mechanism) | top-decile |I| signed move minus half-spread minus taker fee at 100 co -> -1.93 | 60.0 |
| H2 KXNFLSPREAD pre 300s | +0.018 [+0.016, +0.020] p=5.4e-62, 15g | **none** | none (no mechanism) | top-decile |I| signed move minus half-spread minus taker fee at 100 co -> -1.91 | 300.0 |
| H2 KXNFLSPREAD in-game 10s | +0.100 [+0.071, +0.132] p=1.2e-10, 15g | the maker on the thin side who is slow to re-quote | cfb: `H2 slope / KXNCAAFSPREAD|in|10s` | top-decile |I| signed move minus half-spread minus taker fee at 100 co -> -2.38 | 10.0 |
| H2 KXNFLSPREAD in-game 60s | +0.301 [+0.240, +0.358] p=4.2e-22, 15g | the maker on the thin side who is slow to re-quote | cfb: `H2 slope / KXNCAAFSPREAD|in|60s` | top-decile |I| signed move minus half-spread minus taker fee at 100 co -> -2.01 | 60.0 |
| H2 KXNFLSPREAD in-game 300s | +0.454 [+0.234, +0.665] p=5.1e-05, 15g | the maker on the thin side who is slow to re-quote | cfb: `H2 slope / KXNCAAFSPREAD|in|300s` | top-decile |I| signed move minus half-spread minus taker fee at 100 co -> -1.70 | 300.0 |
| H2 KXNFLTOTAL pre 10s | -0.000 [-0.001, -0.000] p=3.0e-06, 15g | **none** | none (no mechanism) | top-decile |I| signed move minus half-spread minus taker fee at 100 co -> -1.91 | 10.0 |
| H2 KXNFLTOTAL pre 60s | +0.001 [+0.001, +0.002] p=8.9e-08, 15g | **none** | none (no mechanism) | top-decile |I| signed move minus half-spread minus taker fee at 100 co -> -1.90 | 60.0 |
| H2 KXNFLTOTAL pre 300s | +0.016 [+0.014, +0.019] p=3.3e-38, 15g | **none** | none (no mechanism) | top-decile |I| signed move minus half-spread minus taker fee at 100 co -> -1.89 | 300.0 |
| H2 KXNFLTOTAL in-game 10s | +0.120 [+0.078, +0.161] p=1.5e-08, 15g | the maker on the thin side who is slow to re-quote | cfb: `H2 slope / KXNCAAFTOTAL|in|10s` | top-decile |I| signed move minus half-spread minus taker fee at 100 co -> -2.21 | 10.0 |
| H2 KXNFLTOTAL in-game 60s | +0.336 [+0.234, +0.413] p=2.0e-13, 15g | the maker on the thin side who is slow to re-quote | cfb: `H2 slope / KXNCAAFTOTAL|in|60s` | top-decile |I| signed move minus half-spread minus taker fee at 100 co -> -1.91 | 60.0 |
| H2 KXNFLTOTAL in-game 300s | +0.608 [+0.398, +0.806] p=3.0e-09, 15g | the maker on the thin side who is slow to re-quote | cfb: `H2 slope / KXNCAAFTOTAL|in|300s` | top-decile |I| signed move minus half-spread minus taker fee at 100 co -> -1.30 | 300.0 |
| H2 KXNFLGAME pre 60s | +0.001 [+0.000, +0.002] p=6.6e-03, 15g | **none** | none (no mechanism) | top-decile |I| signed move minus half-spread minus taker fee at 100 co -> -2.11 | 60.0 |
| H2 KXNFLGAME pre 300s | +0.008 [+0.005, +0.011] p=1.2e-06, 15g | **none** | none (no mechanism) | top-decile |I| signed move minus half-spread minus taker fee at 100 co -> -2.10 | 300.0 |
| H2 KXNFLGAME in-game 10s | +0.284 [+0.181, +0.381] p=1.9e-08, 15g | the maker on the thin side who is slow to re-quote | cfb: `H2 slope / KXNCAAFGAME|in|10s` | top-decile |I| signed move minus half-spread minus taker fee at 100 co -> -1.32 | 10.0 |
| H2 KXNFLGAME in-game 60s | +0.545 [+0.310, +0.810] p=2.2e-05, 15g | the maker on the thin side who is slow to re-quote | cfb: `H2 slope / KXNCAAFGAME|in|60s` | top-decile |I| signed move minus half-spread minus taker fee at 100 co -> -1.03 | 60.0 |
| H2 KXNFLGAME in-game 300s | +0.626 [+0.096, +1.218] p=3.2e-02, 15g | the maker on the thin side who is slow to re-quote | cfb: `H2 slope / KXNCAAFGAME|in|300s` | top-decile |I| signed move minus half-spread minus taker fee at 100 co -> -1.48 | 300.0 |
| H3 KXNFLREC ttk >72h - 1-6h | +6.384 [+5.413, +7.522] p=2.2e-31, 16g | the taker who crosses at those times | week2 | not an edge - a spread contrast carries no executable PnL -> n/a | None |
| H3 KXNFLREC ttk 24-72h - 1-6h | +1.747 [+0.898, +2.831] p=3.9e-04, 16g | the taker who crosses at those times | week2 | not an edge - a spread contrast carries no executable PnL -> n/a | None |
| H3 KXNFLREC ttk 6-24h - 1-6h | +0.686 [+0.148, +1.402] p=3.9e-02, 16g | the taker who crosses at those times | week2 | not an edge - a spread contrast carries no executable PnL -> n/a | None |
| H3 KXNFLREC ttk 0-1h - 1-6h | -0.811 [-1.076, -0.548] p=8.7e-10, 16g | the taker who crosses at other times | week2 | not an edge - a spread contrast carries no executable PnL -> n/a | None |
| H3 KXNFLREC ttk in-game - 1-6h | +15.619 [+12.766, +18.056] p=9.4e-32, 16g | the taker who crosses at those times | week2 | not an edge - a spread contrast carries no executable PnL -> n/a | None |
| H3 KXNFLREC hour 00-08 - 09-16 | -0.912 [-1.352, -0.393] p=1.5e-04, 16g | **none** | none (no mechanism) | not an edge - a spread contrast carries no executable PnL -> n/a | None |
| H3 KXNFLRSHATT ttk >72h - 1-6h | -6.966 [-11.860, -0.831] p=2.0e-02, 16g | **none** | none (no mechanism) | not an edge - a spread contrast carries no executable PnL -> n/a | None |
| H3 KXNFLRSHATT ttk 6-24h - 1-6h | +5.276 [+2.043, +9.511] p=6.7e-03, 16g | the taker who crosses at those times | week2 | not an edge - a spread contrast carries no executable PnL -> n/a | None |
| H3 KXNFLRSHATT ttk in-game - 1-6h | +36.183 [+31.411, +39.827] p=6.2e-65, 16g | the taker who crosses at those times | week2 | not an edge - a spread contrast carries no executable PnL -> n/a | None |
| H3 KXNFLRSHATT hour 00-08 - 09-16 | -3.527 [-5.407, -1.013] p=1.7e-03, 16g | **none** | none (no mechanism) | not an edge - a spread contrast carries no executable PnL -> n/a | None |
| H3 KXNFLRSHATT Sunday - Mon-Fri | +10.032 [+4.676, +14.263] p=4.1e-05, 16g | **none** | none (no mechanism) | not an edge - a spread contrast carries no executable PnL -> n/a | None |
| H3 KXNFLSPREAD ttk >72h - 1-6h | +0.922 [+0.754, +1.053] p=8.9e-33, 16g | the taker who crosses at those times | cfb: `H3 contrast / KXNCAAFSPREAD|ttk >72h - 1-6h` | not an edge - a spread contrast carries no executable PnL -> n/a | None |
| H3 KXNFLSPREAD ttk 24-72h - 1-6h | +0.446 [+0.362, +0.515] p=4.2e-29, 16g | the taker who crosses at those times | cfb: `H3 contrast / KXNCAAFSPREAD|ttk 24-72h - 1-6h` | not an edge - a spread contrast carries no executable PnL -> n/a | None |
| H3 KXNFLSPREAD ttk 6-24h - 1-6h | +0.248 [+0.189, +0.306] p=5.8e-16, 16g | the taker who crosses at those times | cfb: `H3 contrast / KXNCAAFSPREAD|ttk 6-24h - 1-6h` | not an edge - a spread contrast carries no executable PnL -> n/a | None |
| H3 KXNFLSPREAD ttk in-game - 1-6h | +1.747 [+1.327, +2.139] p=1.6e-16, 16g | the taker who crosses at those times | cfb: `H3 contrast / KXNCAAFSPREAD|ttk in-game - 1-6h` | not an edge - a spread contrast carries no executable PnL -> n/a | None |
| H3 KXNFLSPREAD Sunday - Mon-Fri | -0.269 [-0.363, -0.161] p=2.5e-07, 16g | the taker who crosses at other times | cfb: `H3 contrast / KXNCAAFSPREAD|Sunday - Mon-Fri` | not an edge - a spread contrast carries no executable PnL -> n/a | None |
| H3 KXNFLTOTAL ttk >72h - 1-6h | +0.710 [+0.657, +0.763] p=1.3e-142, 16g | the taker who crosses at those times | cfb: `H3 contrast / KXNCAAFTOTAL|ttk >72h - 1-6h` | not an edge - a spread contrast carries no executable PnL -> n/a | None |
| H3 KXNFLTOTAL ttk 24-72h - 1-6h | +0.348 [+0.279, +0.409] p=6.5e-26, 16g | the taker who crosses at those times | cfb: `H3 contrast / KXNCAAFTOTAL|ttk 24-72h - 1-6h` | not an edge - a spread contrast carries no executable PnL -> n/a | None |
| H3 KXNFLTOTAL ttk 6-24h - 1-6h | +0.200 [+0.147, +0.244] p=3.7e-16, 16g | the taker who crosses at those times | cfb: `H3 contrast / KXNCAAFTOTAL|ttk 6-24h - 1-6h` | not an edge - a spread contrast carries no executable PnL -> n/a | None |
| H3 KXNFLTOTAL ttk in-game - 1-6h | +1.622 [+1.193, +2.041] p=2.0e-13, 16g | the taker who crosses at those times | cfb: `H3 contrast / KXNCAAFTOTAL|ttk in-game - 1-6h` | not an edge - a spread contrast carries no executable PnL -> n/a | None |
| H3 KXNFLTOTAL hour 17-23 - 09-16 | +0.042 [+0.003, +0.086] p=5.2e-02, 16g | the taker who crosses at other times | cfb: `H3 contrast / KXNCAAFTOTAL|hour 17-23 - 09-16` | not an edge - a spread contrast carries no executable PnL -> n/a | None |
| H3 KXNFLTOTAL Sunday - Mon-Fri | -0.202 [-0.309, -0.088] p=2.3e-04, 16g | the taker who crosses at other times | cfb: `H3 contrast / KXNCAAFTOTAL|Sunday - Mon-Fri` | not an edge - a spread contrast carries no executable PnL -> n/a | None |
| H3 KXNFLGAME ttk >72h - 1-6h | +0.019 [+0.008, +0.031] p=1.4e-03, 16g | **none** | none (no mechanism) | not an edge - a spread contrast carries no executable PnL -> n/a | None |
| H3 KXNFLGAME ttk in-game - 1-6h | +0.055 [+0.038, +0.075] p=1.1e-08, 16g | **none** | none (no mechanism) | not an edge - a spread contrast carries no executable PnL -> n/a | None |
| H3 WINS Sunday - Mon-Fri | +4.592 [+0.052, +6.678] p=1.4e-02, 6g | **none** | none (no mechanism) | not an edge - a spread contrast carries no executable PnL -> n/a | None |
| H3 DIVISION Sunday - Mon-Fri | +0.401 [+0.193, +0.519] p=1.6e-05, 6g | **none** | none (no mechanism) | not an edge - a spread contrast carries no executable PnL -> n/a | None |

Mechanisms (verbatim, shared by the ids listed):

- **12 ids** (H2 KXNFLREC pre 10s ... H2 KXNFLGAME pre 300s): No credible mechanism: <=0.08pp per unit I, 95%+ of 10s lookups return the same quote row, n ~900k - a resolution artifact of a huge sample.
- **6 ids** (H2 KXNFLREC in-game 10s ... H2 KXNFLRSHATT in-game 300s): No tradable mechanism: the in-game prop touch is 1-2 contracts, so 'imbalance' is one stale order; the 10s positive slope comes from rows where depth was fresher than the quote (look-ahead) and reverses at 60/300s.
- **9 ids** (H2 KXNFLSPREAD in-game 10s ... H2 KXNFLGAME in-game 300s): In-game the thin side of the touch is consumed as game state changes, so the next move is toward it; about half the slope survives restricting to instants where depth and quote touches agree.
- **14 ids** (H3 KXNFLREC ttk >72h - 1-6h ... H3 KXNFLTOTAL ttk in-game - 1-6h): Makers widen and thin out when information risk is high (in-game) and when interest is low (far from kickoff). Execution timing, not an edge.
- **4 ids** (H3 KXNFLREC ttk 0-1h - 1-6h ... H3 KXNFLTOTAL Sunday - Mon-Fri): Peak maker attention just before kickoff and on Sunday mornings narrows the book. Execution timing, not an edge.
- **4 ids** (H3 KXNFLREC hour 00-08 - 09-16 ... H3 KXNFLRSHATT Sunday - Mon-Fri): No credible mechanism beyond composition: 09-16 ET and Sunday contain the wide in-game samples; RSHATT >72h reverses sign on the same markets (mix artifact).
- **2 ids** (H3 KXNFLGAME ttk >72h - 1-6h ... H3 KXNFLGAME ttk in-game - 1-6h): No mechanism: effects of 0.02-0.06c sit at the 1c tick floor.
- **2 ids** (H3 WINS Sunday - Mon-Fri ... H3 DIVISION Sunday - Mon-Fri): No mechanism claimed: one calendar day of futures.

## Part 3 - open scan (33 candidates)

| id | search test: est [95%] p, games | other side | replication | cost basis -> value | persistence |
|---|---|---|---|---|---|
| S3 reversal KXNFLREC pre h=60s | -0.478 [-0.541, -0.388] p=5.4e-34, 16g | the market maker re-quoting the touch; there is no counterparty at the mid, so the bounce is not tradeable by crossing | week2 (no CFB player props) | top-decile signed next move minus half-spread + taker fee (net must be -> -3.84 | 60.0 |
| S3 reversal KXNFLREC pre h=600s | -0.347 [-0.389, -0.298] p=8.5e-48, 16g | the market maker re-quoting the touch; there is no counterparty at the mid, so the bounce is not tradeable by crossing | week2 (no CFB player props) | top-decile signed next move minus half-spread + taker fee (net must be -> -4.79 | 600.0 |
| S3 reversal KXNFLREC pre h=3600s | -0.239 [-0.278, -0.203] p=1.8e-34, 16g | the market maker re-quoting the touch; there is no counterparty at the mid, so the bounce is not tradeable by crossing | week2 (no CFB player props) | top-decile signed next move minus half-spread + taker fee (net must be -> -5.58 | 3600.0 |
| S3 reversal KXNFLREC in h=60s | -0.209 [-0.238, -0.178] p=2.0e-42, 16g | the market maker re-quoting the touch; there is no counterparty at the mid, so the bounce is not tradeable by crossing | week2 (no CFB player props) | top-decile signed next move minus half-spread + taker fee (net must be -> -10.91 | 60.0 |
| S3 reversal KXNFLREC in h=600s | -0.213 [-0.252, -0.172] p=4.5e-24, 16g | the market maker re-quoting the touch; there is no counterparty at the mid, so the bounce is not tradeable by crossing | week2 (no CFB player props) | top-decile signed next move minus half-spread + taker fee (net must be -> -11.37 | 600.0 |
| S3 reversal KXNFLREC in h=3600s | -0.148 [-0.249, -0.052] p=4.4e-03, 16g | the market maker re-quoting the touch; there is no counterparty at the mid, so the bounce is not tradeable by crossing | week2 (no CFB player props) | top-decile signed next move minus half-spread + taker fee (net must be -> -5.20 | 3600.0 |
| S3 reversal KXNFLRSHATT pre h=60s | -0.044 [-0.104, -0.006] p=8.2e-02, 16g | the market maker re-quoting the touch; there is no counterparty at the mid, so the bounce is not tradeable by crossing | week2 (no CFB player props) | top-decile signed next move minus half-spread + taker fee (net must be -> -16.47 | 60.0 |
| S3 reversal KXNFLRSHATT pre h=600s | -0.279 [-0.348, -0.213] p=1.4e-15, 16g | the market maker re-quoting the touch; there is no counterparty at the mid, so the bounce is not tradeable by crossing | week2 (no CFB player props) | top-decile signed next move minus half-spread + taker fee (net must be -> -8.77 | 600.0 |
| S3 reversal KXNFLRSHATT pre h=3600s | -0.233 [-0.282, -0.184] p=6.8e-20, 16g | the market maker re-quoting the touch; there is no counterparty at the mid, so the bounce is not tradeable by crossing | week2 (no CFB player props) | top-decile signed next move minus half-spread + taker fee (net must be -> -8.93 | 3600.0 |
| S3 reversal KXNFLRSHATT in h=60s | -0.254 [-0.295, -0.202] p=6.1e-26, 16g | the market maker re-quoting the touch; there is no counterparty at the mid, so the bounce is not tradeable by crossing | week2 (no CFB player props) | top-decile signed next move minus half-spread + taker fee (net must be -> -21.42 | 60.0 |
| S3 reversal KXNFLRSHATT in h=600s | -0.325 [-0.365, -0.283] p=3.1e-53, 16g | the market maker re-quoting the touch; there is no counterparty at the mid, so the bounce is not tradeable by crossing | week2 (no CFB player props) | top-decile signed next move minus half-spread + taker fee (net must be -> -15.58 | 600.0 |
| S3 reversal KXNFLRSHATT in h=3600s | -0.159 [-0.262, -0.036] p=7.9e-03, 16g | the market maker re-quoting the touch; there is no counterparty at the mid, so the bounce is not tradeable by crossing | week2 (no CFB player props) | top-decile signed next move minus half-spread + taker fee (net must be -> -17.87 | 3600.0 |
| S3 reversal KXNFLSPREAD pre h=60s | -0.353 [-0.398, -0.297] p=1.0e-42, 16g | the market maker re-quoting the touch; there is no counterparty at the mid, so the bounce is not tradeable by crossing | cfb: `cfb_rep_pricepath / KXNCAAFSPREAD | pre | h=60s | slope` | top-decile signed next move minus half-spread + taker fee (net must be -> -1.87 | 60.0 |
| S3 reversal KXNFLSPREAD pre h=600s | -0.240 [-0.268, -0.208] p=4.4e-56, 16g | the market maker re-quoting the touch; there is no counterparty at the mid, so the bounce is not tradeable by crossing | cfb: `cfb_rep_pricepath / KXNCAAFSPREAD | pre | h=600s | slope` | top-decile signed next move minus half-spread + taker fee (net must be -> -1.95 | 600.0 |
| S3 reversal KXNFLSPREAD pre h=3600s | -0.184 [-0.217, -0.138] p=3.1e-18, 16g | the market maker re-quoting the touch; there is no counterparty at the mid, so the bounce is not tradeable by crossing | cfb: `cfb_rep_pricepath / KXNCAAFSPREAD | pre | h=3600s | slope` | top-decile signed next move minus half-spread + taker fee (net must be -> -2.00 | 3600.0 |
| S3 reversal KXNFLSPREAD in h=60s | -0.062 [-0.097, -0.021] p=2.0e-03, 16g | the market maker re-quoting the touch; there is no counterparty at the mid, so the bounce is not tradeable by crossing | cfb: `cfb_rep_pricepath / KXNCAAFSPREAD | in | h=60s | slope` | top-decile signed next move minus half-spread + taker fee (net must be -> -3.14 | 60.0 |
| S3 reversal KXNFLTOTAL pre h=60s | -0.130 [-0.152, -0.105] p=1.8e-27, 16g | the market maker re-quoting the touch; there is no counterparty at the mid, so the bounce is not tradeable by crossing | cfb: `cfb_rep_pricepath / KXNCAAFTOTAL | pre | h=60s | slope` | top-decile signed next move minus half-spread + taker fee (net must be -> -1.79 | 60.0 |
| S3 reversal KXNFLTOTAL pre h=600s | -0.254 [-0.275, -0.233] p=2.3e-119, 16g | the market maker re-quoting the touch; there is no counterparty at the mid, so the bounce is not tradeable by crossing | cfb: `cfb_rep_pricepath / KXNCAAFTOTAL | pre | h=600s | slope` | top-decile signed next move minus half-spread + taker fee (net must be -> -1.87 | 600.0 |
| S3 reversal KXNFLTOTAL pre h=3600s | -0.244 [-0.267, -0.221] p=1.3e-94, 16g | the market maker re-quoting the touch; there is no counterparty at the mid, so the bounce is not tradeable by crossing | cfb: `cfb_rep_pricepath / KXNCAAFTOTAL | pre | h=3600s | slope` | top-decile signed next move minus half-spread + taker fee (net must be -> -1.82 | 3600.0 |
| S3 reversal KXNFLTOTAL in h=60s | -0.069 [-0.109, -0.036] p=1.6e-04, 16g | the market maker re-quoting the touch; there is no counterparty at the mid, so the bounce is not tradeable by crossing | cfb: `cfb_rep_pricepath / KXNCAAFTOTAL | in | h=60s | slope` | top-decile signed next move minus half-spread + taker fee (net must be -> -2.81 | 60.0 |
| S3 reversal KXNFLGAME pre h=600s | -0.174 [-0.290, -0.017] p=1.4e-02, 16g | the market maker re-quoting the touch; there is no counterparty at the mid, so the bounce is not tradeable by crossing | cfb: `cfb_rep_pricepath / KXNCAAFGAME | pre | h=600s | slope` | top-decile signed next move minus half-spread + taker fee (net must be -> -2.21 | 600.0 |
| S5 continuation KXNFLREC h=60s | +1.382 [+0.625, +2.201] p=7.7e-04, 14g | resting makers adversely selected by the informed print; an outsider sees the print only after the move | week2 (CFB has no trade tape) | continuation minus half-spread + taker fee (net must be > 0) -> -2.68 | 60.0 |
| S5 continuation KXNFLREC h=600s | +1.938 [+0.954, +2.777] p=2.9e-05, 14g | resting makers adversely selected by the informed print; an outsider sees the print only after the move | week2 (CFB has no trade tape) | continuation minus half-spread + taker fee (net must be > 0) -> -1.83 | 600.0 |
| S5 continuation KXNFLRSHATT h=60s | +3.273 [+0.081, +6.464] p=4.2e-02, 13g | resting makers adversely selected by the informed print; an outsider sees the print only after the move | week2 (CFB has no trade tape) | continuation minus half-spread + taker fee (net must be > 0) -> -2.85 | 60.0 |
| S2 far-rung gap KXNFLSPREAD dist 7-10 | -0.685 [-1.005, -0.382] p=2.1e-05, 16g | if real: passive far-rung sellers on Kalshi; if model error: nobody | week2 (football-specific distribution) | executable arm for |gap|>2.5pp, PnL/contract after fee (net must be >  -> n/a | None |
| S2 far-rung gap KXNFLSPREAD dist 10+ | -0.944 [-1.167, -0.716] p=5.4e-16, 16g | if real: passive far-rung sellers on Kalshi; if model error: nobody | week2 (football-specific distribution) | executable arm for |gap|>2.5pp, PnL/contract after fee (net must be >  -> n/a | None |
| S2 far-rung gap KXNFLTOTAL dist 7-10 | -0.556 [-0.949, -0.172] p=5.2e-03, 16g | if real: passive far-rung sellers on Kalshi; if model error: nobody | week2 (football-specific distribution) | executable arm for |gap|>2.5pp, PnL/contract after fee (net must be >  -> n/a | None |
| S2 far-rung gap KXNFLTOTAL dist 10+ | -0.365 [-0.577, -0.165] p=5.5e-04, 16g | if real: passive far-rung sellers on Kalshi; if model error: nobody | week2 (football-specific distribution) | executable arm for |gap|>2.5pp, PnL/contract after fee (net must be >  -> n/a | None |
| S1 C1 GAME - SPREAD1.5 | pre | dev | +2.652 [+2.483, +2.833] p=6.4e-195, 16g | none - the deviation is the definition of the claims | none needed | no executable violation exists -> n/a | None |
| S1 C1 GAME - SPREAD1.5 | in | dev | +3.101 [+2.549, +3.767] p=1.7e-23, 16g | none - the deviation is the definition of the claims | none needed | no executable violation exists -> n/a | None |
| S1 C1 GAME - SPREAD1.5 | in | viol | +1.097 [+0.361, +2.140] p=1.6e-02, 16g | a stale resting quote in-game | cfb: `cfb_rep_constraints / C1 CFBGAME - SPREAD1.5 | in | viol` | depth-verified executable net after fees -> -1.57 | None |
| S1 C2 SPREAD1.5 A + B - 1 | pre | dev | -5.628 [-5.975, -5.318] p=3.7e-250, 16g | none - the deviation is the definition of the claims | none needed | no executable violation exists -> n/a | None |
| S1 C2 SPREAD1.5 A + B - 1 | in | dev | -6.274 [-7.609, -5.176] p=3.8e-24, 16g | none - the deviation is the definition of the claims | none needed | no executable violation exists -> n/a | None |

Mechanisms (verbatim, shared by the ids listed):

- **21 ids** (S3 reversal KXNFLREC pre h=60s ... S3 reversal KXNFLGAME pre h=600s): Mid-quote bounce: on a thin book the touch flickers as resting size is lifted and refilled, so successive mid changes are negatively autocorrelated.
- **3 ids** (S5 continuation KXNFLREC h=60s ... S5 continuation KXNFLRSHATT h=60s): Large prop takers carry information (lineup, injury, role news) and their price impact is permanent.
- **4 ids** (S2 far-rung gap KXNFLSPREAD dist 7-10 ... S2 far-rung gap KXNFLTOTAL dist 10+): Either Kalshi makers sell far rungs cheap (longshot rungs nobody prices), or the shifted historical margin distribution has fat tails from mixing eras and matchups - the second is the prior.
- **4 ids** (S1 C1 GAME - SPREAD1.5 | pre | dev ... S1 C2 SPREAD1.5 A + B - 1 | in | dev): Arithmetic of the claims: P(win) - P(win by over 1.5) is P(margin = 1) ~2.1%, and A+B-1 is -P(|margin| <= 1) ~4.4-4.8% - no mispricing implied.
- **1 ids** (S1 C1 GAME - SPREAD1.5 | in | viol): Ladder rungs update out of step in-game (the 019 mechanism): a stale resting quote briefly violates P(win) >= P(win by over 1.5).

