"""One number per metric, enforced (unit a-36, audit N-02).

    python -m jobs.metric_registry --dest <WEB_EXPORT_DIR>    # check served files on disk

THE BUG THIS EXISTS FOR. On 2026-09-26 the site printed its headline finding two
ways one click apart: the register (R18) said -2.43pp on 44,198 after the
settlement fix, while `/` and `/nfl/analytics` still said -1.40pp on 43,209 from a
hand-copied 2026-09-10 file. Method printed two calibration errors for one set -
one cited from DECISIONS.md, one computed. Every one of those numbers was
correct about SOMETHING; what failed is that nothing tied a figure to one place.

THE REGISTRY. `METRICS` maps a metric id to the ONE served file and JSON path
that owns it (`source`), plus every OTHER served location known to carry the same
number (`copies`). The sport manifest exports it (`metrics`), so the site renders
a registered figure by reading the owner rather than by typing it.

THE GATE. `check(files)` resolves every registered location and refuses when a
copy disagrees with its owner at the metric's published precision. Two escapes,
both loud:
  * a copy may carry `declared` - a written reason it is KNOWN to disagree. A
    declared copy that AGREES also fails: the declaration is stale, and a stale
    declaration is how a real disagreement later hides behind an old excuse.
  * nothing else. A location that does not resolve is a failure, never a skip -
    a gate that cannot find a value has checked nothing (CLAUDE.md, "a guard
    asserts only over the shapes it walks").

PATH GRAMMAR (the site implements the same, b-47). Dot-separated keys; `[N]` is
an array index; `[key=value]` selects the ONE array element whose `key` equals
`value` (compared as a string), and zero or two matches is an error. Examples:
`over_bias.interval_pp`, `hypotheses[id=R18].estimate`,
`by_stat[stat=sacks].estimate_pp`.

The result is a `GateReport`, which REFUSES truth-testing (CLAUDE.md, "a guard
returns the statement it approved"): read `.clean` and `.statement`.
"""
import argparse
import json
import os
import re
import sys

MARKET = "research/market_calibration.json"
SCORE = "research/calibration.json"
REGISTER = "research/hypotheses.json"

# R10 was restated 2026-09-17 on the n=706 common set, when 229 week-1 Kalshi
# books were one-sided at the prediction instant. The Kalshi candle backfill
# (quotes.source 'backfill:kalshi_candles', ingested after 09-17) now supplies a
# two-sided quote at or before entry for all 935, so score.py's common set is 935
# and its figures moved. Which quote is the right entry price is a methodology
# decision nobody has taken; until it is, the two are published as what they are
# and this reason travels with them. Measured by a-36 on 2026-09-26.
R10_DECLARED = ("R10 is the 2026-09-17 restatement on n=706 (229 books one-sided at entry); "
                "research/calibration.json now scores n=935 because the Kalshi candle backfill "
                "supplies a two-sided entry quote for every prediction. Unresolved: which entry "
                "quote is correct (a-36).")


def _m(id, label, unit, decimals, source, copies=()):
    return {"id": id, "label": label, "unit": unit, "decimals": decimals,
            "source": {"file": source[0], "path": source[1]},
            "copies": [dict(file=c[0], path=c[1], **({"declared": c[2]} if len(c) > 2 else {}))
                       for c in copies]}


STATS = ("receiving_yards", "receptions", "tackles_assists", "rush_attempts", "sacks")
SEASONS = (2023, 2024, 2025)

