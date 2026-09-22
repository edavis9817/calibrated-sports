"""pfr_alias: PFR ids in nfl_snap_counts that no player_xwalk row carries (unit f-04).

    python -m jobs.build_pfr_alias --dry-run          # read-only: print what it would write
    python -m jobs.build_pfr_alias --out PATH.db      # write the table into a scratch store
    python -m jobs.build_pfr_alias                    # write into config.DB_PATH

The source store is ALWAYS opened `mode=ro`; the only write is the pfr_alias
upsert into the target. Every pairing is derived from the raw nflverse archive,
so the table can be rebuilt from nothing at zero requests (invariant 2).

WHY. Every snap consumer reaches a gsis_id through `player_xwalk.pfr_id`. A snap
row whose pfr id no crosswalk row carries is dropped by every one of those joins
- a-02 measured 28 such ids, 229 latest-version rows, 2013-2026, with 9 of the 28
on published player pages showing `snaps: null` for games we hold. nflverse has
never linked them: no archived players or roster_weekly release carries any of
the 28 pfr ids.

THREE METHODS, AND A FOURTH ANSWER. They are different evidence, so they are
different rows (primary key `(pfr_id, method)`), and a pfr id whose methods
name different gsis ids joins nothing (store.pfr_gsis):

  archive_release   an archived nflverse file carries BOTH ids on ONE row
                    (players, roster_weekly, draft_picks). The strongest thing
                    available: the source itself asserts the pairing.

  draft_slot        NAME-FREE. draft_picks carries the pfr id at a draft slot
                    (year, overall pick) with gsis_id NULL; a players release
                    carries exactly one gsis id at the same slot. A slot is one
                    person. Checked: the gsis is rostered for the snap team in
                    every asserted season. Calibrated on the draft rows that
                    carry BOTH ids (research/pfr_alias_calibration.py).

  participation     NAME-FREE. For every snap row in the participation window
                    (pbp_participation, 2016+), the candidate is the ONLY gsis
                    id on that team whose on-field play counts (offense,
                    defense, special teams) sit within TOL of the snap row -
                    intersected over every checked game. Then, as checks on the
                    survivor, never as the selector: rostered for that team in
                    every checked week, a shared name token, and no pfr_id of
                    its own in player_xwalk. Precision measured on known pairs
                    by research/pfr_alias_calibration.py before it was applied.

  unresolved        no method establishes a pairing. gsis_id is NULL and
                    `evidence` says what was tried. A name match, however
                    plausible, is NOT a method here: it is exactly the fuzzy
                    route the unit forbids, and a name-only row would be read
                    downstream as identity.

COVERAGE is the seasons the pairing is asserted for: the seasons actually
checked. A participation pairing verified on 2019-2020 does not join a 2026 snap
row - that waits for 2026 participation, which nflverse publishes after the
postseason.

MERGE. Written through `store.upsert_preserving`, so a build that returns a
field as NULL cannot unsay a held value; a build that finds NO evidence for a
held pairing writes nothing for it and the row stands - absence fails toward
keeping data. Coverage only WIDENS across builds for the same gsis. A build that
names a DIFFERENT gsis for a held (pfr, method) is a conflict: reported, not
written, and the job exits 1.
"""
import argparse
import glob
import os
import re
import sqlite3
import sys
import time
import unicodedata
from collections import defaultdict

import polars as pl

import config
import store

# On-field play-count tolerance, per game, summed over offense/defense/ST.
# Chosen from the calibration distribution BEFORE the orphans were run: on
# 2024 known pairs the true player's |diff| was <= 5 on 99.7% of rows, so 6
# admits essentially every true match. 4 and 8 were run as sensitivity; at 8
# two orphans lose uniqueness, at 4 none change (see the f-04 report).
TOL = 6
# Fewest checked games a participation pairing may rest on. Set from the
# calibration's per-k error rates - see research/pfr_alias_calibration.py.
MIN_GAMES = 2

