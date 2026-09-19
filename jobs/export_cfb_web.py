"""W07 track C: the CFB export. Local files only - NOTHING is published.

    python -m jobs.export_cfb_web --out D:/path/to/dir    # build and validate
    python -m jobs.export_cfb_web --dry-run               # validate, write nothing
    python -m jobs.export_cfb_web --findings              # what the contract resists

0 requests, 0 credits, no network. Reads `cfb.db` read-only.

WHY THIS EXISTS. CFB is the test of whether the contract is sport-agnostic - that is
why it was chosen as the second sport. Every place the contract resists is a finding
about the CONTRACT, filed to track A in `docs/track-a-requests.md`, never worked around
here. `--findings` prints them with the measurement behind each.

NOT PUBLISHED, DELIBERATELY. `jobs/export_web.upload()` deletes by absence
(`set(state) - set(local)`) and `weekly_refresh` runs `--upload-only` on a schedule, so
any run that did not also build CFB would delete these keys from R2 - the same hazard
track F filed as F3. This job has no upload path at all: it writes to a directory you
name and stops.

REUSE, NOT A SECOND EXPORTER. `jobs.export_web` owns the contract machinery -
`validate_contract`, `assert_stats_defined`, `write_if_changed`, `envelope`, `slugify`.
This module supplies CFB data and nothing else. A second validator would be a second
source of truth about the contract, which is the duplication this project keeps paying
for. Nothing in `jobs/export_web.py` is edited.

WHAT IS EXPORTED, and the coverage is stated rather than implied:
  sports.json               nfl + cfb
  cfb/manifest.json         seasons, stat definitions, teams, colours, counts
  cfb/teams/<slug>.json     138 FBS teams: schedule, per-season splits, current roster
  cfb/players/index.json    EMPTY, on purpose - see finding C-4. The contract's player
                            index is the page list, and CFB ships no player pages.
"""
import argparse
import json
import os
import re
import sqlite3
import sys
import time
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cfb import paths, sources                                        # noqa: E402
from jobs.export_web import (assert_stats_defined, envelope, iso,      # noqa: E402
                             slugify, validate_contract, write_if_changed)

SPORT = "cfb"
SPORT_NAME = "College Football"
PERIOD_TYPE = "week"
CLASSIFICATION = "fbs"          # the export's scope; see finding C-1
STAT_ERA_FROM = 2004            # the first season with box scores
CURRENT_SEASON = sources.CURRENT_SEASON

# Stat keys this export emits, and their manifest definitions. Keys are CFB's own -
# the contract does not enumerate them, which is one thing it gets right for a second
# sport.
STAT_DEFS = {
    "pass_cmp": ("Completions", "int", "passing", True),
    "pass_att": ("Pass attempts", "int", "passing", True),
    "pass_yds": ("Passing yards", "int", "passing", True),
    "pass_td": ("Passing TD", "int", "passing", True),
    "pass_int": ("Interceptions thrown", "int", "passing", False),
    "rush_att": ("Carries", "int", "rushing", True),
    "rush_yds": ("Rushing yards", "int", "rushing", True),
    "rush_td": ("Rushing TD", "int", "rushing", True),
    "rec": ("Receptions", "int", "receiving", True),
    "rec_yds": ("Receiving yards", "int", "receiving", True),
    "rec_td": ("Receiving TD", "int", "receiving", True),
    "points": ("Points", "int", "team", True),
}
OFFENSE_KEYS = [k for k in STAT_DEFS if k != "points"]


def ro():
    return sqlite3.connect(f"file:{paths.db_path()}?mode=ro", uri=True)


def stat_definitions():
    return {k: {"label": lab, "format": fmt, "group": grp, "higher_is_better": hib}
            for k, (lab, fmt, grp, hib) in STAT_DEFS.items()}


# ---------------------------------------------------------------------------
# reads
# ---------------------------------------------------------------------------

KEY_SLUG = re.compile(r"^[a-z0-9][a-z0-9-]*$")


