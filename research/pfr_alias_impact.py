"""What a built pfr_alias table would change in the snap consumers, measured by running them.

    python -m research.pfr_alias_impact --alias PATH.db

Read-only. The store is opened `mode=ro`; the table built by
`python -m jobs.build_pfr_alias --out PATH.db` is ATTACHed, never copied in.
The consumer functions themselves are run twice - once through player_xwalk
alone, once with the attached aliases - so the difference is what the code
would do, not what a re-implementation of it predicts:

  export_web.load_snaps       snap rows reaching a gsis_id; ids left unresolved
  export_web.snap_weeks       (gsis, season, week) rows - the played-zero input
  settle_outcomes.load_snap_index
                              the settlement snap index

and, for the players whose pairing is new, how many of their stat weeks carry
a snap row after and not before (periods publishing `snaps: null` today).
"""
import argparse
import sqlite3

import config
import store
from jobs import export_web, settle_outcomes


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--alias", required=True)
    a = ap.parse_args(argv)
    con = sqlite3.connect(f"file:{config.DB_PATH}?mode=ro", uri=True)
    con.execute(f"ATTACH DATABASE '{a.alias}' AS al")

    xw_only = store.PfrJoin(store._PFR_GSIS_XWALK, "xwalk only", 0)
    alias_sql = store._PFR_GSIS_ALIAS.replace("FROM pfr_alias", "FROM al.pfr_alias")
    assert alias_sql != store._PFR_GSIS_ALIAS, "alias SQL did not retarget - the measurement would read nothing"
    n = con.execute(f"SELECT COUNT(*) FROM ({alias_sql})").fetchone()[0]
    with_alias = store.PfrJoin(f"{store._PFR_GSIS_XWALK} UNION ALL {alias_sql}", "with aliases", n)
    assert n > 0, "zero joinable aliases in the attached table"
    print(f"joinable aliases: {n}")

    real = store.pfr_gsis
    res = {}
    try:
        for label, pj in (("before", xw_only), ("after", with_alias)):
            store.pfr_gsis = lambda c, pj=pj: pj
            snaps, unres = export_web.load_snaps(con, {})
            weeks = export_web.snap_weeks(con, {})
            sidx, players, _ = settle_outcomes.load_snap_index(con)
            res[label] = (snaps, unres, weeks, sidx, players)
    finally:
        store.pfr_gsis = real

    b, af = res["before"], res["after"]
    print(f"load_snaps rows        {len(b[0]):,} -> {len(af[0]):,}  (+{len(af[0]) - len(b[0])})")
    print(f"unresolved pfr ids     {len(b[1])} -> {len(af[1])}: still {sorted(af[1])}")
    print(f"snap_weeks rows        {len(b[2]):,} -> {len(af[2]):,}  (+{len(af[2]) - len(b[2])})")
    print(f"settlement snap index  {len(b[3]):,} -> {len(af[3]):,}  (+{len(af[3]) - len(b[3])}); "
          f"players {len(b[4]):,} -> {len(af[4]):,}")
    changed_keys = [k for k in b[0] if af[0].get(k) != b[0][k]]
    assert not changed_keys, f"{len(changed_keys)} existing snap rows CHANGED value - aliases must only add"

    new_gsis = sorted({k[0] for k in af[2]} - {k[0] for k in b[2]})
    print(f"\ngsis ids newly carrying snap rows: {len(new_gsis)}")
    filled = zero = 0
    for g in new_gsis:
        stat = {(s, w) for s, w in con.execute(
            "SELECT DISTINCT season, week FROM nfl_player_week WHERE gsis_id=?", (g,))}
        wk = {(s, w): v for (gg, s, w), v in af[2].items() if gg == g}
        f = sum(1 for k in wk if k in stat)
        z = sum(1 for k, v in wk.items() if k not in stat and (v[1] or 0) > 0)
        filled += f
        zero += z
        print(f"  {g}  snap weeks {len(wk):3}  stat weeks now with snaps {f:3}  "
              f"played-zero candidates {z:3}")
    print(f"total: stat weeks gaining a snap row {filled}; played-zero candidates {zero}")


if __name__ == "__main__":
    main()
