"""Queries over the normalized nflverse store.

    python -m queries --player "Amon-Ra St. Brown" --stat receptions --threshold 5
    python -m queries --player 00-0036355 --stat receptions --threshold 5 \
                      --as-of 2026-09-14

Every query here is AS-OF aware. `nfl_player_week` carries one row per
(player, season, week, season_type, data_version), and a stat correction adds a
version rather than overwriting one - so a query that ignores data_version
silently mixes what we know now into a backtest of what we knew then, which is
invariant #5 and the reason the table is shaped this way.

`latest_as_of()` is the join every reader wants: for each player-week, the
newest version at or before a date. Default is "everything we have now".
"""
import argparse

import store

# Threshold markets are natively "N or more" on both exchanges - Kalshi lists
# "4+ receptions" with floor_strike 3.5 - so the comparison here is >=, and it
# matches the contract being priced without an off-by-one in between.
STATS = {
    "receptions": "receptions",
    "targets": "targets",
    "receiving_yards": "receiving_yards",
    "receiving_tds": "receiving_tds",
    "carries": "carries",
    "rushing_yards": "rushing_yards",
    "rushing_tds": "rushing_tds",
    "attempts": "attempts",
    "completions": "completions",
    "passing_yards": "passing_yards",
    "passing_tds": "passing_tds",
    "fantasy_points_ppr": "fantasy_points_ppr",
}

# Column names are interpolated into SQL, so they may only ever come from the
# whitelist above - never from caller input directly.
_LATEST = """
    SELECT gsis_id, season, week, season_type, MAX(data_version) AS dv
      FROM nfl_player_week
     WHERE {where}
     GROUP BY gsis_id, season, week, season_type
"""


def resolve_player(who: str, as_of: str = None):
    """Accept a gsis_id or a display name -> [(gsis_id, name, last_season)].

    Names are ambiguous and gsis_ids are not, which is exactly why identity is
    resolved to gsis_id at ingest. This is the convenience layer for a human at
    a terminal, not something analysis code should call.
    """
    if who.startswith("00-"):
        with store.db() as c:
            row = c.execute(
                "SELECT gsis_id, MAX(player_name), MAX(season) FROM nfl_player_week"
                " WHERE gsis_id=? GROUP BY gsis_id", (who,)).fetchone()
        return [row] if row else []
    with store.db() as c:
        return c.execute(
            "SELECT gsis_id, MAX(player_name) AS name, MAX(season) AS last_season"
            "  FROM nfl_player_week"
            " WHERE player_name = ? COLLATE NOCASE"
            " GROUP BY gsis_id ORDER BY last_season DESC", (who,)).fetchall()


def search_players(fragment: str, limit: int = 20):
    with store.db() as c:
        return c.execute(
            "SELECT gsis_id, MAX(player_name), MAX(position), MAX(season)"
            "  FROM nfl_player_week WHERE player_name LIKE ?"
            " GROUP BY gsis_id ORDER BY MAX(season) DESC LIMIT ?",
            (f"%{fragment}%", limit)).fetchall()


def threshold_counts(gsis_id: str, stat: str = "receptions", threshold: float = 5,
                     as_of: str = None, season_type: str = "REG",
                     seasons=None):
    """How many times did this player reach `threshold`+ of `stat`, by season.

    Returns [(season, games, hits, rate)] - the shape of the product feature,
    and the fastest proof the ingest join actually works end to end.
    """
    if stat not in STATS:
        raise ValueError(f"unknown stat {stat!r}; known: {', '.join(sorted(STATS))}")
    col = STATS[stat]

    where = ["gsis_id = ?"]
    args = [gsis_id]
    if as_of:
        where.append("data_version <= ?")
        args.append(as_of)
    if season_type:
        where.append("season_type = ?")
        args.append(season_type)
    if seasons:
        where.append(f"season IN ({','.join('?' * len(seasons))})")
        args += list(seasons)

    latest = _LATEST.format(where=" AND ".join(where))
    sql = f"""
        WITH latest AS ({latest})
        SELECT p.season,
               COUNT(*)                                        AS games,
               SUM(CASE WHEN p.{col} >= ? THEN 1 ELSE 0 END)    AS hits
          FROM nfl_player_week p
          JOIN latest l
            ON p.gsis_id = l.gsis_id AND p.season = l.season
           AND p.week = l.week AND p.season_type = l.season_type
           AND p.data_version = l.dv
         WHERE p.{col} IS NOT NULL
         GROUP BY p.season
         ORDER BY p.season
    """
    with store.db() as c:
        rows = c.execute(sql, (*args, threshold)).fetchall()
    return [(s, g, h, (h / g if g else 0.0)) for s, g, h in rows]


def player_week(gsis_id: str, season: int, as_of: str = None):
    """One player's weekly line for a season, at the newest version as of a date."""
    where = ["gsis_id = ?", "season = ?"]
    args = [gsis_id, season]
    if as_of:
        where.append("data_version <= ?")
        args.append(as_of)
    latest = _LATEST.format(where=" AND ".join(where))
    with store.db() as c:
        return c.execute(f"""
            WITH latest AS ({latest})
            SELECT p.week, p.season_type, p.team, p.opponent, p.receptions,
                   p.targets, p.receiving_yards, p.carries, p.rushing_yards,
                   p.data_version
              FROM nfl_player_week p
              JOIN latest l ON p.gsis_id=l.gsis_id AND p.season=l.season
               AND p.week=l.week AND p.season_type=l.season_type
               AND p.data_version=l.dv
             ORDER BY p.season_type DESC, p.week
        """, args).fetchall()


def main():
    ap = argparse.ArgumentParser(description="Query the nflverse store")
    ap.add_argument("--player", required=True, help="gsis_id or display name")
    ap.add_argument("--stat", default="receptions", choices=sorted(STATS))
    ap.add_argument("--threshold", type=float, default=5)
    ap.add_argument("--as-of", dest="as_of", help="YYYY-MM-DD; default: now")
    ap.add_argument("--season-type", default="REG", choices=("REG", "POST"))
    ap.add_argument("--search", action="store_true", help="fuzzy name lookup")
    args = ap.parse_args()

    store.init_db()
    if args.search:
        for gid, name, pos, last in search_players(args.player):
            print(f"  {gid}  {name:26s} {pos or '':4s} last {last}")
        return

    hits = resolve_player(args.player, args.as_of)
    if not hits:
        print(f"no player matching {args.player!r} - try --search")
        raise SystemExit(1)
    if len(hits) > 1:
        print(f"{len(hits)} players match that name; using the most recent:")
        for gid, name, last in hits:
            print(f"    {gid}  {name}  last season {last}")
    gsis_id, name = hits[0][0], hits[0][1]

    rows = threshold_counts(gsis_id, args.stat, args.threshold, args.as_of,
                            args.season_type)
    label = f"{name} ({gsis_id}) - {args.stat} >= {args.threshold:g}"
    print(label)
    print(f"  as of {args.as_of or 'now'}, {args.season_type} season\n")
    print(f"  {'season':>6}  {'games':>5}  {'hits':>4}  rate")
    tg = th = 0
    for season, games, h, rate in rows:
        tg += games
        th += h
        print(f"  {season:>6}  {games:>5}  {h:>4}  {rate:6.1%}")
    if rows:
        print(f"  {'total':>6}  {tg:>5}  {th:>4}  {th/tg:6.1%}")
    else:
        print("  (no rows - has that season been ingested?)")


if __name__ == "__main__":
    main()
