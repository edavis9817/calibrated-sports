"""The CFB export: contract-valid, honest about coverage, and unable to publish.

Run: pytest -q tests/test_export_cfb_web.py

Every fixture is INVENTED - team ids, names and stat lines are placeholders. The
contract validated against is the real one, because validating against a copy would
test the copy.
"""
import json
import os
import time

import pytest

import config
from cfb import paths, schema
from jobs import export_cfb_web as X
from jobs import ingest_cfb

SEASON = X.CURRENT_SEASON
META = ("src_dataset", "src_season", "src_part", "src_file_id", "row_sha", "valid_from_ts")
METAV = ("test", SEASON, None, 1, "x", 1.0)


def _insert(con, table, **cols):
    cols = {**cols, "sport": "cfb"}
    names = list(cols) + list(META)
    vals = list(cols.values()) + list(METAV)
    con.execute(f"INSERT INTO {table} ({', '.join(names)}) "
                f"VALUES ({', '.join('?' * len(names))})", vals)


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "STORAGE_DIR", str(tmp_path))
    paths.ensure_dirs()
    con = ingest_cfb.connect()
    # two FBS teams that share nothing, and a D-II team whose ABBREVIATION COLLIDES
    _insert(con, "cfb_teams", season=SEASON, team_id=1, abbreviation="AAA",
            display_name="Alpha State Aces", slug="alpha-state-aces", classification="fbs",
            conference_name="Conf A", color="002b5c", alternate_color="ffffff")
    _insert(con, "cfb_teams", season=SEASON, team_id=2, abbreviation="BBB",
            display_name="Beta Tech Bears", slug="beta-tech-bears", classification="fbs",
            conference_name="Conf B", color="1a5632", alternate_color=None)
    _insert(con, "cfb_teams", season=SEASON, team_id=3, abbreviation="AAA",
            display_name="Alpha Valley (D-II)", slug="alpha-valley", classification="ii",
            conference_name="Conf C", color="ff0000", alternate_color=None)
    # one played game and one fixture, a week apart
    _insert(con, "cfb_games", game_id=100, season=SEASON, week=1, season_type="regular",
            start_ts=time.time() - 14 * 86400, home_id=1, away_id=2, home_team="Alpha State",
            away_team="Beta Tech", home_abbreviation="AAA", away_abbreviation=None,
            home_points=31, away_points=17, home_division="fbs", away_division="fbs")
    _insert(con, "cfb_games", game_id=101, season=SEASON, week=9, season_type="regular",
            start_ts=time.time() + 30 * 86400, home_id=2, away_id=1, home_team="Beta Tech",
            away_team="Alpha State", home_abbreviation="BBB", away_abbreviation="AAA",
            home_points=None, away_points=None, home_division="fbs", away_division="fbs")
    # three rostered players; only one recorded a stat
    for aid, name, pos in ((10, "Player One", "WR"), (11, "Player Two", "RB"),
                           (12, "Player Three", "LB")):
        _insert(con, "cfb_rosters", season=SEASON, team_id=1, athlete_id=aid,
                full_name=name, position=pos)
    _insert(con, "cfb_player_game_box", game_id=100, season=SEASON, team_id=1, athlete_id=10,
            athlete_name="Player One", rec=6, rec_yds=88, rec_td=1, rush_att=0, rush_yds=0,
            pass_att=0, pass_cmp=0, pass_yds=0, pass_td=0, pass_int=0, rush_td=0)
    _insert(con, "cfb_player_game_usage", game_id=100, season=SEASON, week=1, team_id=1,
            athlete_id=10, athlete_name="Player One", targets=8, rushes=1, team_targets=32,
            team_touches=64)
    con.commit()
    yield con
    con.close()


def test_the_export_validates_against_the_real_contract(store):
    files, _notes = X.build(store)
    X.validate_contract(files)              # raises on any violation
    X.assert_stats_defined(files, files["cfb/manifest.json"]["stat_definitions"])
    assert set(files) >= {"sports.json", "cfb/manifest.json", "cfb/players/index.json",
                          "cfb/teams/alpha-state-aces.json", "cfb/teams/beta-tech-bears.json"}


def test_team_slugs_are_the_schools_own_and_are_legal_keys(store):
    """C-1 closed 2026-09-19: the key pattern allows hyphens, so the readable slug is used.

    Before that, `cfb/teams/alpha-state-aces.json` failed validation and `aaa.json` passed -
    CFB URLs were abbreviation-shaped by the contract's choice, on a sport whose
    abbreviations are not unique."""
    files, _ = X.build(store)
    assert "cfb/teams/alpha-state-aces.json" in files
    assert "cfb/teams/aaa.json" not in files          # the retired abbreviation key
    keys = [k.split("/")[-1][: -len(".json")] for k in files if "/teams/" in k]
    assert all(X.KEY_SLUG.match(k) for k in keys), keys
    X.validate_contract(files)              # the widened pattern accepts these keys


def test_two_teams_cannot_share_a_slug_because_a_slug_is_a_url(store):
    X._insert_dupe = None
    store.execute(
        "INSERT INTO cfb_teams (season, team_id, abbreviation, display_name, slug, "
        "classification, conference_name, color, sport, src_dataset, src_season, "
        "src_file_id, row_sha, valid_from_ts) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (SEASON, 4, "AA2", "Alpha State Aces", "alpha-state-aces", "fbs", "Conf A",
         "002b5c", "cfb", "test", SEASON, 1, "y", 1.0))
    store.commit()
    with pytest.raises(ValueError, match="share a slug"):
        X.build(store)


