"""c-07: does unifying the MLB aggregation rule change any number? Read-only.

    python -m research.c07_totals_equivalence D:/calibrated-sports/data/mlb.db [--export 2024-2025]

c-03 implemented the rule twice (SQL in `jobs.ingest_mlb.season_totals`, a Python fold in
`jobs.export_mlb_web`); c-07 deleted the SQL copy and routed both through `mlb.totals`.
This compares the NEW code against the OLD SQL, which is kept below VERBATIM as an
oracle - here, in research, and nowhere in product code. Every cell must match:

  1. `season_totals` (new) against the old SQL, every season held, batting and pitching.
  2. `team_records` (new) against the old SQL, every season held.
  3. with --export: the export's regular-season totals in summary.json against the old
     SQL, the comparison f-03 made (90,116 cells over 2024-2025).

`teams` changed shape on purpose: the old GROUP_CONCAT(DISTINCT) order was unspecified,
the new one is sorted, so it is compared as a set. Exit 1 on any mismatch, and exit 1
if nothing was compared - a check that compared nothing has not passed.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from jobs import ingest_mlb as J        # noqa: E402
from mlb import schema                   # noqa: E402

OLD_REGULAR = ("regular", "playoff")


def _old_total(col):
    return f"CASE WHEN COUNT({col}) = COUNT(*) THEN SUM({col}) END AS {col}"


def old_season_totals(con, season, kind):
    """ba81999's `season_totals`, verbatim but for the player filter."""
    table = {"batting": "mlb_batting", "pitching": "mlb_pitching"}[kind]
    stats = schema.BAT_STATS + schema.BAT_FLAGS if kind == "batting" else \
        schema.PIT_STATS + schema.PIT_FLAGS
    where = f"{J.CURRENT} AND season = ? AND gametype IN ({','.join('?' * len(OLD_REGULAR))})"
    sql = (f"SELECT player_id, COUNT(DISTINCT game_id) AS games, "
           f"GROUP_CONCAT(DISTINCT team) AS teams, {', '.join(_old_total(c) for c in stats)} "
           f"FROM {table} WHERE {where} GROUP BY player_id ORDER BY player_id")
    cur = con.execute(sql, [season, *OLD_REGULAR])
    names = [d[0] for d in cur.description]
    return [dict(zip(names, r)) for r in cur.fetchall()], stats


def old_team_records(con, season):
    return {t: (g, w, l, ti) for t, g, w, l, ti in con.execute(
        f"SELECT team, COUNT(*), SUM(win), SUM(loss), SUM(tie) FROM mlb_team_games "
        f"WHERE {J.CURRENT} AND season = ? AND gametype IN ('regular','playoff') "
        f"GROUP BY team ORDER BY team", (season,))}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("db")
    ap.add_argument("--export", metavar="SEASONS")
    a = ap.parse_args(argv)
    con = J.connect_ro(a.db)
    seasons = [r[0] for r in con.execute(
        "SELECT DISTINCT season FROM mlb_team_games WHERE valid_to_ts IS NULL ORDER BY 1")]
    cells = mism = players = nulls = 0
    for season in seasons:
        for kind in ("batting", "pitching"):
            old, stats = old_season_totals(con, season, kind)
            new = {r["player_id"]: r for r in J.season_totals(con, season, kind)}
            if set(new) != {r["player_id"] for r in old}:
                print(f"  {season} {kind}: player sets differ")
                mism += 1
            for o in old:
                n = new.get(o["player_id"])
                if n is None:
                    continue
                players += 1
                if n["games"] != o["games"] or set(n["teams"].split(",")) != set(o["teams"].split(",")):
                    mism += 1
                for c in stats:
                    cells += 1
                    nulls += o[c] is None
                    if n[c] != o[c]:
                        mism += 1
                        if mism <= 10:
                            print(f"  MISMATCH {season} {kind} {o['player_id']} {c}: old {o[c]} new {n[c]}")
    print(f"season_totals: {len(seasons)} seasons ({seasons[0]}-{seasons[-1]}), "
          f"{players:,} player-season-kinds, {cells:,} cells ({nulls:,} null), mismatches {mism}")
    tmism = tcount = 0
    for season in seasons:
        o, n = old_team_records(con, season), J.team_records(con, season)
        tcount += len(o)
        tmism += sum(1 for t in set(o) | set(n) if o.get(t) != n.get(t))
    print(f"team_records: {tcount:,} team-seasons, mismatches {tmism}")
    rc = 0 if (mism == 0 and tmism == 0 and cells and tcount) else 1

    if a.export:
        from jobs import export_mlb_web as E
        exp_seasons = J.parse_seasons(a.export)
        files = E.build(con, exp_seasons)
        ecells = emism = 0
        for season in exp_seasons:
            for kind in ("batting", "pitching"):
                old, _stats = old_season_totals(con, season, kind)
                cols = schema.BAT_STATS if kind == "batting" else schema.PIT_STATS + schema.PIT_FLAGS
                for o in old:
                    summ = files.get(f"mlb/players/{o['player_id']}/summary.json")
                    reg = [t for t in (summ or {}).get("season_totals", [])
                           if t["season"] == season and t["season_type"] == "regular"]
                    if not reg:
                        emism += 1
                        continue
                    for c in cols:
                        ecells += 1
                        if reg[0]["stats"].get(c) != o[c]:
                            emism += 1
        print(f"export {exp_seasons}: {ecells:,} cells against the old SQL, mismatches {emism}")
        rc = rc or (0 if emism == 0 and ecells else 1)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
