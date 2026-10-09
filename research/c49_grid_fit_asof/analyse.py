"""c-49: read the eight walks and apply the pre-registered rule.

    python research/c49_grid_fit_asof/analyse.py --dir D:/temp/c49 \
        --published research/results/cfb_game_forecast.json \
        --json-out research/c49_grid_fit_asof/results/c49_grid_fit_asof.json \
        --log-out research/c49_grid_fit_asof/results/c49_grid_fit_asof.log

Rule: docs/C49-grid-fit-asof-preregistration.md. Reads only walks.py's outputs
and the committed c-39 result; opens no database. The verdict is one of
`repaired`, `repaired with a moved figure`, `not repaired`.
"""
import argparse
import json
import os

import numpy as np

MOVE = 1e-12            # f-32's detector
THRESHOLD = 0.0015      # f-32's binding materiality threshold
F32_CONTROL = 1378      # f-32's planted case, base code
F32_POP = 15508
YEARS = [str(y) for y in range(2005, 2027)]
FILES = ["base_a1", "base_a1_planted", "new_a1", "new_a1_planted",
         "base_mov", "new_mov", "base_plain", "new_plain"]


def moved(a, b):
    """(compared, moved beyond MOVE, max |delta|) over the forecasts both walks hold."""
    keys = [k for k in a["p"] if k in b["p"]]
    if not keys:
        return 0, 0, float("nan")
    d = np.abs(np.array([a["p"][k] for k in keys]) - np.array([b["p"][k] for k in keys]))
    return len(keys), int((d > MOVE).sum()), float(d.max())


def fits_changed(a, b):
    return [y for y in YEARS if a["fits"].get(y) != b["fits"].get(y)]


def brier(w):
    keys = sorted(w["p"])
    p = np.array([w["p"][k] for k in keys])
    y = np.array([w["y"][k] for k in keys], float)
    return float(np.mean((p - y) ** 2)), len(keys)


