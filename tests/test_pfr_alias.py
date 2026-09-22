"""pfr_alias (unit f-04): snap-count pfr ids that no player_xwalk row carries.

Run: pytest -q tests/test_pfr_alias.py

What is asserted, and why each half:

* A RELEASE THAT DROPS A FIELD cannot unsay a held pairing - at both layers
  where it could. The archive layer: `release_pairs` still finds a pairing
  that only an OLDER release carried. The store layer: a build that returns
  gsis_id NULL for a held row leaves it standing, and a build that does not
  mention it at all leaves it standing. Each is paired with the other answer:
  a plain `INSERT OR REPLACE` of the same row DOES null it, so the test can
  tell the preserving write from a write that merely happened not to touch it.
* A RESTATEMENT is not silence. A build naming a different gsis for a held
  (pfr, method) is reported as a conflict and not written.
* The join (`store.pfr_gsis`) refuses rather than guesses: methods that
  disagree join nothing, an alias whose gsis already holds a pfr id joins
  nothing, `unresolved` rows join nothing, a snap row outside the asserted
  seasons joins nothing, and player_xwalk wins for any pfr id it carries.
* The selection rules are name-free, and each refusal is driven to fire.
* End to end on a synthetic archive: build -> write -> the two consumers.
"""
import os
import sqlite3

import polars as pl
import pytest

import config
import store
from jobs import build_pfr_alias as b


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setattr(config, "RAW_DIR", str(tmp_path / "raw"))
    monkeypatch.setattr(config, "STORAGE_DIR", str(tmp_path))
    store.init_db()
    return tmp_path


def con():
    return sqlite3.connect(config.DB_PATH)


def alias_rows():
    with con() as c:
        return {(p, m): (g, lo, hi, ev) for p, m, g, lo, hi, ev in c.execute(
            "SELECT pfr_id, method, gsis_id, season_from, season_to, evidence FROM pfr_alias")}


def row(pfr="OrphAn00", method="participation", g="00-0000001", lo=2020, hi=2021,
        ev="checked"):
    return (pfr, method, g, lo, hi, f"{lo}-{hi}", ev, 1.0)


def xwalk(gsis, pfr, name="Some Player"):
    with con() as c:
        c.execute("INSERT INTO player_xwalk (gsis_id, display_name, pfr_id) VALUES (?,?,?)",
                  (gsis, name, pfr))


