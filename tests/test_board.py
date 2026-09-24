"""The Board read job (a-26): definitions, every 5.6 state, and the ledger.

Run: pytest -q tests/test_board.py

Unit tests drive core/board.py directly. The scenario tests build a throwaway
store under tmp_path (never the live one), write Odds API snapshots into it, and
run jobs/board_read.run() read after read the way the cadence would - so each
5.6 state is produced by the real job, not asserted about a hand-built dict.
"""
import json
import os
import sqlite3
from datetime import datetime, timezone

import pytest

import config
import store
from core import board as B
from core import settlement


# ============================================================ definitions

def ladder(**lines):
    """ladder(l7_5={"draftkings": (-120, 100)}) -> the job's ladder shape."""
    return {float(k[1:].replace("_", ".")): {b: {"over": o, "under": u} for b, (o, u) in v.items()}
            for k, v in lines.items()}


def test_devig_is_multiplicative_on_a_two_way_market():
    # -110/-110: both 0.5238, normalised to exactly 0.5
    assert B.devig_mult(-110, -110) == pytest.approx(0.5)
    po, pu = 120 / 220, 100 / 200            # -120 / +100
    assert B.devig_mult(-120, 100) == pytest.approx(po / (po + pu))
    assert B.devig_mult(-110, None) is None  # one-sided: no de-vig, no probability


def test_market_prob_is_the_median_over_benchmark_books_only_with_a_count():
    lad = ladder(l4_5={"draftkings": (-110, -110), "fanduel": (-130, 110),
                       "betmgm": (100, -120), "bovada": (-300, 250)})
    p, n = B.market_prob(lad, 4.5)
    assert n == 3                                          # bovada is not a benchmark book
    assert p == pytest.approx(0.5)                         # median of 0.4773 / 0.5 / 0.5625... middle
    # A book missing: the count drops, the row survives.
    lad2 = ladder(l4_5={"draftkings": (-110, -110), "fanduel": (-130, 110)})
    assert B.market_prob(lad2, 4.5)[1] == 2


def test_main_line_is_the_rung_nearest_even_money_and_ties_are_fixed():
    lad = ladder(l3_5={"draftkings": (-200, 160)}, l4_5={"draftkings": (-105, -115)},
                 l5_5={"draftkings": (150, -180)})
    assert B.main_line(lad) == 4.5
    # tie on distance: more books wins, then the lower line
    tie = ladder(l4_5={"draftkings": (-110, -110)},
                 l5_5={"draftkings": (-110, -110), "fanduel": (-110, -110)})
    assert B.main_line(tie) == 5.5
    tie2 = ladder(l4_5={"draftkings": (-110, -110)}, l5_5={"fanduel": (-110, -110)})
    assert B.main_line(tie2) == 4.5
    assert B.main_line(ladder(l4_5={"bovada": (-110, -110)})) is None


def test_lean_threshold_is_a_log_not_a_setting():
    log = config.BOARD_LEAN_THRESHOLD_LOG
    assert log[0][1] == 4.0
    assert [e[0] for e in log] == sorted(e[0] for e in log)
    assert all(len(e) == 3 and e[2] for e in log)          # every change says why
    # it is not read from the environment - nothing to tune at deploy time
    src = open(config.__file__, encoding="utf-8").read()
    block = src[src.index("BOARD_LEAN_THRESHOLD_LOG"):src.index("BOARD_BENCH_BOOKS")]
    assert "getenv" not in block
    later = log + (("2027-01-01T00:00:00Z", 5.0, "test"),)
    assert B.lean_threshold_at("2026-12-31T00:00:00Z", later) == 4.0
    assert B.lean_threshold_at("2027-01-02T00:00:00Z", later) == 5.0
    with pytest.raises(ValueError):
        B.lean_threshold_at("2020-01-01T00:00:00Z")


@pytest.mark.parametrize("gap,side", [(4.0, "over"), (3.99, None), (-4.0, "under"),
                                      (-3.99, None), (None, None), (0.0, None)])
def test_lean_at_the_threshold(gap, side):
    assert B.lean(gap, 4.0) == side


@pytest.mark.parametrize("gap,band", [(3.9, None), (4.0, (4.0, 6.0)), (-5.99, (4.0, 6.0)),
                                      (6.0, (6.0, 8.0)), (-8.0, (8.0, None)), (40.0, (8.0, None))])
