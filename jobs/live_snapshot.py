"""The Live page's snapshot: one scheduled reader, so no page request ever calls a third party.

    python -m jobs.live_snapshot --once --no-upload [--out PATH]   one read, staged locally
    python -m jobs.live_snapshot --once                            one read, PUT to R2
    python -m jobs.live_snapshot --loop [--log]                    the scheduled process

WHY THIS EXISTS (unit a-23, audit P-live-01 / S-09). At 01:02 ET on 2026-09-24 the live
page was a wall of "could not be read ... HTTP 403" and "Exchange answered HTTP 429",
because the Worker read the scoreboard and the exchange AT REQUEST TIME. Every visitor
was a read, so traffic made the rate limit worse. The rule this establishes: no public
page calls a third party at request time. Every external read happens here, on a
schedule, and the page renders `live/{sport}/snapshot.json`.

WHAT THE FILE IS. A slate that always exists, with each source's read time beside it:
  * the SKELETON is the schedule already in the store (nflverse, read-only) - dates,
    kickoffs, teams, lines. It is present whenever the store is, so a scoreboard outage
    degrades the page to "fixtures, scores delayed, last read HH:MM" instead of an error;
  * the SCOREBOARD overlays state, clock and score on the games it can be joined to;
  * the EXCHANGE overlays the game-winner quotes (bid/ask, no mid, no de-vig);
  * the INJURY REPORT is the current version held in `feeds.db`, with the time THIS
    store captured each row - nflverse rewrites the file in place and carries no capture
    time of its own from 2025, so the capture stamp is the only date there is.
Out of season, overnight and between games the slate is the NEXT week with an
unfinished game, and `last_week` carries the finals before it. There is no state in
which the file has no slate while the schedule has a future game.

A FAILED SOURCE CARRIES NO NUMBER. A source that failed this cycle contributes nothing
but its status, the reason as a word (never an HTTP status code: the page must not be
able to print one, so the file does not hold one), when it was last read successfully,
and when it will be tried again. There is no carrying forward of an old score: the
fallback is the schedule, which is a different source with its own label.

CADENCE (`cadence()`): 30 s while any game is in its live window, 15 min on a game day
(ET calendar date with a kickoff), hourly otherwise - and never sleeping past the next
kickoff. `stale_after` is the deadline for the next write, so a stopped job is visible
from the file alone.

POLITENESS. One request per source per cycle. Exponential backoff per source on every
failure, honouring `Retry-After` whenever one is sent. The User-Agent is MEASURED, not
guessed: the scoreboard's edge answered 403 to a browser string, to an empty or missing
header and to a bare product token, and 200 to any string carrying a known client token
such as `python-httpx/0.28.1` (2026-09-24, from this machine). So the agent names this
job AND carries the client token - see `user_agent()`.

HEALTH. The logger's healthcheck is the logger's dead-man, and pinging it for success
from a second process would keep it green while the logger was dead. So this job never
sends it a success ping: failures go to its `/log` endpoint, which records an event
without changing the check's state (visible, not masking). A dedicated
`LIVE_HEALTHCHECK_URL`, when set, gets this job's own success and `/fail` pings.

WHAT IT WRITES, AND WHAT IT NEVER TOUCHES.
  * exactly ONE R2 key, `live/{sport}/snapshot.json`, by PUT. There is no delete call in
    this module (asserted by AST). `live/` is refused by `export_web.upload()` for
    declaration, upload and deletion alike (a-09), so the batch uploader can neither
    overwrite nor remove it. The only other writer under `live/` is a-09's
    `publish_live_prices`, which owns the one key `live/{sport}/prices.json`, is off by
    default, and was withdrawn by Ethan on 2026-09-23; the two keys are disjoint.
  * a local copy under `<STORAGE_DIR>/live/`, NOT under WEB_EXPORT_DIR.
  * `market_log.db` is opened `mode=ro`, for one short query an hour. `feeds.db` is
    read `mode=ro`; its injury rows are refreshed by running the existing
    `jobs.ingest_feeds --injuries` as a subprocess, which owns that store's lock.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import signal
import sqlite3
import subprocess
import sys
import time
import traceback
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx  # noqa: E402

import config  # noqa: E402
from jobs.publish_live_prices import LIVE_PREFIX, ContractError, _contract  # noqa: E402

KIND = "live.snapshot"
CACHE_CONTROL = "no-store"
ET = ZoneInfo("America/New_York")
PRODUCER = "calibratedsports-live/1.0"

SCOREBOARD_URL = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard"
PRICES_SERIES = "KXNFLGAME"
# A pagination ceiling RAISES; it does not truncate (CLAUDE.md, Polymarket). One open
# game-winner page is ~64 markets; five pages of 200 is room for a full board.
PRICES_MAX_PAGES = 5

# Every code that differed between the three feeds when all 32 teams were read on
# 2026-09-24: the scoreboard writes LAR/WSH, the exchange LAR/JAC, the schedule
# LA/WAS/JAX. The schedule's code is canonical because the site's team keys are.
TEAM_ALIASES = {"LAR": "LA", "WSH": "WAS", "JAC": "JAX"}

# The words a failure may be reported in. No HTTP status code is ever written.
REASONS = ("refused", "rate_limited", "server_error", "http_error", "timeout", "network",
           "shape", "ceiling", "store_unavailable", "refresh_failed")

POSTSEASON_LABEL = {"WC": "Wild Card", "DIV": "Divisional round",
                    "CON": "Conference championships", "SB": "Championship game"}


def key_for(sport: str) -> str:
    return f"{LIVE_PREFIX}{sport}/snapshot.json"


def iso(ts):
    """Contract `Timestamp`, floored to the second: never later than the truth."""
    if ts is None:
        return None
    return datetime.fromtimestamp(math.floor(ts), tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def canon(code):
    code = (code or "").upper()
    return TEAM_ALIASES.get(code, code)


def et_date(ts) -> str:
    return datetime.fromtimestamp(ts, tz=ET).strftime("%Y-%m-%d")


def user_agent() -> str:
    """Names this job and carries the client token the scoreboard's edge accepts.

    Measured 2026-09-24: 403 for a browser string, for no header, and for
    `calibratedsports-live/1.0` alone; 200 for the same string followed by
    `python-httpx/0.28.1`. Built from the installed version, never a literal."""
    site = config.WEB_SITE_URL
    ident = f"{PRODUCER} (+{site})" if site else PRODUCER
    return f"{ident} python-httpx/{httpx.__version__}"


# =========================================================================== reading

class Failure(Exception):
    """A read that did not produce data. `reason` is one of REASONS; `status` is kept
    for the job's own log line only and never enters the snapshot."""

    def __init__(self, reason, detail="", retry_after_s=None, status=None):
        assert reason in REASONS, reason
        super().__init__(f"{reason}: {detail}")
        self.reason = reason
        self.detail = detail
        self.retry_after_s = retry_after_s
        self.status = status


