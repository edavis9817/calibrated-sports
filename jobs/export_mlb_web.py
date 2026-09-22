"""c-03: does the contract admit MLB? A local-only probe export. NOTHING is published.

    python -m jobs.export_mlb_web --out D:/temp/mlb_probe --seasons 2024-2025
    python -m jobs.export_mlb_web --dry-run                 # validate, write nothing
    python -m jobs.export_mlb_web --measure                 # the period-index collisions
    python -m jobs.export_mlb_web --findings                # what the contract resists

0 requests, 0 credits, no network. Reads `mlb.db` read-only. No upload path.

WHY THIS EXISTS. Track A answered "does the contract admit contracts data" by trying it.
This answers "does it admit a second sport's STATS" the same way: build every kind a
sport ships from real MLB rows, and run them through `jobs.export_web.validate_contract`
- track A's single choke point, not a second validator. Where the contract resists, that
is a finding filed to track A, never a local workaround.

NOT A SLUG REGISTRY WRITE. Slugs are URLs, and `web/slugs/{sport}.json` is append-only
and committed. Minting MLB entries there would publish URLs for pages nobody has built,
so this probe computes slugs in memory and discards them.
"""
import argparse
import os
import sqlite3
import sys
from collections import defaultdict
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from jobs.export_web import (assert_stats_defined, envelope, iso,      # noqa: E402
                             slugify, validate_contract, write_if_changed)
from jobs.ingest_mlb import REGULAR_SEASON                            # noqa: E402
from mlb import paths, schema                                          # noqa: E402

SPORT = "mlb"
SPORT_NAME = "MLB"
# THE DECISION THIS PROBE HAD TO MAKE (finding M-1). `index` is a DATE ORDINAL - days
# since 1 January of the season - because it is the only league-wide period MLB has:
# a team's game number differs per team, and a traded player's two teams number the
# same day differently. It is not unique for a player (doubleheaders; the Jansen game),
# and `--measure` counts exactly how often.
PERIOD_TYPE = "date"
CURRENT_SEASON = 2026          # the real one; the store holds none of it (M-2)

LABELS = {
    # Retrosheet gives EVERY pitcher a batting line (0 PA under the DH), so this counts
    # games with a batting line, not games batted.
    "b_g": ("Games with a batting line", "batting", True),
    "b_pa": ("PA", "batting", True), "b_ab": ("AB", "batting", True),
    "b_r": ("R", "batting", True), "b_h": ("H", "batting", True),
    "b_d": ("2B", "batting", True), "b_t": ("3B", "batting", True),
    "b_hr": ("HR", "batting", True), "b_rbi": ("RBI", "batting", True),
    "b_sh": ("SH", "batting", True), "b_sf": ("SF", "batting", True),
    "b_hbp": ("HBP", "batting", True), "b_w": ("BB", "batting", True),
    "b_iw": ("IBB", "batting", True), "b_k": ("SO", "batting", False),
    "b_sb": ("SB", "batting", True), "b_cs": ("CS", "batting", False),
    "b_gdp": ("GIDP", "batting", False), "b_xi": ("Reached on interference", "batting", True),
    "b_roe": ("Reached on error", "batting", True),
    "p_g": ("Games (pitching)", "pitching", True),
    "p_ipouts": ("Outs recorded", "pitching", True), "p_noout": ("Batters faced, no out", "pitching", False),
    "p_bfp": ("Batters faced", "pitching", True), "p_h": ("H allowed", "pitching", False),
    "p_d": ("2B allowed", "pitching", False), "p_t": ("3B allowed", "pitching", False),
    "p_hr": ("HR allowed", "pitching", False), "p_r": ("R allowed", "pitching", False),
    "p_er": ("ER", "pitching", False), "p_w": ("BB allowed", "pitching", False),
    "p_iw": ("IBB allowed", "pitching", False), "p_k": ("SO", "pitching", True),
    "p_hbp": ("HBP", "pitching", False), "p_wp": ("WP", "pitching", False),
    "p_bk": ("BK", "pitching", False), "p_sh": ("SH allowed", "pitching", False),
    "p_sf": ("SF allowed", "pitching", False), "p_sb": ("SB allowed", "pitching", False),
    "p_cs": ("CS", "pitching", True), "p_pb": ("PB", "pitching", False),
    "wp": ("W", "pitching", True), "lp": ("L", "pitching", False),
    "save": ("SV", "pitching", True), "p_gs": ("GS", "pitching", True),
    "p_gf": ("GF", "pitching", True), "p_cg": ("CG", "pitching", True),
}
BAT = schema.BAT_STATS
PIT = schema.PIT_STATS + schema.PIT_FLAGS


