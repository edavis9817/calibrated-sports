"""W07 track C phase 3: P1 (event markets) and the forward game-line capture, mocked.

Run: pytest -q tests/test_oddsapi_capture.py

Both spend from a pool shared with the NFL logger, against approved numbers: P1 74
credits once, the forward capture 3 credits per kickoff hour and 45 a CFB week. So
these tests are about the ceilings holding under every path - reruns, overcharges,
a week with more kickoff hours than the cap buys - and about every paid response
reaching the archive byte for byte.

Every fixture is INVENTED: ids, teams, balances and kickoffs are placeholders, not
measurements. Kickoffs are placed in a future CFB week relative to the wall clock
so the week arithmetic never straddles a boundary whenever the suite runs.
"""
import gzip
import json
import os
import time
from datetime import datetime, timezone

import httpx
import pytest

import config
from cfb import oddsapi, oddsapi_capture, paths
from jobs import ingest_cfb

KEY = "placeholder-key-000"


def iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "STORAGE_DIR", str(tmp_path))
    monkeypatch.setattr(config, "ODDS_BASE", "https://odds.test/v4")
    monkeypatch.setattr(config, "ODDS_API_KEY", KEY)
    monkeypatch.setenv("ODDS_RESERVE", "1000")
    paths.ensure_dirs()
    conn = ingest_cfb.connect()
    yield conn
    conn.close()


class FakeOdds:
    """/events is free; /events/{id}/markets costs 1; /odds costs 3 (or `odds_cost`)."""

    def __init__(self, events, remaining=50000):
        self.events = events
        self.remaining = remaining
        self.calls = []
        self.odds_cost = 3
        self.markets_status = 200

    def handler(self, request: httpx.Request):
        path = request.url.path
        params = dict(request.url.params)
        assert params.pop("apiKey") == KEY
        self.calls.append((path, params))
        if path.endswith("/events"):
            cost, status, body = 0, 200, self.events
        elif path.endswith("/markets"):
            eid = path.split("/")[-2]
            e = next(x for x in self.events if x["id"] == eid)
            cost, status = 1, self.markets_status
            body = {**e, "bookmakers": [{"key": "bookA", "title": "Book A", "markets": [
                {"key": "h2h", "last_update": e["commence_time"]},
                {"key": "player_receptions", "last_update": e["commence_time"]}]}]}
        elif path.endswith("/odds"):
            cost, status = self.odds_cost, 200
            body = [{**e, "bookmakers": [{"key": "bookA", "last_update": e["commence_time"],
                                          "markets": [{"key": "spreads",
                                                       "last_update": e["commence_time"],
                                                       "outcomes": [
                                                           {"name": e["home_team"], "price": -110,
                                                            "point": -3.5},
                                                           {"name": e["away_team"], "price": -110,
                                                            "point": 3.5}]}]}]}
                    for e in self.events]
        else:
            raise AssertionError(path)
        self.remaining -= cost
        return httpx.Response(status, json=body, headers={
            "x-requests-last": str(cost), "x-requests-remaining": str(self.remaining),
            "x-requests-used": "0"})

    def client(self, conn):
        return oddsapi.Client(conn, httpx.Client(transport=httpx.MockTransport(self.handler)),
                              api_key=KEY)

    def paid(self, suffix):
        return [c for c in self.calls if c[0].endswith(suffix)]


def _events(start_ts, per_hour):
    """per_hour: list of event counts, one kickoff hour each, from start_ts."""
    out, n = [], 0
    for h, k in enumerate(per_hour):
        for j in range(k):
            out.append({"id": f"ev{n:04d}", "sport_key": oddsapi.SPORT,
                        "commence_time": iso(start_ts + h * 3600 + j * 60),
                        "home_team": f"Home {n}", "away_team": f"Away {n}"})
            n += 1
    return out


def _next_week_wednesday():
    return oddsapi.week_start_ts(time.time()) + 7 * 86400 + 12 * 3600    # Wed 00:00Z


# -----------------------------------------------------------------------------
# request shapes and the pre-request refusals
# -----------------------------------------------------------------------------

def test_only_the_two_approved_paid_shapes_exist_and_their_params_are_fixed():
    url, params, cost = oddsapi.build_paid("odds")
    assert (params, cost) == ({"regions": "us", "markets": "h2h,spreads,totals",
                               "oddsFormat": "american"}, 3)
    url, params, cost = oddsapi.build_paid("event_markets", event_id="abc123")
    assert url.endswith(f"/sports/{oddsapi.SPORT}/events/abc123/markets")
    assert (params, cost) == ({"regions": "us"}, 1)
    for bad in ("historical_odds", "historical_event_odds", "event_odds", "historical_events"):
        with pytest.raises(ValueError):
            oddsapi.build_paid(bad)
    with pytest.raises(ValueError):
        oddsapi.build_paid("event_markets", event_id="../odds")
    with pytest.raises(ValueError):
        oddsapi.build_paid("odds", event_id="abc123")


