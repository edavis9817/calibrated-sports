"""Export the website's data - contract v2 (docs/web-schema.md).

    python -m jobs.export_web                   # export everything to WEB_EXPORT_DIR
    python -m jobs.export_web --only players    # players | teams | market | research | manifest
    python -m jobs.export_web --only components --dest D:/staging   # league table, staged
    python -m jobs.export_web --dry-run         # build and count, write nothing
    python -m jobs.export_web --upload          # export, then upload changed keys to R2
    python -m jobs.export_web --upload-only     # upload the existing local export

Keys are sport-first (`nfl/players/{id}/summary.json`, ...) and the local export
directory mirrors them exactly, so the R2 upload is a straight copy of changed
files. The contract's rules, enforced here:

  * stat_definitions live ONLY in the sport manifest, and every stat key used
    in any file must be defined there (asserted at export)
  * no fantasy points are stored - components only; scoring_presets are data
  * period_type replaces "week"; postseason periods are labelled from game_type
  * slugs are stable within the sport: a newcomer never renames an existing page

Reads the store read-only. Writes are atomic and happen only when content
changed (ignoring generated_at). Keys that fall out of scope are deleted locally
and, on the next upload, remotely. Missing R2 credentials skip the upload with a
log line and exit 0 - a pending token must not break the weekly job.
"""
import argparse
import bisect
import hashlib
import json
import os
import random
import re
import sqlite3
import statistics
import sys
import time
import unicodedata
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from jsonschema import Draft202012Validator

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
import store  # noqa: E402
from core import settlement as ST  # noqa: E402
from core import stats as core_stats  # noqa: E402
from jobs.publish_live_prices import LIVE_PREFIX  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# THE contract, and it is executable. This exporter validates everything it
# writes against it; the site generates its TypeScript types from the same
# document. docs/web-schema.md is the prose description of this file, not a
# second source of truth. Missing or unreadable is a hard import failure - an
# export that cannot check itself must not run.
CONTRACT_PATH = os.path.join(ROOT, "web", "contract", "v2", "contract.schema.json")
with open(CONTRACT_PATH, encoding="utf-8") as _f:
    CONTRACT = json.load(_f)
SCHEMA_VERSION = CONTRACT["x-contract"]["schema_version"]
SPORT = "nfl"
SPORT_NAME = "NFL"
PERIOD_TYPE = "week"
PARTS = ("players", "teams", "market", "research", "manifest", "components")
# Empty. `components` moved into PARTS on 2026-09-24 - the publish decision a-11
# left to Ethan, taken because /nfl/analytics renders 64 marked blanks without it
# and the site's reader (b-17) is ready to merge behind it.
#
# ONE WAY, measured by a-13: the first publish is all 28 seasons, 27 MB raw /
# 3.1 MB gzip, then normally one file a run (+31 s, ~16% on a default export).
# Taking a part back OUT of PARTS does NOT remove what it published: no run would
# declare `nfl/components/` again, and `upload()` only deletes inside declared
# prefixes, so the objects would stay in R2 with nothing to clean them up.
OPTIONAL_PARTS = ()
STATE_FILE = ".upload_state.json"

# The one line an export prints so a caller in the SAME JOB can learn which
# prefixes it rebuilt and hand them to the uploader. Producer (`main`) and
# consumer (`parse_refreshed`) live in this file together so they cannot drift;
# a test drives the real one into the real other with no fake in between,
# because two fictions can agree with each other while neither matches reality.
REFRESHED_SENTINEL = "REFRESHED"
# The same record, mirrored into the bucket it describes. Local-only meant that
# losing the machine cost a 22,927-object re-upload instead of one download.
# The `_state/` prefix is deliberately unreachable through the site's /data/
# route - sanitizeKey requires every path segment to START with an alphanumeric,
# so an underscore-led segment is refused (asserted in the site's tests).
REMOTE_STATE_KEY = "_state/upload_state.json"
# The committed slug registry (docs/web-schema.md): id -> slug, append-only.
SLUG_DIR = os.path.join(ROOT, "web", "slugs")
UPLOAD_WORKERS = 8

# nflverse keeps the abbreviation of the era in nfl_games; player-weeks already
# use the franchise's current one. Team keys use the current one, lower-cased.
FRANCHISE = {"OAK": "LV", "SD": "LAC", "STL": "LA"}
TEAM_NAMES = {
    "ARI": "Arizona Cardinals", "ATL": "Atlanta Falcons", "BAL": "Baltimore Ravens",
    "BUF": "Buffalo Bills", "CAR": "Carolina Panthers", "CHI": "Chicago Bears",
    "CIN": "Cincinnati Bengals", "CLE": "Cleveland Browns", "DAL": "Dallas Cowboys",
    "DEN": "Denver Broncos", "DET": "Detroit Lions", "GB": "Green Bay Packers",
    "HOU": "Houston Texans", "IND": "Indianapolis Colts", "JAX": "Jacksonville Jaguars",
    "KC": "Kansas City Chiefs", "LA": "Los Angeles Rams", "LAC": "Los Angeles Chargers",
    "LV": "Las Vegas Raiders", "MIA": "Miami Dolphins", "MIN": "Minnesota Vikings",
    "NE": "New England Patriots", "NO": "New Orleans Saints", "NYG": "New York Giants",
    "NYJ": "New York Jets", "PHI": "Philadelphia Eagles", "PIT": "Pittsburgh Steelers",
    "SEA": "Seattle Seahawks", "SF": "San Francisco 49ers", "TB": "Tampa Bay Buccaneers",
    "TEN": "Tennessee Titans", "WAS": "Washington Commanders",
}
POST_LABELS = {"WC": "Wild Card", "DIV": "Divisional", "CON": "Conference", "SB": "Super Bowl"}

# nfl_player_week column -> contract stat key
STAT_MAP = (("targets", "targets"), ("receptions", "rec"), ("receiving_yards", "rec_yds"),
            ("receiving_tds", "rec_td"), ("carries", "rush_att"), ("rushing_yards", "rush_yds"),
            ("rushing_tds", "rush_td"), ("attempts", "pass_att"), ("completions", "pass_cmp"),
            ("passing_yards", "pass_yds"), ("passing_tds", "pass_td"), ("interceptions", "int"),
            # Mapped 2026-09-16. These were published as NULL since v1 under the
            # note "not projected by nfl_player_week" - true of the table, and
            # the wrong conclusion: nflverse has carried all three since 1999
            # and the ingest simply never selected them. Standard scoring in
            # essentially every league, so a custom-scoring product cannot omit
            # them.
            ("fumbles_lost", "fum_lost"), ("two_pt_conversions", "two_pt"),
            ("return_tds", "ret_td"))
# Nothing is unmapped any more. Kept as an empty tuple rather than deleted: the
# concept is real - a source that cannot supply a component must publish null
# rather than a silent zero - and the next sport will need it.
MISSING_COMPONENTS = ()
COUNT_KEYS = tuple(k for _, k in STAT_MAP) + MISSING_COMPONENTS
PERIOD_KEYS = ("snaps", "snap_share", "targets", "target_share", "rec", "rec_yds", "rec_td",
               "rush_att", "rush_yds", "rush_td", "pass_att", "pass_cmp", "pass_yds", "pass_td",
               "int", "fum_lost", "two_pt", "ret_td")
DEF_COLUMNS = (("def_tkl_solo", "def_tackles_solo"), ("def_tkl_with_assist", "def_tackles_with_assist"),
               ("def_tkl_ast", "def_tackle_assists"), ("def_tfl", "def_tackles_for_loss"),
               ("def_sacks", "def_sacks"), ("def_qb_hits", "def_qb_hits"),
               ("def_int", "def_interceptions"), ("def_pd", "def_pass_defended"),
               ("def_ff", "def_fumbles_forced"), ("def_td", "def_tds"),
               ("def_safeties", "def_safeties"))


# =============================================================================
# THE EXTENDED PROFILE (a-14): defence, special teams and identity on players
# =============================================================================
#
# Track B's A-B2, A-B3 and A-B4 (docs/track-a-requests.md). STAGED, NOT
# PUBLISHED: built only with `--extended`, which main() refuses without `--dest`,
# so neither the weekly refresh nor an --upload can produce these keys. The
# weekly refresh runs from whatever branch the working clone has checked out and
# uploads what it builds - so "merged but not published" has to be a property of
# the code, not of anyone remembering. Making this the default is the publish
# decision, and it is Ethan's (the a-11 precedent for `components`).
#
# The default export is untouched by everything below: every extended path is
# reached only through an `ExtendedInputs`, and the default passes None.

# Published key -> nfl_player_week column. The published keys for defence are
# the ones team splits already use (DEF_COLUMNS), so a player's line and his
# team's line share one vocabulary.
ST_MAP = (("fg_att", "fg_att"), ("fg_made", "fg_made"), ("fg_missed", "fg_missed"),
          ("fg_blocked", "fg_blocked"),
          ("fg_made_0_19", "fg_made_0_19"), ("fg_made_20_29", "fg_made_20_29"),
          ("fg_made_30_39", "fg_made_30_39"), ("fg_made_40_49", "fg_made_40_49"),
          ("fg_made_50_59", "fg_made_50_59"), ("fg_made_60_plus", "fg_made_60_"),
          ("fg_missed_0_19", "fg_missed_0_19"), ("fg_missed_20_29", "fg_missed_20_29"),
          ("fg_missed_30_39", "fg_missed_30_39"), ("fg_missed_40_49", "fg_missed_40_49"),
          ("fg_missed_50_59", "fg_missed_50_59"), ("fg_missed_60_plus", "fg_missed_60_"),
          ("pat_att", "pat_att"), ("pat_made", "pat_made"),
          ("punt_ret", "punt_returns"), ("punt_ret_yds", "punt_return_yards"),
          ("kick_ret", "kickoff_returns"), ("kick_ret_yds", "kickoff_return_yards"))
# (published key, nfl_player_week column), in period-row order.
EXT_STAT_MAP = DEF_COLUMNS + ST_MAP
EXT_COUNT_KEYS = tuple(k for k, _ in EXT_STAT_MAP)
# Phase snaps from nfl_snap_counts. NOT in totals - `snaps` is not totalled
# either, and a season's snap count is the game log's business.
PHASE_SNAP_KEYS = ("defense_snaps", "st_snaps")
EXT_PERIOD_KEYS = PHASE_SNAP_KEYS + EXT_COUNT_KEYS
# published extended key -> source column, for collected(). Phase snaps are
# governed by SNAP_FIRST_SEASON instead.
EXT_SOURCE_OF = {k: c for k, c in EXT_STAT_MAP}


# =============================================================================
# STAGED FEATURES (a-15): the current period's fixtures, air yards, red zone
# =============================================================================
#
# Track B's A-B5 and A-B8. Same gate as the extended profile, and for the same
# reason: the weekly refresh runs this exporter from whatever branch the working
# clone has checked out and uploads what it builds (Task Scheduler, measured
# 2026-09-23 - still `code\calibrated-sports`, not `code\prod`). So each feature
# is reachable only with `--stage NAME --dest DIR`, and publishing one is a code
# change: adding its name to DEFAULT_STAGES. Separate names rather than folding
# into --extended, because the extended profile is an 11,000-player scope
# change and these are not; one should not have to wait on the other.
#
#   fixtures  current.fixtures[] on the sport manifest (A-B5)
#   air_rz    rec_air_yds, rz_targets, rz_rush_att on player period rows,
#             totals and (with --only components) the components table (A-B8)
STAGES = ("fixtures", "air_rz")
DEFAULT_STAGES = ()

# published key -> source. rec_air_yds is a stats_player_week column; the two
# red-zone keys are derived from play-by-play into nfl_pbp_looks.
AIR_RZ_KEYS = ("rec_air_yds", "rz_targets", "rz_rush_att")
AIR_RZ_SOURCE_OF = {"rec_air_yds": "receiving_air_yards", "rz_targets": "rz_targets",
                    "rz_rush_att": "rz_rush_att"}


# =============================================================================
# THE SILENT-ZERO CLASS
# =============================================================================
#
# A column that is PRESENT, POPULATED AND ZERO for a run of seasons. No null
# check can see it: `nonnull` is 1.000 throughout, so every guard passes and a
# mean returns a number that is simply wrong.
#
# The contract already requires the fix. `Stats` is `{"type": ["number","null"]}`
# and its own description reads "null means unknown or not collected, never
# zero" - so emitting 0 here violated the intent the contract states, and
# publishing null needs no contract change.
#
# SWEPT, NOT GUESSED, AND SWEPT ON THE RIGHT QUESTION. Track F swept the nflverse
# release (`analytics.survey --silent-zeros`, docs/F01-pbp-survey.md 2.9) and
# found 13 columns. The question here is different and smaller - which columns
# THIS EXPORT PUBLISHES are affected - and the answer is three, measured against
# `nfl_player_week` on 2026-09-17. The other ten are in neither STAT_MAP nor
# DEF_COLUMNS and reach no page.
#
# The runs are declared here rather than imported from `analytics`: that package
# is track F's, reads its own `analytics.db`, and is explicitly scoped out of
# this file. Importing it would make the export refuse to run unless another
# track's database had been scanned. `tests/test_silent_zeros.py` cross-checks
# this table against their sweep when that database exists, so the two cannot
# drift in silence - which is the real risk of declaring it twice.
#
# EFFECTIVELY ZERO, NEVER EXACTLY ZERO. League targets for 2003-2008 are
# 3, 5, 0, 67, 14, 17 - an exact-zero test walks past five of the six seasons,
# and track F's first version did exactly that and missed the defect it was
# named after.
NOT_COLLECTED = {
    # source column           inclusive season runs
    "targets": ((2003, 2008),),
    # Mostly NULL in the hole rather than zero (60 non-null rows of 17,211 in
    # 2003, none at all in 2005), so it degrades honestly except for a residue
    # that would otherwise render as a genuine usage share inside a gap.
    "target_share": ((2003, 2008),),
    # The longest run in the archive, and it was unknown until swept: 1,796
    # player-weeks in 2002, 0 through nine seasons, 1,843 in 2012.
    "def_tackles_for_loss": ((2003, 2011),),
    "def_qb_hits": ((2003, 2005),),
    # a-15 (A-B8), staged. Track F's sweep finds exactly this run, 2003-2008.
    # The 1999-2002 run is declared separately below: it is not zero.
    "receiving_air_yards": ((2003, 2008),),
}

# PARTIALLY COLLECTED: present, populated, NON-zero - and still not a record of
# what happened. A zero sweep cannot see this class by construction, which is
# why it is a separate table and why track F's anti-drift test compares only
# NOT_COLLECTED. Measured 2026-09-23 on the raw archive:
#   receiving_air_yards 1999-2002: league sums 11,104 / 8,197 / 8,370 / 7,889
#   on 440-506 non-zero player-weeks, against 138-160k on ~4,100 from 2009,
#   while play-by-play carries air_yards on 0% of those seasons' passes. About
#   6% of a season, from an unknown source - a receiver with 100 targets would
#   publish 0 air yards. Null, not a number that looks like a small one.
PARTIAL_COLLECTION = {
    "receiving_air_yards": ((1999, 2002),),
}

# Derived here from play-by-play, not a stats_player_week column, so outside
# both tables above (and outside track F's weekly_stats sweep). rz_targets is a
# subset of targets, and targets 2003-2008 cannot be rebuilt: the receiver is
# named on completions only (0.0-0.4% of incompletions, measured 2026-09-23),
# so a red-zone target count there would be red-zone RECEPTIONS under another
# name. rz_rush_att has no hole - carries reconcile in every season.
DERIVED_NOT_COLLECTED = {
    "rz_targets": ((2003, 2008),),
}

# published stat key -> the nfl_player_week column behind it. `_totals` works in
# published keys and the coverage table is keyed by source column.
SOURCE_OF = {key: col for col, key in STAT_MAP}


def collected(col, season):
    """Did the source record this column in this season?

    False means NOT COLLECTED - a fact about the feed, not about the player.
    Callers must emit null, never 0.
    """
    for table in (NOT_COLLECTED, PARTIAL_COLLECTION, DERIVED_NOT_COLLECTED):
        for lo, hi in table.get(col, ()):
            if lo <= season <= hi:
                return False
    return True


# =============================================================================
# PER-ROW KEY SETS: a row carries what the player's production justifies
# =============================================================================
#
# One fixed key list for every player means a receiver's page carries eleven
# defensive zeros and a linebacker's carries the receiving block. Worse, the
# played-zero path MANUFACTURES them - `{k: 0 for k in PERIOD_KEYS}` writes
# zeros that were never in the source - which is the silent-zero defect
# self-inflicted, one day after removing the last one.
#
# The populations are mostly disjoint, measured 2026-09-17: 759 players
# offensive-only, 6,856 defensive-only, 3,233 both. So this is not a tidying
# preference; it is the difference between a page about a player and a page
# about the sport's stat vocabulary.
#
# THE RULE: emit a key if ANY period has a NON-ZERO value or a NULL for it;
# omit it only if it is zero in every period.
#
# THE NULL CLAUSE IS LOAD-BEARING. `null` means "unknown or not collected" -
# 2003-08 targets, 2003-2011 def_tfl - and the page renders that absence as
# "not recorded". Dropping the key would turn a documented gap back into
# silence, which is the exact defect the silent-zero work removed.
#
# USAGE KEYS ARE EXEMPT, for two different reasons:
#   * `snaps` / `snap_share` - a played-zero row is DEFINED by snaps > 0 with
#     every stat zero. Applying the rule would delete the one key proving he
#     played and leave a row asserting nothing.
#   * `target_share` - the site's usage frame reads `stats[key] ?? null` and
#     renders null as "not recorded for {season}". An ABSENT key is
#     indistinguishable from a null one there, so dropping it for a player with
#     genuinely zero targets would publish "Tgt % is not recorded", which is
#     false. Absent means "not applicable to this player"; null means "nobody
#     recorded it". Until the site can tell them apart, the frame's series stay.
USAGE_KEYS = ("snaps", "snap_share", "target_share")
STAT_CANDIDATES = tuple(k for k in PERIOD_KEYS if k not in USAGE_KEYS)


def emitted_keys(stat_dicts, candidates=STAT_CANDIDATES):
    """The subset of `candidates` this player's own production justifies.

    Order follows `candidates`, so a re-export of unchanged data is
    byte-identical and `write_if_changed` stays quiet.
    """
    out = []
    for key in candidates:
        for stats in stat_dicts:
            if key not in stats:
                continue                 # never carried: not evidence of anything
            value = stats[key]
            # `is None` before the comparison: a null is KEPT. And `!= 0` rather
            # than truthiness, because rushing yards can be negative and `if -7`
            # is the kind of falsy that silently deletes a real figure.
            if value is None or value != 0:
                out.append(key)
                break
    return tuple(out)


def _d(label, fmt, group, higher=True):
    return {"label": label, "format": fmt, "group": group, "higher_is_better": higher}