def test_bands(gap, band):
    assert B.band_for(gap) == band


def test_longshot_flag_both_tails():
    assert B.is_longshot(0.10) and B.is_longshot(0.90)
    assert not B.is_longshot(0.15) and not B.is_longshot(0.5) and not B.is_longshot(None)


def test_streak_rules_fire_in_fixed_order_against_todays_line():
    g = lambda v, away=False, opp="X": {"value": v, "away": away, "opp": opp}  # noqa: E731
    hist = [g(3)] + [g(6)] * 6                     # 6 of the last 7 over 4.5
    assert B.streak(hist, 4.5, False, "DAL")["rule"] == "L7"
    assert B.streak([g(6)] * 5, 4.5, False, "DAL")["rule"] == "L5"
    # the same games against a HIGHER line fire nothing - the rule is about today's line
    assert B.streak([g(6)] * 5, 6.5, False, "DAL") is None
    # a push on the line is not graded
    assert B.streak([g(5)] * 5, 5.0, False, "DAL") is None
    h2h = [g(1), g(1), g(9, opp="DAL")]
    assert B.streak(h2h, 4.5, False, "DAL")["rule"] == "H2H1"
    away = [g(9, away=True)] * 5 + [g(1), g(1), g(1)]
    assert B.streak(away, 4.5, True, "NYJ")["rule"] == "AWAY5"
    assert B.streak(away, 4.5, False, "NYJ") is None


def test_grade_vocabulary():
    assert B.grade(4.5, settlement.OVER, None, "over") == (B.CLEARED, "cleared")
    assert B.grade(4.5, settlement.OVER, None, "under") == (B.CLEARED, "missed")
    assert B.grade(4.5, settlement.UNDER, None, "under") == (B.MISSED, "cleared")
    assert B.grade(4.0, settlement.PUSH, None, "over") == (B.PUSH, "push")
    assert B.grade(4.5, settlement.VOID, settlement.INACTIVE, "over") == (B.VOID, None)
    assert B.grade(4.5, settlement.UNSETTLED, None, "over") == (None, None)
    assert B.grade(4.5, settlement.OVER, None, None) == (B.CLEARED, None)


# ============================================================ the ledger, in isolation

def _row(claim="G:P:receptions", line=4.5, lean="over", kick=1000.0, status=None):
    status = B.UPCOMING if status is None else status
    return {"claim_id": claim, "row_id": f"{claim}:{line}", "season": 2026, "week": 3,
            "game_id": "G", "gsis_id": "P", "market": "receptions", "line": line,
            "lean": lean, "status": status, "kickoff_ts": kick, "mkt_p_over": 0.5,
            "mkt_books": 3, "model_p_over": 0.6, "gap_pp": 10.0, "lean_price": -110,
            "band": {"lo_pp": 8.0, "hi_pp": None}}


def test_append_only_refuses_a_rewrite_and_a_shrink():
    ev = B.ledger_events([], [_row()], "2026-09-24T12:00:00Z", lambda e: None, {}, "m", 4.0)
    assert len(ev) == 1
    B.assert_append_only(ev, ev + [dict(ev[0], event="void", void_reason="market_pulled")])
    with pytest.raises(AssertionError, match="rewritten"):
        B.assert_append_only(ev, [dict(ev[0], price=-105)])
    with pytest.raises(AssertionError, match="shrank"):
        B.assert_append_only(ev, [])


def test_a_lean_is_published_once_and_a_moved_line_is_a_second_lean():
    led = B.ledger_events([], [_row(line=4.5)], "r1", lambda e: None, {}, "m", 4.0)
    again = B.ledger_events(led, [_row(line=4.5)], "r2", lambda e: None, {}, "m", 4.0)
    assert again == []                                          # same lean, no new row
    moved = B.ledger_events(led, [_row(line=5.5)], "r3", lambda e: None, {}, "m", 4.0)
    assert [e["line"] for e in moved] == [5.5]
    # the 4.5 lean is graded on 4.5 even though the board now shows 5.5
    led = led + moved
    seen = []
    graded = B.ledger_events(led, [_row(line=5.5, status=B.LIVE)], "r4",
                             lambda e: seen.append(e["line"]) or (
                                 settlement.resolve(5.0, e["line"], False), 5.0, None),
                             {}, "m", 4.0)
    assert sorted(seen) == [4.5, 5.5]
    by_line = {e["line"]: e["result"] for e in graded}
    assert by_line == {4.5: "cleared", 5.5: "missed"}           # 5 > 4.5, 5 < 5.5


