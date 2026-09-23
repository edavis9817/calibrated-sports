"""The silent-zero class: present, populated, and zero for a run of seasons.

Run: pytest -q tests/test_silent_zeros.py

WHY THIS CLASS NEEDS ITS OWN TESTS. Every other data gap in this project
announces itself as a null, and every guard already checks for nulls. This one
does not: `nonnull` is 1.000 across the whole run, so the column looks perfectly
healthy and a mean over it returns a number. It has shipped on the live site
twice - 2003-08 `targets`, and nine seasons of `def_tackles_for_loss` that
nobody knew about until the archive was swept.

The load-bearing test is `test_a_career_total_spanning_an_uncollected_season_is_null`.
A per-season rule is easy and insufficient: `_totals` is also called over an
entire career, and that is the call where a silent zero does the real damage -
the hole is invisible inside a lifetime sum.
"""
import os
import sqlite3

import pytest

import config
from jobs import export_web as E


# ------------------------------------------------------------ the declaration

def test_every_declared_column_is_actually_published():
    """A coverage entry for a column no page carries is dead weight that will
    outlive whoever can explain it. Every key must be a real source column
    behind STAT_MAP, DEF_COLUMNS, the derived target_share, or a staged
    feature's source column (a-15: receiving_air_yards, behind `air_rz`)."""
    published = ({c for c, _k in E.STAT_MAP} | {c for _k, c in E.DEF_COLUMNS} | {"target_share"}
                 | set(E.AIR_RZ_SOURCE_OF.values()))
    for col in E.NOT_COLLECTED:
        assert col in published, f"{col} is declared uncollected but is never published"


def test_the_runs_are_well_formed():
    for col, runs in E.NOT_COLLECTED.items():
        assert runs, col
        for lo, hi in runs:
            assert 1999 <= lo <= hi <= 2100, (col, lo, hi)


def test_collected_is_false_only_inside_a_run():
    assert not E.collected("targets", 2003)
    assert not E.collected("targets", 2008)
    assert E.collected("targets", 2002), "the season before the run is collected"
    assert E.collected("targets", 2009), "the season after the run is collected"


def test_an_undeclared_column_is_always_collected():
    """The table is an exception list, not an allowlist - a column nobody swept
    must keep publishing, or one omission blanks the site."""
    assert E.collected("receptions", 2003)
    assert E.collected("carries", 1999)


def test_the_longest_run_is_the_defensive_one():
    """def_tackles_for_loss, 2003-2011. Recorded because it is the one a career
    total hides, and the one that was unknown before the sweep."""
    assert E.NOT_COLLECTED["def_tackles_for_loss"] == ((2003, 2011),)
    for s in range(2003, 2012):
        assert not E.collected("def_tackles_for_loss", s), s
    assert E.collected("def_tackles_for_loss", 2002)
    assert E.collected("def_tackles_for_loss", 2012)


# ------------------------------------------------------------- what is emitted

def test_a_period_in_an_uncollected_season_publishes_null_not_zero():
    assert E._count({"season": 2005, "targets": 0.0}, "targets") is None
    assert E._count({"season": 2005, "targets": None}, "targets") is None


def test_the_same_column_publishes_zero_where_it_IS_collected():
    """Discriminating: without this, a helper that returned None unconditionally
    would pass the test above."""
    assert E._count({"season": 2010, "targets": None}, "targets") == 0
    assert E._count({"season": 2010, "targets": 7.0}, "targets") == 7


def test_an_unaffected_column_is_untouched_inside_the_run():
    """Receptions are fine in 2003-08 - 100% of completions carry a receiver.
    Nulling the whole season would be its own defect."""
    assert E._count({"season": 2005, "receptions": 4.0}, "receptions") == 4


def period(season, **stats):
    s = {k: 0 for k in E.PERIOD_KEYS}
    s["snap_share"] = None
    s.update(stats)
    return {"season": season, "season_type": "REG", "stats": s}