def team_slug(feed_slug, abbr, name):
    """The school's own slug - `alabama-crimson-tide` - now that the key pattern allows it.

    C-1 filed that `^[a-z0-9]+/teams/[a-z0-9]+\\.json$` forbade hyphens, so CFB team URLs
    were abbreviation-shaped by the contract's choice rather than the sport's, and CFB
    abbreviations are not unique (69 collide across divisions in 2026). Track A widened the
    pattern to `[a-z0-9][a-z0-9-]*` on 2026-09-19 - backward compatible, since `kc` still
    matches - so the readable form is used from here. Nothing CFB was ever published, so
    this changed no URL that existed.

    The feed's slug is preferred, the abbreviation is the fallback, and `build` REFUSES on a
    duplicate: a slug is a URL, and two teams at one URL is not a display problem."""
    for candidate in (feed_slug, slugify(name), (abbr or "").lower()):
        c = (candidate or "").strip("-")
        if c and KEY_SLUG.match(c):
            return c
    raise ValueError(f"no legal slug for {name!r} (feed {feed_slug!r}, abbr {abbr!r})")


def teams(con, season=CURRENT_SEASON, classification=CLASSIFICATION):
    rows = con.execute(
        "SELECT team_id, abbreviation, display_name, slug, conference_name, color, "
        "alternate_color FROM cfb_teams WHERE season=? AND classification=? AND "
        "valid_to_ts IS NULL ORDER BY display_name", (season, classification)).fetchall()
    return [{"team_id": r[0], "abbr": r[1], "name": r[2],
             "slug": team_slug(r[3], r[1], r[2]), "conference": r[4],
             "color": r[5], "alternate_color": r[6]} for r in rows]


def colors(con, season=CURRENT_SEASON):
    """Every team in the season's reference feed, not only the exported ones.

    Keyed on ABBREVIATION because the contract says so - and see finding C-2: CFB
    abbreviations collide across divisions, so this drops rows the sport holds."""
    out, seen, dropped = {}, {}, []
    for abbr, name, color, alt in con.execute(
            "SELECT abbreviation, display_name, color, alternate_color FROM cfb_teams "
            "WHERE season=? AND valid_to_ts IS NULL AND abbreviation IS NOT NULL AND "
            "color IS NOT NULL ORDER BY classification, display_name", (season,)):
        if abbr in seen:
            dropped.append((abbr, name, seen[abbr]))
            continue
        seen[abbr] = name
        out[abbr] = {"primary": "#" + color, "secondary": "#" + alt if alt else None}
    return out, dropped


def abbr_map(con):
    """team_id -> abbreviation, newest season that has one. `cfb_games` leaves an
    abbreviation null on 54,974 sides of 46,296 games from 2004 (mostly non-FBS
    opponents), and the contract requires `opponent_abbr` to be a non-null string."""
    out = {}
    for tid, abbr in con.execute(
            "SELECT team_id, abbreviation FROM cfb_teams WHERE valid_to_ts IS NULL AND "
            "abbreviation IS NOT NULL ORDER BY season"):
        out[tid] = abbr
    return out


def schedule_rows(con, team_id, lines, abbrs, blanks):
    rows = con.execute(
        "SELECT season, week, season_type, game_id, start_ts, home_id, away_id, "
        "home_team, away_team, home_points, away_points, home_abbreviation, "
        "away_abbreviation FROM cfb_games WHERE valid_to_ts IS NULL AND season>=? AND "
        "(home_id=? OR away_id=?) ORDER BY season, start_ts", (STAT_ERA_FROM, team_id, team_id))
    out = []
    for (season, week, stype, gid, ts, hid, aid, hname, aname, hp, ap, habbr, aabbr) in rows:
        home = hid == team_id
        pf, pa = (hp, ap) if home else (ap, hp)
        spread, total = lines.get(gid, (None, None))
        out.append({
            "season": season, "index": week if week is not None else 0,
            "label": f"Week {week}" if week is not None else (stype or "game").title(),
            "game_type": "POST" if (stype or "").lower() == "postseason" else "REG",
            "game_id": str(gid),
            "date": time.strftime("%Y-%m-%d", time.gmtime(ts)) if ts else None,
            "kickoff_ts": ts,
            "home": home,
            "opponent": (aname if home else hname) or "",
            "opponent_abbr": _opponent_abbr(aid if home else hid,
                                            aabbr if home else habbr, abbrs, blanks),
            "points_for": pf, "points_against": pa,
            "result": None if pf is None or pa is None else
                      ("W" if pf > pa else "L" if pf < pa else "T"),
            # Spread from the team's own side; CFBD quotes the HOME line (measured).
            "spread": None if spread is None else (spread if home else -spread),
            "total": total,
            # No coach feed is ingested: the column exists, the data does not.
            "coach": None, "opponent_coach": None,
        })
    return out


