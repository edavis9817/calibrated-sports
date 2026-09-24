"""The Live page's snapshot job (unit a-23).

Run: pytest -q tests/test_live_snapshot.py

What is asserted, each driven to both answers where there are two:

  1. the file matches the contract, and its key routes to `live.snapshot` and nothing else;
  2. A FAILED SCOREBOARD STILL PRODUCES A SLATE - from the schedule, with the failure in
     words and no HTTP status code anywhere in the file;
  3. per-source exponential backoff, and Retry-After wins when it is longer;
  4. the slate is always the next unfinished week, with last week's finals beside it -
     overnight, between weeks, mid-game and out of season;
  5. cadence: 30 s in a live window, 15 min on a game day, hourly otherwise, never past
     the next kickoff;
  6. the three feeds' team codes join;
  7. the writer PUTs exactly one key and has no delete path; the batch uploader cannot
     reach it;
  8. the logger's healthcheck only ever receives LOG events from this job.
"""
import ast
import inspect
import json
import re
import sqlite3
from datetime import datetime, timezone

import httpx
import pytest

import config
from jobs import export_web
from jobs import live_snapshot as S
from jobs import publish_live_prices as L


def ts(s):
    return datetime.fromisoformat(s).replace(tzinfo=timezone.utc).timestamp()


# Thursday 2026-09-24, 09:00Z = 05:00 ET. TNF kicks at 00:15Z Friday (20:15 ET Thursday).
NOW = ts("2026-09-24T09:00:00")


@pytest.fixture(autouse=True)
def pinned(tmp_path, monkeypatch):
    """Nothing here may reach the live store, the real bucket or a real healthcheck."""
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "market_log.db"))
    monkeypatch.setattr(config, "STORAGE_DIR", str(tmp_path))
    monkeypatch.setattr(config, "RAW_DIR", str(tmp_path / "raw"))
    monkeypatch.setattr(config, "WEB_EXPORT_DIR", str(tmp_path / "export"))
    monkeypatch.setattr(config, "WEB_R2_BUCKET", "bucket")
    monkeypatch.setattr(config, "WEB_R2_ACCESS_KEY_ID", "x")
    monkeypatch.setattr(config, "WEB_R2_SECRET_ACCESS_KEY", "y")
    monkeypatch.setattr(config, "HEALTHCHECK_URL", None)
    monkeypatch.setattr(config, "LIVE_HEALTHCHECK_URL", None)


def game(gid, season, week, kick, home, away, hs=None, as_=None, gt="REG", spread=3.0):
    return {"game_id": gid, "season": season, "week": week, "game_type": gt,
            "kickoff_ts": kick, "home": home, "away": away, "home_score": hs,
            "away_score": as_, "spread_line": spread, "total_line": 44.5,
            "home_moneyline": -150.0, "away_moneyline": 130.0, "ingested_ts": NOW - 3600}


W2 = [game("2026_02_DET_BUF", 2026, 2, ts("2026-09-21T00:20:00"), "BUF", "DET", 41, 31),
      game("2026_02_SF_LA", 2026, 2, ts("2026-09-20T20:25:00"), "LA", "SF", 20, 17)]
W3 = [game("2026_03_ATL_GB", 2026, 3, ts("2026-09-25T00:15:00"), "GB", "ATL"),
      game("2026_03_SEA_WAS", 2026, 3, ts("2026-09-27T17:00:00"), "WAS", "SEA"),
      game("2026_03_NE_JAX", 2026, 3, ts("2026-09-27T17:00:00"), "JAX", "NE"),
      game("2026_03_LA_DEN", 2026, 3, ts("2026-09-28T00:20:00"), "DEN", "LA")]
W4 = [game("2026_04_PIT_CLE", 2026, 4, ts("2026-10-02T00:15:00"), "CLE", "PIT")]
SCHEDULE = W2 + W3 + W4


def ev(away, home, start, state="pre", a=None, h=None, eid="1"):
    return {"espn_id": eid, "start_ts": start, "state": state, "detail": "d", "situation": None,
            "away": {"team": S.canon(away), "score": a}, "home": {"team": S.canon(home), "score": h},
            "odds": None}