STAT_DEFINITIONS = {
    "snaps": _d("Snaps", "int", "usage"),
    "snap_share": _d("Snap %", "pct", "usage"),
    "snap_share_mean": _d("Avg snap %", "pct", "usage"),
    "targets": _d("Tgt", "int", "receiving"),
    "target_share": _d("Tgt %", "pct", "usage"),
    "rec": _d("Rec", "int", "receiving"),
    "rec_yds": _d("Rec Yds", "int", "receiving"),
    "rec_td": _d("Rec TD", "int", "receiving"),
    "rush_att": _d("Car", "int", "rushing"),
    "rush_yds": _d("Rush Yds", "int", "rushing"),
    "rush_td": _d("Rush TD", "int", "rushing"),
    "pass_att": _d("Att", "int", "passing"),
    "pass_cmp": _d("Cmp", "int", "passing"),
    "pass_yds": _d("Pass Yds", "int", "passing"),
    "pass_td": _d("Pass TD", "int", "passing"),
    "int": _d("INT", "int", "passing", higher=False),
    "fum_lost": _d("Fum Lost", "int", "misc", higher=False),
    "two_pt": _d("2-Pt", "int", "misc"),
    # special_teams_tds ALONE since a-14 (2026-09-23). It used to add
    # pt_return_tds, which is the PUNTER's column - touchdowns allowed on his
    # punts - and credited 300 punter-weeks on 93 pages with a return TD. See
    # normalize_weekly_stats.
    "ret_td": _d("Ret TD", "int", "scoring"),
    "td": _d("TD", "int", "scoring"),
    # team offense
    "points": _d("Pts", "int", "team_offense"),
    "int_thrown": _d("INT Thrown", "int", "team_offense", higher=False),
    # team defense
    "points_allowed": _d("Pts Allowed", "int", "team_defense", higher=False),
    "def_tkl_solo": _d("Solo Tkl", "int", "team_defense"),
    "def_tkl_with_assist": _d("Tkl w/ Ast", "int", "team_defense"),
    "def_tkl_ast": _d("Ast Tkl", "int", "team_defense"),
    "def_tfl": _d("TFL", "int", "team_defense"),
    "def_sacks": _d("Sacks", "dec1", "team_defense"),
    "def_qb_hits": _d("QB Hits", "int", "team_defense"),
    "def_int": _d("INT", "int", "team_defense"),
    "def_pd": _d("PD", "int", "team_defense"),
    "def_ff": _d("FF", "int", "team_defense"),
    "def_td": _d("Def TD", "int", "team_defense"),
    "def_safeties": _d("Safeties", "int", "team_defense"),
    # The DENOMINATORS of the two usage shares, published so a page can compute
    # a share over any span as sum(part) / sum(whole). Averaging the per-game
    # shares instead weights a 20-snap game the same as a 70-snap one, which is
    # a different number and the wrong one. Carried only on the components
    # table; see build_components.
    "team_targets": _d("Team Tgt", "int", "usage"),
    "team_snaps": _d("Team Snaps", "int", "usage"),
}

# The extended profile's additions (a-14). Merged into the manifest ONLY by an
# extended export, so the published manifest does not move until the profile
# does. The eleven def_* keys are NOT redefined here: they already exist above,
# in group `team_defense`, and a player's defensive line reuses them - moving
# them to a new group would move the team page too, which is track B's call.
EXT_STAT_DEFINITIONS = {
    "defense_snaps": _d("Def Snaps", "int", "usage"),
    "st_snaps": _d("ST Snaps", "int", "usage"),
    "fg_att": _d("FGA", "int", "kicking"),
    "fg_made": _d("FGM", "int", "kicking"),
    # Blocked kicks are NOT misses: fg_att = fg_made + fg_missed + fg_blocked.
    "fg_missed": _d("FG Missed", "int", "kicking", higher=False),
    "fg_blocked": _d("FG Blocked", "int", "kicking", higher=False),
    "fg_made_0_19": _d("FGM 0-19", "int", "kicking"),
    "fg_made_20_29": _d("FGM 20-29", "int", "kicking"),
    "fg_made_30_39": _d("FGM 30-39", "int", "kicking"),
    "fg_made_40_49": _d("FGM 40-49", "int", "kicking"),
    "fg_made_50_59": _d("FGM 50-59", "int", "kicking"),
    "fg_made_60_plus": _d("FGM 60+", "int", "kicking"),
    # Missed by band, so a band reads made/(made+missed). Blocked kicks carry no
    # distance in the source, so that is attempts LESS blocks, not attempts.
    "fg_missed_0_19": _d("FG Miss 0-19", "int", "kicking", higher=False),
    "fg_missed_20_29": _d("FG Miss 20-29", "int", "kicking", higher=False),
    "fg_missed_30_39": _d("FG Miss 30-39", "int", "kicking", higher=False),
    "fg_missed_40_49": _d("FG Miss 40-49", "int", "kicking", higher=False),
    "fg_missed_50_59": _d("FG Miss 50-59", "int", "kicking", higher=False),
    "fg_missed_60_plus": _d("FG Miss 60+", "int", "kicking", higher=False),
    "pat_att": _d("XPA", "int", "kicking"),
    "pat_made": _d("XPM", "int", "kicking"),
    "punt_ret": _d("PR", "int", "returns"),
    "punt_ret_yds": _d("PR Yds", "int", "returns"),
    "kick_ret": _d("KR", "int", "returns"),
    "kick_ret_yds": _d("KR Yds", "int", "returns"),
}

# The page renders `label` and `description`, not this comment - so the
# definition of a red-zone look lives in the description, word for word what
# was counted.
AIR_RZ_STAT_DEFINITIONS = {
    "rec_air_yds": {**_d("Air Yds", "int", "receiving"),
                    "description": "Receiving air yards: the distance the ball travelled past the "
                                   "line of scrimmage on every pass thrown to this player, caught or "
                                   "not, as nflverse credits it (receiving_air_yards). Negative on a "
                                   "pass caught behind the line. Not recorded before 2009."},
    "rz_targets": {**_d("RZ Tgt", "int", "receiving"),
                   "description": "Red-zone targets: passes thrown to this player with the ball at "
                                  "or inside the opponent's 20-yard line at the snap. Counted from "
                                  "play-by-play on the same plays as Tgt, so it is never larger; "
                                  "two-point attempts excluded. Not recorded 2003-2008, when "
                                  "incomplete passes name no receiver."},
    "rz_rush_att": {**_d("RZ Car", "int", "rushing"),
                    "description": "Red-zone carries: rushing attempts by this player with the ball "
                                   "at or inside the opponent's 20-yard line at the snap. Counted "
                                   "from play-by-play on the same plays as Car, kneel-downs "
                                   "included; two-point attempts excluded."},
}


def _m(label, stat, note=None):
    return {"label": label, "stat": stat, **({"note": note} if note else {})}


# THE MARKET VOCABULARY, WHICH IS NOT THE STAT VOCABULARY. Measured 2026-09-18
# on the real export: all 8 of these names appear in `prop_history` across 672
# players and 25,529 records, and NOT ONE is a `stat_definitions` key - so the
# site could render a hit rate and had no way to say what the rate was about.
# The producer already spelled the same claim two ways: a market file's
# components carry `rec` and `rush_att`, while prop_history carries `receptions`
# and `rush_attempts`.
#
# `stat` IS NULL WHERE NO PUBLISHED STAT IS THE SAME CLAIM, and every null here
# is measured rather than assumed. Scanning every shape a stat key can appear in
# - period rows, season totals, career, team splits and market components - puts
# `def_sacks` and `def_tkl_ast` in TEAM SPLITS ONLY, with no player-level
# counterpart. That is the same fact as the 681 defenders who have settled props
# and no page. A null is a real state, not a gap to fill in later: pointing a
# market at a near-miss key would publish a false equivalence, which is the
# failure the contract exists to prevent.
MARKET_DEFINITIONS = {
    "receptions": _m("Receptions", "rec"),
    "receiving_yards": _m("Receiving Yards", "rec_yds"),
    "rush_attempts": _m("Rush Attempts", "rush_att"),
    "rush_yards": _m("Rushing Yards", "rush_yds"),
    "passing_yards": _m("Passing Yards", "pass_yds"),
    "sacks": _m("Sacks", None,
                "def_sacks is published only inside team splits; this sport exports no "
                "player-level defensive stats yet"),
    "tackles_assists": _m("Tackles + Assists", None,
                          "settles on the SUM of def_tkl_solo, def_tkl_with_assist and "
                          "def_tkl_ast - no single published key is the same claim, and all "
                          "three are team-splits only"),
    "anytime_td": _m("Anytime TD", None,
                     "a binary claim - did he score at all - while `td` is a count. The two "
                     "are not the same question and pointing one at the other would state "
                     "something false"),
}

# Under the extended profile a player's defensive line IS exported, so the sacks
# market has a published key making the same claim: settlement reads
# COALESCE(def_sacks, 0) (jobs/settle_outcomes.py), and def_sacks is that
# column. tackles_assists stays null - it settles on a SUM of three keys, and no
# single key is that claim, extended or not.
EXT_MARKET_DEFINITIONS = {
    **MARKET_DEFINITIONS,
    "sacks": _m("Sacks", "def_sacks"),
    "tackles_assists": _m("Tackles + Assists", None,
                          "settles on the SUM of def_tkl_solo, def_tkl_with_assist and "
                          "def_tkl_ast - all three are published per player, and no single "
                          "key is the same claim"),
}

_BASE_WEIGHTS = {"rec": 1, "rec_yds": 0.1, "rec_td": 6, "rush_yds": 0.1, "rush_td": 6,
                 "pass_yds": 0.04, "pass_td": 4, "int": -2, "fum_lost": -2, "two_pt": 2,
                 # A return touchdown is 6 points in essentially every league,
                 # and nflverse's own fantasy_points_ppr credits it: measured
                 # +6.00 exactly on every 2025 row where a receiver or back
                 # returned one. Mapping ret_td without weighting it would have
                 # left it silently worth zero, which is the failure mode this
                 # project keeps finding - a column that exists, flows through,
                 # and scores nothing.
                 "ret_td": 6}
SCORING_PRESETS = {
    "ppr": {"label": "PPR", "weights": dict(_BASE_WEIGHTS), "bonuses": []},
    "half": {"label": "Half PPR", "weights": dict(_BASE_WEIGHTS, rec=0.5), "bonuses": []},
    "standard": {"label": "Standard", "weights": dict(_BASE_WEIGHTS, rec=0), "bonuses": []},
}
# research/implied.py names its scorings differently; the distributions keep the
# manifest's preset keys.
IMPLIED_SCORING = {"ppr": "ppr", "half": "half_ppr", "standard": "standard"}
SCORING_NOTE_BASE = ("Scored from the components present, which now include fumbles lost, "
                     "two-point conversions and return touchdowns.")

# Reasons published in the manifest's unresolved_ids. Both are exclusions of a
# sort, and both are counted in the export summary on every run: an exclusion
# nobody can see is indistinguishable from a bug.
HOLD_REASON = "published in the site export"
REASON_NO_NAME = "no resolvable name - excluded from the export"
REASON_NO_TEAM = "period row with no team - dropped from that season's team list"

# A rung is a step function: ~143 quotes carry 35-59 changes. Change-point
# encoding is therefore lossless AND smaller than any curve downsample. The cap
# exists only for a volatile game-day market; over it, the smallest changes are
# omitted and counted, never interpolated.
PATH_MAX_POINTS = 60

SNAP_FIRST_SEASON = 2013
CDF_X = tuple(range(0, 51))
THRESHOLDS = (5, 10, 15, 20, 25, 30)
N_SIMS = 4000
MARKET_STATS = {"receptions": "KXNFLREC", "rush_attempts": "KXNFLRSHATT"}
VALIDATION = {
    "status": ("The method was validated on sportsbook ladders, 2023-25 (q90 coverage 0.101 "
               "against 0.100). The Kalshi-ladder arm used here has not been independently "
               "validated."),
    "source": "CLAUDE.md, market-implied fantasy distribution",
}
MARKET_SOURCE = {
    "venue": "kalshi",
    "method": ("research/implied.py arm A: mid of each rung, no de-vig (exchange); isotonic "
               "survival fit; Gaussian copula receptions↔receiving yards; Monte Carlo"),
    "n_sims": N_SIMS,
}
TTK_BUCKETS = (">72h", "24-72h", "6-24h", "1-6h", "0-1h", "in-game")
EXEC_SERIES = ("KXNFLREC", "KXNFLRSHATT", "KXNFLSPREAD", "KXNFLTOTAL", "KXNFLGAME")
# Values CLAUDE.md records for brief 022 H3 (the H3 module prints these; it does
# not register them). Anything CLAUDE.md does not record is null.
EXEC_TOUCH = {"KXNFLREC": {"1-6h": 50, "in-game": 2}, "KXNFLRSHATT": {"1-6h": 3, "in-game": 1},
              "KXNFLSPREAD": {"1-6h": 7943, "in-game": 89}}
EXEC_RATIO = {"CHAMP": 3.88, "WINSWEEK": 2.76, "KXNFLRSHATT": 2.51, "WINS": 2.13,
              "DIVISION": 1.18, "KXNFLREC": 1.05, "KXNFLTOTAL": 0.31, "KXNFLSPREAD": 0.30,
              "KXNFLGAME": 0.30}
EXEC_RULE = "Cross game lines 1-6h before kickoff; never cross a prop in-game."

# (regex on the key, kind, sport) - DERIVED from the contract's own key table.
# A second hand-maintained copy here would be exactly the drift surface the
# contract exists to close.
_SPORTLESS_KINDS = set(CONTRACT["x-contract"]["sportless_kinds"])
KIND_BY_KEY = tuple((re.compile(k["pattern"]), k["kind"],
                     None if k["kind"] in _SPORTLESS_KINDS else "sport")
                    for k in CONTRACT["x-contract"]["keys"])


class ConfigError(RuntimeError):
    """A required setting is missing. Refuse rather than guess."""


class StatDefinitionError(AssertionError):
    """A stat key is used in a file but not defined in the sport manifest."""


class MarketDefinitionError(AssertionError):
    """A MARKET key is used in a file but not defined in the sport manifest.

    A sibling of StatDefinitionError rather than a reuse of it, because the two
    name different vocabularies and a reader of the failure needs to know which
    one is short.
    """


class ContractError(AssertionError):
    """A file does not match web/contract/v2/contract.schema.json for its kind."""


# =============================================================================
# small pure pieces
# =============================================================================

def require_setting(name):
    value = getattr(config, name, None)
    if not value:
        raise ConfigError(f"config.{name} is not set - put {name}=... in .env. "
                          "The web export has no defaults, by design.")
    return value


def iso(ts=None):
    ts = time.time() if ts is None else ts
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def envelope(kind, generated_at, sport=SPORT):
    return {"schema_version": SCHEMA_VERSION, "generated_at": generated_at, "kind": kind,
            "sport": sport}


def rnd(x, dp=4):
    if x is None:
        return None
    if isinstance(x, float):
        return round(x, dp)
    return x


def num(x):
    return 0 if x is None else x


def intish(x):
    """Stats are stored REAL; counts read better as integers."""
    if x is None:
        return None
    return int(x) if float(x).is_integer() else round(float(x), 2)


def team_slug(abbr):
    return None if abbr is None else FRANCHISE.get(abbr, abbr).lower()


def team_spread(spread_line, home):
    """nflverse spread_line is POSITIVE when the HOME team is favoured. From a
    team's own perspective, positive means THIS team is favoured."""
    if spread_line is None or home is None:
        return None
    return spread_line if home else -spread_line


def result_of(points_for, points_against):
    if points_for is None or points_against is None:
        return None
    return "W" if points_for > points_against else "L" if points_for < points_against else "T"


def period_label(game_type, index, season_type=None):
    if game_type in POST_LABELS:
        return POST_LABELS[game_type]
    if game_type == "REG" or (game_type is None and season_type == "REG"):
        return f"Week {index}"
    return f"Postseason week {index}"


def period_key(season, index):
    return f"{season}-{index}"


def slugify(text):
    if not text:
        return None
    s = unicodedata.normalize("NFKD", str(text)).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s or None


class SlugRegistryError(RuntimeError):
    """The committed slug registry is corrupt (a slug assigned to two ids)."""


def slug_registry_path(sport=SPORT):
    """web/slugs/{sport}.json in this repo. Read at call time so tests can point
    SLUG_DIR elsewhere."""
    return os.path.join(SLUG_DIR, f"{sport}.json")


def check_registry(registry):
    seen = {}
    for pid, slug in registry.items():
        if slug in seen:
            raise SlugRegistryError(f"slug {slug!r} is assigned to both {seen[slug]} and {pid}")
        seen[slug] = pid


def load_slug_registry(path):
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as f:
        registry = json.load(f)
    check_registry(registry)
    return registry


def write_slug_registry(path, registry):
    """One entry per line, sorted by id, so a diff shows exactly what was added."""
    check_registry(registry)
    lines = [f"{json.dumps(pid)}: {json.dumps(registry[pid], ensure_ascii=False)}"
             for pid in sorted(registry)]
    body = "{\n" + ",\n".join(lines) + ("\n" if lines else "") + "}\n"
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(body)
    os.replace(tmp, path)


def assign_slugs(entries, registry=None):
    """{id: {"name", "first_season", "reg_games"}} + registry -> (registry', added).

    Registry entries are NEVER changed. Only ids absent from the registry get a
    slug. Namesakes arriving together are ranked by most regular-season career
    games, then earliest first_season, then lowest id: the top one takes the
    bare slug if it is free; everyone else gets `-{first_season}`, then
    `-{last 4 of id}`. Every bare slug that is about to be handed out is
    reserved before any suffix, so a suffix never equals another player's bare
    or suffixed slug. Seeding is this same function on an empty registry."""
    registry = dict(registry or {})
    check_registry(registry)
    used = set(registry.values())
    groups = defaultdict(list)
    for pid, e in entries.items():
        if pid in registry:
            continue
        base = slugify(e.get("name")) or slugify(pid)
        groups[base].append((-(e.get("reg_games") or 0), e.get("first_season") or 9999, pid))
    added = {}
    for base in sorted(groups):
        groups[base].sort()
        if base not in used:
            pid = groups[base][0][2]
            added[pid] = base
            used.add(base)
    for base in sorted(groups):
        for _neg_games, fs, pid in groups[base]:
            if pid in added:
                continue
            pslug = slugify(pid) or "x"
            for cand in (f"{base}-{fs}", f"{base}-{pslug[-4:]}", f"{base}-{pslug}"):
                if cand not in used:
                    used.add(cand)
                    added[pid] = cand
                    break
            else:
                raise SlugRegistryError(f"no free slug for {pid} ({base})")
    registry.update(added)
    check_registry(registry)
    return registry, added


def price_path(rows, cap=PATH_MAX_POINTS):
    """One rung's price history as change-points. -> (points, dropped) or (None, 0).

    LOSSLESS, not a downsample. A rung carries ~143 quotes but only 35-59 actual
    changes and 11-19 distinct values - it is a step function, so keeping the
    first point, every change and the last reproduces the series exactly in
    roughly a third of the points. A curve-fitting downsample would reposition
    points onto triangle-optimal picks and turn a discrete jump into a slope,
    which is the one thing a repricing chart must not do.

    The cap bounds a volatile game-day market. Over it, the SMALLEST-magnitude
    changes go first and the count is published: omitting a 1c wobble is honest,
    inventing a point is not, so nothing here ever interpolates. The first and
    last points are never dropped - they anchor the span.
    """
    if not rows:
        return None, 0
    pts = [{"ts": ts, "p": rnd(p)} for ts, p in rows if p is not None]
    if not pts:
        return None, 0
    keep = [0] + [i for i in range(1, len(pts)) if pts[i]["p"] != pts[i - 1]["p"]]
    if keep[-1] != len(pts) - 1:
        keep.append(len(pts) - 1)
    dropped = 0
    while len(keep) > cap:
        # Interior only; index 0 and the last point anchor first/last_quote_ts.
        interior = keep[1:-1]
        smallest = min(interior, key=lambda i: abs(pts[i]["p"] - pts[i - 1]["p"]))
        keep.remove(smallest)
        dropped += 1
    return [pts[i] for i in keep], dropped


