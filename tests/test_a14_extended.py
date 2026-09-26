"""a-14: defence, special teams and identity on player pages - the extended profile.

Run: pytest -q tests/test_a14_extended.py

Track B's A-B2/A-B3/A-B4 (docs/track-a-requests.md). What is pinned here, and
why each one is a test rather than a comment:

  * THE GATE. The weekly refresh runs this exporter from whatever branch its
    clone has checked out and uploads the result. So "merged but not published"
    must be a property of the code: `--extended` without `--dest` refuses.
  * NULL IS NOT ZERO. A stored NULL in a new column means the row predates the
    column (nflverse publishes zero nulls in these 31 columns across 478,384
    rows). Publishing it as 0 is a page saying a kicker attempted nothing in
    1999 - the failure the brief names.
  * pt_return_tds IS A PUNTING STAT. It was summed into return_tds, crediting
    punters with the touchdowns their punts ALLOWED.
  * THE JERSEY IS NOT ALWAYS A NUMBER. '69B' is in the 2004 roster file.

Every rule is shown producing BOTH answers, because a rule that can only give
the answer being tested is decoration.
"""
import io
import sqlite3

import polars as pl
import pytest

import config
import store
from jobs import export_web as E
from jobs import ingest_nflverse as I
from venues.mapping import build_crosswalk


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setattr(config, "RAW_DIR", str(tmp_path / "raw"))
    monkeypatch.setattr(E, "SLUG_DIR", str(tmp_path / "slugs"))   # never the committed registry
    store.init_db()
    return tmp_path


def parquet(rows, schema=None):
    buf = io.BytesIO()
    pl.DataFrame(rows, schema=schema).write_parquet(buf)
    return buf.getvalue()


# ------------------------------------------------------------------ the gate

def test_extended_without_dest_is_refused():
    with pytest.raises(SystemExit):
        E.main(["--extended"])


def test_extended_with_upload_is_refused_even_with_dest(tmp_path):
    with pytest.raises(SystemExit):
        E.main(["--extended", "--dest", str(tmp_path / "out"), "--upload"])


def test_extended_refuses_the_components_part(db):
    with pytest.raises(E.ConfigError):
        E.export(only=["components"], dest=str(db / "out"), extended=True)


def test_the_staged_registry_is_a_copy_beside_the_tree_never_the_committed_one(db):
    committed = E.slug_registry_path()
    import os
    os.makedirs(os.path.dirname(committed), exist_ok=True)
    with open(committed, "w", encoding="utf-8") as f:
        f.write('{"00-A": "wide-one"}')
    dest = db / "stage" / "out"
    path = E.staged_registry(str(dest))
    assert os.path.abspath(path) != os.path.abspath(committed)
    assert not os.path.abspath(path).startswith(os.path.abspath(dest) + os.sep)
    assert open(path, encoding="utf-8").read() == '{"00-A": "wide-one"}'


# ------------------------------------------------------- the ingest, return TDs

# Until a-16 the core columns were REQUIRED here: normalize_weekly_stats selected
# them without an alias, so two absent ones collided as `literal` and the
# normalizer raised. `_col` now aliases its null literal; see
# test_two_absent_core_columns_normalize_to_null below.
CORE =("receptions", "targets", "receiving_yards", "receiving_tds", "target_share",
        "carries", "rushing_yards", "rushing_tds", "attempts", "completions",
        "passing_yards", "passing_tds", "fantasy_points_ppr")
WEEKLY_SCHEMA = {"player_id": pl.Utf8, "season": pl.Int32, "week": pl.Int32,
                 "season_type": pl.Utf8, "position": pl.Utf8,
                 **{c: pl.Float64 for c in CORE},
                 "special_teams_tds": pl.Int32, "pt_return_tds": pl.Int32,
                 "fg_att": pl.Int32, "fg_made": pl.Int32, "kickoff_returns": pl.Int32}


