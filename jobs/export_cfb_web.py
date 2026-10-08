"""W07 track C: the CFB export. Local files only - NOTHING is published.

    python -m jobs.export_cfb_web --out D:/path/to/dir    # build and validate
    python -m jobs.export_cfb_web --dry-run               # validate, write nothing
    python -m jobs.export_cfb_web --findings              # what the contract resists

0 requests, 0 credits, no network. Reads `cfb.db` read-only.

WHY THIS EXISTS. CFB is the test of whether the contract is sport-agnostic - that is
why it was chosen as the second sport. Every place the contract resists is a finding
about the CONTRACT, filed to track A in `docs/track-a-requests.md`, never worked around
here. `--findings` prints them with the measurement behind each.

THE PUBLISHING PATH (c-05), and why it is safe now when it was not before. The old
hazard was `upload()` deleting by absence (`set(state) - set(local)`): any NFL-only run
would have deleted every CFB key from R2 (track F's F3). That rule is gone - `upload()`
deletes only under prefixes the producing run DECLARES - so CFB follows the second
producer's pattern track F established for `analytics/`, rather than growing an uploader
of its own:

    --dest own   (default) writes to <STORAGE_DIR>/cfb/web_export. STAGING. Nothing
                 reads it, no uploader walks it, no sentinel is printed.
    --dest web   writes `cfb/` into WEB_EXPORT_DIR and prints `REFRESHED cfb/`.
                 THIS IS THE PUBLISH DECISION, not a staging step: the scheduled
                 `weekly_refresh` uploads every key in that tree whose bytes differ
                 from its upload record, so a CFB tree written there reaches R2 on
                 the next Tue/Wed/Thu 09:00 run whether or not anyone runs the upload.

This job still has no network path. There is one uploader (`jobs.export_web`), one
upload record and one bucket; this module owns exactly `cfb/`, fills all of it, and says
so with a `sync_keys` call over that one prefix. `sports.json` is NOT written here: track
A's export writes it on every manifest run, and two builders of one key ping-pong it on
every upload. The sports list naming cfb is track A's to emit.

REUSE, NOT A SECOND EXPORTER. `jobs.export_web` owns the contract machinery -
`validate_contract`, `assert_stats_defined`, `write_if_changed`, `envelope`, `slugify`.
This module supplies CFB data and nothing else. A second validator would be a second
source of truth about the contract, which is the duplication this project keeps paying
for. Nothing in `jobs/export_web.py` is edited.

WHAT IS EXPORTED, and the coverage is stated rather than implied - every key under `cfb/`:
  cfb/manifest.json         seasons, stat definitions, teams, colours, counts
  cfb/teams/<slug>.json     138 FBS teams: schedule, per-season splits, current roster
  cfb/players/index.json    every player in scope (a-72): an offensive box row for an
                            exported team in a season >= PLAYER_SCOPE_FROM
  cfb/players/<id>/summary.json, cfb/players/<id>/<season>.json
                            the NFL player kinds, key for key. What college cannot
                            answer (snaps, markets, prop history) is a NULL the manifest's
                            `absences` list names with its reason - never an omitted key.

a-72 CONFORMANCE. The site's templates are the NFL's; this file's job is to emit what
they read. `research/cfb_contract_diff.py` diffs a CFB tree against an NFL one path by
path and is the check; `python -m jobs.export_cfb_web --absences` prints what is
declared absent and why.
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

from cfb import limitations, paths, sources                           # noqa: E402
from jobs.export_web import (REFRESHED_SENTINEL, assert_stats_defined,  # noqa: E402
                             cumulative_path, emitted_keys, envelope, iso,
                             reconcile_path, require_setting, slugify, sync_keys,
                             validate_contract, write_if_changed)
from jobs import source_registry                                      # noqa: E402

SPORT = "cfb"
SPORT_NAME = "College Football"
PERIOD_TYPE = "week"
CLASSIFICATION = "fbs"          # the export's scope; see finding C-1
STAT_ERA_FROM = 2004            # the first season with box scores
CURRENT_SEASON = sources.CURRENT_SEASON

# WHICH PLAYERS GET A PAGE (a-72). An offensive box row (a pass attempt, a carry or a
# catch) for an exported team in a season >= this. The NFL rule is the same shape -
# "players with offensive usage" - and the floor is what college's scale forces:
# `research/cfb_scale.py` measures the count, the bytes and the identity quality at
# every floor. 2015 is where the usage feed's unidentified-target share falls under
# 0.5% (about 5% a season before). A player in scope carries his WHOLE career,
# including seasons before the floor and rows for teams outside the export.
PLAYER_SCOPE_FROM = 2015

# Stat keys this export emits. WHERE A KEY MEANS WHAT THE FIRST SPORT'S KEY MEANS, IT IS
# THE SAME KEY, label and group included (a-72): the templates name `targets`, `rec`,
# `rush_att`, `snap_share`, `points` and the groups `team_offense` / `team_defense`
# literally, so a college-only spelling of the same stat is a column the page silently
# drops. Until a-72 this file said `pass_int` where the first producer says `int`, and
# put a team's points in a group called `team`.
STAT_DEFS = {
    "snaps": ("Snaps", "int", "usage", True),
    "snap_share": ("Snap %", "pct", "usage", True),
    "snap_share_mean": ("Avg snap %", "pct", "usage", True),
    "targets": ("Tgt", "int", "receiving", True),
    "target_share": ("Tgt %", "pct", "usage", True),
    "rec": ("Rec", "int", "receiving", True),
    "rec_yds": ("Rec Yds", "int", "receiving", True),
    "rec_td": ("Rec TD", "int", "receiving", True),
    "rush_att": ("Car", "int", "rushing", True),
    "rush_yds": ("Rush Yds", "int", "rushing", True),
    "rush_td": ("Rush TD", "int", "rushing", True),
    "pass_att": ("Att", "int", "passing", True),
    "pass_cmp": ("Cmp", "int", "passing", True),
    "pass_yds": ("Pass Yds", "int", "passing", True),
    "pass_td": ("Pass TD", "int", "passing", True),
    "int": ("INT", "int", "passing", False),
    "fum_lost": ("Fum Lost", "int", "misc", False),
    "points": ("Pts", "int", "team_offense", True),
    "int_thrown": ("INT Thrown", "int", "team_offense", False),
    "points_allowed": ("Pts Allowed", "int", "team_defense", False),
    "def_tkl_solo": ("Solo Tkl", "int", "team_defense", True),
    "def_tfl": ("TFL", "dec1", "team_defense", True),
    "def_sacks": ("Sacks", "dec1", "team_defense", True),
    "def_int": ("INT", "int", "team_defense", True),
    "def_pd": ("PD", "int", "team_defense", True),
    "def_td": ("Def TD", "int", "team_defense", True),
    "def_tkl_with_assist": ("Tkl w/ Ast", "int", "team_defense", True),
    "def_tkl_ast": ("Ast Tkl", "int", "team_defense", True),
    "def_qb_hits": ("QB Hits", "int", "team_defense", True),
    "def_ff": ("FF", "int", "team_defense", True),
    "def_safeties": ("Safeties", "int", "team_defense", True),
    "two_pt": ("2-Pt", "int", "misc", True),
    "ret_td": ("Ret TD", "int", "scoring", True),
    # College's own: the box carries total tackles and hurries, and what a defence
    # ALLOWED is the opponents' box on the other side of each game.
    "def_tkl_total": ("Tkl", "int", "team_defense", True),
    "def_qb_hurries": ("QB Hurries", "int", "team_defense", True),
    "pass_yds_allowed": ("Pass Yds Allowed", "int", "team_defense", False),
    "rush_yds_allowed": ("Rush Yds Allowed", "int", "team_defense", False),
}
# published key -> cfb_player_game_box column.
PLAYER_BOX = (("rec", "rec"), ("rec_yds", "rec_yds"), ("rec_td", "rec_td"),
              ("rush_att", "rush_att"), ("rush_yds", "rush_yds"), ("rush_td", "rush_td"),
              ("pass_att", "pass_att"), ("pass_cmp", "pass_cmp"), ("pass_yds", "pass_yds"),
              ("pass_td", "pass_td"), ("int", "pass_int"), ("fum_lost", "fumbles_lost"),
              ("ret_td", "kr_td"))             # + pr_td: see RET_TD_COLUMNS
RET_TD_COLUMNS = ("kr_td", "pr_td")
TEAM_OFFENSE_BOX = (("pass_yds", "pass_yds"), ("rush_yds", "rush_yds"), ("rec_yds", "rec_yds"),
                    ("pass_att", "pass_att"), ("pass_cmp", "pass_cmp"), ("pass_td", "pass_td"),
                    ("rush_td", "rush_td"), ("rec_td", "rec_td"), ("int_thrown", "pass_int"),
                    ("rush_att", "rush_att"))
TEAM_DEFENSE_BOX = (("def_tkl_solo", "tkl_solo"), ("def_tkl_total", "tkl_total"),
                    ("def_tfl", "tfl"), ("def_sacks", "sacks"), ("def_int", "def_int"),
                    ("def_pd", "pass_def"), ("def_td", "def_td"),
                    ("def_qb_hurries", "qb_hurries"))
ALLOWED_BOX = (("pass_yds_allowed", "pass_yds"), ("rush_yds_allowed", "rush_yds"))
BOX_COLUMNS = tuple(dict.fromkeys([c for _, c in PLAYER_BOX + TEAM_OFFENSE_BOX
                                   + TEAM_DEFENSE_BOX + ALLOWED_BOX] + list(RET_TD_COLUMNS)))
OFFENSE_COLUMNS = ("pass_att", "rush_att", "rec")     # any of these > 0 is offensive usage
# The per-row key rule is the first producer's (`jobs.export_web.emitted_keys`): a key is
# kept when some period carries a non-zero value or a NULL for it. These are exempt, as
# there: they are the series the usage frame draws, and an absent one would read as "not
# applicable to this player" where the truth is "nobody recorded it".
USAGE_KEYS = ("snaps", "snap_share", "target_share")
PLAYER_CANDIDATES = ("targets",) + tuple(k for k, _ in PLAYER_BOX) + ("two_pt",)

# A box column the feed carries and did not record in a season is published NULL there,
# never the zero (or the fraction) it holds. The silent-zero class and its
# partially-collected cousin (CLAUDE.md), DETECTED rather than typed, so a new hole is
# found by the run that first exports it. See `not_collected` for the two tests.
HOLE_SHARE = 0.2
PARTIAL_SHARE = 0.6
PARTIAL_MIN_REFERENCE = 1000
DEFENSIVE_CATEGORY = ("tkl_solo", "tkl_total", "tfl", "sacks", "pass_def", "def_td",
                      "qb_hurries")
# Keys the FIRST sport publishes and this feed has no column for at all. Published NULL
# and named in `absences` rather than left out: a key that is missing reads as "not
# applicable", and these are applicable and unrecorded.
TEAM_DEFENSE_ABSENT = ("def_tkl_with_assist", "def_tkl_ast", "def_qb_hits", "def_ff",
                       "def_safeties")
PLAYER_ABSENT = ("two_pt",)

REASON_NO_NAME = "no resolvable name - excluded from the export"
NO_SNAPS = ("Not collected: no public source records college snap counts or whether a "
            "player took the field.")
STAT_ABSENT = ("snaps", "snap_share", "snap_share_mean")

SEASON_TYPE = {"regular": "REG", "postseason": "POST", "spring_regular": "REG",
               "spring_postseason": "POST"}


def ro():
    return sqlite3.connect(f"file:{paths.db_path()}?mode=ro", uri=True)


def stat_definitions():
    out = {k: {"label": lab, "format": fmt, "group": grp, "higher_is_better": hib}
           for k, (lab, fmt, grp, hib) in STAT_DEFS.items()}
    for k in STAT_ABSENT:
        out[k]["description"] = NO_SNAPS
    return out


def game_type(stype):
    """REG or POST - the two literals the templates test. A spring season (2020-21,
    lower divisions) is that season's regular or post season, not a third kind."""
    return SEASON_TYPE.get((stype or "").lower(), "REG")


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
    abbreviations collide across divisions, so this drops rows the sport holds.

    THE EXPORTED TIER WINS A COLLISION, EXPLICITLY. This used to `ORDER BY
    classification` and rely on 'fbs' sorting first - but SQLite sorts NULL before every
    string, and 'Faulkner Eagles' (classification NULL) shares `FAU` with Florida
    Atlantic, so the FBS team's chip was published in another school's colour (measured
    c-05, 2026: 1 of 138). And an exported team with no colour of its own claims its
    abbreviation anyway and emits none: a colliding school's colour standing in for a
    missing one is the same wrong chip by a different route."""
    out, seen, dropped = {}, {}, []
    for abbr, name, color, alt, tier in con.execute(
            "SELECT abbreviation, display_name, color, alternate_color, classification "
            "FROM cfb_teams WHERE season=? AND valid_to_ts IS NULL AND abbreviation IS NOT "
            "NULL ORDER BY classification IS NOT ?, classification, display_name",
            (season, CLASSIFICATION)):
        if color is None and tier != CLASSIFICATION:
            continue                  # as before: an uncoloured non-exported row claims nothing
        if abbr in seen:
            if color is not None:
                dropped.append((abbr, name, seen[abbr]))
            continue
        seen[abbr] = name
        if color is not None:
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


def slug_map(con, exported):
    """team_id -> slug for EVERY team in the feed, exported or not.

    `ScheduleGame.opponent` is "the opponent's team slug" and the templates build
    `/{sport}/team/{opponent}` from it, so it is a slug for an FCS opponent too - the
    slug that team WOULD have. Whether a page exists there is `manifest.teams`' to say,
    never this field's. An exported team's slug is the manifest's; any other takes the
    newest season's feed row, and one a second team already holds is suffixed with the
    team id rather than left ambiguous."""
    out = {t["team_id"]: t["slug"] for t in exported}
    taken = set(out.values())
    for tid, slug, abbr, name in con.execute(
            "SELECT team_id, slug, abbreviation, display_name FROM cfb_teams WHERE "
            "valid_to_ts IS NULL ORDER BY season DESC, team_id"):
        if tid in out:
            continue
        try:
            s = team_slug(slug, abbr, name)
        except ValueError:
            s = f"team-{tid}"
        if s in taken:
            s = f"{s}-{tid}"
        out[tid] = s
        taken.add(s)
    return out


def period_label(week, stype, bowl=None, round_name=None):
    if game_type(stype) == "POST":
        return bowl or round_name or "Postseason"
    return f"Week {week}" if week is not None else (stype or "game").title()


def schedule_rows(con, team_id, lines, abbrs, blanks, slugs):
    rows = con.execute(
        "SELECT season, week, season_type, game_id, start_ts, home_id, away_id, "
        "home_team, away_team, home_points, away_points, home_abbreviation, "
        "away_abbreviation, playoff_bowl_name, playoff_round_name FROM cfb_games WHERE "
        "valid_to_ts IS NULL AND season>=? AND "
        "(home_id=? OR away_id=?) ORDER BY season, start_ts", (STAT_ERA_FROM, team_id, team_id))
    out = []
    for (season, week, stype, gid, ts, hid, aid, hname, aname, hp, ap, habbr, aabbr,
         bowl, rnd_name) in rows:
        home = hid == team_id
        pf, pa = (hp, ap) if home else (ap, hp)
        spread, total = lines.get(gid, (None, None))
        opp_id = aid if home else hid
        out.append({
            "season": season, "index": week if week is not None else 0,
            "label": period_label(week, stype, bowl, rnd_name),
            "game_type": game_type(stype),
            "game_id": str(gid),
            "date": time.strftime("%Y-%m-%d", time.gmtime(ts)) if ts else None,
            "kickoff_ts": ts,
            "home": home,
            # A SLUG, as the contract says and the first producer emits. Until a-72 this
            # was the opponent's display name, which the templates turn into a link.
            "opponent": slugs.get(opp_id) or f"team-{opp_id}",
            "opponent_abbr": _opponent_abbr(opp_id, aabbr if home else habbr, abbrs, blanks),
            "points_for": pf, "points_against": pa,
            "result": None if pf is None or pa is None else
                      ("W" if pf > pa else "L" if pf < pa else "T"),
            "spread": team_spread(spread, home),
            "total": total,
            # No coach feed is ingested: the column exists, the data does not.
            "coach": None, "opponent_coach": None,
        })
    return out


def _opponent_abbr(opp_id, listed, abbrs, blanks):
    """The listed code, else the teams feed, else NULL and counted. An invented code
    would look like an identity the sport does not have, and the contract made this
    field nullable (finding C-7) so the gap need not be an empty string, which renders
    as a blank that says nothing about why."""
    out = listed or abbrs.get(opp_id) or None
    if not out:
        blanks.add(opp_id)
    return out


def team_spread(spread, home):
    """CFBD `spread` is the HOME side's betting line: NEGATIVE when the home team is
    favoured ("Alabama -18.5" is stored -18.5 with Alabama at home). The contract's
    `spread` is POSITIVE when THIS team is favoured. So the home side negates and the
    away side keeps the number - the opposite of `jobs.export_web.team_spread`, because
    nflverse `spread_line` is positive when the home team is favoured. c-14 measured
    it: corr(CFBD spread, home margin) = -0.713 over 13,738 scored games on the line
    this export selects, and every provider in `cfb_game_lines` is negative. Copying the
    NFL rule here published every CFB spread with the wrong sign (c-13, P1)."""
    if spread is None or home is None:
        return None
    if spread == 0:
        return 0.0                    # a pick'em; negating it would publish -0.0
    return -spread if home else spread


def game_lines(con):
    """game_id -> (spread, total). One provider per game, consensus preferred."""
    out = {}
    for gid, provider, spread, total in con.execute(
            "SELECT game_id, provider, spread, total FROM cfb_game_lines WHERE "
            "valid_to_ts IS NULL ORDER BY game_id, CASE provider WHEN 'consensus' THEN 0 "
            "ELSE 1 END, provider"):
        out.setdefault(gid, (spread, total))
    return out


def not_collected(con, team_ids, rates=None):
    """{box column: set(seasons)} the feed carries but did not RECORD.

    Measured over the EXPORTED teams' rows only. The feed's coverage of lower divisions
    grew over the archive, so a league-wide rate per game falls as more thin box scores
    arrive - the first version of this read 2026 interceptions as a hole for that reason.

    DETECTED, not typed. Each column's league-wide sum PER GAME WITH A BOX SCORE is
    compared with its own reference - the median of its three best seasons - and a
    season is a hole when:
      * the rate is under `HOLE_SHARE` of the reference: the silent-zero class, read as
        "effectively zero" rather than exactly zero (kick-return TDs sum to 0, 0, 0, 4, 0
        across 2004-2008 and an exact-zero test walks past 2007); or
      * the rate is under `PARTIAL_SHARE` of it AND the column is large enough that
        sampling noise cannot explain the gap (`PARTIAL_MIN_REFERENCE`): PARTIALLY
        collected - present, non-zero and still not a record of what happened. Solo
        tackles run 6,041 in 2016 against 65,152 in 2024; a 2016 team line would
        publish a tenth of its tackles as its tackles.
    The defensive columns are one feed category and share coverage, so a hole in any of
    them is a hole in all - a rare one (defensive TDs) is too small to show its own gap.

    Per GAME, not per season, so the season in progress is judged at its own length."""
    sums, games = defaultdict(dict), {}
    cols = ", ".join(f"SUM(ABS(COALESCE({c}, 0)))" for c in BOX_COLUMNS)
    marks = ",".join("?" * len(team_ids))
    for season, n, *vals in con.execute(
            f"SELECT season, COUNT(DISTINCT game_id || '-' || team_id), {cols} FROM "
            f"cfb_player_game_box WHERE valid_to_ts IS NULL AND season>=? AND team_id IN "
            f"({marks}) GROUP BY season", (STAT_ERA_FROM, *sorted(team_ids))):
        games[season] = n
        for c, v in zip(BOX_COLUMNS, vals):
            sums[c][season] = v or 0
    out = {}
    for c, by_season in sums.items():
        rate = {s: v / games[s] for s, v in by_season.items() if games[s]}
        # of the seasons that recorded anything: a column two seasons old must not read
        # as a hole everywhere because the third-best season is a zero
        best = sorted((r for r in rate.values() if r > 0), reverse=True)[:3]
        ref = best[len(best) // 2] if best else 0
        ref_sum = max(by_season.values()) if by_season else 0
        holes = set()
        if rates is not None:
            rates[c] = {s: (round(r / ref, 3) if ref else None) for s, r in rate.items()}
        for s, r in rate.items():
            if ref == 0 or r < HOLE_SHARE * ref:
                holes.add(s)
            elif r < PARTIAL_SHARE * ref and ref_sum >= PARTIAL_MIN_REFERENCE:
                holes.add(s)
        if holes:
            out[c] = holes
    shared = set().union(*(out.get(c, set()) for c in DEFENSIVE_CATEGORY))
    if shared:
        for c in DEFENSIVE_CATEGORY:
            out[c] = set(shared)
    return out


def _runs(seasons):
    """[2004, 2005, 2006, 2010] -> '2004-2006, 2010'."""
    out, run = [], []
    for s in sorted(seasons):
        if run and s == run[-1] + 1:
            run.append(s)
        else:
            if run:
                out.append(run)
            run = [s]
    if run:
        out.append(run)
    return ", ".join(str(r[0]) if len(r) == 1 else f"{r[0]}-{r[-1]}" for r in out)


def usage_by_game(con):
    """-> ({(game_id, athlete_id): targets}, {(game_id, team_id): team_targets}).

    The usage feed is play-by-play; a game it does not cover has no row for anyone, and
    that is "nobody recorded it" (null), not zero targets. `team_targets` is constant
    across a team's rows in one game and includes targets thrown to players the feed
    could not identify, which are in no player's row."""
    player, team = {}, {}
    for gid, tid, aid, tg, team_tg in con.execute(
            "SELECT game_id, team_id, athlete_id, targets, team_targets FROM "
            "cfb_player_game_usage WHERE valid_to_ts IS NULL"):
        player[(gid, aid)] = (player.get((gid, aid)) or 0) + (tg or 0)
        if team_tg is not None:
            team[(gid, tid)] = max(team.get((gid, tid), 0), team_tg)
    return player, team


