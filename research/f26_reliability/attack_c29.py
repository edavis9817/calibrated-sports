"""f-29 target adapter: c-29's CLV record (research/results/clv_record.json, tier published).

    CLAIM  touch CLV -0.08pp [-0.34, +0.24], MDE 0.41pp, 119 leans, 11 games,
           2026 week 3: a null, reported with its power.

READ-ONLY. Step 1 needs the target's own script re-run first, into scratch:

    python -m research.clv_record --board <board tree> --db <market_log.db> --out D:/temp/f29/c29_rerun.json
    python research/f26_reliability/attack_c29.py --src D:/temp/f29/c-29src \
        --rerun D:/temp/f29/c29_rerun.json --out D:/temp/f29/c29.json

The re-run reads a ledger that has grown since, so the comparison is made on the
PUBLISHED lean ids, row by row, and the new weeks are reported beside it.
"""
import argparse
import json
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True)
    ap.add_argument("--rerun", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    sys.path.insert(0, HERE)
    import f26lib as L
    sys.path.insert(0, os.path.abspath(a.src))
    os.chdir(a.src)
    from research import clv_record as CR
    if not os.path.abspath(CR.__file__).startswith(os.path.abspath(a.src)):
        raise SystemExit("research.clv_record resolved outside --src: %s" % CR.__file__)
    pub = json.load(open(os.path.join(a.src, "research", "results", "clv_record.json"), encoding="utf-8"))
    now = json.load(open(a.rerun, encoding="utf-8"))
    out = lambda s="": print(s, flush=True)  # noqa: E731
    R = {"target": "c-29"}
    P, N = {r["lean_id"]: r for r in pub["leans"]}, {r["lean_id"]: r for r in now["leans"]}
    if not P or not N:
        raise SystemExit("no lean rows on one side - refusing")

    out("== 1. REPRODUCE (c-29's script re-run today; compared on the %d published lean ids)" % len(P))
    missing = sorted(set(P) - set(N))
    diffs = [abs(P[k]["touch"] - N[k]["touch"]) for k in P if k in N]
    same = [N[k] for k in P if k in N]
    h = CR.game_bootstrap(same, "touch")
    R["reproduce"] = {"published_ids": len(P), "missing_today": len(missing), "max_abs_touch_diff_pp": max(diffs),
                      "today_same_ids": h, "published": pub["headline"],
                      "est": L.reproduce("touch CLV pp", h["est"], round(pub["headline"]["est"], 2), 2),
                      "today_all": now["headline"], "today_weeks": now["weeks_scored"]}
    out("   %d of %d published leans are no longer scored today; largest per-lean change in touch CLV %.4f pp"
        % (len(missing), len(P), max(diffs)))
    out("   same ids, c-29's game_bootstrap today: %+.2f [%+.2f, %+.2f] MDE %.2f n %d games %d   published %+.2f [%+.2f, %+.2f] MDE %.2f"
        % (h["est"], h["lo"], h["hi"], h["mde"], h["n"], h["n_games"], pub["headline"]["est"], pub["headline"]["lo"],
           pub["headline"]["hi"], pub["headline"]["mde"]))
    t = now["headline"]
    out("   the record as it would be written today (weeks %s): %+.2f [%+.2f, %+.2f] MDE %.2f n %d games %d"
        % ([w["week"] for w in now["weeks_scored"]], t["est"], t["lo"], t["hi"], t["mde"], t["n"], t["n_games"]))

    out("\n== 2. BLOCKS: THROUGH CR.game_bootstrap")
    R["through"] = {}
    for label, rows in (("published week 3", same), ("today, all weeks", now["leans"])):
        units = len({r["game_id"] for r in rows})

        def w(rr):
            q = CR.game_bootstrap(rr, "touch", draws=1000)
            return q["hi"] - q["lo"]
        d = L.duplication_through(w, rows, "game_id", fn_name="clv_record.game_bootstrap", units=units)
        R["through"][label] = d
        out("   %-17s %s" % (label, L.through_line(d)))
    L.require_through(list(R["through"]), R["through"])
    x = np.array([r["touch"] for r in now["leans"]])
    stat = lambda idx: float(x[idx].mean())  # noqa: E731
    labs = {"game": [r["game_id"] for r in now["leans"]], "week": [r["week"] for r in now["leans"]],
            "player": [r["gsis_id"] for r in now["leans"]],
            "kickoff slot": [str(r["kickoff"])[:13] for r in now["leans"]]}
    R["alt_blocks"] = L.alt_blocks(stat, len(x), labs, seed=12)
    for nm, r in R["alt_blocks"].items():
        out("   today, all weeks  %-12s (%3d blocks) %s" % (nm, r["n_blocks"], L.fmt(r, 2)))

    out("\n== 3. LEAKAGE, from the scored rows (the pipeline's own as-of assertions are not re-proved here)")

    def audit(rows):
        bad = []
        for r in rows:
            if not (r["close_lag_min"] is not None and r["close_lag_min"] > 0):
                bad.append((r["lean_id"], "close not before kickoff"))
            if not (r["lead_h"] is not None and r["lead_h"] > 0):
                bad.append((r["lean_id"], "read not before kickoff"))
            if not (r["entry_age_h"] is not None and 0 <= r["entry_age_h"] <= 6):
                bad.append((r["lean_id"], "entry capture after the read or older than 6h"))
        return bad
    bad = audit(now["leans"])
    planted = [dict(r) for r in now["leans"]]
    planted[0]["close_lag_min"], planted[1]["entry_age_h"], planted[2]["lead_h"] = -3.0, -0.5, -1.0
    pb = audit(planted)
    R["leak_rows"] = {"rows": len(now["leans"]), "violations": len(bad), "planted_violations": len(pb),
                      "planted_fires": len(pb) == len(bad) + 3}
    out("   %d scored leans: %d with a close at/after kickoff, a read at/after kickoff, or an entry captured after the read"
        % (len(now["leans"]), len(bad)))
    out("      planted (one of each, in memory): %d reported -> %s" % (len(pb), "FIRES" if R["leak_rows"]["planted_fires"] else "DID NOT FIRE"))

    out("\n== 4-5. SPECIFICATIONS AND MDE")
    k = now.get("strata_tests") or pub.get("strata_tests") or 0
    k = k if isinstance(k, int) else len(k)
    R["k_strata"] = k
    for label, hh in (("published", pub["headline"]), ("same ids today", h), ("today, all weeks", now["headline"])):
        mu = L.multiplicity(hh["est"], hh["se"], (1, max(k, 1) + 3), n_blocks=hh["n_games"])
        md = L.mde_ratio(hh["est"], hh["se"])
        cl = L.mde_claim(hh["mde"], hh["se"])
        R.setdefault("mde", {})[label] = {"mult": mu, "ratio": md, "claim": cl}
        out("   %-17s z %+.2f  |est|/MDE %.2f (%s)  stated MDE %.2f vs 2.8 x SE %.2f -> %s"
            % (label, mu["z"], md["ratio"], md["reading"], cl["stated"], cl["remeasured"],
               "consistent" if cl["consistent"] else "INCONSISTENT"))
    out("   the headline is a null: there is nothing for a correction over its %d stratum intervals to remove" % k)
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(R, f, indent=1, default=str)
    out("\nwrote %s" % a.out)


if __name__ == "__main__":
    main()
