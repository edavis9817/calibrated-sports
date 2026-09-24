"""F12 step 2: conditional on a line move of size D, is the outcome biased against the NEW line?

Runs exactly the design pre-registered in docs/F12-line-movement.md section 2,
committed at d528d72 before this file existed. Nothing here may be tuned after
reading its output; a change to a definition is a new registration.

    P  NFL 2023-2025 spreads and totals, Odds API historical featured snapshots
       open (lead 120-504h, nearest 168h) -> close (lead <= 1h), and
       late (lead 12-48h, nearest 24h)   -> close
    S  CFB, CFBD spread_open/total_open -> spread/total (untimed), one provider per
       game in the order DraftKings, Bovada, ESPN Bet

Units are points. Spread lines are converted to expected home margin m = -h, where
h is the home handicap (home covers iff margin + h > 0). D = line_close - line_from
in those units, e = outcome - line_close. Under efficiency E[e | D] = 0; a NEGATIVE
slope, or a negative band mean of sign(D)*e, is overreaction.

Intervals: bootstrap over season-week clusters, 2,000 draws, percentile 95%;
z = estimate / bootstrap SE. Bonferroni |z| > 2.81 for P's 10 tests.

    python -m research.f12_movement_test --extract D:/temp/f12/extract.db --out <json>
"""
import argparse
import collections
import json
import sqlite3
import statistics

import numpy as np

H = 3600.0
OPEN_WIN, OPEN_TARGET = (120 * H, 504 * H), 168 * H
LATE_WIN, LATE_TARGET = (12 * H, 48 * H), 24 * H
CLOSE_MAX = 1 * H
MIN_COMMON_BOOKS = 2
BANDS = ((0.0, 1.0, "(0, 1]"), (1.0, 2.5, "(1, 2.5]"), (2.5, float("inf"), "(2.5, inf)"))
DRAWS = 2000
SEED = 20260924
BONFERRONI_Z = 2.81        # P: 10 tests
S_BONFERRONI_Z = 2.73      # S: 8 tests
CFB_PROVIDERS = ("DraftKings", "Bovada", "ESPN Bet")


# ----------------------------------------------------------------------------- P

def nfl_rows(c):
    games = {}
    for gid, season, week, gt, ko, home, away, hs, as_ in c.execute(
            "SELECT game_id, season, week, game_type, kickoff_ts, home_team, away_team, "
            "home_score, away_score FROM nfl_games WHERE season BETWEEN 2023 AND 2025"):
        if hs is None or as_ is None:
            continue
        games[gid] = dict(season=season, week=week, ko=ko, home=home, away=away,
                          margin=hs - as_, total=hs + as_)
    # snap[market][game][ts][book] = line in expected-home-margin (spread) or total points
    snap = {"spread": collections.defaultdict(lambda: collections.defaultdict(dict)),
            "total": collections.defaultdict(lambda: collections.defaultdict(dict))}
    away_only = collections.defaultdict(lambda: collections.defaultdict(dict))
    for e, v, mt, subj, line, side, ts in c.execute(
            "SELECT event_id, venue, market_type, subject, line, side, ts FROM oddsapi_quotes "
            "WHERE source='oddsapi_historical' AND market_type IN ('spreads','totals')"):
        g = games.get(e)
        if g is None or line is None or ts >= g["ko"]:
            continue
        if mt == "totals":
            if side == "over":
                snap["total"][e][ts][v] = line
        elif subj == g["home"]:
            snap["spread"][e][ts][v] = -line
        elif subj == g["away"]:
            away_only[e][ts][v] = line          # m = -h_home = +h_away
    for e, by_ts in away_only.items():         # home row wins where both exist
        for ts, books in by_ts.items():
            for v, line in books.items():
                snap["spread"][e][ts].setdefault(v, line)
    return games, snap


def pick(ts_list, ko, win, target):
    cands = [t for t in ts_list if win[0] <= ko - t <= win[1]]
    return min(cands, key=lambda t: (abs((ko - t) - target), -t)) if cands else None


def build_p(games, snap):
    out = {}
    for market, by_game in snap.items():
        for horizon, win, target in (("open", OPEN_WIN, OPEN_TARGET), ("late", LATE_WIN, LATE_TARGET)):
            rows, why = [], collections.Counter()
            for gid, g in games.items():
                s = by_game.get(gid)
                if not s:
                    why["no quotes"] += 1
                    continue
                ts = sorted(s)
                closes = [t for t in ts if 0 < g["ko"] - t <= CLOSE_MAX]
                if not closes:
                    why["no close snapshot"] += 1
                    continue
                tc = closes[-1]
                tf = pick(ts, g["ko"], win, target)
                if tf is None:
                    why[f"no {horizon} snapshot"] += 1
                    continue
                common = sorted(set(s[tc]) & set(s[tf]))
                if len(common) < MIN_COMMON_BOOKS:
                    why["<2 common books"] += 1
                    continue
                d = statistics.median(s[tc][b] - s[tf][b] for b in common)
                close = statistics.median(s[tc].values())
                y = g["margin"] if market == "spread" else g["total"]
                rows.append(dict(game=gid, cluster=(g["season"], g["week"]), d=d, close=close,
                                 e=y - close, lead_from_h=(g["ko"] - tf) / H,
                                 lead_close_h=(g["ko"] - tc) / H, books=len(common)))
            out[(market, horizon)] = (rows, dict(why))
    return out