def test_a_paid_call_refuses_before_any_request(store):
    fake = FakeOdds(_events(_next_week_wednesday(), [1]))
    c = fake.client(store)
    with pytest.raises(oddsapi.BudgetRefused, match="balance unknown"):
        c.get_paid("odds", "forward", 45)
    c.remaining = 1002
    with pytest.raises(oddsapi.BudgetRefused, match="reserve"):
        c.get_paid("odds", "forward", 45)
    c.remaining = 50000
    with pytest.raises(oddsapi.BudgetRefused, match="approved budget"):
        c.get_paid("odds", "forward", 2)
    assert fake.calls == []


def test_week_start_is_tuesday_noon_utc():
    tue_noon = datetime(2026, 9, 22, 12, 0, tzinfo=timezone.utc).timestamp()
    assert oddsapi.week_start_ts(tue_noon) == tue_noon
    assert oddsapi.week_start_ts(tue_noon - 1) == tue_noon - 7 * 86400
    assert oddsapi.week_start_ts(tue_noon + 6 * 86400 + 23 * 3600) == tue_noon


# -----------------------------------------------------------------------------
# P1
# -----------------------------------------------------------------------------

def test_p1_refuses_before_saturday_morning(store):
    fake = FakeOdds(_events(_next_week_wednesday(), [3]))
    with pytest.raises(oddsapi.BudgetRefused, match="not before"):
        ingest_cfb.run_odds_p1(store, fake.client(store),
                               now=oddsapi.iso_ts(oddsapi.P1_NOT_BEFORE) - 1)
    assert fake.calls == []


def test_p1_never_spends_past_the_approved_total_even_across_reruns(store, monkeypatch):
    monkeypatch.setattr(oddsapi, "P1_NOT_BEFORE", "2000-01-01T00:00:00Z")
    fake = FakeOdds(_events(_next_week_wednesday(), [40, 40]))          # 80 events
    counts = ingest_cfb.run_odds_p1(store, fake.client(store), gap_s=0)
    assert counts["credits"] == 74 and len(fake.paid("/markets")) == 74
    assert counts["not_reached"] == 6
    with pytest.raises(oddsapi.BudgetRefused, match="nothing left"):
        ingest_cfb.run_odds_p1(store, fake.client(store), gap_s=0)
    assert len(fake.paid("/markets")) == 74
    assert ingest_cfb.p1_spent(store) == 74


def test_p1_keeps_every_response_verbatim_even_when_identical(store, monkeypatch):
    monkeypatch.setattr(oddsapi, "P1_NOT_BEFORE", "2000-01-01T00:00:00Z")
    fake = FakeOdds(_events(_next_week_wednesday(), [2]))
    ingest_cfb.run_odds_p1(store, fake.client(store), gap_s=0, max_credits=2)
    files = store.execute("SELECT rel_path, bytes, asset FROM cfb_raw_files WHERE "
                          "dataset='oddsapi_event_markets' ORDER BY asset").fetchall()
    assert [f[2] for f in files] == ["ev0000", "ev0001"]
    for rel, nbytes, eid in files:
        with gzip.open(os.path.join(paths.raw_root(), *rel.split("/"))) as f:
            raw = f.read()
        assert len(raw) == nbytes and json.loads(raw)["id"] == eid
    keys = {r[0] for r in store.execute("SELECT market_key FROM cfb_odds_event_markets")}
    assert keys == {"h2h", "player_receptions"}
    assert ingest_cfb.audit(store).clean


def test_p1_orders_fbs_first_and_stops_on_a_non_200(store, monkeypatch):
    monkeypatch.setattr(oddsapi, "P1_NOT_BEFORE", "2000-01-01T00:00:00Z")
    ev = _events(_next_week_wednesday(), [3])
    from cfb import oddsapi_join
    div = {"ev0000": "fcs/fcs", "ev0001": "fbs/fcs", "ev0002": "fbs/fbs"}

    def fake_match(conn, events, season):
        g = lambda e: (0, 0, 0, 0, 0, 0, "", "", *div[e["id"]].split("/"))
        return [], [(e, g(e)) for e in events], [], [], 0
    monkeypatch.setattr(oddsapi_join, "match", fake_match)
    plan = oddsapi_capture.plan_p1(store, ev, time.time(), 2026)
    assert [e["id"] for e, _l in plan] == ["ev0002", "ev0001", "ev0000"]

    fake = FakeOdds(ev)
    fake.markets_status = 422
    counts = ingest_cfb.run_odds_p1(store, fake.client(store), gap_s=0)
    assert counts["called"] == 1 and counts["stopped"] == 422
    assert store.execute("SELECT COUNT(*) FROM cfb_raw_files WHERE "
                         "dataset='oddsapi_event_markets_error'").fetchone()[0] == 1


