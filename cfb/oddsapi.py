"""The Odds API for college football: free endpoints, two approved paid shapes, the guards.

THE CREDIT POOL IS SHARED with the live NFL logger (same key, `venues/oddsapi.py`
in the main clone), and the NFL has first claim on it. This module never imports
that one: NFL code is not a dependency of the CFB track.

FREE (phase 3 step 2):

  GET /v4/sports                         "does not count against the usage quota"
  GET /v4/sports/{sport}/events          "does not count against the usage quota"

PAID, each approved by Ethan on 2026-09-17 against a costed number, with its
parameters FIXED here because the cost is a function of them:

  event_markets  GET /sports/{sport}/events/{id}/markets?regions=us      1 credit
                 P1: one pass over the listed events, P1_APPROVED_CREDITS in total
  odds           GET /sports/{sport}/odds?regions=us&markets=h2h,spreads,totals
                 3 credits; the forward capture, one per kickoff hour,
                 at most FORWARD_WEEKLY_CAP (75) a CFB week

Nothing else can be built: no historical endpoint, no event odds, no other market
or region. Adding one means a new approved number and a new entry in PAID.

The docs are a claim, so every response is checked against the server:

  1. A free call must carry `x-requests-last` 0. Anything else is recorded as
     `charged_<n>` / `charge_unknown` and raises `UnexpectedCharge`.
  2. A paid call needs the pool balance from a call made IN THIS RUN and refuses,
     before the request, if its expected cost exceeds the budget left or would
     take the pool below the reserve. `x-requests-last` above the expected cost
     (or absent) stops the run.
  3. EVERY request - failures included - is a row in `oddsapi_requests` before
     its body is touched, with `purpose` and host/checkout in `origin`. The API
     key is never written: not in the ledger, not in an exception, not in the
     archive.
  4. A reserve is only ever read from the environment. `config.ODDS_RESERVE`
     defaults to 40 when unset, and this clone's `.env` did not set it, so the
     NFL fallback would have silently become the CFB floor.
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

# endpoint name -> path template. Documented free.
FREE = {
    "sports": "/sports",
    "events": "/sports/{sport}/events",
}

# name -> (path template, fixed params, expected credits per call)
PAID = {
    "event_markets": ("/sports/{sport}/events/{event_id}/markets", {"regions": "us"}, 1),
    "odds": ("/sports/{sport}/odds",
             {"regions": "us", "markets": "h2h,spreads,totals", "oddsFormat": "american"}, 3),
}

# Approved budgets (Ethan, 2026-09-17). P1 is a LIFETIME total for purpose 'p1'.
# The forward cap is per CFB week, Tuesday 12:00Z to Tuesday 12:00Z.
P1_APPROVED_CREDITS = 74
P1_NOT_BEFORE = "2026-09-19T12:00:00Z"     # "Saturday morning": keys fill in near kickoff
# 75 = the measured maximum week (22 kickoff hours, 66 credits, the 2026-09-01 week)
# plus THREE hours of slack (75 - 66 = 9 credits, 3 credits an hour). Ethan, 2026-09-18, raising his own 45: that figure was
# set before a Saturday was known to hold 14 hours, and it had already bound twice - the
# 09-01 week by 21 credits and the 09-22 week by 6. Sizing to the measured maximum with no
# slack reproduces the same condition, and kickoff drift CREATES hours: a 23:30 kickoff
# moving to 23:33 cost nothing on 2026-09-17, but +90 minutes would have opened a new one.
# Worst case 75 x 13 weeks ~= 975 credits, about 4.2% of what is spendable above the
# reserve. A cap is a ceiling, not a spend: quiet weeks still cost 33-45.
# `research/cfb_forward_cap.py --season-hours` re-derives the schedule this is sized to.
FORWARD_WEEKLY_CAP = 75


class UnexpectedCharge(Exception):
    pass


class OddsApiError(Exception):
    pass


class BudgetRefused(Exception):
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
        raise ValueError(f"Odds API endpoint {endpoint!r} is not a free endpoint; free: "
                         f"{sorted(FREE)} (paid shapes go through build_paid)")
    return config.ODDS_BASE.rstrip("/") + FREE[endpoint].format(sport=sport)


def build_paid(name: str, sport: str = SPORT, event_id: str | None = None):
    """(url, params, expected cost). The parameters are fixed per shape."""
    if name not in PAID:
        raise ValueError(f"paid Odds API shape {name!r} is not approved; approved: {sorted(PAID)}")
    path, params, cost = PAID[name]
    if "{event_id}" in path:
        if not event_id or not event_id.isalnum():
            raise ValueError(f"{name} needs an alphanumeric event id, got {event_id!r}")
    elif event_id is not None:
        raise ValueError(f"{name} takes no event id")
    url = config.ODDS_BASE.rstrip("/") + path.format(sport=sport, event_id=event_id)
    return url, dict(params), cost


def origin() -> str:
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return f"{socket.gethostname()}|{repo}|jobs.ingest_cfb"


def month_key(ts=None):
    return datetime.fromtimestamp(ts or time.time(), timezone.utc).strftime("%Y-%m")


def week_start_ts(ts: float) -> float:
    """Start of the CFB week containing ts: the latest Tuesday 12:00Z at or before it."""
    d = datetime.fromtimestamp(ts, timezone.utc)
    anchor = d.replace(hour=12, minute=0, second=0, microsecond=0).timestamp()
    start = anchor - ((d.weekday() - 1) % 7) * 86400        # Monday=0, Tuesday=1
    return start if start <= ts else start - 7 * 86400


def iso_ts(s: str) -> float:
    return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


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
        self.remaining = None            # the pool balance as last reported IN THIS RUN

    def _log(self, endpoint, sport, status, last, remaining, used, nbytes, outcome,
             purpose="free", event_id=None):
        cur = self.conn.execute(
            "INSERT INTO oddsapi_requests (ts, month, endpoint, sport_key, status, cost_last, "
            "remaining, used, bytes, file_id, run_id, origin, outcome, purpose, event_id) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (time.time(), month_key(), endpoint, sport, status, last, remaining, used, nbytes,
             None, self.run_id, self.origin, outcome, purpose, event_id))
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
        if remaining is not None:
            self.remaining = remaining
        if r.status_code != 200:
            self.settle(row, None, f"http_{r.status_code}")
            raise OddsApiError(f"/{endpoint} returned {r.status_code}: "
                               f"{r.content[:200].decode('utf-8', 'replace')}")
        return r.content, {"remaining": remaining, "used": used, "last": last}, row

    def get_paid(self, name: str, purpose: str, budget_left: int, event_id: str | None = None,
                 sport: str = SPORT):
        """One paid request of an approved shape. Refuses BEFORE the request unless the
        expected cost fits `budget_left` and keeps the pool (as reported earlier in this
        run) at or above the reserve. Returns (status, body, cost, ledger row)."""
        url, params, expected = build_paid(name, sport, event_id)
        floor = reserve()
        if expected > budget_left:
            raise BudgetRefused(f"{purpose}: {name} costs {expected}; {budget_left} left of the "
                                f"approved budget")
        if self.remaining is None:
            raise BudgetRefused(f"{purpose}: pool balance unknown in this run; an unknown "
                                f"balance is not an infinite one")
        if self.remaining - expected < floor:
            raise BudgetRefused(f"{purpose}: pool {self.remaining} - {expected} would go below "
                                f"the {floor} reserve")
        params["apiKey"] = self.api_key
        try:
            r = self.http.get(url, params=params)
        except httpx.HTTPError as e:
            self._log(name, sport, None, None, None, None, None, f"error: {type(e).__name__}",
                      purpose, event_id)
            raise OddsApiError(f"{name}: {type(e).__name__}") from None
        last = _int(r.headers.get("x-requests-last"))
        remaining = _int(r.headers.get("x-requests-remaining"))
        used = _int(r.headers.get("x-requests-used"))
        row = self._log(name, sport, r.status_code, last, remaining, used, len(r.content), None,
                        purpose, event_id)
        if remaining is not None:
            self.remaining = remaining
        if last is None or last > expected:
            self.settle(row, None, "charge_unknown" if last is None else f"overcharged_{last}")
            raise UnexpectedCharge(f"{purpose}: {name} expected {expected} credits, "
                                   f"x-requests-last {last}; stopping")
        return r.status_code, r.content, last, row


def events_from(body: bytes) -> list[dict]:
    data = json.loads(body)
    if not isinstance(data, list):
        raise OddsApiError(f"events response is {type(data).__name__}, expected a list")
    return data
