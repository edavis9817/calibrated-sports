"""GitHub release fetching: bounded, throttled, resumable.

BOUNDED. The caller passes a finite plan from `cfb.sources.plan`. This module
never discovers work: one release listing per (repo, tag) per run, cached, and
at most `max_files` downloads.

THROTTLED. Downloads are sequential with `DOWNLOAD_GAP_S` between them. The
REST listing is "60 requests per hour" unauthenticated; the last reported
`x-ratelimit-remaining` is kept and a listing is refused below
`MIN_API_REMAINING`. A 403 or 429 from anywhere STOPS the run - it does not
retry, and it does not move on to the next file.

RESUMABLE. A download streams to `cache/<name>.part`, is size-checked against
the listing, hashed, and only then moved into `raw/`. An interrupted run leaves
a .part file that the next run overwrites; nothing half-written is ever
manifested.
"""
import hashlib
import os
import time

import httpx

DOWNLOAD_GAP_S = 0.5
MIN_API_REMAINING = 5
USER_AGENT = "calibrated-sports-cfb-ingest"


class RateLimited(Exception):
    pass


class Client:
    def __init__(self, conn, http: httpx.Client | None = None):
        self.conn = conn
        self.http = http or httpx.Client(follow_redirects=True, timeout=180,
                                         headers={"User-Agent": USER_AGENT})
        self.api_remaining = None
        self._releases = {}
        self._last_download = 0.0

    def _log(self, url, status, nbytes, remaining):
        self.conn.execute("INSERT INTO cfb_http_log VALUES (?,?,?,?,?)",
                          (time.time(), url, status, nbytes, remaining))
        self.conn.commit()

    @staticmethod
    def _stop_if_limited(r, url):
        if r.status_code in (403, 429):
            body = r.read()[:200].decode("utf-8", "replace")
            raise RateLimited(f"{r.status_code} from {url}: {body}")

    def release_assets(self, repo, tag) -> dict:
        """{asset name: {size, updated_at, url}} for one release. One request."""
        if (repo, tag) in self._releases:
            return self._releases[(repo, tag)]
        if self.api_remaining is not None and self.api_remaining < MIN_API_REMAINING:
            raise RateLimited(f"GitHub API remaining {self.api_remaining} "
                              f"< {MIN_API_REMAINING}; refusing to list {tag}")
        url = f"https://api.github.com/repos/{repo}/releases/tags/{tag}"
        r = self.http.get(url, headers={"Accept": "application/vnd.github+json"})
        rem = r.headers.get("x-ratelimit-remaining")
        self.api_remaining = int(rem) if rem and rem.isdigit() else self.api_remaining
        self._log(url, r.status_code, len(r.content), self.api_remaining)
        self._stop_if_limited(r, url)
        r.raise_for_status()
        rel = r.json()
        assets = rel.get("assets") or []
        if len(assets) >= 100:
            # The inline list may be truncated past this size. Rather than page
            # silently, refuse: a short asset list reads as "season missing".
            raise RuntimeError(f"{repo}@{tag} lists {len(assets)} assets inline; "
                               f"paging is not implemented, refusing to guess")
        out = {a["name"]: {"size": a["size"], "updated_at": a["updated_at"],
                           "url": a["browser_download_url"]} for a in assets}
        self._releases[(repo, tag)] = out
        return out

    def download(self, url, part_path, expected_size) -> tuple[int, str]:
        """Stream to `part_path`. Returns (bytes, sha256). Raises on a short or
        long file - a size mismatch is a failed download, not a new version."""
        wait = DOWNLOAD_GAP_S - (time.time() - self._last_download)
        if wait > 0:
            time.sleep(wait)
        os.makedirs(os.path.dirname(part_path), exist_ok=True)
        h = hashlib.sha256()
        n = 0
        try:
            with self.http.stream("GET", url) as r:
                self._stop_if_limited(r, url)
                r.raise_for_status()
                with open(part_path, "wb") as f:
                    for chunk in r.iter_bytes(1 << 20):
                        f.write(chunk)
                        h.update(chunk)
                        n += len(chunk)
            self._log(url, r.status_code, n, None)
        finally:
            self._last_download = time.time()
        if expected_size is not None and n != expected_size:
            raise IOError(f"{url}: got {n} bytes, listing says {expected_size}")
        return n, h.hexdigest()
