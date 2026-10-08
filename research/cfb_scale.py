"""a-72: the three places college differs from the NFL, measured. Reads `cfb.db` read-only.

    python -m research.cfb_scale            # prints; writes nothing
    python -m research.cfb_scale --nfl      # also reads market_log.db (mode=ro) for the
                                            # NFL column of the talent-spread table

0 requests. Every figure the a-72 report quotes about scale, talent spread or identity
comes from here.

  1 SCALE     players in scope at each season floor, and what that does to the index.
  2 SPREAD    how lopsided the sport is, and the constants an NFL page or model carries
              that would be WRONG here if reused: home margin, the spread of the margin,
              the Pythagorean exponent, the season's length in weeks.
  3 IDENTITY  how far a college player id can be trusted: placeholder ids, one id with
              two names, one name with two ids, box-to-roster match by season, and the
              draft crosswalk's match rate into this store.

Scope is stated on every line: FBS means the teams classified `fbs` in the season named,
and "FBS v FBS" a game with both sides so classified in its own season.
"""
import argparse
import math
import os
import sqlite3
import statistics
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cfb import paths, sources                                    # noqa: E402

FROM = 2004
OFF = "(COALESCE(pass_att,0)+COALESCE(rush_att,0)+COALESCE(rec,0)) > 0"
INDEX_BYTES_PER_PLAYER = None      # measured from a staged tree with --tree, never assumed


def ro(path):
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)


def fbs_by_season(con):
    out = defaultdict(set)
    for season, tid in con.execute(
            "SELECT season, team_id FROM cfb_teams WHERE valid_to_ts IS NULL AND "
            "classification='fbs'"):
        out[season].add(tid)
    return out


def scale(con, current):
    ids = sorted(current)
    marks = ",".join("?" * len(ids))
    print("1 SCALE - players with an offensive box row for a team that is FBS in "
          f"{sources.CURRENT_SEASON}, by the season floor of that row")
    print("    floor   players   box rows")
    for floor in (2004, 2010, 2015, 2020, 2023, 2024, 2025, 2026):
        n, rows = con.execute(
            f"SELECT COUNT(DISTINCT athlete_id), COUNT(*) FROM cfb_player_game_box WHERE "
            f"valid_to_ts IS NULL AND athlete_id > 0 AND season >= ? AND team_id IN "
            f"({marks}) AND {OFF}", (floor, *ids)).fetchone()
        print(f"    {floor}   {n:>7,}   {rows:>8,}")
    n_any = con.execute(
        f"SELECT COUNT(DISTINCT athlete_id) FROM cfb_player_game_box WHERE valid_to_ts IS "
        f"NULL AND athlete_id > 0 AND team_id IN ({marks})", ids).fetchone()[0]
    print(f"    any stat row at all, 2004 on: {n_any:,} players")
    print(f"    teams: {len(ids)} FBS in {sources.CURRENT_SEASON}")
    wk = con.execute(
        "SELECT season, MAX(week) FROM cfb_games WHERE valid_to_ts IS NULL AND "
        "season_type='regular' AND season >= ? GROUP BY season", (FROM,)).fetchall()
    print("    regular-season weeks in the feed, by season: "
          + str(dict(Counter(w for _s, w in wk))) + "  (weeks: seasons)")
    double = defaultdict(int)
    for season, week, hid, aid in con.execute(
            "SELECT season, week, home_id, away_id FROM cfb_games WHERE valid_to_ts IS NULL "
            "AND season_type='regular' AND season >= ? AND week IS NOT NULL", (FROM,)):
        for t in (hid, aid):
            if t in current:
                double[(season, week, t)] += 1
    twice = [k for k, v in double.items() if v > 1]
    print(f"    team-weeks holding TWO regular-season games: {len(twice)} of {len(double):,} "
          f"({sum(1 for k in twice if k[0] == sources.CURRENT_SEASON)} in "
          f"{sources.CURRENT_SEASON})")


