"""Cloudflare R2 (S3-compatible) object storage for the raw archive.

Used only by `jobs/rotate_raw.py`. The logger itself never talks to R2 - the
capture path must not depend on a network service being up.

Why R2 rather than S3: egress is free, which is what makes verify-by-download
affordable. Every shard that leaves local disk is read back and hashed first,
because "the upload returned 200" and "the bytes are there" are different
claims, and the archive is the thing every derivation is re-runnable from.

Credentials come from the environment (R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY,
R2_ACCOUNT_ID, R2_BUCKET). `.env` is gitignored; nothing here reads a file.
"""
import hashlib
import os

import config

CHUNK = 1 << 20      # 1MB, both for hashing and for streaming a verify read


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(CHUNK), b""):
            h.update(block)
    return h.hexdigest()


class R2Unavailable(RuntimeError):
    """R2 is not configured or boto3 is missing. Callers skip, never crash."""


def client():
    """An S3 client pointed at the R2 endpoint.

    boto3 is imported here rather than at module scope so that the logger, the
    tests and `verify.py` all import cleanly on a box that has never installed
    it. Rotation is the only thing that needs it.
    """
    if not config.r2_configured():
        raise R2Unavailable(
            "R2 not configured - set R2_ACCOUNT_ID, R2_BUCKET, R2_ACCESS_KEY_ID "
            "and R2_SECRET_ACCESS_KEY (see .env.example)")
    try:
        import boto3
        from botocore.config import Config
    except ImportError as e:
        raise R2Unavailable("boto3 not installed - pip install boto3") from e

    return boto3.client(
        "s3",
        endpoint_url=config.R2_ENDPOINT,
        aws_access_key_id=config.R2_ACCESS_KEY_ID,
        aws_secret_access_key=config.R2_SECRET_ACCESS_KEY,
        # R2 ignores the region but botocore insists on one being set.
        region_name="auto",
        config=Config(retries={"max_attempts": 5, "mode": "standard"},
                      signature_version="s3v4"),
    )


def key_for(rel_path: str) -> str:
    """Object key for a shard, mirroring its on-disk layout under R2_PREFIX."""
    rel = rel_path.replace(os.sep, "/").lstrip("/")
    prefix = (config.R2_PREFIX or "").strip("/")
    return f"{prefix}/{rel}" if prefix else rel


def upload(s3, local_path: str, key: str, sha256: str = None) -> dict:
    """Put one shard. Returns the head of the stored object."""
    extra = {"Metadata": {"sha256": sha256}} if sha256 else {}
    s3.upload_file(local_path, config.R2_BUCKET, key, ExtraArgs=extra or None)
    return s3.head_object(Bucket=config.R2_BUCKET, Key=key)


def verify(s3, key: str, expect_bytes: int, expect_sha256: str,
           download: bool = None) -> tuple[bool, str]:
    """Is the object really there, and really the same bytes?

    Size first because it is one cheap call and catches a truncated upload.
    Then, unless explicitly relaxed, stream the object back and hash it: an
    ETag on a multipart upload is not the MD5 of the content, so it cannot be
    compared against anything we computed locally, and a hash we did not check
    is a hash we are only pretending to have.
    """
    try:
        head = s3.head_object(Bucket=config.R2_BUCKET, Key=key)
    except Exception as e:
        return False, f"head failed: {type(e).__name__}: {e}"

    size = head.get("ContentLength")
    if size != expect_bytes:
        return False, f"size mismatch: remote {size} vs local {expect_bytes}"

    if download is None:
        download = config.RAW_VERIFY_DOWNLOAD
    if not download:
        return True, f"size ok ({size} bytes), content not re-read"

    h = hashlib.sha256()
    try:
        body = s3.get_object(Bucket=config.R2_BUCKET, Key=key)["Body"]
        for block in iter(lambda: body.read(CHUNK), b""):
            h.update(block)
    except Exception as e:
        return False, f"read-back failed: {type(e).__name__}: {e}"

    got = h.hexdigest()
    if got != expect_sha256:
        return False, f"sha256 mismatch: remote {got[:12]} vs local {expect_sha256[:12]}"
    return True, f"verified {size} bytes, sha256 {got[:12]}"


def fetch(s3, key: str, dest_path: str):
    """Pull a rotated shard back. The recovery path for a re-derivation."""
    os.makedirs(os.path.dirname(dest_path) or ".", exist_ok=True)
    s3.download_file(config.R2_BUCKET, key, dest_path)
    return dest_path
