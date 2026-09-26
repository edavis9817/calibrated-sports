"""a-37 (audit N-03): one line per game, carrying where it came from and when it was read.

Run: pytest -q tests/test_game_lines.py

What is pinned:

  * THE SAME FUNCTION FEEDS BOTH FILES. The manifest's current.fixtures[] and the live
    snapshot's games carry identical provenance for identical store rows - asserted on
    one in-memory store read through each producer's own query.
  * PREVIOUS MEANS A DIFFERENT LINE, NOT AN OLDER ROW. A version that repeats the
    current line, or carries no line at all (nflverse blanks unposted weeks), is skipped.
    Shown producing each answer: a move, a move back, no move, and a blank in between.
  * THE CONTRACT REFUSES A LINE WITHOUT ITS READ TIME, on both kinds.
"""
import sqlite3

import pytest
from jsonschema import Draft202012Validator

from jobs import export_web as E
from jobs import game_lines as G
from jobs import live_snapshot as S


def rows(gid, *versions):
    """versions: (data_version, spread, total, ingested_ts)"""
    return [(gid, dv, sp, tot, ts) for dv, sp, tot, ts in versions]


def test_a_single_read_has_a_read_time_and_no_previous_line():
    got = G.provenance(rows("g", ("2026-09-20", 4.5, 43.5, 1_758_400_000.9)))["g"]
    assert got == {"line_source": "nflverse.schedule", "line_read_at": "2025-09-20T20:26:40Z",
                   "line_previous": None}


def test_a_moved_line_names_what_it_moved_from_and_when_that_was_read():
    got = G.provenance(rows("g", ("2026-09-20", 4.5, 43.5, 100.0),
                            ("2026-09-21", 4.5, 43.5, 200.0),
                            ("2026-09-22", 5.5, 42.5, 300.0)))["g"]
    assert got["line_read_at"] == G.iso(300.0)
    # The LAST read that carried the old line, not the first.
    assert got["line_previous"] == {"spread": 4.5, "total": 43.5, "read_at": G.iso(200.0)}


def test_a_repeat_of_the_current_line_is_not_a_move():
    got = G.provenance(rows("g", ("2026-09-20", 5.5, 42.5, 100.0),
                            ("2026-09-21", 5.5, 42.5, 200.0)))["g"]
    assert got["line_previous"] is None


def test_a_blank_week_is_skipped_so_the_move_is_measured_across_it():
    # Measured shape: 2026_04_ARI_NYG read 7.0/45.5 to 09-12, null 09-13..09-21, 2.5/43.5 after.
    got = G.provenance(rows("g", ("2026-09-12", 7.0, 45.5, 100.0),
                            ("2026-09-13", None, None, 200.0),
                            ("2026-09-22", 2.5, 43.5, 300.0)))["g"]
    assert got["line_previous"] == {"spread": 7.0, "total": 45.5, "read_at": G.iso(100.0)}


def test_a_line_that_moves_back_reports_the_line_in_between():
    got = G.provenance(rows("g", ("2026-09-20", 4.5, 43.5, 100.0),
                            ("2026-09-21", 5.5, 43.5, 200.0),
                            ("2026-09-22", 4.5, 43.5, 300.0)))["g"]
    assert got["line_previous"] == {"spread": 5.5, "total": 43.5, "read_at": G.iso(200.0)}


def test_a_line_that_disappears_keeps_the_last_one_as_previous():
    got = G.provenance(rows("g", ("2026-09-20", 4.5, 43.5, 100.0),
                            ("2026-09-21", None, None, 200.0)))["g"]
    assert got["line_read_at"] == G.iso(200.0)
    assert got["line_previous"] == {"spread": 4.5, "total": 43.5, "read_at": G.iso(100.0)}


def test_the_current_line_is_the_newest_version_whatever_the_row_order():
    got = G.provenance(list(reversed(rows("g", ("2026-09-20", 4.5, 43.5, 100.0),
                                          ("2026-09-22", 5.5, 42.5, 300.0)))))["g"]
    assert got["line_read_at"] == G.iso(300.0)
    assert got["line_previous"]["spread"] == 4.5


# --------------------------------------------- both producers, one store

@pytest.fixture
def games_db(tmp_path):
    db = tmp_path / "g.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE nfl_games (sport TEXT, game_id TEXT, data_version TEXT, season INT, "
                "week INT, game_type TEXT, kickoff_ts REAL, home_team TEXT, away_team TEXT, "
                "home_score REAL, away_score REAL, spread_line REAL, total_line REAL, "
                "home_moneyline REAL, away_moneyline REAL, ingested_ts REAL)")
    ko = S.time.time() + 3 * 86400
    con.executemany("INSERT INTO nfl_games VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", [
        ("nfl", "2026_04_ATL_GB", "2026-09-22", 2026, 4, "REG", ko, "GB", "ATL", None, None,
         4.5, 43.5, -200, 170, 1_758_500_000.0),
        ("nfl", "2026_04_ATL_GB", "2026-09-25", 2026, 4, "REG", ko, "GB", "ATL", None, None,
         5.5, 42.5, -230, 190, 1_758_800_000.0)])
    con.commit()
    con.close()
    return str(db)


def test_the_fixture_and_the_live_game_carry_identical_provenance(games_db):
    live = S.load_schedule(games_db)
    (game,) = live
    con = sqlite3.connect(f"file:{games_db}?mode=ro", uri=True)
    try:
        exported = G.provenance(E.load_line_history(con, ["2026_04_ATL_GB"]))
    finally:
        con.close()
    assert game["line_provenance"] == exported["2026_04_ATL_GB"]
    assert exported["2026_04_ATL_GB"]["line_previous"]["spread"] == 4.5
    # And the value beside it is the newest read on both sides.
    assert game["spread_line"] == 5.5


# ---------------------------------------------------------------- the contract

def validator(name):
    c = E.CONTRACT
    return Draft202012Validator({"$ref": "#/$defs/%s" % name, "$defs": c["$defs"]})


def fixture(**over):
    f = {"game_id": "g", "kickoff_ts": 1.0, "home": "gb", "away": "atl", "spread": 5.5,
         "total": 42.5, **G.provenance(rows("g", ("2026-09-20", 4.5, 43.5, 100.0),
                                             ("2026-09-22", 5.5, 42.5, 300.0)))["g"]}
    f.update(over)
    return f


def test_the_contract_accepts_a_stamped_fixture_and_refuses_an_unstamped_one():
    v = validator("Fixture")
    assert not list(v.iter_errors(fixture()))
    unstamped = fixture()
    del unstamped["line_read_at"]
    assert list(v.iter_errors(unstamped))
    assert list(v.iter_errors(fixture(line_source=None)))               # a line always has one
    assert list(v.iter_errors(fixture(line_source="")))
    assert list(v.iter_errors(fixture(line_read_at=300.0)))             # a Timestamp, not a ts


def test_the_line_source_is_an_id_the_sources_file_can_label():
    from jobs import source_registry
    assert G.LINE_SOURCE in source_registry.SOURCES
    assert "nfl" in source_registry.SOURCES[G.LINE_SOURCE]["sports"]


def test_the_live_line_uses_the_same_three_fields():
    fx = validator("Fixture").schema["$defs"]["Fixture"]["properties"]
    live = validator("LiveGame").schema["$defs"]["LiveGame"]["properties"]["line"]["anyOf"][0]
    for k in ("line_source", "line_read_at", "line_previous"):
        assert fx[k] == live["properties"][k] and k in live["required"]