def _retry_after(headers):
    v = headers.get("retry-after") if headers is not None else None
    if not v:
        return None
    try:
        return max(0.0, float(v))
    except ValueError:
        try:
            from email.utils import parsedate_to_datetime
            return max(0.0, parsedate_to_datetime(v).timestamp() - time.time())
        except Exception:                      # noqa: BLE001 - an unreadable header is no header
            return None


def classify(status: int) -> str:
    if status == 403:
        return "refused"
    if status == 429:
        return "rate_limited"
    if status >= 500:
        return "server_error"
    return "http_error"


def get_json(client, url, params=None):
    """One GET. Returns parsed JSON or raises Failure. Never anything else."""
    try:
        r = client.get(url, params=params)
    except httpx.TimeoutException as e:
        raise Failure("timeout", type(e).__name__) from None
    except httpx.HTTPError as e:
        raise Failure("network", type(e).__name__) from None
    if r.status_code >= 400:
        raise Failure(classify(r.status_code), f"HTTP {r.status_code}",
                      retry_after_s=_retry_after(r.headers), status=r.status_code)
    try:
        return r.json()
    except ValueError:
        raise Failure("shape", "body is not JSON") from None


def _obj(v):
    return v if isinstance(v, dict) else {}


def _num(v):
    try:
        n = float(v)
    except (TypeError, ValueError):
        return None
    return n if math.isfinite(n) else None


def parse_scoreboard(raw) -> list[dict]:
    """The scoreboard's events. A body with no events array is a SHAPE failure, not an
    empty slate - an empty list here would read as 'no games' on a Sunday."""
    root = _obj(raw)
    if not isinstance(root.get("events"), list):
        raise Failure("shape", "scoreboard has no events array")
    out = []
    for ev in root["events"]:
        ev = _obj(ev)
        comp = _obj((ev.get("competitions") or [None])[0])
        sides = {}
        for c in comp.get("competitors") or []:
            c = _obj(c)
            abbr = _obj(c.get("team")).get("abbreviation")
            if c.get("homeAway") in ("home", "away") and abbr:
                sides[c["homeAway"]] = {"team": canon(abbr), "score": _num(c.get("score"))}
        st = _obj(_obj(ev.get("status") or comp.get("status")).get("type"))
        state = st.get("state")
        try:
            start = datetime.strptime(ev.get("date", ""), "%Y-%m-%dT%H:%MZ").replace(
                tzinfo=timezone.utc).timestamp()
        except ValueError:
            try:
                start = datetime.fromisoformat(str(ev.get("date")).replace("Z", "+00:00")).timestamp()
            except ValueError:
                continue
        if len(sides) != 2 or state not in ("pre", "in", "post"):
            continue
        sit = _obj(comp.get("situation"))
        odds = _obj((comp.get("odds") or [None])[0]) if comp.get("odds") else {}
        out.append({
            "espn_id": str(ev.get("id")) if ev.get("id") is not None else None,
            "start_ts": start,
            "state": state,
            "detail": st.get("shortDetail") or st.get("detail") or None,
            "situation": sit.get("downDistanceText") or sit.get("shortDownDistanceText") or None,
            "away": sides["away"], "home": sides["home"],
            "odds": ({"provider": _obj(odds.get("provider")).get("name") or None,
                      "details": odds.get("details") or None,
                      "over_under": _num(odds.get("overUnder"))} if odds else None),
        })
    return out


