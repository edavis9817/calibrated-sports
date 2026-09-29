"""The season model as a published shape: `season/{sport}/division.json` (a-41).

Built by `jobs.season_model`; this module only shapes, validates and writes.

EVERY SENTENCE IS GENERATED FROM THE FIGURES, and every verdict can come out
the other way (tests drive each value). The one that matters is `display`:
when the walk-forward record does not beat the standings carried forward, the
file says so and names the standings baseline as what a page should print, and
that switch is computed here, not decided by whoever runs the export.

WHERE IT WRITES. Its own top-level prefix `season/`, which this builder owns
WHOLLY and fills wholly (CLAUDE.md, one builder per prefix).
  * `write()` - `config.storage_path("season_model")`, reaching nothing. The
    a-41 default, and still the default of `jobs.season_model --write`.
  * `publish()` - WEB_EXPORT_DIR, through `export_web.sync_keys(dest, files,
    ["season/"])` (a-42). That is the tree the uploader walks, so this is the
    publishing path, and it is reached only by `--dest web`. `sync_keys` gives
    the contract check and the source gate; `publish()` adds the season metric
    gate before it. `season/` is new and top-level, so no other builder's prefix
    contains it or is contained by it (asserted in tests/test_season_model.py).
It never uploads. The uploader does, when `weekly_refresh` hands it the
`REFRESHED season/` declaration this job prints on a real `--dest web` write.
"""
from __future__ import annotations

import datetime as dt
import json
import math
import os

from jsonschema import Draft202012Validator

import config
from models import season as M

SCHEMA_KIND = "season_model"
SPORT = "nfl"
MODEL = "division"
KEY = "season/%s/%s.json" % (SPORT, MODEL)
Z95 = 1.959963984540054

BLOCK_CODE = "divseason_block"
SEASON_BLOCK_CODE = "season_block"
MC_CODE = "mc_binomial95"
CAL_CODE = "divseason_block_rate"

BASELINE_LABELS = {
    "standings_leader": "the standings leader, carried forward (co-leaders share)",
    "standings_coin_flip": "the standings, with every remaining game a coin flip",
}


def _contract_path():
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "web", "contract", "v2", "contract.schema.json")


def contract():
    with open(_contract_path(), encoding="utf-8") as f:
        return json.load(f)


class ContractError(AssertionError):
    pass


def kind_of(key, c=None):
    import re
    c = c or contract()
    for e in c["x-contract"]["keys"]:
        if re.match(e["pattern"], key):
            return e["kind"]
    return None


def validated(payload, key=KEY):
    c = contract()
    k = kind_of(key, c)
    if k != payload.get("kind"):
        raise ContractError("key %r routes to kind %r, payload says %r"
                            % (key, k, payload.get("kind")))
    name = c["x-contract"]["kinds"][k]
    v = Draft202012Validator({"$ref": "#/$defs/%s" % name, "$defs": c["$defs"]})
    errs = sorted(v.iter_errors(payload), key=lambda e: list(e.path))
    if errs:
        raise ContractError("; ".join("%s: %s" % ("/".join(map(str, e.path)), e.message)
                                      for e in errs[:8]))
    # invariants the schema cannot state
    for d in payload["divisions"]:
        for field in ("p", "p_standings"):
            s = sum(t[field] for t in d["teams"])
            if abs(s - 1.0) > 1e-6:
                raise ContractError("%s %s sums to %.6f" % (d["division"], field, s))
    return payload


def iso(ts=None):
    t = dt.datetime.now(dt.timezone.utc) if ts is None else \
        dt.datetime.fromtimestamp(ts, dt.timezone.utc)
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def r4(x):
    return None if x is None else round(float(x), 4)


def mc_se(p, n):
    return math.sqrt(max(p * (1.0 - p), 0.0) / n)


def record_verdict(vs):
    """`beats_standings` only if the model is better than BOTH baselines."""
    v = [vs[b]["verdict"] for b in ("standings_leader", "standings_coin_flip")]
    if "not_readable" in v:
        return "not_readable"
    if all(x == "better_than" for x in v):
        return "beats_standings"
    if any(x == "worse_than" for x in v):
        return "worse_than_standings"
    return "no_better_than_standings"


def display_choice(verdict):
    if verdict == "beats_standings":
        return {"show": "model",
                "reason": "the walk-forward record beats the standings carried forward "
                          "against both baselines"}
    return {"show": "standings_coin_flip",
            "reason": "the walk-forward record does not beat the standings carried "
                      "forward (%s), so a page should print the standings baseline, "
                      "labelled as such" % verdict.replace("_", " ")}


