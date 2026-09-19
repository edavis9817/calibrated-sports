"""Does track F's analytics prefix actually work through track A's uploader?

NOT A TEST OF TRACK A'S CODE - it is track F's check that the path it intends to
publish through behaves as it needs, exercised rather than reasoned about.
Ethan, 2026-09-18: "get that in writing from them rather than assuming the path
works". This is the measurement the filing rests on, so the filing quotes a
result instead of an expectation.

Three questions, and the answers differ:

  1. does a key under `analytics/` UPLOAD?                            yes
  2. is a STALE key under `analytics/` deleted from R2 when the run
     declares only track A's five prefixes?                          NO - withheld
  3. is it deleted when `analytics/` is declared?                     yes

(2) is the gap. Uploading is unscoped so publishing works on its own; DELETION is
scoped to declared prefixes, and none of track A's five call sites declares
`analytics/`. A metric track F stops publishing would stay served from R2
forever, and the only thing that would say so is a climbing
`removed_withheld`.
"""
import json
import os

import pytest

from jobs import export_web

A_PREFIXES = ["nfl/market/", "nfl/players/", "nfl/teams/", "research/"]
ANALYTICS = "analytics/"


class FakeR2:
    """Enough of an S3 client for `upload()`. Records what it was asked to do."""

    def __init__(self):
        self.objects = {}
        self.deleted = []
        self.put = []

    def put_object(self, Bucket=None, Key=None, Body=None, **kw):
        self.objects[Key] = Body
        self.put.append(Key)

    def delete_object(self, Bucket=None, Key=None):
        self.objects.pop(Key, None)
        self.deleted.append(Key)

    def get_object(self, Bucket=None, Key=None):
        raise RuntimeError("no remote state")

    def list_objects_v2(self, **kw):
        return {"Contents": [{"Key": k} for k in self.objects]}


@pytest.fixture
def dest(tmp_path, monkeypatch):
    """A WEB_EXPORT_DIR holding one track A key and one track F key, with an
    upload record that also remembers a track F key no longer on disk."""
    root = tmp_path / "export"
    for key in ("nfl/teams/cin.json", "analytics/nfl/pace.seconds_per_play.json"):
        p = root.joinpath(*key.split("/"))
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text('{"k": 1}', encoding="utf-8")
    state = {
        "nfl/teams/cin.json": "stale-sha",
        "analytics/nfl/pace.seconds_per_play.json": "stale-sha",
        # present in the record, ABSENT from disk: a metric no longer published
        "analytics/nfl/pace.plays_per_game.json": "whatever",
        # and track A's own equivalent, for contrast
        "nfl/teams/gone.json": "whatever",
    }
    (root / export_web.STATE_FILE).write_text(json.dumps(state), encoding="utf-8")
    monkeypatch.setattr(export_web.config, "WEB_R2_ACCESS_KEY_ID", "x")
    monkeypatch.setattr(export_web.config, "WEB_R2_SECRET_ACCESS_KEY", "y")
    monkeypatch.setattr(export_web.config, "WEB_R2_BUCKET", "bucket")
    return str(root)


def _run(dest, refreshed):
    client = FakeR2()
    res = export_web.upload(dest=dest, client=client, refreshed=refreshed,
                            log=lambda *a, **k: None)
    return res, client


def test_an_analytics_key_uploads_because_uploading_is_not_scoped(dest):
    """The half that works with no change from track A: `local_keys` walks the
    whole dest tree, so anything under WEB_EXPORT_DIR reaches R2."""
    res, client = _run(dest, A_PREFIXES)
    assert res["configured"] is True
    assert "analytics/nfl/pace.seconds_per_play.json" in client.put
    assert "nfl/teams/cin.json" in client.put


def test_a_stale_analytics_key_is_WITHHELD_when_only_track_A_declares(dest):
    """THE GAP. Track A's five call sites do not declare `analytics/`, so a
    metric track F stops publishing is never removed from the bucket."""
    res, client = _run(dest, A_PREFIXES)
    assert "analytics/nfl/pace.plays_per_game.json" not in client.deleted
    assert res["removed_withheld"] >= 1
    # track A's own stale key IS removed, which is the contrast that shows the
    # mechanism working exactly as designed - and not covering this prefix
    assert "nfl/teams/gone.json" in client.deleted


def test_the_same_key_IS_removed_once_analytics_is_declared(dest):
    """So the fix is a declaration, not a code change in the uploader."""
    res, client = _run(dest, A_PREFIXES + [ANALYTICS])
    assert "analytics/nfl/pace.plays_per_game.json" in client.deleted
    assert res["removed_withheld"] == 0


def test_no_declaration_deletes_nothing_at_all(dest):
    """Absence is not information - the property that makes an undeclared run
    safe rather than catastrophic."""
    res, client = _run(dest, None)
    assert client.deleted == []
    assert res["declared_prefixes"] is None
    assert res["removed_withheld"] == 2


def test_track_A_declares_five_prefixes_and_analytics_is_not_among_them():
    """Read from the producer rather than asserted, so this fails the day it
    stops being true - in either direction."""
    import ast
    import inspect
    tree = ast.parse(inspect.getsource(export_web))
    declared = []
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "append"
                and getattr(node.func.value, "id", None) == "refreshed"):
            arg = node.args[0]
            if isinstance(arg, ast.Constant):
                declared.append(arg.value)
            elif isinstance(arg, ast.JoinedStr):
                declared.append("".join(
                    v.value if isinstance(v, ast.Constant) else "*"
                    for v in v_list(arg)))
    assert declared, "read no declarations - the check found nothing"
    assert not any(d.startswith(ANALYTICS) for d in declared), declared


def v_list(joined):
    return joined.values
