"""f-14: an INDEPENDENT re-derivation of the opportunity residual (a-18).

    python -m research.f14_residual_verify [--draws 1000] [--out PATH]

Written without importing a-18's module. Reads analytics.db and market_log.db
READ-ONLY (mode=ro). Writes nothing but the --out JSON.

What it measures, per production stat and position group:

  fit        OLS production ~ opportunity per (season, group) over player-weeks,
             R^2 point value (independent implementation: np.linalg.lstsq).
  lookahead  the residual under the FULL-SEASON fit against the residual under a
             strictly AS-OF fit (beta from the previous season plus this season's
             weeks < w), and the forward claim - residual to date (weeks <= W)
             against residual rest-of-season (weeks > W) - under both.
  persist    per-game mean residual and per-game mean fitted value (opportunity),
             season T vs T+1 and odd vs even weeks, player-block bootstrap.
  survivor   how lag-1 r moves with the games requirement, and whether the
             players who drop out differ in residual from those who stay.
  nulls      counts of player-weeks with a null input, and a check that the
             weeks dropped for a null are absent from BOTH sides.
  record     the r ~ 0.09 of CLAUDE.md, re-derived with research/externals.py's
             own definition (residual vs the player's own strictly-prior
             expanding mean, >=4 prior games, season mean over >=8 games),
             from nfl_player_week rather than the pw_*.parquet it read.
"""
import argparse
import json
import sqlite3
import sys
import zlib

import numpy as np

AN = "file:D:/calibrated-sports/data/analytics.db?mode=ro"
ML = "file:D:/calibrated-sports/data/market_log.db?mode=ro"

GROUP = {"WR": "WR", "TE": "TE", "RB": "RB", "FB": "RB", "HB": "RB", "QB": "QB"}
STATS = {
    # stat: (groups, first season, opportunity columns)
    "receiving_yards": (("WR", "TE", "RB"), 2009, ("tgt", "ay")),
    "receiving_yards_tgt_only": (("WR", "TE", "RB"), 2009, ("tgt",)),
    "receptions": (("WR", "TE", "RB"), 2009, ("tgt",)),
    "rushing_yards": (("RB", "QB"), 1999, ("car",)),
    "receiving_tds": (("WR", "TE", "RB"), 2009, ("tgt",)),
    "rushing_tds": (("RB", "QB"), 1999, ("car",)),
    # the same fit with QB kneel-downs removed from BOTH sides (f-14 addition:
    # nflverse sets rush_attempt=1 on every kneel, so a kneel is a carry that
    # can only buy about -1 yard, and kneels follow the team winning)
    "rushing_yards_no_kneel": (("RB", "QB"), 1999, ("car",)),
}
RAW = "D:/calibrated-sports/data/raw/nflverse"
LAST = 2025             # last COMPLETE season; 2026 is in progress
MIN_PLAYERS = 30


def connect(uri):
    return sqlite3.connect(uri, uri=True, timeout=5)


def positions(ml):
    pos = {}
    multi = 0
    for gsis, p in ml.execute("SELECT DISTINCT gsis_id, position FROM nfl_player_week "
                              "WHERE position IS NOT NULL"):
        g = GROUP.get(p)
        if g is None:
            continue
        if gsis in pos and pos[gsis] != g:
            multi += 1
        pos[gsis] = g
    return pos, multi


def kneel_keys():
    """{(game_id, play_id)} of every qb_kneel, newest pull per season."""
    import glob
    import os
    import polars as pl
    newest = {}
    for f in glob.glob(RAW + "/*/play_by_play_*.parquet"):
        season = os.path.basename(f)[len("play_by_play_"):-len(".parquet")]
        if season.isdigit():
            newest[season] = max(newest.get(season, f), f)
    keys = set()
    for season, f in sorted(newest.items()):
        d = pl.read_parquet(f, columns=["game_id", "play_id", "qb_kneel"])
        for gid, pid in d.filter(pl.col("qb_kneel") == 1).select("game_id", "play_id").rows():
            keys.add((gid, int(pid)))
    if not keys:
        raise SystemExit("found no kneels at all - the raw mirror was not read")
    return keys, len(newest)