def build_price_path(con, rungs, until_ts):
    """The ONE series a market publishes: the rung nearest the posted line.

    A threshold ladder posts no single line, so "the line" is the rung the
    market itself treats as the median - the one whose mid sits nearest 0.50.
    Nine overlaid rungs would be an unreadable chart, and the survival curve
    already shows every rung at one instant, which is the complementary view.
    """
    if not rungs:
        return None
    r = min(rungs, key=lambda x: abs((x["bid"] + x["ask"]) / 2.0 - 0.5))
    rows = con.execute(
        "SELECT ts, (best_bid + best_ask) / 2.0 FROM quotes WHERE venue = 'kalshi' "
        "AND market_id = ? AND ts < ? AND best_bid IS NOT NULL AND best_ask IS NOT NULL "
        "ORDER BY ts", (r["market_id"], until_ts)).fetchall()
    points, dropped = price_path(rows)
    if not points:
        return None
    return {"stat": r["stat"], "line": r["line"], "market_id": r["market_id"],
            # Derived from the series, never asserted: these markets happen to
            # start at listing, which will not generalise.
            "first_quote_ts": points[0]["ts"], "last_quote_ts": points[-1]["ts"],
            "raw_points": len(rows), "dropped": dropped, "points": points}


def distribution_summary(sims):
    """Sorted simulated totals -> cdf / thresholds / quantiles, per the contract."""
    s = sorted(sims)
    n = len(s)
    cdf = [{"x": x, "p_at_most": rnd(bisect.bisect_right(s, x) / n)} for x in CDF_X]
    thr = [{"points": t, "p_at_least": rnd((n - bisect.bisect_left(s, t)) / n)} for t in THRESHOLDS]

    def q(p):
        return rnd(s[min(int(p * (n - 1)), n - 1)], 2)
    return {"cdf": cdf, "thresholds": thr,
            "quantiles": {"q10": q(0.10), "q25": q(0.25), "q50": q(0.50),
                          "q75": q(0.75), "q90": q(0.90)}}


def kind_for_key(key):
    for rx, kind, sport in KIND_BY_KEY:
        if rx.match(key):
            return kind, sport
    return None, None


def stat_keys_used(obj):
    """Every stat key a contract file uses, by kind."""
    kind = obj.get("kind")
    keys = set()
    if kind == "player_season":
        for p in obj.get("periods", []):
            keys |= set(p.get("stats", {}))
    elif kind == "player_summary":
        for t in obj.get("season_totals", []):
            keys |= set(t.get("stats", {}))
        keys |= set((obj.get("career") or {}).get("stats", {}))
    elif kind == "team":
        for s in obj.get("splits", []):
            keys |= set(s.get("offense", {})) | set(s.get("defense", {}))
    elif kind == "market":
        keys |= {c["stat"] for c in obj.get("components", [])}
    elif kind == "components":
        keys |= set(obj.get("columns", []))
    elif kind == "sport_manifest":
        for p in obj.get("scoring_presets", {}).values():
            keys |= set(p.get("weights", {}))
            keys |= {b["stat"] for b in p.get("bonuses", [])}
    return keys


def assert_stats_defined(files, definitions):
    missing = defaultdict(set)
    for key, obj in files.items():
        for k in stat_keys_used(obj) - set(definitions):
            missing[k].add(key)
    if missing:
        detail = "; ".join(f"{k} (e.g. {sorted(v)[0]})" for k, v in sorted(missing.items()))
        raise StatDefinitionError(f"stat keys used but not in stat_definitions: {detail}")


def market_keys_used(obj):
    """Every MARKET key a contract file uses, by kind.

    Separate from `stat_keys_used` on purpose: markets and stats are different
    vocabularies, so one lookup table cannot serve both without one of the two
    answers being wrong.

    THIS IS THE SHAPE THE OTHER GUARD DOES NOT WALK, and that was not a
    suspicion - it was demonstrated on 2026-09-18, both answers on both inputs.
    `stat_keys_used` has five branches (player_season, player_summary, team,
    market, sport_manifest) and its player_summary branch reads only
    `season_totals[].stats` and `career.stats`. A planted
    `totally_not_a_real_stat_key` inside `prop_history` was ACCEPTED by
    `assert_stats_defined`, while the same key in `career.stats` RAISED. So
    25,529 published records had never been checked against any vocabulary,
    while `docs/web-schema.md` asserted that every stat key in every file was.
    A guard asserts only over the shapes it walks.
    """
    keys = set()
    if obj.get("kind") == "player_summary":
        history = obj.get("prop_history") or {}
        keys |= {s["stat"] for s in history.get("stats", [])}
        keys |= {r["stat"] for r in history.get("records", [])}
    return keys


def assert_markets_defined(files, definitions):
    missing = defaultdict(set)
    for key, obj in files.items():
        for k in market_keys_used(obj) - set(definitions):
            missing[k].add(key)
    if missing:
        detail = "; ".join(f"{k} (e.g. {sorted(v)[0]})" for k, v in sorted(missing.items()))
        raise MarketDefinitionError(f"market keys used but not in market_definitions: {detail}")


_VALIDATORS = None


def contract_validators():
    """One compiled validator per kind, built once from the contract document."""
    global _VALIDATORS
    if _VALIDATORS is None:
        defs = CONTRACT["$defs"]
        _VALIDATORS = {kind: Draft202012Validator({"$ref": f"#/$defs/{name}", "$defs": defs})
                       for kind, name in CONTRACT["x-contract"]["kinds"].items()}
    return _VALIDATORS


def validate_contract(files, limit=12):
    """Every file must match the contract for the kind its key implies.

    This is what makes the contract executable rather than aspirational. The
    site's types are generated from the same document, so a field added,
    dropped or re-typed here fails the export instead of reaching a page as a
    200 with something broken underneath. The contract closes its objects, so
    an ADDITIVE field fails too - deliberately: it must be added to the
    contract in the same commit, which is what regenerates the site's types.
    """
    vs = contract_validators()
    problems = []
    for key, obj in sorted(files.items()):
        kind, _ = kind_for_key(key)
        if kind is None:
            problems.append(f"{key}: no kind matches this key in the contract's key table")
            continue
        if obj.get("kind") != kind:
            problems.append(f"{key}: the key implies kind {kind!r}, the file says {obj.get('kind')!r}")
            continue
        for e in vs[kind].iter_errors(obj):
            loc = "/".join(str(p) for p in e.absolute_path) or "(root)"
            problems.append(f"{key}: {loc}: {e.message}")
    if problems:
        shown = "\n  ".join(problems[:limit])
        more = f"\n  ... and {len(problems) - limit} more" if len(problems) > limit else ""
        raise ContractError(
            f"{len(problems)} contract violation(s) against "
            f"{os.path.relpath(CONTRACT_PATH, ROOT).replace(os.sep, '/')}:\n  {shown}{more}")


def cache_control(key):
    short = key == "sports.json" or key.endswith("/manifest.json") or key.endswith("/index.json")
    return "public, max-age=60" if short else "public, max-age=300"


# =============================================================================
# loading
# =============================================================================

def ro():
    return sqlite3.connect(f"file:{config.DB_PATH}?mode=ro", uri=True)


def _dicts(cur):
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def load_games(con):
    rows = _dicts(con.execute(
        "SELECT g.* FROM nfl_games g JOIN (SELECT game_id, MAX(data_version) dv FROM nfl_games "
        "GROUP BY game_id) v ON v.game_id = g.game_id AND v.dv = g.data_version"))
    games = {}
    for g in rows:
        g["home_team"] = FRANCHISE.get(g["home_team"], g["home_team"])
        g["away_team"] = FRANCHISE.get(g["away_team"], g["away_team"])
        games[g["game_id"]] = g
    return games


def game_index(games):
    idx = {}
    for g in games.values():
        for team in (g["home_team"], g["away_team"]):
            idx[(g["season"], g["week"], team)] = g
    return idx


def load_player_weeks(con):
    return _dicts(con.execute(
        "SELECT w.* FROM nfl_player_week w JOIN (SELECT gsis_id, season, week, season_type, "
        "MAX(data_version) dv FROM nfl_player_week GROUP BY gsis_id, season, week, season_type) v "
        "ON v.gsis_id = w.gsis_id AND v.season = w.season AND v.week = w.week "
        "AND v.season_type = w.season_type AND v.dv = w.data_version "
        "ORDER BY w.gsis_id, w.season, w.week"))


def load_xwalk(con):
    xw = {r["gsis_id"]: r for r in _dicts(con.execute(
        "SELECT * FROM player_xwalk WHERE sport = ?", (SPORT,)))}
    aliases = defaultdict(set)
    for alias, gsis in con.execute("SELECT alias, gsis_id FROM player_alias WHERE sport = ?",
                                   (SPORT,)):
        aliases[gsis].add(alias)
    return xw, aliases


def valid_headshot(url):
    """https with a non-empty host, or None. Hotlinked only - never fetched."""
    if not url or not isinstance(url, str):
        return None
    from urllib.parse import urlparse
    try:
        p = urlparse(url.strip())
    except ValueError:
        return None
    return url.strip() if p.scheme == "https" and p.netloc else None


def load_headshots(con):
    """gsis -> the most recent VALID headshot URL (latest season, then week).
    An invalid latest URL falls back to the newest valid one."""
    try:
        rows = con.execute(
            "SELECT gsis_id, season, week, headshot_url FROM player_headshot WHERE sport = ? "
            "ORDER BY gsis_id, season DESC, week DESC", (SPORT,)).fetchall()
    except sqlite3.OperationalError:          # table absent on an old store
        return {}
    out = {}
    for gsis, _season, _week, url in rows:
        if gsis in out:
            continue
        ok = valid_headshot(url)
        if ok:
            out[gsis] = ok
    return out


# The markets the research settled on: "Priority markets: targets, rush attempts,
# receptions. Yards below them, TDs and QB props near zero." Emitted as a
# `priority` flag rather than used to filter, because suppressing the stat with
# the DEEPEST record (receiving yards) to flatter the model's own scope would be
# its own dishonesty. The site words the ordering; this decides it.
PRIORITY_PROPS = ("receptions", "rush_attempts", "targets")


def load_prop_history(con, scope):
    """gsis -> {"stats": [...], "records": [...]} for players in `scope`.

    A FACT, NOT A PICK. The editorial line permits hit-rate history - "Jefferson
    is 11-6 to the over" is research - and forbids recommendations. Nothing here
    forecasts.

    IT NEEDS NO CLOSING PRICE, which is why it covers the current season. The
    posted `line` and the settled `result` are sufficient, and `line` is non-null
    on 100% of settled rows in every season. `outcome_close` has no 2026 rows at
    all, so anything comparing to the market stops at 2025 while this does not.

    EACH MARKET IS COUNTED ONCE, AND THE RULE IS NOT COPIED HERE. A settled prop
    exists twice in `outcomes` - once as the over, once as the under - carrying
    the SAME market-level result. Pooling leaves the rate right and doubles `n`,
    so the interval comes out about sqrt(2) too narrow and the error survives
    review. The deduplication key and the counting both live in `core.stats`;
    this function collapses to one row per market and hands it over.
    """
    by_market = {}
    # LATEST data_version only, like every other read in this repo. 171 outcomes
    # carry two settlement rows; today they agree on `result` (measured, 0
    # disagreements), so taking either was harmless - but "harmless because the
    # duplicates happen to agree" is not a rule, and a re-derive that changed a
    # result would make the published record depend on row ordering.
    for season, week, gsis, stat, line, side, result in con.execute(
            "SELECT o.season, o.week, o.entity_id, o.stat, o.line, o.side, s.result "
            "FROM outcome_settlement s JOIN outcomes o ON o.outcome_id = s.outcome_id "
            "JOIN (SELECT outcome_id, MAX(data_version) dv FROM outcome_settlement "
            "      GROUP BY outcome_id) latest "
            "  ON latest.outcome_id = s.outcome_id AND latest.dv = s.data_version "
            "WHERE o.entity_type = 'player' AND s.result IN (?, ?) "
            "ORDER BY o.season, o.week, o.entity_id, o.stat, o.line, o.side",
            (ST.OVER, ST.UNDER)):
        if gsis not in scope:
            continue
        row = {"season": season, "week": week, "entity_id": gsis, "stat": stat,
               "line": line, "side": side, "result": result}
        # One row per market. `result` is market-level and identical on both
        # sides, so whichever arrives first answers "did the over clear".
        by_market.setdefault(core_stats.market_key(row), row)

    per_player = defaultdict(list)
    for row in by_market.values():
        per_player[row["entity_id"]].append(row)

    out = {}
    for gsis, rows in per_player.items():
        by_stat, by_record = defaultdict(list), defaultdict(list)
        for r in rows:
            by_stat[r["stat"]].append(r)
            by_record[(r["season"], r["stat"], r["line"])].append(r)

        stats_out = []
        for stat, srows in by_stat.items():
            h = core_stats.hit_rate(srows)
            stats_out.append({
                "stat": stat, "priority": stat in PRIORITY_PROPS,
                # `n` is posted lines, `games` the independent events behind
                # them. The interval is built on `games` - a ladder's rungs all
                # settle off one final stat line, so counting them as separate
                # evidence made the published interval up to 3.2x too narrow.
                "n": h["n"], "games": h["games"], "cleared": h["cleared"],
                "rate": rnd(h["rate"]),
                "interval": None if h["rate"] is None else [rnd(h["lo"]), rnd(h["hi"])]})
        # Priority markets first, in the order the research ranks them; then
        # everything else by depth. Deterministic on ties, so a re-export does
        # not churn the file.
        stats_out.sort(key=lambda s: (
            PRIORITY_PROPS.index(s["stat"]) if s["priority"] else len(PRIORITY_PROPS),
            -s["n"], s["stat"]))

        records = []
        for (season, stat, line), rrows in sorted(by_record.items()):
            h = core_stats.hit_rate(rrows)
            records.append({
                "season": season, "stat": stat, "line": line,
                "n": h["n"], "cleared": h["cleared"], "rate": rnd(h["rate"]),
                "interval": None if h["rate"] is None else [rnd(h["lo"]), rnd(h["hi"])]})

        out[gsis] = {"stats": stats_out, "records": records}
    return out


def load_snaps(con, xwalk):
    """(gsis, game_id) -> (offense_snaps, offense_pct), and the pfr ids that joined nothing.

    pfr -> gsis goes through `store.pfr_gsis`: player_xwalk, plus the
    archive-derived pfr_alias inside its asserted seasons (unit f-04). `xwalk`
    is kept in the signature for the callers; the join no longer reads it.
    """
    pj = store.pfr_gsis(con)
    snaps, unresolved = {}, {}
    for pfr, gid, off, pct, name, g in con.execute(
            "SELECT s.pfr_player_id, s.game_id, s.offense_snaps, s.offense_pct, s.player, x.gsis_id "
            "FROM nfl_snap_counts s JOIN (SELECT pfr_player_id, game_id, MAX(data_version) dv "
            "FROM nfl_snap_counts GROUP BY pfr_player_id, game_id) v "
            "ON v.pfr_player_id = s.pfr_player_id AND v.game_id = s.game_id "
            "AND v.dv = s.data_version "
            f"LEFT {pj.on()}"):
        if g is None:
            unresolved[pfr] = name
            continue
        snaps[(g, gid)] = (off, pct)
    return snaps, unresolved


def snap_weeks(con, xwalk):
    """(gsis, season, week) -> (team, offense_snaps, defense_snaps, offense_pct, game_id).

    SEPARATE from `load_snaps`, which keys on (gsis, game_id) and is read at two
    call sites that have no reason to change. This one carries the two things
    that one drops and a played-zero row cannot be built without:

      team            `game_index` keys on (season, week, team), so without it
                      there is no opponent, no date and no home flag - all of
                      which PeriodRow requires.
      defense_snaps   so a defender is visible rather than reading as zero.
                      Dropping it is exactly what made walkforward's copy of
                      the settlement rule impossible to fix in one line.
    """
    pj = store.pfr_gsis(con)
    out = {}
    for pfr, gid, season, week, team, off, dfn, pct, g in con.execute(
            "SELECT s.pfr_player_id, s.game_id, s.season, s.week, s.team, "
            "s.offense_snaps, s.defense_snaps, s.offense_pct, x.gsis_id "
            "FROM nfl_snap_counts s JOIN (SELECT pfr_player_id, game_id, MAX(data_version) dv "
            "FROM nfl_snap_counts GROUP BY pfr_player_id, game_id) v "
            "ON v.pfr_player_id = s.pfr_player_id AND v.game_id = s.game_id "
            f"AND v.dv = s.data_version {pj.on()} WHERE s.week IS NOT NULL"):
        if g is not None:
            out[(g, season, week)] = (team, off or 0, dfn or 0, pct, gid)
    return out


def load_phase_snaps(con):
    """(gsis, game_id) -> (defense_snaps, defense_pct, st_snaps) - extended only.

    Same join and same latest-version rule as `load_snaps`, which is kept as it
    is: it is read at two call sites that have no reason to change, and the
    default export must not move.
    """
    pj = store.pfr_gsis(con)
    out = {}
    for gid, dfn, dpct, st, g in con.execute(
            "SELECT s.game_id, s.defense_snaps, s.defense_pct, s.st_snaps, x.gsis_id "
            "FROM nfl_snap_counts s JOIN (SELECT pfr_player_id, game_id, MAX(data_version) dv "
            "FROM nfl_snap_counts GROUP BY pfr_player_id, game_id) v "
            "ON v.pfr_player_id = s.pfr_player_id AND v.game_id = s.game_id "
            f"AND v.dv = s.data_version {pj.on()}"):
        if g is not None:
            out[(g, gid)] = (dfn, dpct, st)
    return out


def load_jerseys(con):
    """(gsis, season) -> jersey number - extended only (A-B4).

    ONE number per season, chosen at read time: the latest week of that season
    that carries one, on the latest data_version. A player traded mid-season can
    wear two numbers in one season; the season's LAST is the one a season file
    shows, and the per-week history stays in nfl_roster_week. Empty - never an
    error - on a store that has not normalized rosters yet, and the export
    reports that count rather than guessing.
    """
    try:
        rows = con.execute(
            "SELECT r.gsis_id, r.season, r.week, TRIM(r.jersey_number) FROM nfl_roster_week r "
            "JOIN (SELECT gsis_id, season, week, team, MAX(data_version) dv FROM nfl_roster_week "
            "GROUP BY gsis_id, season, week, team) v ON v.gsis_id = r.gsis_id "
            "AND v.season = r.season AND v.week = r.week AND v.team = r.team "
            "AND v.dv = r.data_version WHERE r.sport = ? AND r.jersey_number IS NOT NULL "
            "ORDER BY r.gsis_id, r.season, r.week", (SPORT,)).fetchall()
    except sqlite3.OperationalError:          # table absent on a pre-a-14 store
        return {}
    out = {}
    for gsis, season, _week, jersey in rows:
        # ascending week: the last one wins. A value that is not one or two
        # digits ('69B' - 10 roster rows 2004-2014) publishes null rather than
        # a guess at what the letter means; a string, not an int, so 0 and 00
        # stay different numbers.
        out[(gsis, season)] = jersey if JERSEY.fullmatch(jersey) else None
    return out


