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

Reads `market_log.db` read-only (`mode=ro`) and nothing else. Never uploads.
Publishes nothing from KXNFLSPREAD or KXNFLTOTAL, and no total at all: c-28's
total is a league-level placeholder, not a forecast of this game.
"""
from __future__ import annotations

import argparse
import datetime as dt
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

def stage_block(m, stage):
    out = {"games": m["n"][stage], "vs": {}}
    for b, _l, _d in BASELINES:
        r = m["stages"][stage][b]
        out["vs"][b] = {"brier_model": r4(r["corp_model"]["bs"]),
                        "brier_baseline": r4(r["corp_comparator"]["bs"]),
                        "d_brier": interval(r["diffs"]["dBrier"])}
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
             "vs_elo_nomov": d, "compared": compared(d["verdict"])}
    if other:
        o = interval(m["stages"][other]["elo_nomov"]["diffs"]["dBrier"])
        ofig = f"{o['estimate']:+.4f} [{o['interval'][0]:+.4f}, {o['interval'][1]:+.4f}]"
        owhere = {"weeks_1_4": "in weeks 1 to 4", "weeks_5_plus": "from week 5 on"}[other]
        text += (f" {owhere[0].upper() + owhere[1:]} it has been {compared(o['verdict'])} it: "
                 f"{ofig} over {m['n'][other]:,} games.")
        block["other"] = {"stage": other, "games": m["n"][other], "vs_elo_nomov": o,
                          "compared": compared(o["verdict"])}
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
    return f"stage check: {len(pairs)} stage figure(s) agree with the record"


def gate(files):
    """The contract, the source gate and the metric gate, over whatever built;
    with both files, also the stage check. -> statements; raises on any failure."""
    from jobs import export_web as E
    from jobs import metric_registry as MR
    from jobs import source_registry
    E.validate_contract(files)
    source_registry.require_declared(files)
    mets = [m for m in MR.GAME_METRICS if MR._files_of(m) <= set(files)]
    out = [MR.require(dict(files), mets).statement]
    if FORECAST_KEY in files and RECORD_KEY in files:
        out.append(stage_agrees(files))
    return "\n".join(out)


def build(now_ts, log=print, draws=None):
    """-> ({key: payload}, [{file, error}]). Each file is built and gated on its
    own; if both build, the pair is gated too, and a disagreement writes neither."""
    m = measure(log=log, draws=draws)
    files, failed = {}, []
    for key, kind, fn in ((RECORD_KEY, RECORD_KIND, lambda: build_record(m)),
                          (FORECAST_KEY, FORECAST_KIND, lambda: build_forecast(m, now_ts))):
        try:
            payload = {key: envelope(kind, now_ts, fn())}
            gate(payload)
            files.update(payload)
        except Exception as e:  # noqa: BLE001 - one file's failure is that file's
            failed.append({"file": key, "error": f"{type(e).__name__}: {e}"})
    if len(files) == 2:
        try:
            log(gate(files))
        except Exception as e:  # noqa: BLE001 - the two disagree: publish neither
            failed.append({"file": "*", "error": f"{type(e).__name__}: {e}"})
            files = {}
    return files, failed


def publish(dest, now_ts=None, log=print, draws=None, dry_run=False):
    """Build, gate and write what passed. NEVER RAISES: a failure is logged and
    listed, and that file's previous copy stays where it was. -> summary."""
    from jobs import export_web as E
    now_ts = time.time() if now_ts is None else now_ts
    out = {"built": [], "failed": [], "written": 0}
    try:
        files, failed = build(now_ts, log=log, draws=draws)
        out["failed"] += failed
        out["built"] = sorted(files)
        if files:
            out["written"], _deleted = E.sync_keys(dest, files, [], dry_run=dry_run)
    except (Exception, SystemExit) as e:  # noqa: BLE001 - the step failed, not the refresh
        out["failed"].append({"file": "*", "error": f"{type(e).__name__}: {e}"})
    for f in out["failed"]:
        log(f"!!! GAME FILE FAILED ({f['file']}): {f['error']} - the previously written file "
            "is left in place")
    log(f"game: built {out['built']} wrote {out['written']} failed {len(out['failed'])}")
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
    out = publish(dest, now_ts=now)
    return 1 if out["failed"] or not out["built"] else 0


if __name__ == "__main__":
    sys.exit(main())
