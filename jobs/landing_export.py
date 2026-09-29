"""`landing.json` - the one file the landing page reads (unit a-47).

    python -m jobs.landing_export --check                 # build from WEB_EXPORT_DIR, write nothing
    python -m jobs.landing_export --write --dest web      # write landing.json into WEB_EXPORT_DIR
    python -m jobs.landing_export --write --dest D:/scratch/web --board D:/x/board_export

WHY ONE FILE. The landing draws nine scenes off figures that live in six or seven
files, several per-sport and one not published at all. A page that assembles a
headline number from seven fetches can assemble a wrong one - divide a week by the
archive (DECISIONS-2026-09-28 §P), or quote a stale copy of a register row. So this
job assembles it once, from the files, and the page makes one fetch.

EVERY FIGURE IS READ FROM A FILE, and the file says which. A counter carries
`source: {key, path, reduce}`; after the build, `verify()` re-resolves every one
against the input files with `metric_registry.resolve` and refuses on any
disagreement. A counter whose source is the metric registry's owner (or a
registered copy) names the metric id too, and a test asserts that pairing.

WHAT IT READS - files only, never the store:
  WEB_EXPORT_DIR  nfl/manifest.json, nfl/market/*/{current period}.json,
                  research/{calibration,hypotheses,market_calibration}.json,
                  lab/nfl/index.json
  --board         the Board's own tree (BOARD_EXPORT_DIR): the current week's
                  index.json and the read it names as `latest`. Optional.
  --lab-meta      the Lab universe's meta (server-side, never served): its row
                  count is published ONLY when its `built` matches the served Lab
                  index's `universe_built`, i.e. it describes the universe the
                  served presets were run on. Optional.
A missing input makes its part null and lists it under `unavailable` with the
reason; it never becomes a zero or a guess.

THE SHOWPIECES FALL BACK, AND SAY SO (a-52, DECISIONS-2026-09-28 §T). The
featured ladder, the distributions and the fantasy example are drawn from
market files, and the market builder writes only the current period's UNPLAYED
games - `sync_keys` then deletes every other period's file. So on a Monday the
served tree holds no market file at all and all three went null. Each part now
walks back, independently, from the current period through at most
`LANDING_FALLBACK_PERIODS` earlier ones and takes the first period its
UNCHANGED selection rule is satisfied in. Past the bound it is null and listed
under `unavailable` as before.
  - The walk reads the LANDING ARCHIVE (`--archive`, server-side, never
    served): every run with --write copies the current period's served market
    files there, overwriting a player's file with its newer publication and
    removing nothing. So a carried part is a figure this system published, as
    it was last published - never a recomputation, never an example.
  - Every such part carries `provenance` (`carried`, the period it came from,
    its as_of, how many periods back) and is listed under `carried`, NOT under
    `unavailable`.
  - A carried part comes from a played period, so it carries `result` where
    the result is on disk: the settled stat from the player's season file and,
    for the featured ladder, the Board's lean on that claim with its grade.
    SELECTION NEVER READS THE RESULT. The rule picks; the result is attached
    afterwards, whatever it was. A result absent from disk is null, never
    inferred.

WHERE IT WRITES. `landing.json`, a single top-level key, through
`export_web.sync_keys(dest, {KEY: payload}, [])`: contract check and source gate,
and it OWNS NO PREFIX, so it can delete nothing (the same shape as sports.json).
It prints no REFRESHED line for the same reason - there is nothing for the
uploader to authorise deleting.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import glob
import json
import math
import os
import re
import sys

import config
from core import board as B
from jobs import metric_registry as MR

KEY = "landing.json"
SPORT = "nfl"
MANIFEST = f"{SPORT}/manifest.json"
SCORE = "research/calibration.json"
REGISTER = "research/hypotheses.json"
MARKET_STUDY = "research/market_calibration.json"
LAB_INDEX = f"lab/{SPORT}/index.json"
# Not a served key: the Lab universe's meta lives beside the universe on the
# server. Named with a prefix no served key can carry so a page can tell.
LAB_META = "server:lab/universe.meta.json"
# The landing archive: earlier periods' market files, as last published. Also
# never served, so the same prefix.
ARCHIVE = "server:landing-archive/"
# The Board's append-only lean ledger (contract table board_ledger), served.
LEDGER = f"board/{SPORT}/ledger.csv"
# Not a file: the period window load_inputs read, so build walks exactly it.
WINDOW = "internal:window"
N_DISTRIBUTIONS = 14
FANTASY_POINTS = 20
TIERS = ("archive", "season", "week")

RULE_FEATURED = ("the (player, market) ladder with the most rungs among this period's "
                 "posted markets; ties broken by the most recent read (the market file's "
                 "as_of), then by player id")
RULE_DISTRIBUTIONS = ("the %d players with the most rungs listed across their posted markets "
                      "this period; ties broken by the most recent read (as_of), then by "
                      "player id. Fewer are published when fewer are priced." % N_DISTRIBUTIONS)
RULE_FANTASY = "the featured ladder's player"
RULE_DEVIG = ("the Board's latest read, main line: the featured ladder's player and market if "
              "the Board lists it, otherwise the main-line row quoted by the most books (ties "
              "by row id); the first book on that row that quotes both sides")
DEVIG_EXCHANGE_NOTE = ("An exchange rung carries no margin to remove: the yes-book mid is the "
                       "probability, and a de-vig applied to it would invent a correction for a "
                       "margin that does not exist.")


class LandingError(AssertionError):
    """The landing file would publish a figure its own inputs do not support."""


def iso(ts):
    return dt.datetime.fromtimestamp(ts, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(s):
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


# =============================================================================
# inputs
# =============================================================================

def _load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def window(manifest, fallback_periods=None):
    """The periods a part may be drawn from, newest first: [(walked, season,
    index, key)] from the current period back `fallback_periods` periods. The
    walk stops at the season's first period - the one before it is the previous
    season, months away, which the bound exists to refuse."""
    n = config.LANDING_FALLBACK_PERIODS if fallback_periods is None else fallback_periods
    if n < 0:
        raise ValueError(f"fallback_periods must be >= 0, got {n}")
    season, index = manifest["current"]["season"], manifest["current"]["period"]["index"]
    return [(k, season, index - k, f"{season}-{index - k}") for k in range(n + 1)
            if index - k >= 1]


def _market_glob(root, pkey):
    for p in sorted(glob.glob(os.path.join(root, SPORT, "market", "*", f"{pkey}.json"))):
        yield os.path.basename(os.path.dirname(p)), p


def load_inputs(dest, board_dir=None, lab_meta=None, archive_dir=None, fallback_periods=None):
    """-> ({key: parsed file}, [missing notes]). Keys are the served keys, except
    the Lab universe meta (LAB_META) and the landing archive (ARCHIVE...). Never
    raises on an absent optional input."""
    files, missing = {}, []
    mpath = os.path.join(dest, *MANIFEST.split("/"))
    if not os.path.isfile(mpath):
        raise LandingError(f"{MANIFEST} is not in {dest}: nothing to build a landing from")
    files[MANIFEST] = manifest = _load(mpath)
    fallback_periods = (config.LANDING_FALLBACK_PERIODS if fallback_periods is None
                        else fallback_periods)
    files[WINDOW] = {"bound": fallback_periods, "periods": window(manifest, fallback_periods)}
    for _, _, _, pkey in files[WINDOW]["periods"]:
        for pid, p in _market_glob(dest, pkey):
            files[f"{SPORT}/market/{pid}/{pkey}.json"] = _load(p)
        if archive_dir:
            for pid, p in _market_glob(archive_dir, pkey):
                files[f"{ARCHIVE}{SPORT}/market/{pid}/{pkey}.json"] = _load(p)
    if not archive_dir:
        missing.append("no landing archive was given: no earlier period can be carried")
    # The settled stat for every player a window market file names, from the
    # player's own season file - the settlement a carried part is shown with.
    for k in [k for k in files if "/market/" in k]:
        f = files[k]
        skey = f"{SPORT}/players/{f['identity']['id']}/{f['period']['season']}.json"
        spath = os.path.join(dest, *skey.split("/"))
        if skey not in files and os.path.isfile(spath):
            files[skey] = _load(spath)
    for key in (SCORE, REGISTER, MARKET_STUDY, LAB_INDEX):
        p = os.path.join(dest, *key.split("/"))
        if os.path.isfile(p):
            files[key] = _load(p)
        else:
            missing.append(f"{key} is not in the export tree")
    if board_dir:
        season = manifest["current"]["season"]
        week = manifest["current"]["period"]["index"]
        wk = f"board/{SPORT}/{season}/wk{int(week):02d}"
        ipath = os.path.join(board_dir, *wk.split("/"), "index.json")
        if os.path.isfile(ipath):
            idx = _load(ipath)
            files[f"{wk}/index.json"] = idx
            rkey = f"{wk}/{B.read_file_name(idx['latest'])}"
            rpath = os.path.join(board_dir, *rkey.split("/"))
            if os.path.isfile(rpath):
                files[rkey] = _load(rpath)
            else:
                missing.append(f"{rkey} (the Board index's latest read) is not in {board_dir}")
        else:
            missing.append(f"the Board has no read for {season} week {week} in {board_dir}")
        lpath = os.path.join(board_dir, *LEDGER.split("/"))
        if os.path.isfile(lpath):
            with open(lpath, encoding="utf-8", newline="") as f:
                files[LEDGER] = list(csv.DictReader(f))
        else:
            missing.append(f"{LEDGER} is not in {board_dir}: no lean can be shown with a result")
    else:
        missing.append("no Board tree was given (--board / BOARD_EXPORT_DIR unset)")
    if lab_meta:
        if os.path.isfile(lab_meta):
            files[LAB_META] = _load(lab_meta)
        else:
            missing.append(f"the Lab universe meta {lab_meta} does not exist")
    else:
        missing.append("no Lab universe meta was given")
    return files, missing


def _market_files(files, manifest):
    """The current period's SERVED market files - what the site shows now."""
    return _served(files, manifest["current"]["period"]["key"])


