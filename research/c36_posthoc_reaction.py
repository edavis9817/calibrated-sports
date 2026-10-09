"""c-36 POST HOC - how long a win total takes to absorb the team's own result.

    python -m research.c36_posthoc_reaction --db .../market_log.db --raw .../research_raw/c36 \
        --json-out research/results/win_total_reaction.json

NOT PRE-REGISTERED. Written after the registered run (f850f46) showed the
6-hour game window holding a mean jump of +0.24 / -0.38 wins against a weekly
change of +0.60 / -0.62: the result seemed to arrive after kickoff + 5h. This
script measures that and prices the obvious trade at candle touch prices. It
is one look, chosen by the data, on four weeks - a thing to re-measure.

Reads the candle archive and nfl_games (mode=ro). Writes the JSON named.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import fees                                      # noqa: E402
from jobs import season_model as J                          # noqa: E402
from research import win_total_drift as W                   # noqa: E402

HOURS = (3.5, 4, 5, 6, 8, 12, 17, 24)
ENTRY_HOURS = (4, 5, 6, 8)
LOG = []


def out(s=""):
    print(s)
    LOG.append(s)


def mean_of(key):
    def fn(rs):
        return float(np.mean([x[key] for x in rs])) if rs else None
    return fn


def share_done(rs):
    f = sum(x["f"] for x in rs)
    return sum(x["s"] for x in rs) / f if f else None


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--raw", required=True)
    ap.add_argument("--json-out", required=True)
    a = ap.parse_args(argv)
    candles, _, _ = W.load_candles(a.raw)
    tape = W.Tape(candles)
    con = W.ro(a.db)
    games, _g, _v = J.load(con)
    reg = [g for g in games if g["season"] == W.YEAR and g["game_type"] == "REG"
           and g["home_score"] is not None]
    team_games = defaultdict(list)
    for g in reg:
        d = g["home_score"] - g["away_score"]
        hr = 1.0 if d > 0 else (0.0 if d < 0 else 0.5)
        team_games[g["home"]].append({"week": g["week"], "kick": g["kickoff_ts"], "res": hr,
                                      "game": g["game_id"]})
        team_games[g["away"]].append({"week": g["week"], "kick": g["kickoff_ts"], "res": 1 - hr,
                                      "game": g["game_id"]})

    def wins_at(team, T):
        done = [g for g in team_games[team] if g["kick"] + 3.4 * W.HOUR <= T]
        return sum(1 for g in done if g["res"] == 1.0), 17 - len(done)

    res = {"unit": "c-36", "post_hoc": True, "hours": list(HOURS)}
    out("== POST HOC: the path of the market number after the team's own kickoff K (wins)")
    rows = []
    for t in sorted(tape.by_team):
        for g in team_games[t]:
            if g["res"] == 0.5:
                continue
            K = g["kick"]
            r = {"team": t, "week": g["week"], "game": g["game"], "win": g["res"], "kick": K,
                 "pre": tape.number(t, K - W.HOUR, wins_at)}
            for h in HOURS:
                r[f"h{h}"] = tape.number(t, K + h * W.HOUR, wins_at)
                r[f"n{h}"] = len([1 for _k, p in tape.ladder(t, K + h * W.HOUR, wins_at)
                                  if 0 < p < 1])
            r["npre"] = len([1 for _k, p in tape.ladder(t, K - W.HOUR, wins_at) if 0 < p < 1])
            rows.append(r)
    if len(rows) < 100:
        raise SystemExit(f"REFUSED: {len(rows)} team-games, expected ~128")
    out(f"  team-games {len(rows)} (ties dropped); usable undecided rungs per ladder before "
        f"kickoff: median {np.median([r['npre'] for r in rows]):.0f}")
    path = {}
    for h in HOURS:
        v = [r for r in rows if r["pre"] is not None and r[f"h{h}"] is not None
             and r["h24"] is not None]
        w = [r[f"h{h}"] - r["pre"] for r in v if r["win"] == 1.0]
        ls = [r[f"h{h}"] - r["pre"] for r in v if r["win"] == 0.0]
        pr = [{"game": r["game"],
               "s": (r[f"h{h}"] - r["pre"]) * (1 if r["win"] == 1.0 else -1),
               "f": (r["h24"] - r["pre"]) * (1 if r["win"] == 1.0 else -1)} for r in v]
        frac = W.boot_stat(pr, share_done, "game")
        path[h] = {"n": len(v), "win_mean": float(np.mean(w)), "loss_mean": float(np.mean(ls)),
                   "share_of_24h_move": frac,
                   "rungs_median": float(np.median([r[f"n{h}"] for r in rows]))}
        out(f"  K+{h:>4}h: after a win {np.mean(w):+.2f}, after a loss {np.mean(ls):+.2f} "
            f"(n {len(v)}); share of the K+24h move done {W.fmt(frac, 2)}; usable rungs median "
            f"{path[h]['rungs_median']:.0f}")
    res["path"] = path

    out("\n== POST HOC: the obvious trade. Central rung chosen BEFORE kickoff; after the game buy "
        "the result's side at the candle touch, sell at K+24h at the opposite touch; taker fee "
        "both legs at 100 contracts; game blocks")
    trades = {}
    for he in ENTRY_HOURS:
        tr = []
        skipped = 0
        for r in rows:
            t, K = r["team"], r["kick"]
            c0, rem0 = wins_at(t, K - W.HOUR)
            c1, rem1 = wins_at(t, K + 24 * W.HOUR)
            best = None
            for k_, tk in tape.by_team[t]:
                if W.decided(c0, rem0, k_) is not None or W.decided(c1, rem1, k_) is not None:
                    continue
                q = tape.quote(tk, K - W.HOUR)
                if q and W.usable(*q):
                    c = abs((q[0] + q[1]) / 2 - 0.5)
                    if best is None or c < best[0]:
                        best = (c, tk, q)
            if best is None:
                skipped += 1
                continue
            _c, tk, qpre = best
            qe, qx = tape.quote(tk, K + he * W.HOUR), tape.quote(tk, K + 24 * W.HOUR)
            if not (qe and qx and qe[0] and qe[1] and qx[0] and qx[1]
                    and 0 < qe[0] < qe[1] < 1 and 0 < qx[0] < qx[1] < 1):
                skipped += 1
                continue
            mid_pre = (qpre[0] + qpre[1]) / 2
            mid_e = (qe[0] + qe[1]) / 2
            mid_x = (qx[0] + qx[1]) / 2
            if r["win"] == 1.0:
                buy, sell, mark = qe[1], qx[0], mid_x
                moved, final = mid_e - mid_pre, mid_x - mid_pre
            else:
                buy, sell, mark = 1 - qe[0], 1 - qx[1], 1 - mid_x
                moved, final = mid_pre - mid_e, mid_pre - mid_x
            fee_in = fees.fee_per_contract(round(buy, 4), 100, "taker", 1)
            fee_out = fees.fee_per_contract(round(sell, 4), 100, "taker", 1)
            tr.append({"team": t, "week": r["week"], "game": r["game"], "ticker": tk,
                       "net": sell - buy - fee_in - fee_out, "to_mid_one_fee": mark - buy - fee_in,
                       "mid_moved_at_entry": moved, "mid_moved_at_24h": final,
                       "entry_spread": qe[1] - qe[0]})
        d = {"n": len(tr), "skipped": skipped,
             "net": W.boot_stat(tr, mean_of("net"), "game"),
             "to_mid_one_fee": W.boot_stat(tr, mean_of("to_mid_one_fee"), "game"),
             "mid_moved_at_entry": W.boot_stat(tr, mean_of("mid_moved_at_entry"), "game"),
             "mid_moved_at_24h": W.boot_stat(tr, mean_of("mid_moved_at_24h"), "game"),
             "entry_spread_median": float(np.median([x["entry_spread"] for x in tr])),
             "share_positive": float(np.mean([x["net"] > 0 for x in tr])),
             "by_week": {w: float(np.mean([x["net"] for x in tr if x["week"] == w]))
                         for w in sorted({x["week"] for x in tr})}}
        trades[he] = d
        out(f"  enter K+{he}h (n {len(tr)}, no quote {skipped}): central rung mid moved toward the "
            f"result by entry {W.fmt(d['mid_moved_at_entry'], 4)}, by K+24h "
            f"{W.fmt(d['mid_moved_at_24h'], 4)}; entry spread median "
            f"{d['entry_spread_median'] * 100:.0f}c")
        out(f"       round trip net {W.fmt(d['net'], 4)}; marked to the K+24h mid with the entry fee "
            f"{W.fmt(d['to_mid_one_fee'], 4)}; positive in {d['share_positive']:.0%}; by week "
            + ", ".join(f"wk{w} {v * 100:+.1f}c" for w, v in d["by_week"].items()))
    res["trade"] = trades
    res["log"] = LOG
    json.dump(res, open(a.json_out, "w"), indent=1, default=str)
    out(f"\nwrote {a.json_out}")


if __name__ == "__main__":
    main()