def team_splits(con, team_ids, holes, team_targets):
    """(team_id, season, 'REG'|'POST') -> {'offense': Stats, 'defense': Stats, 'games': set}.

    OFFENCE is the team's own box. DEFENCE is the first producer's shape - the team's own
    defenders' box (`def_*`) - plus what its opponents produced against it (`*_allowed`),
    read from the same rows on the other side of each game. A column the feed did not
    collect in a season is NULL there (`not_collected`), never the zero it holds.

    Split by season type since a-72: this used to label every row REG while summing bowl
    games into it, which is a postseason total under a regular-season name."""
    per = defaultdict(lambda: {"offense": defaultdict(float), "defense": defaultdict(float),
                               "games": set()})
    cols = ", ".join(f"COALESCE(b.{c}, 0)" for c in BOX_COLUMNS)
    q = (f"SELECT g.season, g.season_type, b.team_id, g.game_id, g.home_id, g.away_id, {cols} "
         "FROM cfb_player_game_box b JOIN cfb_games g ON g.game_id=b.game_id AND "
         "g.valid_to_ts IS NULL WHERE b.valid_to_ts IS NULL AND g.season>=?")
    for row in con.execute(q, (STAT_ERA_FROM,)):
        season, stype, tid, gid, hid, aid, *vals = row
        v = dict(zip(BOX_COLUMNS, vals))
        gt = game_type(stype)
        if tid in team_ids:
            b = per[(tid, season, gt)]
            b["games"].add(gid)
            for key, col in TEAM_OFFENSE_BOX:
                b["offense"][key] += v[col]
            for key, col in TEAM_DEFENSE_BOX:
                b["defense"][key] += v[col]
        other = aid if tid == hid else hid
        if other in team_ids:
            b = per[(other, season, gt)]
            for key, col in ALLOWED_BOX:
                b["defense"][key] += v[col]
    out = {}
    for (tid, season, gt), b in per.items():
        if not b["games"]:
            continue                  # only an opponent's box exists: no split to state
        off = {k: _hole(b["offense"][k], c, season, holes) for k, c in TEAM_OFFENSE_BOX}
        deff = {k: _hole(b["defense"][k], c, season, holes)
                for k, c in TEAM_DEFENSE_BOX + ALLOWED_BOX}
        deff.update({k: None for k in TEAM_DEFENSE_ABSENT})
        # Team targets are the usage feed's. Stated only when EVERY game in the split is
        # covered - a sum over the covered games would be a lower bound that reads as a
        # total.
        tg = [team_targets.get((gid, tid)) for gid in b["games"]]
        off["targets"] = None if any(x is None for x in tg) else int(sum(tg))
        out[(tid, season, gt)] = {"offense": off, "defense": deff, "games": b["games"]}
    return out


