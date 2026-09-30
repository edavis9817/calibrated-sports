"""c-27 - does forecasting the factors separately raise resolution?

    set LOGGER_DB=D:/calibrated-sports/data/market_log.db   (opened mode=ro only)
    python -m research.decomposed_usage --premise                       # Step 0 only
    python -m research.decomposed_usage --build-p2 --cache D:/temp/c19/extract.sqlite \
        --c24-rows D:/temp/c24/rows.json --p2-out D:/temp/c27/p2_rows.json
    python -m research.decomposed_usage --ledger D:/temp/c24/wf_ledger.csv \
        --p2 D:/temp/c27/p2_rows.json --out-dir D:/temp/c27

PRE-REGISTRATION: docs/C27-decomposed-usage-preregistration.md, committed at
fc25f38 BEFORE this script existed. This file implements it; it does not extend
it. The scoring is research.ranking_calibration, IMPORTED - never copied - so the
comparison with c-24 cannot drift.

    receptions    = team targets x target share x catch rate
    rush attempts = team carries x carry share

Everything PRINTED is an aggregate or an interval (BET_LIST_RESTRICTION). The
per-outcome predictions go to --out-dir, which is scratch and never committed.
"""
import argparse
import csv
import json
import os
import sqlite3
import sys
import time
import zlib
from collections import Counter, defaultdict

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
from research import ranking_calibration as rc  # noqa: E402  (the c-24 scoring code)

TRAIN = tuple(range(2013, 2023))      # snap counts start 2013; first test season 2023
PREV_W = 0.5                          # weight on season T-1 games (declared, not tuned)
MIN_GAMES = 4
CATCH_MIN_TGT = 20
DRAWS = 20_000
BOOT_SEED = 27
S_LO, S_HI = 0.002, 0.9
VOL = {"receptions": "tgt", "rush_attempts": "car"}
REC_POS = ("WR", "TE", "RB")
RUSH_POS = ("RB",)
# current code -> the code nfl_games uses in the seasons before the move
RELOCATED = {"LV": "OAK", "LAC": "SD", "LA": "STL"}


# =============================================================================
# the store, mode=ro
# =============================================================================

def ro():
    con = sqlite3.connect(f"file:{config.DB_PATH}?mode=ro", uri=True)
    if not con.execute("SELECT name FROM sqlite_master WHERE name='nfl_player_week'").fetchone():
        raise SystemExit(f"{config.DB_PATH} has no nfl_player_week - set LOGGER_DB to the store")
    return con


GAMES_SQL = """SELECT g.game_id, g.season, g.week, g.kickoff_ts, g.home_team, g.away_team
  FROM nfl_games g JOIN (SELECT game_id, MAX(data_version) dv FROM nfl_games GROUP BY game_id) v
    ON v.game_id = g.game_id AND v.dv = g.data_version WHERE g.game_type = 'REG'"""
PW_SQL = """SELECT w.gsis_id, w.season, w.week, w.position, w.team, w.targets, w.receptions,
       w.carries, w.receiving_yards, w.rushing_yards, w.attempts
  FROM nfl_player_week w JOIN (SELECT gsis_id, season, week, season_type, MAX(data_version) dv
         FROM nfl_player_week GROUP BY gsis_id, season, week, season_type) v
    ON v.gsis_id = w.gsis_id AND v.season = w.season AND v.week = w.week
   AND v.season_type = w.season_type AND v.dv = w.data_version
 WHERE w.season_type = 'REG'"""
SNAP_SQL = """SELECT x.gsis_id, s.game_id, s.team, s.position, s.offense_snaps
  FROM nfl_snap_counts s JOIN (SELECT game_id, pfr_player_id, MAX(data_version) dv
         FROM nfl_snap_counts GROUP BY game_id, pfr_player_id) v
    ON v.game_id = s.game_id AND v.pfr_player_id = s.pfr_player_id AND v.dv = s.data_version
  JOIN player_xwalk x ON x.pfr_id = s.pfr_player_id"""


class Panel:
    """Player-games and team-games, REG, newest data_version.

    A PLAYED game is a player-week row OR a snap row with offense_snaps > 0; a
    played game with no stat row is a zero (nflverse writes no row for a player
    who played and recorded nothing - CLAUDE.md). Team totals are sums of the
    team's player-week rows, which the missing zero rows cannot change."""

    def __init__(self, con):
        self.games = {}
        gid = {}
        # every game type, for the PREDICTED game's week, kickoff and opponent;
        # the history below is REG only, as in the baseline's as-of join
        self.meta = {g: (s, w, k, h, a) for g, s, w, k, h, a in
                     con.execute(GAMES_SQL.replace("WHERE g.game_type = 'REG'", ""))}
        for g, s, w, k, h, a in con.execute(GAMES_SQL):
            self.games[g] = (s, w, k, h, a)
            gid[(s, w, h)] = g
            gid[(s, w, a)] = g
        self.census = Counter()
        pg = {}
        for gs, s, w, pos, team, tgt, rec, car, ryd, rsh, att in con.execute(PW_SQL):
            g = gid.get((s, w, team))
            if g is None and team in RELOCATED:
                # player-week writes today's code for a relocated franchise; the
                # schedule writes the code of the season. Fold to the schedule's.
                team = RELOCATED[team]
                g = gid.get((s, w, team))
            if g is None:
                self.census["player-week row with no REG game"] += 1
                continue
            pg[(gs, g)] = {"team": team, "pos": pos, "tgt": tgt or 0, "rec": rec or 0,
                           "car": car or 0, "ryd": ryd or 0, "rsh": rsh or 0, "att": att or 0,
                           "snaps": None}
        for gs, g, team, pos, off in con.execute(SNAP_SQL):
            if g not in self.games:
                continue
            if team not in self.games[g][3:] and RELOCATED.get(team) in self.games[g][3:]:
                team = RELOCATED[team]
            r = pg.get((gs, g))
            if r is None:
                if not off:
                    continue
                self.census["played, no stat row (zero added)"] += 1
                pg[(gs, g)] = r = {"team": team, "pos": pos, "tgt": 0, "rec": 0, "car": 0,
                                   "ryd": 0, "rsh": 0, "att": 0, "snaps": None}
            r["snaps"] = max(r["snaps"] or 0, off or 0)
        self.pg = pg
        # team-game totals, and the team's offensive snaps (its max player's)
        tg = defaultdict(lambda: {"tgt": 0, "car": 0, "att": 0, "rec": 0, "snaps": 0})
        for (gs, g), r in pg.items():
            t = tg[(g, r["team"])]
            for c in ("tgt", "car", "att", "rec"):
                t[c] += r[c]
            t["snaps"] = max(t["snaps"], r["snaps"] or 0)
        self.tg = dict(tg)
        # indices
        self.player = defaultdict(list)
        for (gs, g), r in pg.items():
            s, w, k, _h, _a = self.games[g]
            self.player[gs].append((k, s, g, r))
        for v in self.player.values():
            v.sort(key=lambda x: x[0])
        self.team = defaultdict(list)       # team -> [(kick, season, game, own, allowed)]
        for (g, team), t in self.tg.items():
            s, w, k, h, a = self.games[g]
            opp = a if team == h else h
            allowed = self.tg.get((g, opp))
            if allowed is None:
                continue
            self.team[team].append((k, s, g, t, allowed))
        for v in self.team.values():
            v.sort(key=lambda x: x[0])
        self.all_tg = sorted(((self.games[g][2], self.games[g][0], t) for (g, _tm), t in self.tg.items()),
                             key=lambda x: x[0])


