"""Injuries, weather and news: as-of correctness, and the two rules that bound them.

Run: pytest -q tests/test_ingest_feeds.py

Every fixture is INVENTED and no test touches the network. The two rules under test:
news never carries anyone else's words, and an injury report is answerable AS OF an
instant rather than only as "now".
"""
import gzip
import io
import json
import os
import sqlite3
import time
from datetime import datetime, timezone

import httpx
import polars as pl
import pytest

import config
from feeds import fetch, normalize, openmeteo, paths, rss, schema, sources
from jobs import ingest_feeds as J

RSS_DOC = b"""<?xml version="1.0"?><rss version="2.0"><channel>
  <title>Example Sports</title>
  <item><title>Headline one</title><link>https://example.test/1</link>
    <guid>g-1</guid><pubDate>Sat, 19 Sep 2026 13:00:00 GMT</pubDate>
    <description>FULL ARTICLE TEXT THAT MUST NEVER BE STORED</description></item>
  <item><title>Headline two</title><link>https://example.test/2</link>
    <guid>g-2</guid><pubDate>not a date</pubDate>
    <content:encoded xmlns:content="http://purl.org/rss/1.0/modules/content/">MORE TEXT</content:encoded></item>
</channel></rss>"""

ATOM_DOC = b"""<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom">
  <title>Atom Sports</title>
  <entry><title>Atom headline</title><id>a-1</id>
    <link rel="alternate" href="https://example.test/a1"/>
    <published>2026-09-19T14:30:00Z</published>
    <summary>SUMMARY TEXT THAT MUST NEVER BE STORED</summary></entry>
</feed>"""


def injuries_parquet(season, *, with_date_modified, rows=2):
    """The two real eras: <=2024 has date_modified and no season_type, >=2025 the reverse."""
    base = {
        "season": [season] * rows, "game_type": ["REG"] * rows, "team": ["AAA", "BBB"][:rows],
        "week": [1] * rows, "gsis_id": [f"00-000{i}" for i in range(rows)],
        "position": ["WR"] * rows, "full_name": [f"Player {i}" for i in range(rows)],
        "first_name": ["P"] * rows, "last_name": ["L"] * rows,
        "report_primary_injury": ["Knee"] * rows, "report_secondary_injury": [None] * rows,
        "report_status": ["Questionable"] * rows,
        "practice_primary_injury": ["Knee"] * rows,
        "practice_secondary_injury": [None] * rows,
        "practice_status": ["Limited Participation in Practice"] * rows,
    }
    if with_date_modified:
        base["date_modified"] = [datetime(season, 9, 6, 19, 5, tzinfo=timezone.utc)] * rows
    else:
        base["season_type"] = ["REG"] * rows
    buf = io.BytesIO()
    pl.DataFrame(base).write_parquet(buf)
    return buf.getvalue()


def venues_parquet(season):
    buf = io.BytesIO()
    pl.DataFrame({
        "venue_id": [10, 10, 11, 12, 13],           # 10 twice (two teams, one stadium)
        "venue_name": ["Open Field", "Open Field", "Dome Arena", "No Coords Park", "Grass Bowl"],
        "city": ["A", "A", "B", "C", "D"], "state": ["S"] * 5,
        "country_code": ["US"] * 5,
        "latitude": [40.0, 40.0, 41.0, None, 42.0],
        "longitude": [-80.0, -80.0, -81.0, None, -82.0],
        "elevation": [100.0] * 5, "timezone": ["America/New_York"] * 5,
        "dome": [False, False, True, False, False], "grass": [True] * 5,
        "capacity": [50000] * 5,
    }).write_parquet(buf)
    return buf.getvalue()


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "STORAGE_DIR", str(tmp_path))
    monkeypatch.setattr(fetch, "GAP_S", 0)
    paths.ensure_dirs()
    con = J.connect()
    yield con
    con.close()


