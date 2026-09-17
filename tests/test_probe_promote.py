"""The exchange probe promotion: the join, the close, and provenance.

Run: pytest -q tests/test_probe_promote.py

A synthetic probe database stands in for cfb_probe.db. The real one is 23.9 GB,
read-only, and never touched by a test.
"""
import sqlite3

import pytest

import config
from cfb import paths, probe_promote, schema, versioning
from jobs import ingest_cfb

KICK = 1789228800.0          # 2026-09-12 16:00Z = 12:00 ET


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "STORAGE_DIR", str(tmp_path))
    paths.ensure_dirs()
    conn = ingest_cfb.connect()
    for table, dataset, part in (("cfb_games", "games", None),
                                 ("cfb_cfbd_games", "cfbd_games", "regular:w2")):
        cols = schema.columns(table)
        g = dict.fromkeys(cols)
        g.update(game_id=401, season=2026, week=2, start_ts=KICK,
                 home_team="San José State", away_team="UMass")
        versioning.apply(conn, dataset, 2026, 1, 1.0, table, [tuple(g[c] for c in cols)], part=part)
    conn.commit()
    yield conn
    conn.close()


@pytest.fixture
def probe(tmp_path):
    p = sqlite3.connect(str(tmp_path / "cfb_probe.db"))
    p.executescript("""
      CREATE TABLE markets (venue TEXT, market_id TEXT, event_id TEXT, sport TEXT, market_type TEXT,
        subject TEXT, line REAL, title TEXT, open_ts REAL, close_ts REAL, settle_ts REAL,
        result TEXT, first_seen REAL, last_seen REAL);
      CREATE TABLE quotes (id INTEGER PRIMARY KEY, ts REAL, venue TEXT, market_id TEXT,
        best_bid REAL, best_ask REAL, mid REAL, last REAL, volume REAL, open_interest REAL);
      CREATE TABLE poll_log (ts REAL, venue TEXT, endpoint TEXT);
    """)
    M = "INSERT INTO markets VALUES (?,?,?,'nfl',?,?,?,?,0,0,0,NULL,0,0)"
    p.executemany(M, [
        ("cfb_kalshi", "KXNCAAFGAME-26SEP12MASSSJSU-SJSU", "KXNCAAFGAME-26SEP12MASSSJSU", "game",
         "San Jose St.", None, "San Jose St. wins"),
        ("cfb_kalshi", "KXNCAAFGAME-26SEP12MASSSJSU-MASS", "KXNCAAFGAME-26SEP12MASSSJSU", "game",
         "UMass", None, "UMass wins"),
        ("cfb_kalshi", "KXNCAAFTOTAL-26SEP12MASSSJSU-52", "KXNCAAFTOTAL-26SEP12MASSSJSU", "total",
         "Over 51.5", 51.5, "Over 51.5"),
        ("cfb_kalshi", "KXNCAAFCONF-26-SEC", "KXNCAAFCONF-26", "future", "SEC", None, "SEC"),
        ("cfb_polymarket", "pm1", "cfb-umass-sjsu-2026-09-12", "total", "O/U 51.5", None,
         "UMass vs. San Jose State: O/U 51.5"),
        ("cfb_polymarket", "pm2", "ncaa-football-2026-national-champion", "future", "x", None,
         "Will X win?"),
    ])
    Q = "INSERT INTO quotes (ts, venue, market_id, best_bid, best_ask, mid) VALUES (?,?,?,?,?,?)"
    p.executemany(Q, [
        (KICK - 3600, "cfb_kalshi", "KXNCAAFTOTAL-26SEP12MASSSJSU-52", 0.40, 0.44, 0.42),
        (KICK - 90, "cfb_kalshi", "KXNCAAFTOTAL-26SEP12MASSSJSU-52", 0.45, 0.47, 0.46),
        (KICK, "cfb_kalshi", "KXNCAAFTOTAL-26SEP12MASSSJSU-52", 0.60, 0.70, 0.65),   # at kickoff
        (KICK + 600, "cfb_kalshi", "KXNCAAFTOTAL-26SEP12MASSSJSU-52", 0.90, 0.95, 0.925),
        (KICK - 30, "cfb_polymarket", "pm1", 0.0, 1.0, 0.5),
        (KICK - 7200, "cfb_kalshi", "KXNCAAFCONF-26-SEC", 0.3, 0.32, 0.31),
        (KICK + 7200, "cfb_kalshi", "KXNCAAFCONF-26-SEC", 0.3, 0.32, 0.31),
    ])
    p.commit()
    yield p
    p.close()


def test_the_normaliser_still_agrees_with_c01():
    from research import cfb_calibration as c01
    assert probe_promote.ALIAS == c01.ALIAS
    for name in ("San José State", "UMass", "Miami (FL)", "Miami (OH)", "Louisiana-Monroe",
                 "Texas A&M", "Anderson (IN)", "NC State"):
        assert probe_promote.norm(name) == c01.norm(name)


def test_markets_join_to_their_game_on_both_venues(store, probe):
    markets, closes, m = probe_promote.extract(probe, store)
    by = {r[1]: r for r in markets}
    assert by["KXNCAAFTOTAL-26SEP12MASSSJSU-52"][13] == 401
    assert by["pm1"][13] == 401
    assert by["KXNCAAFCONF-26-SEC"][13] is None and by["pm2"][13] is None
    assert all(r[15] == probe_promote.CAPTURE for r in markets)


def test_the_close_is_the_last_quote_strictly_before_kickoff(store, probe):
    _markets, closes, m = probe_promote.extract(probe, store)
    c = {r[1]: r for r in closes}
    k = c["KXNCAAFTOTAL-26SEP12MASSSJSU-52"]
    assert (k[4], k[5], k[6], k[7], k[12]) == (KICK - 90, 90, 0.45, 0.47, "two_sided")
    meas = {key: v for key, v, _d in m}
    assert meas["probe.closes_quote_at_kickoff_second"] == 1
    assert meas["probe.kickoff_agreement_with_cfbd"] == 1


def test_an_empty_polymarket_book_is_labelled_not_priced(store, probe):
    _markets, closes, _m = probe_promote.extract(probe, store)
    assert {r[1]: r[12] for r in closes}["pm1"] == "empty_0_1"


def test_futures_get_no_close(store, probe):
    _markets, closes, _m = probe_promote.extract(probe, store)
    assert "KXNCAAFCONF-26-SEC" not in {r[1] for r in closes}


def test_promotion_is_idempotent_and_marks_the_source_external(store, probe, monkeypatch, tmp_path):
    monkeypatch.setattr(probe_promote, "probe_path", lambda: str(tmp_path / "cfb_probe.db"))
    first, _ = ingest_cfb.promote_probe(store, probe)
    again, _ = ingest_cfb.promote_probe(store, probe)
    assert first["probe_markets"]["inserted"] == 6 and first["probe_closes"]["inserted"] == 2
    assert again["probe_markets"] == {"rows": 6, "inserted": 0, "closed": 0, "unchanged": 6}
    rels = [r[0] for r in store.execute("SELECT rel_path FROM cfb_raw_files")]
    assert all(r.startswith("external:") for r in rels if "probe" in r)
    assert ingest_cfb.audit(store).clean


def test_book_states():
    s = probe_promote.book_state
    assert (s(0.4, 0.45), s(None, 0.45), s(0.4, None), s(0.0, 1.0), s(None, None)) == \
        ("two_sided", "ask_only", "bid_only", "empty_0_1", "empty")
