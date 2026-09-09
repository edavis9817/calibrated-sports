"""Retention tests. Run: pytest -q

Two failure modes are being pinned here, and they are the ones that killed the
two projects before this one:

  - an archive that grows until the disk fills, and a process that dies when it
    does rather than degrading
  - a rotation that deletes a local shard on the strength of an upload it never
    actually checked

So: rotation must upload, verify by reading the bytes back, and only then
delete - and a low-disk condition must cost the raw archive, not the process.
"""
import gzip
import io
import json
import os
import time

import pytest

import config
import r2
import store
from jobs import prune_quotes, rotate_raw


# --- a fake S3, matching only the surface r2.py uses -------------------------

class FakeS3:
    """In-memory object store. `corrupt` flips a byte on the way in, which is
    how a silently bad upload is simulated."""

    def __init__(self, corrupt=False, fail_upload=False):
        self.objects = {}
        self.corrupt = corrupt
        self.fail_upload = fail_upload

    def upload_file(self, local, bucket, key, ExtraArgs=None):
        if self.fail_upload:
            raise RuntimeError("network went away")
        data = open(local, "rb").read()
        if self.corrupt:
            data = data + b"x"
        self.objects[(bucket, key)] = data

    def head_object(self, Bucket, Key):
        if (Bucket, Key) not in self.objects:
            raise RuntimeError("NoSuchKey")
        return {"ContentLength": len(self.objects[(Bucket, Key)])}

    def get_object(self, Bucket, Key):
        return {"Body": io.BytesIO(self.objects[(Bucket, Key)])}

    def download_file(self, bucket, key, dest):
        with open(dest, "wb") as f:
            f.write(self.objects[(bucket, key)])


@pytest.fixture
def env(tmp_path, monkeypatch):
    """An isolated DB, raw dir and R2 config. Never touches the live archive."""
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setattr(config, "RAW_DIR", str(tmp_path / "raw"))
    monkeypatch.setattr(config, "R2_BUCKET", "test-bucket")
    monkeypatch.setattr(config, "R2_ACCESS_KEY_ID", "k")
    monkeypatch.setattr(config, "R2_SECRET_ACCESS_KEY", "s")
    monkeypatch.setattr(config, "R2_ENDPOINT", "https://example.r2.example.com")
    monkeypatch.setattr(config, "R2_PREFIX", "raw")
    store.init_db()
    store.reset_disk_cache()
    store.reset_quote_state()
    yield tmp_path
    store.reset_disk_cache()
    store.reset_quote_state()


def make_shard(root, venue="kalshi", day="2026-08-01", hour="04", payloads=3):
    """A realistic gzipped JSONL shard, same shape store.archive_raw writes."""
    d = os.path.join(root, venue, day)
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, f"{hour}.jsonl.gz")
    with gzip.open(path, "at", encoding="utf-8") as f:
        for i in range(payloads):
            f.write(json.dumps({"ts": 1788900000 + i, "endpoint": "orderbooks",
                                "payload": {"orderbooks": [{"ticker": f"T{i}"}]}}) + "\n")
    return path, f"{venue}/{day}/{hour}.jsonl.gz"


# --- rotation ----------------------------------------------------------------

def test_rotation_uploads_verifies_then_deletes_locally(env, monkeypatch):
    """The acceptance case: the shard reaches R2 intact, the local copy is gone,
    and the manifest can still say where it went."""
    path, rel = make_shard(config.RAW_DIR)
    original = open(path, "rb").read()
    digest = r2.sha256_file(path)
    fake = FakeS3()
    monkeypatch.setattr(r2, "client", lambda: fake)

    stats = rotate_raw.run(days=0)

    assert stats == {"due": 1, "rotated": 1, "failed": 0,
                     "bytes": len(original), "skipped": None}
    assert not os.path.exists(path)                       # local copy reclaimed
    assert fake.objects[("test-bucket", "raw/" + rel)] == original   # byte-for-byte

    state, bucket, key, verified_ts = store.locate_shard(rel)
    assert (state, bucket, key) == ("remote", "test-bucket", "raw/" + rel)
    assert verified_ts is not None

    with store.db() as c:
        row = c.execute("SELECT sha256, bytes, deleted_local_ts FROM raw_shards "
                        "WHERE rel_path=?", (rel,)).fetchone()
    assert row[0] == digest and row[1] == len(original) and row[2] is not None