METRICS = [
    # --- the closing sportsbook market, 2023-2025 (R18) --------------------
    _m("market.over_bias.estimate_pp", "Over-side pricing gap at the close", "pp", 2,
       (MARKET, "over_bias.estimate_pp"), [(REGISTER, "hypotheses[id=R18].estimate")]),
    _m("market.over_bias.interval_pp", "Over-side pricing gap, game-block 95% interval", "pp", 2,
       (MARKET, "over_bias.interval_pp"), [(REGISTER, "hypotheses[id=R18].interval")]),
    _m("market.over_bias.n", "Settled over-side props", "outcomes", 0,
       (MARKET, "population.n"), [(REGISTER, "hypotheses[id=R18].n")]),
    _m("market.over_bias.games", "Games", "games", 0,
       (MARKET, "population.games"), [(REGISTER, "hypotheses[id=R18].games")]),
    _m("market.over_bias.priced", "Priced over rate at the close", "probability", 4,
       (MARKET, "over_bias.priced")),
    _m("market.over_bias.realized", "Realized over rate", "probability", 4,
       (MARKET, "over_bias.realized")),
    _m("market.over_bias.boot_se_pp", "Game-block bootstrap standard error", "pp", 2,
       (MARKET, "over_bias.boot_se_pp")),
    _m("market.over_bias.z_null", "z under the null-variance se (outcomes treated as "
       "independent; prefer the interval)", "z", 1, (MARKET, "over_bias.z_null")),
    _m("market.close.ece", "Calibration error of the close, over side", "ECE", 4,
       (MARKET, "score.ece")),
    _m("market.close.brier", "Brier score of the close, over side", "Brier", 4,
       (MARKET, "score.brier")),
    _m("market.close.log_loss", "Log loss of the close, over side", "log loss", 4,
       (MARKET, "score.log_loss")),
    *[_m(f"market.over_bias.by_stat.{s}.estimate_pp", f"Over-side pricing gap, {s}", "pp", 2,
         (MARKET, f"by_stat[stat={s}].estimate_pp")) for s in STATS],
    *[_m(f"market.over_bias.by_season.{y}.estimate_pp", f"Over-side pricing gap, {y}", "pp", 2,
         (MARKET, f"by_season[season={y}].estimate_pp")) for y in SEASONS],
    # --- the model against Kalshi, 2026 week 1 (R10) ------------------------
    _m("model.brier_minus_market", "Brier(model) - Brier(Kalshi mid), week 1", "Brier", 4,
       (SCORE, "brier.model_minus_market.estimate"),
       [(REGISTER, "hypotheses[id=R10].estimate", R10_DECLARED)]),
    _m("model.brier_minus_market.interval", "Brier(model) - Brier(Kalshi mid), 95% interval",
       "Brier", 4, (SCORE, "brier.model_minus_market.interval"),
       [(REGISTER, "hypotheses[id=R10].interval", R10_DECLARED)]),
    _m("model.brier_minus_market.n", "Predictions scored on the common set", "predictions", 0,
       (SCORE, "n"), [(REGISTER, "hypotheses[id=R10].n", R10_DECLARED)]),
    _m("model.brier_minus_market.games", "Games in the common set", "games", 0,
       (SCORE, "games"), [(REGISTER, "hypotheses[id=R10].games")]),
    _m("model.brier_minus_naive", "Brier(model) - Brier(naive prior), week 1", "Brier", 4,
       (SCORE, "brier.model_minus_naive.estimate")),
    _m("model.brier_minus_naive.interval", "Brier(model) - Brier(naive prior), 95% interval",
       "Brier", 4, (SCORE, "brier.model_minus_naive.interval")),
    _m("model.brier", "Brier score, model, week 1", "Brier", 4, (SCORE, "brier.model")),
    _m("kalshi.brier", "Brier score, Kalshi mid, week 1", "Brier", 4, (SCORE, "brier.market")),
    _m("naive.brier", "Brier score, naive prior, week 1", "Brier", 4, (SCORE, "brier.naive")),
    _m("model.ece", "Calibration error, model, week 1", "ECE", 4,
       (SCORE, "ece.model"), [(SCORE, "series[name=model].ece")]),
    _m("kalshi.ece", "Calibration error, Kalshi mid, week 1", "ECE", 4,
       (SCORE, "ece.market"), [(SCORE, "series[name=market].ece")]),
    # --- walk-forward against the sportsbook close (R15) --------------------
    # Owned by the register until research/walkforward.py publishes a file; only
    # 2025 is a field today (2023 and 2024 live in R15's prose).
    _m("model.walkforward.brier_minus_close.2025",
       "Walk-forward Brier(model) - Brier(close), 2025", "Brier", 4,
       (REGISTER, "hypotheses[id=R15].estimate")),
    _m("model.walkforward.brier_minus_close.2025.interval",
       "Walk-forward Brier(model) - Brier(close), 2025, 95% interval", "Brier", 4,
       (REGISTER, "hypotheses[id=R15].interval")),
    _m("model.walkforward.brier_minus_close.2025.n", "Walk-forward outcomes, 2025", "outcomes", 0,
       (REGISTER, "hypotheses[id=R15].n")),
]

FILES = sorted({m["source"]["file"] for m in METRICS}
               | {c["file"] for m in METRICS for c in m["copies"]})


class Unresolved(LookupError):
    pass


_TOKEN = re.compile(r"([^.\[\]]+)|\[(\d+)\]|\[([^=\]]+)=([^\]]*)\]")


def parse(path):
    toks, pos = [], 0
    while pos < len(path):
        if path[pos] == ".":
            pos += 1
            continue
        m = _TOKEN.match(path, pos)
        if not m:
            raise ValueError(f"bad metric path {path!r} at {pos}")
        if m.group(1) is not None:
            toks.append(("key", m.group(1)))
        elif m.group(2) is not None:
            toks.append(("index", int(m.group(2))))
        else:
            toks.append(("select", m.group(3), m.group(4)))
        pos = m.end()
    return toks