def ro():
    return sqlite3.connect(f"file:{paths.db_path()}?mode=ro", uri=True)


def stat_definitions():
    return {k: {"label": lab, "format": "int", "group": grp, "higher_is_better": hib}
            for k, (lab, grp, hib) in LABELS.items()}


def ordinal(yyyymmdd: str) -> int:
    d = date(int(yyyymmdd[:4]), int(yyyymmdd[4:6]), int(yyyymmdd[6:]))
    return (d - date(d.year, 1, 1)).days + 1


def iso_date(yyyymmdd):
    return f"{yyyymmdd[:4]}-{yyyymmdd[4:6]}-{yyyymmdd[6:]}"


def _stats(row, cols):
    return {c: row[c] for c in cols}


def _q(con, sql, params=()):
    cur = con.execute(sql, params)
    names = [d[0] for d in cur.description]
    return [dict(zip(names, r)) for r in cur.fetchall()]


CUR = "valid_to_ts IS NULL AND stattype = 'value'"


def build(con, seasons):
    generated_at = iso()
    ph = ",".join("?" * len(seasons))
    files = {}
    bat = _q(con, f"SELECT * FROM mlb_batting WHERE {CUR} AND season IN ({ph}) "
                  f"AND gametype != 'allstar'", seasons)
    pit = _q(con, f"SELECT * FROM mlb_pitching WHERE {CUR} AND season IN ({ph}) "
                  f"AND gametype != 'allstar'", seasons)
    people = _q(con, f"SELECT * FROM mlb_player_teams WHERE valid_to_ts IS NULL "
                     f"AND season IN ({ph}) AND team NOT IN ('ALS','NLS')", seasons)
    tgames = _q(con, f"SELECT * FROM mlb_team_games WHERE {CUR} AND season IN ({ph}) "
                     f"AND gametype != 'allstar'", seasons)

    # ---- players: one PeriodRow per (game, team), batting and pitching merged
    lines = defaultdict(dict)       # (pid, season) -> {(gid, team): row}
    for src, cols, gkey in ((bat, BAT, "b_g"), (pit, PIT, "p_g")):
        for r in src:
            k = (r["game_id"], r["team"])
            row = lines[(r["player_id"], r["season"])].setdefault(k, {
                "season": r["season"], "index": ordinal(r["date"]),
                "label": iso_date(r["date"]) + (f" (G{r['number']})" if r["number"] else ""),
                "season_type": r["gametype"], "game_id": r["game_id"],
                "date": iso_date(r["date"]), "team": r["team"].lower(),
                "opponent": r["opp"].lower(), "home": r["vishome"] == "h", "stats": {}})
            row["stats"].update(_stats(r, cols))
            row["stats"][gkey] = 1

    names, pos = {}, {}
    by_player = defaultdict(list)
    for p in people:
        names[p["player_id"]] = " ".join(x for x in (p["first"], p["last"]) if x) or None
        by_player[p["player_id"]].append(p)
    for pid, rows in by_player.items():
        g_p = sum(r["g_p"] or 0 for r in rows)
        g = sum(r["g"] or 0 for r in rows)
        pos[pid] = "P" if g and g_p * 2 >= g else "B"      # M-4: one string, two roles

    slugs, taken = {}, set()
    for pid in sorted(by_player):
        base = slugify(names.get(pid)) or pid
        s = base if base not in taken else f"{base}-{pid}"
        taken.add(s)
        slugs[pid] = s

    index = []
    for pid, prow in sorted(by_player.items()):
        seasons_held = sorted({s for (p, s) in lines if p == pid})
        if not seasons_held:
            continue
        slug, name = slugs[pid], names.get(pid)
        last = max(prow, key=lambda r: (r["season"], r["last_g"] or ""))
        season_list, totals = [], []
        career = defaultdict(lambda: 0)
        career_games = 0
        for s in seasons_held:
            periods = sorted(lines[(pid, s)].values(), key=lambda r: (r["index"], r["game_id"]))
            files[f"{SPORT}/players/{pid}/{s}.json"] = {
                **envelope("player_season", generated_at, SPORT),
                "identity": {"id": pid, "slug": slug, "name": name},
                "season": s, "periods": periods}
            teams_s = sorted({r["team"] for r in periods})
            reg = [r for r in periods if r["season_type"] in REGULAR_SEASON]
            season_list.append({"season": s, "teams": teams_s,
                                "games": len({r["game_id"] for r in reg}),
                                "key": f"{SPORT}/players/{pid}/{s}.json"})
            by_type = defaultdict(list)
            for r in periods:
                # a tiebreaker counts toward the regular season (ingest_mlb.REGULAR_SEASON)
                by_type["regular" if r["season_type"] in REGULAR_SEASON
                        else r["season_type"]].append(r)
            for st, rs in sorted(by_type.items()):
                agg = {}
                for r in rs:
                    for k, v in r["stats"].items():
                        agg[k] = None if (v is None or agg.get(k, 0) is None) else agg.get(k, 0) + v
                totals.append({"season": s, "season_type": st,
                               "games": len({r["game_id"] for r in rs}), "stats": agg})
                if st == "regular":
                    career_games += len({r["game_id"] for r in rs})
                    for k, v in agg.items():
                        career[k] = None if (v is None or career[k] is None) else career[k] + v
        files[f"{SPORT}/players/{pid}/summary.json"] = {
            **envelope("player_summary", generated_at, SPORT),
            "identity": {"id": pid, "slug": slug, "name": name, "position": pos.get(pid),
                         "team": last["team"].lower(),
                         "ids": {"retrosheet": pid, "mlbam": None, "bbref": None},
                         "aliases": [], "headshot_url": None},
            "seasons": season_list, "season_totals": totals,
            "career": {"season_type": "regular", "games": career_games, "stats": dict(career)},
            "market": None, "prop_history": None}
        index.append({"id": pid, "slug": slug, "name": name, "position": pos.get(pid),
                      "team": last["team"].lower(), "first_season": seasons_held[0],
                      "last_season": seasons_held[-1], "aliases": [], "has_market": False})
    files[f"{SPORT}/players/index.json"] = {**envelope("player_index", generated_at, SPORT),
                                            "players": index}

    # ---- teams
    by_team = defaultdict(list)
    for t in tgames:
        by_team[t["team"]].append(t)
    team_entries = []
    for code, rows in sorted(by_team.items()):
        slug = code.lower()
        rows.sort(key=lambda r: (r["season"], r["date"], r["number"] or 0))
        sched, n_by_season = [], defaultdict(int)
        for r in rows:
            n_by_season[r["season"]] += 1
            opp_line = next((o for o in by_team.get(r["opp"], ())
                             if o["game_id"] == r["game_id"]), None)
            sched.append({
                "season": r["season"], "index": n_by_season[r["season"]],
                "label": iso_date(r["date"]), "game_type": r["gametype"],
                "game_id": r["game_id"], "date": iso_date(r["date"]), "kickoff_ts": None,
                "home": r["vishome"] == "h", "opponent": r["opp"].lower(),
                "opponent_abbr": r["opp"], "points_for": r["b_r"],
                "points_against": opp_line["b_r"] if opp_line else None,
                "result": "W" if r["win"] else "L" if r["loss"] else "T" if r["tie"] else None,
                "spread": None, "total": None, "coach": r["mgr"],
                "opponent_coach": opp_line["mgr"] if opp_line else None})
        splits = []
        def split_key(r):
            return (r["season"],
                    "regular" if r["gametype"] in REGULAR_SEASON else r["gametype"])
        for (s, st), rs in sorted(_group(rows, split_key).items()):
            splits.append({"season": s, "season_type": st, "games": len(rs),
                           "offense": {c: _sum(rs, c) for c in BAT},
                           "defense": {c: _sum(rs, c) for c in schema.PIT_STATS}})
        roster = []
        for p in people:
            if p["team"] != code:
                continue
            roster.append({"season": p["season"], "id": p["player_id"],
                           "slug": slugs.get(p["player_id"]),
                           "name": names.get(p["player_id"]),
                           "position": pos.get(p["player_id"]), "games": p["g"],
                           "snap_share": None, "target_share": None, "carry_share": None,
                           "has_page": (p["player_id"], p["season"]) in lines})
        seasons_t = sorted({r["season"] for r in rows})
        files[f"{SPORT}/teams/{slug}.json"] = {
            **envelope("team", generated_at, SPORT),
            "identity": {"slug": slug, "abbr": code, "name": code},     # M-5
            "seasons": seasons_t, "memberships": [], "schedule": sched,
            "splits": splits, "roster": roster,
            "coaches": [{"season": s, "head_coach": _mode([r["mgr"] for r in rows
                                                           if r["season"] == s])}
                        for s in seasons_t]}
        if max(seasons_t) == max(seasons):
            team_entries.append({"slug": slug, "abbr": code, "name": code, "conference": None,
                                 "division": None, "classification": None,
                                 "season": None})                        # M-2

    newest = max(seasons)
    last_day = max((ordinal(r["date"]) for r in tgames if r["season"] == newest), default=0)
    files[f"{SPORT}/manifest.json"] = {
        **envelope("sport_manifest", generated_at, SPORT),
        "name": SPORT_NAME, "period_type": PERIOD_TYPE,
        "current": {
            "season": CURRENT_SEASON,
            "period": {"index": 0, "label": "No current-season data",
                       "key": f"{CURRENT_SEASON}-0"},
            "data_through": {"season": newest, "index": last_day},
            "source_version": None,
            "stale": True,
            "stale_reason": ("Retrosheet publishes a season only after it ends; the "
                             f"newest season held is {newest}."),
        },
        "seasons": sorted(seasons), "stat_definitions": stat_definitions(),
        "market_definitions": {}, "scoring_presets": {},
        "scoring_note": "No scoring preset is published for MLB.",
        "teams": team_entries, "team_colors": {},
        "counts": {"players": len(index), "teams": len(team_entries), "market": 0,
                   "games": len({t["game_id"] for t in tgames}), "rungs": 0},
        "unresolved_ids": [],
    }
    return files


