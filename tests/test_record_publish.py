"""a-61: the record is published after every Board tick, and never half-published.

The record (a-57) was built to scratch and nothing ran it. `record_export.publish`
is now the step `board_read.tick` runs after the Board's own upload. What these
pin down:

  * it writes into its OWN tree beside the Board's, and ships it as its own
    upload - never the web tree, whose uploader ships every changed key;
  * a tier that fails to build, or builds a file the contract refuses, leaves its
    previously published file exactly where it was, locally and in the bucket,
    while the other tiers still publish;
  * nothing it does can raise into the tick or change the tick's exit.

Each refusal is shown next to the case it lets through.
"""
import json
import os

import pytest

import config
from core import record as R
from jobs import export_web as E
from jobs import record_export as X
from tests.test_export_web import FakeS3, creds  # noqa: F401
from tests.test_record import KICK, _ledger_tree, graded, pub

KEYS = sorted(X.KEYS.values())


def _board(tmp_path):
    p = pub("a1")
    return _ledger_tree(tmp_path, [p, graded(p, "cleared")])


def _bytes(tree, key):
    with open(E.local_path(tree, key), "rb") as f:
        return f.read()


# ------------------------------------------------------------------ the tree

def test_the_record_tree_sits_beside_the_boards_and_is_derived_from_it(tmp_path, monkeypatch):
    board = str(tmp_path / "x" / "board_export")
    assert config.record_export_dir(board) == os.path.join(str(tmp_path / "x"), "record_export")
    monkeypatch.setattr(config, "BOARD_EXPORT_DIR", board)
    assert config.record_export_dir() == os.path.join(str(tmp_path / "x"), "record_export")
    monkeypatch.setattr(config, "BOARD_EXPORT_DIR", None)
    with pytest.raises(SystemExit, match="BOARD_EXPORT_DIR is unset"):
        config.record_export_dir()


def test_publish_builds_all_three_into_its_own_tree_and_uploads_record_keys_only(tmp_path, creds):  # noqa: F811
    board = _board(tmp_path)
    s3 = FakeS3()
    out = X.publish(board, now_ts=KICK + 90_000, upload=True, client=s3, log=lambda *_: None)
    tree = config.record_export_dir(board)
    assert out["failed"] == [] and sorted(out["built"]) == KEYS and out["written"] == 3
    assert sorted(E.local_keys(tree)) == KEYS
    assert sorted(k for k in s3.puts if not k.startswith("_state/")) == KEYS
    assert set(k for k in s3.puts if k.startswith("_state/")) == {E.RECORD_STATE_KEY}
    assert out["upload"]["tree"] == "record" and out["upload"]["deleted"] == 0
    # nothing landed in the Board's tree
    assert not any(k.startswith("record/") for k in E.local_keys(board))
    # a second run with nothing changed uploads nothing
    again = X.publish(board, now_ts=KICK + 90_600, upload=True, client=s3, log=lambda *_: None)
    assert again["written"] == 0 and again["upload"]["uploaded"] == 0


# --------------------------------------------------- a failure keeps the old file

def test_a_tier_that_raises_keeps_its_previous_file_and_the_others_still_publish(
        tmp_path, creds, monkeypatch):  # noqa: F811
    board = _board(tmp_path)
    tree = config.record_export_dir(board)
    s3 = FakeS3()
    X.publish(board, now_ts=KICK + 90_000, upload=True, client=s3, log=lambda *_: None)
    before = _bytes(tree, X.KEYS["published"])
    in_bucket = s3.objects[X.KEYS["published"]]

    def boom(*a, **k):
        raise R.RecordError("a planted failure")
    monkeypatch.setattr(R, "build_published", boom)
    # a later clock, so the tiers that DO build carry a new generated_at
    logs = []
    out = X.publish(board, now_ts=KICK + 200_000, upload=True, client=s3, log=logs.append)
    assert [f["tier"] for f in out["failed"]] == ["published"]
    assert sorted(out["built"]) == sorted([X.KEYS["research"], X.KEYS["backtest"]])
    assert _bytes(tree, X.KEYS["published"]) == before              # untouched locally
    assert s3.objects[X.KEYS["published"]] == in_bucket             # and in the bucket
    assert any("!!! RECORD STEP FAILED (published)" in line for line in logs)


