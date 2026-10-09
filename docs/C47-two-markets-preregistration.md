# C47 — c-41's frame on receptions and rush attempts. Pre-registration

Committed and pushed **before** the script exists and before any outcome has been read
beside a price or a candidate in either market. The script is
`research/residual_two_markets.py`; it is written after this file and must implement what
is below. Nothing here is edited after the first run; a correction is appended at the
bottom, dated, labelled post hoc where it is, with what had been seen.

Unit c-47 (track C), 2026-10-08. Branch `c-47-two-markets`, cut from
`origin/c-46-population-and-null` (`e0cf03f`), which contains
`origin/c-41-residual-given-line` (`abbda29`) and c-45's wording corrections.
`market_log.db` is opened `mode=ro` only. No request to any API is made. Zero credits.

## The question

c-41 ran one market of the three its brief framed. Its own `not_established` says the
"no" does not cover receptions or rush attempts. This unit runs those two, on c-41's
frame, and nothing else:

**Given the line, does anything in c-41's six-candidate set predict the residual of the
bench close in receptions, and in rush attempts?**

The residual of a rung is `r = y - p`: `y` is 1 if the over cleared, `p` is the book's own
de-vigged probability at its own line. The null is that no candidate predicts it. "No" is
a complete answer and the expected one. **No line-blind model is built**; nothing here
turns a player's history into a probability.

**Receiving yards is not re-run, re-worded or re-scored here.** c-41 owns it.

## What was done before this file

- Read c-41's pre-registration, script, findings and result JSON, and f-31 run 4's power
  measurement as c-45 recorded it in `docs/findings/c41-residual-given-line.md`.
- **Counted** rows in `market_log.db` (`mode=ro`), by SQL at a prompt. No settlement was
  computed, no outcome joined to a price, no candidate computed.

      outcome_close JOIN outcomes, side = over, lead_min <= 15, p_bench present
      stat            season  bench rungs  player-games  games   lead_min
      receptions      2023    3,463        3,171         283     14.27-14.37
      receptions      2024    3,613        3,279         285     14.33-14.38
      receptions      2025    4,132        3,624         284     14.23-14.38
      rush_attempts   2023    1,326        1,080         280     14.27-14.37
      rush_attempts   2024    1,615        1,305         282     14.33-14.38
      rush_attempts   2025    1,903        1,486         283     14.23-14.38

  Other counts, same query family: no bench rung in either market sits on an integer
  line; `push_possible` is 0 on every row; bench prices inside [0.45, 0.55] are 39.8% of
  receptions rungs and 76.9% of rush-attempt rungs (receiving yards: 99.3%), so here the
  price is **not** a constant 0.50; one book stands behind the bench price on 21.4% of
  receptions rungs and 46.1% of rush-attempt rungs. By `player_xwalk.position`:
  receptions WR 5,895 / RB 2,691 / TE 2,582 rungs (40 others); rush attempts RB 3,307 /
  **QB 1,485** / WR 37 / TE 15.
- The only outcome-bearing facts I hold about these two markets are published ones:
  CLAUDE.md's over bias inside the price band (rush attempts -2.4pp, receptions -0.8pp,
  2023-2025) and brief 023 (the usage model loses to this close). Neither is a candidate.

## Scope

- **Markets**: NFL player `receptions` and `rush_attempts`. **Side**: over. **Seasons**:
  2023, 2024, 2025, regular season only - **the same span as c-41 for both markets**; the
  closes for all three seasons are on disk for both (table above), so nothing is
  shortened. **Venue group**: US sportsbooks through the Odds API archive, price
  `outcome_close.p_bench` (de-vigged median of DraftKings / FanDuel / BetMGM at that exact
  line). No Pinnacle. **Instant**: the close snapshot, `lead_min <= 15` (about 14.3
  minutes before kickoff).
- **Rows** (`RB`, the primary population): rungs with `p_bench` present, a regular-season
  game, settled over or under through `jobs.settle_outcomes.settle_one` (void, push and
  unsettled dropped and counted), and `actual == line` dropped and counted (c-41 addendum
  1; no integer line exists here, so the count should be 0 and is printed).
- **Position.** Receptions: WR / TE / RB, as c-41. **Rush attempts: WR / TE / RB / QB.**
  That is a decision taken here, before any outcome: c-41's three positions were the
  receiving positions for a receiving market, and applied verbatim to rush attempts they
  would drop a third of that market's bench rungs (QB 1,485 of 4,844) from the smaller of
  the two samples. Position is derived as in c-41 (the player's most recent prior game in
  the two-season window, else `player_xwalk.position`), and is **not** a candidate and
  **not** a registered cut. One descriptive arm, outside the family and the verdict:
  rush attempts without QBs, pooled coefficients only (6 specifications).
- **`RM`**: one rung per player-game, c-41's rule (largest `n_bench`, then `n_all`, then
  the lowest line), scored against `p_bench`.

## The candidates — c-41's six, no seventh

