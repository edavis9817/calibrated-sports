"""Export the website's data (brief W02) - one job, one schema, one destination.

    python -m jobs.export_web                  # everything
    python -m jobs.export_web --only players   # players | teams | market | research | manifest
    python -m jobs.export_web --dry-run        # build and count, write nothing

The contract is docs/web-schema.md (schema_version 1). The destination is
config.WEB_DATA_DIR and nothing else; there is no default and no literal path.

Reads the store read-only. Writes are atomic (temp file + rename) and happen
only when a file's content changed, ignoring `generated_at`, so an unchanged
refresh produces no git diff. Files that fall out of scope are deleted; the
four legacy root files (calibration/bias/liquidity/site.json) are never touched.
"""
import argparse
import hashlib
import json
import os
import random
import sqlite3
import statistics
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCHEMA_VERSION = 1
PARTS = ("players", "teams", "market", "research", "manifest")
LEGACY = frozenset({"calibration.json", "bias.json", "liquidity.json", "site.json"})

# nflverse keeps the abbreviation of the era in nfl_games; player-weeks already
# use the franchise's current one. Team files are keyed by the current one.
FRANCHISE = {"OAK": "LV", "SD": "LAC", "STL": "LA"}
TEAM_NAMES = {
    "ARI": "Arizona Cardinals", "ATL": "Atlanta Falcons", "BAL": "Baltimore Ravens",
    "BUF": "Buffalo Bills", "CAR": "Carolina Panthers", "CHI": "Chicago Bears",
    "CIN": "Cincinnati Bengals", "CLE": "Cleveland Browns", "DAL": "Dallas Cowboys",
    "DEN": "Denver Broncos", "DET": "Detroit Lions", "GB": "Green Bay Packers",
    "HOU": "Houston Texans", "IND": "Indianapolis Colts", "JAX": "Jacksonville Jaguars",
    "KC": "Kansas City Chiefs", "LA": "Los Angeles Rams", "LAC": "Los Angeles Chargers",
    "LV": "Las Vegas Raiders", "MIA": "Miami Dolphins", "MIN": "Minnesota Vikings",
    "NE": "New England Patriots", "NO": "New Orleans Saints", "NYG": "New York Giants",
    "NYJ": "New York Jets", "PHI": "Philadelphia Eagles", "PIT": "Pittsburgh Steelers",
    "SEA": "Seattle Seahawks", "SF": "San Francisco 49ers", "TB": "Tampa Bay Buccaneers",
    "TEN": "Tennessee Titans", "WAS": "Washington Commanders",
}

STATS = ("targets", "receptions", "receiving_yards", "receiving_tds", "carries",
         "rushing_yards", "rushing_tds", "attempts", "completions", "passing_yards",
         "passing_tds", "interceptions")
DEF_COLUMNS = {
    "tackles_solo": "def_tackles_solo", "tackles_with_assist": "def_tackles_with_assist",
    "tackle_assists": "def_tackle_assists", "tackles_for_loss": "def_tackles_for_loss",
    "sacks": "def_sacks", "qb_hits": "def_qb_hits", "interceptions": "def_interceptions",
    "pass_defended": "def_pass_defended", "fumbles_forced": "def_fumbles_forced",
    "def_tds": "def_tds", "safeties": "def_safeties",
}

_BASE = {"rec": 1.0, "rec_yd": 0.1, "rush_yd": 0.1, "td": 6, "pass_yd": 0.04,
         "pass_td": 4, "int": -2, "fumble_lost": -2, "two_pt": 2}
SCORING = {"ppr": dict(_BASE), "half": dict(_BASE, rec=0.5), "standard": dict(_BASE, rec=0.0)}
SCORING_NOTE = ("Computed once at export. fumbles_lost and two_point_conversions are not in "
                "the source table (nfl_player_week), so they are null and score 0.")

SNAP_FIRST_SEASON = 2013
CDF_X = tuple(range(0, 51))
THRESHOLDS = (5, 10, 15, 20, 25, 30)
N_SIMS = 4000
MARKET_STATS = {"receptions": "KXNFLREC", "rush_attempts": "KXNFLRSHATT"}
VALIDATION = {
    "status": ("The method was validated on sportsbook ladders, 2023-25 (q90 coverage 0.101 "
               "against 0.100). The Kalshi-ladder arm used here has not been independently "
               "validated."),
    "source": "CLAUDE.md, market-implied fantasy distribution",
}
MARKET_SOURCE = {
    "venue": "kalshi",
    "method": ("research/implied.py arm A: mid of each rung, no de-vig (exchange); isotonic "
               "survival fit; Gaussian copula receptions↔receiving yards; Monte Carlo"),
    "n_sims": N_SIMS,
}
TTK_BUCKETS = (">72h", "24-72h", "6-24h", "1-6h", "0-1h", "in-game")
EXEC_SERIES = ("KXNFLREC", "KXNFLRSHATT", "KXNFLSPREAD", "KXNFLTOTAL", "KXNFLGAME")
# Values CLAUDE.md records for brief 022 H3 (the H3 module prints these; it does
# not register them). Anything CLAUDE.md does not record is null.
EXEC_TOUCH = {"KXNFLREC": {"1-6h": 50, "in-game": 2}, "KXNFLRSHATT": {"1-6h": 3, "in-game": 1},
              "KXNFLSPREAD": {"1-6h": 7943, "in-game": 89}}