def _pct(x):
    return "%.1f%%" % (100 * x)


def record_statement(rec):
    b = rec["brier"]
    vc = rec["vs"]["standings_coin_flip"]
    vl = rec["vs"]["standings_leader"]
    head = ("Scored walk-forward on %d division races, %d-%d, from every week of each "
            "season (%d forecasts). Brier, lower is better: model %.3f; standings with "
            "coin-flip games %.3f; standings leader %.3f."
            % (rec["division_seasons"], rec["seasons"][0], rec["seasons"][1],
               rec["n_states"], b["model"], b["standings_coin_flip"], b["standings_leader"]))
    diffs = (" Model minus coin-flip standings %+.3f (95%% interval %+.3f to %+.3f); "
             "model minus leader %+.3f (%+.3f to %+.3f), resampling whole division races."
             % (vc["estimate"], *vc["interval"], vl["estimate"], *vl["interval"]))
    tail = {
        "beats_standings": " Better than the standings carried forward, on both baselines.",
        "no_better_than_standings": " No better than the standings carried forward.",
        "worse_than_standings": " Worse than the standings carried forward.",
        "not_readable": " Too few races to read.",
    }[rec["verdict"]]
    return head + diffs + tail


def calibration_bins(bins):
    out = []
    for c in bins:
        if not c["n"]:
            out.append({"bin": c["bin"], "n": 0, "n_blocks": 0, "forecast": None,
                        "realised": None, "interval": None, "verdict": None})
            continue
        lo, hi = c["interval"]
        v = "within" if lo <= c["forecast"] <= hi else \
            ("realised_above" if c["forecast"] < lo else "realised_below")
        out.append({"bin": [r4(c["bin"][0]), r4(c["bin"][1])], "n": c["n"],
                    "n_blocks": c["n_blocks"], "forecast": r4(c["forecast"]),
                    "realised": r4(c["realised"]), "interval": [r4(lo), r4(hi)],
                    "verdict": v})
    return out


def calibration_statement(bins):
    live = [b for b in bins if b["n"]]
    off = [b for b in live if b["verdict"] != "within"]
    if not off:
        return ("Every one of %d probability bins realised a rate whose interval "
                "contains its average forecast." % len(live))
    parts = ["%s-%s forecast %s, realised %s" % (_pct(b["bin"][0]), _pct(b["bin"][1]),
                                                 _pct(b["forecast"]), _pct(b["realised"]))
             for b in off]
    over = sum(1 for b in off if (b["forecast"] < 0.5) == (b["verdict"] == "realised_above"))
    lean = (" In %d of them the forecast sits further from 50%% than what happened: "
            "the model is overconfident there." % over) if over else ""
    return ("%d of %d probability bins realised a rate whose interval excludes the "
            "average forecast: %s.%s" % (len(off), len(live), "; ".join(parts), lean))


def _vs(d):
    return {"estimate": r4(d["estimate"]), "interval": [r4(x) for x in d["interval"]],
            "n_blocks": d["n_blocks"], "n_states": d["n_states"], "method": BLOCK_CODE,
            "verdict": d["verdict"],
            "by_season": {"estimate": r4(d["by_season_blocks"]["estimate"]),
                          "interval": [r4(x) for x in d["by_season_blocks"]["interval"]],
                          "n_blocks": d["by_season_blocks"]["n_blocks"],
                          "method": SEASON_BLOCK_CODE,
                          "verdict": d["by_season_blocks"]["verdict"]}}


