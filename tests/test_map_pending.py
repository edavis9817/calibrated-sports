"""The pending mapping pass (a-68).

What failed was not a mapper. Kalshi lists most of a week's props on Thursday
afternoon, hours after the week's last scheduled mapping run, so a slate sat
with NO `market_outcome` row until the following Tuesday. These tests are on
that state - a listed market nobody has looked at - and on the one thing a
faster mapper must not do, which is resolve a market any differently from the
full pass. Every match is asserted by the claim it lands on, never by a count.
"""
import time
from datetime import datetime, timezone

import pytest

import config
import store
from jobs import map_markets
from venues import oddsapi

EID = "8bd90781e17e6d97df3941063cedae1e"
KICK = datetime(2026, 9, 10, 0, 20, tzinfo=timezone.utc).timestamp()
JSN = "Jaxon Smith-Njigba"
GSIS = "00-0038543"
K7 = "KXNFLREC-26SEP09NESEA-SEAJSMITHNJIGBA1-7"
POLY_EVENT = "nfl-ne-sea-2026-09-10-player-props"


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setattr(config, "STORAGE_DIR", str(tmp_path))
    monkeypatch.setattr(config, "RAW_DIR", str(tmp_path / "raw"))
    store.init_db()
    now = time.time()
    store.replace_rows(
        "nfl_games",
        ("sport", "game_id", "data_version", "season", "week", "gameday",
         "home_team", "away_team", "kickoff_ts", "source", "ingested_ts"),
        [("nfl", "2026_01_NE_SEA", "2026-09-09", 2026, 1, "2026-09-09",
          "SEA", "NE", KICK, "test", now)])
    store.replace_rows(
        "player_xwalk",
        ("gsis_id", "display_name", "position", "last_season", "status", "ingested_ts"),
        [(GSIS, JSN, "WR", 2026, "ACT", now)])
    store.replace_rows(
        "player_alias", ("alias", "gsis_id", "source", "last_season"),
        [("jaxon smith njigba", GSIS, "display", 2026)])
    yield tmp_path


def kalshi_rung(n=7, ticker=None, name=JSN):
    return {"venue": "kalshi", "market_id": ticker or K7.rsplit("-", 1)[0] + f"-{n}",
            "event_id": "KXNFLREC-26SEP09NESEA", "market_type": "prop",
            "title": f"{name}: {n}+ receptions", "subject": f"{name}: {n}+",
            "line": n - 0.5}


def poly_line(line=6.5, market_id="0xjsn65"):
    return {"venue": "polymarket", "market_id": market_id, "event_id": POLY_EVENT,
            "market_type": "prop", "title": f"{JSN}: Receptions O/U {line}"}


def mo(venue, market_id):
    with store.db() as c:
        return c.execute(
            "SELECT outcome_id, method, unmapped_reason, mapped_ts FROM market_outcome "
            "WHERE venue=? AND market_id=?", (venue, market_id)).fetchone()


def claim(outcome_id):
    with store.db() as c:
        return tuple(c.execute(
            "SELECT entity_id, season, week, stat, line, side, event_id FROM outcomes "
            "WHERE outcome_id=?", (outcome_id,)).fetchone())


def n_outcomes():
    with store.db() as c:
        return c.execute("SELECT COUNT(*) FROM outcomes").fetchone()[0]


# --- the state that went unnoticed ------------------------------------------------

def test_a_listed_market_nobody_looked_at_has_no_reason_row():
    """Why the reason census never showed it: there is nothing to group by."""
    store.upsert_markets([kalshi_rung()])
    assert mo("kalshi", K7) is None
    assert [r["market_id"] for r in map_markets.pending_rows()] == [K7]


def test_pending_maps_it_to_the_claim_the_ticker_names():
    store.upsert_markets([kalshi_rung()])
    res = map_markets.run_pending(book_props=False)
    oid = mo("kalshi", K7)[0]
    # shown correct, not counted: the player, the game, the stat, the line, the side
    assert claim(oid) == (GSIS, 2026, 1, "receptions", 6.5, "over", "2026_01_NE_SEA")
    assert res["stats"]["kalshi:pending"] == 1
    assert res["stats"]["kalshi:mapped"] == 1


def test_a_second_pass_with_nothing_new_writes_nothing():
    store.upsert_markets([kalshi_rung()])
    map_markets.run_pending(book_props=False)
    before = mo("kalshi", K7)
    res = map_markets.run_pending(book_props=False)
    assert res["stats"]["kalshi:pending"] == 0
    assert mo("kalshi", K7) == before          # mapped_ts included: no rewrite