def test_a_rotated_shard_is_still_retrievable(env, monkeypatch):
    """Moving is only moving if the bytes come back. This is the recovery path
    a re-derivation depends on."""
    path, rel = make_shard(config.RAW_DIR)
    original = open(path, "rb").read()
    fake = FakeS3()
    monkeypatch.setattr(r2, "client", lambda: fake)
    rotate_raw.run(days=0)

    state, bucket, key, _ = store.locate_shard(rel)
    dest = os.path.join(config.RAW_DIR, "restored.jsonl.gz")
    r2.fetch(fake, key, dest)

    assert open(dest, "rb").read() == original
    with gzip.open(dest, "rt", encoding="utf-8") as f:
        assert len(f.readlines()) == 3


def test_a_failed_verify_keeps_the_local_copy(env, monkeypatch):
    """A verify that does not match is a reason to hold two copies, never a
    reason to hold none. This is the check that makes deletion safe."""
    path, rel = make_shard(config.RAW_DIR)
    monkeypatch.setattr(r2, "client", lambda: FakeS3(corrupt=True))

    stats = rotate_raw.run(days=0)

    assert stats["rotated"] == 0 and stats["failed"] == 1
    assert os.path.exists(path)                            # untouched
    assert store.locate_shard(rel)[0] == "local"
    assert store.health("rotate_raw")[1] == 0


def test_a_failed_upload_keeps_the_local_copy(env, monkeypatch):
    path, rel = make_shard(config.RAW_DIR)
    monkeypatch.setattr(r2, "client", lambda: FakeS3(fail_upload=True))

    stats = rotate_raw.run(days=0)

    assert stats["failed"] == 1
    assert os.path.exists(path)


def test_rotation_only_touches_shards_past_the_window(env, monkeypatch):
    """Age is measured from the end of the shard's UTC day, so today's shard -
    which is still being appended to - can never be selected."""
    today = time.strftime("%Y-%m-%d", time.gmtime())
    old_path, old_rel = make_shard(config.RAW_DIR, day="2026-08-01")
    new_path, _ = make_shard(config.RAW_DIR, day=today)
    monkeypatch.setattr(r2, "client", lambda: FakeS3())

    assert [rel for rel, _ in rotate_raw.due(days=7)] == [old_rel]

    rotate_raw.run(days=7)
    assert not os.path.exists(old_path)
    assert os.path.exists(new_path)


def test_rotation_is_idempotent(env, monkeypatch):
    make_shard(config.RAW_DIR)
    monkeypatch.setattr(r2, "client", lambda: FakeS3())

    first = rotate_raw.run(days=0)
    second = rotate_raw.run(days=0)

    assert first["rotated"] == 1
    assert second == {"due": 0, "rotated": 0, "failed": 0, "bytes": 0,
                      "skipped": None}


def test_unconfigured_r2_degrades_instead_of_crashing(env, monkeypatch):
    """No credentials on the box is a degraded state, not an outage. The logger
    keeps capturing; the disk guard is what stops that hurting anything."""
    make_shard(config.RAW_DIR)
    monkeypatch.setattr(config, "R2_BUCKET", None)

    stats = rotate_raw.run(days=0)

    assert stats["rotated"] == 0
    assert "not configured" in stats["skipped"].lower()
    assert store.health("rotate_raw")[1] == 0


# --- disk headroom -----------------------------------------------------------

def test_low_disk_degrades_rather_than_crashing(env, monkeypatch):
    """The acceptance case. Below the floor the archive stops and everything
    else keeps working - a quote row is small and is what the scoreboard reads,
    a raw payload is what fills a disk."""
    monkeypatch.setattr(store, "disk_free_bytes",
                        lambda path=None: int(0.5e9))     # 0.5GB, floor is 5GB

    ref = store.archive_raw("kalshi", "orderbooks", {"orderbooks": []})

    assert ref is None                                     # skipped, not raised
    assert not os.path.isdir(os.path.join(config.RAW_DIR, "kalshi"))

    ok, detail = store.health("disk")[1], store.health("disk")[2]
    assert ok == 0 and "suspended" in detail

    # and the capture path keeps going: quotes are what must not be lost
    n = store.write_quotes([{"ts": time.time(), "sport": "nfl", "venue": "kalshi",
                             "market_id": "M1", "best_bid": 0.4, "best_ask": 0.42,
                             "mid": 0.41}])
    assert n == 1


