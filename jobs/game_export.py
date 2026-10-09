"""The game forecast, published with its settlement record (a-63).

    python -m jobs.game_export --check                 # measure, build, validate; write nothing
    python -m jobs.game_export --write --dest D:/x     # ... into a scratch tree
    python -m jobs.game_export --write --dest web     # ... into WEB_EXPORT_DIR (weekly refresh)

    game/nfl/forecast.json   the current week: per game P(home wins), the home
                             margin's mean and central bands, and the as-of instant
    game/nfl/record.json     the settlement record c-28 measured: three baselines,
                             dBrier against each with its interval, the game count
                             and the seasons - and the market comparison, WITHHELD

THE FORECAST IS c-28's, NOT A COPY OF IT. `models.game` holds the object and
`research.game_forecast` the walk-forward fit and the scoring (pre-registered at
45abdce); both are imported, so the published record is the same computation as
`docs/findings/game-forecast.md`, re-run on the store as it stands. Parameters for
season T are fitted on 2000..T-1 (`jobs.season_model.best_params`), the ratings
entering a game read only final scores of games already played, and a game whose
kickoff is not after the build instant is not forecast at all.

THE MARKET COMPARISON IS COMPUTED, STORED AND NOT FOR DISPLAY (Ethan, 2026-09-30).
The model loses to the nflverse moneyline close, and the decision is to publish
the forecast without that comparison on the page. Dropping the figure would make
turning it on later a re-measurement; storing it keeps it checkable and makes it a
rendering change. It is marked the way `record.backtest`'s `statement_parts` is:
figures and names only, NO TEXT - there is no string in `market_comparison` a page
could print by accident - and `withheld.display` is `false` by contract `const`.

THE EARLY-SEASON SENTENCE TRAVELS WITH THE NUMBER. c-28 measured that the
margin-of-victory term adds nothing in weeks 1-4 against plain Elo. So
`forecast.json` carries `season_stage`: which part of the record applies to THIS
week, with its interval and a sentence WORDED FROM THAT INTERVAL - "no better than"
when it contains zero, "better than" / "worse than" when it does not - so it can
come out the other way when the data does. The same figures sit in record.json and
the metric gate refuses the pair if they disagree.

NON-FATAL PER FILE, AND NOTHING HALF-WRITTEN. Each file is built and validated
in memory on its own; only a file that built AND passed the contract, the source
gate and the metric gate reaches `sync_keys`, which replaces it atomically. A file
that fails is logged and its previously written copy is left exactly as it was.
`sync_keys(dest, files, [])` OWNS NO PREFIX, so this job can delete nothing - the
landing's and the record's shape (a-47, a-61). Exit 0 when every file wrote, 1
when any failed, so the weekly refresh records a WARN and carries on.

A REFUSAL IS RECORDED, NOT ONLY LOGGED (a-82). a-79 made a moved figure refuse its
file, and "its previous copy stands" then had one witness: a WARN line. `publish`
now returns - and with `--report` (default for `--dest web`:
<STORAGE_DIR>/logs/game_gate.json) writes - a gate report: per file that did not
publish, the registered place that moved, the served and the carried figure, their
difference, and WHEN THE COPY STILL STANDING WAS MADE. `frozen` is every key an
earlier build is still answering for; `clean` is false whenever anything is. The
matchup index carries the short form (`refused`: key, reason, registered places,
the standing copy's time, NO FIGURES - a-77: a qualified figure is not printed
without its sentence), because an index listing no games otherwise reads the same
as a week with none. The index cannot be the only carrier: when the forecast or
its record is refused the matchup step never runs and no index is written, so the
report is what always says so.

Reads `market_log.db` read-only (`mode=ro`) and nothing else. Never uploads.
Publishes nothing from KXNFLSPREAD or KXNFLTOTAL, and no total at all: c-28's
total is a league-level placeholder, not a forecast of this game.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
import sys
import time

import numpy as np
from scipy.stats import norm

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from jobs import season_model as S                      # noqa: E402
from models import game as G                            # noqa: E402
from models import season as M                          # noqa: E402

SPORT = "nfl"
FORECAST_KEY = f"game/{SPORT}/forecast.json"
RECORD_KEY = f"game/{SPORT}/record.json"
FORECAST_KIND = "game.forecast"
RECORD_KIND = "game.record"
BANDS = (50, 80, 95)                 # central bands of the home margin, percent
EARLY_LAST_WEEK = 4                  # c-28's registered cut: REG weeks 1-4 / 5+
GAME_PREFIX = f"game/{SPORT}/"
GATE_REPORT_KIND = "game.gate_report"        # an operational file, not a contract kind
GATE_REPORT_FILE = "game_gate.json"          # under <STORAGE_DIR>/logs for --dest web
MARKET_DECISION = {"display": False, "decided_by": "Ethan", "decided_on": "2026-09-30",
                   "unit": "a-63"}
BASELINES = (
    ("home", "the home team", "P(home wins) = the share of decisive games the home team won, "
     "seasons 2000..T-1"),
    ("record", "the better record", "the team with the better record so far this season wins "
     "with the rate that held in seasons 2000..T-1; equal records get the home rate"),
    ("elo_nomov", "plain Elo", "the same rating walk and fit with the margin-of-victory "
     "multiplier fixed at 1"),
)
PLAIN = {"home": "picking the home team every time",
         "record": "picking the team with the better record",
         "elo_nomov": "the same rating without the margin-of-victory term (plain Elo)"}


def iso(ts):
    return dt.datetime.fromtimestamp(ts, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def envelope(kind, now_ts, body):
    return {"schema_version": 2, "generated_at": iso(now_ts), "kind": kind, "sport": SPORT,
            **body}


def r4(x):
    return None if x is None else round(float(x), 4)


def interval(d):
    """c-28's bootstrap dict -> the published shape. `verdict` is the interval's
    own sign: 'below' (model error lower), 'above', or 'contains_zero'."""
    from research import ranking_calibration as rc
    s = rc.sign(d)
    return {"estimate": r4(d["est"]), "interval": [r4(d["lo"]), r4(d["hi"])],
            "se": r4(d["se"]), "verdict": {"below": "below", "above": "above",
                                           "contains 0": "contains_zero"}.get(s, "not_read")}


def compared(verdict):
    """The words an interval licenses, and nothing stronger (CLAUDE.md, Claims)."""
    return {"below": "better than", "above": "worse than"}.get(verdict, "no better than")


# =============================================================================
# the measurement - c-28's, imported
# =============================================================================

def measure(log=print, draws=None):
    """-> everything both files are built from. One read of the store (mode=ro)."""
    from research import game_forecast as GF
    from research import ranking_calibration as rc
    draws = rc.BOOT if draws is None else draws
    con = S.market_log_ro()
    try:
        games, _groupings, versions = S.load(con)
        ml = {r[0]: (r[1], r[2]) for r in con.execute(
            "SELECT g.game_id, g.home_moneyline, g.away_moneyline FROM nfl_games g JOIN "
            "(SELECT game_id, MAX(data_version) dv FROM nfl_games GROUP BY game_id) v "
            "ON v.game_id = g.game_id AND v.dv = g.data_version")}
    finally:
        con.close()
    t0 = time.time()
    grid = S.grid()
    walk = GF.Walk(games, S.game_losses(games, grid), mov=True)
    walk0 = GF.Walk(games, GF.nomov_losses(games, grid), mov=False)
    log(f"game: fits (MOV and plain) {time.time() - t0:.0f}s over {len(games):,} games")
    year = max(g["season"] for g in games if g["game_type"] == "REG")
    years = list(range(GF.SCORE_FROM, GF.SCORE_TO + 1)) + [year]
    signs = GF.record_signs(games)
    consts = {y: GF.baseline_constants(games, signs, y) for y in years}
    pop = [i for i, g in enumerate(games)
           if GF.SCORE_FROM <= g["season"] <= GF.SCORE_TO and GF.scored(g)
           and g["home_score"] != g["away_score"]]
    ties = sum(1 for g in games if GF.SCORE_FROM <= g["season"] <= GF.SCORE_TO
               and GF.scored(g) and g["home_score"] == g["away_score"])

    def comp(b, i):
        if b == "elo_nomov":
            return walk0.p(i)
        h, q = consts[games[i]["season"]]
        if b == "home":
            return h
        s = signs[i]
        return h if s == 0 else (q if s > 0 else 1.0 - q)

    def rows(idx, b):
        return [{"game": games[i]["game_id"], "stat": "ml", "line": 0.0,
                 "y": 1.0 if games[i]["home_score"] > games[i]["away_score"] else 0.0,
                 "m": walk.p(i), "k": comp(b, i)} for i in idx]

    quiet = []                               # compare()'s per-figure lines; logged by us
    reg = [i for i in pop if games[i]["game_type"] == "REG"]
    stages = {"all": pop,
              "weeks_1_4": [i for i in reg if games[i]["week"] <= EARLY_LAST_WEEK],
              "weeks_5_plus": [i for i in reg if games[i]["week"] > EARLY_LAST_WEEK]}
    res = {}
    for st, idx in stages.items():
        res[st] = {}
        for b, _l, _d in BASELINES:
            t0 = time.time()
            res[st][b] = GF.compare(f"{st} vs {b}", rows(idx, b), quiet.append, [], draws=draws)
            d = res[st][b]["diffs"]["dBrier"]
            log(f"game: {st:<12} vs {b:<9} n {len(idx):>5}  dBrier {d['est']:+.4f} "
                f"[{d['lo']:+.4f}, {d['hi']:+.4f}]  ({time.time() - t0:.0f}s)")
    # the withheld comparison: c-28 Part 2c, the nflverse moneyline close
    mrows = []
    for i in pop:
        g = games[i]
        o = ml.get(g["game_id"])
        if g["season"] < GF.ML_FROM or o is None or o[0] is None or o[1] is None:
            continue
        ih, ia = GF.american(o[0]), GF.american(o[1])
        mrows.append({"game": g["game_id"], "stat": "ml", "line": 0.0,
                      "y": 1.0 if g["home_score"] > g["away_score"] else 0.0,
                      "m": walk.p(i), "k": ih / (ih + ia)})
    market = GF.compare("market", mrows, quiet.append, [], draws=draws)
    d = market["diffs"]["dBrier"]
    log(f"game: market (withheld) n {market['n']:,} dBrier {d['est']:+.4f} "
        f"[{d['lo']:+.4f}, {d['hi']:+.4f}]")
    market["seasons"] = [min(games[i]["season"] for i in pop if games[i]["season"] >= GF.ML_FROM),
                         GF.SCORE_TO]
    return {"games": games, "versions": versions, "walk": walk, "year": year,
            "stages": res, "n": {k: len(v) for k, v in stages.items()}, "ties": ties,
            "market": market, "draws": draws,
            "seasons": [GF.SCORE_FROM, GF.SCORE_TO]}


# =============================================================================
# the record
# =============================================================================

def record_stage_sentence(stage, baseline, d):
    """a-77: the record publishes the weeks 1-4 figure against plain Elo a second
    time, in `by_stage`, and it carries track F's sentence there too. Asked of the
    registry by the figure's own path, so which of the six comparisons need one is
    the carried file's to say; null for the rest."""
    from jobs import required_sentences as RS
    holder = f"by_stage.{stage}.vs.{baseline}"
    if RS.figure_id(RECORD_KEY, holder) not in RS.required():
        return None
    return RS.qualifier(RECORD_KEY, holder, d)