METHODS = ("archive_release", "draft_slot", "participation")   # priority order
SPECIAL = ("kickoff", "punt", "field_goal", "extra_point")
# nflverse rosters and snap counts spell four franchises differently.
TEAM_ALIAS = {"BLT": "BAL", "CLV": "CLE", "HST": "HOU", "ARZ": "ARI"}
# (file kind, pfr column, gsis column) - archived files that carry both ids on one row.
RELEASE_KINDS = (("players", "pfr_id", "gsis_id"),
                 ("roster_weekly", "pfr_id", "gsis_id"),
                 ("draft_picks", "pfr_player_id", "gsis_id"))

LATEST = """
SELECT s.* FROM nfl_snap_counts s
JOIN (SELECT pfr_player_id p, game_id g, MAX(data_version) dv
        FROM nfl_snap_counts GROUP BY 1, 2) v
  ON v.p = s.pfr_player_id AND v.g = s.game_id AND v.dv = s.data_version
"""


def raw(*parts):
    return os.path.join(config.RAW_DIR, *parts)


def _ro(path):
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)


def norm_tokens(name):
    s = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode().lower()
    s = re.sub(r"[.'`]", "", s)
    s = re.sub(r"[^a-z]+", " ", s)
    return {t for t in s.split() if len(t) >= 4 and t not in ("junior", "senior")}


def latest_file(pattern):
    """Newest archived copy of one nflverse file, by release directory."""
    hits = sorted(glob.glob(raw("nflverse", "*", pattern)))
    return hits[-1] if hits else None


# ---------------------------------------------------------------- the cohort

def orphans(src):
    """pfr id -> latest-version snap rows, for ids no player_xwalk row carries."""
    src.row_factory = sqlite3.Row
    rows = src.execute(
        f"SELECT * FROM ({LATEST}) WHERE pfr_player_id NOT IN "
        "(SELECT pfr_id FROM player_xwalk WHERE pfr_id IS NOT NULL) "
        "ORDER BY pfr_player_id, season, week").fetchall()
    src.row_factory = None
    by = defaultdict(list)
    for r in rows:
        by[r["pfr_player_id"]].append(dict(r))
    return dict(by)


# ---------------------------------------------------------- archive_release

def release_pairs(ids):
    """pfr id -> {gsis_id: [evidence, ...]} from every archived file carrying both.

    Scans every dated release directory, not only the newest: a pairing a later
    release dropped is still a pairing the source once asserted, and invariant 2
    is what makes it recoverable.
    """
    ids = list(ids)
    out = defaultdict(lambda: defaultdict(list))
    scanned = 0
    for kind, pcol, gcol in RELEASE_KINDS:
        for f in sorted(glob.glob(raw("nflverse*", "*", f"{kind}*.parquet"))):
            schema = pl.read_parquet_schema(f)
            if pcol not in schema or gcol not in schema:
                continue
            scanned += 1
            d = (pl.read_parquet(f, columns=[pcol, gcol])
                 .with_columns(pl.col(pcol).cast(pl.Utf8), pl.col(gcol).cast(pl.Utf8))
                 .filter(pl.col(pcol).is_in(ids) & pl.col(gcol).is_not_null()).unique())
            rel = os.path.relpath(f, config.RAW_DIR).replace("\\", "/")
            for p, g in d.iter_rows():
                out[p][g].append(rel)
    return out, scanned


# --------------------------------------------------------------- draft_slot

def slot_index():
    """(draft_year, overall pick) -> {gsis: first release carrying it}, over every players release."""
    idx = defaultdict(dict)
    for f in sorted(glob.glob(raw("nflverse", "*", "players.parquet"))):
        rel = os.path.relpath(f, config.RAW_DIR).replace("\\", "/")
        d = pl.read_parquet(f, columns=["gsis_id", "draft_year", "draft_pick"]).drop_nulls()
        for g, y, p in d.iter_rows():
            idx[(int(y), int(p))].setdefault(g, rel)
    return idx