class FakeHTTP:
    """Release listings, assets, RSS documents and Open-Meteo, with no network."""

    def __init__(self):
        self.calls = []
        self.weather_hours = 24

    def handler(self, request: httpx.Request):
        url = str(request.url)
        self.calls.append(url)
        if "api.github.com" in url:
            tag = url.rsplit("/", 1)[-1]
            names = ({"injuries_2024.parquet", "injuries_2025.parquet"} if tag == "injuries"
                     else {"cfb_team_info_2026.parquet"})
            return httpx.Response(200, json={"assets": [
                {"name": n, "browser_download_url": f"https://assets.test/{n}"} for n in names]})
        if "assets.test" in url:
            name = url.rsplit("/", 1)[-1]
            if name.startswith("injuries_"):
                season = int(name.split("_")[1].split(".")[0])
                return httpx.Response(200, content=injuries_parquet(
                    season, with_date_modified=season <= 2024))
            return httpx.Response(200, content=venues_parquet(2026))
        if "open-meteo" in url:
            lats = request.url.params["latitude"].split(",")
            date = request.url.params["start_date"]
            blocks = []
            for _ in lats:
                times = [f"{date}T{h:02d}:00" for h in range(self.weather_hours)]
                blocks.append({"latitude": 40.0, "longitude": -80.0, "elevation": 99.0,
                               "hourly": {"time": times,
                                          "temperature_2m": [50.0] * len(times),
                                          "relative_humidity_2m": [60] * len(times),
                                          "precipitation": [0.1] * len(times),
                                          "wind_speed_10m": [9.0] * len(times),
                                          "wind_gusts_10m": [15.0] * len(times),
                                          "cloud_cover": [40] * len(times)}})
            return httpx.Response(200, json=blocks)
        return httpx.Response(200, content=ATOM_DOC if "atom" in url else RSS_DOC)

    def client(self, con):
        return fetch.Client(con, httpx.Client(transport=httpx.MockTransport(self.handler)))


# ---------------------------------------------------------------------------
# news: headline, source, timestamp, link - and never anything else
# ---------------------------------------------------------------------------

def test_the_news_table_cannot_hold_article_text():
    assert schema.news_column_violations() == []
    assert not (set(schema.columns("news_items")) & set(schema.NEWS_FORBIDDEN))


def test_the_parser_returns_only_four_fields_and_never_the_body():
    for doc in (RSS_DOC, ATOM_DOC):
        items = rss.parse(doc, "feed")
        assert items
        for item in items:
            assert set(item) == set(rss.KEPT)
            blob = json.dumps(item).upper()
            assert "ARTICLE TEXT" not in blob and "SUMMARY TEXT" not in blob


def test_an_unparseable_date_is_null_not_now():
    items = {i["guid"]: i for i in rss.parse(RSS_DOC, "feed")}
    assert items["g-1"]["published_ts"] == 1789822800.0
    assert items["g-2"]["published_ts"] is None


def test_news_lands_in_the_store_with_no_body_anywhere(store):
    fake = FakeHTTP()
    J.run_news(store, ["espn_nfl"], client=fake.client(store), verbose=False)
    rows = store.execute("SELECT sport, feed, title, link, source FROM news_items").fetchall()
    assert len(rows) == 2 and rows[0][0] == "nfl"
    dump = "\n".join(store.iterdump()).upper()
    assert "ARTICLE TEXT" not in dump
    # the ARCHIVE keeps the document verbatim, which is the point of raw-first: the body
    # exists on disk, is never parsed into the store, and can prove what was published.
    rel = store.execute("SELECT rel_path FROM feeds_raw_files").fetchone()[0]
    with gzip.open(os.path.join(paths.raw_root(), *rel.split("/"))) as f:
        assert b"FULL ARTICLE TEXT" in f.read()


# ---------------------------------------------------------------------------
# injuries: two eras, and the as-of that the whole design exists for
# ---------------------------------------------------------------------------