def stage_block(m, stage):
    out = {"games": m["n"][stage], "vs": {}}
    for b, _l, _d in BASELINES:
        r = m["stages"][stage][b]
        d = interval(r["diffs"]["dBrier"])
        out["vs"][b] = {"brier_model": r4(r["corp_model"]["bs"]),
                        "brier_baseline": r4(r["corp_comparator"]["bs"]),
                        "d_brier": d,
                        "qualifier": record_stage_sentence(stage, b, d)}
    return out


def build_record(m):
    s0, s1 = m["seasons"]
    allr = m["stages"]["all"]
    mk = m["market"]
    md = mk["diffs"]
    verdicts = [interval(allr[b]["diffs"]["dBrier"])["verdict"] for b, _l, _d in BASELINES]
    n = m["n"]["all"]
    covers = [
        f"{n:,} games with a winner, {s0} to {s1}, regular season and postseason.",
        f"Each forecast was made before its game: the model's settings for a season were "
        f"chosen on the seasons from {S.FIT_FROM} to the one before it, and the ratings read "
        f"only games already played.",
        "It scores the chance of a home win against the result (Brier score, lower is better).",
    ]
    not_cover = [
        f"The {m['ties']} tied games in those seasons are not scored.",
        f"This season's games are not in it yet: the record ends with {s1}.",
        "The margin bands are not scored here; only the chance of a home win is.",
        "It compares the forecast with three simple baselines. It does not compare it with "
        "betting prices on this page.",
    ]
    body = {
        "population": {"games": n, "seasons": [s0, s1], "game_types": ["REG", "POST"],
                       "ties_excluded": m["ties"], "blocks": "game",
                       "fit_seasons_from": S.FIT_FROM,
                       "held_out": ("walk-forward: season T's Elo constants are chosen by grid "
                                    f"search on seasons {S.FIT_FROM}..T-1; the ratings entering "
                                    "a game are a function of earlier results only")},
        "model": {"brier": r4(allr["home"]["corp_model"]["bs"]),
                  "auc": r4(allr["home"]["auc_model"])},
        "baselines": [{"id": b, "label": lab, "definition": d,
                       "brier": r4(allr[b]["corp_comparator"]["bs"]),
                       "d_brier": interval(allr[b]["diffs"]["dBrier"]),
                       "compared": compared(interval(allr[b]["diffs"]["dBrier"])["verdict"])}
                      for b, lab, d in BASELINES],
        "beats_all_baselines": all(v == "below" for v in verdicts),
        "by_stage": {"weeks_1_4": stage_block(m, "weeks_1_4"),
                     "weeks_5_plus": stage_block(m, "weeks_5_plus")},
        "interval_method": {"kind": "percentile bootstrap over games", "draws": m["draws"],
                            "level": 0.95, "difference": "model minus baseline"},
        "covers": covers,
        "does_not_cover": not_cover,
        "market_comparison": {
            "withheld": dict(MARKET_DECISION),
            "benchmark": {"source": "nflverse home_moneyline / away_moneyline",
                          "devig": "multiplicative, two-way",
                          "provenance_recorded": False},
            "seasons": mk["seasons"], "games": mk["n"],
            "brier_model": r4(mk["corp_model"]["bs"]),
            "brier_benchmark": r4(mk["corp_comparator"]["bs"]),
            "d_brier": interval(md["dBrier"]), "d_miscalibration": interval(md["dMCB"]),
            "d_discrimination": interval(md["dDSC"]), "d_auc": interval(md["dAUC"]),
        },
        "source": {"preregistration": "docs/C28-game-forecast-preregistration.md",
                   "findings": "docs/findings/game-forecast.md",
                   "script": "research/game_forecast.py",
                   "versions": dict(m["versions"])},
    }
    return body


