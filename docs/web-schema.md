# Web data contract — schema_version 1

The contract between `jobs/export_web.py` (this repo) and calibratedsports.com
(`calibratedsports-web`). This file is the source of truth; the site's
`lib/schema.ts` transcribes it. Changing a field's meaning or removing a field
bumps `schema_version`. Adding an optional field does not.

## Destination

`config.WEB_DATA_DIR`, read from the environment (`WEB_DATA_DIR` in `.env`),
normally `<web repo>/public/data`. **There is no default.** The export refuses
to run when it is unset, rather than guessing a path; guessing is how
`6e42f09` happened.

## Envelope — every file

```json
{
  "schema_version": 1,
  "generated_at": "2026-09-15T18:00:00Z",
  "kind": "manifest | player | team | market | research.hypotheses | research.calibration | research.execution"
}
```

The site checks `schema_version === 1` AND `kind`. Any mismatch, a missing
file or a parse failure renders the explicit **"data format changed"** state,
naming the file and both versions. It never renders a blank chart.

Conventions:
- Every timestamp is ISO-8601 UTC. `*_ts` fields are unix seconds.
- A missing value is `null`, never 0: a player with no snap data pre-2013 has
  `snaps: null`.
- Probabilities are in [0, 1]. Rates and shares are in [0, 1].

## Layout

```
public/data/
  manifest.json
  players/{id}.json        # id = nflverse gsis_id, e.g. 00-0036355
  teams/{team}.json        # team = nflverse abbreviation, e.g. BUF
  market/{id}.json         # current week only; absent when no market exists
  research/hypotheses.json
  research/calibration.json
  research/execution.json
```

**Player scope (v1).** Every player with a regular-season week of offensive
usage (`targets + carries + attempts > 0`) in 1999–current: 3,971 players.

Cloudflare Pages' free plan caps a site at 20,000 files. A static-exported
player route costs 3 (`index.html`, `index.txt` and its JSON), so all 11,484
players would not fit. Defensive players appear on team pages (roster and
defensive splits) without their own page. The paid plan raises the cap to
100,000; widening scope then is an export filter, not a redesign.

## manifest.json

```json
{
  "schema_version": 1, "generated_at": "...", "kind": "manifest",
  "current": {
    "season": 2026, "week": 2,
    "data_through": {"season": 2026, "week": 1},
    "nflverse_version": "2026-09-15",
    "stale": false,
    "stale_reason": null
  },
  "seasons": [1999, 2000, "...", 2026],
  "teams": [{"team": "BUF", "name": "Buffalo Bills"}],
  "players": [
    {"id": "00-0036355", "name": "Justin Herbert", "position": "QB", "team": "LAC",
     "first_season": 2020, "last_season": 2026, "has_market": false}
  ],
  "counts": {"players": 3971, "teams": 32, "market": 0},
  "unresolved_ids": [{"id": "...", "name": "...", "reason": "not in player_xwalk"}]
}
```

- `current.week` is the upcoming or in-progress week.
- `data_through` is the last week whose player stats are in the database.
- `stale` is true when `data_through` lags the last completed week (every game
  has a final score) — nflverse is late. `stale_reason` says which week is
  missing.
- The site shows "stats through week N" and, when stale, says so plainly.

## players/{id}.json