def pythag(team_seasons):
    """The exponent minimising squared error of win share against PF^k/(PF^k+PA^k)."""
    best = None
    for i in range(100, 501, 5):
        k = i / 100
        err = sum((w - pf ** k / (pf ** k + pa ** k)) ** 2 for w, pf, pa in team_seasons)
        if best is None or err < best[1]:
            best = (k, err)
    return best[0]


def spread_table(label, games):
    """games: [(home_points, away_points, neutral, home_key, away_key, season)]."""
    margins = [h - a for h, a, n, *_ in games if not n]
    allm = [h - a for h, a, *_ in games]
    ts = defaultdict(lambda: [0, 0, 0, 0])
    for h, a, _n, hk, ak, season in games:
        for key, pf, pa in ((hk, h, a), (ak, a, h)):
            t = ts[(key, season)]
            t[0] += 1
            t[1] += 1 if pf > pa else 0.5 if pf == pa else 0
            t[2] += pf
            t[3] += pa
    rows = [(w / g, pf, pa) for g, w, pf, pa in ts.values() if g >= 8 and pf > 0 and pa > 0]
    print(f"    {label}: {len(games):,} games")
    print(f"      home margin, non-neutral: mean {statistics.mean(margins):+.2f}, "
          f"sd {statistics.pstdev(margins):.2f}, n {len(margins):,}")
    print(f"      home win share, non-neutral: "
          f"{sum(1 for m in margins if m > 0) / len(margins):.4f}")
    print(f"      |margin| >= 21: {sum(1 for m in allm if abs(m) >= 21) / len(allm):.4f}   "
          f">= 28: {sum(1 for m in allm if abs(m) >= 28) / len(allm):.4f}")
    print(f"      Pythagorean exponent, team-seasons of 8+ games (n {len(rows):,}): "
          f"{pythag(rows):.2f}")
    share = sorted(w for w, _pf, _pa in rows)
    print(f"      team-season win share: sd {statistics.pstdev(share):.4f}, "
          f"p10 {share[len(share) // 10]:.3f}, p90 {share[len(share) * 9 // 10]:.3f}")


def spread(con, fbs, nfl_db=None, lo=2015, hi=None):
    hi = hi or sources.CURRENT_SEASON - 1
    print(f"\n2 TALENT SPREAD - finished games, seasons {lo}-{hi}")
    games = []
    for season, hid, aid, hp, ap, neutral in con.execute(
            "SELECT season, home_id, away_id, home_points, away_points, neutral_site FROM "
            "cfb_games WHERE valid_to_ts IS NULL AND season BETWEEN ? AND ? AND home_points "
            "IS NOT NULL AND away_points IS NOT NULL AND season_type IN "
            "('regular','postseason')", (lo, hi)):
        if hid in fbs[season] and aid in fbs[season]:
            games.append((hp, ap, bool(neutral), hid, aid, season))
    spread_table("college, FBS v FBS", games)
    if nfl_db:
        con2 = ro(nfl_db)
        try:
            rows = con2.execute(
                "SELECT g.season, g.home_team, g.away_team, g.home_score, g.away_score "
                "FROM nfl_games g JOIN (SELECT game_id, MAX(data_version) dv "
                "FROM nfl_games GROUP BY game_id) v ON v.game_id=g.game_id AND "
                "v.dv=g.data_version WHERE g.season BETWEEN ? AND ? AND g.home_score IS NOT "
                "NULL AND g.away_score IS NOT NULL", (lo, hi)).fetchall()
        finally:
            con2.close()
        # nfl_games carries no neutral-site flag, so the NFL "home" figures INCLUDE the
        # few neutral games a season (international series, the Super Bowl).
        spread_table("NFL, every game (neutral sites not separable)", [
            (hs, as_, False, h, a, s) for s, h, a, hs, as_ in rows])
    else:
        print("    NFL column not read (pass --nfl)")


