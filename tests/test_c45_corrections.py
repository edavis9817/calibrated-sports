"""c-45: the six wording corrections taken from f-31 run 4 stay corrected.

No store, no model, no number is computed here. Each pin is a sentence: the corrected
text must be in the file that carries the claim, the sentence it replaced must not be,
and both must be in the audit trail (`docs/findings/c45-corrections.md`), so a
correction cannot revert, and cannot be made, without the trail showing it.

Whitespace is collapsed before comparing, so re-wrapping a paragraph is not a failure.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TRAIL = "docs/findings/c45-corrections.md"

C41 = "docs/findings/c41-residual-given-line.md"
C34 = "docs/findings/known-before-kickoff.md"
C38 = "docs/findings/cross-sport.md"
C35 = "docs/findings/ladder-edges.md"
C33 = "docs/findings/soft-markets.md"
C40 = "docs/findings/c40-counterfactual-ledger.md"
CLAUDE = "CLAUDE.md"

# (unit, file, sentence replaced or None for an addition, corrected sentence)
PINS: list[tuple[str, str, str | None, str]] = [
    # ---- c-41: a null worded past its power
    ("c-41", C41, "## The answer is no", "## None of the six passes the registered rule"),
    ("c-41", C41,
     "What this rules out is an effect of about 2 points of over probability per standard "
     "deviation; it does not rule out one of 1.",
     "**This null does not rule out an effect of 2 points of over probability per standard "
     "deviation.**"),
    ("c-41", C41, None,
     "the rule passes 9% of the time at 0.020 per sd, 41% at 0.033 and 82-85% at 0.045-0.050."),
    ("c-41", C41,
     "**`team_total` is the nearest thing to a signal and it fails three ways.**",
     "It did not pass, three ways - and it is unresolved, not refuted.**"),
    ("c-41", C41,
     "**The only arm that improves on the close out of sample is the constant**",
     "**The only arm whose out-of-sample improvement on the close exceeds its MDE is the "
     "constant**"),
    ("c-41", CLAUDE,
     "**Given the line, nothing on a six-candidate list predicts the receiving-yards residual**",
     "**Given the line, none of six candidates passed the registered rule on the "
     "receiving-yards residual - not detected, by a rule that reaches 80% detection only near "
     "4.5-5 points per sd; team implied total is unresolved, not refuted**"),
    ("c-41", CLAUDE,
     "an effect of ~2 points of over probability per sd is ruled out; 1 point is not.",
     "**Scope of the null: it does NOT rule out 2 points of over probability per sd.**"),
    ("c-41", CLAUDE,
     "The only arm that beats the close out of sample is the constant",
     "The only arm whose out-of-sample gain on the close exceeds its MDE is the constant "
     "(1.16x, descriptive)"),
    # ---- c-34: a "tight null" on a perturbation that could not have closed the gap
    ("c-34", C34, "A null, not a refutation — but a tight one:",
     "A null, not a refutation — and not a tight one."),
    ("c-34", C34, "An improvement worth having would have been seen.",
     "replacing those same rows with the close's own price raises DSC by +0.0006, about 15% "
     "of the 0.0041 gap, which this test detects barely for arm 1 and not for arm 3."),
    ("c-34", C34, "and both improve calibration.",
     "arm 3 lowers miscalibration and arm 1's fall does not survive correction."),
    ("c-34", C34, None,
     "**Arm 1's fall in miscalibration does not survive correction**"),
    ("c-34", C34, "has can only ever buy MCB.",
     "an inference from two correlations with no interval, not a measured limit."),
    ("c-34", C34,
     "**at the close, on main lines, the market has already used everything public.**",
     "inferred, not measured, and not established by this unit"),
    # ---- c-38: a success rule that could not have failed; a bare size claim
    ("c-38", C38, None,
     "6 of the 16 are gated to published values and the 4 ordering contrasts cannot fail even "
     "at worst-case SE, so the registered success rule could not have returned failure."),
    ("c-38", C38,
     "- **The advantage is larger in college on every baseline, on both scales.**",
     "**The RAW advantage is larger in college on every baseline, on both scales, and the raw "
     "gap is not a same-strength comparison.**"),
    ("c-38", C38, None,
     "the dBrier gap is -0.0028 [-0.0071, +0.0012] against home (0.46 of its MDE: not "
     "detected, a null this population cannot resolve, not a measured zero)"),
    ("c-38", C38, None, "**No re-weighted figure exists on the skill scale at all**"),
    ("c-38", C38,
     "## Why it is bigger in college: mostly that college games are more lopsided",
     "## Why the raw gap is bigger in college: against home, mostly that college games are "
     "more lopsided"),
    ("c-38", CLAUDE, "It is LARGER in college on every baseline - college minus NFL",
     "The RAW gap is larger in college on every baseline - college minus NFL"),
    ("c-38", CLAUDE, None,
     "and the raw gap is not a same-strength comparison: re-weighted to the NFL's distribution "
     "of favourite probability it is -0.0028 [-0.0071, +0.0012] against home"),
    ("c-38", CLAUDE, None,
     "by a success rule that could not have returned failure once c-28's and c-39's published "
     "figures reproduced"),
    ("c-38", CLAUDE,
     "**The size gap against `home` is the wider spread of college games, not a better model**",
     "**At equal forecast strength the size gap against `home` is not detected: -0.0028 "
     "[-0.0071, +0.0012], 0.46 of its MDE - a null this population cannot resolve, not a "
     "measured zero**"),
    # ---- c-35: a share that says nothing about this model; an unsupported "no edge"
    ("c-35", C35,
     "**The shape between the rungs is the shape receivers actually produce.**",
     "**By the registered rule the ladder's implied shape departs from what happened in one "
     "cell: receptions-RB, one catch above the line; 1 of 48 registered intervals survives "
     "correction, unreplicated.**"),
    ("c-35", C35, "and the level is the model's error.**",
     "and that share says nothing about this model.**"),
    ("c-35", C35, None,
     "pairing each market ladder with ANOTHER player's model ladder gives 0.959 (f-31 run 4)."),
    ("c-35", C35, "Nothing here is an edge.",
     "No edge is reported here, and none is ruled out below the MDEs."),
    ("c-35", C35, None,
     "0.03 of its 12.6pp MDE (f-31 run 4), so this run could not have detected an edge of "
     "that size or told one from none."),
    ("c-35", C35, "Not an edge, and not reported as one.",
     "Not reported as an edge; at 0.03 of its 12.6pp MDE (f-31 run 4) this join cannot tell "
     "an edge from none."),
    ("c-35", C35, "The level is right everywhere", "No level error is detected anywhere"),
    ("c-35", C35, "a market whose level is right**",
     "a market whose level is not detectably off**"),
    ("c-35", C35, "no rule beats the default, the default being the right rung",
     "not a demonstration that the central rung is the right one."),
    ("c-35", C35, None,
     "the fit is as-of kickoff while the price it is compared with is at kickoff - 180 minutes, "
     "and position and team from the game's own stat row enter the fit."),
    # ---- c-33: the number moved with the ruler; the class contrast stands
    ("c-33", C33, "## 3. Within props, the three proxies do NOT agree on an order",
     "## 3. Within props, no common order is shown, and the order depends on the slope chosen"),
    ("c-33", C33,
     "**There is no common \"softness\" among these keys for a composite to find.**",
     "**That -0.48 is not evidence of anything: over 8 keys its exact permutation p is 0.24, "
     "and under the registered slope it is +0.74**"),
    ("c-33", C33,
     "- **Disagreement is highest on the yards keys** (rush 2.81, receiving 2.57)",
     "**Under the addendum-1 slope, on 2026 weeks 3-4 (32 games), disagreement is highest on "
     "the yards keys** (rush 2.81, receiving 2.57)"),
    ("c-33", C33, None,
     "under the registered slope rush yards is 0.73pp and rank 6 of 8"),
    ("c-33", C33, "which is the check that the ladder slope is the right quantity",
     "it is not a check of the yards slope."),
    ("c-33", C33, "which is where a forecast has the most room — nothing more.",
     "Whether a forecast has more room there is untested"),
    ("c-33", C33, "disagree with each other about 2-4x as much",
     "disagree with each other about 1.1pp more"),
    # ---- c-40: a description of the model, not of misses
    ("c-40", C40, "So the concentration is real and it is not about missing.",
     "So the concentration is real and is not detectably about missing."),
    ("c-40", C40, "No input is over-represented among the misses.",
     "No input is detectably over-represented among the misses: a difference in share under "
     "about 0.11-0.12 would not have been seen on 31 games."),
    ("c-40", C40, None,
     "`own_mean` is the flipping input on 67.1% of all 477 leans whatever the outcome, and with "
     "the missed/cleared label permuted within game 66.1% sits at the 62nd percentile of its "
     "null (p about 0.75, drawn twice independently)."),
    ("c-40", C40, None, "**All 19 are week 4**"),
    ("c-40", C40, "The same sentence is true, at the same rate, of the leans that cleared.",
     "at a rate this sample does not distinguish from the misses'"),
    ("c-40", CLAUDE, "is the player's own prior mean, on misses and clears alike**",
     "a description of the model, not of misses; missed against cleared is a null at 0.16 of "
     "its MDE (0.12)**"),
    ("c-40", CLAUDE, None, "The 19 excluded leans are all week 4."),
]

# c-33's class contrast survived f-31's correction and must not be softened with the rest.
KEPT: list[tuple[str, str]] = [
    (C33, "## 2. Props, as a class, are softer than game lines on all three proxies"),
    (C33, "**This class contrast stands, at full strength**"),
    (C33, "surviving Bonferroni over all 372 computed intervals."),
]

UNITS = ("c-33", "c-34", "c-35", "c-38", "c-40", "c-41")


def norm(text: str) -> str:
    return re.sub(r"\s+", " ", text)


def read(rel: str) -> str:
    text = (ROOT / rel).read_text(encoding="utf-8")
    assert len(text) > 2000, f"{rel} is unexpectedly short ({len(text)} chars)"
    return norm(text)


def problems(text: str, old: str | None, new: str) -> list[str]:
    """What is wrong with `text` for one pin. Empty means the correction holds."""
    out = []
    if norm(new) not in text:
        out.append("corrected sentence missing")
    if old is not None and norm(old) in text:
        out.append("replaced sentence is back")
    return out


IDS = [f"{u}:{Path(f).stem}:{i}" for i, (u, f, _, _) in enumerate(PINS)]


@pytest.mark.parametrize("unit,rel,old,new", PINS, ids=IDS)
def test_the_corrected_sentence_holds(unit, rel, old, new):
    assert problems(read(rel), old, new) == [], f"{unit} {rel}: {new[:70]!r}"


@pytest.mark.parametrize("unit,rel,old,new", PINS, ids=IDS)
def test_the_audit_trail_carries_both_sentences(unit, rel, old, new):
    trail = read(TRAIL)
    assert norm(new) in trail, f"{unit}: corrected sentence not in {TRAIL}"
    if old is not None:
        assert norm(old) in trail, f"{unit}: replaced sentence not in {TRAIL}"


@pytest.mark.parametrize("rel,kept", KEPT)
def test_c33_class_contrast_is_not_softened(rel, kept):
    assert norm(kept) in read(rel)


def test_every_unit_is_pinned_in_its_findings_doc_and_where_it_has_one_in_claude_md():
    assert len(PINS) == 47
    by_unit = {u: {f for uu, f, _, _ in PINS if uu == u} for u in UNITS}
    assert set(by_unit) == {u for u, _, _, _ in PINS}
    for unit, files in by_unit.items():
        assert any(f.startswith("docs/findings/") for f in files), unit
    # c-33, c-34 and c-35 never had a CLAUDE.md entry; the other three do and are pinned there.
    assert {u for u, f in ((u, f) for u, f, _, _ in PINS) if f == CLAUDE} == {
        "c-38", "c-40", "c-41"}
    # every replaced sentence differs from what replaced it, and is not contained in it
    for unit, _, old, new in PINS:
        if old is not None:
            assert norm(old) not in norm(new), (unit, old)


def test_the_check_fails_on_a_reverted_sentence():
    """The pin discriminates: shown returning the other answer on the other input."""
    unit, rel, old, new = next(p for p in PINS if p[0] == "c-38" and p[1] == C38 and p[2])
    good = read(rel)
    assert problems(good, old, new) == []
    reverted = good.replace(norm(new), norm(old))
    assert problems(reverted, old, new) == ["corrected sentence missing",
                                           "replaced sentence is back"]
    assert problems(good + " " + norm(old), old, new) == ["replaced sentence is back"]
    assert problems("", old, new) == ["corrected sentence missing"]


def test_the_trail_names_f31_and_does_not_claim_a_measurement_of_its_own():
    trail = read(TRAIL)
    for unit in UNITS:
        assert f"## {unit}" in trail, unit
    assert "f-31 run 4" in trail
    assert "No number in it was measured by c-45" in trail