def build(res, generated_at=None):
    from jobs import season_model as J
    s = res["summary"]
    cur = res["current"]
    tb = res["tiebreak"]
    n = cur["n"]
    seasons = sorted(res["fits"])
    rec = {
        "seasons": [seasons[0], seasons[-1]],
        "division_seasons": s["races"]["division_seasons"],
        "n_states": s["vs"]["coinflip"]["n_states"],
        "simulations_per_state": res["n_walk"],
        "block": "division-season: one division's forecasts across the weeks of one "
                 "season are one race, resampled whole",
        "states": "after week k, for k = 0 (preseason) through the week before the last",
        "brier": {"model": r4(s["brier"]["model"]),
                  "standings_coin_flip": r4(s["brier"]["coinflip"]),
                  "standings_leader": r4(s["brier"]["leader"])},
        "baselines": BASELINE_LABELS,
        "vs": {"standings_leader": _vs(s["vs"]["leader"]),
               "standings_coin_flip": _vs(s["vs"]["coinflip"])},
        "by_week": [{"after_week": w["after_week"], "n": w["n_division_seasons"],
                     "model": r4(w["model"]), "standings_coin_flip": r4(w["coinflip"]),
                     "standings_leader": r4(w["leader"])} for w in s["by_week"]],
        "races": {"division_seasons": s["races"]["division_seasons"],
                  "tied_at_top_final": s["races"]["tied_at_top_final"],
                  "margin_le_1_game": s["races"]["margin_le_1_game"],
                  "open_after_week": s["races"]["open_after_week"],
                  "open_definition": s["races"]["open_definition"]},
        "open_states": {
            "note": "descriptive, not the pre-registered verdict: only the forecasts "
                    "made while the race was open",
            "n_states": s["open_states"]["n_states"],
            "n_blocks": s["open_states"]["n_blocks"],
            "brier": {"model": r4(s["open_states"]["brier"]["model"]),
                      "standings_coin_flip": r4(s["open_states"]["brier"]["coinflip"]),
                      "standings_leader": r4(s["open_states"]["brier"]["leader"])},
            "vs": {b2: {"estimate": r4(v["estimate"]),
                        "interval": [r4(x) for x in v["interval"]],
                        "n_blocks": v["n_blocks"], "verdict": v["verdict"]}
                   for b2, v in (("standings_leader", s["open_states"]["vs"]["leader"]),
                                 ("standings_coin_flip", s["open_states"]["vs"]["coinflip"]))},
        },
        "constants_by_season": [{"season": y, **{k: r4(v) for k, v in res["fits"][y]["params"].items()},
                                 "fit_seasons": res["fits"][y]["fit_seasons"],
                                 "fit_games": res["fits"][y]["fit_games"]} for y in seasons],
    }
    rec["verdict"] = record_verdict(rec["vs"])
    rec["statement"] = record_statement(rec)
    cal = calibration_bins(s["calibration"]["model"])
    rec["calibration"] = cal
    rec["calibration_statement"] = calibration_statement(cal)

    divisions = []
    for d in sorted(cur["model"]):
        members = cur["season_obj"].divisions[d]
        conf = cur["season_obj"].conference[members[0]]
        teams = []
        for t in members:
            p = cur["model"][d][t]
            pc = cur["coinflip"][d][t]
            w, l, ti = cur["record"][t]
            se = mc_se(p, n)
            teams.append({"team": t, "wins": w, "losses": l, "ties": ti,
                          "rating": round(cur["ratings"][t], 1),
                          "p": r4(p), "mc_se": r4(se),
                          "p_interval": [r4(max(0.0, p - Z95 * se)), r4(min(1.0, p + Z95 * se))],
                          "p_standings": r4(pc), "mc_se_standings": r4(mc_se(pc, n)),
                          "leader_share": r4(cur["leader"][d][t])})
        # rounding can move a sum off 1 by a few 1e-4; restore it on the largest
        for field in ("p", "p_standings"):
            gap = round(1.0 - sum(x[field] for x in teams), 4)
            if gap:
                big = max(teams, key=lambda x: x[field])
                big[field] = round(big[field] + gap, 4)
        divisions.append({"division": d, "conference": conf, "teams": teams})

    params = cur["params"]
    max_se = max(t["mc_se"] for d in divisions for t in d["teams"])
    payload = {
        "schema_version": 2,
        "generated_at": generated_at or iso(),
        "kind": SCHEMA_KIND,
        "sport": SPORT,
        "model": MODEL,
        "label": "Chance to win the division",
        "season": cur["season"],
        "as_of": {"games_played": cur["games_played"], "games_total": cur["games_total"],
                  "last_complete_week": cur["last_complete_week"],
                  "last_game_ts": cur["last_game_ts"]},
        "sources": ["nfl_games@%s" % res["versions"]["nfl_games"],
                    "nfl_teams@%s" % res["versions"]["nfl_teams"]],
        "method": {
            "rating": ("Margin-of-victory Elo from final scores only, every game since "
                       "1999, regular season and playoffs. Chosen because it is "
                       "sequential by construction (a rating is a function of earlier "
                       "games only), has three constants that can be refit each season "
                       "on earlier seasons alone, and reads results rather than market "
                       "prices. Margin multiplier ln(|margin|+1) x 2.2 / (0.001 x winner's "
                       "rating edge + 2.2); ratings regress toward 1500 by `regress` at "
                       "each season start; a relocated franchise keeps its rating."),
            "game_distribution": ("Each remaining game is a Bernoulli draw with "
                                  "P(home wins) = 1 / (1 + 10^(-(rating difference + "
                                  "home advantage) / 400))."),
            "constants": {"k": r4(params.k), "hfa": r4(params.hfa),
                          "regress": r4(params.regress),
                          "fitted_on": [J.FIT_FROM, cur["season"] - 1],
                          "fit_games": cur["fit_games"],
                          "fit_log_loss": r4(cur["fit_log_loss"]),
                          "grid": "k %s; hfa %s; regress %s; lowest mean game log loss"
                                  % (J.GRID["k"], J.GRID["hfa"],
                                     [round(x, 3) for x in J.GRID["regress"]])},
            "simulations": n,
            "mc_se_max": r4(max_se),
            "limitations": [
                "Ratings are held fixed inside each simulation, so a long remaining "
                "schedule is simulated with less uncertainty than it has. The record's "
                "calibration shows the consequence.",
                "Ties are never simulated; actual ties count half a win.",
                "Every game has a home side, including neutral-site international games.",
                "Scores only: injuries, quarterback changes and the market are not read.",
            ],
        },
        "tiebreakers": {
            "format": "division",
            "implemented": list(M.IMPLEMENTED_TIEBREAKERS),
            "not_implemented": list(M.NOT_IMPLEMENTED_TIEBREAKERS),
            "residual": M.RESIDUAL_TIEBREAK,
            "restart_rule": "when a step eliminates a club from a 3- or 4-club tie, the "
                            "survivors restart at head-to-head",
            "checked": {"seasons": [J.SCORE_FROM, J.SCORE_TO],
                        "division_seasons": tb["division_seasons"],
                        "agree": tb["agree"], "tied_at_top": tb["tied_at_top"],
                        "tied_agree": tb["tied_agree"],
                        "residual_draws": tb["residual_draws"],
                        "against": "division winners read from postseason hosting, "
                                   "independent of this code"},
        },
        "methods": [
            {"code": MC_CODE, "interval": "p +/- 1.96 Monte Carlo standard errors, "
             "sqrt(p(1-p)/simulations): simulation error only, not model error",
             "coverage_level": 0.95},
            {"code": BLOCK_CODE, "interval": "percentile bootstrap over division-seasons "
             "(whole races), 2,000 resamples", "coverage_level": 0.95},
            {"code": SEASON_BLOCK_CODE, "interval": "percentile bootstrap over seasons, "
             "2,000 resamples", "coverage_level": 0.95},
            {"code": CAL_CODE, "interval": "realised rate in the bin, percentile bootstrap "
             "over division-seasons, 2,000 resamples", "coverage_level": 0.95},
        ],
        "divisions": divisions,
        "record": rec,
        "display": display_choice(rec["verdict"]),
    }
    return payload


