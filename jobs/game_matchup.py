"""Everything we know about a game, one file per matchup (a-64).

Built by `jobs.game_export` in the same run as the forecast and its record, from
the same measurement, so the three files can never describe different walks:

    game/nfl/matchup/<game_id>.json   one per game of the forecast week
    game/nfl/matchup/index.json       the week: which games, and where each file is
    game/nfl/record_spread.json       c-30's scoring record for the spread
    game/nfl/record_total.json        c-31's scoring record for the total

Per game, four blocks:

  numbers    the model's number beside the market's, and the difference, for the
             moneyline (c-28), the spread (c-28's margin with c-30's key-number
             shape) and the total (c-31). Each carries `record`: the file that
             scores it, what it beats and by how much, and how it does against the
             closing price - which for all three is WORSE.
  teams      both sides as of the build: rating and its path this season, points
             per game, points per play then plays per game (c-31: efficiency is
             the sticky half, plays the noisy one), passing and rushing by unit,
             opponents' strength so far, rest.
  head_to_head  every meeting in the store, the splits, common opponents this season,
             and the first season the store holds - it does not claim completeness.
  situation  rest, short week, bye, travel, neutral site, roof, and wind with the
             effect size c-31 measured on the total.

AS-OF. Every team figure reads only games whose kickoff is before the build instant
and that have a final score. The fitted constants are c-30's and c-31's season-T
fits (seasons 2000..T-1), read from their committed result files
(`research/results/`), and refused for any other season: a fit for 2026 is not a fit
for 2027. The ratings are the forecast's own walk. The market's number is the
nflverse schedule's line as of the ingested data version - the same source the
record scores against - and nflverse does not record which book or when.

NOTHING IS A PICK. The files carry numbers, differences and intervals; the only
strings are names, definitions, reasons for an absent figure, and the scope of a
record. No verdict about a side is computed, because none was measured.

THE TOTAL NEEDS TRACK F'S PACE FOR EVERY GAME ALREADY PLAYED THIS SEASON. c-31's
factors drop a team-game with no pace row entirely (plays AND points), so a missing
week is not a smaller sample, it is a stale forecast. Where the season's pace is
incomplete the total is published as null with the coverage that stopped it.
"""
from __future__ import annotations

import json
import math
import os
from collections import Counter

import numpy as np
from scipy.stats import norm

from models import game as G

SPORT = "nfl"
MATCHUP_DIR = f"game/{SPORT}/matchup/"
INDEX_KEY = MATCHUP_DIR + "index.json"
RECORD_SPREAD_KEY = f"game/{SPORT}/record_spread.json"
RECORD_TOTAL_KEY = f"game/{SPORT}/record_total.json"
MATCHUP_KIND = "game.matchup"
INDEX_KIND = "game.matchup_index"
MODEL_RECORD_KIND = "game.model_record"

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
C30_RESULT = "research/results/c30_against_the_spread.json"
C31_RESULT = "research/results/c31_game_total.json"

# The brief (a-64) asks every model figure to travel with the fact that it loses to
# the closing price. a-63 stored the moneyline's version of that and marked it not
# for display (Ethan, 2026-09-30, the same day). The two instructions conflict; this
# unit followed the later, more specific one and says so in its report. ONE switch:
# False puts every `vs_close` in the matchup files back behind `display: false`.
SHOW_CLOSE = True

RECENT_MEETINGS = 10
HOME_COUNTRY = "US"          # ISO 3166-1 alpha-2; a venue anywhere else is international
SHORT_WEEK_DAYS = 6          # fewer days of rest than this is a short week
BYE_GAP_WEEKS = 2            # the previous game was at least this many weeks ago


def r4(x):
    return None if x is None else round(float(x), 4) + 0.0


def r1(x):
    return None if x is None else round(float(x), 1) + 0.0


def devig(a, b):
    """Multiplicative two-way de-vig of American prices -> P(first side), or None."""
    from research import game_forecast as GF
    if a is None or b is None:
        return None
    ia, ib = GF.american(a), GF.american(b)
    return ia / (ia + ib)


# =============================================================================
# the frozen fits and their records
# =============================================================================

def load_result(rel):
    with open(os.path.join(HERE, *rel.split("/")), encoding="utf-8") as f:
        return json.load(f)


def frozen(year, params, sigma):
    """-> (c30, c31, problems). Refuses a fit for any season but `year`, and a c-30
    fit whose base (Elo constants, margin sd) is not the walk being published - the
    key-number weights were fitted around that base and mean nothing on another."""
    c30, c31 = load_result(C30_RESULT), load_result(C31_RESULT)
    problems = {}
    f30 = c30["fits"].get(str(year))
    if f30 is None:
        problems["spread"] = f"c-30's result holds no fit for season {year}"
    elif f30["params"] != params or abs(f30["sigma_m"] - sigma) > 1e-9:
        problems["spread"] = (f"c-30's {year} fit was made on Elo {f30['params']} with margin sd "
                              f"{f30['sigma_m']}; the forecast walk is {params} and {sigma}")
    if str(year) not in c31["stacks"]["FULL"] or str(year) not in c31["factor_params"]:
        problems["total"] = f"c-31's result holds no fit for season {year}"
    return c30, c31, problems


def gi(d):
    """A result-file interval -> the contract's GameInterval."""
    from jobs.game_export import interval
    return interval(d)


def compared(verdict):
    from jobs.game_export import compared as c
    return c(verdict)


def sentence(key, holder, served):
    """a-75: the sentence a figure may only be cited with, worded from track F's
    carried figures. `jobs.required_sentences.require` refuses the file without it."""
    from jobs import required_sentences as RS
    return RS.qualifier(key, holder, served)