def test_the_pending_pass_and_the_full_pass_agree_row_for_row():
    """One rule, two callers. A faster path that matched more would be a
    widened rule under another name."""
    rows = [kalshi_rung(7), kalshi_rung(4),
            kalshi_rung(5, ticker="KXNFLREC-26SEP09NESEA-SEANOBODY1-5", name="No Body"),
            {"venue": "kalshi", "market_id": "KXNFLWINS-SEA-9", "market_type": "future",
             "title": "Seattle wins 9+"}]
    store.upsert_markets(rows)
    map_markets.run_pending(book_props=False)
    pending = {r["market_id"]: mo("kalshi", r["market_id"])[:3] for r in rows}
    map_markets.run("kalshi")
    full = {r["market_id"]: mo("kalshi", r["market_id"])[:3] for r in rows}
    assert pending == full
    # and the set is not vacuous: two resolve, two refuse, each for its own reason
    assert sum(1 for v in full.values() if v[0]) == 2
    assert full["KXNFLREC-26SEP09NESEA-SEANOBODY1-5"][2] == "no player matches 'No Body'"
    assert full["KXNFLWINS-SEA-9"][2].startswith("future:")


def test_an_unresolvable_name_is_recorded_unmapped_never_guessed():
    """'J. Smith' is not Jaxon Smith-Njigba. The pending pass must refuse it
    exactly as the full pass does."""
    row = kalshi_rung(5, ticker="KXNFLREC-26SEP09NESEA-SEAJSMITH1-5", name="J. Smith")
    store.upsert_markets([row])
    res = map_markets.run_pending(book_props=False)
    got = mo("kalshi", row["market_id"])
    assert got[0] is None and got[2] == "no player matches 'J. Smith'"
    assert res["stats"]["kalshi:unmapped"] == 1
    assert n_outcomes() == 0


def test_a_refusal_that_is_a_property_of_the_market_is_not_read_again():
    row = kalshi_rung(5, ticker="KXNFLREC-26SEP09NESEA-SEAJSMITH1-5", name="J. Smith")
    store.upsert_markets([row])
    map_markets.run_pending(book_props=False)
    assert map_markets.pending_rows() == []


# --- link-only lines wait for the exchange rung, then link --------------------------

def test_a_polymarket_line_listed_before_the_kalshi_rung_links_once_it_exists():
    """The order Thursday actually happens in. The line is recorded as a wait,
    creates nothing, and the next pass after Kalshi lists the rung links it to
    the SAME outcome."""
    store.upsert_markets([poly_line()])
    map_markets.run_pending(book_props=False)
    waiting = mo("polymarket", "0xjsn65")
    assert waiting[0] is None and waiting[2].startswith("polymarket-only claim ")
    assert n_outcomes() == 0                   # link only: nothing created

    store.upsert_markets([kalshi_rung(7)])
    res = map_markets.run_pending(book_props=False)
    k = mo("kalshi", K7)[0]
    assert mo("polymarket", "0xjsn65")[0] == k
    assert claim(k) == (GSIS, 2026, 1, "receptions", 6.5, "over", "2026_01_NE_SEA")
    assert res["stats"]["polymarket:mapped"] == 1
    assert n_outcomes() == 1


def test_a_polymarket_line_at_another_number_stays_unlinked():
    """6.5 and 5.5 are different claims. A retry must not turn a wait into a
    link to the nearest rung."""
    store.upsert_markets([poly_line(5.5, "0xjsn55"), kalshi_rung(7)])
    map_markets.run_pending(book_props=False)
    map_markets.run_pending(book_props=False)
    got = mo("polymarket", "0xjsn55")
    assert got[0] is None and got[2].startswith("polymarket-only claim ")
    assert n_outcomes() == 1


def test_a_wait_the_venue_no_longer_lists_is_not_retried():
    store.upsert_markets([poly_line()])
    map_markets.run_pending(book_props=False)
    assert len(map_markets.pending_rows()) == 1            # listed: retried
    before = mo("polymarket", "0xjsn65")
    res = map_markets.run_pending(book_props=False)
    assert res["stats"]["polymarket:waiting"] == 1
    assert mo("polymarket", "0xjsn65") == before           # and not rewritten
    later = time.time() + map_markets.RETRY_LISTED_WITHIN + 60
    assert map_markets.pending_rows(now=later) == []       # delisted: left alone


# --- the books ---------------------------------------------------------------------

def _quote(book, mkey, side, line, ts):
    return {"ts": ts, "sport": "nfl", "venue": f"oddsapi:{book}", "event_id": EID,
            "market_id": f"{EID}|{mkey}|{JSN}|{side}", "market_type": "prop",
            "subject": JSN, "line": line, "side": side, "mid": 0.52, "last": -110,
            "raw_ref": "oddsapi/prop"}


