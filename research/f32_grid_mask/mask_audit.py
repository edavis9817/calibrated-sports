"""f-32: the walks behind the `grid_fit` mask audit. READ-ONLY.

Every mode runs c-39's own code from a detached worktree of
origin/c-39-cfb-game-model against cfb.db opened mode=ro, and writes only under
--out. One mode per process, because one 207,360-point walk is ~8.5 minutes:

    inst        this file's INSTRUMENTED copy of grid_fit's loop: same arithmetic,
                but it records the first season each grid point goes `bad` and
                leaves the sums unmasked. One walk yields every season's as-of
                choice set. It counts only where `base` and `trunc` agree with it.
    base        the genuine pipeline, CF.Walk on the registered MOV grid: sums,
                fits and every Part-1 forecast.
    trunc       the genuine truncated refit: C.grid_fit on games with season < T,
                then C.best_params, MOV and plain grids, for each T in --seasons.
    scr         the genuine pipeline on a store whose scores at/after the cutoff
                are scrambled (--subgrid drops a = None: the P2 demonstration).
    plant_base  P1: one late game of the cutoff season re-dated before its opener.
    plant_scr   P1 store, scrambled at the cutoff on TRUE kickoffs.

--chunk N calls C.grid_fit (or the instrumented copy) on N grid points at a time and
concatenates. Every operation in the loop is elementwise over grid points, so a
point's result does not depend on which other points share its call; the reason to
do it is the cache (1.66 MB vectors at 207,360 points; 14 unchunked walks at once
ran 5x slower each). `base` without --chunk is the unchunked reference, and
analyse.py compares the chunked sums against it before reading anything else.

    python research/f32_grid_mask/mask_audit.py MODE --src D:/temp/f32/c-39src \
        --db D:/calibrated-sports/data/cfb.db --out D:/temp/f32 [--seasons 2005,2006]
"""
import argparse
import copy
import json
import math
import os
import sqlite3
import sys
import time

import numpy as np

SEED = 32
NEVER = 9999


