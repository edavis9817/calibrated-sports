"""W07 track C phase 2: CFBD game lines and results, against a mocked server.

Run: pytest -q tests/test_ingest_cfb_cfbd.py

The CFBD quota is 1,000 calls a month, shared with the main clone and with CBBD.
Every test here is about spending: that nothing is spent before the budget is
known, that nothing is spent past the plan or the floor, that a per-game loop
cannot be built, and that every call - including the ones that fail - leaves a
ledger row naming where it came from.
"""
import gzip
import json
import os

import httpx
import pytest

import config
from cfb import cfbd, cfbd_normalize, fetch, paths, versioning
from jobs import ingest_cfb


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "STORAGE_DIR", str(tmp_path))
    monkeypatch.setattr(config, "CFBD_RESERVE", 100)
    monkeypatch.setattr(config, "CFBD_BASE", "https://cfbd.test")
    monkeypatch.setattr(fetch, "DOWNLOAD_GAP_S", 0)
    paths.ensure_dirs()
    conn = ingest_cfb.connect()
    yield conn
    conn.close()


def _game(gid, week=1, season_type="regular", lines=None, home="Alabama", away="Auburn"):
    return {"id": gid, "season": 2025, "week": week, "seasonType": season_type,
            "startDate": "2025-08-30T19:30:00.000Z", "homeTeamId": 333, "homeTeam": home,
            "awayTeamId": 2, "awayTeam": away, "lines": lines if lines is not None else [
                {"provider": "DraftKings", "spread": -7.0, "formattedSpread": f"{home} -7",
                 "spreadOpen": -6.5, "overUnder": 51.5, "overUnderOpen": 50.0,
                 "homeMoneyline": -280, "awayMoneyline": 230}]}


class FakeCFBD:
    def __init__(self, remaining=900, used=100):
        self.remaining, self.used = remaining, used
        self.calls = []
        self.payloads = {}         # (endpoint, frozenset(params)) -> list
        self.status = {}           # endpoint -> forced status
        self.info_status = 200

    def handler(self, request: httpx.Request):
        url = request.url
        endpoint = url.path.strip("/")
        self.calls.append((endpoint, dict(url.params)))
        assert request.headers["Authorization"] == "Bearer k"
        if endpoint == "info":
            if self.info_status != 200:
                return httpx.Response(self.info_status, json={})
            return httpx.Response(200, json={"remainingCalls": self.remaining,
                                             "usedCalls": self.used, "monthlyLimit": 1000})
        st = self.status.get(endpoint, 200)
        if st == 200:
            self.remaining -= 1
            self.used += 1
        body = self.payloads.get((endpoint, frozenset(url.params.items())), [])
        return httpx.Response(st, json=body if st == 200 else {"message": "nope"},
                              headers={"X-CallLimit-Remaining": str(self.remaining)})

    def client(self, conn):
        return cfbd.Client(conn, httpx.Client(transport=httpx.MockTransport(self.handler)),
                           api_key="k")

    @property
    def metered(self):
        return [c for c in self.calls if c[0] != "info"]


def _ledger(conn):
    return conn.execute("SELECT endpoint, status, metered, file_id, outcome, origin, run_id "
                        "FROM cfbd_requests ORDER BY ts").fetchall()


# =============================================================================
# the shape of what can be asked
# =============================================================================

@pytest.mark.parametrize("params", [{"year": 2025, "gameId": 1}, {"year": 2025, "id": 1},
                                    {"year": 2025, "team": "Alabama"}, {"week": 3}])
def test_no_per_game_or_per_team_request_can_be_built(params, store):
    with pytest.raises(ValueError):
        cfbd.build_url("lines", params)


def test_only_allowed_endpoints_can_be_built(store):
    with pytest.raises(ValueError):
        cfbd.build_url("plays", {"year": 2025, "week": 1})


def test_plans_are_the_advertised_size():
    assert len(cfbd.lines_backfill(range(2013, 2026))) == 13
    assert [r.endpoint for r in cfbd.week(2026, 3)] == ["games", "lines"]
    for r in cfbd.lines_backfill(range(2013, 2026)) + cfbd.week(2026, 3):
        cfbd.build_url(r.endpoint, r.param_dict())         # every planned URL is legal


def test_a_missing_key_refuses_before_any_request(store, monkeypatch):
    monkeypatch.setattr(config, "CFBD_API_KEY", None)
    with pytest.raises(cfbd.BudgetRefused, match="CFBD_API_KEY"):
        ingest_cfb.run_cfbd(store, cfbd.week(2026, 3))


# =============================================================================
# the budget
# =============================================================================