def draft_rows(ids=None):
    """pfr id -> [(season, pick, round, team, gsis_or_None, release)] from every draft_picks release."""
    out = defaultdict(set)
    for f in sorted(glob.glob(raw("nflverse*", "*", "draft_picks.parquet"))):
        rel = os.path.relpath(f, config.RAW_DIR).replace("\\", "/")
        d = pl.read_parquet(f, columns=["season", "pick", "round", "team", "pfr_player_id", "gsis_id"])
        if ids is not None:
            d = d.filter(pl.col("pfr_player_id").is_in(list(ids)))
        for s, p, r, t, pfr, g in d.drop_nulls(["season", "pick", "pfr_player_id"]).iter_rows():
            out[pfr].add((int(s), int(p), r, t, g, rel))
    return out


def draft_slot_match(drafts, slots):
    """-> (gsis, evidence) or (None, reason). NAME-FREE.

    A draft slot - (year, overall pick) - is one person. draft_picks carries the
    pfr id at the slot; players carries the gsis id at the slot. Distinct slots
    or distinct gsis ids at one slot refuse.
    """
    keys = {(s, p) for s, p, *_ in drafts}
    if not keys:
        return None, "no draft_picks row"
    if len(keys) > 1:
        return None, f"draft_picks places this pfr id at {len(keys)} slots: {sorted(keys)}"
    (y, p), = keys
    at = slots.get((y, p), {})
    if len(at) != 1:
        return None, f"players releases carry {len(at)} gsis ids at draft slot {y} #{p}"
    (g, prel), = at.items()
    _, _, rnd, team, _, drel = sorted(drafts)[0]
    return g, (f"draft slot {y} rd{rnd} #{p} {team}: pfr id in {drel}, gsis {g} at the same "
               f"slot in {prel} (the only gsis at that slot in any players release)")


# ------------------------------------------------------------ participation

def participation_seasons():
    return sorted({int(m.group(1)) for f in glob.glob(raw("nflverse", "*", "pbp_participation_*.parquet"))
                   if (m := re.search(r"pbp_participation_(\d{4})", f))})


def participation_counts(season, games=None):
    """(game_id, team) -> {gsis: (offense, defense, special)} on-field play counts.

    A player's team in a game is the side he appears for MOST often; special-
    teams plays are counted in `special` whatever side the file lists him on,
    because possession on a kick is the receiving team's and would otherwise
    split a kicker across both teams.
    """
    pf = latest_file(f"pbp_participation_{season}.parquet")
    bf = latest_file(f"play_by_play_{season}.parquet")
    if pf is None or bf is None:
        return {}
    part = pl.read_parquet(pf, columns=["nflverse_game_id", "play_id", "possession_team",
                                        "offense_players", "defense_players"])
    if games is not None:
        part = part.filter(pl.col("nflverse_game_id").is_in(list(games)))
    part = part.with_columns(pl.col("play_id").cast(pl.Float64))
    pbp = (pl.read_parquet(bf, columns=["game_id", "play_id", "play_type",
                                        "special_teams_play", "home_team", "away_team"])
           .rename({"game_id": "nflverse_game_id"})
           .with_columns(pl.col("play_id").cast(pl.Float64)))
    part = part.join(pbp, on=["nflverse_game_id", "play_id"], how="left")
    other = (pl.when(pl.col("possession_team") == pl.col("home_team"))
             .then(pl.col("away_team")).otherwise(pl.col("home_team")))
    frames = []
    for col, side in (("offense_players", "off"), ("defense_players", "def")):
        frames.append(
            part.filter(pl.col(col).is_not_null() & (pl.col(col) != ""))
            .with_columns(pl.col(col).str.split(";").alias("g")).explode("g")
            .with_columns(team=pl.col("possession_team") if side == "off" else other,
                          st=((pl.col("special_teams_play") == 1)
                              | pl.col("play_type").is_in(list(SPECIAL))).fill_null(False),
                          side=pl.lit(side))
            .select("nflverse_game_id", "play_id", "team", "g", "st", "side"))
    e = pl.concat(frames).unique(["nflverse_game_id", "play_id", "g"])
    home = (e.group_by("nflverse_game_id", "g", "team").len()
            .sort("len", descending=True).unique(["nflverse_game_id", "g"], keep="first")
            .select("nflverse_game_id", "g", pl.col("team").alias("home_side")))
    c = (e.join(home, on=["nflverse_game_id", "g"])
         .group_by("nflverse_game_id", "home_side", "g")
         .agg(off=((pl.col("side") == "off") & ~pl.col("st")).sum(),
              dfn=((pl.col("side") == "def") & ~pl.col("st")).sum(),
              st=pl.col("st").sum()))
    out = defaultdict(dict)
    for gid, team, g, off, dfn, st in c.iter_rows():
        out[(gid, team)][g] = (off, dfn, st)
    return dict(out)