def load_spine(an, kneels=None):
    """{(pid, season, week): dict} from the play rows, built in Python."""
    rec, rush = {}, {}
    cur = an.execute("SELECT player_id, season, week, role, is_target, is_reception, "
                     "is_carry, air_yards, yards, game_id, play_id FROM f_play_usage "
                     "WHERE season_type='REG' AND role IN ('receiver','rusher')")
    for pid, s, w, role, t, r, c, ay, y, gid, play in cur:
        if kneels is not None and (role != "rusher" or (gid, play) in kneels):
            continue
        k = (pid, s, w)
        if role == "receiver":
            d = rec.setdefault(k, {"tgt": 0, "rec": 0, "yds": 0.0, "ay": 0.0,
                                   "null_y": 0, "null_ay": 0, "null_flag": 0})
            if t is None or r is None:
                d["null_flag"] += 1
                continue
            if t:
                d["tgt"] += 1
                if ay is None:
                    d["null_ay"] += 1
                else:
                    d["ay"] += ay
            if r:
                d["rec"] += 1
                if y is None:
                    d["null_y"] += 1
                else:
                    d["yds"] += y
        else:
            d = rush.setdefault(k, {"car": 0, "yds": 0.0, "null_y": 0, "null_flag": 0})
            if c is None:
                d["null_flag"] += 1
                continue
            if c:
                d["car"] += 1
                if y is None:
                    d["null_y"] += 1
                else:
                    d["yds"] += y
    return rec, rush


def load_weekly(ml):
    """Newest data_version per player-week."""
    out = {}
    for gsis, s, w, dv, tgt, car, rtd, rutd in ml.execute(
            "SELECT gsis_id, season, week, data_version, targets, carries, receiving_tds, "
            "rushing_tds FROM nfl_player_week WHERE season_type='REG'"):
        k = (gsis, s, w)
        if k not in out or dv > out[k][0]:
            out[k] = (dv, tgt, car, rtd, rutd)
    return out


def observations(stat, rec, rush, weekly, pos):
    """[(pid, season, week, group, y, x...)] and null accounting."""
    groups, first, _opp = STATS[stat]
    obs, nulls = [], {"dropped_null": 0, "kept": 0, "zero_opp_skipped": 0}
    if stat in ("receiving_yards", "receiving_yards_tgt_only", "receptions"):
        for (pid, s, w), d in rec.items():
            if s < first or pos.get(pid) not in groups:
                continue
            if d["tgt"] == 0 and not d["null_flag"]:
                nulls["zero_opp_skipped"] += 1
                continue
            if d["null_flag"]:
                nulls["dropped_null"] += 1
                continue
            if stat == "receptions":
                y, x = d["rec"], (d["tgt"],)
            else:
                if d["null_y"] or (stat == "receiving_yards" and d["null_ay"]):
                    nulls["dropped_null"] += 1
                    continue
                y = d["yds"]
                x = (d["tgt"], d["ay"]) if stat == "receiving_yards" else (d["tgt"],)
            obs.append((pid, s, w, pos[pid], float(y)) + tuple(float(v) for v in x))
            nulls["kept"] += 1
    elif stat in ("rushing_yards", "rushing_yards_no_kneel"):
        for (pid, s, w), d in rush.items():
            if s < first or pos.get(pid) not in groups:
                continue
            if d["car"] == 0 and not d["null_flag"]:
                nulls["zero_opp_skipped"] += 1
                continue
            if d["null_flag"] or d["null_y"]:
                nulls["dropped_null"] += 1
                continue
            obs.append((pid, s, w, pos[pid], d["yds"], float(d["car"])))
            nulls["kept"] += 1
    else:
        yi, xi = (3, 1) if stat == "receiving_tds" else (4, 2)
        for (pid, s, w), row in weekly.items():
            if s < first or pos.get(pid) not in groups:
                continue
            y, x = row[yi], row[xi]
            if x is None or y is None:
                if (x or 0) or (y or 0):
                    nulls["dropped_null"] += 1
                continue
            if not x:
                nulls["zero_opp_skipped"] += 1
                continue
            obs.append((pid, s, w, pos[pid], float(y), float(x)))
            nulls["kept"] += 1
    return obs, nulls


def ols(X, y):
    A = np.column_stack([np.ones(len(y)), X])
    beta, *_ = np.linalg.lstsq(A, y, rcond=None)
    fit = A @ beta
    sst = ((y - y.mean()) ** 2).sum()
    return beta, 1 - ((y - fit) ** 2).sum() / sst