# =============================================================================
# method of moments - pure
# =============================================================================

def mom(groups):
    """groups: per entity-season sequences in time order. -> r1, within variance,
    tau^2, k = within/tau^2, w8 = 8/(8+k)."""
    groups = [np.asarray(g, float) for g in groups if len(g) >= 2]
    if not groups:
        return None
    ss = sum(((g - g.mean()) ** 2).sum() for g in groups)
    dof = sum(len(g) - 1 for g in groups)
    within = ss / dof
    means = np.array([g.mean() for g in groups])
    tau2 = float(means.var(ddof=1) - np.mean([within / len(g) for g in groups]))
    k = within / tau2 if tau2 > 0 else float("inf")
    w8 = 8 / (8 + k) if tau2 > 0 else 0.0
    a = np.concatenate([g[:-1] for g in groups])
    b = np.concatenate([g[1:] for g in groups])
    r1 = float(np.corrcoef(a, b)[0, 1]) if a.std() > 0 and b.std() > 0 else None
    return {"r1": r1, "within": float(within), "tau2": tau2, "k": float(k), "w8": float(w8),
            "entities": len(groups), "obs": int(sum(len(g) for g in groups)),
            "mean": float(np.concatenate(groups).mean())}


def betabin_rho(t, V, s):
    """Intra-class correlation of a beta-binomial by moments:
    E[(t - Vs)^2] = V s (1-s) [1 + (V-1) rho]."""
    t, V, s = (np.asarray(x, float) for x in (t, V, s))
    ok = (V >= 2) & (s > 0) & (s < 1)
    t, V, s = t[ok], V[ok], s[ok]
    num = ((t - V * s) ** 2 - V * s * (1 - s)).sum()
    den = (V * s * (1 - s) * (V - 1)).sum()
    return float(min(max(num / den, 1e-4), 0.5))


def shrink(n_eff, xbar, k, prior, within):
    """Normal-normal posterior: mean and variance of the shrunk estimate."""
    if not np.isfinite(k):
        return prior, 0.0
    return (n_eff * xbar + k * prior) / (n_eff + k), within / (n_eff + k)


def prob_over(x, line):
    return float(np.mean(x > line))


# =============================================================================
# Step 0 - the premise check, training seasons only
# =============================================================================

def _player_seasons(panel, seasons, positions):
    """-> {(gsis, season): [(kick, game, r, team_totals)]} in time order."""
    out = defaultdict(list)
    for gs, games in panel.player.items():
        for k, s, g, r in games:
            if s in seasons and r["pos"] in positions:
                out[(gs, s)].append((k, g, r, panel.tg[(g, r["team"])]))
    return {key: v for key, v in out.items() if len(v) >= MIN_GAMES}