```json
{
  "schema_version": 1, "generated_at": "...", "kind": "player",
  "id": "00-0036355", "name": "...", "position": "WR", "team": "BUF",
  "ids": {"gsis": "...", "pfr": "...", "espn": "...", "sleeper": "...",
          "yahoo": "...", "pff": "..."},
  "aliases": ["..."],
  "seasons": [2020, 2021],
  "has_market": true,
  "games": [
    {"season": 2025, "week": 3, "season_type": "REG", "game_id": "2025_03_BUF_MIA",
     "date": "2025-09-18", "team": "BUF", "opponent": "MIA", "home": true,
     "snaps": 54, "snap_share": 0.87,
     "targets": 9, "target_share": 0.28, "receptions": 7,
     "receiving_yards": 88, "receiving_tds": 1,
     "carries": 0, "rushing_yards": 0, "rushing_tds": 0,
     "attempts": 0, "completions": 0, "passing_yards": 0, "passing_tds": 0,
     "interceptions": 0, "fumbles_lost": 0, "two_point_conversions": 0,
     "fantasy": {"ppr": 21.8, "half": 18.3, "standard": 14.8}}
  ],
  "season_totals": [
    {"season": 2025, "season_type": "REG", "games": 17,
     "targets": 0, "receptions": 0, "receiving_yards": 0, "receiving_tds": 0,
     "carries": 0, "rushing_yards": 0, "rushing_tds": 0, "attempts": 0,
     "completions": 0, "passing_yards": 0, "passing_tds": 0, "interceptions": 0,
     "fumbles_lost": 0, "two_point_conversions": 0,
     "snap_share_mean": 0.81,
     "fantasy": {"ppr": {"total": 0, "per_game": 0},
                 "half": {"total": 0, "per_game": 0},
                 "standard": {"total": 0, "per_game": 0}}}
  ],
  "career": {"games": 0, "...": "same fields as a season total, REG only"},
  "usage": [{"season": 2025, "week": 3, "snap_share": 0.87, "target_share": 0.28}],
  "fantasy_scoring": {
    "ppr":      {"rec": 1.0, "rec_yd": 0.1, "rush_yd": 0.1, "td": 6, "pass_yd": 0.04,
                 "pass_td": 4, "int": -2, "fumble_lost": -2, "two_pt": 2},
    "half":     {"rec": 0.5, "...": "otherwise as ppr"},
    "standard": {"rec": 0.0, "...": "otherwise as ppr"},
    "note": "Computed once at export. Components missing from the source are null and scored as 0, and the note says which."
  }
}
```

- `games` covers every week of every season, REG and POST, in chronological
  order.
- `snaps` and `snap_share` are `null` before 2013 (nflverse snap counts start
  then).
- `usage` is REG only, chronological, one row per week played.
- `career` is regular season only.

## teams/{team}.json

```json
{
  "schema_version": 1, "generated_at": "...", "kind": "team",
  "team": "BUF", "name": "Buffalo Bills",
  "seasons": [1999, "...", 2026],
  "schedule": [
    {"season": 2026, "week": 1, "game_type": "REG", "game_id": "2026_01_BUF_HOU",
     "date": "2026-09-13", "kickoff_ts": 1789318800, "home": false,
     "opponent": "HOU", "points_for": 36, "points_against": 31, "result": "W",
     "spread": 1.5, "total": 44.5, "coach": "...", "opponent_coach": "..."}
  ],
  "splits": [
    {"season": 2026, "season_type": "REG", "games": 1,
     "offense": {"points": 36, "passing_yards": 0, "rushing_yards": 0,
                 "receiving_yards": 0, "pass_attempts": 0, "completions": 0,
                 "passing_tds": 0, "rushing_tds": 0, "receiving_tds": 0,
                 "interceptions_thrown": 0, "targets": 0, "carries": 0},
     "defense": {"points_allowed": 31, "tackles_solo": 0, "tackles_with_assist": 0,
                 "tackle_assists": 0, "tackles_for_loss": 0, "sacks": 0,
                 "qb_hits": 0, "interceptions": 0, "pass_defended": 0,
                 "fumbles_forced": 0, "def_tds": 0, "safeties": 0}}
  ],
  "roster": [
    {"season": 2026, "id": "...", "name": "...", "position": "WR", "games": 1,
     "snap_share": 0.87, "target_share": 0.28, "carry_share": 0.0,
     "has_page": true}
  ],
  "coaches": [{"season": 2026, "head_coach": "..."}]
}
```

- `spread` is from THIS team's perspective: positive means this team is
  favoured. nflverse `spread_line` is positive when the home team is favoured,
  so the export flips it for away games.
- `result` and the points are `null` before a game is played.
- `splits` are season totals; the site divides by `games` for per-game.
- `roster` is the latest season with any player data for the team.
- `has_page` is false for players outside the v1 player scope.

## market/{id}.json

