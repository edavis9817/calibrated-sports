"""a-50: the Board tick deadlock, and the containment around it.

Production, 2026-09-28: every tick exited 1 on
    published lean 2026-03-CIN-PIT:00-0038597:rush_attempts:16.5 under is not on the read
The week's latest read had been written BEFORE a-34 reached production, by the
merge that replaced a claim's row when its main line moved (16.5 -> 15.5), so the
lean the ledger published at 16.5 was on no later read. a-34's guard then refused
every read of the week, and because `_tick` caught only NoRows, the whole tick -
every other week and the upload - died with it, every five minutes.

These tests pin the repair (core.board.restore_legacy, which puts the row back
from the read that carried it, so `leans_on_board` holds AS WRITTEN) and the
containment (one week's failure is that week's).
"""
import json
import os

import pytest

from core import board as B
from tests.test_board import H, K_DET, T0, _brow, env, read, snapshot  # noqa: F401
from tests.test_board_contract import _first_snapshot, _put_keys
from tests.test_export_web import FakeS3, creds  # noqa: F401
from jobs import source_registry as R


@pytest.fixture(autouse=True)
def _fresh_runtime_ledger():
    yield
    R._READS.pop("nfl", None)


def _legacy(row):
    """A row as the pre-a-34 job wrote it: no priced_at, no publication flags."""
    return {k: v for k, v in row.items()
            if k not in ("priced_at", "line_moved_after_publication", "lean_changed_after_publication")}


def _ledger(*reads):
    led = []
    for iso_, rows in reads:
        led += B.ledger_events(led, rows, iso_, lambda e: None, {}, "m", 4.0)
    return led


# ============================================================ the repair, pure

def test_a_lean_the_old_merge_dropped_is_restored_from_the_read_that_carried_it():
    r1 = [_legacy(_brow(7.5))]                       # published 7.5 over at r1
    r2 = [_legacy(_brow(6.5))]                       # the old merge REPLACED it at r2
    led = _ledger(("r1", r1), ("r2", r2))
    pubs = [e for e in led if e["event"] == "published"]
    # the production failure, reproduced: the guard refuses r2 as written
    with pytest.raises(AssertionError, match=r"G:SB:receptions:7.5 over is not on the read"):
        B.leans_on_board(led, r2, 2026, 3, 500.0)
    rows, note = B.restore_legacy([("r2", r2), ("r1", r1)], pubs)
    assert note == {"priced_at_backfilled": 1, "leans_restored": ["G:SB:receptions:7.5 over"]}
    by = {r["line"]: r for r in rows}
    assert (by[7.5]["priced_at"], by[7.5]["is_main"], by[7.5]["line_moved_after_publication"],
            by[7.5]["lean_changed_after_publication"]) == ("r1", False, True, False)
    assert (by[6.5]["priced_at"], by[6.5]["is_main"], by[6.5]["line_moved_after_publication"]) == \
        ("r2", True, False)
    # the guard holds AS WRITTEN - not relaxed - on the repaired rows
    assert B.leans_on_board(led, rows, 2026, 3, 500.0).startswith(
        "2026 wk03: 2 published = 0 graded + 2 upcoming")
    # and after kickoff both go live and stay on the board, through merge_read
    live = B.merge_read(rows, [], 2000.0, "r3")
    assert {(r["line"], r["status"]) for r in live} == {(7.5, B.LIVE), (6.5, B.LIVE)}
    assert B.leans_on_board(led, live, 2026, 3, 2000.0).startswith(
        "2026 wk03: 2 published = 0 graded + 0 upcoming + 2 live")


def test_a_lean_that_stopped_leaning_at_the_same_line_replaces_the_unleaned_row():
    r1 = [_legacy(_brow(2.5, lean="over"))]
    r2 = [_legacy(_brow(2.5, lean=None))]            # MIN-TB 00-0039361 receptions 2.5, in prod
    led = _ledger(("r1", r1), ("r2", r2))
    rows, note = B.restore_legacy([("r2", r2), ("r1", r1)],
                                  [e for e in led if e["event"] == "published"])
    assert len(rows) == 1 and note["leans_restored"] == ["G:SB:receptions:2.5 over"]
    r = rows[0]
    assert (r["lean"], r["priced_at"], r["is_main"], r["lean_changed_after_publication"],
            r["line_moved_after_publication"]) == ("over", "r1", True, True, False)
    assert B.leans_on_board(led, rows, 2026, 3, 500.0)


