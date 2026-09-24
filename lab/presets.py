"""Library presets, and the register figures they must reproduce.

    python -m lab.presets --check --fixed DIR --prefix DIR

A preset is a lab.strategy/1 like any user rule; it goes through `lab.run`, the
same code a user runs. Each carries CHECKS: a register figure, the universe it
was measured on, and the tolerance "within rounding" means - half a unit in the
last digit the figure was published to. A check that fails is a finding and
stops the unit (a-27 brief); this script exits 1 on any failure.

TWO UNIVERSES, ON PURPOSE. The calibration register's by-season and by-market
figures (DECISIONS.md 2026-09-10, CLAUDE.md "the OVER is overpriced") were
measured BEFORE the 2026-09-17 settlement fix. Checking them against the fixed
store would fail for a reason that is not the engine's, and checking only the
restated R18 would leave eight published figures untested. So each figure is
checked on the store it was measured on:

    fixed    the live store (settlement fixed)       R18, restated by a-29
    prefix   data/repairs/market_log-presettlement   the 2026-09-10 figures

A match on `prefix` says the engine computes what the register computed. The
`fixed` value of the same slice is printed beside it as the CURRENT figure -
which is what a library card must show - and several differ materially.

MIDDLES ARE NOT A PRESET. See `NOT_EXPRESSIBLE`.
"""
import argparse
import copy
import json
import sys

from lab import run, universe

PROPS = ["receptions", "receiving_yards", "rush_attempts", "tackles_assists", "sacks"]
BAND = [{"feature": "price.p_devig", "op": "between", "value": [0.45, 0.55]}]

# Every over (or under) rung, all five markets, the three-book benchmark close,
# no per-player limit - the calibration population exactly. The holdout is
# REVEALED so `all_seasons` pools 2023-2025 as the register did; the in-sample
# figure a user sees by default still excludes 2025.
_BASE = {
    "schema": "lab.strategy/1", "sport": "nfl", "bet_type": "prop",
    "markets": PROPS,
    "seasons": {"from": 2023, "to": 2025, "season_type": "REG",
                "weeks": {"from": 1, "to": 18}},
    "holdout": {"season": 2025, "revealed": True},
    "side": "over", "line_choice": "all_rungs",
    "price": {"source": "consensus_close",
              "books": ["draftkings", "fanduel", "betmgm"],
              "devig": "multiplicative", "use_posted_juice": True,
              "assume_juice_if_missing": -110},
    "limits": {"per_game": None, "per_player_week": None, "per_week": None},
}


def _s(**kw):
    s = copy.deepcopy(_BASE)
    s.update(copy.deepcopy(kw))
    return s


def _season(y, **kw):
    return _s(seasons={"from": y, "to": y, "season_type": "REG",
                       "weeks": {"from": 1, "to": 18}}, **kw)


class Check:
    __slots__ = ("label", "strategy", "metric", "expected", "tol", "n", "on", "source")

    def __init__(self, label, strategy, metric, expected, tol, n, on, source):
        self.label, self.strategy, self.metric = label, strategy, metric
        self.expected, self.tol, self.n, self.on, self.source = expected, tol, n, on, source


PRESETS = [
    {"key": "every_over", "title": "Bet every over at the close",
     "strategy": _s(name="Bet every over at the close"),
     "checks": [
         Check("R18 over-side pricing gap", _s(), "pricing_gap_pp", -2.43, 0.005,
               44198, "fixed",
               "docs/hypotheses.json R18 (a-29, branch a-29-market-calibration-register)"),
         Check("pre-fix over-side pricing gap", _s(), "pricing_gap_pp", -1.40, 0.005,
               43209, "prefix", "CLAUDE.md 'The closing market is well calibrated'"),
     ]},
    {"key": "over_near_even", "title": "Every over priced 0.45-0.55",
     "strategy": _s(name="Every over priced 0.45-0.55", conditions=BAND),
     "checks": [Check("pre-fix band gap", _s(conditions=BAND), "pricing_gap_pp",
                      -1.26, 0.005, 30440, "prefix", "CLAUDE.md, the band figure")]
     + [Check("pre-fix band gap, %s" % m, _s(markets=[m], conditions=BAND),
              "pricing_gap_pp", v, 0.05, None, "prefix",
              "DECISIONS.md 2026-09-10, 'by stat, inside the band'")
        for m, v in (("sacks", -7.2), ("rush_attempts", -2.4),
                     ("tackles_assists", -2.1), ("receiving_yards", -0.8),
                     ("receptions", -0.8))]},
    {"key": "over_by_season", "title": "Every over, one season at a time",
     "strategy": _s(name="Every over, one season at a time"),
     "checks": [Check("pre-fix season %d gap" % y, _season(y), "pricing_gap_pp", v,
                      0.005, None, "prefix",
                      "CLAUDE.md / DECISIONS.md 2026-09-10, by season")
                for y, v in ((2023, 0.60), (2024, -1.67), (2025, -2.65))]},
    {"key": "fade_every_over", "title": "Fade every over (bet the under), near-even",
     "strategy": _s(name="Fade every over", side="under", conditions=BAND),
     "checks": [Check("pre-fix under-side band gap", _s(side="under", conditions=BAND),
                      "pricing_gap_pp", 1.26, 0.005, 30440, "prefix",
                      "CLAUDE.md '+0.0126/contract'")]},
]