def weekly(**over):
    base = {"player_id": "00-P", "season": 2025, "week": 1, "season_type": "REG",
            "position": "P", **{c: 0.0 for c in CORE}, "special_teams_tds": 0,
            "pt_return_tds": 1, "fg_att": 0, "fg_made": 0, "kickoff_returns": 0}
    base.update(over)
    return base


def normalized(*rows):
    _t, cols, out = I.normalize_weekly_stats(parquet(list(rows), WEEKLY_SCHEMA), "v1")
    return [dict(zip(cols, r)) for r in out]


def test_a_punters_allowed_return_td_is_not_his_return_td():
    (row,) = normalized(weekly())
    assert row["return_tds"] == 0


def test_a_returners_touchdown_still_counts():
    """The other answer: special_teams_tds is where returners' TDs live."""
    (row,) = normalized(weekly(player_id="00-W", position="WR", special_teams_tds=1,
                               pt_return_tds=0))
    assert row["return_tds"] == 1


def test_kicking_and_return_columns_flow_through_and_absent_ones_are_null():
    (row,) = normalized(weekly(player_id="00-K", position="K", fg_att=4, fg_made=3,
                               pt_return_tds=0, kickoff_returns=0))
    assert (row["fg_att"], row["fg_made"], row["kickoff_returns"]) == (4.0, 3.0, 0.0)
    # Not in this frame at all -> NULL, never 0. `_col` supplies a null literal.
    assert row["pat_att"] is None


def test_two_absent_core_columns_normalize_to_null():
    """a-16: a release missing two core columns must still normalize. Before the
    fix both became `literal` and the select raised DuplicateError."""
    gone = ("receptions", "target_share")
    schema = {k: v for k, v in WEEKLY_SCHEMA.items() if k not in gone}
    row = {k: v for k, v in weekly(player_id="00-W", position="WR").items() if k not in gone}
    _t, cols, out = I.normalize_weekly_stats(parquet([row], schema), "v1")
    (r,) = [dict(zip(cols, x)) for x in out]
    assert (r["receptions"], r["target_share"]) == (None, None)
    # The other answer: a present column still carries its value, not null.
    assert r["targets"] == 0.0


def test_an_absent_column_with_a_default_takes_the_default_under_its_own_name():
    frame = pl.DataFrame({"x": [1]})
    out = frame.select(I._col(frame, "a"), I._col(frame, "b", 0))
    assert out.columns == ["a", "b"] and out.row(0) == (None, 0)


# ------------------------------------------------------------- the jersey

def test_roster_jersey_is_stored_verbatim_as_text(db):
    schema = {"gsis_id": pl.Utf8, "season": pl.Int32, "week": pl.Int32, "game_type": pl.Utf8,
              "team": pl.Utf8, "position": pl.Utf8, "jersey_number": pl.Utf8, "status": pl.Utf8}
    rows = [{"gsis_id": "00-A", "season": 2004, "week": 1, "game_type": "REG", "team": "BUF",
             "position": "OL", "jersey_number": "69B", "status": "ACT"},
            {"gsis_id": "00-B", "season": 2004, "week": 1, "game_type": "REG", "team": "BUF",
             "position": "WR", "jersey_number": "17", "status": "ACT"}]
    table, cols, out = I.normalize_weekly_rosters(parquet(rows, schema), "v1")
    got = {r[1]: r[cols.index("jersey_number")] for r in out}
    assert table == "nfl_roster_week" and got == {"00-A": "69B", "00-B": "17"}


def _roster(db, rows):
    c = sqlite3.connect(config.DB_PATH)
    c.executemany("INSERT INTO nfl_roster_week (gsis_id, season, week, game_type, team, "
                  "data_version, jersey_number, source, ingested_ts) "
                  "VALUES (?,?,?,'REG',?,?,?,'t',0)", rows)
    c.commit()
    c.close()


def test_the_seasons_last_week_decides_the_number(db):
    _roster(db, [("00-A", 2025, 3, "BUF", "v1", "13"), ("00-A", 2025, 9, "KC", "v1", "81"),
                 ("00-A", 2026, 1, "KC", "v1", "0"), ("00-A", 2026, 2, "KC", "v1", "00")])
    ro = sqlite3.connect(f"file:{config.DB_PATH}?mode=ro", uri=True)
    j = E.load_jerseys(ro)
    ro.close()
    assert j[("00-A", 2025)] == "81"            # traded: the number he finished in
    assert j[("00-A", 2026)] == "00"            # a string - 0 and 00 stay different