# ------------------------------------------------------------------------ estimation

def slope(d, e):
    if len(d) < 3 or np.var(d) == 0:
        return float("nan")
    return float(np.cov(d, e, bias=True)[0, 1] / np.var(d))


def band_stats(d, e):
    res = {}
    for lo, hi, name in BANDS:
        m = (np.abs(d) > lo) & (np.abs(d) <= hi)
        se = np.sign(d[m]) * e[m]
        nonpush = se != 0
        res[name] = (float(se.mean()) if m.any() else float("nan"),
                     float((se[nonpush] > 0).mean()) if nonpush.any() else float("nan"),
                     int(m.sum()))
    return res


def estimate(rows, rng):
    d = np.array([r["d"] for r in rows], float)
    e = np.array([r["e"] for r in rows], float)
    cl = [r["cluster"] for r in rows]
    keys = sorted(set(cl))
    idx = collections.defaultdict(list)
    for i, k in enumerate(cl):
        idx[k].append(i)
    groups = [np.array(idx[k]) for k in keys]
    point = {"slope": slope(d, e), "intercept": float(e.mean() - slope(d, e) * d.mean()),
             "bands": band_stats(d, e)}
    boots = {"slope": []} | {b[2]: [] for b in BANDS} | {b[2] + " cover": [] for b in BANDS}
    for _ in range(DRAWS):
        pick_ = rng.integers(0, len(groups), len(groups))
        ii = np.concatenate([groups[j] for j in pick_])
        boots["slope"].append(slope(d[ii], e[ii]))
        bs = band_stats(d[ii], e[ii])
        for name, (mean, cover, _n) in bs.items():
            boots[name].append(mean)
            boots[name + " cover"].append(cover)

    def summ(est, draws, null=0.0):
        a = np.array(draws, float)
        a = a[~np.isnan(a)]
        if len(a) < DRAWS * 0.9:
            return {"est": est, "lo": None, "hi": None, "se": None, "z": None,
                    "note": f"only {len(a)} of {DRAWS} draws estimable"}
        se = float(a.std(ddof=1))
        return {"est": est, "lo": float(np.percentile(a, 2.5)), "hi": float(np.percentile(a, 97.5)),
                "se": se, "z": ((est - null) / se) if se > 0 else None, "null": null}

    out = {"n": len(rows), "clusters": len(keys),
           "d_dist": {q: float(np.percentile(d, p)) for q, p in
                      (("p10", 10), ("p25", 25), ("p50", 50), ("p75", 75), ("p90", 90))},
           "share_d_zero": float((d == 0).mean()),
           "d_zero": {"n": int((d == 0).sum()),
                      "mean_e": float(e[d == 0].mean()) if (d == 0).any() else None},
           "mean_e_all": float(e.mean()),
           "slope": summ(point["slope"], boots["slope"]),
           "intercept": point["intercept"], "bands": {}}
    for lo, hi, name in BANDS:
        mean, cover, n = point["bands"][name]
        out["bands"][name] = {"n": n, "mean_signed_e": summ(mean, boots[name]),
                              "toward_side_covers_close": summ(cover, boots[name + " cover"], null=0.5)}
    return out


# ----------------------------------------------------------------------------- S

