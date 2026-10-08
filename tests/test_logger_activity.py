"""a-74: what the logger did. Every test builds its own store under tmp_path and
passes it by argument - nothing here resolves the configured store."""
import ast
import inspect
import json
import os
import sqlite3
from datetime import datetime, timezone

import pytest

from jobs import board_read
from jobs import export_web as E
from jobs import logger_activity as L
from jobs import source_registry as R


def ts(s):
    return datetime.strptime(s, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc).timestamp()


SCHEMA = """
CREATE TABLE poll_log (ts REAL, venue TEXT, endpoint TEXT, n_markets INTEGER, n_quotes INTEGER,
                       ok INTEGER, error TEXT, elapsed_s REAL);
CREATE TABLE source_health (source TEXT PRIMARY KEY, ok INTEGER NOT NULL, detail TEXT,
                            watermark REAL, last_ok_ts REAL, last_fail_ts REAL, updated_ts REAL);
CREATE TABLE market_depth (ts REAL, venue TEXT, market_id TEXT, outcome_id TEXT, side TEXT,
                           PRIMARY KEY (ts, venue, market_id, side));
CREATE TABLE raw_shards (rel_path TEXT PRIMARY KEY, venue TEXT, day TEXT, hour TEXT, state TEXT);
CREATE TABLE markets (venue TEXT, market_id TEXT, market_type TEXT, first_seen REAL,
                      last_seen REAL, close_ts REAL, PRIMARY KEY (venue, market_id));
CREATE TABLE market_outcome (venue TEXT, market_id TEXT, outcome_id TEXT,
                             PRIMARY KEY (venue, market_id));
CREATE TABLE outcomes (outcome_id TEXT PRIMARY KEY, created_ts REAL, event_id TEXT);
CREATE TABLE quotes (id INTEGER PRIMARY KEY, ts REAL, venue TEXT, market_id TEXT);
CREATE INDEX ix_quotes_market_ts ON quotes(venue, market_id, ts);
CREATE TABLE nfl_games (sport TEXT, game_id TEXT, data_version TEXT, season INTEGER,
                        week INTEGER, kickoff_ts REAL);
"""

# 2026 week 5: Tuesday 2026-10-06 12:00Z opens it; Thursday night's game kicks 00:15Z Friday.
OPEN = ts("2026-10-06 12:00:00")
KICK = ts("2026-10-09 00:15:00")
AS_OF = ts("2026-10-08 12:00:00")
OUTAGE = (ts("2026-10-07 03:24:06"), ts("2026-10-07 04:47:55"))      # 83.8 min


def polls(lo, hi, step, venue="kalshi", endpoint="quotes:futures", skip=None):
    out, t = [], lo
    while t <= hi:
        if not (skip and skip[0] < t < skip[1]):
            out.append((t, venue, endpoint, 1, 1, 1, None, 0.1))
        t += step
    return out


@pytest.fixture
def store(tmp_path):
    path = str(tmp_path / "store.db")
    con = sqlite3.connect(path)
    con.executescript(SCHEMA)
    rows = polls(OPEN, AS_OF - 60, 120, skip=None)
    rows = [r for r in rows if not OUTAGE[0] < r[0] < OUTAGE[1]]
    rows += [(OUTAGE[0], "kalshi", "quotes:futures", 1, 1, 1, None, 0.1),
             (OUTAGE[1], "kalshi", "quotes:futures", 1, 1, 1, None, 0.1)]
    rows += polls(OPEN, AS_OF - 60, 600, venue="polymarket", endpoint="discovery", skip=OUTAGE)
    rows.append((OPEN + 50, "oddsapi", "snapshot", 1, 1, 0, "getaddrinfo failed", 0.1))
    con.executemany("INSERT INTO poll_log VALUES (?,?,?,?,?,?,?,?)", rows)
    con.execute("INSERT INTO nfl_games VALUES ('nfl','2026_05_A_B','v1',2026,5,?)", (KICK,))
    mk = [("kalshi", "KXNFLREC-26OCT08AB-EARLY", "prop", OPEN + 100, AS_OF, KICK + 4 * 3600),
          ("kalshi", "KXNFLREC-26OCT08AB-LATE", "prop", OPEN + 100, AS_OF, KICK + 4 * 3600),
          ("kalshi", "KXNFLREC-26OCT08AB-NOROW", "prop", OPEN + 100, AS_OF, KICK + 4 * 3600),
          ("kalshi", "KXNFLGAME-26OCT08AB-A", "moneyline", OPEN + 100, AS_OF, KICK)]
    con.executemany("INSERT INTO markets VALUES (?,?,?,?,?,?)", mk)
    con.executemany("INSERT INTO market_outcome VALUES ('kalshi',?,?)",
                    [("KXNFLREC-26OCT08AB-EARLY", "o-early"), ("KXNFLREC-26OCT08AB-LATE", "o-late")])
    con.executemany("INSERT INTO outcomes VALUES (?,?,'2026_05_A_B')",
                    [("o-early", KICK - 3600), ("o-late", KICK + 3600)])
    con.execute("INSERT INTO quotes (ts, venue, market_id) VALUES (?,?,?)",
                (OPEN + 500, "kalshi", "KXNFLREC-26OCT08AB-EARLY"))
    con.execute("INSERT INTO market_depth VALUES (?,?,?,?,?)",
                (OPEN + 500, "kalshi", "KXNFLREC-26OCT08AB-EARLY", None, "buy_yes"))
    con.execute("INSERT INTO raw_shards VALUES ('kalshi_depth/2026-10-06/12.jsonl.gz',"
                "'kalshi_depth','2026-10-06','12','local')")
    health = [("liveness", 1, "last poll 0.1 min ago", None, AS_OF, OUTAGE[1], AS_OF),
              ("rotate_raw", 0, r"failed on C:\Users\someone\data\raw\x.gz", None, OPEN, AS_OF, AS_OF),
              ("depth_capture", 1, "kalshi_rows=288, poly_rows=0, raw_books_kept=0, "
               "allowlist_week=2026-wk5, elapsed=10.4", None, AS_OF, None, AS_OF),
              ("logger_start", 1, "build abc pid 1", None, AS_OF, None, AS_OF)]
    con.executemany("INSERT INTO source_health VALUES (?,?,?,?,?,?,?)", health)
    con.commit()
    con.close()
    return path


