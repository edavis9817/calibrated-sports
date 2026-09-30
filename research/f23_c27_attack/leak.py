"""f-23 - as-of audit of c-27's decomposed forecaster, instrumented, read-only.

Run as a SCRIPT (not -m), so the `research` package it imports is c-27's:

    set LOGGER_DB=D:/calibrated-sports/data/market_log.db   (c-27 opens it mode=ro)
    python research/f23_c27_attack/leak.py --c27-src D:/temp/f23/c27src \
        --ledger D:/temp/c24/wf_ledger.csv

Wraps Forecaster._window, Forecaster._team_players and Forecaster.league, then
calls components() for every P1 fit (no simulation). For every history row a
fit reads, it records the (season, week) of that row against the forecast's,
and counts rows from the forecast week or later. A leak on the path the brief
names (team volume, opponent, share, snap-rank role) shows as a non-zero count.
The same-week rows the league mean reads are counted separately: they are
OTHER teams' earlier kickoffs that week, not the forecast team's.
"""
import argparse
import sys
from collections import Counter


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--c27-src", required=True)
    ap.add_argument("--ledger", required=True)
    ap.add_argument("--plant", action="store_true",
                    help="widen the window 7 days past kickoff: the audit must then report a LEAK")
    a = ap.parse_args()
    sys.path.insert(0, a.c27_src)
    from research import decomposed_usage as du  # noqa: E402
    from research import walkforward as wf  # noqa: E402

    con = du.ro()
    panel = du.Panel(con)
    C = du.fit_constants(panel)
    fc = du.Forecaster(panel, C)
    games = panel.games
    C_ = Counter()
    cur = {}

    orig_window = du.Forecaster._window

    def window(self, rows, T, kick):
        for w, row in orig_window(self, rows, T, kick + (7 * 86400 if a.plant else 0)):
            g = row[2]
            s, wk = games[g][0], games[g][1]
            C_["history rows read"] += 1
            if (s, wk) >= cur["sw"]:
                C_["history row from forecast week or later"] += 1
            if row[0] >= cur["kick"]:
                C_["history row at/after kickoff"] += 1
            if g == cur["game"]:
                C_["history row IS the forecast game"] += 1
            yield w, row
    du.Forecaster._window = window

    orig_tp = du.Forecaster._team_players

    def team_players(self, team, T, kick):
        # recompute what _team_players reads, and check it
        acc = orig_tp(self, team, T, kick)
        for s in (T - 1, T):
            for k, gs, r in self._tidx.get((team, s), []):
                if k >= kick:
                    break
                C_["role rows read"] += 1
                if k >= cur["kick"]:
                    C_["role row at/after kickoff"] += 1
        return acc
    du.Forecaster._team_players = team_players

    wk_kicks = {}
    for (gg, _tm) in panel.tg:
        s_, w_, k_ = games[gg][:3]
        wk_kicks.setdefault((s_, w_), []).append(k_)

    def league_audit(T, kick):
        n = 0
        for k, s, t in panel.all_tg:
            if k >= kick:
                break
            if s in (T - 1, T):
                n += 1
        return n

    rows = du.load_p1_rows(a.ledger)
    pw_by, snap_by = {}, {}
    seen = set()
    for r in rows:
        T = r["season"]
        if T not in pw_by:
            pw_by[T], snap_by[T] = wf.player_week_rows(con, T), wf.snap_rows(con, T)
        g = r["game"]
        wk = fc.P.meta[g][1]
        pt = pw_by[T].get((r["gsis"], wk))
        if pt:
            pos, team = pt
        else:
            _snap, team, pos = snap_by[T].get((r["gsis"], g), (None, None, None))
            C_["team/pos from the forecast game's SNAP row (no stat row)"] += 1
        key = (r["gsis"], r["stat"], g)
        if key in seen:
            continue
        seen.add(key)
        cur.update(sw=(T, wk), kick=r["kick"], game=g)
        fc.components(r["gsis"], r["stat"], g, r["kick"], T, team, pos)
        C_["fits audited"] += 1
        # league mean: rows from the forecast week (other teams' earlier kickoffs)
        C_["league-mean team-games from the forecast week (other teams)"] += sum(
            1 for kk in wk_kicks.get((T, wk), ()) if kk < r["kick"])
        C_["league-mean team-games total"] += league_audit(T, r["kick"])
    for k_, v in sorted(C_.items()):
        print(f"  {k_:<62} {v:>12,}")
    bad = C_["history row from forecast week or later"] + C_["history row at/after kickoff"] \
        + C_["history row IS the forecast game"] + C_["role row at/after kickoff"]
    if C_["fits audited"] != 13935:
        raise SystemExit(f"audited {C_['fits audited']} fits, c-27 reports 13,935 - the audit did not walk what c-27 fitted")
    print("AS-OF AUDIT:", "CLEAN" if bad == 0 else f"LEAK - {bad} rows")


if __name__ == "__main__":
    main()
