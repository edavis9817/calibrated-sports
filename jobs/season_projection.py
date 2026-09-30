"""The projected final record, with its walk-forward record (a-55).

Called by `jobs.season_model`, which owns `season/` and publishes this beside
the division file; this module measures and computes, `jobs.season_export`
shapes and validates.

WHAT IT REPLACES. The team page's forward line was the season's win rate so far
times the games on the schedule: a 3-0 team's rate is 1.000, so its "pace" was
17-0. That is a multiplication, not a projection. This is the season model's
simulation of the REAL remaining schedule (a-41's margin-of-victory Elo, its
game distribution, its schedule), summarised per team as a distribution over
final wins, the cumulative path to it, and what the remaining schedule does to
it - every figure read off the same simulations.

ONE DIFFERENCE FROM THE DIVISION MODEL, MEASURED. a-41 holds ratings fixed
inside a simulation and says so. On final wins that is badly overconfident:
walk-forward 2002-2025 with fixed ratings, the central 80% interval after week
3 held the actual final record 73.4% of the time while carrying 88.0% of the
simulated mass, and 81.5% against 91.0% over every state; drawn, 87.0% against
86.4% after week 3 and 89.5% against 90.1% overall. The mean barely moves (RMS
error 1.75 fixed, 1.76 drawn): drawing ratings buys honest intervals, not a
better centre (research/a55_projection_uncertainty.py). So each simulation draws
every team's rating once, N(rating, sigma^2). `sigma` is one constant per
target season, chosen from SIGMA_GRID by mean CRPS of final wins over every
state of seasons SIGMA_FIT_FROM..T-1 - never the season it forecasts. The
division file is NOT changed by this unit: its record was scored under fixed
ratings and re-scoring it is a separate decision.

THE VERDICT RULE. Written after an exploratory run of the fixed-rating version
(disclosed - the rule is not pre-registered in the strict sense), and fixed
before the rating-uncertainty version was scored:
  Score = squared error of the projected MEAN final wins against the actual
  final wins (ties half), per team per state, states "after week k" for
  k = 1 .. the week before the last, for every team that has played. A team
  with no game yet (k = 0, or a week-1 bye) is excluded from the comparison
  because the pace baseline is undefined with no games played.
  Baselines:
    pace                 - wins so far / games so far x games on the schedule.
                           What the team page drew.
    standings_coin_flip  - wins so far + half the games remaining. The
                           standings carried forward with no information.
  model - baseline is bootstrapped over SEASONS (32 teams' final records in
  one season are zero-sum, so a season is the block). The model BEATS the
  baselines only if the 95% interval lies below zero against BOTH.
  Interval honesty is scored separately and descriptively: the share of actual
  final records inside the central 80% and 95% intervals, against the
  simulated mass inside the same (discrete) interval.

READS `market_log.db` read-only, through `jobs.season_model.market_log_ro`.
Writes nothing.
"""
from __future__ import annotations

import time

import numpy as np

from models import season as M

SIGMA_GRID = [0.0, 40.0, 60.0, 80.0, 100.0, 120.0, 150.0]
SIGMA_FIT_FROM = 2001           # 2000's Elo constants would be fitted on nothing
N_PROJ_WALK = 1000              # sims per (state, sigma) in the walk-forward
N_PROJ_CURRENT = 20000          # sims for the published projection
N_BOOT = 2000
ALPHA = 0.05
MIN_READABLE_BLOCKS = 5
QUANTILES = {"80": (0.10, 0.90), "95": (0.025, 0.975)}


def _state_sims(J, games, groupings, year, k, params, ratings, sigma, n, tag):
    s = J.season_at(games, groupings, year, k)
    rng = J.rng_for(tag, year, k, sigma)
    draws = M.rating_draws(s, ratings, sigma, n, rng)
    R = M.simulate(s, M.p_home_draws(s, draws, params.hfa), n, rng)
    return s, R