def _price(v):
    p = _num(v)
    return p if p is not None and 0.0 < p < 1.0 else None


_EVENT_DATE = re.compile(r"-(\d{2})([A-Z]{3})(\d{2})")
_MONTHS = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]


def event_date(event: str):
    """`KXNFLGAME-26SEP27BUFMIA` -> '2026-09-27' (the exchange dates tickers in ET)."""
    m = _EVENT_DATE.search(event or "")
    if not m or m.group(2) not in _MONTHS:
        return None
    return f"20{m.group(1)}-{_MONTHS.index(m.group(2)) + 1:02d}-{m.group(3)}"


def parse_markets(raw) -> tuple[list[dict], str | None]:
    root = _obj(raw)
    if not isinstance(root.get("markets"), list):
        raise Failure("shape", "market list has no markets array")
    out = []
    for m in root["markets"]:
        m = _obj(m)
        tk, ev = m.get("ticker"), m.get("event_ticker")
        if not tk or not ev:
            continue
        out.append({"ticker": tk, "event": ev, "team": canon(tk.rsplit("-", 1)[-1]),
                    # Prices are DOLLARS and a contract settles at $1: the price is the
                    # probability. 0 and 1 are not quotes.
                    "bid": _price(m.get("yes_bid_dollars")),
                    "ask": _price(m.get("yes_ask_dollars"))})
    return out, (root.get("cursor") or None)


def read_scoreboard(client):
    return parse_scoreboard(get_json(client, SCOREBOARD_URL))


def read_prices(client, max_pages=None):
    max_pages = PRICES_MAX_PAGES if max_pages is None else max_pages
    out, cursor = [], None
    for _ in range(max_pages):
        params = {"series_ticker": PRICES_SERIES, "status": "open", "limit": 200}
        if cursor:
            params["cursor"] = cursor
        got, cursor = parse_markets(get_json(client, f"{config.KALSHI_BASE}/markets", params))
        out.extend(got)
        if not cursor:
            return out
    raise Failure("ceiling", f"more than {max_pages} pages of open markets")


def _ro(path):
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)


def load_schedule(db_path=None, now=None) -> list[dict]:
    """The newest version of every game near now, READ-ONLY and in one short query.

    The window reaches back far enough to find last week's finals and forward far enough
    to find the next season's first week from the depth of an offseason."""
    now = time.time() if now is None else now
    path = db_path or config.DB_PATH
    if not os.path.exists(path):
        raise Failure("store_unavailable", "schedule store is absent")
    con = _ro(path)
    try:
        rows = con.execute(
            "SELECT g.game_id, g.season, g.week, g.game_type, g.kickoff_ts, g.home_team, "
            "g.away_team, g.home_score, g.away_score, g.spread_line, g.total_line, "
            "g.home_moneyline, g.away_moneyline, g.ingested_ts "
            "FROM nfl_games g JOIN (SELECT game_id, MAX(data_version) dv FROM nfl_games "
            "  WHERE sport = 'nfl' AND kickoff_ts BETWEEN ? AND ? GROUP BY game_id) m "
            "ON m.game_id = g.game_id AND m.dv = g.data_version ORDER BY g.kickoff_ts",
            (now - 150 * 86400, now + 300 * 86400)).fetchall()
    except sqlite3.Error as e:
        raise Failure("store_unavailable", type(e).__name__) from None
    finally:
        con.close()
    cols = ("game_id", "season", "week", "game_type", "kickoff_ts", "home", "away",
            "home_score", "away_score", "spread_line", "total_line", "home_moneyline",
            "away_moneyline", "ingested_ts")
    return [dict(zip(cols, r)) for r in rows]


def load_injuries(season, week, season_type="REG", db_path=None):
    """The CURRENT version of the report for one week, from feeds.db, read-only.

    Falls back to the latest week of the same season that has rows, and says which week
    it is - a team's report for the coming game is published Wednesday to Friday, so on
    a Tuesday the newest report is last week's."""
    path = db_path or config.storage_path("feeds.db")
    if not os.path.exists(path):
        raise Failure("store_unavailable", "feeds store is absent")
    con = _ro(path)
    try:
        wk = con.execute(
            "SELECT MAX(week) FROM injury_reports WHERE sport = 'nfl' AND season = ? AND "
            "season_type = ? AND week <= ? AND valid_to_ts IS NULL",
            (season, season_type, week)).fetchone()[0]
        if wk is None:
            return None
        rows = con.execute(
            "SELECT team, player_id, player_name, position, report_status, practice_status, "
            "report_primary_injury, upstream_asof_ts, valid_from_ts FROM injury_reports "
            "WHERE sport = 'nfl' AND season = ? AND season_type = ? AND week = ? "
            "AND valid_to_ts IS NULL ORDER BY team, player_name",
            (season, season_type, wk)).fetchall()
    except sqlite3.Error as e:
        raise Failure("store_unavailable", type(e).__name__) from None
    finally:
        con.close()
    return {"season": season, "week": wk, "season_type": season_type, "rows": [
        {"team": canon(r[0]), "player_id": r[1], "player": r[2], "position": r[3],
         "status": r[4], "practice": r[5], "injury": r[6],
         "upstream_dated_at": iso(r[7]), "captured_at": iso(r[8])} for r in rows]}