EXEC_RATIO = {"CHAMP": 3.88, "WINSWEEK": 2.76, "KXNFLRSHATT": 2.51, "WINS": 2.13,
              "DIVISION": 1.18, "KXNFLREC": 1.05, "KXNFLTOTAL": 0.31, "KXNFLSPREAD": 0.30,
              "KXNFLGAME": 0.30}
EXEC_RULE = "Cross game lines 1-6h before kickoff; never cross a prop in-game."


class ConfigError(RuntimeError):
    """A required setting is missing. Refuse rather than guess a path."""


# =============================================================================
# small pure pieces
# =============================================================================

def require_setting(name):
    value = getattr(config, name, None)
    if not value:
        raise ConfigError(f"config.{name} is not set - put {name}=... in .env. "
                          "The web export has no default destination, by design.")
    return value


def iso(ts=None):
    ts = time.time() if ts is None else ts
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def envelope(kind, generated_at):
    return {"schema_version": SCHEMA_VERSION, "generated_at": generated_at, "kind": kind}


def rnd(x, dp=4):
    if x is None:
        return None
    if isinstance(x, float):
        return round(x, dp)
    return x


def num(x):
    return 0 if x is None else x


def intish(x):
    """Stats are stored REAL; counts read better as integers."""
    if x is None:
        return None
    return int(x) if float(x).is_integer() else round(float(x), 2)


def fantasy_points(row, scoring):
    w = SCORING[scoring]
    return round(
        w["rec"] * num(row.get("receptions"))
        + w["rec_yd"] * num(row.get("receiving_yards"))
        + w["rush_yd"] * num(row.get("rushing_yards"))
        + w["td"] * (num(row.get("receiving_tds")) + num(row.get("rushing_tds")))
        + w["pass_yd"] * num(row.get("passing_yards"))
        + w["pass_td"] * num(row.get("passing_tds"))
        + w["int"] * num(row.get("interceptions"))
        + w["fumble_lost"] * num(row.get("fumbles_lost"))
        + w["two_pt"] * num(row.get("two_point_conversions")), 2)


def team_spread(spread_line, home):
    """nflverse spread_line is POSITIVE when the HOME team is favoured. From a
    team's own perspective, positive means THIS team is favoured."""
    if spread_line is None or home is None:
        return None
    return spread_line if home else -spread_line


def result_of(points_for, points_against):
    if points_for is None or points_against is None:
        return None
    return "W" if points_for > points_against else "L" if points_for < points_against else "T"


def distribution_summary(sims):
    """Sorted simulated totals -> cdf / thresholds / quantiles, per the contract."""
    s = sorted(sims)
    n = len(s)
    import bisect
    cdf = [{"x": x, "p_at_most": rnd(bisect.bisect_right(s, x) / n)} for x in CDF_X]
    thr = [{"points": t, "p_at_least": rnd((n - bisect.bisect_left(s, t)) / n)} for t in THRESHOLDS]

    def q(p):
        return rnd(s[min(int(p * (n - 1)), n - 1)], 2)
    return {"cdf": cdf, "thresholds": thr,
            "quantiles": {"q10": q(0.10), "q25": q(0.25), "q50": q(0.50),
                          "q75": q(0.75), "q90": q(0.90)}}


# =============================================================================
# loading
# =============================================================================

def ro():
    return sqlite3.connect(f"file:{config.DB_PATH}?mode=ro", uri=True)


def _dicts(cur):
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def load_games(con):
    rows = _dicts(con.execute(
        "SELECT g.* FROM nfl_games g JOIN (SELECT game_id, MAX(data_version) dv FROM nfl_games "
        "GROUP BY game_id) v ON v.game_id = g.game_id AND v.dv = g.data_version"))
    games = {}
    for g in rows:
        g["home_team"] = FRANCHISE.get(g["home_team"], g["home_team"])
        g["away_team"] = FRANCHISE.get(g["away_team"], g["away_team"])
        games[g["game_id"]] = g
    return games