def build_record_spread(c30):
    p2b = c30["part2b_book"]["pooled"]
    pooled = c30["part1"]["cells"]["pooled"]["diffs"]["E-N0"]["dBrier"]
    n1 = c30["part1_population"]
    push = []
    for line in ("on 3", "on 7"):
        pr = c30["push_rate"][line]
        e = pr["E"]["pred-real"]
        push.append({"line": int(line.split()[1]), "games": pr["n"],
                     "realised": r4(pr["realized"]), "predicted": r4(pr["E"]["mean_pred"]),
                     "difference": gi(e)})
    return {
        "market": "spread",
        "model": ("c-28's margin-of-victory Elo margin with c-30's key-number shape: the "
                  "chance of a home win is c-28's, and the margin inside each side is "
                  "reshaped so 3 and 7 carry their real weight"),
        "population": {"games": n1["cover_rows"], "seasons": [2001, 2025],
                       "pushes_excluded": True, "blocks": "game"},
        "against_baselines": [],
        "against_shape": {"label": "the same margin without the key-number shape (a Normal)",
                          "d_brier": gi(pooled),
                          "compared": compared(gi(pooled)["verdict"]),
                          "qualifier": sentence(RECORD_SPREAD_KEY, "against_shape",
                                                gi(pooled))},
        "push_rates": push,
        "wind_per_mph": None,
        "vs_close": {"benchmark": "the nflverse closing spread's cover price",
                     "devig": "multiplicative, two-way", "provenance_recorded": False,
                     "seasons": [2006, 2025], "games": p2b["n"],
                     "brier_benchmark": r4(p2b["brier_book"]),
                     "d_brier": gi(p2b["E-book"]),
                     "compared": compared(gi(p2b["E-book"])["verdict"]),
                     "qualifier": None,
                     "display": SHOW_CLOSE},
        "covers": [
            f"{n1['cover_rows']:,} games with a closing spread and no push, 2001 to 2025, "
            "regular season and postseason.",
            "It scores the chance the home team covers the closing spread against the result "
            "(Brier score, lower is better), and the predicted push rate on 3 and 7 against the "
            "rate that happened.",
        ],
        "does_not_cover": [
            "No simple baseline was registered for covering the spread, so it is compared "
            "with the same forecast without the key-number shape, and with the closing price.",
            "The margin's mean and bands are not scored here.",
            "This season's games are not in it: the record ends with 2025.",
        ],
        "source": {"preregistration": "docs/C30-against-the-spread-preregistration.md",
                   "findings": "docs/findings/against-the-spread.md",
                   "script": "research/against_the_spread.py", "result": C30_RESULT,
                   "versions": dict(c30["versions"])},
    }


def build_record_total(c31):
    pb = c31["part1"]["pb"]
    s2 = c31["part2"]["S2 book"]
    d4 = c31["diagnostics"]["D4"]["i_full_stack"]
    games = pb["league"]["diffs"]["dBrier"]["games"]
    labels = (("league", "the league's average total (c-28)"),
              ("season_avg", "the two teams' season averages"))
    return {
        "market": "total",
        "model": ("c-31's two-team total: each team's plays times points per play, as of the "
                  "game and shrunk, with c-28's expected margin and the wind"),
        "population": {"games": games, "seasons": [2001, 2025], "pushes_excluded": False,
                       "blocks": "game"},
        "against_baselines": [
            {"id": b, "label": lab, "d_brier": gi(pb[b]["diffs"]["dBrier"]),
             "compared": compared(gi(pb[b]["diffs"]["dBrier"])["verdict"]),
             "qualifier": sentence(RECORD_TOTAL_KEY, f"against_baselines[id={b}]",
                                   gi(pb[b]["diffs"]["dBrier"]))}
            for b, lab in labels],
        "against_shape": None,
        "push_rates": [],
        "vs_close": {"benchmark": "the nflverse closing total's over price",
                     "devig": "multiplicative, two-way", "provenance_recorded": False,
                     "seasons": [2006, 2025], "games": s2["diffs"]["dBrier"]["games"],
                     "brier_benchmark": r4(s2["corp_comparator"]["bs"]),
                     "d_brier": gi(s2["diffs"]["dBrier"]),
                     "compared": compared(gi(s2["diffs"]["dBrier"])["verdict"]),
                     "qualifier": sentence(RECORD_TOTAL_KEY, "vs_close",
                                           gi(s2["diffs"]["dBrier"])),
                     "display": SHOW_CLOSE},
        "wind_per_mph": gi(d4),
        "covers": [
            f"{games:,} games, 2001 to 2025, regular season and postseason.",
            "It scores the chance the total goes over each of eight lines from 33.5 to 54.5 "
            "against the result (Brier score, lower is better).",
        ],
        "does_not_cover": [
            "It was scored with the wind recorded at the game. A forecast made before kickoff "
            "does not have that, so this record flatters the forecast shown before a game.",
            "This season's games are not in it: the record ends with 2025.",
        ],
        "source": {"preregistration": "docs/C31-game-total-preregistration.md",
                   "findings": "docs/findings/game-total.md",
                   "script": "research/game_total.py", "result": C31_RESULT,
                   "versions": dict(c31["versions"])},
    }


# =============================================================================
# inputs beyond the forecast's walk
# =============================================================================