def test_a_tier_the_contract_refuses_is_never_written_and_the_others_are(
        tmp_path, creds, monkeypatch):  # noqa: F811
    """Validated PER TIER before anything is written. Validating the batch in
    `sync_keys` alone would refuse all three over one bad file."""
    board = _board(tmp_path)
    tree = config.record_export_dir(board)
    X.publish(board, now_ts=KICK + 90_000, log=lambda *_: None)
    before = _bytes(tree, X.KEYS["published"])
    real = R.build_published

    def broken(*a, **k):
        body = real(*a, **k)
        body.pop("record")                                            # a required field
        return body
    monkeypatch.setattr(R, "build_published", broken)
    out = X.publish(board, now_ts=KICK + 200_000, log=lambda *_: None)
    assert [f["tier"] for f in out["failed"]] == ["published"]
    assert "ContractError" in out["failed"][0]["error"]
    assert _bytes(tree, X.KEYS["published"]) == before
    assert sorted(out["built"]) == sorted([X.KEYS["research"], X.KEYS["backtest"]])


def test_publish_never_raises_even_when_it_cannot_find_a_tree(monkeypatch):
    monkeypatch.setattr(config, "BOARD_EXPORT_DIR", None)
    out = X.publish(None, log=lambda *_: None)
    assert out["failed"] and out["failed"][0]["tier"] == "*"
    assert "BOARD_EXPORT_DIR is unset" in out["failed"][0]["error"]


# ------------------------------------------------------------- the uploaders

def test_the_record_tree_refuses_a_foreign_key(tmp_path, creds):  # noqa: F811
    tree = str(tmp_path / "record_export")
    E.write_if_changed(E.local_path(tree, "nfl/manifest.json"), {"kind": "x"})
    with pytest.raises(ValueError, match="carries the record only"):
        E.upload(dest=tree, client=FakeS3(), tree="record", log=lambda *_: None)


def test_the_record_tree_takes_no_declaration(tmp_path, creds):  # noqa: F811
    with pytest.raises(ValueError, match="deletes nothing"):
        E.upload(dest=str(tmp_path), client=FakeS3(), tree="record", refreshed=["record/"])


def test_the_web_tree_skips_record_keys_and_may_not_claim_the_prefix(tmp_path, creds):  # noqa: F811
    web = str(tmp_path / "web")
    E.write_if_changed(E.local_path(web, "sports.json"), {"kind": "x"})
    E.write_if_changed(E.local_path(web, "record/research.json"), {"kind": "x"})
    s3 = FakeS3()
    res = E.upload(dest=web, client=s3, log=lambda *_: None)
    assert res["record_skipped"] == 1
    assert "sports.json" in s3.puts and "record/research.json" not in s3.puts
    for p in ("record/", "rec", ""):
        with pytest.raises(ValueError):
            E.upload(dest=web, client=FakeS3(), refreshed=[p], log=lambda *_: None)


# ------------------------------------------------------------------ the tick

def test_a_record_failure_does_not_fail_the_tick(env, creds, monkeypatch):  # noqa: F811
    from tests.test_board import T0
    from tests.test_board_contract import _first_snapshot
    J = env["J"]
    _first_snapshot()

    def boom(*a, **k):
        raise RuntimeError("record exploded")
    monkeypatch.setattr(X, "build", boom)
    s3, logs = FakeS3(), []
    out = J.tick(2026, env["dest"], upload=True, now_ts=T0 + 60, client=s3, log=logs.append)
    assert out["upload"]["uploaded"] == 4                               # the Board shipped
    assert len(out["record"]["failed"]) == 3 and out["record"]["built"] == []
    assert not any(k.startswith("record/") for k in s3.puts)
    assert any("!!! RECORD STEP FAILED" in line for line in logs)
    summary = json.loads([line for line in logs if line.startswith('{"at"')][-1])
    assert len(summary["record"]["failed"]) == 3                        # in the run log


from tests.test_board import env  # noqa: E402,F401
