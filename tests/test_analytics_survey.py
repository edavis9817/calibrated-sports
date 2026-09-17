"""The coverage survey, and the four shapes its detector has to catch.

The first version of the detector caught ONE of them - a leading-edge cliff -
and it looked like it worked, because `air_yards` from 2006 is a leading-edge
cliff and that is the example everybody checks. It reported nothing for the
2003-2008 targets hole, which is the defect the survey exists to prevent.

So the fixture below is synthetic and carries all four on purpose. The real
archive is checked separately, and skips where there is no store.
"""
import os
import sqlite3

import pytest

from analytics import survey

SEASONS = list(range(1999, 2027))


def _db(columns, tmp_path):
    """A f_pbp_columns table from {column: {season: informative_share}}."""
    con = sqlite3.connect(str(tmp_path / "t.db"))
    con.executescript(survey.SCHEMA)
    rows = 45000
    for col, by_season in columns.items():
        for season in SEASONS:
            share = by_season.get(season, 0.0)
            inf = int(rows * share)
            # informative implies non-null; a "zero" cliff is non-null and 0.
            nn = rows if by_season.get("nonnull_when_empty") else inf
            con.execute(
                "INSERT INTO f_pbp_columns VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                ("pbp", season, col, "", "Float64", rows, max(nn, inf), inf,
                 0, 2, "2026-09-09", 0))
    con.execute("INSERT OR REPLACE INTO f_survey_meta VALUES "
                "('schema_version', ?)", (survey.SCHEMA_VERSION,))
    con.commit()
    return con


def test_an_onset_cliff_is_found(tmp_path):
    """air_yards: nothing before 2006, ~0.37 after."""
    con = _db({"air_yards": {s: (0.0 if s < 2006 else 0.37) for s in SEASONS}},
              tmp_path)
    d = survey.anomalies(con)["air_yards"]
    assert d["absent"] == list(range(1999, 2006))
    assert d["kind"] == "null"


def test_a_mid_series_hole_is_found(tmp_path):
    """THE ONE THE FIRST DETECTOR MISSED. Full, empty, full."""
    con = _db({"targets": {s: (0.005 if 2003 <= s <= 2008 else 0.37)
                           for s in SEASONS}}, tmp_path)
    d = survey.anomalies(con)["targets"]
    assert d["absent"] == list(range(2003, 2009))
    assert survey.fmt_runs(d["absent"]) == "2003-2008"


def test_a_late_collapse_is_found(tmp_path):
    """tackle_with_assist: full for two decades, gone at the live end. A
    detector that only looks at leading seasons reports this as clean."""
    con = _db({"twa": {s: (0.11 if s <= 2024 else 0.007) for s in SEASONS}},
              tmp_path)
    d = survey.anomalies(con)["twa"]
    # 0.007 against a reference of 0.11 is 6% - past ABSENT, inside THIN. What
    # the test asserts is that the LIVE seasons are flagged, not which bucket.
    flagged = set(d["absent"]) | set(d["thin"])
    assert {2025, 2026} <= flagged


def test_a_ramp_that_never_reaches_the_floor_is_found_as_a_step(tmp_path):
    """receiver_player_id holds 0.22 where the reference is 0.39 - 57%, which
    clears every absent/thin threshold, and still means a target cannot be
    attributed on an incompletion for six seasons."""
    con = _db({"receiver_player_id":
               {s: (0.22 if 2003 <= s <= 2008 else 0.38) for s in SEASONS}},
              tmp_path)
    d = survey.anomalies(con)["receiver_player_id"]
    assert d["absent"] == [] and d["thin"] == []
    assert [s for s, _a, _b in d["steps"]] == [2003, 2009]


def test_a_flag_that_is_rare_by_construction_is_not_an_anomaly(tmp_path):
    """`interception` is 0 on 99% of plays in every season. A detector that
    scores against an absolute share calls all 28 seasons empty."""
    con = _db({"interception": {s: 0.012 for s in SEASONS}}, tmp_path)
    assert "interception" not in survey.anomalies(con)


def test_a_column_populated_in_only_two_seasons_does_not_set_the_reference(
        tmp_path):
    """`end_yard_line` is 0.86 in 2001-2002 and 0 in the other 26. Scoring
    against the MAX makes 26 seasons the finding; the finding is the two."""
    con = _db({"end_yard_line": {2001: 0.86, 2002: 0.86}}, tmp_path)
    d = survey.anomalies(con)["end_yard_line"]
    assert d["kind"] == "sparse" and d["covered"] == [2001, 2002]


def test_a_zero_cliff_is_distinguished_from_a_null_cliff(tmp_path):
    """qb_hit is 0.0 and NOT NULL for 2003-2005. A null check finds nothing;
    a sum over it is silently wrong, which is the dangerous one."""
    con = _db({"qb_hit": {**{s: 0.06 for s in SEASONS},
                          2003: 0.0, 2004: 0.0, 2005: 0.0,
                          "nonnull_when_empty": True}}, tmp_path)
    assert survey.anomalies(con)["qb_hit"]["kind"] == "zero"