def states(**kw):
    st = {n: S.SourceState(n) for n in ("scoreboard", "prices", "schedule", "injuries")}
    for n in st:
        st[n].ok(NOW)
    for n, reason in kw.items():
        st[n] = S.SourceState(n)
        st[n].fail(NOW, reason)
    return st


def build(schedule=SCHEDULE, events=(), markets=(), now=NOW, st=None, injuries=None):
    return S.build(now, "nfl", schedule, None if events is None else list(events),
                   None if markets is None else list(markets), injuries, st or states(), now,
                   live_window_s=240 * 60)


# ============================================================ 1. the contract

def test_a_built_file_validates_and_routes_to_live_snapshot_only():
    doc = build(events=[ev("ATL", "GB", W3[0]["kickoff_ts"])])
    assert S.validate(S.key_for("nfl"), doc) is doc
    hits = [k["kind"] for k in export_web.CONTRACT["x-contract"]["keys"]
            if re.match(k["pattern"], "live/nfl/snapshot.json")]
    assert hits == ["live.snapshot"]


def test_the_contract_refuses_what_it_should():
    doc = build()
    for mutate in (lambda d: d.update(extra=1),
                   lambda d: d["sources"]["scoreboard"].update(failure="HTTP 403"),
                   lambda d: d["sources"]["scoreboard"].update(http_status=403),
                   lambda d: d["slate"]["games"][0]["markets"].append(
                       {"ticker": "t", "team": "GB", "bid": 1.5, "ask": None})):
        bad = json.loads(json.dumps(doc))
        mutate(bad)
        with pytest.raises(L.ContractError):
            S.validate("live/nfl/snapshot.json", bad)


def test_the_key_must_route_to_exactly_this_kind():
    doc = build()
    with pytest.raises(L.ContractError):
        S.validate("live/nfl/prices.json", doc)
    with pytest.raises(L.ContractError):
        S.validate("nfl/live/snapshot.json", doc)


def test_no_key_shape_resolves_to_two_kinds_with_the_new_pattern():
    for key in ("live/nfl/prices.json", "live/nfl/snapshot.json", "nfl/manifest.json",
                "nfl/teams/buf.json", "analytics/nfl/index.json"):
        hits = [k["kind"] for k in export_web.CONTRACT["x-contract"]["keys"]
                if re.match(k["pattern"], key)]
        assert len(hits) == 1, (key, hits)


# ============================================================ 2. a failed scoreboard still has a slate

def test_a_refused_scoreboard_leaves_the_slate_from_the_schedule_and_no_status_code():
    st = states(scoreboard="refused")
    doc = build(events=None, st=st)
    S.validate("live/nfl/snapshot.json", doc)
    assert doc["slate"]["label"] == "Week 3" and len(doc["slate"]["games"]) == 4
    g = doc["slate"]["games"][0]
    assert (g["state"], g["state_source"], g["score_source"]) == ("pre", "schedule", None)
    sb = doc["sources"]["scoreboard"]
    assert (sb["status"], sb["failure"], sb["read_at"]) == ("failed", "refused", None)
    assert sb["next_attempt_at"] is not None
    # Last week's finals still come from the schedule.
    assert doc["last_week"]["games"][0]["score_source"] == "schedule"
    text = json.dumps(doc)
    assert "403" not in text and "429" not in text and "HTTP" not in text


def test_the_same_game_is_scored_by_the_scoreboard_when_it_answers():
    """The other answer: a healthy scoreboard is the state and score source."""
    kick = W3[0]["kickoff_ts"]
    doc = build(events=[ev("ATL", "GB", kick, "in", 7, 3)], now=kick + 600)
    g = next(g for g in doc["slate"]["games"] if g["game_id"] == "2026_03_ATL_GB")
    assert (g["state"], g["state_source"], g["score_source"]) == ("in", "scoreboard", "scoreboard")
    assert (g["away"]["score"], g["home"]["score"]) == (7, 3)
    assert doc["sources"]["scoreboard"]["status"] == "ok"