def _hole(value, col, season, holes):
    if season in holes.get(col, ()):
        return None
    return int(value) if float(value).is_integer() else round(value, 1)


def team_points(con):
    """(team_id, season, 'REG'|'POST') -> (points for, points against), from the
    schedule rather than the box."""
    out = defaultdict(lambda: [0, 0])
    for season, stype, hid, aid, hp, ap in con.execute(
            "SELECT season, season_type, home_id, away_id, home_points, away_points FROM "
            "cfb_games WHERE valid_to_ts IS NULL AND season>=? AND home_points IS NOT NULL "
            "AND away_points IS NOT NULL", (STAT_ERA_FROM,)):
        gt = game_type(stype)
        out[(hid, season, gt)][0] += hp
        out[(hid, season, gt)][1] += ap
        out[(aid, season, gt)][0] += ap
        out[(aid, season, gt)][1] += hp
    return out


def memberships(con, team_id):
    """One row per season the feed holds for this team. Conference is a per-SEASON fact
    in this sport (finding C-3) and the feed holds it per season, so the history is real
    rather than today's value backdated. `division` is null: the feed's `division` column
    is the competitive tier under another name, which `classification` already carries."""
    return [{"season": s, "conference": conf, "division": None, "classification": cls}
            for s, conf, cls in con.execute(
                "SELECT season, conference_name, classification FROM cfb_teams WHERE "
                "team_id=? AND valid_to_ts IS NULL AND season>=? ORDER BY season",
                (team_id, STAT_ERA_FROM))]


