"""The logger outlives a locked database (unit a-67). Run: pytest -q

On 2026-10-04 and again on 2026-10-06 the logger died on the same path. The
hourly prune ran one DELETE in one transaction and held the write lock for
over a minute; a poll's `store.log_poll` waited out the 30 s busy timeout and
raised "database is locked"; the SAME call inside poll_venue's own except
block raised again, and that second exception left the process.

Four things are pinned, one per link of that chain, and each is shown giving
the OTHER answer under the old arrangement - a test that could not fail under
the code that crashed proves nothing about the code that replaced it.

  1. a bookkeeping write that fails costs a log row, never the process;
  2. nothing in run_logger.py calls those writes unguarded (by AST, on the
     call sites, not on the helper's existence);
  3. the prune deletes exactly the rows the single statement deleted, in
     committed batches a concurrent writer can get in between;
  4. the raw-book allowlist follows the current week, not week 1.
"""
import ast
import asyncio
import os
import sqlite3
import threading
import time

import pytest

import config
import run_logger
import store
from core import single_instance
from jobs import capture_depth, prune_quotes

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture
def env(tmp_path, monkeypatch):
    """An isolated store. The root is pinned as well as the leaf: STORAGE_DIR
    is computed once at import, so DB_PATH alone leaves locks in the real one."""
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setattr(config, "STORAGE_DIR", str(tmp_path))
    monkeypatch.setattr(config, "RAW_DIR", str(tmp_path / "raw"))
    store.init_db()
    store.reset_disk_cache()
    store.reset_quote_state()
    yield tmp_path
    store.reset_disk_cache()
    store.reset_quote_state()


def locked(*_a, **_k):
    raise sqlite3.OperationalError("database is locked")


# --- 1. a failed bookkeeping write is not fatal -------------------------------

class OneMarketClient:
    name = "kalshi"

    async def fetch_quotes(self, subset):
        return []


def _poll_once():
    market = {"venue": "kalshi", "market_id": "M", "market_type": "future"}
    asyncio.run(run_logger.poll_venue(OneMarketClient(), [market], "futures"))


def test_a_locked_poll_log_does_not_leave_poll_venue(env, monkeypatch):
    lines = []
    monkeypatch.setattr(store, "log_poll", locked)
    monkeypatch.setattr(run_logger, "log", lines.append)
    _poll_once()                                  # the crash was HERE
    assert any("poll_log NOT WRITTEN" in x and "database is locked" in x
               for x in lines), lines


def test_the_old_wiring_dies_on_the_same_input(env, monkeypatch):
    """The discriminating half. Put the unguarded call back and the identical
    poll raises - out of the success path AND out of the except block."""
    monkeypatch.setattr(store, "log_poll", locked)
    monkeypatch.setattr(run_logger, "_log_poll", store.log_poll)
    monkeypatch.setattr(run_logger, "log", lambda _m: None)
    with pytest.raises(sqlite3.OperationalError) as e:
        _poll_once()
    assert e.value.__context__ is not None        # raised while handling one


def test_a_locked_write_quotes_is_a_failed_poll_not_a_dead_venue(env, monkeypatch):
    lines = []
    monkeypatch.setattr(store, "write_quotes", locked)
    monkeypatch.setattr(store, "log_poll", locked)
    monkeypatch.setattr(run_logger, "log", lines.append)
    _poll_once()
    assert any("ERROR OperationalError" in x for x in lines), lines


def test_the_helpers_report_whether_the_row_landed(env, monkeypatch):
    monkeypatch.setattr(run_logger, "log", lambda _m: None)
    assert run_logger._log_poll("kalshi", "quotes:hot", 1, 1, True, None, 0.1) is True
    assert run_logger._record_health("a67_test", True, "ok") is True
    monkeypatch.setattr(store, "log_poll", locked)
    monkeypatch.setattr(store, "record_health", locked)
    assert run_logger._log_poll("kalshi", "quotes:hot", 1, 1, True, None, 0.1) is False
    assert run_logger._record_health("a67_test", True, "ok") is False
    with store.db() as c:
        assert c.execute("SELECT COUNT(*) FROM poll_log").fetchone()[0] == 1


# --- 2. no unguarded call site, by AST ----------------------------------------

GUARDED = {"log_poll": "_log_poll", "record_health": "_record_health"}


