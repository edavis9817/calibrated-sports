"""The crosswalk merge: a release that OMITS an id must not erase it.

Run: pytest -q tests/test_crosswalk_merge.py

`build_crosswalk` is the sole production writer of `player_xwalk`, the identity
table every join in the project keys on - and until 2026-09-19 it had NO TEST
AT ALL. It wrote with `INSERT OR REPLACE`, a whole-row write, so a release that
stopped publishing a column silently nulled it for every player. Measured on
the real archive: the 09-19 nflverse players release omitted `pfr_id` for 75
players and `espn_id` for 38 that the 09-17 release carried, and the store
unsaid all 113 values. Nothing recorded that they had ever been known.

The damage is a SILENT DROP, not a visible gap. `pfr_id` is the join key into
`nfl_snap_counts`, so `pfr_id IS NULL` matches no rows and those players fall
out of `settle_outcomes` and three research scripts as a shortfall nobody
counts - the same shape as the missing-stat-row defect that inflated the
realized over rate.

Every test here is written to discriminate: each one is paired with the other
answer, because a preserve rule that also preserved a RESTATEMENT would be a
different and equally wrong bug, and a test that only ever sees nulls cannot
tell the two apart.
"""
import io
import sqlite3

import polars as pl
import pytest

import config
import store
from venues.mapping import build_crosswalk

# Written explicitly so a column of all-None does not come back as polars' Null
# dtype and change what the reader sees.
SCHEMA = {"gsis_id": pl.Utf8, "display_name": pl.Utf8, "first_name": pl.Utf8,
          "last_name": pl.Utf8, "position": pl.Utf8, "latest_team": pl.Utf8,
          "last_season": pl.Int64, "status": pl.Utf8, "pfr_id": pl.Utf8,
          "espn_id": pl.Utf8, "yahoo_id": pl.Utf8, "pff_id": pl.Utf8}


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setattr(config, "RAW_DIR", str(tmp_path / "raw"))
    store.init_db()
    return tmp_path


def player(**over):
    base = {"gsis_id": "00-A", "display_name": "Wide One", "first_name": "Wide",
            "last_name": "One", "position": "WR", "latest_team": "BUF",
            "last_season": 2026, "status": "ACT", "pfr_id": "WideWi00",
            "espn_id": "1234", "yahoo_id": None, "pff_id": "9001"}
    base.update(over)
    return base


def release(*players):
    """One nflverse players release, as the parquet bytes build_crosswalk reads."""
    buf = io.BytesIO()
    pl.DataFrame(list(players), schema=SCHEMA).write_parquet(buf)
    return buf.getvalue()


def row(gsis="00-A"):
    c = sqlite3.connect(config.DB_PATH)
    try:
        return c.execute(
            "SELECT pfr_id, espn_id, pff_id, position, status, last_team "
            "FROM player_xwalk WHERE gsis_id=?", (gsis,)).fetchone()
    finally:
        c.close()


# ------------------------------------------------- the defect, and the fix

def test_a_release_that_OMITS_an_id_does_not_erase_it(db):
    """The whole unit in one assertion."""
    build_crosswalk(release(player()))
    assert row()[:3] == ("WideWi00", "1234", "9001")

    build_crosswalk(release(player(pfr_id=None, espn_id=None, pff_id=None)))
    assert row()[:3] == ("WideWi00", "1234", "9001"), (
        "a release that went silent on the ids erased them")


def test_the_OTHER_answer_a_restated_id_still_wins(db):
    """Preserve must mean "not nulled", never "frozen". A source correcting an
    id is the correction path for a fact (invariant 6); refusing a restatement
    would be a different bug with the same green test if only nulls were ever
    tried."""
    build_crosswalk(release(player()))
    build_crosswalk(release(player(pfr_id="Corrct00", espn_id="9999")))
    assert row()[:3] == ("Corrct00", "9999", "9001")


def test_a_genuinely_new_id_is_accepted_over_a_null(db):
    build_crosswalk(release(player(pfr_id=None, espn_id=None, pff_id=None)))
    assert row()[:3] == (None, None, None)
    build_crosswalk(release(player()))
    assert row()[:3] == ("WideWi00", "1234", "9001")


def test_preserve_is_SCOPED_to_the_external_ids(db):
    """Not a blanket never-null. A player's status and team legitimately go
    empty, and publishing a stale team because the feed went quiet would be the
    "never approximate a historical field from a current one" defect."""
    build_crosswalk(release(player()))
    build_crosswalk(release(player(status=None, latest_team=None, position=None)))
    assert row()[3:] == (None, None, None), "non-id columns must take the new value"
    assert row()[:3] == ("WideWi00", "1234", "9001"), "ids must survive it"


def test_re_ingesting_the_omitting_release_leaves_recovery_intact(db):
    """Recovery alone lasts until the next ingest. This is the half that makes
    it durable: the omitting release runs twice more and changes nothing."""
    build_crosswalk(release(player()))
    for _ in range(3):
        build_crosswalk(release(player(pfr_id=None, espn_id=None, pff_id=None)))
    assert row()[:3] == ("WideWi00", "1234", "9001")


def test_replace_rows_WOULD_have_nulled_it(db):
    """The defect, demonstrated, so the tests above are known to discriminate.

    If `replace_rows` also preserved, every assertion here would pass against
    the unfixed code and this file would be decorative.
    """
    xw = ("gsis_id", "display_name", "pfr_id", "espn_id", "pff_id")
    store.replace_rows("player_xwalk", xw,
                       [("00-A", "Wide One", "WideWi00", "1234", "9001")])
    assert row()[:3] == ("WideWi00", "1234", "9001")
    store.replace_rows("player_xwalk", xw,
                       [("00-A", "Wide One", None, None, None)])
    assert row()[:3] == (None, None, None), (
        "if this passes, INSERT OR REPLACE no longer nulls and the fix is untested")


# --------------------------------------------------------- the primitive

def test_upsert_preserving_refuses_a_key_it_cannot_find(db):
    with pytest.raises(ValueError, match="not in cols"):
        store.upsert_preserving("player_xwalk", ("gsis_id",), [("00-A",)],
                                ("nope",), ())


def test_upsert_preserving_refuses_to_preserve_a_key(db):
    """Preserving the conflict target is incoherent - it is the value that
    MATCHED. Refusing says so rather than generating SQL that silently does
    nothing."""
    with pytest.raises(ValueError, match="cannot be preserved"):
        store.upsert_preserving("player_xwalk", ("gsis_id", "pfr_id"),
                                [("00-A", "x")], ("gsis_id",), ("gsis_id",))


def test_upsert_preserving_is_a_no_op_on_no_rows(db):
    assert store.upsert_preserving("player_xwalk", ("gsis_id",), [],
                                   ("gsis_id",), ()) == 0


def test_aliases_still_land(db):
    """The alias write is deliberately unchanged; assert it did not regress."""
    build_crosswalk(release(player()))
    c = sqlite3.connect(config.DB_PATH)
    try:
        n = c.execute("SELECT COUNT(*) FROM player_alias WHERE gsis_id='00-A'").fetchone()[0]
    finally:
        c.close()
    assert n > 0
