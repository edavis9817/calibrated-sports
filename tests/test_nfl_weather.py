"""NFL weather (f-05): sourced stadium points, measured offsets, roofs, and scope.

Run: pytest -q tests/test_nfl_weather.py

No network. The committed crosswalk and points files ARE under test - they are the
provenance of every NFL coordinate - so several tests read them rather than a fixture.
"""
import sqlite3
import time
from datetime import datetime, timezone

import httpx
import polars as pl
import pytest

import config
from feeds import fetch, nfl_venues, openmeteo, paths, schema
from jobs import ingest_feeds as J

UTC = timezone.utc


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "STORAGE_DIR", str(tmp_path))
    monkeypatch.setattr(fetch, "GAP_S", 0)
    paths.ensure_dirs()
    con = J.connect()
    yield con
    con.close()


class FakeMeteo:
    """Open-Meteo with no network. Answers from a cell 0.01 deg north of each point asked,
    so grid_offset_km is a known ~1.11 km."""

    def __init__(self):
        self.calls = []

    def handler(self, request):
        self.calls.append(str(request.url))
        lats = [float(x) for x in request.url.params["latitude"].split(",")]
        lons = [float(x) for x in request.url.params["longitude"].split(",")]
        date = request.url.params["start_date"]
        times = [f"{date}T{h:02d}:00" for h in range(24)]
        blocks = [{"latitude": la + 0.01, "longitude": lo, "elevation": 200.0,
                   "hourly": {"time": times, "temperature_2m": [55.0] * 24,
                              "relative_humidity_2m": [50] * 24,
                              "precipitation": [0.0] * 24, "wind_speed_10m": [12.0] * 24,
                              "wind_gusts_10m": [20.0] * 24, "cloud_cover": [10] * 24}}
                  for la, lo in zip(lats, lons)]
        return httpx.Response(200, json=blocks if len(blocks) > 1 else blocks[0])

    def client(self, con):
        return fetch.Client(con, httpx.Client(transport=httpx.MockTransport(self.handler)))


def games_frame(rows):
    cols = ["game_id", "season", "gameday", "gametime", "stadium_id", "stadium", "roof"]
    return pl.DataFrame([dict(zip(cols, r)) for r in rows])


# ---------------------------------------------------------------------------
# the committed provenance files
# ---------------------------------------------------------------------------

def test_every_crosswalk_venue_has_a_points_row_and_a_stated_source():
    xw = nfl_venues.load_crosswalk()
    pts = nfl_venues.load_points()
    qids = {m["qid"] for m in xw.values()}
    assert qids and qids <= set(pts), sorted(qids - set(pts))
    assert {m["roof_source"] for m in xw.values()} <= {"nflverse_history", "hand"}
    measured = [p for p in pts.values() if p["offset_km"] is not None]
    # A measured offset always names the footprint it was measured against.
    assert measured and all(p["osm_ref"] for p in measured)
    assert all(p["offset_km"] >= 0 for p in measured)


def test_a_coordinate_outside_its_footprint_says_so():
    """SoFi's Wikidata point is at 0.0108 deg precision and lies outside the footprint:
    the file must carry that as a positive offset, not round it to the venue."""
    sofi = nfl_venues.load_points()["Q19520501"]
    assert sofi["inside_footprint"] == 0 and sofi["offset_km"] > 0.1


# ---------------------------------------------------------------------------
# resolution: the pair, never the id alone
# ---------------------------------------------------------------------------

def test_a_london_game_under_a_jacksonville_id_resolves_to_london():
    xw, pts = nfl_venues.load_crosswalk(), nfl_venues.load_points()
    v, why = nfl_venues.resolve("JAX00", "Tottenham Hotspur Stadium", xw, pts)
    assert why is None and v["qid"] == "Q55074091" and v["latitude"] > 51
    home, _ = nfl_venues.resolve("JAX00", "EverBank Stadium", xw, pts)
    assert home["latitude"] < 31


def test_an_unknown_name_under_a_known_id_is_refused_not_guessed():
    xw, pts = nfl_venues.load_crosswalk(), nfl_venues.load_points()
    v, why = nfl_venues.resolve("JAX00", "Some New Stadium", xw, pts)
    assert v is None and why == "pair_not_in_crosswalk"


def test_a_venue_without_a_measured_offset_is_refused():
    xw = {("X00", "Old Park"): {"qid": "Q1", "roof_type": "open_air", "roof_source": "hand"}}
    pts = {"Q1": {"qid": "Q1", "label": "Old Park", "latitude": 40.0, "longitude": -80.0,
                  "offset_km": None}}
    assert nfl_venues.resolve("X00", "Old Park", xw, pts) == (None, "offset_not_measured")
    pts["Q1"]["offset_km"] = 0.0
    v, why = nfl_venues.resolve("X00", "Old Park", xw, pts)
    assert why is None and v["offset_km"] == 0.0


