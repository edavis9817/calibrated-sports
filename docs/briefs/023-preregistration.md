# Brief 023 — the three gaps: pre-registration

Committed before any Part 1, 2 or 3 number is computed. The brief 022 grading
bars apply to anything that looks positive: a mechanism naming the other side,
replication, net of cost at real depth, and persistence measured in minutes.

## Inventory (done before this file; the plans depend on it)

- **Odds API history on disk.** `quotes` with `source='oddsapi_historical'`
  holds 907,421 rows (2023 344,537; 2024 285,383; 2025 260,726).
  - **Props:** exactly ONE snapshot per event, the close. That is 280-285
    events per season, 7-10 books, markets receptions, reception yards, rush
    attempts, tackles+assists and sacks.
  - **Featured `h2h`/`spreads`/`totals`:** 9-11 snapshots per event, 10-16 books.
  - **Raw archive** `raw/oddsapi_historical` is complete and readable: 1,707
    records, 413 snapshot timestamps, 82,214 bookmaker entries carrying
    `last_update`.
  - `quotes.source_ts` is NULL on historical rows; per-book freshness must be
    read from raw.
- **No opening prop line exists on disk.** The 007 backfill bought closes only.
- **Model.**
  - `fit_player_stat(..., prior_seasons)` accepts any window, and the as-of join
    uses only games with `kickoff_ts < as_of`.
  - Module constants: `SHRINK_GAMES_DEFAULT=6`, `MEAN_DRIFT_VAR`,
    `SHRINK_GAMES_VMR=12`, `TEAM_CHANGE_KEEP=0.60`, `COACH_CHANGE_KEEP=0.70`.
  - `research/shrinkage.py` refits by season pair.
- **Inactives.** nflverse is mirrored without an injuries dataset, and nflverse
  injuries carry no game-day inactive announcement time. Nothing on disk
  timestamps an inactive.

## Part 1 — model against book props, walk-forward

- **Markets:** `player_receptions` and `player_rush_attempts`, the model's two
  validated count stats with Kalshi counterparts. Side: over. Seasons T = 2023,
  2024, 2025, each reported separately; nothing is pooled across seasons.
- **Walk-forward rule, enforced in code.**
  - Every FITTED constant used to predict season T is fitted only on seasons
    <= T-1. The harness asserts it: each constant carries the seasons it was fit
    on, and prediction refuses if any is >= T.
  - Player features are as-of: only games with `kickoff_ts < kickoff` of the
    predicted game. For season T that includes weeks of T already played, which
    is exactly what the live model sees. Features are data, not fitted constants.
- **Constant provenance decides feasibility.** For each module constant:
  - fitted on data including any season >= 2023 -> refit per window with the
    same procedure restricted to <= T-1;
  - chosen before any 2023-25 data was examined (documented arithmetic or a
    prior) -> kept, with the evidence cited;
  - **provenance unknown and not refittable -> Part 1 STOPS**, and that is
    reported as the finding.
  - `MEAN_DRIFT_VAR` is refit from season pair (T-2, T-1) with
    `research/shrinkage.py`'s own measurement.
- **Close benchmark.** `outcome_close.p_bench` (the de-vigged close at lead
  <= 15 min), joined to the model's probability for the same outcome_id.
  Common set only.
- **Open benchmark.** NOT ON DISK. It is costed, not bought: credits for one
  early snapshot per event across 3 seasons, for 2 markets and for all 5.
  **No spend.**
- **Scoring**, per season:
  - Brier and log loss (p clipped to [1e-4, 1-1e-4]) for model and close;
  - Brier(model) - Brier(close) and log-loss difference, 2,000-draw game block
    bootstrap;
  - MDE = 2.8 x bootstrap SE.
  - Reported per stat within season as a secondary line, counted as tests.
- **Stop rule.** Beating neither benchmark on every season closes Part 1
  permanently. Here that means: no season's Brier(model) - Brier(close) interval
  lies below zero. With no open benchmark on disk, the open half of that rule
  cannot be applied until it is bought.

## Part 2 — book versus book (no forecast)

