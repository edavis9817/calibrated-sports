"""c-29 - CLV on every published lean (research/clv_record.py).

Run: pytest -q tests/test_clv_record.py

Pure pieces are driven directly. The scenario test builds a throwaway store and
Board tree under tmp_path (never the live ones) and runs `build()` end to end, so
every exclusion reason is produced by the real code and the partition is
asserted on real output.
"""
import json
import os
import sqlite3

import pytest

import config
import store
from research import clv_record as C

# ============================================================ pure pieces


def test_touch_is_vig_inclusive_and_fair_removes_the_hold():
    q = {"over": -110.0, "under": -110.0}
    assert C.touch(q, "over") == pytest.approx(110 / 210)
    assert C.fair(q, "over") == pytest.approx(0.5)
    assert C.fair(q, "under") == pytest.approx(0.5)
    assert C.fair({"over": -110.0}, "over") is None          # one-sided: no fair price


def test_the_sign_is_side_correct_both_ways():
    # the under got shorter (-110 -> -130) between entry and close: the lean on
    # the under got the better price, the lean on the over the worse one
    e = {"dk": {"over": -110.0, "under": -110.0}}
    c = {"dk": {"over": +110.0, "under": -130.0}}
    u, o = C.lean_clv(e, c, "under"), C.lean_clv(e, c, "over")
    assert u["touch"] > 0 and o["touch"] < 0
    assert u["mid"] == pytest.approx(-o["mid"])               # fair sides are complements
    assert u["held"] > 0 and o["held"] < 0


def test_only_books_on_both_ends_count_and_composition_cannot_move_it():
    e = {"dk": {"over": -110.0, "under": -110.0}}
    same = C.lean_clv(e, {"dk": {"over": -110.0, "under": -110.0}}, "over")
    # a second book appears at the close at a very different price: it is ignored
    more = C.lean_clv(e, {"dk": {"over": -110.0, "under": -110.0},
                          "fd": {"over": -300.0, "under": +240.0}}, "over")
    assert same["books"] == more["books"] == ["dk"]
    assert same["touch"] == more["touch"] == 0.0
    assert C.lean_clv(e, {"fd": {"over": -110.0, "under": -110.0}}, "over")["books"] == []


def test_held_arm_carries_the_hold():
    q = {"over": -110.0, "under": -110.0}
    r = C.lean_clv({"dk": q}, {"dk": q}, "over")
    assert r["touch"] == 0.0 and r["mid"] == 0.0
    assert r["held"] == pytest.approx(100 * (0.5 - 110 / 210))   # minus half the hold


def _rows(n_games, per_game, value=lambda g, i: 0.1 * g - 0.3 * i):
    return [{"game_id": f"g{g}", "touch": value(g, i)} for g in range(n_games) for i in range(per_game)]


def test_the_interval_blocks_on_game_so_duplicating_leans_does_not_narrow_it():
    base = C.game_bootstrap(_rows(8, 2), "touch", draws=2000)
    dup = C.game_bootstrap([dict(r) for r in _rows(8, 2) for _ in range(20)], "touch", draws=2000)
    assert dup["est"] == pytest.approx(base["est"])
    assert dup["hi"] - dup["lo"] == pytest.approx(base["hi"] - base["lo"], rel=0.05)


def test_fewer_than_five_games_prints_no_verdict_and_one_game_no_interval():
    four = C.game_bootstrap(_rows(4, 3), "touch", draws=500)
    assert not four["readable"] and four["lo"] is not None
    assert C.verdict(four).startswith("no verdict")
    one = C.game_bootstrap(_rows(1, 5), "touch", draws=500)
    assert one["lo"] is None and one["est"] is not None


@pytest.mark.parametrize("vals, starts", [
    (lambda g, i: 1.0 + 0.01 * g, "the leans beat the close"),
    (lambda g, i: -1.0 - 0.01 * g, "the close beat the leans"),
    (lambda g, i: (-1) ** g * 0.5, "no measurable CLV"),
])
def test_every_verdict_is_reachable(vals, starts):
    iv = C.game_bootstrap(_rows(8, 2, vals), "touch", draws=1000)
    assert C.verdict(iv).startswith(starts)


def test_the_mechanical_term_and_the_covariance_sum_to_the_mean():
    rows = [{"side": s, "over_drift": d} for s, d in
            (("under", 0.5), ("under", 0.3), ("over", -0.2), ("under", 0.1))]
    d = C.decompose(rows)
    assert d["cov"] + d["mechanical"] == pytest.approx(d["mean_sgn_x_drift"])
    assert d["mean_sgn"] == pytest.approx(-0.5)


