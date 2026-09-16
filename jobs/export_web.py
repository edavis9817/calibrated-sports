"""Export the website's data - contract v2 (docs/web-schema.md).

    python -m jobs.export_web                   # export everything to WEB_EXPORT_DIR
    python -m jobs.export_web --only players    # players | teams | market | research | manifest
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
PARTS = ("players", "teams", "market", "research", "manifest")
STATE_FILE = ".upload_state.json"
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
            ("passing_yards", "pass_yds"), ("passing_tds", "pass_td"), ("interceptions", "int"))
MISSING_COMPONENTS = ("fum_lost", "two_pt")     # not projected by nfl_player_week
COUNT_KEYS = tuple(k for _, k in STAT_MAP) + MISSING_COMPONENTS
PERIOD_KEYS = ("snaps", "snap_share", "targets", "target_share", "rec", "rec_yds", "rec_td",
               "rush_att", "rush_yds", "rush_td", "pass_att", "pass_cmp", "pass_yds", "pass_td",
               "int", "fum_lost", "two_pt")
DEF_COLUMNS = (("def_tkl_solo", "def_tackles_solo"), ("def_tkl_with_assist", "def_tackles_with_assist"),
               ("def_tkl_ast", "def_tackle_assists"), ("def_tfl", "def_tackles_for_loss"),
               ("def_sacks", "def_sacks"), ("def_qb_hits", "def_qb_hits"),
               ("def_int", "def_interceptions"), ("def_pd", "def_pass_defended"),
               ("def_ff", "def_fumbles_forced"), ("def_td", "def_tds"),
               ("def_safeties", "def_safeties"))


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
}

_BASE_WEIGHTS = {"rec": 1, "rec_yds": 0.1, "rec_td": 6, "rush_yds": 0.1, "rush_td": 6,
                 "pass_yds": 0.04, "pass_td": 4, "int": -2, "fum_lost": -2, "two_pt": 2}
SCORING_PRESETS = {
    "ppr": {"label": "PPR", "weights": dict(_BASE_WEIGHTS), "bonuses": []},
    "half": {"label": "Half PPR", "weights": dict(_BASE_WEIGHTS, rec=0.5), "bonuses": []},
    "standard": {"label": "Standard", "weights": dict(_BASE_WEIGHTS, rec=0), "bonuses": []},
}
# research/implied.py names its scorings differently; the distributions keep the
# manifest's preset keys.
IMPLIED_SCORING = {"ppr": "ppr", "half": "half_ppr", "standard": "standard"}
SCORING_NOTE_BASE = ("Scored from the components present. fum_lost and two_pt are null in the "
                     "NFL source table (nfl_player_week) and score 0.")

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


def load_snaps(con, xwalk):
    pfr_to_gsis = {r["pfr_id"]: g for g, r in xwalk.items() if r.get("pfr_id")}
    snaps, unresolved = {}, {}
    for pfr, gid, off, pct, name in con.execute(
            "SELECT s.pfr_player_id, s.game_id, s.offense_snaps, s.offense_pct, s.player "
            "FROM nfl_snap_counts s JOIN (SELECT pfr_player_id, game_id, MAX(data_version) dv "
            "FROM nfl_snap_counts GROUP BY pfr_player_id, game_id) v "
            "ON v.pfr_player_id = s.pfr_player_id AND v.game_id = s.game_id "
            "AND v.dv = s.data_version"):
        g = pfr_to_gsis.get(pfr)
        if g is None:
            unresolved[pfr] = name
            continue
        snaps[(g, gid)] = (off, pct)
    return snaps, unresolved


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


def player_scope(weeks):
    """v1 scope, kept: any regular-season week with offensive usage."""
    return {r["gsis_id"] for r in weeks if r["season_type"] == "REG"
            and num(r["targets"]) + num(r["carries"]) + num(r["attempts"]) > 0}


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
    return intish(r.get(col)) if r.get(col) is not None else 0


def _totals(periods):
    shares = [p["stats"]["snap_share"] for p in periods if p["stats"]["snap_share"] is not None]
    stats = {}
    for k in COUNT_KEYS:
        stats[k] = None if k in MISSING_COMPONENTS else intish(sum(num(p["stats"][k]) for p in periods))
    stats["snap_share_mean"] = rnd(statistics.fmean(shares)) if shares else None
    return stats


def build_players(games, by_player, snaps, xwalk, aliases, slugs, market_keys, generated_at,
                  headshots=None):
    """-> ({key: obj} for summaries and season files, [index entries], unresolved)."""
    headshots = headshots or {}
    gidx = game_index(games)
    files, index, unresolved = {}, [], []
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
                   "target_share": rnd(r.get("target_share"))}
            for col, key in STAT_MAP:
                raw[key] = _count(r, col)
            for key in MISSING_COMPONENTS:
                raw[key] = None
            periods.append({
                "season": r["season"], "index": r["week"],
                "label": period_label(None if g is None else g.get("game_type"), r["week"],
                                      r["season_type"]),
                "season_type": r["season_type"],
                "game_id": None if g is None else g["game_id"],
                "date": None if g is None else g["gameday"],
                "team": r["team"], "opponent": opp, "home": home,
                "stats": {k: raw[k] for k in PERIOD_KEYS}})

        if any(p["team"] is None for p in periods):
            unresolved.append({"id": gsis, "name": name, "reason": REASON_NO_TEAM})

        by_season = defaultdict(list)
        for p in periods:
            by_season[p["season"]].append(p)
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
            season_entries.append({"season": season, "teams": teams, "games": len(ps), "key": key})

        totals = []
        grouped = defaultdict(list)
        for p in periods:
            grouped[(p["season"], p["season_type"])].append(p)
        for (season, stype) in sorted(grouped, key=lambda k: (k[0], k[1] != "REG")):
            ps = grouped[(season, stype)]
            totals.append({"season": season, "season_type": stype, "games": len(ps),
                           "stats": _totals(ps)})
        reg = [p for p in periods if p["season_type"] == "REG"]

        files[f"{SPORT}/players/{gsis}/summary.json"] = {
            **envelope("player_summary", generated_at),
            "identity": {"id": gsis, "slug": slug, "name": name, "position": position,
                         "team": latest.get("team"),
                         "ids": {"gsis": gsis, "pfr": x.get("pfr_id"), "espn": x.get("espn_id"),
                                 "sleeper": x.get("sleeper_id"), "yahoo": x.get("yahoo_id"),
                                 "pff": x.get("pff_id")},
                         "aliases": sorted(aliases.get(gsis, ())),
                         "headshot_url": headshots.get(gsis)},
            "seasons": season_entries,
            "season_totals": totals,
            "career": {"season_type": "REG", "games": len(reg), "stats": _totals(reg)},
            "market": {"key": market_keys[gsis]} if gsis in market_keys else None,
        }
        index.append({"id": gsis, "slug": slug, "name": name, "position": position,
                      "team": latest.get("team"), "first_season": rows[0]["season"],
                      "last_season": rows[-1]["season"],
                      "aliases": sorted(aliases.get(gsis, ())),
                      "has_market": gsis in market_keys})
    index.sort(key=lambda p: (p["name"] or "", p["id"]))
    return files, index, unresolved


def build_teams(games, weeks, snaps, scope, xwalk, slugs, generated_at):
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

                def tot(col):
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
            team_tgt = sum(num(r["targets"]) for r in prows)
            team_car = sum(num(r["carries"]) for r in prows)
            per = defaultdict(list)
            for r in prows:
                per[r["gsis_id"]].append(r)
            for gsis, rs in per.items():
                shares = []
                for r in rs:
                    g = gidx.get((r["season"], r["week"], abbr))
                    sn = snaps.get((gsis, g["game_id"])) if g else None
                    if sn and sn[1] is not None:
                        shares.append(sn[1])
                xw = xwalk.get(gsis) or {}
                roster.append({
                    "season": season, "id": gsis, "slug": slugs.get(gsis),
                    "name": xw.get("display_name") or rs[-1].get("player_name"),
                    "position": xw.get("position") or rs[-1].get("position"),
                    "games": len(rs),
                    "snap_share": rnd(statistics.fmean(shares)) if shares else None,
                    "target_share": rnd(sum(num(r["targets"]) for r in rs) / team_tgt) if team_tgt else None,
                    "carry_share": rnd(sum(num(r["carries"]) for r in rs) / team_car) if team_car else None,
                    "has_page": gsis in scope})
            roster.sort(key=lambda p: (-(p["snap_share"] or 0), p["name"] or ""))

        files[f"{SPORT}/teams/{slug}.json"] = {
            **envelope("team", generated_at),
            "identity": {"slug": slug, "abbr": abbr, "name": name},
            "seasons": seasons, "schedule": schedule, "splits": splits, "roster": roster,
            "coaches": [{"season": s, "head_coach": c.most_common(1)[0][0]}
                        for s, c in sorted(coaches.items())]}
    return files


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
    out["research/calibration.json"] = {
        **envelope("research.calibration", generated_at, None),
        "source": "research/score.py (brief 021)",
        "population": (f"NFL week 1 2026, KXNFLREC + KXNFLRSHATT, common set n={len(common)}, "
                       f"{len({r['game'] for r in common})} games"),
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


def build_manifest(games, current, index, market_keys, unresolved, source_version, scoring_note,
                   generated_at):
    return {
        **envelope("sport_manifest", generated_at),
        "name": SPORT_NAME,
        "period_type": PERIOD_TYPE,
        "current": {"season": current["season"], "period": current["period"],
                    "data_through": current["data_through"],
                    "source_version": source_version,
                    "stale": current["stale"], "stale_reason": current["stale_reason"]},
        "seasons": sorted({g["season"] for g in games.values()}),
        "stat_definitions": STAT_DEFINITIONS,
        "scoring_presets": SCORING_PRESETS,
        "scoring_note": scoring_note,
        "teams": [{"slug": team_slug(a), "abbr": a, "name": n} for a, n in TEAM_NAMES.items()],
        "counts": {"players": len(index), "teams": len(TEAM_NAMES), "market": len(market_keys)},
        "unresolved_ids": unresolved,
    }


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


def export(only=None, dry_run=False, now_ts=None, dest=None, log=print, registry_path=None):
    dest = dest or require_setting("WEB_EXPORT_DIR")
    parts = set(only or PARTS)
    now_ts = time.time() if now_ts is None else now_ts
    generated_at = iso(now_ts)
    t0 = time.time()
    con = ro()
    games = load_games(con)
    weeks = load_player_weeks(con)
    xwalk, aliases = load_xwalk(con)
    snaps, snap_unresolved = load_snaps(con, xwalk)
    current = current_period(games, weeks, now_ts)
    scope = player_scope(weeks)
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
        assert_stats_defined(market, STAT_DEFINITIONS)
        summary["market"] = sync_keys(dest, market, [f"{SPORT}/market/"], dry_run)
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
    player_files, index, unresolved = build_players(games, by_player, snaps, xwalk, aliases, slugs,
                                                    market_keys, generated_at, headshots)
    summary["headshots"] = {"with_url": sum(1 for g in by_player if g in headshots),
                            "players": len(by_player)}
    med, p99, n = ppr_check(weeks, scope)
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
        assert_stats_defined(player_files, STAT_DEFINITIONS)
        index_obj = {**envelope("player_index", generated_at), "players": index}
        summary["players"] = sync_keys(dest, {**player_files, f"{SPORT}/players/index.json": index_obj},
                                       [f"{SPORT}/players/"], dry_run)
    if "teams" in parts:
        teams = build_teams(games, weeks, snaps, scope, xwalk, slugs, generated_at)
        assert_stats_defined(teams, STAT_DEFINITIONS)
        summary["teams"] = sync_keys(dest, teams, [f"{SPORT}/teams/"], dry_run)
    if "research" in parts:
        research = build_research(generated_at)
        summary["research"] = sync_keys(dest, research, ["research/"], dry_run)
    if "manifest" in parts:
        src = con.execute("SELECT MAX(data_version) FROM nflverse_versions "
                          "WHERE dataset = 'weekly_stats'").fetchone()[0]
        manifest = build_manifest(games, current, index, market_keys, unresolved, src, note,
                                  generated_at)
        assert_stats_defined({f"{SPORT}/manifest.json": manifest}, STAT_DEFINITIONS)
        summary["manifest"] = sync_keys(dest, {f"{SPORT}/manifest.json": manifest,
                                               "sports.json": build_sports(generated_at)},
                                        [], dry_run)
    con.close()
    summary["counts"] = {"players": len(index), "teams": len(TEAM_NAMES), "market": len(market_keys)}
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


def _save_state(path, state):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, sort_keys=True, separators=(",", ":"))
    os.replace(tmp, path)


def upload(dest=None, client=None, dry_run=False, log=print, workers=UPLOAD_WORKERS):
    """Upload keys whose sha256 differs from the local upload record, delete keys
    that were removed. The record (.upload_state.json) is never uploaded."""
    dest = dest or require_setting("WEB_EXPORT_DIR")
    if not (config.WEB_R2_ACCESS_KEY_ID and config.WEB_R2_SECRET_ACCESS_KEY):
        log("R2 upload not configured (WEB_R2_ACCESS_KEY_ID / WEB_R2_SECRET_ACCESS_KEY unset) - "
            "local export only")
        return {"configured": False}
    bucket = require_setting("WEB_R2_BUCKET")
    client = client or r2_client()
    state_path = os.path.join(dest, STATE_FILE)
    try:
        with open(state_path, encoding="utf-8") as f:
            state = json.load(f)
    except (OSError, ValueError):
        state = {}

    local = local_keys(dest)
    todo = []
    for key, path in sorted(local.items()):
        with open(path, "rb") as f:
            data = f.read()
        sha = hashlib.sha256(data).hexdigest()
        if state.get(key) != sha:
            todo.append((key, data, sha))
    removed = sorted(set(state) - set(local))
    result = {"configured": True, "bucket": bucket, "considered": len(local),
              "changed": len(todo), "uploaded": 0, "deleted": 0, "bytes": 0,
              "removed": len(removed)}
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
                    _save_state(state_path, state)
                    log(f"  uploaded {i:,}/{len(todo):,}")
        for key in removed:
            client.delete_object(Bucket=bucket, Key=key)
            state.pop(key, None)
            result["deleted"] += 1
    finally:
        _save_state(state_path, state)
    return result


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--only", action="append", choices=PARTS)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--upload", action="store_true", help="export, then upload changed keys to R2")
    ap.add_argument("--upload-only", action="store_true",
                    help="upload the existing local export without exporting again")
    a = ap.parse_args(argv)
    if not a.upload_only:
        s = export(only=a.only, dry_run=a.dry_run)
        printable = {k: v for k, v in s.items()
                     if k not in ("unresolved", "market_players", "slug_collisions")}
        print(json.dumps(printable, indent=1, default=str))
        print(f"unresolved ids: {len(s['unresolved'])}; slug collisions resolved: "
              f"{len(s['slug_collisions'])}")
        if s.get("market_players") is not None:
            print(f"market players ({len(s['market_players'])}): {', '.join(s['market_players'][:40])}")
    if a.upload or a.upload_only:
        print(json.dumps(upload(dry_run=a.dry_run), indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