# =============================================================================
# the forecast
# =============================================================================

def current_games(games, year, now_ts):
    """-> (week, game_type, [indices]) - every game of the first week of `year`
    that still has a game to kick off after `now_ts`, excluding any already
    kicked off - or (None, None, []) when the season has none left."""
    ahead = [i for i, g in enumerate(games) if g["season"] == year
             and g["kickoff_ts"] is not None and g["kickoff_ts"] > now_ts
             and g["home_score"] is None]
    if not ahead:
        return None, None, []
    week = min(games[i]["week"] for i in ahead)
    idx = sorted((i for i in ahead if games[i]["week"] == week),
                 key=lambda i: (games[i]["kickoff_ts"], games[i]["game_id"]))
    return week, games[idx[0]]["game_type"], idx


def stage_sentence(stage, d):
    """a-75: the weeks 1-4 figure may only be cited with track F's sentence about
    plain Elo's fitting grid, wherever in the block it is served. Null elsewhere."""
    from jobs import required_sentences as RS
    if stage != RS.EARLY_STAGE:
        return None
    return RS.qualifier(FORECAST_KEY, RS.STAGE_HOLDER, d)


def season_stage(m, week, game_type):
    """Which part of the record speaks for THIS week, against plain Elo, and a
    sentence worded from that part's interval (so it can come out otherwise)."""
    s0, s1 = m["seasons"]
    if game_type == "REG" and week <= EARLY_LAST_WEEK:
        stage, other = "weeks_1_4", "weeks_5_plus"
    elif game_type == "REG":
        stage, other = "weeks_5_plus", "weeks_1_4"
    else:
        stage, other = "all", None
    d = interval(m["stages"][stage]["elo_nomov"]["diffs"]["dBrier"])
    n = m["n"][stage]
    fig = f"{d['estimate']:+.4f} [{d['interval'][0]:+.4f}, {d['interval'][1]:+.4f}]"
    where = {"weeks_1_4": "In weeks 1 to 4", "weeks_5_plus": "From week 5 on",
             "all": "Over all games"}[stage]
    text = (f"This is week {week}. {where}, this forecast has been {compared(d['verdict'])} "
            f"{PLAIN['elo_nomov']}: the difference in forecast error (Brier score) is {fig} "
            f"over {n:,} games, {s0} to {s1}.")
    block = {"stage": stage, "week": week, "game_type": game_type, "games": n,
             "vs_elo_nomov": d, "compared": compared(d["verdict"]),
             "qualifier": stage_sentence(stage, d)}
    if other:
        o = interval(m["stages"][other]["elo_nomov"]["diffs"]["dBrier"])
        ofig = f"{o['estimate']:+.4f} [{o['interval'][0]:+.4f}, {o['interval'][1]:+.4f}]"
        owhere = {"weeks_1_4": "in weeks 1 to 4", "weeks_5_plus": "from week 5 on"}[other]
        text += (f" {owhere[0].upper() + owhere[1:]} it has been {compared(o['verdict'])} it: "
                 f"{ofig} over {m['n'][other]:,} games.")
        block["other"] = {"stage": other, "games": m["n"][other], "vs_elo_nomov": o,
                          "compared": compared(o["verdict"]),
                          "qualifier": stage_sentence(other, o)}
    else:
        block["other"] = None
    block["statement"] = text
    return block


