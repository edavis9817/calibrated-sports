"""c-46 - the 19 and the permutation null. Run: pytest -q tests/test_counterfactual_population.py

What these guard, in the order they would hurt:

- R1 really restricts the model's own SQL to what had been ingested, and a row
  ingested late really moves the fit - otherwise "recovered" is a tautology;
- the permutation shuffles WITHIN game and nothing else;
- the null can reject and can fail to reject (a test that cannot do both is decoration);
- the registered verdict comes out both ways, and each of its three bars can refuse alone;
- a game-block interval does not narrow when rows are duplicated inside a game.
"""
import sqlite3
import time

import numpy as np
import pytest

import config
import store
from research import counterfactual_ledger as CL
from research import counterfactual_population as CP


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setattr(config, "RAW_DIR", str(tmp_path / "raw"))
    monkeypatch.setattr(config, "STORAGE_DIR", str(tmp_path))
    store.init_db()
    now = time.time()
    past = now - 86400 * 400
    early = past - 86400                       # ingested long before the read
    games = [("nfl", f"2025_{w:02d}_DET_GB", "v1", 2025, w, "2025-09-07",
              "GB", "DET", past + w * 86400 * 7, "test", early) for w in range(1, 11)]
    store.replace_rows(
        "nfl_games",
        ("sport", "game_id", "data_version", "season", "week", "gameday",
         "home_team", "away_team", "kickoff_ts", "source", "ingested_ts"), games)
    rows = []
    for p, (team, base) in enumerate((("DET", 4.0), ("DET", 2.0), ("GB", 5.0), ("GB", 1.0),
                                      ("DET", 6.0), ("GB", 3.0)), start=1):
        # week 10 kicked off before the read and was ingested AFTER it
        rows += [("nfl", f"00-000000{p}", 2025, w, "REG", "v1", f"WR {p}", "WR", team,
                  float(base + (w % 3) + (9 if w == 10 else 0)), "test",
                  now + 3600 if w == 10 else early) for w in range(1, 11)]
    store.replace_rows(
        "nfl_player_week",
        ("sport", "gsis_id", "season", "week", "season_type", "data_version",
         "player_name", "position", "team", "receptions", "source", "ingested_ts"), rows)
    yield now