def arrays(obs):
    pid = np.array([o[0] for o in obs])
    season = np.array([o[1] for o in obs])
    week = np.array([o[2] for o in obs])
    grp = np.array([o[3] for o in obs])
    y = np.array([o[4] for o in obs])
    X = np.array([o[5:] for o in obs])
    return pid, season, week, grp, y, X


def full_fits(season, grp, y, X):
    """Full-season fit per (season, group). Returns fitted array and R^2 table."""
    fitted = np.full(len(y), np.nan)
    r2 = {}
    for s in np.unique(season):
        for g in np.unique(grp):
            m = (season == s) & (grp == g)
            if m.sum() == 0:
                continue
            beta, r = ols(X[m], y[m])
            fitted[m] = np.column_stack([np.ones(m.sum()), X[m]]) @ beta
            r2[(int(s), str(g))] = (float(r), int(m.sum()))
    return fitted, r2


def asof_fitted(season, week, grp, y, X):
    """Strictly as-of: beta from season-1 (all weeks) plus season weeks < w."""
    fitted = np.full(len(y), np.nan)
    for s in np.unique(season):
        for g in np.unique(grp):
            ms = (season == s) & (grp == g)
            prev = (season == s - 1) & (grp == g)
            if prev.sum() == 0:
                continue
            for w in np.unique(week[ms]):
                train = prev | (ms & (week < w))
                beta, _ = ols(X[train], y[train])
                mw = ms & (week == w)
                fitted[mw] = np.column_stack([np.ones(mw.sum()), X[mw]]) @ beta
    return fitted


def corr(a, b):
    a, b = np.asarray(a, float), np.asarray(b, float)
    if len(a) < 3 or a.std() == 0 or b.std() == 0:
        return None
    return float(np.corrcoef(a, b)[0, 1])


def boot_pairs(blocks, a, b, draws, seed):
    """Pearson r of paired values, player-block bootstrap. blocks: array of ids."""
    point = corr(a, b)
    ub, inv = np.unique(blocks, return_inverse=True)
    # sufficient stats per block
    k = len(ub)
    S = np.zeros((k, 6))
    np.add.at(S, inv, np.column_stack([np.ones(len(a)), a, b, a * b, a * a, b * b]))
    rng = np.random.default_rng(seed)
    rs = []
    for _ in range(draws):
        idx = rng.integers(0, k, k)
        n, sx, sy, sxy, sxx, syy = S[idx].sum(0)
        vx, vy = sxx - sx * sx / n, syy - sy * sy / n
        if vx > 0 and vy > 0:
            rs.append((sxy - sx * sy / n) / np.sqrt(vx * vy))
    lo, hi = np.percentile(rs, [2.5, 97.5])
    return {"r": point, "lo": float(lo), "hi": float(hi), "players": int(k), "pairs": int(len(a))}


def player_season(pid, season, week, grp, fitted, resid, mask=None):
    """{(pid, season): (group, [weeks], [fitted], [resid])}"""
    out = {}
    for i in range(len(pid)):
        if mask is not None and not mask[i]:
            continue
        if np.isnan(fitted[i]):
            continue
        d = out.setdefault((pid[i], int(season[i])), (grp[i], [], [], []))
        d[1].append(int(week[i]))
        d[2].append(fitted[i])
        d[3].append(resid[i])
    return out


def lag1_season(ps, g, min_games, draws, seed, last=LAST):
    ids, oa, ob, ra, rb = [], [], [], [], []
    for (p, s), (gg, wk, f, r) in ps.items():
        if gg != g or s + 1 > last:
            continue
        nx = ps.get((p, s + 1))
        if nx is None or len(wk) < min_games or len(nx[1]) < min_games:
            continue
        ids.append(p)
        oa.append(np.mean(f)); ob.append(np.mean(nx[2]))
        ra.append(np.mean(r)); rb.append(np.mean(nx[3]))
    if len(set(ids)) < MIN_PLAYERS:
        return None
    ids = np.array(ids)
    return {"opportunity": boot_pairs(ids, np.array(oa), np.array(ob), draws, seed),
            "residual": boot_pairs(ids, np.array(ra), np.array(rb), draws, seed + 1)}


