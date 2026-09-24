"""lab.strategy/1: the one object every backtest is.

The chat produces it, the builder edits it, the engine runs it and a share link
encodes it (AUDIT-2026-09-24 section 6.3). Two layers of checking, on purpose:

1. `strategy.schema.json` - the SHAPE. Enumerations, types, closed objects.
   Versioned by `schema`; a /2 is a new file, never an edit to this one.

2. `check(strategy, catalogue)` - what THE DATA can support. A shape-valid rule
   can still ask for something the archive cannot answer: a 2026-only timing on
   history, a model side when no walk-forward predictions are stored, a
   condition whose feature does not exist for the chosen seasons. Those are
   REFUSED, with every reason at once, and nothing runs. A partial run on the
   supportable half of a rule would report a result for a rule nobody wrote.

`check` returns the statement it approved (`Approved.strategy`, with defaults
filled, and `Approved.statement`, one line) and refuses truth-testing - the
standing rule for guards in this repo, because `if check(s):` would discard
exactly what the check learned.
"""
import copy
import functools
import hashlib
import json
import os
import re

SCHEMA_ID = "lab.strategy/1"
RESULT_ID = "lab.result/1"
SCHEMA_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "strategy.schema.json")

# The three-book benchmark (research/calibration.BENCH_BOOKS). Imported by
# value rather than by module: research/calibration opens the store at import
# of nothing, but it is a research script and the engine must not depend on
# one. `tests/test_lab_engine.py` asserts the two stay equal.
BENCH_BOOKS = ("draftkings", "fanduel", "betmgm")

PROP_MARKETS = ("receptions", "receiving_yards", "rush_attempts",
                "tackles_assists", "sacks")
GAME_MARKETS = {"spread": ("spread",), "total": ("total",),
                "moneyline": ("moneyline",)}

SIDES = {
    "prop": ("over", "under", "model_lean", "against_model"),
    "total": ("over", "under"),
    "spread": ("favourite", "underdog", "home", "away"),
    "moneyline": ("favourite", "underdog", "home", "away"),
}

DEFAULTS = {
    "sport": "nfl",
    "conditions": [],
    "line_choice": "main",
    "price": {"source": "consensus_close", "books": list(BENCH_BOOKS),
              "devig": "multiplicative", "use_posted_juice": True,
              "assume_juice_if_missing": -110},
    "timing": "close",
    "staking": {"method": "flat", "unit": 1},
    "limits": {"per_game": None, "per_player_week": 1, "per_week": None},
    "settlement": {"push": "refund", "no_snap": "void"},
    "evaluation": {"bootstrap": "by_week", "resamples": 2000, "null_draws": 1000},
}

# Budget guards from section 6.8. The schema caps them too; this is the value a
# missing field gets, and the cap the engine enforces whatever it is handed.
MAX_RESAMPLES = 2000
MAX_NULL_DRAWS = 1000

_NEAREST = re.compile(r"^nearest_to\((-?[0-9]+(?:\.[0-9]+)?)\)$")


class Invalid(ValueError):
    """The object is not a lab.strategy/1 (shape)."""


class Unsupported(ValueError):
    """A valid rule the data cannot answer. `.reasons` lists every one."""

    def __init__(self, reasons):
        self.reasons = list(reasons)
        super().__init__("; ".join(self.reasons))


class Approved:
    """What `check` allowed. Refuses truth-testing - read `.strategy`."""
    __slots__ = ("strategy", "statement", "notes")

    def __init__(self, strategy, statement, notes):
        self.strategy, self.statement, self.notes = strategy, statement, notes

    def __bool__(self):
        raise TypeError("Approved is not a boolean: check() raises when it "
                        "refuses. Read .strategy and .statement.")


@functools.lru_cache(maxsize=1)
def load_schema():
    with open(SCHEMA_PATH, encoding="utf-8") as f:
        return json.load(f)


def validate_shape(strategy):
    import jsonschema
    try:
        jsonschema.validate(strategy, load_schema())
    except jsonschema.ValidationError as e:
        path = "/".join(str(p) for p in e.absolute_path) or "(root)"
        raise Invalid("%s: %s" % (path, e.message)) from None


