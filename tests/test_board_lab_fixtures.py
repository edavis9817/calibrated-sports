"""a-38: the Board and Lab fixture trees under tests/fixtures/board_lab/ stay valid.

Run: pytest -q tests/test_board_lab_fixtures.py

Track B builds the Lines and Replay pages against these files, so a contract change
that makes them stale must fail here, in the producer's CI, rather than surface as a
page built against a shape the producer no longer writes. Each tree is checked with
the producer's OWN `check_tree` (contract, source gate, ledger pair, index/read
partition), and each check is shown refusing a tampered copy so it discriminates.
"""
import json
import os
import shutil

import pytest

import config
from jobs import board_read as J
from jobs import lab_publish as L

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "board_lab")
WK03 = os.path.join(ROOT, "wk03-upcoming")
WK02 = os.path.join(ROOT, "wk02-graded")
LAB = os.path.join(ROOT, "lab-library")
QUIET = dict(log=lambda *_: None)


@pytest.fixture(autouse=True)
def _pinned(tmp_path, monkeypatch):
    # check_tree writes nothing; pinned anyway, so a future change to it cannot
    # reach the real store from here.
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setattr(config, "STORAGE_DIR", str(tmp_path / "storage"), raising=False)


def _latest(tree, season, week):
    wd = J.week_dir(tree, season, week)
    with open(os.path.join(wd, "index.json"), encoding="utf-8") as f:
        idx = json.load(f)
    with open(os.path.join(wd, J.read_name(idx["latest"])), encoding="utf-8") as f:
        return idx, json.load(f)


@pytest.mark.parametrize("tree,season,week", [(WK03, 2026, 3), (WK02, 2026, 2)])
def test_board_fixture_tree_passes_the_producers_check(tree, season, week):
    got = J.check_tree(tree, **QUIET)
    assert got["json"] >= 2 and got["tables"] == 2
    idx, read = _latest(tree, season, week)
    assert read["rows"], "a fixture read with no rows is not a fixture"
    assert sum(idx["leans"].values()) > 0


def test_fixtures_cover_every_state_a_real_board_has_reached():
    """wk03 is a live pre-kickoff read; wk02 is graded, with void leans. `live` is
    NOT covered: no real read has yet been taken while a game was in progress."""
    idx3, _ = _latest(WK03, 2026, 3)
    idx2, read2 = _latest(WK02, 2026, 2)
    assert idx3["leans"]["upcoming"] > 0
    assert idx2["leans"]["graded"] > 0 and idx2["leans"]["void"] > 0
    results = {(r["result"] or {}).get("cleared") for r in read2["rows"] if r.get("result")}
    assert results == {True, False}


def test_board_check_refuses_a_tampered_fixture(tmp_path):
    t = str(tmp_path / "t")
    shutil.copytree(WK02, t)
    idx, read = _latest(t, 2026, 2)
    victim = next(r for r in read["rows"] if r.get("lean") and r["status"] in ("cleared", "missed"))
    read["rows"].remove(victim)
    with open(os.path.join(J.week_dir(t, 2026, 2), J.read_name(idx["latest"])), "w",
              encoding="utf-8") as f:
        json.dump(read, f)
    with pytest.raises(Exception, match="reconcile"):
        J.check_tree(t, **QUIET)


def test_lab_fixture_tree_passes_the_producers_check():
    got = L.check_tree(LAB, **QUIET)
    assert got["keys"] == 5


def test_lab_check_refuses_an_index_naming_a_missing_preset(tmp_path):
    t = str(tmp_path / "t")
    shutil.copytree(LAB, t)
    os.remove(os.path.join(t, "lab", "nfl", "presets", "chase_the_streak.json"))
    with pytest.raises(Exception, match="not on disk"):
        L.check_tree(t, **QUIET)