def _book_slate():
    store.upsert_markets([{
        "venue": "oddsapi", "market_id": f"game:{EID}", "event_id": EID,
        "market_type": "game", "title": "New England Patriots @ Seattle Seahawks",
        "close_ts": KICK}])
    t0 = KICK - 10 * 3600
    store.write_quotes([_quote("draftkings", "player_receptions", "Over", 6.5, t0),
                        _quote("draftkings", "player_receptions", "Over", 5.5, t0),
                        _quote("fanduel", "player_reception_yds", "Over", 65.5, t0)],
                       dedupe=False)


def test_a_book_line_links_when_its_rung_is_mapped_and_only_that_line_changes():
    """The Board's dependency. The book quoted before Kalshi listed, so the
    line is book-only; once the rung is mapped the same pass links it, and the
    lines whose claim still has no outcome are not touched."""
    _book_slate()
    now = KICK - 9 * 3600
    q65 = oddsapi.prop_market_id(f"{EID}|player_receptions|{JSN}|Over", 6.5)
    q55 = oddsapi.prop_market_id(f"{EID}|player_receptions|{JSN}|Over", 5.5)
    first = map_markets.run_pending(now=now)
    assert first["stats"]["oddsapi_props:changed"] == 3
    assert first["stats"]["oddsapi_props:newly_linked"] == 0
    assert mo("oddsapi:draftkings", q65)[2].startswith(oddsapi.BookOnly.PREFIX)
    untouched = mo("oddsapi:draftkings", q55)

    store.upsert_markets([kalshi_rung(7)])
    second = map_markets.run_pending(now=now)
    assert mo("oddsapi:draftkings", q65)[0] == mo("kalshi", K7)[0]
    assert second["stats"]["oddsapi_props:changed"] == 1
    assert second["stats"]["oddsapi_props:newly_linked"] == 1
    assert mo("oddsapi:draftkings", q55) == untouched      # 5.5 is not 6.5
    assert n_outcomes() == 1                               # the books created none

    third = map_markets.run_pending(now=now)
    assert third["stats"]["oddsapi_props:changed"] == 0


def test_a_game_outside_the_slate_window_is_left_to_the_weekly_pass():
    _book_slate()
    res = map_markets.run_pending(now=KICK + map_markets.BOOK_BEHIND + 3600)
    assert res["stats"]["oddsapi_props:events"] == 0
    assert res["stats"]["oddsapi_props:changed"] == 0


def test_the_pass_reports_its_own_health_row():
    store.upsert_markets([kalshi_rung()])
    map_markets.run_pending(book_props=False, now=1791000000.0)
    with store.db() as c:
        ok, detail, mark = c.execute(
            "SELECT ok, detail, watermark FROM source_health "
            "WHERE source='mapping_pending'").fetchone()
    assert ok == 1 and "kalshi:mapped=1" in detail and mark == 1791000000.0


# --- the logger runs it, and survives it ---------------------------------------------

def test_the_logger_pass_maps_a_newly_listed_market():
    import asyncio

    import run_logger
    store.upsert_markets([kalshi_rung()])
    stats = asyncio.run(run_logger.map_pending_once())
    assert stats["kalshi:mapped"] == 1
    assert claim(mo("kalshi", K7)[0])[:5] == (GSIS, 2026, 1, "receptions", 6.5)


def test_a_mapping_failure_costs_one_pass_and_is_recorded_as_failing():
    import asyncio

    import run_logger

    def boom():
        raise RuntimeError("mapper fell over")

    assert asyncio.run(run_logger.map_pending_once(boom)) is None
    with store.db() as c:
        ok, detail = c.execute("SELECT ok, detail FROM source_health "
                               "WHERE source='mapping_pending'").fetchone()
    assert ok == 0 and "mapper fell over" in detail


def test_the_worker_is_started_by_main_and_can_be_switched_off(monkeypatch):
    """On the call site, not on the definition: a worker nobody gathers is a
    worker that never runs, and nothing else would say so."""
    import ast
    import asyncio
    import os

    import run_logger
    src = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "run_logger.py"), encoding="utf-8").read()
    main = next(n for n in ast.walk(ast.parse(src))
                if isinstance(n, ast.AsyncFunctionDef) and n.name == "main")
    gathered = [a.func.id for n in ast.walk(main)
                if isinstance(n, ast.Call) and getattr(n.func, "attr", "") == "gather"
                for a in n.args if isinstance(a, ast.Call) and isinstance(a.func, ast.Name)]
    assert "mapping_worker" in gathered and "depth_worker" in gathered

    monkeypatch.setattr(config, "MAP_PENDING_EVERY", 0)
    called = []
    monkeypatch.setattr(run_logger, "map_pending_once",
                        lambda *a, **k: called.append(1))
    asyncio.run(asyncio.wait_for(run_logger.mapping_worker(), timeout=5))
    assert called == []