def _served(files, pkey):
    return {k: v for k, v in sorted(files.items())
            if k.startswith(f"{SPORT}/market/") and k.endswith(f"/{pkey}.json")}


def _period_files(files, pkey):
    """One period's market files, served and archived. A player's served file
    wins over its archived copy (it is the same publication or a newer one)."""
    out = {}
    for k, v in sorted(files.items()):
        if k.startswith(f"{ARCHIVE}{SPORT}/market/") and k.endswith(f"/{pkey}.json"):
            out[v["identity"]["id"]] = (k, v)
    for k, v in _served(files, pkey).items():
        out[v["identity"]["id"]] = (k, v)
    return dict(sorted(out.values()))


def walk(files):
    """-> [(walked, key, market files, carried)], the order a part is looked for
    in: the current period as served, then - carried - the current period as last
    published (its played games are gone from the served tree), then each earlier
    period in the window. Never reads a result."""
    win = _window(files)["periods"]
    cur = files[MANIFEST]["current"]["period"]["key"]
    return [(0, cur, _served(files, cur), False)] + \
        [(k, pkey, _period_files(files, pkey), True) for k, _, _, pkey in win]


def _window(files):
    """The window load_inputs read; a hand-built `files` without one walks only
    the current period (bound 0), never an unread one."""
    return files.get(WINDOW) or {"bound": 0, "periods": window(files[MANIFEST], 0)}


def _fall_back(files, choose):
    """The first step of the walk where `choose(market files)` is truthy.
    -> (pick, step) or (None, None)."""
    for step in walk(files):
        pick = choose(step[2])
        if pick:
            return pick, step
    return None, None


def provenance(files, step, f, part, as_of=None):
    walked, pkey, _, carried = step
    cur = files[MANIFEST]["current"]["period"]
    reason = None
    if carried:
        reason = ("nothing served for %s satisfies this part's rule; carried from %s, %s "
                  % (cur["label"], f["period"]["label"],
                     "as it was last published before its games were played" if walked == 0
                     else "%d period(s) back, the most recent within the window of %d that does"
                     % (walked, _window(files)["bound"])))
    return {"carried": carried, "walked": walked, "period": dict(f["period"]),
            "as_of": as_of or f["as_of"], "reason": reason}


def _board(files):
    idx = [k for k in files if k.startswith("board/") and k.endswith("/index.json")]
    if not idx:
        return None, None, None, None
    ikey = idx[0]
    rkey = ikey.rsplit("/", 1)[0] + "/" + B.read_file_name(files[ikey]["latest"])
    return ikey, files[ikey], rkey, files.get(rkey)


# =============================================================================
# 1. counters
# =============================================================================

def _span(season_from=None, season_to=None, period=None):
    return {"season_from": season_from, "season_to": season_to,
            "period": dict(period) if period else None}


def counter(id, label, tier, value, span, key, path, reduce=None, metric=None, reason=None,
            served=True):
    assert tier in TIERS, tier
    return {"id": id, "label": label, "sport": SPORT, "tier": tier, "value": value,
            "span": span, "metric": metric, "reason": reason,
            "source": {"key": key, "path": path, "reduce": reduce, "served": served}}