def roster_index(seasons):
    """(season, week, team) -> {gsis: full_name}, from every archived roster_weekly."""
    files = sorted(glob.glob(raw("nflverse", "*", "roster_weekly_*.parquet")))
    newest = {}
    for f in files:                       # newest release wins per season file
        newest[os.path.basename(f)] = f
    idx = defaultdict(dict)
    for name, f in newest.items():
        m = re.search(r"roster_weekly_(\d{4})", name)
        if not m or int(m.group(1)) not in seasons:
            continue
        d = pl.read_parquet(f, columns=["season", "week", "team", "gsis_id", "full_name"])
        for s, w, t, g, n in d.iter_rows():
            if g:
                idx[(s, w, TEAM_ALIAS.get(t, t))][g] = n
    return idx


def _dist(v, r):
    return (abs(v[0] - (r.get("offense_snaps") or 0)) + abs(v[1] - (r.get("defense_snaps") or 0))
            + abs(v[2] - (r.get("st_snaps") or 0)))


def participation_match(rows, counts, rosters, xw_names, xw_pfr_by_gsis,
                        tol=TOL, min_games=MIN_GAMES, check_own_pfr=True):
    """-> (gsis, seasons, detail) or (None, None, reason). Name-free selection.

    `check_own_pfr=False` is for calibration only, where every subject is a
    known pair and therefore already holds a pfr id.
    """
    checked, survivors = [], None
    for r in rows:
        cands = counts.get((r["game_id"], r["team"]))
        if cands is None:
            continue
        ok = {g for g, v in cands.items() if _dist(v, r) <= tol}
        survivors = ok if survivors is None else survivors & ok
        checked.append(r)
    if not checked:
        return None, None, "no snap game inside the participation window"
    if len(checked) < min_games:
        return None, None, (f"{len(checked)} checked game(s), fewer than the {min_games} "
                            f"the calibration requires ({len(survivors)} survivor(s))")
    if len(survivors) != 1:
        return None, None, (f"{len(survivors)} gsis ids within +/-{tol} plays on all "
                            f"{len(checked)} checked games - not unique")
    g = next(iter(survivors))
    weeks = [(r["season"], r["week"], r["team"]) for r in checked]
    rostered = sum(g in rosters.get(k, {}) for k in weeks)
    if rostered != len(weeks):
        return None, None, f"survivor {g} rostered for only {rostered} of {len(weeks)} checked weeks"
    snap_toks = set().union(*(norm_tokens(r.get("player")) for r in checked))
    their = set().union(*(norm_tokens(rosters[k].get(g)) for k in weeks)) | norm_tokens(xw_names.get(g))
    shared = snap_toks & their
    if not shared:
        return None, None, f"survivor {g} shares no name token with the snap rows"
    if check_own_pfr and xw_pfr_by_gsis.get(g):
        return None, None, (f"survivor {g} already carries pfr_id {xw_pfr_by_gsis[g]} in "
                            "player_xwalk - two pfr ids for one player would double-count")
    seasons = sorted({r["season"] for r in checked})
    return g, seasons, (f"unique within +/-{tol} on-field plays on {len(checked)} of "
                        f"{len(rows)} snap games; rostered {rostered}/{len(weeks)} weeks; "
                        f"name tokens {sorted(shared)}")


