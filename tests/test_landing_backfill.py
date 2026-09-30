"""a-54: the landing archive is filled with PLAYED periods, rebuilt from the store
at the closing read, by the live export's own builder - without the live
export's filter moving."""
import json
import os
import sqlite3

import pytest

from jobs import export_web as E
from jobs import landing_backfill as B
from jobs import landing_export as L

SEASON, WEEK = 2026, 3
PKEY = f"{SEASON}-{WEEK}"
KICK_A = 1790000000.0          # played: kicked off, final score in
KICK_B = 1790100000.0          # kicked off, no score yet (Monday night)
KICK_C = 1790900000.0          # not yet kicked off
NOW = 1790850000.0          # after the early read of the unplayed game
P1, P2, P3 = "00-0000001", "00-0000002", "00-0000003"


@pytest.fixture(autouse=True)
def _no_store(monkeypatch):
    """The fits open their own store connection; none is reached here. The
    simulation is replaced by a fixed sample - these tests are about WHICH games
    and WHICH quotes, not the Monte Carlo."""
    from research import implied as I
    monkeypatch.setattr(I, "fit_td_anchor", lambda: None)
    monkeypatch.setattr(I, "fit_ypc", lambda: {})
    monkeypatch.setattr(I, "fit_ypr", lambda: {})
    monkeypatch.setattr(I, "fit_copula", lambda: {})
    monkeypatch.setattr(I, "simulate_player_game",
                        lambda fits, real, *a, **k: [float(i % 31) for i in range(400)])


def _game(gid, kick, score, home, away):
    return {"game_id": gid, "season": SEASON, "week": WEEK, "game_type": "REG",
            "kickoff_ts": kick, "home_score": score, "away_score": score,
            "home_team": home, "away_team": away, "total_line": 45.5, "spread_line": 3.0}


@pytest.fixture
def world(tmp_path):
    games = {g["game_id"]: g for g in (
        _game("2026_03_KC_BUF", KICK_A, 24.0, "BUF", "KC"),
        _game("2026_03_PHI_CHI", KICK_B, None, "CHI", "PHI"),
        _game("2026_03_DAL_NYG", KICK_C, None, "NYG", "DAL"))}
    con = sqlite3.connect(":memory:")
    con.executescript("""
        CREATE TABLE outcomes (outcome_id TEXT, sport TEXT, season INT, week INT,
            entity_type TEXT, entity_id TEXT, stat TEXT, line REAL, side TEXT, event_id TEXT);
        CREATE TABLE market_outcome (outcome_id TEXT, venue TEXT, market_id TEXT);
        CREATE TABLE quotes (venue TEXT, market_id TEXT, ts REAL, best_bid REAL, best_ask REAL);
    """)
    for pid, gid, kick in ((P1, "2026_03_KC_BUF", KICK_A), (P2, "2026_03_PHI_CHI", KICK_B),
                           (P3, "2026_03_DAL_NYG", KICK_C)):
        for line in (2.5, 3.5, 4.5, 5.5):
            oid, mid = f"{pid}-{line}", f"KXNFLREC-{pid}-{line}"
            con.execute("INSERT INTO outcomes VALUES (?,?,?,?,?,?,?,?,?,?)",
                        (oid, "nfl", SEASON, WEEK, "player", pid, "receptions", line, "over", gid))
            con.execute("INSERT INTO market_outcome VALUES (?,?,?)", (oid, "kalshi", mid))
            p = 0.9 - 0.15 * (line - 2.5)
            # an early read, the CLOSE (the last one strictly before kickoff), and
            # an in-game quote that must never be read
            for ts, bump in ((kick - 86400, -0.05), (kick - 60, 0.0), (kick + 600, 0.3)):
                con.execute("INSERT INTO quotes VALUES (?,?,?,?,?)",
                            ("kalshi", mid, ts, min(p + bump, 0.98) - 0.01, min(p + bump, 0.98) + 0.01))
    weeks = [{"gsis_id": pid, "season": s, "week": w, "season_type": "REG", "team": team,
              "receiving_tds": 1, "rushing_tds": 0}
             for pid, team in ((P1, "KC"), (P2, "PHI"), (P3, "DAL"))
             for s, w in ((2025, 17), (2026, 1), (2026, 2))]
    # A row FROM the period itself, naming a team not in the game: the as-of cut
    # must hide it, or P1 is dropped as "team not in the game".
    weeks.append({"gsis_id": P1, "season": 2026, "week": WEEK, "season_type": "REG",
                  "team": "MIA", "receiving_tds": 0, "rushing_tds": 0})
    xwalk = {pid: {"display_name": f"Player {pid[-1]}", "position": "WR", "last_team": t}
             for pid, t in ((P1, "KC"), (P2, "PHI"), (P3, "DAL"))}
    return con, games, weeks, xwalk, tmp_path