def game_index(games):
    idx = {}
    for g in games.values():
        for team in (g["home_team"], g["away_team"]):
            idx[(g["season"], g["week"], team)] = g
    return idx


def load_player_weeks(con):
    return _dicts(con.execute(
        "SELECT w.* FROM nfl_player_week w JOIN (SELECT gsis_id, season, week, season_type, "
        "MAX(data_version) dv FROM nfl_player_week GROUP BY gsis_id, season, week, season_type) v "
        "ON v.gsis_id = w.gsis_id AND v.season = w.season AND v.week = w.week "
        "AND v.season_type = w.season_type AND v.dv = w.data_version "
        "ORDER BY w.gsis_id, w.season, w.week"))


def load_xwalk(con):
    xw = {r["gsis_id"]: r for r in _dicts(con.execute("SELECT * FROM player_xwalk"))}
    aliases = defaultdict(set)
    for alias, gsis in con.execute("SELECT alias, gsis_id FROM player_alias"):
        aliases[gsis].add(alias)
    return xw, aliases


def load_snaps(con, xwalk):
    pfr_to_gsis = {r["pfr_id"]: g for g, r in xwalk.items() if r.get("pfr_id")}
    snaps, unresolved = {}, {}
    for pfr, gid, off, pct, name in con.execute(
            "SELECT s.pfr_player_id, s.game_id, s.offense_snaps, s.offense_pct, s.player "
            "FROM nfl_snap_counts s JOIN (SELECT pfr_player_id, game_id, MAX(data_version) dv "
            "FROM nfl_snap_counts GROUP BY pfr_player_id, game_id) v "
            "ON v.pfr_player_id = s.pfr_player_id AND v.game_id = s.game_id "
            "AND v.dv = s.data_version"):
        g = pfr_to_gsis.get(pfr)
        if g is None:
            unresolved[pfr] = name
            continue
        snaps[(g, gid)] = (off, pct)
    return snaps, unresolved


def current_week(games, weeks, now_ts=None):
    """season/week now, what the stats reach, and whether nflverse is late."""
    now_ts = time.time() if now_ts is None else now_ts
    season = max(g["season"] for g in games.values())
    this = [g for g in games.values() if g["season"] == season and g["week"] is not None]
    unplayed = [g for g in this if g["home_score"] is None]
    week = min(g["week"] for g in unplayed) if unplayed else max(g["week"] for g in this)
    by_week = defaultdict(list)
    for g in this:
        by_week[g["week"]].append(g)
    completed = [w for w, gs in by_week.items() if all(x["home_score"] is not None for x in gs)]
    last_completed = max(completed) if completed else None
    in_season = [(r["season"], r["week"]) for r in weeks if r["season"] == season]
    if in_season:
        through = max(in_season)
    else:
        prev = [(r["season"], r["week"]) for r in weeks if r["season"] < season]
        through = max(prev) if prev else (None, None)
    stale, reason = False, None
    if last_completed is not None and (through[0] != season or through[1] < last_completed):
        stale = True
        have = f"week {through[1]}" if through[0] == season else "no weeks"
        reason = (f"week {last_completed} of {season} is complete but nflverse player stats "
                  f"reach {have}")
    return {"season": season, "week": week,
            "data_through": {"season": through[0], "week": through[1]},
            "stale": stale, "stale_reason": reason, "last_completed_week": last_completed}


def player_scope(weeks):
    """v1: any regular-season week with offensive usage."""
    return {r["gsis_id"] for r in weeks if r["season_type"] == "REG"
            and num(r["targets"]) + num(r["carries"]) + num(r["attempts"]) > 0}


# =============================================================================
# builders
# =============================================================================

def _stat_block(rows):
    out = {k: intish(sum(num(r.get(k)) for r in rows)) for k in STATS}
    out["fumbles_lost"] = None
    out["two_point_conversions"] = None
    return out


