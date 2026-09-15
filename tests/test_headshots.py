"""Headshots: URLs from the raw archive only, https only, latest season wins,
and never an image in the export."""
import json
import os
import socket
import sqlite3

import polars as pl
import pytest

import config
import store
from jobs import export_web as E
from jobs import ingest_headshots as H

NOW = 1_789_500_000.0


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setattr(config, "RAW_DIR", str(tmp_path / "raw"))
    monkeypatch.setattr(E, "SLUG_DIR", str(tmp_path / "slugs"))
    store.init_db()
    return tmp_path


def _parquet(raw, date, season, rows):
    d = raw / "nflverse" / date
    d.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(rows, schema={"gsis_id": pl.Utf8, "season": pl.Int32, "week": pl.Int32,
                               "headshot_url": pl.Utf8}, orient="row").write_parquet(
        d / f"roster_weekly_{season}.parquet")


def test_valid_headshot_is_https_with_a_host_only():
    assert E.valid_headshot("https://static.www.nfl.com/image/x.png") == "https://static.www.nfl.com/image/x.png"
    assert E.valid_headshot("http://static.www.nfl.com/image/x.png") is None
    assert E.valid_headshot("https:///image/x.png") is None
    assert E.valid_headshot("") is None and E.valid_headshot(None) is None
    assert E.valid_headshot("javascript:alert(1)") is None


def test_ingest_reads_only_the_archive_and_never_the_network(env, monkeypatch):
    def no_network(*a, **k):
        raise AssertionError("network used")
    monkeypatch.setattr(socket.socket, "connect", no_network)
    monkeypatch.setattr(socket, "create_connection", no_network)
    raw = env / "raw"
    _parquet(raw, "2026-09-09", 2025, [("00-A", 2025, 1, "https://static.www.nfl.com/a25.png"),
                                       ("00-B", 2025, 1, None)])
    _parquet(raw, "2026-09-14", 2026, [("00-A", 2026, 1, "https://static.www.nfl.com/a26old.png")])
    _parquet(raw, "2026-09-15", 2026, [("00-A", 2026, 1, "https://static.www.nfl.com/a26.png")])
    assert H.archived_seasons() == [2025, 2026]
    s = H.ingest([2025, 2026], log=lambda *_: None)
    assert s["rows"] == 2 and s["players"] == 1 and s["hosts"] == {"static.www.nfl.com": 2}
    c = sqlite3.connect(config.DB_PATH)
    rows = c.execute("SELECT season, headshot_url, source_version FROM player_headshot ORDER BY season").fetchall()
    assert rows == [(2025, "https://static.www.nfl.com/a25.png", "2026-09-09"),
                    (2026, "https://static.www.nfl.com/a26.png", "2026-09-15")]   # newest date dir
    H.ingest([2026], log=lambda *_: None)                                            # idempotent upsert
    assert c.execute("SELECT COUNT(*) FROM player_headshot").fetchone()[0] == 2


def test_latest_season_then_week_wins_and_invalid_latest_falls_back(env):
    c = sqlite3.connect(config.DB_PATH)
    c.executemany("INSERT INTO player_headshot (gsis_id, season, week, headshot_url) VALUES (?,?,?,?)", [
        ("00-A", 2024, 18, "https://static.www.nfl.com/a24.png"),
        ("00-A", 2025, 2, "https://static.www.nfl.com/a25w2.png"),
        ("00-A", 2025, 1, "https://static.www.nfl.com/a25w1.png"),
        ("00-B", 2025, 3, "http://insecure.example/b.png"),
        ("00-B", 2024, 3, "https://static.www.nfl.com/b24.png"),
        ("00-C", 2025, 1, "http://insecure.example/c.png")])
    c.commit()
    hs = E.load_headshots(sqlite3.connect(f"file:{config.DB_PATH}?mode=ro", uri=True))
    assert hs == {"00-A": "https://static.www.nfl.com/a25w2.png",
                  "00-B": "https://static.www.nfl.com/b24.png"}


def test_summary_always_carries_the_key_and_the_export_holds_no_images(env):
    c = sqlite3.connect(config.DB_PATH)
    c.execute("INSERT INTO nfl_games (game_id, data_version, season, week, game_type, gameday, kickoff_ts, "
              "home_team, away_team, home_score, away_score, source, ingested_ts) "
              "VALUES ('2025_01_BUF_MIA','v1',2025,1,'REG','2025-09-07',?, 'MIA','BUF',20,27,'t',0)", (NOW - 3e7,))
    c.execute("INSERT INTO nfl_games (game_id, data_version, season, week, game_type, gameday, kickoff_ts, "
              "home_team, away_team, source, ingested_ts) "
              "VALUES ('2026_01_BUF_HOU','v1',2026,1,'REG','2026-09-13',?, 'HOU','BUF','t',0)", (NOW + 2e5,))
    for gsis in ("00-A", "00-B"):
        c.execute("INSERT INTO nfl_player_week (gsis_id, season, week, season_type, data_version, player_name, "
                  "position, team, receptions, targets, source, ingested_ts) "
                  "VALUES (?,2025,1,'REG','v1',?,'WR','BUF',3,4,'t',0)", (gsis, f"Player {gsis}"))
    c.execute("INSERT INTO player_headshot (gsis_id, season, week, headshot_url) VALUES "
              "('00-A', 2025, 1, 'https://static.www.nfl.com/a.png')")
    c.commit()
    dest = str(env / "out")
    s = E.export(only=["players", "manifest"], now_ts=NOW, dest=dest)
    assert s["headshots"] == {"with_url": 1, "players": 2}
    a = json.load(open(E.local_path(dest, "nfl/players/00-A/summary.json"), encoding="utf-8"))
    b = json.load(open(E.local_path(dest, "nfl/players/00-B/summary.json"), encoding="utf-8"))
    assert a["identity"]["headshot_url"] == "https://static.www.nfl.com/a.png"
    assert "headshot_url" in b["identity"] and b["identity"]["headshot_url"] is None
    for root, _dirs, files in os.walk(dest):
        for fn in files:
            assert not fn.lower().endswith((".png", ".jpg", ".jpeg", ".webp", ".gif")), fn
            if fn != E.STATE_FILE:
                assert fn.endswith(".json"), fn
                json.load(open(os.path.join(root, fn), encoding="utf-8"))