def read_context(con, year, versions):
    """Everything the matchup reads that the forecast does not. One read of each."""
    import polars as pl

    import config
    lines = {r[0]: dict(zip(("spread_line", "total_line", "home_moneyline", "away_moneyline",
                              "home_spread_odds", "away_spread_odds", "over_odds",
                              "under_odds", "stadium", "roof", "data_version", "ingested_ts"),
                             r[1:]))
             for r in con.execute(
                 "SELECT g.game_id, g.spread_line, g.total_line, g.home_moneyline, "
                 "g.away_moneyline, g.home_spread_odds, g.away_spread_odds, g.over_odds, "
                 "g.under_odds, g.stadium, g.roof, g.data_version, g.ingested_ts "
                 "FROM nfl_games g JOIN (SELECT game_id, MAX(data_version) dv FROM nfl_games "
                 "GROUP BY game_id) v ON v.game_id = g.game_id AND v.dv = g.data_version")}
    rel = con.execute("SELECT rel_path FROM nflverse_versions WHERE dataset = 'games' AND "
                      "data_version = ? ORDER BY ingested_ts DESC LIMIT 1",
                      (versions["nfl_games"],)).fetchone()
    if rel is None:
        raise RuntimeError(f"no archived games.parquet for data_version {versions['nfl_games']}")
    sched = pl.read_parquet(config.storage_path("raw", *rel[0].split("/")),
                            columns=["game_id", "gameday", "location", "div_game", "stadium_id",
                                     "stadium", "roof", "temp", "wind"])
    sched = {r["game_id"]: r for r in sched.iter_rows(named=True)}
    units = {}
    for wk, team, opp, py, ry, ints in con.execute(
            "SELECT p.week, p.team, p.opponent, SUM(p.passing_yards), SUM(p.rushing_yards), "
            "SUM(p.interceptions) FROM nfl_player_week p JOIN (SELECT gsis_id, season, week, "
            "season_type, MAX(data_version) dv FROM nfl_player_week WHERE season = ? "
            "GROUP BY 1, 2, 3, 4) v ON v.gsis_id = p.gsis_id AND v.season = p.season AND "
            "v.week = p.week AND v.season_type = p.season_type AND v.dv = p.data_version "
            "WHERE p.season = ? GROUP BY 1, 2, 3", (year, year)):
        units[(wk, G.franchise(team))] = {"opp": G.franchise(opp), "pass": py or 0.0,
                                          "rush": ry or 0.0, "int": ints or 0.0}
    team_week, tw_why = read_team_week(con, year)
    return {"lines": lines, "sched": sched, "units": units, "rel": rel[0],
            "team_week": team_week, "team_week_reason": tw_why}


TEAM_WEEK_READ = ("passing_yards", "rushing_yards", "passing_interceptions", "passing_epa",
                  "rushing_epa", "carries")


def read_team_week(con, year):
    """-> ({(game_id, franchise): row}, reason). nflverse's own team-game totals
    (stats_team, a-66), newest data_version per team-game. A store without the
    table, or without this season in it, returns nothing AND SAYS SO: the units
    then fall back to the player rows and the EPA figures are absent with the
    reason, never zero."""
    import sqlite3
    try:
        rows = con.execute(
            "SELECT t.game_id, t.team, " + ", ".join("t." + c for c in TEAM_WEEK_READ) +
            " FROM nfl_team_week t JOIN (SELECT team, season, week, season_type, "
            "MAX(data_version) dv FROM nfl_team_week WHERE season = ? GROUP BY 1, 2, 3, 4) v "
            "ON v.team = t.team AND v.season = t.season AND v.week = t.week AND "
            "v.season_type = t.season_type AND v.dv = t.data_version WHERE t.season = ?",
            (year, year)).fetchall()
    except sqlite3.OperationalError as e:
        return {}, f"the store holds no team-game totals ({e})"
    out = {(r[0], G.franchise(r[1])): dict(zip(TEAM_WEEK_READ, r[2:])) for r in rows
           if r[0] is not None}
    return out, (None if out else f"the store holds no team-game totals for {year}")


def read_forecasts(as_of_ts, game_ids):
    """-> ({game_id: forecast row}, reason). ONLY forecasts taken before each kickoff
    and before `as_of_ts` (feeds.weather_read.pregame_forecasts): recorded weather has
    no path into a file built before a game."""
    from feeds import weather_read as W
    try:
        con = W.connect_ro()
        try:
            return W.pregame_forecasts(con, as_of_ts, game_ids), None
        finally:
            con.close()
    except Exception as e:  # noqa: BLE001 - no feeds store: the forecast is absent
        return {}, f"the weather store could not be read ({type(e).__name__})"


def read_pace(by_id):
    from research import game_total as T
    try:
        pace, _drives, _un = T.load_pace(by_id)
        return pace, None
    except Exception as e:  # noqa: BLE001 - no analytics store: the pace figures are absent
        return {}, f"{type(e).__name__}: {e}"


def venues():
    from feeds import nfl_venues as V
    return V.load_crosswalk(), V.load_points()


def venue_of(row, cross, points):
    from feeds import nfl_venues as V
    if row is None:
        return None, "no schedule row"
    return V.resolve(row["stadium_id"], row["stadium"], cross, points)


# =============================================================================
# the blocks
# =============================================================================

def played_before(games, team, year, cutoff):
    """This season's scored games of `team` (franchise) that kicked off before `cutoff`."""
    out = [g for g in games if g["season"] == year and g["home_score"] is not None
           and g["kickoff_ts"] is not None and g["kickoff_ts"] < cutoff
           and team in (G.franchise(g["home"]), G.franchise(g["away"]))]
    return sorted(out, key=lambda g: (g["kickoff_ts"], g["game_id"]))


def result_for(g, team):
    home = G.franchise(g["home"]) == team
    pf, pa = (g["home_score"], g["away_score"]) if home else (g["away_score"], g["home_score"])
    opp = G.franchise(g["away"] if home else g["home"])
    return {"game_id": g["game_id"], "week": g["week"], "opponent": opp, "home": home,
            "points_for": pf, "points_against": pa,
            "result": "win" if pf > pa else ("loss" if pf < pa else "tie")}