# -----------------------------------------------------------------------------
# the forward capture
# -----------------------------------------------------------------------------

def test_the_window_opens_eight_minutes_out_and_an_hour_is_bought_once(store):
    t0 = _next_week_wednesday()
    fake = FakeOdds(_events(t0, [3]))
    c = fake.client(store)
    assert ingest_cfb.run_odds_forward(store, c, now=t0 - 600)["captured"] == 0
    assert ingest_cfb.run_odds_forward(store, c, now=t0 - 300)["captured"] == 1
    assert ingest_cfb.run_odds_forward(store, c, now=t0 - 120)["captured"] == 0
    assert len(fake.paid("/odds")) == 1
    row = store.execute("SELECT outcome, cost, file_id FROM cfb_odds_snapshots").fetchone()
    assert row[0] == "captured" and row[1] == 3 and row[2]
    assert store.execute("SELECT COUNT(*) FROM cfb_odds_quotes").fetchone()[0] == 6


def test_a_late_first_tick_records_a_miss_and_buys_nothing(store):
    t0 = _next_week_wednesday()
    fake = FakeOdds(_events(t0, [2]))
    ingest_cfb.run_odds_forward(store, fake.client(store), now=t0 - 30)
    assert fake.paid("/odds") == []
    assert store.execute("SELECT outcome FROM cfb_odds_snapshots").fetchone()[0] == "missed"


def test_a_full_week_of_ticks_never_passes_the_weekly_cap(store, monkeypatch):
    """17 kickoff hours in one CFB week against a 45-credit cap: 15 are bought, the two
    with the fewest games are skipped, and nothing is missed.

    The cap is pinned HERE rather than taken from the shipped constant: this test is
    about the mechanism, and raising the real cap (45 -> 75 on 2026-09-18) must not
    quietly turn it into a test of nothing. `test_the_shipped_cap_covers_the_schedule`
    is the one that watches the real number."""
    monkeypatch.setattr(oddsapi, "FORWARD_WEEKLY_CAP", 45)
    t0 = _next_week_wednesday()
    per_hour = [5, 1, 8, 8, 2, 9, 9, 9, 7, 6, 6, 4, 3, 3, 3, 2, 1]
    fake = FakeOdds(_events(t0, per_hour))
    c = fake.client(store)
    for now in range(int(t0 - 1800), int(t0 + len(per_hour) * 3600), 300):
        ingest_cfb.run_odds_forward(store, c, now=now)
    outcomes = dict(store.execute("SELECT outcome, COUNT(*) FROM cfb_odds_snapshots GROUP BY 1"))
    assert outcomes == {"captured": 15, "skipped_weekly_cap": 2}
    assert len(fake.paid("/odds")) == 15
    assert store.execute("SELECT SUM(cost) FROM cfb_odds_snapshots").fetchone()[0] == 45
    skipped = [r[0] for r in store.execute("SELECT n_events FROM cfb_odds_snapshots WHERE "
                                           "outcome='skipped_weekly_cap'")]
    assert sorted(skipped) == [1, 1]


def test_an_overcharge_stops_and_is_counted_against_the_week(store):
    t0 = _next_week_wednesday()
    fake = FakeOdds(_events(t0, [2]))
    fake.odds_cost = 30
    with pytest.raises(oddsapi.UnexpectedCharge):
        ingest_cfb.run_odds_forward(store, fake.client(store), now=t0 - 300)
    assert store.execute("SELECT outcome, cost FROM cfb_odds_snapshots").fetchone() == \
        ("charge_unknown", 30)


def test_a_quiet_tick_takes_no_lock_and_makes_no_request(store, monkeypatch):
    t0 = _next_week_wednesday()
    fake = FakeOdds(_events(t0, [2]))
    ingest_cfb.refresh_events(store, fake.client(store))
    before = store.execute("SELECT COUNT(*) FROM oddsapi_requests").fetchone()[0]

    def no_client(*a, **k):
        raise AssertionError("a quiet tick must not build a client")
    monkeypatch.setattr(oddsapi, "Client", no_client)
    monkeypatch.setattr(ingest_cfb, "InstanceLock", no_client)
    assert ingest_cfb._main(["--odds-forward"]) == 0
    assert store.execute("SELECT COUNT(*) FROM oddsapi_requests").fetchone()[0] == before


