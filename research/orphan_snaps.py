"""The orphan snap cohort: snap-count rows whose pfr_player_id matches no crosswalk row.

    python -m research.orphan_snaps

Read-only. Opens the store `mode=ro` and reads raw nflverse parquet from the
archive; writes nothing.

WHAT AN ORPHAN IS. `nfl_snap_counts` is keyed on PFR's player id and every
consumer (settle_outcomes, export_web, models/features, research/shrinkage,
research/walkforward) reaches a gsis_id through `player_xwalk.pfr_id`. A snap
row whose pfr id no crosswalk row carries is dropped by every one of those
joins - the export alone lists it, in the manifest's `unresolved_ids`.

WHAT THIS MEASURES
  1. the cohort, on the LATEST version of each (pfr id, game) - the view every
     consumer reads - and on all versions, which is what "232" counted;
  2. whether any archived nflverse source (players, roster_weekly) ever links
     the pfr id to a gsis_id;
  3. a proposed gsis_id per pfr id, found by ROSTER CO-PRESENCE: the gsis ids
     on the same team in the same season-week as every snap row, surname-
     matched, then position-checked. Coverage is the share of the orphan's
     snap weeks the candidate was rostered for that team - a discriminating
     test, not a name match;
  4. reachability: which proposed players have a published player page, how
     many periods publish `snaps: null` while a snap row exists, and how many
     played-zero periods are missing because the snap row could not be joined;
  5. growth: the cohort as of each snap-count release.
"""
import glob
import os
import re
import sqlite3
import unicodedata
from collections import defaultdict

import polars as pl

import config

LATEST = """
SELECT s.* FROM nfl_snap_counts s
JOIN (SELECT pfr_player_id p, game_id g, MAX(data_version) dv
        FROM nfl_snap_counts {where} GROUP BY 1, 2) v
  ON v.p = s.pfr_player_id AND v.g = s.game_id AND v.dv = s.data_version
"""
XW_PFR = "SELECT pfr_id FROM player_xwalk WHERE pfr_id IS NOT NULL"

# Position families: snap-count and roster vocabularies differ (C/G/T vs OL,
# DB/CB/S vs DB, DE/DT vs DL). A candidate outside the family is flagged, not
# dropped - the T.J. Carter pair is decided by this and nothing else.
FAMILY = {"C": "OL", "G": "OL", "T": "OL", "OL": "OL", "OT": "OL",
          "DE": "DL", "DT": "DL", "NT": "DL", "DL": "DL",
          "LB": "LB", "ILB": "LB", "OLB": "LB", "MLB": "LB",
          "CB": "DB", "DB": "DB", "S": "DB", "FS": "DB", "SS": "DB",
          "WR": "WR", "TE": "TE", "RB": "RB", "FB": "RB", "QB": "QB",
          "K": "K", "P": "P", "LS": "LS"}


def _ro():
    return sqlite3.connect("file:%s?mode=ro" % config.DB_PATH, uri=True)


def norm_tokens(name):
    s = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode().lower()
    s = re.sub(r"[.'`]", "", s)
    s = re.sub(r"[^a-z]+", " ", s)
    return {t for t in s.split() if len(t) >= 4 and t not in ("junior", "senior")}


def raw(*parts):
    return os.path.join(config.RAW_DIR, "nflverse", *parts)


def roster_files():
    """Every archived roster_weekly: the 09-09 full history plus each later 2026."""
    files = sorted(glob.glob(raw("2026-09-09", "roster_weekly_*.parquet")))
    files += sorted(f for f in glob.glob(raw("*", "roster_weekly_2026.parquet"))
                    if "2026-09-09" not in f)
    return files


def cohort(con):
    con.row_factory = sqlite3.Row
    rows = con.execute(
        f"SELECT * FROM ({LATEST.format(where='')}) WHERE pfr_player_id NOT IN ({XW_PFR}) "
        "ORDER BY pfr_player_id, season, week").fetchall()
    allv = con.execute(
        f"SELECT COUNT(*), COUNT(DISTINCT pfr_player_id) FROM nfl_snap_counts "
        f"WHERE pfr_player_id NOT IN ({XW_PFR})").fetchone()
    total = con.execute(f"SELECT COUNT(*) FROM ({LATEST.format(where='')})").fetchone()[0]
    by = defaultdict(list)
    for r in rows:
        by[r["pfr_player_id"]].append(dict(r))
    return by, (allv[0], allv[1]), total