def test_a_void_carries_its_reason_and_the_states_partition_the_ledger():
    pub = B.ledger_events([], [_row(claim="A"), _row(claim="B", kick=5000.0),
                               _row(claim="C", kick=500.0), _row(claim="D")],
                          "r1", lambda e: None, {}, "m", 4.0)
    term = B.ledger_events(pub, [], "r2",
                           lambda e: {"A": (settlement.UNDER, 2.0, None),
                                      "C": None, "D": (settlement.VOID, None, settlement.INACTIVE)
                                      }.get(e["claim_id"]),
                           {"B": "r2"}, "m", 4.0)
    led = pub + term
    states = B.lean_states(led, now_ts=800.0)
    by_claim = {e["claim_id"]: states[e["lean_id"]] for e in pub}
    assert by_claim == {"A": B.S_GRADED, "B": B.S_VOID, "C": B.S_LIVE, "D": B.S_VOID}
    reasons = {e["claim_id"]: e["void_reason"] for e in term if e["event"] == "void"}
    assert reasons == {"B": B.VOID_MARKET_PULLED, "D": B.VOID_INACTIVE}
    # disjoint and exhaustive: one state per published lean, no lean without one
    assert set(states) == {e["lean_id"] for e in pub}
    assert set(states.values()) <= {B.S_GRADED, B.S_UPCOMING, B.S_LIVE, B.S_VOID}
    # and the check can fail: a second terminal event is refused
    with pytest.raises(AssertionError, match="two terminal"):
        B.lean_states(led + [dict(term[0])], 800.0)
    with pytest.raises(AssertionError, match="no reason"):
        B.lean_states(pub + [dict(pub[0], event="void", void_reason=None)], 800.0)


# ============================================================ the job, against a fixture store

T0 = datetime(2026, 9, 23, 16, 0, tzinfo=timezone.utc).timestamp()   # Wed 12:00 ET
K_GB = T0 + 1 * 86400 + 8 * 3600                                     # Thu night
K_DET = T0 + 4 * 86400 + 1 * 3600                                    # Sun 17:00Z
H = 3600.0