def test_a_game_in_progress_with_the_scoreboard_down_shows_no_stale_score():
    kick = W3[0]["kickoff_ts"]
    doc = build(events=None, now=kick + 600, st=states(scoreboard="rate_limited"))
    g = next(g for g in doc["slate"]["games"] if g["game_id"] == "2026_03_ATL_GB")
    assert (g["state"], g["score_source"], g["away"]["score"]) == ("unknown", None, None)
    assert doc["mode"] == "live"          # the live window still sets the cadence


def test_get_json_classifies_without_leaking_a_status_into_the_reason():
    def client(handler):
        return httpx.Client(transport=httpx.MockTransport(handler))

    cases = [(lambda r: httpx.Response(403), "refused", None),
             (lambda r: httpx.Response(429, headers={"Retry-After": "120"}), "rate_limited", 120.0),
             (lambda r: httpx.Response(503), "server_error", None),
             (lambda r: httpx.Response(404), "http_error", None),
             (lambda r: httpx.Response(200, content=b"<html>"), "shape", None)]
    for handler, reason, retry in cases:
        with pytest.raises(S.Failure) as e:
            S.get_json(client(handler), "https://x/")
        assert e.value.reason == reason and e.value.retry_after_s == retry
        assert reason in S.REASONS

    def timeout(r):
        raise httpx.ReadTimeout("slow")
    with pytest.raises(S.Failure) as e:
        S.get_json(client(timeout), "https://x/")
    assert e.value.reason == "timeout"
    assert S.get_json(client(lambda r: httpx.Response(200, json={"a": 1})), "https://x/") == {"a": 1}


def test_a_scoreboard_with_no_events_array_is_a_shape_failure_not_an_empty_slate():
    with pytest.raises(S.Failure) as e:
        S.parse_scoreboard({"leagues": []})
    assert e.value.reason == "shape"
    assert S.parse_scoreboard({"events": []}) == []


# ============================================================ 3. backoff

def test_backoff_is_exponential_per_source_and_capped():
    s = S.SourceState("x", base_s=30, max_s=300)
    waits = []
    for i in range(6):
        s.fail(NOW, "refused")
        waits.append(s.next_ts - NOW)
    assert waits == [30, 60, 120, 240, 300, 300]
    assert not s.due(NOW + 299) and s.due(NOW + 300)
    s.ok(NOW + 300)
    assert (s.failures, s.reason, s.status()) == (0, None, "ok") and s.due(NOW + 300)


def test_retry_after_wins_when_longer_and_not_when_shorter():
    s = S.SourceState("x", base_s=30, max_s=3600)
    s.fail(NOW, "rate_limited", retry_after_s=600)
    assert s.next_ts - NOW == 600
    t = S.SourceState("y", base_s=30, max_s=3600)
    t.fail(NOW, "rate_limited", retry_after_s=5)
    assert t.next_ts - NOW == 30


def test_a_backing_off_source_is_not_called_at_all():
    calls = []

    def handler(r):
        calls.append(r.url.host)
        return httpx.Response(403)

    job = S.Job(http=httpx.Client(transport=httpx.MockTransport(handler)), upload=False,
                refresh_injuries=False, log=lambda *_: None)
    job.schedule, job.schedule_read_ts = SCHEDULE, NOW
    job.cycle(NOW)
    n = len(calls)
    assert n == 2                                   # scoreboard and exchange, once each
    job.cycle(NOW + 5)                              # both inside their 30 s backoff
    assert len(calls) == n
    job.cycle(NOW + 31)
    assert len(calls) == n + 2


# ============================================================ 4. which week

def test_overnight_before_a_game_day_the_slate_is_this_week_and_last_week_has_finals():
    doc = build()
    assert (doc["slate"]["week"], doc["last_week"]["week"]) == (3, 2)
    assert all(g["state"] == "post" for g in doc["last_week"]["games"])
    assert all(g["markets"] == [] for g in doc["last_week"]["games"])


def test_after_the_last_game_of_a_week_the_slate_moves_to_the_next_week():
    last_kick = W3[-1]["kickoff_ts"]
    done = [dict(g, home_score=20, away_score=10) for g in W3]
    doc = build(schedule=W2 + done + W4, now=last_kick + 5 * 3600)
    assert (doc["slate"]["week"], doc["last_week"]["week"]) == (4, 3)


