"""f-31 run 4 - c-33 "soft markets" attacked from its ARCHIVED EXTRACT, with its own code.

    python research/f26_reliability/attack_c33.py --src <c-33 worktree> --extract <dir holding the three
        c33_*.parquet files> --recorded <the unit's result.json> [--out result.json]

CLAIM (US books via The Odds API `us` region, no Pinnacle, NFL; two panels never pooled: L = live 2026
weeks 3-4, 32 games, 8 two-sided prop keys; H = 2023-2025 closes, 855 games, 5 prop keys):
  book disagreement is highest on rush yards 2.81pp [2.15, 3.60] and receiving yards 2.57pp (L); receiving
  yards 2.31 vs receptions 1.80 and rush attempts 1.85 (H); props as a class sit above game lines on hold,
  disagreement and last-24h movement; hold vs disagreement rank correlation -0.48 over 8 keys.

RUN: 1 reproduce (research.soft_markets.run_panel on the archived extract, `now` = the unit's run_ts);
  2 blocks (duplication_through on soft_markets.game_vec + boot_means, the functions every published
  interval came from; alt blocks by week and kickoff day; leave-one-book-out); 3 the slope refit inside each
  draw, plus the slope at its IQR ends, the unit's own empirical slope and a per-claim slope; 4 counts,
  registered-vs-post-hoc ranking, Bonferroni on the class contrasts, exact permutation p of the Spearman;
  5 MDE on the between-key differences; 6 timestamp order of the two movement reads.
NOT RUN: the store (the live 2026 rows prune at 14 days; nothing here opens a database), the
  forecastability axis, the shortlist rule, any network call. No leakage scramble: there is no forecast.
"""
import argparse
import itertools
import json
import math
import os
import sys
import time

import numpy as np
import polars as pl

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import f26lib as F                                        # noqa: E402

PROPS = ["player_receptions", "player_reception_yds", "player_rush_attempts", "player_rush_yds",
         "player_pass_yds", "player_pass_attempts", "player_pass_tds", "player_tackles_assists"]
GAME = ["h2h", "spreads", "totals"]
RY, REY, REC, RA, PTD = ("player_rush_yds", "player_reception_yds", "player_receptions",
                         "player_rush_attempts", "player_pass_tds")
PUB = {"L.dis.rush_yds": (2.81, 2.15, 3.60), "L.dis.reception_yds": (2.57, 2.17, 3.10),
       "H.dis.reception_yds": (2.31, 2.22, 2.40), "H.dis.receptions": (1.80,), "H.dis.rush_attempts": (1.85,),
       "L.spearman.hold_dis": (-0.48,)}
PUB_CLASS = {"L.hold": ((6.24, 6.71), (4.17, 4.45, 4.59)), "H.hold": ((5.85, 6.71), (4.25, 4.68, 4.77)),
             "L.dis": ((1.12, 2.81), (0.77, 0.68, 0.63)), "H.dis": ((1.22, 2.31), (0.82, 0.63, 0.53)),
             "L.mv": ((1.48, 2.76), (1.37, 1.11, 0.84))}