JERSEY = re.compile(r"[0-9]{1,2}")


class ExtendedInputs:
    """What the extended profile reads beyond the default export. Its presence
    IS the switch: every builder takes `ext=None` and the default path never
    constructs one."""

    def __init__(self, phase_snaps, jerseys):
        self.phase_snaps = phase_snaps
        self.jerseys = jerseys


def load_looks(con):
    """-> ({(gsis, season, week, season_type): (rz_targets, rz_carries)},
           {game_id covered by play-by-play}) - staged `air_rz` only (A-B8).

    Latest data_version per player-week, like every other nflverse table here.
    THE COVERED SET IS WHAT MAKES A ZERO A ZERO. A player-week with no looks row
    is 0 only if play-by-play reached that game; the live season's pbp can lag
    its weekly stats, and a game it has not reached is unknown - null - not a
    player who had no red-zone looks. Every game has named looks, so a covered
    game always leaves rows here.
    """
    looks, covered = {}, set()
    for g, season, week, stype, gid, rzt, rzc in con.execute(
            "SELECT l.gsis_id, l.season, l.week, l.season_type, l.game_id, l.rz_targets, "
            "l.rz_carries FROM nfl_pbp_looks l JOIN (SELECT gsis_id, season, week, season_type, "
            "MAX(data_version) dv FROM nfl_pbp_looks GROUP BY gsis_id, season, week, season_type) v "
            "ON v.gsis_id = l.gsis_id AND v.season = l.season AND v.week = l.week "
            "AND v.season_type = l.season_type AND v.dv = l.data_version"):
        looks[(g, season, week, stype)] = (rzt, rzc)
        if gid:
            covered.add(gid)
    return looks, covered


class AirRzInputs:
    """What the staged `air_rz` feature reads. Like ExtendedInputs, its
    presence IS the switch; the default path passes None."""

    def __init__(self, looks, covered):
        self.looks = looks
        self.covered = covered

    def values(self, r, gsis, game_id, stype):
        """{rec_air_yds, rz_targets, rz_rush_att} for one stat row.

        rec_air_yds goes through _ext_count: a stored NULL (a row written
        before the column was ingested) stays null rather than printing 0.
        """
        season = r["season"]
        out = {"rec_air_yds": _ext_count(r, "receiving_air_yards")}
        out.update(self.red_zone(gsis, season, r["week"], stype, game_id))
        return out

    def red_zone(self, gsis, season, week, stype, game_id):
        if game_id is None or game_id not in self.covered:
            return {"rz_targets": None, "rz_rush_att": None}
        rzt, rzc = self.looks.get((gsis, season, week, stype), (0, 0))
        return {"rz_targets": intish(rzt) if collected("rz_targets", season) else None,
                "rz_rush_att": intish(rzc)}


def played_zero_periods(gsis, rows, snap_index, gidx, ext=None, own=None, airz=None):
    """Periods for weeks this player PLAYED and recorded no stat row.

    nflverse writes no row for a player who played and recorded nothing, so the
    absence is not evidence of absence - the snap counts are. Same upstream gap
    as the settlement defect; see core/settlement.py for the five-case
    partition and tests/test_played_zero.py for what may and may not be emitted.

    THREE THINGS ARE REFUSED, and the refusals matter more than the emissions:
      * zero snaps in BOTH phases - he sat out, and a row would assert he did
        not. 5,904 in-scope weeks, the largest excluded group.
      * defence-only snaps - `snaps` here is OFFENSIVE and PERIOD_KEYS is
        offensive vocabulary, so the row would publish snaps=0 with all-zero
        offence and read on the page as sitting out. 1,070 weeks.
        EXCEPT under the extended profile (`ext`), whose row carries
        defense_snaps and st_snaps and so can say he played; there a week
        with snaps in ANY phase is emitted.
      * a week with no joinable game, or a game with no final score.
    """
    have = {(r["season"], r["week"]) for r in rows}
    out = []
    # `own` is this player's slice of the index when the caller has grouped it
    # (build_players does, once); scanning the whole index per player is
    # quadratic, and the extended scope is ~4x the players.
    items = own if own is not None else snap_index.items()
    for (g, season, week), (team, off, dfn, pct, gid) in items:
        if g != gsis or season < SNAP_FIRST_SEASON or (season, week) in have:
            continue
        # EXTENDED: a defence-only or special-teams-only week is a game PLAYED,
        # and the row can say so, because it carries defense_snaps and st_snaps
        # beside the offensive `snaps` (which is then an honest 0). Refusing it
        # was right only while a row could speak offence alone.
        phase = (ext.phase_snaps.get((gsis, gid)) if ext is not None else None) or (None, None, None)
        st = phase[2]
        if ext is None:
            if not off or off <= 0:
                continue
        elif not any(v and v > 0 for v in (off, dfn, st)):
            continue
        game = gidx.get((season, week, team))
        if game is None or game.get("home_score") is None:
            continue
        home = team == game["home_team"]
        # THE SEASON TYPE COMES FROM THE GAME, NEVER A CONSTANT. `game_index`
        # carries every game type, so hard-coding "REG" here emitted 745 playoff
        # games as regular season - rows reading season_type=REG beside
        # label='Divisional', 'Wild Card', even 'Super Bowl'. `_totals` groups on
        # (season, season_type), so each one was counted into that season's REG
        # total and into the per-game denominator the site divides by. A row that
        # contradicts itself is worse than a missing row.
        gtype = game.get("game_type") or "REG"
        stype = "REG" if gtype == "REG" else "POST"
        stats = {k: 0 for k in PERIOD_KEYS}
        stats["snaps"] = intish(off)
        stats["snap_share"] = rnd(pct)
        if ext is not None:
            # No stat row means nflverse recorded nothing for him that week, so
            # every count is a recorded zero - the same reasoning as the
            # offensive keys above, and why this is only reached from 2013,
            # where snaps prove he played.
            stats.update({k: 0 for k in EXT_COUNT_KEYS})
            stats["defense_snaps"] = intish(dfn or 0)
            stats["st_snaps"] = intish(st or 0)
        if airz is not None:
            # No stat row: no pass was thrown to him, so air yards are a
            # recorded 0 (every played-zero season is >= 2013, collected). The
            # red-zone pair still asks play-by-play whether it reached the game.
            stats["rec_air_yds"] = 0
            stats.update(airz.red_zone(gsis, season, week, stype, game["game_id"]))
        out.append({
            "season": season, "index": week,
            "label": period_label(gtype, week, stype),
            "season_type": stype,
            "game_id": game["game_id"], "date": game["gameday"],
            "team": team,
            "opponent": game["away_team"] if home else game["home_team"],
            "home": home, "stats": stats})
    return out


def current_period(games, weeks, now_ts=None):
    """The current period, what the stats reach, and whether nflverse is late."""
    now_ts = time.time() if now_ts is None else now_ts
    season = max(g["season"] for g in games.values())
    this = [g for g in games.values() if g["season"] == season and g["week"] is not None]
    unplayed = [g for g in this if g["home_score"] is None]
    index = min(g["week"] for g in unplayed) if unplayed else max(g["week"] for g in this)
    by_week = defaultdict(list)
    for g in this:
        by_week[g["week"]].append(g)
    completed = [w for w, gs in by_week.items() if all(x["home_score"] is not None for x in gs)]
    last_completed = max(completed) if completed else None
    in_season = [(r["season"], r["week"]) for r in weeks if r["season"] == season]
    if in_season:
        through = max(in_season)
    else:
        prev = [(r["season"], r["week"]) for r in weeks if r["season"] < season]
        through = max(prev) if prev else (None, None)
    stale, reason = False, None
    if last_completed is not None and (through[0] != season or through[1] < last_completed):
        stale = True
        have = f"week {through[1]}" if through[0] == season else "no weeks"
        reason = (f"week {last_completed} of {season} is complete but nflverse player stats "
                  f"reach {have}")
    gtype = next((g.get("game_type") for g in by_week.get(index, [])), "REG")
    return {"season": season,
            "period": {"index": index, "label": period_label(gtype, index),
                       "key": period_key(season, index)},
            "data_through": {"season": through[0], "index": through[1]},
            "stale": stale, "stale_reason": reason, "last_completed": last_completed}


def fixture_spread(spread_line):
    """The contract's `Fixture.spread`: the HOME team's expected margin, positive
    when the HOME team is favoured.

    nflverse's `spread_line` is already that - measured, not assumed: on
    2023_01_DET_KC it is +4.0 with the home side at -198 on the moneyline, and on
    Super Bowl LIX (2024_22_KC_PHI, PHI the designated home side) it is -1.5
    with the AWAY side at -120. A pass-through for THIS source only. CFBD's
    line is home-NEGATIVE (c-14: 21,378 CFB rows shipped inverted by assuming
    otherwise), so a CFB emitter must negate, and must say so in its own code.
    """
    return None if spread_line is None else float(spread_line)


def current_fixtures(games, current):
    """current.fixtures[] (A-B5, staged): every game of `current.period`, in
    kickoff order. Teams are slugs, as ScheduleGame.opponent is."""
    season, index = current["season"], current["period"]["index"]
    gs = [g for g in games.values() if g["season"] == season and g["week"] == index]
    gs.sort(key=lambda g: (g["kickoff_ts"] is None, g["kickoff_ts"] or 0, g["game_id"]))
    return [{"game_id": g["game_id"], "kickoff_ts": g["kickoff_ts"],
             "home": team_slug(g["home_team"]), "away": team_slug(g["away_team"]),
             "spread": fixture_spread(g.get("spread_line")),
             "total": None if g.get("total_line") is None else float(g["total_line"])}
            for g in gs]


def player_scope(weeks, extended=False):
    """v1 scope, kept: any regular-season week with offensive usage.

    EXTENDED (a-14): also any regular-season week with a non-zero published
    defensive or special-teams stat. Only a stat the page can show admits a
    player: an offensive lineman with snaps and no stat row has nothing to
    render, and a page of zeros is not a player page.
    """
    base = {r["gsis_id"] for r in weeks if r["season_type"] == "REG"
            and num(r["targets"]) + num(r["carries"]) + num(r["attempts"]) > 0}
    if not extended:
        return base
    cols = [c for _, c in EXT_STAT_MAP]
    return base | {r["gsis_id"] for r in weeks if r["season_type"] == "REG"
                   and any(r.get(c) for c in cols)}


def players_by_id(weeks, scope):
    by = defaultdict(list)
    for r in weeks:
        if r["gsis_id"] in scope:
            by[r["gsis_id"]].append(r)
    for rows in by.values():
        rows.sort(key=lambda r: (r["season"], r["week"]))
    return by


def resolved_name(gsis, rows, xwalk):
    """The player's display name, or None when no source names them."""
    return (xwalk.get(gsis) or {}).get("display_name") or rows[-1].get("player_name")


def drop_nameless(by_player, xwalk):
    """Exclude players no source can name. -> (kept, {id: unresolved row}).

    A page titled by a bare source id is not a player page, and a nameless row
    in the index cannot be searched for. So they are excluded rather than
    rendered - but the count is REPORTED on every run and the ids are published
    in the manifest's unresolved_ids, because a silent filter is how a real
    player disappears without anyone noticing. A count that prints "0" most
    weeks makes the week it prints "1" visible the day it happens.
    """
    kept, dropped = {}, {}
    for gsis, rows in by_player.items():
        if resolved_name(gsis, rows, xwalk):
            kept[gsis] = rows
        else:
            dropped[gsis] = {"id": gsis, "name": None, "reason": REASON_NO_NAME}
    return kept, dropped


def slug_entries(by_player, xwalk):
    return {gsis: {"name": resolved_name(gsis, rows, xwalk),
                   "first_season": rows[0]["season"],
                   "reg_games": sum(1 for r in rows if r["season_type"] == "REG")}
            for gsis, rows in by_player.items()}


def scope_slugs(by_player, xwalk, registry_path=None, dry_run=False):
    """Load the registry, append slugs for new ids, write it back (unless dry-run).
    -> (slugs for the ids in scope, ids added this run)."""
    path = registry_path or slug_registry_path()
    registry, added = assign_slugs(slug_entries(by_player, xwalk), load_slug_registry(path))
    if added and not dry_run:
        write_slug_registry(path, registry)
    return {gsis: registry[gsis] for gsis in by_player}, added


# =============================================================================
# builders
# =============================================================================

def _count(r, col):
    # A season the source did not collect publishes null. Without this the row
    # reads as a real zero, which is indistinguishable from "he had none".
    if not collected(col, r["season"]):
        return None
    return intish(r.get(col)) if r.get(col) is not None else 0


def _ext_count(r, col):
    """An extended stat, where a stored NULL STAYS null - never 0.

    NOT `_count`. nflverse publishes these 31 columns with zero nulls in 478,384
    rows (1999-2026, measured 2026-09-23), so a NULL in the store can mean only
    one thing: that row was written before the column was ingested and has not
    been re-derived from the archive. `_count` turns NULL into 0, which is right
    for its own columns and would print "0 of 0 field goals" on every kicker
    whose season has not been rebuilt - a page saying a kicker attempted
    nothing in 1999. Unknown is null.
    """
    if not collected(col, r["season"]):
        return None
    v = r.get(col)
    return None if v is None else intish(v)


def _totals(periods, ext=False, airz=False):
    """Totals over `periods`, which may be ONE SEASON or a WHOLE CAREER.

    THE CAREER CALL IS WHY THE RULE IS "ANY", NOT "ALL". This is called twice:
    per (season, season_type) for `season_totals`, and over every REG period for
    `career`. A rule of "null only when every period is uncollected" would be
    right for the first and useless for the second - a player spanning 2002-2012
    would get a career TFL figure that silently dropped nine seasons and looked
    like a number. So a total spanning ANY uncollected season is null: the honest
    answer is that it cannot be stated, not a sum of the half that exists.
    """
    shares = [p["stats"].get("snap_share") for p in periods
              if p["stats"].get("snap_share") is not None]
    stats = {}
    # ONLY THE KEYS THE PERIODS CARRY. The period rows now hold what the player's
    # production justifies, so totalling the full COUNT_KEYS would put
    # `pass_att: 0` in a receiver's season totals while his game log has no such
    # column - a file contradicting itself, which is the `season_type: "REG"`
    # lesson: a row that disagrees with its own siblings is worse than a missing
    # one.
    for k in COUNT_KEYS:
        if not any(k in p["stats"] for p in periods):
            continue
        if k in MISSING_COMPONENTS:
            stats[k] = None
            continue
        col = SOURCE_OF.get(k, k)
        if any(not collected(col, p["season"]) for p in periods):
            stats[k] = None
            continue
        stats[k] = intish(sum(num(p["stats"].get(k)) for p in periods))
    if ext:
        # Same "any" rule, one clause stricter: a period carrying the key as
        # NULL - not collected, or not yet re-derived (see _ext_count) - makes
        # the total unknown. Summing it as 0 would state a career figure built
        # from the half that exists.
        for k in EXT_COUNT_KEYS:
            if not any(k in p["stats"] for p in periods):
                continue
            if any(k in p["stats"] and p["stats"][k] is None for p in periods) or any(
                    not collected(EXT_SOURCE_OF[k], p["season"]) for p in periods):
                stats[k] = None
                continue
            stats[k] = intish(sum(num(p["stats"].get(k)) for p in periods))
    if airz:
        # The extended rule, for the same reason: a NULL period - not collected,
        # not yet re-derived, or a game play-by-play has not reached - makes the
        # total unknown rather than the sum of the half that exists.
        for k in AIR_RZ_KEYS:
            if not any(k in p["stats"] for p in periods):
                continue
            if any(k in p["stats"] and p["stats"][k] is None for p in periods) or any(
                    not collected(AIR_RZ_SOURCE_OF[k], p["season"]) for p in periods):
                stats[k] = None
                continue
            stats[k] = intish(sum(num(p["stats"].get(k)) for p in periods))
    stats["snap_share_mean"] = rnd(statistics.fmean(shares)) if shares else None
    return stats


