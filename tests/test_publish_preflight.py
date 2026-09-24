"""a-33: the publish preflight's two load-bearing parts, and the flag it names.

  * the write guard - every write lands in scratch because a write-capable open
    of the live store RAISES, shown refusing and shown allowing;
  * the key-level plan - its added/changed/removed/withheld must be exactly what
    `upload(dry_run=True)` decides, or the table it prints details a different
    upload from the one that would run;
  * `analytics.residual --no-asof` reaches `publish(asof=False)`, and its absence
    still publishes the as-of family.
"""
import io
import json
import os
import sqlite3

import pytest

import config
from jobs import export_web as E
from jobs import publish_preflight as P


# ------------------------------------------------------------------ the guard

def test_the_guard_refuses_every_write_capable_spelling_of_the_live_store(tmp_path):
    live = tmp_path / "market_log.db"
    sqlite3.connect(live).close()
    other = tmp_path / "scratch.db"
    guard = P.write_guard(str(live), sqlite3.connect)
    posix = str(live).replace("\\", "/")
    for spelling in (str(live), posix, f"file:{posix}", f"file:{posix}?mode=rw"):
        with pytest.raises(P.LiveWriteRefused):
            guard(spelling, uri=spelling.startswith("file:"))
    # ...and it DISCRIMINATES: read-only on the live file, and anything else, open.
    guard(f"file:{posix}?mode=ro", uri=True).close()
    guard(str(other)).close()
    assert other.exists()


# ------------------------------------------------------------------ the plan

class FakeS3:
    def __init__(self):
        self.objects, self.puts, self.deletes = {}, {}, []

    def put_object(self, Bucket, Key, Body, ContentType, CacheControl):
        self.puts[Key] = Body
        self.objects[Key] = Body

    def delete_object(self, Bucket, Key):
        self.deletes.append(Key)
        self.objects.pop(Key, None)

    def get_object(self, Bucket, Key):
        if Key not in self.objects:
            raise KeyError(Key)
        return {"Body": io.BytesIO(self.objects[Key])}

    def list_objects_v2(self, Bucket):
        return {"Contents": [{"Key": k} for k in sorted(self.objects)]}


@pytest.fixture
def creds(monkeypatch):
    monkeypatch.setattr(config, "WEB_R2_ACCESS_KEY_ID", "id")
    monkeypatch.setattr(config, "WEB_R2_SECRET_ACCESS_KEY", "secret")
    monkeypatch.setattr(config, "WEB_R2_BUCKET", "calibrated-sports-site")


def _put(dest, key, obj):
    E.write_if_changed(E.local_path(dest, key), obj)


def test_the_plan_is_the_upload_key_for_key_and_the_dry_run_touches_nothing(tmp_path, creds):
    dest = str(tmp_path / "web")
    for key in ("nfl/manifest.json", "nfl/players/00-A/summary.json",
                "nfl/players/00-B/summary.json", "cfb/teams/x.json"):
        _put(dest, key, {"kind": "x", "key": key})
    E.upload(dest=dest, client=FakeS3(), log=lambda *_: None)          # seeds the record
    state = json.load(open(os.path.join(dest, E.STATE_FILE), encoding="utf-8"))

    _put(dest, "nfl/manifest.json", {"kind": "x", "changed": True})    # changed
    _put(dest, "lab/nfl/index.json", {"kind": "x"})                    # added
    os.remove(E.local_path(dest, "nfl/players/00-B/summary.json"))     # removed: declared
    os.remove(E.local_path(dest, "cfb/teams/x.json"))                  # withheld: not declared
    refreshed = ["nfl/players/", "lab/"]

    p = P.plan(dest, state, refreshed)
    assert [r["key"] for r in p["added"]] == ["lab/nfl/index.json"]
    assert [r["key"] for r in p["changed"]] == ["nfl/manifest.json"]
    assert p["removed"] == ["nfl/players/00-B/summary.json"]
    assert p["withheld"] == ["cfb/teams/x.json"]

    s3 = FakeS3()
    r = E.upload(dest=dest, client=s3, dry_run=True, refreshed=refreshed, log=lambda *_: None)
    assert (r["changed"], r["removed"], r["removed_withheld"]) == (
        len(p["added"]) + len(p["changed"]), len(p["removed"]), len(p["withheld"]))
    assert s3.puts == {} and s3.deletes == []

    # With no declaration nothing is removed, and the plan says so too.
    q = P.plan(dest, state, None)
    assert q["removed"] == [] and len(q["withheld"]) == 2


def test_the_summary_names_every_removed_key_and_the_withheld_prefixes(tmp_path):
    p = {"added": [{"key": "lab/a.json", "bytes": 10, "gz": 5}],
         "changed": [{"key": "nfl/x.json", "bytes": 4, "gz": 2}],
         "removed": ["nfl/players/gone.json"], "withheld": ["cfb/teams/x.json"]}
    s = P.summarise(p, [("nfl/players/", "jobs.export_web")])
    assert s["removed"] == {"count": 1, "keys": ["nfl/players/gone.json"]}
    assert s["removed_withheld"]["prefixes"] == ["cfb/"]
    assert s["upload"] == {"count": 2, "bytes": 14, "gz": 7}


# ------------------------------------------------------------------ --no-asof

def test_no_asof_reaches_publish_and_its_absence_publishes_the_family(monkeypatch):
    from analytics import paths, residual
    seen = []
    monkeypatch.setattr(residual, "publish", lambda con, facts, stats, asof=True: seen.append(asof))

    class _Con:
        def close(self):
            pass
    monkeypatch.setattr(paths, "market_log_ro", lambda: _Con())
    monkeypatch.setattr(paths, "connect", lambda read_only=False: _Con())
    assert residual.main(["--publish", "--no-asof"]) == 0
    assert residual.main(["--publish"]) == 0
    assert seen == [False, True]


# ------------------------------------------------------------------ served

def test_verify_served_compares_bytes_and_refuses_fewer_than_four(tmp_path):
    keys = [f"nfl/k{i}.json" for i in range(4)]
    for k in keys:
        _put(str(tmp_path), k, {"k": k})
    body = {k: open(E.local_path(str(tmp_path), k), "rb").read() for k in keys}
    served = dict(body)
    served["nfl/k3.json"] = b'{"k": "stale"}\n'
    fetch = lambda url: (200, served[url.split("/data/", 1)[1]])  # noqa: E731
    assert P.verify_served(keys, str(tmp_path), "https://x", fetch=fetch, log=lambda *_: None) == 1
    served["nfl/k3.json"] = body["nfl/k3.json"]
    assert P.verify_served(keys, str(tmp_path), "https://x", fetch=fetch, log=lambda *_: None) == 0
    with pytest.raises(SystemExit):
        P.verify_served(keys[:3], str(tmp_path), "https://x", fetch=fetch)
