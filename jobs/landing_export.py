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

WHERE IT WRITES. `landing.json`, a single top-level key, through
`export_web.sync_keys(dest, {KEY: payload}, [])`: contract check and source gate,
and it OWNS NO PREFIX, so it can delete nothing (the same shape as sports.json).
It prints no REFRESHED line for the same reason - there is nothing for the
uploader to authorise deleting.
"""
from __future__ import annotations

import argparse
import datetime as dt
import glob
import json
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


def load_inputs(dest, board_dir=None, lab_meta=None):
    """-> ({key: parsed file}, [missing notes]). Keys are the served keys, except
    the Lab universe meta (LAB_META). Never raises on an absent optional input."""
    files, missing = {}, []
    mpath = os.path.join(dest, *MANIFEST.split("/"))
    if not os.path.isfile(mpath):
        raise LandingError(f"{MANIFEST} is not in {dest}: nothing to build a landing from")
    files[MANIFEST] = manifest = _load(mpath)
    pkey = manifest["current"]["period"]["key"]
    for p in sorted(glob.glob(os.path.join(dest, SPORT, "market", "*", f"{pkey}.json"))):
        pid = os.path.basename(os.path.dirname(p))
        files[f"{SPORT}/market/{pid}/{pkey}.json"] = _load(p)
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
    pkey = manifest["current"]["period"]["key"]
    return {k: v for k, v in sorted(files.items())
            if k.startswith(f"{SPORT}/market/") and k.endswith(f"/{pkey}.json")}


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


def _player(manifest, f):
    i = f["identity"]
    return {"id": i["id"], "slug": i["slug"], "name": i["name"], "position": i["position"],
            "team": _team(manifest, i["team"])}


def build_featured(files, pick):
    m = files[MANIFEST]
    key, f, comp = pick
    market, label = _market_of_stat(m)[comp["stat"]]
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
        "candidates": len(_ladders(_market_files(files, m))),
        "source": {"key": key, "path": "components[stat=%s].rungs" % comp["stat"]},
    }


def _survival(cdf):
    """P(X > x) at every published x of the file's own cdf. Not resampled."""
    return [{"x": pt["x"], "p_over": round(1.0 - pt["p_at_most"], 4)} for pt in cdf]


def build_distributions(files, picks):
    m = files[MANIFEST]
    out = []
    for key, f, rungs in picks:
        d = f["distributions"]["ppr"]
        out.append({"player": _player(m, f), "rungs": rungs, "as_of": f["as_of"],
                    "quantiles": dict(d["quantiles"]), "survival": _survival(d["cdf"]),
                    "source": {"key": key, "path": "distributions.ppr.cdf"}})
    return {"rule": RULE_DISTRIBUTIONS, "scoring": "ppr",
            "scoring_label": m["scoring_presets"]["ppr"]["label"],
            "requested": N_DISTRIBUTIONS, "published": len(out), "players": out}


def build_fantasy(files, pick):
    m = files[MANIFEST]
    key, f, _ = pick
    presets = []
    for name, d in f["distributions"].items():
        at = [t["p_at_least"] for t in d["thresholds"] if t["points"] == FANTASY_POINTS]
        presets.append({"preset": name, "label": m["scoring_presets"][name]["label"],
                        "weights": dict(m["scoring_presets"][name]["weights"]),
                        "median": d["quantiles"]["q50"], "quantiles": dict(d["quantiles"]),
                        "p_at_least": {"points": FANTASY_POINTS,
                                       "p": at[0] if len(at) == 1 else None},
                        "cdf": [dict(pt) for pt in d["cdf"]],
                        "source": {"key": key, "path": f"distributions.{name}"}})
    return {"rule": RULE_FANTASY, "player": _player(m, f), "as_of": f["as_of"],
            "presets": presets}


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
    pick = pick_featured(mfiles)
    featured = build_featured(files, pick) if pick else None
    if featured is None:
        unavailable.append({"part": "featured_ladder",
                            "reason": "no posted market this period carries a ladder"})
    dist_picks = pick_distributions(mfiles)
    distributions = build_distributions(files, dist_picks) if dist_picks else None
    if distributions is None:
        unavailable.append({"part": "distributions",
                            "reason": "no posted market this period carries a distribution"})
    elif distributions["published"] < N_DISTRIBUTIONS:
        unavailable.append({"part": "distributions", "reason": "%d of %d requested: only %d "
                            "players carry a posted market this period"
                            % (distributions["published"], N_DISTRIBUTIONS,
                               distributions["published"])})
    fantasy = build_fantasy(files, pick) if pick and "ppr" in (pick[1].get("distributions")
                                                               or {}) else None
    if fantasy is None:
        unavailable.append({"part": "fantasy", "reason": "no featured player with a "
                            "fantasy distribution this period"})
    devig = build_devig(files, featured)
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


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dest", default="web",
                    help="'web' for WEB_EXPORT_DIR (the default), or a tree path")
    ap.add_argument("--board", help="the Board's tree (default BOARD_EXPORT_DIR)")
    ap.add_argument("--lab-meta", help="the Lab universe meta (default <STORAGE>/lab)")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--write", action="store_true", help="write landing.json into --dest")
    g.add_argument("--check", action="store_true", help="build and verify, write nothing")
    a = ap.parse_args(argv)
    from jobs.export_web import require_setting
    dest = require_setting("WEB_EXPORT_DIR") if a.dest == "web" else a.dest
    board = a.board or getattr(config, "BOARD_EXPORT_DIR", None) or os.getenv("BOARD_EXPORT_DIR")
    files, missing = load_inputs(dest, board, a.lab_meta or default_lab_meta())
    payload = build(files, missing)
    written, deleted, statement = publish(payload, files, dest, dry_run=not a.write)
    print(statement)
    print(summary(payload))
    for u in payload["unavailable"]:
        print("  unavailable: %s - %s" % (u["part"], u["reason"]))
    print("%s %s: written %d, deleted %d" % ("wrote" if a.write else "checked (dry run)",
                                            os.path.join(dest, KEY), written, deleted))
    return 0


if __name__ == "__main__":
    sys.exit(main())