def test_a_plan_over_the_run_cap_is_refused_before_info(store):
    fake = FakeCFBD()
    with pytest.raises(cfbd.BudgetRefused, match="cap"):
        ingest_cfb.run_cfbd(store, cfbd.lines_backfill(range(2000, 2026)), client=fake.client(store))
    assert fake.calls == []


def test_max_requests_can_lower_the_cap_but_not_raise_it(store):
    fake = FakeCFBD()
    with pytest.raises(cfbd.BudgetRefused):
        ingest_cfb.run_cfbd(store, cfbd.week(2026, 3), client=fake.client(store), max_requests=1)
    plan = cfbd.lines_backfill(range(2000, 2021))            # 21
    with pytest.raises(cfbd.BudgetRefused):
        ingest_cfb.run_cfbd(store, plan, client=fake.client(store), max_requests=500)


def test_the_floor_is_checked_against_the_server_before_spending(store):
    fake = FakeCFBD(remaining=110)
    with pytest.raises(cfbd.BudgetRefused, match="below the 100 floor"):
        ingest_cfb.run_cfbd(store, cfbd.lines_backfill(range(2013, 2026)), client=fake.client(store))
    assert fake.metered == []
    assert [r[0] for r in _ledger(store)] == ["info"]          # the /info call is logged too


def test_an_unknown_quota_is_a_refusal(store):
    fake = FakeCFBD()
    fake.info_status = 500
    with pytest.raises(cfbd.BudgetRefused, match="unknown"):
        ingest_cfb.run_cfbd(store, cfbd.week(2026, 3), client=fake.client(store))
    assert fake.metered == []


def test_the_run_stops_when_the_header_reaches_the_floor(store):
    """Allowed to start (115 - 13 = 102), but the quota is SHARED: another client
    spends 2 per call of ours, so the header reaches the floor mid-run."""
    fake = FakeCFBD(remaining=115)
    real = fake.handler

    def shared(request):
        if request.url.path.strip("/") != "info":
            fake.remaining -= 2
        return real(request)
    client = cfbd.Client(store, httpx.Client(transport=httpx.MockTransport(shared)), api_key="k")
    counts = ingest_cfb.run_cfbd(store, cfbd.lines_backfill(range(2013, 2026)), client=client)
    assert counts.get("stopped_at_reserve") == 1
    assert len(fake.metered) == 5                  # 115 -> 112 -> 109 -> 106 -> 103 -> 100
    assert fake.remaining == 100


def test_a_failed_request_stops_the_run_and_is_still_logged(store):
    fake = FakeCFBD()
    fake.status["games"] = 429
    counts = ingest_cfb.run_cfbd(store, cfbd.week(2026, 3), client=fake.client(store))
    assert counts == {"http_429": 1}
    assert [c[0] for c in fake.metered] == ["games"]           # lines never requested
    rows = _ledger(store)
    assert [(r[0], r[1], r[2], r[4]) for r in rows] == [("info", 200, 0, "ok"),
                                                         ("games", 429, 1, "http_429")]


def test_every_call_names_its_origin_and_run(store):
    fake = FakeCFBD()
    ingest_cfb.run_cfbd(store, cfbd.week(2026, 3), client=fake.client(store))
    rows = _ledger(store)
    assert len(rows) == 3 and len({r[6] for r in rows}) == 1
    import socket
    assert all(r[5] == cfbd.origin() and socket.gethostname() in r[5]
               and "jobs.ingest_cfb" in r[5] for r in rows)
    assert all(r[3] is not None for r in rows if r[2] == 1)    # metered rows point at their file


# =============================================================================
# raw first, then parse
# =============================================================================

def _week_payloads(fake, week_no, games, lines):
    p = frozenset({("year", "2025"), ("week", str(week_no)), ("seasonType", "regular")})
    fake.payloads[("games", p)] = games
    fake.payloads[("lines", p)] = lines


def test_responses_are_archived_verbatim_and_unchanged_content_is_not_copied(store):
    fake = FakeCFBD()
    _week_payloads(fake, 1, [{"id": 1, "season": 2025, "week": 1, "homeId": 333,
                              "homeLineScores": [7, 7, 0, 3]}], [_game(1)])
    ingest_cfb.run_cfbd(store, cfbd.week(2025, 1), client=fake.client(store))
    counts = ingest_cfb.run_cfbd(store, cfbd.week(2025, 1), client=fake.client(store))
    assert counts == {"unchanged_content": 2}
    files = store.execute("SELECT rel_path FROM cfb_raw_files ORDER BY rel_path").fetchall()
    assert len(files) == 2
    with gzip.open(os.path.join(paths.raw_root(), *files[1][0].split("/"))) as f:
        assert json.loads(f.read())[0]["lines"][0]["provider"] == "DraftKings"
    assert ingest_cfb.audit(store).clean