def test_a_jersey_that_is_not_a_number_publishes_null_not_a_guess(db):
    _roster(db, [("00-A", 2004, 1, "BUF", "v1", "69B"), ("00-B", 2004, 1, "BUF", "v1", "69")])
    ro = sqlite3.connect(f"file:{config.DB_PATH}?mode=ro", uri=True)
    j = E.load_jerseys(ro)
    ro.close()
    assert j[("00-A", 2004)] is None and j[("00-B", 2004)] == "69"


def test_a_store_without_rosters_yields_no_jerseys_rather_than_failing(tmp_path):
    c = sqlite3.connect(tmp_path / "old.db")
    c.execute("CREATE TABLE unrelated (x)")
    assert E.load_jerseys(c) == {}
    c.close()


# ------------------------------------------------------------- birth date

XW_SCHEMA = {"gsis_id": pl.Utf8, "display_name": pl.Utf8, "first_name": pl.Utf8,
             "last_name": pl.Utf8, "position": pl.Utf8, "latest_team": pl.Utf8,
             "last_season": pl.Int64, "status": pl.Utf8, "pfr_id": pl.Utf8,
             "espn_id": pl.Utf8, "yahoo_id": pl.Utf8, "pff_id": pl.Utf8,
             "birth_date": pl.Utf8}


def xw(bd):
    return parquet([{"gsis_id": "00-A", "display_name": "Wide One", "first_name": "Wide",
                     "last_name": "One", "position": "WR", "latest_team": "BUF",
                     "last_season": 2026, "status": "ACT", "pfr_id": None, "espn_id": None,
                     "yahoo_id": None, "pff_id": None, "birth_date": bd}], XW_SCHEMA)


def birth(db):
    c = sqlite3.connect(config.DB_PATH)
    try:
        return c.execute("SELECT birth_date FROM player_xwalk WHERE gsis_id='00-A'").fetchone()[0]
    finally:
        c.close()


def test_a_release_that_omits_birth_date_does_not_erase_it(db):
    build_crosswalk(xw("1998-03-10"))
    build_crosswalk(xw(None))
    assert birth(db) == "1998-03-10"


def test_a_restated_birth_date_still_wins(db):
    build_crosswalk(xw("1998-03-10"))
    build_crosswalk(xw("1998-03-11"))
    assert birth(db) == "1998-03-11"


# ------------------------------------------------------------ null is not zero

def test_an_uningested_extended_column_publishes_null_not_zero():
    r = {"season": 1999, "fg_att": None}
    assert E._ext_count(r, "fg_att") is None
    assert E._count(r, "targets") == 0          # the default reader's rule, for contrast


def test_a_recorded_zero_is_zero():
    assert E._ext_count({"season": 1999, "fg_att": 0.0}, "fg_att") == 0


def test_a_season_the_source_did_not_collect_is_null_even_when_stored_as_zero():
    assert E._ext_count({"season": 2005, "def_tackles_for_loss": 0.0}, "def_tackles_for_loss") is None
    assert E._ext_count({"season": 2013, "def_tackles_for_loss": 0.0}, "def_tackles_for_loss") == 0


def p(season, **stats):
    return {"season": season, "stats": stats}


def test_a_career_total_over_a_null_period_is_null():
    periods = [p(1999, fg_att=None), p(2000, fg_att=30)]
    assert E._totals(periods, ext=True)["fg_att"] is None
    assert E._totals([p(2000, fg_att=30), p(2001, fg_att=28)], ext=True)["fg_att"] == 58


def test_default_totals_ignore_extended_keys():
    assert "fg_att" not in E._totals([p(2000, fg_att=30)])


# ------------------------------------------------------------------- scope