def _opponent_abbr(opp_id, listed, abbrs, blanks):
    """The listed code, else the teams feed, else EMPTY and counted. An invented code
    would be worse than a blank: it would look like an identity the sport does not have."""
    out = listed or abbrs.get(opp_id) or ""
    if not out:
        blanks.add(opp_id)
    return out


def game_lines(con):
    """game_id -> (spread, total). One provider per game, consensus preferred."""
    out = {}
    for gid, provider, spread, total in con.execute(
            "SELECT game_id, provider, spread, total FROM cfb_game_lines WHERE "
            "valid_to_ts IS NULL ORDER BY game_id, CASE provider WHEN 'consensus' THEN 0 "
            "ELSE 1 END, provider"):
        out.setdefault(gid, (spread, total))
    return out


def team_splits(con, team_ids):
    """(team_id, season) -> {'offense': Stats, 'defense': Stats, 'games': n}.

    Offence is the team's own box aggregate; DEFENCE is what its opponents produced,
    computed from the same rows on the other side of each game."""
    per = defaultdict(lambda: {"offense": defaultdict(int), "defense": defaultdict(int),
                               "games": set()})
    cols = ", ".join(f"COALESCE(b.{k}, 0)" for k in OFFENSE_KEYS)
    q = (f"SELECT g.season, b.team_id, g.game_id, g.home_id, g.away_id, {cols} "
         "FROM cfb_player_game_box b JOIN cfb_games g ON g.game_id=b.game_id AND "
         "g.valid_to_ts IS NULL WHERE b.valid_to_ts IS NULL AND g.season>=?")
    for row in con.execute(q, (STAT_ERA_FROM,)):
        season, tid, gid, hid, aid, *vals = row
        other = aid if tid == hid else hid
        for owner, side in ((tid, "offense"), (other, "defense")):
            if owner not in team_ids:
                continue
            bucket = per[(owner, season)]
            for k, v in zip(OFFENSE_KEYS, vals):
                bucket[side][k] += v or 0
            if side == "offense":
                bucket["games"].add(gid)
    return per


def team_points(con):
    """(team_id, season) -> points scored, from the schedule rather than the box."""
    out = defaultdict(int)
    for season, hid, aid, hp, ap in con.execute(
            "SELECT season, home_id, away_id, home_points, away_points FROM cfb_games "
            "WHERE valid_to_ts IS NULL AND season>=? AND home_points IS NOT NULL",
            (STAT_ERA_FROM,)):
        out[(hid, season)] += hp or 0
        out[(aid, season)] += ap or 0
    return out


