"""f-32: are `cfb_games` conference and division labels the season's own, or today's?

The test and its expected values were fixed in PREREGISTRATION.md before this ran.
READ-ONLY; cfb.db mode=ro. Columns: home_conference, away_conference,
home_division, away_division (what research.cfb_game_forecast.load reads).

    python research/f32_grid_mask/labels_asof.py --db D:/calibrated-sports/data/cfb.db \
        --json-out research/f32_grid_mask/results/f32_labels_asof.json
"""
import argparse
import collections
import json
import os
import sqlite3

U = """(SELECT season, home_id tid, home_team team, home_conference conf, home_division div, game_id
        FROM cfb_games WHERE valid_to_ts IS NULL
        UNION ALL SELECT season, away_id, away_team, away_conference, away_division, game_id
        FROM cfb_games WHERE valid_to_ts IS NULL)"""
CONF = [("Nebraska", 2010, "Big 12"), ("Nebraska", 2011, "Big Ten"), ("Texas A&M", 2011, "Big 12"),
        ("Texas A&M", 2012, "SEC"), ("Utah", 2010, "Mountain West"), ("Utah", 2011, "Pac-12"),
        ("TCU", 2011, "Mountain West"), ("TCU", 2012, "Big 12"), ("Maryland", 2013, "ACC"),
        ("Maryland", 2014, "Big Ten"), ("Texas", 2023, "Big 12"), ("Texas", 2024, "SEC")]
# the pre-registration wrote "Appalachian State"; the store's label for that school is "App State"
DIV = [("App State", 2007, "fcs"), ("James Madison", 2021, "fcs"), ("James Madison", 2022, "fbs"),
       ("Massachusetts", 2011, "fcs"), ("Massachusetts", 2012, "fbs")]
# not pre-registered: conference NAMES that exist only in their own era
ERA = [(2010, "Pac-10", True), (2010, "Pac-12", False), (2012, "Big East", True), (2012, "American Athletic", False),
       (2013, "American Athletic", True), (2013, "Big East", False), (2012, "Western Athletic", True)]
FB = """WITH fb AS (SELECT DISTINCT season, home_id tid FROM cfb_games WHERE valid_to_ts IS NULL AND home_division = 'fbs'
                    UNION SELECT DISTINCT season, away_id FROM cfb_games WHERE valid_to_ts IS NULL AND away_division = 'fbs')
        SELECT g.season, g.home_team, g.home_division, g.away_team, g.away_division, g.home_points, g.away_points
        FROM cfb_games g WHERE g.valid_to_ts IS NULL AND g.season BETWEEN 2001 AND 2026
          AND (g.home_division IS NULL OR g.away_division IS NULL)
          AND g.completed = 1 AND g.home_points IS NOT NULL AND g.away_points IS NOT NULL
          AND g.home_points != g.away_points
          AND (g.home_division = 'fbs' OR EXISTS(SELECT 1 FROM fb WHERE fb.season = g.season AND fb.tid = g.home_id))
          AND (g.away_division = 'fbs' OR EXISTS(SELECT 1 FROM fb WHERE fb.season = g.season AND fb.tid = g.away_id))
        ORDER BY 1"""