# ------------------------------------------------------------------- build

def _span(seasons):
    return f"{seasons[0]}" if seasons[0] == seasons[-1] else f"{seasons[0]}-{seasons[-1]}"


def build(src, now=None):
    """-> (rows to upsert, per-pfr report). Reads only; writes nothing."""
    now = now or time.time()
    by = orphans(src)
    assert by, "zero orphan pfr ids - the cohort is gone or the query is wrong; check before trusting"
    xw_names = dict(src.execute("SELECT gsis_id, display_name FROM player_xwalk"))
    xw_pfr = dict(src.execute("SELECT gsis_id, pfr_id FROM player_xwalk WHERE pfr_id IS NOT NULL"))

    rel, scanned = release_pairs(by)
    assert scanned > 0, "no archived release carrying both ids was found - RAW_DIR is wrong"

    drafts, slots = draft_rows(by), slot_index()
    window = set(participation_seasons())
    all_seasons = {r["season"] for rows in by.values() for r in rows}
    games = {r["game_id"] for rows in by.values() for r in rows}
    counts = {}
    for s in sorted(all_seasons & window):
        counts.update(participation_counts(s, games))
    rosters = roster_index(all_seasons)

    out, report = [], {}
    for pfr, rows in sorted(by.items()):
        snap_seasons = sorted({r["season"] for r in rows})
        got = []
        cands = rel.get(pfr, {})
        if len(cands) == 1:
            (g, files), = cands.items()
            if xw_pfr.get(g):
                got.append(("refused", f"archive_release names {g}, which already carries "
                                       f"pfr_id {xw_pfr[g]}"))
            else:
                ev = (f"{len(files)} archived file(s) carry pfr {pfr} and gsis {g} on one row; "
                      f"first {files[0]}")
                out.append((pfr, "archive_release", g, snap_seasons[0], snap_seasons[-1],
                            f"{_span(snap_seasons)}, all {len(rows)} snap games", ev, now))
                got.append(("archive_release", g))
        elif len(cands) > 1:
            got.append(("refused", f"archive releases disagree: {sorted(cands)}"))

        g, detail = draft_slot_match(drafts.get(pfr, ()), slots)
        if g is None:
            got.append(("draft_slot-no", detail))
        elif xw_pfr.get(g):
            got.append(("refused", f"draft_slot names {g}, which already carries pfr_id {xw_pfr[g]}"))
        else:
            # The slot is identity-level; coverage is still only the seasons
            # whose snap rows the survivor was ROSTERED for, so the pairing is
            # asserted where it was checked and nowhere else.
            on = lambda r: g in rosters.get((r["season"], r["week"], r["team"]), {})
            ok = sorted({r["season"] for r in rows}
                        - {r["season"] for r in rows if not on(r)})
            n_ok = sum(r["season"] in ok for r in rows)
            if not ok:
                got.append(("draft_slot-no", f"{detail}; but {g} is rostered for none of the snap weeks"))
            else:
                out.append((pfr, "draft_slot", g, ok[0], ok[-1],
                            f"{_span(ok)}, {n_ok} of {len(rows)} snap games",
                            f"{detail}; rostered for the snap team in {_span(ok)}", now))
                got.append(("draft_slot", g))

        g, ss, detail = participation_match(rows, counts, rosters, xw_names, xw_pfr)
        if g is not None:
            n_in = sum(r["season"] in ss for r in rows)
            files = [os.path.relpath(latest_file(f"pbp_participation_{s}.parquet"),
                                     config.RAW_DIR).replace("\\", "/") for s in ss]
            detail = f"{detail}; files {', '.join(files)}"
            out.append((pfr, "participation", g, ss[0], ss[-1],
                        f"{_span(ss)}, {n_in} of {len(rows)} snap games", detail, now))
            got.append(("participation", g))
        else:
            got.append(("participation-no", detail))

        resolved = {x[0] for x in got if x[0] in METHODS}
        if len({x[1] for x in got if x[0] in METHODS}) > 1:
            got.append(("refused", "methods name different gsis ids - the join refuses this pfr id"))
        if not resolved:
            tried = "; ".join(f"{k}: {v}" for k, v in got)
            out.append((pfr, "unresolved", None, None, None, None,
                        f"snap {_span(snap_seasons)}, {len(rows)} games. {tried}", now))
        report[pfr] = {"name": "/".join(sorted({r['player'] or '' for r in rows})),
                       "seasons": snap_seasons, "games": len(rows), "got": got,
                       "resolved": sorted(resolved)}
    return out, report