def roster_rows(con, team_id, season=CURRENT_SEASON):
    """The season's roster with usage shares. `snap_share` is NULL and always will be:
    no public CFB source records snaps (cfb.stats_and_usage_only)."""
    usage = {r[0]: r[1:] for r in con.execute(
        "SELECT u.athlete_id, SUM(u.targets), SUM(u.rushes), SUM(u.team_targets), "
        "SUM(u.team_touches), COUNT(*) FROM cfb_player_game_usage u JOIN cfb_games g ON "
        "g.game_id=u.game_id AND g.valid_to_ts IS NULL WHERE u.valid_to_ts IS NULL AND "
        "g.season=? AND u.team_id=? GROUP BY u.athlete_id", (season, team_id))}
    box_games = {r[0]: r[1] for r in con.execute(
        "SELECT b.athlete_id, COUNT(DISTINCT b.game_id) FROM cfb_player_game_box b JOIN "
        "cfb_games g ON g.game_id=b.game_id AND g.valid_to_ts IS NULL WHERE "
        "b.valid_to_ts IS NULL AND g.season=? AND b.team_id=? GROUP BY b.athlete_id",
        (season, team_id))}
    out = []
    for aid, name, pos in con.execute(
            "SELECT athlete_id, full_name, position FROM cfb_rosters WHERE season=? AND "
            "team_id=? AND valid_to_ts IS NULL ORDER BY full_name", (season, team_id)):
        tg, rush, team_tg, team_touch, _n = usage.get(aid, (0, 0, 0, 0, 0))
        out.append({
            "season": season, "id": str(aid), "slug": None, "name": name,
            "position": pos,
            # GAMES WITH A STAT ROW, which is a LOWER BOUND, not an appearance count.
            # See finding C-3: the contract has no way to say that.
            "games": int(box_games.get(aid, 0)),
            "snap_share": None,
            "target_share": round(tg / team_tg, 4) if team_tg else None,
            "carry_share": round(rush / team_touch, 4) if team_touch else None,
            "has_page": False,
        })
    return out


# ---------------------------------------------------------------------------
# build
# ---------------------------------------------------------------------------