def test_a_game_past_kickoff_and_not_final_keeps_its_week_on_the_slate():
    doc = build(now=W3[-1]["kickoff_ts"] + 3600, events=None, st=states(scoreboard="timeout"))
    assert doc["slate"]["week"] == 3


def test_a_game_the_scoreboard_calls_final_releases_the_week():
    last = W3[-1]
    others = [dict(g, home_score=1, away_score=0) for g in W3[:-1]]
    evs = [ev("LAR", "DEN", last["kickoff_ts"], "post", 24, 21)]
    doc = build(schedule=W2 + others + [last] + W4, events=evs, now=last["kickoff_ts"] + 3 * 3600)
    assert doc["slate"]["week"] == 4
    assert doc["last_week"]["games"][-1]["score_source"] == "scoreboard"


def test_out_of_season_there_is_no_slate_but_there_are_finals_and_a_note():
    doc = build(schedule=W2, now=ts("2027-03-01T12:00:00"))
    assert doc["slate"] is None and doc["slate_note"]
    assert doc["last_week"]["week"] == 2
    assert doc["mode"] == "idle"
    S.validate("live/nfl/snapshot.json", doc)


def test_postseason_weeks_are_named():
    wc = [game("2026_19_A_B", 2026, 19, ts("2027-01-16T21:30:00"), "BUF", "MIA", gt="WC")]
    doc = build(schedule=wc, now=ts("2027-01-15T12:00:00"))
    assert doc["slate"]["label"] == "Wild Card"


# ============================================================ 5. cadence

def test_mode_and_cadence():
    kick = W3[0]["kickoff_ts"]
    # 05:00 ET Thursday, TNF tonight: a game day.
    doc = build()
    assert (doc["mode"], doc["next_read_in_s"]) == ("gameday", 900)
    # Ten minutes before kickoff the wait is cut to the kickoff, never less than 30 s.
    doc = build(now=kick - 600)
    assert doc["next_read_in_s"] == 600
    doc = build(now=kick - 10)
    assert doc["next_read_in_s"] == 30
    # In the live window: 30 s.
    doc = build(now=kick + 60, events=[ev("ATL", "GB", kick, "in", 0, 0)])
    assert (doc["mode"], doc["next_read_in_s"]) == ("live", 30)
    # Tuesday: idle, hourly.
    doc = build(now=ts("2026-09-29T15:00:00"))
    assert (doc["mode"], doc["next_read_in_s"]) == ("idle", 3600)


