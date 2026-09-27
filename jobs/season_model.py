"""Chance to win the division, with its walk-forward record in the same file (a-41).

    python -m jobs.season_model --check            # measure, build, validate; write nothing
    python -m jobs.season_model --write            # ... and write the file locally
    python -m jobs.season_model --record-json r.json   # also save the raw record

A PREDICTOR SHIPS WITH ITS RECORD, IN THE SAME FILE, KEYED TO THE SAME SLICE -
the rule `analytics/predictor_export.py` states for drafting, applied to the
season model. The file carries the current probabilities, their Monte Carlo
error, and how the same procedure did on every season it could not see.

WALK-FORWARD, EXACTLY. For season T the model's three constants are chosen by
grid search on game-level log loss over seasons 2000..T-1 only (1999 is burn-in:
every rating starts at 1500). Ratings entering a state are a function of games
already played. Seasons 2002-2025 are scored: 2002 is the first season of the
eight four-team divisions, and a forecast needs a realised winner.

THE REALISED WINNER IS READ FROM THE POSTSEASON, NOT FROM OUR TIEBREAKERS. A
division winner is seeded 1-4 and so either hosts a wild-card game or has a
bye and hosts a divisional game; no wild card ever hosts a wild-card game. So
winners = wild-card hosts, plus divisional hosts that played no wild-card game.
That is independent of `models.season`'s tiebreak code, which is what lets the
tiebreak code be CHECKED against it (`tiebreak_check`).

READS `market_log.db` READ-ONLY (`mode=ro`) and nothing else. Writes only its
own prefix, `season/`, under `config.storage_path("season_model")`; never
WEB_EXPORT_DIR, never an upload. It does not publish.
"""
from __future__ import annotations

import argparse
import datetime as dt
import itertools
import json
import math
import os
import sqlite3
import sys
import time
import zlib

import numpy as np

import config
from models import season as M

SPORT = "nfl"
MODEL = "division"
OWNED_PREFIX = "season/"
KEY = "%s%s/%s.json" % (OWNED_PREFIX, SPORT, MODEL)

FIT_FROM = 2000                 # 1999 is burn-in
SCORE_FROM, SCORE_TO = 2002, 2025
GRID = {"k": [10.0, 15.0, 20.0, 25.0, 30.0, 40.0],
        "hfa": [0.0, 25.0, 50.0, 75.0],
        "regress": [0.0, 0.25, 1.0 / 3.0, 0.5, 0.6, 0.75]}
N_WALK = 4000                   # sims per walk-forward state
N_CURRENT = 20000               # sims for the published forecast
N_BOOT = 2000
ALPHA = 0.05
MIN_READABLE_BLOCKS = 5
OPEN_MAX = 0.9                  # a race is "open" while no club's coin-flip p >= this


# ------------------------------------------------------------------ loading

def market_log_ro():
    path = os.path.abspath(config.DB_PATH)
    uri = "file:" + path.replace("\\", "/") + "?mode=ro"
    return sqlite3.connect(uri, uri=True, timeout=5)


def load(con):
    """(games, groupings, versions). Newest data_version per game_id."""
    q = ("SELECT g.game_id, g.season, g.week, g.game_type, g.kickoff_ts, "
         "g.home_team, g.away_team, g.home_score, g.away_score, g.data_version "
         "FROM nfl_games g JOIN (SELECT game_id, MAX(data_version) dv FROM nfl_games "
         "GROUP BY game_id) v ON v.game_id = g.game_id AND v.dv = g.data_version")
    games = [dict(game_id=r[0], season=r[1], week=r[2], game_type=r[3],
                  kickoff_ts=r[4], home=r[5], away=r[6], home_score=r[7],
                  away_score=r[8], data_version=r[9])
             for r in con.execute(q)]
    rows = con.execute(
        "SELECT t.team_abbr, t.team_conf, t.team_division, t.data_version FROM nfl_teams t "
        "JOIN (SELECT team_abbr, MAX(data_version) dv FROM nfl_teams GROUP BY team_abbr) v "
        "ON v.team_abbr = t.team_abbr AND v.dv = t.data_version").fetchall()
    groupings = {a: (c, d) for a, c, d, _ in rows}
    versions = {"nfl_games": max(g["data_version"] for g in games),
                "nfl_teams": max(r[3] for r in rows)}
    if not games or len(groupings) < 32:
        raise RuntimeError("loaded %d games and %d team groupings - refusing"
                           % (len(games), len(groupings)))
    return games, groupings, versions


