"""F13's frozen grading rule. Run: pytest -q tests/test_f13_call_grading.py

Nothing here reads data - the rule was registered before any call existed. What is
tested is that (a) every verdict is reachable, so the record can come out against the
board; (b) the multiplicity asymmetry runs against the board; (c) the module and the
pre-registration document state the same constants; (d) the pinned weights are the
export's PPR today, so a silent preset change breaks this rather than the record.
"""
import os
import re

import pytest

from research import f13_call_grading as R

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOC = os.path.join(ROOT, "docs", "briefs", "f13-fantasy-calls-preregistration.md")
ENOUGH = {"sell_high": 40, "buy_low": 40}


def doc():
    with open(DOC, encoding="utf-8") as f:
        return f.read()


# ------------------------------------------------ every verdict is reachable

def test_supported_is_reachable():
    assert R.verdict((-0.10, 0.20), (-0.05, 0.15), ENOUGH)[0] == "supported"


def test_failed_is_reachable():
    assert R.verdict((0.20, 0.60), (0.30, 0.50), ENOUGH)[0] == "failed"


def test_inconclusive_is_reachable_by_width():
    assert R.verdict((0.00, 0.40), (0.05, 0.35), ENOUGH)[0] == "inconclusive"


def test_inconclusive_is_reachable_by_size_and_names_the_thin_band():
    v, why = R.verdict((-0.10, 0.20), (-0.05, 0.15), {"sell_high": 40, "buy_low": 12})
    assert v == "inconclusive" and "buy_low" in why and "sell_high" not in why


def test_the_same_intervals_flip_on_tau():
    """Discriminating: the verdict depends on the threshold, not only on the shape."""
    simul, unadj = (0.05, 0.20), (0.08, 0.17)
    assert R.verdict(simul, unadj, ENOUGH, tau=0.25)[0] == "supported"
    assert R.verdict(simul, unadj, ENOUGH, tau=0.04)[0] == "failed"


# ------------------------------------ the asymmetry runs against the board

def test_supported_uses_the_WIDE_interval_and_failed_the_NARROW_one():
    """Unadjusted says 'supported' (upper 0.24 < 0.25) but the simultaneous upper
    bound does not clear, so the board does NOT get supported. And the unadjusted
    lower bound alone is enough to fail it."""
    assert R.verdict((0.00, 0.27), (0.05, 0.24), ENOUGH)[0] == "inconclusive"
    assert R.verdict((0.22, 0.60), (0.26, 0.50), ENOUGH)[0] == "failed"


def test_swapped_intervals_raise_rather_than_grade():
    with pytest.raises(ValueError):
        R.verdict((0.05, 0.24), (0.00, 0.27), ENOUGH)


def test_simultaneous_levels():
    assert R.simultaneous_level("primary") == pytest.approx(0.975)
    assert R.simultaneous_level("secondary") == pytest.approx(0.9875)
    with pytest.raises(ValueError):
        R.simultaneous_level("everything")


def test_carried_fraction_refuses_a_zero_call():
    assert R.carried_fraction(4.0, 1.0) == 0.25
    with pytest.raises(ValueError):
        R.carried_fraction(0.0, 1.0)


# --------------------------------------- the module and the document agree

@pytest.mark.parametrize("needle", [
    "TAU = 0.25", "NULL_REPS = 1000", "2,000 draws", "UNGRADABLE_GAP_FLAG",
    "fewer than 30 distinct players", "97.5%", "98.75%", "`ppr` preset",
    "is not removed", "R18",
])
def test_the_document_states_the_rule(needle):
    assert needle in doc(), needle


def test_the_documented_constants_are_the_modules():
    text = doc()
    assert R.TAU == float(re.search(r"TAU = ([0-9.]+)", text).group(1))
    assert R.NULL_REPS == int(re.search(r"NULL_REPS = ([0-9]+)", text).group(1))
    assert R.BOOT_DRAWS == int(re.search(r"([0-9,]+) draws", text).group(1).replace(",", ""))
    assert R.MIN_PLAYERS_PER_BAND == int(re.search(r"fewer than ([0-9]+) distinct", text).group(1))
    assert len(R.PRIMARY) == 2 and len(R.SECONDARY) == 4


def test_the_pinned_weights_are_the_exports_ppr_today():
    """If the export's PPR moves, this fails - and the answer is a new call series
    (document section 9), not editing PRESET_WEIGHTS to match."""
    from jobs import export_web
    assert export_web.SCORING_PRESETS[R.PRESET]["weights"] == R.PRESET_WEIGHTS


# ------------------------------------------------ no ledger, no result

def test_main_refuses_without_a_ledger(capsys):
    assert R.main([]) == 2
    assert "nothing is graded" in capsys.readouterr().err


def test_main_with_a_ledger_still_refuses_until_the_run_is_built():
    with pytest.raises(R.NoLedger):
        R.main(["--ledger", "x.jsonl"])