def with_defaults(strategy, latest_complete_season=None):
    """A deep copy with every optional field filled. Nested objects merge key by
    key, so `{"price": {"source": "book", "books": ["fanduel"]}}` keeps the
    default juice rules rather than dropping them."""
    s = copy.deepcopy(strategy)
    for k, v in DEFAULTS.items():
        if k not in s:
            s[k] = copy.deepcopy(v)
        elif isinstance(v, dict):
            merged = copy.deepcopy(v)
            merged.update(s[k])
            s[k] = merged
    s["seasons"].setdefault("season_type", "REG")
    s["seasons"].setdefault("weeks", {"from": 1, "to": 22})
    if "holdout" not in s:
        # The latest complete season is held out by DEFAULT (6.5). A caller
        # that does not know it gets the rule's own last season, which is the
        # conservative reading: hold out more rather than less.
        season = latest_complete_season or s["seasons"]["to"]
        s["holdout"] = {"season": season, "revealed": False, "revealed_at": None}
    s["holdout"].setdefault("revealed_at", None)
    s["evaluation"]["resamples"] = min(int(s["evaluation"]["resamples"]), MAX_RESAMPLES)
    s["evaluation"]["null_draws"] = min(int(s["evaluation"]["null_draws"]), MAX_NULL_DRAWS)
    return s


def nearest_target(line_choice):
    """The x of nearest_to(x), or None for main / all_rungs."""
    if isinstance(line_choice, dict):
        return float(line_choice["nearest_to"])
    m = _NEAREST.match(line_choice)
    return float(m.group(1)) if m else None


def canonical(strategy):
    """The bytes a result is cached and seeded by. `name` and `revealed_at` do
    not change a result, so they do not change the hash; `revealed` does."""
    s = copy.deepcopy(strategy)
    s.pop("name", None)
    if "holdout" in s:
        s["holdout"].pop("revealed_at", None)
    return json.dumps(s, sort_keys=True, separators=(",", ":"))


def strategy_hash(strategy):
    return hashlib.sha256(canonical(strategy).encode("utf-8")).hexdigest()


def _condition_problems(cond, feature, s):
    """Why one condition cannot run, as a list (empty when it can)."""
    out = []
    key, op, val = cond["feature"], cond["op"], cond["value"]
    if feature is None:
        return ["condition on %r: no such feature in the catalogue" % key]
    if s["bet_type"] not in feature["bet_types"]:
        out.append("condition on %r: not defined for bet_type %r (it applies to %s)"
                   % (key, s["bet_type"], ", ".join(feature["bet_types"])))
    if feature.get("availability") == "none":
        out.append("condition on %r: unavailable - %s" % (key, feature.get("note", "")))
        return out
    want_lo, want_hi = s["seasons"]["from"], s["seasons"]["to"]
    per = feature.get("by_market")
    if per:
        # A stat-derived feature is ranged per market: every market the rule
        # bets must support it, and the refusal names the one that does not.
        for m in s["markets"]:
            v = per.get(m)
            if v is None or v.get("availability") == "none":
                out.append("condition on %r: unavailable for %s - %s"
                           % (key, m, (v or {}).get("note", "not ranged")))
            elif want_lo < v["season_from"] or want_hi > v["season_to"]:
                out.append("condition on %r: %s supported %d-%d only (%s); the "
                           "rule asks for %d-%d" % (key, m, v["season_from"],
                                                    v["season_to"], v["note"],
                                                    want_lo, want_hi))
        lo = hi = None
    else:
        lo, hi = feature.get("season_from"), feature.get("season_to")
    if per:
        pass
    elif lo is None or hi is None:
        out.append("condition on %r: its season range was never derived (%s)"
                   % (key, feature.get("note", "no note")))
    elif want_lo < lo or want_hi > hi:
        out.append("condition on %r: supported %d-%d only (%s); the rule asks "
                   "for %d-%d" % (key, lo, hi, feature.get("note", ""),
                                  want_lo, want_hi))
    if op in ("in", "not_in") and not isinstance(val, list):
        out.append("condition on %r: %r takes a list" % (key, op))
    if op == "between" and not (isinstance(val, list) and len(val) == 2):
        out.append("condition on %r: 'between' takes [low, high]" % key)
    if feature["dtype"] == "number" and op not in ("in", "not_in", "between", "==", "!="):
        if not isinstance(val, (int, float)) or isinstance(val, bool):
            out.append("condition on %r: %r compares a number" % (key, op))
    if feature["dtype"] in ("text", "bool") and op in ("<", "<=", ">", ">=", "between"):
        out.append("condition on %r: %r is not an ordering on a %s feature"
                   % (key, op, feature["dtype"]))
    return out


