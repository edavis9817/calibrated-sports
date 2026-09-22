"""The contracts survey's two traps, and the figures F06 quotes.

The traps are both already-tabled classes, met in a new place: a silent double
hiding inside a nested list-of-struct, and a silent zero in `year_signed`.
"""
import os
import sqlite3

import pytest

from analytics import contracts_survey as cs
from analytics import paths

# `latest_asset` RAISES when no mirror exists at all (a clean clone, CI), so a
# bare call here errored the whole suite at collection rather than skipping.
try:
    HAS_FILE = paths.latest_asset(cs.ASSET) is not None
except FileNotFoundError:
    HAS_FILE = False
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
    assert got["total_rows"] == 8955
    assert got["agreeing"] / got["compared"] > 0.99, got
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


@needs_file
def test_the_nested_columns_are_duplicated_on_every_contract_row():
    """THE TRAP THAT CAUGHT THE FIRST VERSION OF THIS MODULE. The table is one
    row per (player, CONTRACT) and each row carries the player's WHOLE nested
    history, byte-identical - so exploding from the flat frame multiplies by
    that player's own row count. F06 first published counts inflated 6.19x
    because of it, and combined with the "Total" row a naive sum overstates
    total cap 9.33x.

    Worse than a constant double in one way: the multiplier is per player and
    variable (median 2, max 38), so no magnitude check catches it consistently
    and a ratio of two figures computed the same wrong way comes out right."""
    df = cs.load()
    got = cs.duplication(df)
    assert got["season_history"]["inflation"] > 5
    assert got["contract_history"]["inflation"] > 8
    assert got["rows_per_player"]["max"] >= 38


@needs_file
def test_a_players_nested_history_is_identical_on_every_one_of_his_rows():
    """The reason dedup is safe: the copies are not different slices of one
    history, they are the same history repeated."""
    import polars as pl
    df = cs.load()
    busiest = (df.group_by("otc_id").agg(pl.len())
               .sort("len", descending=True)["otc_id"][0])
    rows = df.filter(pl.col("otc_id") == busiest)
    assert rows.height > 1
    assert len({str(x) for x in rows["season_history"].to_list()}) == 1


@needs_file
def test_every_nested_accessor_deduplicates_first():
    """Assert on the SOURCE, because a function that forgets `per_player`
    returns a plausible number rather than an error."""
    import ast
    import inspect
    src = inspect.getsource(cs)
    tree = ast.parse(src)
    for fn in tree.body:
        if not isinstance(fn, ast.FunctionDef):
            continue
        body = ast.dump(fn)
        if "'explode'" not in body and '"explode"' not in body:
            continue
        if fn.name in ("duplication",):        # measures the inflation itself
            continue
        assert "per_player" in body, (
            "%s explodes a nested column without deduplicating first" % fn.name)