def build(world, now=NOW):
    con, games, weeks, xwalk, _ = world
    return B.build_period(con, games, weeks, xwalk, {P1: "player-1"}, SEASON, WEEK, now,
                          E.iso(now), n_sims=400)


def test_it_takes_the_kicked_off_games_and_the_live_export_takes_the_rest(world):
    """The two selections are complements: played=True never reads a game the
    live filter would, and the live filter is exactly what it was."""
    con, games, weeks, xwalk, _ = world
    files, _ = build(world)
    ids = {f["identity"]["id"] for f in files.values()}
    assert ids == {P1, P2}                          # scored AND unscored kicked-off games
    cur = B.period_of(games, SEASON, WEEK)
    live, _, _, _ = E.build_market(con, games, B.as_of_weeks(weeks, SEASON, WEEK), xwalk, {},
                                   cur, NOW, E.iso(NOW), n_sims=400)
    assert {f["identity"]["id"] for f in live.values()} == {P3}
    assert set(live).isdisjoint(files)


def test_every_rung_is_the_last_quote_strictly_before_kickoff(world):
    files, _ = build(world)
    for f in files.values():
        for r in f["components"][0]["rungs"]:
            assert r["quote_ts"] == f["kickoff_ts"] - 60      # not the early read, not in-game
        assert L.parse_iso(f["as_of"]) == f["kickoff_ts"] - 60


def test_the_period_cannot_see_itself(world):
    """Invariant 5: P1's week-3 row says MIA. Seen, it drops P1 as 'player team
    not in the game'; cut, P1 is built with the team it had going in."""
    rows = B.as_of_weeks(world[2], SEASON, WEEK)
    assert all((r["season"], r["week"]) < (SEASON, WEEK) for r in rows)
    files, census = build(world)
    assert files[f"nfl/market/{P1}/{PKEY}.json"]["identity"]["team"] == E.team_slug("KC")
    # and the same builder WITHOUT the cut does drop him - the test discriminates
    con, games, weeks, xwalk, _ = world
    leaky, _, c2, _ = E.build_market(con, games, weeks, xwalk, {}, B.period_of(games, SEASON, WEEK),
                                     NOW, E.iso(NOW), n_sims=400, played=True)
    assert f"nfl/market/{P1}/{PKEY}.json" not in leaky
    assert c2.get("player team not in the game") == 1


def test_the_file_is_the_live_shape_and_validates(world):
    files, _ = build(world)
    E.validate_contract(files)                      # raises on any violation
    f = files[f"nfl/market/{P1}/{PKEY}.json"]
    assert f["kind"] == "market" and f["period"]["key"] == PKEY
    assert "ppr" in f["distributions"]              # the ridge and fantasy scenes read it


def test_a_rerun_leaves_the_bytes_alone(world):
    archive = str(world[4] / "archive")
    files, _ = build(world)
    first = B.archive_period(files, archive, "s1")
    assert first == {"new": 2, "unchanged": 0, "replaced": []}
    path = E.local_path(archive, f"nfl/market/{P1}/{PKEY}.json")
    before, mtime = open(path, "rb").read(), os.path.getmtime(path)
    files2, _ = build(world, now=NOW + 3600)        # a later run: new generated_at only
    again = B.archive_period(files2, archive, "s2")
    assert again == {"new": 0, "unchanged": 2, "replaced": []}
    assert open(path, "rb").read() == before and os.path.getmtime(path) == mtime