def season_at(games, groupings, year, through_week):
    """`models.season.Season` for `year` with results for REG weeks <= k."""
    reg = [g for g in games if g["season"] == year and g["game_type"] == "REG"]
    teams = sorted({g["home"] for g in reg} | {g["away"] for g in reg})
    missing = [t for t in teams if t not in groupings]
    if missing:
        raise RuntimeError("no division for %s in %d" % (missing, year))
    sched = []
    for g in sorted(reg, key=lambda g: (g["week"], g["kickoff_ts"] or 0, g["game_id"])):
        res = None
        if g["week"] <= through_week and g["home_score"] is not None:
            d = g["home_score"] - g["away_score"]
            res = 1.0 if d > 0 else (0.0 if d < 0 else 0.5)
        sched.append((g["home"], g["away"], g["week"], res))
    return M.Season(teams=teams,
                    division={t: groupings[t][1] for t in teams},
                    conference={t: groupings[t][0] for t in teams},
                    games=sched)


def realised_winners(games, groupings, year):
    """{division: winner} from postseason hosting. Raises unless 8 are found."""
    post = [g for g in games if g["season"] == year and g["game_type"] in ("WC", "DIV")]
    wc_hosts = {g["home"] for g in post if g["game_type"] == "WC"}
    wc_teams = wc_hosts | {g["away"] for g in post if g["game_type"] == "WC"}
    div_hosts = {g["home"] for g in post if g["game_type"] == "DIV"} - wc_teams
    winners = {}
    for t in wc_hosts | div_hosts:
        d = groupings[t][1]
        if d in winners:
            raise RuntimeError("%d: two winners in %s (%s, %s)" % (year, d, winners[d], t))
        winners[d] = t
    if len(winners) != 8:
        raise RuntimeError("%d: %d division winners found" % (year, len(winners)))
    return winners


def last_reg_week(games, year):
    return max(g["week"] for g in games if g["season"] == year and g["game_type"] == "REG")


# ------------------------------------------------------------------ fitting

def grid():
    return [M.EloParams(k, h, r) for k, h, r in
            itertools.product(GRID["k"], GRID["hfa"], GRID["regress"])]


def game_losses(games, params_list):
    """{params: (seasons array, log-loss array)} over every scored game."""
    out = {}
    for p in params_list:
        pre, _ = M.run_elo(games, p)
        seasons = np.array([games[i]["season"] for i, _ in pre])
        y = [games[i] for i, _ in pre]
        ll = np.array([M.log_loss(q, 1.0 if g["home_score"] > g["away_score"]
                                  else (0.0 if g["home_score"] < g["away_score"] else 0.5))
                       for (_, q), g in zip(pre, y)])
        out[p] = (seasons, ll)
    return out


def best_params(losses, year):
    """The grid point with the lowest mean log loss on seasons FIT_FROM..year-1."""
    best, best_ll, n = None, math.inf, 0
    for p, (s, ll) in losses.items():
        m = (s >= FIT_FROM) & (s < year)
        v = float(ll[m].mean())
        if v < best_ll - 1e-12:
            best, best_ll, n = p, v, int(m.sum())
    return best, best_ll, n


def p_home_for(season: M.Season, ratings, params):
    d = np.array([ratings.get(M.franchise(h), M.MEAN) - ratings.get(M.franchise(a), M.MEAN)
                  for h, a, _w, _r in season.games])
    return M.win_prob(d + params.hfa)


def rng_for(*parts):
    return np.random.default_rng(zlib.crc32(repr(parts).encode()))


# ------------------------------------------------------------------ walk-forward