def _absent(id, label, tier, span, key, path, reason, reduce=None, served=True):
    return counter(id, label, tier, None, span, key, path, reduce=reduce, reason=reason,
                   served=served)


def build_counters(files):
    m = files[MANIFEST]
    cur = m["current"]
    season, period = cur["season"], cur["period"]
    week = _span(season, season, {**period, "season": season})
    den = m.get("denominators")
    out = []
    if den:
        a, s, w = den["archive"], den["season"], den["week"]
        out += [
            counter("players", "players with %s, whole archive" % den["participation"]["noun"],
                    "archive", a["players"],
                    _span(a["span"]["season_from"], a["span"]["season_to"]),
                    MANIFEST, "denominators.archive.players", metric="coverage.players.archive"),
            counter("players_season", "players with %s this season"
                    % den["participation"]["noun"], "season", s["players"],
                    _span(season, season), MANIFEST, "denominators.season.players",
                    metric="coverage.players.season"),
            counter("games", "games with a final score, whole archive", "archive",
                    a["games"]["final"],
                    _span(a["games"]["season_from"], a["games"]["season_to"]),
                    MANIFEST, "denominators.archive.games.final",
                    metric="coverage.games.archive"),
            counter("games_season", "games with a final score this season", "season",
                    s["games"]["final"], _span(season, season), MANIFEST,
                    "denominators.season.games.final", metric="coverage.games.season"),
            counter("markets", "players with a posted market this period", "week",
                    w["players"]["priced"], week, MANIFEST, "denominators.week.players.priced",
                    metric="coverage.players.week.priced"),
        ]
    else:
        # A manifest written before a-46. The legacy counts ARE these tiers - the
        # metric registry holds them as registered copies of the tier owners - but
        # the season tier has no legacy field, so it is absent rather than derived.
        seasons = m["seasons"]
        why = "the manifest predates the denominators block (a-46); no season-tier count"
        out += [
            counter("players", "players with offensive usage, whole archive", "archive",
                    m["counts"]["players"], _span(seasons[0], seasons[-1]), MANIFEST,
                    "counts.players", metric="coverage.players.archive"),
            _absent("players_season", "players with offensive usage this season", "season",
                    _span(season, season), MANIFEST, "denominators.season.players", why),
            counter("games", "games with a final score, whole archive", "archive",
                    m["counts"]["games"], _span(seasons[0], seasons[-1]), MANIFEST,
                    "counts.games", metric="coverage.games.archive"),
            _absent("games_season", "games with a final score this season", "season",
                    _span(season, season), MANIFEST, "denominators.season.games.final", why),
            counter("markets", "players with a posted market this period", "week",
                    m["counts"]["market"], week, MANIFEST, "counts.market",
                    metric="coverage.players.week.priced"),
        ]
    seasons = m["seasons"]
    out += [
        counter("rungs", "ladder rungs read this period", "week", m["counts"]["rungs"], week,
                MANIFEST, "counts.rungs"),
        counter("teams", "teams", "season", m["counts"]["teams"], _span(season, season),
                MANIFEST, "counts.teams"),
        counter("seasons", "seasons in the archive", "archive", len(seasons),
                _span(seasons[0], seasons[-1]), MANIFEST, "seasons", reduce="count"),
    ]
    if MARKET_STUDY in files:
        pop = files[MARKET_STUDY]["population"]
        out.append(counter("settled_props", "settled player props in the market study",
                           "archive", pop["n"], _span(min(pop["seasons"]), max(pop["seasons"])),
                           MARKET_STUDY, "population.n", metric="market.over_bias.n"))
    else:
        out.append(_absent("settled_props", "settled player props in the market study",
                           "archive", _span(), MARKET_STUDY, "population.n",
                           f"{MARKET_STUDY} is not in the export tree"))
    ikey, idx, rkey, read = _board(files)
    if read is not None:
        bspan = _span(read["season"], read["season"],
                      {"index": read["week"], "label": f"Week {read['week']}",
                       "key": f"{read['season']}-{read['week']}", "season": read["season"]})
        out.append(counter("lines_posted", "sportsbook lines on the Board's latest read",
                           "week", len(read["rows"]), bspan, rkey, "rows", reduce="count"))
        out.append(counter("leans", "leans posted on the Board this week", "week",
                           sum(idx["leans"].values()), bspan, ikey, "leans", reduce="sum"))
    else:
        why = "no Board read for the current period was available to this run"
        out.append(_absent("lines_posted", "sportsbook lines on the Board's latest read",
                           "week", week, "board", "rows", why, reduce="count"))
        out.append(_absent("leans", "leans posted on the Board this week", "week", week,
                           "board", "leans", why, reduce="sum"))
    meta, lab = files.get(LAB_META), files.get(LAB_INDEX)
    if meta is not None and lab is not None and \
            parse_iso(meta["built"]) == parse_iso(lab["universe_built"]):
        cov = [y for span in meta["price_coverage"].values() for y in span]
        out.append(counter("universe_rows", "rows in the replay universe", "archive",
                           meta["rows"], _span(min(cov), max(cov)), LAB_META, "rows",
                           served=False))
    else:
        why = ("the Lab universe meta was not given" if meta is None else
               "the served Lab index is absent" if lab is None else
               "the Lab universe meta (built %s) is not the universe the served presets ran "
               "on (built %s)" % (meta["built"], lab["universe_built"]))
        out.append(_absent("universe_rows", "rows in the replay universe", "archive", _span(),
                           LAB_META, "rows", why, served=False))
    if REGISTER in files:
        out.append(counter("tests", "tests published in the research register", "archive",
                           len(files[REGISTER]["hypotheses"]), _span(), REGISTER, "hypotheses",
                           reduce="count"))
    else:
        out.append(_absent("tests", "tests published in the research register", "archive",
                           _span(), REGISTER, "hypotheses", f"{REGISTER} is not in the tree",
                           reduce="count"))
    return out


# =============================================================================
# 2. the featured ladder, 4. the distributions, 7. the fantasy example
# =============================================================================

def _team(manifest, slug):
    for t in manifest["teams"]:
        if slug in (t["slug"], t["abbr"]):
            return {"slug": t["slug"], "abbr": t["abbr"], "name": t["name"]}
    raise LandingError(f"team {slug!r} is not in the manifest's teams")


def _market_of_stat(manifest):
    return {d["stat"]: (k, d["label"]) for k, d in manifest["market_definitions"].items()
            if d.get("stat")}


def _ladders(mfiles):
    """-> [(key, file, component)] for every MARKET component carrying rungs."""
    return [(k, f, c) for k, f in mfiles.items() for c in f["components"]
            if c["basis"] == "MARKET" and c.get("rungs")]


