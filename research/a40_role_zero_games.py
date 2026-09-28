"""Unit a-40: how far the first `analytics.role` construction read high.

    python -m research.a40_role_zero_games              # 2025, both kinds
    python -m research.a40_role_zero_games --season 2019

THE DEFECT. `role._touch_blocks` / `_onfield_blocks` built each (player, bucket)
from the games in which the player had a row IN THAT BUCKET. A game where he
played and had no snap (or no touch) on 3rd and short was therefore not in his
3rd-and-short share at all: selection on the numerator, and the share reads high.

WHAT THIS MEASURES, per kind and bucket, over players with >= 8 games that season:
  - the share of player-game-bucket cells the old construction dropped;
  - old share minus corrected share, per player: median, p90, max.
The corrected construction fixes a player's games first (any row in any bucket
that game) and counts every bucket his team ran a play in, at zero where he had
none - the rule `analytics.role._blocks` now implements.

Reads `analytics.db` READ-ONLY. Writes nothing.
"""
import argparse
import os
import statistics as st
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

BUCKETS = ("1st", "2nd", "3rd_short", "3rd_med", "3rd_long", "4th")
MIN_GAMES = 8

SQL = {
    "onfield": (
        "SELECT game_id, team, down_bucket, plays FROM f_onfield_team_game "
        "WHERE season=?",
        "SELECT player_id, game_id, team, down_bucket, snaps FROM f_onfield_game "
        "WHERE season=?"),
    "touch": (
        "SELECT game_id, team, down_bucket, SUM(is_carry) + SUM(is_target) "
        "FROM f_play_usage WHERE season=? AND role IN ('rusher','receiver') "
        "GROUP BY game_id, team, down_bucket",
        "SELECT player_id, game_id, team, down_bucket, "
        "SUM(is_carry) + SUM(is_target) FROM f_play_usage WHERE season=? "
        "AND role IN ('rusher','receiver') "
        "GROUP BY player_id, game_id, team, down_bucket"),
}


def measure(con, kind, season):
    team_sql, player_sql = SQL[kind]
    team = {(g, t, b): n or 0 for g, t, b, n in con.execute(team_sql, (season,))}
    players = {}
    for p, g, t, b, n in con.execute(player_sql, (season,)):
        players.setdefault(p, {}).setdefault((g, t), {})[b] = n or 0
    cells = dropped = 0
    bias = {b: [] for b in BUCKETS}
    for games in players.values():
        if len(games) < MIN_GAMES:
            continue
        for b in BUCKETS:
            old_n = old_d = new_n = new_d = 0
            for (g, t), by_bucket in games.items():
                d = team.get((g, t, b), 0)
                if not d:
                    continue
                cells += 1
                if b in by_bucket:
                    old_n += by_bucket[b]
                    old_d += d
                else:
                    dropped += 1
                new_n += by_bucket.get(b, 0)
                new_d += d
            if old_d and new_d:
                bias[b].append(old_n / old_d - new_n / new_d)
    if not cells:
        raise SystemExit("%s %d: no cells - nothing measured is not a result"
                         % (kind, season))
    return cells, dropped, bias


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--season", type=int, default=2025)
    a = ap.parse_args(argv)
    from analytics import paths
    con = paths.connect(read_only=True)
    for kind in ("onfield", "touch"):
        cells, dropped, bias = measure(con, kind, a.season)
        print("%s %d: %d player-game-bucket cells, old construction dropped %d (%.3f)"
              % (kind, a.season, cells, dropped, dropped / cells))
        for b in BUCKETS:
            x = sorted(bias[b])
            if not x:
                continue
            print("  %-10s players %4d  old - corrected: median %+.4f  p90 %+.4f  max %+.4f"
                  % (b, len(x), st.median(x), x[int(0.9 * (len(x) - 1))], x[-1]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