def premise(panel, out):
    seasons = set(TRAIN)
    teams = defaultdict(list)
    for team, games in panel.team.items():
        for k, s, g, t, _al in games:
            if s in seasons:
                teams[(team, s)].append(t)
    fac = {}
    fac["team plays"] = [[t["att"] + t["car"] for t in v] for v in teams.values()]
    fac["team pass attempts"] = [[t["att"] for t in v] for v in teams.values()]
    fac["team targets"] = [[t["tgt"] for t in v] for v in teams.values()]
    fac["team carries"] = [[t["car"] for t in v] for v in teams.values()]
    fac["team rush rate"] = [[t["car"] / (t["att"] + t["car"]) for t in v if t["att"] + t["car"] > 0]
                             for v in teams.values()]
    rec = _player_seasons(panel, seasons, REC_POS)
    rush = _player_seasons(panel, seasons, RUSH_POS)
    pos_of = {key: Counter(r["pos"] for _k, _g, r, _t in v).most_common(1)[0][0] for key, v in rec.items()}

    def pl(ps, f, cond=lambda r, t: True):
        return {key: [f(r, t) for _k, _g, r, t in v if cond(r, t)] for key, v in ps.items()}

    pf = {
        "target share": pl(rec, lambda r, t: r["tgt"] / t["tgt"], lambda r, t: t["tgt"] > 0),
        "snap share": pl(rec, lambda r, t: r["snaps"] / t["snaps"],
                         lambda r, t: r["snaps"] is not None and t["snaps"] > 0),
        "catch rate": pl(rec, lambda r, t: r["rec"] / r["tgt"], lambda r, t: r["tgt"] >= 1),
        "yards per target": pl(rec, lambda r, t: r["ryd"] / r["tgt"], lambda r, t: r["tgt"] >= 1),
        "receptions (raw)": pl(rec, lambda r, t: r["rec"]),
        "targets (raw)": pl(rec, lambda r, t: r["tgt"]),
        "carry share": pl(rush, lambda r, t: r["car"] / t["car"], lambda r, t: t["car"] > 0),
        "yards per carry": pl(rush, lambda r, t: r["rsh"] / r["car"], lambda r, t: r["car"] >= 1),
        "carries (raw)": pl(rush, lambda r, t: r["car"]),
    }
    res = {}
    out(f"\n== STEP 0 - premise check, training seasons {TRAIN[0]}-{TRAIN[-1]} (REG) ==")
    out(f"   panel census: {dict(panel.census)}")
    out(f"   {'factor':<20} {'entities':>8} {'obs':>7} {'mean':>8} {'r1':>7} {'k':>8} {'w8':>6}"
        f"   | position-season demeaned: r1, w8")
    for name, groups in fac.items():
        m = mom(groups)
        res[name] = {"pooled": m}
        out(f"   {name:<20} {m['entities']:>8} {m['obs']:>7} {m['mean']:>8.3f} {m['r1']:>7.3f} "
            f"{m['k']:>8.2f} {m['w8']:>6.3f}")
    for name, d in pf.items():
        m = mom(list(d.values()))
        # demeaned by position-season (removes position mix)
        if name in ("carry share", "yards per carry", "carries (raw)"):
            dm = m
        else:
            by = defaultdict(list)
            for key, v in d.items():
                by[(pos_of[key], key[1])] += v
            mu = {pk: np.mean(v) for pk, v in by.items() if v}
            dm = mom([[x - mu[(pos_of[key], key[1])] for x in v] for key, v in d.items()])
            # within-group demeaning leaves r1 of raw series but the between part
            # loses position mix; tau^2 is recomputed on the demeaned means
        res[name] = {"pooled": m, "demeaned": dm}
        out(f"   {name:<20} {m['entities']:>8} {m['obs']:>7} {m['mean']:>8.3f} {m['r1']:>7.3f} "
            f"{m['k']:>8.2f} {m['w8']:>6.3f}   | {dm['r1']:.3f}, {dm['w8']:.3f}")
    # the pre-registered rule
    cr, ts = res["catch rate"]["pooled"], res["target share"]["pooled"]
    q_r, q_w = cr["r1"] / ts["r1"], cr["w8"] / ts["w8"]
    if q_r >= 0.75 or q_w >= 0.75:
        verdict = "REFUTED"
    elif q_r < 0.5 and q_w < 0.5:
        verdict = "HOLDS"
    else:
        verdict = "WEAKENED"
    cr2, ts2 = res["catch rate"]["demeaned"], res["target share"]["demeaned"]
    out(f"\n   RULE (receptions): r1 ratio catch/share {q_r:.3f}, w8 ratio {q_w:.3f} -> PREMISE {verdict}")
    out(f"   secondary, demeaned: r1 ratio {cr2['r1'] / ts2['r1']:.3f}, w8 ratio "
        f"{(cr2['w8'] / ts2['w8']) if ts2['w8'] else float('nan'):.3f}")
    cs, tc = res["carry share"]["pooled"], res["team carries"]["pooled"]
    out(f"   rush (descriptive, not a gate): carry share r1 {cs['r1']:.3f} w8 {cs['w8']:.3f}; "
        f"team carries r1 {tc['r1']:.3f} w8 {tc['w8']:.3f}")
    res["_rule"] = {"r1_ratio": q_r, "w8_ratio": q_w, "verdict": verdict,
                    "demeaned_r1_ratio": cr2["r1"] / ts2["r1"]}
    return res


# =============================================================================
# Step 1 - constants on 2013-2022, then the decomposed forecast
# =============================================================================

def fit_constants(panel):
    seasons = set(TRAIN)
    C = {"fit_seasons": list(TRAIN)}
    # league-season mean team volume
    lg = defaultdict(list)
    for k, s, t in panel.all_tg:
        if s in seasons:
            lg[s].append(t)
    for stat, v in VOL.items():
        teams, allowed = defaultdict(list), defaultdict(list)
        lmean = {s: np.mean([t[v] for t in ts]) for s, ts in lg.items()}
        for team, games in panel.team.items():
            for k, s, g, t, al in games:
                if s in seasons:
                    teams[(team, s)].append(t[v])
                    allowed[(team, s)].append(al[v] / lmean[s])
        mt, ma = mom(list(teams.values())), mom(list(allowed.values()))
        ps = _player_seasons(panel, seasons, REC_POS if stat == "receptions" else RUSH_POS)
        share = [[r[v] / t[v] for _k, _g, r, t in g if t[v] > 0] for g in ps.values()]
        msh = mom(share)
        tt, VV, ss = [], [], []
        for g in ps.values():
            tot = sum(r[v] for _k, _g, r, t in g)
            den = sum(t[v] for _k, _g, r, t in g)
            if den <= 0:
                continue
            for _k, _g, r, t in g:
                tt.append(r[v]); VV.append(t[v]); ss.append(tot / den)  # noqa: E702
        C[stat] = {"k_team": mt["k"], "within_team": mt["within"], "vmr_team": mt["within"] / mt["mean"],
                   "k_opp": ma["k"], "within_opp": ma["within"],
                   "k_share": msh["k"], "within_share": msh["within"],
                   "rho": betabin_rho(tt, VV, ss)}
    # prior share by (position, snap rank within team-position-season), per stat
    seg = defaultdict(lambda: {"tgt": 0, "car": 0, "ttgt": 0, "tcar": 0, "snaps": 0, "n": 0})
    for gs, games in panel.player.items():
        for k, s, g, r in games:
            if s not in seasons:
                continue
            t = panel.tg[(g, r["team"])]
            x = seg[(gs, s, r["team"], r["pos"])]
            x["tgt"] += r["tgt"]; x["car"] += r["car"]; x["ttgt"] += t["tgt"]  # noqa: E702
            x["tcar"] += t["car"]; x["snaps"] += r["snaps"] or 0; x["n"] += 1  # noqa: E702
    grp = defaultdict(list)
    for (gs, s, team, pos), x in seg.items():
        grp[(s, team, pos)].append(x)
    prior = defaultdict(lambda: defaultdict(list))
    for (s, team, pos), xs in grp.items():
        xs.sort(key=lambda x: -x["snaps"])
        for i, x in enumerate(xs):
            if x["n"] < MIN_GAMES:
                continue
            b = min(i + 1, 3)
            for stat, v in VOL.items():
                den = x["t" + v]
                if den > 0:
                    prior[stat][(pos, b)].append(x[v] / den)
                    prior[stat][(pos, 0)].append(x[v] / den)
    C["prior_share"] = {stat: {f"{p}|{b}": float(np.mean(v)) for (p, b), v in d.items()}
                        for stat, d in prior.items()}
    # catch-rate prior: position mean, strength from tau^2 over >= 20-target player-seasons
    tot = defaultdict(lambda: [0, 0])
    ps = defaultdict(lambda: [0, 0, None])
    for gs, games in panel.player.items():
        for k, s, g, r in games:
            if s in seasons and r["tgt"] > 0:
                tot[r["pos"]][0] += r["rec"]; tot[r["pos"]][1] += r["tgt"]  # noqa: E702
                p = ps[(gs, s)]
                p[0] += r["rec"]; p[1] += r["tgt"]; p[2] = r["pos"]  # noqa: E702
    cbar = {pos: a / b for pos, (a, b) in tot.items() if b >= 100}
    dev, noise = [], []
    for (gs, s), (a, b, pos) in ps.items():
        if b >= CATCH_MIN_TGT and pos in cbar:
            dev.append(a / b - cbar[pos])
            noise.append(cbar[pos] * (1 - cbar[pos]) / b)
    tau2 = float(np.var(dev, ddof=1) - np.mean(noise))
    C["catch"] = {"cbar": cbar, "tau2": tau2,
                  "strength": {pos: max(c * (1 - c) / tau2 - 1, 1.0) for pos, c in cbar.items()}}
    C["catch"]["league"] = sum(a for a, b in tot.values()) / sum(b for a, b in tot.values())
    return C


