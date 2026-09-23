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
    # EXACTLY the cfb/ tree. `sports.json` is track A's key (c-05): two builders of one
    # key ping-pong it on every upload.
    assert set(files) == {"cfb/manifest.json", "cfb/players/index.json",
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


def test_a_NULL_classification_collider_cannot_take_an_exported_teams_colour(store):
    """c-05, measured on the real store: 'Faulkner Eagles' has classification NULL and
    shares `FAU` with Florida Atlantic. `ORDER BY classification` sorts NULL FIRST, so the
    FBS chip went out in Faulkner's black. The exported tier must win explicitly."""
    _insert(store, "cfb_teams", season=SEASON, team_id=5, abbreviation="BBB",
            display_name="Beta Bible (no tier)", slug="beta-bible", classification=None,
            conference_name=None, color="000000", alternate_color=None)
    store.commit()
    files, notes = X.build(store)
    colours = files["cfb/manifest.json"]["team_colors"]
    assert colours["BBB"]["primary"] == "#1a5632"          # Beta Tech's own, not #000000
    assert ("BBB", "Beta Bible (no tier)", "Beta Tech Bears") in notes["dropped_colors"]


def test_an_exported_team_with_no_colour_gets_no_chip_not_a_neighbours(store):
    store.execute("UPDATE cfb_teams SET color=NULL WHERE team_id=1")     # Alpha State, AAA
    store.commit()
    files, _ = X.build(store)
    assert "AAA" not in files["cfb/manifest.json"]["team_colors"]     # not the D-II red


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


def test_the_export_has_no_NETWORK_path_at_all(store):
    """Revised in c-05. This used to forbid `sync_keys` as well, because `upload()` then
    deleted by absence and a CFB key published once would be deleted by the next NFL-only
    run (track F's F3). Deletion is now scoped to DECLARED prefixes - driven below in
    `test_F3_is_closed_*` rather than assumed - so the CFB tree reaches R2 the way track F's
    does: written into WEB_EXPORT_DIR, uploaded by the ONE uploader. What must still never
    exist here is a second uploader: no S3 client, no put, no delete, no call to upload()."""
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
    assert not imported & {"boto3", "botocore", "r2", "store", "upload", "r2_client",
                           "httpx", "requests"}, sorted(imported)
    called = {n.func.attr if isinstance(n.func, ast.Attribute) else getattr(n.func, "id", "")
              for n in ast.walk(tree) if isinstance(n, ast.Call)}
    assert not called & {"upload", "put_object", "upload_file", "delete_object",
                         "r2_client"}, sorted(called)
    assert "sync_keys" in called         # the one write path, and it is local


# ------------------------------------------------------------ c-05: prefix ownership

def _ownership():
    from tests import test_prefix_ownership as P
    cfb, cfb_dynamic = P.owned_prefixes(X.__file__, ns={"SPORT": X.SPORT})
    nfl, nfl_dynamic = P.owned_prefixes()
    return P, cfb, cfb_dynamic, nfl, nfl_dynamic


def test_the_cfb_export_owns_exactly_cfb_and_the_scan_found_it():
    """Exit 0 is not a result: the AST walk must find the call and resolve it."""
    _P, cfb, dynamic, _nfl, _ = _ownership()
    assert cfb == ["cfb/"] and dynamic == 0, (cfb, dynamic)


def test_no_owned_prefix_reaches_another_producers_keys():
    """Mutual containment, both directions, against track A's prefixes and track F's.
    `sync_keys` deletes what it owns and did not build, so a CFB prefix inside `nfl/` or
    containing `analytics/` would delete another track's files on an ordinary run."""
    P, cfb, _, nfl, nfl_dynamic = _ownership()
    assert nfl_dynamic == 0 and len(nfl) >= 3, nfl
    bad = []
    for theirs in nfl + [P.FOREIGN, "sports.json"]:
        bad += P.containment_violations(cfb, foreign=theirs)
    for ours in cfb:
        bad += P.containment_violations(nfl, foreign=ours)
    assert not bad, bad
    # and it discriminates: the same check fires on prefixes that WOULD collide
    assert P.containment_violations([""], foreign="nfl/teams/")
    assert P.containment_violations(["cfb/"], foreign="cfb/teams/")


def test_staging_deletes_a_stale_cfb_key_and_touches_nothing_outside(store, tmp_path):
    """The owned prefix is filled completely, so a team file the build no longer makes is
    removed - and a key another producer wrote into the same tree is left alone."""
    out = tmp_path / "tree"
    for key in ("cfb/teams/left-fbs.json", "nfl/teams/kc.json", "analytics/cfb/x.json",
                "sports.json"):
        p = out.joinpath(*key.split("/"))
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("{}", encoding="utf-8")
    _files, notes = X.export(str(out), verbose=False)
    assert notes["deleted"] == 1
    assert not (out / "cfb" / "teams" / "left-fbs.json").exists()
    for key in ("nfl/teams/kc.json", "analytics/cfb/x.json", "sports.json"):
        assert out.joinpath(*key.split("/")).read_text(encoding="utf-8") == "{}", key


def test_a_key_outside_cfb_refuses_before_anything_is_written(store, tmp_path, monkeypatch):
    real = X.build

    def leaky(con, generated_at=None):
        files, notes = real(con, generated_at)
        files["sports.json"] = {"kind": "sports"}
        return files, notes
    monkeypatch.setattr(X, "build", leaky)
    with pytest.raises(ValueError, match="outside cfb/"):
        X.export(str(tmp_path / "tree"), verbose=False)
    assert not (tmp_path / "tree").exists()


def test_the_sentinel_is_printed_only_by_a_write_into_the_publishing_tree(
        store, tmp_path, monkeypatch, capsys):
    """A staging run, a dry run and a publishing dry run authorise nothing; only a real
    write into WEB_EXPORT_DIR declares `cfb/`. Read back with the uploader's own
    `parse_refreshed`, so producer and consumer are checked against each other."""
    from jobs.export_web import parse_refreshed
    web = tmp_path / "web"
    monkeypatch.setattr(config, "WEB_EXPORT_DIR", str(web), raising=False)
    for argv, expect in ((["--out", str(tmp_path / "stage")], None),
                         (["--dry-run"], None),
                         (["--dest", "web", "--dry-run"], None),
                         (["--dest", "web"], ["cfb/"])):
        assert X.main(argv) == 0
        assert parse_refreshed(capsys.readouterr().out) == expect, argv
    assert (tmp_path / "stage" / "cfb" / "manifest.json").exists()
    assert (web / "cfb" / "manifest.json").exists()
    assert not (web / "sports.json").exists()


def test_publishing_needs_WEB_EXPORT_DIR_and_never_guesses_it(store, monkeypatch):
    from jobs.export_web import ConfigError
    monkeypatch.setattr(config, "WEB_EXPORT_DIR", None, raising=False)
    with pytest.raises(ConfigError):
        X.main(["--dest", "web"])


class _FakeR2:
    def __init__(self):
        self.put, self.deleted = [], []

    def put_object(self, Bucket=None, Key=None, Body=None, **kw):
        self.put.append(Key)

    def delete_object(self, Bucket=None, Key=None):
        self.deleted.append(Key)

    def get_object(self, Bucket=None, Key=None):
        raise RuntimeError("no remote state")

    def list_objects_v2(self, **kw):
        return {"Contents": []}


@pytest.fixture
def published(store, tmp_path, monkeypatch):
    """A WEB_EXPORT_DIR after a CFB publish: the CFB tree plus one NFL key, and an upload
    record that has seen every one of them."""
    from jobs import export_web
    web = tmp_path / "web"
    files, _ = X.export(str(web), verbose=False)
    nfl = web / "nfl" / "teams" / "kc.json"
    nfl.parent.mkdir(parents=True)
    nfl.write_text("{}", encoding="utf-8")
    state = {k: "old" for k in list(files) + ["nfl/teams/kc.json"]}
    (web / export_web.STATE_FILE).write_text(json.dumps(state), encoding="utf-8")
    monkeypatch.setattr(export_web.config, "WEB_R2_ACCESS_KEY_ID", "x")
    monkeypatch.setattr(export_web.config, "WEB_R2_SECRET_ACCESS_KEY", "y")
    monkeypatch.setattr(export_web.config, "WEB_R2_BUCKET", "bucket")
    return web, files


def _upload(web, refreshed):
    from jobs import export_web
    client = _FakeR2()
    res = export_web.upload(dest=str(web), client=client, refreshed=refreshed,
                            log=lambda *a, **k: None)
    return res, client


NFL_DECLARATION = ["nfl/market/", "nfl/players/", "nfl/teams/", "research/", "analytics/"]


def test_F3_is_closed_an_nfl_run_cannot_delete_a_published_cfb_key(published):
    """THE HAZARD THAT KEPT THIS EXPORT UNPUBLISHED, driven rather than argued. The CFB
    tree vanishes from the local export (a rebuilt directory, a new machine) and the
    ordinary NFL refresh uploads with its own declaration: every CFB key is WITHHELD, none
    deleted, and `withheld_prefixes` names `cfb/`, so the absence is visible."""
    import shutil
    web, files = published
    shutil.rmtree(web / "cfb")
    res, client = _upload(web, NFL_DECLARATION)
    assert client.deleted == []
    assert res["removed_withheld"] == len(files)
    assert res["withheld_prefixes"] == ["cfb/"]


def test_an_undeclared_run_deletes_nothing_and_says_so_differently(published):
    """None (nobody said) and [] (said: I own nothing) both withhold, and are reported
    apart - `declared_prefixes` is the field that tells them apart."""
    import shutil
    web, files = published
    shutil.rmtree(web / "cfb")
    for refreshed in (None, []):
        res, client = _upload(web, refreshed)
        assert client.deleted == [] and res["removed_withheld"] == len(files)
        assert res["declared_prefixes"] == refreshed


def test_a_cfb_declaration_deletes_only_cfb_and_uploads_the_tree(published):
    """The other answer on the other input: with `cfb/` declared, a team file that left
    the tree IS removed from R2 - and the NFL key beside it is not."""
    web, files = published
    gone = "cfb/teams/beta-tech-bears.json"
    os.remove(web.joinpath(*gone.split("/")))
    res, client = _upload(web, ["cfb/"])
    assert client.deleted == [gone]
    assert res["removed_withheld"] == 0
    assert set(client.put) >= set(files) - {gone}
    state = json.loads((web / ".upload_state.json").read_text(encoding="utf-8"))
    assert gone not in state


def test_writing_twice_changes_nothing(store, tmp_path):
    out = str(tmp_path / "export")
    _files, _ = X.export(out, verbose=False)
    again, _ = X.export(out, verbose=False)
    written = sum(1 for k in again
                  if X.write_if_changed(os.path.join(out, *k.split("/")), again[k], False))
    assert written == 0
    with open(os.path.join(out, "cfb", "manifest.json"), encoding="utf-8") as f:
        assert json.load(f)["kind"] == "sport_manifest"


# --- c-14: the spread's SIGN, pinned against results rather than against the transform.
# CFBD `spread` is the home side's betting line (negative = home favoured); nflverse
# `spread_line` is the opposite, and copying the NFL rule published every CFB spread
# upside down (c-13 P1). A test that only restated the transform would have passed on
# the copy too, so these assert the DIRECTION against who actually won.

def _line(con, game_id, spread, home_ml, away_ml):
    g = con.execute("SELECT season, week, home_id, away_id FROM cfb_games WHERE game_id=?",
                    (game_id,)).fetchone()
    _insert(con, "cfb_game_lines", game_id=game_id, season=g[0], week=g[1],
            home_id=g[2], away_id=g[3], provider="consensus", spread=spread, total=50.5,
            home_moneyline=home_ml, away_moneyline=away_ml)
    con.commit()


def _game(files, slug, game_id):
    return next(g for g in files[f"cfb/teams/{slug}.json"]["schedule"]
                if g["game_id"] == str(game_id))


def test_the_favourite_is_the_side_the_book_favoured_and_it_is_positive(store):
    """Invented: Alpha State at home, CFBD line -7.0, home moneyline -300, won 31-17.
    The contract says positive = THIS team favoured, so Alpha reads +7.0 and Beta -7.0."""
    _line(store, 100, -7.0, -300.0, 240.0)
    files, _ = X.build(store)
    home, away = _game(files, "alpha-state-aces", 100), _game(files, "beta-tech-bears", 100)
    assert home["home"] and not away["home"]
    assert home["result"] == "W" and away["result"] == "L"
    assert home["spread"] == 7.0 and away["spread"] == -7.0
    # the side the moneyline favours is the side the published spread favours
    assert (home["spread"] > 0) == (-300.0 < 240.0)


def test_an_away_favourite_is_positive_on_the_away_file(store):
    """The other branch: Alpha State AWAY at Beta Tech, home line +3.5 (Beta the dog)."""
    _line(store, 101, 3.5, 150.0, -175.0)
    files, _ = X.build(store)
    assert _game(files, "alpha-state-aces", 101)["spread"] == 3.5
    assert _game(files, "beta-tech-bears", 101)["spread"] == -3.5


def test_a_pickem_is_zero_on_both_sides_and_never_negative_zero(store):
    _line(store, 100, 0.0, -110.0, -110.0)
    files, _ = X.build(store)
    for slug in ("alpha-state-aces", "beta-tech-bears"):
        s = _game(files, slug, 100)["spread"]
        assert s == 0 and json.dumps(s) == "0.0"


REAL = pytest.mark.skipif(not os.path.exists(paths.db_path()),
                          reason="reads the real cfb.db, which CI has no copy of")


def _ro():
    import sqlite3
    return sqlite3.connect(f"file:{paths.db_path()}?mode=ro", uri=True)


@REAL
def test_alabama_favoured_by_18_5_over_florida_state_is_published_as_plus_18_5():
    """A NAMED GAME WITH A KNOWN OUTCOME. 2026 week 3, game 401856685: Florida State at
    Alabama. CFBD stores the line -18.5 (Bovada and DraftKings alike) with Alabama's
    moneyline at -1350/-1500, and Alabama won 50-36. Before c-14 the Alabama file said
    -18.5 and the page rendered "+18.5" - an 18.5-point UNDERDOG that won."""
    con = _ro()
    try:
        g = con.execute("SELECT home_id, away_id, home_points, away_points FROM cfb_games "
                        "WHERE game_id=401856685 AND valid_to_ts IS NULL").fetchone()
        assert g is not None, "the pinned game left the store - re-pin, do not delete"
        home_id, away_id, hp, ap = g
        assert hp > ap                                     # Alabama, at home, won
        lines, abbrs = X.game_lines(con), X.abbr_map(con)
        assert lines[401856685][0] == -18.5                # CFBD: negative = home favoured
        rows = {tid: next(r for r in X.schedule_rows(con, tid, lines, abbrs, set())
                          if r["game_id"] == "401856685") for tid in (home_id, away_id)}
    finally:
        con.close()
    ala, fsu = rows[home_id], rows[away_id]
    assert ala["home"] and ala["result"] == "W" and ala["spread"] == 18.5
    assert not fsu["home"] and fsu["result"] == "L" and fsu["spread"] == -18.5


@REAL
def test_over_the_whole_store_the_published_favourite_wins_most_games():
    """The direction, over every scored game with a line, from BOTH sides - through the
    exporter's own line selection and `team_spread`. Measured c-14 (2026-09-23): the
    home favourite wins ~79% of games and corr(team spread, own margin) ~ +0.71; the
    wrong sign gives the mirror image, so a flip cannot pass either bound."""
    con = _ro()
    try:
        lines = X.game_lines(con)
        games = con.execute("SELECT game_id, home_points, away_points FROM cfb_games "
                            "WHERE valid_to_ts IS NULL AND home_points IS NOT NULL AND "
                            "away_points IS NOT NULL").fetchall()
    finally:
        con.close()
    xs, ys, fav, fav_won = [], [], 0, 0
    for gid, hp, ap in games:
        spread = lines.get(gid, (None, None))[0]
        if spread is None:
            continue
        for home, margin in ((True, hp - ap), (False, ap - hp)):
            s = X.team_spread(spread, home)
            xs.append(s)
            ys.append(margin)
            if s > 0:
                fav += 1
                fav_won += margin > 0
    assert len(xs) > 20000, len(xs)
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    corr = cov / (sum((x - mx) ** 2 for x in xs) * sum((y - my) ** 2 for y in ys)) ** 0.5
    assert corr > 0.5, corr
    assert fav_won / fav > 0.7, (fav_won, fav)


# --- c-15: WHICH provider the published line is. Pinned as it IS, not as it should be,
# so a change to the rule is a deliberate diff to this test rather than a silent move in
# every published number. research/cfb_line_provenance.py measures what it does to the
# real store: `consensus` 2013-2022, then - because CFBD stops sending consensus - the
# alphabetically first book (Bovada / ESPN Bet / DraftKings), per game, unlabelled.

def _prov(con, game_id, provider, spread, total):
    g = con.execute("SELECT season, week, home_id, away_id FROM cfb_games WHERE game_id=?",
                    (game_id,)).fetchone()
    _insert(con, "cfb_game_lines", game_id=game_id, season=g[0], week=g[1], home_id=g[2],
            away_id=g[3], provider=provider, provider_raw=provider, spread=spread, total=total)
    con.commit()


def test_c15_consensus_wins_wherever_it_exists(store):
    _prov(store, 100, "DraftKings", -3.5, 51.5)
    _prov(store, 100, "consensus", -4.0, 50.5)
    _prov(store, 100, "Bovada", -3.0, 52.0)
    assert X.game_lines(store)[100] == (-4.0, 50.5)


def test_c15_without_consensus_the_alphabetically_first_book_is_published(store):
    """Not the first to arrive, not a named book, not an average: name order. On the real
    store this silently switches the published book mid-season from game to game."""
    _prov(store, 100, "ESPN Bet", -3.5, 51.5)
    _prov(store, 100, "DraftKings", -4.5, 50.5)
    _prov(store, 100, "Bovada", -3.0, 52.0)
    assert X.game_lines(store)[100] == (-3.0, 52.0)
    _prov(store, 101, "ESPN Bet", 7.0, 44.0)
    _prov(store, 101, "DraftKings", 6.5, 44.5)
    assert X.game_lines(store)[101] == (6.5, 44.5)          # a different book, same rule


def test_c15_the_total_comes_from_the_chosen_row_even_when_that_row_has_none(store):
    """KNOWN DEFECT, measured by c-15: 2,907 exported games publish total=null although
    another provider in the store carries one (2,901 of them a consensus row with no
    overUnder, 2013-2016). Fixing it changes published totals, so it waits until c-14's
    republish is out (one export publishes everything that changed). When it is fixed,
    this assertion is the one that must flip."""
    _prov(store, 100, "consensus", -4.0, None)
    _prov(store, 100, "Bovada", -3.0, 52.0)
    assert X.game_lines(store)[100] == (-4.0, None)