- **Populations.**
  - Historical `raw/oddsapi_historical`: featured spreads/totals at every
    snapshot, and props at their single close snapshot.
  - 2026 week-1 live `raw/oddsapi` `odds_bulk` and props snapshots.
  - **Pre-kickoff only:** fetch time < kickoff - 5 min, kickoff from nflverse.
- **Freshness (2a).**
  - A book's quote is usable at a fetch only if `fetch_ts - last_update <=
    600s` (market-level `last_update`, falling back to bookmaker-level).
  - A PAIR of books is comparable only if `|last_update_A - last_update_B| <=
    300s`. **Primary window 300s.**
  - The discard count is reported at every step.
  - The curve at 60s / 900s is reported as a curve.
- **Arbitrage (2b).**
  - Same event, same market, same point (spreads: team at -L against the
    opponent at +L; totals: over/under at L; props: player, stat, over/under at
    L), freshness-eligible.
  - Best over price from book A and best under price from a DIFFERENT fresh book
    B.
  - Arb iff implied(over_A) + implied(under_B) < 1; size = 1 - sum, in pp.
  - Report the frequency per (event, market, point, fetch), the size
    distribution, and which book pairs generate it.
- **Middles (2b).**
  - Spreads: team X at -L1 at A, opponent at +L2 at B, L2 > L1.
  - Totals: over L1 at A, under L2 at B, L2 > L1.
  - Props: over L1 at A, under L2 at B, L2 > L1.
  - Width = L2 - L1.
  - Hit probability from the EMPIRICAL distribution, never a normal:
    - spreads use nflverse 1999-2025 favourite margins of games whose closing
      `spread_line` is within +-1 of the consensus;
    - totals use combined scores of games with `total_line` within +-1.5;
    - props use the empirical distribution of the stat among outcome_close
      outcomes with the same stat and the same consensus line, from seasons
      OTHER than the one tested.
  - EV per unit staked on both legs, after each leg's price, is reported. The
    realized hit rate on the actual instances is reported beside it.
- **Persistence.** Survival of the same arb or middle to the next snapshot of
  that event, with the snapshot spacing stated as the resolution floor: hours
  historically, minutes in 2026.
- **Tests:**
  - arb rate (Wilson), mean arb size and middle EV, each with a game block
    bootstrap;
  - by market class (spreads, totals, each prop stat) x population (hist, 2026).
  - Counted. Positives must meet the 022 bars.
- **Ceiling (2c).** Stated as a percentage with the constraint written beside
  it: $50-500 prop limits, and account limiting for arbing. Never annualised.

## Part 3 — the inactives reaction

- **3a.** Confirm there is no announcement timestamp on disk. Checking the
  nflverse injuries schema is a free GitHub download: archive it raw under a
  venue directory no other process writes (`nflverse_023`), with a separate
  manifest DB, never the logger's `nflverse` directory. No other network.
- **On-disk proxy (week 1), clearly labelled a proxy.**
  - **Inactive set:** players with a week-1 Kalshi KXNFLREC/KXNFLRSHATT market
    and zero offensive snaps in `nfl_snap_counts`, or no `nfl_player_week` row.
  - **Proxy t0:** the first as-of instant after the player's team's T-120 min at
    which his own highest-priced rung's mid falls below 0.10, or at which his
    markets stop quoting.
  - **Direct baseline:** because t0 is DEFINED by the direct line, direct
    reaction speed is not measurable here. Report only its shape: the mid at
    t0-5 min, t0 and t0+5 min.
  - **Second-order:** the same team's other KXNFLREC/KXNFLRSHATT players,
    grouped by position group (WR/TE for receptions, RB for rush attempts), each
    rung.
    - Measures: mid and spread at t0-5 min; the mid path at the logger's
      resolution (hot tier 15s pre-kickoff; say so); the move at t0+1, 5, 15 and
      30 min; time to reach within 1c of the t0+30 min level; touch size from
      market_depth (60s) during the transition.
  - **Tests:** mean |move| at each horizon, game block bootstrap, counted.
- **Bar (3c).** The second-order move must exceed 3.6pp, take > 60s to happen,
  and carry real size at the touch.
- **Forward spec** (`docs/briefs/023-inactives-capture-spec.md`) for Sunday
  09-20: a timestamped inactive source and what the logger already captures.
  **Spec only, not built.**