def walk_forward(games, groupings, losses, n=N_WALK, seasons=None, log=print):
    rows = []                   # one per (season, division, state)
    fits = {}
    seasons = seasons or range(SCORE_FROM, SCORE_TO + 1)
    for year in seasons:
        params, ll, n_games = best_params(losses, year)
        fits[year] = {"params": params.as_dict(), "fit_log_loss": ll,
                      "fit_games": n_games, "fit_seasons": [FIT_FROM, year - 1]}
        last = last_reg_week(games, year)
        states = list(range(0, last))
        _, snaps = M.run_elo(games, params, snapshot_at=[(year, k) for k in states])
        truth = realised_winners(games, groupings, year)
        t0 = time.time()
        for k in states:
            s = season_at(games, groupings, year, k)
            pm = M.division_probs(s, p_home_for(s, snaps[(year, k)], params), n,
                                  rng_for("model", year, k))
            pc = M.division_probs(s, np.full(s.G, 0.5), n, rng_for("coin", year, k))
            pl = M.leader_probs(s)
            for d in s.divisions:
                w = truth[d]
                rows.append({"season": year, "division": d, "state": k,
                             "winner": w, "model": pm[d], "coinflip": pc[d],
                             "leader": pl[d],
                             "b_model": M.brier(pm[d], w),
                             "b_coinflip": M.brier(pc[d], w),
                             "b_leader": M.brier(pl[d], w)})
        log("  %d  k=%.0f hfa=%.0f regress=%.2f  fit ll %.4f on %d games  %d states %.1fs"
            % (year, params.k, params.hfa, params.regress, ll, n_games, len(states),
               time.time() - t0))
    return rows, fits


def block_bootstrap(rows, a, b, block, n_boot=N_BOOT, seed=41):
    """Mean of (a - b) over rows, with a percentile interval over blocks."""
    keys = sorted({block(r) for r in rows})
    ix = {k: i for i, k in enumerate(keys)}
    s = np.zeros(len(keys))
    c = np.zeros(len(keys))
    for r in rows:
        j = ix[block(r)]
        s[j] += r[a] - r[b]
        c[j] += 1
    est = s.sum() / c.sum()
    rng = np.random.default_rng(seed)
    draws = rng.integers(0, len(keys), size=(n_boot, len(keys)))
    boots = s[draws].sum(axis=1) / c[draws].sum(axis=1)
    lo, hi = np.quantile(boots, [ALPHA / 2, 1 - ALPHA / 2])
    return {"estimate": float(est), "interval": [float(lo), float(hi)],
            "n_blocks": len(keys), "n_states": int(c.sum()),
            "se": float(boots.std(ddof=1))}


def verdict(diff):
    if diff["n_blocks"] < MIN_READABLE_BLOCKS:
        return "not_readable"
    lo, hi = diff["interval"]
    if hi < 0:
        return "better_than"
    if lo > 0:
        return "worse_than"
    return "no_better_than"


def calibration(rows, key="model", edges=np.linspace(0, 1, 11), n_boot=N_BOOT, seed=7):
    """Team-level reliability bins, clustered over division-seasons."""
    obs = []
    for r in rows:
        for t, p in r[key].items():
            obs.append((p, 1.0 if t == r["winner"] else 0.0, (r["season"], r["division"])))
    p = np.array([o[0] for o in obs])
    y = np.array([o[1] for o in obs])
    blocks = [o[2] for o in obs]
    keys = sorted(set(blocks))
    bix = np.array([keys.index(b) for b in blocks]) if len(keys) < 400 else None
    rng = np.random.default_rng(seed)
    out = []
    for lo, hi in zip(edges[:-1], edges[1:]):
        m = (p >= lo) & ((p < hi) if hi < 1 else (p <= hi))
        if not m.any():
            out.append({"bin": [float(lo), float(hi)], "n": 0, "n_blocks": 0,
                        "forecast": None, "realised": None, "interval": None})
            continue
        bs = bix[m]
        ub = np.unique(bs)
        # per-block sums inside this bin
        ps = np.bincount(bs, weights=p[m], minlength=len(keys))[ub]
        ys = np.bincount(bs, weights=y[m], minlength=len(keys))[ub]
        cs = np.bincount(bs, minlength=len(keys))[ub].astype(float)
        draws = rng.integers(0, len(ub), size=(n_boot, len(ub)))
        rb = ys[draws].sum(1) / cs[draws].sum(1)
        out.append({"bin": [float(lo), float(hi)], "n": int(m.sum()), "n_blocks": int(len(ub)),
                    "forecast": float(p[m].mean()), "realised": float(y[m].mean()),
                    "interval": [float(np.quantile(rb, ALPHA / 2)),
                                 float(np.quantile(rb, 1 - ALPHA / 2))]})
    return out