# =========================================================================== state

class SourceState:
    """One source's read history and backoff. Pure bookkeeping; no I/O."""

    def __init__(self, name, base_s=None, max_s=None, enabled=True):
        self.name = name
        self.base_s = config.LIVE_SNAPSHOT_BACKOFF_BASE if base_s is None else base_s
        self.max_s = config.LIVE_SNAPSHOT_BACKOFF_MAX if max_s is None else max_s
        self.enabled = enabled
        self.last_ok_ts = None
        self.attempted_ts = None
        self.read_ts = None            # set only for the cycle that succeeded
        self.failures = 0
        self.reason = None
        self.next_ts = 0.0

    def due(self, now) -> bool:
        return self.enabled and now >= self.next_ts

    def ok(self, now):
        self.attempted_ts = self.read_ts = self.last_ok_ts = now
        self.failures, self.reason, self.next_ts = 0, None, 0.0

    def fail(self, now, reason, retry_after_s=None):
        """Exponential from `base_s`, capped at `max_s`; a Retry-After wins when longer."""
        self.attempted_ts, self.read_ts = now, None
        self.failures += 1
        self.reason = reason
        wait = min(self.base_s * 2 ** (self.failures - 1), self.max_s)
        if retry_after_s is not None:
            wait = max(wait, min(retry_after_s, self.max_s))
        self.next_ts = now + wait

    def skipped(self):
        """A cycle in which this source was not tried (backing off): no data this cycle."""
        self.read_ts = None

    def status(self) -> str:
        if not self.enabled:
            return "disabled"
        if self.read_ts is not None:
            return "ok"
        return "failed" if self.failures else "not_read"

    def doc(self, label, now) -> dict:
        return {"name": label, "status": self.status(), "read_at": iso(self.read_ts),
                "last_ok_at": iso(self.last_ok_ts), "attempted_at": iso(self.attempted_ts),
                "failure": self.reason if self.read_ts is None else None,
                "consecutive_failures": self.failures,
                "next_attempt_at": iso(self.next_ts) if self.failures and self.next_ts > now else None}


# =========================================================================== assembling

def _week_key(g):
    return (g["season"], g["week"])


def _label(game_type, week):
    if game_type == "REG" or game_type is None:
        return f"Week {week}"
    return POSTSEASON_LABEL.get(game_type, game_type)


def _pair(a, b):
    return frozenset((canon(a), canon(b)))


def join_scoreboard(games, events, live_window_s):
    """{game_id: event} by team pair and kickoff within 36 h; plus unmatched events."""
    by_pair = {}
    for g in games:
        by_pair.setdefault(_pair(g["home"], g["away"]), []).append(g)
    joined, unmatched = {}, []
    for e in events:
        cands = [g for g in by_pair.get(_pair(e["home"]["team"], e["away"]["team"]), [])
                 if g["kickoff_ts"] is not None and abs(g["kickoff_ts"] - e["start_ts"]) < 36 * 3600]
        if cands:
            g = min(cands, key=lambda g: abs(g["kickoff_ts"] - e["start_ts"]))
            joined[g["game_id"]] = e
        else:
            unmatched.append(f"{e['away']['team']}@{e['home']['team']} {et_date(e['start_ts'])}")
    return joined, unmatched


def join_prices(games, markets):
    """{game_id: [market]} by the event's team pair and its ET date within one day."""
    events = {}
    for m in markets:
        events.setdefault(m["event"], []).append(m)
    by_pair = {}
    for g in games:
        by_pair.setdefault(_pair(g["home"], g["away"]), []).append(g)
    joined, unmatched = {}, []
    for ev, ms in sorted(events.items()):
        teams = frozenset(m["team"] for m in ms)
        day = event_date(ev)
        hit = None
        if len(teams) == 2 and day:
            d = datetime.strptime(day, "%Y-%m-%d").date()
            for g in by_pair.get(teams, []):
                gd = datetime.fromtimestamp(g["kickoff_ts"], tz=ET).date()
                if abs((gd - d).days) <= 1:
                    hit = g
                    break
        if hit is None:
            unmatched.append(ev)
        else:
            joined[hit["game_id"]] = sorted(
                [{"ticker": m["ticker"], "team": m["team"], "bid": m["bid"], "ask": m["ask"]}
                 for m in ms], key=lambda m: m["team"])
    return joined, unmatched


def is_final(g, ev):
    if ev is not None and ev["state"] == "post":
        return True
    return g["home_score"] is not None and g["away_score"] is not None