class Forecaster:
    def __init__(self, panel, C):
        self.P, self.C = panel, C
        self._league = {}

    def league(self, T, kick, v):
        key = (T, kick, v)
        if key not in self._league:
            num = den = 0.0
            for k, s, t in self.P.all_tg:
                if k >= kick:
                    break
                if s in (T - 1, T):
                    w = PREV_W if s == T - 1 else 1.0
                    num += w * t[v]; den += w  # noqa: E702
            self._league[key] = num / den if den else None
        return self._league[key]

    def _window(self, rows, T, kick):
        for row in rows:
            if row[0] >= kick:
                break
            if row[1] in (T - 1, T):
                yield (PREV_W if row[1] == T - 1 else 1.0), row

    def components(self, gsis, stat, game, kick, T, team, pos):
        v = VOL[stat]
        c = self.C[stat]
        L = self.league(T, kick, v)
        # team volume
        n = sx = 0.0
        for w, (k, s, g, t, al) in self._window(self.P.team.get(team, []), T, kick):
            n += w; sx += w * t[v]  # noqa: E702
        mu_team, pv_team = shrink(n, sx / n if n else L, c["k_team"], L, c["within_team"])
        # opponent: volume it allowed, relative to the as-of league mean
        s_, _w, _k, h, a = self.P.meta[game]
        opp = a if team == h else h
        n = sx = 0.0
        for w, (k, s, g, t, al) in self._window(self.P.team.get(opp, []), T, kick):
            n += w; sx += w * al[v] / L  # noqa: E702
        opp_f, _pv = shrink(n, sx / n if n else 1.0, c["k_opp"], 1.0, c["within_opp"])
        # share, and the role bucket for its prior
        n = num = den = snaps = rec = tgt = 0.0
        for w, (k, s, g, r) in self._window(self.P.player.get(gsis, []), T, kick):
            tv = self.P.tg[(g, r["team"])][v]
            snaps += w * (r["snaps"] or 0)
            rec += r["rec"]; tgt += r["tgt"]  # noqa: E702
            if tv > 0:
                n += w; num += w * r[v]; den += w * tv  # noqa: E702
        if n == 0:
            bucket = 2
        else:
            mates = [sn for gs2, (p2, sn) in self._team_players(team, T, kick).items()
                     if gs2 != gsis and p2 == pos]
            bucket = min(1 + sum(1 for x in mates if x > snaps), 3)
        ps = self.C["prior_share"][stat]
        s0 = ps.get(f"{pos}|{bucket}", ps.get(f"{pos}|0", 0.05))
        s_hat, pv_s = shrink(n, num / den if den else s0, c["k_share"], s0, c["within_share"])
        out = {"mu_V": mu_team * opp_f, "var_V": mu_team * opp_f * c["vmr_team"] + opp_f ** 2 * pv_team,
               "s": s_hat, "pv_s": pv_s, "phi": 1 / c["rho"] - 1, "bucket": bucket,
               "n_share": n, "opp": opp_f}
        if stat == "receptions":
            cb = self.C["catch"]
            c0 = cb["cbar"].get(pos, cb["league"])
            st = cb["strength"].get(pos, min(cb["strength"].values()))
            out["ca"], out["cb"] = st * c0 + rec, st * (1 - c0) + max(tgt - rec, 0)
        return out

    def _team_players(self, team, T, kick):
        """as-of {gsis: (position, weighted snaps)} of every player with a played
        game FOR `team` in seasons T-1 and T before `kick`."""
        if not hasattr(self, "_tidx"):
            idx = defaultdict(list)
            for (gs, g), r in self.P.pg.items():
                s, w, k, h, a = self.P.games[g]
                idx[(r["team"], s)].append((k, gs, r))
            for lst in idx.values():
                lst.sort(key=lambda x: x[0])
            self._tidx, self._tp = idx, {}
        key = (team, T, kick)
        if key not in self._tp:
            acc = {}
            for s in (T - 1, T):
                w = PREV_W if s == T - 1 else 1.0
                for k, gs, r in self._tidx.get((team, s), []):
                    if k >= kick:
                        break
                    pos, sn = acc.get(gs, (r["pos"], 0.0))
                    acc[gs] = (pos, sn + w * (r["snaps"] or 0))
            self._tp[key] = acc
        return self._tp[key]

    def simulate(self, comp, stat, seed, draws=DRAWS):
        rng = np.random.default_rng(seed)
        mu, var = comp["mu_V"], comp["var_V"]
        if var > mu * 1.0001:
            V = rng.negative_binomial(mu * mu / (var - mu), mu / var, draws)
        else:
            V = rng.poisson(mu, draws)
        s = np.clip(rng.normal(comp["s"], np.sqrt(comp["pv_s"]), draws), S_LO, S_HI)
        phi = comp["phi"]
        sg = rng.beta(s * phi, (1 - s) * phi)
        X = rng.binomial(V, sg)
        if stat == "receptions":
            c = rng.beta(comp["ca"], comp["cb"], draws)
            X = rng.binomial(X, c)
        return X


def seed_of(gsis, stat, game):
    return zlib.crc32(f"{gsis}|{stat}|{game}".encode())