def tiebreak_check(games, groupings):
    """Our division tiebreakers on FINAL results against the realised winners."""
    per = []
    for year in range(SCORE_FROM, SCORE_TO + 1):
        s = season_at(games, groupings, year, 99)
        truth = realised_winners(games, groupings, year)
        R = M.simulate(s, np.full(s.G, 0.5), 1, rng_for("tb", year))
        tab = M.Tables(s, R)
        for d, members in s.divisions.items():
            ids = [s.idx[t] for t in members]
            pct = tab.pct[0, ids]
            top = [members[j] for j in range(len(members)) if abs(pct[j] - pct.max()) < 1e-9]
            st = {}
            w = M.resolve(tab, sorted(top), np.array([0]), rng_for("tb", year, d), st)[0]
            per.append({"season": year, "division": d, "tied": len(top),
                        "ours": w, "actual": truth[d], "decided_by": sorted(st) or None})
    tied = [p for p in per if p["tied"] > 1]
    return {
        "division_seasons": len(per),
        "agree": sum(p["ours"] == p["actual"] for p in per),
        "tied_at_top": len(tied),
        "tied_agree": sum(p["ours"] == p["actual"] for p in tied),
        "residual_draws": sum(1 for p in tied if p["decided_by"] and "residual_draw" in p["decided_by"]),
        "disagreements": [p for p in per if p["ours"] != p["actual"]],
        "tied_cases": tied,
    }


def race_counts(games, groupings, rows):
    """How many division races were genuinely uncertain, three ways."""
    finals = {}
    for year in range(SCORE_FROM, SCORE_TO + 1):
        s = season_at(games, groupings, year, 99)
        rec = M.standings(s)
        for d, members in s.divisions.items():
            w = sorted((rec[t][0] + 0.5 * rec[t][2] for t in members), reverse=True)
            finals[(year, d)] = w[0] - w[1]
    by = {}
    for r in rows:
        by.setdefault((r["season"], r["division"]), {})[r["state"]] = max(r["coinflip"].values())
    open_at = {}
    for k in (4, 8, 12, 15):
        open_at[str(k)] = sum(1 for v in by.values() if k in v and v[k] < OPEN_MAX)
    return {"division_seasons": len(finals),
            "tied_at_top_final": sum(1 for v in finals.values() if v == 0),
            "margin_le_1_game": sum(1 for v in finals.values() if v <= 1),
            "open_after_week": open_at,
            "open_definition": ("no club's probability under the coin-flip "
                                "standings baseline reaches %.2f" % OPEN_MAX)}


def summarise(rows, fits, games, groupings):
    div_block = lambda r: (r["season"], r["division"])
    season_block = lambda r: r["season"]
    mean = lambda k, rs: float(np.mean([r[k] for r in rs]))
    out = {"brier": {k: mean("b_" + k, rows) for k in ("model", "coinflip", "leader")}}
    out["vs"] = {}
    for base in ("leader", "coinflip"):
        d = block_bootstrap(rows, "b_model", "b_" + base, div_block)
        d["verdict"] = verdict(d)
        d["by_season_blocks"] = block_bootstrap(rows, "b_model", "b_" + base, season_block)
        d["by_season_blocks"]["verdict"] = verdict(d["by_season_blocks"])
        out["vs"][base] = d
    out["beats_standings"] = all(out["vs"][b]["verdict"] == "better_than" for b in out["vs"])
    # open races only - descriptive, not the pre-registered verdict
    opn = [r for r in rows if max(r["coinflip"].values()) < OPEN_MAX]
    out["open_states"] = {"n_states": len(opn),
                          "n_blocks": len({div_block(r) for r in opn}),
                          "brier": {k: mean("b_" + k, opn) for k in ("model", "coinflip", "leader")},
                          "vs": {b: block_bootstrap(opn, "b_model", "b_" + b, div_block)
                                 for b in ("leader", "coinflip")}}
    for b in out["open_states"]["vs"]:
        out["open_states"]["vs"][b]["verdict"] = verdict(out["open_states"]["vs"][b])
    by_week = []
    for k in sorted({r["state"] for r in rows}):
        rs = [r for r in rows if r["state"] == k]
        by_week.append({"after_week": k, "n_division_seasons": len(rs),
                        **{k2: mean("b_" + k2, rs) for k2 in ("model", "coinflip", "leader")}})
    out["by_week"] = by_week
    out["calibration"] = {"model": calibration(rows, "model"),
                          "coinflip": calibration(rows, "coinflip")}
    out["races"] = race_counts(games, groupings, rows)
    return out


