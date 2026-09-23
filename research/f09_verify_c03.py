"""F09: re-derive c-03's MLB store claims from mlb.db, READ-ONLY. Stdlib only.

    python -m research.f09_verify_c03 [path/to/mlb.db]

Default path is config.storage_path("mlb.db"), where c-03's mlb.paths puts the store. Opens with mode=ro and
PRAGMA query_only, so it cannot write. Prints:
  1. row counts and season range per fact table, and how many rows are current;
  2. games per season x gametype, and per-team game counts (partial-load check);
  3. referential completeness (2 team lines per game, every game has batting/pitching);
  4. the cost of a season-totals query shaped like c-03's jobs.ingest_mlb.season_totals
     (same WHERE, same GROUP BY, a subset of the SUM columns), with its query plan.

The totals SQL here is a restatement, not an import: when this was written c-03's code
(ba81999) was not yet on origin/main. The export-vs-SQL agreement check in docs/F09 was
run against the cs-cfb clone and is not reproduced by this script.
"""
import sqlite3
import sys
import time
from collections import defaultdict

import config

DB = sys.argv[1] if len(sys.argv) > 1 else config.storage_path("mlb.db")
TABLES = ["mlb_games", "mlb_team_games", "mlb_batting", "mlb_pitching", "mlb_player_teams"]
CURRENT = "valid_to_ts IS NULL AND stattype = 'value'"
REG = ("regular", "playoff")     # c-03: a Game 163 tiebreaker is filed as 'playoff'


def p(*a):
    print(*a, flush=True)


def main():
    p("store:", DB)
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    con.execute("PRAGMA query_only=1")
    q = lambda s, a=(): con.execute(s, a).fetchall()

    p("== 1. tables: rows, current rows, min/max season, distinct seasons")
    for t in TABLES:
        r = q(f"SELECT COUNT(*), SUM(valid_to_ts IS NULL), MIN(season), MAX(season), "
              f"COUNT(DISTINCT season) FROM {t}")[0]
        p(f"  {t:18s} {r}")
        assert r[0] > 0, f"{t} is empty"

    p("== 2a. games per season x gametype")
    rows = q("SELECT season, gametype, COUNT(*) FROM mlb_games WHERE valid_to_ts IS NULL "
             "GROUP BY 1, 2")
    d = defaultdict(dict)
    for s, g, n in rows:
        d[s][g] = n
    gts = sorted({g for _, g, _ in rows})
    p("  season", gts)
    for s in sorted(d):
        p("  ", s, [d[s].get(g, 0) for g in gts])

    p("== 2b. per season: teams, min/max team games (regular + tiebreaker), ties")
    for r in q("SELECT season, COUNT(*), MIN(n), MAX(n), SUM(t) FROM ("
               "  SELECT season, team, COUNT(*) n, SUM(tie) t FROM mlb_team_games "
               f"  WHERE {CURRENT} AND gametype IN (?, ?) GROUP BY 1, 2) GROUP BY 1 ORDER BY 1",
               REG):
        p("  ", r)

    p("== 3. completeness")
    p("  team lines per game:", q("SELECT c, COUNT(*) FROM (SELECT game_id, COUNT(*) c "
                                   "FROM mlb_team_games GROUP BY 1) GROUP BY 1"))
    for t in ("mlb_team_games", "mlb_batting", "mlb_pitching"):
        n = q(f"SELECT COUNT(*) FROM (SELECT game_id FROM mlb_games "
              f"EXCEPT SELECT DISTINCT game_id FROM {t})")[0][0]
        p(f"  games with no row in {t}: {n}")

    p("== 4. season-totals cost (c-03 shape), 3 runs each; the first may be cold")
    cols = ["b_pa", "b_ab", "b_r", "b_h", "b_hr", "b_rbi", "b_sb", "b_w", "b_k"]
    tot = ", ".join(f"CASE WHEN COUNT({c}) = COUNT(*) THEN SUM({c}) END AS {c}" for c in cols)
    base = (f"SELECT player_id, COUNT(DISTINCT game_id), GROUP_CONCAT(DISTINCT team), {tot} "
            f"FROM mlb_batting WHERE {CURRENT} AND season = ? AND gametype IN (?, ?)")
    one = base + " AND player_id = ? GROUP BY player_id"
    allp = base + " GROUP BY player_id"
    p("  plan (one player):", q("EXPLAIN QUERY PLAN " + one, (2025, *REG, "ohtas001")))
    for label, sql, args in (("season 2025, all batters", allp, (2025, *REG)),
                             ("season 2025, one batter", one, (2025, *REG, "ohtas001"))):
        ts = []
        for _ in range(3):
            a = time.perf_counter()
            n = len(q(sql, args))
            ts.append(round(time.perf_counter() - a, 3))
        p(f"  {label}: rows={n} seconds={ts}")
    a = time.perf_counter()
    hr = 0
    for s in range(1999, 2026):
        r = q(one, (s, *REG, "pujoa001"))
        hr += r[0][7] if r else 0      # b_hr: player_id, games, teams, then cols[4]
    p(f"  career loop, one batter, 27 season queries: {time.perf_counter() - a:.1f}s "
      f"(pujoa001 HR={hr})")


if __name__ == "__main__":
    main()