def team_block(games, team, year, week, cutoff, snaps, ctx, pace, pace_err):
    played = played_before(games, team, year, cutoff)
    res = [result_for(g, team) for g in played]
    n = len(res)
    rating = snaps[(year, 99)].get(team, G.MEAN)
    path = [{"after_week": k, "rating": r1(snaps[(year, k)].get(team, G.MEAN))}
            for k in range(0, week) if (year, k) in snaps]
    opp = [r["opponent"] for r in res]
    sos = (sum(snaps[(year, 99)].get(o, G.MEAN) for o in opp) / len(opp)) if opp else None
    rec = Counter(r["result"] for r in res)
    pf = sum(r["points_for"] for r in res)
    pa = sum(r["points_against"] for r in res)
    # efficiency first, then plays (c-31: points per play is the sticky factor)
    off = [pace.get((r["game_id"], team)) for r in res]
    dfn = [pace.get((r["game_id"], r["opponent"])) for r in res]
    covered = sum(1 for a, b in zip(off, dfn) if a and b)
    if n and covered == n:
        opl, dpl = sum(off), sum(dfn)
        eff = {"points_per_play_for": r4(pf / opl), "points_per_play_against": r4(pa / dpl),
               "plays_per_game_for": r1(opl / n), "plays_per_game_against": r1(dpl / n),
               "reason": None}
    else:
        why = (pace_err or f"team pace is built for {covered} of the {n} games this team has "
                           f"played this season, and a partial season is not published")
        eff = {"points_per_play_for": None, "points_per_play_against": None,
               "plays_per_game_for": None, "plays_per_game_against": None,
               "reason": why if n else "no game played yet this season"}
    eff["games_with_pace"] = covered
    units = units_block(res, team, n, ctx)
    return {"team": team, "games": n,
            "record": {"wins": rec["win"], "losses": rec["loss"], "ties": rec["tie"]},
            "rating": {"current": r1(rating), "path": path, "league_mean": G.MEAN},
            "points_per_game_for": r1(pf / n) if n else None,
            "points_per_game_against": r1(pa / n) if n else None,
            "efficiency": eff, "units": units,
            "schedule_strength": {"opponents_mean_rating": r1(sos), "opponents": len(opp),
                                  "definition": ("the mean of the current ratings of the "
                                                 "opponents played so far this season")},
            "results": res}


def _complete(rows, cols):
    return bool(rows) and all(r is not None and all(r.get(c) is not None for c in cols)
                              for r in rows)


def units_block(res, team, n, ctx):
    """Passing and rushing by unit. From nflverse's team-game totals where they cover
    EVERY game this team has played (both sides of each), else from the player rows;
    `source` says which. The EPA figures exist only on the team rows. Fumbles are
    published from neither, as a-64 left them. That is a choice carried forward, not
    a measured defect: the team's fumbles_lost_total equals the player rows' on all
    but 6 team-games in 28 seasons (research/stats_team_audit.py)."""
    tw = ctx.get("team_week") or {}
    t_for = [tw.get((r["game_id"], team)) for r in res]
    t_ag = [tw.get((r["game_id"], r["opponent"])) for r in res]
    yards = ("passing_yards", "rushing_yards", "passing_interceptions")
    epa = ("passing_epa", "rushing_epa", "carries")
    out = {"epa_definition": ("expected points added, as nflverse sums it per team-game: "
                              "rushing over every carry, passing over its own set of pass "
                              "plays, which is why passing has no per-play figure here")}
    if n and _complete(t_for, yards) and _complete(t_ag, yards):
        out.update({
            "source": "team_rows",
            "passing_yards_per_game_for": r1(sum(t["passing_yards"] for t in t_for) / n),
            "rushing_yards_per_game_for": r1(sum(t["rushing_yards"] for t in t_for) / n),
            "passing_yards_per_game_against": r1(sum(t["passing_yards"] for t in t_ag) / n),
            "rushing_yards_per_game_against": r1(sum(t["rushing_yards"] for t in t_ag) / n),
            "interceptions_thrown": int(sum(t["passing_interceptions"] for t in t_for)),
            "interceptions_made": int(sum(t["passing_interceptions"] for t in t_ag)),
            "definition": ("nflverse's team-game totals: passing yards are gross (before "
                           "sacks)"),
            "reason": None})
    else:
        u_for = [ctx["units"].get((r["week"], team)) for r in res]
        u_ag = [ctx["units"].get((r["week"], r["opponent"])) for r in res]
        ok = n and all(u_for) and all(u_ag)
        out.update({
            "source": "player_rows" if ok else None,
            "passing_yards_per_game_for": r1(sum(u["pass"] for u in u_for) / n) if ok else None,
            "rushing_yards_per_game_for": r1(sum(u["rush"] for u in u_for) / n) if ok else None,
            "passing_yards_per_game_against": r1(sum(u["pass"] for u in u_ag) / n) if ok else None,
            "rushing_yards_per_game_against": r1(sum(u["rush"] for u in u_ag) / n) if ok else None,
            "interceptions_thrown": int(sum(u["int"] for u in u_for)) if ok else None,
            "interceptions_made": int(sum(u["int"] for u in u_ag)) if ok else None,
            "definition": ("summed from player rows: passing yards are gross (before sacks), "
                           "as credited to passers"),
            "reason": None if ok else ("no game played yet this season" if not n else
                                       "a played game has no player rows for one side")})
    if n and _complete(t_for, epa) and _complete(t_ag, epa):
        car_for, car_ag = sum(t["carries"] for t in t_for), sum(t["carries"] for t in t_ag)
        out.update({
            "passing_epa_per_game_for": r4(sum(t["passing_epa"] for t in t_for) / n),
            "passing_epa_per_game_against": r4(sum(t["passing_epa"] for t in t_ag) / n),
            "rushing_epa_per_game_for": r4(sum(t["rushing_epa"] for t in t_for) / n),
            "rushing_epa_per_game_against": r4(sum(t["rushing_epa"] for t in t_ag) / n),
            "rushing_epa_per_carry_for": r4(sum(t["rushing_epa"] for t in t_for) / car_for)
            if car_for else None,
            "rushing_epa_per_carry_against": r4(sum(t["rushing_epa"] for t in t_ag) / car_ag)
            if car_ag else None,
            "epa_reason": None})
    else:
        covered = sum(1 for a, b in zip(t_for, t_ag)
                      if _complete([a], epa) and _complete([b], epa))
        out.update({
            "passing_epa_per_game_for": None, "passing_epa_per_game_against": None,
            "rushing_epa_per_game_for": None, "rushing_epa_per_game_against": None,
            "rushing_epa_per_carry_for": None, "rushing_epa_per_carry_against": None,
            "epa_reason": ("no game played yet this season" if not n else
                           ctx.get("team_week_reason") or
                           f"team-game totals cover {covered} of the {n} games this team has "
                           f"played this season, and a partial season is not published")})
    return out