def season_summaries(con, team_ids, season=CURRENT_SEASON):
    """{team_id: TeamSeasonSummary} for the current regular season, and the frame.

    The path and the record are the FIRST PRODUCER'S FUNCTIONS (`cumulative_path`,
    `reconcile_path`), handed this sport's games in the shape they read, so the teams
    board draws college by the rule it draws the NFL by and the two cannot drift.

    Two things college does that the frame was not written around, both measured in
    `research/cfb_scale.py` and neither hidden:
      * a team can play TWICE in one feed week (the feed's week 1 holds the season's
        opening two weekends). The path's week is then both games, and a week counts as
        played once any of its games has a score - scored games are ordered last so the
        resolver, which keeps one game per week, sees one.
      * most teams have TWO open weeks, and the frame's rule calls an unresolved bye a
        `gap`. So a college path reads `gap` where a reader would say bye. That is the
        contract's own definition, applied; it is not a defect in the data.
    `markets` is 0: no college market is priced by this pipeline."""
    reg = defaultdict(list)
    frame = 0
    for week, hid, aid, hp, ap in con.execute(
            "SELECT week, home_id, away_id, home_points, away_points FROM cfb_games WHERE "
            "valid_to_ts IS NULL AND season=? AND season_type='regular' AND week IS NOT NULL",
            (season,)):
        if hid not in team_ids and aid not in team_ids:
            continue
        frame = max(frame, week)
        scored = hp is not None and ap is not None
        g = {"week": week, "home_team": hid, "away_team": aid,
             "home_score": hp if scored else None, "away_score": ap if scored else None}
        for t in (hid, aid):
            if t in team_ids:
                reg[t].append(g)
    out = {}
    for tid in team_ids:
        games = sorted(reg.get(tid, []), key=lambda g: (g["week"], g["home_score"] is not None))
        path = cumulative_path(games, tid, frame)
        played = [e for e in path if e["state"] == "played"]
        last = played[-1] if played else None
        entry = {
            "games": (last["cleared"] + last["missed"] + last["tied"]) if last else 0,
            "cleared": last["cleared"] if last else 0,
            "missed": last["missed"] if last else 0,
            "tied": last["tied"] if last else 0,
            "points_for": last["points_for"] if last else None,
            "points_against": last["points_against"] if last else None,
            "markets": 0,
            "cumulative": path,
        }
        # Independently of the path: every scored regular-season game this team has. If
        # the two disagree a played game fell outside the frame, and neither is published.
        scored_games = [g for g in games if g["home_score"] is not None]
        if len(scored_games) != entry["games"]:
            raise ValueError(f"team {tid}: {len(scored_games)} scored regular-season games "
                             f"but the season path ends at {entry['games']}")
        reconcile_path(tid, entry)
        out[tid] = entry
    return out, frame