def walk_forward(J, games, groupings, losses, n=N_PROJ_WALK, seasons=None, log=print,
                 fit_from=None):
    """Every (season, state, team) forecast at every sigma in the grid, then the
    sigma each scored season would have chosen from earlier seasons alone.

    -> (rows, sigma_by_season). `rows` hold only the chosen sigma's forecast."""
    scored = list(seasons or range(J.SCORE_FROM, J.SCORE_TO + 1))
    fit_from = SIGMA_FIT_FROM if fit_from is None else fit_from
    years = list(range(fit_from, max(scored) + 1))
    crps_by = {}                    # (year, sigma) -> [mean crps per state]
    fc = {}                         # (year, sigma) -> rows
    t0 = time.time()
    for year in years:
        params, _, _ = J.best_params(losses, year)
        last = J.last_reg_week(games, year)
        states = list(range(0, last))
        _, snaps = M.run_elo(games, params, snapshot_at=[(year, k) for k in states])
        final = J.season_at(games, groupings, year, 99)
        rec = M.standings(final)
        for sigma in SIGMA_GRID:
            out = []
            for k in states:
                s, R = _state_sims(J, games, groupings, year, k, params, snaps[(year, k)],
                                   sigma, n, "proj-walk")
                W = M.final_wins(s, R)
                actual = np.array([rec[t][0] + 0.5 * rec[t][2] for t in s.teams])
                c = M.crps(W, actual)
                st = M.standings(s)
                for t in s.teams:
                    j = s.idx[t]
                    w = W[:, j]
                    w0, l0, t0_ = st[t]
                    g0 = w0 + l0 + t0_
                    G = int(s.games_played_by[j])
                    cur = w0 + 0.5 * t0_
                    row = {"season": year, "state": k, "team": t, "actual": float(actual[j]),
                           "model": float(w.mean()), "crps": float(c[j]),
                           "pace": (cur / g0 * G) if g0 else None,
                           "coinflip": cur + 0.5 * (G - g0)}
                    for lvl, (qa, qb) in QUANTILES.items():
                        lo, hi = np.quantile(w, [qa, qb])
                        row["in" + lvl] = bool(lo <= actual[j] <= hi)
                        row["mass" + lvl] = float(np.mean((w >= lo) & (w <= hi)))
                    out.append(row)
            fc[(year, sigma)] = out
            crps_by[(year, sigma)] = float(np.mean([r["crps"] for r in out]))
        log("  projection %d: crps by sigma %s  %.0fs"
            % (year, " ".join("%g:%.3f" % (sg, crps_by[(year, sg)]) for sg in SIGMA_GRID),
               time.time() - t0))
    sigma_by = {}
    rows = []
    for year in scored:
        fit = [y for y in years if y < year]
        if not fit:
            raise RuntimeError("no earlier season to fit sigma for %d" % year)
        best = min(SIGMA_GRID, key=lambda sg: (np.mean([crps_by[(y, sg)] for y in fit]), sg))
        sigma_by[year] = {"sigma": best, "fit_seasons": [fit[0], fit[-1]],
                          "fit_crps": float(np.mean([crps_by[(y, best)] for y in fit]))}
        rows.extend(fc[(year, best)])
    return rows, sigma_by, crps_by


def fit_sigma(crps_by, year):
    """The sigma a forecast for `year` uses: lowest mean CRPS on earlier seasons."""
    fit = sorted({y for y, _ in crps_by if y < year})
    best = min(SIGMA_GRID, key=lambda sg: (np.mean([crps_by[(y, sg)] for y in fit]), sg))
    return best, [fit[0], fit[-1]], float(np.mean([crps_by[(y, best)] for y in fit]))


def block_diff(rows, a, b, n_boot=N_BOOT, seed=55):
    """Mean of (a-err^2 - b-err^2) with a percentile interval over season blocks."""
    keys = sorted({r["season"] for r in rows})
    ix = {k: i for i, k in enumerate(keys)}
    s = np.zeros(len(keys))
    c = np.zeros(len(keys))
    for r in rows:
        j = ix[r["season"]]
        s[j] += (r[a] - r["actual"]) ** 2 - (r[b] - r["actual"]) ** 2
        c[j] += 1
    rng = np.random.default_rng(seed)
    draws = rng.integers(0, len(keys), size=(n_boot, len(keys)))
    boots = s[draws].sum(axis=1) / c[draws].sum(axis=1)
    lo, hi = np.quantile(boots, [ALPHA / 2, 1 - ALPHA / 2])
    return {"estimate": float(s.sum() / c.sum()), "interval": [float(lo), float(hi)],
            "n_blocks": len(keys), "n_forecasts": int(c.sum())}


