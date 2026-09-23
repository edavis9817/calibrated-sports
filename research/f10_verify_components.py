"""F10: re-derive a-11's components table from the source tables, without the exporter.

Reads market_log.db read-only (mode=ro) and the staged components files, and
prints every figure docs/F10-verify-components.md quotes. Nothing is written.

    python -m research.f10_verify_components --db D:/calibrated-sports/data/market_log.db \
        --staged D:/calibrated-sports/staging/a-11/nfl/components \
        --pbp D:/calibrated-sports/data/analytics.db \
        --export D:/calibrated-sports/data/web_export

The population, row set and column mapping below were derived from the source
and the staged OUTPUT before jobs/export_web.py was opened; see the doc for the
order and for which parts were fitted rather than predicted.
"""
import argparse
import collections
import glob
import json
import os
import sqlite3
import sys

# The franchise moves. Only the ORIGINAL codes are listed; nfl_player_week
# already carries the current ones.
RELOCATED = {"OAK": "LV", "SD": "LAC", "STL": "LA"}
SNAP_FIRST_SEASON = 2013
TARGETS_UNCOLLECTED = range(2003, 2009)
NO_NAME = {"00-0005532"}  # documented export exclusion: no resolvable name

MAP = {"targets": "targets", "target_share": "target_share", "rec": "receptions",
       "rec_yds": "receiving_yards", "rec_td": "receiving_tds", "rush_att": "carries",
       "rush_yds": "rushing_yards", "rush_td": "rushing_tds", "pass_att": "attempts",
       "pass_cmp": "completions", "pass_yds": "passing_yards", "pass_td": "passing_tds",
       "int": "interceptions", "fum_lost": "fumbles_lost", "two_pt": "two_pt_conversions",
       "ret_td": "return_tds"}


def num(v):
    return None if v is None or (isinstance(v, float) and v != v) else v