Definitions are c-41's, with the market's own stat in place of receiving yards. "Prior
games" = the player's played regular-season games with kickoff strictly before this
kickoff, seasons `S-1` and `S`; the script raises if a feature row is not before kickoff.

| | name | definition |
|---|---|---|
| C1 | `form_gap` | `(ybar - line) / (line + 10)`; `ybar` = mean of the stat (receptions, or carries) over prior games, weight 0.5 on season `S-1`; needs 2 prior games |
| C2 | `last_game` | `(y_last - line) / (line + 10)`; the stat in the most recent prior game of season `S` |
| C3 | `book_gap` | `p_all - p_bench` at the same rung |
| C4 | `line_pos` | `(line - Lbar) / (Lbar + 10)`; `Lbar` = `n_all`-weighted mean line over every over rung quoted for the player-game at the close |
| C5 | `team_total` | the player's team's implied points from `nfl_games.total_line` / `spread_line`; team resolved as in c-41 |
| C6 | `log_line` | `ln(line)` |

- **The `+10` is kept verbatim, as a decision.** It was sized for yards (median line
  about 31); on a count line of 3 to 10 it makes C1, C2 and C4 closer to a level
  difference than to a ratio. Re-sizing it would be a new candidate definition chosen by
  me on the night, which is what "the same set" forbids. Stated so the difference in
  meaning is on the page.
- Standardised on training rows (walk-forward) or on the rows of the cut (coefficient),
  winsorised at +/-3, missing is 0 after standardisation, missing count printed.
- **C5 is declared soft, as in c-41**: nflverse's closing total and spread carry no
  timestamp. If C5 is the only candidate *detected* in a market, the verdict reads
  "detected on an input that is not timestamped" and the unit's answer for that market is
  still not "yes".
- The intercept is not a candidate. The mean residual is the published over bias; it is
  arm `close + a`, descriptive, outside the family. A candidate's adjustment is `b * z`
  with no intercept added.

## The tests

Per market, exactly c-41's tests:

- **Coefficient**: OLS of `r` on `z` with an intercept; game-block bootstrap, 2,000 draws,
  seed 47; `p = 2 * (1 - Phi(|b| / SE_boot))`; the 95% interval is the bootstrap
  percentile interval. A bootstrap SE below `1e-12` (tolerance, not `== 0`) or fewer than
  5 games enters at p = 1. Cuts: `RB` pooled, `RB` per season (3), `RM` pooled.
  5 x 6 = 30 tests.
- **Walk-forward Brier**: for `T` in (2024, 2025) fit `r = a + b z` on seasons `< T`,
  standardised on those rows; `q = clip(p + b_T z, 0.01, 0.99)`; statistic
  `Brier(q) - Brier(p)` on the pooled 2024 + 2025 rows, one contrast over shared game
  blocks. `RB` and `RM`: 12 tests. The script refuses a training row with season `>= T`.
- **Descriptive, outside the family and the verdict**: `close + a` and `close + joint` on
  `RB` and `RM` (4 per market), and the rush-attempts no-QB coefficients (6).

**One Holm family over all 84 registered tests across both markets** (2 x 42). "After
correction" means Holm-adjusted p < 0.05. Beside each estimate: the unadjusted 95%
interval and the Bonferroni interval at 0.05 / 84, `est +/- 3.434 SE`.

**Specifications tried: 98** = 84 registered + 8 descriptive arms + 6 no-QB coefficients.
The report states the number of runs and what each showed.

## The verdict — three states, per market and per candidate

Read on `RB` only. `RM` and the per-season cuts are consistency checks; nothing found in
a cut is promoted and a cut cannot rescue a candidate.

1. **DETECTED** — c-41's pass rule unchanged: the pooled coefficient has Holm-adjusted
   p < 0.05 (in the 84-test family) with the same sign in all three seasons, **and** the
   walk-forward `Brier(q) - Brier(p)` is negative, its 95% interval excludes zero, and the
   gain is larger than its realized MDE (2.8 x bootstrap SE).
2. **EXCLUDED at size d**, for d in the three pre-stated sizes **0.020, 0.033, 0.045**
   of over probability per standard deviation of the candidate — all three of:
   (a) the pooled coefficient's **Bonferroni** interval (`est +/- 3.434 SE`) lies wholly
   inside `(-d, +d)`;
   (b) the pooled coefficient test's detection probability at `d`, **written down before
   the run** (addendum 1, below, from an outcome-free simulation at the Bonferroni
   critical value), is at or above 0.80; and
   (c) the same holds on the realized SE: `d >= (3.434 + 0.842) x SE_realized`.
   The report gives the smallest of the three sizes at which a candidate is excluded.
   What is excluded is a **pooled 2023-2025 linear coefficient of that size**, nothing
   wider.
3. **UNRESOLVED** — everything else. It is reported as *unresolved*, by candidate name
   and market, with its estimate, interval and MDE. It is not a null and is not
   aggregated into a "no".