def choose_weeks(games, events_by_game, now, live_window_s):
    """(slate week key or None, last week key or None).

    The slate is the week of the earliest game that is not known to be over and whose
    live window has not closed. `last_week` is the week before it - or, with no slate,
    the most recent week that has started."""
    weeks = {}
    for g in games:
        weeks.setdefault(_week_key(g), []).append(g)
    order = sorted(weeks, key=lambda k: min(g["kickoff_ts"] for g in weeks[k]))
    pending = [g for g in games if g["kickoff_ts"] is not None
               and g["kickoff_ts"] > now - live_window_s
               and not is_final(g, events_by_game.get(g["game_id"]))]
    slate = _week_key(min(pending, key=lambda g: g["kickoff_ts"])) if pending else None
    if slate is not None:
        i = order.index(slate)
        last = order[i - 1] if i > 0 else None
    else:
        started = [k for k in order if min(g["kickoff_ts"] for g in weeks[k]) <= now]
        last = started[-1] if started else None
    return slate, last, weeks


def game_doc(g, ev, markets, now, live_window_s, scoreboard_ok):
    use_ev = ev if scoreboard_ok else None
    if use_ev is not None:
        state, state_src = use_ev["state"], "scoreboard"
    elif g["home_score"] is not None and g["away_score"] is not None:
        state, state_src = "post", "schedule"
    elif g["kickoff_ts"] > now:
        state, state_src = "pre", "schedule"
    else:
        state, state_src = "unknown", "schedule"   # past kickoff and nothing read says how it stands
    if use_ev is not None and use_ev["state"] in ("in", "post"):
        away_s, home_s, score_src = use_ev["away"]["score"], use_ev["home"]["score"], "scoreboard"
    elif g["home_score"] is not None and g["away_score"] is not None:
        away_s, home_s, score_src = g["away_score"], g["home_score"], "schedule"
    else:
        away_s = home_s = score_src = None
    line = None
    if any(g[k] is not None for k in ("spread_line", "total_line", "home_moneyline")):
        line = {"spread_line": g["spread_line"], "total_line": g["total_line"],
                "home_moneyline": g["home_moneyline"], "away_moneyline": g["away_moneyline"]}
    return {
        "game_id": g["game_id"],
        "scoreboard_id": ev["espn_id"] if ev is not None else None,
        "kickoff_at": iso(g["kickoff_ts"]),
        "away": {"team": canon(g["away"]), "score": away_s},
        "home": {"team": canon(g["home"]), "score": home_s},
        "state": state,
        "state_source": state_src,
        "score_source": score_src,
        "detail": use_ev["detail"] if use_ev is not None else None,
        "situation": (use_ev["situation"] if use_ev is not None and use_ev["state"] == "in"
                      else None),
        "line": line,
        "scoreboard_odds": use_ev["odds"] if use_ev is not None else None,
        "markets": markets,
    }


def mode_for(games, events_by_game, now, live_window_s, scoreboard_ok):
    """'live' | 'gameday' | 'idle' - which cadence the NEXT read runs at."""
    for g in games:
        ev = events_by_game.get(g["game_id"]) if scoreboard_ok else None
        if ev is not None and ev["state"] == "in":
            return "live"
        if (g["kickoff_ts"] is not None and g["kickoff_ts"] <= now < g["kickoff_ts"] + live_window_s
                and not is_final(g, ev)):
            return "live"
    today = et_date(now)
    if any(g["kickoff_ts"] is not None and et_date(g["kickoff_ts"]) == today for g in games):
        return "gameday"
    return "idle"


def cadence(mode, games, now, every=None) -> float:
    """Seconds until the next read: the mode's period, but never past the next kickoff."""
    every = {"live": config.LIVE_SNAPSHOT_EVERY_LIVE, "gameday": config.LIVE_SNAPSHOT_EVERY_GAMEDAY,
             "idle": config.LIVE_SNAPSHOT_EVERY_IDLE} if every is None else every
    wait = float(every[mode])
    ahead = [g["kickoff_ts"] - now for g in games
             if g["kickoff_ts"] is not None and g["kickoff_ts"] > now]
    if ahead:
        wait = min(wait, max(float(every["live"]), min(ahead)))
    return wait