def head_to_head(games, stadium, home, away, cutoff, year):
    meet = [g for g in games if g["home_score"] is not None and g["kickoff_ts"] is not None
            and g["kickoff_ts"] < cutoff
            and {G.franchise(g["home"]), G.franchise(g["away"])} == {home, away}]
    meet.sort(key=lambda g: (g["kickoff_ts"], g["game_id"]), reverse=True)

    def split(gs):
        c = Counter()
        for g in gs:
            m = g["home_score"] - g["away_score"]
            w = G.franchise(g["home"]) if m > 0 else (G.franchise(g["away"]) if m < 0 else None)
            c["ties" if w is None else ("home_side" if w == home else "away_side")] += 1
        return {"games": len(gs), "home_side_wins": c["home_side"],
                "away_side_wins": c["away_side"], "ties": c["ties"]}

    rows = [{"game_id": g["game_id"], "season": g["season"], "week": g["week"],
             "game_type": g["game_type"], "date": g.get("gameday"),
             "home": g["home"], "away": g["away"], "home_score": g["home_score"],
             "away_score": g["away_score"], "venue": stadium.get(g["game_id"])} for g in meet]
    first = min(g["season"] for g in games)
    # common opponents this season
    mine = {t: {} for t in (home, away)}
    for t in (home, away):
        for g in played_before(games, t, year, cutoff):
            r = result_for(g, t)
            if r["opponent"] not in (home, away):
                mine[t].setdefault(r["opponent"], []).append(r)
    common = [{"opponent": o, "home_team": mine[home][o], "away_team": mine[away][o]}
              for o in sorted(set(mine[home]) & set(mine[away]))]
    return {"meetings": rows,
            "sides": ("home_side is the team at home in THIS game and away_side its opponent, "
                      "whoever hosted each meeting"),
            "all_time": split(meet), "recent": split(meet[:RECENT_MEETINGS]),
            "recent_n": RECENT_MEETINGS, "record_starts": first,
            "common_opponents": common}


def rest_of(games, team, g, year):
    prev = [x for x in played_or_scheduled(games, team, year) if x["kickoff_ts"] < g["kickoff_ts"]]
    if not prev:
        return {"days": None, "short_week": None, "off_bye": None,
                "reason": "first game of the season"}
    p = prev[-1]
    import datetime as dt
    days = (dt.date.fromisoformat(g["gameday"]) - dt.date.fromisoformat(p["gameday"])).days
    return {"days": days, "short_week": days < SHORT_WEEK_DAYS,
            "off_bye": g["week"] - p["week"] >= BYE_GAP_WEEKS, "reason": None}


def played_or_scheduled(games, team, year):
    return sorted((x for x in games if x["season"] == year and x["kickoff_ts"] is not None
                   and team in (G.franchise(x["home"]), G.franchise(x["away"]))),
                  key=lambda x: (x["kickoff_ts"], x["game_id"]))


def home_venue(games, team, year, sched):
    """The (stadium_id, stadium) this team hosts most of its own-site games at this season."""
    c = Counter()
    for x in played_or_scheduled(games, team, year):
        r = sched.get(x["game_id"])
        if r and G.franchise(x["home"]) == team and r["location"] == "Home":
            c[(r["stadium_id"], r["stadium"])] += 1
    return c.most_common(1)[0][0] if c else None


def situation(games, g, year, ctx, cross, points, wind_fit, assumed_wind):
    from feeds import nfl_venues as V
    row = ctx["sched"].get(g["game_id"])
    venue, why = venue_of(row, cross, points)
    out = {"venue": {"name": row["stadium"] if row else None,
                     "neutral_site": (row["location"] == "Neutral") if row else None,
                     "division_game": bool(row["div_game"]) if row and row["div_game"] is not None
                     else None,
                     "roof_structure": venue["roof_type"] if venue else None,
                     "roof_this_game": (row["roof"] or None) if row else None,
                     "open_to_sky": (V.playing_conditions(venue["roof_type"], row["roof"])
                                     if venue and row else None),
                     "country_code": venue["country_code"] if venue else None,
                     "international": (venue["country_code"] != HOME_COUNTRY)
                     if venue and venue["country_code"] else None,
                     "international_reason": None if venue and venue["country_code"] else (
                         "the venue has no sourced country; the site is named, and "
                         "neutral_site says whether it is either team's home")},
           "teams": {}}
    for side in ("home", "away"):
        t = G.franchise(g[side])
        rest = rest_of(games, t, g, year)
        hv = home_venue(games, t, year, ctx["sched"])
        km, kwhy, tz, tzwhy = None, None, None, None
        if venue is None:
            kwhy = tzwhy = f"the game's venue has no sourced coordinate ({why})"
        elif hv is None:
            kwhy = tzwhy = "no home venue in this season's schedule"
        else:
            base, bwhy = V.resolve(hv[0], hv[1], cross, points)
            if base is None:
                kwhy = tzwhy = f"the team's home venue has no sourced coordinate ({bwhy})"
            else:
                km = round(V.haversine_km(base["latitude"], base["longitude"],
                                          venue["latitude"], venue["longitude"]))
                z = V.zones_crossed(base["timezone"], venue["timezone"], g["kickoff_ts"])
                if z is None:
                    tzwhy = "one of the two venues has no sourced time zone"
                elif not float(z).is_integer():
                    tzwhy = f"local time at the two venues differs by {z} hours, not a whole number"
                else:
                    tz = int(z)
        out["teams"][side] = {"team": t, "rest": rest,
                              "travel_km": km, "travel_definition":
                                  "great-circle distance from the team's home stadium to "
                                  "this game's stadium; not the route travelled",
                              "travel_reason": kwhy,
                              "time_zones_crossed": tz,
                              "time_zones_definition":
                                  "hours between local time at the team's home stadium and local "
                                  "time at this game's stadium at kickoff, the short way round",
                              "time_zones_reason": tzwhy}
    rec_wind = row["wind"] if row else None
    rec_temp = row["temp"] if row else None
    out["weather"] = {
        "recorded_wind_mph": None if rec_wind is None else float(rec_wind),
        "recorded_temp_f": None if rec_temp is None else float(rec_temp),
        "recorded_reason": None if rec_wind is not None else
        "nflverse records wind and temperature after the game",
        **forecast_fields(g, venue, ctx),
        "wind_effect_on_total": {"per_mph": wind_fit,
                                 "measured_on": "recorded game-day wind, 2001 to 2025 (c-31)"},
        "total_assumes_wind_mph": assumed_wind,
    }
    return out