def build_players(games, weeks, snaps, xwalk, aliases, scope, market_ids, generated_at):
    gidx = game_index(games)
    by_player = defaultdict(list)
    for r in weeks:
        if r["gsis_id"] in scope:
            by_player[r["gsis_id"]].append(r)
    files, ppr_diff, unresolved = {}, [], []
    for gsis, rows in by_player.items():
        rows.sort(key=lambda r: (r["season"], r["week"]))
        xw = xwalk.get(gsis)
        latest = rows[-1]
        if xw is None:
            unresolved.append({"id": gsis, "name": latest.get("player_name"),
                               "reason": "not in player_xwalk"})
        games_out = []
        for r in rows:
            g = gidx.get((r["season"], r["week"], r["team"]))
            home = None if g is None else (r["team"] == g["home_team"])
            opp = r.get("opponent") if g is None else (g["away_team"] if home else g["home_team"])
            snap = snaps.get((gsis, g["game_id"])) if g is not None else None
            if r["season"] < SNAP_FIRST_SEASON:
                snap = None
            row = {"season": r["season"], "week": r["week"], "season_type": r["season_type"],
                   "game_id": None if g is None else g["game_id"],
                   "date": None if g is None else g["gameday"],
                   "team": r["team"], "opponent": opp, "home": home,
                   "snaps": None if snap is None else intish(snap[0]),
                   "snap_share": None if snap is None else rnd(snap[1]),
                   "target_share": rnd(r.get("target_share"))}
            for k in STATS:
                row[k] = intish(r.get(k)) if r.get(k) is not None else 0
            row["fumbles_lost"] = None
            row["two_point_conversions"] = None
            row["fantasy"] = {s: fantasy_points(row, s) for s in SCORING}
            if r.get("fantasy_points_ppr") is not None:
                ppr_diff.append(abs(row["fantasy"]["ppr"] - r["fantasy_points_ppr"]))
            # key order as the contract lists it
            games_out.append({k: row[k] for k in (
                "season", "week", "season_type", "game_id", "date", "team", "opponent", "home",
                "snaps", "snap_share", "targets", "target_share", "receptions",
                "receiving_yards", "receiving_tds", "carries", "rushing_yards", "rushing_tds",
                "attempts", "completions", "passing_yards", "passing_tds", "interceptions",
                "fumbles_lost", "two_point_conversions", "fantasy")})

        totals = []
        by_season = defaultdict(list)
        for g in games_out:
            by_season[(g["season"], g["season_type"])].append(g)
        for (season, stype), gs in sorted(by_season.items(), key=lambda kv: (kv[0][0], kv[0][1] != "REG")):
            totals.append(_season_total(season, stype, gs))
        reg = [g for g in games_out if g["season_type"] == "REG"]
        career = _season_total(None, "REG", reg)
        career.pop("season")
        career.pop("season_type")

        files[gsis] = {
            **envelope("player", generated_at),
            "id": gsis,
            "name": (xw or {}).get("display_name") or latest.get("player_name"),
            "position": (xw or {}).get("position") or latest.get("position"),
            "team": latest.get("team"),
            "ids": {"gsis": gsis,
                    "pfr": (xw or {}).get("pfr_id"), "espn": (xw or {}).get("espn_id"),
                    "sleeper": (xw or {}).get("sleeper_id"), "yahoo": (xw or {}).get("yahoo_id"),
                    "pff": (xw or {}).get("pff_id")},
            "aliases": sorted(aliases.get(gsis, ())),
            "seasons": sorted({g["season"] for g in games_out}),
            "has_market": gsis in market_ids,
            "games": games_out,
            "season_totals": totals,
            "career": career,
            "usage": [{"season": g["season"], "week": g["week"], "snap_share": g["snap_share"],
                       "target_share": g["target_share"]} for g in reg],
            "fantasy_scoring": {**{s: dict(w) for s, w in SCORING.items()}, "note": None},
        }
    return files, ppr_diff, unresolved


def _season_total(season, stype, gs):
    shares = [g["snap_share"] for g in gs if g["snap_share"] is not None]
    n = len(gs)
    fant = {}
    for s in SCORING:
        tot = round(sum(g["fantasy"][s] for g in gs), 2)
        fant[s] = {"total": tot, "per_game": round(tot / n, 2) if n else None}
    return {"season": season, "season_type": stype, "games": n, **_stat_block(gs),
            "snap_share_mean": rnd(statistics.fmean(shares)) if shares else None,
            "fantasy": fant}