def iso(ts):
    return B.iso(ts)


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setattr(config, "RAW_DIR", str(tmp_path / "raw"))
    store.init_db()
    from jobs import board_read as J
    # The shape research/board_bands.py writes (games, ci_method and
    # roi_ci_method included - the contract requires them on a published band).
    bands = {"bands": {"receptions|8+": {"n": 100, "k": 48, "cleared": 0.48, "ci": [0.38, 0.58],
                                         "ci_method": "wilson_95", "games": 40,
                                         "roi": -0.08, "roi_ci": [-0.2, 0.05],
                                         "roi_ci_method": "game_block_bootstrap_2000"}}}
    (tmp_path / "bands.json").write_text(json.dumps(bands))
    monkeypatch.setattr(J, "BANDS_PATH", str(tmp_path / "bands.json"))
    monkeypatch.setattr(J, "SLUGS_PATH", str(tmp_path / "no-slugs.json"))

    # The model is not under test here: a fixed P(over) for receptions, and NO
    # model for rush attempts - which is the "model missing for a market" state.
    def model(con, gsis, stat, season, read_ts, pos, team, line, cache):
        return {"receptions": 0.62}.get(stat)
    monkeypatch.setattr(J, "model_prob", model)
    monkeypatch.setattr(config, "BOARD_MODEL_STATS", ("receptions",))

    c = sqlite3.connect(config.DB_PATH)
    for gid, k, home, away, day in (("2026_03_ATL_GB", K_GB, "GB", "ATL", "2026-09-24"),
                                    ("2026_03_NYJ_DET", K_DET, "DET", "NYJ", "2026-09-27")):
        c.execute("INSERT INTO nfl_games (game_id, data_version, season, week, game_type, gameday, "
                  "kickoff_ts, home_team, away_team, source, ingested_ts) "
                  "VALUES (?, 'v1', 2026, 3, 'REG', ?, ?, ?, ?, 't', 0)", (gid, day, k, home, away))
        c.execute("INSERT INTO markets (venue, market_id, event_id, market_type, title, close_ts) "
                  "VALUES ('oddsapi', ?, ?, 'game', ?, ?)",
                  (f"game:{gid}", f"ev-{gid}", {"GB": "Atlanta Falcons @ Green Bay Packers",
                                                 "DET": "New York Jets @ Detroit Lions"}[home], k))
    players = [("00-SB", "Amon-Ra St. Brown", "WR", "DET", "pfr-sb"),
               ("00-JW", "Jameson Williams", "WR", "DET", "pfr-jw"),
               ("00-KP", "Kyle Pitts", "TE", "ATL", "pfr-kp"),
               ("00-BR", "Bijan Robinson", "RB", "ATL", "pfr-br"),
               ("00-DL", "Drake London", "WR", "ATL", "pfr-dl"),
               ("00-TK", "Tucker Kraft", "TE", "GB", "pfr-tk")]
    from venues.mapping import norm_name
    for gsis, name, pos, team, pfr in players:
        c.execute("INSERT INTO player_xwalk (gsis_id, display_name, position, last_team, last_season, "
                  "pfr_id, status) VALUES (?,?,?,?,2026,?,'ACT')", (gsis, name, pos, team, pfr))
        c.execute("INSERT INTO player_alias (alias, gsis_id, source, last_season) VALUES (?,?,'display',2026)",
                  (norm_name(name), gsis))
        # week 1 and 2 history, and a week-2 snap row so "plays elsewhere" is knowable
        for wk in (1, 2):
            c.execute("INSERT INTO nfl_player_week (gsis_id, season, week, season_type, data_version, "
                      "team, position, receptions, carries, source, ingested_ts) "
                      "VALUES (?, 2026, ?, 'REG', 'v1', ?, ?, 6, 12, 't', 0)", (gsis, wk, team, pos))
            c.execute("INSERT INTO nfl_snap_counts (pfr_player_id, game_id, data_version, season, week, "
                      "team, offense_snaps, defense_snaps, source, ingested_ts) "
                      "VALUES (?, ?, 'v1', 2026, ?, ?, 40, 0, 't', 0)", (pfr, f"2026_0{wk}_X_{team}", wk, team))
    c.commit()
    c.close()
    return {"J": J, "dest": str(tmp_path / "out"), "tmp": tmp_path}


def snapshot(ts, event, claims, books=("draftkings", "fanduel", "betmgm")):
    """claims: [(market_key, player, line, over, under, [books])]."""
    c = sqlite3.connect(config.DB_PATH)
    for mkey, player, line, over, under, only in claims:
        for book in (only or books):
            for side, price in (("Over", over), ("Under", under)):
                c.execute("INSERT INTO quotes (ts, sport, venue, event_id, market_id, market_type, "
                          "subject, line, side, last, source, ingest_ts, source_ts) "
                          "VALUES (?, 'nfl', ?, ?, ?, 'prop', ?, ?, ?, ?, 'live', ?, ?)",
                          (ts, f"oddsapi:{book}", event, f"{event}|{mkey}|{player}|{side}",
                           player, line, side, price, ts, ts))
    c.commit()
    c.close()


def stats(game_id, week, rows, snaps):
    """rows: {gsis: receptions}; snaps: {pfr: offense_snaps}."""
    c = sqlite3.connect(config.DB_PATH)
    for gsis, rec in rows.items():
        c.execute("INSERT INTO nfl_player_week (gsis_id, season, week, season_type, data_version, team, "
                  "receptions, carries, source, ingested_ts) VALUES (?, 2026, ?, 'REG', 'v2', 'X', ?, ?, 't', 0)",
                  (gsis, week, rec, rec))
    for pfr, off in snaps.items():
        c.execute("INSERT INTO nfl_snap_counts (pfr_player_id, game_id, data_version, season, week, team, "
                  "offense_snaps, defense_snaps, source, ingested_ts) VALUES (?, ?, 'v2', 2026, ?, 'X', ?, 0, 't', 0)",
                  (pfr, game_id, week, off))
    c.commit()
    c.close()


def read(env, ts):
    env["J"].run(2026, 3, env["dest"], read_ts=ts, log=lambda s: None)
    wd = env["J"].week_dir(env["dest"], 2026, 3)
    idx = json.load(open(os.path.join(wd, "index.json")))
    doc = json.load(open(os.path.join(wd, env["J"].read_name(idx["latest"]))))
    return {r["claim_id"].split(":")[1] + ":" + r["market"]: r for r in doc["rows"]}, idx