def build_forecast(m, now_ts):
    from research import game_forecast as GF
    games, year, walk = m["games"], m["year"], m["walk"]
    params = walk.params(year)
    sigma = GF.margin_sigmas(games, walk, [year])[year]
    _pre, snaps = M.run_elo(games, params, snapshot_at=[(year, 99)])
    ratings = snaps[(year, 99)]
    played = [g for g in games if g["season"] <= year and GF.scored(g)]
    last = max(g["kickoff_ts"] for g in played if g["kickoff_ts"] is not None)
    if last >= now_ts:
        raise RuntimeError(f"a scored game kicks off at {iso(last)}, not before the build "
                           f"instant {iso(now_ts)} - refusing an as-of it cannot keep")
    week, gtype, idx = current_games(games, year, now_ts)
    rows = []
    for i in idx:
        g = games[i]
        rh = ratings.get(G.franchise(g["home"]), G.MEAN)
        ra = ratings.get(G.franchise(g["away"]), G.MEAN)
        _d, p = G.pregame(rh, ra, params)
        f = G.GameForecast(game_id=g["game_id"], home=g["home"], away=g["away"],
                           as_of=iso(now_ts), p_home=p, sigma_m=sigma, mu_t=0.0, sigma_t=1.0)
        mu = f.margin_mean()
        rows.append({"game_id": g["game_id"], "kickoff": iso(g["kickoff_ts"]),
                     "home": g["home"], "away": g["away"],
                     "p_home_win": r4(p),
                     "margin": {"mean": round(mu, 1) + 0.0,     # never -0.0
                                "bands": [{"level": b,
                                           "lo": round(float(norm.ppf((1 - b / 100) / 2, mu, sigma)), 1) + 0.0,
                                           "hi": round(float(norm.ppf(1 - (1 - b / 100) / 2, mu, sigma)), 1) + 0.0}
                                          for b in BANDS]}})
    body = {
        "season": year, "week": week, "game_type": gtype,
        "as_of": {"instant": iso(now_ts), "results_through": iso(last),
                  "games_seen": len(played), "versions": dict(m["versions"])},
        "games": rows,
        "reason": None if rows else f"no {year} game is left to kick off after {iso(now_ts)}",
        "method": {"model": "margin-of-victory Elo (models/game.py)",
                   "params": params.as_dict(), "fit_seasons": [S.FIT_FROM, year - 1],
                   "margin": {"family": "normal", "sd": round(sigma, 1),
                              "mean_rule": "sd x PhiInv(p_home_win), so P(margin > 0) = "
                                           "p_home_win",
                              "declared": ["a normal margin carries no key numbers (3, 7)",
                                           "ties are not modelled"]}},
        "season_stage": season_stage(m, week, gtype) if rows else None,
        "record": RECORD_KEY,
    }
    return body