def _lean(now):
    return {"gsis_id": "00-0000001", "market": "receptions", "season": 2026, "line": 3.5,
            "read_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now))}


def test_r1_hides_a_row_ingested_after_the_read_and_that_moves_the_fit(env):
    x = _lean(env)
    today = sqlite3.connect(f"file:{config.DB_PATH}?mode=ro", uri=True)
    r1 = sqlite3.connect(f"file:{config.DB_PATH}?mode=ro", uri=True)
    CP.set_r1(r1, CL.parse_iso(x["read_at"]))
    c0, p0 = CP.rebuild(today, x, "WR", "DET")
    c1, p1 = CP.rebuild(r1, x, "WR", "DET")
    assert c0["n"] == 10 and c1["n"] == 9, "the late row is visible today and hidden under R1"
    assert abs(p0 - p1) > CL.GATE_TOL, "fixture must move the probability past the gate"
    assert CP.not_yet_ingested(today, CL.parse_iso(x["read_at"])) == 6
    # a read taken after the ingestion sees everything: R1 is a no-op there
    CP.set_r1(r1, env + 7200)
    assert r1.execute("SELECT COUNT(*) FROM nfl_player_week").fetchone()[0] == 60
    assert CP.not_yet_ingested(today, env + 7200) == 0
    today.close()
    r1.close()


def test_r1_writes_nothing_to_the_facts_file(env):
    con = sqlite3.connect(f"file:{config.DB_PATH}?mode=ro", uri=True)
    CP.set_r1(con, env)
    con.close()
    con = sqlite3.connect(f"file:{config.DB_PATH}?mode=ro", uri=True)
    assert con.execute("SELECT COUNT(*) FROM sqlite_master WHERE type = 'view'").fetchone()[0] == 0
    assert con.execute("SELECT COUNT(*) FROM nfl_player_week").fetchone()[0] == 60
    con.close()


def _rows(effect=0.0, games=30, per=12, mult=1, seed=0):
    """`effect` raises the chance a MISSED row's flipping input is own_mean."""
    rng = np.random.default_rng(seed)
    rows = []
    for g in range(games):
        for n in range(per):
            res = "missed" if rng.random() < 0.5 else "cleared"
            p = 0.5 + (effect if res == "missed" else 0.0)
            inp = "own_mean" if rng.random() < p else ("line" if rng.random() < 0.6 else "group_mean")
            rows += [{"game_id": f"g{g}", "result": res, "flipping_input": inp,
                      "market": "receptions", "side": "under", "band": "8+"}] * mult
    return rows


def test_the_permutation_keeps_each_games_missed_count_and_every_rows_input():
    g, miss, f, n_games = CP._arrays(_rows())
    pm = CP.permuted_missed_counts(g, miss, f, n_games, np.random.default_rng(1), draws=200)
    assert np.all(pm.sum(axis=1) == miss.sum()), "the number of misses is fixed in every draw"
    assert np.all(pm <= f.sum(axis=0)) and pm.std(axis=0)[0] > 0
    # one game, all rows sharing an input: shuffling inside it can change nothing
    same = [{"game_id": "g", "result": r, "flipping_input": "line"} for r in ("missed", "cleared") * 4]
    g, miss, f, n_games = CP._arrays(same)
    pm = CP.permuted_missed_counts(g, miss, f, n_games, np.random.default_rng(1), draws=50)
    assert np.all(pm[:, CP.INPUTS.index("line")] == 4)


def test_the_null_rejects_a_planted_difference_and_not_an_absent_one():
    planted, _ = CP.slice_tests("P", "all", _rows(effect=0.35), 1, {}, draws=2000, boot=500)
    absent, pct = CP.slice_tests("P", "all", _rows(effect=0.0), 1, {}, draws=2000, boot=500)
    own = lambda ts: next(t for t in ts if t["input"] == "own_mean")           # noqa: E731
    assert own(planted)["p"] < 0.001 and own(planted)["estimate"] > 0.2
    assert own(planted)["lo"] > 0
    assert own(absent)["p"] > 0.05
    assert 0.0 < pct["percentile"] < 1.0 and pct["wilson95"][0] < pct["percentile"] < pct["wilson95"][1]


def test_an_input_that_is_never_the_flipping_input_is_p_one_not_p_zero():
    ts, _ = CP.slice_tests("P", "all", _rows(), 1, {}, draws=500, boot=200)
    for name in ("weight", "dispersion"):
        t = next(t for t in ts if t["input"] == name)
        assert t["p"] == 1.0 and t["mde_used"] is None and "zero-variance" in t["note"]


def test_a_slice_with_under_five_games_or_an_empty_arm_is_p_one():
    few = [r for r in _rows() if r["game_id"] in ("g0", "g1", "g2")]
    ts, pct = CP.slice_tests("P", "x", few, 1, {}, draws=200, boot=100)
    assert len(ts) == 5 and all(t["p"] == 1.0 and t["lo"] is None for t in ts) and pct is None
    one_arm = [dict(r, result="missed") for r in _rows()]
    ts, _ = CP.slice_tests("P", "x", one_arm, 1, {}, draws=200, boot=100)
    assert all(t["p"] == 1.0 for t in ts)


def test_duplicating_rows_inside_a_game_does_not_narrow_the_interval():
    a, _ = CP.slice_tests("P", "all", _rows(mult=1), 1, {}, draws=200, boot=500)
    b, _ = CP.slice_tests("P", "all", _rows(mult=20), 1, {}, draws=200, boot=500)
    for x, y in zip(a, b):
        assert x["estimate"] == pytest.approx(y["estimate"])
        assert (y["hi"] - y["lo"]) == pytest.approx(x["hi"] - x["lo"], rel=1e-9)


def _t(inp="own_mean", est=0.2, lo=0.05, hi=0.35, p_holm=0.01, mde=0.12, pop="P496", sl="all"):
    return {"population": pop, "slice": sl, "input": inp, "estimate": est, "lo": lo, "hi": hi,
            "p_holm": p_holm, "mde_used": mde}


def test_the_verdict_comes_out_both_ways_and_each_bar_refuses_alone():
    assert CP.verdict([_t()], "P496") == {"verdict": "diagnostic", "population": "P496",
                                          "inputs": ["own_mean"]}
    no = "not diagnostic, at this power"
    assert CP.verdict([_t(p_holm=0.2)], "P496")["verdict"] == no           # not after correction
    assert CP.verdict([_t(lo=-0.01)], "P496")["verdict"] == no             # interval holds zero
    assert CP.verdict([_t(est=0.10, lo=0.01, hi=0.2)], "P496")["verdict"] == no   # under its MDE
    assert CP.verdict([_t(mde=None)], "P496")["verdict"] == no             # no MDE, no verdict
    assert CP.verdict([_t(est=-0.2, lo=-0.35, hi=-0.05)], "P496")["verdict"] == "diagnostic"
    # a sub-slice, or the other population, changes nothing
    assert CP.verdict([_t(sl="side=under")], "P496")["verdict"] == no
    assert CP.verdict([_t(pop="P496")], "P477")["verdict"] == no
    assert CP.verdict([_t(est=0.01, p_holm=1.0)], "P496")["mde"] == {"own_mean": 0.12}


def test_the_bound_is_the_two_extreme_assignments():
    rows = ([{"result": "missed", "flipping_input": "own_mean"}] * 6
            + [{"result": "missed", "flipping_input": "line"}] * 4
            + [{"result": "cleared", "flipping_input": "own_mean"}] * 5
            + [{"result": "cleared", "flipping_input": "line"}] * 5)
    lo, hi = CP.own_mean_bound(rows, 2, 1)
    assert lo == pytest.approx(6 / 12 - 6 / 11) and hi == pytest.approx(8 / 12 - 5 / 11)
    assert CP.own_mean_bound(rows, 0, 0) == pytest.approx((0.1, 0.1))


def test_the_family_is_eighty_and_a_wrong_count_refuses():
    rows = _rows()
    tests, pcts = CP.analyse({"P477": rows, "P496": rows + rows[:19]}, {})
    assert len(tests) == 80 and set(pcts) == {"P477", "P496"}
    assert all("p_holm" in t and t["p_holm"] >= t["p"] for t in tests)


def test_a_ledger_that_is_not_the_registered_snapshot_is_refused(tmp_path):
    other = tmp_path / "ledger.parquet"
    other.write_bytes(b"not the snapshot")
    with pytest.raises(SystemExit, match="not the registered ledger"):
        CP.main(["--ledger", str(other), "--reads-dir", str(tmp_path), "--facts-db", "x",
                 "--c40-dir", str(tmp_path), "--results-dir", str(tmp_path / "o"),
                 "--now", "0", "--fits-cache", str(tmp_path / "c.json")])