def export_dir():
    return config.storage_path("season_model")


OWNED_PREFIX = "season/"


def publish(*_a, **_k):
    """RETIRED by a-55. `season/` now holds two files and one builder owns it, so
    publishing the division file alone would have `sync_keys` delete the
    projection beside it. Use `publish_all({KEY: division, PROJECTION_KEY:
    projection}, dest)`. Raises rather than quietly publishing half the prefix
    (CLAUDE.md: when a call stops meaning what its callers assume, make the old
    usage raise)."""
    raise TypeError("season_export.publish is retired (a-55): season/ holds the division "
                    "and projection files - call publish_all with both")


def write(payload, root=None, key=KEY):
    root = root or export_dir()
    assert key.startswith("season/"), key
    path = os.path.join(root, *key.split("/"))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(payload, f, indent=1, sort_keys=False)
        f.write("\n")
    return path


def count_values(obj):
    if isinstance(obj, dict):
        return sum(count_values(v) for v in obj.values())
    if isinstance(obj, list):
        return sum(count_values(v) for v in obj)
    return 1


def count_keys(obj):
    if isinstance(obj, dict):
        return len(obj) + sum(count_keys(v) for v in obj.values())
    if isinstance(obj, list):
        return sum(count_keys(v) for v in obj)
    return 0