def test_the_observation_tables_stay_inside_the_scope_boundary():
    from cfb import guards, schema
    assert guards.scope_violations(schema.ODDS_TABLES) == []


def test_reparse_rebuilds_the_same_rows_from_the_archive(store):
    t0 = _next_week_wednesday()
    fake = FakeOdds(_events(t0, [3]))
    ingest_cfb.run_odds_forward(store, fake.client(store), now=t0 - 300)
    q = "SELECT event_id, bookmaker, market_key, outcome_name, price, point FROM cfb_odds_quotes ORDER BY 1,4"
    before = store.execute(q).fetchall()
    store.execute("DELETE FROM cfb_odds_quotes")
    assert ingest_cfb.run_odds_reparse(store) >= 2
    assert store.execute(q).fetchall() == before


def test_no_paid_path_writes_the_key(store, tmp_path, monkeypatch):
    monkeypatch.setattr(oddsapi, "P1_NOT_BEFORE", "2000-01-01T00:00:00Z")
    t0 = _next_week_wednesday()
    fake = FakeOdds(_events(t0, [2]))
    ingest_cfb.run_odds_forward(store, fake.client(store), now=t0 - 300)
    ingest_cfb.run_odds_p1(store, fake.client(store), gap_s=0, max_credits=2)
    assert KEY not in "\n".join(store.iterdump())
    for dirpath, _d, files in os.walk(tmp_path):
        for f in files:
            with open(os.path.join(dirpath, f), "rb") as fh:
                data = fh.read()
            if f.endswith(".gz"):
                data = gzip.decompress(data)
            assert KEY.encode() not in data, f


def test_identical_paid_responses_in_one_second_never_overwrite(store):
    from cfb import cfbd
    req = cfbd.Request("oddsapi_odds", "odds", 2026, oddsapi.SPORT)
    ts = 1_000_000_000.0
    ids = [ingest_cfb.archive_cfbd(store, req, b"[]", ts, source="oddsapi",
                                   repo="the-odds-api.com", dedupe=False)[0] for _ in range(3)]
    rels = [r[0] for r in store.execute("SELECT rel_path FROM cfb_raw_files ORDER BY file_id")]
    assert len(set(ids)) == 3 and len(set(rels)) == 3
    assert ingest_cfb.audit(store).clean


def test_a_deduplicated_listing_keeps_the_earlier_fetch_time(store):
    """Found on the first live capture: an unchanged listing re-parsed on a later tick
    was claiming that tick's fetch time, so "when did the API first say this kickoff"
    read as "the last time nothing changed"."""
    t0 = _next_week_wednesday()
    fake = FakeOdds(_events(t0, [1]))
    first, _ev, _h = ingest_cfb.refresh_events(store, fake.client(store))
    ts1 = store.execute("SELECT fetched_ts FROM cfb_odds_events WHERE src_file_id=?",
                        (first,)).fetchone()[0]
    time.sleep(1.1)
    again, _ev, _h = ingest_cfb.refresh_events(store, fake.client(store))
    ts2 = store.execute("SELECT fetched_ts FROM cfb_odds_events WHERE src_file_id=?",
                        (again,)).fetchone()[0]
    assert again == first and ts2 == ts1          # same bytes, same archive row, same instant


def test_the_shipped_cap_covers_the_measured_schedule():
    """The real constant, against the season it has to cover.

    22 kickoff hours is the largest CFB week in the 2026 schedule (the 2026-09-01 week),
    which is 66 credits; `research/cfb_forward_cap.py --season-hours` re-derives it from
    `cfb_games`. A cap sized exactly to the measured maximum reproduces the condition that
    made 45 bind twice, and kickoff drift creates hours, so the shipped number carries
    slack above it. This test is what makes lowering the cap a deliberate act."""
    measured_max_hours = 22
    cost_per_hour = oddsapi.PAID["odds"][2]
    assert oddsapi.FORWARD_WEEKLY_CAP >= measured_max_hours * cost_per_hour, (
        "the cap no longer covers the biggest week in the schedule")
    slack_hours = (oddsapi.FORWARD_WEEKLY_CAP - measured_max_hours * cost_per_hour) / cost_per_hour
    assert slack_hours >= 3, f"only {slack_hours:.0f} hours of slack above the measured maximum"
