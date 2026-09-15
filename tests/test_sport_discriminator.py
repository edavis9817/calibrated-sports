"""Invariant 7: every entity carries a sport discriminator. The identity tables
did not, until site-architecture §1.4 said to verify rather than assume."""
import sqlite3

import config
import store


def test_identity_tables_carry_sport_on_a_fresh_db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setattr(config, "RAW_DIR", str(tmp_path / "raw"))
    store.init_db()
    con = sqlite3.connect(config.DB_PATH)
    for table in ("player_xwalk", "player_alias"):
        cols = {r[1]: r for r in con.execute(f"PRAGMA table_info({table})")}
        assert "sport" in cols, table
        assert cols["sport"][3] == 1, f"{table}.sport must be NOT NULL"


def test_migration_adds_sport_to_an_existing_db_and_defaults_rows_to_nfl(tmp_path, monkeypatch):
    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    # the pre-migration shapes, with a row in each
    # full pre-migration column list: init_db's schema indexes pfr_id, so a
    # two-column stub would fail before the migration ever ran
    old.execute("CREATE TABLE player_xwalk (gsis_id TEXT PRIMARY KEY, display_name TEXT, "
                "first_name TEXT, last_name TEXT, position TEXT, last_team TEXT, "
                "last_season INTEGER, status TEXT, pfr_id TEXT, espn_id TEXT, "
                "sleeper_id TEXT, yahoo_id TEXT, pff_id TEXT, ingested_ts REAL)")
    old.execute("CREATE TABLE player_alias (alias TEXT NOT NULL, gsis_id TEXT NOT NULL, "
                "source TEXT NOT NULL, last_season INTEGER, PRIMARY KEY (alias, gsis_id))")
    old.execute("INSERT INTO player_xwalk (gsis_id, display_name) VALUES ('00-0036223', 'Jonathan Taylor')")
    old.execute("INSERT INTO player_alias VALUES ('j taylor', '00-0036223', 'short', 2025)")
    old.commit()
    old.close()
    monkeypatch.setattr(config, "DB_PATH", str(path))
    monkeypatch.setattr(config, "RAW_DIR", str(tmp_path / "raw"))
    store.init_db()
    con = sqlite3.connect(path)
    assert con.execute("SELECT sport FROM player_xwalk").fetchone() == ("nfl",)
    assert con.execute("SELECT sport FROM player_alias").fetchone() == ("nfl",)