def test_a_moved_file_is_set_aside_and_says_why(world):
    archive = str(world[4] / "archive")
    files, _ = build(world)
    key = f"nfl/market/{P1}/{PKEY}.json"
    older = json.loads(json.dumps(files[key]))
    older["as_of"] = "2026-09-20T12:00:00Z"
    B.archive_period({key: older}, archive, "s0")
    moved = B.archive_period(files, archive, "s1")
    assert moved["new"] == 1 and len(moved["replaced"]) == 1
    rkey, why = moved["replaced"][0]
    assert rkey == key and "read moved 2026-09-20T12:00:00Z ->" in why
    aside = E.local_path(os.path.join(archive, B.SUPERSEDED, "s1"), key)
    assert json.load(open(aside))["as_of"] == "2026-09-20T12:00:00Z"
    # the aside is outside the tree the landing globs, so it is never walked
    assert [p for _, p in L._market_glob(archive, PKEY)] == sorted(
        E.local_path(archive, k) for k in files)


def test_a_file_it_did_not_rebuild_is_kept(world):
    archive = str(world[4] / "archive")
    files, _ = build(world)
    stray = f"nfl/market/00-0000009/{PKEY}.json"
    B.archive_period({stray: files[f"nfl/market/{P1}/{PKEY}.json"]}, archive, "s0")
    B.archive_period(files, archive, "s1")
    assert B.kept_unrebuilt(archive, PKEY, files) == [stray]
    assert os.path.isfile(E.local_path(archive, stray))


def test_check_writes_nothing(world):
    archive = str(world[4] / "archive")
    files, _ = build(world)
    assert B.archive_period(files, archive, "s1", dry_run=True)["new"] == 2
    assert not os.path.exists(archive)


def test_the_landing_carries_a_backfilled_period(world, tmp_path):
    """End to end through a-52's reader, unchanged: the backfilled week is walked
    and the featured ladder is carried from it."""
    archive = str(tmp_path / "archive")
    files, _ = build(world)
    B.archive_period(files, archive, "s1")
    # Week 4 is current and serves nothing (the Monday tree); week 3 is archived.
    man = {"current": {"season": SEASON, "period": {"index": 4, "label": "Week 4",
                                                     "key": f"{SEASON}-4"}}}
    loaded = {L.MANIFEST: man, L.WINDOW: {"bound": 3, "periods": L.window(man, 3)}}
    for pid, path in L._market_glob(archive, PKEY):
        loaded[f"{L.ARCHIVE}nfl/market/{pid}/{PKEY}.json"] = json.load(open(path))
    pick, step = L._fall_back(loaded, L.pick_featured)
    assert step[0] == 1 and step[1] == PKEY                         # one back
    assert L.provenance(loaded, step, pick[1], "featured_ladder", [pick[0]])["carried"]
    assert pick[1]["identity"]["id"] in {P1, P2}
    assert L.pick_fantasy(step[2]) is not None                       # ppr is there


def test_auto_builds_the_window_and_only_periods_with_a_kicked_off_game():
    games = {}
    for w in range(1, 6):
        g = _game(f"g{w}", 1000.0 * w, 1.0 if w < 4 else None, "BUF", "KC")
        g["week"] = w
        games[g["game_id"]] = g
    # week 4 is current (first unscored) and has kicked off; week 5 has not
    got = B.auto_periods(games, [], now_ts=4500.0, fallback_periods=2)
    assert got == [(SEASON, 4), (SEASON, 3), (SEASON, 2)]
    # the current period is skipped while nothing in it has kicked off
    assert B.auto_periods(games, [], now_ts=3500.0, fallback_periods=2)[0] == (SEASON, 3)


def test_the_served_copy_never_overwrites_a_later_read(tmp_path):
    """landing_export.archive_current: a served file that outlived its kickoff by
    one run is an EARLIER read than the close the backfill wrote - keep the close."""
    d = str(tmp_path)
    key = f"nfl/market/{P1}/{PKEY}.json"
    close = {"identity": {"id": P1}, "as_of": "2026-09-27T16:59:00Z", "x": 1}
    served = {"identity": {"id": P1}, "as_of": "2026-09-26T09:00:00Z", "x": 2}
    path = E.local_path(d, key)
    os.makedirs(os.path.dirname(path))
    json.dump(close, open(path, "w"))
    man = {"current": {"season": SEASON, "period": {"key": PKEY}}}
    files = {L.MANIFEST: man, key: served}
    assert L.archive_current(files, d) == (0, 1)
    assert json.load(open(path))["x"] == 1
    # and a LATER served read does overwrite
    files[key] = dict(served, as_of="2026-09-28T09:00:00Z")
    assert L.archive_current(files, d) == (1, 0)
    assert json.load(open(path))["x"] == 2