def test_fmt_runs_compresses_consecutive_seasons():
    assert survey.fmt_runs([2003, 2004, 2005, 2008]) == "2003-2005,2008"
    assert survey.fmt_runs([]) == "-"


# =============================================================================
# against the real archive
# =============================================================================

from analytics import paths                                    # noqa: E402

HAS_SCAN = False
if os.path.isabs(str(paths.db_path())) and os.path.exists(paths.db_path()):
    try:
        c = paths.connect(read_only=True)
        HAS_SCAN = c.execute(
            "SELECT COUNT(*) FROM f_pbp_columns").fetchone()[0] > 0
    except sqlite3.Error:
        HAS_SCAN = False

needs_scan = pytest.mark.skipif(
    not HAS_SCAN,
    reason="no scanned analytics.db: run `python -m analytics.survey --scan`")


@needs_scan
def test_the_real_archive_reproduces_the_three_known_cliffs():
    """Air yards from 2006, snap counts from 2013, and the 2003-08 targets
    hole. Stated in CLAUDE.md; measured here so a data change is visible."""
    found = survey.anomalies(paths.connect(read_only=True))
    assert survey.fmt_runs(found["air_yards"]["absent"]) == "1999-2005"
    assert survey.fmt_runs(found["qb_hit"]["absent"]) == "2003-2005"
    assert [s for s, _a, _b in found["receiver_player_id"]["steps"]] == [2003]


@needs_scan
def test_the_report_is_not_empty_and_names_its_denominator():
    con = paths.connect(read_only=True)
    found = survey.anomalies(con)
    total = con.execute("SELECT COUNT(DISTINCT column_name) FROM "
                        "f_pbp_columns WHERE dataset='pbp'").fetchone()[0]
    assert total > 300
    assert 0 < len(found) < total, "everything or nothing flagged is a bug"


@needs_scan
def test_every_headline_figure_in_the_report_is_reproducible():
    """`docs/F01-pbp-survey.md` section 1. No unsourced figures: if a number is
    on the page it is re-derived here, so a re-pull that moves one fails rather
    than leaving the document quietly wrong."""
    con = paths.connect(read_only=True)
    n_seasons, plays, games, cols = con.execute(
        "SELECT COUNT(*), SUM(rows), SUM(games), MAX(columns_n) "
        "FROM f_pbp_files WHERE dataset='pbp'").fetchone()
    assert (n_seasons, plays, games, cols) == (28, 1282384, 7289, 372)
    assert con.execute("SELECT MIN(season), MAX(season) FROM f_pbp_files "
                       "WHERE dataset='pbp'").fetchone() == (1999, 2026)
    assert con.execute("SELECT rows, games FROM f_pbp_files "
                       "WHERE dataset='pbp' AND season=2026").fetchone() == (2756, 16)


@needs_scan
def test_the_anomaly_counts_in_the_report_are_reproducible():
    """Section 2's opening line: 98 of 372, split 62 / 24 / 12."""
    con = paths.connect(read_only=True)
    found = survey.anomalies(con)
    absent = {c for c, d in found.items() if d["absent"]}
    thin = {c for c, d in found.items() if d["thin"]} - absent
    assert (len(found), len(absent), len(thin),
            len(found) - len(absent) - len(thin)) == (98, 62, 24, 12)


@needs_scan
def test_the_dead_and_dtype_changing_columns_are_still_the_named_ones():
    con = paths.connect(read_only=True)
    dead = [r[0] for r in con.execute(
        "SELECT column_name FROM f_pbp_columns WHERE dataset='pbp' "
        "GROUP BY column_name HAVING MAX(nonnull) = 0 ORDER BY column_name")]
    assert dead == ["lateral_sack_player_id", "lateral_sack_player_name"]
    moving = [r[0] for r in con.execute(
        "SELECT column_name FROM f_pbp_columns WHERE dataset='pbp' "
        "GROUP BY column_name HAVING COUNT(DISTINCT dtype) > 1 "
        "ORDER BY column_name")]
    assert moving == ["goal_to_go", "xyac_median_yardage"]


# =============================================================================
# the silent-zero class: non-null, and zero, for a run of seasons
# =============================================================================

def test_the_sweep_catches_a_run_of_exact_zeros(tmp_path):
    con = _db({"qb_hit": {**{s: 0.06 for s in SEASONS},
                          2003: 0.0, 2004: 0.0, 2005: 0.0,
                          "nonnull_when_empty": True}}, tmp_path)
    found = {r[0]: r for r in survey.silent_zeros(con)}
    assert found["qb_hit"][2] == [(2003, 2005)]


