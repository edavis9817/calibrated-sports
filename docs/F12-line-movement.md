# F12 — is there a line-movement dataset here, and does a move overreact?

Track F, unit f-12. Research. Scripts:

    python -m research.f12_extract            --market-log <STORAGE_DIR>/market_log.db --cfb-db <STORAGE_DIR>/cfb.db --out D:/temp/f12/extract.db
    python -m research.f12_movement_inventory --extract D:/temp/f12/extract.db --raw D:/calibrated-sports/data/raw --out <json>
    python -m research.f12_movement_test      --extract D:/temp/f12/extract.db --out <json>

`f12_extract` is the only thing that opens a live store, `mode=ro`, once. Everything
else reads the scratch copy.

## 1. Inventory (step 1 of the brief) — measured 2026-09-24 00:15 UTC

| source | series per game/market | span | verdict |
|---|---|---|---|
| nflverse `nfl_games`, 2021-2025 | **1** version per game | — | no movement: one value, ingested after the season |
| nflverse `nfl_games`, 2026 | 15 daily versions per game; 54 of 272 spreads and 56 totals changed | 09-09 .. 09-23 | a movement series at DAY resolution only, and see the overwrite below |
| nflverse raw `games.parquet` | 67 distinct contents recorded in `nflverse_versions`, **15 on disk** | — | **52 same-day versions overwritten** (path is per day; `os.replace`) |
| Odds API live (`market_log.db`, source `live`), game lines | kicked games: median 54 pre-kickoff snapshot instants (p10 30, max 91) | T-7d+ .. T-5m | a real series, **32 games** (2026 wk 1-2) |
| Odds API live, props | 9 pre-kickoff snapshots per game (T-3d .. T-5m), 31,558 book-markets | T-3d .. T-5m | a real series, **32 games** |
| Odds API historical (`oddsapi_historical`), spreads / totals | median 10 / 9 pre-kickoff snapshots per game, earliest median 1,410 h out | whole 2023-25 seasons | **a real series, 855 games, 17 books** |
| Odds API historical, props | **1** snapshot per game (T-10m) | — | no movement: closes only |
| Kalshi live, KXNFLSPREAD / TOTAL / GAME | median 1,138 / 1,096 / 997 distinct instants per market, span ~93 h; gap p50 ~5 min, p10 ~11 s | 2026 only | dense, **32 games** |
| Kalshi live, KXNFLREC / RSHATT | median 445 / 444 instants, span ~73 h | 2026 only | dense, 2 weeks |
| `cfb_game_lines` (CFBD) | 1 row per (game, provider); **0 of 38,729 read before kickoff**; no timestamps | 2013-2026 | no timed series — BUT 8,556 rows carry CFBD's `spread_open` beside its last `spread` (6,860 moved), Bovada 2019-26, ESPN Bet 2023-25, DraftKings 2023-26 |
| CFB `--odds-forward` (`cfb_odds_quotes`) | 17 captured snapshots, **17 distinct files retained**, median 10 pre-kickoff snapshots per event | 09-17 .. 09-20 (one week) | **retains successive reads**; it does not overwrite |

The brief's question "does `--odds-forward` overwrite?": **no.** `store_rows` deletes
only the rows of the raw file being re-parsed, keyed on `src_file_id`; 17 captures
are 17 distinct files and 17 distinct `fetched_ts` in `cfb_odds_quotes`. The overwrite
that does exist is elsewhere, in the nflverse games archive (table above).

The brief's "`read_at`" column does not exist in `market_log.db`; the capture time is
`quotes.ts` and the write time `quotes.ingest_ts`.

## 2. Pre-registration — written and committed BEFORE any outcome was joined to a move

Nothing below was chosen after seeing a coefficient. The only data read before this
was written is the inventory above, which contains no outcome.

### Primary — P: NFL 2023-2025 game lines, Odds API historical featured snapshots

Population: every event in `oddsapi_historical` spreads/totals with a final score in
`nfl_games` (all game types). Kickoff = `nfl_games.kickoff_ts`.

- **Snapshot** = one distinct `ts`. Only snapshots with `ts < kickoff` are used.
- **Close** = the latest snapshot with lead in (0, 1 h].
- **Open** = the snapshot with lead in [120 h, 504 h] (5-21 days) closest to 168 h.
- **Late** = the snapshot with lead in [12 h, 48 h] closest to 24 h.
- Per book, the home team's spread `h` (home covers iff margin + h > 0) and the total
  `T` (over line). One line per book per snapshot — verified: no book carries two.
- **Move** Δ is computed per book present at BOTH endpoints, then the median across
  those books; ≥ 2 common books required. Book composition therefore cannot create
  a move. The closing line is the median across every book at the close snapshot.
- Spread, in expected-home-margin units: m = −h. Δ = m_close − m_from.
  Residual e = (home − away) − m_close.