def test_stale_after_is_the_deadline_for_the_next_write():
    doc = build()
    gen = datetime.strptime(doc["generated_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    stale = datetime.strptime(doc["stale_after"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    assert (stale - gen).total_seconds() == 900 + config.LIVE_SNAPSHOT_STALE_GRACE


def test_a_game_day_is_the_eastern_calendar_date():
    """00:15Z Friday is 20:15 ET Thursday: Thursday is the game day, not Friday."""
    fri_morning_utc = ts("2026-09-25T02:30:00")       # 22:30 ET Thursday, game still live
    assert S.et_date(W3[0]["kickoff_ts"]) == "2026-09-24"
    doc = build(schedule=[W3[0]], now=ts("2026-09-24T14:00:00"))
    assert doc["mode"] == "gameday"
    doc = build(schedule=[W3[0]], now=fri_morning_utc)
    assert doc["mode"] == "live"


# ============================================================ 6. joins

def test_the_three_feeds_team_codes_join():
    evs = [ev("SEA", "WSH", W3[1]["kickoff_ts"], eid="11"), ev("LAR", "DEN", W3[3]["kickoff_ts"], eid="12")]
    mks = [{"ticker": "KXNFLGAME-26SEP27NEJAC-JAC", "event": "KXNFLGAME-26SEP27NEJAC", "team": "JAX", "bid": 0.6, "ask": 0.62},
           {"ticker": "KXNFLGAME-26SEP27NEJAC-NE", "event": "KXNFLGAME-26SEP27NEJAC", "team": "NE", "bid": 0.38, "ask": 0.4},
           {"ticker": "KXNFLGAME-26OCT09XXXYYY-XXX", "event": "KXNFLGAME-26OCT09XXXYYY", "team": "XXX", "bid": 0.5, "ask": 0.52},
           {"ticker": "KXNFLGAME-26OCT09XXXYYY-YYY", "event": "KXNFLGAME-26OCT09XXXYYY", "team": "YYY", "bid": 0.5, "ask": 0.52}]
    doc = build(events=evs, markets=mks)
    by = {g["game_id"]: g for g in doc["slate"]["games"]}
    assert by["2026_03_SEA_WAS"]["scoreboard_id"] == "11"
    assert by["2026_03_LA_DEN"]["scoreboard_id"] == "12"
    assert {m["team"] for m in by["2026_03_NE_JAX"]["markets"]} == {"JAX", "NE"}
    assert doc["unmatched"]["exchange_events"] == ["KXNFLGAME-26OCT09XXXYYY"]
    assert doc["counts"]["slate_priced"] == 1


def test_parse_markets_canonicalises_codes_and_refuses_non_quotes():
    got, cur = S.parse_markets({"markets": [
        {"ticker": "KXNFLGAME-26SEP27NEJAC-JAC", "event_ticker": "KXNFLGAME-26SEP27NEJAC",
         "yes_bid_dollars": "0.0000", "yes_ask_dollars": "0.6200"}], "cursor": ""})
    assert got[0]["team"] == "JAX" and got[0]["bid"] is None and got[0]["ask"] == 0.62 and cur is None


def test_a_pagination_ceiling_raises_rather_than_truncating():
    def handler(r):
        return httpx.Response(200, json={"markets": [], "cursor": "more"})
    c = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(S.Failure) as e:
        S.read_prices(c, max_pages=3)
    assert e.value.reason == "ceiling"


def test_the_user_agent_names_the_job_and_carries_the_client_token():
    """Measured 2026-09-24: the scoreboard refuses a browser string and a bare product
    token, and accepts a string carrying `python-httpx/<version>`."""
    ua = S.user_agent()
    assert ua.startswith(S.PRODUCER) and f"python-httpx/{httpx.__version__}" in ua
    job = S.Job(upload=False, refresh_injuries=False, log=lambda *_: None)
    assert job._client().headers["user-agent"] == ua


# ============================================================ 7. what it writes

class FakeR2:
    def __init__(self, fail=False):
        self.puts, self.fail = [], fail

    def put_object(self, **kw):
        if self.fail:
            raise RuntimeError("r2 down")
        self.puts.append(kw)


def ok_http():
    def handler(r):
        if r.url.host == "site.api.espn.com":
            return httpx.Response(200, json={"events": []})
        return httpx.Response(200, json={"markets": [], "cursor": ""})
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_a_cycle_puts_exactly_one_key_and_writes_a_local_copy_outside_the_export_tree(tmp_path):
    r2 = FakeR2()
    job = S.Job(http=ok_http(), r2=r2, refresh_injuries=False, log=lambda *_: None)
    job.schedule, job.schedule_read_ts = SCHEDULE, NOW
    doc, body, uploaded = job.cycle(NOW)
    assert uploaded and len(r2.puts) == 1
    put = r2.puts[0]
    assert (put["Key"], put["Bucket"], put["CacheControl"]) == ("live/nfl/snapshot.json", "bucket", "no-store")
    assert json.loads(put["Body"]) == doc
    local = tmp_path / "live" / "nfl" / "snapshot.json"
    assert local.read_bytes() == body
    assert not (tmp_path / "export").exists()


def test_a_failed_upload_does_not_raise_and_the_next_cycle_tries_again():
    r2 = FakeR2(fail=True)
    job = S.Job(http=ok_http(), r2=r2, refresh_injuries=False, log=lambda *_: None)
    job.schedule, job.schedule_read_ts = SCHEDULE, NOW
    _, _, uploaded = job.cycle(NOW)
    assert not uploaded
    r2.fail = False
    _, _, uploaded = job.cycle(NOW + 900)
    assert uploaded and len(r2.puts) == 1


def test_the_writer_has_no_delete_path_at_all():
    tree = ast.parse(inspect.getsource(S))
    calls = {n.func.attr for n in ast.walk(tree)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
    assert "put_object" in calls                       # the walk sees calls at all
    assert not calls & {"delete_object", "delete_objects", "sync_keys", "upload"}, calls


def test_the_key_is_under_the_prefix_the_batch_uploader_refuses_and_is_not_a09s_key():
    """export_web.upload() refuses to declare, upload or delete under LIVE_PREFIX; that
    guard (tests/test_live_prices.py TRAP 1-5) covers this key only if it is under it."""
    assert S.key_for("nfl").startswith(L.LIVE_PREFIX)
    assert S.key_for("nfl") != L.key_for("nfl")


def test_the_schedule_is_read_only_and_an_absent_store_is_not_created(tmp_path):
    missing = tmp_path / "nope.db"
    with pytest.raises(S.Failure) as e:
        S.load_schedule(str(missing), NOW)
    assert e.value.reason == "store_unavailable" and not missing.exists()
    db = tmp_path / "g.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE nfl_games (sport TEXT, game_id TEXT, data_version TEXT, season INT, "
                "week INT, game_type TEXT, kickoff_ts REAL, home_team TEXT, away_team TEXT, "
                "home_score REAL, away_score REAL, spread_line REAL, total_line REAL, "
                "home_moneyline REAL, away_moneyline REAL, ingested_ts REAL)")
    rows = [("nfl", "g1", "2026-09-01", 2026, 3, "REG", NOW + 100, "GB", "ATL", None, None, 1, 40, -120, 100, 1),
            ("nfl", "g1", "2026-09-20", 2026, 3, "REG", NOW + 200, "GB", "ATL", None, None, 2, 41, -130, 110, 2)]
    con.executemany("INSERT INTO nfl_games VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    con.commit()
    con.close()
    got = S.load_schedule(str(db), NOW)
    assert len(got) == 1 and got[0]["kickoff_ts"] == NOW + 200     # the newest version only
    with pytest.raises(sqlite3.OperationalError):
        S._ro(str(db)).execute("DELETE FROM nfl_games")


# ============================================================ 8. health

class Rec:
    def __init__(self):
        self.calls = []

    def get(self, url, timeout=None):
        self.calls.append(("get", url))
        return httpx.Response(200)

    def post(self, url, content=None, timeout=None):
        self.calls.append(("post", url))
        return httpx.Response(200)


def test_the_loggers_check_only_ever_gets_log_events_and_they_are_throttled():
    rec = Rec()
    h = S.Health(client=rec, logger_url="https://hc/logger", own_url="", throttle_s=900)
    assert h.failure("scoreboard", "refused", NOW)
    assert not h.failure("scoreboard", "refused", NOW + 60)        # throttled
    assert h.failure("prices", "rate_limited", NOW + 60)            # a different failure
    assert h.failure("scoreboard", "refused", NOW + 901)
    h.cycle(True)
    h.cycle(False)
    assert rec.calls and all(m == "post" and u == "https://hc/logger/log" for m, u in rec.calls)


def test_the_jobs_own_check_gets_success_and_fail():
    rec = Rec()
    h = S.Health(client=rec, logger_url="", own_url="https://hc/live", throttle_s=900)
    h.cycle(True)
    h.cycle(False)
    assert rec.calls == [("get", "https://hc/live"), ("get", "https://hc/live/fail")]


def test_a_failing_source_in_a_cycle_reaches_the_loggers_log_endpoint():
    rec = Rec()

    def handler(r):
        return httpx.Response(429, headers={"Retry-After": "90"})
    job = S.Job(http=httpx.Client(transport=httpx.MockTransport(handler)), upload=False,
                refresh_injuries=False, log=lambda *_: None,
                health=S.Health(client=rec, logger_url="https://hc/logger", own_url="", throttle_s=900))
    job.schedule, job.schedule_read_ts = SCHEDULE, NOW
    doc, _, _ = job.cycle(NOW)
    assert doc["sources"]["scoreboard"]["failure"] == "rate_limited"
    assert doc["sources"]["scoreboard"]["next_attempt_at"] == S.iso(NOW + 90)
    assert doc["slate"]["label"] == "Week 3"
    assert ("post", "https://hc/logger/log") in rec.calls
    assert ("get", "https://hc/logger") not in rec.calls