def test_the_sweep_catches_A_RUN_THAT_IS_NOT_EXACTLY_ZERO(tmp_path):
    """THE ONE THE FIRST VERSION MISSED, AND IT IS THE WORKED EXAMPLE.

    League `targets` for 2003-2008 is 3, 5, 0, 67, 14, 17 - five of the six
    seasons are not exactly zero. A sweep requiring == 0 returns two unrelated
    columns and walks past the defect it is named after."""
    counts = {2003: 3, 2004: 5, 2005: 0, 2006: 67, 2007: 14, 2008: 17}
    rows = 45000
    con = _db({"targets": {**{s: 0.25 for s in SEASONS},
                           **{s: c / rows for s, c in counts.items()},
                           "nonnull_when_empty": True}}, tmp_path)
    found = {r[0]: r for r in survey.silent_zeros(con)}
    assert "targets" in found, "the sweep missed its own worked example"
    assert found["targets"][2] == [(2003, 2008)]
    assert found["targets"][5] == sum(counts.values())   # rows, quoted not trusted


def test_a_column_that_is_NULL_in_the_run_is_not_a_silent_zero(tmp_path):
    """A null cliff is loud and belongs to `anomalies`. Reporting it here too
    would bury the three columns that actually need this sweep."""
    con = _db({"air_yards": {s: (0.0 if s < 2006 else 0.37) for s in SEASONS}},
              tmp_path)
    assert "air_yards" not in {r[0] for r in survey.silent_zeros(con)}


def test_a_single_zero_season_is_not_a_run(tmp_path):
    con = _db({"x": {**{s: 0.1 for s in SEASONS}, 2010: 0.0,
                     "nonnull_when_empty": True}}, tmp_path)
    assert "x" not in {r[0] for r in survey.silent_zeros(con)}


@needs_scan
def test_the_real_silent_zero_class_is_the_fifteen_named_in_the_report():
    """Section 2.9. Nine seasons of `def_tackles_for_loss` is the longest run
    in the archive; `targets` is the one that shipped.

    Was 13 before NaN stopped counting as informative: `target_share` and
    `wopr` join the class at 2007-2008, where they are not wholly NaN but are
    effectively zero."""
    con = paths.connect(read_only=True)
    pbp = {r[0]: r[2] for r in survey.silent_zeros(con, dataset="pbp")}
    wk = {r[0]: r[2] for r in survey.silent_zeros(con, dataset="weekly_stats")}
    assert set(pbp) == {"no_huddle", "qb_hit", "special_teams_play"}
    assert pbp["qb_hit"] == [(2003, 2005)]
    assert wk["targets"] == [(2003, 2008)]
    assert wk["def_tackles_for_loss"] == [(2003, 2011)]
    assert len(wk) == 12
    # snap_counts and participation are clean, and that is a result, not a skip
    assert survey.silent_zeros(con, dataset="snap_counts") == []
    assert survey.silent_zeros(con, dataset="participation") == []


# =============================================================================
# the shape an edge-scanning detector cannot see (track C, 2026-09-17)
# =============================================================================

def test_a_column_dead_at_BOTH_ends_with_a_populated_middle_is_found(tmp_path):
    """Track C's `qb_hurry`: zero 2004-2006 AND 2014-2020 around a populated
    middle. Every cliff detector written here scans from an edge, so this is
    the shape that could have been invisible to all of them. `anomalies` scores
    each season against the column's own reference and never asks where the
    season sits, which SHOULD catch it - this is the check rather than the
    reasoning."""
    dead = set(range(1999, 2007)) | set(range(2014, 2027))
    con = _db({"qb_hurry": {s: (0.0 if s in dead else 0.08) for s in SEASONS}},
              tmp_path)
    d = survey.anomalies(con)["qb_hurry"]
    assert 1999 in d["absent"] and 2026 in d["absent"]
    assert 2007 not in d["absent"] and 2013 not in d["absent"]
    found = {r[0]: r for r in survey.dead_ends(con)}
    assert "qb_hurry" in found
    col, first, last, _ref, n_covered = found["qb_hurry"]
    assert first == (1999, 2006) and last == (2014, 2026)
    assert n_covered == 7


def test_a_leading_only_cliff_is_not_reported_as_a_dead_end(tmp_path):
    """`air_yards` is dead at one end. Reporting it here would bury the shape
    this detector exists for under thirty-six that the cliff report covers."""
    con = _db({"air_yards": {s: (0.0 if s < 2006 else 0.37) for s in SEASONS}},
              tmp_path)
    assert "air_yards" not in {r[0] for r in survey.dead_ends(con)}


def test_a_column_that_is_never_populated_is_not_a_dead_end(tmp_path):
    con = _db({"nothing": {s: 0.0 for s in SEASONS}}, tmp_path)
    assert survey.dead_ends(con) == []