def ledger(env):
    return env["J"].read_ledger(env["dest"])


def test_before_lines_post_the_job_refuses_rather_than_publishing_nothing(env):
    with pytest.raises(SystemExit, match="ZERO rows"):
        env["J"].run(2026, 3, env["dest"], read_ts=T0, log=lambda s: None)
    assert not os.path.exists(env["J"].ledger_path(env["dest"]))


def test_the_week_every_state_and_every_lean_accounted_for(env):
    J = env["J"]
    GB, DET = "ev-2026_03_ATL_GB", "ev-2026_03_NYJ_DET"
    # ---- read 1: Wednesday. Every claim listed; Williams at two books only.
    snapshot(T0 - H, GB, [("player_receptions", "Kyle Pitts", 3.5, -110, -110, None),
                          ("player_receptions", "Drake London", 5.5, -110, -110, None),
                          ("player_receptions", "Tucker Kraft", 3.5, -110, -110, None),
                          ("player_rush_attempts", "Bijan Robinson", 17.5, -110, -110, None)])
    snapshot(T0 - H, DET, [("player_receptions", "Amon-Ra St. Brown", 7.5, -105, -115, None),
                           ("player_receptions", "Amon-Ra St. Brown", 6.5, -160, 130, None),
                           ("player_receptions", "Jameson Williams", 3.5, -110, -110,
                            ["draftkings", "fanduel"])])
    rows, idx = read(env, T0)
    assert idx["lean_threshold_pp"] == 4.0 and idx["model_version"]
    assert set(rows) == {"00-KP:receptions", "00-DL:receptions", "00-TK:receptions",
                         "00-BR:rush_attempts", "00-SB:receptions", "00-JW:receptions"}
    assert rows["00-JW:receptions"]["mkt_books"] == 2                       # a book missing
    assert len(rows["00-JW:receptions"]["books"]) == 2
    sb = rows["00-SB:receptions"]
    assert sb["line"] == 7.5 and sb["is_main"] and sb["main_line_changed_since_open"] is False
    assert sb["row_id"] == "2026-03-NYJ-DET:00-SB:receptions:7.5"
    br = rows["00-BR:rush_attempts"]                                         # model missing
    assert br["model_p_over"] is None and br["gap_pp"] is None and br["lean"] is None
    assert br["status"] == B.UPCOMING
    assert sb["lean"] == "over" and sb["band"]["n"] == 100                  # 0.62 vs ~0.48
    published = {e["claim_id"] for e in ledger(env) if e["event"] == "published"}
    assert "2026_03_ATL_GB:00-BR:rush_attempts" not in published            # no model, no lean
    l1 = ledger(env)

    # ---- read 2: Thursday morning. Pitts pulled everywhere; St. Brown's main line moves to 6.5.
    snapshot(T0 + 20 * H, GB, [("player_receptions", "Drake London", 5.5, -110, -110, None),
                               ("player_receptions", "Tucker Kraft", 3.5, -110, -110, None),
                               ("player_rush_attempts", "Bijan Robinson", 17.5, -110, -110, None)])
    snapshot(T0 + 20 * H, DET, [("player_receptions", "Amon-Ra St. Brown", 7.5, 150, -180, None),
                                ("player_receptions", "Amon-Ra St. Brown", 6.5, -105, -115, None),
                                ("player_receptions", "Jameson Williams", 3.5, -110, -110,
                                 ["draftkings", "fanduel"])])
    rows, _ = read(env, T0 + 21 * H)
    kp = rows["00-KP:receptions"]
    assert kp["status"] == B.VOID and kp["void_reason"] == B.VOID_MARKET_PULLED
    assert kp["pulled_at"] == iso(T0 + 21 * H)                               # the Bowers case
    sb = rows["00-SB:receptions"]
    assert sb["line"] == 6.5 and sb["opened_line"] == 7.5
    assert sb["main_line_changed_since_open"] is True
    l2 = ledger(env)
    B.assert_append_only(l1, l2)
    sb_leans = {(e["line"], e["side"]) for e in l2
                if e["claim_id"].endswith("00-SB:receptions") and e["event"] == "published"}
    assert sb_leans == {(7.5, "over"), (6.5, "over")}                       # both kept
    pitts = [e for e in l2 if e["claim_id"].endswith("00-KP:receptions")]
    assert [e["event"] for e in pitts] == ["published", "void"]
    assert pitts[1]["void_reason"] == B.VOID_MARKET_PULLED
    kp_frozen = kp

    # ---- read 3: Thursday game in progress. A late snapshot moves London - the row must not.
    london_before = rows["00-DL:receptions"]
    snapshot(K_GB + H, GB, [("player_receptions", "Drake London", 2.5, -300, 240, None)])
    rows, _ = read(env, K_GB + 1.5 * H)
    dl = rows["00-DL:receptions"]
    assert dl["status"] == B.LIVE
    assert {k: v for k, v in dl.items() if k != "status"} == \
        {k: v for k, v in london_before.items() if k != "status"}           # frozen, no lean change
    assert rows["00-KP:receptions"] == kp_frozen                              # settled: verbatim

    # ---- read 4: Thursday stats land. London played (8); Kraft inactive (no snap row this
    # game, plays elsewhere); Robinson dressed and took 0 snaps.
    stats("2026_03_ATL_GB", 3, {"00-DL": 8}, {"pfr-dl": 50, "pfr-br": 0})
    rows, _ = read(env, K_GB + 5 * H)
    assert rows["00-DL:receptions"]["status"] == B.CLEARED
    assert rows["00-DL:receptions"]["result"] == {"value": 8.0, "cleared": True}
    assert rows["00-TK:receptions"]["status"] == B.VOID
    assert rows["00-TK:receptions"]["void_reason"] == B.VOID_INACTIVE
    assert rows["00-BR:rush_attempts"]["status"] == B.VOID
    assert rows["00-BR:rush_attempts"]["void_reason"] == B.VOID_NO_SNAP
    # the Sunday game has no data yet: nothing on it may be voided by Thursday's snaps
    assert rows["00-SB:receptions"]["status"] == B.UPCOMING
    settled_thursday = {k: v for k, v in rows.items() if v["status"] in B.SETTLED}

    # ---- read 4b: Sunday, DET in progress, its stats not in - but Thursday's snaps ARE. The
    # per-week coverage test would call every DET player "no snap row, plays elsewhere ->
    # void inactive". Nothing may settle until THIS game has data.
    rows, _ = read(env, K_DET + 1 * H)
    assert rows["00-SB:receptions"]["status"] == B.LIVE
    assert rows["00-JW:receptions"]["status"] == B.LIVE
    assert not [e for e in ledger(env) if e["claim_id"].startswith("2026_03_NYJ_DET")
                and e["event"] != "published"]

    # ---- read 5: Sunday after the game. St. Brown 7 (clears 6.5, misses 7.5); Williams
    # played with no stat row -> settles at 0 (the zero-row fix).
    stats("2026_03_NYJ_DET", 3, {"00-SB": 7}, {"pfr-sb": 60, "pfr-jw": 31})
    rows, idx = read(env, K_DET + 5 * H)
    for k, v in settled_thursday.items():
        assert rows[k] == v                                                   # never rewritten
    assert rows["00-SB:receptions"]["status"] == B.CLEARED
    assert rows["00-JW:receptions"]["status"] == B.MISSED
    assert rows["00-JW:receptions"]["result"]["value"] == 0.0
    final = ledger(env)
    B.assert_append_only(l2, final)
    sb = {e["line"]: e["result"] for e in final
          if e["claim_id"].endswith("00-SB:receptions") and e["event"] == "graded"}
    assert sb == {6.5: "cleared", 7.5: "missed"}

    # ---- no gaps, ever: every published lean is graded or void once the week is over
    states = B.lean_states(final, K_DET + 5 * H)
    assert set(states) == {e["lean_id"] for e in final if e["event"] == "published"}
    assert set(states.values()) <= {B.S_GRADED, B.S_VOID}
    assert idx["leans"][B.S_GRADED] + idx["leans"][B.S_VOID] == len(states)
    assert idx["leans"][B.S_UPCOMING] == idx["leans"][B.S_LIVE] == 0
    # the CSV copy is the same ledger
    import polars as pl
    csv = pl.read_csv(J.ledger_path(env["dest"]).replace(".parquet", ".csv"))
    assert csv.height == len(final)