def cutoff_of(games, pop):
    kicks = sorted(games[i]["start_ts"] for i in pop)
    return kicks[len(kicks) // 2]


def scramble(games, true_ts, c, completed):
    """f-29's rule: every completed game with TRUE kickoff >= c gets swapped,
    perturbed scores; no ties."""
    rng = np.random.default_rng(SEED)
    g2 = copy.deepcopy(games)
    n = 0
    for g in g2:
        if completed(g) and true_ts[g["game_id"]] >= c:
            g["home_score"], g["away_score"] = g["away_score"] + int(rng.integers(1, 9)), g["home_score"]
            if g["home_score"] == g["away_score"]:
                g["home_score"] += 1
            n += 1
    return g2, n


def plant_p1(games, pop, c):
    """The latest Part-1 game of the cutoff's season, re-dated a day before that
    season's first game. Returns (planted games, index)."""
    season = next(games[i]["season"] for i in pop if games[i]["start_ts"] == c)
    late = max((i for i in pop if games[i]["season"] == season), key=lambda i: games[i]["start_ts"])
    early = min(g["start_ts"] for g in games if g["season"] == season)
    g2 = copy.deepcopy(games)
    g2[late]["start_ts"] = early - 86400.0
    return g2, late


def gfit(C, games, plist, mov, chunk):
    """C.grid_fit, called on `chunk` grid points at a time (0: one call)."""
    if not chunk:
        return C.grid_fit(games, plist, mov=mov)
    parts = [C.grid_fit(games, plist[j:j + chunk], mov=mov) for j in range(0, len(plist), chunk)]
    for p in parts[1:]:
        if p[0] != parts[0][0] or not np.array_equal(p[2], parts[0][2]):
            raise SystemExit("chunks disagree on seasons or counts")
    return parts[0][0], np.concatenate([p[1] for p in parts], axis=1), parts[0][2]


def make_walk(CF, C, games, plist, mov, chunk):
    """CF.Walk with its grid_fit call chunked; params(), pre() and p() are CF's own."""
    if not chunk:
        return CF.Walk(games, plist, mov)
    w = CF.Walk.__new__(CF.Walk)
    w.games, w.plist, w.mov = games, plist, mov
    w.seasons, w.sums, w.counts = gfit(C, games, plist, mov, chunk)
    w._pre, w.fits = {}, {}
    return w


def instrumented(C, games, params_list, out):
    """grid_fit's loop, line for line, plus first_bad. Sums are NOT masked."""
    MEAN, P_CLIP = C.MEAN, C.P_CLIP
    P = len(params_list)
    k = np.array([p.k for p in params_list])
    hfa = np.array([p.hfa for p in params_list])
    reg = np.array([p.regress for p in params_list])
    a_none = np.array([p.a is None for p in params_list])
    a = np.array([1.0 if p.a is None else p.a for p in params_list])
    cap = np.array([np.inf if p.cap is None else p.cap for p in params_list])
    cw = np.array([p.conf_w for p in params_list])
    entry = np.array([p.entry for p in params_list])
    caps = np.unique(cap)
    cap_idx = np.searchsorted(caps, cap)
    conf = C.conferences(games)
    ordr = C.order(games)
    seasons = sorted({games[i]["season"] for i in ordr})
    sidx = {s: j for j, s in enumerate(seasons)}
    sums = np.zeros((len(seasons), P))
    counts = np.zeros(len(seasons), dtype=int)
    bad = np.zeros(P, dtype=bool)
    first_bad = np.full(P, NEVER, dtype=np.int32)
    first_bad_ts = np.full(P, np.nan)
    bad_games = np.zeros(len(seasons), dtype=int)       # games in which >= 1 point newly went bad
    r = {}
    first = season = None
    c400 = math.log(10.0) / 400.0
    for n, i in enumerate(ordr):
        g = games[i]
        s = g["season"]
        if s != season:
            if season is not None:
                tot = {}
                for t, v in r.items():
                    c = conf.get((s, t))
                    if c is not None:
                        e = tot.setdefault(c, [np.zeros(P), 0])
                        e[0] = e[0] + v
                        e[1] += 1
                for t in list(r):
                    c = conf.get((s, t))
                    if c is None:
                        target = MEAN
                    else:
                        target = cw * (tot[c][0] / tot[c][1]) + (1.0 - cw) * MEAN
                    r[t] = target + (1.0 - reg) * (r[t] - target)
            else:
                first = s
            season = s
            out("   season %d (%d games walked, %d points bad so far)" % (s, n, int(bad.sum())))
        h, aw = g["home"], g["away"]
        for t in (h, aw):
            if t not in r:
                r[t] = np.full(P, MEAN) if s == first else MEAN + entry
        rh, ra = r[h], r[aw]
        diff = rh - ra + (0.0 if g["neutral"] else hfa)
        p = 1.0 / (1.0 + np.exp(-diff * c400))
        margin = g["home_score"] - g["away_score"]
        win = margin > 0
        if g.get("fit", True):
            q = np.clip(p if win else 1.0 - p, P_CLIP, 1 - P_CLIP)
            sums[sidx[s]] -= np.log(q)
            counts[sidx[s]] += 1
        logm = np.log(np.minimum(abs(margin), caps) + 1.0)[cap_idx]
        den = 0.001 * (diff if win else -diff) + a
        now = (~a_none) & (den <= 0)
        new = now & ~bad
        if new.any():
            first_bad[new] = s
            first_bad_ts[new] = g["start_ts"]
            bad_games[sidx[s]] += 1
        bad |= now
        mult = np.where(a_none, logm, logm * a / np.where(den <= 0, 1.0, den))
        delta = k * mult * ((1.0 if win else 0.0) - p)
        r[h] = rh + delta
        r[aw] = ra - delta
    return seasons, sums, counts, first_bad, first_bad_ts, bad_games


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["inst", "base", "trunc", "scr", "plant_base", "plant_scr"])
    ap.add_argument("--src", required=True)
    ap.add_argument("--db", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--seasons", default="")
    ap.add_argument("--subgrid", action="store_true")
    ap.add_argument("--chunk", type=int, default=0)
    a = ap.parse_args()
    sys.path.insert(0, os.path.abspath(a.src))
    os.chdir(a.src)
    from models import cfb_game as C
    from research import cfb_game_forecast as CF
    for mod in (C, CF):
        if not os.path.abspath(mod.__file__).startswith(os.path.abspath(a.src)):
            raise SystemExit("%s resolved outside --src" % mod.__file__)
    out = lambda s="": print(s, flush=True)  # noqa: E731
    t0 = time.time()
    con = sqlite3.connect("file:" + os.path.abspath(a.db).replace("\\", "/") + "?mode=ro", uri=True, timeout=5)
    games = CF.load(con)
    con.close()
    if len(games) < 18000:
        raise SystemExit("loaded %d games - expected ~18,991" % len(games))
    spec = dict(CF.GRID_MOV)
    if a.subgrid:
        spec["a"] = [v for v in spec["a"] if v is not None]
    gm, gp = CF.grid(spec), CF.grid(CF.GRID_PLAIN)
    years = list(range(CF.SCORE_FROM, CF.CURRENT + 1))
    pop = [i for i, g in enumerate(games) if C.completed(g) and CF.SCORE_FROM <= g["season"] <= CF.SCORE_TO]
    true_ts = {g["game_id"]: g["start_ts"] for g in games}
    c = cutoff_of(games, pop)
    tag = a.mode + ("_sub" if a.subgrid else "") + ("_chunk" if a.chunk and a.mode == "base" else "")
    out("%s: %d games, %d Part-1, MOV grid %d points, cutoff ts %d" % (tag, len(games), len(pop), len(gm), c))

    if a.mode == "inst":
        step = a.chunk or len(gm)
        quiet = lambda s_="": None  # noqa: E731
        parts = [instrumented(C, games, gm[j:j + step], out if j == 0 else quiet) for j in range(0, len(gm), step)]
        seasons, counts = parts[0][0], parts[0][2]
        sums = np.concatenate([p[1] for p in parts], axis=1)
        fb = np.concatenate([p[3] for p in parts])
        fbt = np.concatenate([p[4] for p in parts])
        np.savez_compressed(os.path.join(a.out, "inst.npz"), seasons=np.array(seasons), sums=sums, counts=counts,
                            first_bad=fb, first_bad_ts=fbt)
        out("inst: %d points ever bad; wrote inst.npz (%.0fs)" % (int((fb != NEVER).sum()), time.time() - t0))
        return

    if a.mode == "trunc":
        for yv in [int(s) for s in a.seasons.split(",") if s]:
            t1 = time.time()
            trunc = [g for g in games if g["season"] < yv]
            seasons, sums, counts = gfit(C, trunc, gm, True, a.chunk)
            p, ll, n = C.best_params(gm, seasons, sums, counts, CF.FIT_FROM, yv)
            s0, u0, c0 = C.grid_fit(trunc, gp, mov=False)
            p0, ll0, n0 = C.best_params(gp, s0, u0, c0, CF.FIT_FROM, yv)
            bad = np.isnan(sums[0])
            np.save(os.path.join(a.out, "trunc_bad_%d.npy" % yv), np.packbits(bad))
            m = np.array([CF.FIT_FROM <= s < yv for s in seasons])
            np.save(os.path.join(a.out, "trunc_fitsum_%d.npy" % yv), sums[m].sum(axis=0))
            R = {"season": yv, "games": len(trunc), "mov": p.as_dict(), "mov_ll": ll, "fit_games": n,
                 "plain": p0.as_dict(), "plain_ll": ll0, "bad_as_of": int(bad.sum()),
                 "plain_nan_points": int(np.isnan(u0[0]).sum()), "seconds": time.time() - t1}
            with open(os.path.join(a.out, "trunc_%d.json" % yv), "w", encoding="utf-8") as f:
                json.dump(R, f, indent=1)
            out("trunc %d: %d games, MOV %s, bad as of then %d (%.0fs)" % (yv, len(trunc), p.as_dict(), R["bad_as_of"],
                                                                         R["seconds"]))
        return

    store, note = games, {}
    if a.mode in ("plant_base", "plant_scr"):
        store, late = plant_p1(games, pop, c)
        note["planted_game"] = games[late]["game_id"]
        note["planted_true_ts"] = games[late]["start_ts"]
        note["planted_ts"] = store[late]["start_ts"]
    if a.mode in ("scr", "plant_scr"):
        store, nscr = scramble(store, true_ts, c, C.completed)
        note["scrambled_games"] = nscr
    walk = make_walk(CF, C, store, gm, True, a.chunk)
    fits, errs = {}, {}
    for yv in years:
        try:
            fits[yv] = walk.params(yv).as_dict()
        except (SystemExit, ValueError) as e:              # recorded, not swallowed
            errs[yv] = repr(e)
    # forecasts: every Part-1 game of the unscrambled population, at its season's fit
    ps = {}
    for i in pop:
        g = store[i]
        if g["season"] in fits and C.completed(g):
            try:
                ps[games[i]["game_id"]] = walk.p(i)
            except (SystemExit, ValueError) as e:
                errs.setdefault("p", repr(e))
    R = dict(note, mode=tag, cutoff_ts=c, n_games=len(games), n_pop=len(pop), grid_points=len(gm),
             bad_full=int(np.isnan(walk.sums[0]).sum()), fits={str(k): v for k, v in fits.items()},
             errors={str(k): v for k, v in errs.items()}, p=ps, seconds=time.time() - t0)
    with open(os.path.join(a.out, tag + ".json"), "w", encoding="utf-8") as f:
        json.dump(R, f)
    if a.mode == "base":
        np.savez_compressed(os.path.join(a.out, tag + "_sums.npz"), seasons=np.array(walk.seasons), sums=walk.sums,
                            counts=walk.counts)
    out("%s: bad over the whole walk %d; %d fits, %d errors; wrote %s.json (%.0fs)"
        % (tag, R["bad_full"], len(fits), len(errs), tag, R["seconds"]))


if __name__ == "__main__":
    main()