@needs_scan
def test_no_nfl_column_in_any_feed_has_the_dead_ends_shape():
    """The answer to track C's question, as a query rather than a claim.

    The one candidate was `stats_player_week.target_share`, and it was an
    artifact of this survey's own NaN handling, not of the data - see
    `test_a_column_of_pure_nan_is_not_informative`."""
    con = paths.connect(read_only=True)
    for dataset in survey.DATASETS:
        assert survey.dead_ends(con, dataset=dataset) == [], dataset


# =============================================================================
# NaN is not null and is not information
# =============================================================================

@needs_scan
def test_a_column_of_pure_nan_is_not_informative():
    """`target_share` is targets over zero targets for 2003-2008. Counted with
    `fill_null(0) != 0` it read 99.7% informative and INVERTED the verdict -
    the survey called the other twenty-two seasons the anomaly."""
    con = paths.connect(read_only=True)
    rows = {r[0]: r for r in survey.coverage(con, "target_share", "weekly_stats")}
    season, n, nonnull, informative, _dn, _dt, nan = rows[2005]
    assert nonnull == 0 and informative == 0
    assert nan == n, "every 2005 row should be NaN"
    assert survey.anomalies(con, dataset="weekly_stats")["target_share"]["absent"]


@needs_scan
def test_the_silent_zero_sweep_still_names_its_worked_example():
    con = paths.connect(read_only=True)
    wk = {r[0]: r[2] for r in survey.silent_zeros(con, dataset="weekly_stats")}
    assert wk["targets"] == [(2003, 2008)]
    assert wk["def_tackles_for_loss"] == [(2003, 2011)]


# =============================================================================
# the seam track C asked for
# =============================================================================

def test_scan_files_takes_files_and_a_connection_and_does_not_enumerate(tmp_path):
    """Track C could not reuse the scan because it found its own files through
    the nflverse registry and the NFL mirror layout. The measurement has
    nothing to do with where the file came from."""
    import sqlite3
    import polars as pl
    path = tmp_path / "anything.parquet"
    pl.DataFrame({"a": [1, 0, 2], "b": ["x", "", "y"]}).write_parquet(path)
    con = sqlite3.connect(str(tmp_path / "other.db"))
    stats = survey.scan_files(
        con, [("some_other_sport", 2031, str(path), "2026-09-17")],
        verbose=False)
    assert stats["seasons"] == 1 and stats["datasets"] == 1
    got = survey.coverage(con, "a", dataset="some_other_sport")
    assert got[0][:4] == (2031, 3, 3, 2)          # season, rows, nonnull, informative


def test_scan_files_refuses_an_empty_list(tmp_path):
    import sqlite3
    con = sqlite3.connect(str(tmp_path / "e.db"))
    with pytest.raises(SystemExit, match="not a scan"):
        survey.scan_files(con, [], verbose=False)


def test_run_scan_is_the_nfl_wrapper_and_enumeration_is_separate():
    """`nflverse_files` enumerates and returns tuples; `scan_files` measures."""
    import inspect
    src = inspect.getsource(survey.run_scan)
    assert "scan_files" in src and "nflverse_files" in src
    assert "scan_parquet" not in src, "run_scan must not do the measuring"


# =============================================================================
# a reader must fail, not double-count, across a schema change
# =============================================================================

def test_profiles_refuses_to_double_count_when_a_key_column_is_unfiltered(tmp_path):
    """The exact failure track C hit: `condition` joined the primary key, older
    code did not filter on it, and every column-season came back twice. The
    query succeeded and the report printed wrong runs."""
    con = _db({"air_yards": {s: 0.37 for s in SEASONS}}, tmp_path)
    con.execute("INSERT INTO f_pbp_columns VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                ("pbp", 2015, "air_yards", "pass_attempt", "Float64",
                 45000, 40000, 40000, 0, 2, "2026-09-09", 0))
    con.commit()
    # the conditional row is a different `condition`, so the default read is
    # unaffected - that is the fix working
    assert len(survey.profiles(con)["air_yards"][1]) == len(SEASONS)
    # and a reader that ignores the key is refused rather than doubled
    rows = con.execute("SELECT column_name, dtype, season, rows, nonnull, "
                       "informative FROM f_pbp_columns WHERE dataset='pbp' "
                       "ORDER BY column_name, season").fetchall()
    assert len(rows) == len(SEASONS) + 1


def test_a_database_written_at_another_schema_version_is_refused(tmp_path):
    con = _db({"x": {s: 0.1 for s in SEASONS}}, tmp_path)
    con.execute("UPDATE f_survey_meta SET value='1' WHERE key='schema_version'")
    con.commit()
    with pytest.raises(RuntimeError, match="schema_version"):
        survey.profiles(con)