CHANGED = """SELECT a.game_id, a.season, a.home_division, a.away_division FROM cfb_games a JOIN cfb_games b
             ON a.game_id = b.game_id AND a.valid_from_ts < b.valid_from_ts WHERE
             COALESCE(a.home_conference, '') != COALESCE(b.home_conference, '') OR
             COALESCE(a.away_conference, '') != COALESCE(b.away_conference, '') OR
             COALESCE(a.home_division, '') != COALESCE(b.home_division, '') OR
             COALESCE(a.away_division, '') != COALESCE(b.away_division, '')"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--json-out", required=True)
    a = ap.parse_args()
    con = sqlite3.connect("file:" + os.path.abspath(a.db).replace("\\", "/") + "?mode=ro", uri=True, timeout=5)
    q = lambda s, *p: con.execute(s, p).fetchall()  # noqa: E731
    R = {"source": q("SELECT src_dataset, COUNT(*), MIN(season), MAX(season) FROM cfb_games WHERE valid_to_ts IS NULL "
                     "GROUP BY 1")}
    print("source rows:", R["source"])
    R["tests"], fails = [], 0
    for team, season, want in CONF + DIV:
        col = "conf" if (team, season, want) in CONF else "div"
        got = q("SELECT %s, COUNT(*) FROM %s WHERE season = ? AND team = ? GROUP BY 1" % (col, U), season, team)
        ok = len(got) == 1 and got[0][0] == want
        fails += not ok
        R["tests"].append({"team": team, "season": season, "column": col, "expected": want, "got": got, "pass": ok})
        print("   %-14s %d %-4s expected %-14s got %-28s %s" % (team, season, col, want, got, "PASS" if ok else "FAIL"))
    if len(R["tests"]) != 17:
        raise SystemExit("expected 17 tests")
    R["era_names"] = []
    for season, name, want in ERA:
        n = q("SELECT COUNT(DISTINCT tid) FROM %s WHERE div = 'fbs' AND season = ? AND conf = ?" % U, season, name)[0][0]
        ok = (n > 0) == want
        fails += not ok
        R["era_names"].append({"season": season, "label": name, "fbs_teams": n, "expected_present": want, "pass": ok})
        print("   era name  %d %-18s fbs teams %2d  expected present %-5s %s" % (season, name, n, want, "PASS" if ok else "FAIL"))
    R["failures"] = fails
    multi = q("SELECT COUNT(*) FROM (SELECT season, tid FROM %s WHERE div = 'fbs' GROUP BY 1, 2 "
              "HAVING COUNT(DISTINCT conf) > 1)" % U)[0][0]
    total = q("SELECT COUNT(*) FROM (SELECT DISTINCT season, tid FROM %s WHERE div = 'fbs')" % U)[0][0]
    R["fbs_team_seasons"], R["fbs_team_seasons_with_two_conference_labels"] = total, multi
    print("FBS team-seasons carrying more than one conference label: %d of %d" % (multi, total))
    by = collections.defaultdict(dict)
    for tid, team, s, d in q("SELECT tid, MAX(team), season, div FROM %s WHERE div IS NOT NULL GROUP BY 1, 3, 4" % U):
        by[(tid, team)].setdefault(s, set()).add(d)
    moves = []
    for (_tid, team), m in by.items():
        ss = sorted(m)
        for x, y in zip(ss, ss[1:]):
            if m[x] != m[y] and ("fbs" in m[x] or "fbs" in m[y]):
                moves.append((y, team, "/".join(sorted(m[x])), "/".join(sorted(m[y]))))
    R["division_moves_involving_fbs"] = sorted(moves)
    print("division changes involving FBS, by first season of the new label (%d): %s" % (len(moves), sorted(moves)))
    v = R["vintage"] = {
        "valid_from_range_seasons_before_current": q(
            "SELECT MIN(valid_from_ts), MAX(valid_from_ts), COUNT(*) FROM cfb_games WHERE valid_to_ts IS NULL "
            "AND season < 2026")[0],
        "superseded_versions_by_season": q(
            "SELECT season, COUNT(DISTINCT game_id) FROM cfb_games WHERE valid_to_ts IS NOT NULL GROUP BY 1"),
        "label_changes_between_versions": q(CHANGED)}
    print("every current row for seasons before 2026 was written between ts %.0f and %.0f (%d rows); superseded "
          "versions exist for: %s; label changes between versions: %s"
          % (v["valid_from_range_seasons_before_current"][0], v["valid_from_range_seasons_before_current"][1],
             v["valid_from_range_seasons_before_current"][2], v["superseded_versions_by_season"],
             v["label_changes_between_versions"]))
    nd = R["null_division"] = {
        "rows": q("SELECT COUNT(*) FROM cfb_games WHERE valid_to_ts IS NULL AND season BETWEEN 2001 AND 2026 "
                  "AND (home_division IS NULL OR away_division IS NULL)")[0][0],
        "fbs_team_both_sides_dropped": q(FB)}
    print("rows with a NULL division on a side: %d; completed decisive games where BOTH teams are FBS in their other "
          "games that season but one side's label is NULL (so load() drops them): %d"
          % (nd["rows"], len(nd["fbs_team_both_sides_dropped"])))
    for r in nd["fbs_team_both_sides_dropped"]:
        print("     ", r)
    con.close()
    R["reading"] = "season-vintage" if fails == 0 and multi == 0 else "FAILED - see tests"
    print("READING: %s (%d failures of %d checks)" % (R["reading"], fails, len(R["tests"]) + len(R["era_names"])))
    os.makedirs(os.path.dirname(os.path.abspath(a.json_out)), exist_ok=True)
    with open(a.json_out, "w", encoding="utf-8") as f:
        json.dump(R, f, indent=1, default=str)


if __name__ == "__main__":
    main()