def build_teams(games, weeks, snaps, scope, xwalk, generated_at):
    files = {}
    by_team_week = defaultdict(list)
    for r in weeks:
        by_team_week[(r["team"], r["season"], r["season_type"])].append(r)
    gidx = game_index(games)
    for team, name in TEAM_NAMES.items():
        tgames = sorted((g for g in games.values() if team in (g["home_team"], g["away_team"])),
                        key=lambda g: (g["season"], g["week"] or 0))
        schedule, coaches = [], defaultdict(Counter)
        for g in tgames:
            home = g["home_team"] == team
            pf = g["home_score"] if home else g["away_score"]
            pa = g["away_score"] if home else g["home_score"]
            coach = g["home_coach"] if home else g["away_coach"]
            if coach:
                coaches[g["season"]][coach] += 1
            schedule.append({
                "season": g["season"], "week": g["week"], "game_type": g["game_type"],
                "game_id": g["game_id"], "date": g["gameday"], "kickoff_ts": g["kickoff_ts"],
                "home": home, "opponent": g["away_team"] if home else g["home_team"],
                "points_for": intish(pf), "points_against": intish(pa),
                "result": result_of(pf, pa),
                "spread": team_spread(g["spread_line"], home), "total": g["total_line"],
                "coach": coach,
                "opponent_coach": g["away_coach"] if home else g["home_coach"]})

        splits = []
        seasons = sorted({g["season"] for g in tgames})
        for season in seasons:
            for stype in ("REG", "POST"):
                played = [s for s in schedule if s["season"] == season
                          and (s["game_type"] == "REG") == (stype == "REG")
                          and s["points_for"] is not None]
                prows = by_team_week.get((team, season, stype), [])
                if not played and not prows:
                    continue
                off = {"points": intish(sum(s["points_for"] for s in played)),
                       "passing_yards": intish(sum(num(r["passing_yards"]) for r in prows)),
                       "rushing_yards": intish(sum(num(r["rushing_yards"]) for r in prows)),
                       "receiving_yards": intish(sum(num(r["receiving_yards"]) for r in prows)),
                       "pass_attempts": intish(sum(num(r["attempts"]) for r in prows)),
                       "completions": intish(sum(num(r["completions"]) for r in prows)),
                       "passing_tds": intish(sum(num(r["passing_tds"]) for r in prows)),
                       "rushing_tds": intish(sum(num(r["rushing_tds"]) for r in prows)),
                       "receiving_tds": intish(sum(num(r["receiving_tds"]) for r in prows)),
                       "interceptions_thrown": intish(sum(num(r["interceptions"]) for r in prows)),
                       "targets": intish(sum(num(r["targets"]) for r in prows)),
                       "carries": intish(sum(num(r["carries"]) for r in prows))}
                de = {"points_allowed": intish(sum(s["points_against"] for s in played))}
                for k, col in DEF_COLUMNS.items():
                    de[k] = intish(sum(num(r[col]) for r in prows))
                splits.append({"season": season, "season_type": stype, "games": len(played),
                               "offense": off, "defense": de})

        roster = []
        with_data = sorted({s for (t, s, st) in by_team_week if t == team and st == "REG"})
        if with_data:
            season = with_data[-1]
            prows = by_team_week[(team, season, "REG")]
            team_tgt = sum(num(r["targets"]) for r in prows)
            team_car = sum(num(r["carries"]) for r in prows)
            per = defaultdict(list)
            for r in prows:
                per[r["gsis_id"]].append(r)
            for gsis, rs in per.items():
                shares = []
                for r in rs:
                    g = gidx.get((r["season"], r["week"], team))
                    sn = snaps.get((gsis, g["game_id"])) if g else None
                    if sn and sn[1] is not None:
                        shares.append(sn[1])
                xw = xwalk.get(gsis) or {}
                roster.append({
                    "season": season, "id": gsis,
                    "name": xw.get("display_name") or rs[-1].get("player_name"),
                    "position": xw.get("position") or rs[-1].get("position"),
                    "games": len(rs),
                    "snap_share": rnd(statistics.fmean(shares)) if shares else None,
                    "target_share": rnd(sum(num(r["targets"]) for r in rs) / team_tgt) if team_tgt else None,
                    "carry_share": rnd(sum(num(r["carries"]) for r in rs) / team_car) if team_car else None,
                    "has_page": gsis in scope})
            roster.sort(key=lambda p: (-(p["snap_share"] or 0), p["name"] or ""))

        files[team] = {**envelope("team", generated_at), "team": team, "name": name,
                       "seasons": seasons, "schedule": schedule, "splits": splits,
                       "roster": roster,
                       "coaches": [{"season": s, "head_coach": c.most_common(1)[0][0]}
                                   for s, c in sorted(coaches.items())]}
    return files


