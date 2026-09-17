"""W07 track C phase 3: the Odds API's free endpoints, against a mocked server.

Run: pytest -q tests/test_oddsapi_cfb.py

The credit pool is shared with the live NFL logger and nothing in phase 3 may
spend without an approved number. So these tests are about spending nothing: a
paid endpoint cannot be built, a call the server bills stops the run, the floor
cannot come from the NFL default, and the key never reaches the disk.

Every fixture is INVENTED: the key, balances, event and kickoff are placeholders,
not measurements, and are deliberately round so they cannot be mistaken for one.
"""
import gzip
import json
import os

import httpx
import pytest

import config
from cfb import oddsapi, paths
from jobs import ingest_cfb

KEY = "secret-key-123"


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "STORAGE_DIR", str(tmp_path))
    monkeypatch.setattr(config, "ODDS_BASE", "https://odds.test/v4")
    monkeypatch.setenv("ODDS_RESERVE", "20000")
    paths.ensure_dirs()
    conn = ingest_cfb.connect()
    yield conn
    conn.close()


EVENTS = [{"id": "e1", "sport_key": oddsapi.SPORT, "commence_time": "2000-01-01T00:00:00Z",
           "home_team": "Home Team", "away_team": "Away Team"}]


class FakeOdds:
    def __init__(self, last="0", remaining=50000):
        self.last, self.remaining = last, remaining
        self.calls = []
        self.status = 200
        self.raise_transport = False

    def handler(self, request: httpx.Request):
        if self.raise_transport:
            raise httpx.ConnectError(f"boom {request.url}")
        self.calls.append(request.url.path)
        assert request.url.params["apiKey"] == KEY
        headers = {"x-requests-remaining": str(self.remaining), "x-requests-used": "100"}
        if self.last is not None:
            headers["x-requests-last"] = self.last
        body = ([{"key": oddsapi.SPORT, "active": True}] if request.url.path.endswith("/sports")
                else EVENTS)
        return httpx.Response(self.status, json=body, headers=headers)

    def client(self, conn):
        return oddsapi.Client(conn, httpx.Client(transport=httpx.MockTransport(self.handler)),
                              api_key=KEY)


def _ledger(conn):
    return conn.execute("SELECT endpoint, status, cost_last, file_id, outcome, origin "
                        "FROM oddsapi_requests ORDER BY ts").fetchall()


def test_only_free_endpoints_can_be_built():
    assert oddsapi.build_url("sports").endswith("/sports")
    assert oddsapi.build_url("events").endswith(f"/sports/{oddsapi.SPORT}/events")
    for paid in ("odds", "event_odds", "event_markets", "historical_odds", "historical_events"):
        with pytest.raises(ValueError):
            oddsapi.build_url(paid)


def test_free_run_archives_both_and_logs_zero_cost(store):
    fake = FakeOdds()
    out = ingest_cfb.run_odds_free(store, fake.client(store))
    assert out["ncaaf_active"] is True and out["n_events"] == 1
    rows = _ledger(store)
    assert [r[0] for r in rows] == ["sports", "events"]
    assert all(r[1] == 200 and r[2] == 0 and r[3] and r[4] == "new" for r in rows)
    assert all("jobs.ingest_cfb" in r[5] for r in rows)
    rel = store.execute("SELECT rel_path FROM cfb_raw_files WHERE dataset='oddsapi_events'").fetchone()[0]
    assert rel.startswith("oddsapi/events/")
    with gzip.open(os.path.join(paths.raw_root(), *rel.split("/"))) as f:
        assert json.loads(f.read()) == EVENTS
    assert ingest_cfb.audit(store).clean


def test_identical_content_is_not_archived_twice(store):
    fake = FakeOdds()
    ingest_cfb.run_odds_free(store, fake.client(store))
    ingest_cfb.run_odds_free(store, fake.client(store))
    assert store.execute("SELECT COUNT(*) FROM cfb_raw_files").fetchone()[0] == 2
    assert [r[4] for r in _ledger(store)][2:] == ["unchanged_content"] * 2


@pytest.mark.parametrize("last,outcome", [("1", "charged_1"), (None, "charge_unknown")])
def test_a_billed_or_unverifiable_call_stops_the_run(store, last, outcome):
    fake = FakeOdds(last=last)
    with pytest.raises(oddsapi.UnexpectedCharge):
        ingest_cfb.run_odds_free(store, fake.client(store))
    assert len(fake.calls) == 1                     # /events never called
    assert [r[4] for r in _ledger(store)] == [outcome]
    assert store.execute("SELECT COUNT(*) FROM cfb_raw_files").fetchone()[0] == 0


def test_non_200_stops_without_archiving(store):
    fake = FakeOdds()
    fake.status = 401
    with pytest.raises(oddsapi.OddsApiError):
        ingest_cfb.run_odds_free(store, fake.client(store))
    assert [r[4] for r in _ledger(store)] == ["http_401"]
    assert store.execute("SELECT COUNT(*) FROM cfb_raw_files").fetchone()[0] == 0


def test_reserve_must_come_from_the_environment(store, monkeypatch):
    monkeypatch.delenv("ODDS_RESERVE", raising=False)
    fake = FakeOdds()
    with pytest.raises(oddsapi.OddsApiError, match="ODDS_RESERVE"):
        ingest_cfb.run_odds_free(store, fake.client(store))
    assert fake.calls == [] and _ledger(store) == []


def test_missing_key_refuses_before_any_request(store, monkeypatch):
    monkeypatch.setattr(config, "ODDS_API_KEY", None)
    with pytest.raises(oddsapi.OddsApiError, match="ODDS_API_KEY"):
        oddsapi.Client(store)


def test_the_key_never_reaches_the_ledger_the_archive_or_an_exception(store, tmp_path):
    fake = FakeOdds()
    ingest_cfb.run_odds_free(store, fake.client(store))
    fake.raise_transport = True
    with pytest.raises(oddsapi.OddsApiError) as ei:
        fake.client(store).get_free("sports")
    assert KEY not in str(ei.value) and ei.value.__cause__ is None
    dump = "\n".join(store.iterdump())
    assert KEY not in dump
    for dirpath, _d, files in os.walk(tmp_path):
        for f in files:
            with open(os.path.join(dirpath, f), "rb") as fh:
                data = fh.read()
            if f.endswith(".gz"):
                data = gzip.decompress(data)
            assert KEY.encode() not in data, f