def planted_case(unplanted, planted):
    n, m, mx = moved(unplanted, planted)
    return {"population": unplanted["n_pop"], "compared": n, "moved": m, "max_abs_delta_p": mx,
            "fits_changed": fits_changed(unplanted, planted),
            "errors": {"unplanted": unplanted["errors"], "planted": planted["errors"]},
            "ever_bad": {"unplanted": unplanted["ever_bad"], "planted": planted["ever_bad"]}}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True)
    ap.add_argument("--published", required=True)
    ap.add_argument("--json-out", required=True)
    ap.add_argument("--log-out", required=True)
    a = ap.parse_args(argv)
    lines = []

    def out(s=""):
        print(s, flush=True)
        lines.append(s)

    W = {}
    for name in FILES:
        with open(os.path.join(a.dir, name + ".json"), encoding="utf-8") as f:
            W[name] = json.load(f)
    with open(os.path.join(a.dir, "plant.json"), encoding="utf-8") as f:
        plant = json.load(f)

    # ---- shape: assert what was read before trusting what is printed
    prints = {w["real_fingerprint"] for w in W.values()} | {plant["real_fingerprint"]}
    if len(prints) != 1:
        raise SystemExit("the eight walks did not read one store: %d fingerprints" % len(prints))
    for name, w in W.items():
        want = {"repaired_code": name.startswith("new_"), "planted": name.endswith("_planted"),
                "grid": name.split("_")[1], "chunk": 8640}
        got = {k: w[k] for k in want}
        if got != want:
            raise SystemExit("%s is not what its name says: %r" % (name, got))
        if w["n_pop"] < 10000 or len(w["y"]) != w["n_pop"]:
            raise SystemExit("%s: population %d, outcomes %d" % (name, w["n_pop"], len(w["y"])))
    pop = W["base_mov"]["n_pop"]
    if {w["n_pop"] for w in W.values()} != {pop}:
        raise SystemExit("populations differ between walks")
    for name in ("base_a1_planted", "new_a1_planted"):
        if W[name]["n_games"] != W[name.replace("_planted", "")]["n_games"] + 1:
            raise SystemExit("%s is not the unplanted store plus one game" % name)
    if not plant["feasible"]:
        raise SystemExit("the planted game is not feasible on today's store")
    if plant["game"]["start_ts"] <= W["base_a1"]["kick_max_pop"]:
        raise SystemExit("the synthetic game is not after every forecast in the population")
    R = {"preregistration": "docs/C49-grid-fit-asof-preregistration.md @ 4ceaeca",
         "store_fingerprint": prints.pop(), "games": W["base_mov"]["n_games"], "population": pop,
         "f32_population": F32_POP, "chunk": 8640,
         "seconds": {n: round(w["seconds"]) for n, w in W.items()},
         "plant": {k: plant[k] for k in ("game", "target", "rating_gap_plus_hfa", "needed", "feasible", "built_by")}}
    out("store: %d FBS-FBS games, %d forecasts in the 2005-2025 population (f-32: %d); one store across all eight walks"
        % (R["games"], pop, F32_POP))
    out("planted game: team %s at team %s, away side wins 1-0, a day after the last real game; gap + hfa %.0f "
        "against %.0f needed" % (plant["game"]["away"], plant["game"]["home"], plant["rating_gap_plus_hfa"],
                                 plant["needed"]))

    # ---- the planted case
    out("\n-- the planted case (GRID_MOV with a = 1.0, %d points)" % W["base_a1"]["grid_points"])
    ctl = planted_case(W["base_a1"], W["base_a1_planted"])
    rep = planted_case(W["new_a1"], W["new_a1_planted"])
    for tag, c in (("base code (control)", ctl), ("repaired code", rep)):
        out("   %-20s compared %d of %d, MOVED %d (max |dp| %.3g); fits changed in %d of 22 seasons %s; errors %d / %d; "
            "points ever bad %d -> %d"
            % (tag, c["compared"], c["population"], c["moved"], c["max_abs_delta_p"], len(c["fits_changed"]),
               c["fits_changed"], len(c["errors"]["unplanted"]), len(c["errors"]["planted"]),
               c["ever_bad"]["unplanted"], c["ever_bad"]["planted"]))
    fires = ctl["moved"] > 0
    nonvacuous = (rep["compared"] == rep["population"] and not rep["errors"]["unplanted"]
                  and not rep["errors"]["planted"]
                  and all(y in W["new_a1"]["fits"] and y in W["new_a1_planted"]["fits"] for y in YEARS))
    passed = fires and nonvacuous and rep["moved"] == 0 and not rep["fits_changed"]
    # the plant must still be SEEN by the repaired code
    fb0, fb1 = W["new_a1"]["first_bad_by_season"], W["new_a1_planted"]["first_bad_by_season"]
    cs0, cs1 = W["new_a1"]["choice_set"], W["new_a1_planted"]["choice_set"]
    newly = {s: fb1.get(s, 0) - fb0.get(s, 0) for s in sorted(set(fb0) | set(fb1)) if fb1.get(s, 0) != fb0.get(s, 0)}
    sets_moved = [y for y in cs0 if cs0[y] != cs1[y]]
    seen = {"points_marked_by_the_plant_by_season": newly, "choice_sets_changed": sets_moved,
            "choice_set_2027": {"unplanted": cs0["2027"], "planted": cs1["2027"]},
            "seen_only_by_the_season_after": set(newly) == {"2026"} and sets_moved == ["2027"]}
    out("   the plant, as the repaired code sees it: points newly marked by season %s; choice sets that changed %s "
        "(2027: %d -> %d)" % (newly, sets_moved, cs0["2027"], cs1["2027"]))
    und = {n: {y: v for y, v in W[n]["undefined"].items() if v["whole_walk"]} for n in ("new_a1", "new_a1_planted")}
    out("   substituted games under a chosen point (a = 1.0 grid): unplanted %s; planted %s"
        % (und["new_a1"] or "none", und["new_a1_planted"] or "none"))
    a1_base_vs_new = fits_changed(W["base_a1"], W["new_a1"])
    out("   on the a = 1.0 grid itself the repair changes the unplanted fit in %d of 22 seasons %s "
        "(not a published grid)" % (len(a1_base_vs_new), a1_base_vs_new))
    R["planted_case"] = {"control_base_code": ctl, "repaired_code": rep, "f32_control": F32_CONTROL,
                         "control_reproduces_f32": ctl["moved"] == F32_CONTROL and pop == F32_POP,
                         "detector_fires": fires, "non_vacuous": nonvacuous, "pass": passed, "plant_seen": seen,
                         "undefined": und, "a1_fits_changed_base_vs_repaired_unplanted": a1_base_vs_new}
    out("   control %d against f-32's %d; detector %s; repaired zero is %s -> planted case %s"
        % (ctl["moved"], F32_CONTROL, "FIRES" if fires else "BLIND", "non-vacuous" if nonvacuous else "VACUOUS",
           "PASS" if passed else "FAIL"))

    # ---- the no-change condition on the registered grids
    out("\n-- no-change condition, registered grids (base code against repaired code, same store)")
    nc = {}
    for g in ("mov", "plain"):
        b, n = W["base_" + g], W["new_" + g]
        if b["errors"] or n["errors"]:
            raise SystemExit("errors on the registered %s grid: %r / %r" % (g, b["errors"], n["errors"]))
        ch = fits_changed(b, n)
        cmpd, mv, mx = moved(b, n)
        if cmpd != pop:
            raise SystemExit("registered %s grid: compared %d of %d" % (g, cmpd, pop))
        nc[g] = {"grid_points": b["grid_points"], "seasons_unchanged": 22 - len(ch), "seasons_changed": ch,
                 "changed": {y: {"base": b["fits"][y], "repaired": n["fits"][y]} for y in ch},
                 "forecasts_compared": cmpd, "forecasts_moved": mv, "max_abs_delta_p": mx,
                 "brier_base": brier(b)[0], "brier_repaired": brier(n)[0]}
        out("   %-5s %7d points: constants unchanged in %d of 22 seasons%s; forecasts moved %d of %d (max |dp| %.3g); "
            "Brier %.6f -> %.6f" % (g, b["grid_points"], 22 - len(ch), "" if not ch else " CHANGED %s" % ch, mv, cmpd, mx,
                                    nc[g]["brier_base"], nc[g]["brier_repaired"]))
    d_model = nc["mov"]["brier_repaired"] - nc["mov"]["brier_base"]
    d_base = nc["mov"]["brier_base"] - nc["plain"]["brier_base"]
    d_new = nc["mov"]["brier_repaired"] - nc["plain"]["brier_repaired"]
    nc["dbrier"] = {"vs_home_and_vs_record_change": d_model, "vs_plain_elo_base": d_base,
                    "vs_plain_elo_repaired": d_new, "vs_plain_elo_change": d_new - d_base,
                    "threshold": THRESHOLD, "published_vs_plain_elo": -0.0042}
    worst = max(abs(d_model), abs(d_new - d_base))
    out("   dBrier against home and against better record moves by %+.6f (neither baseline reads grid_fit); "
        "against plain Elo %.4f -> %.4f, a move of %+.6f (published -0.0042); threshold %.4f"
        % (d_model, d_base, d_new, d_new - d_base, THRESHOLD))
    if abs(round(d_base, 4) - (-0.0042)) > 0.0002:
        out("   NOTE: the base-code MOV-minus-plain Brier is %.4f on today's store, not the published -0.0042" % d_base)
    nm = W["new_mov"]
    cs, whole = nm["choice_set"], nm["grid_points"] - nm["ever_bad"]
    returned = {y: cs[y] - whole for y in YEARS}
    nc["choice_sets"] = {"whole_walk": whole, "ever_bad": nm["ever_bad"], "base_ever_bad": W["base_mov"]["ever_bad"],
                         "as_of": {y: cs[y] for y in YEARS}, "returned_by_the_repair": returned,
                         "first_bad_by_season": nm["first_bad_by_season"]}
    out("   registered MOV grid: %d points ever bad (base code masks %d); points the repair returns to a season's "
        "choice set: min %d, max %d (f-32: 224 to 9,097); 2026: %d"
        % (nm["ever_bad"], W["base_mov"]["ever_bad"], min(returned.values()), max(returned.values()),
           returned["2026"]))
    und_reg = {g: sum(v["whole_walk"] for v in W["new_" + g]["undefined"].values()) for g in ("mov", "plain")}
    nc["undefined_games_under_chosen_points"] = und_reg
    out("   substituted games under any chosen point: MOV %d, plain %d" % (und_reg["mov"], und_reg["plain"]))
    with open(a.published, encoding="utf-8") as f:
        pub = json.load(f)["fits"]
    sec = {k: [y for y in YEARS if W["new_" + g]["fits"][y] != pub[y][k]] for g, k in (("mov", "mov"), ("plain", "nomov"))}
    nc["secondary_vs_committed_c39_result"] = sec
    out("   secondary - repaired fits against the committed c-39 result (an earlier store): MOV differs in %s, "
        "plain differs in %s" % (sec["mov"] or "no season", sec["nomov"] or "no season"))
    constants_same = not nc["mov"]["seasons_changed"] and not nc["plain"]["seasons_changed"]
    figure_same = worst <= THRESHOLD
    nc["constants_unchanged_22_of_22"] = constants_same
    nc["no_dbrier_beyond_threshold"] = figure_same
    nc["any_forecast_moved"] = bool(nc["mov"]["forecasts_moved"] or nc["plain"]["forecasts_moved"])
    R["no_change"] = nc

    if not passed:
        verdict = "not repaired"
    elif constants_same and figure_same:
        verdict = "repaired"
    else:
        verdict = "repaired with a moved figure"
    R["verdict"] = verdict
    out("\nVERDICT: %s" % verdict)
    with open(a.json_out, "w", encoding="utf-8") as f:
        json.dump(R, f, indent=1)
    with open(a.log_out, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
