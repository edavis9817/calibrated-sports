"""f-26 target adapter: c-31's total record, as a-64 publishes it in game/nfl/record_total.json.

    CLAIM  ladder dBrier(model - baseline), settlement 2001-2025, 54,064 rungs in
           6,758 game blocks: league constant -0.0075 [-0.0090, -0.0060], season
           averages -0.0063 [-0.0078, -0.0048], nflverse closing total
           +0.0044 [+0.0032, +0.0057].

READ-ONLY. c-31's main() is driven from a detached worktree of
origin/c-31-team-total with its scoring calls replaced by a capture, so the
forecasts are c-31's own code end to end and the arithmetic is this file's:

    LOGGER_DB=<market_log.db> python research/f26_reliability/attack_c31.py \
        --src D:/temp/f26/c31src --recorded D:/temp/c31/result.json --out D:/temp/f26/c31.json \
        --served-json <record_total.json fetched from /data/game/nfl/record_total.json>

LOGGER_DB must be in the environment: c-31 reads it through `config`, and opens
market_log.db and analytics.db mode=ro itself.
"""
import argparse
import copy
import json
import os
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
PUBLISHED = {"league": (-0.0075, -0.0090, -0.0060), "season_avg": (-0.0063, -0.0078, -0.0048),
             "close": (0.0044, 0.0032, 0.0057)}
PUBLISHED_N = (54064, 6758)


class _Stop(Exception):
    pass