def unguarded_calls(source: str):
    """(name, lineno) for every `store.log_poll(...)` / `store.record_health(...)`
    CALL. The helpers pass the function as a value, never call it, so they do
    not appear - and a docstring quoting the old name is not a Call node."""
    out = []
    for node in ast.walk(ast.parse(source)):
        f = getattr(node, "func", None)
        if (isinstance(node, ast.Call) and isinstance(f, ast.Attribute)
                and f.attr in GUARDED and isinstance(f.value, ast.Name)
                and f.value.id == "store"):
            out.append((f.attr, node.lineno))
    return out


def test_run_logger_has_no_unguarded_bookkeeping_call():
    with open(os.path.join(ROOT, "run_logger.py"), encoding="utf-8") as f:
        src = f.read()
    assert unguarded_calls(src) == []
    # and the guard is not blind: the guarded names ARE called, many times
    names = [n.func.id for n in ast.walk(ast.parse(src))
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)]
    assert names.count("_log_poll") >= 8
    assert names.count("_record_health") >= 10


def test_the_ast_guard_fires_on_a_planted_call():
    planted = "import store\ntry:\n    pass\nexcept Exception as e:\n    store.log_poll(1)\n"
    assert unguarded_calls(planted) == [("log_poll", 5)]
    assert unguarded_calls('"""store.log_poll(x) is quoted here"""\n') == []


# --- 3. the prune, batched ----------------------------------------------------

DAY = 86400.0


def seed(n_old=1200, n_new=40):
    """n_old prunable live rows, plus one of every kind that must survive."""
    now = time.time()
    old, new = now - 30 * DAY, now - 1 * DAY
    rows = [(old, "kalshi", f"OLD{i % 7}", "live", old) for i in range(n_old)]
    rows += [(new, "kalshi", "NEW", "live", new) for _ in range(n_new)]
    rows += [(old, "kalshi", "CANDLE", "backfill:kalshi_candles", now)]   # rule 1
    rows += [(old, "kalshi", "REPARSED", "live", now)]                    # rule 2
    rows += [(old, "kalshi", "HELD", "live", old)]                        # the hold
    rows += [(old, "kalshi", "NOINGEST", "live", None)]                   # pre-column
    with store.db() as c:
        c.executemany("INSERT INTO quotes (ts, venue, market_id, source, ingest_ts) "
                      "VALUES (?,?,?,?,?)", rows)
    store.hold_quotes([("kalshi", "HELD")], now + 7 * DAY, "a-67 test")
    return n_old + 1                              # the NOINGEST row prunes too


def survivors():
    with store.db() as c:
        return sorted(c.execute(
            "SELECT market_id, COUNT(*) FROM quotes GROUP BY 1").fetchall())


def test_batches_delete_exactly_what_the_rule_says(env):
    expected = seed()
    s = prune_quotes.run(batch=500, pause_s=0)
    assert s["candidates"] == expected and s["deleted"] == expected
    assert s["batches"] == 3 and s["unfinished"] == 0
    assert survivors() == [("CANDLE", 1), ("HELD", 1), ("NEW", 40), ("REPARSED", 1)]
    assert s["remaining"] == 43