def summary(payload):
    rec = payload["record"]
    return ("%s: %d divisions, %d teams, %d keys, %d leaf values; record %s over %d "
            "division-seasons; display=%s; validated"
            % (KEY, len(payload["divisions"]),
               sum(len(d["teams"]) for d in payload["divisions"]),
               count_keys(payload), count_values(payload), rec["verdict"],
               rec["division_seasons"], payload["display"]["show"]))


# ================================================================ projection (a-55)
#
# `season/{sport}/projection.json`, kind `season_projection`. The team page's
# forward line, from the season model's simulation of the remaining schedule
# rather than from the win rate so far times the games left. Built from
# `jobs.season_projection`'s walk-forward and current state, which
# `jobs.season_model.compute` runs beside the division model; published with it
# by `publish_all`, because one builder owns `season/` and fills all of it.

PROJECTION_KIND = "season_projection"
PROJECTION_MODEL = "projection"
PROJECTION_KEY = "season/%s/%s.json" % (SPORT, PROJECTION_MODEL)
PROJ_BLOCK_CODE = "season_block"
PROJ_Q_CODE = "sim_quantile"
PROJ_COVER_CODE = "season_block_coverage"
PROJECTION_BASELINES = {
    "pace": "the win rate so far times the games on the schedule - the line the team "
            "page drew before this file",
    "standings_coin_flip": "wins so far plus half the games remaining",
}
LINE_LABEL = "Season model"


def r2(x):
    return None if x is None else round(float(x), 2)


def proj_record_verdict(vs):
    v = [vs[b]["verdict"] for b in ("pace", "standings_coin_flip")]
    if "not_readable" in v:
        return "not_readable"
    if all(x == "better_than" for x in v):
        return "beats_baselines"
    if any(x == "worse_than" for x in v):
        return "worse_than_a_baseline"
    return "no_better_than_a_baseline"


def proj_display(verdict):
    if verdict == "beats_baselines":
        return {"show": "model",
                "reason": "the walk-forward record beats both the pace line and the "
                          "standings with coin-flip games on final wins"}
    return {"show": "standings_coin_flip",
            "reason": "the walk-forward record does not beat both baselines (%s), so a "
                      "page should draw the coin-flip standings line, labelled as such"
                      % verdict.replace("_", " ")}


def proj_record_statement(rec):
    r = rec["rmse"]
    vp, vc = rec["vs"]["pace"], rec["vs"]["standings_coin_flip"]
    head = ("Scored walk-forward on %d team-seasons, %d-%d, from every week of each season "
            "once the team had played (%d forecasts). Error of the projected final wins, "
            "root mean square: model %.2f wins; pace %.2f; standings with coin-flip games %.2f."
            % (rec["team_seasons"], rec["seasons"][0], rec["seasons"][1], rec["n_forecasts"],
               r["model"], r["pace"], r["standings_coin_flip"]))
    diffs = (" Mean squared error, model minus pace %+.2f (95%% interval %+.2f to %+.2f); "
             "model minus coin-flip standings %+.2f (%+.2f to %+.2f), resampling whole seasons."
             % (vp["estimate"], *vp["interval"], vc["estimate"], *vc["interval"]))
    tail = {
        "beats_baselines": " Better than both.",
        "no_better_than_a_baseline": " No better than at least one of them.",
        "worse_than_a_baseline": " Worse than at least one of them.",
        "not_readable": " Too few seasons to read.",
    }[rec["verdict"]]
    return head + diffs + tail


def proj_coverage_statement(cov):
    parts = []
    for lvl in ("80", "95"):
        c = cov[lvl]
        word = {"within": "consistent with", "too_narrow": "fewer than",
                "too_wide": "more than",
                "not_readable": "too few seasons to compare with"}[c["verdict"]]
        parts.append("the central %s%% interval held the real final record %.1f%% of the time, "
                     "%s the %.1f%% of simulations inside it"
                     % (lvl, 100 * c["realised"], word, 100 * c["mass"]))
    return ("Walk-forward, " + "; ".join(parts) + ". Wins are whole numbers, so an interval "
            "holds more than its nominal share of simulations; the share it holds is published "
            "beside it.")


def _ordinal(n):
    return "%d%s" % (n, "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th"))


def _w(x):
    return ("%.1f" % x).rstrip("0").rstrip(".")