@pytest.fixture
def wlog(tmp_path):
    p = tmp_path / "logger_watchdog.log"
    p.write_text("\ufeff2026-10-07T04:47:51Z DOWN logger not running (log last written 4919s ago, "
                 "last holder pid 60200); starting from C:\\Users\\someone\\prod\n"
                 "2026-10-07T04:48:06Z STARTED pid 28760\n", encoding="utf-8")
    return str(p)


@pytest.fixture
def obj(store, wlog):
    return L.build(db=store, now=AS_OF, log_path=wlog)


# ------------------------------------------------------------------ uptime

def test_the_outage_appears_with_its_start_end_and_length(obj):
    gaps = obj["uptime"]["gaps"]
    assert len(gaps) == 1
    g = gaps[0]
    assert (g["start"], g["end"], g["minutes"]) == ("2026-10-07T03:24:06Z", "2026-10-07T04:47:55Z", 83.8)
    assert not g["open"] and not g["cadence_floor"]
    assert obj["uptime"]["total"]["down_min"] == 83.8
    assert obj["uptime"]["total"]["longest_gap_min"] == 83.8
    day = {d["day"]: d for d in obj["uptime"]["days"]}
    assert day["2026-10-07"]["down_min"] == 83.8 and day["2026-10-06"]["down_min"] == 0.0
    assert day["2026-10-07"]["up_min"] == pytest.approx(1440 - 83.8, abs=0.11)
    (wk,) = obj["uptime"]["weeks"]
    assert wk["week"] == "2026-wk05" and wk["gaps"] == 1 and wk["complete"] is False


def test_uptime_is_gaps_not_liveness_and_can_read_clean():
    """The same function returns the other answer on a record with no gap."""
    even = [float(t) for t in range(0, 7200, 60)]
    assert L.find_gaps(even, 7200) == []
    assert L.window_uptime([], 0, 7200)["up_share"] == 1.0
    holed = even[:30] + even[60:]
    (g,) = L.find_gaps(holed, 7200)
    assert g["end"] - g["start"] == 31 * 60 and not g["open"]


def test_a_logger_that_is_down_now_shows_an_open_gap():
    (g,) = L.find_gaps([0.0, 60.0], 60.0 + 1000)
    assert g["open"] and g["start"] == 60.0
    assert L.find_gaps([0.0, 60.0], 60.0 + L.GAP_S) == []      # exactly the threshold is not over it


def test_a_cadence_floor_gap_is_flagged_and_still_counted_as_down(store, wlog):
    con = sqlite3.connect(store)
    # kalshi polls every 120 s: drop two and put one back 315 s after the last
    con.execute("DELETE FROM poll_log WHERE venue='kalshi' AND ts > ? AND ts < ?",
                (OPEN + 3000, OPEN + 3360))
    con.execute("INSERT INTO poll_log VALUES (?,'kalshi','quotes:futures',1,1,1,NULL,0.1)",
                (OPEN + 3315,))
    con.commit()
    con.close()
    o = L.build(db=store, now=AS_OF, log_path=wlog)
    floor = [g for g in o["uptime"]["gaps"] if g["cadence_floor"]]
    assert len(floor) == 1 and len(o["uptime"]["gaps"]) == 2
    assert o["uptime"]["total"]["down_min"] == pytest.approx(83.8 + floor[0]["minutes"], abs=0.11)