def test_both_injury_eras_parse_and_the_upstream_asof_is_kept_where_it_exists():
    old, _ = normalize.injuries(injuries_parquet(2024, with_date_modified=True), 2024)
    new, _ = normalize.injuries(injuries_parquet(2025, with_date_modified=False), 2025)
    cols = schema.columns("injury_reports")
    o = dict(zip(cols, old[0]))
    n = dict(zip(cols, new[0]))
    assert o["season_type"] == "REG" and n["season_type"] == "REG"   # falls back to game_type
    assert o["upstream_asof_ts"] == datetime(2024, 9, 6, 19, 5, tzinfo=timezone.utc).timestamp()
    assert n["upstream_asof_ts"] is None


def test_an_unrecognised_capture_column_is_refused_not_ignored():
    df = pl.read_parquet(io.BytesIO(injuries_parquet(2025, with_date_modified=False)))
    buf = io.BytesIO()
    df.with_columns(pl.lit("2026-01-01").alias("scraped_at")).write_parquet(buf)
    with pytest.raises(normalize.RefusedFile, match="capture-time"):
        normalize.injuries(buf.getvalue(), 2025)


def test_the_report_answers_as_of_an_instant_not_only_as_of_now(store):
    """The whole point: what was KNOWN on Sunday morning, not what turned out true."""
    fake = FakeHTTP()
    J.run_injuries(store, [2025], client=fake.client(store), verbose=False)
    first = time.time()
    time.sleep(0.05)
    # upstream amends the report: one player is downgraded to Out
    amended = pl.read_parquet(io.BytesIO(injuries_parquet(2025, with_date_modified=False)))
    amended = amended.with_columns(
        pl.when(pl.col("gsis_id") == "00-0000").then(pl.lit("Out"))
        .otherwise(pl.col("report_status")).alias("report_status"))
    buf = io.BytesIO()
    amended.write_parquet(buf)
    file_id, _ = fetch.archive(store, "injuries", 2025, "u", buf.getvalue(), suffix=".parquet.gz")
    rows, _ = normalize.injuries(buf.getvalue(), 2025)
    J._apply(store, "injuries", 2025, file_id, rows, "injury_reports", "amended")

    before = dict((r[1], r[3]) for r in J.injury_report_as_of(store, 2025, 1, first))
    after = dict((r[1], r[3]) for r in J.injury_report_as_of(store, 2025, 1, time.time()))
    assert before["Player 0"] == "Questionable", "the earlier report must survive the amendment"
    assert after["Player 0"] == "Out"


# ---------------------------------------------------------------------------
# venues and weather
# ---------------------------------------------------------------------------

def test_one_row_per_venue_and_a_venue_with_no_coordinates_is_dropped(store):
    rows, dropped = normalize.venues(venues_parquet(2026), 2026)
    ids = [r[2] for r in rows]
    assert sorted(ids) == [10, 11, 13]                    # 10 deduplicated, 12 has no coords
    assert dropped["no_coordinates"] == 1


def test_weather_takes_the_kickoff_HOUR_and_never_interpolates(store):
    payload = json.loads(FakeHTTP().handler(httpx.Request(
        "GET", "https://archive-api.open-meteo.com/v1/archive?latitude=40&longitude=-80"
               "&start_date=2026-09-19&end_date=2026-09-19")).content)
    kickoff = datetime(2026, 9, 19, 19, 30, tzinfo=timezone.utc).timestamp()
    at = openmeteo.at_hour(payload, 0, openmeteo.floor_hour(kickoff))
    assert at["observed_hour_ts"] == datetime(2026, 9, 19, 19, tzinfo=timezone.utc).timestamp()
    assert at["temperature_f"] == 50.0
    # an hour the provider did not publish is ABSENT, not the nearest one
    assert openmeteo.at_hour(payload, 0, openmeteo.floor_hour(kickoff) + 30 * 3600) is None


