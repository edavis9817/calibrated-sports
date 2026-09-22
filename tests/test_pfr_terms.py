"""Sports-Reference's terms, per column the drafting predictor reads (f-07).

The guard is over what `analytics.drafting` actually reads, by AST: a PFR
column read that nobody classified fails here rather than publishing on an
unread question. Shown firing on a planted read before its silence is trusted.
"""
import ast
import os

import pytest

from analytics import drafting, pfr_terms


def _constants(src):
    return {n.value for n in ast.walk(ast.parse(src))
            if isinstance(n, ast.Constant) and isinstance(n.value, str)}


def _snap_columns_read(src):
    """The `columns=[...]` literal inside `load_snaps`."""
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.FunctionDef) and node.name == "load_snaps":
            for kw in (k for c in ast.walk(node) if isinstance(c, ast.Call)
                       for k in c.keywords):
                if kw.arg == "columns":
                    return [e.value for e in kw.value.elts]
    raise AssertionError("load_snaps reads no columns= literal - the guard is blind")


def unclassified(src):
    """PFR columns `src` names that COLUMNS does not classify."""
    dp = set(pfr_terms.PFR_DATASETS["draft_picks"])
    miss = sorted(("draft_picks", c) for c in _constants(src) & dp
                  if ("draft_picks", c) not in pfr_terms.COLUMNS)
    miss += sorted(("snap_counts", c) for c in _snap_columns_read(src)
                   if ("snap_counts", c) not in pfr_terms.COLUMNS)
    return miss


def _src():
    with open(drafting.__file__, encoding="utf-8") as f:
        return f.read()


def test_every_pfr_column_drafting_reads_is_classified():
    assert unclassified(_src()) == []


def test_the_guard_fires_on_a_planted_read():
    src = _src()
    planted = src.replace('pl.col("w_av").fill_null(0)',
                          'pl.col("dr_av").fill_null(0)', 1)
    assert planted != src
    assert ("draft_picks", "dr_av") in unclassified(planted)
    planted = src.replace('"offense_snaps", "defense_snaps"]',
                          '"offense_snaps", "defense_snaps", "st_snaps"]', 1)
    assert planted != src
    assert ("snap_counts", "st_snaps") in unclassified(planted)


def test_the_guard_sees_what_drafting_reads():
    """Coverage, not only absence: the walk finds the columns we know are read."""
    src = _src()
    assert {"w_av", "pick", "team", "season"} <= _constants(src)
    assert set(_snap_columns_read(src)) == {"season", "game_type", "pfr_player_id",
                                            "offense_snaps", "defense_snaps"}


def test_every_classified_column_has_a_known_use_and_verdict():
    for (ds, col), (use, why) in pfr_terms.COLUMNS.items():
        assert ds in pfr_terms.PFR_DATASETS and col in pfr_terms.PFR_DATASETS[ds]
        assert use in pfr_terms.USES and why
        want = "credit_required" if use == "published" else "none"
        assert pfr_terms.column_verdict(ds, col) == want


def test_an_unclassified_column_has_no_verdict():
    with pytest.raises(KeyError):
        pfr_terms.column_verdict("draft_picks", "dr_av")


def test_the_brief_s_four_columns_each_have_an_answer():
    assert set(pfr_terms.BRIEF_COLUMNS) == {"w_av", "dr_av", "games", "snap_counts"}
    assert pfr_terms.column_verdict("draft_picks", "w_av") == "credit_required"
    assert pfr_terms.column_verdict("draft_picks", "games") == "none"
    assert ("draft_picks", "dr_av") not in pfr_terms.COLUMNS
    assert pfr_terms.column_verdict("snap_counts", "offense_snaps") == "credit_required"


def test_the_quotes_run_both_ways():
    q = pfr_terms.QUOTES
    assert "is welcomed" in q["permits"] and "explicitly credit SRL" in q["permits"]
    assert "material substitute" in q["prohibits_5i"]
    assert "machine learning" in q["prohibits_5j"]
    assert "should not create websites or tools" in q["data_use_gloss"]


def test_every_slice_rests_on_a_classified_pfr_dataset():
    from analytics import predictor_export as px
    assert set(pfr_terms.slice_datasets()) == set(px.SLICES)
    for sl, dss in pfr_terms.slice_datasets().items():
        assert "draft_picks" in dss and set(dss) <= set(pfr_terms.PFR_DATASETS)