def _recency(f):
    return parse_iso(f["as_of"])


def pick_featured(mfiles):
    """The written rule, RULE_FEATURED. -> (key, file, component) or None."""
    ladders = _ladders(mfiles)
    if not ladders:
        return None
    return sorted(ladders, key=lambda t: (-len(t[2]["rungs"]), -_recency(t[1]),
                                          t[1]["identity"]["id"], t[2]["stat"]))[0]


def pick_distributions(mfiles, n=N_DISTRIBUTIONS):
    """The written rule, RULE_DISTRIBUTIONS. -> [(key, file, rungs)]."""
    ranked = []
    for k, f in mfiles.items():
        rungs = sum(len(c.get("rungs") or []) for c in f["components"] if c["basis"] == "MARKET")
        if rungs and "ppr" in (f.get("distributions") or {}):
            ranked.append((k, f, rungs))
    return sorted(ranked, key=lambda t: (-t[2], -_recency(t[1]), t[1]["identity"]["id"]))[:n]


def pick_fantasy(mfiles):
    """The written rule, RULE_FANTASY: the featured ladder's player of the SAME
    market files, when that player carries a fantasy distribution."""
    pick = pick_featured(mfiles)
    return pick if pick and "ppr" in (pick[1].get("distributions") or {}) else None


def _player(manifest, f):
    i = f["identity"]
    return {"id": i["id"], "slug": i["slug"], "name": i["name"], "position": i["position"],
            "team": _team(manifest, i["team"])}


# ----------------------------------------------------------------------------
# results: attached AFTER selection, read from files, never inferred
# ----------------------------------------------------------------------------

def _settled_row(files, f):
    """-> (the player's season-file row for f's game, its key, its path) or
    (None, key, None). A player with no row for the game - not yet ingested,
    did not play, or played and recorded nothing - has no result here: a missing
    row is not a zero (CLAUDE.md, nflverse)."""
    key = f"{SPORT}/players/{f['identity']['id']}/{f['period']['season']}.json"
    doc = files.get(key)
    rows = [r for r in (doc or {}).get("periods", []) if r.get("game_id") == f["game_id"]]
    if len(rows) != 1:
        return None, key, None
    return rows[0], key, "periods[game_id=%s].stats" % f["game_id"]


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def outcome(value, line):
    """A rung against the settled value. `cleared` / `missed`, never win / loss."""
    return "cleared" if value > line else "missed" if value < line else "push"


def score(stats, weights):
    """THE site's scoring (calibratedsports-web lib/scoring.ts), mirrored: sum of
    weight x stat, a missing or null component scores 0, rounded to the cent the
    way Math.round does (half up, not half to even)."""
    total = sum(w * stats[k] for k, w in weights.items() if _num(stats.get(k)))
    return math.floor(total * 100 + 0.5) / 100


# The components a fantasy distribution is built on. A row missing any of them
# is not scored: a missing rec scores 0 on the site, and a 0 in place of a
# receiver's unrecorded catches is a result the page would state with confidence.
FANTASY_REQUIRES = ("rec", "rec_yds")


def band(value, quantiles):
    """Which published quantile interval `value` fell in - an exact comparison
    against the file's own quantiles, no interpolation."""
    qs = sorted(quantiles.items(), key=lambda kv: (kv[1], kv[0]))
    if value < qs[0][1]:
        return "below %s" % qs[0][0]
    for (lo, a), (hi, b) in zip(qs, qs[1:]):
        if a <= value < b:
            return "%s-%s" % (lo, hi)
    return "at or above %s" % qs[-1][0]


def fantasy_result(files, f, presets):
    """-> {game_id, source, points: {preset: points}} or None."""
    row, key, path = _settled_row(files, f)
    if row is None or not all(_num(row["stats"].get(k)) for k in FANTASY_REQUIRES):
        return None
    m = files[MANIFEST]
    return {"game_id": f["game_id"], "source": {"key": key, "path": path},
            "points": {p: score(row["stats"], m["scoring_presets"][p]["weights"])
                       for p in presets}}


def lean_on(files, f, market):
    """The Board's lean on this claim, from the ledger, ONLY once it is settled
    (graded or void): a pending lean is a pick, and the landing does not publish
    picks. Several leans on one claim -> the first published (then lean_id), a
    choice that cannot see how any of them turned out. -> dict or None."""
    rows = [r for r in files.get(LEDGER) or []
            if r["gsis_id"] == f["identity"]["id"] and r["market"] == market
            and r["season"] == str(f["period"]["season"])
            and r["week"] == str(f["period"]["index"])]
    by = {}
    for r in rows:                      # append-only: the last event is the state
        by.setdefault(r["lean_id"], []).append(r)
    firsts = sorted((min(e["read_at"] for e in evs), lid) for lid, evs in by.items())
    if not firsts:
        return None
    lid = firsts[0][1]
    last = by[lid][-1]
    if last["event"] not in ("graded", "void"):
        return None
    return {"lean_id": lid, "side": last["side"], "line": float(last["line"]),
            "read_at": last["read_at"], "status": last["event"],
            "result": last["result"] or None,
            "actual": float(last["actual"]) if last["actual"] else None,
            "void_reason": last["void_reason"] or None,
            "source": {"key": LEDGER, "path": "[lean_id=%s]" % lid}}


def featured_result(files, f, comp):
    """-> {game_id, stat, value, rungs: [{line, outcome}], source} or None."""
    row, key, path = _settled_row(files, f)
    if row is None or not _num(row["stats"].get(comp["stat"])):
        return None
    v = row["stats"][comp["stat"]]
    return {"game_id": f["game_id"], "stat": comp["stat"], "value": v,
            "rungs": [{"line": r["line"], "outcome": outcome(v, r["line"])}
                      for r in comp["rungs"]],
            "source": {"key": key, "path": "%s.%s" % (path, comp["stat"])}}