def build(con, generated_at=None):
    generated_at = generated_at or iso()
    ts = teams(con)
    ids = {t["team_id"] for t in ts}
    lines = game_lines(con)
    splits = team_splits(con, ids)
    points = team_points(con)
    color_map, dropped_colors = colors(con)
    abbrs = abbr_map(con)
    blank_abbrs = set()
    files = {}

    seasons = [r[0] for r in con.execute(
        "SELECT DISTINCT season FROM cfb_games WHERE valid_to_ts IS NULL AND season>=? "
        "ORDER BY season", (STAT_ERA_FROM,))]
    # counts.games is scoped to what this file describes - games with a final score
    # involving an EXPORTED team, from the stat era. The store holds 46,184 scored games
    # across every division back to 2001; publishing that beside `teams: 138` would put
    # two different denominators in one object with nothing saying so (finding C-5).
    marks = ",".join("?" * len(ids))
    played = con.execute(
        f"SELECT COUNT(*) FROM cfb_games WHERE valid_to_ts IS NULL AND season>=? AND "
        f"home_points IS NOT NULL AND (home_id IN ({marks}) OR away_id IN ({marks}))",
        (STAT_ERA_FROM, *sorted(ids), *sorted(ids))).fetchone()[0]
    last_week = con.execute(
        "SELECT MAX(week) FROM cfb_games WHERE valid_to_ts IS NULL AND season=? AND "
        "season_type='regular' AND home_points IS NOT NULL", (CURRENT_SEASON,)).fetchone()[0]
    # Stale = the schedule says a week has finished and the store has no result for it.
    # `latest_completed_week` is the ingest's own rule (last game kicked off 12h+ ago),
    # reused rather than restated.
    from jobs.ingest_cfb import latest_completed_week
    done = latest_completed_week(con, CURRENT_SEASON)
    done_week = done[1] if done else None
    stale = bool(done_week is not None and (last_week or 0) < done_week)

    dupes = {t["slug"] for t in ts if [x["slug"] for x in ts].count(t["slug"]) > 1}
    if dupes:
        raise ValueError(f"two exported teams share a slug, which is a URL: "
                         f"{sorted((t['name'], t['slug']) for t in ts if t['slug'] in dupes)}")
    for t in ts:
        sched = schedule_rows(con, t["team_id"], lines, abbrs, blank_abbrs)
        team_seasons = sorted({s["season"] for s in sched})
        sp = []
        for season in team_seasons:
            b = splits.get((t["team_id"], season))
            if not b:
                continue
            off = {k: b["offense"].get(k, 0) for k in OFFENSE_KEYS}
            deff = {k: b["defense"].get(k, 0) for k in OFFENSE_KEYS}
            off["points"] = points.get((t["team_id"], season), 0)
            sp.append({"season": season, "season_type": "REG", "games": len(b["games"]),
                       "offense": off, "defense": deff})
        files[f"{SPORT}/teams/{t['slug']}.json"] = {
            **envelope("team", generated_at, SPORT),
            "identity": {"slug": t["slug"], "abbr": t["abbr"], "name": t["name"]},
            "memberships": [],
            "seasons": team_seasons,
            "schedule": sched,
            "splits": sp,
            "roster": roster_rows(con, t["team_id"]),
            "coaches": [],
        }

    files[f"{SPORT}/players/index.json"] = {
        **envelope("player_index", generated_at, SPORT),
        "players": [],
    }

    files[f"{SPORT}/manifest.json"] = {
        **envelope("sport_manifest", generated_at, SPORT),
        "name": SPORT_NAME,
        "period_type": PERIOD_TYPE,
        "current": {
            "season": CURRENT_SEASON,
            "period": {"index": last_week or 0, "label": f"Week {last_week or 0}",
                       "key": f"{CURRENT_SEASON}-{last_week or 0}"},
            "data_through": {"season": CURRENT_SEASON, "index": last_week or 0},
            "source_version": None,
            "stale": stale,
            "stale_reason": (f"results for {CURRENT_SEASON} week {done_week} are not in the "
                             f"store; the newest week with a final score is "
                             f"{last_week}") if stale else None,
        },
        "seasons": seasons,
        "stat_definitions": stat_definitions(),
        "market_definitions": {},
        "scoring_presets": {},
        "scoring_note": ("No scoring preset is published for college football: the components "
                         "are stored, and no fantasy scoring is applied to them here."),
        "teams": [{"slug": t["slug"], "abbr": t["abbr"], "name": t["name"],
                   "conference": t["conference"],
                   "division": None,
                   "classification": CLASSIFICATION,
                   "season": None}
                  for t in ts],
        "team_colors": color_map,
        "counts": {
            "players": 0,
            "teams": len(ts),
            "market": 0,
            "games": played,
            "rungs": 0,
        },
        "unresolved_ids": [],
    }

    files["sports.json"] = {
        "schema_version": envelope("sports", generated_at, None)["schema_version"],
        "generated_at": generated_at, "kind": "sports", "sport": None,
        "sports": [
            {"sport": "nfl", "name": "NFL", "manifest": "nfl/manifest.json"},
            {"sport": SPORT, "name": SPORT_NAME, "manifest": f"{SPORT}/manifest.json"},
        ],
    }
    return files, {"dropped_colors": dropped_colors, "blank_opponent_abbrs": blank_abbrs,
                   "stale": stale}


def export(out_dir, dry_run=False, verbose=True):
    con = ro()
    files, notes = build(con)
    validate_contract(files)                       # the one choke point, track A's
    assert_stats_defined(files, files[f"{SPORT}/manifest.json"]["stat_definitions"])
    written = 0
    for key, obj in sorted(files.items()):
        path = os.path.join(out_dir, *key.split("/"))
        written += write_if_changed(path, obj, dry_run)
    if verbose:
        print(f"  files {len(files)}  written {written}  "
              f"{'(dry run, nothing on disk)' if dry_run else out_dir}")
        print(f"  teams {len(files[f'{SPORT}/manifest.json']['teams'])}  "
              f"seasons {len(files[f'{SPORT}/manifest.json']['seasons'])}  "
              f"colours {len(files[f'{SPORT}/manifest.json']['team_colors'])}  "
              f"colour rows dropped to an abbreviation collision "
              f"{len(notes['dropped_colors'])}")
        print(f"  opponents with no abbreviation anywhere in the feeds: "
              f"{len(notes['blank_opponent_abbrs'])}  (emitted as \"\", never invented)")
        print(f"  current.stale {notes['stale']}")
    con.close()
    return files, notes