def team_statement(t, of):
    p = t["projection"]
    rem = t["remaining"]
    lo, hi = p["interval80"]
    s = ("Projected %.1f wins over %d games; %s to %s in the central interval, which holds "
         "%.0f%% of simulations." % (p["mean"], t["games_total"], _w(lo), _w(hi),
                                     100 * p["interval80_mass"]))
    if not rem["games"]:
        return s + " No games remain."
    eff = round(rem["schedule_effect"], 1)
    if eff == 0:
        e = "the same number of wins, to one decimal, as against league-average opponents"
    else:
        e = "%.1f %s wins than against league-average opponents" % (
            abs(eff), "fewer" if eff < 0 else "more")
    rank = rem["difficulty_rank"]
    where = "hardest" if rank == 1 else "%s hardest" % _ordinal(rank)
    return s + (" The %d games left are the %s schedule of %d for a league-average "
                "team, by the model's own ratings, and are worth %s."
                % (rem["games"], where, of, e))


def build_projection(res, generated_at=None):
    from jobs import season_model as J
    from jobs import season_projection as P
    s = res["projection_summary"]
    pc = res["projection_current"]
    cur = res["current"]
    seasons = sorted(res["projection_sigma"])
    vs = {}
    for name, key in (("pace", "pace"), ("standings_coin_flip", "coinflip")):
        d = s["vs"][key]
        vs[name] = {"estimate": r4(d["estimate"]), "interval": [r4(x) for x in d["interval"]],
                    "n_blocks": d["n_blocks"], "n_forecasts": d["n_forecasts"],
                    "method": PROJ_BLOCK_CODE, "verdict": d["verdict"]}
    cov = {lvl: {"nominal": r4(c["nominal"]), "realised": r4(c["realised"]),
                 "mass": r4(c["mass"]),
                 "difference_interval": [r4(x) for x in c["difference_interval"]],
                 "n_blocks": c["n_blocks"], "n_forecasts": c["n_forecasts"],
                 "method": PROJ_COVER_CODE, "verdict": c["verdict"]}
           for lvl, c in s["coverage"].items()}
    rec = {
        "seasons": [seasons[0], seasons[-1]],
        "team_seasons": s["team_seasons"],
        "n_forecasts": s["n_forecasts"],
        "states": s["states"],
        "simulations_per_state": res["n_proj_walk"],
        "block": "season: the 32 final records of one season are zero-sum, resampled whole",
        "rmse": {"model": r4(s["rmse"]["model"]), "pace": r4(s["rmse"]["pace"]),
                 "standings_coin_flip": r4(s["rmse"]["coinflip"])},
        "baselines": PROJECTION_BASELINES,
        "vs": vs,
        "coverage": cov,
        "by_week": [{"after_week": w["after_week"], "n": w["n"], "model": r4(w["model"]),
                     "pace": r4(w["pace"]), "standings_coin_flip": r4(w["coinflip"]),
                     "coverage80": r4(w["coverage80"]), "mass80": r4(w["mass80"])}
                    for w in s["by_week"]],
        "sigma_by_season": [{"season": y, "sigma": r4(res["projection_sigma"][y]["sigma"]),
                             "fit_seasons": res["projection_sigma"][y]["fit_seasons"]}
                            for y in seasons],
    }
    rec["verdict"] = proj_record_verdict(vs)
    rec["statement"] = proj_record_statement(rec)
    rec["coverage_statement"] = proj_coverage_statement(cov)

    sobj = pc["season_obj"]
    n = pc["n"]
    teams = []
    for t in pc["teams"]:
        mc = t["sd"] / math.sqrt(n)
        rem = t["remaining"]
        row = {
            "team": t["team"], "conference": sobj.conference[t["team"]],
            "division": sobj.division[t["team"]], "rating": round(t["rating"], 1),
            "wins": t["wins"], "losses": t["losses"], "ties": t["ties"],
            "games_played": t["games_played"], "games_total": t["games_total"],
            "projection": {
                "mean": r2(t["mean"]), "mc_se": r4(mc), "sd": r2(t["sd"]),
                "median": t["median"],
                "interval80": t["interval80"], "interval80_mass": r4(t["interval80_mass"]),
                "interval95": t["interval95"], "interval95_mass": r4(t["interval95_mass"]),
                "distribution": [{"wins": d["wins"], "p": r4(d["p"])} for d in t["distribution"]],
            },
            "path": [{"week": p["week"], "mean": r2(p["mean"]),
                      "interval80": p["interval80"], "interval95": p["interval95"]}
                     for p in t["path"]],
            "remaining": {
                "games": rem["games"],
                "opponent_rating_mean": None if rem["opponent_rating_mean"] is None
                else round(rem["opponent_rating_mean"], 1),
                "expected_wins": r2(rem["expected_wins"]),
                "expected_wins_average_opponents": r2(rem["expected_wins_average_opponents"]),
                "schedule_effect": r2(rem["schedule_effect"]),
                "average_team_expected_wins": r2(rem["average_team_expected_wins"]),
                "average_team_win_share": r4(rem["average_team_win_share"]),
                "difficulty_rank": rem["difficulty_rank"],
            },
            "schedule": [{"week": g["week"], "opponent": g["opponent"], "home": g["home"],
                          "opponent_rating": round(g["opponent_rating"], 1),
                          "p_win": r4(g["p_win"])} for g in t["schedule"]],
        }
        row["statement"] = team_statement(row, pc["ranked_of"])
        teams.append(row)

    params = pc["params"]
    payload = {
        "schema_version": 2,
        "generated_at": generated_at or iso(),
        "kind": PROJECTION_KIND,
        "sport": SPORT,
        "model": PROJECTION_MODEL,
        "label": "Projected final record",
        "season": pc["season"],
        "as_of": {"games_played": cur["games_played"], "games_total": cur["games_total"],
                  "last_complete_week": cur["last_complete_week"],
                  "last_game_ts": cur["last_game_ts"]},
        "sources": ["nfl_games@%s" % res["versions"]["nfl_games"],
                    "nfl_teams@%s" % res["versions"]["nfl_teams"]],
        "line": {
            "label": LINE_LABEL,
            "replaces": PROJECTION_BASELINES["pace"],
            "note": ("The dashed forward line is the season model's simulation of the "
                     "remaining schedule: each remaining game at the model's ratings of the "
                     "two teams, the ratings themselves uncertain. Not the average so far "
                     "carried forward."),
        },
        "method": {
            "rating": ("Margin-of-victory Elo from final scores only - the division model's "
                       "rating (season/%s/division.json), same constants, same games. Points "
                       "per game is not an input." % SPORT),
            "game_distribution": ("Each remaining game is a Bernoulli draw with P(home wins) = "
                                  "1 / (1 + 10^(-(rating difference + home advantage) / 400)), "
                                  "at each simulation's drawn ratings."),
            "rating_uncertainty": ("Each simulation draws every team's rating once from "
                                   "N(rating, sigma^2) and plays the whole remaining season at "
                                   "that strength. sigma is chosen from the grid by the lowest "
                                   "mean CRPS of final wins on earlier seasons only. The division "
                                   "file holds ratings fixed; this file does not, because on "
                                   "final wins fixed ratings were measured overconfident."),
            "constants": {"k": r4(params.k), "hfa": r4(params.hfa),
                          "regress": r4(params.regress),
                          "fitted_on": [J.FIT_FROM, pc["season"] - 1],
                          "fit_games": cur["fit_games"],
                          "fit_log_loss": r4(cur["fit_log_loss"]),
                          "grid": "k %s; hfa %s; regress %s; lowest mean game log loss"
                                  % (J.GRID["k"], J.GRID["hfa"],
                                     [round(x, 3) for x in J.GRID["regress"]])},
            "sigma": {"value": r4(pc["sigma"]), "fitted_on": pc["sigma_fit_seasons"],
                      "fit_crps": r4(pc["sigma_fit_crps"]), "grid": list(P.SIGMA_GRID),
                      "criterion": "lowest mean CRPS of final wins, every state of every "
                                   "earlier season"},
            "simulations": n,
            "mc_se_max": r4(max(x["projection"]["mc_se"] for x in teams)),
            "limitations": [
                "Ties are never simulated; actual ties count half a win.",
                "Every game has a home side, including neutral-site international games.",
                "Scores only: injuries, quarterback changes and the market are not read.",
                "A team's drawn strength is held for the rest of its season; ratings are not "
                "updated by simulated results.",
                "The central intervals are quantiles of whole wins, so each holds more than "
                "its nominal share of simulations; the share is published beside it.",
                "The division file holds ratings fixed and this file does not, so the two can "
                "differ in how far apart they put the teams of one division.",
            ],
        },
        "methods": [
            {"code": PROJ_Q_CODE, "interval": "central 80% and 95% quantiles of the simulated "
             "final (or cumulative) wins", "coverage_level": 0.8},
            {"code": PROJ_BLOCK_CODE, "interval": "percentile bootstrap over seasons, "
             "2,000 resamples", "coverage_level": 0.95},
            {"code": PROJ_COVER_CODE, "interval": "realised share inside the interval minus "
             "the simulated mass inside it, percentile bootstrap over seasons, 2,000 "
             "resamples", "coverage_level": 0.95},
        ],
        "teams": teams,
        "record": rec,
        "display": proj_display(rec["verdict"]),
    }
    return payload


