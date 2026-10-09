# C41 — what predicts the residual given the line. Pre-registration

Committed and pushed **before** the script exists and before any outcome has been read
beside a price or a candidate. The script is `research/residual_given_line.py`; it is
written after this file and must implement what is below. Nothing here is edited after
the first run; a correction is appended at the bottom, dated, labelled post hoc where it
is, with what had been seen.

Unit c-41 (track C), 2026-10-08. Branch `c-41-residual-given-line`, cut from
`origin/main` (`d9dd3ba`) as the brief says. `market_log.db` is opened `mode=ro` only.
No request to any API is made.

## The question

Take the line as given. The forecast is the book's own de-vigged probability at its own
line. The **residual** of a rung is `r = y - p`, where `y` is 1 if the over cleared and
`p` is that probability. The question is whether any observable known when the line is
read predicts `r`. The null is that the close is efficient on this market: every
coefficient is zero.

**No line-blind model is built.** No distribution is fitted to a player's outcomes.
Two candidates use the player's own past yards as a *regressor of the residual*; neither
produces a probability on its own, and neither is ever compared with the line as a
forecast.

## What was done before this file

- Read c-37's pre-registration, findings and script from `origin/c-37-yards-markets`
  (not merged to `origin/main`; nothing of it is imported here).
- Listed the schema of `market_log.db` (`mode=ro`) and **counted** rows: closes per
  season and stat, how many carry `p_bench` / `p_all` / `dispersion`, `n_all` against
  `n_bench`, the `lead_min` range, and that `nfl_games.spread_line` / `total_line` are
  non-null for every 2023-2025 game.

      outcome_close JOIN outcomes, stat = receiving_yards, side = over, lead_min <= 15
      season   rungs    p_bench present   of those, n_all > n_bench
      2023     21,515    5,589             4,628
      2024     23,389    6,744             4,910
      2025     11,828    7,578             4,585
      lead_min on the p_bench rungs: 14.23 to 14.38 minutes in every season

- **No settlement has been computed, no outcome has been joined to a price, and no
  candidate has been computed.** The only outcome-bearing facts I hold are c-37's
  published aggregates: on its bench rungs the close's Brier is 0.2498 against 0.2500 for
  a constant, 99.5% of closes are inside [0.45, 0.55], and the realized over rate is
  0.479. Those shaped the framing (the residual is essentially `y - 0.5`) and are the
  reason the intercept is not a candidate (below).

## Scope

- **Market**: NFL player receiving yards. **Side**: over. **Seasons**: 2023, 2024, 2025,
  regular season only. **Venue**: US sportsbooks through the Odds API archive; the line's
  probability is `outcome_close.p_bench`, the de-vigged median of DraftKings / FanDuel /
  BetMGM at that exact line (often one book - c-37 measured 12,432 of 18,666). No
  Pinnacle. **Instant**: the close snapshot, `lead_min <= 15` (measured: about 14.3
  minutes before kickoff).
