"""f-15: run a-17's OWN committed code, read-only, and compare it with the
independent re-derivation in `research.f15_vacancy_verify`.

    git -C <calibrated-sports> archive <a-17 commit> | tar -x -C <dir>
    python -m research.f15_compare_a17 --a17-root <dir> --mine <verify.json>

a-17's module is imported from an extracted copy of its commit and driven
through `compute` / `aggregate` on mode=ro connections - its `publish` is never
called, and nothing in its tree is edited. Prints event counts at every
filter, the (season, team, player, group) key sets, and every slot/group figure
side by side with the absolute difference and the width ratio.
"""
import argparse
import json
import sqlite3
import sys


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--a17-root", required=True)
    ap.add_argument("--mine", required=True)
    a = ap.parse_args(argv)
    from analytics import paths
    from research import f15_vacancy_verify as v
    acon = sqlite3.connect(paths._uri(paths.db_path()) + "?mode=ro", uri=True)
    mcon = v.ro_market_log()
    sys.path.insert(0, a.a17_root)
    for mod in [k for k in sys.modules if k == "analytics" or k.startswith("analytics.")]:
        del sys.modules[mod]
    from analytics import vacancy
    assert vacancy.__file__.replace("\\", "/").startswith(
        a.a17_root.replace("\\", "/")), vacancy.__file__
    ab, pl, counts = vacancy.compute(acon, mcon, 2013, 2025)
    print("a-17 counts:")
    for k, n in counts.items():
        print("  %-62s %7d" % (k, n))
    theirs_ev = {(e["season"], e["team"], e["gi"], e["pfr"], e["group"])
                 for e in ab}
    mine_all = json.load(open(a.mine))
    print("mine counts:", mine_all["counts"])
    theirs = {}
    for meas in vacancy.MEASURES:
        for _s, sl, e in vacancy.aggregate(ab, pl, meas, "vacancy.%s" % meas):
            theirs["%s|%s" % (meas, sl)] = (e.est, e.lo, e.hi)
    worst, ratios = 0.0, []
    for meas, grp in [("targets", "WR"), ("targets", "TE"), ("targets", "RB"),
                      ("snaps", "WR"), ("snaps", "TE"), ("snaps", "RB"),
                      ("carries", "RB")]:
        cell = mine_all["cells"]["%s|%s|all|own|team_season" % (meas, grp)]
        for col in ("slot1", "slot2", "slot3", "group", "other"):
            for suf in ("", "|net"):
                t = theirs["%s|%s|%s%s" % (meas, grp, col, suf)]
                m = cell[col + suf]
                d = m[0] - t[0]
                r = (m[2] - m[1]) / (t[2] - t[1])
                worst = max(worst, abs(d))
                ratios.append(r)
                print("%-8s %-3s %-10s mine %+.4f [%+.4f,%+.4f]  a-17 %+.4f "
                      "[%+.4f,%+.4f]  diff %+.4f  width %.2f"
                      % (meas, grp, col + suf, *m, *t, d, r))
    print("figures compared %d, max |diff| %.4f, width ratio %.2f-%.2f"
          % (len(ratios), worst, min(ratios), max(ratios)))
    mine_ev = {tuple(x) for x in mine_all["events"]}
    print("events: a-17 %d, mine %d, common %d, only a-17 %d, only mine %d"
          % (len(theirs_ev), len(mine_ev), len(theirs_ev & mine_ev),
             len(theirs_ev - mine_ev), len(mine_ev - theirs_ev)))
    if not ratios:
        raise SystemExit("compared nothing")
    return 0


if __name__ == "__main__":
    sys.exit(main())