# ---------------------------------------------------------------------------
# roofs
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("structure,label,expect", [
    ("open_air", "outdoors", 1),
    ("open_air", "dome", None),        # nflverse's Melbourne/Munich/Paris label: a conflict
    ("open_air", "", None),
    ("retractable", "open", 1),
    ("retractable", "closed", 0),
    ("retractable", "", None),          # not published until the game is played
    ("retractable", "outdoors", None),  # Frankfurt: not a state a retractable roof reports
    ("fixed", "dome", 0),
    ("fixed", "outdoors", None),
])
def test_playing_conditions_needs_both_sources_to_agree(structure, label, expect):
    assert nfl_venues.playing_conditions(structure, label) == expect


# ---------------------------------------------------------------------------
# time and geometry
# ---------------------------------------------------------------------------

def test_kickoff_is_eastern_in_both_daylight_and_standard_time():
    assert nfl_venues.kickoff_ts("2026-09-13", "13:00") == datetime(
        2026, 9, 13, 17, tzinfo=UTC).timestamp()
    assert nfl_venues.kickoff_ts("2026-01-11", "13:00") == datetime(
        2026, 1, 11, 18, tzinfo=UTC).timestamp()
    assert nfl_venues.kickoff_ts("2026-01-11", None) is None


def test_footprint_offset_is_zero_inside_and_the_edge_distance_outside():
    ring = [(0.0, 0.0), (0.0, 0.01), (0.01, 0.01), (0.01, 0.0), (0.0, 0.0)]
    inside, off, cen = nfl_venues.footprint_offset(0.005, 0.005, ring and [ring])
    assert inside and off == 0.0 and cen < 0.01
    inside, off, _ = nfl_venues.footprint_offset(0.005, 0.02, [ring])
    assert not inside and off == pytest.approx(1.112, abs=0.01)   # 0.01 deg of longitude


# ---------------------------------------------------------------------------
# the fetch, end to end
# ---------------------------------------------------------------------------

def _now():
    return datetime(2026, 9, 22, 12, tzinfo=UTC).timestamp()


def test_nfl_weather_rows_carry_offsets_roof_and_horizon(store, monkeypatch):
    # `fetched_ts` is the REAL fetch instant (ingest_feeds.py:404), on purpose - a
    # forecast's horizon is only meaningful against the clock the fetch actually
    # happened on, and a caller must not be able to write a false one. So freeze the
    # real clock here rather than threading `now` into it, or this assertion rots.
    monkeypatch.setattr(time, "time", _now)
    games = games_frame([
        ("2026_03_A_GB", 2026, "2026-09-24", "20:15", "GNB00", "Lambeau Field", "outdoors"),
        ("2026_03_B_DET", 2026, "2026-09-24", "13:00", "DET00", "Ford Field", "dome"),
        ("2026_03_C_IND", 2026, "2026-09-24", "16:25", "IND00", "Lucas Oil Stadium", ""),
        ("2026_03_D_LAC", 2026, "2026-09-24", "16:05", "LAX97", "StubHub Center", "outdoors"),
        ("2026_03_E_XXX", 2026, "2026-09-24", "13:00", "XXX00", "Nowhere Field", "outdoors"),
    ])
    fake = FakeMeteo()
    out = J.run_nfl_weather(store, games, days=3, client=fake.client(store), now=_now(),
                            verbose=False)
    assert out["domed"] == 1                                   # Ford Field: skipped
    assert out["refused"] == {"offset_not_measured": 1, "pair_not_in_crosswalk": 1}
    rows = {r[0]: r for r in J.nfl_weather_readout(store)}
    assert set(rows) == {"2026_03_A_GB", "2026_03_C_IND"}
    gb, ind = rows["2026_03_A_GB"], rows["2026_03_C_IND"]
    # (game, kind, kickoff, coord_ref, coord_offset, grid_offset, horizon_h, roof_type,
    #  game_roof, playing_conditions, temp, wind)
    assert gb[1] == "forecast" and gb[3] == "Q860790" and gb[4] == 0.0
    assert gb[5] == pytest.approx(1.112, abs=0.01)
    assert gb[6] > 48                                          # fetched days ahead
    assert gb[7:10] == ("open_air", "outdoors", 1)
    assert ind[7:10] == ("retractable", None, None)            # roof state not published


def test_archive_rows_have_no_fetch_time_because_a_reanalysis_has_no_horizon(store):
    games = games_frame([("2025_01_GB", 2025, "2025-09-14", "16:25", "GNB00",
                          "Lambeau Field", "outdoors")])
    J.run_nfl_weather(store, games, season=2025, client=FakeMeteo().client(store),
                      now=_now(), verbose=False)
    kind, fetched = store.execute("SELECT kind, fetched_ts FROM weather_at_kickoff "
                                  "WHERE sport='nfl'").fetchone()
    assert kind == "archive" and fetched is None