def build(now, sport, schedule, events, markets, injuries, states, schedule_read_ts,
          live_window_s=None, every=None, grace_s=None) -> dict:
    """The contract document for this instant. Pure: no I/O.

    `schedule`, `events`, `markets` are None when that source has nothing this cycle."""
    live_window_s = config.LIVE_WINDOW_MIN * 60 if live_window_s is None else live_window_s
    grace_s = config.LIVE_SNAPSHOT_STALE_GRACE if grace_s is None else grace_s
    games = schedule or []
    scoreboard_ok = events is not None
    ev_by_game, unmatched_sb = join_scoreboard(games, events or [], live_window_s)
    px_by_game, unmatched_px = join_prices(games, markets or [])
    slate_k, last_k, weeks = choose_weeks(games, ev_by_game if scoreboard_ok else {}, now,
                                          live_window_s)

    def week_doc(k, with_markets):
        if k is None:
            return None
        gs = sorted(weeks[k], key=lambda g: (g["kickoff_ts"], g["game_id"]))
        return {"season": k[0], "week": k[1], "label": _label(gs[0]["game_type"], k[1]),
                "games": [game_doc(g, ev_by_game.get(g["game_id"]),
                                   (px_by_game.get(g["game_id"], []) if with_markets else []),
                                   now, live_window_s, scoreboard_ok) for g in gs]}

    slate = week_doc(slate_k, True)
    last = week_doc(last_k, False)
    slate_ids = {g["game_id"] for g in weeks.get(slate_k, [])}
    # Exchange events priced on the slate's own games, or on none of the schedule's.
    px_off_slate = sorted(gid for gid in px_by_game if gid not in slate_ids)
    mode = mode_for(games, ev_by_game, now, live_window_s, scoreboard_ok)
    wait = cadence(mode, games, now, every)
    if slate is None:
        note = ("The schedule holds no unfinished game." if schedule is not None
                else "The schedule could not be read, so there is no slate to show.")
    else:
        note = None
    return {
        "schema_version": _contract()["x-contract"]["schema_version"],
        "generated_at": iso(now),
        "kind": KIND,
        "sport": sport,
        "mode": mode,
        "next_read_in_s": max(1, int(round(wait))),
        "stale_after": iso(now + wait + grace_s),
        "sources": {
            "scoreboard": states["scoreboard"].doc("ESPN scoreboard", now),
            "prices": states["prices"].doc("Kalshi game-winner markets", now),
            "schedule": dict(states["schedule"].doc("nflverse schedule", now),
                             read_at=iso(schedule_read_ts) if schedule is not None else None,
                             data_as_of=iso(max((g["ingested_ts"] for g in games), default=None))
                             if games else None),
            "injuries": states["injuries"].doc("nflverse injury report", now),
        },
        "slate": slate,
        "slate_note": note,
        "last_week": last,
        "injuries": injuries,
        "counts": {
            "slate_games": len(slate["games"]) if slate else 0,
            "slate_scored_by_scoreboard": sum(1 for g in (slate or {}).get("games", [])
                                              if g["score_source"] == "scoreboard"),
            "slate_priced": sum(1 for g in (slate or {}).get("games", []) if g["markets"]),
            "markets_read": len(markets or []),
            "markets_off_slate_games": len(px_off_slate),
        },
        # Listed, not dropped: an event that joins no scheduled game is either a feed
        # drifting (a new team code) or a game the schedule does not hold yet.
        "unmatched": {"scoreboard_events": unmatched_sb, "exchange_events": unmatched_px},
    }


# =========================================================================== contract

_VALIDATOR = None


def validate(key: str, doc: dict) -> dict:
    """Refuse what the contract refuses, and a key that routes to any other kind."""
    from jsonschema import Draft202012Validator
    global _VALIDATOR
    c = _contract()
    routed = [k["kind"] for k in c["x-contract"]["keys"] if re.match(k["pattern"], key)]
    if routed != [KIND]:
        raise ContractError(f"{key} routes to {routed}, not [{KIND!r}]")
    if doc.get("kind") != KIND:
        raise ContractError(f"{key}: the file says kind {doc.get('kind')!r}")
    if _VALIDATOR is None:
        name = c["x-contract"]["kinds"][KIND]
        _VALIDATOR = Draft202012Validator({"$ref": f"#/$defs/{name}", "$defs": c["$defs"]})
    errors = [f"{'/'.join(map(str, e.absolute_path)) or '(root)'}: {e.message}"
              for e in _VALIDATOR.iter_errors(doc)]
    if errors:
        raise ContractError(f"{len(errors)} contract violation(s) in {key}: " + "; ".join(errors[:5]))
    return doc


# =========================================================================== health

class Health:
    """Failures to the logger's check as LOG events only; success/fail pings only to a
    check of this job's own. Never raises, never blocks a cycle for long."""

    def __init__(self, client=None, logger_url=None, own_url=None, throttle_s=None):
        self.client = client
        self.logger_url = config.HEALTHCHECK_URL if logger_url is None else logger_url
        self.own_url = config.LIVE_HEALTHCHECK_URL if own_url is None else own_url
        self.throttle_s = config.LIVE_SNAPSHOT_LOG_THROTTLE if throttle_s is None else throttle_s
        self.last_logged = {}

    def _send(self, method, url, body=None):
        try:
            c = self.client or httpx
            r = (c.post(url, content=body, timeout=config.HEALTHCHECK_TIMEOUT) if method == "post"
                 else c.get(url, timeout=config.HEALTHCHECK_TIMEOUT))
            return r.status_code < 400
        except Exception:                      # noqa: BLE001 - monitoring must not cost a cycle
            return False

    def failure(self, what, reason, now):
        """Record a failure on the logger's check without changing its state."""
        sent = False
        if self.logger_url and now - self.last_logged.get((what, reason), -1e18) >= self.throttle_s:
            self.last_logged[(what, reason)] = now
            sent = self._send("post", self.logger_url.rstrip("/") + "/log",
                              f"live snapshot: {what} {reason} at {iso(now)}".encode())
        return sent

    def cycle(self, ok: bool):
        if not self.own_url:
            return False
        return self._send("get", self.own_url if ok else self.own_url.rstrip("/") + "/fail")


# =========================================================================== the job