def identity(con, current):
    print("\n3 IDENTITY")
    n, bad, ids = con.execute(
        "SELECT COUNT(*), SUM(athlete_id <= 0), COUNT(DISTINCT athlete_id) FROM "
        "cfb_player_game_box WHERE valid_to_ts IS NULL").fetchone()
    print(f"    box rows {n:,}; athlete_id <= 0 on {bad:,} ({bad / n:.4%}); "
          f"{ids:,} distinct ids")
    by = con.execute(
        "SELECT season, COUNT(*) FROM cfb_player_game_box WHERE valid_to_ts IS NULL AND "
        "athlete_id <= 0 GROUP BY season ORDER BY season").fetchall()
    print("    placeholder-id rows by season: " + ", ".join(f"{s}:{c}" for s, c in by))
    names = defaultdict(set)
    key_ids = defaultdict(set)
    seasons_of = defaultdict(set)
    for aid, name, tid, season in con.execute(
            f"SELECT athlete_id, athlete_name, team_id, season FROM cfb_player_game_box "
            f"WHERE valid_to_ts IS NULL AND athlete_id > 0 AND {OFF}"):
        if tid not in current:
            continue
        names[aid].add(name)
        key_ids[(season, tid, name)].add(aid)
        seasons_of[(season, tid, aid)].add(1)
    two_names = sum(1 for v in names.values() if len(v) > 1)
    two_ids = sum(1 for v in key_ids.values() if len(v) > 1)
    print(f"    offensive players on current-FBS teams: {len(names):,}; "
          f"one id under two or more names: {two_names:,} ({two_names / len(names):.4%})")
    print(f"    one (season, team, name) under two or more ids: {two_ids:,} of "
          f"{len(key_ids):,} ({two_ids / len(key_ids):.4%})")
    roster, rostered = set(), set()
    for season, tid, aid in con.execute(
            "SELECT season, team_id, athlete_id FROM cfb_rosters WHERE valid_to_ts IS NULL"):
        roster.add((season, tid, aid))
        rostered.add(aid)
    per = defaultdict(lambda: [0, 0, 0])
    for (season, tid, aid) in seasons_of:
        p = per[season]
        p[0] += 1
        p[1] += (season, tid, aid) in roster
        p[2] += aid in rostered
    tot = [sum(p[i] for p in per.values()) for i in range(3)]
    print(f"    BOX -> ROSTER, offensive player-team-seasons: {tot[0]:,}; on that team's "
          f"roster that season {tot[1] / tot[0]:.4f}; on any roster ever {tot[2] / tot[0]:.4f}")
    print("    by season (n, same season+team, any roster):")
    for season in sorted(per):
        n_, a, b = per[season]
        print(f"      {season}  {n_:>5,}  {a / n_:.4f}  {b / n_:.4f}")
    xw = con.execute(
        "SELECT espn_athlete_id, gsis_id, rookie_season FROM cfb_player_xwalk WHERE "
        "valid_to_ts IS NULL").fetchall()
    boxed = {r[0] for r in con.execute(
        "SELECT DISTINCT athlete_id FROM cfb_player_game_box WHERE valid_to_ts IS NULL")}
    linked = [r for r in xw if r[0] is not None]
    print(f"    DRAFT CROSSWALK (nflverse players, espn id -> gsis id): {len(xw):,} rows, "
          f"{len(linked):,} with a college id")
    for lo, hi in ((None, 2004), (2005, 2015), (2016, 2026)):
        rows = [r for r in linked if r[2] is not None
                and (lo is None or r[2] >= lo) and r[2] <= hi]
        if not rows:
            continue
        in_box = sum(1 for r in rows if r[0] in boxed)
        in_ros = sum(1 for r in rows if r[0] in rostered)
        print(f"      rookie season {lo or 'up'}-{hi}: {len(rows):,} linked; in this store's "
              f"box scores {in_box / len(rows):.4f}; on a roster {in_ros / len(rows):.4f}")
    none = sum(1 for r in linked if r[2] is None)
    print(f"      no rookie season: {none:,}")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--nfl", action="store_true")
    a = ap.parse_args(argv)
    con = ro(paths.db_path())
    try:
        fbs = fbs_by_season(con)
        current = fbs[sources.CURRENT_SEASON]
        if not current:
            raise SystemExit("no FBS teams for the current season - nothing to measure")
        scale(con, current)
        nfl_db = None
        if a.nfl:
            import config
            nfl_db = config.DB_PATH
        spread(con, fbs, nfl_db)
        identity(con, current)
    finally:
        con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
