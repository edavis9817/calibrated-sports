"""A module constant in default-argument position is bound at IMPORT, not at call.

Run: pytest -q tests/test_default_arg_constants.py

So `def f(cap=other.CAP)` freezes the value: editing the constant changes what you
read and not what runs, and no test can lower it by setting the attribute. Found
2026-09-18 raising `FORWARD_WEEKLY_CAP` from 45 to 75; the sweep
(`research/default_arg_constants.py`) then answered how many siblings it had.

This pins the CROSS-MODULE list, which is the dangerous half - the attribute can be
reassigned or monkeypatched at runtime by code that never sees the frozen copy. A new
one has to be added here with a reason, so it is acknowledged rather than discovered.
Same-module defaults (83 of them) are a smell, not a defect: the constant and the
default move in one diff.
"""
import pytest

from research import default_arg_constants as sweep

REPO = __import__("os").path.dirname(__import__("os").path.dirname(
    __import__("os").path.abspath(__file__)))

# (file, function, argument) -> why it is allowed to stay frozen at import.
ALLOWED = {
    ("core/outcomes.py", "player_prop", "side"): "Side.OVER is an enum member; it cannot drift",
    ("core/outcomes.py", "player_prop", "sport"): "Sport.NFL is an enum member; it cannot drift",
    ("research/maker.py", "_gap_bootstrap", "n"): "frozen research settings module (S00)",
    ("research/maker.py", "_gap_bootstrap", "seed"): "frozen research settings module (S00)",
    ("research/sweep/h2_imbalance.py", "load_games", "season"): "frozen sweep settings (S)",
    ("research/sweep/h2_imbalance.py", "load_games", "week"): "frozen sweep settings (S)",
    ("research/sweep/h3_lifecycle.py", "hist_boot_median", "n"): "frozen sweep settings (S)",
    ("research/sweep/h3_lifecycle.py", "hist_boot_median", "seed"): "frozen sweep settings (S)",
    ("research/sweep/scan.py", "_boot_groups", "n"): "frozen sweep settings (S)",
    ("research/sweep/scan.py", "_boot_groups", "seed"): "frozen sweep settings (S)",
    ("research/sweep/scan_cfb.py", "touch_ok", "contracts"): "frozen sweep settings (X)",
    ("research/sweep/scan_cfb.py", "touch_ok", "window"): "frozen sweep settings (X)",
}

# A constant that bounds SPEND or requests must never be frozen into a default: that is
# the class the forward cap belonged to, and both survivors were saved only by a second
# read inside the function.
BUDGET_WORDS = ("CAP", "CREDIT", "BUDGET", "RESERVE", "LIMIT", "MAX_REQUESTS", "QUOTA")


def _cross_module():
    return [f for f in sweep.findings(REPO) if f[5] == "cross-module"]


def test_every_cross_module_default_is_on_the_list_with_a_reason():
    found = {(path, fn, arg) for path, _line, fn, arg, _src, _k in _cross_module()}
    new = found - set(ALLOWED)
    assert not new, (
        f"{len(new)} constant(s) newly frozen into a default argument: {sorted(new)}. "
        f"Use `arg=None` and read the constant inside the function, or add it here with a reason.")


def test_no_budget_constant_is_frozen_into_a_default():
    frozen = [(path, fn, arg, src) for path, _l, fn, arg, src, _k in _cross_module()
              if any(w in src.upper() for w in BUDGET_WORDS)]
    assert frozen == [], f"a spend limit bound at import: {frozen}"


def test_the_sweep_itself_finds_a_planted_example(tmp_path):
    """A sweep that has never been seen to fire is not a sweep."""
    (tmp_path / "planted.py").write_text(
        "import other\nLOCAL_CAP = 1\n"
        "def f(a=other.REMOTE_CAP, b=LOCAL_CAP, c=3):\n    return a, b, c\n", encoding="utf-8")
    found = sweep.findings(str(tmp_path))
    kinds = {(f[3], f[5]) for f in found}
    assert ("a", "cross-module") in kinds and ("b", "same-module") in kinds
    assert not any(f[3] == "c" for f in found)          # a literal default is not a finding


@pytest.mark.parametrize("name,expected", [("FORWARD_WEEKLY_CAP", 75), ("P1_APPROVED_CREDITS", 74)])
def test_the_shipped_caps_are_the_approved_numbers(name, expected):
    from cfb import oddsapi
    assert getattr(oddsapi, name) == expected


def test_plan_forward_follows_the_constant_when_it_moves(monkeypatch):
    """The regression the sweep exists for: a frozen default would keep planning
    against the old cap while the constant read as the new one."""
    import sqlite3

    from cfb import oddsapi, oddsapi_capture, schema
    con = sqlite3.connect(":memory:")
    con.executescript(schema.ddl())
    events = [{"id": "a", "commence_time": "2030-01-02T00:00:00Z", "home_team": "H",
               "away_team": "A"},
              {"id": "b", "commence_time": "2030-01-02T02:00:00Z", "home_team": "H2",
               "away_team": "A2"}]
    now = oddsapi.iso_ts("2030-01-02T01:55:00Z")
    monkeypatch.setattr(oddsapi, "FORWARD_WEEKLY_CAP", 0)
    due, _missed, skipped = oddsapi_capture.plan_forward(con, events, now)
    assert due == [] and skipped, "a cap of 0 must buy nothing"
    monkeypatch.setattr(oddsapi, "FORWARD_WEEKLY_CAP", 75)
    due, _missed, _skipped = oddsapi_capture.plan_forward(con, events, now)
    assert len(due) == 1, "with headroom the same tick buys the hour"