def roster_rows(con, team_id, pages, season=CURRENT_SEASON):
    """The season's roster with usage shares.

    `games` is NULL (finding C-4, and the contract's own wording): no public source
    records whether a college player dressed, and the only countable thing - games with
    a stat row - is a lower bound that reads as an appearance count. `snap_share` is null
    for the same reason. `pages` is {athlete_id: slug} for players in the export's scope."""
    usage = {r[0]: r[1:] for r in con.execute(
        "SELECT u.athlete_id, SUM(u.targets), SUM(u.rushes), SUM(u.team_targets), "
        "SUM(u.team_touches), COUNT(*) FROM cfb_player_game_usage u JOIN cfb_games g ON "
        "g.game_id=u.game_id AND g.valid_to_ts IS NULL WHERE u.valid_to_ts IS NULL AND "
        "g.season=? AND u.team_id=? GROUP BY u.athlete_id", (season, team_id))}
    out = []
    for aid, name, pos in con.execute(
            "SELECT athlete_id, full_name, position FROM cfb_rosters WHERE season=? AND "
            "team_id=? AND valid_to_ts IS NULL ORDER BY full_name", (season, team_id)):
        tg, rush, team_tg, team_touch, _n = usage.get(aid, (0, 0, 0, 0, 0))
        out.append({
            "season": season, "id": str(aid), "slug": pages.get(aid), "name": name,
            "position": pos,
            "games": None,
            "snap_share": None,
            "target_share": round(tg / team_tg, 4) if team_tg else None,
            "carry_share": round(rush / team_touch, 4) if team_touch else None,
            "has_page": aid in pages,
        })
    return out


# ---------------------------------------------------------------------------
# players
# ---------------------------------------------------------------------------

JERSEY = re.compile(r"[0-9]{1,2}")


def fold(name):
    """A search alias: lower case, ASCII, single spaces."""
    import unicodedata
    s = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


def player_scope(con, team_ids, from_season=None):
    """{athlete_id} with an offensive box row for an exported team from `from_season`.

    Ids at or below zero are the feed's placeholders for a player it could not identify
    (`research/cfb_scale.py` counts them) and name no one, so they are never in scope."""
    from_season = PLAYER_SCOPE_FROM if from_season is None else from_season
    marks = ",".join("?" * len(team_ids))
    usage = " + ".join(f"COALESCE({c}, 0)" for c in OFFENSE_COLUMNS)
    return {r[0] for r in con.execute(
        f"SELECT DISTINCT athlete_id FROM cfb_player_game_box WHERE valid_to_ts IS NULL "
        f"AND athlete_id > 0 AND season >= ? AND team_id IN ({marks}) AND ({usage}) > 0",
        (from_season, *sorted(team_ids)))}


def roster_identity(con, scope):
    """athlete_id -> {'name', 'position', 'headshot', 'jerseys': {season: n}} from the
    roster feed, newest season winning. A player the roster never lists has no entry:
    his name then comes from the box and his position is null."""
    out = {}
    for aid, season, name, pos, jersey, head in con.execute(
            "SELECT athlete_id, season, full_name, position, jersey, headshot_url FROM "
            "cfb_rosters WHERE valid_to_ts IS NULL ORDER BY season"):
        if aid not in scope:
            continue
        e = out.setdefault(aid, {"name": None, "position": None, "headshot": None,
                                 "jerseys": {}})
        e["name"] = name or e["name"]
        e["position"] = pos or e["position"]
        if head and str(head).startswith("https://"):
            e["headshot"] = head
        j = None if jersey is None else str(jersey).strip()
        if j and JERSEY.fullmatch(j):
            e["jerseys"][season] = j
    return out


def _sum(periods, key):
    """A total over periods: NULL if any period that carries the key is null. A sum over
    the recorded games would be a lower bound wearing a total's name."""
    vals = [p["stats"][key] for p in periods if key in p["stats"]]
    if not vals:
        return "absent"
    if any(v is None for v in vals):
        return None
    return sum(vals)