def build_players(games, by_player, snaps, xwalk, aliases, slugs, market_keys, generated_at,
                  headshots=None, snap_index=None, prop_history=None, ext=None, airz=None):
    """-> ({key: obj} for summaries and season files, [index entries], unresolved).

    `ext` (an ExtendedInputs) switches on the a-14 profile; None is the default
    export, byte-for-byte what it was. `airz` (an AirRzInputs) likewise
    switches on the staged a-15 air-yards and red-zone keys.
    """
    headshots = headshots or {}
    gidx = game_index(games)
    files, index, unresolved = {}, [], []
    period_keys = (PERIOD_KEYS + (EXT_PERIOD_KEYS if ext is not None else ())
                   + (AIR_RZ_KEYS if airz is not None else ()))
    candidates = (STAT_CANDIDATES + (EXT_PERIOD_KEYS if ext is not None else ())
                  + (AIR_RZ_KEYS if airz is not None else ()))
    own_snaps = defaultdict(list)
    for item in (snap_index or {}).items():
        own_snaps[item[0][0]].append(item)
    for gsis, rows in by_player.items():
        xw = xwalk.get(gsis)
        latest = rows[-1]
        if xw is None:
            unresolved.append({"id": gsis, "name": latest.get("player_name"),
                               "reason": "not in player_xwalk"})
        x = xw or {}
        slug = slugs[gsis]
        name = x.get("display_name") or latest.get("player_name")
        position = x.get("position") or latest.get("position")
        periods = []
        for r in rows:
            g = gidx.get((r["season"], r["week"], r["team"]))
            home = None if g is None else (r["team"] == g["home_team"])
            opp = r.get("opponent") if g is None else (g["away_team"] if home else g["home_team"])
            snap = (snaps.get((gsis, g["game_id"]))
                    if g is not None and r["season"] >= SNAP_FIRST_SEASON else None)
            raw = {"snaps": None if snap is None else intish(snap[0]),
                   "snap_share": None if snap is None else rnd(snap[1]),
                   "target_share": (rnd(r.get("target_share"))
                                    if collected("target_share", r["season"]) else None)}
            for col, key in STAT_MAP:
                raw[key] = _count(r, col)
            for key in MISSING_COMPONENTS:
                raw[key] = None
            if ext is not None:
                for key, col in EXT_STAT_MAP:
                    raw[key] = _ext_count(r, col)
                # Same gate as `snaps`: before 2013 the snap feed does not exist,
                # so a phase snap count is unknown, not zero.
                ph = (ext.phase_snaps.get((gsis, g["game_id"]))
                      if g is not None and r["season"] >= SNAP_FIRST_SEASON else None)
                raw["defense_snaps"] = None if ph is None else intish(ph[0] or 0)
                raw["st_snaps"] = None if ph is None else intish(ph[2] or 0)
            if airz is not None:
                raw.update(airz.values(r, gsis, None if g is None else g["game_id"],
                                       r["season_type"]))
            periods.append({
                "season": r["season"], "index": r["week"],
                "label": period_label(None if g is None else g.get("game_type"), r["week"],
                                      r["season_type"]),
                "season_type": r["season_type"],
                "game_id": None if g is None else g["game_id"],
                "date": None if g is None else g["gameday"],
                "team": r["team"], "opponent": opp, "home": home,
                "stats": {k: raw[k] for k in period_keys}})

        # Weeks he played and recorded nothing. Appended after the stat-row
        # periods and re-sorted, so a season file reads in week order whatever
        # the source of each row.
        if snap_index:
            periods.extend(played_zero_periods(gsis, rows, snap_index, gidx, ext=ext,
                                               own=own_snaps.get(gsis, []), airz=airz))
            periods.sort(key=lambda p: (p["season"], p["index"] or 0))

        if ext is not None:
            # A PHASE-SNAP KEY APPEARS ONLY FOR A PLAYER WHO PLAYED THAT PHASE.
            # The per-season rule below keeps a key that is null, and phase
            # snaps are null in every season before 2013 - so without this a
            # 2005 receiver would carry "Def Snaps: not recorded", true and
            # meaningless. Decided over the CAREER: a linebacker keeps the key
            # through his pre-2013 seasons, where null is the honest value.
            for k in PHASE_SNAP_KEYS:
                if not any(p["stats"].get(k) for p in periods):
                    for p in periods:
                        p["stats"].pop(k, None)

        # A ROW CARRIES WHAT THIS PLAYER'S PRODUCTION JUSTIFIES. Applied here,
        # once, AFTER the played-zero rows are in - so the key set is decided
        # over every period the player has, and a zero-production week keeps the
        # same columns as the rest of his season instead of inventing its own.
        #
        # Usage keys survive unconditionally: a played-zero row IS snaps > 0 with
        # every stat zero, and dropping `snaps` would leave a row asserting
        # nothing. See emitted_keys() for why a NULL keeps its key.
        if any(p["team"] is None for p in periods):
            unresolved.append({"id": gsis, "name": name, "reason": REASON_NO_TEAM})

        by_season = defaultdict(list)
        for p in periods:
            by_season[p["season"]].append(p)

        # PER SEASON, NOT PER CAREER, and the difference is not cosmetic. A
        # reader opens ONE season file, so the question a key must answer is
        # "did he do this THAT year". Scoped to the career, a receiver who
        # scored once in 2025 carries `rec_td: 0` through every other season,
        # and a back who ran once in 2026 carries `rush_att: 0` back through
        # 2025 - which is what the guard caught, and it caught the wiring
        # rather than itself.
        #
        # The CAREER total is unaffected: `_totals` keeps any key some period
        # carries, so summing over every REG period gives the union across
        # seasons and a one-year stat still appears on the career line.
        for ps in by_season.values():
            justified = set(emitted_keys([p["stats"] for p in ps], candidates)) | set(USAGE_KEYS)
            for p in ps:
                p["stats"] = {k: v for k, v in p["stats"].items() if k in justified}
        season_entries = []
        for season in sorted(by_season):
            ps = by_season[season]
            key = f"{SPORT}/players/{gsis}/{season}.json"
            files[key] = {**envelope("player_season", generated_at),
                          "identity": {"id": gsis, "slug": slug, "name": name},
                          "season": season, "periods": ps}
            # "Teams played for" is a display list, so a row with no team
            # contributes nothing to it. The period row itself keeps its null
            # team - that is the honest record - and the player is counted in
            # unresolved_ids above rather than quietly cleaned up.
            teams = []
            for p in ps:
                if p["team"] is not None and p["team"] not in teams:
                    teams.append(p["team"])
            entry = {"season": season, "teams": teams, "games": len(ps), "key": key}
            if ext is not None:
                # Per season, because numbers change. null before 2002, where
                # nflverse publishes no roster, and wherever no roster row
                # names one.
                entry["jersey_number"] = ext.jerseys.get((gsis, season))
            season_entries.append(entry)

        totals = []
        grouped = defaultdict(list)
        for p in periods:
            grouped[(p["season"], p["season_type"])].append(p)
        for (season, stype) in sorted(grouped, key=lambda k: (k[0], k[1] != "REG")):
            ps = grouped[(season, stype)]
            totals.append({"season": season, "season_type": stype, "games": len(ps),
                           "stats": _totals(ps, ext=ext is not None, airz=airz is not None)})
        reg = [p for p in periods if p["season_type"] == "REG"]

        files[f"{SPORT}/players/{gsis}/summary.json"] = {
            **envelope("player_summary", generated_at),
            "identity": {"id": gsis, "slug": slug, "name": name, "position": position,
                         "team": latest.get("team"),
                         "ids": {"gsis": gsis, "pfr": x.get("pfr_id"), "espn": x.get("espn_id"),
                                 "sleeper": x.get("sleeper_id"), "yahoo": x.get("yahoo_id"),
                                 "pff": x.get("pff_id")},
                         "aliases": sorted(aliases.get(gsis, ())),
                         "headshot_url": headshots.get(gsis),
                         # EXTENDED (A-B4), and present only there so the default
                         # summary does not move. The number is the one he wore in
                         # the season of his latest stat row, matching `team`
                         # beside it; birth_date is the part, and the site
                         # computes age from it at read time.
                         **({"jersey_number": ext.jerseys.get((gsis, latest["season"])),
                             "birth_date": x.get("birth_date")} if ext is not None else {})},
            "seasons": season_entries,
            "season_totals": totals,
            "career": {"season_type": "REG", "games": len(reg),
                       "stats": _totals(reg, ext=ext is not None, airz=airz is not None)},
            "market": {"key": market_keys[gsis]} if gsis in market_keys else None,
            # null, never an empty record: a player with no settled props has no
            # history, which is a different statement from a history of nothing.
            "prop_history": (prop_history or {}).get(gsis),
        }
        index.append({"id": gsis, "slug": slug, "name": name, "position": position,
                      "team": latest.get("team"), "first_season": rows[0]["season"],
                      "last_season": rows[-1]["season"],
                      "aliases": sorted(aliases.get(gsis, ())),
                      "has_market": gsis in market_keys})
    index.sort(key=lambda p: (p["name"] or "", p["id"]))
    return files, index, unresolved


def build_teams(games, weeks, snaps, scope, xwalk, slugs, generated_at, ext=None):
    files = {}
    by_team_week = defaultdict(list)
    for r in weeks:
        by_team_week[(r["team"], r["season"], r["season_type"])].append(r)
    gidx = game_index(games)
    for abbr, name in TEAM_NAMES.items():
        slug = team_slug(abbr)
        tgames = sorted((g for g in games.values() if abbr in (g["home_team"], g["away_team"])),
                        key=lambda g: (g["season"], g["week"] or 0))
        schedule, coaches = [], defaultdict(Counter)
        for g in tgames:
            home = g["home_team"] == abbr
            pf = g["home_score"] if home else g["away_score"]
            pa = g["away_score"] if home else g["home_score"]
            coach = g["home_coach"] if home else g["away_coach"]
            if coach:
                coaches[g["season"]][coach] += 1
            opp = g["away_team"] if home else g["home_team"]
            schedule.append({
                "season": g["season"], "index": g["week"],
                "label": period_label(g["game_type"], g["week"]),
                "game_type": g["game_type"], "game_id": g["game_id"], "date": g["gameday"],
                "kickoff_ts": g["kickoff_ts"], "home": home,
                "opponent": team_slug(opp), "opponent_abbr": opp,
                "points_for": intish(pf), "points_against": intish(pa),
                "result": result_of(pf, pa),
                "spread": team_spread(g["spread_line"], home), "total": g["total_line"],
                "coach": coach,
                "opponent_coach": g["away_coach"] if home else g["home_coach"]})

        splits = []
        seasons = sorted({g["season"] for g in tgames})
        for season in seasons:
            for stype in ("REG", "POST"):
                played = [s for s in schedule if s["season"] == season
                          and (s["game_type"] == "REG") == (stype == "REG")
                          and s["points_for"] is not None]
                prows = by_team_week.get((abbr, season, stype), [])
                if not played and not prows:
                    continue

                def tot(col, _season=season):
                    # Per-season here, so the whole split is null or none of it
                    # is. `def_tfl` is 0 for nine straight seasons on every team.
                    if not collected(col, _season):
                        return None
                    return intish(sum(num(r[col]) for r in prows))
                off = {"points": intish(sum(s["points_for"] for s in played)),
                       "pass_yds": tot("passing_yards"), "rush_yds": tot("rushing_yards"),
                       "rec_yds": tot("receiving_yards"), "pass_att": tot("attempts"),
                       "pass_cmp": tot("completions"), "pass_td": tot("passing_tds"),
                       "rush_td": tot("rushing_tds"), "rec_td": tot("receiving_tds"),
                       "int_thrown": tot("interceptions"), "targets": tot("targets"),
                       "rush_att": tot("carries")}
                de = {"points_allowed": intish(sum(s["points_against"] for s in played))}
                for key, col in DEF_COLUMNS:
                    de[key] = tot(col)
                splits.append({"season": season, "season_type": stype, "games": len(played),
                               "offense": off, "defense": de})

        roster = []
        with_data = sorted({s for (t, s, st) in by_team_week if t == abbr and st == "REG"})
        if with_data:
            season = with_data[-1]
            prows = by_team_week[(abbr, season, "REG")]
            # None, not 0: a share whose denominator was never collected is
            # unknown. `if team_tgt` below already yields null for a falsy
            # denominator, but that was accidental - a residue of 54 stray rows
            # across the hole makes it truthy on some teams.
            team_tgt = (sum(num(r["targets"]) for r in prows)
                        if collected("targets", season) else None)
            team_car = sum(num(r["carries"]) for r in prows)
            per = defaultdict(list)
            for r in prows:
                per[r["gsis_id"]].append(r)
            for gsis, rs in per.items():
                shares, dshares = [], []
                for r in rs:
                    g = gidx.get((r["season"], r["week"], abbr))
                    sn = snaps.get((gsis, g["game_id"])) if g else None
                    if sn and sn[1] is not None:
                        shares.append(sn[1])
                    if ext is not None and g:
                        ph = ext.phase_snaps.get((gsis, g["game_id"]))
                        if ph and ph[1] is not None:
                            dshares.append(ph[1])
                xw = xwalk.get(gsis) or {}
                # EXTENDED (A-B2): `snap_share` is OFFENSIVE snaps, so every
                # linebacker, safety, punter and kicker prints 0% beside the
                # games he played. The defensive share goes BESIDE it, computed
                # the same way (mean of per-game pct over games with a snap
                # row); `snap_share` keeps its meaning.
                extra = ({"defense_snap_share": rnd(statistics.fmean(dshares)) if dshares else None}
                         if ext is not None else {})
                roster.append({
                    "season": season, "id": gsis, "slug": slugs.get(gsis),
                    "name": xw.get("display_name") or rs[-1].get("player_name"),
                    "position": xw.get("position") or rs[-1].get("position"),
                    "games": len(rs),
                    "snap_share": rnd(statistics.fmean(shares)) if shares else None,
                    "target_share": rnd(sum(num(r["targets"]) for r in rs) / team_tgt) if team_tgt else None,
                    "carry_share": rnd(sum(num(r["carries"]) for r in rs) / team_car) if team_car else None,
                    "has_page": gsis in scope, **extra})
            roster.sort(key=lambda p: (-(p["snap_share"] or 0), p["name"] or ""))

        files[f"{SPORT}/teams/{slug}.json"] = {
            **envelope("team", generated_at),
            "identity": {"slug": slug, "abbr": abbr, "name": name},
            # EMPTY ON PURPOSE, and staged rather than deferred. Membership is a
            # per-season fact (track C's C-3: 26 teams in one league changed
            # conference between two consecutive seasons), but `nfl_teams` holds
            # ONE row per abbreviation with no season column - there is no
            # history in the store to publish. An empty array says "none is
            # published", which is a different statement from the key being
            # absent, and it is the honest one until a per-season source exists.
            # The CURRENT season's grouping is in the manifest, where a listing
            # can read it in the fetch it already makes.
            "memberships": [],
            "seasons": seasons, "schedule": schedule, "splits": splits, "roster": roster,
            "coaches": [{"season": s, "head_coach": c.most_common(1)[0][0]}
                        for s, c in sorted(coaches.items())]}
    return files


# =============================================================================
# THE COMPONENTS TABLE (site-architecture §3; a-11)
# =============================================================================
#
# One league-wide table per season: every in-scope player's per-game stat
# components, in one file, so a page can sort the whole league on one key and
# score it under the reader's own weights. The per-player files cannot do that -
# a leaderboard over them is one fetch per player, ~700-800 a season.
#
# THE GRAIN IS PLAYER-GAME, NOT PLAYER-SEASON. Scoring presets carry per-GAME
# threshold bonuses (the contract's ScoringPreset.bonuses), and a bonus cannot
# be applied to a season sum: 100 yards in each of two games and 200 in one game
# sum to the same total and score differently. The period leaderboard needs the
# week anyway. A season total is one sum away in the browser, and storing it
# would be a derived total beside its parts.
#
# ONE FILE PER SEASON. Measured 2026-09-22: a modern season is ~1.1 MB raw and
# ~135 KB gzipped; all 28 in one file would be 27.1 MB raw, 2.9 MB gzipped, for a
# page that reads one season at a time.
#
# IT IS A PROJECTION OF THE PLAYER SEASON FILES, not a second derivation. It is
# built from the period rows build_players emitted, so the two cannot disagree
# about a player's week: same scope, same played-zero rows, same silent-zero
# nulls. The one thing added is the two share denominators.
#
# COLUMNS ARE FIXED, SO AN ABSENT KEY IS WRITTEN AS 0 - and that is exact, not a
# default. build_players drops a stat key from a player's season only when it is
# ZERO IN EVERY PERIOD of that season (emitted_keys); a null keeps its key. So
# the only thing absence can mean is zero. Usage keys are never dropped, and an
# absent usage key raises rather than being filled.
COMPONENT_COLUMNS = PERIOD_KEYS + ("team_targets", "team_snaps")


def load_team_snaps(con):
    """(game_id, snap-table team) -> the team's offensive snaps in that game.

    MAX over the players, because someone - the quarterback, the line - is on
    the field for every snap. Measured 2026-09-22 against the value implied by
    offense_snaps / offense_pct: the max equals the median implied total in
    7,165 of 7,188 team-games (the pct is published to 2dp, so the implied
    figure is itself noisy by a few snaps). Over ALL snap rows, not only the
    crosswalked ones: a lineman with no crosswalk row still played the snaps.
    """
    out = {}
    for gid, team, off in con.execute(
            "SELECT s.game_id, s.team, MAX(s.offense_snaps) FROM nfl_snap_counts s "
            "JOIN (SELECT pfr_player_id, game_id, MAX(data_version) dv FROM nfl_snap_counts "
            "GROUP BY pfr_player_id, game_id) v ON v.pfr_player_id = s.pfr_player_id "
            "AND v.game_id = s.game_id AND v.dv = s.data_version GROUP BY s.game_id, s.team"):
        if off:
            out[(gid, team)] = intish(off)
    return out


def team_targets_by_game(weeks):
    """(season, week, team) -> targets thrown by that team, over EVERY stat row.

    Every row, not the export's scope: the denominator of a share is the team's
    whole passing game. Reproduces nflverse's own target_share on 375,672 of
    375,711 rows (measured 2026-09-22), so it is the same denominator the
    per-game share already uses. Null where the source did not collect targets.
    """
    out = defaultdict(int)
    for r in weeks:
        out[(r["season"], r["week"], r["team"])] += num(r.get("targets"))
    return {k: (intish(v) if collected("targets", k[0]) else None) for k, v in out.items()}


def build_components(player_files, weeks, snap_index, team_snaps, generated_at, airz=False):
    """-> ({key: components file per season}, census).

    `snap_index` resolves a row's own snap record, whose team code is the snap
    table's; that is the code `team_snaps` is keyed on, so the two never have to
    agree with nflverse's weekly-stats team codes.
    """
    team_tgts = team_targets_by_game(weeks)
    census = Counter()
    seasons = defaultdict(lambda: {"players": [], "rows": []})
    identity = {}
    for key, obj in player_files.items():
        if obj.get("kind") == "player_summary":
            i = obj["identity"]
            identity[i["id"]] = {"id": i["id"], "slug": i["slug"], "name": i["name"],
                                 "position": i["position"]}
    for key in sorted(k for k, o in player_files.items() if o.get("kind") == "player_season"):
        obj = player_files[key]
        gsis, season = obj["identity"]["id"], obj["season"]
        s = seasons[season]
        pi = len(s["players"])
        s["players"].append(identity[gsis])
        for p in obj["periods"]:
            stats = p["stats"]
            values = []
            for col in PERIOD_KEYS:
                if col in stats:
                    values.append(stats[col])
                elif col in USAGE_KEYS:
                    raise AssertionError(f"{key} week {p['index']}: usage key {col!r} absent - "
                                         f"build_players never drops one, so this is a new shape")
                else:
                    values.append(0)
            # Off the FILLED value, not `stats`: a quarterback with no target all
            # season has no `targets` key, and his row still needs the team's
            # denominator - his zero is a share of something.
            tt = None
            if values[PERIOD_KEYS.index("targets")] is not None:
                tt = team_tgts.get((season, p["index"], p["team"]))
                census["team_targets_unmatched" if tt is None else "team_targets"] += 1
            ts = None
            if stats.get("snaps") is not None:
                own = snap_index.get((gsis, season, p["index"]))
                ts = None if own is None else team_snaps.get((own[4], own[0]))
                census["team_snaps_unmatched" if ts is None else "team_snaps"] += 1
                if ts is not None and stats["snaps"] > ts:
                    raise AssertionError(f"{key} week {p['index']}: {stats['snaps']} snaps "
                                         f"against a team total of {ts}")
            values += [tt, ts]
            if airz:
                # APPENDED after the denominators, so every existing column keeps
                # its position. Absent means zero all season (emitted_keys drops
                # only an all-zero key); a null stays null.
                values += [stats.get(col, 0) for col in AIR_RZ_KEYS]
            s["rows"].append({"player": pi, "index": p["index"],
                              "season_type": p["season_type"], "team": p["team"],
                              "values": values})
            census["rows"] += 1
    files = {}
    for season, s in sorted(seasons.items()):
        s["rows"].sort(key=lambda r: (r["index"], r["season_type"] != "REG", r["player"]))
        files[f"{SPORT}/components/{season}.json"] = {
            **envelope("components", generated_at), "season": season,
            "columns": list(COMPONENT_COLUMNS) + (list(AIR_RZ_KEYS) if airz else []),
            "players": s["players"], "rows": s["rows"]}
    census["seasons"] = len(files)
    return files, dict(census)