def verdict(d):
    if d["n_blocks"] < MIN_READABLE_BLOCKS:
        return "not_readable"
    lo, hi = d["interval"]
    return "better_than" if hi < 0 else ("worse_than" if lo > 0 else "no_better_than")


def coverage(rows, lvl, n_boot=N_BOOT, seed=56):
    """Share of actual final records inside the central interval, against the
    simulated mass inside it, with a season-block interval on the difference."""
    keys = sorted({r["season"] for r in rows})
    ix = {k: i for i, k in enumerate(keys)}
    hit = np.zeros(len(keys))
    mass = np.zeros(len(keys))
    c = np.zeros(len(keys))
    for r in rows:
        j = ix[r["season"]]
        hit[j] += r["in" + lvl]
        mass[j] += r["mass" + lvl]
        c[j] += 1
    rng = np.random.default_rng(seed)
    draws = rng.integers(0, len(keys), size=(n_boot, len(keys)))
    diff = (hit[draws].sum(1) - mass[draws].sum(1)) / c[draws].sum(1)
    lo, hi = np.quantile(diff, [ALPHA / 2, 1 - ALPHA / 2])
    v = "within" if lo <= 0 <= hi else ("too_narrow" if hi < 0 else "too_wide")
    if len(keys) < MIN_READABLE_BLOCKS:
        v = "not_readable"
    return {"nominal": float(QUANTILES[lvl][1] - QUANTILES[lvl][0]),
            "realised": float(hit.sum() / c.sum()), "mass": float(mass.sum() / c.sum()),
            "difference_interval": [float(lo), float(hi)], "n_blocks": len(keys),
            "n_forecasts": int(c.sum()), "verdict": v}


def rmse(rows, key):
    return float(np.sqrt(np.mean([(r[key] - r["actual"]) ** 2 for r in rows])))


def summarise(rows):
    comp = [r for r in rows if r["pace"] is not None]
    out = {"n_forecasts": len(comp),
           "states": "after week k, k = 1 .. the week before the last, for every team "
                     "that has played (pace is undefined before a team's first game)",
           "team_seasons": len({(r["season"], r["team"]) for r in comp}),
           "rmse": {k: rmse(comp, k) for k in ("model", "pace", "coinflip")},
           "crps": float(np.mean([r["crps"] for r in comp]))}
    out["vs"] = {}
    for base in ("pace", "coinflip"):
        d = block_diff(comp, "model", base)
        d["verdict"] = verdict(d)
        out["vs"][base] = d
    out["coverage"] = {lvl: coverage(rows, lvl) for lvl in QUANTILES}
    by_week = []
    for k in sorted({r["state"] for r in rows}):
        rs = [r for r in rows if r["state"] == k]
        by_week.append({"after_week": k, "n": len(rs),
                        "model": rmse(rs, "model"),
                        "pace": (rmse([r for r in rs if r["pace"] is not None], "pace")
                                 if any(r["pace"] is not None for r in rs) else None),
                        "coinflip": rmse(rs, "coinflip"),
                        "coverage80": float(np.mean([r["in80"] for r in rs])),
                        "mass80": float(np.mean([r["mass80"] for r in rs]))})
    out["by_week"] = by_week
    return out


# ------------------------------------------------------------------ current

def _q(x, a):
    return float(np.quantile(x, a))