def build_market(con, games, weeks, xwalk, current, now_ts, generated_at, n_sims=N_SIMS):
    """Current-week market-implied fantasy distributions (research/implied.py arm A)."""
    from research import implied as I

    season, week = current["season"], current["week"]
    wk_games = {gid: g for gid, g in games.items()
                if g["season"] == season and g["week"] == week and g["home_score"] is None
                and g["kickoff_ts"] and g["kickoff_ts"] > now_ts}
    if not wk_games:
        return {}, {"reason": "no unplayed games in the current week"}
    rows = con.execute(
        "SELECT o.entity_id, o.stat, o.line, o.event_id, mo.market_id FROM outcomes o "
        "JOIN market_outcome mo ON mo.outcome_id = o.outcome_id AND mo.venue = 'kalshi' "
        "WHERE o.season = ? AND o.week = ? AND o.entity_type = 'player' AND o.side = 'over' "
        "AND o.line IS NOT NULL AND o.stat IN ('receptions', 'rush_attempts')",
        (season, week)).fetchall()
    ladders = defaultdict(list)
    census = Counter()
    for gsis, stat, line, game_id, market_id in rows:
        g = wk_games.get(game_id)
        if g is None:
            census["market not in an unplayed current-week game"] += 1
            continue
        if not market_id.startswith(MARKET_STATS[stat] + "-"):
            census["market series does not match stat"] += 1
            continue
        q = con.execute(
            "SELECT ts, best_bid, best_ask FROM quotes WHERE venue = 'kalshi' AND market_id = ? "
            "AND ts < ? AND best_bid IS NOT NULL AND best_ask IS NOT NULL "
            "ORDER BY ts DESC LIMIT 1", (market_id, min(now_ts, g["kickoff_ts"]))).fetchone()
        if not q:
            census["no two-sided quote before kickoff"] += 1
            continue
        ts, bid, ask = q
        ladders[(gsis, game_id)].append({"stat": stat, "line": line, "bid": bid, "ask": ask,
                                         "ts": ts})
    if not ladders:
        return {}, dict(census, reason="no priced current-week ladders")

    anchor, ypc, ypr, cop = I.fit_td_anchor(), I.fit_ypc(), I.fit_ypr(), I.fit_copula()
    history = defaultdict(list)
    latest_team = {}
    for r in weeks:
        latest_team[r["gsis_id"]] = r["team"]
        if r["season_type"] == "REG" and season - 3 <= r["season"] <= season:
            history[r["gsis_id"]].append(1.0 if num(r["receiving_tds"]) + num(r["rushing_tds"]) > 0 else 0.0)

    files = {}
    for (gsis, game_id), rungs in ladders.items():
        by_stat = defaultdict(list)
        for x in rungs:
            by_stat[x["stat"]].append(x)
        if "receptions" not in by_stat:
            census["no receptions ladder (outside validated scope)"] += 1
            continue
        fits = {}
        for stat, xs in by_stat.items():
            pts = [(x["line"], min(max((x["bid"] + x["ask"]) / 2.0, 1e-4), 1 - 1e-4)) for x in xs]
            f = I.fit_marginal(stat, pts)
            if f:
                fits[stat] = f
        if "receptions" not in fits:
            census["receptions ladder did not fit"] += 1
            continue
        g = wk_games[game_id]
        xw = xwalk.get(gsis) or {}
        team = latest_team.get(gsis) or xw.get("last_team")
        if team not in (g["home_team"], g["away_team"]):
            census["player team not in the game"] += 1
            continue
        home = team == g["home_team"]
        pos = (xw.get("position") or "").upper()
        h = history.get(gsis, [])
        fits["_td_rate"] = statistics.fmean(h) if len(h) >= 4 else 0.0
        real = {"pos": pos, "total": g["total_line"], "spread": g["spread_line"], "home": home}
        rho = cop.get(pos, {}).get("rho", {}).get(("receptions", "receiving_yards"), 0.75)
        seed = int(hashlib.sha1(f"{gsis}|{game_id}".encode()).hexdigest()[:8], 16)
        dists = {}
        for key, imp in (("ppr", "ppr"), ("half", "half_ppr"), ("standard", "standard")):
            sims = I.simulate_player_game(fits, real, anchor, ypc, rho, random.Random(seed),
                                          n_sims, imp, ypr=ypr)
            dists[key] = distribution_summary(sims)

        def rung_list(stat):
            return [{"line": x["line"], "p_over": rnd((x["bid"] + x["ask"]) / 2.0),
                     "bid": rnd(x["bid"]), "ask": rnd(x["ask"]), "quote_ts": x["ts"]}
                    for x in sorted(by_stat.get(stat, []), key=lambda x: x["line"])]
        rush = rung_list("rush_attempts")
        components = [
            {"stat": "receptions", "basis": "MARKET", "rungs": rung_list("receptions")},
            {"stat": "receiving_yards", "basis": "DERIVED",
             "note": "receptions × yards per catch by position"},
            {"stat": "rush_attempts", "basis": "MARKET", "rungs": rush,
             **({} if rush else {"note": "no rush-attempts ladder listed; contributes 0"})},
            {"stat": "rushing_yards", "basis": "DERIVED",
             "note": "attempts × yards per carry by position"},
            {"stat": "touchdowns", "basis": "ANCHORED",
             "note": "player TD rate scaled by the game total and spread"},
        ]
        files[gsis] = {
            **envelope("market", generated_at),
            "id": gsis, "name": xw.get("display_name"), "position": xw.get("position"),
            "team": team, "season": season, "week": week, "game_id": game_id,
            "opponent": g["away_team"] if home else g["home_team"], "kickoff_ts": g["kickoff_ts"],
            "as_of": iso(max(x["ts"] for x in rungs)),
            "source": dict(MARKET_SOURCE, n_sims=n_sims),
            "components": components,
            "game_lines": {"total": g["total_line"], "spread": team_spread(g["spread_line"], home),
                           "source": "nflverse games"},
            "distributions": dists,
            "validation": dict(VALIDATION),
        }
    return files, dict(census)