def test_a_slug_falls_back_before_it_invents(store):
    assert X.team_slug("alpha-state-aces", "AAA", "Alpha State Aces") == "alpha-state-aces"
    assert X.team_slug(None, "AAA", "Alpha State Aces") == "alpha-state-aces"   # from the name
    assert X.team_slug(None, "AAA", None) == "aaa"                              # from the abbr
    with pytest.raises(ValueError):
        X.team_slug(None, None, None)


@pytest.mark.skipif(not os.path.exists(paths.db_path()),
                    reason="reads the real cfb.db, which CI has no copy of")
def test_the_real_fbs_slugs_are_unique_and_legal():
    """The uniqueness pin flagged when C-1 was filed. Measured 2026-09-19 on season 2026:
    138 FBS slugs, all legal, none colliding. Across ALL divisions there is exactly ONE
    collision - `tba`, twice - and both rows are placeholder fixtures ("TBA"), not two
    schools, which is why widening the export beyond FBS needs this test to stay green
    rather than to be relaxed."""
    import sqlite3
    con = sqlite3.connect(f"file:{paths.db_path()}?mode=ro", uri=True)
    rows = con.execute("SELECT slug, display_name, classification FROM cfb_teams WHERE "
                       "season=? AND valid_to_ts IS NULL", (SEASON,)).fetchall()
    con.close()
    fbs = [r for r in rows if r[2] == "fbs"]
    assert len(fbs) >= 130
    assert all(r[0] and X.KEY_SLUG.match(r[0]) for r in fbs)
    dupes = {r[0] for r in fbs if [x[0] for x in fbs].count(r[0]) > 1}
    assert dupes == set(), sorted(dupes)
    all_dupes = {r[0] for r in rows if r[0] and [x[0] for x in rows].count(r[0]) > 1}
    assert all_dupes <= {"tba"}, sorted(all_dupes)


def test_a_colliding_abbreviation_drops_a_colour_row_and_says_so(store):
    """The contract keys colours on abbreviation; CFB abbreviations are not unique."""
    _files, notes = X.build(store)
    assert [d[0] for d in notes["dropped_colors"]] == ["AAA"]


def test_roster_games_count_stat_rows_and_snap_share_is_null(store):
    """No CFB source records whether a player dressed: `games` is a LOWER BOUND, and the
    contract's non-nullable integer cannot say that (finding C-3)."""
    files, _ = X.build(store)
    roster = {r["name"]: r for r in files["cfb/teams/alpha-state-aces.json"]["roster"]}
    assert roster["Player One"]["games"] == 1
    assert roster["Player Two"]["games"] == 0        # rostered, no stat row, did not "miss"
    assert all(r["snap_share"] is None for r in roster.values())
    assert roster["Player One"]["target_share"] == pytest.approx(8 / 32)
    assert all(r["has_page"] is False and r["slug"] is None for r in roster.values())


def test_the_player_index_is_empty_and_counts_say_zero(store):
    """The contract's index IS the page list: every entry needs a slug, and a slug is a
    URL. CFB ships no player pages, so the honest export is empty (finding C-4)."""
    files, _ = X.build(store)
    assert files["cfb/players/index.json"]["players"] == []
    assert files["cfb/manifest.json"]["counts"]["players"] == 0


def test_counts_games_is_scoped_to_the_exported_teams(store):
    files, _ = X.build(store)
    assert files["cfb/manifest.json"]["counts"]["games"] == 1      # one played, one fixture


def test_an_opponent_abbreviation_is_recovered_not_invented(store):
    files, _ = X.build(store)
    week1 = [g for g in files["cfb/teams/alpha-state-aces.json"]["schedule"] if g["index"] == 1][0]
    assert week1["opponent_abbr"] == "BBB"       # null in the game row, found in the feed
    assert week1["result"] == "W" and week1["points_for"] == 31


def test_stale_is_computed_from_the_schedule_not_asserted(store):
    """A week whose last game kicked off 12h+ ago with no result in the store is stale."""
    files, _ = X.build(store)
    assert files["cfb/manifest.json"]["current"]["stale"] is False
    store.execute("UPDATE cfb_games SET home_points=NULL, away_points=NULL WHERE game_id=100")
    store.commit()
    files, _ = X.build(store)
    cur = files["cfb/manifest.json"]["current"]
    assert cur["stale"] is True and "week 1" in cur["stale_reason"]


def test_the_export_has_no_publish_path_at_all(store):
    """upload() deletes by absence and weekly_refresh runs --upload-only on a schedule,
    so a CFB key published now would be deleted by the next NFL-only run (track F's F3).
    This job must not be able to publish even by accident."""
    import ast
    tree = ast.parse(open(X.__file__, encoding="utf-8").read())
    # CODE, not text: the first version of this test read the source as a string and
    # failed on the module docstring that explains why there is no upload path.
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
            imported.update(a.name for a in node.names)
    assert not imported & {"boto3", "botocore", "r2", "store"}, sorted(imported)
    called = {n.func.attr if isinstance(n.func, ast.Attribute) else getattr(n.func, "id", "")
              for n in ast.walk(tree) if isinstance(n, ast.Call)}
    assert not called & {"upload", "put_object", "upload_file", "sync_keys"}, sorted(called)


def test_writing_twice_changes_nothing(store, tmp_path):
    out = str(tmp_path / "export")
    _files, _ = X.export(out, verbose=False)
    again, _ = X.export(out, verbose=False)
    written = sum(1 for k in again
                  if X.write_if_changed(os.path.join(out, *k.split("/")), again[k], False))
    assert written == 0
    with open(os.path.join(out, "cfb", "manifest.json"), encoding="utf-8") as f:
        assert json.load(f)["kind"] == "sport_manifest"
