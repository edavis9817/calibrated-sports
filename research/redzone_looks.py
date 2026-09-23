"""a-15 (track B's A-B8): is a red-zone look a subset of the looks the page totals?

    python -m research.redzone_looks            # every season in the store

Reads `nfl_pbp_looks` (derived from play-by-play by jobs.ingest_nflverse) and
`nfl_player_week` (stats_player_week), latest data_version of each, mode=ro.
For every season it prints:

  * targets / carries reconciliation, PER PLAYER-WEEK: how many player-weeks
    the play-by-play count differs from stats_player_week, and by how much in
    total. This is what licenses calling rz_targets "the same plays as Tgt".
  * the red-zone totals, and the looks with no yardline (in neither rz count).
  * receiving_air_yards: league sum and non-zero player-weeks - the numbers
    behind NOT_COLLECTED and PARTIAL_COLLECTION in jobs/export_web.py.

Exits 1 if the store holds no looks rows at all: a reconciliation over nothing
is not a pass.
"""
import sqlite3
import sys
from collections import defaultdict

import config

LATEST_LOOKS = (
    "SELECT l.gsis_id, l.season, l.week, l.season_type, l.targets, l.carries, l.rz_targets, "
    "l.rz_carries, l.no_yardline FROM nfl_pbp_looks l JOIN (SELECT gsis_id, season, week, "
    "season_type, MAX(data_version) dv FROM nfl_pbp_looks GROUP BY gsis_id, season, week, "
    "season_type) v ON v.gsis_id = l.gsis_id AND v.season = l.season AND v.week = l.week "
    "AND v.season_type = l.season_type AND v.dv = l.data_version")
LATEST_WEEK = (
    "SELECT w.gsis_id, w.season, w.week, w.season_type, w.targets, w.carries, "
    "w.receiving_air_yards FROM nfl_player_week w JOIN (SELECT gsis_id, season, week, "
    "season_type, MAX(data_version) dv FROM nfl_player_week GROUP BY gsis_id, season, week, "
    "season_type) v ON v.gsis_id = w.gsis_id AND v.season = w.season AND v.week = w.week "
    "AND v.season_type = w.season_type AND v.dv = w.data_version")


def reconcile(con):
    looks = {(g, s, w, t): r for g, s, w, t, *r in con.execute(LATEST_LOOKS)}
    weeks = {(g, s, w, t): r for g, s, w, t, *r in con.execute(LATEST_WEEK)}
    if not looks:
        raise SystemExit("nfl_pbp_looks is empty - run ingest_nflverse --from-archive "
                         "--dataset pbp first. Nothing reconciled.")
    out = defaultdict(lambda: defaultdict(float))
    covered = {k[1] for k in looks}
    for key in set(looks) | set(weeks):
        season = key[1]
        if season not in covered:
            continue
        lt, lc, rzt, rzc, noy = looks.get(key, (0, 0, 0, 0, 0))
        wt, wc, ay = weeks.get(key, (0, 0, None))
        o = out[season]
        o["tgt_pbp"] += lt
        o["tgt_wk"] += wt or 0
        o["car_pbp"] += lc
        o["car_wk"] += wc or 0
        o["tgt_pw_off"] += (lt != (wt or 0))
        o["car_pw_off"] += (lc != (wc or 0))
        o["rz_tgt"] += rzt
        o["rz_car"] += rzc
        o["no_yardline"] += noy
        o["air_sum"] += ay or 0
        o["air_nonzero"] += bool(ay)
        o["air_null"] += key in weeks and ay is None
    return out


def main():
    path = config.DB_PATH.replace("\\", "/")
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        out = reconcile(con)
    finally:
        con.close()
    cols = ("tgt_pbp", "tgt_wk", "tgt_pw_off", "car_pbp", "car_wk", "car_pw_off", "rz_tgt",
            "rz_car", "no_yardline", "air_sum", "air_nonzero", "air_null")
    print("season " + " ".join(f"{c:>11}" for c in cols))
    for season in sorted(out):
        print(f"{season:>6} " + " ".join(f"{int(out[season][c]):>11,}" for c in cols))
    print(f"\n{len(out)} seasons reconciled")
    return 0 if out else 1


if __name__ == "__main__":
    sys.exit(main())