def _group(rows, key):
    out = defaultdict(list)
    for r in rows:
        out[key(r)].append(r)
    return out


def _sum(rows, c):
    vals = [r[c] for r in rows]
    return None if any(v is None for v in vals) else sum(vals)


def _mode(xs):
    xs = [x for x in xs if x]
    return max(set(xs), key=xs.count) if xs else None


def measure(con):
    """Period-index collisions under each candidate `period_type`, per player-season,
    over every season held. A collision is two PeriodRows with the same (season, index)."""
    out = {}
    rows = con.execute(
        f"SELECT player_id, season, game_id, team, date, number FROM mlb_batting "
        f"WHERE {CUR} AND gametype IN ('regular', 'playoff') UNION "
        f"SELECT player_id, season, game_id, team, date, number FROM mlb_pitching "
        f"WHERE {CUR} AND gametype IN ('regular', 'playoff')").fetchall()
    team_no = {}
    seq = defaultdict(int)
    for gid, team, season, d, n in con.execute(
            f"SELECT game_id, team, season, date, number FROM mlb_team_games WHERE {CUR} "
            f"AND gametype IN ('regular', 'playoff') ORDER BY team, season, date, number"):
        seq[(team, season)] += 1
        team_no[(gid, team)] = seq[(team, season)]
    by_date, by_game = defaultdict(int), defaultdict(int)
    player_seasons = set()
    for pid, season, gid, team, d, n in rows:
        player_seasons.add((pid, season))
        by_date[(pid, season, d)] += 1
        by_game[(pid, season, team_no.get((gid, team)))] += 1
    out["player_game_lines"] = len(rows)
    out["player_seasons"] = len(player_seasons)
    out["date_index_collisions"] = sum(v - 1 for v in by_date.values() if v > 1)
    out["date_index_player_seasons_hit"] = len({(p, s) for (p, s, _d), v in by_date.items() if v > 1})
    out["team_game_index_collisions"] = sum(v - 1 for v in by_game.values() if v > 1)
    out["team_game_index_player_seasons_hit"] = len(
        {(p, s) for (p, s, _g), v in by_game.items() if v > 1})
    out["seasons"] = [r[0] for r in con.execute(
        "SELECT DISTINCT season FROM mlb_team_games WHERE valid_to_ts IS NULL ORDER BY 1")]
    return out