- **Rows** (`RB`, the primary population, c-37's): rungs with `p_bench` present, settled
  over or under through `jobs.settle_outcomes.settle_one` (the shared rule: a stat row
  settles; no row with offensive snaps settles at 0; void, push and unsettled are
  dropped and counted), player position WR / TE / RB.
- c-37 took position from a frame that is not on this branch. Here position is the
  `position` on the player's most recent `nfl_player_week` row with kickoff before this
  game, else `player_xwalk.position`. **Reconciliation check, registered**: c-37 reported
  18,666 rungs, 10,016 player-games, 814 games. The run prints all three beside its own.
  If the rung count differs by more than 2% the report says the populations are not the
  same and does not call the two units comparable row for row.
- **`RM`** (one claim per player-game): among a player-game's `RB` rungs, the rung with
  the largest `n_bench`; ties to the largest `n_all`, then the lowest line. Scored
  against `p_bench`. (c-37's `RM` used `p_all`; this one stays on the bench price so the
  forecast is the same object in both populations. Stated as a difference.)

## The candidates — closed, six

Every one is computable from rows timestamped before the kickoff of the game in
question, or from the close snapshot itself. "Prior games" means the player's played
regular-season games (a `nfl_player_week` row, newest `data_version`) with **kickoff
strictly before this kickoff**, in seasons `S-1` and `S`. The script refuses (raises) if
a feature row carries a kickoff at or after the target's.

| | name | definition | the hypothesis it encodes |
|---|---|---|---|
| C1 | `form_gap` | `(ybar - line) / (line + 10)`; `ybar` = mean receiving yards over prior games, weight 0.5 on season `S-1`. Needs at least 2 prior games | the line under- or over-reacts to the player's own level |
| C2 | `last_game` | `(y_last - line) / (line + 10)`; `y_last` = receiving yards in the single most recent prior game of season `S` | recency: the line over-reacts to the last game |
| C3 | `book_gap` | `p_all - p_bench` at the same rung | books outside the bench carry information the bench price lacks |
| C4 | `line_pos` | `(line - Lbar) / (Lbar + 10)`; `Lbar` = the `n_all`-weighted mean line over **every** over rung quoted for that player-game at the close (all books, `p_all` present) | a bench line off the all-book consensus line is the stale one |
| C5 | `team_total` | the player's team's implied points, `total_line / 2 -+ spread_line / 2` (`spread_line` is positive when the home team is favoured). Team = the team on the player's most recent prior game if it is in this game, else `nfl_roster_week` for the week, else missing | the prop line under-prices the scoring environment |
| C6 | `log_line` | `ln(line)` | the over bias varies with the level of the line |

Common treatment, fixed now:

- A candidate is **standardised on the training rows only** (mean and sd of its
  non-missing values), then winsorised at +/-3. A missing value is 0 after
  standardisation, i.e. no adjustment. The missing count is printed per candidate.
- `+10` in C1, C2 and C4 is a floor so a 2.5-yard line does not produce a ratio of 20. It
  is a constant chosen here and not tuned.
- **Declared softness of C5.** `nfl_games.total_line` and `spread_line` are nflverse's
  closing numbers and carry no timestamp, so C5 is not provably as-of a read 14 minutes
  before kickoff. It is admitted on the assumption that a game line moves little in its
  last 14 minutes - **an assumption, not a measurement**. If C5 is the only candidate to
  pass, the verdict is "passes on an input that is not timestamped" and the answer to
  the unit is still **no** until it is re-derived from the timestamped game rungs.
- Position is **not** a candidate and **not** a cut.

**The intercept is not a candidate.** The mean residual is the over bias, already
published for this market (CLAUDE.md: receiving yards -0.8pp inside the band; c-37:
realized 0.479 against 0.50). It is reported as arm `close + a`, descriptively, outside
the family and outside the verdict, so that no candidate can take credit for it: a
candidate's adjustment is `b * z` with **no intercept added**.

## The test

**Coefficient (in-sample, all three seasons).** Per candidate, ordinary least squares of
`r` on `z` with an intercept, `z` standardised on the rows in the regression. Estimate
`b`, in probability per standard deviation of the candidate. Interval and p-value from a
**game-block bootstrap**, 2,000 draws, seed 41: `p = 2 * (1 - Phi(|b| / SE_boot))`. A
bootstrap SE below `1e-12` enters at p = 1 (compared against a tolerance, not `== 0`);
an estimate resting on fewer than 5 games enters at p = 1.

**Cuts.** `RB` pooled (the one the verdict reads), `RB` per season (3), `RM` pooled.
5 cuts x 6 candidates = **30 coefficient tests**.

**Walk-forward Brier (out of sample).** For target season `T` in (2024, 2025): fit
`r = a + b z` on all rows of seasons `< T` (2023 for 2024; 2023-2024 for 2025), with `z`
standardised on those training rows. Forecast on `T`: `q = clip(p + b_T * z, 0.01,
0.99)`. Statistic: `Brier(q) - Brier(p)` on the pooled 2024 + 2025 test rows, game-block
bootstrap as above, the same draws for both arms (it is one contrast, bootstrapped as
one quantity). On `RB` and on `RM`: **12 Brier tests**. The script refuses if any
training row's season is `>= T`.

**Descriptive arms, reported and in the specification count but outside the verdict:**
`close + a` (intercept only) and `close + joint` (all six in one regression, with
intercept fitted and not added), each on `RB` and `RM`: 4 Brier tests.

**Correction.** One Holm family over the 30 + 12 = **42 registered tests**. "Excludes
zero after the correction" means Holm-adjusted p < 0.05. Beside each estimate the run
prints the unadjusted 95% interval and the Bonferroni interval at 0.05 / 42
(`est +/- 3.24 SE`).

**Specifications tried: 46** (42 registered + 4 descriptive). One run. If the script is
re-run for a bug, the report says how many times and what each run showed.

## Success — both, or the answer is no

A candidate **passes** only if, on `RB`:

1. its pooled coefficient has Holm-adjusted p < 0.05 **and** carries the same sign in
   all three seasons; **and**
2. its walk-forward `Brier(q) - Brier(p)` is negative, its 95% interval excludes zero,
   **and** the improvement is larger than the realized MDE (2.8 x bootstrap SE of that
   difference).

Anything else is **no**. If exactly one condition holds the report says which and still
says no. `RM` and the per-season cuts are read as consistency checks; they cannot rescue
a candidate that fails on `RB` and nothing found in a cut is promoted.

If no candidate passes, the result is: **nothing on this list predicts the residual of
the receiving-yards bench close, 2023-2025, at the MDE below** - scoped to these six,
this venue, this instant. It is not "the close is efficient".

## MDE, computed and written before the run

From counts only (c-37's 10,016 player-games on 814 games; test seasons hold
14,322 / 19,911 = 72% of the bench rungs, so about 7,200 test player-games). Rungs of
one player-game share one outcome and are treated as one observation; `sd(r)` is 0.50
because the price is 0.50.

| test | expected SE | MDE at 2.8 SE (single interval) | MDE at the Holm worst case, 4.08 SE |
|---|---|---|---|
| coefficient, `RB` pooled | 0.50 / sqrt(10,016) = **0.0050** | **0.0140** per sd | **0.0204** per sd |
| coefficient, `RB` one season (~3,300 player-games) | 0.0087 | 0.0244 | 0.0355 |
| coefficient, `RM` pooled | 0.0050 | 0.0140 | 0.0204 |

**Brier.** For a true standardised coefficient `b`, adding `b z` improves Brier by about
`b^2`, and the per-observation contrast has sd about `|b|`, so
`SE = |b| / sqrt(7,200) = |b| / 84.9`. The improvement clears 2.8 SE only when
`b^2 > 2.8 |b| / 84.9`, i.e. **`|b| > 0.033`**, where the improvement is **0.0011**.

So the binding bar is the Brier one: **a true effect smaller than 3.3 points of over
probability per standard deviation of a candidate cannot pass this unit**, even though
the coefficient test alone could detect 2.0. An effect between 0.020 and 0.033 would
show as "coefficient excludes zero, Brier does not clear its MDE" and the answer would be
no; that outcome is named here so it is not re-read afterwards as a near miss.

The run prints the realized SE and MDE of every test. If a realized pooled coefficient
SE exceeds **0.0100** (twice the expectation) that test is reported **under-powered**,
whatever its interval says.

**Expectation, recorded so it can be held against the result:** C4 (`line_pos`) is the
one I expect to be non-zero - c-37 measured books sitting on different lines, and brief
023 found cross-book prop disagreements worth about 1pp. I expect C1, C2, C5 and C6 at
zero and C3 small. I expect no candidate to clear the Brier bar.

## What a smoke run may and may not touch

The script may be exercised on the synthetic fixtures in
`tests/test_residual_given_line.py` and with `--dry`, which loads rows and computes
candidates and prints **counts and missing rates only** - no settlement is joined, no
residual formed. The first execution that forms a residual on real rows is the
registered run. If it crashes, the crash is fixed and reported.

## What this cannot show

- Nothing about receptions, rush attempts, rushing yards, or any market but receiving
  yards; nothing about 2026, the open, an exchange, or a price other than a US bench
  close about 14 minutes before kickoff.
- Nothing about tradeability. No vig, limit or execution is modelled; a residual
  predictable at 1-2pp sits inside the ~2.3pp half-overround of a book prop.
- A linear term in six named variables. A non-linear or interacted effect is not tested.
- Whether the close is efficient. Six nulls are six nulls.

Everything printed and committed is an aggregate or an interval. Per-rung rows go to a
scratch directory and are never committed.

## Addendum 1 — 2026-10-08, written with the script and BEFORE any residual was formed

The script has been executed twice: on the synthetic fixtures (11 tests) and once with
`--dry`, which printed counts and missing rates only - no settlement was joined and no
outcome read. Nothing above is edited. What `--dry` showed:

    bench rungs 19,911; dropped: not regular season 1,151, position 75
    RB before settlement: 18,685 rungs, 10,030 player-games, 814 games
    (c-37 after settlement: 18,666 / 10,016 / 814)
    missing: form_gap 269, last_game 1,200, team_total 327, the other three 0

Definitions that writing the code showed to be under-specified:

1. **A tie is a push and is dropped.** `push_possible` is 0 on every receiving-yards
   outcome row, and `core.settlement.resolve` grades `actual == line` as OVER when that
   flag is 0. On an integer line that would score a tie as a cleared over. A rung with
   `actual == line` is dropped and counted (`drop_push_on_line`), as c-37 did.
2. **Position** is the position on the player's most recent prior game *in the two-season
   window* (seasons `S-1`, `S`), else `player_xwalk.position`.
3. **Prior games are regular-season games only**, so C1 and C2 do not see a playoff game.
4. **Each coefficient test standardises its candidate on the rows of its own cut** (a
   per-season test on that season's rows). The walk-forward standardises on training
   rows only, as registered.
5. **The 95% interval is the bootstrap percentile interval; the p-value is from
   `est / SE_boot`**, as registered. The Brier condition "interval excludes zero" reads
   the percentile interval's upper end.
6. **The joint arm and `close + a` are fitted by the same walk-forward** as the
   candidates; the joint arm fits an intercept and does not add it.
7. **Under-powered** is flagged per candidate from its `RB` pooled coefficient SE
   against 0.0100.