def test_a_read_cannot_go_backwards(env):
    snapshot(T0 - H, "ev-2026_03_NYJ_DET",
             [("player_receptions", "Amon-Ra St. Brown", 7.5, -110, -110, None)])
    read(env, T0)
    with pytest.raises(SystemExit, match="not after"):
        env["J"].run(2026, 3, env["dest"], read_ts=T0, log=lambda s: None)


# ============================================================ cadence

def test_cadence_hourly_then_every_fifteen_minutes_before_a_slot():
    kicks = [K_GB, K_DET]
    assert not B.read_due(T0 - 60, None, kicks, window_open_ts=T0)          # before Tuesday noon
    assert B.read_due(T0, None, kicks, T0)
    assert not B.read_due(T0 + 30 * 60, T0, kicks, T0)                       # hourly
    assert B.read_due(T0 + 60 * 60, T0, kicks, T0)
    near = K_GB - 90 * 60                                                    # inside 2h of a slot
    assert B.read_due(near, near - 15 * 60, kicks, T0)
    assert not B.read_due(near, near - 10 * 60, kicks, T0)
    assert not B.read_due(K_DET + 60, K_DET - 3600, kicks, T0)               # nothing left to read
    assert B.grade_due(K_DET + 2 * H, K_DET, [{"status": B.LIVE}])
    assert not B.grade_due(K_DET + 2 * H, K_DET, [])