def build_featured(files, pick, step):
    """-> (the part, a conflict note or None)."""
    m = files[MANIFEST]
    key, f, comp = pick
    market, label = _market_of_stat(m)[comp["stat"]]
    result, lean = featured_result(files, f, comp), lean_on(files, f, market)
    conflict = None
    if result and lean and lean["actual"] is not None and lean["actual"] != result["value"]:
        # Two settlements of one claim that disagree: a page showing both
        # contradicts itself, so the stat is withheld and the ledger - the public
        # record the lean was graded on - stands. Reported, never resolved here.
        conflict = ("the player file settles %s at %s and the Board ledger at %s; the "
                    "result is withheld" % (comp["stat"], result["value"], lean["actual"]))
        result = None
    return {
        "rule": RULE_FEATURED,
        "player": _player(m, f),
        "market": {"key": market, "stat": comp["stat"], "label": label},
        "opponent": _team(m, f["opponent"]),
        "game_id": f["game_id"],
        "kickoff_ts": f["kickoff_ts"],
        "kickoff": iso(f["kickoff_ts"]),
        "as_of": f["as_of"],
        "period": dict(f["period"]),
        "venue": f["source"]["venue"],
        "mid_basis": "the mid of each rung's bid and ask, no de-vig (an exchange)",
        "rungs": [{"line": r["line"], "mid": r["p_over"], "bid": r["bid"], "ask": r["ask"],
                   "quote_ts": r["quote_ts"]} for r in comp["rungs"]],
        "candidates": len(_ladders(step[2])),
        "source": {"key": key, "path": "components[stat=%s].rungs" % comp["stat"]},
        "provenance": provenance(files, step, f, "featured_ladder"),
        "result": result,
        "lean": lean,
    }, conflict


def _survival(cdf):
    """P(X > x) at every published x of the file's own cdf. Not resampled."""
    return [{"x": pt["x"], "p_over": round(1.0 - pt["p_at_most"], 4)} for pt in cdf]


def build_distributions(files, picks, step):
    m = files[MANIFEST]
    out = []
    for key, f, rungs in picks:
        d = f["distributions"]["ppr"]
        r = fantasy_result(files, f, ["ppr"])
        out.append({"player": _player(m, f), "rungs": rungs, "as_of": f["as_of"],
                    "quantiles": dict(d["quantiles"]), "survival": _survival(d["cdf"]),
                    "source": {"key": key, "path": "distributions.ppr.cdf"},
                    "result": None if r is None else
                    {"game_id": r["game_id"], "points": r["points"]["ppr"],
                     "band": band(r["points"]["ppr"], d["quantiles"]), "source": r["source"]}})
    newest = max(picks, key=lambda t: _recency(t[1]))[1]
    return {"rule": RULE_DISTRIBUTIONS, "scoring": "ppr",
            "scoring_label": m["scoring_presets"]["ppr"]["label"],
            "requested": N_DISTRIBUTIONS, "published": len(out), "players": out,
            "provenance": provenance(files, step, newest, "distributions")}


def build_fantasy(files, pick, step):
    m = files[MANIFEST]
    key, f, _ = pick
    r = fantasy_result(files, f, list(f["distributions"]))
    presets = []
    for name, d in f["distributions"].items():
        at = [t["p_at_least"] for t in d["thresholds"] if t["points"] == FANTASY_POINTS]
        presets.append({"preset": name, "label": m["scoring_presets"][name]["label"],
                        "weights": dict(m["scoring_presets"][name]["weights"]),
                        "median": d["quantiles"]["q50"], "quantiles": dict(d["quantiles"]),
                        "p_at_least": {"points": FANTASY_POINTS,
                                       "p": at[0] if len(at) == 1 else None},
                        "cdf": [dict(pt) for pt in d["cdf"]],
                        "source": {"key": key, "path": f"distributions.{name}"},
                        "result": None if r is None else
                        {"points": r["points"][name],
                         "band": band(r["points"][name], d["quantiles"])}})
    return {"rule": RULE_FANTASY, "player": _player(m, f), "as_of": f["as_of"],
            "presets": presets, "provenance": provenance(files, step, f, "fantasy"),
            "result": None if r is None else {"game_id": r["game_id"], "source": r["source"]}}


# =============================================================================
# 3. the de-vig worked example
# =============================================================================

def pick_devig(read, featured_player=None, featured_market=None):
    rows = [r for r in read["rows"] if r["is_main"]
            and any(b.get("over") is not None and b.get("under") is not None
                    for b in r["books"])]
    if not rows:
        return None, None
    same = [r for r in rows if r["gsis_id"] == featured_player and r["market"] == featured_market]
    row = same[0] if same else sorted(rows, key=lambda r: (-len(r["books"]), r["row_id"]))[0]
    book = next(b for b in row["books"] if b.get("over") is not None
                and b.get("under") is not None)
    return row, book


def devig_arithmetic(over, under):
    """Both sides' implied probabilities, the overround and the multiplicative
    de-vig - core.board's own functions, the same the Board and the Lab use."""
    po, pu = B.american_to_prob(over), B.american_to_prob(under)
    if po is None or pu is None:
        raise LandingError(f"not a two-sided American price: {over!r} / {under!r}")
    d = B.devig_mult(over, under)
    return {"implied_over": round(po, 4), "implied_under": round(pu, 4),
            "overround": round(po + pu, 4), "hold": round(B.hold(over, under), 4),
            "devig_over": round(d, 4), "devig_under": round(1.0 - d, 4)}


def build_devig(files, featured):
    m = files[MANIFEST]
    ikey, idx, rkey, read = _board(files)
    fp = featured["player"]["id"] if featured else None
    fm = featured["market"]["key"] if featured else None
    row, book = pick_devig(read, fp, fm) if read is not None else (None, None)
    if row is not None:
        a = devig_arithmetic(book["over"], book["under"])
        # The Board computed the same two numbers when it published this row. A
        # disagreement means one of the two derivations is wrong, and which one is
        # not this job's call - so it refuses rather than publishing either.
        for mine, theirs in (("devig_over", "p_over_devig"), ("hold", "hold")):
            if book.get(theirs) is not None and abs(a[mine] - book[theirs]) > 1e-4:
                raise LandingError(f"de-vig disagrees with the Board's own {theirs} on "
                                   f"{row['row_id']}/{book['book']}: {a[mine]} vs {book[theirs]}")
        market = m["market_definitions"].get(row["market"], {})
        return {"basis": "book", "rule": RULE_DEVIG,
                "player": {"id": row["gsis_id"], "slug": row["slug"], "name": row["name"],
                           "position": row["pos"],
                           "team": _team(m, row["team"])},
                "market": {"key": row["market"], "stat": market.get("stat"),
                           "label": market.get("label", row["market"])},
                "line": row["line"], "book": book["book"], "read_at": book["read_at"],
                "over_american": book["over"], "under_american": book["under"],
                **a, "method": "multiplicative: each side's implied probability divided by "
                               "their sum",
                "note": None,
                "exchange": None if row.get("kalshi_mid") is None else
                {"mid": row["kalshi_mid"], "note": DEVIG_EXCHANGE_NOTE},
                "source": {"key": rkey, "path": "rows[row_id=%s].books[book=%s]"
                           % (row["row_id"], book["book"])}}
    if featured is None:
        return None
    # No two-sided book price is on disk for this run: the exchange version,
    # labelled for what it is.
    r = min(featured["rungs"], key=lambda x: (abs(x["mid"] - 0.5), x["line"]))
    return {"basis": "exchange", "rule": "the featured ladder's rung whose mid is nearest "
                                         "0.5 (ties to the lower line)",
            "player": featured["player"], "market": featured["market"], "line": r["line"],
            "book": featured["venue"], "read_at": iso(r["quote_ts"]),
            "over_american": None, "under_american": None,
            "implied_over": r["mid"], "implied_under": round(1.0 - r["mid"], 4),
            "overround": 1.0, "hold": 0.0, "devig_over": r["mid"],
            "devig_under": round(1.0 - r["mid"], 4),
            "method": "none: the exchange mid is the probability",
            "note": "No two-sided sportsbook price was available to this run. "
                    + DEVIG_EXCHANGE_NOTE,
            "exchange": {"mid": r["mid"], "note": DEVIG_EXCHANGE_NOTE},
            "source": {"key": featured["source"]["key"],
                       "path": featured["source"]["path"] + "[line=%s]" % r["line"]}}