# ------------------------------------------------------------------ current

def current(games, groupings, losses, n=N_CURRENT):
    year = max(g["season"] for g in games if g["game_type"] == "REG")
    reg = [g for g in games if g["season"] == year and g["game_type"] == "REG"]
    played = [g for g in reg if g["home_score"] is not None]
    # the state is every scored game, which may be part-way through a week
    s = season_at(games, groupings, year, 99)
    params, ll, n_games = best_params(losses, year)
    _, snaps = M.run_elo(games, params, snapshot_at=[(year, 99)])
    ratings = snaps[(year, 99)]
    pm = M.division_probs(s, p_home_for(s, ratings, params), n, rng_for("cur-model", year))
    pc = M.division_probs(s, np.full(s.G, 0.5), n, rng_for("cur-coin", year))
    pl = M.leader_probs(s)
    rec = M.standings(s)
    weeks_done = sorted({g["week"] for g in played})
    complete = [w for w in weeks_done
                if all(g["home_score"] is not None for g in reg if g["week"] == w)]
    return {"season": year, "params": params, "fit_log_loss": ll, "fit_games": n_games,
            "games_played": len(played), "games_total": len(reg),
            "last_complete_week": max(complete) if complete else 0,
            "last_game_ts": max(g["kickoff_ts"] for g in played) if played else None,
            "ratings": {t: ratings.get(M.franchise(t), M.MEAN) for t in s.teams},
            "season_obj": s, "model": pm, "coinflip": pc, "leader": pl, "record": rec, "n": n}


# ------------------------------------------------------------------ main

def compute(log=print, n_walk=N_WALK, n_current=N_CURRENT, seasons=None):
    con = market_log_ro()
    try:
        games, groupings, versions = load(con)
    finally:
        con.close()
    t0 = time.time()
    losses = game_losses(games, grid())
    log("grid: %d parameter sets over %d scored games, %.1fs"
        % (len(losses), sum(g["home_score"] is not None for g in games), time.time() - t0))
    rows, fits = walk_forward(games, groupings, losses, n=n_walk, seasons=seasons, log=log)
    summ = summarise(rows, fits, games, groupings)
    tb = tiebreak_check(games, groupings)
    cur = current(games, groupings, losses, n=n_current)
    return {"games": games, "groupings": groupings, "versions": versions,
            "rows": rows, "fits": fits, "summary": summ, "tiebreak": tb, "current": cur,
            "n_walk": n_walk}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--record-json")
    ap.add_argument("--seasons", help="e.g. 2002-2004, for a quick run")
    ap.add_argument("--n-walk", type=int, default=N_WALK)
    a = ap.parse_args(argv)
    seasons = None
    if a.seasons:
        lo, hi = (int(x) for x in a.seasons.split("-"))
        seasons = range(lo, hi + 1)
    res = compute(n_walk=a.n_walk, seasons=seasons)
    summ = res["summary"]
    print(json.dumps({k: summ[k] for k in ("brier", "beats_standings", "races")}, indent=1))
    for b, d in summ["vs"].items():
        print("model - %s: %+.4f [%+.4f, %+.4f] blocks %d states %d -> %s | season blocks [%+.4f, %+.4f] %s"
              % (b, d["estimate"], *d["interval"], d["n_blocks"], d["n_states"], d["verdict"],
                 *d["by_season_blocks"]["interval"], d["by_season_blocks"]["verdict"]))
    tb = res["tiebreak"]
    print("tiebreak: agree %d/%d, tied at top %d (agree %d), residual draws %d"
          % (tb["agree"], tb["division_seasons"], tb["tied_at_top"], tb["tied_agree"],
             tb["residual_draws"]))
    for p in tb["disagreements"]:
        print("  disagree", p)
    if a.record_json:
        slim = {k: res[k] for k in ("fits", "summary", "tiebreak", "versions", "n_walk")}
        slim["rows"] = res["rows"]
        with open(a.record_json, "w") as f:
            json.dump(slim, f, indent=1, default=str)
    if a.check or a.write:
        from jobs import season_export
        payload = season_export.validated(season_export.build(res))
        print(season_export.summary(payload))
        if a.write:
            print("wrote", season_export.write(payload))
    return 0


if __name__ == "__main__":
    sys.exit(main())