def test_a_season_total_in_an_uncollected_season_is_null():
    t = E._totals([period(2005, targets=None), period(2005, targets=None)])
    assert t["targets"] is None
    assert t["rec"] == 0, "only the uncollected key is nulled"


def test_a_career_total_spanning_an_uncollected_season_is_null():
    """THE ONE THAT MATTERS. `_totals` is called over every REG period for the
    career figure, so a player active 2002-2012 has real targets either side of
    the hole. Summing them and calling it a career total states a number the
    data cannot support - and reads as complete."""
    career = [period(2002, targets=90), period(2005, targets=None), period(2010, targets=110)]
    assert E._totals(career)["targets"] is None


def test_a_career_total_clear_of_every_run_still_states_a_number():
    """Discriminating: the rule must not null every career total."""
    career = [period(2010, targets=90), period(2011, targets=110)]
    assert E._totals(career)["targets"] == 200


def test_the_career_rule_is_ANY_not_ALL():
    """One uncollected season among many collected ones is enough. An `all`
    rule would pass every test above except this one."""
    career = [period(2010, targets=5)] * 9 + [period(2005, targets=None)]
    assert E._totals(career)["targets"] is None


# ------------------------------------- cross-check against track F's own sweep

def _analytics_db():
    p = config.storage_path("analytics.db")
    return p if os.path.exists(p) else None


@pytest.mark.skipif(
    _analytics_db() is None,
    reason="no analytics.db: track F's sweep is the cross-check source and this "
           "box has not scanned it (run `python -m analytics.survey --scan`)",
)
def test_this_table_agrees_with_track_Fs_independent_sweep():
    """THE ANTI-DRIFT GUARD. This table is declared here rather than imported,
    because `analytics` is track F's package and reads its own database - the
    export must not refuse to run because another track has not scanned. The
    cost of declaring it twice is drift, and this is what pays that cost.

    Only the intersection is compared: track F sweeps every column in the
    release, this file covers the ones the site publishes.
    """
    try:
        from analytics import survey
    except ImportError:
        pytest.skip("analytics package unavailable")
    con = sqlite3.connect(f"file:{_analytics_db()}?mode=ro", uri=True)
    try:
        rows = survey.silent_zeros(con, dataset="weekly_stats")
    except Exception as exc:                      # their schema, not ours
        pytest.skip(f"track F sweep unavailable: {exc}")
    finally:
        con.close()

    theirs = {col: tuple(tuple(r) for r in runs) for col, _dt, runs, *_ in rows}
    assert theirs, "the sweep returned nothing - it cannot confirm anything"

    for col, runs in E.NOT_COLLECTED.items():
        if col == "target_share":
            continue            # derived here, not a swept source column
        assert col in theirs, f"{col} is declared uncollected but their sweep does not see it"
        assert theirs[col] == runs, f"{col}: ours {runs}, theirs {theirs[col]}"


@pytest.mark.skipif(_analytics_db() is None, reason="no analytics.db on this box")
def test_no_PUBLISHED_column_they_flag_is_missing_from_this_table():
    """The other direction, which is the one that lets a defect through: they
    find a silent zero in a column this export publishes and nobody notices."""
    try:
        from analytics import survey
    except ImportError:
        pytest.skip("analytics package unavailable")
    con = sqlite3.connect(f"file:{_analytics_db()}?mode=ro", uri=True)
    try:
        rows = survey.silent_zeros(con, dataset="weekly_stats")
    except Exception as exc:
        pytest.skip(f"track F sweep unavailable: {exc}")
    finally:
        con.close()

    published = {c for c, _k in E.STAT_MAP} | {c for _k, c in E.DEF_COLUMNS}
    missed = [col for col, _dt, _runs, *_ in rows
              if col in published and col not in E.NOT_COLLECTED]
    assert not missed, f"published columns with an unhandled silent zero: {missed}"
