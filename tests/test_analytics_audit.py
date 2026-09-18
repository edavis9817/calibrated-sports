"""The audit's arithmetic, and the classification that decides its answer.

The risk in this module is not a crash - it is a plausible number. Testing a
descriptive metric against zero returns ~100% "decisive" and would sit beside
track B's 93.1% looking like a finding. So the classification is asserted
complete, and the criterion is checked against hand-computed cases.
"""
import os
import sqlite3

import pytest

from analytics import audit, paths

HAS_METRICS = False
if os.path.isabs(str(paths.db_path())) and os.path.exists(paths.db_path()):
    try:
        HAS_METRICS = paths.connect(read_only=True).execute(
            "SELECT COUNT(*) FROM f_metrics").fetchone()[0] > 0
    except sqlite3.Error:
        HAS_METRICS = False

needs_metrics = pytest.mark.skipif(
    not HAS_METRICS, reason="no published metrics in analytics.db")


# =============================================================================
# the classification
# =============================================================================

def test_a_difference_has_a_zero_null_and_a_share_does_not():
    assert audit.null_kind("script_elasticity.targets", "") == "zero"
    assert audit.null_kind("script_elasticity.targets", "trailing_7plus") == "reference"
    assert audit.null_kind("usage_stability.within_lag1.targets", "") == "zero"
    assert audit.null_kind("ngs_stability.between.receiving.avg_cushion", "") == "zero"
    assert audit.null_kind("role.touch_share", "3rd_short") == "reference"
    assert audit.null_kind("air_yards.quantiles.receiver", "q50") == "reference"
    assert audit.null_kind("pace.plays_per_game", "") == "reference"


def test_an_unclassified_family_refuses_rather_than_defaulting_to_zero():
    """A metric whose null nobody decided would be tested against zero and look
    decisive for free. That is the failure this module exists to avoid."""
    with pytest.raises(SystemExit, match="not classified"):
        audit.null_kind("brand_new_family.something", "")


@needs_metrics
def test_every_published_metric_family_is_classified():
    con = paths.connect(read_only=True)
    for (metric,) in con.execute("SELECT metric FROM f_metrics"):
        audit.null_kind(metric, "")          # raises if unclassified


# =============================================================================
# the criterion
# =============================================================================

def _db(rows, tmp_path):
    """rows: (metric, slice, est, lo, hi, n, subject_type)."""
    con = sqlite3.connect(str(tmp_path / "a.db"))
    con.executescript("""
      CREATE TABLE f_metrics (metric TEXT PRIMARY KEY, label TEXT,
        subject_type TEXT, availability TEXT);
      CREATE TABLE f_metric_values (metric TEXT, slice TEXT, est REAL,
        lo REAL, hi REAL, n INTEGER);
    """)
    seen = set()
    for metric, sl, est, lo, hi, n, st in rows:
        if metric not in seen:
            con.execute("INSERT INTO f_metrics VALUES (?,?,?,?)",
                        (metric, metric, st, "current"))
            seen.add(metric)
        con.execute("INSERT INTO f_metric_values VALUES (?,?,?,?,?,?)",
                    (metric, sl, est, lo, hi, n))
    con.commit()
    return con


def test_a_zero_null_value_is_decisive_only_when_the_interval_clears_zero(tmp_path):
    con = _db([("script_elasticity.targets", "", 0.05, 0.01, 0.09, 12, "player"),
               ("script_elasticity.targets", "", 0.05, -0.01, 0.11, 12, "player"),
               ("script_elasticity.targets", "", -0.06, -0.10, -0.02, 12, "player")],
              tmp_path)
    per_value, _ = audit.audit(con)
    assert [v[3] for v in per_value] == [True, False, True]


def test_a_descriptive_value_is_judged_against_the_median_not_zero(tmp_path):
    """All three intervals exclude 0 - every share does. Only the one that
    clears the MEDIAN of 0.20 counts."""
    con = _db([("role.touch_share", "3rd_short", 0.20, 0.15, 0.25, 12, "player"),
               ("role.touch_share", "3rd_short", 0.10, 0.05, 0.15, 12, "player"),
               ("role.touch_share", "3rd_short", 0.30, 0.22, 0.38, 12, "player")],
              tmp_path)
    per_value, _ = audit.audit(con)
    assert all(v[2] == "reference" for v in per_value)
    assert [v[3] for v in per_value] == [False, True, True]


def test_testing_a_share_against_zero_would_return_everything(tmp_path):
    """The number this module exists to avoid producing. Stated as a test so
    the reason the reference null exists is not only a docstring."""
    con = _db([("role.touch_share", "x", 0.20, 0.15, 0.25, 12, "player"),
               ("role.touch_share", "x", 0.10, 0.05, 0.15, 12, "player"),
               ("role.touch_share", "x", 0.30, 0.22, 0.38, 12, "player")],
              tmp_path)
    rows = con.execute("SELECT lo, hi FROM f_metric_values").fetchall()
    assert all(lo > 0 or hi < 0 for lo, hi in rows), "every share clears zero"
    per_value, _ = audit.audit(con)
    assert sum(v[3] for v in per_value) < len(rows)


def test_the_player_and_league_blocks_are_counted_separately(tmp_path):
    con = _db([("script_elasticity.targets", "", 0.05, -0.01, 0.11, 12, "player"),
               ("usage_stability.within_lag1.targets", "", -0.02, -0.03, -0.01,
                1540, "league")], tmp_path)
    per_value, _ = audit.audit(con)
    assert audit.split(per_value, "zero", "player") == (0, 1)
    assert audit.split(per_value, "zero", "league") == (1, 1)
    assert audit.split(per_value, "zero") == (1, 2)


def test_an_empty_store_refuses_rather_than_reporting_a_clean_zero(tmp_path):
    con = _db([], tmp_path)
    with pytest.raises(SystemExit, match="audit of nothing"):
        audit.audit(con)


# =============================================================================
# against the real store
# =============================================================================

@needs_metrics
def test_the_published_split_is_the_one_in_the_report():
    """docs/F04 quotes 485 of 3,287 per player. A data change must move the
    document, not leave it quietly wrong."""
    con = paths.connect(read_only=True)
    per_value, per_metric = audit.audit(con)
    assert len(per_metric) == 87
    assert audit.split(per_value, "zero", "player") == (485, 3287)
    assert audit.split(per_value, "zero", "league") == (69, 72)
    assert audit.split(per_value, "reference") == (27124, 49224)