def test_weeks_are_separate_scopes_and_do_not_close_each_other(store):
    fake = FakeCFBD()
    _week_payloads(fake, 1, [], [_game(1, week=1)])
    _week_payloads(fake, 2, [], [_game(2, week=2)])
    ingest_cfb.run_cfbd(store, cfbd.week(2025, 1), client=fake.client(store))
    ingest_cfb.run_parse_cfbd(store)
    ingest_cfb.run_cfbd(store, cfbd.week(2025, 2), client=fake.client(store))
    ingest_cfb.run_parse_cfbd(store)
    current = store.execute("SELECT game_id, src_part FROM cfb_game_lines "
                            "WHERE valid_to_ts IS NULL ORDER BY game_id").fetchall()
    assert current == [(1, "regular:w1"), (2, "regular:w2")]


def test_a_line_correction_closes_one_row_and_opens_another(store):
    fake = FakeCFBD()
    _week_payloads(fake, 1, [], [_game(1)])
    ingest_cfb.run_cfbd(store, cfbd.week(2025, 1), client=fake.client(store))
    ingest_cfb.run_parse_cfbd(store)
    moved = _game(1, lines=[{"provider": "DraftKings", "spread": -7.5,
                             "formattedSpread": "Alabama -7.5", "overUnder": 51.5}])
    _week_payloads(fake, 1, [], [moved])
    ingest_cfb.run_cfbd(store, cfbd.week(2025, 1), client=fake.client(store))
    t = ingest_cfb.run_parse_cfbd(store)
    assert (t["inserted"], t["closed"]) == (1, 1)
    assert store.execute("SELECT spread FROM cfb_game_lines WHERE valid_to_ts IS NULL").fetchone() == (-7.5,)


def test_a_season_scope_cannot_overlap_a_week_scope(store):
    fake = FakeCFBD()
    _week_payloads(fake, 1, [], [_game(1)])
    fake.payloads[("lines", frozenset({("year", "2025"), ("seasonType", "both")}))] = [_game(1)]
    ingest_cfb.run_cfbd(store, cfbd.week(2025, 1), client=fake.client(store))
    ingest_cfb.run_parse_cfbd(store)
    ingest_cfb.run_cfbd(store, cfbd.lines_backfill([2025]), client=fake.client(store))
    t = ingest_cfb.run_parse_cfbd(store)
    assert t["refused"] == 1
    assert store.execute("SELECT COUNT(*) FROM cfb_game_lines WHERE valid_to_ts IS NULL").fetchone() == (1,)


def test_parts_overlap_rules():
    assert cfbd.parts_overlap("both", "regular:w3")
    assert cfbd.parts_overlap("both", "postseason:w1")
    assert not cfbd.parts_overlap("regular:w3", "regular:w4")
    assert not cfbd.parts_overlap("regular", "postseason:w1")
    assert not cfbd.parts_overlap("both", "both")


# =============================================================================
# normalisers
# =============================================================================

def test_the_side_a_spread_is_quoted_from_is_measured_not_assumed():
    home = _game(1, lines=[{"provider": "A", "spread": -7, "formattedSpread": "Alabama -7"}])
    away = _game(2, lines=[{"provider": "A", "spread": 3, "formattedSpread": "Auburn -3"}])
    flipped = _game(3, lines=[{"provider": "A", "spread": 7, "formattedSpread": "Alabama -7"}])
    pickem = _game(4, lines=[{"provider": "A", "spread": 0, "formattedSpread": "Alabama 0"}])
    m = {k: v for k, v, _ in cfbd_normalize.cfbd_lines([home, away, flipped, pickem], 2025).measurements}
    assert m["cfbd_lines.spread_quoted_home_side"] == 2
    assert m["cfbd_lines.spread_quoted_away_side"] == 1
    assert m["cfbd_lines.spread_side_unchecked"] == 1


def test_lines_without_a_provider_are_counted_and_games_without_lines_measured():
    n = cfbd_normalize.cfbd_lines([_game(1, lines=[{"spread": -3}]), _game(2, lines=[])], 2025)
    m = {k: v for k, v, _ in n.measurements}
    assert n.rows == [] and n.dropped == {"no_provider": 1}
    assert (m["cfbd_lines.games"], m["cfbd_lines.games_with_lines"]) == (2, 1)