def wk(gsis, **cols):
    base = {"gsis_id": gsis, "season_type": "REG", "targets": 0, "carries": 0, "attempts": 0}
    base.update(cols)
    return base


def test_the_extended_scope_admits_a_kicker_and_a_linebacker_the_default_does_not():
    weeks = [wk("00-K", fg_att=3), wk("00-L", def_tackles_solo=5), wk("00-W", targets=4),
             wk("00-O")]
    assert E.player_scope(weeks) == {"00-W"}
    assert E.player_scope(weeks, extended=True) == {"00-K", "00-L", "00-W"}


def test_a_player_with_nothing_to_show_stays_out_of_the_extended_scope():
    assert E.player_scope([wk("00-O", fg_att=0, def_sacks=0.0)], extended=True) == set()


# ------------------------------------------------------------ played zero

def game(season, week, home, away):
    return {"game_id": f"{season}_{week:02d}_{away}_{home}", "season": season, "week": week,
            "game_type": "REG", "gameday": f"{season}-09-0{week}", "home_team": home,
            "away_team": away, "home_score": 20, "away_score": 17}


def test_a_defence_only_week_is_emitted_under_extended_and_refused_by_default():
    g = game(2024, 5, "BUF", "MIA")
    idx = {(2024, 5, "BUF"): g, (2024, 5, "MIA"): g}
    snaps = {("00-L", 2024, 5): ("BUF", 0, 58, 0.0, g["game_id"])}
    assert E.played_zero_periods("00-L", [], snaps, idx) == []
    ext = E.ExtendedInputs({("00-L", g["game_id"]): (58, 0.91, 12)}, {})
    (row,) = E.played_zero_periods("00-L", [], snaps, idx, ext=ext)
    s = row["stats"]
    assert (s["snaps"], s["defense_snaps"], s["st_snaps"]) == (0, 58, 12)
    assert s["def_tkl_solo"] == 0 and s["fg_att"] == 0      # recorded zeros, not nulls


def test_a_week_he_sat_out_is_still_never_emitted_under_extended():
    g = game(2024, 5, "BUF", "MIA")
    idx = {(2024, 5, "BUF"): g}
    snaps = {("00-L", 2024, 5): ("BUF", 0, 0, 0.0, g["game_id"])}
    ext = E.ExtendedInputs({("00-L", g["game_id"]): (0, 0.0, 0)}, {})
    assert E.played_zero_periods("00-L", [], snaps, idx, ext=ext) == []


# ------------------------------------------------------ definitions and contract

def test_every_extended_period_key_is_defined():
    defs = {**E.STAT_DEFINITIONS, **E.EXT_STAT_DEFINITIONS}
    assert set(E.EXT_PERIOD_KEYS) <= set(defs)


def test_no_extended_definition_is_in_the_default_manifest():
    """The default manifest must not move until the profile is published."""
    assert not set(E.EXT_STAT_DEFINITIONS) & set(E.STAT_DEFINITIONS)
    assert E.MARKET_DEFINITIONS["sacks"]["stat"] is None
    assert E.EXT_MARKET_DEFINITIONS["sacks"]["stat"] == "def_sacks"


def test_the_contract_accepts_the_identity_fields_and_refuses_a_bad_jersey():
    v = E.contract_validators()["player_summary"]
    base = {"schema_version": E.SCHEMA_VERSION, "generated_at": "2026-09-23T00:00:00Z",
            "kind": "player_summary", "sport": "nfl",
            "identity": {"id": "00-A", "slug": "a", "name": "A", "position": "K", "team": "BUF",
                         "ids": {}, "aliases": [], "headshot_url": None,
                         "jersey_number": "04", "birth_date": "1998-03-10"},
            "seasons": [{"season": 2025, "teams": ["BUF"], "games": 1,
                         "key": "nfl/players/00-A/2025.json", "jersey_number": None}],
            "season_totals": [], "career": {"season_type": "REG", "games": 1, "stats": {}},
            "market": None, "prop_history": None}
    assert not list(v.iter_errors(base))
    base["identity"]["jersey_number"] = "69B"
    assert list(v.iter_errors(base))