FINDINGS = r"""Contract findings from the CFB export - filed to track A, never worked around here.
Every figure is from `cfb.db` and reproduced by `python -m jobs.export_cfb_web`.

C-1  CLOSED 2026-09-19 by track A. A team key could not contain a hyphen
     (`^[a-z0-9]+/teams/[a-z0-9]+\.json$`), so a sport whose teams are multi-word schools
     had no readable team URL and CFB's were abbreviation-shaped by the contract's choice.
     NFL structurally could not see it: its slugs ARE abbreviations. The pattern is now
     `^[a-z0-9]+/teams/[a-z0-9][a-z0-9-]*\.json$` - backward compatible, all 23,209
     existing keys still resolve - and this export emits `cfb/teams/alabama-crimson-tide.json`
     from the school's own slug. No published URL changed, because nothing was published.

C-2  ABBREVIATIONS ARE NOT UNIQUE IN THIS SPORT, and two parts of the contract assume
     they are. `team_colors` is keyed on abbreviation: 69 abbreviations collide across
     divisions in 2026 and 86 colour rows are dropped to keep the map legal. Combined
     with C-1, the same collision lands in the URL space the moment a second division
     is exported.

C-3  A SPORT WITH DIVISIONS AND MOVING CONFERENCES HAS NOWHERE TO SAY SO.
     `SportManifest.teams` is {slug, abbr, name} and `TeamFile.identity` the same three,
     both closed. CFB 2026 holds fbs 138, fcs 128, ii 162, iii 242, and 26 FBS teams
     changed conference between 2025 and 2026 - conference is a per-SEASON fact with no
     field at any level. This export ships FBS only: a scope the contract forced.

C-4  `RosterEntry.games` IS A NON-NULLABLE INTEGER AND CFB CANNOT ANSWER IT HONESTLY.
     No public source records whether a college player dressed, so the only available
     number is games with a stat row - a lower bound that reads as an appearance count.
     On the Alabama 2026 roster that is 0 for 116 of 126 players. `snap_share` beside it
     is nullable and correctly null; the field that cannot be null is the one the sport
     cannot produce.

C-5  THE PLAYER INDEX IS THE PAGE LIST, so a sport cannot publish players without
     publishing pages. `IndexPlayer.slug` is a required string, and a slug is a URL.
     CFB holds 645,777 box rows and 372,638 roster rows and ships no player pages, so
     this export writes an EMPTY index rather than mint URLs track B has not built.
     `counts.players` then reads 0 while the store holds thousands, and `counts` is
     closed (`additionalProperties: false`, five fixed keys), so there is nowhere to say
     "held, not published".

C-6  `counts` HAS NO SCOPE. `counts.teams` counts exported teams (138) while
     `counts.games` counted every scored game the store holds (46,184) until this export
     narrowed it by hand to games involving an exported team (20,954). Both are correct
     numbers about different populations, and the file cannot say which it means.

C-7  `ScheduleGame.opponent_abbr` IS A NON-NULLABLE STRING and the feed often has none:
     54,974 of the two sides across 46,296 games from 2004 carry no abbreviation, mostly
     non-FBS opponents. The teams feed recovers all but ONE, which is emitted as "" -
     an invented code would look like an identity the sport does not have.

NON-FINDINGS, recorded so they are not re-opened:
  * Coaches. `TeamFile.coaches` and `ScheduleGame.coach` are required keys with nullable
    values, no CFB coach feed is ingested, and an empty array plus nulls is honest. The
    contract behaved correctly.
  * Stat keys. `Stats` deliberately does not enumerate keys, so CFB's own keys fit with
    no change - the part of the contract that is genuinely sport-agnostic.
  * `period_type`. Declaring "week" worked exactly as intended.
"""


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", default=paths.root("web_export"),
                    help="directory to write into (default <STORAGE_DIR>/cfb/web_export)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--findings", action="store_true")
    a = ap.parse_args(argv)
    if a.findings:
        print(FINDINGS)
        return 0
    print(f"CFB export (local only, never published):")
    export(a.out, dry_run=a.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