def build_market(con, games, weeks, xwalk, slugs, current, now_ts, generated_at, n_sims=N_SIMS):
    """Current-period market-implied fantasy distributions (research/implied.py arm A).
    -> ({key: obj}, {gsis: key}, census, {(venue, market_id)} published).

    The fourth value is what retention holds on: the venue market ids whose
    ladders are actually ON the site. A market the site is showing must not have
    its price history pruned out from under it (see quote_retention_hold)."""
    from research import implied as I

    season, index = current["season"], current["period"]["index"]
    pkey = current["period"]["key"]
    wk_games = {gid: g for gid, g in games.items()
                if g["season"] == season and g["week"] == index and g["home_score"] is None
                and g["kickoff_ts"] and g["kickoff_ts"] > now_ts}
    if not wk_games:
        return {}, {}, {"reason": "no unplayed games in the current period"}, set()
    rows = con.execute(
        "SELECT o.entity_id, o.stat, o.line, o.event_id, mo.market_id FROM outcomes o "
        "JOIN market_outcome mo ON mo.outcome_id = o.outcome_id AND mo.venue = 'kalshi' "
        "WHERE o.sport = ? AND o.season = ? AND o.week = ? AND o.entity_type = 'player' "
        "AND o.side = 'over' AND o.line IS NOT NULL AND o.stat IN ('receptions', 'rush_attempts')",
        (SPORT, season, index)).fetchall()
    ladders = defaultdict(list)
    census = Counter()
    for gsis, stat, line, game_id, market_id in rows:
        g = wk_games.get(game_id)
        if g is None:
            census["market not in an unplayed current-period game"] += 1
            continue
        if not market_id.startswith(MARKET_STATS[stat] + "-"):
            census["market series does not match stat"] += 1
            continue
        q = con.execute(
            "SELECT ts, best_bid, best_ask FROM quotes WHERE venue = 'kalshi' AND market_id = ? "
            "AND ts < ? AND best_bid IS NOT NULL AND best_ask IS NOT NULL "
            "ORDER BY ts DESC LIMIT 1", (market_id, min(now_ts, g["kickoff_ts"]))).fetchone()
        if not q:
            census["no two-sided quote before kickoff"] += 1
            continue
        ts, bid, ask = q
        ladders[(gsis, game_id)].append({"stat": stat, "line": line, "bid": bid, "ask": ask,
                                         "ts": ts, "market_id": market_id})
    if not ladders:
        return {}, {}, dict(census, reason="no priced current-period ladders"), set()

    anchor, ypc, ypr, cop = I.fit_td_anchor(), I.fit_ypc(), I.fit_ypr(), I.fit_copula()
    history = defaultdict(list)
    latest_team = {}
    for r in weeks:
        latest_team[r["gsis_id"]] = r["team"]
        if r["season_type"] == "REG" and season - 3 <= r["season"] <= season:
            history[r["gsis_id"]].append(
                1.0 if num(r["receiving_tds"]) + num(r["rushing_tds"]) > 0 else 0.0)

    files, keys, published = {}, {}, set()
    for (gsis, game_id), rungs in ladders.items():
        by_stat = defaultdict(list)
        for x in rungs:
            by_stat[x["stat"]].append(x)
        if "receptions" not in by_stat:
            census["no receptions ladder (outside validated scope)"] += 1
            continue
        fits = {}
        for stat, xs in by_stat.items():
            pts = [(x["line"], min(max((x["bid"] + x["ask"]) / 2.0, 1e-4), 1 - 1e-4)) for x in xs]
            f = I.fit_marginal(stat, pts)
            if f:
                fits[stat] = f
        if "receptions" not in fits:
            census["receptions ladder did not fit"] += 1
            continue
        g = wk_games[game_id]
        xw = xwalk.get(gsis) or {}
        team = latest_team.get(gsis) or xw.get("last_team")
        if team not in (g["home_team"], g["away_team"]):
            census["player team not in the game"] += 1
            continue
        home = team == g["home_team"]
        pos = (xw.get("position") or "").upper()
        h = history.get(gsis, [])
        fits["_td_rate"] = statistics.fmean(h) if len(h) >= 4 else 0.0
        real = {"pos": pos, "total": g["total_line"], "spread": g["spread_line"], "home": home}
        rho = cop.get(pos, {}).get("rho", {}).get(("receptions", "receiving_yards"), 0.75)
        seed = int(hashlib.sha1(f"{gsis}|{game_id}".encode()).hexdigest()[:8], 16)
        dists = {}
        for preset, imp in IMPLIED_SCORING.items():
            sims = I.simulate_player_game(fits, real, anchor, ypc, rho, random.Random(seed),
                                          n_sims, imp, ypr=ypr)
            dists[preset] = distribution_summary(sims)

        def rung_list(stat):
            return [{"line": x["line"], "p_over": rnd((x["bid"] + x["ask"]) / 2.0),
                     "bid": rnd(x["bid"]), "ask": rnd(x["ask"]), "quote_ts": x["ts"]}
                    for x in sorted(by_stat.get(stat, []), key=lambda x: x["line"])]
        rush = rung_list("rush_attempts")
        components = [
            {"stat": "rec", "basis": "MARKET", "rungs": rung_list("receptions")},
            {"stat": "rec_yds", "basis": "DERIVED", "note": "receptions × yards per catch by position"},
            {"stat": "rush_att", "basis": "MARKET", "rungs": rush,
             **({} if rush else {"note": "no rush-attempts ladder listed; contributes 0"})},
            {"stat": "rush_yds", "basis": "DERIVED", "note": "attempts × yards per carry by position"},
            {"stat": "td", "basis": "ANCHORED", "note": "player TD rate scaled by the game total and spread"},
        ]
        key = f"{SPORT}/market/{gsis}/{pkey}.json"
        keys[gsis] = key
        # Only the ladders that survived every census check reach here, so this
        # is exactly the set the site displays - not everything that was mapped.
        published |= {("kalshi", x["market_id"]) for x in rungs}
        files[key] = {
            **envelope("market", generated_at),
            "identity": {"id": gsis, "slug": slugs.get(gsis), "name": xw.get("display_name"),
                         "position": xw.get("position"), "team": team_slug(team)},
            "period": dict(current["period"], season=season),
            "game_id": game_id,
            "opponent": team_slug(g["away_team"] if home else g["home_team"]),
            "kickoff_ts": g["kickoff_ts"],
            "as_of": iso(max(x["ts"] for x in rungs)),
            "source": dict(MARKET_SOURCE, n_sims=n_sims),
            "components": components,
            "path": build_price_path(con, by_stat.get("receptions", []),
                                     min(now_ts, g["kickoff_ts"])),
            "game_lines": {"total": g["total_line"], "spread": team_spread(g["spread_line"], home),
                           "source": "nflverse games"},
            "distributions": dists,
            "validation": dict(VALIDATION),
        }
    return files, keys, dict(census), published


def build_research(generated_at):
    out = {}
    with open(os.path.join(ROOT, "docs", "hypotheses.json"), encoding="utf-8") as f:
        src = json.load(f)
    out["research/hypotheses.json"] = {**envelope("research.hypotheses", generated_at, None),
                                       "hypotheses": src["hypotheses"]}

    from research import score as SC
    rows, *_ = SC.load(2026, 1)
    common = [r for r in rows if r["market_p"] is not None]
    series = []
    for name, field in (("model", "model"), ("market", "market_p")):
        table, ece = SC.reliability([r[field] for r in common], [r["y"] for r in common])
        series.append({"name": name, "ece": rnd(ece), "bins": [
            {"lo": t["lo"], "hi": t["hi"], "n": t["n"],
             "mean_forecast": rnd(t.get("mean_p")), "realized": rnd(t.get("rate")),
             "wilson": [rnd(t["wilson"][0]), rnd(t["wilson"][1])] if t["n"] else None}
            for t in table]})
    brier = {f: rnd(statistics.fmean(SC.brier(r[k], r["y"]) for r in common))
             for f, k in (("model", "model"), ("market", "market_p"), ("naive", "naive"))}
    head = SC.boot_mean(common, lambda r: SC.brier(r["model"], r["y"]) - SC.brier(r["market_p"], r["y"]))
    brier["model_minus_market"] = {"estimate": rnd(head["est"]),
                                   "interval": [rnd(head["lo"]), rnd(head["hi"])]}
    # ON `common`, NOT on the full settled set. score.py also computes a
    # model-naive difference over every settled row (its "secondary"), and that
    # is a DIFFERENT population - one-sided books included. Publishing it beside
    # Brier scores computed on `common` would put an interval and the numbers it
    # describes on different denominators, and the site gates "identical to
    # naive" on model == naive at four places. Same rows, or the page can show
    # 0.1916 = 0.1916 next to an interval that never saw those outcomes.
    nv = SC.boot_mean(common, lambda r: SC.brier(r["model"], r["y"]) - SC.brier(r["naive"], r["y"]))
    brier["model_minus_naive"] = {"estimate": rnd(nv["est"]),
                                  "interval": [rnd(nv["lo"]), rnd(nv["hi"])]}
    out["research/calibration.json"] = {
        **envelope("research.calibration", generated_at, None),
        "source": "research/score.py (brief 021)",
        "population": (f"NFL week 1 2026, KXNFLREC + KXNFLRSHATT, common set n={len(common)}, "
                       f"{len({r['game'] for r in common})} games. Every figure in `brier` - the "
                       f"three scores and both intervals - is computed on this one set."),
        "series": series, "brier": brier}

    reg = os.path.join(ROOT, "research", "sweep", "results", "h3.jsonl")
    med = {}
    with open(reg, encoding="utf-8") as f:
        for line in f:
            r = json.loads(line)
            if r.get("family") == "H3 median spread" and r.get("estimable"):
                med[r["name"]] = r.get("est")
    out["research/execution.json"] = {
        **envelope("research.execution", generated_at, None),
        "source": "research/sweep/h3_lifecycle.py (brief 022)",
        "series": [{"series": s, "by_time_to_kickoff": [
            {"bucket": b, "median_spread_c": med.get(f"{s}|ttk|{b}"),
             "median_touch": EXEC_TOUCH.get(s, {}).get(b)} for b in TTK_BUCKETS]}
            for s in EXEC_SERIES],
        "spread_to_volatility": [{"series": s, "ratio": v} for s, v in EXEC_RATIO.items()],
        "rule": EXEC_RULE}
    return out


def played(games):
    """Games with a final score.

    NOT len(games): nfl_games carries scheduled rows whose score is still None
    (a whole unplayed season lands the moment the schedule is published), and a
    coverage count including them claims a record the site does not hold.
    build_market finds the current period's fixtures by exactly this field.
    """
    return sum(1 for g in games.values() if g["home_score"] is not None)


def count_rungs(market_files):
    """Ladder rungs across published market files, summed over components.

    Counted off the EMITTED objects rather than threaded out of build_market:
    census there is a Counter of exclusion reasons - a diagnostic - and folding
    a shipped-volume tally into it would corrupt what it means.
    """
    return sum(len(c.get("rungs") or [])
               for m in market_files.values() for c in m.get("components", []))


def count_markets_by_team(market_files):
    """{team slug: distinct priced markets} across published market files.

    A MARKET, not a priced player. A component is a market when it was QUOTED -
    `basis == "MARKET"` with a non-empty ladder - so a player with receptions and
    rush attempts priced is TWO, and one with only receptions is one. `rec_yds`
    is DERIVED and `td` is ANCHORED: neither was quoted, so neither is a market,
    and a `rush_att` component carrying `rungs: []` because no ladder was listed
    counts as nothing.

    Counting players instead was the first version and it was wrong. The card's
    label says "markets" and its empty state says "No ladder" - both name the
    market, not the person - and `market_keys` is one file key per player, so it
    is structurally incapable of seeing the difference.

    KEYED ON THE SLUG, because that is what a market file holds:
    `identity.team` is `team_slug(team)` ("buf"), while TEAM_NAMES is keyed on
    the abbreviation ("BUF"). Counting one against the other returns 0 for every
    team and looks exactly like a slate with no ladders.
    """
    out = Counter()
    for m in market_files.values():
        team = (m.get("identity") or {}).get("team")
        if not team:
            continue
        out[team] += sum(1 for c in m.get("components", [])
                         if c.get("basis") == "MARKET" and c.get("rungs"))
    return out


def load_team_colors(con):
    """Team colours keyed on the abbreviation AS PUBLISHED.

    NOT folded through FRANCHISE. The whole point of keying on the published
    abbreviation is that STL and LA are different identities; mapping them to a
    current franchise here would undo that at the last step.

    Wider than TEAM_NAMES on purpose: 32 current franchises have pages, and the
    colour map also carries the predecessors (STL, SD, OAK) plus LAR, which
    nflverse publishes alongside LA for the same club.
    """
    rows = con.execute(
        "SELECT t.team_abbr, t.team_color, t.team_color2 FROM nfl_teams t "
        "JOIN (SELECT team_abbr, MAX(data_version) dv FROM nfl_teams GROUP BY team_abbr) v "
        "ON v.team_abbr = t.team_abbr AND v.dv = t.data_version"
    ).fetchall()
    return {a: {"primary": c1, "secondary": c2} for a, c1, c2 in rows if c1}


def load_team_groupings(con):
    """Conference and division keyed on the abbreviation AS PUBLISHED.

    The same join and the same rule as `load_team_colors`: newest
    `data_version` per abbreviation, and NOT folded through FRANCHISE, because
    STL and LA are different identities and keying on the published
    abbreviation is what keeps them apart. Wider than TEAM_NAMES for the same
    reason - the table carries 36 rows including STL, SD, OAK and the LAR that
    nflverse publishes alongside LA.

    PUBLISHED AS THE SOURCE HOLDS THEM, and this is the part worth knowing:
    `team_division` ALREADY CONTAINS THE CONFERENCE. The eight values are
    "AFC East" through "NFC West" - measured, not assumed - and no bare region
    is stored anywhere. So `division` is that atom verbatim and is never split
    into "West". Splitting it would publish a decomposition the source does not
    record, and it would be the wrong shape for a sport whose groupings are
    conferences rather than conference-plus-region.
    """
    rows = con.execute(
        "SELECT t.team_abbr, t.team_conf, t.team_division FROM nfl_teams t "
        "JOIN (SELECT team_abbr, MAX(data_version) dv FROM nfl_teams GROUP BY team_abbr) v "
        "ON v.team_abbr = t.team_abbr AND v.dv = t.data_version"
    ).fetchall()
    return {a: {"conference": c, "division": d} for a, c, d in rows}


def team_season_summaries(games, season, markets_by_slug):
    """{abbr: TeamSeasonSummary} for the CURRENT regular season.

    Track B's A14: the teams board wants a record, points and a market count per
    team, and `manifest.teams` carried {slug, abbr, name}. Getting one figure per
    team meant opening 32 team files on an index page, which is the exact cost
    `counts` was exported to remove - so the board shipped with its figures
    marked rather than inventing them.

    COMPONENTS, NOT DERIVED TOTALS. Season totals go out and the reader divides
    by `games`; `cleared`/`missed`/`tied` go out rather than a record string,
    because three integers cannot disagree with one another the way a string can
    disagree with its own parts.

    TIED IS HERE BECAUSE THE SPORT HAS TIES, and A14 did not ask for it.
    `ScheduleGame.result` is already `W`/`L`/`T`/null, so a {games, cleared,
    missed} triple silently loses a drawn result: `cleared + missed` stops
    equalling `games` and nothing on the page says why. Implementing the
    requested shape faithfully would have published a record that drops a
    result.

    `points_for` and `points_against` are NULL before a team has played, not 0:
    no games is a different statement from no points.

    `cumulative` is the season PATH the teams board draws (a-08): one entry per
    regular-season week in the league's frame, see `cumulative_path`. Its last
    played entry IS this summary's totals - the two are computed from one list
    of games and the export refuses if they ever disagree.
    """
    out = {}
    frame_weeks = season_frame(games, season)
    for abbr in TEAM_NAMES:
        reg = [g for g in games.values()
               if g["season"] == season and g["game_type"] == "REG"
               and abbr in (g["home_team"], g["away_team"])]
        played = [g for g in reg
                  if g["home_score"] is not None and g["away_score"] is not None]
        tally = Counter()
        pf = pa = 0
        for g in played:
            home = g["home_team"] == abbr
            for_, against = ((g["home_score"], g["away_score"]) if home
                             else (g["away_score"], g["home_score"]))
            pf += for_
            pa += against
            tally[result_of(for_, against)] += 1
        entry = {
            "games": len(played),
            "cleared": tally["W"], "missed": tally["L"], "tied": tally["T"],
            "points_for": intish(pf) if played else None,
            "points_against": intish(pa) if played else None,
            # DISTINCT PRICED MARKETS, not priced players - and looked up by
            # SLUG, because that is how a market file names its team.
            "markets": markets_by_slug.get(team_slug(abbr), 0),
            "cumulative": cumulative_path(reg, abbr, frame_weeks),
        }
        reconcile_path(abbr, entry)
        out[abbr] = entry
    return out


# The four states a week of the season path can be in. Three of them carry no
# value, and they are three different facts, so none of them is a zero.
PATH_STATES = ("played", "bye", "unplayed", "gap")


def season_frame(games, season):
    """The regular season's length in weeks: the highest REG week the league
    scheduled in `season`. 18 for 2026. 0 when nothing is scheduled.

    Taken from the schedule rather than hard-coded, so a season of another
    length is described as the length it has. The site's strip draws a frame of
    `config.seasonWeeks`; the two agree while the schedule has 18 weeks, and a
    test pins that on the real schedule.
    """
    wks = [g["week"] for g in games.values()
           if g["season"] == season and g["game_type"] == "REG" and g["week"] is not None]
    return max(wks) if wks else 0


def path_states(reg, frame_weeks):
    """{week: state} for one team's regular season - THE STRIP'S RESOLVER, ported.

    The team page's schedule strip (calibratedsports-web
    `components/views/TeamView.tsx` `slots()` over `lib/slotState.ts`) already
    decides played / bye / unplayed from the team's own schedule. The board's
    chart must not decide it a second way, so this is that rule line for line,
    in the same order:

      played    a REG fixture for that week carrying both scores - the strip's
                `result != null`, since `result_of` is null unless both are.
      bye       the ONE regular-season week, up to the team's last scheduled
                week, that has no fixture. If there are two or more such weeks
                the bye is UNRESOLVED - a cancelled game never made up looks
                exactly like a bye - and every one of them is `gap`, as the
                strip draws them. Guessing which was the bye is worse than one
                honest absence.
      gap       no fixture at all for that week, bye unresolved or past the
                team's last scheduled week. The strip's "no row for this
                period".
      unplayed  a fixture with no result yet. The strip splits this into
                `off` and `live` by comparing kickoff with the reader's clock;
                that split is a property of WHEN the file is read, not of the
                data, and neither side of it carries a value, so it is not
                exported. A reader wanting it has `kickoff_ts` on the team file.
    """
    by_week = {}
    for g in reg:
        if g["week"] is not None:
            by_week[g["week"]] = g
    scheduled = list(by_week)
    up_to = min(frame_weeks, max(scheduled)) if scheduled else 0
    gaps = [wk for wk in range(1, up_to + 1) if wk not in by_week]
    bye = gaps[0] if len(gaps) == 1 else None
    out = {}
    for wk in range(1, frame_weeks + 1):
        g = by_week.get(wk)
        if g is not None and g["home_score"] is not None and g["away_score"] is not None:
            out[wk] = "played"
        elif wk == bye:
            out[wk] = "bye"
        elif g is None:
            out[wk] = "gap"
        else:
            out[wk] = "unplayed"
    return out


def cumulative_path(reg, abbr, frame_weeks):
    """One entry per week 1..frame_weeks: the team's running record and points.

    RETROSPECTIVE ONLY. A played week carries the cumulative total through that
    week; a bye, an unplayed week and a gap carry NULL in every value - not 0,
    and not the previous week's total carried forward. No value is ever
    projected: the shell's forward projection was a hash of the team
    abbreviation and was ruled out, so the path fills in as the season runs
    and says nothing about weeks that have not happened.

    A bye's value IS knowable (it is the week before's), and it is still null:
    it is not an observation, and a reader drawing the line holds the last
    played value across it by reading `state`. Publishing it would put a figure
    on a week the team did not play.

    `cleared` is the chart's "Wins" tab; `missed` and `tied` travel with it so
    the running record sums to the games played, which a reader can check.
    """
    states = path_states(reg, frame_weeks)
    played_by_week = defaultdict(list)
    for g in reg:
        if states.get(g["week"]) == "played":
            played_by_week[g["week"]].append(g)
    tally, pf, pa = Counter(), 0, 0
    out = []
    for wk in range(1, frame_weeks + 1):
        st = states[wk]
        if st != "played":
            out.append({"index": wk, "state": st, "cleared": None, "missed": None,
                        "tied": None, "points_for": None, "points_against": None})
            continue
        for g in played_by_week[wk]:
            home = g["home_team"] == abbr
            for_, against = ((g["home_score"], g["away_score"]) if home
                             else (g["away_score"], g["home_score"]))
            pf += for_
            pa += against
            tally[result_of(for_, against)] += 1
        out.append({"index": wk, "state": st, "cleared": tally["W"], "missed": tally["L"],
                    "tied": tally["T"], "points_for": intish(pf), "points_against": intish(pa)})
    return out