COLS = ("pfr_id", "method", "gsis_id", "season_from", "season_to", "coverage", "evidence", "built_ts")
PRESERVE = ("gsis_id", "season_from", "season_to", "coverage")


def merge(rows, held):
    """Widen coverage against what is stored; hold back identity flips.

    `held` is {(pfr, method): (gsis, from, to)} from the target. Returns
    (rows to write, conflicts). Absence never narrows: a build that checked
    fewer seasons than last time keeps last time's range.
    """
    keep, conflicts = [], []
    for r in rows:
        pfr, method, g, lo, hi = r[:5]
        h = held.get((pfr, method))
        if h and g is not None and h[0] is not None and h[0] != g:
            conflicts.append(f"{pfr} {method}: stored {h[0]}, this build {g} - not written")
            continue
        if h and g is not None and h[1] is not None:
            nlo, nhi = min(lo, h[1]), max(hi, h[2])
            if (nlo, nhi) != (lo, hi):
                r = r[:3] + (nlo, nhi, f"{_span([nlo, nhi])} (widened by a held build); {r[5]}") + r[6:]
        keep.append(r)
    return keep, conflicts


def write(rows):
    """Upsert into config.DB_PATH's pfr_alias. Never deletes."""
    store.init_db()
    with store.db() as c:
        held = {(p, m): (g, lo, hi) for p, m, g, lo, hi in c.execute(
            "SELECT pfr_id, method, gsis_id, season_from, season_to FROM pfr_alias")}
    keep, conflicts = merge(rows, held)
    store.upsert_preserving("pfr_alias", COLS, keep, ("pfr_id", "method"), PRESERVE)
    return len(keep), conflicts


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--source", default=config.DB_PATH, help="store to read, opened mode=ro")
    ap.add_argument("--out", help="write pfr_alias into THIS store instead of config.DB_PATH")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    src = _ro(a.source)
    rows, report = build(src)
    src.close()

    by_method = defaultdict(int)
    for pfr, r in report.items():
        tag = "+".join(r["resolved"]) or "UNRESOLVED"
        by_method[tag] += 1
        why = "" if r["resolved"] else "  <- " + "; ".join(v for _, v in r["got"])
        pairs = ", ".join(f"{k}={v}" for k, v in r["got"] if k in METHODS)
        print(f"{pfr:9} {r['name'][:24]:24} {_span(r['seasons']):9} {r['games']:3}g  {tag:32} {pairs}{why}")
    print(f"\n{len(report)} orphan pfr ids: " + ", ".join(f"{k} {v}" for k, v in sorted(by_method.items())))
    if a.dry_run:
        print("dry run: nothing written")
        return 0
    if a.out:
        config.DB_PATH = a.out
    n, conflicts = write(rows)
    print(f"wrote {n} pfr_alias rows to {config.DB_PATH}")
    for c in conflicts:
        print("CONFLICT", c)
    return 1 if conflicts else 0


if __name__ == "__main__":
    sys.exit(main())