def build_research(generated_at):
    out = {}
    with open(os.path.join(ROOT, "docs", "hypotheses.json"), encoding="utf-8") as f:
        src = json.load(f)
    out["hypotheses.json"] = {**envelope("research.hypotheses", generated_at),
                              "hypotheses": src["hypotheses"]}

    from research import score as SC
    rows, *_ = SC.load(2026, 1)
    common = [r for r in rows if r["market_p"] is not None]
    series = []
    for name, field in (("model", "model"), ("market", "market_p")):
        table, ece = SC.reliability([r[field] for r in common], [r["y"] for r in common])
        series.append({"name": name, "ece": rnd(ece), "bins": [
            {"lo": t["lo"], "hi": t["hi"], "n": t["n"],
             "mean_forecast": rnd(t.get("mean_p")), "realized": rnd(t.get("rate")),
             "wilson": [rnd(t["wilson"][0]), rnd(t["wilson"][1])] if t["n"] else None}
            for t in table]})
    brier = {f: rnd(statistics.fmean(SC.brier(r[k], r["y"]) for r in common))
             for f, k in (("model", "model"), ("market", "market_p"), ("naive", "naive"))}
    head = SC.boot_mean(common, lambda r: SC.brier(r["model"], r["y"]) - SC.brier(r["market_p"], r["y"]))
    brier["model_minus_market"] = {"estimate": rnd(head["est"]),
                                   "interval": [rnd(head["lo"]), rnd(head["hi"])]}
    out["calibration.json"] = {
        **envelope("research.calibration", generated_at),
        "source": "research/score.py (brief 021)",
        "population": (f"NFL week 1 2026, KXNFLREC + KXNFLRSHATT, common set n={len(common)}, "
                       f"{len({r['game'] for r in common})} games"),
        "series": series, "brier": brier}

    reg = os.path.join(ROOT, "research", "sweep", "results", "h3.jsonl")
    med = {}
    with open(reg, encoding="utf-8") as f:
        for line in f:
            r = json.loads(line)
            if r.get("family") == "H3 median spread" and r.get("estimable"):
                med[r["name"]] = r.get("est")
    out["execution.json"] = {
        **envelope("research.execution", generated_at),
        "source": "research/sweep/h3_lifecycle.py (brief 022)",
        "series": [{"series": s, "by_time_to_kickoff": [
            {"bucket": b, "median_spread_c": med.get(f"{s}|ttk|{b}"),
             "median_touch": EXEC_TOUCH.get(s, {}).get(b)} for b in TTK_BUCKETS]}
            for s in EXEC_SERIES],
        "spread_to_volatility": [{"series": s, "ratio": v} for s, v in EXEC_RATIO.items()],
        "rule": EXEC_RULE}
    return out


def build_manifest(games, current, players, scope_files, xwalk, market_ids, unresolved,
                   nflverse_version, generated_at):
    return {
        **envelope("manifest", generated_at),
        "current": {"season": current["season"], "week": current["week"],
                    "data_through": current["data_through"],
                    "nflverse_version": nflverse_version,
                    "stale": current["stale"], "stale_reason": current["stale_reason"]},
        "seasons": sorted({g["season"] for g in games.values()}),
        "teams": [{"team": t, "name": n} for t, n in TEAM_NAMES.items()],
        "players": sorted(players, key=lambda p: (p["name"] or "", p["id"])),
        "counts": {"players": len(players), "teams": len(TEAM_NAMES),
                   "market": len(market_ids)},
        "unresolved_ids": unresolved,
    }


# =============================================================================
# writing
# =============================================================================

def _canonical(obj):
    return json.dumps({k: v for k, v in obj.items() if k != "generated_at"},
                      sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def write_if_changed(path, obj, dry_run=False):
    """True when written. Unchanged content (ignoring generated_at) is left alone."""
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                if _canonical(json.load(f)) == _canonical(obj):
                    return False
        except (OSError, ValueError):
            pass
    if dry_run:
        return True
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, path)
    return True