NOT_EXPRESSIBLE = {
    "buy_the_middle": {
        "register": "R16 / brief 023 Part 2, research/bookvbook.py: spreads middle "
                    "mean EV -2.54pp empirical, -2.65pp realized, n=10,860 over 848 games "
                    "(data/repairs/bookvbook-2026-09-17.txt)",
        "why": "a middle is TWO legs at two books at two thresholds; lab.strategy/1 is "
               "a single-leg language (one side, one price source), and the register "
               "figure is taken over every fresh pre-kickoff snapshot of the raw "
               "archive, where the universe holds one close per claim. No strategy "
               "in this schema expresses it, so no run of this engine can reproduce "
               "it - the library card must cite the R16 script, or the schema must "
               "grow a `legs` field (a lab.strategy/2)."},
    "arbitrage_between_books": {
        "register": "R16: receptions arbitrage mean size +1.09pp",
        "why": "the same two-leg shape as the middle"},
}


def run_check(c, universes):
    u = universes[c.on]
    r = run(c.strategy, u)
    a = r["all_seasons"] if r["holdout"]["revealed"] else r["summary"]
    got = a[c.metric]
    n = a["cleared"] + a["missed"]
    ok = got is not None and abs(got - c.expected) <= c.tol + 1e-9
    if c.n is not None:
        ok = ok and n == c.n
    return {"label": c.label, "on": c.on, "expected": c.expected, "got": got,
            "tol": c.tol, "n_expected": c.n, "n": n, "ok": ok, "source": c.source,
            "publishable": r["publishable"]}


def check_all(universes, current=None):
    out = []
    for p in PRESETS:
        for c in p["checks"]:
            if c.on not in universes:
                out.append({"label": c.label, "on": c.on, "ok": None,
                            "skipped": "no %s universe given" % c.on})
                continue
            row = run_check(c, universes)
            if current is not None and c.on == "prefix":
                cur = run(c.strategy, current)
                a = cur["all_seasons"]
                row["current_on_fixed"] = a[c.metric]
                row["current_n"] = a["cleared"] + a["missed"]
            row["preset"] = p["key"]
            out.append(row)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--fixed", help="universe dir built from the live store")
    ap.add_argument("--prefix", help="universe dir built from the pre-fix store")
    ap.add_argument("--json")
    a = ap.parse_args(argv)
    if not a.check:
        ap.print_help()
        return 2
    universes = {}
    if a.fixed:
        universes["fixed"] = universe.load(a.fixed)
    if a.prefix:
        universes["prefix"] = universe.load(a.prefix)
    rows = check_all(universes, universes.get("fixed"))
    bad = 0
    for r in rows:
        if r.get("ok") is None:
            print("SKIP  %-34s %s" % (r["label"], r["skipped"]))
            continue
        bad += not r["ok"]
        cur = ("   current (fixed) %+.2f n=%d" % (r["current_on_fixed"], r["current_n"])
               if "current_on_fixed" in r else "")
        print("%s  %-34s [%s] register %+.2f  engine %+.4f  n %d%s%s"
              % ("OK  " if r["ok"] else "FAIL", r["label"], r["on"], r["expected"],
                 r["got"], r["n"],
                 "" if r["n_expected"] is None else " (register n %d)" % r["n_expected"],
                 cur))
    for k, v in NOT_EXPRESSIBLE.items():
        print("NOT EXPRESSIBLE  %s: %s" % (k, v["register"]))
    ran = [r for r in rows if r.get("ok") is not None]
    print("%d checks run, %d failed, %d skipped" % (len(ran), bad, len(rows) - len(ran)))
    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump({"checks": rows, "not_expressible": NOT_EXPRESSIBLE}, f, indent=1)
    if not ran:
        print("no check ran - that is a failure, not a pass")
        return 1
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