def _totals(periods):
    out = {}
    for key in PLAYER_CANDIDATES:
        v = _sum(periods, key)
        if v != "absent":
            out[key] = v
    out["snap_share_mean"] = None          # declared absent: STAT_ABSENT
    return out


def build_players(con, scope, team_abbr_of, exported_abbrs, holes, generated_at):
    """-> ({key: obj}, [index entries], unresolved, {athlete_id: slug}, census).

    The first producer's player kinds, key for key: `summary.json` and one file per
    season, period rows carrying the row's own key set. Three things are this sport's:

      * SLUGS NEED NO REGISTRY. `{name}-{athlete id}` is unique by construction and
        stable for as long as the feed's id is, so there is no namesake rule to get
        wrong and no append-only file that a change of scope would strand entries in.
        College has far too many namesakes for a bare-name slug to mean anyone.
      * `team` on a row is the team's abbreviation, as the first producer writes it.
        `identity.team` is the latest row's team ONLY when that team is exported - the
        templates look it up in `manifest.teams` and `team_colors` by that code.
      * what cannot be answered is null and named in `absences`: snaps and snap share on
        every row, `market`, `prop_history`."""
    rosters = roster_identity(con, scope)
    targets, team_targets = usage_by_game(con)
    cols = ", ".join(f"b.{c}" for _, c in PLAYER_BOX) + ", b.pr_td"
    rows = defaultdict(list)
    for r in con.execute(
            f"SELECT b.athlete_id, b.athlete_name, b.team_id, g.game_id, g.season, g.week, "
            f"g.season_type, g.start_ts, g.home_id, g.away_id, g.playoff_bowl_name, "
            f"g.playoff_round_name, {cols} FROM cfb_player_game_box b JOIN cfb_games g ON "
            "g.game_id=b.game_id AND g.valid_to_ts IS NULL WHERE b.valid_to_ts IS NULL AND "
            "b.athlete_id > 0 AND g.season >= ? ORDER BY g.season, g.start_ts, g.game_id",
            (STAT_ERA_FROM,)):
        if r[0] in scope:
            rows[r[0]].append(r)

    files, index, unresolved, pages = {}, [], [], {}
    census = {"null_target_rows": 0, "rows": 0, "null_target_totals": 0, "totals": 0,
              "no_roster": 0, "team_not_exported": 0}
    for aid in sorted(rows):
        prs = rows[aid]
        ros = rosters.get(aid)
        if ros is None:
            census["no_roster"] += 1
        name = (ros or {}).get("name") or next(
            (r[1] for r in reversed(prs) if r[1]), None)
        sid = str(aid)
        if not name:
            unresolved.append({"id": sid, "name": None, "reason": REASON_NO_NAME})
            continue
        slug = f"{slugify(name)}-{sid}".strip("-")
        pages[aid] = slug
        periods = []
        for (_a, _n, tid, gid, season, week, stype, ts, hid, away, bowl, rnd_name,
             *vals) in prs:
            home = tid == hid
            stats = {"snaps": None, "snap_share": None}
            covered = (gid, tid) in team_targets
            tg = targets.get((gid, aid), 0) if covered else None
            team_tg = team_targets.get((gid, tid))
            stats["target_share"] = round(tg / team_tg, 4) if covered and team_tg else None
            stats["targets"] = tg
            for (key, col), v in zip(PLAYER_BOX, vals):
                stats[key] = None if season in holes.get(col, ()) else (v or 0)
            # A return TD is a kick-return or a punt-return one; null where either
            # column is a hole, since a sum over the recorded half is a lower bound.
            if any(season in holes.get(c, ()) for c in RET_TD_COLUMNS):
                stats["ret_td"] = None
            else:
                stats["ret_td"] += vals[-1] or 0
            for key in PLAYER_ABSENT:
                stats[key] = None
            census["rows"] += 1
            census["null_target_rows"] += tg is None
            periods.append({
                "season": season, "index": week if week is not None else 0,
                "label": period_label(week, stype, bowl, rnd_name),
                "season_type": game_type(stype),
                "game_id": str(gid),
                "date": time.strftime("%Y-%m-%d", time.gmtime(ts)) if ts else None,
                "team": team_abbr_of.get(tid),
                "opponent": team_abbr_of.get(away if home else hid),
                "home": home,
                "stats": stats,
            })
        by_season = defaultdict(list)
        for p in periods:
            by_season[p["season"]].append(p)
        for ps in by_season.values():
            keep = set(emitted_keys([p["stats"] for p in ps], PLAYER_CANDIDATES)) | set(USAGE_KEYS)
            for p in ps:
                p["stats"] = {k: v for k, v in p["stats"].items() if k in keep}
        season_entries = []
        for season in sorted(by_season):
            ps = by_season[season]
            key = f"{SPORT}/players/{sid}/{season}.json"
            files[key] = {**envelope("player_season", generated_at, SPORT),
                          "identity": {"id": sid, "slug": slug, "name": name},
                          "season": season, "periods": ps}
            teams_ = []
            for p in ps:
                if p["team"] is not None and p["team"] not in teams_:
                    teams_.append(p["team"])
            season_entries.append({"season": season, "teams": teams_, "games": len(ps),
                                   "key": key,
                                   "jersey_number": (ros or {}).get("jerseys", {}).get(season)})
        totals = []
        grouped = defaultdict(list)
        for p in periods:
            grouped[(p["season"], p["season_type"])].append(p)
        for (season, st) in sorted(grouped, key=lambda k: (k[0], k[1] != "REG")):
            t = _totals(grouped[(season, st)])
            census["totals"] += 1
            census["null_target_totals"] += t.get("targets", 0) is None
            totals.append({"season": season, "season_type": st,
                           "games": len(grouped[(season, st)]), "stats": t})
        reg = [p for p in periods if p["season_type"] == "REG"]
        latest_team = team_abbr_of.get(prs[-1][2])
        if latest_team not in exported_abbrs:
            census["team_not_exported"] += 1
            latest_team = None
        aliases = sorted({a for a in (fold(name), fold(prs[-1][1])) if a})
        last_season = prs[-1][4]
        files[f"{SPORT}/players/{sid}/summary.json"] = {
            **envelope("player_summary", generated_at, SPORT),
            "identity": {"id": sid, "slug": slug, "name": name,
                         "position": (ros or {}).get("position"),
                         "team": latest_team,
                         # `gsis` is NOT here yet: the store's draft crosswalk
                         # (cfb_player_xwalk) would link a college page to the same
                         # player's NFL one, and it is an nflverse release this sport's
                         # source registry does not declare. Absent, not null - absent
                         # means not produced, null would claim it is not known.
                         "ids": {"espn": sid},
                         "aliases": aliases,
                         "headshot_url": (ros or {}).get("headshot"),
                         "jersey_number": (ros or {}).get("jerseys", {}).get(last_season)},
            "seasons": season_entries,
            "season_totals": totals,
            "career": {"season_type": "REG", "games": len(reg), "stats": _totals(reg)},
            "market": None,
            "prop_history": None,
        }
        index.append({"id": sid, "slug": slug, "name": name,
                      "position": (ros or {}).get("position"), "team": latest_team,
                      "first_season": prs[0][4], "last_season": last_season,
                      "aliases": aliases, "has_market": False})
    index.sort(key=lambda p: (p["name"] or "", p["id"]))
    return files, index, unresolved, pages, census


