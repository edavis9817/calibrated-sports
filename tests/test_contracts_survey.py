"""The contracts survey's two traps, and the figures F06 quotes.

The traps are both already-tabled classes, met in a new place: a silent double
hiding inside a nested list-of-struct, and a silent zero in `year_signed`.
"""
import os
import sqlite3

import pytest

from analytics import contracts_survey as cs
from analytics import paths

HAS_FILE = paths.latest_asset(cs.ASSET) is not None
needs_file = pytest.mark.skipif(
    not HAS_FILE, reason="historical_contracts.parquet is not in the archive")

HAS_SPINE = False
if os.path.isabs(str(paths.db_path())) and os.path.exists(paths.db_path()):
    try:
        HAS_SPINE = paths.connect(read_only=True).execute(
            "SELECT COUNT(*) FROM f_play_usage").fetchone()[0] > 0
    except sqlite3.Error:
        HAS_SPINE = False
needs_spine = pytest.mark.skipif(not HAS_SPINE, reason="no spine built")


def test_the_needed_fields_table_names_the_four_gaps():
    """The gap list is the deliverable, so it is pinned: a field appearing
    upstream later must turn a MISSING into a hit without anyone remembering
    to look, and a field quietly dropped from the check must fail here."""
    for gap in ("dead money", "void years", "restructures",
                "guarantee structure (injury vs full)"):
        assert gap in cs.NEEDED, gap


@needs_file
def test_dead_money_and_void_years_are_absent_at_every_level():
    """Checked across the flat columns AND both nested structs - an absence
    established by looking only at the top level would be worth nothing."""
    df = cs.load()
    found = cs.gaps(df)
    assert found["dead money"] == []
    assert found["void years"] == []
    assert found["restructures"] == []
    assert found["incentives / escalators"] == []
    # and the contrast: what IS there, so the check can distinguish
    assert found["cap hit per year"] == ["cap_number"]
    assert "draft_round" in found["draft capital"]


@needs_file
def test_the_nested_Total_row_duplicates_the_years_it_sits_beside():
    """THE SILENT DOUBLE. Explode and sum without excluding it and every cap
    figure doubles - same class as NGS week 0, one level less visible."""
    got = cs.total_row_trap(cs.load())
    assert got["total_rows"] > 40000
    assert got["agreeing"] / got["compared"] > 0.95, got
    assert got["null_year_rows"] > 0, "year is nullable too, and must be filtered"


@needs_file
def test_year_signed_carries_a_silent_zero_not_a_null():
    """A filter like `year_signed >= 2010` drops these without saying so."""
    d = cs.depth(cs.load())
    assert "unknown (year_signed = 0)" in d
    assert d["unknown (year_signed = 0)"] > 1000


@needs_file
def test_the_identity_gap_is_measured_in_both_directions():
    got = cs.identity(cs.load())
    assert got["players_without_any_gsis"] > 1500
    # ids that point at nobody: present here, absent from nflverse's crosswalk
    assert got["gsis_not_in_players_parquet"] > 1000
    assert got["active_with_gsis"] / got["active_players"] > 0.98


@needs_file
@needs_spine
def test_the_join_is_a_cliff_and_not_a_flat_rate():
    """F06's central claim: 60.6% overall hides 0-5% before 2010 and 98-100%
    from 2015. A flat rate would have licensed a feature that is empty for
    every player it cannot cover."""
    got = cs.join_to_spine(cs.load(), paths.connect(read_only=True))
    era = got["by_era"]
    early = sum(era[k][0] for k in era if k < "2010")
    early_total = sum(sum(era[k]) for k in era if k < "2010")
    late = sum(era[k][0] for k in era if k >= "2015")
    late_total = sum(sum(era[k]) for k in era if k >= "2015")
    assert early / max(early_total, 1) < 0.10, "pre-2010 should be near zero"
    assert late / max(late_total, 1) > 0.95, "2015+ should be near complete"