def r2_put(key, body, client=None):
    """The site bucket, short timeouts, one PUT. Reuses a-09's client on purpose."""
    from jobs import publish_live_prices
    if not publish_live_prices.configured():
        raise RuntimeError("R2 not configured (WEB_R2_BUCKET / keys / endpoint)")
    client = client or publish_live_prices.r2_client()
    client.put_object(Bucket=config.WEB_R2_BUCKET, Key=key, Body=body,
                      ContentType="application/json", CacheControl=CACHE_CONTROL)
    return client


def local_path(sport):
    return config.storage_path("live", sport, "snapshot.json")


def _write_local(path, body: bytes):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".part"
    with open(tmp, "wb") as f:
        f.write(body)
    os.replace(tmp, path)


class Job:
    """Holds the per-source state across cycles. `cycle()` does one read and one write."""

    def __init__(self, sport="nfl", http=None, upload=True, r2=None, health=None,
                 log=print, prices_enabled=None, injuries_every=None, schedule_every=None,
                 refresh_injuries=True):
        self.sport = sport
        self.http = http
        self.upload = upload
        self.r2 = r2
        self.log = log
        self.health = health if health is not None else Health()
        prices_enabled = config.LIVE_SNAPSHOT_PRICES if prices_enabled is None else prices_enabled
        self.states = {"scoreboard": SourceState("scoreboard"),
                       "prices": SourceState("prices", enabled=prices_enabled),
                       "schedule": SourceState("schedule"),
                       "injuries": SourceState("injuries")}
        self.schedule = None
        self.schedule_read_ts = None
        self.schedule_every = (config.LIVE_SNAPSHOT_SCHEDULE_EVERY if schedule_every is None
                               else schedule_every)
        self.injuries_every = (config.LIVE_SNAPSHOT_INJURIES_EVERY if injuries_every is None
                               else injuries_every)
        self.refresh_injuries = refresh_injuries
        self.injury_proc = None
        self.injury_refresh_ts = None
        self.writes = 0

    # -- sources ---------------------------------------------------------------------

    def _client(self):
        if self.http is None:
            self.http = httpx.Client(timeout=config.LIVE_SNAPSHOT_TIMEOUT,
                                     headers={"User-Agent": user_agent(),
                                              "Accept": "application/json"})
        return self.http

    def _try(self, name, fn, now):
        st = self.states[name]
        if not st.due(now):
            st.skipped()
            return None
        try:
            got = fn()
        except Failure as f:
            st.fail(now, f.reason, f.retry_after_s)
            self.log(f"  {name}: FAILED {f.reason} ({f.detail}); retry after "
                     f"{iso(st.next_ts)} [{st.failures} in a row]")
            self.health.failure(name, f.reason, now)
            return None
        except Exception as e:                 # noqa: BLE001 - one bad source is not a dead job
            st.fail(now, "shape")
            self.log(f"  {name}: FAILED unexpected {type(e).__name__}: {e}")
            self.health.failure(name, "shape", now)
            return None
        st.ok(now)
        return got

    def _schedule(self, now):
        st = self.states["schedule"]
        if self.schedule is not None and now - self.schedule_read_ts < self.schedule_every:
            st.read_ts = self.schedule_read_ts          # the held read, with its own time
            return self.schedule
        got = self._try("schedule", lambda: load_schedule(now=now), now)
        if got is not None:
            self.schedule, self.schedule_read_ts = got, now
            return got
        if self.schedule is not None:
            # The store failed after an earlier good read: keep the fixtures, which do not
            # move, but the source still reports the failure and its own last read.
            return self.schedule
        return None

    def _injuries(self, now, slate_key):
        self._poll_injury_refresh(now)
        if slate_key is None:
            self.states["injuries"].skipped()
            return None
        season, week, stype = slate_key
        return self._try("injuries", lambda: load_injuries(season, week, stype), now)

    def _poll_injury_refresh(self, now):
        """Run `jobs.ingest_feeds --injuries SEASON` every `injuries_every`, never two at
        once, never waiting on it. It owns feeds.db's lock and its own archive."""
        if not self.refresh_injuries:
            return
        if self.injury_proc is not None:
            rc = self.injury_proc.poll()
            if rc is None:
                return
            self.log(f"  injuries refresh exited {rc}")
            if rc == 0:
                self.injury_refresh_ts = now
            else:
                self.health.failure("injuries-refresh", "refresh_failed", now)
            self.injury_proc = None
            return
        last = self.injury_refresh_ts or 0.0
        if now - last < self.injuries_every:
            return
        season = datetime.fromtimestamp(now, tz=ET).year
        if datetime.fromtimestamp(now, tz=ET).month < 3:
            season -= 1                     # January and February belong to last season
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        logf = open(config.storage_path("live", "injuries_refresh.log"), "ab")
        self.injury_proc = subprocess.Popen(
            [sys.executable, "-m", "jobs.ingest_feeds", "--injuries", str(season)],
            cwd=root, stdout=logf, stderr=subprocess.STDOUT)
        logf.close()
        # Counted as run when it STARTS, so a hung refresh cannot be relaunched each cycle.
        self.injury_refresh_ts = now

    # -- one cycle -------------------------------------------------------------------

    def cycle(self, now=None):
        now = time.time() if now is None else now
        os.makedirs(config.storage_path("live", self.sport), exist_ok=True)
        schedule = self._schedule(now)
        events = self._try("scoreboard", lambda: read_scoreboard(self._client()), now)
        markets = (self._try("prices", lambda: read_prices(self._client()), now)
                   if self.states["prices"].enabled else None)
        live_window_s = config.LIVE_WINDOW_MIN * 60
        # Pick the slate once without injuries, read the matching week's report, rebuild.
        doc = build(now, self.sport, schedule, events, markets, None, self.states,
                    self.schedule_read_ts, live_window_s)
        slate = doc["slate"]
        slate_key = None
        if slate is not None:
            stype = {g["game_type"] for g in (schedule or [])
                     if (g["season"], g["week"]) == (slate["season"], slate["week"])}
            stype = "REG" if stype in ({"REG"}, set()) else "POST"
            slate_key = (slate["season"], slate["week"], stype)
        injuries = self._injuries(now, slate_key)
        doc = build(now, self.sport, schedule, events, markets, injuries, self.states,
                    self.schedule_read_ts, live_window_s)
        key = key_for(self.sport)
        validate(key, doc)
        body = json.dumps(doc, separators=(",", ":"), sort_keys=True).encode("utf-8")
        _write_local(local_path(self.sport), body)
        uploaded = False
        if self.upload:
            try:
                self.r2 = r2_put(key, body, self.r2)
                uploaded = True
                self.writes += 1
            except Exception as e:             # noqa: BLE001 - the next cycle tries again
                self.log(f"  upload FAILED {type(e).__name__}: {e}")
                self.health.failure("upload", "network", now)
        if self.upload:
            self.health.cycle(uploaded)
        c = doc["counts"]
        self.log(f"{iso(now)} {doc['mode']:<7} slate={slate['label'] if slate else None} "
                 f"games={c['slate_games']} scored={c['slate_scored_by_scoreboard']} "
                 f"priced={c['slate_priced']} "
                 + " ".join(f"{k}={v['status']}" for k, v in doc["sources"].items())
                 + f" -> {key} {len(body)}B {'uploaded' if uploaded else 'local only'}; "
                   f"next in {doc['next_read_in_s']}s")
        return doc, body, uploaded