# =============================================================================
# publish
# =============================================================================

def stage_agrees(files):
    """The forecast's `season_stage` must carry the record's figures for the
    stage it names - the pair check the metric registry cannot express, since
    which stage applies moves with the week. -> the statement; raises otherwise."""
    fc, rec = files[FORECAST_KEY], files[RECORD_KEY]
    st = fc["season_stage"]
    if st is None:
        return "stage check: no game to forecast, nothing to compare"

    def owner(stage):
        if stage == "all":
            return next(b["d_brier"] for b in rec["baselines"] if b["id"] == "elo_nomov")
        return rec["by_stage"][stage]["vs"]["elo_nomov"]["d_brier"]

    pairs = [(st["stage"], st["vs_elo_nomov"])]
    if st["other"]:
        pairs.append((st["other"]["stage"], st["other"]["vs_elo_nomov"]))
    for stage, got in pairs:
        if got != owner(stage):
            raise RuntimeError(f"{FORECAST_KEY} season_stage {stage} is {got}, but "
                               f"{RECORD_KEY} says {owner(stage)} - refusing both")
    # a-77: one figure, two files - and so one sentence. The registry holds an entry
    # for each place; this is what refuses the two entries drifting apart.
    for blk in (st, st["other"]):
        if blk is None or blk["stage"] == "all":
            continue
        theirs = rec["by_stage"][blk["stage"]]["vs"]["elo_nomov"]["qualifier"]
        if blk["qualifier"] != theirs:
            raise RuntimeError(f"{FORECAST_KEY} season_stage {blk['stage']} and {RECORD_KEY} "
                               f"by_stage carry different sentences for one figure - "
                               f"refusing both")
    return f"stage check: {len(pairs)} stage figure(s) agree with the record"


def gate(files):
    """The contract, the source gate, the metric gate and the sentence gate (a-75:
    a figure that may only be cited with a sentence carries it), over whatever built;
    with both files, also the stage check. -> statements; raises on any failure."""
    from jobs import export_web as E
    from jobs import metric_registry as MR
    from jobs import required_sentences as RS
    from jobs import source_registry
    E.validate_contract(files)
    source_registry.require_declared(files)
    mets = [m for m in MR.GAME_METRICS if MR._files_of(m) <= set(files)]
    out = [MR.require(dict(files), mets).statement, RS.require(files)]
    if FORECAST_KEY in files and RECORD_KEY in files:
        out.append(stage_agrees(files))
    return "\n".join(out)


def build(now_ts, log=print, draws=None, standing=None):
    """-> ({key: payload}, [{file, error, moved, moved_unread}]). Each file is built
    and gated on its own; if both build, the pair is gated too, and a disagreement
    writes neither. `standing` (a-82) is `key -> the copy already at the
    destination`, handed through to the matchup index; None when nothing is being
    written (`--check`), and the index then says no earlier copy was looked for."""
    m = measure(log=log, draws=draws)
    files, failed = {}, []
    for key, kind, fn in ((RECORD_KEY, RECORD_KIND, lambda: build_record(m)),
                          (FORECAST_KEY, FORECAST_KIND, lambda: build_forecast(m, now_ts))):
        payload = None
        try:
            payload = {key: envelope(kind, now_ts, fn())}
            gate(payload)
            files.update(payload)
        except Exception as e:  # noqa: BLE001 - one file's failure is that file's
            failed.append(refusal(key, e, payload))
    if len(files) == 2:
        try:
            log(gate(files))
        except Exception as e:  # noqa: BLE001 - the two disagree: publish neither
            failed.append(refusal("*", e, files))
            files = {}
    if len(files) == 2:
        add_matchups(m, files, failed, now_ts, log, standing=standing)
    return files, failed