def test_one_batch_and_many_batches_leave_the_same_table(env, tmp_path, monkeypatch):
    seed()
    prune_quotes.run(batch=10_000, pause_s=0)
    whole = survivors()
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "u.db"))
    store.init_db()
    seed()
    s = prune_quotes.run(batch=7, pause_s=0)
    assert s["batches"] == -(-1201 // 7)
    assert survivors() == whole


def test_the_id_list_is_a_hint_and_the_rule_is_reapplied(env, monkeypatch):
    """A stale list must not delete a protected row. Hand the delete EVERY id in
    the table and only the prunable ones may go."""
    expected = seed()

    def everything(c, *_a):
        from array import array
        return array("q", [r[0] for r in c.execute("SELECT id FROM quotes")])

    monkeypatch.setattr(prune_quotes, "_candidate_ids", everything)
    s = prune_quotes.run(batch=200, pause_s=0)
    assert s["deleted"] == expected
    assert survivors() == [("CANDLE", 1), ("HELD", 1), ("NEW", 40), ("REPARSED", 1)]


def test_a_pass_that_runs_out_of_time_says_so_and_the_next_finishes(env):
    expected = seed()
    s = prune_quotes.run(batch=100, pause_s=0.02, max_seconds=0.05)
    assert 0 < s["deleted"] < expected
    assert s["unfinished"] == expected - s["deleted"]
    s2 = prune_quotes.run(batch=100, pause_s=0)
    assert s["deleted"] + s2["deleted"] == expected and s2["unfinished"] == 0


def test_dry_run_deletes_nothing(env):
    expected = seed()
    s = prune_quotes.run(dry_run=True)
    assert s["candidates"] == expected and s["deleted"] == 0 and s["batches"] == 0
    assert dict(survivors())["OLD0"] > 0


def test_a_writer_with_a_short_timeout_gets_in_between_batches(env):
    """The property that matters. A second connection that will wait only half
    a second writes all the way through a prune - it never could through one
    DELETE that kept the lock for the whole pass."""
    seed(n_old=6000)
    done, landed, errors = threading.Event(), [], []

    def writer():
        con = sqlite3.connect(config.DB_PATH, timeout=0.5)
        try:
            while not done.is_set():
                try:
                    con.execute("INSERT INTO poll_log (ts,venue,endpoint,n_markets,"
                                "n_quotes,ok,error,elapsed_s) VALUES (?,?,?,?,?,?,?,?)",
                                (time.time(), "kalshi", "t", 1, 1, 1, None, 0.0))
                    con.commit()
                    landed.append(time.time())
                except sqlite3.OperationalError as e:
                    errors.append(str(e))
                time.sleep(0.005)
        finally:
            con.close()

    t = threading.Thread(target=writer)
    t.start()
    t0 = time.time()
    try:
        s = prune_quotes.run(batch=100, pause_s=0.01)
    finally:
        t1 = time.time()
        done.set()
        t.join()
    assert s["deleted"] == 6001 and s["batches"] == 61
    assert errors == []
    assert len([x for x in landed if t0 < x < t1]) >= 5
    assert s["max_batch_s"] < 0.5


# --- 4. the raw-book allowlist follows the week -------------------------------

def _week_store(tmp_path):
    con = sqlite3.connect(str(tmp_path / "w.db"))
    con.executescript("""
        CREATE TABLE nfl_games (season INT, week INT, kickoff_ts REAL);
        CREATE TABLE outcomes (outcome_id TEXT, season INT, week INT, stat TEXT);
        CREATE TABLE market_outcome (venue TEXT, market_id TEXT, outcome_id TEXT);
        CREATE TABLE paper_ledger (venue TEXT, market_id TEXT);
    """)
    con.executemany("INSERT INTO nfl_games VALUES (?,?,?)",
                    [(2026, 1, 1000.0), (2026, 1, 2000.0),
                     (2026, 5, 50_000_000.0), (2026, 5, 50_100_000.0),
                     (2026, 6, 51_000_000.0)])
    con.executemany("INSERT INTO outcomes VALUES (?,?,?,?)",
                    [("o1", 2026, 1, "receptions"), ("o5", 2026, 5, "rush_attempts"),
                     ("o5y", 2026, 5, "receiving_yards"), ("o6", 2026, 6, "targets")])
    con.executemany("INSERT INTO market_outcome VALUES (?,?,?)",
                    [("kalshi", "WK1", "o1"), ("kalshi", "WK5", "o5"),
                     ("polymarket", "WK5P", "o5"), ("kalshi", "WK5YDS", "o5y"),
                     ("kalshi", "WK6", "o6")])
    con.execute("INSERT INTO paper_ledger VALUES ('kalshi', 'LEDGER')")
    con.commit()
    return con


def test_the_allowlist_is_the_current_week_not_week_one(tmp_path):
    con = _week_store(tmp_path)
    in_week_5 = 50_000_000.0 + 3600                 # first game under way
    assert capture_depth.current_week(con, in_week_5) == (2026, 5)
    assert capture_depth.allowlist(con, in_week_5) == {
        ("kalshi", "WK5"), ("polymarket", "WK5P"), ("kalshi", "LEDGER")}


def test_the_allowlist_moves_when_the_week_does(tmp_path):
    """The other answer from the same store, on the clock alone. The old query
    returned WK1 at every one of these instants."""
    con = _week_store(tmp_path)
    live = config.LIVE_WINDOW_MIN * 60
    assert capture_depth.allowlist(con, 1500.0) == {("kalshi", "WK1"), ("kalshi", "LEDGER")}
    after_wk5 = 50_100_000.0 + live + 1             # last week-5 game is over
    assert capture_depth.current_week(con, after_wk5) == (2026, 6)
    assert ("kalshi", "WK6") in capture_depth.allowlist(con, after_wk5)
    assert ("kalshi", "WK5") not in capture_depth.allowlist(con, after_wk5)


def test_no_game_ahead_falls_back_to_the_last_week_and_no_schedule_to_the_ledger(tmp_path):
    con = _week_store(tmp_path)
    assert capture_depth.current_week(con, 9e9) == (2026, 6)
    con.execute("DROP TABLE nfl_games")
    assert capture_depth.current_week(con, 9e9) is None
    assert capture_depth.allowlist(con, 9e9) == {("kalshi", "LEDGER")}


# --- 5. liveness is asked of the lock -----------------------------------------

def test_is_held_tells_a_held_lock_from_a_stale_file(env):
    assert single_instance.is_held("a67_probe") is False      # no file at all
    lock = single_instance.acquire("a67_probe")
    try:
        assert single_instance.is_held("a67_probe") is True
    finally:
        lock.release()
    # the FILE and its stamp are still there; the lock is not
    assert os.path.exists(single_instance.lock_path("a67_probe"))
    assert single_instance.holder("a67_probe")["pid"] == os.getpid()
    assert single_instance.is_held("a67_probe") is False
    # and asking did not take it, nor wipe the last holder's record
    assert single_instance.holder("a67_probe")["pid"] == os.getpid()
    single_instance.acquire("a67_probe").release()


def test_the_cli_exit_code_is_the_answer(env, capsys):
    assert single_instance._main(["--held", "a67_cli"]) == 1
    assert '"held":false' in capsys.readouterr().out
    lock = single_instance.acquire("a67_cli")
    try:
        assert single_instance._main(["--held", "a67_cli"]) == 0
        assert '"held":true' in capsys.readouterr().out
    finally:
        lock.release()


def test_a_batch_is_a_primary_key_lookup_not_a_walk_of_every_live_row(env):
    """The first version of the batch passed every test above and was wrong
    where it mattered: with a bare `source IN (...)` SQLite planned the delete
    through ix_quotes_ingest and walked every live row per batch, holding the
    write lock 4.1 s a batch on the live store (EXPLAIN QUERY PLAN there read
    `SEARCH quotes USING INDEX ix_quotes_ingest (source=?)`). The same rows are
    deleted either way, so only the PLAN tells the two apart.

    What this can and cannot show: THIS fixture does not reproduce the bad
    plan, because it runs ANALYZE and explains three ids - and with either one
    the planner takes the primary key for the bare form too. What is pinned
    here is that the shipped statement cannot use that index at all
    (`+source`), and that its plan is the primary key.

    CORRECTED by a-69: this said the bad plan could only be read off the
    30M-row store. It reproduces on an empty table under the live store's own
    conditions - no sqlite_stat1, a real batch of 500 ids - and
    tests/test_prune_lock_duration.py does that, and times the lock."""
    seed()
    held = ("AND NOT EXISTS (SELECT 1 FROM quote_retention_hold h WHERE h.venue = "
            "quotes.venue AND h.market_id = quotes.market_id AND h.until_ts > ?)")
    sql = prune_quotes.delete_sql("?,?,?", "?", held)
    assert "+source IN" in sql and " source IN" not in sql
    with store.db() as c:
        c.execute("ANALYZE")
        plan = " | ".join(r[3] for r in c.execute(
            "EXPLAIN QUERY PLAN " + sql,
            (1, 2, 3, time.time(), "live", time.time())))
    assert "PRIMARY KEY" in plan, plan
    assert "ix_quotes_ingest" not in plan, plan


def test_held_is_the_complement_of_the_candidates(env):
    seed()
    with store.db() as c:
        c.executemany("INSERT INTO quotes (ts, venue, market_id, source, ingest_ts) "
                      "VALUES (?,?,?,?,?)",
                      [(time.time() - 30 * DAY, "kalshi", "HELD", "live",
                        time.time() - 30 * DAY)] * 4)
    s = prune_quotes.run(dry_run=True)
    assert s["held"] == 5 and s["candidates"] == 1201