def source_links(ids):
    """Does ANY archived players.parquet or roster_weekly carry these pfr ids?"""
    hits = {"players": 0, "roster": 0}
    pfiles = sorted(glob.glob(raw("*", "players.parquet")))
    for f in pfiles:
        hits["players"] += pl.read_parquet(f, columns=["pfr_id"]).filter(
            pl.col("pfr_id").is_in(ids)).height
    rfiles = roster_files()
    for f in rfiles:
        hits["roster"] += pl.read_parquet(f, columns=["pfr_id"]).filter(
            pl.col("pfr_id").is_in(ids)).height
    return hits, len(pfiles), len(rfiles)


def load_rosters(seasons):
    frames = []
    for f in roster_files():
        m = re.search(r"roster_weekly_(\d{4})", f)
        if int(m.group(1)) in seasons:
            frames.append(pl.read_parquet(f, columns=[
                "season", "week", "team", "position", "full_name", "gsis_id", "pfr_id"]))
    return pl.concat(frames).unique()


# nflverse rosters and snap counts spell four franchises differently.
TEAM_ALIAS = {"BLT": "BAL", "CLV": "CLE", "HST": "HOU", "ARZ": "ARI"}


def propose(con, by):
    seasons = {r["season"] for rows in by.values() for r in rows}
    ros = load_rosters(seasons).with_columns(
        pl.col("team").replace(TEAM_ALIAS))
    idx = defaultdict(list)
    for r in ros.iter_rows(named=True):
        idx[(r["season"], r["week"], r["team"])].append(r)
    xw = {g: (n, p, pf) for g, n, p, pf in con.execute(
        "SELECT gsis_id, display_name, position, pfr_id FROM player_xwalk")}
    out = {}
    for pfr, rows in by.items():
        toks = set().union(*(norm_tokens(r["player"]) for r in rows))
        fam = {FAMILY.get(r["position"]) for r in rows} - {None}
        weeks = {(r["season"], r["week"], r["team"]) for r in rows}
        seen = defaultdict(set)
        for k in weeks:
            for c in idx.get(k, []):
                names = norm_tokens(c["full_name"]) | norm_tokens((xw.get(c["gsis_id"]) or ("",))[0])
                if toks & names:
                    seen[c["gsis_id"]].add(k)
        cands = []
        for g, ks in seen.items():
            name, pos, xpfr = xw.get(g, (None, None, None))
            rpos = set(ros.filter(pl.col("gsis_id") == g)["position"].unique().to_list())
            gfam = {FAMILY.get(p) for p in rpos | {pos}} - {None}
            cands.append({"gsis_id": g, "name": name, "xwalk_pfr": xpfr,
                          "coverage": len(ks) / len(weeks),
                          "position_ok": bool(fam & gfam) or not fam})
        cands.sort(key=lambda c: (-c["position_ok"], -c["coverage"]))
        out[pfr] = cands
    return out


def reach(con, by, proposal):
    exp = os.getenv("WEB_EXPORT_DIR")
    res = {}
    for pfr, rows in by.items():
        c = proposal[pfr][0] if proposal[pfr] else None
        if c is None:
            res[pfr] = None
            continue
        g = c["gsis_id"]
        stat_weeks = {(s, w) for s, w in con.execute(
            "SELECT DISTINCT season, week FROM nfl_player_week WHERE gsis_id=? AND season_type='REG'",
            (g,))}
        all_stat = {(s, w) for s, w in con.execute(
            "SELECT DISTINCT season, week FROM nfl_player_week WHERE gsis_id=?", (g,))}
        snap_w = {(r["season"], r["week"]): r for r in rows}
        null_snaps = sum(1 for k in all_stat if k in snap_w)
        missing_zero = sum(1 for k, r in snap_w.items()
                           if k not in all_stat and (r["offense_snaps"] or 0) > 0)
        page = bool(exp) and os.path.exists(os.path.join(exp, "nfl", "players", g, "summary.json"))
        res[pfr] = {"gsis_id": g, "page": page, "stat_weeks": len(stat_weeks),
                    "periods_snaps_null": null_snaps, "played_zero_missing": missing_zero}
    return res


def growth(con):
    versions = [v for (v,) in con.execute(
        "SELECT DISTINCT data_version FROM nfl_snap_counts ORDER BY 1")]
    out = []
    for v in versions:
        n, ids, s26 = con.execute(
            f"SELECT COUNT(*), COUNT(DISTINCT pfr_player_id), SUM(season=2026) FROM "
            f"({LATEST.format(where='WHERE data_version <= ?')}) "
            f"WHERE pfr_player_id NOT IN ({XW_PFR})", (v,)).fetchone()
        out.append((v, n, ids, s26 or 0))
    return out