def build_s(c):
    games = {}
    for gid, season, stype, week, hp, ap in c.execute(
            "SELECT game_id, season, season_type, week, home_points, away_points FROM cfb_games "
            "WHERE valid_to_ts IS NULL AND home_points IS NOT NULL AND away_points IS NOT NULL"):
        games[gid] = dict(cluster=(season, stype, week), margin=hp - ap, total=hp + ap)
    lines = collections.defaultdict(dict)
    for gid, prov, sp, spo, tot, toto in c.execute(
            "SELECT game_id, provider, spread, spread_open, total, total_open FROM cfb_game_lines"):
        lines[gid][prov] = (sp, spo, tot, toto)
    out = {}
    for market, (i_last, i_open) in (("spread", (0, 1)), ("total", (2, 3))):
        rows, why, used = [], collections.Counter(), collections.Counter()
        for gid, by_p in lines.items():
            g = games.get(gid)
            if g is None:
                why["no final score"] += 1
                continue
            chosen = next((p for p in CFB_PROVIDERS if p in by_p and by_p[p][i_last] is not None
                           and by_p[p][i_open] is not None), None)
            if chosen is None:
                why["no provider with open and last"] += 1
                continue
            used[chosen] += 1
            last, opn = by_p[chosen][i_last], by_p[chosen][i_open]
            if market == "spread":
                last, opn = -last, -opn          # CFBD negative = home favoured: m = -spread
            y = g["margin"] if market == "spread" else g["total"]
            rows.append(dict(game=gid, cluster=g["cluster"], d=last - opn, close=last, e=y - last))
        out[market] = (rows, dict(why), dict(used))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--extract", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    c = sqlite3.connect(f"file:{a.extract}?mode=ro", uri=True)
    rng = np.random.default_rng(SEED)
    games, snap = nfl_rows(c)
    if not games or not snap["spread"] or not snap["total"]:
        raise SystemExit("P: empty read - refusing to report")
    res = {"registration": "docs/F12-line-movement.md @ d528d72", "P": {}, "S": {}}
    p = build_p(games, snap)
    tests = []
    for (market, horizon), (rows, why) in p.items():
        if len(rows) < 100:
            raise SystemExit(f"P {market}/{horizon}: only {len(rows)} games - check the read")
        est = estimate(rows, rng)
        est["excluded"] = why
        est["lead_from_h_p50"] = float(np.median([r["lead_from_h"] for r in rows]))
        est["lead_close_h_p50"] = float(np.median([r["lead_close_h"] for r in rows]))
        est["books_common_p50"] = float(np.median([r["books"] for r in rows]))
        res["P"][f"{market}/{horizon}"] = est
        tests.append((f"P {market} {horizon} slope", est["slope"]))
        if horizon == "open":
            for name, b in est["bands"].items():
                tests.append((f"P {market} open band {name}", b["mean_signed_e"]))
    s = build_s(c)
    s_tests = []
    for market, (rows, why, used) in s.items():
        if len(rows) < 100:
            raise SystemExit(f"S {market}: only {len(rows)} games - check the read")
        est = estimate(rows, rng)
        est["excluded"] = why
        est["providers_used"] = used
        res["S"][market] = est
        s_tests.append((f"S {market} slope", est["slope"]))
        for name, b in est["bands"].items():
            s_tests.append((f"S {market} band {name}", b["mean_signed_e"]))
    res["P_tests"] = len(tests)
    res["P_bonferroni_survivors"] = [n for n, t in tests if t.get("z") is not None
                                     and abs(t["z"]) > BONFERRONI_Z]
    res["P_nominal_excluding_zero"] = [n for n, t in tests if t.get("lo") is not None
                                       and (t["lo"] > 0 or t["hi"] < 0)]
    # S has its own family of 8: Bonferroni at alpha 0.05/8, |z| > 2.73.
    res["S_tests"] = len(s_tests)
    res["S_bonferroni_survivors"] = [n for n, t in s_tests if t.get("z") is not None
                                     and abs(t["z"]) > S_BONFERRONI_Z]
    res["S_nominal_excluding_zero"] = [n for n, t in s_tests if t.get("lo") is not None
                                       and (t["lo"] > 0 or t["hi"] < 0)]
    with open(a.out, "w") as fh:
        json.dump(res, fh, indent=1, default=str)

    def fmt(t):
        if t.get("lo") is None:
            return f"{t['est']:+.3f}  (n/a: {t.get('note')})"
        return (f"{t['est']:+.3f} [{t['lo']:+.3f}, {t['hi']:+.3f}] "
                f"z={t['z']:+.2f}{'' if not t.get('null') else ' vs ' + str(t['null'])}")
    for part in ("P", "S"):
        for k, v in res[part].items():
            print(f"{part} {k}: n={v['n']} clusters={v['clusters']} share D=0 {v['share_d_zero']:.3f} "
                  f"D p10/p50/p90 {v['d_dist']['p10']:+.2f}/{v['d_dist']['p50']:+.2f}/{v['d_dist']['p90']:+.2f} "
                  f"mean e {v['mean_e_all']:+.3f}  excluded {v['excluded']}")
            print(f"   slope {fmt(v['slope'])}")
            print(f"   D=0: n={v['d_zero']['n']} mean e {v['d_zero']['mean_e']}")
            for name, b in v["bands"].items():
                print(f"   |D| {name:<11} n={b['n']:<5} signed e {fmt(b['mean_signed_e'])}   "
                      f"toward-side covers {fmt(b['toward_side_covers_close'])}")
    print(f"P tests {res['P_tests']}; Bonferroni survivors {res['P_bonferroni_survivors']}; "
          f"nominal {res['P_nominal_excluding_zero']}")
    print(f"S tests {res['S_tests']}; Bonferroni survivors {res['S_bonferroni_survivors']}; "
          f"nominal {res['S_nominal_excluding_zero']}")


if __name__ == "__main__":
    main()
