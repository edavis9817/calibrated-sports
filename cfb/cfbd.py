"""collegefootballdata.com: the one metered source, and everything that guards it.

THE QUOTA IS SHARED. Free tier "1,000 calls/month" (collegefootballdata.com/
api-tiers), one unit per request whatever it returns, non-2xx refunded, and
"Requests to CFBD and CBBD count against the same shared quota pool"
(collegefootballdata.com/terms). The main clone spends from the same key. So:

  1. The work list is built FIRST and is finite: a request is a (endpoint,
     params) pair from `lines_backfill` or `week`, nothing else.
  2. A run may make at most MAX_REQUESTS_PER_RUN metered requests. The cap is a
     constant; `--max-requests` can lower it, never raise it.
  3. Before any metered request, GET /info - unmetered per CFBD's server code -
     and REFUSE the whole run unless remainingCalls minus the planned count stays
     above `config.CFBD_RESERVE`. An /info that fails is a refusal: an unknown
     quota must not read as an infinite one.
  4. After every request, `X-CallLimit-Remaining` is re-read and the run stops
     at the reserve.
  5. EVERY request - /info included, failures included - is a row in
     `cfbd_requests` BEFORE its body is touched, with the host and checkout in
     `origin`, so spend stays attributable across clones.
  6. Only year/week-level endpoints can be built. A `gameId`, `id` or `team`
     parameter raises: a per-game loop is ~800 requests a season.
"""
import json
import os
import socket
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

import httpx

import config

MAX_REQUESTS_PER_RUN = 20
USER_AGENT = "calibrated-sports-cfb-ingest"

# endpoint -> the only parameters it may be called with. `year` is required.
ALLOWED = {
    "games": {"year", "week", "seasonType"},
    "lines": {"year", "week", "seasonType"},
}
SEASON_TYPES = {"regular", "postseason", "both"}


class BudgetRefused(Exception):
    pass


class CfbdError(Exception):
    pass


@dataclass(frozen=True)
class Request:
    dataset: str          # cfbd_games | cfbd_lines
    endpoint: str
    season: int
    part: str             # "both" (a season) or "regular:w3" (a week)
    params: tuple = field(default=())

    def param_dict(self):
        return dict(self.params)


def build_url(endpoint: str, params: dict) -> str:
    if endpoint not in ALLOWED:
        raise ValueError(f"CFBD endpoint {endpoint!r} is not allowed; allowed: {sorted(ALLOWED)}")
    extra = set(params) - ALLOWED[endpoint]
    if extra:
        raise ValueError(f"CFBD /{endpoint} may not be called with {sorted(extra)} - "
                         f"only season- or week-level requests are allowed")
    if "year" not in params:
        raise ValueError(f"CFBD /{endpoint} requires year")
    if params.get("seasonType", "regular") not in SEASON_TYPES:
        raise ValueError(f"seasonType {params['seasonType']!r} not in {sorted(SEASON_TYPES)}")
    return str(httpx.URL(f"{config.CFBD_BASE}/{endpoint}", params=params))


def lines_backfill(seasons) -> list[Request]:
    """One request per season, regular AND postseason (`seasonType=both`)."""
    return [Request("cfbd_lines", "lines", y, "both",
                    (("year", y), ("seasonType", "both"))) for y in sorted(seasons)]


def week(season: int, wk: int, season_type: str = "regular") -> list[Request]:
    """Two requests: that week's results and its lines."""
    part = f"{season_type}:w{wk}"
    p = (("year", season), ("week", wk), ("seasonType", season_type))
    return [Request("cfbd_games", "games", season, part, p),
            Request("cfbd_lines", "lines", season, part, p)]


def part_types(part: str) -> set:
    base = part.split(":")[0]
    return {"regular", "postseason"} if base == "both" else {base}


def parts_overlap(a: str, b: str) -> bool:
    """Two different scopes that could carry the same game. A season-level part
    overlaps any week of a season type it covers; two weeks never overlap."""
    if a == b or not (part_types(a) & part_types(b)):
        return False
    return ":w" not in a or ":w" not in b


def month_key(ts=None):
    return datetime.fromtimestamp(ts or time.time(), timezone.utc).strftime("%Y-%m")


def origin() -> str:
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return f"{socket.gethostname()}|{repo}|jobs.ingest_cfb"


class Client:
    def __init__(self, conn, http: httpx.Client | None = None, api_key: str | None = None):
        self.conn = conn
        self.api_key = api_key if api_key is not None else config.CFBD_API_KEY
        if not self.api_key:
            raise BudgetRefused("CFBD_API_KEY is not set - refusing before any request")
        self.http = http or httpx.Client(timeout=120, headers={"User-Agent": USER_AGENT})
        self.run_id = uuid.uuid4().hex[:12]
        self.origin = origin()
        self.metered = 0

    def _headers(self):
        return {"Authorization": f"Bearer {self.api_key}", "Accept": "application/json"}

    def _log(self, endpoint, params, status, metered, remaining, used, nbytes, outcome):
        cur = self.conn.execute(
            "INSERT INTO cfbd_requests (ts, month, endpoint, params, status, metered, remaining, "
            "used, bytes, file_id, run_id, origin, outcome) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (time.time(), month_key(), endpoint, json.dumps(params) if params else None, status,
             metered, remaining, used, nbytes, None, self.run_id, self.origin, outcome))
        self.conn.commit()
        return cur.lastrowid

    def settle(self, request_row, file_id, outcome):
        self.conn.execute("UPDATE cfbd_requests SET file_id=?, outcome=? WHERE rowid=?",
                          (file_id, outcome, request_row))
        self.conn.commit()

    def info(self) -> dict:
        """Unmetered. Logged anyway, with metered=0."""
        url = f"{config.CFBD_BASE}/info"
        try:
            r = self.http.get(url, headers=self._headers())
        except httpx.HTTPError as e:
            self._log("info", None, None, 0, None, None, None, f"error: {type(e).__name__}")
            raise BudgetRefused(f"/info unreachable ({type(e).__name__}); quota unknown, refusing")
        body = r.json() if r.status_code == 200 else None
        rem = body.get("remainingCalls") if body else None
        used = body.get("usedCalls") if body else None
        self._log("info", None, r.status_code, 0, rem, used, len(r.content),
                  "ok" if r.status_code == 200 else "refused")
        if r.status_code != 200 or rem is None:
            raise BudgetRefused(f"/info returned {r.status_code}; quota unknown, refusing")
        return body

    def get(self, req: Request):
        """One metered request. Returns (status, body bytes, remaining, ledger row)."""
        if self.metered >= MAX_REQUESTS_PER_RUN:
            raise BudgetRefused(f"run cap of {MAX_REQUESTS_PER_RUN} metered requests reached")
        url = build_url(req.endpoint, req.param_dict())
        self.metered += 1
        try:
            r = self.http.get(url, headers=self._headers())
        except httpx.HTTPError as e:
            row = self._log(req.endpoint, req.param_dict(), None, 1, None, None, None,
                            f"error: {type(e).__name__}")
            raise CfbdError(f"{url}: {type(e).__name__}: {e}") from e
        rem = r.headers.get("x-calllimit-remaining")
        rem = int(rem) if rem and rem.isdigit() else None
        row = self._log(req.endpoint, req.param_dict(), r.status_code, 1, rem, None,
                        len(r.content), None)
        return r.status_code, r.content, rem, row