def split_half(ps, g, draws, seed, last=LAST, min_half=3):
    ids, oa, ob, ra, rb = [], [], [], [], []
    for (p, s), (gg, wk, f, r) in ps.items():
        if gg != g or s > last:
            continue
        odd = [i for i, w in enumerate(wk) if w % 2 == 1]
        even = [i for i, w in enumerate(wk) if w % 2 == 0]
        if len(odd) < min_half or len(even) < min_half:
            continue
        ids.append(p)
        oa.append(np.mean([f[i] for i in odd])); ob.append(np.mean([f[i] for i in even]))
        ra.append(np.mean([r[i] for i in odd])); rb.append(np.mean([r[i] for i in even]))
    if len(set(ids)) < MIN_PLAYERS:
        return None
    ids = np.array(ids)
    return {"opportunity": boot_pairs(ids, np.array(oa), np.array(ob), draws, seed),
            "residual": boot_pairs(ids, np.array(ra), np.array(rb), draws, seed + 1)}


def forward(ps, g, W, draws, seed, last=LAST, min_before=4, min_after=4):
    """Residual to date (weeks <= W) against rest of season (weeks > W)."""
    ids, oa, ob, ra, rb = [], [], [], [], []
    for (p, s), (gg, wk, f, r) in ps.items():
        if gg != g or s > last:
            continue
        bi = [i for i, w in enumerate(wk) if w <= W]
        ai = [i for i, w in enumerate(wk) if w > W]
        if len(bi) < min_before or len(ai) < min_after:
            continue
        ids.append(p)
        oa.append(np.mean([f[i] for i in bi])); ob.append(np.mean([f[i] for i in ai]))
        ra.append(np.mean([r[i] for i in bi])); rb.append(np.mean([r[i] for i in ai]))
    if len(set(ids)) < MIN_PLAYERS:
        return None
    ids = np.array(ids)
    return {"opportunity": boot_pairs(ids, np.array(oa), np.array(ob), draws, seed),
            "residual": boot_pairs(ids, np.array(ra), np.array(rb), draws, seed + 1)}


def survivorship(ps, g, min_games=5, last=LAST):
    """Among qualifying player-seasons in T, compare those that return in T+1
    (>= min_games) with those that do not."""
    stay, leave = [], []
    for (p, s), (gg, wk, f, r) in ps.items():
        if gg != g or s + 1 > last or len(wk) < min_games:
            continue
        nx = ps.get((p, s + 1))
        (stay if nx is not None and len(nx[1]) >= min_games else leave).append(np.mean(r))
    if not stay or not leave:
        return None
    return {"n_stay": len(stay), "n_leave": len(leave),
            "share_leave": len(leave) / (len(stay) + len(leave)),
            "mean_resid_stay": float(np.mean(stay)), "mean_resid_leave": float(np.mean(leave))}