def forecast_fields(g, venue, ctx):
    """The pre-game forecast for this kickoff, with how far ahead it was taken - or
    nulls and the reason. ctx["forecast"] holds only rows feeds.weather_read let
    through: kind forecast, taken before the kickoff and before the build."""
    from jobs.game_export import iso
    f = (ctx.get("forecast") or {}).get(g["game_id"])
    out = {"forecast_wind_mph": None, "forecast_temp_f": None, "forecast_lead_hours": None,
           "forecast_taken": None,
           "forecast_source": ("Open-Meteo's forecast model for the grid cell holding the "
                               "stadium, at the kickoff hour; not a measurement at the "
                               "stadium"),
           "forecast_reason": None}
    if f is None:
        out["forecast_reason"] = (
            ctx.get("forecast_reason")
            or ("the venue has a fixed roof, so no forecast is read for it"
                if venue and venue["roof_type"] == "fixed" else
                "the store holds no forecast taken before this kickoff"))
        return out
    out.update(forecast_wind_mph=r1(f["wind_speed_mph"]), forecast_temp_f=r1(f["temperature_f"]),
               forecast_lead_hours=r1(f["lead_hours"]), forecast_taken=iso(f["fetched_ts"]))
    return out


# =============================================================================
# the numbers
# =============================================================================

def model_notes(row):
    """Facts about the model that bear on THIS game, generated from the game."""
    notes = []
    if row is not None and row["location"] == "Neutral":
        notes.append("This game is at a neutral site. The model still gives the designated "
                     "home team its home-field advantage, as it does for every game.")
    return notes


def total_forecast(g, i, games, c31, year, states, fc, wx, assumed_wind):
    from models import game_total as GT
    kp, rp, kq, rq = c31["factor_params"][str(year)]
    st = c31["stacks"]["FULL"][str(year)]
    th = GT.team_expectation(states[(i, "home")], kp, rp, kq, rq)
    ta = GT.team_expectation(states[(i, "away")], kp, rp, kq, rq)
    raw = th[0] * th[1] + ta[0] * ta[1]
    eam = GT.expected_abs_normal(fc.mu_m, fc.sigma_m)
    wind_out, dome, wind_na = wx
    if wind_na:                     # a game not yet played has no recorded wind:
        wind_out, wind_na = assumed_wind, 0.0     # the training mean, stated in the file
    x = [1.0, raw, eam, wind_out, dome, wind_na]
    mu = float(np.dot(st["beta"], x))
    return GT.GameTotalForecast(base=fc, mu=mu, gamma=st["gamma"], s_e=st["s_e"]), {
        "home_plays": r1(th[0]), "home_points_per_play": r4(th[1]),
        "away_plays": r1(ta[0]), "away_points_per_play": r4(ta[1])}


def pace_complete(games, year, cutoff, pace):
    """-> (complete, played team-games, covered team-games) for this season so far."""
    tg = [(g["game_id"], G.franchise(t)) for g in games if g["season"] == year
          and g["home_score"] is not None and g["kickoff_ts"] is not None
          and g["kickoff_ts"] < cutoff for t in (g["home"], g["away"])]
    cov = sum(1 for k in tg if pace.get(k))
    return cov == len(tg), len(tg), cov


def close_ref(key, path, d_brier, verdict_word, qualifier):
    """A copy of a record's figure. a-77: it prints the digits, so it carries the
    owner's sentence as well as the pointer; `qualifier` is the owner's, null when
    that figure needs none. `jobs.required_sentences.require` refuses the copy of a
    registered figure without it."""
    return {"record": key, "path": path, "d_brier": d_brier, "compared": verdict_word,
            "qualifier": qualifier, "display": SHOW_CLOSE}


def beat_ref(b):
    """A copy of one `beats` row, with the owner's sentence (a-77)."""
    return {"id": b["id"], "d_brier": b["d_brier"], "compared": b["compared"],
            "qualifier": b.get("qualifier")}