def refusal(key, e, payload=None):
    """One entry of `failed`. a-82: beside the error it carries `moved` - every
    registered figure in the refused payload that is not the one its sentence was
    measured beside (`required_sentences.moved`) - so the gate report names the
    place and the figures from the payload itself, never from the error's text.
    `moved_unread` says why when the payload could not be walked: an empty `moved`
    with a reason beside it is not "nothing moved"."""
    out = {"file": key, "error": f"{type(e).__name__}: {e}", "moved": [], "moved_unread": None}
    if payload:
        try:
            from jobs import required_sentences as RS
            out["moved"] = RS.moved(payload)
        except Exception as x:  # noqa: BLE001 - the record of a failure must not be one
            out["moved_unread"] = f"{type(x).__name__}: {x}"
    else:
        out["moved_unread"] = "nothing was built to read"
    return out


def add_matchups(m, files, failed, now_ts, log=print, standing=None):
    """a-64: the matchup files and the spread and total records, from the same
    measurement. Each file is gated on its own and a matchup that disagrees with
    the forecast or a record it copies is dropped; the index lists only matchups
    that survived, and is gated last."""
    from jobs import game_matchup as GM
    try:
        built, bad = GM.build(m, files[FORECAST_KEY], files[RECORD_KEY], now_ts, log=log)
    except Exception as e:  # noqa: BLE001 - the matchup step failed; the forecast stands
        failed.append(refusal(GM.MATCHUP_DIR + "*", e))
        return
    mine = [{"file": k, "error": v, "moved": [], "moved_unread": "refused before it was built"}
            for k, v in bad.items()]
    index = built.pop(GM.INDEX_KEY, None)
    order = [GM.RECORD_SPREAD_KEY, GM.RECORD_TOTAL_KEY] + sorted(
        k for k in built if k.startswith(GM.MATCHUP_DIR))
    for key in order:
        if key not in built:
            continue
        kind = GM.MODEL_RECORD_KIND if key.startswith("game/nfl/record_") else GM.MATCHUP_KIND
        payload = None
        try:
            payload = {key: envelope(kind, now_ts, built[key])}
            gate(payload)
            if kind == GM.MATCHUP_KIND:
                matchup_agrees(key, payload[key], files)
            files.update(payload)
        except Exception as e:  # noqa: BLE001 - that file's failure is that file's
            mine.append(refusal(key, e, payload))
    failed += mine
    if index is None:
        return
    index["games"] = [r for r in index["games"] if r["key"] in files]
    index["refused"] = index_refused(mine, standing)
    try:
        payload = {GM.INDEX_KEY: envelope(GM.INDEX_KIND, now_ts, index)}
        gate(payload)
        files.update(payload)
    except Exception as e:  # noqa: BLE001
        failed.append({"file": GM.INDEX_KEY, "error": f"{type(e).__name__}: {e}"})


def matchup_agrees(key, mu, files):
    """A matchup repeats figures other files own: the forecast's probability, margin
    and stage, and each record's figures. It must carry exactly theirs, or it is
    refused - a copy that can drift is a second, unchecked claim."""
    from jobs import game_matchup as GM
    from jobs import metric_registry as MR
    fc = files[FORECAST_KEY]
    row = next((g for g in fc["games"] if g["game_id"] == mu["game_id"]), None)
    problems = []
    if row is None:
        problems.append("not in the forecast")
    else:
        if mu["numbers"]["moneyline"]["model"]["p_home_win"] != row["p_home_win"]:
            problems.append("p_home_win differs from the forecast")
        if mu["margin"] != row["margin"]:
            problems.append("margin differs from the forecast")
    if mu["season_stage"] != fc["season_stage"]:
        problems.append("season_stage differs from the forecast")
    for market in ("moneyline", "spread", "total"):
        rec = mu["numbers"][market]["record"]
        owner = files.get(rec["file"])
        if owner is None:
            problems.append(f"{market}: {rec['file']} did not build")
            continue
        vc = rec["vs_close"]
        if MR.resolve(owner, vc["path"]) != vc["d_brier"]:
            problems.append(f"{market}: vs_close differs from {rec['file']}")
        mine = {b["id"]: b["d_brier"] for b in rec["beats"]}
        src = ({b["id"]: b["d_brier"] for b in owner["baselines"]} if market == "moneyline"
               else {b["id"]: b["d_brier"] for b in owner["against_baselines"]})
        if mine != src:
            problems.append(f"{market}: beats differs from {rec['file']}")
    if problems:
        raise RuntimeError(f"{key}: " + "; ".join(problems) + " - refusing it")
    return f"{key}: agrees with {FORECAST_KEY} and its three records"


# =============================================================================
# a-82: what a refusal leaves standing
# =============================================================================

def standing_copy(dest, key, now_ts):
    """The copy of `key` already in the export tree -> {generated_at, age_seconds},
    or None when there is none. `generated_at` is the file's own, which is when its
    CONTENT was last written (`write_if_changed` leaves an unchanged file alone);
    a file that is there and does not say comes back with both fields null, which
    is a third answer and not "no copy"."""
    from jobs import export_web as E
    path = E.local_path(dest, key)
    if not os.path.exists(path):
        return None
    out = {"generated_at": None, "age_seconds": None}
    try:
        with open(path, encoding="utf-8") as f:
            made = json.load(f).get("generated_at")
        t = dt.datetime.strptime(made, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)
        out = {"generated_at": made, "age_seconds": round(now_ts - t.timestamp())}
    except (OSError, ValueError, TypeError, AttributeError):
        pass
    return out


