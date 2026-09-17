"""The Odds API for college football: the FREE endpoints only, and the guards.

THE CREDIT POOL IS SHARED with the live NFL logger (same key, `venues/oddsapi.py`
in the main clone). This module never imports that one: NFL code is not a
dependency of the CFB track, and a change there must not move a CFB spend.

What this module may do today (phase 3 step 2, coverage measurement):

  GET /v4/sports                         "does not count against the usage quota"
  GET /v4/sports/{sport}/events          "does not count against the usage quota"

Nothing else can be built. A paid endpoint (bulk odds, event markets, anything
historical) is added only against a number Ethan has approved, with its own
brakes - it is deliberately absent here, not merely unused.

The docs are a claim, so every response is checked against the server:

  1. `x-requests-last` must be present and 0. Anything else is recorded as
     `charged_<n>` and raises `UnexpectedCharge`, which stops the run.
  2. EVERY request - failures included - is a row in `oddsapi_requests` before
     its body is touched, with host and checkout in `origin`. The API key is
     never written: not in the ledger, not in an exception, not in the archive.
  3. A reserve is only ever read from the environment. `config.ODDS_RESERVE`
     defaults to 40 when unset, and this clone's `.env` did not set it, so the
     NFL default would have silently become the CFB floor.
"""
import json
import os
import socket
import time
import uuid
from datetime import datetime, timezone

import httpx

import config

SPORT = "americanfootball_ncaaf"
USER_AGENT = "calibrated-sports-cfb-ingest"

# endpoint name -> path template. Free endpoints only; see the module docstring.
FREE = {
    "sports": "/sports",
    "events": "/sports/{sport}/events",
}


class UnexpectedCharge(Exception):
    pass


class OddsApiError(Exception):
    pass


def reserve() -> int:
    """The credit floor, from the environment and nowhere else."""
    v = os.getenv("ODDS_RESERVE")
    if v is None or not v.strip().isdigit():
        raise OddsApiError("ODDS_RESERVE is not set in the environment; the config default "
                           "(40) is the NFL fallback and is not a CFB floor - refusing")
    return int(v)


def build_url(endpoint: str, sport: str = SPORT) -> str:
    if endpoint not in FREE:
        raise ValueError(f"Odds API endpoint {endpoint!r} is not allowed; this module builds "
                         f"only the free endpoints {sorted(FREE)}")
    return config.ODDS_BASE.rstrip("/") + FREE[endpoint].format(sport=sport)


def origin() -> str:
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return f"{socket.gethostname()}|{repo}|jobs.ingest_cfb"


def month_key(ts=None):
    return datetime.fromtimestamp(ts or time.time(), timezone.utc).strftime("%Y-%m")


def _int(v):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


class Client:
    def __init__(self, conn, http: httpx.Client | None = None, api_key: str | None = None):
        self.conn = conn
        self.api_key = api_key if api_key is not None else config.ODDS_API_KEY
        if not self.api_key:
            raise OddsApiError("ODDS_API_KEY is not set - refusing before any request")
        self.http = http or httpx.Client(timeout=60, headers={"User-Agent": USER_AGENT})
        self.run_id = uuid.uuid4().hex[:12]
        self.origin = origin()

    def _log(self, endpoint, sport, status, last, remaining, used, nbytes, outcome):
        cur = self.conn.execute(
            "INSERT INTO oddsapi_requests (ts, month, endpoint, sport_key, status, cost_last, "
            "remaining, used, bytes, file_id, run_id, origin, outcome) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (time.time(), month_key(), endpoint, sport, status, last, remaining, used, nbytes,
             None, self.run_id, self.origin, outcome))
        self.conn.commit()
        return cur.lastrowid

    def settle(self, row, file_id, outcome):
        self.conn.execute("UPDATE oddsapi_requests SET file_id=?, outcome=? WHERE rowid=?",
                          (file_id, outcome, row))
        self.conn.commit()

    def get_free(self, endpoint: str, sport: str = SPORT):
        """One free request. Returns (body bytes, headers dict, ledger row).
        Raises OddsApiError on transport failure or non-200, UnexpectedCharge if
        the server billed it."""
        url = build_url(endpoint, sport)
        try:
            r = self.http.get(url, params={"apiKey": self.api_key})
        except httpx.HTTPError as e:
            # The exception text can carry the request URL, which carries the key.
            self._log(endpoint, sport, None, None, None, None, None, f"error: {type(e).__name__}")
            raise OddsApiError(f"/{endpoint}: {type(e).__name__}") from None
        last_raw = r.headers.get("x-requests-last")
        last = _int(last_raw)
        remaining = _int(r.headers.get("x-requests-remaining"))
        used = _int(r.headers.get("x-requests-used"))
        if last_raw is None or last != 0:
            outcome = "charge_unknown" if last_raw is None else f"charged_{last}"
            self._log(endpoint, sport, r.status_code, last, remaining, used, len(r.content), outcome)
            raise UnexpectedCharge(
                f"/{endpoint} was documented free but x-requests-last is {last_raw!r} "
                f"(remaining {remaining}); stopping")
        row = self._log(endpoint, sport, r.status_code, last, remaining, used, len(r.content), None)
        if r.status_code != 200:
            self.settle(row, None, f"http_{r.status_code}")
            raise OddsApiError(f"/{endpoint} returned {r.status_code}: "
                               f"{r.content[:200].decode('utf-8', 'replace')}")
        return r.content, {"remaining": remaining, "used": used, "last": last}, row


def events_from(body: bytes) -> list[dict]:
    data = json.loads(body)
    if not isinstance(data, list):
        raise OddsApiError(f"events response is {type(data).__name__}, expected a list")
    return data