def test_an_nfl_run_cannot_close_a_cfb_row_of_the_same_date(store):
    """Both write weather_at_kickoff scoped by (feed, 'date:kind'). Sharing CFB's feed name
    would close every CFB row of the date as 'no longer carried'."""
    date_part = "2026-09-24:forecast"
    fid, _ = fetch.archive(store, "weather", date_part, "u", b"{}", kind="json")
    cfb_venue = {"venue_id": 1, "latitude": 40.0, "longitude": -80.0}
    measured = openmeteo.at_hour(FakeMeteo().handler(httpx.Request(
        "GET", "https://api.open-meteo.com/v1/forecast?latitude=40&longitude=-80"
               "&start_date=2026-09-24&end_date=2026-09-24")).json(), 0,
        datetime(2026, 9, 24, 19, tzinfo=UTC).timestamp())
    J._apply(store, "weather", None, fid,
             [openmeteo.row("cfb", 99, "forecast", cfb_venue,
                            datetime(2026, 9, 24, 19, tzinfo=UTC).timestamp(), measured)],
             "weather_at_kickoff", "cfb", part=date_part)
    # 13:00 ET = 17:00Z, the SAME UTC date as the CFB row. (A 20:15 ET kickoff is the
    # next UTC date, and the first version of this test used one and proved nothing.)
    games = games_frame([("2026_03_A_GB", 2026, "2026-09-24", "13:00", "GNB00",
                          "Lambeau Field", "outdoors")])
    J.run_nfl_weather(store, games, days=3, client=FakeMeteo().client(store), now=_now(),
                      verbose=False)
    assert store.execute("SELECT src_part FROM weather_at_kickoff WHERE sport='nfl'"
                         ).fetchone()[0] == date_part
    current = dict(store.execute("SELECT sport, COUNT(*) FROM weather_at_kickoff "
                                 "WHERE valid_to_ts IS NULL GROUP BY sport").fetchall())
    assert current == {"cfb": 1, "nfl": 1}


def test_a_window_takes_whole_utc_dates_so_a_rerun_closes_nothing(store):
    """Two runs an hour apart whose windows cut the same date at different instants must
    carry the same games; a timestamp window would close the earlier game on the rerun."""
    games = games_frame([
        ("early", 2026, "2026-09-18", "20:15", "GNB00", "Lambeau Field", "outdoors"),  # 00:15Z 19th
        ("late", 2026, "2026-09-19", "15:00", "NYC01", "MetLife Stadium", "outdoors"),  # 19:00Z 19th
    ])
    fake = FakeMeteo()
    t0 = datetime(2026, 9, 22, 1, tzinfo=UTC).timestamp()        # 3 days back: 19th 01:00Z
    a = J.run_nfl_weather(store, games, days=3, client=fake.client(store), now=t0,
                          verbose=False)
    b = J.run_nfl_weather(store, games, days=3, client=fake.client(store), now=t0 + 3600,
                          verbose=False)
    # A timestamp window at t0+1h starts 19th 02:00Z and would drop `early` (00:15Z).
    assert a["games"] == b["games"] == 2
    # A forecast re-fetched is RESTATED (new fetched_ts, new version) - that is the
    # horizon being kept, not a loss. What must not happen is a game with no current row.
    current = dict(store.execute("SELECT game_id, COUNT(*) FROM weather_at_kickoff WHERE "
                                 "sport='nfl' AND valid_to_ts IS NULL GROUP BY game_id"))
    assert current == {"early": 1, "late": 1}
    versions = store.execute("SELECT COUNT(*) FROM weather_at_kickoff WHERE "
                             "game_id='early'").fetchone()[0]
    assert versions == 2                                   # both horizons held


def test_a_store_created_before_f05_gains_the_columns(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "STORAGE_DIR", str(tmp_path))
    paths.ensure_dirs()
    old = sqlite3.connect(paths.db_path())
    added = {c for c, _t in schema.TABLES["weather_at_kickoff"][1]} - {
        "provider_latitude", "provider_longitude", "grid_offset_km", "coord_source",
        "coord_ref", "coord_offset_km", "fetched_ts", "roof_type", "game_roof",
        "playing_conditions"}
    cols = [c for c, _t in schema.TABLES["weather_at_kickoff"][1] if c in added]
    old.execute(f"CREATE TABLE weather_at_kickoff ({', '.join(cols)}, src_dataset, "
                "src_season, src_part, src_file_id, row_sha, valid_from_ts, valid_to_ts)")
    old.commit()
    old.close()
    assert "coord_offset_km" not in {r[1] for r in sqlite3.connect(paths.db_path()).execute(
        "PRAGMA table_info(weather_at_kickoff)")}
    con = J.connect()
    have = {r[1] for r in con.execute("PRAGMA table_info(weather_at_kickoff)")}
    assert {"coord_offset_km", "grid_offset_km", "fetched_ts", "playing_conditions"} <= have
    assert schema.added_columns(con) == []
    con.close()


def test_a_scope_is_required():
    with pytest.raises(ValueError):
        J.nfl_games_for_weather(games_frame([]), now=time.time())
