"""Fetch, archive verbatim, manifest. One place, for all three feeds.

RAW FIRST. Every response is written to `feeds/raw/<feed>/...` and registered in
`feeds_raw_files` before anything parses it, so every derivation is re-runnable from the
archive. A copy is kept only when the CONTENT hash moves - an RSS document re-published
with a new build date but the same items is not a new version.

NEVER OVERWRITE. The path is proven unused, on disk AND in the manifest, before a byte is
written: the CFB archive had this bug (write-then-check) and it cost a file.
"""
import gzip
import hashlib
import json
import os
import time
from datetime import datetime, timezone

import httpx

from feeds import paths

USER_AGENT = "calibrated-sports-feeds (contact: github.com/edavis9817/calibrated-sports)"
GAP_S = 0.5


class FetchError(Exception):
    pass


def utc(ts):
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def content_hash(body: bytes, kind: str) -> str:
    """JSON is hashed canonically so key order cannot invent a version; a zip is hashed
    over its members' names and bytes, so a re-zip that changes only timestamps or
    compression cannot invent one either; anything else is hashed as bytes."""
    if kind == "zip":
        import io
        import zipfile
        h = hashlib.sha256()
        with zipfile.ZipFile(io.BytesIO(body)) as z:
            for name in sorted(z.namelist()):
                h.update(name.encode() + b"/")
                h.update(hashlib.sha256(z.read(name)).digest())
        return h.hexdigest()
    if kind == "json":
        try:
            return hashlib.sha256(json.dumps(json.loads(body), sort_keys=True,
                                             separators=(",", ":")).encode()).hexdigest()
        except ValueError:
            pass
    return hashlib.sha256(body).hexdigest()


# Manifests `archive` may write. The table name is interpolated into SQL, so it is
# checked against this list rather than trusted.
MANIFESTS = ("feeds_raw_files", "mlb_raw_files")


def archive(conn, feed, scope, url, body: bytes, fetched_ts=None, kind="bytes", suffix=".gz",
            *, raw_root=None, manifest="feeds_raw_files"):
    """Returns (file_id, outcome). `unchanged_content` keeps the earlier file.

    `raw_root` and `manifest` let another store (MLB) reuse this rather than copy it;
    the defaults are the feeds store, unchanged."""
    if manifest not in MANIFESTS:
        raise ValueError(f"not a raw manifest: {manifest!r}")
    raw_root = raw_root or paths.raw_root()
    fetched_ts = fetched_ts or time.time()
    bsha = hashlib.sha256(body).hexdigest()
    csha = content_hash(body, kind)
    newest = conn.execute(
        f"SELECT file_id, content_sha256 FROM {manifest} WHERE feed=? AND scope IS ? "
        "ORDER BY fetched_ts DESC LIMIT 1", (feed, scope)).fetchone()
    if newest and newest[1] == csha:
        return newest[0], "unchanged_content"

    # The scope is kept verbatim in the manifest and SANITISED for the path: Windows
    # cannot hold ':' in a filename, and "2026-09-18:forecast" is a legitimate scope.
    safe = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in str(scope or "all"))
    stem = "/".join([feed, safe, f"{utc(fetched_ts)}-{csha[:12]}"])
    rel, n = stem + suffix, 1
    while (os.path.exists(os.path.join(raw_root, *rel.split("/")))
           or conn.execute(f"SELECT 1 FROM {manifest} WHERE rel_path=?", (rel,)).fetchone()):
        n += 1
        rel = f"{stem}-{n}{suffix}"
    dest = os.path.join(raw_root, *rel.split("/"))
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with open(dest + ".part", "wb") as f:
        f.write(gzip.compress(body))
    os.replace(dest + ".part", dest)
    cur = conn.execute(
        f"INSERT INTO {manifest} (rel_path, feed, scope, url, bytes, bytes_sha256, "
        "content_sha256, fetched_ts) VALUES (?,?,?,?,?,?,?,?)",
        (rel, feed, str(scope) if scope is not None else None, url, len(body), bsha, csha,
         fetched_ts))
    conn.commit()
    return cur.lastrowid, "new"


def read_archived(conn, file_id, *, raw_root=None, manifest="feeds_raw_files") -> bytes:
    if manifest not in MANIFESTS:
        raise ValueError(f"not a raw manifest: {manifest!r}")
    raw_root = raw_root or paths.raw_root()
    rel = conn.execute(f"SELECT rel_path FROM {manifest} WHERE file_id=?",
                       (file_id,)).fetchone()[0]
    with gzip.open(os.path.join(raw_root, *rel.split("/")), "rb") as f:
        return f.read()


class Client:
    def __init__(self, conn, http: httpx.Client | None = None):
        self.conn = conn
        self.http = http or httpx.Client(follow_redirects=True, timeout=120,
                                         headers={"User-Agent": USER_AGENT})
        self._last = 0.0

    def _log(self, feed, url, status, nbytes, outcome):
        self.conn.execute("INSERT INTO feeds_http_log VALUES (?,?,?,?,?,?)",
                          (time.time(), feed, url, status, nbytes, outcome))
        self.conn.commit()

    def get(self, feed, url, params=None):
        wait = GAP_S - (time.time() - self._last)
        if wait > 0:
            time.sleep(wait)
        try:
            r = self.http.get(url, params=params)
        except httpx.HTTPError as e:
            self._log(feed, url, None, None, f"error: {type(e).__name__}")
            raise FetchError(f"{feed}: {type(e).__name__}") from None
        finally:
            self._last = time.time()
        self._log(feed, url, r.status_code, len(r.content),
                  "ok" if r.status_code == 200 else f"http_{r.status_code}")
        if r.status_code in (403, 429):
            raise FetchError(f"{feed}: {r.status_code} - stopping rather than retrying")
        if r.status_code != 200:
            raise FetchError(f"{feed}: {r.status_code} from {url}")
        return r.content

    def release_asset(self, feed, repo, tag, name):
        """One release listing per (repo, tag) per run, then the asset itself."""
        key = (repo, tag)
        cache = getattr(self, "_releases", None)
        if cache is None:
            cache = self._releases = {}
        if key not in cache:
            body = self.get(feed, f"https://api.github.com/repos/{repo}/releases/tags/{tag}")
            assets = json.loads(body).get("assets") or []
            if len(assets) >= 100:
                raise FetchError(f"{repo}@{tag} lists {len(assets)} assets inline; paging is "
                                 f"not implemented and a short list reads as 'absent'")
            cache[key] = {a["name"]: a["browser_download_url"] for a in assets}
        if name not in cache[key]:
            return None
        return self.get(feed, cache[key][name])