```json
{
  "schema_version": 1, "generated_at": "...", "kind": "market",
  "id": "...", "name": "...", "position": "WR", "team": "BUF",
  "season": 2026, "week": 2, "game_id": "2026_02_DET_BUF",
  "opponent": "DET", "kickoff_ts": 1789690500,
  "as_of": "2026-09-17T15:00:00Z",
  "source": {
    "venue": "kalshi",
    "method": "research/implied.py arm A: mid of each rung, no de-vig (exchange); isotonic survival fit; Gaussian copula receptions↔receiving yards; Monte Carlo",
    "n_sims": 4000
  },
  "components": [
    {"stat": "receptions", "basis": "MARKET",
     "rungs": [{"line": 3.5, "p_over": 0.62, "bid": 0.61, "ask": 0.63,
                "quote_ts": 1789680000}]},
    {"stat": "receiving_yards", "basis": "DERIVED",
     "note": "receptions × yards per catch by position"},
    {"stat": "rush_attempts", "basis": "MARKET", "rungs": []},
    {"stat": "rushing_yards", "basis": "DERIVED",
     "note": "attempts × yards per carry by position"},
    {"stat": "touchdowns", "basis": "ANCHORED",
     "note": "player TD rate scaled by the game total and spread"}
  ],
  "game_lines": {"total": 51.5, "spread": 3.0, "source": "nflverse games"},
  "distributions": {
    "ppr": {
      "cdf": [{"x": 0, "p_at_most": 0.02}],
      "thresholds": [{"points": 5, "p_at_least": 0.91}],
      "quantiles": {"q10": 0, "q25": 0, "q50": 0, "q75": 0, "q90": 0}
    },
    "half": {"...": "same shape"},
    "standard": {"...": "same shape"}
  },
  "validation": {
    "status": "The method was validated on sportsbook ladders, 2023-25 (q90 coverage 0.101 against 0.100). The Kalshi-ladder arm used here has not been independently validated.",
    "source": "CLAUDE.md, market-implied fantasy distribution"
  }
}
```

- `as_of` is the newest quote used. Only quotes strictly before kickoff are
  used.
- `cdf` covers x = 0..50 in steps of 1.
- `thresholds` are 5, 10, 15, 20, 25 and 30 points.
- A player gets a market file only when he has a receptions ladder (the
  validated scope). Basis labels are mandatory, and the page shows them.

## research/hypotheses.json

```json
{
  "schema_version": 1, "generated_at": "...", "kind": "research.hypotheses",
  "hypotheses": [
    {"id": "R01", "brief": "S00", "date": "2026-09-14",
     "question": "Does the model's week-1 entry beat the Kalshi close?",
     "verdict": "retired",
     "metric": "executable CLV at 1,000 contracts", "estimate": -13.52,
     "interval": [-17.06, -10.40], "unit": "pp", "n": 493, "games": 14,
     "why": "One sentence, plain.",
     "script": "research/clv.py"}
  ]
}
```

`verdict` is one of: `retired` (tested, no edge), `null` (tested, no effect),
`not_testable` (data does not exist), `open` (awaiting a holdout).

## research/calibration.json

```json
{
  "schema_version": 1, "generated_at": "...", "kind": "research.calibration",
  "source": "research/score.py (brief 021)",
  "population": "NFL week 1 2026, KXNFLREC + KXNFLRSHATT, common set n=682, 14 games",
  "series": [
    {"name": "model", "ece": 0.0764,
     "bins": [{"lo": 0.0, "hi": 0.1, "n": 148, "mean_forecast": 0.049,
               "realized": 0.122, "wilson": [0.078, 0.184]}]},
    {"name": "market", "ece": 0.0387, "bins": []}
  ],
  "brier": {"model": 0.1957, "market": 0.1682, "naive": 0.1924,
            "model_minus_market": {"estimate": 0.0275,
                                   "interval": [0.0117, 0.0410]}}
}
```

- Bins with n < 30 are flagged on the page as not a data point.
- Wilson intervals only.

## research/execution.json

```json
{
  "schema_version": 1, "generated_at": "...", "kind": "research.execution",
  "source": "research/sweep/h3_lifecycle.py (brief 022)",
  "series": [
    {"series": "KXNFLREC",
     "by_time_to_kickoff": [{"bucket": ">72h", "median_spread_c": 9,
                             "median_touch": 50}]}
  ],
  "spread_to_volatility": [{"series": "KXNFLREC", "ratio": 1.05}],
  "rule": "Cross game lines 1-6h before kickoff; never cross a prop in-game."
}
```

## Refresh

`python -m jobs.weekly_refresh` is the one entry point, and it logs to
`config.storage_path("logs", "weekly_refresh.log")`:

1. nflverse ingest;
2. `jobs.map_markets --venue kalshi`;
3. `jobs.export_web`;
4. `npm run check` in the web repo, as a gate;
5. commit `public/data` if anything changed;
6. push, which makes Cloudflare Pages rebuild.

When nflverse is late, the export still runs, `manifest.current.stale` is set
with the reason, the log records a WARN, and the next scheduled run picks up
the late data. An unchanged export makes no commit.