# =============================================================================
# populations
# =============================================================================

def load_p1_rows(path):
    """The c-24 P1 rows WITH their ids, asserted identical to rc.load_p1's."""
    rows = []
    with open(path, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r["p_bench"] in ("", "None"):
                continue
            rows.append({"season": int(r["season"]), "week": int(r["week"]), "game": r["game_id"],
                         "kick": float(r["kickoff_ts"]), "gsis": r["gsis_id"], "oid": r["outcome_id"],
                         "stat": r["stat"], "line": float(r["line"]), "y": float(r["y"]),
                         "m": float(r["p_model"]), "k": float(r["p_bench"])})
    canon = rc.load_p1(path)
    if len(canon) != len(rows) or any(
            (a["game"], a["stat"], a["line"], a["y"], a["m"], a["k"]) !=
            (b["game"], b["stat"], b["line"], b["y"], b["m"], b["k"]) for a, b in zip(rows, canon)):
        raise SystemExit("P1 rows differ from research.ranking_calibration.load_p1 - refusing")
    return rows


def build_p2(cache, c24_rows, workers, out):
    """c-24's P2 rebuilt WITH player ids, matched to rows.json."""
    from research import venue_spread as vs
    games, kout, kmeta, kq, _depth, book = vs.load(cache)
    resolve = vs.resolver()
    _m, _c, _a, _g, _mu, cons = vs.pair(games, kout, kmeta, kq, book, resolve)
    scored = []
    vs.step4(games, kmeta, kq, cons, resolve, workers, out, rows_out=scored)
    with open(c24_rows) as f:
        ref = json.load(f)["p2"]
    key = lambda r: (r["game"], r["stat"], float(r["line"]), float(r["y"]), round(float(r["k"]), 9))  # noqa: E731
    pool = defaultdict(list)
    for r in scored:
        pool[key(r)].append(r)
    rows, unmatched, m_moved = [], 0, 0
    for r in ref:
        cand = pool.get(key(r), [])
        best = min(cand, key=lambda c: abs(c["m"] - r["m"]), default=None)
        if best is None:
            unmatched += 1
            continue
        cand.remove(best)
        if abs(best["m"] - r["m"]) > 1e-9:
            m_moved += 1
        rows.append({"game": r["game"], "stat": r["stat"], "line": float(r["line"]), "y": r["y"],
                     "m": r["m"], "k": r["k"], "week": r["week"], "gsis": best["gsis"],
                     "kick": games[r["game"]]["kick"], "season": vs.SEASON})
    out(f"  P2 match: {len(rows)} of {len(ref)} c-24 rows matched; unmatched {unmatched}; "
        f"baseline p moved since c-24 on {m_moved} (the c-24 value is kept)")
    return rows, {"matched": len(rows), "reference": len(ref), "unmatched": unmatched, "m_moved": m_moved}


def predict_rows(fc, rows, con, out):
    """Adds r['d'] (decomposed P(over)) to every row. Team and position for the
    predicted game come from the player's own row for it, exactly as walkforward."""
    from research import walkforward as wf
    pw_by, snap_by = {}, {}
    census = Counter()
    fits = {}
    t0 = time.time()
    for r in rows:
        T = r["season"]
        if T not in pw_by:
            pw_by[T], snap_by[T] = wf.player_week_rows(con, T), wf.snap_rows(con, T)
            wf.Constants(T, {}, {"c27 constants": tuple(fc.C["fit_seasons"])}).check()
        g = r["game"]
        wk = fc.P.meta[g][1]
        pt = pw_by[T].get((r["gsis"], wk))
        if pt:
            pos, team = pt
        else:
            _snap, team, pos = snap_by[T].get((r["gsis"], g), (None, None, None))
        if team is None:
            s_, w_, k_, h, a = fc.P.meta[g]
            team = h  # never reached on these populations; counted below
            census["no team for the predicted game"] += 1
        key = (r["gsis"], r["stat"], g)
        if key not in fits:
            comp = fc.components(r["gsis"], r["stat"], g, r["kick"], T, team, pos)
            fits[key] = (comp, fc.simulate(comp, r["stat"], seed_of(*key)))
            census["fits"] += 1
            census["share from prior only"] += comp["n_share"] == 0
        r["d"] = min(max(prob_over(fits[key][1], r["line"]), rc.CLIP), 1 - rc.CLIP)
        census["rows"] += 1
    out(f"  predicted {census['rows']} rows, {census['fits']} fits in {time.time() - t0:.0f}s; {dict(census)}")
    return census


# =============================================================================
# scoring - c-24's functions, imported
# =============================================================================

def week_rows(rows):
    return [dict(r, game=f"{r['season']}-{r['week']:02d}") for r in rows]


def dsc(p, y):
    return rc.corp(p, y)["dsc"]


def mcb(p, y):
    return rc.corp(p, y)["mcb"]


def _diff(f, a, b, y, s=None):
    def g(i):
        u = f(a[i], y[i]) if s is None else f(a[i], y[i], s[i])
        v = f(b[i], y[i]) if s is None else f(b[i], y[i], s[i])
        return None if u is None or v is None else u - v
    return g


def score(name, rows, tests, out, block, draws):
    """d = decomposed, m = baseline, k = market, on identical rows."""
    pop = rc.Pop(f"{name} [{block}-block]", rows, "d", "m")
    d, m, y, s = pop.m, pop.k, pop.y, pop.s
    k = np.array([r["k"] for r in rows], float)
    boot = lambda f: pop.boot(f, draws=draws, seed=BOOT_SEED)  # noqa: E731
    cd, cm, ck = rc.corp(d, y), rc.corp(m, y), rc.corp(k, y)
    res = {"n": pop.n, "blocks": pop.games, "block": block,
           "corp": {"decomposed": cd, "baseline": cm, "market": ck},
           "auc": {"decomposed": rc.auc(d, y), "baseline": rc.auc(m, y), "market": rc.auc(k, y)},
           "wauc": {"decomposed": rc.wauc(d, y, s), "baseline": rc.wauc(m, y, s), "market": rc.wauc(k, y, s)},
           "brier": {"decomposed": rc.brier(d, y), "baseline": rc.brier(m, y), "market": rc.brier(k, y)}}
    out(f"\n== {name}: n {pop.n:,}, {pop.games} {block} blocks, realized {y.mean():.4f}")
    for lab, c in (("decomposed", cd), ("baseline", cm), ("market", ck)):
        out(f"   {lab:<10} Brier {c['bs']:.4f} = MCB {c['mcb']:.4f} - DSC {c['dsc']:.4f} + UNC {c['unc']:.4f}"
            f"   AUC {res['auc'][lab]:.4f}  wAUC {res['wauc'][lab]:.4f}")
    return pop, d, m, k, y, s, boot, res


def show(out, tests, pop_name, label, r):
    mde = 2.8 * r["se"] if r.get("se") else None
    r["mde"] = mde
    tests.append((pop_name, label, r))
    out(f"   {label:<46} {rc.fmt(r)}" + (f"  MDE {mde:.4f}" if mde else ""))


# =============================================================================
# main
# =============================================================================

def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--premise", action="store_true", help="Step 0 only")
    ap.add_argument("--posthoc-twin", action="store_true",
                    help="POST-HOC, not registered: the undecomposed twin, read from --out-dir/predictions.csv")
    ap.add_argument("--build-p2", action="store_true")
    ap.add_argument("--cache")
    ap.add_argument("--c24-rows")
    ap.add_argument("--p2-out")
    ap.add_argument("--ledger")
    ap.add_argument("--p2")
    ap.add_argument("--out-dir")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--draws", type=int, default=rc.BOOT)
    a = ap.parse_args()
    out = lambda s: print(s, flush=True)  # noqa: E731

    if a.posthoc_twin:
        return posthoc_twin(a, out)
    if a.build_p2:
        rows, info = build_p2(a.cache, a.c24_rows, a.workers, out)
        if not rows:
            raise SystemExit("P2 rebuild matched nothing - refusing")
        with open(a.p2_out, "w") as f:
            json.dump({"rows": rows, "info": info}, f)
        return 0

    con = ro()
    t0 = time.time()
    panel = Panel(con)
    out(f"panel: {len(panel.pg):,} player-games, {len(panel.tg):,} team-games, {len(panel.games):,} REG games"
        f"  ({time.time() - t0:.0f}s)")
    result = {"prereg": "fc25f38"}
    result["premise"] = premise(panel, out)
    verdict = result["premise"]["_rule"]["verdict"]
    if a.premise:
        return 0
    if verdict == "REFUTED":
        out("\nPREMISE REFUTED - per the pre-registration no model is fitted.")
        _dump(a.out_dir, result)
        return 0

    C = fit_constants(panel)
    result["constants"] = C
    out("\n== STEP 1 constants (2013-2022, method of moments) ==")
    for stat in VOL:
        out(f"   {stat}: " + "  ".join(f"{k} {v:.4g}" for k, v in C[stat].items()))
    out(f"   catch: tau2 {C['catch']['tau2']:.5f}  " + "  ".join(
        f"{p} {C['catch']['cbar'][p]:.3f}/{C['catch']['strength'][p]:.0f}" for p in ("WR", "TE", "RB")))
    out("   prior share: " + json.dumps({s: {k: round(v, 3) for k, v in d.items() if k.split('|')[0] in
                                            ('WR', 'TE', 'RB', 'QB')} for s, d in C["prior_share"].items()}))
    fc = Forecaster(panel, C)

    # ---- P1: c-24's ledger, reproduction check first
    p1 = load_p1_rows(a.ledger)
    out("\n== REPRODUCTION (c-24's gate) ==")
    ok_all = True
    for T, (n_exp, d_exp) in rc.P1_EXPECT.items():
        rs = [r for r in p1 if r["season"] == T]
        dd = float(np.mean([(r["m"] - r["y"]) ** 2 - (r["k"] - r["y"]) ** 2 for r in rs]))
        ok = len(rs) == n_exp and round(dd, 4) == d_exp
        ok_all &= ok
        out(f"   P1 {T}: n {len(rs)} (expect {n_exp})  Brier diff {dd:+.4f} (expect +{d_exp:.4f})  "
            f"{'OK' if ok else 'MISMATCH'}")
    if not ok_all:
        raise SystemExit("P1 does not reproduce c-24 - not read")
    out("\n== predicting P1 ==")
    result["p1_census"] = dict(predict_rows(fc, p1, con, out))
    if any("d" not in r for r in p1):
        raise SystemExit("decomposed coverage of P1 is not 100% - refusing")

    p2 = None
    if a.p2:
        with open(a.p2) as f:
            blob = json.load(f)
        p2, info = blob["rows"], blob["info"]
        result["p2_match"] = info
        if info["matched"] != rc.P2_EXPECT[0]:
            out(f"   P2 NOT READ: matched {info['matched']} of {rc.P2_EXPECT[0]}")
            p2 = None
        else:
            dd = float(np.mean([(r["m"] - r["y"]) ** 2 - (r["k"] - r["y"]) ** 2 for r in p2]))
            out(f"   P2 reproduction: n {len(p2)}  Brier diff {dd:+.4f} (expect +{rc.P2_EXPECT[1]:.4f})")
            if round(dd, 4) != rc.P2_EXPECT[1]:
                p2 = None
                out("   P2 NOT READ: reproduction failed")
            else:
                out("\n== predicting P2 ==")
                result["p2_census"] = dict(predict_rows(fc, p2, con, out))

    if a.out_dir:
        os.makedirs(a.out_dir, exist_ok=True)
        with open(os.path.join(a.out_dir, "predictions.csv"), "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(("pop", "season", "week", "game", "gsis", "stat", "line", "y", "baseline", "market",
                        "decomposed"))
            for pname, rs in (("P1", p1), ("P2", p2 or [])):
                for r in rs:
                    w.writerow((pname, r["season"], r.get("week"), r["game"], r["gsis"], r["stat"], r["line"],
                                r["y"], r["m"], r["k"], r["d"]))

    # ---- scoring
    tests = []
    res = result["scores"] = {}
    # PRIMARY
    pop, d, m, k, y, s, boot, R = score("P1 pooled 2023-2025 vs book close", week_rows(p1), tests, out, "week", a.draws)
    prim = boot(_diff(dsc, d, m, y))
    show(out, tests, "P1 pooled", "PRIMARY dDSC decomposed - baseline", prim)
    R["primary_dDSC"] = prim
    show(out, tests, "P1 pooled", "dDSC decomposed - market", R.setdefault("dDSC_vs_market", boot(_diff(dsc, d, k, y))))
    show(out, tests, "P1 pooled", "dAUC decomposed - baseline", R.setdefault("dAUC_vs_base", boot(_diff(lambda p, yy: rc.auc(p, yy), d, m, y))))
    show(out, tests, "P1 pooled", "d_wAUC decomposed - baseline", R.setdefault("dwAUC_vs_base", boot(_diff(rc.wauc, d, m, y, s))))
    show(out, tests, "P1 pooled", "dAUC decomposed - market", R.setdefault("dAUC_vs_market", boot(_diff(lambda p, yy: rc.auc(p, yy), d, k, y))))
    show(out, tests, "P1 pooled", "d_wAUC decomposed - market", R.setdefault("dwAUC_vs_market", boot(_diff(rc.wauc, d, k, y, s))))
    show(out, tests, "P1 pooled", "dMCB decomposed - baseline", R.setdefault("dMCB_vs_base", boot(_diff(mcb, d, m, y))))
    show(out, tests, "P1 pooled", "dBrier decomposed - baseline", R.setdefault("dBS_vs_base", boot(_diff(rc.brier, d, m, y))))
    show(out, tests, "P1 pooled", "dBrier decomposed - market", R.setdefault("dBS_vs_market", boot(_diff(rc.brier, d, k, y))))
    res["P1 pooled"] = R
    # primary re-read, game-block
    pop, d, m, k, y, s, boot, R = score("P1 pooled (game-block re-read)", p1, tests, out, "game", a.draws)
    show(out, tests, "P1 pooled game-block", "dDSC decomposed - baseline", R.setdefault("dDSC_vs_base", boot(_diff(dsc, d, m, y))))
    res["P1 pooled game-block"] = R
    # per season, per stat
    for T in (2023, 2024, 2025):
        pop, d, m, k, y, s, boot, R = score(f"P1 {T}", week_rows([r for r in p1 if r["season"] == T]),
                                            tests, out, "week", a.draws)
        show(out, tests, f"P1 {T}", "dDSC decomposed - baseline", R.setdefault("dDSC_vs_base", boot(_diff(dsc, d, m, y))))
        res[f"P1 {T}"] = R
    for st in VOL:
        pop, d, m, k, y, s, boot, R = score(f"P1 pooled {st}", week_rows([r for r in p1 if r["stat"] == st]),
                                            tests, out, "week", a.draws)
        show(out, tests, f"P1 {st}", "dDSC decomposed - baseline", R.setdefault("dDSC_vs_base", boot(_diff(dsc, d, m, y))))
        res[f"P1 {st}"] = R
    if p2:
        pop, d, m, k, y, s, boot, R = score("P2 weeks 2-3 2026 vs Kalshi mid", p2, tests, out, "game", a.draws)
        for lab, key, f, a_, b_, strat in (
                ("dDSC decomposed - baseline", "dDSC_vs_base", dsc, d, m, None),
                ("dDSC decomposed - market", "dDSC_vs_market", dsc, d, k, None),
                ("dAUC decomposed - baseline", "dAUC_vs_base", lambda p, yy: rc.auc(p, yy), d, m, None),
                ("d_wAUC decomposed - baseline", "dwAUC_vs_base", rc.wauc, d, m, s),
                ("dAUC decomposed - market", "dAUC_vs_market", lambda p, yy: rc.auc(p, yy), d, k, None),
                ("d_wAUC decomposed - market", "dwAUC_vs_market", rc.wauc, d, k, s),
                ("dMCB decomposed - baseline", "dMCB_vs_base", mcb, d, m, None),
                ("dBrier decomposed - baseline", "dBS_vs_base", rc.brier, d, m, None),
                ("dBrier decomposed - market", "dBS_vs_market", rc.brier, d, k, None)):
            R[key] = boot(_diff(f, a_, b_, y, strat))
            show(out, tests, "P2", lab, R[key])
        res["P2"] = R

    # ---- the verdict, as registered
    pr = res["P1 pooled"]["primary_dDSC"]
    dm = res["P1 pooled"]["dMCB_vs_base"]
    if pr["lo"] > 0:
        v = "resolution rises"
    elif pr["hi"] < 0:
        v = "resolution falls"
    else:
        v = f"no rise in resolution detected (MDE {pr['mde']:.4f})"
    if dm["hi"] < 0 and not pr["lo"] > 0:
        v += " - improved calibration and not resolution: FAILED this unit"
    result["verdict"] = v
    out(f"\n== VERDICT (registered rule, P1 pooled week-block): {v}")
    exc = sum(1 for *_x, r in tests if r.get("lo") is not None and (r["lo"] > 0 or r["hi"] < 0))
    out(f"   intervals computed {len(tests)} (declared 24); excluding zero {exc}")
    result["tests"] = [{"pop": p, "name": n, **r} for p, n, r in tests]
    _dump(a.out_dir, result)
    out(f"   runtime {time.time() - t0:.0f}s")
    return 0