def record_r(ml, draws):
    """research/externals.py part (B), re-derived from nfl_player_week."""
    rows = {}
    for gsis, s, w, dv, pos, rec, ryd, car in ml.execute(
            "SELECT gsis_id, season, week, data_version, position, receptions, "
            "receiving_yards, carries FROM nfl_player_week WHERE season_type='REG' "
            "AND season BETWEEN 2016 AND 2024"):
        k = (gsis, s, w)
        if k not in rows or dv > rows[k][0]:
            rows[k] = (dv, pos, rec, ryd, car)
    out = {}
    for stat, idx, poss, minp in (("receptions", 2, ("WR", "TE"), 2.0),
                                  ("receiving_yards", 3, ("WR", "TE"), 25.0),
                                  ("carries", 4, ("RB",), 6.0)):
        by = {}
        for (g, s, w), row in rows.items():
            if row[1] in poss:
                by.setdefault((g, s), []).append((w, row[idx]))
        ps = {}
        for (g, s), lst in by.items():
            lst.sort()
            cs, gp, res = 0.0, 0, []
            for w, v in lst:
                # externals.py: cum_sum of the stat (nulls propagate as polars
                # cum_sum skips them), gp = row index; base = prior sum / gp
                if gp >= 4 and v is not None:
                    base = cs / gp
                    if base >= minp:
                        res.append(v - base)
                cs += (v or 0.0)
                gp += 1
            if len(res) >= 8:
                ps[(g, s)] = float(np.mean(res))
        ids, a, b = [], [], []
        for (g, s), m in ps.items():
            if (g, s + 1) in ps:
                ids.append(g); a.append(m); b.append(ps[(g, s + 1)])
        out[stat] = boot_pairs(np.array(ids), np.array(a), np.array(b), draws, 7)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--draws", type=int, default=1000)
    ap.add_argument("--out", default="f14_out.json")
    ap.add_argument("--stat", action="append")
    a = ap.parse_args(argv)
    an, ml = connect(AN), connect(ML)
    pos, multi = positions(ml)
    print("positions: %d players mapped; %d carry more than one group" % (len(pos), multi), flush=True)
    rec, rush = load_spine(an)
    kn, kn_seasons = kneel_keys()
    _r, rush_nk = load_spine(an, kn)
    print("kneels: %d plays over %d seasons" % (len(kn), kn_seasons), flush=True)
    weekly = load_weekly(ml)
    print("spine: %d receiver-weeks, %d rusher-weeks; weekly: %d" % (len(rec), len(rush), len(weekly)), flush=True)
    result = {"positions_multi_group": multi, "record_0_09": record_r(ml, a.draws), "stats": {}}
    print("record r (externals.py definition):", json.dumps(result["record_0_09"]), flush=True)
    for stat in (a.stat or STATS):
        groups = STATS[stat][0]
        obs, nulls = observations(stat, rec, rush_nk if stat.endswith("no_kneel") else rush,
                                  weekly, pos)
        pid, season, week, grp, y, X = arrays(obs)
        fitted, r2 = full_fits(season, grp, y, X)
        resid = y - fitted
        af = asof_fitted(season, week, grp, y, X)
        ares = y - af
        ok = ~np.isnan(af)
        la = {"corr_full_vs_asof_resid_playerweek": corr(resid[ok], ares[ok]),
              "n": int(ok.sum())}
        ps_full = player_season(pid, season, week, grp, fitted, resid)
        ps_asof = player_season(pid, season, week, grp, af, ares)
        S = {"nulls": nulls, "r2": {}, "lookahead": la, "groups": {}}
        for g in groups:
            vals = sorted(v[0] for (s, gg), v in r2.items() if gg == g and s <= LAST)
            S["r2"][g] = {"median": float(np.median(vals)), "min": vals[0], "max": vals[-1],
                          "seasons": len(vals),
                          "by_season": {str(s): round(v[0], 4) for (s, gg), v in sorted(r2.items()) if gg == g}}
            seed = zlib.crc32((stat + g).encode()) % 10**6
            G = {
                "lag1_season_min5": lag1_season(ps_full, g, 5, a.draws, seed),
                "split_half": split_half(ps_full, g, a.draws, seed + 10),
                "forward_W8_fullfit": forward(ps_full, g, 8, a.draws, seed + 20),
                "forward_W8_asof": forward(ps_asof, g, 8, a.draws, seed + 30),
                "lag1_season_min5_asof": lag1_season(ps_asof, g, 5, a.draws, seed + 40),
                "survivorship": survivorship(ps_full, g),
                "lag1_by_min_games": {},
            }
            for mg in (1, 3, 8, 12):
                got = lag1_season(ps_full, g, mg, 200, seed + 50 + mg)
                G["lag1_by_min_games"][str(mg)] = got and {
                    "residual_r": got["residual"]["r"], "opportunity_r": got["opportunity"]["r"],
                    "pairs": got["residual"]["pairs"]}
            S["groups"][g] = G
            l1 = G["lag1_season_min5"]
            print("%-26s %s R2 med %.3f | lag1 resid %s opp %s | split resid %s | fwd full %s asof %s"
                  % (stat, g, S["r2"][g]["median"],
                     l1 and "%.3f" % l1["residual"]["r"], l1 and "%.3f" % l1["opportunity"]["r"],
                     G["split_half"] and "%.3f" % G["split_half"]["residual"]["r"],
                     G["forward_W8_fullfit"] and "%.3f" % G["forward_W8_fullfit"]["residual"]["r"],
                     G["forward_W8_asof"] and "%.3f" % G["forward_W8_asof"]["residual"]["r"]),
                  flush=True)
        print("  nulls", nulls, "lookahead", la, flush=True)
        result["stats"][stat] = S
    with open(a.out, "w") as f:
        json.dump(result, f, indent=1, default=str)
    n = len(result["stats"])
    if n == 0:
        raise SystemExit("no stat produced output")
    print("wrote", a.out, "stats:", n)
    return 0


if __name__ == "__main__":
    sys.exit(main())