def validated_projection(payload, key=PROJECTION_KEY):
    """Contract, then the invariants the schema cannot state - above all that the
    headline and the per-game figures come from one simulation."""
    c = contract()
    k = kind_of(key, c)
    if k != payload.get("kind"):
        raise ContractError("key %r routes to kind %r, payload says %r"
                            % (key, k, payload.get("kind")))
    name = c["x-contract"]["kinds"][k]
    v = Draft202012Validator({"$ref": "#/$defs/%s" % name, "$defs": c["$defs"]})
    errs = sorted(v.iter_errors(payload), key=lambda e: list(e.path))
    if errs:
        raise ContractError("; ".join("%s: %s" % ("/".join(map(str, e.path)), e.message)
                                      for e in errs[:8]))
    for t in payload["teams"]:
        p = t["projection"]
        cur = t["wins"] + 0.5 * t["ties"]
        s = sum(d["p"] for d in p["distribution"])
        if abs(s - 1.0) > 5e-4 * len(p["distribution"]):
            raise ContractError("%s distribution sums to %.4f" % (t["team"], s))
        # the headline IS the per-game figures: record so far + sum of p_win
        ident = cur + sum(g["p_win"] for g in t["schedule"])
        if abs(ident - p["mean"]) > 0.006 + 5e-5 * len(t["schedule"]):
            raise ContractError("%s: mean %.2f but record + sum(p_win) = %.3f"
                                % (t["team"], p["mean"], ident))
        if abs(t["remaining"]["expected_wins"] - (p["mean"] - cur)) > 0.011:
            raise ContractError("%s: remaining.expected_wins disagrees with the mean"
                                % t["team"])
        if t["remaining"]["games"] != len(t["schedule"]) or len(t["path"]) != len(t["schedule"]):
            raise ContractError("%s: remaining games, schedule and path disagree" % t["team"])
        if t["games_total"] != t["games_played"] + t["remaining"]["games"]:
            raise ContractError("%s: games_total != played + remaining" % t["team"])
        lo80, hi80 = p["interval80"]
        lo95, hi95 = p["interval95"]
        if not (lo95 <= lo80 <= p["median"] <= hi80 <= hi95):
            raise ContractError("%s: intervals are not nested around the median" % t["team"])
        if lo95 < cur or hi95 > cur + t["remaining"]["games"]:
            raise ContractError("%s: interval outside what the schedule allows" % t["team"])
        if t["path"] and abs(t["path"][-1]["mean"] - p["mean"]) > 0.011:
            raise ContractError("%s: the path does not end at the projection" % t["team"])
    ranks = sorted(t["remaining"]["difficulty_rank"] for t in payload["teams"]
                   if t["remaining"]["difficulty_rank"] is not None)
    if ranks != list(range(1, len(ranks) + 1)):
        raise ContractError("difficulty ranks are not 1..%d" % len(ranks))
    return payload