# =============================================================================
# POST-HOC, NOT REGISTERED - the undecomposed twin (added after the smoke run)
# =============================================================================
# The decomposed model differs from models/baseline.py in more than the
# decomposition: it counts a played game with no stat row as 0, weights season
# T-1 at 0.5, and takes its role prior from a snap rank. So a difference against
# the baseline cannot be attributed to decomposing. The twin keeps ALL of that
# machinery - same window, weights, zero rows, snap-rank buckets, constants from
# 2013-2022 by the same method of moments - and shrinks the RAW STAT per game
# directly, with a negative binomial whose variance carries the posterior
# variance of the mean. decomposed - twin isolates the decomposition. Not among
# the 24 registered intervals; reported as context.

def twin_constants(panel):
    seasons = set(TRAIN)
    C = {}
    for stat, col, positions in (("receptions", "rec", REC_POS), ("rush_attempts", "car", RUSH_POS)):
        ps = _player_seasons(panel, seasons, positions)
        m = mom([[r[col] for _k, _g, r, _t in g] for g in ps.values()])
        C[stat] = {"k": m["k"], "within": m["within"], "vmr": m["within"] / m["mean"]}
    seg = defaultdict(lambda: {"rec": 0, "car": 0, "snaps": 0, "n": 0})
    for gs, games in panel.player.items():
        for k, s, g, r in games:
            if s in seasons:
                x = seg[(gs, s, r["team"], r["pos"])]
                x["rec"] += r["rec"]; x["car"] += r["car"]  # noqa: E702
                x["snaps"] += r["snaps"] or 0; x["n"] += 1  # noqa: E702
    grp = defaultdict(list)
    for (gs, s, team, pos), x in seg.items():
        grp[(s, team, pos)].append(x)
    prior = defaultdict(lambda: defaultdict(list))
    for (s, team, pos), xs in grp.items():
        xs.sort(key=lambda x: -x["snaps"])
        for i, x in enumerate(xs):
            if x["n"] >= MIN_GAMES:
                for stat, col in (("receptions", "rec"), ("rush_attempts", "car")):
                    prior[stat][(pos, min(i + 1, 3))].append(x[col] / x["n"])
                    prior[stat][(pos, 0)].append(x[col] / x["n"])
    C["prior"] = {st: {f"{p}|{b}": float(np.mean(v)) for (p, b), v in d.items()} for st, d in prior.items()}
    return C