# ---------------------------------------------------------------------------
# declared absences
# ---------------------------------------------------------------------------

def absences(holes):
    """What this sport cannot answer, as the manifest publishes it: a contract path, a
    state and the reason. A page reading a null or an empty list at `path` words its
    empty state from `reason` instead of guessing one. Reasons are the store's own
    limitation rows where one exists (`cfb_limitations`), so the wording has one home."""
    lim = {x["id"]: x["statement"] for x in limitations.LIMITATIONS}
    no_appearance = lim.get("cfb.stats_and_usage_only") or NO_SNAPS
    out = [
        {"path": "player_season.periods[].stats.snaps", "state": "not_collected",
         "reason": NO_SNAPS, "limitation": "cfb.stats_and_usage_only"},
        {"path": "player_season.periods[].stats.snap_share", "state": "not_collected",
         "reason": NO_SNAPS, "limitation": "cfb.stats_and_usage_only"},
        {"path": "player_summary.season_totals[].stats.snap_share_mean",
         "state": "not_collected", "reason": NO_SNAPS,
         "limitation": "cfb.stats_and_usage_only"},
        {"path": "team.roster[].snap_share", "state": "not_collected", "reason": NO_SNAPS,
         "limitation": "cfb.stats_and_usage_only"},
        {"path": "team.roster[].games", "state": "not_collected",
         "reason": "No appearance record exists for a college player; games with a stat "
                   "row would be a lower bound that reads as an appearance count.",
         "limitation": "cfb.stats_and_usage_only"},
        {"path": "player_summary.prop_history", "state": "not_published",
         "reason": no_appearance, "limitation": "cfb.stats_and_usage_only"},
        {"path": "player_summary.market", "state": "not_published",
         "reason": "No college player market is priced by this pipeline.",
         "limitation": None},
        {"path": "sport_manifest.market_definitions", "state": "not_published",
         "reason": "No college prop history is published, so no market key is defined.",
         "limitation": "cfb.stats_and_usage_only"},
        {"path": "sport_manifest.scoring_presets", "state": "not_published",
         "reason": "No fantasy scoring is applied to college components here.",
         "limitation": None},
        {"path": "sport_manifest.teams[].division", "state": "not_applicable",
         "reason": "The sport groups teams by conference; the feed's division column is "
                   "the competitive tier, which classification carries.",
         "limitation": None},
        {"path": "team.coaches", "state": "not_collected",
         "reason": "No college coach feed is ingested.", "limitation": None},
        {"path": "team.schedule[].coach", "state": "not_collected",
         "reason": "No college coach feed is ingested.", "limitation": None},
        {"path": "player_season.periods[].stats.targets", "state": "partial",
         "reason": "Targets come from play-by-play, not the box score. A game the "
                   "play-by-play feed does not cover carries null, and a total over a "
                   "span with such a game is null rather than a lower bound.",
         "limitation": "cfb.no_targets_in_box_score"},
    ]
    out += [
        {"path": "team.schedule[].opponent_coach", "state": "not_collected",
         "reason": "No college coach feed is ingested.", "limitation": None},
        {"path": "player_summary.career.stats.snap_share_mean", "state": "not_collected",
         "reason": NO_SNAPS, "limitation": "cfb.stats_and_usage_only"},
        {"path": "sport_manifest.current.source_version", "state": "not_applicable",
         "reason": "The college store versions each row by when it was ingested; there is "
                   "no single upstream release tag to name.", "limitation": None},
    ]
    # OPTIONAL manifest keys the first sport fills. They cannot be published empty - the
    # contract types them and has no null for them - so they are absent and SAID to be.
    out += [
        {"path": "sport_manifest.denominators", "state": "not_published",
         "reason": "No participation denominators are computed for college football: "
                   "they count who played, and no source records that.",
         "limitation": "cfb.stats_and_usage_only"},
        {"path": "sport_manifest.metrics", "state": "not_published",
         "reason": "No college figure is in the metric registry yet.", "limitation": None},
        {"path": "sport_manifest.main_line_definition", "state": "not_applicable",
         "reason": "A main line is defined for prop history, and college publishes none.",
         "limitation": "cfb.stats_and_usage_only"},
    ]
    for key in TEAM_DEFENSE_ABSENT + PLAYER_ABSENT:
        out.append({"path": f"stats.{key}", "state": "not_collected",
                    "reason": "The college box score feed has no column for this stat.",
                    "limitation": None})
    keys_of = defaultdict(list)
    for k, c in PLAYER_BOX + TEAM_OFFENSE_BOX + TEAM_DEFENSE_BOX + ALLOWED_BOX:
        if k not in keys_of[c]:
            keys_of[c].append(k)
    keys_of["pr_td"].append("ret_td")
    seen = set()
    for col in sorted(holes):
        for key in keys_of[col]:
            if (key, col) in seen:
                continue
            seen.add((key, col))
            out.append({"path": f"stats.{key}", "state": "partial",
                        "reason": f"The box score feed did not record {col} in "
                                  f"{_runs(holes[col])}: the column is present there and "
                                  "zero or a fraction of a season, and is published null "
                                  "for those seasons.",
                        "limitation": None})
    for a in out:
        a.setdefault("limitation", None)
    return out


# ---------------------------------------------------------------------------
# build
# ---------------------------------------------------------------------------

