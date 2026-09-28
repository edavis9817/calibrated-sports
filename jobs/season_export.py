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


def publish(payload, dest, key=KEY, dry_run=False):
    """Write into a publishing tree (WEB_EXPORT_DIR) and own `season/` there.

    Order matters: the file is validated against the contract and against the
    metric registry's season gate BEFORE `sync_keys` touches disk, so a refusal
    leaves the served tree as it was. -> (written, deleted, gate statement)."""
    from jobs import export_web as E
    from jobs import metric_registry as MR
    assert key.startswith(OWNED_PREFIX), key
    validated(payload, key)
    gate = MR.require({key: payload}, MR.SEASON_METRICS)
    written, deleted = E.sync_keys(dest, {key: payload}, ["season/"], dry_run=dry_run)
    return written, deleted, gate.statement


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