- Total: Δ = T_close − T_from. Residual e = (home + away) − T_close.
- Under efficiency E[e | Δ] = 0. **Overreaction is a NEGATIVE coefficient.**

Estimates (all intervals: 2,000-draw bootstrap resampling season-weeks as clusters,
percentile 95%; z = estimate / bootstrap SE):

1. OLS slope b in e = a + b·Δ — 2 markets × 2 horizons (open→close, late→close) = 4.
2. Band means of sign(Δ)·e on the open→close horizon, bands of |Δ| in points:
   `(0, 1]`, `(1, 2.5]`, `(2.5, ∞)` — 2 markets × 3 = 6. (Δ = 0 is reported as a
   count and a mean e, not a test: it has no direction.)
3. Band cover rate: share of non-push games in which the side the line moved TOWARD
   covers the CLOSE, same bands, open→close — reported, not counted as extra tests
   (it is a transform of 2).

**Test count: 10.** Read at Bonferroni |z| > 2.81 (α = 0.05 / 10) as well as nominal.
A null is reported as a null.

### Secondary — S: CFB, CFBD open vs last (no timestamps)

Rows of `cfb_game_lines` with both `spread_open` and `spread` (and separately both
totals), games with final scores in `cfb_games` (current row, `valid_to_ts IS NULL`).
One provider per game, fixed order declared now: **DraftKings, then Bovada, then ESPN
Bet.** CFBD sign: negative = home favoured, so h = `spread`. Same Δ, e, slope and
band definitions as P, with a single horizon (open → last). Clusters = season-week.
**Tests: 1 slope + 3 bands per market = 8.**

Caveats fixed in advance: CFBD's "last" value is read after the game and carries no
time; c-15 matched it to the last pre-kickoff Odds API quote on 84 of 92 moved lines
in ONE week (2026 wk 3) and to an in-game line on 0. "Open" has no stated time at
all. S is therefore evidence about CFBD's open→last pair, not about a timed move.

### Declined in advance, and why

- **NFL 2026 props (Odds API live) and Kalshi 2026**: 32 games. With game-clustered
  intervals the effective sample is 32; the P design over 855 games is the first
  thing to learn from. Also, live Odds API props key on player NAME
  (`event|market|Name|side|line`) and no mapping from those to settled outcomes exists
  for the live rows. Not run.
- **Any slice by player, stat, position or team.** Not run, by design.
- **Prior-performance filters.** Not run. Prior performance may enter only as a split
  on a pre-move variable, and no such split is registered here.

## 3. Results — run after the registration above was committed (d528d72)

`python -m research.f12_movement_test`, seed 20260924, 2,000 cluster draws. Output kept at
`_relay/reports/f-12-movement-test-output.txt` and `.json`. Units: points.
Negative = overreaction.

### P — NFL 2023-2025, Odds API featured snapshots

| market / horizon | games | season-weeks | slope b [95%] | z | MDE (80%) |
|---|---|---|---|---|---|
| spread, open (median lead 168 h) → close (median 0.24 h) | 781 | 59 | **+0.263 [−0.172, +0.642]** | +1.26 | 0.58 |
| spread, late (24 h) → close | 264 | 60 | +0.125 [−3.638, +2.186] | +0.09 | 4.07 |
| total, open → close | 780 | 59 | **−0.018 [−0.505, +0.494]** | −0.07 | 0.72 |
| total, late → close | 264 | 60 | −1.275 [−3.391, +0.960] | −1.17 | 3.04 |

Band means of sign(Δ)·e, open → close:

| band \|Δ\| | spread n | spread mean [95%] | total n | total mean [95%] |
|---|---|---|---|---|
| (0, 1] | 312 | −0.187 [−1.537, +1.232] | 270 | +0.137 [−1.021, +1.307] |
| (1, 2.5] | 199 | −0.456 [−2.074, +1.092] | 270 | −0.353 [−1.711, +0.974] |
| (2.5, ∞) | 122 | +2.004 [−0.096, +4.006] | 146 | −0.060 [−2.763, +2.787] |

Share of games in which the side the line moved TOWARD covered the close: 0.448 to
0.538 in every band, and no band excludes 0.5.

**0 of 10 registered tests survive Bonferroni; 0 of 10 exclude zero nominally.**
The one estimate near the edge is the spread band |Δ| > 2.5, at +2.0 points, z = +1.89.
Its sign is the OPPOSITE of overreaction: large moves under-shot, if anything.

**What the null can rule out.** On the open → close spread, a coefficient more
negative than −0.17 is outside the interval: a market that moved a line 3 points
and was wrong by more than ~0.5 of those points in the reverting direction would
have shown up. A smaller overreaction would not: the MDE is 0.58 points per point.
Totals are weaker (lower bound −0.51). The late horizon is uninformative (MDE 3-4),
because only 264 of 855 games have a snapshot 12-48 h out; that is a property of
the backfill's slot schedule (T-10 min of each kickoff slot), not a choice.