def snap(pfr, game, season, week=1, team="BUF", off=30.0, dfn=0.0, st=0.0, dv="2026-09-21",
         name="Orphan Player", pos="WR"):
    with con() as c:
        c.execute("INSERT INTO nfl_snap_counts (pfr_player_id, game_id, data_version, season, week, "
                  "player, position, team, offense_snaps, offense_pct, defense_snaps, st_snaps, "
                  "source, ingested_ts) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                  (pfr, game, dv, season, week, name, pos, team, off, 0.5, dfn, st, "t", 1.0))


def write_parquet(path, data, schema=None):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    pl.DataFrame(data, schema=schema).write_parquet(path)


# ------------------------------------------------ the brief's case: a dropped field

def test_archive_layer_older_release_still_evidences_a_dropped_pairing(env):
    raw = env / "raw" / "nflverse"
    s = {"gsis_id": pl.Utf8, "pfr_id": pl.Utf8}
    write_parquet(str(raw / "2026-09-17" / "players.parquet"),
                  {"gsis_id": ["00-0000001"], "pfr_id": ["OrphAn00"]}, s)
    # The next release drops the pfr id for this player.
    write_parquet(str(raw / "2026-09-19" / "players.parquet"),
                  {"gsis_id": ["00-0000001"], "pfr_id": [None]}, s)
    pairs, scanned = b.release_pairs(["OrphAn00"])
    assert scanned == 2
    assert dict(pairs["OrphAn00"]) == {"00-0000001": ["nflverse/2026-09-17/players.parquet"]}

    # The other answer: reading the newest release alone would have found nothing.
    only_new = pl.read_parquet(str(raw / "2026-09-19" / "players.parquet")).filter(
        pl.col("pfr_id") == "OrphAn00")
    assert only_new.height == 0


def test_store_layer_null_gsis_does_not_unsay_a_held_pairing(env):
    b.write([row(ev="first build")])
    n, conflicts = b.write([row(g=None, ev="second build lost the field")])
    assert conflicts == []
    got = alias_rows()[("OrphAn00", "participation")]
    assert got[0] == "00-0000001"
    assert (got[1], got[2]) == (2020, 2021)


def test_store_layer_plain_replace_would_have_nulled_it(env):
    """Discriminates the test above: the same row through INSERT OR REPLACE loses the id."""
    b.write([row()])
    store.replace_rows("pfr_alias", b.COLS, [row(g=None, lo=None, hi=None)], ("pfr_id", "method"))
    assert alias_rows()[("OrphAn00", "participation")][0] is None


def test_a_build_that_does_not_mention_a_held_pairing_leaves_it(env):
    b.write([row()])
    b.write([row(pfr="OtheRr00", g="00-0000002")])
    assert alias_rows()[("OrphAn00", "participation")][0] == "00-0000001"


def test_a_different_gsis_is_a_conflict_not_a_restatement(env):
    b.write([row()])
    n, conflicts = b.write([row(g="00-0000009")])
    assert n == 0 and len(conflicts) == 1 and "00-0000009" in conflicts[0]
    assert alias_rows()[("OrphAn00", "participation")][0] == "00-0000001"


def test_coverage_widens_and_never_narrows(env):
    b.write([row(lo=2019, hi=2021)])
    b.write([row(lo=2020, hi=2020)])          # a build that checked fewer seasons
    assert alias_rows()[("OrphAn00", "participation")][1:3] == (2019, 2021)
    b.write([row(lo=2019, hi=2023)])          # and one that checked more
    assert alias_rows()[("OrphAn00", "participation")][1:3] == (2019, 2023)


# ------------------------------------------------------------------- the join

def joined(season=2020, pfr="OrphAn00"):
    with con() as c:
        pj = store.pfr_gsis(c)
        return [g for (g,) in c.execute(
            f"SELECT x.gsis_id FROM nfl_snap_counts s {pj.on()} "
            "WHERE s.pfr_player_id=? AND s.season=?", (pfr, season))]


def test_join_uses_alias_inside_coverage_only(env):
    snap("OrphAn00", "g2020", 2020)
    snap("OrphAn00", "g2023", 2023)
    assert joined(2020) == [] and joined(2023) == []
    b.write([row(lo=2020, hi=2021)])
    assert joined(2020) == ["00-0000001"]
    assert joined(2023) == []


def test_player_xwalk_wins_and_alias_is_ignored_once_upstream_links(env):
    snap("OrphAn00", "g2020", 2020)
    b.write([row(g="00-0000001")])
    xwalk("00-0000005", "OrphAn00")
    assert joined(2020) == ["00-0000005"]


def test_methods_that_disagree_join_nothing(env):
    snap("OrphAn00", "g2020", 2020)
    b.write([row(method="participation", g="00-0000001"),
             row(method="draft_slot", g="00-0000002")])
    assert joined(2020) == []
    # The other answer: agreeing methods join exactly once, not twice.
    b.write([row(method="draft_slot", g="00-0000001", pfr="AgreEe00"),
             row(method="participation", g="00-0000001", pfr="AgreEe00")])
    snap("AgreEe00", "g2020b", 2020)
    assert joined(2020, "AgreEe00") == ["00-0000001"]


def test_alias_to_a_gsis_that_already_holds_a_pfr_id_joins_nothing(env):
    snap("OrphAn00", "g2020", 2020)
    xwalk("00-0000001", "HeldPf00")
    b.write([row(g="00-0000001")])
    assert joined(2020) == []


def test_unresolved_rows_never_join(env):
    snap("OrphAn00", "g2020", 2020)
    b.write([("OrphAn00", "unresolved", None, None, None, None, "nothing found", 1.0)])
    assert joined(2020) == []
    assert alias_rows()[("OrphAn00", "unresolved")][3] == "nothing found"


def test_a_store_without_the_table_joins_through_xwalk_and_says_so(tmp_path):
    c = sqlite3.connect(tmp_path / "old.db")
    c.executescript("CREATE TABLE player_xwalk (gsis_id TEXT, pfr_id TEXT);"
                    "CREATE TABLE nfl_snap_counts (pfr_player_id TEXT, season INT);"
                    "INSERT INTO player_xwalk VALUES ('00-1', 'KnowNn00');"
                    "INSERT INTO nfl_snap_counts VALUES ('KnowNn00', 2020);")
    pj = store.pfr_gsis(c)
    assert "no pfr_alias table" in pj.statement and pj.aliases == 0
    assert c.execute(f"SELECT x.gsis_id FROM nfl_snap_counts s {pj.on()}").fetchall() == [("00-1",)]


def test_pfr_join_refuses_truth_testing(env):
    with con() as c:
        pj = store.pfr_gsis(c)
    with pytest.raises(TypeError):
        bool(pj)


# ------------------------------------------------------ the selection rules

def srow(game, season=2020, week=1, team="BUF", off=30, dfn=0, st=0, name="Orphan Player"):
    return {"game_id": game, "season": season, "week": week, "team": team, "player": name,
            "offense_snaps": off, "defense_snaps": dfn, "st_snaps": st}


ROS = {(2020, 1, "BUF"): {"00-A": "Orphan Player", "00-B": "Other Guy"},
       (2020, 2, "BUF"): {"00-A": "Orphan Player", "00-B": "Other Guy"}}


def test_participation_unique_survivor_pairs():
    counts = {("g1", "BUF"): {"00-A": (29, 0, 0), "00-B": (60, 0, 0)},
              ("g2", "BUF"): {"00-A": (12, 0, 1), "00-B": (60, 0, 0)}}
    rows = [srow("g1", off=30), srow("g2", week=2, off=12)]
    g, ss, detail = b.participation_match(rows, counts, ROS, {}, {})
    assert g == "00-A" and ss == [2020] and "2 of 2" in detail


def test_participation_is_name_free_in_selection():
    """The survivor is chosen by counts; a name that matches the OTHER player cannot choose."""
    counts = {("g1", "BUF"): {"00-A": (29, 0, 0), "00-B": (60, 0, 0)},
              ("g2", "BUF"): {"00-A": (12, 0, 1), "00-B": (60, 0, 0)}}
    ros = {k: {"00-A": "Orphan Player", "00-B": "Orphan Player"} for k in ROS}
    rows = [srow("g1", off=30), srow("g2", week=2, off=12)]
    assert b.participation_match(rows, counts, ros, {}, {})[0] == "00-A"


def test_participation_refuses_two_survivors():
    counts = {("g1", "BUF"): {"00-A": (30, 0, 0), "00-B": (31, 0, 0)},
              ("g2", "BUF"): {"00-A": (12, 0, 0), "00-B": (13, 0, 0)}}
    rows = [srow("g1", off=30), srow("g2", week=2, off=12)]
    g, _, detail = b.participation_match(rows, counts, ROS, {}, {})
    assert g is None and "2 gsis ids" in detail


def test_participation_refuses_below_min_games():
    counts = {("g1", "BUF"): {"00-A": (30, 0, 0), "00-B": (60, 0, 0)}}
    g, _, detail = b.participation_match([srow("g1")], counts, ROS, {}, {}, min_games=2)
    assert g is None and "fewer than the 2" in detail
    assert b.participation_match([srow("g1")], counts, ROS, {}, {}, min_games=1)[0] == "00-A"


def test_participation_refuses_survivor_with_its_own_pfr_id():
    counts = {("g1", "BUF"): {"00-A": (30, 0, 0)}, ("g2", "BUF"): {"00-A": (12, 0, 0)}}
    rows = [srow("g1"), srow("g2", week=2, off=12)]
    g, _, detail = b.participation_match(rows, counts, ROS, {}, {"00-A": "HeldPf00"})
    assert g is None and "HeldPf00" in detail


def test_participation_refuses_survivor_sharing_no_name_token():
    counts = {("g1", "BUF"): {"00-B": (30, 0, 0)}, ("g2", "BUF"): {"00-B": (12, 0, 0)}}
    rows = [srow("g1"), srow("g2", week=2, off=12)]
    g, _, detail = b.participation_match(rows, counts, ROS, {}, {})
    assert g is None and "no name token" in detail


def test_participation_refuses_unrostered_survivor():
    counts = {("g1", "BUF"): {"00-A": (30, 0, 0)}, ("g2", "BUF"): {"00-A": (12, 0, 0)}}
    ros = {(2020, 1, "BUF"): {"00-A": "Orphan Player"}}
    rows = [srow("g1"), srow("g2", week=2, off=12)]
    g, _, detail = b.participation_match(rows, counts, ros, {}, {})
    assert g is None and "rostered for only 1 of 2" in detail


def test_draft_slot_pairs_unique_slot_and_refuses_ambiguity():
    d = {(2008, 138, 5, "ATL", None, "draft.parquet")}
    assert b.draft_slot_match(d, {(2008, 138): {"00-R": "players.parquet"}})[0] == "00-R"
    g, why = b.draft_slot_match(d, {(2008, 138): {"00-R": "p", "00-S": "p"}})
    assert g is None and "2 gsis ids" in why
    two = d | {(2009, 12, 1, "ATL", None, "draft.parquet")}
    g, why = b.draft_slot_match(two, {(2008, 138): {"00-R": "p"}})
    assert g is None and "2 slots" in why
    assert b.draft_slot_match(set(), {})[0] is None


# ------------------------------------------------ end to end on a synthetic archive

def synthetic_archive(raw):
    day = raw / "nflverse" / "2026-09-09"
    write_parquet(str(day / "players.parquet"),
                  {"gsis_id": ["00-K", "00-R", "00-A", "00-B"],
                   "pfr_id": ["KnowNn00", None, None, None],
                   "draft_year": [2015, 2008, None, None], "draft_pick": [10, 138, None, None]},
                  {"gsis_id": pl.Utf8, "pfr_id": pl.Utf8, "draft_year": pl.Int32, "draft_pick": pl.Int32})
    write_parquet(str(day / "draft_picks.parquet"),
                  {"season": [2008], "pick": [138], "round": [5], "team": ["ATL"],
                   "pfr_player_id": ["DrafTt00"], "gsis_id": [None]},
                  {"season": pl.Int32, "pick": pl.Int32, "round": pl.Int32, "team": pl.Utf8,
                   "pfr_player_id": pl.Utf8, "gsis_id": pl.Utf8})
    ros = {"season": [2013, 2020, 2020, 2020, 2020], "week": [1, 1, 2, 1, 2],
           "team": ["ATL", "BUF", "BUF", "BUF", "BUF"],
           "gsis_id": ["00-R", "00-A", "00-A", "00-B", "00-B"],
           "full_name": ["Robert James", "Orphan Player", "Orphan Player", "Other Guy", "Other Guy"],
           "pfr_id": [None] * 5}
    rs = {"season": pl.Int32, "week": pl.Int32, "team": pl.Utf8, "gsis_id": pl.Utf8,
          "full_name": pl.Utf8, "pfr_id": pl.Utf8}
    for season in (2013, 2020):
        sub = pl.DataFrame(ros, schema=rs).filter(pl.col("season") == season)
        write_parquet(str(day / f"roster_weekly_{season}.parquet"), sub.to_dict(as_series=False), rs)
    # Two games; 00-A plays 30 then 12 offensive snaps, 00-B plays 60 in both.
    plays = {"nflverse_game_id": [], "play_id": [], "possession_team": [],
             "offense_players": [], "defense_players": []}
    pbp = {"game_id": [], "play_id": [], "play_type": [], "special_teams_play": [],
           "home_team": [], "away_team": []}
    for gid, a_plays in (("2020_01_BUF_NYJ", 30), ("2020_02_BUF_MIA", 12)):
        for i in range(60):
            on = ["00-B"] + (["00-A"] if i < a_plays else [])
            plays["nflverse_game_id"].append(gid)
            plays["play_id"].append(i + 1)
            plays["possession_team"].append("BUF")
            plays["offense_players"].append(";".join(on))
            plays["defense_players"].append("00-Z")
            pbp["game_id"].append(gid)
            pbp["play_id"].append(float(i + 1))
            pbp["play_type"].append("pass")
            pbp["special_teams_play"].append(0.0)
            pbp["home_team"].append("BUF")
            pbp["away_team"].append(gid[-3:])
    write_parquet(str(day / "pbp_participation_2020.parquet"), plays,
                  {"nflverse_game_id": pl.Utf8, "play_id": pl.Int32, "possession_team": pl.Utf8,
                   "offense_players": pl.Utf8, "defense_players": pl.Utf8})
    write_parquet(str(day / "play_by_play_2020.parquet"), pbp)


def test_end_to_end_build_write_and_consumers(env):
    from jobs import export_web, settle_outcomes
    synthetic_archive(env / "raw")
    xwalk("00-K", "KnowNn00", "Known Player")
    snap("KnowNn00", "2020_01_BUF_NYJ", 2020, week=1, off=60)
    snap("OrphAn00", "2020_01_BUF_NYJ", 2020, week=1, off=30)
    snap("OrphAn00", "2020_02_BUF_MIA", 2020, week=2, off=12)
    snap("DrafTt00", "2013_01_ATL_NO", 2013, week=1, team="ATL", off=0, dfn=20, name="Robert James")
    snap("NobodY00", "2020_01_BUF_NYJ", 2020, week=1, off=3, name="Nobody Known")

    src = b._ro(config.DB_PATH)
    rows, report = b.build(src)
    src.close()
    assert report["OrphAn00"]["resolved"] == ["participation"]
    assert report["DrafTt00"]["resolved"] == ["draft_slot"]
    assert report["NobodY00"]["resolved"] == []
    assert "KnowNn00" not in report

    n, conflicts = b.write(rows)
    assert conflicts == []
    held = alias_rows()
    assert held[("OrphAn00", "participation")][:3] == ("00-A", 2020, 2020)
    assert held[("DrafTt00", "draft_slot")][:3] == ("00-R", 2013, 2013)
    assert held[("NobodY00", "unresolved")][0] is None

    # Idempotent: a second build writes the same pairings and moves nothing.
    src = b._ro(config.DB_PATH)
    rows2, _ = b.build(src)
    src.close()
    b.write(rows2)
    assert {k: v[:3] for k, v in alias_rows().items()} == {k: v[:3] for k, v in held.items()}

    ro = sqlite3.connect(f"file:{config.DB_PATH}?mode=ro", uri=True)
    snaps, unresolved = export_web.load_snaps(ro, {})
    assert snaps[("00-A", "2020_01_BUF_NYJ")][0] == 30
    assert snaps[("00-K", "2020_01_BUF_NYJ")][0] == 60
    assert set(unresolved) == {"NobodY00"}
    weeks = export_web.snap_weeks(ro, {})
    assert weeks[("00-R", 2013, 1)][2] == 20          # defence-only, still visible
    s_idx, players, _ = settle_outcomes.load_snap_index(ro)
    assert ("00-A", 2020, 2) in s_idx and "00-R" in players
    ctx = settle_outcomes.snap_context(ro, "00-A", 2020, 1)
    assert ctx[0][1] == 30 and ctx[1] is True
    ro.close()