def test_game_lines_store_no_derived_or_model_fields():
    from cfb import schema
    cols = set(schema.columns("cfb_game_lines")) | set(schema.columns("cfb_cfbd_games"))
    for banned in ("formatted_spread", "elo", "win_probability", "home_postgame_win_probability"):
        assert not any(banned in c for c in cols)


# =============================================================================
# provider names
# =============================================================================

def _dk(raw, spread, opens=True):
    ln = {"provider": raw, "spread": spread, "formattedSpread": f"Alabama {spread}",
          "overUnder": 50.5}
    if opens:
        ln.update(spreadOpen=-6.0, homeMoneyline=-250, awayMoneyline=210)
    return ln


def test_both_draftkings_spellings_become_one_book_and_the_fuller_feed_wins():
    """176 games through 2026 week 2 carry both spellings. The spaced feed never
    has opens or moneylines, and its spread differs on 30."""
    both = _game(1, lines=[_dk("Draft Kings", -7.5, opens=False), _dk("DraftKings", -7.0)])
    only_spaced = _game(2, lines=[_dk("Draft Kings", -3.0, opens=False)])
    n = cfbd_normalize.cfbd_lines([both, only_spaced], 2025)
    from cfb import schema
    cols = schema.columns("cfb_game_lines")
    got = {r[0]: dict(zip(cols, r)) for r in n.rows}
    assert (got[1]["provider"], got[1]["provider_raw"], got[1]["spread"]) == ("DraftKings", "DraftKings", -7.0)
    assert (got[2]["provider"], got[2]["provider_raw"]) == ("DraftKings", "Draft Kings")
    assert n.dropped == {"superseded_feed_rows": 1}
    m = {k: (v, d) for k, v, d in n.measurements}
    assert m["cfbd_lines.provider_feed_collisions"][0] == 1
    assert json.loads(m["cfbd_lines.provider_feed_collisions"][1]) == {"spread": 1}
    assert json.loads(m["cfbd_lines.providers"][1]) == {"DraftKings": 2}


def test_the_fuller_feed_wins_whichever_order_it_arrives_in():
    a = cfbd_normalize.cfbd_lines([_game(1, lines=[_dk("DraftKings", -7.0), _dk("Draft Kings", -7.5, False)])], 2025)
    b = cfbd_normalize.cfbd_lines([_game(1, lines=[_dk("Draft Kings", -7.5, False), _dk("DraftKings", -7.0)])], 2025)
    assert a.rows == b.rows


def test_an_unmapped_provider_spelled_two_ways_refuses_the_file():
    g = _game(1, lines=[_dk("Fan Duel", -7.0), _dk("FanDuel", -7.0)])
    with pytest.raises(cfbd_normalize.ProviderSplit, match="PROVIDER_CANONICAL"):
        cfbd_normalize.cfbd_lines([g], 2026)


def test_a_new_provider_is_kept_and_reported_as_unmapped():
    n = cfbd_normalize.cfbd_lines([_game(1, lines=[_dk("FanDuel", -7.0)])], 2026)
    m = {k: (v, d) for k, v, d in n.measurements}
    assert m["cfbd_lines.unmapped_providers"] == (1, json.dumps({"FanDuel": 1}))
    assert n.rows[0][9] == "FanDuel"


def test_every_provider_seen_2013_2026_is_mapped():
    seen = ["Bovada", "Caesars", "Caesars (Pennsylvania)", "Caesars Sportsbook (Colorado)",
            "consensus", "DraftKings", "Draft Kings", "ESPN Bet", "numberfire", "SugarHouse",
            "teamrankings", "William Hill (New Jersey)"]
    for raw in seen:
        assert cfbd_normalize.provider_fold(raw) in cfbd_normalize.PROVIDER_CANONICAL


def test_a_spelling_split_across_files_is_measured(store):
    fake = FakeCFBD()
    _week_payloads(fake, 1, [], [_game(1, lines=[_dk("FanDuel", -7.0)])])
    _week_payloads(fake, 2, [], [_game(2, week=2, lines=[_dk("Fan Duel", -3.0)])])
    ingest_cfb.run_cfbd(store, cfbd.week(2025, 1), client=fake.client(store))
    ingest_cfb.run_cfbd(store, cfbd.week(2025, 2), client=fake.client(store))
    ingest_cfb.run_parse_cfbd(store)
    ingest_cfb.measure_joins(store)
    v, d = store.execute("SELECT value, detail FROM cfb_measurements "
                         "WHERE key='cfbd_lines.provider_name_splits'").fetchone()
    assert v == 1 and json.loads(d) == {"fanduel": ["Fan Duel", "FanDuel"]}