def current(J, games, groupings, losses, crps_by, n=N_PROJ_CURRENT):
    """The published projection for the latest season, every team."""
    year = max(g["season"] for g in games if g["game_type"] == "REG")
    s = J.season_at(games, groupings, year, 99)
    params, _, _ = J.best_params(losses, year)
    _, snaps = M.run_elo(games, params, snapshot_at=[(year, 99)])
    ratings = snaps[(year, 99)]
    sigma, fit_seasons, fit_crps = fit_sigma(crps_by, year)
    rng = J.rng_for("proj-current", year)
    draws = M.rating_draws(s, ratings, sigma, n, rng)
    R = M.simulate(s, M.p_home_draws(s, draws, params.hfa), n, rng)
    W = M.final_wins(s, R)
    rec = M.standings(s)
    rv = M.rating_vector(s, ratings)
    # the counterfactuals behind "what the remaining schedule does": a
    # league-average team (rating MEAN) drawn with the SAME uncertainty, playing
    # the same games in the same venues. Its own column of draws, same rng.
    avg = (M.MEAN + sigma * rng.standard_normal(n)) if sigma > 0 else np.full(n, M.MEAN)
    teams = []
    for t in s.teams:
        j = s.idx[t]
        w0, l0, t0 = rec[t]
        gi, res = M.team_results(s, R, t)
        open_ = np.array([s.games[i][3] is None for i in gi], dtype=bool)
        cur = w0 + 0.5 * t0
        wt = W[:, j]
        sched = []
        opp_r, e_avg_team, e_vs_avg = [], 0.0, 0.0
        for col, i in enumerate(gi):
            if not open_[col]:
                continue
            h, a, week, _ = s.games[i]
            home = h == t
            o = s.idx[a if home else h]
            # this team, drawn, against a league-average opponent, drawn; and a
            # league-average team, drawn, in this team's place against this opponent
            if home:
                p_self = M.win_prob(draws[:, j] - avg + params.hfa)
                p_avg = M.win_prob(avg - draws[:, o] + params.hfa)
            else:
                p_self = 1.0 - M.win_prob(avg - draws[:, j] + params.hfa)
                p_avg = 1.0 - M.win_prob(draws[:, o] - avg + params.hfa)
            e_vs_avg += float(np.mean(p_self))
            e_avg_team += float(np.mean(p_avg))
            opp_r.append(rv[o])
            sched.append({"week": int(week), "opponent": s.teams[o], "home": bool(home),
                          "opponent_rating": float(rv[o]),
                          "p_win": float(res[:, col].mean())})
        # cumulative path over the remaining games, in week order
        path = []
        cum = np.full(n, cur)
        for col, i in enumerate(gi):
            if not open_[col]:
                continue
            cum = cum + res[:, col]
            path.append({"week": int(s.games[i][2]), "mean": float(cum.mean()),
                         **{"interval" + lvl: [_q(cum, qa), _q(cum, qb)]
                            for lvl, (qa, qb) in QUANTILES.items()}})
        vals, counts = np.unique(wt, return_counts=True)
        e_rem = float(sum(x["p_win"] for x in sched))
        teams.append({
            "team": t, "rating": float(rv[j]), "wins": w0, "losses": l0, "ties": t0,
            "games_played": w0 + l0 + t0, "games_total": int(s.games_played_by[j]),
            "current_wins": cur,
            "mean": float(wt.mean()), "sd": float(wt.std()), "median": _q(wt, 0.5),
            **{"interval" + lvl: [_q(wt, qa), _q(wt, qb)] for lvl, (qa, qb) in QUANTILES.items()},
            **{"interval%s_mass" % lvl: float(np.mean((wt >= _q(wt, qa)) & (wt <= _q(wt, qb))))
               for lvl, (qa, qb) in QUANTILES.items()},
            "distribution": [{"wins": float(v), "p": float(c) / n} for v, c in zip(vals, counts)],
            "path": path, "schedule": sched,
            "remaining": {"games": len(sched),
                          "opponent_rating_mean": float(np.mean(opp_r)) if opp_r else None,
                          "expected_wins": e_rem,
                          "expected_wins_average_opponents": e_vs_avg,
                          "average_team_expected_wins": e_avg_team},
        })
    # difficulty rank: 1 = the schedule a league-average team would do worst on
    share = {x["team"]: (x["remaining"]["average_team_expected_wins"] / x["remaining"]["games"]
                         if x["remaining"]["games"] else None) for x in teams}
    ranked = sorted((v, k) for k, v in share.items() if v is not None)
    rank = {k: i + 1 for i, (v, k) in enumerate(ranked)}
    for x in teams:
        x["remaining"]["average_team_win_share"] = share[x["team"]]
        x["remaining"]["difficulty_rank"] = rank.get(x["team"])
        x["remaining"]["schedule_effect"] = (x["remaining"]["expected_wins"]
                                             - x["remaining"]["expected_wins_average_opponents"])
    return {"season": year, "params": params, "sigma": sigma, "sigma_fit_seasons": fit_seasons,
            "sigma_fit_crps": fit_crps, "teams": teams, "n": n, "season_obj": s,
            "ranked_of": len(ranked)}