def index_refused(mine, standing):
    """The matchup index's `refused`: every matchup-side file this build refused,
    in the short form a published file may carry. `reason` is computed from the
    payload (`moved_figure` when a registered figure is not the one its sentence was
    measured beside, `other` for everything else); `figures` names the registered
    places and prints NO DIGITS (a-77); `standing_generated_at` is when the copy
    still at that key was made, null when there is none there or none was looked
    for."""
    out = []
    for f in sorted(mine, key=lambda f: f["file"]):
        copy_ = standing(f["file"]) if standing else None
        out.append({"key": f["file"],
                    "reason": "moved_figure" if f["moved"] else "other",
                    "figures": sorted({mv["figure"] for mv in f["moved"]}),
                    "standing_generated_at": copy_["generated_at"] if copy_ else None})
    return out


def _duration(seconds):
    return "an unknown time" if seconds is None else (
        f"{seconds / 86400:.1f} days" if seconds >= 86400 else f"{seconds / 3600:.1f} hours")


def staleness(dest, files, failed, now_ts):
    """The gate report: what this run did NOT publish, and what is answering for
    it instead. -> a dict; `clean` is true only when every game file this run
    should have written, it built.

        refused        a file that was built or attempted and did not pass, with the
                       registered figures that moved and its standing copy
        not_attempted  a file an earlier build left that this run never reached,
                       because the forecast or its record was refused before the
                       matchup step: the two model records, the index, and every
                       matchup THE STANDING INDEX STILL LISTS - which is what the
                       site is being pointed at
        steps_failed   a failure with no key of its own (the matchup step raised)
        frozen         every key above with a standing copy: an earlier build is
                       still what is served there
        absent         refused with NO standing copy: nothing is at that key
        earlier_files  other game files on disk this run did not build - previous
                       weeks' matchups, which no run rewrites. A count, not a
                       finding.
    """
    from jobs import export_web as E
    from jobs import game_matchup as GM
    # the game prefix only: the export tree holds ~23,000 files and this runs weekly
    on_disk = {GAME_PREFIX + k for k in E.local_keys(E.local_path(dest, GAME_PREFIX.rstrip("/")))}
    refused, steps = {}, []
    for f in failed:
        if f["file"] == "*":
            keys = [k for k in (FORECAST_KEY, RECORD_KEY) if k not in files]
        elif f["file"].endswith("*"):
            keys = []
        else:
            keys = [f["file"]]
        if not keys:
            steps.append({"step": f["file"], "error": f["error"]})
        for k in keys:
            r = refused.setdefault(k, {"key": k, "errors": [], "moved": [], "moved_unread": None})
            r["errors"].append(f["error"])
            r["moved"] += [mv for mv in f.get("moved", []) if mv["file"] == k
                           and mv not in r["moved"]]
            r["moved_unread"] = r["moved_unread"] or f.get("moved_unread")
    expected = {FORECAST_KEY, RECORD_KEY, GM.RECORD_SPREAD_KEY, GM.RECORD_TOTAL_KEY, GM.INDEX_KEY}
    if GM.INDEX_KEY not in files and GM.INDEX_KEY in on_disk:
        try:
            with open(E.local_path(dest, GM.INDEX_KEY), encoding="utf-8") as fh:
                expected |= {g["key"] for g in json.load(fh)["games"]}
        except (OSError, ValueError, KeyError, TypeError) as e:
            steps.append({"step": "read the standing index",
                          "error": f"{type(e).__name__}: {e}"})
    not_attempted = [{"key": k} for k in sorted(expected & on_disk)
                     if k not in files and k not in refused]
    rows = sorted(refused.values(), key=lambda r: r["key"]) + not_attempted
    for r in rows:
        r["standing"] = standing_copy(dest, r["key"], now_ts)
    frozen = [r for r in rows if r["standing"] is not None]
    absent = [r["key"] for r in rows if r["standing"] is None]
    dated = [r for r in frozen if r["standing"]["age_seconds"] is not None]
    oldest = max(dated, key=lambda r: r["standing"]["age_seconds"]) if dated else None
    places = sorted({mv["figure"] for r in refused.values() for mv in r["moved"]})
    clean = not rows and not steps
    if clean:
        text = (f"game: nothing frozen - {len(files)} file(s) built, none refused, none "
                "left to an earlier build")
    else:
        text = (f"game: FROZEN {len(frozen)} file(s) still answered by an earlier build"
                + (f" (oldest {oldest['key']}, made {oldest['standing']['generated_at']}, "
                   f"{_duration(oldest['standing']['age_seconds'])} old)" if oldest else "")
                + f"; {len(absent)} refused with no earlier copy; {len(steps)} step failure(s)"
                + (f"; moved figure(s): {', '.join(places)}" if places else
                   "; no registered figure moved"))
    return {"kind": GATE_REPORT_KIND, "checked_at": iso(now_ts), "dest": str(dest),
            "built": sorted(files),
            "refused": sorted(refused.values(), key=lambda r: r["key"]),
            "not_attempted": not_attempted, "steps_failed": steps,
            "frozen": sorted(r["key"] for r in frozen), "absent": sorted(absent),
            "oldest_standing": None if oldest is None else {"key": oldest["key"],
                                                            **oldest["standing"]},
            "moved_figures": places,
            "earlier_files": len(on_disk - set(files) - {r["key"] for r in rows}),
            "clean": clean, "statement": text}