def twin_prob(fc, TC, r, team, pos):
    from scipy.stats import nbinom
    stat, T, kick, gsis = r["stat"], r["season"], r["kick"], r["gsis"]
    col = "rec" if stat == "receptions" else "car"
    n = sx = snaps = 0.0
    for w, (k, s, g, pr) in fc._window(fc.P.player.get(gsis, []), T, kick):
        n += w; sx += w * pr[col]; snaps += w * (pr["snaps"] or 0)  # noqa: E702
    if n == 0:
        bucket = 2
    else:
        mates = [sn for gs2, (p2, sn) in fc._team_players(team, T, kick).items() if gs2 != gsis and p2 == pos]
        bucket = min(1 + sum(1 for x in mates if x > snaps), 3)
    pr_ = TC["prior"][stat]
    mu0 = pr_.get(f"{pos}|{bucket}", pr_.get(f"{pos}|0", 0.1))
    c = TC[stat]
    mean, pv = shrink(n, sx / n if n else mu0, c["k"], mu0, c["within"])
    mean = max(mean, 0.05)
    var = max(mean * c["vmr"] + pv, mean * 1.0001)
    size, prob = mean * mean / (var - mean), mean / var
    return float(nbinom.sf(np.floor(r["line"]), size, prob))


def posthoc_twin(a, out):
    con = ro()
    panel = Panel(con)
    C = fit_constants(panel)
    fc = Forecaster(panel, C)
    TC = twin_constants(panel)
    out("\n== POST-HOC (not registered): the undecomposed twin ==")
    out("   twin constants: " + "  ".join(f"{s} k {TC[s]['k']:.3f} vmr {TC[s]['vmr']:.3f}"
                                         for s in ("receptions", "rush_attempts")))
    from research import walkforward as wf
    rows = []
    with open(os.path.join(a.out_dir, "predictions.csv"), newline="") as f:
        for r in csv.DictReader(f):
            rows.append({"pop": r["pop"], "season": int(r["season"]),
                         "week": int(r["week"]) if r["week"] not in ("", "None") else None,
                         "game": r["game"], "gsis": r["gsis"], "stat": r["stat"], "line": float(r["line"]),
                         "y": float(r["y"]), "m": float(r["baseline"]), "k": float(r["market"]),
                         "d": float(r["decomposed"]), "kick": fc.P.meta[r["game"]][2]})
    if not rows:
        raise SystemExit("no predictions.csv rows - run the registered pass first")
    pw_by, snap_by = {}, {}
    for r in rows:
        T = r["season"]
        if T not in pw_by:
            pw_by[T], snap_by[T] = wf.player_week_rows(con, T), wf.snap_rows(con, T)
        wk = fc.P.meta[r["game"]][1]
        pt = pw_by[T].get((r["gsis"], wk))
        if pt:
            pos, team = pt
        else:
            _snap, team, pos = snap_by[T].get((r["gsis"], r["game"]), (None, None, None))
        r["t"] = min(max(twin_prob(fc, TC, r, team, pos), rc.CLIP), 1 - rc.CLIP)
    res = {}
    for pname, sub, block in (("P1 pooled", [r for r in rows if r["pop"] == "P1"], "week"),
                              ("P1 receptions", [r for r in rows if r["pop"] == "P1" and r["stat"] == "receptions"], "week"),
                              ("P1 rush_attempts", [r for r in rows if r["pop"] == "P1" and r["stat"] == "rush_attempts"], "week"),
                              ("P2", [r for r in rows if r["pop"] == "P2"], "game")):
        if not sub:
            continue
        rs = week_rows(sub) if block == "week" else sub
        pop = rc.Pop(pname, rs, "d", "t")
        d, t, y = pop.m, pop.k, pop.y
        m = np.array([r["m"] for r in rs], float)
        ct, cd, cm = rc.corp(t, y), rc.corp(d, y), rc.corp(m, y)
        out(f"   {pname} [{block}-block, {pop.games}]: DSC decomposed {cd['dsc']:.4f}  twin {ct['dsc']:.4f}  "
            f"baseline {cm['dsc']:.4f};  MCB {cd['mcb']:.4f} / {ct['mcb']:.4f} / {cm['mcb']:.4f};  "
            f"AUC {rc.auc(d, y):.4f} / {rc.auc(t, y):.4f} / {rc.auc(m, y):.4f}")
        a1 = pop.boot(_diff(dsc, d, t, y), draws=a.draws, seed=BOOT_SEED)
        a2 = pop.boot(_diff(dsc, t, m, y), draws=a.draws, seed=BOOT_SEED)
        out(f"      dDSC decomposed - twin   {rc.fmt(a1)}")
        out(f"      dDSC twin - baseline     {rc.fmt(a2)}")
        res[pname] = {"dsc": {"decomposed": cd["dsc"], "twin": ct["dsc"], "baseline": cm["dsc"]},
                      "mcb": {"decomposed": cd["mcb"], "twin": ct["mcb"], "baseline": cm["mcb"]},
                      "dDSC_decomposed_minus_twin": a1, "dDSC_twin_minus_baseline": a2}
    with open(os.path.join(a.out_dir, "posthoc_twin.json"), "w") as f:
        json.dump(res, f, indent=1, default=float)
    return 0


def _dump(out_dir, result):
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
        with open(os.path.join(out_dir, "result.json"), "w") as f:
            json.dump(result, f, indent=1, default=float)


if __name__ == "__main__":
    sys.exit(main())
