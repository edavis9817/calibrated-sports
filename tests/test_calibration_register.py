"""R18's figure: `python -m research.calibration --register` (unit a-29).

Every test builds its own store in `tmp_path` and pins `config.DB_PATH` to it;
nothing here opens the live store.
"""
import sqlite3
import sys

import pytest

import config
from jobs import build_hypotheses as G
from research import calibration as C


def _store(path, rows):
    """rows: (oid, event_id, season, week, side, result, p_bench)."""
    con = sqlite3.connect(path)
    con.executescript("""
        CREATE TABLE outcomes (outcome_id TEXT, season INT, week INT, stat TEXT,
            line REAL, side TEXT, event_id TEXT);
        CREATE TABLE outcome_settlement (outcome_id TEXT, result TEXT, actual REAL);
        CREATE TABLE outcome_close (outcome_id TEXT, p_bench REAL, n_bench INT,
            p_all REAL, n_all INT, dispersion REAL, lead_min REAL);
        CREATE TABLE market_outcome (venue TEXT, market_id TEXT, outcome_id TEXT);
        CREATE TABLE market_liquidity (venue TEXT, market_id TEXT, bucket TEXT);
    """)
    for oid, ev, season, week, side, result, p in rows:
        con.execute("INSERT INTO outcomes VALUES (?,?,?,?,?,?,?)",
                    (oid, season, week, "receptions", 3.5, side, ev))
        con.execute("INSERT INTO outcome_settlement VALUES (?,?,?)", (oid, result, 0.0))
        con.execute("INSERT INTO outcome_close VALUES (?,?,?,?,?,?,?)",
                    (oid, p, 3, p, 5, 0.01, 1.0))
    con.commit()
    con.close()


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = tmp_path / "store.db"
    monkeypatch.setattr(config, "DB_PATH", str(path).replace("\\", "/"))
    return path


def test_postseason_is_excluded_and_counted(db):
    # Two regular-season games: one over wins at 0.5, one loses at 0.5 -> gap 0.
    # One postseason over, settled a loss: included it would drag the gap down.
    _store(db, [
        ("a", "g1", 2024, 1, "over", "over", 0.5),
        ("b", "g2", 2024, 2, "over", "under", 0.5),
        ("c", "g1", 2024, 1, "under", "under", 0.5),   # under side: not counted
        ("d", "g3", 2024, 19, "over", "under", 0.5),   # postseason: excluded
    ])
    f = C.register_figure(draws=50)
    assert (f["n"], f["games"], f["excluded_postseason"]) == (2, 2, 1)
    assert f["est_pp"] == pytest.approx(0.0)
    assert f["lo_pp"] <= f["est_pp"] <= f["hi_pp"]


def test_the_estimate_is_realized_minus_priced(db):
    # Discriminates the sign: priced 0.6, realized 0.0 -> -60pp, not +60.
    _store(db, [("a", "g1", 2023, 3, "over", "under", 0.6),
                ("b", "g2", 2023, 4, "over", "under", 0.6)])
    f = C.register_figure(draws=50)
    assert f["est_pp"] == pytest.approx(-60.0)
    assert f["priced_over"] == pytest.approx(0.6)
    assert f["realized_over"] == 0


def test_an_empty_population_refuses(db):
    _store(db, [("d", "g3", 2024, 20, "over", "under", 0.5)])
    with pytest.raises(RuntimeError, match="empty population"):
        C.register_figure(draws=50)


def test_register_never_opens_the_store_for_writing(db, monkeypatch, capsys):
    _store(db, [("a", "g1", 2024, 1, "over", "over", 0.5),
                ("b", "g2", 2024, 2, "over", "under", 0.5)])

    def refuse():
        raise AssertionError("--register called store.init_db()")

    monkeypatch.setattr(C.store, "init_db", refuse)
    monkeypatch.setattr(sys, "argv", ["calibration", "--register"])
    C.main()
    assert "REGISTER FIGURE" in capsys.readouterr().out


def test_the_register_carries_the_study_exactly_once():
    """The site links /studies/market-calibration to whichever rows name
    research/calibration.py (b-30). Zero rows is the defect a-29 closed; two
    would make the study's header name two findings."""
    rows = [h for h in G.build()["hypotheses"] if h["script"] == "research/calibration.py"]
    assert [h["id"] for h in rows] == ["R18"]
    r = rows[0]
    assert r["interval"][0] <= r["estimate"] <= r["interval"][1]
    assert r["n"] and r["games"]