# =============================================================================
# 5. calibration, 6. the register
# =============================================================================

def build_calibration(files):
    c = files[SCORE]
    return {"source": {"key": SCORE, "path": "series"}, "generated_at": c["generated_at"],
            "population": c["population"], "n": c["n"], "games": c["games"],
            "series": [{"name": s["name"], "ece": s["ece"],
                        "bins": [dict(b) for b in s["bins"]]} for s in c["series"]]}


# The null each unit is tested against. A difference in pp or Brier is tested
# against zero; a probability is tested against the price it was compared with,
# which the register states in the row's own metric text - read from there, and
# refused if it is not stated exactly once. A count has no null: its row is a
# tally, not a test of a value.
DIFFERENCE_UNITS = {"pp": "a difference in probability points; the null is no difference",
                    "Brier": "a difference of Brier scores; the null is no difference"}
_PRICED = re.compile(r"\(priced (-?\d+(?:\.\d+)?)\)")


def null_of(row):
    """-> (null value or None, basis sentence)."""
    unit = row.get("unit")
    if unit in DIFFERENCE_UNITS:
        return 0.0, DIFFERENCE_UNITS[unit]
    if unit == "probability":
        hits = _PRICED.findall(row.get("metric") or "")
        if len(hits) != 1:
            raise LandingError(f"{row['id']}: a probability row must state the price it is "
                               f"tested against as '(priced x)' exactly once in its metric; "
                               f"found {len(hits)}")
        return float(hits[0]), ("a realized rate tested against the price it was compared "
                                "with, read from the row's metric")
    return None, ("no null: %s is a count, not a tested value" % (unit or "a row with no unit"))


def excludes(interval, null):
    """True / False when both exist; None when either is absent."""
    if interval is None or null is None:
        return None
    lo, hi = interval
    return bool(lo > null or hi < null)


def build_register(files):
    rows = []
    for h in files[REGISTER]["hypotheses"]:
        null, basis = null_of(h)
        rows.append({"id": h["id"], "question": h["question"], "verdict": h["verdict"],
                     "metric": h["metric"], "estimate": h["estimate"], "interval": h["interval"],
                     "unit": h["unit"], "n": h["n"], "games": h["games"], "null": null,
                     "null_basis": basis, "excludes_null": excludes(h["interval"], null)})
    ex = [r for r in rows if r["excludes_null"] is True]
    return {"source": {"key": REGISTER, "path": "hypotheses"},
            "generated_at": files[REGISTER]["generated_at"], "rows": rows,
            "counts": {"rows": len(rows), "excludes_null": len(ex),
                       "includes_null": sum(1 for r in rows if r["excludes_null"] is False),
                       "no_interval_or_null": sum(1 for r in rows if r["excludes_null"] is None)}}


# =============================================================================
# 8. freshness
# =============================================================================

def build_freshness(files, mfiles):
    reads = []
    if mfiles:
        k, f = max(mfiles.items(), key=lambda kv: (_recency(kv[1]), kv[0]))
        reads.append({"source": "kalshi.ladders", "label": "Kalshi ladders (the newest posted "
                      "market's last quote)", "at": f["as_of"], "key": k, "path": "as_of"})
    ikey, idx, rkey, read = _board(files)
    if read is not None:
        reads.append({"source": "oddsapi", "label": "sportsbook lines (the Board's latest read)",
                      "at": read["read_at"], "key": rkey, "path": "read_at"})
    if not reads:
        return {"last_read": None, "source": None, "reads": [],
                "data_through": files[MANIFEST]["current"]["data_through"],
                "source_version": files[MANIFEST]["current"]["source_version"]}
    latest = max(reads, key=lambda r: (parse_iso(r["at"]), r["source"]))
    return {"last_read": latest["at"], "source": latest["source"], "reads": reads,
            "data_through": files[MANIFEST]["current"]["data_through"],
            "source_version": files[MANIFEST]["current"]["source_version"]}


# =============================================================================
# the file
# =============================================================================