def reconcile_path(abbr, summary):
    """The path's last played entry must BE the season totals beside it.

    Two statements of one figure on one page - the board's record and the end
    of that team's line - and a chart whose endpoint disagrees with the number
    printed next to it is the failure the shell's axis-ceiling note exists to
    prevent. They come from the same games, so a disagreement means a played
    game fell outside the frame (a REG week beyond `season_frame`, or a null
    week) - refuse rather than publish either.
    """
    played = [e for e in summary["cumulative"] if e["state"] == "played"]
    if not played:
        got = {"games": 0, "points_for": None, "points_against": None,
               "cleared": 0, "missed": 0, "tied": 0}
    else:
        last = played[-1]
        got = {"games": last["cleared"] + last["missed"] + last["tied"],
               "points_for": last["points_for"], "points_against": last["points_against"],
               "cleared": last["cleared"], "missed": last["missed"], "tied": last["tied"]}
    want = {k: summary[k] for k in got}
    if got != want:
        raise ValueError(f"{abbr}: season path ends at {got} but the season summary says {want}")


def build_manifest(games, current, index, market_keys, unresolved, source_version, scoring_note,
                   generated_at, rungs, team_colors, team_groupings, team_seasons,
                   stat_definitions=None, market_definitions=None, fixtures=None):
    # `fixtures` is None unless the staged feature built it, and then the key is
    # ABSENT - not an empty list, which would read as a week with no games.
    return {
        **envelope("sport_manifest", generated_at),
        "name": SPORT_NAME,
        "period_type": PERIOD_TYPE,
        "current": {"season": current["season"], "period": current["period"],
                    "data_through": current["data_through"],
                    "source_version": source_version,
                    "stale": current["stale"], "stale_reason": current["stale_reason"],
                    **({"fixtures": fixtures} if fixtures is not None else {})},
        "seasons": sorted({g["season"] for g in games.values()}),
        "stat_definitions": STAT_DEFINITIONS if stat_definitions is None else stat_definitions,
        "market_definitions": (MARKET_DEFINITIONS if market_definitions is None
                               else market_definitions),
        "scoring_presets": SCORING_PRESETS,
        "scoring_note": scoring_note,
        # Published as the source holds them: `division` is nflverse's
        # `team_division`, which ALREADY contains the conference ("NFC West"),
        # and is not split into a bare region the source never records.
        # `classification` is null because this sport has no competitive tier -
        # a null, not an invented value.
        "teams": [{"slug": team_slug(a), "abbr": a, "name": n,
                   "conference": (team_groupings.get(a) or {}).get("conference"),
                   "division": (team_groupings.get(a) or {}).get("division"),
                   "classification": None,
                   "season": team_seasons.get(a)}
                  for a, n in TEAM_NAMES.items()],
        "team_colors": team_colors,
        "counts": {"players": len(index), "teams": len(TEAM_NAMES), "market": len(market_keys),
                   "games": played(games), "rungs": rungs},
        "unresolved_ids": unresolved,
    }


def air_rz_census(player_files, airz, log=print):
    """What the staged air_rz keys published, printed on every run - null and
    zero counted apart, because "not recorded" and "none" are different claims
    and a total that folds them together hides which one moved."""
    out = {"games_covered": len(airz.covered)}
    for k in AIR_RZ_KEYS:
        c = Counter()
        for obj in player_files.values():
            if obj.get("kind") != "player_season":
                continue
            for p in obj["periods"]:
                if k not in p["stats"]:
                    c["absent"] += 1
                elif p["stats"][k] is None:
                    c["null"] += 1
                elif p["stats"][k] == 0:
                    c["zero"] += 1
                else:
                    c["nonzero"] += 1
        out[k] = dict(c)
        log(f"air_rz {k}: {c['nonzero']} non-zero, {c['zero']} zero, {c['null']} null, "
            f"{c['absent']} absent (all-zero season) period rows")
    return out


def extended_census(weeks, by_player, xwalk, ext, log=print):
    """What an extended run could and could not fill, printed on every run.

    THE STORE, NOT THE CODE, DECIDES HOW MUCH OF THIS IS NULL. The kicking and
    return columns exist in nfl_player_week only on rows written after a-14; an
    older row reads NULL, which the export publishes as null (see _ext_count).
    So a store that has not been re-derived from the archive yields a correct
    and nearly empty special-teams tab - and this is the line that says so,
    rather than a reader discovering it. Zero counts are printed too: a number
    that reads 0 most runs is what makes the run it reads 400 visible.
    """
    st_cols = [c for _, c in ST_MAP]
    stale = Counter()
    for r in weeks:
        if r["gsis_id"] in by_player and any(r.get(c) is None for c in st_cols):
            stale[r["season"]] += 1
    players = len(by_player)
    with_jersey = sum(1 for g, rows in by_player.items()
                      if ext.jerseys.get((g, rows[-1]["season"])) is not None)
    with_birth = sum(1 for g in by_player if (xwalk.get(g) or {}).get("birth_date"))
    out = {"players": players,
           "rows_special_teams_null": sum(stale.values()),
           "seasons_not_rederived": sorted(stale),
           "players_with_jersey": with_jersey, "players_with_birth_date": with_birth,
           "phase_snap_rows": len(ext.phase_snaps)}
    log(f"extended: {players} players; {out['rows_special_teams_null']} in-scope stat rows carry "
        f"NULL special-teams columns (seasons {out['seasons_not_rederived'] or 'none'}); "
        f"jersey {with_jersey}/{players}; birth_date {with_birth}/{players}")
    if stale:
        log("WARN extended: nfl_player_week has not been re-derived for those seasons - run "
            "`python -m jobs.ingest_nflverse --from-archive --dataset weekly_stats`, which writes "
            "the store; their special-teams stats publish as null until then")
    return out


def build_sports(generated_at):
    return {**envelope("sports", generated_at, None),
            "sports": [{"sport": SPORT, "name": SPORT_NAME, "manifest": f"{SPORT}/manifest.json"}]}


def ppr_check(weeks, scope):
    """Our preset PPR from components against nflverse's own fantasy_points_ppr.
    Stored nowhere - it only calibrates the scoring note."""
    w = SCORING_PRESETS["ppr"]["weights"]
    diffs = []
    for r in weeks:
        if r["gsis_id"] not in scope or r.get("fantasy_points_ppr") is None:
            continue
        ours = sum(w.get(key, 0) * num(r.get(col)) for col, key in STAT_MAP)
        diffs.append(abs(round(ours, 2) - r["fantasy_points_ppr"]))
    diffs.sort()
    if not diffs:
        return None, None, 0
    return statistics.median(diffs), diffs[min(len(diffs) - 1, int(0.99 * len(diffs)))], len(diffs)


# =============================================================================
# writing
# =============================================================================

def hold_published_markets(published, now_ts, dry_run=False):
    """Hold the quote history of every market the site is showing.

    Retention prunes live quotes at QUOTES_RETENTION_DAYS measured on ingestion
    time (invariant 8). That window is about bounding GROWTH, and it knows
    nothing about what is on the site - so a market published here would have
    its opening prices deleted while the page still drew them.

    The hold is renewed on every export, so it follows what is actually
    published: drop a market from the site and its hold simply ages out at the
    normal window. `until_ts` is one retention window from now rather than
    forever, because a hold nobody renews should expire rather than pin rows
    for good.
    """
    until = now_ts + config.QUOTES_RETENTION_DAYS * 86400
    # Reported on EVERY run, including a dry run and including zero - the same
    # rule as the two export exclusions. A dry run says what it WOULD hold;
    # silence would make "no markets published" and "the hold step never ran"
    # look identical from the summary.
    out = {"markets": len(published), "until": iso(until), "written": 0,
           "would_write": len(published) if dry_run else 0}
    if not published or dry_run:
        return out
    with store.db() as c:
        c.executemany(
            "INSERT INTO quote_retention_hold (venue, market_id, until_ts, reason, held_ts) "
            "VALUES (?, ?, ?, ?, ?) ON CONFLICT(venue, market_id) DO UPDATE SET "
            "until_ts = excluded.until_ts, held_ts = excluded.held_ts",
            [(v, m, until, HOLD_REASON, now_ts) for v, m in sorted(published)])
    out["written"] = len(published)
    return out


def local_path(dest, key):
    return os.path.join(dest, *key.split("/"))


def _canonical(obj):
    return json.dumps({k: v for k, v in obj.items() if k != "generated_at"},
                      sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def write_if_changed(path, obj, dry_run=False):
    """True when written. Unchanged content (ignoring generated_at) is left alone."""
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                if _canonical(json.load(f)) == _canonical(obj):
                    return False
        except (OSError, ValueError):
            pass
    if dry_run:
        return True
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, path)
    return True


def local_keys(dest):
    out = {}
    if not os.path.isdir(dest):
        return out
    for root, _dirs, files in os.walk(dest):
        for fn in files:
            if not fn.endswith(".json") or fn == STATE_FILE:
                continue
            path = os.path.join(root, fn)
            out[os.path.relpath(path, dest).replace(os.sep, "/")] = path
    return out


def sync_keys(dest, wanted, prefixes, dry_run=False):
    """Write {key: obj}; delete local *.json under `prefixes` no longer wanted.

    Nothing reaches disk unvalidated: this is the one choke point every
    exported file passes through, so the contract check lives here rather than
    at each call site, where a new part could forget it.
    """
    validate_contract(wanted)
    written = deleted = 0
    for key, obj in wanted.items():
        written += write_if_changed(local_path(dest, key), obj, dry_run)
    if prefixes:
        for key, path in local_keys(dest).items():
            if key not in wanted and any(key.startswith(p) for p in prefixes):
                if not dry_run:
                    os.remove(path)
                deleted += 1
        if not dry_run:
            for root, dirs, files in os.walk(dest, topdown=False):
                if root != dest and not dirs and not files:
                    os.rmdir(root)
    return written, deleted


class NumericStackBroken(RuntimeError):
    """This interpreter's numpy cannot compute. Refuse before writing anything."""


# `np.finfo(np.longdouble)` is the discriminator, measured 2026-09-17: it
# SEGFAULTS on the dev box's MINGW-W64 numpy (exit 139) and returns
# eps = 2.22e-16 on a clean 3.12 venv.
_NUMPY_PROBE = (
    "import math, sys; import numpy as np; "
    "fi = np.finfo(np.longdouble); e = float(fi.eps); "
    "sys.exit(0 if math.isfinite(e) and e > 0 else 3)"
)
_numeric_ok = None


def assert_numeric_stack(executable=None, timeout=60):
    """Refuse to export on an interpreter whose numpy dies when it computes.

    THE INCIDENT, 2026-09-17. `python -m jobs.export_web` on this box's default
    3.14 interpreter SEGFAULTED inside `build_research()` - exit 139, no Python
    exception, no traceback - after market, players and teams had been written
    and before research and manifest. Every file it had written was valid and
    contract-clean, so a TRUNCATED EXPORT WAS INDISTINGUISHABLE FROM A GOOD ONE:
    `--upload-only` pushed 4,910 correct keys to R2 beside an eight-hour-old
    manifest and reported success. Only checking the SERVED manifest's
    `generated_at` afterwards revealed it.

    So the rule is: a job that writes a published tree part by part must refuse
    at the START, where refusing costs nothing, rather than die in the middle,
    where the wreckage looks like success.

    IT RUNS IN A SUBPROCESS BECAUSE THE PROBE ITSELF SEGFAULTS. There is no
    exception to catch - `try: np.finfo(np.longdouble)` does not help, the
    interpreter is gone. Only a child process can report "it died" without
    dying, which is also why the check is an exit code and not a return value.

    Importing numpy proves nothing: `import core.distributions` succeeds on the
    broken build. The probe must make numpy COMPUTE.
    """
    global _numeric_ok
    if _numeric_ok is not None:
        return _numeric_ok
    import subprocess          # local: needed once per process, on this path only
    exe = executable or sys.executable
    try:
        r = subprocess.run([exe, "-c", _NUMPY_PROBE], capture_output=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as exc:
        raise NumericStackBroken(
            f"could not probe the numeric stack with {exe}: {type(exc).__name__}: {exc}") from exc
    if r.returncode != 0:
        tail = (r.stderr or b"")[-400:].decode("utf-8", "replace").strip()
        raise NumericStackBroken(
            f"numpy on {exe} cannot compute (probe exit {r.returncode}"
            f"{', SEGFAULT' if r.returncode in (139, -11, 0xC0000005) else ''}). "
            "The export writes the published tree part by part, so it refuses here "
            "rather than dying half way and leaving a truncated tree that uploads "
            "cleanly. Run it from the 3.12 venv."
            + (f"\n  probe stderr: {tail}" if tail else ""))
    _numeric_ok = True
    return True


def export(only=None, dry_run=False, now_ts=None, dest=None, log=print, registry_path=None,
           extended=False, stages=None):
    # BEFORE `dest` is resolved and long before anything is written.
    assert_numeric_stack()
    dest = dest or require_setting("WEB_EXPORT_DIR")
    parts = set(only or PARTS)
    if extended and "components" in parts:
        # The components table is a fixed-column projection of the OFFENSIVE
        # period keys (a-11). Which extended keys it should carry, and whether
        # defenders belong in a fantasy leaderboard at all, was not decided by
        # a-14 - so the two are not combined by accident.
        raise ConfigError("--extended does not build `components`; the columns for the "
                          "extended keys are undecided")
    stages = frozenset(DEFAULT_STAGES if stages is None else stages)
    unknown = stages - set(STAGES)
    if unknown:
        raise ConfigError(f"unknown stage(s) {sorted(unknown)}; known: {', '.join(STAGES)}")
    defs = {**STAT_DEFINITIONS, **EXT_STAT_DEFINITIONS} if extended else STAT_DEFINITIONS
    if "air_rz" in stages:
        defs = {**defs, **AIR_RZ_STAT_DEFINITIONS}
    mdefs = EXT_MARKET_DEFINITIONS if extended else MARKET_DEFINITIONS
    # THE DECLARATION: the prefixes this run actually rebuilt, accumulated beside
    # the sync_keys calls that own them and returned to the caller. `upload()`
    # deletes only inside these. It is never written to a file - a stale copy on
    # disk would authorise deletions for a run that did not happen.
    #
    # The prefix literals stay AT the call sites rather than moving into a shared
    # helper: tests/test_prefix_ownership.py resolves them from the AST and
    # asserts zero call sites it cannot evaluate, so routing them through a
    # variable would blind the guard that keeps track A out of `analytics/`.
    refreshed = []
    now_ts = time.time() if now_ts is None else now_ts
    generated_at = iso(now_ts)
    t0 = time.time()
    con = ro()
    games = load_games(con)
    weeks = load_player_weeks(con)
    xwalk, aliases = load_xwalk(con)
    snaps, snap_unresolved = load_snaps(con, xwalk)
    # Weeks a player PLAYED and recorded nothing - nflverse writes no row for
    # those, so the snap counts are the only evidence. See played_zero_periods.
    # A SECOND index rather than a wider load_snaps: that one keys on
    # (gsis, game_id) and is read at two call sites with no reason to change.
    # Costs one more pass over nfl_snap_counts; §3.4 makes the export
    # incremental and is where that scan stops being paid on every run.
    snap_index = snap_weeks(con, xwalk)
    current = current_period(games, weeks, now_ts)
    scope = player_scope(weeks, extended=extended)
    # The scoring note calibrates the OFFENSIVE presets against nflverse's PPR,
    # so it is measured over the offensive scope whatever this run exports - a
    # note that moved because kickers joined the denominator would be a
    # different claim wearing the same sentence.
    offensive_scope = player_scope(weeks)
    ext = None
    if extended:
        ext = ExtendedInputs(load_phase_snaps(con), load_jerseys(con))
    airz = AirRzInputs(*load_looks(con)) if "air_rz" in stages else None
    by_player = players_by_id(weeks, scope)
    # Before slugs: an excluded player must not append to the slug registry,
    # which is permanent.
    by_player, nameless = drop_nameless(by_player, xwalk)
    slugs, slugs_added = scope_slugs(by_player, xwalk, registry_path, dry_run)
    summary = {"current": current, "slugs_added": len(slugs_added),
               "excluded_no_name": {"count": len(nameless), "ids": sorted(nameless)}}
    log(f"excluded {len(nameless)} player(s) with no resolvable name"
        + (": " + ", ".join(sorted(nameless)) if nameless else ""))

    if "market" in parts:
        market, market_keys, census, published_markets = build_market(
            con, games, weeks, xwalk, slugs, current, now_ts, generated_at)
        summary["retention_holds"] = hold_published_markets(published_markets, now_ts, dry_run)
        assert_stats_defined(market, defs)
        summary["market"] = sync_keys(dest, market, [f"{SPORT}/market/"], dry_run)
        refreshed.append(f"{SPORT}/market/")
        summary["market_census"] = census
        summary["market_players"] = sorted((m["identity"]["name"] or m["identity"]["id"])
                                           for m in market.values())
    else:
        pkey = current["period"]["key"]
        market_keys = {}
        for key in local_keys(dest):
            parts_k = key.split("/")
            if len(parts_k) == 4 and parts_k[:2] == [SPORT, "market"] and parts_k[3] == f"{pkey}.json":
                market_keys[parts_k[2]] = key

    headshots = load_headshots(con)
    prop_history = load_prop_history(con, scope)
    player_files, index, unresolved = build_players(games, by_player, snaps, xwalk, aliases, slugs,
                                                    market_keys, generated_at, headshots,
                                                    snap_index=snap_index,
                                                    prop_history=prop_history, ext=ext,
                                                    airz=airz)
    summary["prop_history"] = {"players": len(prop_history),
                               "records": sum(len(v["records"]) for v in prop_history.values())}
    summary["headshots"] = {"with_url": sum(1 for g in by_player if g in headshots),
                            "players": len(by_player)}
    med, p99, n = ppr_check(weeks, offensive_scope)
    note = SCORING_NOTE_BASE if med is None else (
        f"{SCORING_NOTE_BASE} Against nflverse's own fantasy_points_ppr (which includes them) the "
        f"PPR preset differs by median {med:.2f} and p99 {p99:.2f} points per game over {n:,} "
        f"player-games.")
    summary["ppr_check"] = {"median_abs_diff": med, "p99_abs_diff": p99, "n": n}
    unresolved += [{"id": pfr, "name": name, "reason": "snap-count pfr id not in player_xwalk"}
                   for pfr, name in sorted(snap_unresolved.items())]
    unresolved += [nameless[gsis] for gsis in sorted(nameless)]
    summary["unresolved"] = unresolved
    summary["rows_without_team"] = sum(1 for u in unresolved if u["reason"] == REASON_NO_TEAM)
    log(f"{summary['rows_without_team']} player(s) had a period row with no team")
    collisions = {gsis: s for gsis, s in slugs.items() if slugify((xwalk.get(gsis) or {}).get("display_name")
                  or by_player[gsis][-1].get("player_name")) != s}
    summary["slug_collisions"] = collisions

    if "players" in parts:
        assert_stats_defined(player_files, defs)
        # Beside its sibling, not somewhere else: the two vocabularies are
        # checked at the same choke point so neither can be forgotten while the
        # other is remembered.
        assert_markets_defined(player_files, mdefs)
        index_obj = {**envelope("player_index", generated_at), "players": index}
        summary["players"] = sync_keys(dest, {**player_files, f"{SPORT}/players/index.json": index_obj},
                                       [f"{SPORT}/players/"], dry_run)
        refreshed.append(f"{SPORT}/players/")
    if "components" in parts:
        components, census = build_components(player_files, weeks, snap_index,
                                              load_team_snaps(con), generated_at,
                                              airz=airz is not None)
        assert_stats_defined(components, defs)
        summary["components_census"] = census
        summary["components"] = sync_keys(dest, components, [f"{SPORT}/components/"], dry_run)
        refreshed.append(f"{SPORT}/components/")
    if "teams" in parts:
        teams = build_teams(games, weeks, snaps, scope, xwalk, slugs, generated_at, ext=ext)
        assert_stats_defined(teams, defs)
        summary["teams"] = sync_keys(dest, teams, [f"{SPORT}/teams/"], dry_run)
        refreshed.append(f"{SPORT}/teams/")
    if "research" in parts:
        research = build_research(generated_at)
        summary["research"] = sync_keys(dest, research, ["research/"], dry_run)
        refreshed.append("research/")
    # THE MARKET OBJECTS, RESOLVED ONCE for every count derived from them.
    # Rungs come from the emitted objects when this run built them; on a run
    # without the market part they are read back off disk, because defaulting to
    # 0 there would publish "0 rungs" beside a non-zero market count, which reads
    # as a broken ladder rather than as a partial run.
    #
    # Hoisted out of the manifest branch 2026-09-19. `summary["counts"]` below
    # says it "mirrors the manifest's counts exactly" and was deriving `rungs`
    # INDEPENDENTLY - its own disk read, its own call - so the claim was an
    # intention rather than a guarantee. Two derivations of one number in one
    # function is how a run report comes to disagree with the file it just
    # wrote. This also drops the third `local_keys()` read; the summary already
    # paid for one on every run, so resolving once is strictly less work.
    if "market" in parts:
        market_files = market
    else:
        on_disk = local_keys(dest)
        market_files = {k: json.load(open(on_disk[k], encoding="utf-8"))
                        for k in market_keys.values() if k in on_disk}
    rungs = count_rungs(market_files)

    if "manifest" in parts:
        src = con.execute("SELECT MAX(data_version) FROM nflverse_versions "
                          "WHERE dataset = 'weekly_stats'").fetchone()[0]
        manifest = build_manifest(games, current, index, market_keys, unresolved, src, note,
                                  generated_at, rungs, load_team_colors(con),
                                  load_team_groupings(con),
                                  team_season_summaries(games, current["season"],
                                                        count_markets_by_team(market_files)),
                                  stat_definitions=defs, market_definitions=mdefs,
                                  fixtures=(current_fixtures(games, current)
                                            if "fixtures" in stages else None))
        assert_stats_defined({f"{SPORT}/manifest.json": manifest}, defs)
        summary["manifest"] = sync_keys(dest, {f"{SPORT}/manifest.json": manifest,
                                               "sports.json": build_sports(generated_at)},
                                        [], dry_run)
    con.close()
    # Mirrors the manifest's counts exactly. A run report that says something
    # different from the file it just wrote is worse than one that says less.
    summary["counts"] = {"players": len(index), "teams": len(TEAM_NAMES), "market": len(market_keys),
                         "games": played(games), "rungs": rungs}
    # The manifest part deliberately owns no prefix (it passes []), so a
    # manifest-only run returns [] here: DECLARED, and owns nothing. That is a
    # different fact from `None`, which means nobody said - see `upload()`.
    if ext is not None:
        summary["extended"] = extended_census(weeks, by_player, xwalk, ext, log)
    summary["stages"] = sorted(stages)
    if airz is not None:
        summary["air_rz"] = air_rz_census(player_files, airz, log)
    summary["refreshed"] = refreshed
    summary["runtime_s"] = round(time.time() - t0, 1)
    if current["stale"]:
        log(f"WARN nflverse is late: {current['stale_reason']}")
    return summary


# =============================================================================
# upload
# =============================================================================

def r2_client():
    if not config.R2_ENDPOINT:
        raise ConfigError("R2_ENDPOINT (or R2_ACCOUNT_ID) is not set - the R2 endpoint comes "
                          "from the logger's R2 account settings")
    import boto3
    from botocore.config import Config
    return boto3.client(
        "s3", endpoint_url=config.R2_ENDPOINT, region_name="auto",
        aws_access_key_id=config.WEB_R2_ACCESS_KEY_ID,
        aws_secret_access_key=config.WEB_R2_SECRET_ACCESS_KEY,
        config=Config(signature_version="s3v4", retries={"max_attempts": 5, "mode": "standard"},
                      max_pool_connections=UPLOAD_WORKERS * 2))


def _save_state(path, state, client=None, bucket=None):
    """Write the upload record locally, and mirror it into the bucket.

    A failed mirror is logged nowhere and raises nothing: the local copy is
    authoritative for the machine that did the uploading, and an upload must not
    fail because its bookkeeping could not be copied.
    """
    blob = json.dumps(state, sort_keys=True, separators=(",", ":"))
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(blob)
    os.replace(tmp, path)
    if client is not None and bucket:
        try:
            client.put_object(Bucket=bucket, Key=REMOTE_STATE_KEY,
                              Body=blob.encode("utf-8"), ContentType="application/json",
                              CacheControl="no-store")
        except Exception:
            pass


def _remote_state(client, bucket):
    """The record as the bucket last saw it, or None."""
    try:
        return json.loads(client.get_object(Bucket=bucket, Key=REMOTE_STATE_KEY)["Body"].read())
    except Exception:
        return None


def _bucket_keys(client, bucket):
    """Every key actually in the bucket, or None if it cannot be listed."""
    try:
        if hasattr(client, "get_paginator"):
            keys = set()
            for page in client.get_paginator("list_objects_v2").paginate(Bucket=bucket):
                keys |= {o["Key"] for o in page.get("Contents", ())}
            return keys
        return {o["Key"] for o in client.list_objects_v2(Bucket=bucket).get("Contents", ())}
    except Exception:
        return None


def load_upload_state(dest, client, bucket, log=print):
    """The upload record: local first, else the bucket's copy. -> (state, source).

    A record that does not describe the bucket is WORSE than no record, because
    every key it wrongly claims is a key that silently never gets uploaded. So a
    recovered record is reconciled against a listing and anything the bucket
    does not actually hold is dropped; if the bucket cannot be listed we return
    nothing and re-upload, which is expensive and correct rather than cheap and
    wrong.
    """
    path = os.path.join(dest, STATE_FILE)
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f), "local"
    except (OSError, ValueError):
        pass
    remote = _remote_state(client, bucket)
    if not remote:
        return {}, "none"
    present = _bucket_keys(client, bucket)
    if present is None:
        log("  found a remote upload record but could not list the bucket - "
            "re-uploading rather than trusting it")
        return {}, "unverified"
    stale = [k for k in remote if k not in present]
    for k in stale:
        remote.pop(k)
    log(f"  recovered the upload record from R2: {len(remote):,} keys"
        + (f", {len(stale):,} dropped as absent from the bucket" if stale else ""))
    return remote, "r2"