def numbers(g, row, fc, km, tot, tot_why, feats, rec_ml, rec_sp, rec_tt, sp_why):
    line = row or {}
    p_mkt = devig(line.get("home_moneyline"), line.get("away_moneyline"))
    mc = rec_ml["market_comparison"]
    ml = {"model": {"p_home_win": r4(fc.p_home)},
          "market": {"p_home_win": r4(p_mkt)},
          "difference": {"p_home_win": r4(fc.p_home - p_mkt) if p_mkt is not None else None},
          "record": {"file": "game/nfl/record.json",
                     "beats": [beat_ref(b) for b in rec_ml["baselines"]],
                     "vs_close": close_ref("game/nfl/record.json", "market_comparison.d_brier",
                                           mc["d_brier"], compared(mc["d_brier"]["verdict"]),
                                           None)}}
    sl = line.get("spread_line")
    if km is not None:
        mean = km.margin_mean()
        pc = km.prob_cover(sl) if sl is not None else None
        push = km.prob_margin_eq(sl) if sl is not None and float(sl).is_integer() else (
            0.0 if sl is not None else None)
        model_sp = {"home_margin_mean": r1(mean), "p_home_covers": r4(pc), "p_push": r4(push),
                    "scored": {"home_margin_mean": False, "p_home_covers": True},
                    "reason": None}
    else:
        model_sp = {"home_margin_mean": None, "p_home_covers": None, "p_push": None,
                    "scored": {"home_margin_mean": False, "p_home_covers": True},
                    "reason": sp_why}
    mk_pc = devig(line.get("home_spread_odds"), line.get("away_spread_odds"))
    spread = {"model": model_sp,
              "market": {"home_margin": sl, "p_home_covers": r4(mk_pc)},
              "difference": {
                  "home_margin": r1(model_sp["home_margin_mean"] - sl)
                  if model_sp["home_margin_mean"] is not None and sl is not None else None,
                  "p_home_covers": r4(model_sp["p_home_covers"] - mk_pc)
                  if model_sp["p_home_covers"] is not None and mk_pc is not None else None},
              "unscored": ["The mean margin is not scored: only the chance of covering is."],
              "record": {"file": RECORD_SPREAD_KEY,
                         "beats": [],
                         "vs_close": close_ref(RECORD_SPREAD_KEY, "vs_close.d_brier",
                                               rec_sp["vs_close"]["d_brier"],
                                               rec_sp["vs_close"]["compared"],
                                               rec_sp["vs_close"]["qualifier"])}}
    tl = line.get("total_line")
    mk_po = devig(line.get("over_odds"), line.get("under_odds"))
    if tot is not None:
        model_t = {"mean": r1(tot.mean()),
                   "p_over": r4(tot.prob_over_push_void(tl)) if tl is not None else None,
                   "components": feats, "reason": None}
    else:
        model_t = {"mean": None, "p_over": None, "components": None, "reason": tot_why}
    total = {"model": model_t,
             "market": {"total": tl, "p_over": r4(mk_po)},
             "difference": {
                 "total": r1(model_t["mean"] - tl) if model_t["mean"] is not None
                 and tl is not None else None,
                 "p_over": r4(model_t["p_over"] - mk_po) if model_t["p_over"] is not None
                 and mk_po is not None else None},
             "record": {"file": RECORD_TOTAL_KEY,
                        "beats": [beat_ref(b) for b in rec_tt["against_baselines"]],
                        "vs_close": close_ref(RECORD_TOTAL_KEY, "vs_close.d_brier",
                                              rec_tt["vs_close"]["d_brier"],
                                              rec_tt["vs_close"]["compared"],
                                              rec_tt["vs_close"]["qualifier"])}}
    return {"moneyline": ml, "spread": spread, "total": total}


# =============================================================================
# assembly
# =============================================================================

def prepare(m, week, now_ts, con=None, ctx=None, pace=None, pace_err=None):
    """-> everything the per-game model objects are built from, read once: the
    walk's parameters and margin sd, c-30's and c-31's frozen fits (and why either
    is withheld), the schedule context, team pace, the rating snapshots and the
    total's factor states. `build` and jobs.appealing_export both start here, so a
    rung priced there and a matchup built here are one computation (a-70)."""
    from jobs import season_model as S
    from models import season as M
    from research import game_forecast as GF
    from research import game_total as T
    games, year, walk = m["games"], m["year"], m["walk"]
    params = walk.params(year)
    sigma_exact = GF.margin_sigmas(games, walk, [year])[year]
    c30, c31, problems = frozen(year, params.as_dict(), sigma_exact)
    if ctx is None:
        own = con is None
        con = S.market_log_ro() if own else con
        try:
            ctx = read_context(con, year, m["versions"])
        finally:
            if own:
                con.close()
    by_id = {g["game_id"]: g for g in games}
    if pace is None:
        pace, pace_err = read_pace(by_id)
    _pre, snaps = M.run_elo(
        games, params, snapshot_at=[(year, k) for k in range(0, week)] + [(year, 99)])
    ok, n_tg, n_cov = pace_complete(games, year, now_ts, pace)
    states = T.factor_states(games, pace) if ok and "total" not in problems else {}
    tot_why = problems.get("total") or (None if ok else (
        pace_err or f"team pace is built for {n_cov} of the {n_tg} team-games played this "
                    f"season; c-31's factors drop a game with no pace row, so the total would "
                    f"read a stale season"))
    # the wind a not-yet-played game is forecast at: the training mean, stated
    outdoor = [float(r["wind"]) for gid, r in ctx["sched"].items()
               if r["wind"] is not None and not (isinstance(r["wind"], float) and math.isnan(r["wind"]))
               and (r["roof"] or "") in ("outdoors", "open")
               and by_id.get(gid) and 2000 <= by_id[gid]["season"] < year]
    assumed = r1(sum(outdoor) / len(outdoor)) if outdoor else None
    return {"params": params, "sigma": sigma_exact, "c30": c30, "c31": c31,
            "problems": problems, "ctx": ctx, "by_id": by_id, "pace": pace,
            "pace_err": pace_err, "snaps": snaps, "states": states, "tot_why": tot_why,
            "assumed": assumed}