def check(strategy, catalogue, universe_seasons=None, latest_complete_season=None):
    """Shape, then support. -> Approved, or raises Invalid / Unsupported.

    `catalogue` is {feature_key: feature_dict} with derived ranges, as
    `lab.catalogue.ranges()` returns and the universe meta carries.
    `universe_seasons` is {bet_type: (first, last)} - what the prices cover.
    """
    validate_shape(strategy)
    s = with_defaults(strategy, latest_complete_season)
    bt = s["bet_type"]
    reasons, notes = [], []

    if s["seasons"]["from"] > s["seasons"]["to"]:
        reasons.append("seasons.from %d is after seasons.to %d"
                       % (s["seasons"]["from"], s["seasons"]["to"]))
    w = s["seasons"]["weeks"]
    if w["from"] > w["to"]:
        reasons.append("weeks.from %d is after weeks.to %d" % (w["from"], w["to"]))

    allowed = PROP_MARKETS if bt == "prop" else GAME_MARKETS[bt]
    bad = [m for m in s["markets"] if m not in allowed]
    if bad:
        reasons.append("markets %s are not %s markets (allowed: %s)"
                       % (bad, bt, ", ".join(allowed)))

    if s["side"] not in SIDES[bt]:
        reasons.append("side %r is not a %s side (allowed: %s)"
                       % (s["side"], bt, ", ".join(SIDES[bt])))
    if s["side"] in ("model_lean", "against_model"):
        # Prerequisite 5: walk-forward ONLY. research/walkforward.py computes
        # its predictions in memory and persists none, and the stored
        # `predictions` table is the 2026 live model, which is in-sample on
        # every historical season. So there is no walk-forward probability to
        # condition on, and refusing is the only honest answer.
        reasons.append("side %r needs walk-forward model predictions, and none "
                       "are persisted for the historical seasons (the stored "
                       "predictions are the live model, in-sample on history)"
                       % s["side"])

    if s["timing"] != "close":
        reasons.append("timing %r is 2026 forward capture only; the history "
                       "holds one close per claim, so there is no earlier "
                       "price to bet at" % s["timing"])
    if s["price"]["source"] == "kalshi_mid":
        reasons.append("price.source 'kalshi_mid' has no settled history in "
                       "the universe - Kalshi prices exist from 2026 only")
    if s["price"]["source"] == "book" and len(s["price"]["books"]) != 1:
        reasons.append("price.source 'book' takes exactly one book, got %d"
                       % len(s["price"]["books"]))

    if bt == "prop" and s["seasons"]["season_type"] == "POST":
        # Measured by a-29 (2026-09-24): settle_outcomes reads REG stat rows
        # only, so since the settlement fix every postseason over settles as a
        # loss at 0 - 2,941 rows, all `under`. A postseason prop backtest would
        # report that defect as a finding.
        reasons.append("postseason props are not settled correctly (every "
                       "postseason over settles under at 0; a-29) - REG only")

    if universe_seasons is not None:
        cov = universe_seasons.get(bt)
        if not cov:
            reasons.append("the universe carries no %s rows at all" % bt)
        elif s["seasons"]["to"] < cov[0] or s["seasons"]["from"] > cov[1]:
            reasons.append("%s prices cover %d-%d; the rule asks for %d-%d"
                           % (bt, cov[0], cov[1], s["seasons"]["from"],
                              s["seasons"]["to"]))
        elif s["seasons"]["from"] < cov[0] or s["seasons"]["to"] > cov[1]:
            notes.append("%s prices cover %d-%d; seasons outside that return "
                         "no bets" % (bt, cov[0], cov[1]))

    for cond in s["conditions"]:
        reasons.extend(_condition_problems(cond, catalogue.get(cond["feature"]), s))

    st = s["staking"]
    if st["method"] == "flat":
        st.setdefault("unit", 1)
    elif st["method"] == "pct_bankroll":
        if "pct" not in st or "bankroll" not in st:
            reasons.append("staking pct_bankroll needs pct and bankroll")
    elif st["method"] == "kelly_fraction":
        st.setdefault("fraction", 0.25)
        st.setdefault("unit", 1)
        if s["side"] != "model_lean":
            notes.append("Kelly stakes zero here: it needs a probability that "
                         "differs from the price, and only side model_lean "
                         "supplies one")

    if reasons:
        raise Unsupported(reasons)
    statement = ("%s %s on %s, %d-%d %s weeks %d-%d, %d condition(s), %s at %s"
                 % (bt, s["side"], "/".join(s["markets"]), s["seasons"]["from"],
                    s["seasons"]["to"], s["seasons"]["season_type"], w["from"],
                    w["to"], len(s["conditions"]), s["price"]["source"],
                    s["timing"]))
    return Approved(s, statement, notes)
