"""a-37 (audit N-21): every analytics file states its interval's coverage level, in words.

Run: pytest -q tests/test_analytics_methods.py

What is pinned:

  * THE LEVEL IS STATED OR THE EXPORT REFUSES. A tag with no rule raises; nothing is
    guessed.
  * WHAT WAS RESAMPLED COMES FROM THE METRIC'S `block`. "Interval from resampling
    games" is false for the player-blocked stability metrics, so a player block must
    produce the other word.
  * THE IMPLICIT 0.95 IS CHECKED, NOT ASSUMED. `hist2000`, `block2000` and `bayes2000t`
    do not carry their level in the tag; they are 0.95 only because the constructors
    default to it and no caller overrides it. The AST guard below is what makes that a
    claim, and it is shown catching a planted override.
  * THE CONTRACT REFUSES A FILE WITHOUT THE BLOCK, and a level written as 95.
"""
import ast
import glob
import os

import pytest
from jsonschema import Draft202012Validator

from analytics import export as X
from analytics import schedule

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# Constructors whose tag does not carry the level. Their `conf` must stay at 0.95.
IMPLICIT = ("histogram_bootstrap", "block_bootstrap", "share_bootstrap")
# The 0-based position of `conf` in each signature, for a positional override.
CONF_POSITION = {"histogram_bootstrap": 3, "block_bootstrap": 3, "share_bootstrap": 2}


# ------------------------------------------------------------- describe_method

@pytest.mark.parametrize("code,block,level,words", [
    ("hist2000", "game", 0.95, "resampling games"),
    ("hist2000", "player", 0.95, "resampling players"),
    ("block2000", "game", 0.95, "resampling games"),
    ("bayes2000", "game", 0.95, "reweighting games"),
    ("bayes2000t", "game", 0.95, "widened"),
    ("cluster_t95", "game", 0.95, "between games"),
    ("cluster_t90", "team", 0.90, "between teams"),
    ("wilson95", "game", 0.95, "proportion"),
    ("t99", "game", 0.99, "independent games"),
])
def test_every_known_tag_states_a_level_and_names_what_was_resampled(code, block, level, words):
    m = X.describe_method(code, block)
    assert m["code"] == code and m["coverage_level"] == level
    assert words in m["interval"]
    assert "hist2000" not in m["interval"] and code not in m["interval"]


def test_the_block_decides_the_word_so_players_are_never_called_games():
    assert "games" in X.describe_method("hist2000", "game")["interval"]
    assert "games" not in X.describe_method("hist2000", "player")["interval"]


@pytest.mark.parametrize("code", ["classblock2000", "hist", "bootstrap", "", None])
def test_an_unknown_tag_refuses_rather_than_guessing_a_level(code):
    with pytest.raises(X.MethodError):
        X.describe_method(code, "game")


def test_methods_block_is_one_entry_per_tag_used_sorted():
    values = [{"method": "hist2000"}, {"method": "bayes2000t"}, {"method": "hist2000"}]
    got = X.methods_block(values, "game")
    assert [m["code"] for m in got] == ["bayes2000t", "hist2000"]
    assert X.methods_block([], "game") == []


# ------------------------------------------------ the implicit 0.95, by AST

def conf_overrides(source, filename="<src>"):
    """(file, line, callee) for every call to an IMPLICIT constructor passing `conf`,
    outside that constructor's own module forwarding its own parameter."""
    hits = []
    for node in ast.walk(ast.parse(source, filename)):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        name = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", None)
        if name not in IMPLICIT:
            continue
        for kw in node.keywords:
            forwarded = isinstance(kw.value, ast.Name) and kw.value.id == "conf"
            if kw.arg == "conf" and not (filename.endswith("intervals.py") and forwarded):
                hits.append((filename, node.lineno, name))
        if len(node.args) > CONF_POSITION[name]:
            hits.append((filename, node.lineno, name))       # conf passed positionally
    return hits


def test_no_caller_overrides_the_level_of_a_tag_that_does_not_carry_it():
    files = glob.glob(os.path.join(ROOT, "analytics", "*.py"))
    assert len(files) > 10, "scanned too few files to mean anything"
    calls, hits = 0, []
    for path in files:
        src = open(path, encoding="utf-8").read()
        calls += sum(1 for n in ast.walk(ast.parse(src)) if isinstance(n, ast.Call)
                     and (getattr(n.func, "attr", None) or getattr(n.func, "id", None)) in IMPLICIT)
        hits += conf_overrides(src, path)
    assert calls >= 8, "found %d constructor calls; the scan is not seeing them" % calls
    assert hits == []


def test_the_guard_catches_a_planted_override():
    planted = ("from analytics.intervals import histogram_bootstrap\n"
               "got = histogram_bootstrap(by_game, stats, draws=2000, conf=0.90)\n")
    assert conf_overrides(planted, "analytics/planted.py") == [
        ("analytics/planted.py", 2, "histogram_bootstrap")]
    positional = "got = share_bootstrap(by_game, 2000, 0.90)\n"
    assert conf_overrides(positional, "analytics/planted.py") == [
        ("analytics/planted.py", 1, "share_bootstrap")]


def test_the_defaults_and_the_schedule_level_are_the_stated_level():
    import inspect
    from analytics import intervals
    for name in IMPLICIT:
        params = inspect.signature(getattr(intervals, name)).parameters
        assert list(params).index("conf") == CONF_POSITION[name]
        assert params["conf"].default == X.IMPLICIT_LEVEL
    assert schedule.CONF == X.IMPLICIT_LEVEL        # bayes2000 / bayes2000t


# ---------------------------------------------------------------- the contract

def validator(name):
    c = X.contract()
    return Draft202012Validator({"$ref": "#/$defs/%s" % name, "$defs": c["$defs"]})


def a_file():
    values = [{"subject": "00-0036355", "slice": "", "estimate": 0.2, "interval": [0.1, 0.3],
               "n": 12, "rows": 80, "method": "hist2000"}]
    return {"schema_version": 2, "generated_at": "2026-09-26T00:00:00Z",
            "kind": "analytics.metric", "sport": "nfl", "metric": "m", "label": "M",
            "unit": "u", "subject_type": "player", "block": "game", "basis": "pbp",
            "slice_kind": "", "shares_denominator": None, "season_from": 2016,
            "season_to": 2026, "range_note": "r", "availability": "current", "requires": [],
            "methods": X.methods_block(values, "game"), "values": values}


def test_the_contract_requires_the_methods_block_and_a_proportion():
    v = validator("AnalyticMetricFile")
    good = a_file()
    assert not list(v.iter_errors(good))
    missing = dict(good)
    del missing["methods"]
    assert list(v.iter_errors(missing))
    as_percent = a_file()
    as_percent["methods"][0]["coverage_level"] = 95
    assert list(v.iter_errors(as_percent))