def build(con, generated_at=None, player_from=None):
    generated_at = generated_at or iso()
    ts = teams(con)
    ids = {t["team_id"] for t in ts}
    lines = game_lines(con)
    holes = not_collected(con, ids)
    _targets, team_targets = usage_by_game(con)
    splits = team_splits(con, ids, holes, team_targets)
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
    # THE TEMPLATES KEY ON THE ABBREVIATION - colours, the player's team chip, the
    # analytics subject - so two exported teams sharing one is two teams at one identity.
    # 0 of 138 today; refuse the day that stops being true rather than publish it.
    codes = [t["abbr"] for t in ts]
    clash = sorted({a for a in codes if a is None or codes.count(a) > 1}, key=str)
    if clash:
        raise ValueError(f"exported teams without a unique abbreviation: {clash}")

    slugs = slug_map(con, ts)
    scope = player_scope(con, ids, player_from)
    player_files, index, unresolved, pages, census = build_players(
        con, scope, abbrs, set(codes), holes, generated_at)
    files.update(player_files)
    summaries, frame = season_summaries(con, ids)

    for t in ts:
        sched = schedule_rows(con, t["team_id"], lines, abbrs, blank_abbrs, slugs)
        team_seasons = sorted({s["season"] for s in sched})
        sp = []
        for season in team_seasons:
            for gt in ("REG", "POST"):
                b = splits.get((t["team_id"], season, gt))
                if not b:
                    continue
                pf, pa = points.get((t["team_id"], season, gt), (0, 0))
                sp.append({"season": season, "season_type": gt, "games": len(b["games"]),
                           "offense": {"points": pf, **b["offense"]},
                           "defense": {"points_allowed": pa, **b["defense"]}})
        files[f"{SPORT}/teams/{t['slug']}.json"] = {
            **envelope("team", generated_at, SPORT),
            "identity": {"slug": t["slug"], "abbr": t["abbr"], "name": t["name"]},
            "memberships": memberships(con, t["team_id"]),
            "seasons": team_seasons,
            "schedule": sched,
            "splits": sp,
            "roster": roster_rows(con, t["team_id"], pages),
            "coaches": [],
        }

    files[f"{SPORT}/players/index.json"] = {
        **envelope("player_index", generated_at, SPORT),
        "players": index,
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
                   "season": summaries[t["team_id"]]}
                  for t in ts],
        "team_colors": color_map,
        "counts": {
            "players": len(index),
            "teams": len(ts),
            "market": 0,
            "games": played,
            "rungs": 0,
        },
        "unresolved_ids": unresolved,
        "absences": absences(holes),
    }

    return files, {"dropped_colors": dropped_colors, "blank_opponent_abbrs": blank_abbrs,
                   "stale": stale, "holes": holes, "frame": frame, "census": census,
                   "players": len(index), "scope": len(scope)}


OWNED_PREFIX = f"{SPORT}/"


def export(out_dir, dry_run=False, verbose=True):
    """Build, validate and write the CFB tree into `out_dir`. -> (files, notes).

    The write goes through `sync_keys` over `cfb/` and nothing else: this builder owns
    that prefix and fills all of it, so a team that leaves FBS is deleted from the tree
    rather than left behind as a page for a team the manifest no longer lists. The
    prefix is a literal at the call site so `tests/test_prefix_ownership.owned_prefixes`
    can read it."""
    con = ro()
    source_registry.watch(con, SPORT)    # a-30: sync_keys refuses after an unmapped read
    try:
        files, notes = build(con)
    finally:
        con.close()
    stray = sorted(k for k in files if not k.startswith(OWNED_PREFIX))
    if stray:
        # A key outside the owned prefix would be written by sync_keys and never cleaned
        # up by it - and if it is another producer's key, it is two builders on one key.
        raise ValueError(f"the CFB export built keys outside {OWNED_PREFIX}: {stray}")
    assert_stats_defined(files, files[f"{SPORT}/manifest.json"]["stat_definitions"])
    written, deleted = sync_keys(out_dir, files, [f"{SPORT}/"], dry_run)   # validates
    notes = {**notes, "written": written, "deleted": deleted}
    if verbose:
        m = files[f"{SPORT}/manifest.json"]
        print(f"  files {len(files)}  written {written}  deleted {deleted}  "
              f"{'(dry run, nothing on disk)' if dry_run else out_dir}")
        print(f"  teams {len(m['teams'])}  seasons {len(m['seasons'])}  "
              f"colours {len(m['team_colors'])}  "
              f"colour rows dropped to an abbreviation collision "
              f"{len(notes['dropped_colors'])}")
        print(f"  opponents with no abbreviation anywhere in the feeds: "
              f"{len(notes['blank_opponent_abbrs'])}  (emitted null, never invented)")
        print(f"  current.stale {notes['stale']}")
        c = notes["census"]
        print(f"  players {notes['players']} of {notes['scope']} in scope "
              f"(season >= {PLAYER_SCOPE_FROM}); {len(m['unresolved_ids'])} excluded with no "
              f"name; {c['no_roster']} never on a roster (position null); "
              f"{c['team_not_exported']} whose latest team is not exported (team null)")
        print(f"  player rows {c['rows']}, targets null on {c['null_target_rows']} "
              f"(no play-by-play for the game); season totals {c['totals']}, targets null "
              f"on {c['null_target_totals']}")
        print(f"  season frame {notes['frame']} weeks; absences declared {len(m['absences'])}")
        for col in sorted(notes["holes"]):
            print(f"    not recorded: {col} in {_runs(notes['holes'][col])}")
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

C-5  CLOSED BY a-72 ON THE PRODUCER SIDE: this export now publishes the player kinds
     for players in scope (`PLAYER_SCOPE_FROM`), so the paragraph below describes the
     tree before a-72. What it says about the contract still holds.
     THE PLAYER INDEX IS THE PAGE LIST, so a sport cannot publish players without
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
    ap.add_argument("--dest", choices=("own", "web"), default="own",
                    help="own: stage to <STORAGE_DIR>/cfb/web_export (default, reaches "
                         "nothing). web: write cfb/ into WEB_EXPORT_DIR - the PUBLISH "
                         "decision, because the scheduled uploader sends that whole tree")
    ap.add_argument("--out", default=None,
                    help="with --dest own only: stage somewhere else")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--findings", action="store_true")
    ap.add_argument("--absences", action="store_true",
                    help="print what the manifest declares absent, and why; writes nothing")
    a = ap.parse_args(argv)
    if a.findings:
        print(FINDINGS)
        return 0
    if a.absences:
        con = ro()
        try:
            ids = {t["team_id"] for t in teams(con)}
            for x in absences(not_collected(con, ids)):
                print(f"{x['state']:<14} {x['path']}\n{'':<15}{x['reason']}")
        finally:
            con.close()
        return 0
    if a.dest == "web":
        if a.out:
            ap.error("--out is for staging; --dest web writes to WEB_EXPORT_DIR")
        out = require_setting("WEB_EXPORT_DIR")
    else:
        out = a.out or paths.root("web_export")
    print(f"CFB export -> {'WEB_EXPORT_DIR (publishing tree)' if a.dest == 'web' else 'staging'}")
    export(out, dry_run=a.dry_run)
    if a.dest == "web" and not a.dry_run:
        # THE DECLARATION, gated on the write exactly as track F gates theirs: a staging
        # run or a dry run rebuilt nothing in the publishing tree and authorises nothing.
        # Last line, so a caller in the same job can hand it to the uploader.
        print(REFRESHED_SENTINEL + " " + OWNED_PREFIX)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