def export(out_dir, seasons, dry_run=False, verbose=True):
    con = ro()
    files = build(con, seasons)
    validate_contract(files)
    assert_stats_defined(files, files[f"{SPORT}/manifest.json"]["stat_definitions"])
    written = 0
    for key, obj in sorted(files.items()):
        written += write_if_changed(os.path.join(out_dir, *key.split("/")), obj, dry_run)
    if verbose:
        kinds = defaultdict(int)
        for k in files:
            kinds[k.split("/")[1] if k.count("/") else k] += 1
        print(f"  files {len(files)} validated against the contract, written {written}  "
              f"{'(dry run)' if dry_run else out_dir}")
        print(f"  by prefix {dict(kinds)}")
    con.close()
    return files


FINDINGS = r"""Contract findings from the MLB probe (c-03) - for track A, not worked around.
Figures: `python -m jobs.export_mlb_web --measure` and the c-03 report.

M-1  A PERIOD IS NOT UNIQUE FOR A PLAYER, and the contract never says whether it must be.
     `period_type` is free text and `PeriodRow.index` is "the period number within its
     season, in whatever unit period_type declares". MLB has no league-wide unit that is
     unique per player: under a DATE index a doubleheader gives one player two rows with
     one index; under a TEAM-GAME index a traded player's two teams number the same day
     differently and collide. Measured over 1999-2025 regular seasons (1,842,433 player-
     game lines, 35,641 player-seasons): DATE collides 13,630 times in 7,855 player-
     seasons (22%); TEAM-GAME 205 times in 156. The files VALIDATE either way -
     the contract admits the rows - so any site code keying a player's log on (season,
     index) is the thing that breaks, silently. `game_id` is the only unique key, and it
     is nullable.

M-2  A SPORT WITH NO CURRENT SEASON HAS NO HONEST `current`. `current.season` and
     `current.period` are required. The only way to say "this source publishes after the
     season, by design" is `stale: true` - which the contract defines as "the source is
     LATE", a transient state with a banner. MLB-from-Retrosheet is not late; it is
     historical by construction.

M-3  NO ATTRIBUTION FIELD. Retrosheet's licence permits any use on ONE condition: its
     statement "must appear prominently". No kind carries a per-sport credit, and every
     object is closed, so the export cannot add one. Using `scoring_note` would be a
     field lying about what it holds.

M-4  `position` IS ONE STRING AND MLB HAS TWO-WAY PLAYERS. Ohtani 2025: 158 regular-season
     games with a batting line, 14 pitching, 18 games (postseason included) carrying both. The probe emits P or B by majority - a lossy choice the contract forced.
     Separately, `SeasonTotal.games` is one integer, so batting and pitching game counts
     travel as stat keys (`b_g`, `p_g`) - which works, because `Stats` is open.

M-5  NO TEAM NAME IN THE SOURCE. The bundle names teams by Retrosheet code (LAN, CHA,
     ATH). `TeamEntry.name` and `TeamFile.identity.name` are required strings, so the
     probe writes the code there. Not a contract defect - a source gap - but the contract
     cannot say "no name held"; it must be filled or refused.

M-6  THE ROSTER CARRIES THREE FOOTBALL FIELDS AND NO BASEBALL ONE. `snap_share`,
     `target_share`, `carry_share` are required keys, all null for MLB; the sport's own
     usage shares (PA share, IP share) have nowhere to go because RosterEntry is closed.
     `games` IS answerable: `allplayers.g` is a real appearance count, the signal CFB lacks.

M-7  `TeamSeasonSummary.markets` is a required integer. A stats-only sport says 0, which
     is true and indistinguishable from "tracked, none listed".

NON-FINDINGS, so they are not re-opened:
  * Stat keys - `Stats` is an open map; MLB's b_/p_ components fit with no change, and a
    two-way player's batting and pitching lines share one PeriodRow without collision.
  * Key patterns - `mlb/players/ohtas001/2025.json`, `mlb/teams/lan.json` all resolve.
  * `kickoff_ts`, `spread`, `total`, `coach` - football names, nullable; a manager fits
    `coach` and the rest are null. Awkward, not wrong.
  * `result` W/L/T and `tied` - MLB has ties in the historical record; the enum holds them.
  * `memberships: []` - an honest "none published" for a source with no league data.
"""


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", default=paths.root("web_export"))
    ap.add_argument("--seasons", default="2024-2025")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--measure", action="store_true")
    ap.add_argument("--findings", action="store_true")
    a = ap.parse_args(argv)
    if a.findings:
        print(FINDINGS)
        return 0
    if a.measure:
        m = measure(ro())
        for k, v in m.items():
            print(f"  {k}: {v}")
        return 0 if m["player_game_lines"] else 1
    from jobs.ingest_mlb import parse_seasons
    seasons = parse_seasons(a.seasons)
    print(f"MLB probe export (local only, never published) seasons {seasons}:")
    files = export(a.out, seasons, dry_run=a.dry_run)
    return 0 if files else 1


if __name__ == "__main__":
    raise SystemExit(main())