def game_objects(P, m, gid, as_of):
    """-> (i, g, fc, km, tot, feats, why the total is absent). The three model
    objects for one game, from `prepare`'s state: c-28's forecast, c-30's
    key-number margin on it (None when the fit is withheld) and c-31's total (None
    with the reason)."""
    from models import key_margin as KM
    from research import game_total as T
    games, year = m["games"], m["year"]
    snaps, ctx = P["snaps"], P["ctx"]
    i = next(j for j, x in enumerate(games) if x["game_id"] == gid)
    g = games[i]
    # p_home as published is rounded to 4 dp; the objects take the walk's own value
    rh = snaps[(year, 99)].get(G.franchise(g["home"]), G.MEAN)
    ra = snaps[(year, 99)].get(G.franchise(g["away"]), G.MEAN)
    _d, p = G.pregame(rh, ra, P["params"])
    fc = G.GameForecast(game_id=gid, home=g["home"], away=g["away"], as_of=as_of,
                        p_home=p, sigma_m=P["sigma"], mu_t=0.0, sigma_t=1.0)
    km = None
    if "spread" not in P["problems"]:
        f30 = P["c30"]["fits"][str(year)]
        km = KM.KeyMarginForecast(base=fc, w=tuple(f30["w"]), t=f30["t"])
    tot, feats = None, None
    if not P["tot_why"]:
        if (i, "home") in P["states"] and (i, "away") in P["states"]:
            wx = T.weather_x(((ctx["sched"].get(gid) or {}).get("roof") or "", None))
            tot, feats = total_forecast(g, i, games, P["c31"], year, P["states"], fc, wx,
                                        P["assumed"])
    why = P["tot_why"] or (None if tot else "no factor state for one of the teams")
    return i, g, fc, km, tot, feats, why


def build(m, forecast, record, now_ts, con=None, log=print, ctx=None, pace=None,
          pace_err=None):
    """-> ({key: body}, {key: error}). `forecast` and `record` are the bodies
    jobs.game_export built this run. A matchup that fails on its own is reported and
    the others are still built. `ctx` and `pace` are read from the stores unless
    given (the tests give them; nothing else should)."""
    games, year = m["games"], m["year"]
    out, failed = {}, {}
    if not forecast["games"]:
        return out, failed
    week = forecast["week"]
    P = prepare(m, week, now_ts, con=con, ctx=ctx, pace=pace, pace_err=pace_err)
    c30, c31, problems, ctx = P["c30"], P["c31"], P["problems"], P["ctx"]
    pace, pace_err, snaps, tot_why, assumed = (P["pace"], P["pace_err"], P["snaps"],
                                               P["tot_why"], P["assumed"])
    rec_sp = build_record_spread(c30)
    rec_tt = build_record_total(c31)
    out[RECORD_SPREAD_KEY] = rec_sp
    out[RECORD_TOTAL_KEY] = rec_tt
    if "forecast" not in ctx:
        ctx["forecast"], ctx["forecast_reason"] = read_forecasts(
            now_ts, [r["game_id"] for r in forecast["games"]])
    cross, points = venues()
    cutoff = now_ts
    wind_fit = gi(c31["diagnostics"]["D4"]["i_full_stack"])
    stadium = {gid: (r["stadium"] if r else None) for gid, r in ctx["sched"].items()}
    for g in games:
        g.setdefault("gameday", (ctx["sched"].get(g["game_id"]) or {}).get("gameday"))
    idx_rows = []
    fc_by_id = {r["game_id"]: r for r in forecast["games"]}
    for gid, frow in fc_by_id.items():
        key = f"{MATCHUP_DIR}{gid}.json"
        try:
            i, g, fc, km, tot, feats, this_tot_why = game_objects(
                P, m, gid, forecast["as_of"]["instant"])
            home, away = G.franchise(g["home"]), G.franchise(g["away"])
            body = {
                "season": year, "week": week, "game_id": gid,
                "kickoff": frow["kickoff"], "home": g["home"], "away": g["away"],
                "as_of": {"instant": forecast["as_of"]["instant"],
                          "results_through": forecast["as_of"]["results_through"],
                          "market_line_version": (ctx["lines"].get(gid) or {}).get("data_version"),
                          "versions": dict(m["versions"])},
                "numbers": numbers(g, ctx["lines"].get(gid), fc, km, tot, this_tot_why, feats,
                                   record, rec_sp, rec_tt, problems.get("spread")),
                "margin": frow["margin"],
                "teams": {"home": team_block(games, home, year, week, cutoff, snaps, ctx, pace,
                                             pace_err),
                          "away": team_block(games, away, year, week, cutoff, snaps, ctx, pace,
                                             pace_err)},
                "head_to_head": head_to_head(games, stadium, home, away, cutoff, year),
                "situation": situation(games, g, year, ctx, cross, points, wind_fit, assumed),
                "market_source": {"name": "the nflverse schedule's line",
                                  "provenance_recorded": False,
                                  "note_id": "nflverse_line_unattributed"},
                "forecast": "game/nfl/forecast.json", "season_stage": forecast["season_stage"],
                "model_notes": model_notes(ctx["sched"].get(gid)),
            }
            out[key] = body
            n = body["numbers"]
            idx_rows.append({"game_id": gid, "key": key, "kickoff": frow["kickoff"],
                             "home": g["home"], "away": g["away"],
                             "difference": {"p_home_win": n["moneyline"]["difference"]["p_home_win"],
                                            "home_margin": n["spread"]["difference"]["home_margin"],
                                            "total": n["total"]["difference"]["total"]}})
        except Exception as e:  # noqa: BLE001 - one game's failure is that game's
            failed[key] = f"{type(e).__name__}: {e}"
    out[INDEX_KEY] = {"season": year, "week": week,
                      "as_of": {"instant": forecast["as_of"]["instant"],
                                "results_through": forecast["as_of"]["results_through"]},
                      "games": sorted(idx_rows, key=lambda r: (r["kickoff"], r["game_id"])),
                      "records": {"moneyline": "game/nfl/record.json",
                                  "spread": RECORD_SPREAD_KEY, "total": RECORD_TOTAL_KEY}}
    log(f"matchup: {len(idx_rows)} games built, {len(failed)} failed; total "
        f"{'published' if not tot_why else 'withheld: ' + tot_why}; spread "
        f"{'published' if 'spread' not in problems else 'withheld: ' + problems['spread']}")
    return out, failed