def write_report(path, report):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def publish(dest, now_ts=None, log=print, draws=None, dry_run=False, report=None):
    """Build, gate and write what passed. NEVER RAISES: a failure is logged and
    listed, and that file's previous copy stays where it was. -> summary, with
    `gate_report` (a-82, `staleness`) saying what an earlier build is still
    answering for; written to `report` when a path is given, and its one-line
    statement is the LAST line logged, so it is in the weekly refresh's tail."""
    from jobs import export_web as E
    now_ts = time.time() if now_ts is None else now_ts
    out = {"built": [], "failed": [], "written": 0}
    files = {}
    try:
        files, failed = build(now_ts, log=log, draws=draws,
                              standing=lambda key: standing_copy(dest, key, now_ts))
        out["failed"] += failed
        out["built"] = sorted(files)
        if files:
            out["written"], _deleted = E.sync_keys(dest, files, [], dry_run=dry_run)
    except (Exception, SystemExit) as e:  # noqa: BLE001 - the step failed, not the refresh
        # counted as nothing written: if `sync_keys` died part-way a file may be
        # fresh on disk, and its standing copy's own time then says so
        files = {}
        out["built"] = []
        out["failed"].append(refusal("*", e))
    for f in out["failed"]:
        log(f"!!! GAME FILE FAILED ({f['file']}): {f['error']} - the previously written file "
            "is left in place")
    log(f"game: built {out['built']} wrote {out['written']} failed {len(out['failed'])}")
    try:
        out["gate_report"] = staleness(dest, files, out["failed"], now_ts)
    except Exception as e:  # noqa: BLE001 - an unread freeze is reported as one, never as clean
        out["gate_report"] = {"kind": GATE_REPORT_KIND, "checked_at": iso(now_ts),
                              "dest": str(dest), "clean": False,
                              "statement": "game: STALENESS NOT READ "
                                           f"({type(e).__name__}: {e}) - treat every game "
                                           "file as possibly frozen"}
    if report and not dry_run:
        try:
            write_report(report, out["gate_report"])
        except OSError as e:
            log(f"!!! GAME GATE REPORT NOT WRITTEN ({report}): {e}")
    log(out["gate_report"]["statement"])
    return out


def summary(files):
    lines = []
    rec = files.get(RECORD_KEY)
    if rec:
        bl = " | ".join(f"{b['id']} {b['d_brier']['estimate']:+.4f} {b['d_brier']['interval']}"
                        for b in rec["baselines"])
        mk = rec["market_comparison"]["d_brier"]
        lines.append(f"{RECORD_KEY}: {rec['population']['games']:,} games "
                     f"{rec['population']['seasons']} | {bl} | market (withheld) "
                     f"{mk['estimate']:+.4f} {mk['interval']} on "
                     f"{rec['market_comparison']['games']:,}")
    fc = files.get(FORECAST_KEY)
    if fc:
        lines.append(f"{FORECAST_KEY}: {fc['season']} week {fc['week']}, {len(fc['games'])} games, "
                     f"as of {fc['as_of']['instant']}, results through "
                     f"{fc['as_of']['results_through']}")
        if fc["season_stage"]:
            lines.append("  stage: " + fc["season_stage"]["statement"])
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true", help="build and gate; write nothing")
    mode.add_argument("--write", action="store_true")
    ap.add_argument("--dest", help="a directory, or `web` for WEB_EXPORT_DIR (with --write)")
    ap.add_argument("--now", type=float, help="unix seconds; default the wall clock")
    ap.add_argument("--report", help="where the gate report is written (with --write); default "
                                     f"for `--dest web`: <STORAGE_DIR>/logs/{GATE_REPORT_FILE}")
    a = ap.parse_args(argv)
    from jobs import export_web as E
    E.assert_numeric_stack()
    now = time.time() if a.now is None else a.now
    if a.check:
        files, failed = build(now)
        print(summary(files))
        for f in failed:
            print(f"FAILED {f['file']}: {f['error']}", file=sys.stderr)
        return 1 if failed or not files else 0
    if not a.dest:
        ap.error("--write needs --dest")
    dest = E.require_setting("WEB_EXPORT_DIR") if a.dest == "web" else a.dest
    report = a.report
    if report is None and a.dest == "web":
        import config
        report = config.storage_path("logs", GATE_REPORT_FILE)
    out = publish(dest, now_ts=now, report=report)
    return 1 if out["failed"] or not out["built"] else 0


if __name__ == "__main__":
    sys.exit(main())