def departures(con):
    """The id set as of each archived players release, which `growth` cannot see.

    `growth` judges every snap release against TODAY's crosswalk, so a member
    that upstream later resolved is invisible in it. Here a pfr id counts as
    linked once ANY players release up to that date carried it - which is what
    the preserving merge makes true of the store.
    """
    snap_ids = {p for (p,) in con.execute("SELECT DISTINCT pfr_player_id FROM nfl_snap_counts")}
    linked, out = set(), []
    for f in sorted(glob.glob(raw("*", "players.parquet"))):
        day = os.path.basename(os.path.dirname(f))
        linked |= set(pl.read_parquet(f, columns=["pfr_id"])["pfr_id"].drop_nulls().to_list())
        out.append((day, sorted(snap_ids - linked)))
    return out


def main():
    con = _ro()
    by, (all_rows, all_ids), total = cohort(con)
    n = sum(len(v) for v in by.values())
    assert by, "zero orphan ids - either the cohort is gone or the query is wrong; check before trusting"
    print(f"orphans (latest version per pfr/game): {n} rows, {len(by)} pfr ids, of {total:,} rows")
    print(f"orphans (all versions, the '232' basis): {all_rows} rows, {all_ids} pfr ids")

    hits, npf, nrf = source_links(list(by))
    print(f"archived sources linking any orphan pfr id: players {hits['players']} rows over "
          f"{npf} files, roster_weekly {hits['roster']} rows over {nrf} files")
    assert npf > 0 and nrf > 0, "no archived source files found - RAW_DIR is wrong"

    prop = propose(con, by)
    rch = reach(con, by, prop)
    print(f"\n{'pfr':10} {'snap name':24} {'pos':7} {'seasons':10} {'rows':>4}  "
          f"{'proposed gsis':11} {'xwalk name':24} {'cov':>5} pos  xw_pfr    page null0 miss0")
    for pfr, rows in sorted(by.items()):
        names = "/".join(sorted({r["player"] for r in rows}))
        pos = "/".join(sorted({r["position"] or "" for r in rows}))
        ss = sorted({r["season"] for r in rows})
        c = prop[pfr]
        top = c[0] if c else None
        alt = [x for x in c[1:] if x["coverage"] >= 0.5]
        r = rch[pfr] or {}
        print(f"{pfr:10} {names[:24]:24} {pos[:7]:7} {ss[0]}-{ss[-1]:<5} {len(rows):>4}  "
              + (f"{top['gsis_id']:11} {str(top['name'])[:24]:24} {top['coverage']:5.2f} "
                 f"{'ok ' if top['position_ok'] else 'BAD'}  {str(top['xwalk_pfr']):8}  "
                 f"{'Y' if r.get('page') else '-':4} {r.get('periods_snaps_null', 0):5} "
                 f"{r.get('played_zero_missing', 0):5}" if top else "NO CANDIDATE")
              + (f"   [also >=0.5: {', '.join(x['gsis_id'] + ' ' + str(x['name']) for x in alt)}]"
                 if alt else ""))

    tops = [prop[p][0] for p in by if prop[p]]
    print(f"\nproposed: {len(tops)} of {len(by)}; coverage 1.00: "
          f"{sum(t['coverage'] == 1 for t in tops)}; position ok: {sum(t['position_ok'] for t in tops)}; "
          f"candidate already holds a DIFFERENT pfr id: {sum(bool(t['xwalk_pfr']) for t in tops)}")
    pages = [p for p in by if rch[p] and rch[p]["page"]]
    print(f"with a published player page: {len(pages)} -> {sorted(pages)}")
    print(f"  periods on those pages publishing snaps=null while a snap row exists: "
          f"{sum(rch[p]['periods_snaps_null'] for p in pages)}")
    print(f"  played-zero periods (off snaps > 0, no stat row) not published: "
          f"{sum(rch[p]['played_zero_missing'] for p in pages)}")

    print("\ngrowth - cohort as of each snap-count release, against TODAY's crosswalk:")
    print(f"  {'data_version':12} {'rows':>5} {'ids':>4} {'2026 rows':>9}")
    for v, n_, ids, s26 in growth(con):
        print(f"  {v:12} {n_:5} {ids:4} {s26:9}")

    print("\nid set as of each players release (snap ids no release so far has linked):")
    prev = None
    for day, ids in departures(con):
        delta = "" if prev is None else (
            f"  left: {sorted(set(prev) - set(ids))}" if set(prev) - set(ids) else "") + (
            f"  joined: {sorted(set(ids) - set(prev))}" if prev is not None and set(ids) - set(prev) else "")
        print(f"  {day}  {len(ids):3} ids{delta}")
        prev = ids
    extra = set(prev) - set(by)
    print(f"  final set equals the cohort above: {set(prev) == set(by)}"
          + (f" (differs by {sorted(extra)} / {sorted(set(by) - set(prev))})" if set(prev) != set(by) else ""))


if __name__ == "__main__":
    main()