def build(files, missing=(), generated_at=None):
    m = files[MANIFEST]
    generated_at = generated_at or iso(dt.datetime.now(dt.timezone.utc).timestamp())
    mfiles = _market_files(files, m)
    unavailable = [{"part": "inputs", "reason": r} for r in missing]
    bound = _window(files)["bound"]
    within = ("this period or the %d before it" % bound) if bound else "this period"
    # Each part walks independently, with its OWN unchanged rule. The rules read
    # rungs, as_of and ids; nothing in a walk step can see a result.
    pick, fstep = _fall_back(files, pick_featured)
    featured, conflict = build_featured(files, pick, fstep) if pick else (None, None)
    if featured is None:
        unavailable.append({"part": "featured_ladder",
                            "reason": "no posted market in %s carries a ladder" % within})
    if conflict:
        unavailable.append({"part": "featured_ladder.result", "reason": conflict})
    dist_picks, dstep = _fall_back(files, pick_distributions)
    distributions = build_distributions(files, dist_picks, dstep) if dist_picks else None
    if distributions is None:
        unavailable.append({"part": "distributions",
                            "reason": "no posted market in %s carries a distribution" % within})
    elif distributions["published"] < N_DISTRIBUTIONS:
        unavailable.append({"part": "distributions", "reason": "%d of %d requested: only %d "
                            "players carry a posted market in %s"
                            % (distributions["published"], N_DISTRIBUTIONS,
                               distributions["published"], distributions["provenance"]
                               ["period"]["label"])})
    fpick, fastep = _fall_back(files, pick_fantasy)
    fantasy = build_fantasy(files, fpick, fastep) if fpick else None
    if fantasy is None:
        unavailable.append({"part": "fantasy", "reason": "no featured player with a "
                            "fantasy distribution in %s" % within})
    carried = [{"part": part, "period": dict(x["provenance"]["period"]),
                "as_of": x["provenance"]["as_of"], "walked": x["provenance"]["walked"],
                "settled": settled, "reason": x["provenance"]["reason"]}
               for part, x, settled in (
                   ("featured_ladder", featured,
                    featured is not None and featured["result"] is not None),
                   ("distributions", distributions, distributions is not None and any(
                       p["result"] is not None for p in distributions["players"])),
                   ("fantasy", fantasy, fantasy is not None and fantasy["result"] is not None))
               if x is not None and x["provenance"]["carried"]]
    # The de-vig's exchange version reads the featured ladder's rungs as a
    # CURRENT price; a carried ladder is not one, so it is not offered there.
    devig = build_devig(files, featured if featured and not featured["provenance"]["carried"]
                        else None)
    if devig is None:
        unavailable.append({"part": "devig", "reason": "no two-sided book price and no "
                            "exchange ladder this period"})
    elif devig["basis"] == "exchange":
        unavailable.append({"part": "devig", "reason": "no two-sided sportsbook price was "
                            "available; the exchange version is published instead"})
    calibration = build_calibration(files) if SCORE in files else None
    if calibration is None:
        unavailable.append({"part": "calibration", "reason": f"{SCORE} is not in the tree"})
    register = build_register(files) if REGISTER in files else None
    if register is None:
        unavailable.append({"part": "register", "reason": f"{REGISTER} is not in the tree"})
    counters = build_counters(files)
    unavailable += [{"part": f"counters.{c['id']}", "reason": c["reason"]}
                    for c in counters if c["value"] is None]
    return {
        "schema_version": 2, "generated_at": generated_at, "kind": "landing", "sport": None,
        "sports": [SPORT],
        "period": {**m["current"]["period"], "season": m["current"]["season"]},
        "counters": counters,
        "featured_ladder": featured,
        "devig": devig,
        "distributions": distributions,
        "calibration": calibration,
        "register": register,
        "fantasy": fantasy,
        "freshness": build_freshness(files, mfiles),
        "carried": carried,
        "unavailable": unavailable,
    }


# =============================================================================
# the check: every counter re-derived from the file it names
# =============================================================================

class LandingReport:
    """What `verify` approved. Refuses truth-testing: read .clean and .statement."""

    def __init__(self, checked, problems, absent):
        self.checked, self.problems, self.absent = checked, problems, absent
        self.clean = not problems
        self.statement = ("landing check: %d figure(s) re-derived from their named source, "
                          "%d absent with a reason, " % (checked, absent)
                          + ("0 disagreements" if self.clean else "%d PROBLEM(S):\n  %s"
                             % (len(problems), "\n  ".join(problems))))

    def __bool__(self):
        raise TypeError("LandingReport is not a boolean - read .clean and .statement")


def _reduce(v, how):
    if how is None:
        return v
    if how == "count":
        return len(v)
    if how == "sum":
        return sum(v.values()) if isinstance(v, dict) else sum(v)
    raise ValueError(how)


def _scene_problems(payload, files):
    """The scenes that are not counters, each re-read from the source it names."""
    out, n = [], 0

    def at(src, path=None):
        return MR.resolve(files[src["key"]], path or src["path"])

    fl = payload["featured_ladder"]
    if fl is not None:
        n += 1
        want = [(r["line"], r["p_over"], r["bid"], r["ask"]) for r in at(fl["source"])]
        got = [(r["line"], r["mid"], r["bid"], r["ask"]) for r in fl["rungs"]]
        if want != got:
            out.append(f"featured_ladder: rungs differ from {fl['source']['key']}")
    ds = payload["distributions"]
    for p in (ds or {}).get("players", []):
        n += 1
        if p["survival"] != _survival(at(p["source"])):
            out.append(f"distributions: {p['player']['id']} survival differs from its cdf")
    fa = payload["fantasy"]
    for p in (fa or {}).get("presets", []):
        n += 1
        d = at(p["source"])
        if p["median"] != d["quantiles"]["q50"] or p["cdf"] != d["cdf"]:
            out.append(f"fantasy: {p['preset']} differs from {p['source']['path']}")
    # Results: each re-read from the file it names, and each settled figure
    # re-scored from the stats row there - not from the builder's own output.
    def stats_at(src):
        try:
            return at(src)
        except (KeyError, MR.Unresolved) as e:
            out.append(f"result: {src['key']}:{src['path']} does not resolve ({e})")
            return None

    if fl is not None and fl.get("result") is not None:
        n += 1
        r = fl["result"]
        v = stats_at(r["source"])
        if v != r["value"] or [x["outcome"] for x in r["rungs"]] != \
                [outcome(v, x["line"]) for x in fl["rungs"]]:
            out.append("featured_ladder.result: differs from %s" % r["source"]["key"])
    if fl is not None and fl.get("lean") is not None:
        n += 1
        ln = fl["lean"]
        evs = [e for e in files.get(LEDGER) or [] if e["lean_id"] == ln["lean_id"]]
        if not evs or evs[-1]["event"] != ln["status"] or (evs[-1]["result"] or None) != \
                ln["result"] or evs[-1]["side"] != ln["side"]:
            out.append("featured_ladder.lean: differs from %s" % LEDGER)
    weights = {k: v["weights"] for k, v in files[MANIFEST]["scoring_presets"].items()}
    for p in (ds or {}).get("players", []):
        if p.get("result") is not None:
            n += 1
            st = stats_at(p["result"]["source"])
            if st is None or score(st, weights["ppr"]) != p["result"]["points"]:
                out.append(f"distributions: {p['player']['id']} result differs from its row")
    if fa is not None and fa.get("result") is not None:
        st = stats_at(fa["result"]["source"])
        for p in fa["presets"]:
            n += 1
            if st is None or p["result"] is None or \
                    score(st, weights[p["preset"]]) != p["result"]["points"]:
                out.append(f"fantasy: {p['preset']} result differs from its row")
    dv = payload["devig"]
    if dv is not None and dv["basis"] == "book":
        n += 1
        b = at(dv["source"])
        if (b["over"], b["under"]) != (dv["over_american"], dv["under_american"]):
            out.append("devig: the prices differ from the Board row they name")
    rg = payload["register"]
    if rg is not None:
        src = {h["id"]: h for h in files[REGISTER]["hypotheses"]}
        for r in rg["rows"]:
            n += 1
            h = src.get(r["id"])
            if h is None or any(r[k] != h[k] for k in ("estimate", "interval", "unit", "n",
                                                       "verdict", "question")):
                out.append(f"register: {r['id']} differs from {REGISTER}")
    cb = payload["calibration"]
    if cb is not None:
        n += 1
        if [s["bins"] for s in cb["series"]] != [s["bins"] for s in files[SCORE]["series"]]:
            out.append(f"calibration: bins differ from {SCORE}")
    return n, out