def test_window_opens_tuesday_noon_eastern():
    from jobs import board_read as J
    games = {"g": {"kickoff_ts": K_GB}}
    opened = datetime.fromtimestamp(J.window_open_ts(games), J.ET)
    assert (opened.weekday(), opened.hour, opened.minute) == (1, 12, 0)
    assert J.window_open_ts(games) < K_GB


def test_career_posted_grades_each_game_on_its_own_closing_main_line(env):
    """Two past games, each a two-rung ladder. Game 1's main line is 6.5 (0.52 is
    nearer 0.5 than 0.30) and he caught 7: cleared. Game 2's is 7.5 and he caught 7:
    missed. Against TODAY's line either game could read differently - that is the
    `career` key, not this one. A push is not graded."""
    c = sqlite3.connect(config.DB_PATH)
    rungs = [("g1", 6.5, 0.52, "over"), ("g1", 7.5, 0.30, "under"),
             ("g2", 6.5, 0.70, "over"), ("g2", 7.5, 0.49, "under"),
             ("g3", 7.0, 0.50, "push")]
    for i, (g, line, p, res) in enumerate(rungs):
        oid = f"o{i}"
        c.execute("INSERT INTO outcomes (outcome_id, key, sport, season, week, entity_type, entity_id, "
                  "stat, line, side, push_possible, event_id, created_ts) VALUES "
                  "(?, ?, 'nfl', 2025, 1, 'player', '00-SB', 'receptions', ?, 'over', 0, ?, 0)",
                  (oid, oid, line, g))
        c.execute("INSERT INTO outcome_close (outcome_id, close_ts, kickoff_ts, p_bench, n_bench, "
                  "p_all, n_all, built_ts) VALUES (?, 0, ?, ?, 3, ?, 3, 0)", (oid, T0 - 1e6, p, p))
        c.execute("INSERT INTO outcome_settlement (outcome_id, data_version, result, actual, source, "
                  "settled_ts) VALUES (?, 'v1', ?, 7, 't', 0)", (oid, res))
    c.commit()
    con = env["J"].ro()
    try:
        rec = env["J"].posted_record(con, "00-SB", "receptions", T0)
        assert (rec["k"], rec["n"]) == (1, 2)
        assert env["J"].posted_record(con, "00-SB", "receptions", T0 - 2e6) is None   # as-of
    finally:
        con.close()
        c.close()


def test_the_publish_tree_is_refused_because_the_board_has_its_own_tree(env, monkeypatch):
    """a-26 refused WEB_EXPORT_DIR for want of a contract kind; a-31 keeps the
    refusal for a different reason - the Board uploads its own tree with its own
    record (see refuse_publish_tree)."""
    web = env["tmp"] / "web-export"
    monkeypatch.setattr(config, "WEB_EXPORT_DIR", str(web), raising=False)
    for d in (web, web / "board"):
        with pytest.raises(SystemExit, match="WEB_EXPORT_DIR"):
            env["J"].run(2026, 3, str(d), read_ts=T0, log=lambda s: None)
    assert not web.exists()
    env["J"].refuse_publish_tree(str(env["tmp"] / "scratch"))     # anywhere else is fine