# ------------------------------------------------------------------ cadence

def test_cadence_by_venue_and_tier_and_a_silent_venue_is_named(obj):
    (wk,) = obj["cadence"]["weeks"]
    rows = {(r["venue"], r["endpoint"]): r for r in wk["rows"]}
    k = rows[("kalshi", "quotes:futures")]
    assert k["median_s"] == 120.0 and k["max_s"] == pytest.approx(83.8 * 60, abs=1)
    assert rows[("polymarket", "discovery")]["median_s"] == 600.0
    silent = rows[("oddsapi", "snapshot")]
    assert silent["polls_ok"] == 0 and silent["polls_failed"] == 1 and silent["median_s"] is None
    assert wk["venues_without_success"] == ["oddsapi"]
    assert "oddsapi snapshot" in wk["silent"]
    tiers = {(t["venue"], t["endpoint"]): t for t in obj["cadence"]["tiers"]}
    assert tiers[("oddsapi", "snapshot")]["last_ok_at"] is None
    assert tiers[("kalshi", "quotes:futures")]["configured_s"] == 300


# ------------------------------------------------------------------ coverage

def test_props_mapped_before_kickoff_is_not_props_mapped(obj):
    (wk,) = obj["coverage"]["weeks"]
    k = next(v for v in wk["venues"] if v["venue"] == "kalshi")
    assert k["markets_polled"] == 4 and k["markets_quoted"] == 1 and k["markets_mapped"] == 2
    assert k["props_listed"] == 3 and k["props_with_row"] == 2 and k["props_mapped"] == 2
    assert k["props_mapped_before_kickoff"] == 1          # o-late was created after kickoff
    assert k["props_before_kickoff_share"] == pytest.approx(1 / 3, abs=1e-4)
    assert k["priority_props"] == {"listed": 3, "mapped": 2, "mapped_before_kickoff": 1}
    p = next(v for v in wk["venues"] if v["venue"] == "polymarket")
    assert p["markets_polled"] == 0 and p["priority_props"] is None
    assert [n["venue"] for n in obj["coverage"]["not_measured"]] == ["oddsapi"]


# ------------------------------------------------------------------ depth, health

def test_depth_counters_and_the_days_with_nothing(obj):
    assert obj["depth"]["latest_cycle"]["counters"] == {"kalshi_rows": 288, "poly_rows": 0,
                                                        "raw_books_kept": 0}
    (wk,) = obj["depth"]["weeks"]
    k, p = wk["venues"]
    assert k["depth_rows"] == 1 and k["raw_book_shards"] == 1
    assert "2026-10-07" in k["days_without_depth_rows"]
    assert p["depth_rows"] == 0 and p["days_without_raw_books"]
    last = {x["venue"]: x for x in obj["depth"]["last_seen"]}
    assert last["kalshi"]["last_depth_row_day"] == "2026-10-06"
    assert last["polymarket"] == {"venue": "polymarket", "last_depth_row_day": None,
                                  "last_raw_book_day": None}


def test_failing_sources_are_named_and_no_local_path_is_published(obj):
    assert obj["health"]["failing"] == ["rotate_raw"]
    assert "someone" not in json.dumps(obj)
    assert L.scrub(r"failed on C:\Users\x\y.gz then /c/Users/x/z") == "failed on <path> then <path>"


# ------------------------------------------------------------------ watchdog

def test_the_watchdog_record(obj):
    r = obj["watchdog"]["restart"]
    assert r["log_readable"] and r["fires"] == 1 and r["log_lines_unread"] == 0
    (e,) = r["events"]
    assert e["fired_at"] == "2026-10-07T04:47:51Z" and e["outcome"] == "started"
    assert e["restart_s"] == 15.0 and e["log_idle_s_at_fire"] == 4919
    assert e["outage_min"] == 83.8 and e["first_poll_after_s"] == 4.0
    assert r["fires_recorded_in_source_health"] == 0 and r["source_health_rows"] == []
    assert (r["gaps_over_floor"], r["gaps_over_floor_with_a_fire"]) == (1, 1)
    assert obj["watchdog"]["deadman"]["fires_countable"] is False
    assert obj["watchdog"]["deadman"]["last_fail_at"] == "2026-10-07T04:47:55Z"


def test_a_missing_watchdog_log_is_said_not_read_as_zero_fires(store, tmp_path):
    o = L.build(db=store, now=AS_OF, log_path=str(tmp_path / "absent.log"))
    assert o["watchdog"]["restart"]["log_readable"] is False
    assert o["watchdog"]["restart"]["gaps_over_floor_with_a_fire"] == 0