def pos(v):
    v = num(v)
    return v is not None and v > 0


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--staged", required=True)
    ap.add_argument("--pbp", required=True)
    ap.add_argument("--export", required=True)
    a = ap.parse_args(argv)
    c = sqlite3.connect(f"file:{a.db}?mode=ro", uri=True)

    # ---- source: latest data_version per key
    cur = c.execute("SELECT w.* FROM nfl_player_week w JOIN (SELECT gsis_id, season, week, "
                    "season_type, MAX(data_version) dv FROM nfl_player_week GROUP BY 1,2,3,4) v "
                    "USING (gsis_id, season, week, season_type) WHERE w.data_version = v.dv")
    cols = [d[0] for d in cur.description]
    weeks = [dict(zip(cols, r)) for r in cur]
    PW = {(r["gsis_id"], r["season"], r["week"], r["season_type"]): r for r in weeks}

    # ---- population: any REGULAR-season target, carry or pass attempt
    pop = {r["gsis_id"] for r in weeks if r["season_type"] == "REG"
           and (pos(r["targets"]) or pos(r["carries"]) or pos(r["attempts"]))} - NO_NAME
    stat_keys = {k for k in PW if k[0] in pop}

    # ---- snaps
    pfr = {p: g for g, p in c.execute("SELECT gsis_id, pfr_id FROM player_xwalk "
                                      "WHERE pfr_id IS NOT NULL")}
    gtype = dict(c.execute("SELECT game_id, game_type FROM nfl_games"))
    games = {}
    for gid, s, w, h, aw in c.execute("SELECT game_id, season, week, home_team, away_team "
                                      "FROM nfl_games"):
        games[(gid, "h")] = h
        games[(gid, "a")] = aw
    SN, teamgame = {}, collections.defaultdict(list)
    for p, gid, s, w, team, off, pct in c.execute(
            "SELECT s.pfr_player_id, s.game_id, s.season, s.week, s.team, s.offense_snaps, "
            "s.offense_pct FROM nfl_snap_counts s JOIN (SELECT pfr_player_id, game_id, "
            "MAX(data_version) dv FROM nfl_snap_counts GROUP BY 1,2) v "
            "USING (pfr_player_id, game_id) WHERE s.data_version = v.dv"):
        teamgame[(gid, team)].append((off or 0, pct or 0))
        g = pfr.get(p)
        if g:
            stt = "REG" if gtype.get(gid) == "REG" else "POST"
            SN[(g, s, w, stt)] = (off, pct, gid, team)
    snap_only = {k for k, v in SN.items() if k[0] in pop and (v[0] or 0) > 0} - stat_keys
    expected = stat_keys | snap_only

    # ---- staged
    S = {}
    for f in sorted(glob.glob(os.path.join(a.staged, "*.json"))):
        d = json.load(open(f))
        P = d["players"]
        for r in d["rows"]:
            S[(P[r["player"]]["id"], d["season"], r["index"], r["season_type"])] = (
                r["team"], dict(zip(d["columns"], r["values"])))
    staged_players = {k[0] for k in S}

    print("== population and row count")
    print(f"population (REG target|carry|attempt, named)   {len(pop):,}")
    print(f"staged distinct players                         {len(staged_players):,}  "
          f"symmetric diff {len(pop ^ staged_players)}")
    print(f"stat rows for the population                    {len(stat_keys):,}")
    print(f"snap-only rows (offense_snaps>0, no stat row)   {len(snap_only):,}")
    print(f"expected rows                                   {len(expected):,}")
    print(f"staged rows                                     {len(S):,}")
    missing = expected - set(S)
    extra = set(S) - expected
    print(f"expected, not staged {len(missing):,}   staged, not expected {len(extra):,}")
    mteams = collections.Counter(SN[k][3] for k in missing)
    print(f"  missing rows by snap-table team: {dict(mteams)}")
    print(f"  players {len({k[0] for k in missing})}, player-seasons "
          f"{len({k[:2] for k in missing})}, seasons "
          f"{sorted(collections.Counter(k[1] for k in missing).items())}")
    inc_old = sum(1 for k in snap_only & set(S) if SN[k][3] in RELOCATED)
    print(f"  snap-only rows under OAK/SD/STL that DID reach the table: {inc_old}")
    if a.export:
        present = absent = nofile = 0
        cache = {}
        for g, s, w, t in missing:
            p = os.path.join(a.export, "nfl", "players", g, f"{s}.json")
            if p not in cache:
                cache[p] = json.load(open(p)) if os.path.exists(p) else None
            d = cache[p]
            if d is None:
                nofile += 1
            elif any(q["index"] == w and q["season_type"] == t for q in d["periods"]):
                present += 1
            else:
                absent += 1
        print(f"  in the published player season files: present {present}, absent {absent}, "
              f"no season file {nofile}")

    print("== values")
    mism = collections.Counter()
    for k, (team, v) in S.items():
        r = PW.get(k)
        for col, src in MAP.items():
            mine = (num(r[src]) if r else 0)
            if r is not None and mine is None and col not in ("targets", "target_share"):
                mine = 0
            if k[1] in TARGETS_UNCOLLECTED and col in ("targets", "target_share"):
                mine = None  # the documented read-boundary null
            got = v[col]
            if col == "target_share":
                ok = (got is None and mine is None) or (
                    got is not None and mine is not None and abs(got - mine) <= 0.005)
            else:
                ok = got == mine
            mism[col] += not ok
        s = SN.get(k) if k[1] >= SNAP_FIRST_SEASON else None
        for col, i in (("snaps", 0), ("snap_share", 1)):
            mine = None if s is None else s[i]
            got = v[col]
            ok = got == mine or (got is not None and mine is not None and abs(got - mine) <= 0.005)
            mism[col] += not ok
    print(f"value mismatches over {len(S):,} rows x {len(MAP) + 2} columns "
          f"(2003-08 targets/target_share expected null): {dict(mism)}")

    print("== team_targets")
    tt = collections.defaultdict(float)
    for r in weeks:
        if num(r["targets"]) is not None:
            tt[(r["team"], r["season"], r["week"], r["season_type"])] += r["targets"]
    eq = ne = 0
    for k, (team, v) in S.items():
        mine = None if k[1] in TARGETS_UNCOLLECTED else tt.get((team, k[1], k[2], k[3]))
        eq += v["team_targets"] == mine
        ne += v["team_targets"] != mine
    print(f"team_targets == sum of targets over EVERY stat row of the row's team-week: "
          f"{eq:,} equal, {ne:,} differ")
    pb = sqlite3.connect(f"file:{a.pbp}?mode=ro", uri=True)
    pbp = {(t, s, w, st): n for s, w, st, t, n in pb.execute(
        "SELECT season, week, season_type, team, SUM(is_target) FROM f_play_usage "
        "WHERE role = 'receiver' GROUP BY 1,2,3,4")}
    tg = {(team, k[1], k[2], k[3]): v["team_targets"] for k, (team, v) in S.items()
          if v["team_targets"] is not None}
    diffs = collections.Counter(v - pbp[k] for k, v in tg.items() if k in pbp)
    n = sum(diffs.values())
    print(f"team-games {len(tg):,}; with PBP {n:,}; equal to PBP receiver-targets "
          f"{diffs[0]:,} ({diffs[0] / n:.1%}); stats minus PBP = -1: {diffs[-1]:,}, "
          f"-2: {diffs[-2]:,}, -3: {diffs[-3]:,}, >+3: {sum(v for d, v in diffs.items() if d > 3)}")
    big = sorted(k for k, v in tg.items() if k in pbp and v - pbp[k] > 3)
    print(f"  >+3 gaps are all in seasons {sorted({k[1] for k in big})}")

    print("== team_snaps")
    imp = {}
    for key, rows in teamgame.items():
        e = sorted(round(o / p) for o, p in rows if p and p >= 0.5)
        imp[key] = (max(o for o, _ in rows), e[len(e) // 2] if e else None,
                    max(p for _, p in rows))
    rows_eq_max = rows_ne_max = aff_rows = 0
    aff_games, gap = set(), collections.Counter()
    for k, (team, v) in S.items():
        s = SN.get(k)
        if v["team_snaps"] is None or not s:
            continue
        mx, med, top = imp[(s[2], s[3])]
        rows_eq_max += v["team_snaps"] == mx
        rows_ne_max += v["team_snaps"] != mx
        if med is not None and med != v["team_snaps"]:
            aff_rows += 1
            aff_games.add((s[2], s[3]))
            gap[med - v["team_snaps"]] += 1
    print(f"team_snaps == max offense_snaps of the row's own snap-table team: "
          f"{rows_eq_max:,} rows equal, {rows_ne_max} differ")
    print(f"team-games with snaps {len(imp):,}; no player at 100%: "
          f"{sum(1 for v in imp.values() if v[2] < 0.995)}; staged total below the median "
          f"implied total in {len(aff_games)} team-games, {aff_rows} rows, "
          f"gap in snaps (rows) {dict(sorted(gap.items()))}")

    print("== snap-table team labels vs weekly-stats team (players with both)")
    pt = {(g, s, w): t for g, s, w, t in c.execute(
        "SELECT gsis_id, season, week, team FROM nfl_player_week")}
    bygame = collections.defaultdict(lambda: [0, 0])
    for p, gid, s, w, team in c.execute("SELECT pfr_player_id, game_id, season, week, team "
                                        "FROM nfl_snap_counts WHERE offense_snaps > 0"):
        t = pt.get((pfr.get(p), s, w))
        if t is not None:
            bygame[gid][RELOCATED.get(team, team) != t] += 1
    bad = {g: v for g, v in bygame.items() if v[1]}
    print(f"games with any mismatch {len(bad)} of {len(bygame):,}: "
          + ", ".join(f"{g} {v[1]}/{sum(v)}" for g, v in sorted(bad.items())))
    wrong = 0
    for k, (team, v) in S.items():
        s = SN.get(k)
        if s and s[2] in bad and k not in stat_keys:
            teams = {t for (gid, t) in teamgame if gid == s[2]}
            true = (teams - {s[3]}).pop()
            wrong += team != true
    print(f"snap-only staged rows in those games carrying the OTHER team: {wrong}")

    print("== edge cases")
    gameweeks = set()
    for s, w, h, aw in c.execute("SELECT season, week, home_team, away_team FROM nfl_games"):
        gameweeks |= {(RELOCATED.get(h, h), s, w), (RELOCATED.get(aw, aw), s, w)}
    print(f"staged rows in a week their team had no game: "
          f"{[k for k, (t, v) in S.items() if (t, k[1], k[2]) not in gameweeks]}")
    multi = collections.defaultdict(set)
    for k, (t, v) in S.items():
        if k[3] == "REG":
            multi[(k[0], k[1])].add(t)
    print(f"player-seasons with >1 team: {sum(len(v) > 1 for v in multi.values())}")
    z = [k for k, (t, v) in S.items() if v["snaps"] == 0]
    print(f"rows with snaps == 0: {len(z):,}, all stat rows: {all(k in stat_keys for k in z)}")
    print(f"rows with snaps null in 2013+: "
          f"{sum(1 for k, (t, v) in S.items() if v['snaps'] is None and k[1] >= 2013)}; "
          f"pre-2013 rows with non-null snaps: "
          f"{sum(1 for k, (t, v) in S.items() if v['snaps'] is not None and k[1] < 2013)}")

    print("== grain: scoring presets")
    man = json.load(open(os.path.join(a.export, "nfl", "manifest.json")))
    for name, p in man["scoring_presets"].items():
        print(f"  {name}: bonuses = {p['bonuses']}")
    if not S or not pop:
        print("EMPTY INPUT", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