def upload(dest=None, client=None, dry_run=False, log=print, workers=UPLOAD_WORKERS,
           refreshed=None):
    """Upload keys whose sha256 differs from the local upload record, and delete
    keys that were removed FROM A PREFIX THIS RUN REFRESHED.

    `refreshed` is the declaration: the prefixes the run that produced this
    export actually rebuilt. It is PASSED, never persisted - see below.

    WHY DELETION IS SCOPED. The old rule was `set(state) - set(local)`: a key in
    the upload record and not on disk was deleted from R2. That reads absence as
    intent, and absence is not information. `local_keys(dest)` walks
    WEB_EXPORT_DIR only, so every key any OTHER producer publishes - track F's 88
    analytics keys, written to its own root - computes as "removed" and is
    deleted on the next run. `weekly_refresh` runs `--upload-only`
    unconditionally, so that is a SCHEDULED deletion, not a hazard someone might
    trigger. Same defect as `sync_keys` owning a prefix it does not fill, one
    layer down and remote, where there is no local copy to restore from.

    NONE AND [] BOTH WITHHOLD, AND ARE REPORTED DIFFERENTLY.
      None - no declaration. The caller did not say what it rebuilt, so nothing
             is known about what SHOULD be absent. Withhold.
      []   - declared, and owns nothing (a manifest-only run). Also withhold, but
             it is a different fact and a reader should be able to tell them
             apart when `removed_withheld` is non-zero.

    READING `removed_withheld`:
      * non-zero with NO declaration is expected and benign - the run simply did
        not say, so nothing was deleted.
      * non-zero WITH a declaration is a SIGNAL: keys went missing locally that
        lie outside every prefix the run rebuilt, which should not happen in a
        full run. If the declaration ever silently stops being threaded through,
        a climbing `removed_withheld` is the only thing that will say so.

    THE DECLARATION IS NEVER WRITTEN DOWN. It travels as an argument, from the
    run that produced it, and nowhere else. Persisting it would let a stale copy
    authorise deletions for a run that never happened - this same defect wearing
    a fresh coat.
    """
    dest = dest or require_setting("WEB_EXPORT_DIR")
    if not (config.WEB_R2_ACCESS_KEY_ID and config.WEB_R2_SECRET_ACCESS_KEY):
        log("R2 upload not configured (WEB_R2_ACCESS_KEY_ID / WEB_R2_SECRET_ACCESS_KEY unset) - "
            "local export only")
        return {"configured": False}
    bucket = require_setting("WEB_R2_BUCKET")
    client = client or r2_client()
    state_path = os.path.join(dest, STATE_FILE)
    state, state_source = load_upload_state(dest, client, bucket, log)

    # `live/` BELONGS TO THE LOGGER (unit a-09), which PUTs it every few seconds
    # and never records it here. This uploader may not claim it, may not upload
    # into it and may not delete from it - a declaration reaching it is refused
    # outright rather than narrowed, because a caller who believes it owns
    # `live/` has a wrong model that narrowing would hide.
    foreign = [p for p in (refreshed or [])
               if p == "" or p.startswith(LIVE_PREFIX) or LIVE_PREFIX.startswith(p)]
    if foreign:
        raise ValueError(f"declared prefixes {foreign} reach {LIVE_PREFIX!r}, which the logger "
                         "publishes and this uploader never owns")
    local = local_keys(dest)
    # A `live/` file in the export tree is a stale copy by definition - the only
    # writer of that key is the logger, straight to R2. Uploading it would
    # overwrite a fresh price with an old one. Skipped, and counted.
    live_skipped = sorted(k for k in local if k.startswith(LIVE_PREFIX))
    for k in live_skipped:
        local.pop(k)
    todo = []
    for key, path in sorted(local.items()):
        with open(path, "rb") as f:
            data = f.read()
        sha = hashlib.sha256(data).hexdigest()
        if state.get(key) != sha:
            todo.append((key, data, sha))
    absent = sorted(set(state) - set(local))
    # Absence fails toward KEEPING data. A key is deleted only when the run that
    # produced this export says it rebuilt the prefix the key lives under.
    if refreshed is None:
        removed, withheld, declared = [], absent, None
    else:
        removed = [k for k in absent if any(k.startswith(p) for p in refreshed)
                   and not k.startswith(LIVE_PREFIX)]
        withheld = [k for k in absent if k not in set(removed)]
        declared = list(refreshed)
    result = {"configured": True, "bucket": bucket, "considered": len(local),
              "changed": len(todo), "uploaded": 0, "deleted": 0, "bytes": 0,
              "removed": len(removed), "removed_withheld": len(withheld),
              # WHICH prefix went unclaimed, not just how many keys did. Once a
              # second producer writes into this tree, a bare count cannot say
              # whether the withheld keys are a benign undeclared run or a
              # retired metric that will stay served from R2 forever. The count
              # is the signal; this is its subject.
              "withheld_prefixes": sorted({k.split("/")[0] + "/" for k in withheld}),
              "declared_prefixes": declared, "state_source": state_source,
              "live_skipped": len(live_skipped)}
    if live_skipped:
        log(f"  WARN {len(live_skipped)} file(s) under {LIVE_PREFIX} in the export tree were NOT "
            "uploaded: the logger is that prefix's only writer, and a local copy is stale")
    if withheld:
        # Surfaced on every run, because the number only matters when someone
        # sees it. A declaration that silently stops being threaded shows up
        # here and nowhere else.
        if declared is None:
            log(f"  {len(withheld):,} key(s) absent locally and NOT deleted: this run "
                "declared no refreshed prefixes, so absence carries no information")
        else:
            log(f"  WARN {len(withheld):,} key(s) absent locally but outside every declared "
                f"prefix {declared} - not deleted. They sit under "
                f"{sorted({k.split('/')[0] + '/' for k in withheld})}, which nothing in this run "
                "claimed to have rebuilt. In a full run this should be 0; a climbing count means "
                "either the declaration is not reaching the uploader, or a producer that writes "
                "into this tree is not declaring its own prefix")
    if dry_run:
        return result

    def put(item):
        key, data, sha = item
        client.put_object(Bucket=bucket, Key=key, Body=data, ContentType="application/json",
                          CacheControl=cache_control(key))
        return key, sha, len(data)

    try:
        with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            for i, (key, sha, size) in enumerate(pool.map(put, todo), 1):
                state[key] = sha
                result["uploaded"] += 1
                result["bytes"] += size
                if i % 500 == 0:
                    _save_state(state_path, state, client, bucket)
                    log(f"  uploaded {i:,}/{len(todo):,}")
        for key in removed:
            client.delete_object(Bucket=bucket, Key=key)
            state.pop(key, None)
            result["deleted"] += 1
    finally:
        _save_state(state_path, state, client, bucket)
    return result


def staged_registry(dest, sport=SPORT):
    """A COPY of the committed slug registry inside the staging tree.

    The registry is append-only and permanent: a slug, once assigned, is a
    public URL. An extended build assigns thousands of new ones (defenders,
    kickers), and letting a staging run append them to `web/slugs/` would
    decide those URLs ahead of the publish. Seeded from the committed file, not
    empty, so the staged slugs are exactly what the real run would append -
    seeding empty would re-run first-seeding and hand bare slugs out in a
    different order. Kept BESIDE the staging tree, never inside it: everything
    under `dest` is a candidate key to `local_keys`.
    """
    dest = os.path.abspath(dest)
    path = os.path.join(os.path.dirname(dest), os.path.basename(dest) + "-staged-slugs",
                        f"{sport}.json")
    if not os.path.exists(path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        src = slug_registry_path(sport)
        with open(src, encoding="utf-8") as f:
            data = f.read()
        with open(path, "w", encoding="utf-8") as f:
            f.write(data)
    return path


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--only", action="append", choices=PARTS + OPTIONAL_PARTS)
    ap.add_argument("--dest", default=None,
                    help="export into this directory instead of WEB_EXPORT_DIR - a staging tree. "
                         "Refused with --upload/--upload-only, which read WEB_EXPORT_DIR and would "
                         "ship a tree other than the one just built.")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--upload", action="store_true", help="export, then upload changed keys to R2")
    ap.add_argument("--upload-only", action="store_true",
                    help="upload the existing local export without exporting again")
    ap.add_argument("--refreshed", default=None,
                    help="space-separated prefixes the export that produced this tree rebuilt. "
                         "Deletion from R2 is scoped to these. Omit it and NOTHING is deleted: "
                         "absence is not information. Never read from a file - pass it from the "
                         "export run that produced the tree, in the same job.")
    ap.add_argument("--extended", action="store_true",
                    help="a-14: add defence, special teams, jersey and birth date, and widen the "
                         "player scope to defenders and specialists. STAGED ONLY - requires --dest, "
                         "and uses a staged copy of the slug registry so the committed one is never "
                         "appended to.")
    ap.add_argument("--stage", action="append", choices=STAGES, default=[],
                    help="a-15: build a staged feature - `fixtures` (current.fixtures on the "
                         "manifest) or `air_rz` (air yards and red-zone looks). Requires --dest; "
                         "publishing one is adding it to DEFAULT_STAGES, a code change.")
    a = ap.parse_args(argv)
    if a.stage and not a.dest:
        # The same gate as --extended, for the same reason: this job runs from
        # the working clone's checked-out branch and uploads what it builds.
        ap.error("--stage is staged: pass --dest. Publishing it is a decision, not a flag.")
    if a.dest and (a.upload or a.upload_only):
        ap.error("--dest stages a tree; the uploader reads WEB_EXPORT_DIR. Refusing to pair them.")
    if a.extended and not a.dest:
        # THE GATE. The weekly refresh runs `python -m jobs.export_web` from
        # whatever branch its clone has checked out and uploads the result, so
        # an extended build reachable without --dest is one merge away from
        # being published. Enabling the profile for real is a code change, made
        # on purpose.
        ap.error("--extended is staged: pass --dest. Publishing it is a decision, not a flag.")
    registry_path = None
    if a.extended or a.stage:
        # A staged build never appends to the committed registry, whatever it
        # stages: web/slugs is public URLs and only a real refresh may grow it.
        registry_path = staged_registry(a.dest)
    refreshed = None
    if not a.upload_only:
        s = export(only=a.only, dry_run=a.dry_run, dest=a.dest, extended=a.extended,
                   registry_path=registry_path,
                   stages=(DEFAULT_STAGES + tuple(a.stage)) if a.stage else None)
        refreshed = s.get("refreshed")
        printable = {k: v for k, v in s.items()
                     if k not in ("unresolved", "market_players", "slug_collisions")}
        print(json.dumps(printable, indent=1, default=str))
        print(f"unresolved ids: {len(s['unresolved'])}; slug collisions resolved: "
              f"{len(s['slug_collisions'])}")
        if s.get("market_players") is not None:
            print(f"market players ({len(s['market_players'])}): {', '.join(s['market_players'][:40])}")
        # THE SENTINEL. One line, last, so a caller in the same job can read what
        # this run rebuilt and hand it to the uploader on the command line.
        # Deliberately not the JSON summary: parsing that couples two jobs to a
        # shape that changes often, and a failed parse is indistinguishable from
        # a run that declared nothing - which is the exact distinction this
        # whole mechanism exists to preserve. An ABSENT sentinel means "no
        # declaration"; an empty one means "declared, owns nothing".
        print(REFRESHED_SENTINEL + " " + " ".join(refreshed))
    elif a.refreshed is not None:
        refreshed = a.refreshed.split()
    if a.upload or a.upload_only:
        print(json.dumps(upload(dry_run=a.dry_run, refreshed=refreshed), indent=1))
    return 0


def parse_refreshed(text):
    """The prefixes from an export's stdout, or None when it did not say.

    The consumer half of the sentinel, kept beside the producer so the two
    cannot drift apart in separate files. Returns None for absent - no
    declaration - and [] for a run that declared it rebuilt nothing.
    """
    for line in reversed((text or "").splitlines()):
        if line.startswith(REFRESHED_SENTINEL):
            return line[len(REFRESHED_SENTINEL):].split()
    return None


if __name__ == "__main__":
    sys.exit(main())