def test_a_frozen_legacy_row_is_priced_at_the_last_read_it_was_upcoming():
    r1 = [_legacy(_brow(7.5))]
    r2 = [_legacy(_brow(7.5, status=B.LIVE))]        # frozen at kickoff by the old job
    rows, note = B.restore_legacy([("r2", r2), ("r1", r1)], [])
    assert rows[0]["priced_at"] == "r1" and note["priced_at_backfilled"] == 1
    with pytest.raises(AssertionError, match="no read of the week shows it upcoming"):
        B.restore_legacy([("r2", r2)], [])           # nothing to follow back: refused


def test_a_published_lean_on_no_read_is_refused_never_voided_or_invented():
    r1 = [_legacy(_brow(7.5))]
    led = _ledger(("r1", r1))
    with pytest.raises(AssertionError, match="on no read of the week"):
        B.restore_legacy([("r2", [_legacy(_brow(6.5, lean=None))])],
                         [e for e in led if e["event"] == "published"])


def test_the_market_going_away_voids_before_kickoff_and_grades_after_it():
    """The brief's mechanism, which is NOT what happened in production but must
    hold: a published lean whose books stop listing it BEFORE kickoff is void
    market_pulled on the read and the ledger; one whose books are gone AFTER
    kickoff is not dropped and not voided - it is live, and graded on its line."""
    r1 = B.merge_read([], [_brow(7.5)], 100.0, "r1")
    led = _ledger(("r1", r1))
    pulled = B.merge_read(r1, [], 200.0, "r2")                   # before kickoff (1000)
    assert [(r["status"], r["void_reason"], r["pulled_at"]) for r in pulled] == \
        [(B.VOID, B.VOID_MARKET_PULLED, "r2")]
    led += B.ledger_events(led, pulled, "r2", lambda e: None,
                           {r["claim_id"]: r["pulled_at"] for r in pulled}, "m", 4.0)
    assert [e["void_reason"] for e in led if e["event"] == "void"] == [B.VOID_MARKET_PULLED]
    assert "1 void" in B.leans_on_board(led, pulled, 2026, 3, 200.0)

    r1 = B.merge_read([], [_brow(7.5)], 100.0, "r1")
    led = _ledger(("r1", r1))
    gone = B.merge_read(r1, [], 2000.0, "r2")                    # after kickoff, books gone
    assert [(r["status"], r.get("void_reason")) for r in gone] == [(B.LIVE, None)]
    assert "1 live" in B.leans_on_board(led, gone, 2026, 3, 2000.0)


# ============================================================ the repair, through the job

def _make_legacy(env):
    """Two real reads with a line move, then rewritten into the shape the
    pre-a-34 job left in production: the moved lean's row gone from the latest
    read, and no priced_at or publication flags on any row of either read."""
    J = env["J"]
    _first_snapshot()
    read(env, T0)
    snapshot(T0 + 20 * H, "ev-2026_03_NYJ_DET",
             [("player_receptions", "Amon-Ra St. Brown", 7.5, 150, -180, None),
              ("player_receptions", "Amon-Ra St. Brown", 6.5, -105, -115, None)])
    read(env, T0 + 21 * H)
    wd = J.week_dir(env["dest"], 2026, 3)
    idx = json.load(open(os.path.join(wd, "index.json")))
    for iso_ in idx["reads"]:
        p = os.path.join(wd, J.read_name(iso_))
        doc = json.load(open(p))
        doc["rows"] = [_legacy(r) for r in doc["rows"]
                       if not (iso_ == idx["latest"] and r.get("line_moved_after_publication"))]
        json.dump(doc, open(p, "w"))
    moved = "2026-03-NYJ-DET:00-SB:receptions:7.5"
    latest = json.load(open(os.path.join(wd, J.read_name(idx["latest"]))))["rows"]
    assert moved not in {r["row_id"] for r in latest}
    return moved