### S — CFB, CFBD open → last (untimed)

| market | games | season-weeks | slope b [95%] | z |
|---|---|---|---|---|
| spread | 4,644 | 86 | −0.008 [−0.192, +0.176] | −0.08 |
| total | 4,648 | 86 | +0.057 [−0.129, +0.256] | +0.58 |

Providers used, spread: DraftKings 2,388, Bovada 2,199, ESPN Bet 57.

Bands: one of 8 excludes zero nominally, the spread band |Δ| ∈ (0, 1] at
−0.837 [−1.627, −0.070], z = −2.10. It does **not** survive Bonferroni for 8
(|z| > 2.73). Across 18 tests, ~0.9 nominal exclusions are expected by chance. It
is recorded, not believed. Every other band interval contains zero.

### Descriptive, not tested, not registered

Mean residual against the CLOSE over all games: NFL spreads +0.79 points to the home
side, NFL totals +1.36 points to the over (2023-25, n ≈ 780). These are reported
because they print. They are not part of the registration, carry no interval here,
and are not findings.

### Changed after the first run, and why

The cover-rate column first printed z against 0 rather than 0.5 (z = +15.97 for a rate
of 0.505). That column is a registered REPORT, not a registered test. Fixed to test
against 0.5. No estimate, window, band or test moved; the re-run reproduces every
point estimate to the printed precision under the same seed.

### Correction to section 2

The "Declined" paragraph says no mapping from live Odds API prop names to settled
outcomes exists. **That was not checked.** `market_outcome` may map some of them. The
decline stands on the 32-game sample alone.

## 4. What does not exist, and the capture that would make it

What the stores CANNOT answer:

1. **Player props, historically.** 2023-25 props are ONE snapshot per game (T-10 min).
   There is no prop movement before 2026 at any price we have paid.
2. **Player props, 2026.** The logger DOES capture them: 10 scheduled Odds API snapshots
   per game (`ODDS_SNAPSHOTS_MIN=10080,4320,1440,720,360,180,90,45,20,5` in the
   production `.env`), 9 of which land once props are posted. Measured: 32 games, 9
   pre-kickoff snapshots each, 31,558 book-markets. **These rows are `source='live'`
   and `prune_quotes` deletes them 14 days after ingestion.** It ran at 2026-09-24
   00:31 UTC, 16 minutes after this unit's extract, and removed 129,596 live quotes.
   The raw payloads are rotated to R2 at 7 days (190 `oddsapi` shards 09-09..09-16 are
   `remote` in `raw_shards`), so the series is recoverable by re-parse. The store
   copy is not kept.
3. **nflverse game lines at better than daily resolution.** `jobs/ingest_nflverse`
   archives `games.parquet` to `raw/nflverse/<day>/games.parquet` and replaces the
   file on each same-day change. 52 of 67 distinct contents recorded in
   `nflverse_versions` are no longer on disk (4-7 changes a day). What they held is
   unknowable now. For line movement it matters little, because the Odds API series is
   better. For invariant 2 it is a gap: the manifest records hashes whose bytes are gone.
4. **A CFB open.** `--odds-forward` fires at the first kickoff hour of each slate, so
   the earliest CFB snapshot is Thursday/Friday. CFB lines open Sunday/Monday.

The spec, in order of value per credit:

| # | what | markets | cadence | open / close | cost | storage |
|---|---|---|---|---|---|---|
| 1 | Keep NFL Odds API live snapshots out of the prune | the 16 already captured | as now | open = earliest post-listing snapshot (T-72 h for props), close = T-5 min | **0 credits** | measured 12.1 MB raw for 15 days (~0.8 MB/day); store rows ≈ 19k per game for props → ~5.2 M rows for 272 games (extrapolated, not measured) |
| 2 | Archive nflverse games per CONTENT, not per day | `games.parquet` | as now | — | 0 | ~0.5 MB per version × ~4/day ≈ 2 MB/day |
| 3 | One CFB "open" bulk snapshot per week, Monday ~16:00 UTC | h2h, spreads, totals | weekly | open = Monday, close = existing kickoff-hour snapshot | 3 credits/week, ~45/season | negligible |
| 4 | Historical NFL prop opens | receptions, rush attempts | one kickoff −48 h snapshot | open = T-48 h, close = held | ~17,100 credits for 3 seasons (CLAUDE.md figure, not re-measured) | — |

**Earliest honest answer dates if (1) starts this week.**
- NFL props: the effective sample is games. The P spread design needed ~780 games for an
  MDE of 0.58 points per point. Props carry many rungs per game, but rungs within a game
  are not independent. A full regular season is ~272 games. **The earliest read is after
  week 18, around 2027-01-05.** Treat it as a first look with a wide interval, not a
  test at P's power.
- CFB with a timed open (3): one season of FBS slates, ~700 games. **Earliest read:
  2027-01, after bowls.** S already answers the untimed version today on 4,644 games.