s = lambda k: k.replace("player_", "")
P = lambda *a: print(*a, flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True)
    ap.add_argument("--extract", required=True)
    ap.add_argument("--recorded", required=True)
    ap.add_argument("--out")
    a = ap.parse_args()
    sys.path.insert(0, a.src)
    import research.soft_markets as sm
    if not os.path.abspath(sm.__file__).startswith(os.path.abspath(a.src)):
        raise SystemExit("research.soft_markets resolved outside --src: %s" % sm.__file__)
    for f in ("c33_live.parquet", "c33_hist.parquet", "c33_kick.parquet"):
        if not os.path.exists(os.path.join(a.extract, f)):
            raise SystemExit("extract file missing: %s (refusing: extract() would open a store)" % f)
    rec = json.load(open(a.recorded))
    now, res, t0 = rec["run_ts"], {}, time.time()
    pct = lambda d: (float(np.nanpercentile(d, 2.5)), float(np.nanpercentile(d, 97.5)))

    cap, cur = {}, [None]                                  # spy on the target's summarise: its per-game vectors
    orig = sm.summarise

    def spy(name, comps, games, direction, ranked):
        cap[cur[0]][name] = (comps, list(games))
        return orig(name, comps, games, direction, ranked)
    sm.summarise = spy
    L, H, K = sm.extract("", a.extract)
    P("c-33 attack | extract live %d rows, historical %d, kickoffs %d | unit run_ts %s UTC" % (
        L.height, H.height, K.height, time.strftime("%Y-%m-%d %H:%M", time.gmtime(now))))
    nrm, out, close = {}, {}, {}
    for pn, df, ranked in (("L", L, sm.RANKED_L), ("H", H, sm.RANKED_H)):
        cur[0], cap[pn] = pn, {}
        nrm[pn] = sm.normalise(df, K, pn)
        out[pn], close[pn], _ = sm.run_panel(pn, nrm[pn], ranked, now,
                                             ladder=out["L"]["info"]["kappa_ladder"] if pn == "H" else None,
                                             no_dis=("player_sacks",) if pn == "H" else ())

    # ---------------------------------------------------------------- 1 reproduce
    P("\n== 1 REPRODUCE (archived extract, target's run_panel, now = unit run_ts) ==")
    same = diff = 0
    where = {}
    for pn in ("L", "H"):
        for tbl, rows in out[pn].items():
            if isinstance(rows, dict) and tbl in rec[pn] and all(isinstance(v, dict) and "est" in v for v in rows.values()):
                for k, v in rows.items():
                    r = rec[pn][tbl].get(k) or {}
                    for f in ("est", "lo", "hi"):
                        x, y = v[f], r.get(f)
                        nan = lambda z: z is None or z != z
                        ok = (nan(x) and nan(y)) or (not nan(x) and not nan(y) and abs(x - y) < 1e-12)
                        same, diff = same + ok, diff + (not ok)
                        if not ok:
                            where.setdefault("%s.%s" % (pn, tbl), []).append("%s.%s %r vs %r" % (s(k), f, x, y))
    P("  every est/lo/hi in the unit's result.json vs this re-run: %d identical, %d different" % (same, diff))
    for t_, w_ in where.items():
        P("    differs: %s (%d) e.g. %s" % (t_, len(w_), w_[0]))
    rep = []
    for name, pn, k in (("L.dis.rush_yds", "L", RY), ("L.dis.reception_yds", "L", REY), ("H.dis.reception_yds", "H", REY),
                        ("H.dis.receptions", "H", REC), ("H.dis.rush_attempts", "H", RA)):
        r = out[pn]["dis"][k]
        for f, pub in zip(("est", "lo", "hi"), PUB[name]):
            rep.append(F.reproduce("%s.%s" % (name, f), r[f] * 100, pub, 2))
    rep.append(F.reproduce("L.spearman.hold_dis", out["L"]["proxy_spearman"]["hold_dis"], -0.48, 2))
    for name, (pr, gl) in PUB_CLASS.items():
        pn, tb = name.split(".")
        tbl = out[pn]["hold_bench" if tb == "hold" else tb]
        pk = [k for k in (PROPS if pn == "L" else list(sm.RANKED_H)) if k in tbl and not (tb == "dis" and k == "player_sacks")]
        v = [tbl[k]["est"] * 100 for k in pk]
        rep += [F.reproduce(name + ".props_min", min(v), pr[0], 2), F.reproduce(name + ".props_max", max(v), pr[1], 2)]
        rep += [F.reproduce("%s.%s" % (name, g), tbl[g]["est"] * 100, x, 2) for g, x in zip(GAME, gl)]
    for r in rep:
        P("  %-28s published %7.2f  measured %8.4f  %s" % (r["name"], r["published"], r["measured"],
                                                          "reproduces" if r["reproduces"] else "DOES NOT REPRODUCE"))
    res["reproduce"] = {"json_same": same, "json_diff": diff, "rows": rep}
    games = {pn: sorted(close[pn]["event_id"].unique().to_list()) for pn in ("L", "H")}
    kick = dict(K.iter_rows())
    kl = np.array([kick[g] for g in games["L"]])
    d0 = int((kl.min() - 43200) // 86400)                  # NFL week = Tue 12:00Z to Tue 12:00Z (MNF kicks 00:15Z Tuesday)
    tue = (d0 - ((d0 + 3) % 7 - 1) % 7) * 86400 + 43200
    week = ((kl - tue) // (7 * 86400)).astype(int)
    day = [time.strftime("%m-%d", time.gmtime(t - 6 * 3600)) for t in kl]
    P("  games counted here from close event_ids: L %d (kickoffs %s -> %s UTC; %d NFL weeks, games per week %s; %d kickoff days)"
      "  H %d" % (len(games["L"]), time.strftime("%m-%d", time.gmtime(kl.min())), time.strftime("%m-%d", time.gmtime(kl.max())),
                  len(set(week)), [int(x) for x in np.unique(week, return_counts=True)[1]], len(set(day)), len(games["H"])))
    res["games"] = {"L": len(games["L"]), "H": len(games["H"]), "L_weeks": len(set(week)), "L_days": len(set(day))}

    # claim-level rows, built with the target's disagreement()
    kap = {pn: out[pn]["info"]["kappa"] for pn in ("L", "H")}
    cl, bp = {}, {}
    for pn in ("L", "H"):
        c, b = sm.disagreement(close[pn], kap[pn])
        withk = [k for k in out[pn]["keys"] if kap[pn].get(k)]
        cl[pn] = c.with_columns(v=pl.when(pl.col("key").is_in(withk)).then(pl.col("combined")).otherwise(pl.col("same_line")))
        bp[pn] = b
    rows_of = lambda pn, keys: [dict(event_id=e, key=k, v=v) for e, k, v in
                                cl[pn].filter(pl.col("key").is_in(keys)).select("event_id", "key", "v").iter_rows()
                                if v is not None and math.isfinite(v)]

    # ---------------------------------------------------------------- 2 blocks
    P("\n== 2 BLOCKS (through soft_markets.game_vec + soft_markets.boot_means) ==")

    def width_key(rows):
        df = pl.DataFrame(rows)
        g = sorted(df["event_id"].unique().to_list())
        _, d = sm.boot_means([sm.game_vec(df, "v", g, rows[0]["key"])], len(g))
        return float(np.percentile(d, 97.5) - np.percentile(d, 2.5))

    def width_contrast(ka, kb):
        def w(rows):
            df = pl.DataFrame(rows)
            g = sorted(df["event_id"].unique().to_list())
            _, da = sm.boot_means([sm.game_vec(df, "v", g, k) for k in ka], len(g))
            _, db = sm.boot_means([sm.game_vec(df, "v", g, k) for k in kb], len(g))
            return float(np.percentile(da - db, 97.5) - np.percentile(da - db, 2.5))
        return w
    FN = "research.soft_markets.game_vec + boot_means"
    thr = {}
    for name, pn, w, keys in (("L rush_yds disagreement", "L", width_key, [RY]),
                              ("L disagreement props - game lines", "L", width_contrast(PROPS, GAME), PROPS + GAME),
                              ("H reception_yds - receptions", "H", width_contrast([REY], [REC]), [REY, REC])):
        rw = rows_of(pn, keys)
        units = len({r["event_id"] for r in rw})
        thr[name] = F.duplication_through(w, rw, "event_id", fn_name=FN, units=units)
        P("  %-34s %s | %.1f rows per game" % (name, F.through_line(thr[name]), len(rw) / units))
    P("  require_through: %s" % F.require_through(list(thr), thr))
    res["through"] = thr
    bpk = bp["L"].filter(pl.col("key") == RY)
    P("  under the 32 rush_yds game values: %d claims (player-games), %d book pairs; the game value is the mean "
      "over claims, so rows == units at the level the bootstrap sees" % (len(rows_of("L", [RY])), bpk.height))

    V = {pn: {k: np.asarray(c[0], float) for k, c in cap[pn]["dis"][0].items()} for pn in ("L", "H")}
    nL = len(games["L"])
    st_ry = lambda idx: float(np.nanmean(V["L"][RY][idx]))
    st_cls = lambda idx: float(np.mean([np.nanmean(V["L"][k][idx]) for k in PROPS]) - np.mean([np.nanmean(V["L"][k][idx]) for k in GAME]))
    alt = {}
    for name, st in (("rush_yds disagreement", st_ry), ("disagreement props - game lines", st_cls)):
        alt[name] = F.alt_blocks(st, nL, {"game": games["L"], "kickoff_day": day, "week": list(week)})
        for b, r in alt[name].items():
            P("  ALT %-32s by %-11s (%2d blocks) %s" % (name, b, r["n_blocks"], F.fmt({**r, "est": r["est"] * 100, "lo": r["lo"] * 100,
                                                                                    "hi": r["hi"] * 100, "se": r["se"] * 100}, 2)))
    res["alt_blocks"] = alt
    for w in sorted(set(week)):
        m = week == w
        P("    week block %d: %2d games | rush_yds %.2f  reception_yds %.2f  receptions %.2f  pass_tds %.2f  totals %.2f" % (
            w, m.sum(), *[np.nanmean(V["L"][k][m]) * 100 for k in (RY, REY, REC, PTD, "totals")]))
    P("  leave one book out (target's disagreement(), slope fixed), key means pp and rush_yds / reception_yds rank of 8:")
    lobo = {}
    for b in sorted(close["L"].filter(pl.col("key") == RY)["book"].unique().to_list()):
        c, _ = sm.disagreement(close["L"].filter(pl.col("book") != b), kap["L"])
        c = c.with_columns(v=pl.when(pl.col("key").is_in([k for k in PROPS if kap["L"].get(k)])).then(pl.col("combined")).otherwise(pl.col("same_line")))
        e = {k: float(np.nanmean(sm.game_vec(c, "v", games["L"], k))) * 100 for k in PROPS}
        rk = dict(zip(PROPS, sm.ranks_desc(np.array([e[k] for k in PROPS]))))
        lobo[b] = {"est": e, "rank_ry": rk[RY], "rank_rey": rk[REY]}
        P("    without %-12s rush_yds %.2f (r%.0f)  reception_yds %.2f (r%.0f)  receptions %.2f  pass_tds %.2f" % (
            b, e[RY], rk[RY], e[REY], rk[REY], e[REC], e[PTD]))
    res["leave_one_book_out"] = lobo

    # ---------------------------------------------------------------- 3 the slope
    P("\n== 3 THE SLOPE'S OMITTED UNCERTAINTY ==")
    gix = {pn: {g: i for i, g in enumerate(games[pn])} for pn in ("L", "H")}

    def prep(pn, key):
        d = bp[pn].filter(pl.col("key") == key)
        cid = {}
        c = np.array([cid.setdefault(es, len(cid)) for es in zip(d["event_id"].to_list(), d["subj"].to_list())], int)
        g = np.array([gix[pn][e] for e in d["event_id"].to_list()])
        pa, pb = d["pline"].to_numpy(), d["pline_j"].to_numpy()
        lr = np.where((pa > 0) & (pb > 0), np.log(np.where(pa > 0, pa, 1)) - np.log(np.where(pb > 0, pb, 1)), np.nan)
        cg = np.zeros(c.max() + 1, int)
        cg[c] = g
        return {"dp": d["dp"].to_numpy(), "lr": lr, "c": c, "cg": cg, "n": len(games[pn]), "same": d["same"].to_numpy(),
                "claims": [(e, sj, i) for (e, sj), i in cid.items()]}

    def gvec(pr, kappa):
        """per-game mean over claims of the claim mean over book pairs of |dp + kappa * ln ratio| (kappa: scalar or per pair)"""
        comb = np.abs(pr["dp"] + kappa * pr["lr"])
        ok = np.isfinite(comb)
        cs = np.bincount(pr["c"][ok], comb[ok], len(pr["cg"]))
        cn = np.bincount(pr["c"][ok], minlength=len(pr["cg"]))
        cm, has = cs / np.maximum(cn, 1), cn > 0
        gs, gn = np.bincount(pr["cg"][has], cm[has], pr["n"]), np.bincount(pr["cg"][has], minlength=pr["n"])
        return np.where(gn > 0, gs / np.maximum(gn, 1), np.nan)

    # the per-claim slopes the key slope is a median of: the target's ladder_kappa body, kept per claim
    n_, c_ = nrm["L"].filter(pl.col("kick") < now), close["L"]
    g4 = ["book", "event_id", "key", "subj"]
    altl = n_.filter(pl.col("key").str.ends_with("_alternate") & (pl.col("side") == "A") & (pl.col("pline") > 0)).with_columns(
        key=pl.col("key").str.replace("_alternate", "")).select("book", "event_id", "key", "subj", "ts", aline="pline", araw="raw")
    j = altl.join(c_.select(g4 + ["ts", "pline", "hold"]), on=g4 + ["ts"]).with_columns(ap=pl.col("araw") / (1.0 + pl.col("hold")))
    lo_ = j.filter(pl.col("aline") < pl.col("pline")).sort(g4 + ["aline"]).unique(subset=g4, keep="last", maintain_order=True)
    hi_ = j.filter(pl.col("aline") > pl.col("pline")).sort(g4 + ["aline"]).unique(subset=g4, keep="first", maintain_order=True)
    lad = lo_.select(g4 + ["aline", "ap"]).join(hi_.select(g4 + ["aline", "ap"]), on=g4, suffix="_hi").with_columns(
        k=-(pl.col("ap_hi") - pl.col("ap")) / (pl.col("aline_hi").log() - pl.col("aline").log())).filter(pl.col("k").is_finite())
    bp0 = sm.book_pairs(c_)
    src = out["L"]["info"]["kappa_source"]
    slope = {}                                             # key -> (per-observation slope values, their game index)
    for k in PROPS:
        if src[k] == "ladder":
            d = lad.filter(pl.col("key") == k)
            vals = d["k"].to_numpy()
        elif src[k] == "cross-book":
            d = bp0.filter((pl.col("key") == k) & ~pl.col("same") & (pl.col("pline") > 0) & (pl.col("pline_j") > 0))
            vals = -(d["dp"].to_numpy() / (d["pline"].log() - d["pline_j"].log()).to_numpy())
        else:
            continue
        slope[k] = (vals, np.array([gix["L"][e] for e in d["event_id"].to_list()]))
        if abs(float(np.median(vals)) - kap["L"][k]) > 1e-12:
            raise SystemExit("my per-claim slopes do not reproduce the target's kappa for %s" % k)
    P("  per-observation slopes reproduce the target's kappa on %d keys: %s" % (
        len(slope), ", ".join("%s %.3f (%s, n=%d)" % (s(k), kap["L"][k], src[k], len(slope[k][0])) for k in slope)))
    prL = {k: prep("L", k) for k in PROPS}
    for k in slope:
        if abs(np.nanmean(gvec(prL[k], kap["L"][k])) - out["L"]["dis"][k]["est"]) > 1e-12:
            raise SystemExit("my re-implementation of the combined figure does not reproduce %s" % k)
    idx = np.random.default_rng(sm.SEED).integers(0, nL, size=(sm.DRAWS, nL))   # the target's own game draws
    fixed, refit, kd, bad = {}, {}, {}, {}
    Vsl = {k: np.asarray(c[0], float) for k, c in cap["L"]["dis_sl"][0].items()}
    for k in PROPS:
        if k not in slope:
            fixed[k] = refit[k] = np.nanmean(V["L"][k][idx], axis=1)
            continue
        v0 = gvec(prL[k], kap["L"][k])
        fixed[k] = np.nanmean(v0[idx], axis=1)
        vals, vg = slope[k]
        byg = [vals[vg == g] for g in range(nL)]
        kd[k], refit[k] = np.empty(sm.DRAWS), np.empty(sm.DRAWS)
        for d in range(sm.DRAWS):
            pool = np.concatenate([byg[g] for g in idx[d]])
            kk = float(np.median(pool)) if len(pool) >= sm.KAPPA_MIN_PAIRS else float("nan")
            kd[k][d] = kk
            if kk > 0:
                refit[k][d] = np.nanmean(gvec(prL[k], kk)[idx[d]])
            else:                                          # the target's own rule: no usable slope -> the same-line figure
                bad[k] = bad.get(k, 0) + 1
                refit[k][d] = np.nanmean(Vsl[k][idx[d]])
    P("  draws with no usable refitted slope (< %d pairs or not positive; the same-line figure is used, as the target does): %s" % (
        sm.KAPPA_MIN_PAIRS, {s(k): v for k, v in bad.items()} or 0))
    sl = {}
    for k in (RY, REY, REC, "player_pass_yds", RA, PTD, "player_pass_attempts"):
        f, r = pct(fixed[k]), pct(refit[k])
        sl[k] = {"est": out["L"]["dis"][k]["est"], "fixed": f, "refit": r, "se_fixed": float(fixed[k].std()), "se_refit": float(refit[k].std()),
                 "kappa_ci": pct(kd[k]), "kappa_se": float(np.nanstd(kd[k]))}
        P("  %-15s slope %.3f, refit 95%% [%.3f, %.3f] | interval, slope fixed [%.2f, %.2f] -> refit in the draw [%.2f, %.2f]"
          "  width x%.3f" % (s(k), kap["L"][k], *sl[k]["kappa_ci"], f[0] * 100, f[1] * 100, r[0] * 100, r[1] * 100, (r[1] - r[0]) / (f[1] - f[0])))

    def order(D, label):
        M = np.vstack([D[k] for k in PROPS])
        R = np.vstack([sm.ranks_desc(M[:, i]) for i in range(M.shape[1])])
        i = {k: PROPS.index(k) for k in PROPS}
        o = {"both_yards_top2": float(np.mean((R[:, i[RY]] <= 2) & (R[:, i[REY]] <= 2))),
             "rush_yds_first": float(np.mean(R[:, i[RY]] == 1)), "rush_yds_gt_reception_yds": float(np.mean(M[i[RY]] > M[i[REY]])),
             "rush_yds_gt_pass_tds": float(np.mean(M[i[RY]] > M[i[PTD]])), "rush_yds_gt_receptions": float(np.mean(M[i[RY]] > M[i[REC]])),
             "reception_yds_gt_pass_tds": float(np.mean(M[i[REY]] > M[i[PTD]])), "reception_yds_gt_receptions": float(np.mean(M[i[REY]] > M[i[REC]]))}
        P("  ORDER %-22s " % label + "  ".join("%s %.3f" % kv for kv in o.items()))
        return o
    res["slope"] = {"keys": sl, "order_fixed": order(fixed, "slope fixed (the unit)"), "order_refit": order(refit, "slope refit per draw")}

    P("  the slope as a SPECIFICATION (point estimates pp, rank of 8 in brackets; nothing here is resampled):")
    emp = {k: (rec["forecastability"].get(k) or {}).get("kappa_empirical") for k in PROPS}
    q = out["L"]["info"]["kappa_ladder"]
    own = {}
    for k in (RY, REY, REC, "player_pass_yds"):            # each claim's own ladder slope (median over its books) where it has one
        m = {(e, sj): v for e, sj, v in lad.filter(pl.col("key") == k).group_by("event_id", "subj").agg(pl.col("k").median()).iter_rows()}
        ck = np.full(len(prL[k]["cg"]), kap["L"][k])
        for e, sj, c in prL[k]["claims"]:
            if (e, sj) in m and m[(e, sj)] > 0:
                ck[c] = m[(e, sj)]
        own[k] = ck[prL[k]["c"]]
    specs = {"registered (cross-book)": {k: out["L"]["info"]["kappa_cross_book"][k] for k in PROPS},
             "addendum (ladder median)": dict(kap["L"]),
             "ladder q25": {k: (q[k]["q25"] if src[k] == "ladder" else kap["L"][k]) for k in PROPS},
             "ladder q75": {k: (q[k]["q75"] if src[k] == "ladder" else kap["L"][k]) for k in PROPS},
             "adverse: yards q25, others q75": {k: (q[k]["q25"] if k in (RY, REY) else q[k]["q75"] if src[k] == "ladder" else kap["L"][k]) for k in PROPS},
             "unit's empirical slope": {k: (emp[k] if src[k] == "ladder" else kap["L"][k]) for k in PROPS},
             "each claim's own ladder slope": {k: (own[k] if k in own else kap["L"][k]) for k in PROPS}}
    spec_out = {}
    for label, ks in specs.items():
        if label.startswith("registered"):                 # the target's own registered column
            e = {k: out["L"]["dis_registered_kappa"][k]["est"] * 100 for k in PROPS}
        else:
            e = {k: float(np.nanmean(gvec(prL[k], ks[k]) if k in slope else V["L"][k])) * 100 for k in PROPS}
        rk = dict(zip(PROPS, sm.ranks_desc(np.array([e[k] for k in PROPS]))))
        spec_out[label] = {"est": e, "rank": rk}
        P("    %-31s " % label + "  ".join("%s %.2f[%.0f]" % (s(k)[:12], e[k], rk[k]) for k in PROPS))
    res["slope"]["specifications"] = spec_out
    mvr = {k: (out["L"]["mv_registered_kappa"][k]["est"] * 100, out["L"]["mv_registered_kappa"][k].get("rank"),
               out["L"]["mv"][k]["est"] * 100, out["L"]["mv"][k].get("rank")) for k in PROPS}
    P("    movement, registered -> addendum (rank 1 = LEAST movement): " + "  ".join("%s %.2f[%.0f]->%.2f[%.0f]" % ((s(k)[:12],) + mvr[k]) for k in PROPS))
    res["slope"]["movement_registered_vs_addendum"] = mvr

    def breakeven(pr, target, lo=0.0, hi=1.5):
        f = lambda x: float(np.nanmean(gvec(pr, x))) - target
        if f(lo) * f(hi) > 0:
            return None
        for _ in range(40):
            mid = (lo + hi) / 2
            lo, hi = (mid, hi) if f(lo) * f(mid) > 0 else (lo, mid)
        return (lo + hi) / 2
    be = {"L rush_yds = pass_tds (1.85)": breakeven(prL[RY], out["L"]["dis"][PTD]["est"]),
          "L rush_yds = receptions (1.73)": breakeven(prL[RY], out["L"]["dis"][REC]["est"]),
          "L reception_yds = pass_tds (1.85)": breakeven(prL[REY], out["L"]["dis"][PTD]["est"]),
          "L reception_yds = receptions (1.73)": breakeven(prL[REY], out["L"]["dis"][REC]["est"])}
    P("  BREAKEVEN slope at which the yards key falls to the named key (ladder median, [q25, q75] beside it):")
    for nm, v in be.items():
        k = RY if "rush_yds" in nm.split("=")[0] else REY
        P("    %-36s %s   (used %.3f [%.3f, %.3f])" % (nm, "none in [0, 1.5]" if v is None else "%.3f" % v, kap["L"][k], q[k]["q25"], q[k]["q75"]))
    res["slope"]["breakeven"] = be

    # panel H: both yards-vs-count differences, slope fixed and with the 2026 slope's own draws carried in
    nH = len(games["H"])
    idxH = np.random.default_rng(sm.SEED).integers(0, nH, size=(sm.DRAWS, nH))
    prH = {k: prep("H", k) for k in (REY, REC)}
    grid = {k: np.linspace(np.nanmin(kd[k]), np.nanmax(kd[k]), 9) for k in (REY, REC)}
    G = {k: np.vstack([gvec(prH[k], x) for x in grid[k]]) for k in (REY, REC)}
    hf = {k: np.nanmean(V["H"][k][idxH], axis=1) for k in (REY, REC, RA)}
    hr = {}
    for k in (REY, REC):                                   # |dp + kappa*lr| is piecewise linear in kappa: interpolate on a 9-point grid
        jx = np.clip(np.searchsorted(grid[k], kd[k]) - 1, 0, 7)
        w = (kd[k] - grid[k][jx]) / (grid[k][jx + 1] - grid[k][jx])
        hr[k] = np.array([np.nanmean(((1 - w[d]) * G[k][jx[d]] + w[d] * G[k][jx[d] + 1])[idxH[d]]) for d in range(sm.DRAWS)])
    hr[RA] = hf[RA]
    P("  PANEL H (%d games), 2026 ladder slope borrowed: reception_yds %.2f, slope fixed [%.2f, %.2f] -> 2026 slope draws carried [%.2f, %.2f]"
      "  width x%.2f" % (nH, np.nanmean(V["H"][REY]) * 100,
                         *[x * 100 for x in pct(hf[REY])], *[x * 100 for x in pct(hr[REY])],
                         (pct(hr[REY])[1] - pct(hr[REY])[0]) / (pct(hf[REY])[1] - pct(hf[REY])[0])))
    bh = {"H reception_yds = receptions (1.80)": breakeven(prH[REY], out["H"]["dis"][REC]["est"]),
          "H reception_yds = rush_attempts (1.85)": breakeven(prH[REY], out["H"]["dis"][RA]["est"])}
    for nm, v in bh.items():
        P("    BREAKEVEN %-38s %s   (used %.3f, borrowed from 2026; 2026 ladder [q25, q75] [%.3f, %.3f])" % (
            nm, "none" if v is None else "%.3f" % v, kap["H"][REY], q[REY]["q25"], q[REY]["q75"]))
    res["slope"]["breakeven_H"] = bh
    for lab, ks in (("ladder q25 (yards) / q75 (receptions)", {REY: q[REY]["q25"], REC: q[REC]["q75"]}),
                    ("unit's empirical slopes", {REY: emp[REY], REC: emp[REC]})):
        P("    H at %-38s reception_yds %.2f  receptions %.2f  (rush_attempts %.2f, own cross-book slope)" % (
            lab, np.nanmean(gvec(prH[REY], ks[REY])) * 100, np.nanmean(gvec(prH[REC], ks[REC])) * 100, np.nanmean(V["H"][RA]) * 100))

    # ---------------------------------------------------------------- 4 specifications
    P("\n== 4 SPECIFICATIONS ==")
    n_int = sum(1 for pn in ("L", "H") for t, rows in rec[pn].items() if isinstance(rows, dict)
                for v in rows.values() if isinstance(v, dict) and v.get("est") is not None and "lo" in v)
    n_prim = sum(1 for pn, tabs in (("L", ("hold_bench", "dis", "mv")), ("H", ("hold_bench", "dis", "mv")))
                 for t in tabs for v in (rec[pn].get(t) or {}).values() if v.get("est") is not None)
    n_fc = 2 * len(rec["forecastability"])
    try:
        F.registered_count(rec, "c-33")
    except SystemExit as e:
        P("  registered_count(): REFUSED - %s" % e)
    P("  counted from the unit's result.json: %d value intervals in panel tables (%d in the three primary tables), "
      "%d forecastability intervals, 2 anytime-TD. Registered contrast intervals between keys or classes: 0" % (n_int, n_prim, n_fc))
    res["counts"] = {"panel_intervals": n_int, "primary": n_prim, "forecastability": n_fc}

    def keydraws(pn, tab, k):
        comps, g = cap[pn][tab]
        return sm.boot_means(comps[k], len(g))
    cls = {}
    for name in PUB_CLASS:
        pn, tb = name.split(".")
        pk = [k for k in (PROPS if pn == "L" else list(sm.RANKED_H)) if k in cap[pn][tb][0] and not (tb == "dis" and k == "player_sacks")]
        pe, pd = zip(*[keydraws(pn, tb, k) for k in pk])
        ge, gd = zip(*[keydraws(pn, tb, k) for k in GAME])
        est, d = float(np.mean(pe) - np.mean(ge)), np.mean(pd, axis=0) - np.mean(gd, axis=0)
        n_nan = int((~np.isfinite(d)).sum())               # a draw with no game for one bench book: the target drops it too (pct)
        r = F.summ(est, d[np.isfinite(d)])
        m = F.multiplicity(est, r["se"], (len(PUB_CLASS), n_prim, n_int), n_blocks=len(games[pn]))
        weak, wk_ = min((float(np.mean((a > b)[np.isfinite(a) & np.isfinite(b)])), "%s vs %s" % (s(x), y))
                        for a, x in zip(pd, pk) for b, y in zip(gd, GAME))
        cls[name] = {**r, "mult": m, "min_pairwise_share": weak, "weakest_pair": wk_, "prop_keys": len(pk), "nan_draws": n_nan}
        P("  CLASS %-7s props(%d keys) - game lines(3): %s pp | z %.1f | Bonferroni k=%s: %s | weakest pair (%s) holds in %.3f of draws%s" % (
            name, len(pk), F.fmt({**r, "est": est * 100, "lo": r["lo"] * 100, "hi": r["hi"] * 100, "se": r["se"] * 100}, 2), m["z"],
            list(m["bonferroni"]), [v["survives_0.05"] for v in m["bonferroni"].values()], wk_, weak, (" | %d non-finite draws dropped" % n_nan) if n_nan else ""))
    res["class_contrasts"] = cls

    h = [out["L"]["hold_bench"][k]["est"] for k in PROPS]
    dd = [out["L"]["dis"][k]["est"] for k in PROPS]
    rh, rd = sm.ranks_desc(np.array(h)), sm.ranks_desc(np.array(dd))
    rho = lambda x, y: float(np.corrcoef(x, y)[0, 1])
    obs = rho(rh, rd)
    perm = np.array([rho(rh, np.array(p_)) for p_ in itertools.permutations(rd)])
    p2 = float(np.mean(np.abs(perm) >= abs(obs) - 1e-12))
    ap_ = np.abs(perm)
    crit = float(min(x for x in np.unique(np.round(ap_, 10)) if np.mean(ap_ >= x - 1e-12) < 0.05))
    hd = np.vstack([keydraws("L", "hold", k)[1] for k in PROPS])
    bs = np.array([rho(sm.ranks_desc(hd[:, i]), sm.ranks_desc(fixed_i)) for i, fixed_i in enumerate(np.vstack([fixed[k] for k in PROPS]).T)])
    reg_d = [out["L"]["dis_registered_kappa"][k]["est"] for k in PROPS]
    rho_reg = rho(rh, sm.ranks_desc(np.array(reg_d)))
    P("  SPEARMAN hold vs disagreement, 8 keys: %.4f | exact permutation p (40,320 orders, two-sided) %.4f | |rho| needed for p<0.05 at n=8: %.3f"
      % (obs, p2, crit))
    P("    over the unit's 2,000 game draws: rho 95%% [%.2f, %.2f], share of draws < 0: %.3f | under the REGISTERED slope the same rho is %+.2f" % (
        *pct(bs), float(np.mean(bs < 0)), rho_reg))
    res["spearman"] = {"rho": obs, "perm_p": p2, "crit": crit, "boot": pct(bs), "share_neg": float(np.mean(bs < 0)), "rho_registered": rho_reg}

    # ---------------------------------------------------------------- 5 MDE
    P("\n== 5 MDE on the between-key differences (the unit stated no MDE and no difference interval) ==")
    mde = {}
    for name, e, df_, dr_ in (
            ("L rush_yds - receptions", out["L"]["dis"][RY]["est"] - out["L"]["dis"][REC]["est"], fixed[RY] - fixed[REC], refit[RY] - refit[REC]),
            ("L rush_yds - pass_tds (next key)", out["L"]["dis"][RY]["est"] - out["L"]["dis"][PTD]["est"], fixed[RY] - fixed[PTD], refit[RY] - refit[PTD]),
            ("L reception_yds - receptions", out["L"]["dis"][REY]["est"] - out["L"]["dis"][REC]["est"], fixed[REY] - fixed[REC], refit[REY] - refit[REC]),
            ("L reception_yds - pass_tds", out["L"]["dis"][REY]["est"] - out["L"]["dis"][PTD]["est"], fixed[REY] - fixed[PTD], refit[REY] - refit[PTD]),
            ("H reception_yds - receptions", out["H"]["dis"][REY]["est"] - out["H"]["dis"][REC]["est"], hf[REY] - hf[REC], hr[REY] - hr[REC]),
            ("H reception_yds - rush_attempts", out["H"]["dis"][REY]["est"] - out["H"]["dis"][RA]["est"], hf[REY] - hf[RA], hr[REY] - hr[RA])):
        a_, b_ = F.summ(e, df_), F.summ(e, dr_)
        ma, mb = F.mde_ratio(e, a_["se"]), F.mde_ratio(e, b_["se"])
        mde[name] = {"fixed": a_, "refit": b_, "mde_fixed": ma, "mde_refit": mb}
        P("  %-33s %+.2fpp | slope fixed [%+.2f, %+.2f] MDE %.2f ratio %.2f %-5s | slope drawn [%+.2f, %+.2f] MDE %.2f ratio %.2f %s" % (
            name, e * 100, a_["lo"] * 100, a_["hi"] * 100, ma["mde"] * 100, ma["ratio"], ma["reading"],
            b_["lo"] * 100, b_["hi"] * 100, mb["mde"] * 100, mb["ratio"], mb["reading"]))
    P("  mde_claim: %s" % F.mde_claim(None, mde["L rush_yds - receptions"]["fixed"]["se"]))
    res["mde"] = mde

    # ---------------------------------------------------------------- 6 timestamps
    P("\n== 6 LEAKAGE: no forecast, so no scramble. What applies: are both movement reads before kickoff? ==")
    p, _ = sm.pairs(n_)
    lead = pl.col("kick") - pl.col("ts")
    fresh = (pl.col("ts") - pl.col("source_ts")) <= sm.FRESH_MAX_AGE
    op = sm.main_at(p, (lead >= sm.OPEN_MIN_LEAD) & (lead <= sm.OPEN_MAX_LEAD)).filter(fresh)
    ts = {}
    for nm, d in (("close", c_), ("open", op)):
        ld, sl_ = (d["kick"] - d["ts"]).to_numpy(), (d["kick"] - d["source_ts"]).to_numpy()
        ts[nm] = {"rows": d.height, "lead_min_s": float(ld.min()), "lead_med_h": float(np.median(ld) / 3600), "lead_max_h": float(ld.max() / 3600),
                  "source_after_kick": int((sl_ <= 0).sum()), "source_after_read": int((d["source_ts"] > d["ts"]).sum()),
                  "source_null": int(d["source_ts"].null_count())}
        P("  L %-5s %6d rows | read lead: min %.1fs median %.2fh max %.1fh | book last_update at/after kickoff: %d, after the read: %d, null: %d" % (
            nm, d.height, ts[nm]["lead_min_s"], ts[nm]["lead_med_h"], ts[nm]["lead_max_h"], ts[nm]["source_after_kick"],
            ts[nm]["source_after_read"], ts[nm]["source_null"]))
    hp = close["H"].filter(pl.col("key").is_in(list(sm.RANKED_H)))
    hl = (hp["kick"] - hp["ts"]).to_numpy() / 60
    ts["H_prop_close_lead_min"] = [float(np.percentile(hl, x)) for x in (0, 50, 95, 100)]
    P("  H prop 'close' read lead, minutes before nfl_games kickoff: min %.0f median %.0f p95 %.0f max %.0f; no source_ts exists on H rows" % tuple(ts["H_prop_close_lead_min"]))
    res["timestamps"] = ts
    P("\nelapsed %.0fs" % (time.time() - t0))
    if a.out:
        json.dump(res, open(a.out, "w"), indent=1, default=lambda o: o.item() if hasattr(o, "item") else str(o))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
