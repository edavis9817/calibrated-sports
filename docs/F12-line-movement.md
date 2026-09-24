# F12 — is there a line-movement dataset here, and does a move overreact?

Track F, unit f-12. Research. Scripts:

    python -m research.f12_extract            --out D:/temp/f12/extract.db
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
