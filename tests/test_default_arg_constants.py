"""A module constant in default-argument position is bound at IMPORT, not at call.

Run: pytest -q tests/test_default_arg_constants.py

So `def f(cap=other.CAP)` freezes the value: editing the constant changes what you
read and not what runs, and no test can lower it by setting the attribute. Found
2026-09-18 raising `FORWARD_WEEKLY_CAP` from 45 to 75; the sweep
(`research/default_arg_constants.py`) then answered how many siblings it had.

Two things are pinned here, and only the first is an allowlist:

  the CROSS-MODULE list, because that attribute can be reassigned or monkeypatched
  at runtime by code that never sees the frozen copy. A new one needs a reason here.

  the CATEGORICAL SPEND RULE, which covers BOTH classes. Same-module defaults (83 of
  them) bind identically; only the odds of noticing differ, so "they move in one diff"
  is not a defence - a constant at the top of a long file and a default far below it
  are in one diff only if someone edits both. A budget-shaped constant must not sit in
  a default argument in any module that CAN MAKE REQUESTS, and that capability is
  computed rather than listed. This is the part of the guard that survives the
  allowlist going stale.
"""
import os

import pytest

from research import default_arg_constants as sweep

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

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


def _cross_module():
    return [f for f in sweep.findings(REPO) if f[5] == "cross-module"]


def test_every_cross_module_default_is_on_the_list_with_a_reason():
    found = {(path, fn, arg) for path, _line, fn, arg, _src, _k in _cross_module()}
    new = found - set(ALLOWED)
    assert not new, (
        f"{len(new)} constant(s) newly frozen into a default argument: {sorted(new)}. "
        f"Use `arg=None` and read the constant inside the function, or add it here with a reason.")


def test_no_budget_constant_is_frozen_into_a_default_anywhere():
    """The categorical rule, over all 97 findings, not only the cross-module 14."""
    offenders = [f for f, _mod in sweep.spend_shaped(REPO)]
    assert offenders == [], (
        f"a spend or request limit is bound at import inside a module that can make "
        f"requests: {offenders}. Use `arg=None` and read the constant inside the function.")


def test_the_categorical_rule_fires_on_a_planted_spender(tmp_path):
    """A rule never seen to fail is not a rule. Same budget-shaped default in two
    modules: one that can make requests, one that cannot."""
    (tmp_path / "limits.py").write_text("CREDIT_CAP = 10\n", encoding="utf-8")
    (tmp_path / "spender.py").write_text(
        "import httpx\nimport limits\n\n\ndef go(n=limits.CREDIT_CAP):\n    return n\n",
        encoding="utf-8")
    (tmp_path / "reader.py").write_text(
        "import sqlite3\n\nGRID_CAP = 150\n\n\ndef grid(cap=GRID_CAP):\n    return cap\n",
        encoding="utf-8")
    hits = {f[0] for f, _m in sweep.spend_shaped(str(tmp_path))}
    assert "spender.py" in hits, "a budget default in a module that spends must be caught"
    assert "reader.py" not in hits, "a module that cannot make requests holds no spend limit"


def test_a_module_that_imports_a_spender_counts_as_able_to_spend(tmp_path):
    (tmp_path / "client.py").write_text("import httpx\n\nCALL_CAP = 1\n", encoding="utf-8")
    (tmp_path / "job.py").write_text(
        "import client\n\n\ndef run(cap=client.CALL_CAP):\n    return cap\n", encoding="utf-8")
    assert "job.py" in {f[0] for f, _m in sweep.spend_shaped(str(tmp_path))}


def test_the_sweep_itself_finds_a_planted_example(tmp_path):
    """A sweep that has never been seen to fire is not a sweep."""
    (tmp_path / "planted.py").write_text(
        "import other\nLOCAL_CAP = 1\n\n\ndef f(a=other.REMOTE_CAP, b=LOCAL_CAP, c=3):\n"
        "    return a, b, c\n", encoding="utf-8")
    found = sweep.findings(str(tmp_path))
    kinds = {(f[3], f[5]) for f in found}
    assert ("a", "cross-module") in kinds and ("b", "same-module") in kinds
    assert not any(f[3] == "c" for f in found)          # a literal default is not a finding


@pytest.mark.parametrize("name,expected", [("FORWARD_WEEKLY_CAP", 75), ("P1_APPROVED_CREDITS", 74)])
def test_the_shipped_caps_are_the_approved_numbers(name, expected):
    from cfb import oddsapi
    assert getattr(oddsapi, name) == expected


def test_plan_forward_follows_the_constant_when_it_moves(monkeypatch):
    """The regression the sweep exists for: a frozen default would keep planning against
    the old cap while the constant read as the new one."""
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