def test_partition_refuses_a_lean_that_vanished():
    pubs = [{"lean_id": "a"}, {"lean_id": "b"}]
    C.partition(pubs, [{"lean_id": "a"}], {"x": ["b"]})
    with pytest.raises(C.CLVError):
        C.partition(pubs, [{"lean_id": "a"}], {"x": []})
    with pytest.raises(C.CLVError):
        C.partition(pubs, [{"lean_id": "a"}], {"x": ["a"]})


def test_pre_kickoff_is_a57s_rule():
    ok = {"kickoff_ts": 1000.0, "read_at": "1970-01-01T00:10:00Z", "event_at": "1970-01-01T00:10:00Z"}
    assert C.pre_kickoff(ok) is None
    assert "not before kickoff" in C.pre_kickoff(dict(ok, event_at="1970-01-01T00:20:00Z"))


# ============================================================ end to end

KICK = 1_790_528_400.0                 # 2026-09-27T17:00Z
GAME = "2026_03_CAR_CLE"
EV = "ev1"
BOOKS = ("draftkings", "fanduel", "betmgm")


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setattr(config, "RAW_DIR", str(tmp_path / "raw"))
    store.init_db()
    c = sqlite3.connect(config.DB_PATH)
    c.execute("INSERT INTO nfl_games (game_id, data_version, season, week, game_type, gameday, "
              "kickoff_ts, home_team, away_team, source, ingested_ts) "
              "VALUES (?, 'v1', 2026, 3, 'REG', '2026-09-27', ?, 'CLE', 'CAR', 't', 0)", (GAME, KICK))
    c.execute("INSERT INTO markets (venue, market_id, event_id, market_type, title, close_ts) "
              "VALUES ('oddsapi', 'g', ?, 'game', 'Carolina Panthers @ Cleveland Browns', ?)",
              (EV, KICK))
    c.commit()
    c.close()
    return tmp_path


def quote(ts, player, line, over, under, books=BOOKS, mkey="player_receptions"):
    c = sqlite3.connect(config.DB_PATH)
    for b in books:
        for side, price in (("Over", over), ("Under", under)):
            c.execute("INSERT INTO quotes (ts, sport, venue, event_id, market_id, market_type, subject, "
                      "line, side, last, source, ingest_ts, source_ts) "
                      "VALUES (?, 'nfl', ?, ?, ?, 'prop', ?, ?, ?, ?, 'live', ?, ?)",
                      (ts, f"oddsapi:{b}", EV, f"{EV}|{mkey}|{player}|{side}", player, line, side,
                       price, ts, ts))
    c.commit()
    c.close()


def board_tree(tmp, leans, read_ts):
    """leans: [(lean_id, gsis, name, line, side, over, under, books, event)]."""
    import polars as pl
    from jobs import board_read as BR
    read_iso = C.iso(read_ts)
    rows, ledger = [], []
    for lid, gsis, name, line, side, over, under, books, event in leans:
        rid = f"2026-03-CAR-CLE:{gsis}:receptions:{line}"
        rows.append({"row_id": rid, "name": name, "lean": side,
                     "books": [{"book": b, "over": over, "under": under, "read_at": read_iso}
                               for b in books]})
        base = {"lean_id": lid, "claim_id": f"{GAME}:{gsis}:receptions", "row_id": rid,
                "season": 2026, "week": 3, "game_id": GAME, "gsis_id": gsis,
                "market": "receptions", "line": line, "side": side, "read_at": read_iso,
                "kickoff_ts": KICK, "price": over if side == "over" else under, "band": "8+",
                "result": None, "void_reason": None}
        ledger.append(dict(base, event="published", event_at=read_iso))
        if event == "void":
            ledger.append(dict(base, event="void", void_reason="inactive", event_at=C.iso(KICK + 9000)))
        elif event == "graded":
            ledger.append(dict(base, event="graded", result="cleared", event_at=C.iso(KICK + 9000)))
    path = tmp / "board" / BR.read_key(2026, 3, read_iso)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"rows": rows}))
    (tmp / "board" / "board" / "nfl").mkdir(parents=True, exist_ok=True)
    pl.DataFrame(ledger).write_parquet(tmp / "board" / "board" / "nfl" / "ledger.parquet")
    return str(tmp / "board")