def sync_dir(dirpath, wanted, dry_run=False):
    """Write {name: obj} into dirpath and delete *.json no longer wanted."""
    written = deleted = 0
    for name, obj in wanted.items():
        written += write_if_changed(os.path.join(dirpath, f"{name}.json"), obj, dry_run)
    if os.path.isdir(dirpath):
        keep = {f"{n}.json" for n in wanted}
        for fn in os.listdir(dirpath):
            if fn.endswith(".json") and fn not in keep and fn not in LEGACY:
                if not dry_run:
                    os.remove(os.path.join(dirpath, fn))
                deleted += 1
    return written, deleted


def export(only=None, dry_run=False, now_ts=None, dest=None, log=print):
    dest = dest or require_setting("WEB_DATA_DIR")
    parts = set(only or PARTS)
    now_ts = time.time() if now_ts is None else now_ts
    generated_at = iso(now_ts)
    t0 = time.time()
    con = ro()
    games = load_games(con)
    weeks = load_player_weeks(con)
    xwalk, aliases = load_xwalk(con)
    snaps, snap_unresolved = load_snaps(con, xwalk)
    current = current_week(games, weeks, now_ts)
    scope = player_scope(weeks)
    summary = {"current": current}

    market_ids = None
    if "market" in parts:
        market, census = build_market(con, games, weeks, xwalk, current, now_ts, generated_at)
        market_ids = set(market)
        summary["market"] = sync_dir(os.path.join(dest, "market"), market, dry_run)
        summary["market_census"] = census
        summary["market_players"] = sorted((m["name"] or m["id"]) for m in market.values())
    if market_ids is None:
        mdir = os.path.join(dest, "market")
        market_ids = ({fn[:-5] for fn in os.listdir(mdir) if fn.endswith(".json")}
                      if os.path.isdir(mdir) else set())

    players, ppr_diff, unresolved = build_players(games, weeks, snaps, xwalk, aliases, scope,
                                                  market_ids, generated_at)
    ppr_diff.sort()
    if ppr_diff:
        med = statistics.median(ppr_diff)
        p99 = ppr_diff[min(len(ppr_diff) - 1, int(0.99 * len(ppr_diff)))]
        note = (f"{SCORING_NOTE} Against nflverse's own fantasy_points_ppr (which includes them) "
                f"the PPR total differs by median {med:.2f} and p99 {p99:.2f} points per game "
                f"over {len(ppr_diff):,} player-games.")
    else:
        med = p99 = None
        note = SCORING_NOTE
    for p in players.values():
        p["fantasy_scoring"]["note"] = note
    summary["ppr_check"] = {"median_abs_diff": med, "p99_abs_diff": p99, "n": len(ppr_diff)}
    unresolved += [{"id": pfr, "name": name, "reason": "snap-count pfr id not in player_xwalk"}
                   for pfr, name in sorted(snap_unresolved.items())]
    summary["unresolved"] = unresolved

    if "players" in parts:
        summary["players"] = sync_dir(os.path.join(dest, "players"), players, dry_run)
    if "teams" in parts:
        teams = build_teams(games, weeks, snaps, scope, xwalk, generated_at)
        summary["teams"] = sync_dir(os.path.join(dest, "teams"), teams, dry_run)
    if "research" in parts:
        research = build_research(generated_at)
        summary["research"] = sum(write_if_changed(os.path.join(dest, "research", n), o, dry_run)
                                  for n, o in research.items())
    if "manifest" in parts:
        nfv = con.execute("SELECT MAX(data_version) FROM nflverse_versions "
                          "WHERE dataset = 'weekly_stats'").fetchone()[0]
        plist = [{"id": gsis, "name": p["name"], "position": p["position"], "team": p["team"],
                  "first_season": p["seasons"][0], "last_season": p["seasons"][-1],
                  "has_market": p["has_market"]} for gsis, p in players.items()]
        manifest = build_manifest(games, current, plist, players, xwalk, market_ids, unresolved,
                                  nfv, generated_at)
        summary["manifest"] = write_if_changed(os.path.join(dest, "manifest.json"), manifest, dry_run)
    con.close()
    summary["counts"] = {"players": len(players), "teams": len(TEAM_NAMES),
                         "market": len(market_ids)}
    summary["runtime_s"] = round(time.time() - t0, 1)
    if current["stale"]:
        log(f"WARN nflverse is late: {current['stale_reason']}")
    return summary


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--only", action="append", choices=PARTS)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    s = export(only=a.only, dry_run=a.dry_run)
    printable = {k: v for k, v in s.items() if k not in ("unresolved", "market_players")}
    print(json.dumps(printable, indent=1, default=str))
    print(f"unresolved ids: {len(s['unresolved'])}")
    if s.get("market_players") is not None:
        print(f"market players ({len(s['market_players'])}): {', '.join(s['market_players'][:40])}")


if __name__ == "__main__":
    main()
