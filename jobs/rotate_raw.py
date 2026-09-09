"""Rotate raw shards older than N days to R2, then reclaim the local space.

    python -m jobs.rotate_raw                 # rotate anything older than 7d
    python -m jobs.rotate_raw --days 3
    python -m jobs.rotate_raw --dry-run       # say what it would move
    python -m jobs.rotate_raw --status        # where does the archive live now

Sources named in RAW_ROTATE_EXEMPT are skipped entirely - see is_exempt().

Invariant #2 says the raw archive is what every derivation is re-runnable from,
so nothing here deletes data. A shard is uploaded, read back, hashed, and only
then removed from local disk - and the `raw_shards` manifest keeps answering
"where is that hour?" afterwards.

The ordering matters and is the whole job:

    hash local -> upload -> verify remote -> delete local -> mark remote

Every step is idempotent and the job is safe to interrupt at any point. Crash
after upload but before delete and the next run re-verifies and deletes. Crash
after delete but before the manifest write and the next run sees a shard marked
uploaded whose local copy is gone, re-verifies the remote, and reconciles. The
only state that is never reachable is "local copy deleted, remote unverified".
"""
import argparse
import os
import time
from datetime import datetime, timezone

import config
import r2
import store

SOURCE = "rotate_raw"


def _shard_day(rel_path: str):
    parts = rel_path.replace(os.sep, "/").split("/")
    if len(parts) < 2:
        return None
    try:
        return datetime.strptime(parts[1], "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def local_shards(root: str = None):
    """Every shard on disk, as (rel_path, abs_path, day). Newest hour excluded
    by the caller's age cutoff, never by guessing which file is still open."""
    root = root or config.RAW_DIR
    out = []
    if not os.path.isdir(root):
        return out
    for dirpath, _dirs, files in os.walk(root):
        for name in files:
            if not name.endswith(".jsonl.gz"):
                continue
            abs_path = os.path.join(dirpath, name)
            rel = os.path.relpath(abs_path, root).replace(os.sep, "/")
            day = _shard_day(rel)
            if day is None:
                continue
            out.append((rel, abs_path, day))
    return sorted(out)


def is_exempt(rel_path: str) -> bool:
    """Sources retention must never touch.

    The nflverse mirror lives under the same RAW_DIR but is not market data and
    is not a storage problem: the whole corpus is under 1GB and weekly in-season
    snapshots add ~25MB. Those dated snapshots are the only defence against a
    stat correction silently rewriting history, so shipping them off the box -
    or ageing them out - would defeat the reason they exist. Market data is what
    fills a disk; this is a rounding error.
    """
    source = rel_path.replace(os.sep, "/").split("/")[0]
    return source in config.RAW_ROTATE_EXEMPT


def due(days: float = None, now: float = None, root: str = None):
    """Shards old enough to rotate. Age is measured from the END of the shard's
    UTC day, so a shard is never moved while its day could still be written to.
    """
    days = config.RAW_ROTATE_DAYS if days is None else days
    now = now or time.time()
    cutoff = now - days * 86400
    out = []
    for rel, abs_path, day in local_shards(root):
        if is_exempt(rel):
            continue
        day_end = day.timestamp() + 86400
        if day_end <= cutoff:
            out.append((rel, abs_path))
    return out


def rotate_one(s3, rel_path: str, abs_path: str, dry_run: bool = False) -> tuple[bool, str]:
    """Move one shard to R2. Returns (rotated, detail)."""
    size = os.path.getsize(abs_path)
    key = r2.key_for(rel_path)
    if dry_run:
        return False, f"would rotate {size/1e6:.1f}MB -> {key}"

    digest = r2.sha256_file(abs_path)
    store.note_shard(rel_path, bytes=size, sha256=digest, state="local",
                     remote_bucket=config.R2_BUCKET, remote_key=key)

    # Re-uploading an object that is already there is cheap and idempotent; not
    # re-uploading one that only looks present is how archives get holes.
    r2.upload(s3, abs_path, key, sha256=digest)
    store.note_shard(rel_path, uploaded_ts=time.time())

    ok, detail = r2.verify(s3, key, size, digest)
    if not ok:
        # Leave the local copy exactly where it is. A failed verify is a reason
        # to keep two copies, never a reason to have none.
        store.note_shard(rel_path, state="local")
        return False, f"verify failed, local copy kept: {detail}"

    store.note_shard(rel_path, verified_ts=time.time())
    os.remove(abs_path)
    store.note_shard(rel_path, state="remote", deleted_local_ts=time.time())
    _prune_empty_dirs(os.path.dirname(abs_path))
    return True, detail


def _prune_empty_dirs(path: str):
    """Tidy the day/venue directories a rotation empties. Stops at RAW_DIR."""
    root = os.path.abspath(config.RAW_DIR)
    path = os.path.abspath(path)
    while path.startswith(root) and path != root:
        try:
            os.rmdir(path)
        except OSError:
            return
        path = os.path.dirname(path)


def run(days: float = None, dry_run: bool = False, limit: int = None) -> dict:
    """Rotate everything due. Never raises - records health and reports."""
    limit = limit or config.RAW_ROTATE_MAX_SHARDS
    shards = due(days)[:limit]
    stats = {"due": len(shards), "rotated": 0, "failed": 0, "bytes": 0,
             "skipped": None}

    if not shards:
        store.record_health(SOURCE, True, "nothing due", watermark=time.time())
        return stats

    try:
        s3 = r2.client()
    except r2.R2Unavailable as e:
        # Not configured is a degraded state, not a crash: the logger keeps
        # capturing and the disk guard is what stops it hurting anything.
        stats["skipped"] = str(e)
        store.record_health(SOURCE, False, f"{len(shards)} shards due, {e}")
        return stats

    for rel, abs_path in shards:
        size = os.path.getsize(abs_path)
        try:
            rotated, detail = rotate_one(s3, rel, abs_path, dry_run)
        except Exception as e:
            rotated, detail = False, f"{type(e).__name__}: {e}"
        if rotated:
            stats["rotated"] += 1
            stats["bytes"] += size
        elif not dry_run:
            stats["failed"] += 1
        print(f"  {'OK  ' if rotated else 'SKIP'} {rel}  {detail}")

    ok = stats["failed"] == 0
    store.record_health(
        SOURCE, ok,
        f"rotated {stats['rotated']}/{stats['due']} shards "
        f"({stats['bytes']/1e6:.1f}MB reclaimed), {stats['failed']} failed",
        watermark=time.time())
    return stats


def status():
    counts = store.shard_counts()
    local_bytes = sum(os.path.getsize(p) for _, p, _ in local_shards())
    print(f"manifest: {counts}")
    exempt = [(r, p) for r, p, _ in local_shards() if is_exempt(r)]
    print(f"local:    {len(local_shards())} shards, {local_bytes/1e9:.2f}GB")
    print(f"exempt:   {len(exempt)} shards, "
          f"{sum(os.path.getsize(p) for _, p in exempt)/1e9:.2f}GB "
          f"({', '.join(config.RAW_ROTATE_EXEMPT) or 'none'})")
    print(f"due now:  {len(due())} shards")
    print(f"R2:       {'configured' if config.r2_configured() else 'NOT CONFIGURED'} "
          f"bucket={config.R2_BUCKET} prefix={config.R2_PREFIX}")
    for row in store.health() or []:
        print(f"health:   {row[0]:<14} ok={row[1]} {row[2]}")


def check() -> bool:
    """Preflight against the real bucket: put a small object, head it, read it
    back, hash it, delete it.

    The unit tests exercise the rotation LOGIC against a fake S3. They cannot
    tell you that this box's credentials, endpoint and bucket policy actually
    work - and finding that out for the first time on a shard you are about to
    delete locally is the wrong moment. Run this once after setting the
    credentials.
    """
    import hashlib
    import tempfile

    try:
        s3 = r2.client()
    except r2.R2Unavailable as e:
        print(f"  [FAIL] {e}")
        return False

    payload = b"calibrated-sports preflight " + str(time.time()).encode()
    digest = hashlib.sha256(payload).hexdigest()
    key = r2.key_for(f"_preflight/{int(time.time())}.bin")
    tmp = os.path.join(tempfile.gettempdir(), "cs_preflight.bin")
    with open(tmp, "wb") as f:
        f.write(payload)

    try:
        r2.upload(s3, tmp, key, sha256=digest)
        print(f"  [ok] uploaded {len(payload)} bytes -> {config.R2_BUCKET}/{key}")
        ok, detail = r2.verify(s3, key, len(payload), digest, download=True)
        print(f"  {'[ok]' if ok else '[FAIL]'} {detail}")
        s3.delete_object(Bucket=config.R2_BUCKET, Key=key)
        print("  [ok] deleted preflight object")
        store.record_health("r2", ok, detail, watermark=time.time())
        return ok
    except Exception as e:
        print(f"  [FAIL] {type(e).__name__}: {e}")
        store.record_health("r2", False, f"{type(e).__name__}: {e}")
        return False
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--days", type=float, default=None,
                    help=f"rotate shards older than this (default "
                         f"{config.RAW_ROTATE_DAYS:g})")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--check", action="store_true",
                    help="round-trip a test object against the real bucket")
    args = ap.parse_args()

    store.init_db()
    if args.status:
        status()
        return
    if args.check:
        print(f"R2 preflight -> {config.R2_ENDPOINT} bucket={config.R2_BUCKET}")
        raise SystemExit(0 if check() else 1)
    stats = run(days=args.days, dry_run=args.dry_run, limit=args.limit)
    if stats["skipped"]:
        print(f"SKIPPED: {stats['skipped']}")
    print(f"due={stats['due']} rotated={stats['rotated']} failed={stats['failed']} "
          f"reclaimed={stats['bytes']/1e6:.1f}MB")


if __name__ == "__main__":
    main()