def test_an_unreadable_watchdog_line_is_counted():
    ev, unread = L.parse_watchdog_log("2026-10-07T04:47:51Z DOWN x\ngarbage\n"
                                      "2026-10-07T04:48:06Z FAILED venv missing\n")
    assert [w for _t, w, _r in ev] == ["DOWN", "FAILED"] and unread == 1


# ------------------------------------------------------------------ contract, write

def test_the_file_matches_the_contract_and_a_stray_field_is_refused(obj):
    E.validate_contract({L.key_for(): obj})
    assert E.kind_for_key("board/nfl/logger.json")[0] == L.KIND
    bad = dict(obj, uptime_is_fine=True)
    with pytest.raises(E.ContractError):
        E.validate_contract({L.key_for(): bad})


def test_the_definition_of_uptime_is_in_the_file(obj):
    assert "successful-poll gaps, not process liveness" in obj["definitions"]["uptime"]
    assert obj["thresholds"]["gap_s"] == 300


def test_the_kind_declares_its_sources():
    assert R.DECLARED["nfl"][L.KIND]
    assert "jobs.logger_activity" in R.NOT_FOLLOWED


def test_it_writes_one_key_and_deletes_nothing(obj, tmp_path):
    dest = str(tmp_path / "tree")
    other = E.local_path(dest, "board/nfl/2026/wk05/index.json")
    os.makedirs(os.path.dirname(other))
    with open(other, "w") as f:
        f.write("{}")
    assert L.write(dest, obj) == (1, 0)
    assert os.path.exists(other)
    assert L.write(dest, obj) == (0, 0)


def test_the_store_is_opened_read_only(store):
    con = L.ro(store)
    with pytest.raises(sqlite3.OperationalError):
        con.execute("DELETE FROM poll_log")
    con.close()
    src = inspect.getsource(L)
    assert "mode=ro" in src
    calls = [n for n in ast.walk(ast.parse(src)) if isinstance(n, ast.Call)
             and isinstance(n.func, ast.Attribute) and n.func.attr == "connect"]
    assert len(calls) == 1          # one way in, and it is ro()


# ------------------------------------------------------------------ the step cannot fail the run

def test_the_report_step_writes_then_is_not_due(store, wlog, tmp_path):
    dest, lines = str(tmp_path / "tree"), []
    msg = L.report_step(dest, db=store, log=lines.append, now=AS_OF, log_path=wlog)
    assert "written=1" in msg and lines == [msg]
    assert os.path.exists(E.local_path(dest, L.key_for()))
    assert L.report_step(dest, db=store, log=lines.append, now=AS_OF + 600,
                         log_path=wlog) == "logger activity: not due"
    again = L.report_step(dest, db=store, log=lines.append, now=AS_OF + 3600, log_path=wlog)
    assert "written=1" in again


@pytest.mark.parametrize("boom", [RuntimeError("x"), SystemExit("refused"),
                                  sqlite3.OperationalError("database is locked")])
def test_the_report_step_never_raises(monkeypatch, tmp_path, boom):
    def build(**_k):
        raise boom
    monkeypatch.setattr(L, "build", build)
    lines = []
    msg = L.report_step(str(tmp_path / "tree"), db="nope.db", log=lines.append, now=AS_OF)
    assert msg.startswith("logger activity NOT WRITTEN") and lines == [msg]
    assert not os.path.exists(str(tmp_path / "tree"))


def test_a_missing_store_does_not_raise_either(tmp_path):
    msg = L.report_step(str(tmp_path / "tree"), db=str(tmp_path / "absent.db"),
                        log=lambda _m: None, now=AS_OF)
    assert msg.startswith("logger activity NOT WRITTEN")
    assert not os.path.exists(str(tmp_path / "absent.db"))      # mode=ro creates no store


def test_the_board_tick_calls_the_step_before_the_upload_and_unguarded_by_a_raise():
    """By AST, on the caller: the step is called in _tick, its result is carried
    in the tick's summary, and it sits before the upload."""
    fn = next(n for n in ast.walk(ast.parse(inspect.getsource(board_read)))
              if isinstance(n, ast.FunctionDef) and n.name == "_tick")
    calls = [n for n in ast.walk(fn) if isinstance(n, ast.Call)
             and isinstance(n.func, ast.Attribute) and n.func.attr == "report_step"]
    assert len(calls) == 1
    uploads = [n.lineno for n in ast.walk(fn) if isinstance(n, ast.Call)
               and isinstance(n.func, ast.Attribute) and n.func.attr == "upload"]
    assert uploads and calls[0].lineno < min(uploads)
    assign = next(n for n in ast.walk(fn) if isinstance(n, ast.Assign) and n.value is calls[0])
    assert assign.targets[0].slice.value == "logger_activity"