def test_every_lean_lands_in_one_bucket_and_the_close_is_strictly_before_kickoff(env):
    read = KICK - 10 * 3600
    # fresh entry capture 2h before the read, for everyone but "stale"
    quote(read - 2 * 3600, "Fresh Player", 4.5, -110.0, -110.0)
    quote(read - 2 * 3600, "Moved Player", 3.5, -110.0, -110.0)
    quote(read - 2 * 3600, "Void Player", 2.5, -110.0, -110.0)
    quote(read - 2 * 3600, "Lone Player", 1.5, -110.0, -110.0, books=("draftkings",))
    quote(read - 30 * 3600, "Stale Player", 5.5, -110.0, -110.0)
    # the close, 20 minutes before kickoff; and an IN-GAME quote that must be ignored
    quote(KICK - 1200, "Fresh Player", 4.5, +100.0, -120.0)
    quote(KICK - 1200, "Moved Player", 4.5, -110.0, -110.0)        # line moved: 3.5 gone
    quote(KICK - 1200, "Void Player", 2.5, -110.0, -110.0)
    quote(KICK - 1200, "Lone Player", 1.5, -110.0, -110.0, books=("fanduel",))
    quote(KICK - 1200, "Stale Player", 5.5, -110.0, -110.0)
    quote(KICK + 600, "Fresh Player", 4.5, -900.0, +600.0)
    quote(KICK, "Fresh Player", 4.5, -900.0, +600.0)          # AT kickoff is in-game too
    board = board_tree(env, [
        ("fresh", "00-1", "Fresh Player", 4.5, "under", -110.0, -110.0, BOOKS, "graded"),
        ("moved", "00-2", "Moved Player", 3.5, "under", -110.0, -110.0, BOOKS, "graded"),
        ("void", "00-3", "Void Player", 2.5, "under", -110.0, -110.0, BOOKS, "void"),
        ("lone", "00-4", "Lone Player", 1.5, "over", -110.0, -110.0, ("draftkings",), "graded"),
        ("stale", "00-5", "Stale Player", 5.5, "over", -110.0, -110.0, BOOKS, "graded"),
    ], read)
    body = C.build(board, config.DB_PATH, now_ts=KICK + 86400)
    x = {e["reason"]: e["lean_ids"] for e in body["exclusions"]}
    assert [r["lean_id"] for r in body["leans"]] == ["fresh"]
    assert x[C.X_NO_CLOSE] == ["moved"]
    assert x[C.X_VOID] == ["void"]
    assert x[C.X_DISJOINT] == ["lone"]
    assert x[C.X_STALE] == ["stale"]
    assert body["n_published"] == 5 == body["n_scored"] + sum(e["n"] for e in body["exclusions"])
    fresh = body["leans"][0]
    # the under went -110 -> -120: a better price for the under, from the pre-kickoff
    # close - NOT the in-game -900/+600, which would read as a huge CLV
    assert fresh["touch"] == pytest.approx(100 * (120 / 220 - 110 / 210), abs=1e-3)
    assert fresh["close_lag_min"] == pytest.approx(20.0)
    assert body["tier"] == "published" and body["source"]["blended_with"] is None


def test_a_pruned_store_is_not_reported_as_stale_or_pulled(env):
    read = KICK - 10 * 3600
    board = board_tree(env, [("gone", "00-1", "Gone Player", 4.5, "under", -110.0, -110.0,
                              BOOKS, "graded")], read)
    body = C.build(board, config.DB_PATH, now_ts=KICK + 86400)
    x = {e["reason"]: e["lean_ids"] for e in body["exclusions"]}
    assert x[C.X_PRUNED] == ["gone"]
    assert x[C.X_STALE] == [] and x[C.X_NO_CLOSE] == []


def test_a_game_not_yet_played_is_pending_not_excluded_for_want_of_a_close(env):
    read = KICK - 10 * 3600
    quote(read - 3600, "Soon Player", 4.5, -110.0, -110.0)
    board = board_tree(env, [("soon", "00-1", "Soon Player", 4.5, "under", -110.0, -110.0,
                              BOOKS, None)], read)
    body = C.build(board, config.DB_PATH, now_ts=KICK - 3600)
    x = {e["reason"]: e["lean_ids"] for e in body["exclusions"]}
    assert x[C.X_PENDING] == ["soon"] and body["n_scored"] == 0