def verify(payload, files):
    scenes, problems = _scene_problems(payload, files)
    checked, absent = scenes, 0
    for c in payload["counters"]:
        if c["value"] is None:
            absent += 1
            if not c["reason"]:
                problems.append(f"{c['id']}: null with no reason")
            continue
        s = c["source"]
        try:
            got = _reduce(MR.resolve(files[s["key"]], s["path"]), s["reduce"])
        except (KeyError, MR.Unresolved, ValueError, TypeError) as e:
            problems.append(f"{c['id']}: {s['key']}:{s['path']} does not resolve ({e})")
            continue
        checked += 1
        if got != c["value"]:
            problems.append(f"{c['id']}: published {c['value']!r}, {s['key']}:{s['path']} "
                            f"re-derives {got!r}")
        if c["metric"]:
            reg = {x["id"]: x for x in MR.METRICS}.get(c["metric"])
            locs = [] if reg is None else [(reg["source"]["file"], reg["source"]["path"])] + \
                [(x["file"], x["path"]) for x in reg["copies"]]
            if (s["key"], s["path"]) not in locs:
                problems.append(f"{c['id']}: names metric {c['metric']!r}, whose registered "
                                f"locations do not include {s['key']}:{s['path']}")
    return LandingReport(checked, problems, absent)


def summary(payload):
    fl, dv, ds = payload["featured_ladder"], payload["devig"], payload["distributions"]
    return ("landing.json: %d counters (%d null), featured %s, de-vig %s, %s distributions, "
            "register %s rows, calibration %s, fantasy %s; %d unavailable note(s); %d bytes"
            % (len(payload["counters"]), sum(1 for c in payload["counters"] if c["value"] is None),
               "none" if fl is None else "%s %s (%d rungs)" % (fl["player"]["name"],
                                                            fl["market"]["key"], len(fl["rungs"])),
               "none" if dv is None else "%s %s %s @ %s" % (dv["basis"], dv["player"]["name"],
                                                           dv["market"]["key"], dv["book"]),
               "no" if ds is None else ds["published"],
               "no" if payload["register"] is None else len(payload["register"]["rows"]),
               "no" if payload["calibration"] is None else "yes",
               "none" if payload["fantasy"] is None else payload["fantasy"]["player"]["name"],
               len(payload["unavailable"]), len(json.dumps(payload))))


def carried_lines(payload):
    """One line per part served from an earlier period than the current one -
    the line that shows the landing has been carrying last week for a month."""
    if not payload["carried"]:
        return ["carried: none - every showpiece present is from %s"
                % payload["period"]["label"]]
    return ["carried: %s from %s %s (key %s, as_of %s, %d period(s) back), result %s"
            % (c["part"], c["period"]["season"], c["period"]["label"], c["period"]["key"],
               c["as_of"], c["walked"], "on disk" if c["settled"] else "NOT on disk")
            for c in payload["carried"]]


def archive_current(files, archive_dir, dry_run=False):
    """Copy the current period's SERVED market files into the landing archive,
    so a later run can carry them after the export has deleted them. A player's
    file is overwritten by its newer publication; nothing is ever removed.
    -> (written, unchanged)."""
    written = unchanged = 0
    for key, obj in _market_files(files, files[MANIFEST]).items():
        path = os.path.join(archive_dir, *key.split("/"))
        if os.path.isfile(path) and _load(path) == obj:
            unchanged += 1
            continue
        written += 1
        if dry_run:
            continue
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(obj, f)
        os.replace(tmp, path)
    return written, unchanged


def publish(payload, files, dest, dry_run=False):
    """Refuse before any write, then write through sync_keys owning nothing.
    -> (written, deleted, statement)."""
    from jobs import export_web as E
    rep = verify(payload, files)
    if not rep.clean:
        raise LandingError("refusing to write - " + rep.statement)
    written, deleted = E.sync_keys(dest, {KEY: payload}, [], dry_run=dry_run)
    return written, deleted, rep.statement


def default_lab_meta():
    return os.path.join(config.storage_path("lab"), "universe.meta.json")


def default_archive():
    return config.storage_path("landing", "archive")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dest", default="web",
                    help="'web' for WEB_EXPORT_DIR (the default), or a tree path")
    ap.add_argument("--board", help="the Board's tree (default BOARD_EXPORT_DIR)")
    ap.add_argument("--lab-meta", help="the Lab universe meta (default <STORAGE>/lab)")
    ap.add_argument("--archive", help="the landing archive (default <STORAGE>/landing/archive)")
    ap.add_argument("--fallback-periods", type=int, default=None,
                    help="how many periods back a showpiece may be carried from "
                         "(default config.LANDING_FALLBACK_PERIODS)")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--write", action="store_true", help="write landing.json into --dest")
    g.add_argument("--check", action="store_true", help="build and verify, write nothing")
    a = ap.parse_args(argv)
    from jobs.export_web import require_setting
    dest = require_setting("WEB_EXPORT_DIR") if a.dest == "web" else a.dest
    board = a.board or getattr(config, "BOARD_EXPORT_DIR", None) or os.getenv("BOARD_EXPORT_DIR")
    archive = a.archive or default_archive()
    files, missing = load_inputs(dest, board, a.lab_meta or default_lab_meta(), archive,
                                 a.fallback_periods)
    # Archived BEFORE the build, so a landing that refuses still keeps this
    # period's files for the run that will need them once the export deletes them.
    aw, au = archive_current(files, archive, dry_run=not a.write)
    print("%s %d current-period market file(s) into %s (%d unchanged)"
          % ("archived" if a.write else "would archive", aw, archive, au))
    payload = build(files, missing)
    written, deleted, statement = publish(payload, files, dest, dry_run=not a.write)
    print(statement)
    print(summary(payload))
    for line in carried_lines(payload):
        print("  " + line)
    for u in payload["unavailable"]:
        print("  unavailable: %s - %s" % (u["part"], u["reason"]))
    print("%s %s: written %d, deleted %d" % ("wrote" if a.write else "checked (dry run)",
                                            os.path.join(dest, KEY), written, deleted))
    return 0


if __name__ == "__main__":
    sys.exit(main())