def test_the_production_deadlock_reproduces_and_the_repaired_read_clears_it(env, monkeypatch):
    J = env["J"]
    moved = _make_legacy(env)
    # without the repair: exactly the production error
    real = B.restore_legacy
    monkeypatch.setattr(B, "restore_legacy", lambda reads, pubs: ([dict(r) for r in reads[0][1]], {
        "priced_at_backfilled": 0, "leans_restored": []}))
    with pytest.raises(AssertionError, match=f"published lean {moved} over is not on the read"):
        J.run(2026, 3, env["dest"], read_ts=K_DET + 1 * H, log=lambda s: None)
    monkeypatch.setattr(B, "restore_legacy", real)
    logs = []
    s = J.run(2026, 3, env["dest"], read_ts=K_DET + 1 * H, log=logs.append)
    assert s["legacy_repair"]["leans_restored"] == [f"{moved} over"]
    assert any(line.startswith("LEGACY REPAIR 2026 wk03") for line in logs)
    assert "every one on the read (1 line moved" in s["leans_on_board"]
    # graded on its OWN line once the stats land, and the tree is whole again
    from tests.test_board import stats
    stats("2026_03_NYJ_DET", 3, {"00-SB": 7}, {"pfr-sb": 60})
    s = J.run(2026, 3, env["dest"], read_ts=K_DET + 5 * H, log=lambda _: None)
    assert s["legacy_repair"] is None                                  # the latest read is a-34's now
    graded = {e["line"]: e["result"] for e in J.read_ledger(env["dest"]) if e["event"] == "graded"}
    assert graded[7.5] == "missed"                                      # 7 < 7.5, over
    J.pair_ledger(env["dest"])


# ============================================================ containment

@pytest.mark.parametrize("exc", [AssertionError("published lean X over is not on the read"),
                                 SystemExit("a refusal")])
def test_one_week_failing_does_not_stop_the_others_or_the_upload(env, creds, monkeypatch, exc):  # noqa: F811
    J = env["J"]
    _first_snapshot()
    real_run, attempted = J.run, []

    def run(season, week, *a, **k):
        attempted.append(week)
        if week == 2:
            raise exc
        return real_run(season, week, *a, **k)
    monkeypatch.setattr(J, "run", run)
    monkeypatch.setattr(J, "due_reads", lambda *a, **k: [(2, "grade"), (3, "read")])
    s3, logs = FakeS3(), []
    with pytest.raises(J.TickFailed, match=r"1 of 2 due week\(s\) failed: wk02 .*uploaded"):
        J.tick(2026, env["dest"], upload=True, now_ts=T0 + 60, client=s3, log=logs.append)
    assert attempted == [2, 3]                                          # week 3 still read
    out = json.loads([line for line in logs if line.startswith('{"at"')][-1])
    assert [r["week"] for r in out["read"]] == [3] and out["failed"][0]["week"] == 2
    assert out["upload"]["uploaded"] == 4                               # and the upload ran
    assert _put_keys(s3) and all(k.startswith("board/") for k in _put_keys(s3))
    assert any("!!! BOARD READ FAILED 2026 wk02" in line for line in logs)   # loudly


def test_a_tick_with_no_failure_returns_normally(env):
    J = env["J"]
    _first_snapshot()
    out = J.tick(2026, env["dest"], now_ts=T0 + 60, log=lambda *_: None)
    assert out["failed"] == [] and out["read"][0]["week"] == 3


def test_the_scheduled_entry_point_exits_1_on_a_contained_failure(env, monkeypatch, tmp_path):
    J = env["J"]
    monkeypatch.setattr(J, "due_reads", lambda *a, **k: [(3, "read")])
    monkeypatch.setattr(J, "run", lambda *a, **k: (_ for _ in ()).throw(AssertionError("boom")))
    log = tmp_path / "tick.log"
    rc = J._logged(lambda: J.tick(2026, env["dest"], now_ts=T0 + 60), str(log))
    text = log.read_text(encoding="utf-8")
    assert rc == 1 and "TickFailed" in text and "!!! BOARD READ FAILED 2026 wk03" in text
