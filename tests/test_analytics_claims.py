"""The page's wording, and the one conversion it must never make.

An interval covering the null is **absence of evidence**. Copy is where that
turns silently into **evidence of absence**, so the rule from track B's
`rateVerdict` - "cannot be distinguished from X", never "is the same as X" - is
enforced here over every sentence this module can emit, not trusted to whoever
writes the next one.
"""
import os
import sqlite3

import pytest

from analytics import claims, paths

HAS_METRICS = False
if os.path.isabs(str(paths.db_path())) and os.path.exists(paths.db_path()):
    try:
        HAS_METRICS = paths.connect(read_only=True).execute(
            "SELECT COUNT(*) FROM f_metrics").fetchone()[0] > 0
    except sqlite3.Error:
        HAS_METRICS = False

needs_metrics = pytest.mark.skipif(
    not HAS_METRICS, reason="no published metrics in analytics.db")

ALL_VERDICTS = {claims.ABOVE, claims.BELOW, claims.INDISTINCT,
                claims.INSUFFICIENT}


# =============================================================================
# the verdict comes from the interval and nothing else
# =============================================================================

@pytest.mark.parametrize("lo,hi,null,n,want", [
    (0.01, 0.09, 0.0, 12, claims.ABOVE),
    (-0.09, -0.01, 0.0, 12, claims.BELOW),
    (-0.01, 0.09, 0.0, 12, claims.INDISTINCT),
    (0.0, 0.09, 0.0, 12, claims.INDISTINCT),      # touching is not clearing
    (0.01, 0.09, 0.0, 4, claims.INSUFFICIENT),    # brief 020: under 5 blocks
    (0.21, 0.29, 0.25, 12, claims.INDISTINCT),    # a non-zero null
    (0.26, 0.29, 0.25, 12, claims.ABOVE),
])
def test_the_verdict_is_a_function_of_the_interval(lo, hi, null, n, want):
    assert claims.verdict(lo, hi, null, n) == want


def test_every_verdict_is_reachable():
    """Falsifiability, as the research register already requires: a verdict
    value that cannot occur is decoration, not a finding."""
    got = {claims.verdict(*args) for args in [
        (0.01, 0.09, 0.0, 12), (-0.09, -0.01, 0.0, 12),
        (-0.01, 0.09, 0.0, 12), (0.01, 0.09, 0.0, 2)]}
    assert got == ALL_VERDICTS


# =============================================================================
# THE RULE
# =============================================================================

def _every_sentence():
    """One sentence from every (family, verdict) branch this module can emit."""
    out = []
    cases = [(0.05, 0.02, 0.08), (-0.05, -0.08, -0.02), (0.01, -0.03, 0.05)]
    for metric in ("script_elasticity.targets",
                   "usage_stability.within_lag1.target_share",
                   "usage_stability.naive_lag1.targets",
                   "usage_stability.between.carry_share",
                   "ngs_stability.within_lag1.receiving.avg_separation",
                   "ngs_stability.between.rushing.efficiency",
                   "role.touch_share", "air_yards.polarity.receiver",
                   "pace.seconds_per_play"):
        for est, lo, hi in cases:
            out.append(claims.claim(metric, "", est, lo, hi, 40, 0.0))
        out.append(claims.claim(metric, "", 0.05, 0.02, 0.08, 2, 0.0))
    return out


def test_no_generated_sentence_converts_cannot_tell_into_nothing_there():
    sentences = _every_sentence()
    assert len(sentences) >= 36
    for c in sentences:
        low = c["sentence"].lower()
        for phrase in claims.BANNED:
            assert phrase not in low, (phrase, c["metric"], c["sentence"])


def test_an_interval_covering_the_null_says_cannot_be_distinguished():
    c = claims.claim("script_elasticity.targets", "", 0.01, -0.03, 0.05, 40, 0.0)
    assert c["verdict"] == claims.INDISTINCT
    assert "cannot be distinguished from" in c["sentence"]


def test_a_decisive_interval_does_not_say_cannot_be_distinguished():
    c = claims.claim("script_elasticity.targets", "", 0.05, 0.02, 0.08, 40, 0.0)
    assert c["verdict"] == claims.ABOVE
    assert "cannot be distinguished" not in c["sentence"]


def test_every_sentence_carries_its_interval_and_its_n():
    """The structural rule, at the last place it can be dropped: a sentence
    without its apparatus is a point estimate again."""
    for c in _every_sentence():
        if c["verdict"] == claims.INSUFFICIENT:
            assert "too few" in c["sentence"]
            continue
        assert "95% interval" in c["sentence"], c["sentence"]
        assert "n=" in c["sentence"], c["sentence"]


def test_a_descriptive_metric_is_never_told_it_is_zero():
    """"Cannot be distinguished from zero target share" is true and absurd.
    A share's null is the typical subject, and the wording has to say so."""
    c = claims.claim("role.touch_share", "3rd_short", 0.20, 0.15, 0.25, 40, 0.20)
    assert "a typical player" in c["sentence"]
    assert "zero" not in c["sentence"].lower()


def test_the_stability_families_do_not_share_wording():
    """`between` is not persistence. Calling it that would be a hand-written
    claim that happened to be assembled."""
    within = claims.claim("usage_stability.within_lag1.target_share", "",
                          0.2, 0.1, 0.3, 1540, 0.0)["sentence"]
    between = claims.claim("usage_stability.between.target_share", "",
                           0.2, 0.1, 0.3, 1540, 0.0)["sentence"]
    assert "persistence" in within and "persistence" not in between
    assert "separates players" in between


# =============================================================================
# against the real store
# =============================================================================

@needs_metrics
def test_every_published_metric_produces_a_claim():
    con = paths.connect(read_only=True)
    got = claims.all_metrics(con)
    assert len(got) == 87
    for m in got:
        assert m["headline"], m["metric"]
        assert set(m["counts"]) <= ALL_VERDICTS


@needs_metrics
def test_no_published_sentence_breaks_the_rule():
    con = paths.connect(read_only=True)
    checked = 0
    for m in claims.all_metrics(con):
        for text in [m["headline"]] + [c["sentence"] for c in m["claims"][:50]]:
            low = text.lower()
            for phrase in claims.BANNED:
                assert phrase not in low, (phrase, m["metric"], text)
            checked += 1
    assert checked > 500, checked


@needs_metrics
def test_the_metric_envelope_never_contradicts_its_own_values():
    """`ngs_stability` published a registry range of 1999-2026 around values
    stamped 2016-2026 - both halves internally consistent, the file saying two
    different things."""
    con = paths.connect(read_only=True)
    bad = con.execute(
        "SELECT DISTINCT v.metric, m.season_from, m.season_to, v.season_from, "
        "v.season_to FROM f_metric_values v JOIN f_metrics m USING (metric) "
        "WHERE v.season_from < m.season_from OR v.season_to > m.season_to"
    ).fetchall()
    assert bad == [], bad


@needs_metrics
def test_insufficient_fires_on_nothing_today_and_is_still_reachable():
    """Counted out loud: a number that reads 0 today is what makes the day it
    reads 12 visible."""
    con = paths.connect(read_only=True)
    smallest = con.execute("SELECT MIN(n) FROM f_metric_values").fetchone()[0]
    assert smallest >= claims.MIN_BLOCKS
    assert claims.verdict(0.1, 0.2, 0.0, 1) == claims.INSUFFICIENT