# =========================================================================== CLI

_stop = False


def _on_signal(*_):
    global _stop
    _stop = True


def loop(job, log):
    from core.single_instance import AlreadyRunning, acquire
    try:
        lock = acquire("live_snapshot")
    except AlreadyRunning as e:
        log(f"REFUSING: {e}")
        return 3
    signal.signal(signal.SIGINT, _on_signal)
    signal.signal(signal.SIGTERM, _on_signal)
    log(f"live snapshot loop started, pid {os.getpid()}, UA {user_agent()!r}")
    try:
        while not _stop:
            t0 = time.time()
            try:
                doc, _, _ = job.cycle(t0)
                wait = doc["next_read_in_s"]
            except Exception:                  # noqa: BLE001 - the loop outlives any one cycle
                log("CYCLE FAILED\n" + traceback.format_exc())
                job.health.failure("cycle", "shape", t0)
                job.health.cycle(False)
                wait = config.LIVE_SNAPSHOT_EVERY_LIVE
            deadline = t0 + wait
            while not _stop and time.time() < deadline:
                time.sleep(min(1.0, max(0.0, deadline - time.time())))
    finally:
        lock.release()
    log("live snapshot loop stopped")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--once", action="store_true", help="one read, one write, exit")
    g.add_argument("--loop", action="store_true", help="the scheduled process")
    ap.add_argument("--no-upload", action="store_true", help="write the local copy only")
    ap.add_argument("--no-injury-refresh", action="store_true",
                    help="read feeds.db as it is; do not run ingest_feeds")
    ap.add_argument("--out", help="also write the file here")
    ap.add_argument("--log", action="store_true",
                    help="append output to <STORAGE_DIR>/live/live_snapshot.log")
    a = ap.parse_args(argv)
    os.makedirs(config.storage_path("live"), exist_ok=True)
    logf = open(config.storage_path("live", "live_snapshot.log"), "a", encoding="utf-8") \
        if a.log else None

    def log(msg):
        print(msg, flush=True)
        if logf:
            logf.write(msg + "\n")
            logf.flush()

    job = Job(upload=not a.no_upload, log=log, refresh_injuries=not a.no_injury_refresh)
    if a.loop:
        return loop(job, log)
    # --once runs the injury capture FIRST and waits for it, so the one file it writes
    # carries the report it just captured, and no child outlives the process.
    job._poll_injury_refresh(time.time())
    if job.injury_proc is not None:
        try:
            job.injury_proc.wait(timeout=300)
        except subprocess.TimeoutExpired:
            job.injury_proc.kill()
            log("  injuries refresh killed after 300 s")
        job._poll_injury_refresh(time.time())
    doc, body, uploaded = job.cycle()
    if a.out:
        with open(a.out, "wb") as f:
            f.write(body)
    if doc["slate"] is None and doc["last_week"] is None:
        # Exit 0 is not a result: a file with nothing on it is a failed read.
        log("REFUSING exit 0: the snapshot has neither a slate nor last week's finals")
        return 1
    if not a.no_upload and not uploaded:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