class _Anything:
    """Compares equal to every float: lets a SCRAMBLED run past c-31's identity refusal."""
    def __ne__(self, other):
        return False

    def __eq__(self, other):
        return True

    def __repr__(self):
        return "<identity check disabled for a scrambled run>"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True)
    ap.add_argument("--recorded", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--cutoffs", type=int, default=3)
    ap.add_argument("--served-json", default=None,
                    help="game/nfl/record_total.json AS SERVED (fetched over HTTP): its vs_close is attacked too")
    ap.add_argument("--served-only", action="store_true", help="run only the served-figure section")
    a = ap.parse_args()
    if not os.environ.get("LOGGER_DB"):
        raise SystemExit("LOGGER_DB is not set - c-31's config would fall back to a relative path")
    sys.path.insert(0, HERE)
    import f26lib as L
    sys.path.insert(0, os.path.abspath(a.src))
    from jobs import season_model as S
    from research import game_forecast as GF
    from research import game_total as GTR
    from research import ranking_calibration as rc
    if not os.path.abspath(GTR.__file__).startswith(os.path.abspath(a.src)):
        raise SystemExit("research.game_total resolved outside --src: %s" % GTR.__file__)
    orig = {"load": S.load, "pace": GTR.load_pace, "wx": GTR.load_weather, "design": GTR.design,
            "c28": GTR.C28_P1_BRIER, "compare": GF.compare, "mse": GF.mse_compare}
    out = lambda s="": print(s, flush=True)  # noqa: E731
    R = {"target": "c-31 (published by a-64)"}
    t0 = time.time()

    def capture(mut_games=None, mut_pace=None, mut_wx=None, no_wind=False):
        """-> ({baseline: rows}, games). Runs c-31's main() up to the last P-b comparison."""
        got, keep = {}, {}

        def load(con):
            games, grp, ver = orig["load"](con)
            keep["games"] = games if mut_games is None else mut_games(games)
            return keep["games"], grp, ver

        def cmp(name, rows, *_a, **_k):
            if name.startswith("P-b ladder: model vs "):
                got[name.rsplit(" ", 1)[1]] = rows
                if len(got) == 3:
                    raise _Stop()
            return {"diffs": {}}

        S.load = load
        GTR.load_pace = lambda by_id: (lambda r: ((mut_pace(r[0], by_id) if mut_pace else r[0]), r[1], r[2]))(orig["pace"](by_id))
        GTR.load_weather = lambda con, v: (lambda r: ((mut_wx(r[0]) if mut_wx else r[0]), r[1]))(orig["wx"](con, v))
        GTR.design = (lambda arm, raw, eam, wx: orig["design"]("NO_WIND" if arm == "FULL" else arm, raw, eam, wx)) \
            if no_wind else orig["design"]
        GTR.C28_P1_BRIER = _Anything() if mut_games is not None else orig["c28"]
        GF.compare, GF.mse_compare = cmp, (lambda *_a, **_k: {})
        try:
            saved = sys.stdout
            sys.stdout = open(os.devnull, "w")
            try:
                GTR.main(["--json-out", os.devnull, "--draws", "1"])
            finally:
                sys.stdout.close()
                sys.stdout = saved
        except _Stop:
            pass
        finally:
            S.load, GTR.load_pace, GTR.load_weather, GTR.design = orig["load"], orig["pace"], orig["wx"], orig["design"]
            GTR.C28_P1_BRIER, GF.compare, GF.mse_compare = orig["c28"], orig["compare"], orig["mse"]
        if len(got) != 3:
            raise SystemExit("captured %d of 3 P-b populations - c-31's main() did not reach them" % len(got))
        return got, keep["games"]

    def capture_s2(no_wind=False, draws=20):
        """c-31's OWN rows for 'S2 over the book close' - the comparison record_total.json serves as
        vs_close. main() runs for real up to that call (few draws), so nothing before it is stubbed."""
        got = {}

        def cmp(name, rows, *aa, **kk):
            if name.startswith("S2 over the book close"):
                got["rows"] = rows
                raise _Stop()
            return orig["compare"](name, rows, *aa, **kk)

        GTR.design = (lambda arm, raw, eam, wx: orig["design"]("NO_WIND" if arm == "FULL" else arm, raw, eam, wx)) \
            if no_wind else orig["design"]
        def mse(name, *aa, **kk):
            # with wind out, c-31's own "FULL vs NO_WIND" ablation is a zero difference: SE 0, MDE None,
            # and its print line raises. That diagnostic is not what is being captured.
            try:
                return orig["mse"](name, *aa, **kk)
            except TypeError:
                return {"dMSE": {"est": 0.0, "lo": 0.0, "hi": 0.0, "se": 0.0, "mde": None, "games": 0, "n": 0}}
        def rows_boot(fn, n, *aa, **kk):
            # c-31's D4 reads the wind coefficient by position; with wind out of the design it is not there
            try:
                return orig_rows(fn, n, *aa, **kk)
            except IndexError:
                return {"est": 0.0, "lo": 0.0, "hi": 0.0, "se": 0.0, "games": n, "n": n}
        orig_rows = GTR.boot_rows
        GF.compare, GF.mse_compare = cmp, mse
        if no_wind:
            GTR.boot_rows = rows_boot
        saved = sys.stdout
        try:
            sys.stdout = open(os.devnull, "w")
            try:
                GTR.main(["--json-out", os.devnull, "--draws", str(draws)])
            finally:
                sys.stdout.close()
                sys.stdout = saved
        except _Stop:
            pass
        finally:
            GTR.design, GF.compare, GF.mse_compare = orig["design"], orig["compare"], orig["mse"]
            GTR.boot_rows = orig_rows
        if "rows" not in got:
            raise SystemExit("c-31's main() never reached 'S2 over the book close'")
        return got["rows"]

    def served_section():
        """f-27: run 1 attacked the P-b ladder against the closing total (+0.0044, 54,064 rungs) and filed
        it under record_total.json. The file serves a DIFFERENT comparison there: S2, the over at the
        closing line against the de-vigged book price, one row a game. This attacks the figure served."""
        sv = json.load(open(a.served_json, encoding="utf-8"))["vs_close"]
        pub = (sv["d_brier"]["estimate"], sv["d_brier"]["interval"][0], sv["d_brier"]["interval"][1])
        out("\n== SERVED vs_close (record_total.json, as fetched): %+.4f [%+.4f, %+.4f] over %d games"
            % (pub[0], pub[1], pub[2], sv["games"]))
        rws = capture_s2()
        g2 = [r["game"] for r in rws]
        yy = np.array([r["y"] for r in rws])
        mm = np.array([r["m"] for r in rws])
        kk = np.array([r["k"] for r in rws])
        dd = (mm - yy) ** 2 - (kk - yy) ** 2
        st = lambda idx: float(dd[idx].mean())  # noqa: E731
        reg = L.summ(st(np.arange(len(rws))), L.block_boot(st, L.blocks_of(g2), draws=rc.BOOT, seed=rc.SEED))
        S = {"published": pub, "n": len(rws), "games": len(set(g2)), "redrawn": reg,
             "reproduce": [L.reproduce("rows", len(rws), sv["games"], 0)]
             + [L.reproduce("vs_close %s" % nm, got_, pv, 4) for nm, got_, pv in
                zip(("est", "lo", "hi"), (reg["est"], reg["lo"], reg["hi"]), pub)]}
        for r in S["reproduce"]:
            out("   %-14s measured %+.5f  served %+.4f  -> %s"
                % (r["name"], r["measured"], r["published"], "REPRODUCES" if r["reproduces"] else "DOES NOT REPRODUCE"))

        def w_many(rr):
            pp = rc.Pop("dup", rr, "m", "k")
            r = GF.boot_many(pp, lambda i: {"d": rc.brier(pp.m[i], pp.y[i]) - rc.brier(pp.k[i], pp.y[i])}, draws=400)["d"]
            return r["hi"] - r["lo"]
        S["through"] = L.duplication_through(w_many, rws, "game", fn_name="GF.boot_many", units=len(set(g2)))
        out("   " + L.through_line(S["through"]))
        byg = {g["game_id"]: g for g in GTR_games[0]} if GTR_games else {}
        if byg:
            S["alt_blocks"] = L.alt_blocks(st, len(rws), {
                "game": g2, "season-week": ["%d-%02d" % (byg[g]["season"], byg[g]["week"]) for g in g2],
                "season": [byg[g]["season"] for g in g2]}, seed=32)
            for nm, r in S["alt_blocks"].items():
                out("   %-12s (%4d blocks) %s" % (nm, r["n_blocks"], L.fmt(r)))
        try:
            nw = capture_s2(no_wind=True)
        except Exception as e:                                # noqa: BLE001
            S["no_wind"] = {"error": repr(e)[:300]}
            out("   no wind: NOT MEASURED - c-31's main() does not reach S2 with wind out of the design: %r" % (e,))
            return S
        if [r["game"] for r in nw] != g2:
            raise SystemExit("the NO_WIND S2 capture is not row-aligned with the registered one")
        mn = np.array([r["m"] for r in nw])
        dn = (mn - yy) ** 2 - (kk - yy) ** 2
        sn = lambda idx: float(dn[idx].mean())  # noqa: E731
        S["no_wind"] = L.summ(sn(np.arange(len(nw))), L.block_boot(sn, L.blocks_of(g2), seed=33))
        dw = dd - dn
        sw = lambda idx: float(dw[idx].mean())  # noqa: E731
        S["wind_worth"] = L.summ(sw(np.arange(len(nw))), L.block_boot(sw, L.blocks_of(g2), seed=34))
        out("   no wind: %s   | recorded wind is worth %s Brier on this comparison"
            % (L.fmt(S["no_wind"]), L.fmt(S["wind_worth"], 5)))
        k_reg = L.registered_count(json.load(open(a.recorded, encoding="utf-8")), "c-31")
        S["multiplicity"] = L.multiplicity(reg["est"], reg["se"], (k_reg,), n_blocks=len(set(g2)))
        S["mde"] = L.mde_ratio(reg["est"], reg["se"])
        out("   z %+.1f; Bonferroni over c-31's own %d intervals: p %.4f -> %s;  |est|/MDE %.2f (%s)"
            % (S["multiplicity"]["z"], k_reg, S["multiplicity"]["bonferroni"][k_reg]["p_adj"],
               "survives" if S["multiplicity"]["bonferroni"][k_reg]["survives_0.05"] else "DOES NOT SURVIVE",
               S["mde"]["ratio"], S["mde"]["reading"]))
        return S

    GTR_games = []
    if a.served_only:
        if not a.served_json:
            raise SystemExit("--served-only needs --served-json")
        _b, _games = capture()
        GTR_games.append(_games)
        R["served_vs_close"] = served_section()
        with open(a.out, "w", encoding="utf-8") as f:
            json.dump(R, f, indent=1, default=str)
        out("\nwrote %s (%.0fs)" % (a.out, time.time() - t0))
        return
    base, games = capture()
    GTR_games.append(games)
    by_id = {g["game_id"]: g for g in games}
    rows = base["league"]
    n = len(rows)
    gid = [r["game"] for r in rows]
    y = np.array([r["y"] for r in rows])
    m = np.array([r["m"] for r in rows])
    K = {b: np.array([r["k"] for r in base[b]]) for b in base}
    for b in base:
        if [r["game"] for r in base[b]] != gid or [r["line"] for r in base[b]] != [r["line"] for r in rows]:
            raise SystemExit("the three P-b populations are not row-aligned")
    D = {b: (m - y) ** 2 - (K[b] - y) ** 2 for b in K}
    n_games = len(set(gid))
    out("captured %d rungs in %d games in %.0fs (c-31's identity check on c-28's moneyline passed)"
        % (n, n_games, time.time() - t0))

    # ------------------------------------------------------------ 1 reproduce
    out("\n== 1. REPRODUCE (c-31's code, today's stores, own Brier arithmetic)")
    R["reproduce"] = [L.reproduce("rungs", n, PUBLISHED_N[0], 0), L.reproduce("games", n_games, PUBLISHED_N[1], 0)]
    gb = L.blocks_of(gid)
    reg = {}
    for b, d in D.items():
        st = lambda idx, d=d: float(d[idx].mean())  # noqa: E731
        # c-31's own draw: rc.Pop.boot's sequence (seed rc.SEED, blocks in sorted game order)
        reg[b] = L.summ(st(np.arange(n)), L.block_boot(st, gb, draws=rc.BOOT, seed=rc.SEED))
        for nm, got_, pub in zip(("est", "lo", "hi"), (reg[b]["est"], reg[b]["lo"], reg[b]["hi"]), PUBLISHED[b]):
            R["reproduce"].append(L.reproduce("dBrier vs %s %s" % (b, nm), got_, pub, 4))
    R["registered_interval_redrawn"] = reg
    for r in R["reproduce"]:
        out("   %-28s measured %+.5f  published %+.4f  -> %s"
            % (r["name"], r["measured"], r["published"], "REPRODUCES" if r["reproduces"] else "DOES NOT REPRODUCE"))
    rec = json.load(open(a.recorded, encoding="utf-8"))
    R["recorded_versions"] = rec.get("versions")

    # ------------------------------------------------------------ 2 blocks
    out("\n== 2. BLOCKS: 8 rungs settle off ONE final score; every arm THROUGH c-31's own bootstrap functions")
    R["iid_contrast"], R["alt_blocks"], R["through"] = {}, {}, {}

    def w_many(rws):          # GF.boot_many: what GF.compare (the P-b ladder intervals) calls
        pp = rc.Pop("dup", rws, "m", "k")
        r = GF.boot_many(pp, lambda i: {"dBrier": rc.brier(pp.m[i], pp.y[i]) - rc.brier(pp.k[i], pp.y[i])},
                         draws=300)["dBrier"]
        return r["hi"] - r["lo"]

    def w_pop(rws):           # rc.Pop.boot: c-31's per-season / per-rung call sites
        pp = rc.Pop("dup", rws, "m", "k")
        r = pp.boot(lambda i: rc.brier(pp.m[i], pp.y[i]) - rc.brier(pp.k[i], pp.y[i]), draws=300)
        return r["hi"] - r["lo"]

    def w_rows(rws):          # GTR.boot_rows: c-31's ROW bootstrap (D4 wind, S3 ROI) - "one row per game"
        mm = np.array([r["m"] for r in rws])
        kk = np.array([r["k"] for r in rws])
        yy = np.array([r["y"] for r in rws])
        r = GTR.boot_rows(lambda ix: float(((mm[ix] - yy[ix]) ** 2 - (kk[ix] - yy[ix]) ** 2).mean()), len(rws), 300)
        return r["hi"] - r["lo"]
    for b in base:
        for fn_name, fn in (("GF.boot_many", w_many), ("rc.Pop.boot", w_pop)):
            t = L.duplication_through(fn, base[b], "game", fn_name=fn_name, units=n_games)
            R["through"]["%s|%s" % (b, fn_name)] = t
            out("   %-10s %s" % (b, L.through_line(t)))
    R["through_verdicts"] = L.require_through(
        ["%s|%s" % (b, f) for b in ("league", "season_avg", "close") for f in ("GF.boot_many", "rc.Pop.boot")], R["through"])
    # the row bootstrap, shown on the LADDER rows it must never be given (8 rungs a game) and on one rung a game
    t = L.duplication_through(w_rows, base["close"], "game", fn_name="GTR.boot_rows", units=n_games)
    R["through"]["CONTROL ladder rows|GTR.boot_rows (not a call c-31 makes)"] = t
    out("   CONTROL    %s   <- the verdict had c-31 given its ladder to its row bootstrap" % L.through_line(t))
    first = {}
    for r in base["close"]:
        first.setdefault(r["game"], r)
    t = L.duplication_through(w_rows, list(first.values()), "game", fn_name="GTR.boot_rows", units=len(first))
    R["through"]["one rung per game|GTR.boot_rows"] = t
    out("   one/game   %s" % L.through_line(t))
    season = [by_id[g]["season"] for g in gid]
    week = ["%d-%02d" % (by_id[g]["season"], by_id[g]["week"]) for g in gid]
    for b, d in D.items():
        st = lambda idx, d=d: float(d[idx].mean())  # noqa: E731
        ic = L.iid_contrast(st, n, gid, seed=31, draws=1000)
        R["iid_contrast"][b] = ic
        out("   %-10s descriptive: %d rungs in %d games; an iid-rung interval would be x%.2f the width of the game-blocked one"
            % (b, ic["rows"], ic["blocks"], ic["iid_over_blocked_width"]))
        R["alt_blocks"][b] = L.alt_blocks(st, n, {"game": gid, "season-week": week, "season": season}, seed=32)
        for nm, r in R["alt_blocks"][b].items():
            out("   %-10s %-12s (%4d blocks) %s" % (b, nm, r["n_blocks"], L.fmt(r)))


    # ------------------------------------------------------------ 3 leakage
    out("\n== 3. LEAKAGE: scramble every input dated at or after a cutoff; earlier forecasts may not move")
    kicks = sorted({by_id[g]["kickoff_ts"] for g in set(gid)})
    cuts = [kicks[int(q * (len(kicks) - 1))] for q in np.linspace(0.25, 0.9, a.cutoffs)]
    key = [(r["game"], r["line"]) for r in rows]
    mref = dict(zip(key, m))
    kref = {b: dict(zip(key, K[b])) for b in K}
    R["leak"] = []
    for c in cuts:
        rng = np.random.default_rng(int(c) % 9973)

        def mg(gs, c=c, rng=rng):
            g2 = copy.deepcopy(gs)
            for g in g2:
                if g["kickoff_ts"] is not None and g["kickoff_ts"] >= c and g["home_score"] is not None:
                    g["home_score"], g["away_score"] = g["away_score"] + int(rng.integers(0, 9)), g["home_score"]
            return g2

        def mp(pace, bid, c=c, rng=rng):
            return {k: (v * float(rng.uniform(0.6, 1.4)) if bid[k[0]]["kickoff_ts"] is not None
                        and bid[k[0]]["kickoff_ts"] >= c else v) for k, v in pace.items()}

        def mw(wx, c=c):
            return {g: ((roof, (0.0 if wind is None or wind != wind else float(wind)) + 15.0)
                        if g in by_id and by_id[g]["kickoff_ts"] is not None and by_id[g]["kickoff_ts"] >= c
                        else (roof, wind)) for g, (roof, wind) in wx.items()}

        rec_c = {"cutoff_ts": c}
        for label, wxm in (("results+pace", None), ("results+pace+recorded wind", mw)):
            got, _g = capture(mut_games=mg, mut_pace=mp, mut_wx=wxm)
            before = at = mv_before = mv_at = mv_base = 0
            for bname, rws in got.items():
                for r in rws:
                    k = by_id[r["game"]]["kickoff_ts"]
                    kk = (r["game"], r["line"])
                    if k > c or kk not in mref:
                        continue
                    if abs(r["k"] - kref[bname][kk]) > 1e-12:
                        mv_base += 1
                    if bname != "league":
                        continue
                    moved = abs(r["m"] - mref[kk]) > 1e-12
                    if k < c:
                        before += 1
                        mv_before += moved
                    else:
                        at += 1
                        mv_at += moved
            rec_c[label] = {"rungs_before_cutoff": before, "moved_before": int(mv_before), "rungs_at_cutoff": at,
                            "moved_at_cutoff": int(mv_at), "baseline_rungs_moved": mv_base}
            out("   cutoff %d, %-27s: %6d earlier rungs, %d moved | %d rungs AT the cutoff, %d moved | baselines moved %d"
                % (c, label, before, mv_before, at, mv_at, mv_base))
        R["leak"].append(rec_c)
    R["leak_summary"] = {
        "results_and_pace_clean": all(x["results+pace"]["moved_before"] == 0 and x["results+pace"]["moved_at_cutoff"] == 0
                                      for x in R["leak"]),
        "own_game_wind_moves_forecast": any(x["results+pace+recorded wind"]["moved_at_cutoff"] > 0 for x in R["leak"]),
        "later_wind_moves_earlier_forecast": any(x["results+pace+recorded wind"]["moved_before"] > 0 for x in R["leak"])}
    out("   -> results and pace are as-of: %s; the forecast reads its OWN game's recorded wind: %s "
        "(this is the audit firing on a real look-ahead, so it is not blind)"
        % (R["leak_summary"]["results_and_pace_clean"], R["leak_summary"]["own_game_wind_moves_forecast"]))

    # f-27: a PLANTED LEAK into EARLIER rungs. The wind firing above is on the game AT the cutoff; nothing
    # so far showed `moved_before` can be non-zero. One late-2019 game is filed under week 1 (kickoff
    # untouched) and results at/after a mid-2019 cutoff are scrambled: earlier rungs MUST move.
    late = max((g for g in games if g["season"] == 2019 and g["week"] == 12 and g["home_score"] is not None),
               key=lambda g: g["kickoff_ts"])
    c_p = min(g["kickoff_ts"] for g in games if g["season"] == 2019 and g["week"] == 8)

    def plant(gs):
        g2 = copy.deepcopy(gs)
        for g in g2:
            if g["game_id"] == late["game_id"]:
                g["week"] = 1
        return g2

    def plant_scr(gs):
        g2 = plant(gs)
        for g in g2:
            if g["kickoff_ts"] is not None and g["kickoff_ts"] >= c_p and g["home_score"] is not None:
                g["home_score"], g["away_score"] = g["away_score"] + 7, g["home_score"]
        return g2
    try:
        pb, _g = capture(mut_games=plant)
        ps, _g = capture(mut_games=plant_scr)
        ref = {(r["game"], r["line"]): r["m"] for r in pb["league"]}
        pm = sum(1 for r in ps["league"] if (r["game"], r["line"]) in ref and by_id[r["game"]]["kickoff_ts"] < c_p
                 and abs(r["m"] - ref[(r["game"], r["line"])]) > 1e-12)
        R["leak_planted"] = {"plant": "game %s (2019 wk 12) filed under week 1; results at/after 2019 wk 8 scrambled"
                                      % late["game_id"], "earlier_rungs_moved": pm, "fires": bool(pm)}
        out("   PLANTED LEAK (%s): %d EARLIER rungs moved -> %s"
            % (R["leak_planted"]["plant"], pm, "FIRES" if pm else "DID NOT FIRE - the check is blind to this leak"))
    except (SystemExit, Exception) as e:                     # noqa: BLE001 - the plant failing to run is a result
        R["leak_planted"] = {"fires": None, "error": repr(e)[:300]}
        out("   PLANTED LEAK could not be run through c-31's main(): %r" % (e,))

    # what the record is without the look-ahead input
    nw, _g = capture(no_wind=True)
    if [(r["game"], r["line"]) for r in nw["league"]] != key:
        raise SystemExit("the NO_WIND capture is not row-aligned with the registered one")
    mn = np.array([r["m"] for r in nw["league"]])
    R["no_wind"] = {}
    out("\n   the same record with wind removed from the model (a forecast that could exist before kickoff):")
    for b in K:
        dn = (mn - y) ** 2 - (K[b] - y) ** 2
        st = lambda idx, dn=dn: float(dn[idx].mean())  # noqa: E731
        r = L.summ(st(np.arange(n)), L.block_boot(st, gb, seed=33))
        r["mde"] = L.mde_ratio(r["est"], r["se"])
        R["no_wind"][b] = r
        out("   %-10s registered (recorded wind) %+.4f | no wind %s" % (b, D[b].mean(), L.fmt(r)))

    # ------------------------------------------------------------ 4/5 specifications, MDE
    out("\n== 4/5. SPECIFICATIONS AND MDE")
    k_reg = L.registered_count(rec, "c-31")
    R["registered_intervals_c31"] = k_reg
    R["multiplicity"] = {}
    for b, d in D.items():
        mu = L.multiplicity(reg[b]["est"], reg[b]["se"], (3, max(k_reg, 3), 1000))
        R["multiplicity"][b] = dict(mu, mde=L.mde_ratio(reg[b]["est"], reg[b]["se"]))
        out("   %-10s z %+.1f  Bonferroni survives k=3 %s, k=%d %s, k=1000 %s   |est|/MDE %.2f"
            % (b, mu["z"], mu["bonferroni"][3]["survives_0.05"], max(k_reg, 3),
               mu["bonferroni"][max(k_reg, 3)]["survives_0.05"], mu["bonferroni"][1000]["survives_0.05"],
               R["multiplicity"][b]["mde"]["ratio"]))
    fp = rec.get("factor_params") or {}
    R["grid_edge"] = {"K_GRID": list(GTR.K_GRID), "seasons": len(fp),
                      "plays_k_at_max": sum(1 for v in fp.values() if v[0] == max(GTR.K_GRID)),
                      "ppp_k_at_max": sum(1 for v in fp.values() if v[2] == max(GTR.K_GRID))}
    out("   shrinkage grid %s: plays k at the maximum in %d of %d fitted seasons, points-per-play k in %d "
        "(c-31 disclosed the first)" % (list(GTR.K_GRID), R["grid_edge"]["plays_k_at_max"], len(fp),
                                        R["grid_edge"]["ppp_k_at_max"]))

    if a.served_json:
        R["served_vs_close"] = served_section()
    else:
        R["served_vs_close"] = None
        out("\n== SERVED vs_close: NOT ATTACKED (no --served-json). The +0.0044 above is the ladder comparison; "
            "record_total.json serves a different one.")
    R["seconds"] = time.time() - t0
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(R, f, indent=1, default=str)
    out("\nwrote %s (%.0fs)" % (a.out, R["seconds"]))


if __name__ == "__main__":
    main()