def test_a_dome_is_skipped_and_a_missing_venue_is_reported_not_guessed(store, tmp_path):
    fake = FakeHTTP()
    J.run_venues(store, [2026], client=fake.client(store), verbose=False)
    cfb = sqlite3.connect(":memory:")
    cfb.executescript("CREATE TABLE cfb_games (game_id, start_ts, venue_id, venue, "
                      "start_time_tbd, valid_to_ts);")
    now = time.time()
    cfb.executemany("INSERT INTO cfb_games VALUES (?,?,?,?,0,NULL)", [
        (1, now + 3600, 10, "Open Field"),      # has coordinates
        (2, now + 7200, 11, "Dome Arena"),      # domed: skipped
        (3, now + 9000, 99, "Unknown Park"),    # no venue row: reported
    ])
    cfb.commit()
    matched, missing, domed = J.venue_for_games(store, cfb, days=1, now=now)
    assert [m[0] for m in matched] == ["1"]
    assert [d[0] for d in domed] == [2]
    assert [m[0] for m in missing] == [3]


def test_all_batches_of_one_date_are_one_versioned_write(store, monkeypatch):
    """Applying each batch separately closed the previous batch's rows: +50 -50, and a
    store holding only the last batch. Found on the first real run."""
    monkeypatch.setattr(sources, "OPEN_METEO_MAX_COORDS", 2)
    fake = FakeHTTP()
    J.run_venues(store, [2026], client=fake.client(store), verbose=False)
    cfb = sqlite3.connect(":memory:")
    cfb.executescript("CREATE TABLE cfb_games (game_id, start_ts, venue_id, venue, "
                      "start_time_tbd, valid_to_ts);")
    now = time.time()
    base = now + 3600
    cfb.executemany("INSERT INTO cfb_games VALUES (?,?,?,?,0,NULL)",
                    [(i, base + i * 60, [10, 13][i % 2], "V") for i in range(5)])
    cfb.commit()
    out = J.run_weather(store, cfb, days=1, client=fake.client(store), now=now, verbose=False)
    current = store.execute("SELECT COUNT(*) FROM weather_at_kickoff "
                            "WHERE valid_to_ts IS NULL").fetchone()[0]
    assert out["rows_written"] == 5 and current == 5
    assert len([c for c in fake.calls if "open-meteo" in c]) == 3     # 5 games, 2 per call


# ---------------------------------------------------------------------------
# the store's own rules
# ---------------------------------------------------------------------------

def test_the_archive_keeps_one_copy_per_content_and_never_overwrites(store):
    body = b"<rss><channel><title>t</title></channel></rss>"
    a, first = fetch.archive(store, "espn_nfl", None, "u", body)
    b, second = fetch.archive(store, "espn_nfl", None, "u", body)
    assert (first, second) == ("new", "unchanged_content") and a == b
    c, third = fetch.archive(store, "espn_nfl", None, "u", body + b"<!--x-->")
    assert third == "new" and c != a
    assert J.audit(store)["clean"]


def test_a_scope_with_a_colon_is_still_a_legal_path(store):
    """Windows cannot hold ':' in a filename and "2026-09-19:forecast" is a real scope."""
    fid, _ = fetch.archive(store, "weather", "2026-09-19:forecast", "u", b"{}", kind="json")
    rel = store.execute("SELECT rel_path, scope FROM feeds_raw_files WHERE file_id=?",
                        (fid,)).fetchone()
    assert ":" not in rel[0] and rel[1] == "2026-09-19:forecast"
    assert os.path.exists(os.path.join(paths.raw_root(), *rel[0].split("/")))


def test_the_feeds_job_has_no_publish_path(store):
    import ast
    tree = ast.parse(open(J.__file__, encoding="utf-8").read())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert not imported & {"boto3", "botocore", "r2", "store"}, sorted(imported)