A candidate can be excluded at 0.045 and unresolved at 0.020; both are said.

**The sentence this unit may not write.** A test whose power at 0.020 is below 50% **may
not produce a sentence of the form "rules out an effect of 2 points per sd"** - and, by
rule 2(b), neither may one whose power there is below 80%. If no candidate in a market is
excluded at 0.020, the report says in those words that 2 points per sd is not ruled out
in that market.

**No ranking.** Tables list candidates in the fixed order C1-C6 and markets in the order
receptions, rush attempts. No sentence orders candidates or markets by the size of an
estimate, and no between-market difference is tested or stated: f-31 showed a
within-market ranking moves with the choice of slope.

## Power, written before the run

**From counts only** (this file). Player-games after the regular-season filter are taken
as 94% of the table above (c-41 lost 5.8% of rungs to it): about 9,450 receptions, 3,650
rush attempts. One player-game is one observation; `sd(r)` is taken as 0.50.
Power = `Phi(d / SE - z)`, `z` = 1.96 unadjusted, 3.434 at the Bonferroni bound, which a
Holm-adjusted p cannot exceed.

| coefficient test | expected SE | MDE 2.8 SE | MDE at the bound, 80% | power at 0.020 (unadj / bound) | at 0.033 | at 0.045 |
|---|---|---|---|---|---|---|
| receptions, `RB` or `RM` pooled | 0.0051 | 0.0144 | 0.0220 | 0.97 / **0.68** | 1.00 / 1.00 | 1.00 / 1.00 |
| receptions, one season | 0.0089 | 0.0249 | 0.0381 | 0.61 / 0.12 | 0.96 / 0.61 | 1.00 / 0.95 |
| rush attempts, `RB` or `RM` pooled | 0.0083 | 0.0232 | 0.0354 | 0.68 / **0.16** | 0.98 / **0.71** | 1.00 / 0.98 |
| rush attempts, one season | 0.0143 | 0.0402 | 0.0613 | 0.29 / 0.02 | 0.63 / 0.13 | 0.88 / 0.38 |

What that table already says, before a script exists:

- **Neither market can exclude 0.020.** Power at the bound is about 0.68 in receptions
  and 0.16 in rush attempts. Unless addendum 1 moves receptions above 0.80, **this unit
  will not rule out 2 points per sd in either market, whatever the estimates are.**
- 0.033 is excludable in receptions and not (0.71) in rush attempts; 0.045 in both.
- The **detection** rule is weaker than the coefficient test - f-31 measured c-41's at
  9% / 41% / 82-85% for the three sizes on about 10,000 player-games. Receptions has
  about that many; rush attempts has 39% as many. I expect the detection probability to
  be near f-31's in receptions and well below it in rush attempts.

**Addendum 1 replaces the expectation with a measurement, and is committed before the
registered run.** The script's `--power` mode loads the real rows - prices, lines,
candidates, game and player-game structure - with **no settlement joined**, and simulates
outcomes: one uniform draw `U` per player-game, a rung clears when
`U < clip(p + d z, 0.01, 0.99)`, with `z` the planted candidate standardised on the
pooled rows. For each market, each of the six candidate slots, and `d` in
(0, 0.020, 0.033, 0.045), it runs the registered tests of that candidate and records, for
**every one of the 84 tests**: its mean SE and MDE, the rate at which it is significant
unadjusted and at the Bonferroni bound, and - per candidate - the rate at which the full
DETECTED rule fires. Limits, stated now: player-games inside a game are simulated
independent (real ones share a scoring environment), the planted effect is season-stable
and exactly linear, the price is taken as the true probability, and the Bonferroni bound
stands in for Holm. The first three make the simulated power an **upper bound**; the last
makes it slightly conservative. The population is the pre-settlement one, a fraction of a
percent larger than the run's.

**Expectation, recorded:** no candidate detected in either market. Most verdicts
unresolved at 0.020; receptions candidates excluded at 0.033 unless an estimate sits
above about 0.015; rush attempts excluded only at 0.045. If any estimate is non-zero I
expect it to be `line_pos` or `team_total`, c-41's two.

## What a smoke run may and may not touch

Synthetic fixtures in `tests/test_residual_two_markets.py`; `--dry` (counts and missing
rates, no settlement); `--power` (simulated outcomes only, no settlement). The first
execution that joins a settlement to a price on real rows is the registered run. If it
crashes, the crash is fixed and reported with what each run showed.

## What this cannot show

- Nothing about receiving yards (c-41), rushing yards, 2026, the open, an exchange, or a
  price other than a US bench close about 14 minutes before kickoff.
- Nothing about tradeability; no vig, limit or execution is modelled.
- A linear term in six named variables. No interaction, no non-linearity, no variable
  off the list.
- Not that either close is efficient.
- No comparison between the two markets, or with receiving yards.

Everything printed and committed is an aggregate or an interval. Per-rung rows go to a
scratch directory and are never committed.