def resolve(obj, path):
    cur = obj
    for t in parse(path):
        if t[0] == "key":
            if not isinstance(cur, dict) or t[1] not in cur:
                raise Unresolved(f"{path}: no key {t[1]!r}")
            cur = cur[t[1]]
        elif t[0] == "index":
            if not isinstance(cur, list) or t[1] >= len(cur):
                raise Unresolved(f"{path}: no index {t[1]}")
            cur = cur[t[1]]
        else:
            if not isinstance(cur, list):
                raise Unresolved(f"{path}: [{t[1]}={t[2]}] on a non-array")
            hits = [e for e in cur if isinstance(e, dict) and str(e.get(t[1])) == t[2]]
            if len(hits) != 1:
                raise Unresolved(f"{path}: [{t[1]}={t[2]}] matched {len(hits)}, need exactly 1")
            cur = hits[0]
    return cur


def _norm(v, decimals):
    """Value at the published precision. A list compares elementwise."""
    if isinstance(v, list):
        return [_norm(x, decimals) for x in v]
    if isinstance(v, bool) or v is None:
        return v
    if isinstance(v, (int, float)):
        r = round(float(v), decimals)
        return 0.0 if r == 0 else r        # -0.0 and 0.0 are one number
    return v


class GateReport:
    """What the metric gate approved. Refuses truth-testing: read .clean / .statement."""

    def __init__(self, checked, problems, declared):
        self.checked, self.problems, self.declared = checked, problems, declared
        self.clean = not problems
        self.statement = (
            f"metric gate: {len(METRICS)} metrics, {checked} locations resolved, "
            f"{len(declared)} declared disagreement(s), "
            + ("0 undeclared disagreements" if self.clean
               else f"{len(problems)} PROBLEM(S):\n  " + "\n  ".join(problems)))

    def __bool__(self):
        raise TypeError("GateReport is not a boolean - read .clean and .statement")


def check(files, metrics=None):
    """`files` is {key: parsed object}. -> GateReport. Never raises on a
    disagreement; `require()` does."""
    metrics = METRICS if metrics is None else metrics
    problems, declared, checked = [], [], 0

    def get(file, path):
        if file not in files:
            raise Unresolved(f"{file} was not produced")
        return resolve(files[file], path)

    for m in metrics:
        d = m["decimals"]
        try:
            owner = _norm(get(m["source"]["file"], m["source"]["path"]), d)
            checked += 1
        except (Unresolved, ValueError) as e:
            problems.append(f"{m['id']}: owner {m['source']['file']}:{e}")
            continue
        if owner is None:
            problems.append(f"{m['id']}: owner {m['source']['file']}:{m['source']['path']} is null")
        for c in m["copies"]:
            where = f"{c['file']}:{c['path']}"
            try:
                copy = _norm(get(c["file"], c["path"]), d)
                checked += 1
            except (Unresolved, ValueError) as e:
                problems.append(f"{m['id']}: copy {c['file']}:{e}")
                continue
            if c.get("declared"):
                if copy == owner:
                    problems.append(f"{m['id']}: {where} is DECLARED to disagree but agrees "
                                    f"({copy!r}) - remove the stale declaration")
                else:
                    declared.append(f"{m['id']}: {where} {copy!r} vs owner {owner!r}")
            elif copy != owner:
                problems.append(f"{m['id']}: {where} = {copy!r} but "
                                f"{m['source']['file']}:{m['source']['path']} = {owner!r}")
    return GateReport(checked, problems, declared)


class MetricDisagreement(RuntimeError):
    pass


def require(files, metrics=None):
    """The export gate. -> the GateReport it approved, or raises."""
    rep = check(files, metrics)
    if not rep.clean:
        raise MetricDisagreement("refusing to export - " + rep.statement)
    return rep


def manifest_block():
    """What the sport manifest publishes. A copy of METRICS, so no caller can
    mutate the registry through the manifest it built."""
    return json.loads(json.dumps(METRICS))


def load_files(dest):
    out = {}
    for key in FILES:
        p = os.path.join(dest, *key.split("/"))
        if os.path.isfile(p):
            with open(p, encoding="utf-8") as f:
                out[key] = json.load(f)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dest", required=True, help="an export tree (WEB_EXPORT_DIR)")
    a = ap.parse_args(argv)
    rep = check(load_files(a.dest))
    print(rep.statement)
    for d in rep.declared:
        print("  declared: " + d)
    return 0 if rep.clean else 1


if __name__ == "__main__":
    sys.exit(main())