def publish_all(payloads, dest, dry_run=False):
    """Both season files into a publishing tree, in ONE `sync_keys` call over
    `season/`: the builder owns the prefix and must fill all of it, so two calls
    would each delete the other's file (CLAUDE.md, one builder per prefix).
    Every file is validated and the metric gate runs over both BEFORE disk is
    touched. -> (written, deleted, gate statement)."""
    from jobs import export_web as E
    from jobs import metric_registry as MR
    for key, payload in payloads.items():
        assert key.startswith(OWNED_PREFIX), key
        if payload.get("kind") == PROJECTION_KIND:
            validated_projection(payload, key)
        else:
            validated(payload, key)
    gate = MR.require(dict(payloads), MR.SEASON_METRICS)
    written, deleted = E.sync_keys(dest, dict(payloads), [OWNED_PREFIX], dry_run=dry_run)
    return written, deleted, gate.statement


def projection_summary(payload):
    rec = payload["record"]
    return ("%s: %d teams, %d keys, %d leaf values; record %s over %d team-seasons; "
            "sigma %s; display=%s; validated"
            % (PROJECTION_KEY, len(payload["teams"]), count_keys(payload),
               count_values(payload), rec["verdict"], rec["team_seasons"],
               payload["method"]["sigma"]["value"], payload["display"]["show"]))