def test_archiving_resumes_when_space_comes_back(env, monkeypatch):
    """Rotation frees space; the guard must not latch."""
    monkeypatch.setattr(store, "disk_free_bytes", lambda path=None: int(0.5e9))
    assert store.archive_raw("kalshi", "orderbooks", {}) is None

    monkeypatch.setattr(store, "disk_free_bytes", lambda path=None: int(50e9))
    ref = store.archive_raw("kalshi", "orderbooks", {"orderbooks": []})

    assert ref is not None
    assert os.path.exists(os.path.join(config.RAW_DIR, ref))
    assert store.health("disk")[1] == 1


def test_headroom_check_is_cached_not_per_write(env, monkeypatch):
    """archive_raw runs thousands of times an hour; the check must not be a
    stat call each time."""
    calls = []
    real = store.shutil.disk_usage

    def counted(path):
        calls.append(path)
        return real(path)

    monkeypatch.setattr(store.shutil, "disk_usage", counted)
    store.reset_disk_cache()
    for _ in range(20):
        store.disk_free_bytes()

    assert len(calls) == 1


# --- quotes retention --------------------------------------------------------

def _quote(ts, market_id):
    return {"ts": ts, "sport": "nfl", "venue": "kalshi", "market_id": market_id,
            "best_bid": 0.4, "best_ask": 0.42, "mid": 0.41}


def test_prune_deletes_only_rows_past_the_window(env):
    now = time.time()
    store.write_quotes([_quote(now - 40 * 86400, "old"), _quote(now, "new")])

    stats = prune_quotes.run(days=21)

    assert stats["deleted"] == 1 and stats["remaining"] == 1
    with store.db() as c:
        assert [r[0] for r in c.execute("SELECT market_id FROM quotes")] == ["new"]


def test_prune_dry_run_deletes_nothing(env):
    now = time.time()
    store.write_quotes([_quote(now - 40 * 86400, "old")])

    stats = prune_quotes.run(days=21, dry_run=True)

    assert stats["candidates"] == 1 and stats["deleted"] == 0
    with store.db() as c:
        assert c.execute("SELECT COUNT(*) FROM quotes").fetchone()[0] == 1


def test_prune_leaves_the_coverage_record_alone(env):
    """poll_log is how gaps stay visible; pruning quotes must not erase the
    evidence of whether they were ever captured."""
    store.log_poll("kalshi", "quotes:game", 10, 10, True, None, 0.1)
    with store.db() as c:
        c.execute("UPDATE poll_log SET ts = ts - ?", (99 * 86400,))
    store.write_quotes([_quote(time.time() - 40 * 86400, "old")])

    prune_quotes.run(days=21)

    with store.db() as c:
        assert c.execute("SELECT COUNT(*) FROM poll_log").fetchone()[0] == 1
        assert c.execute("SELECT COUNT(*) FROM markets").fetchone()[0] == 0


# --- dead-man switch ---------------------------------------------------------

def test_deadman_fires_when_nothing_has_succeeded(env):
    """The failure this exists for is the process that stays up and quietly
    stops capturing - from outside, identical to a healthy quiet night."""
    from run_logger import deadman_status
    now = time.time()

    dead, stale, newest = deadman_status(
        [("kalshi", now - 40 * 60), ("polymarket", now - 45 * 60)],
        now, limit_min=20)

    assert dead is True
    assert set(stale) == {"kalshi", "polymarket"}
    assert newest == pytest.approx(now - 40 * 60)


def test_deadman_is_quiet_while_data_is_arriving(env):
    from run_logger import deadman_status
    now = time.time()

    dead, stale, _ = deadman_status(
        [("kalshi", now - 30), ("polymarket", now - 45)], now, limit_min=20)

    assert dead is False and stale == []


def test_deadman_flags_one_silent_venue_without_declaring_death(env):
    """One venue dropping out of discovery is the quiet failure poll_log alone
    does not surface - the others keep the global timestamp fresh."""
    from run_logger import deadman_status
    now = time.time()

    dead, stale, _ = deadman_status(
        [("kalshi", now - 30), ("polymarket", now - 90 * 60)], now, limit_min=20)

    assert dead is False
    assert stale == ["polymarket"]


def test_deadman_treats_an_empty_poll_log_as_dead(env):
    """A logger that has never succeeded is not 'not yet stale'."""
    from run_logger import deadman_status

    dead, stale, newest = deadman_status([], time.time(), limit_min=20)

    assert dead is True and newest is None
