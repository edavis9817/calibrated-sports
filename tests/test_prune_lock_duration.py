"""How long the prune holds the write lock (unit a-69). Run: pytest -q

a-67's first batched prune deleted exactly the right rows and held the write
lock up to 5.877 s a batch on the live store: `id IN (...) AND source IN (...)`
planned through `ix_quotes_ingest (source=?)` and walked every live row per
batch. Every row-level test passed, because the same rows go either way. The
plan was found by reading EXPLAIN QUERY PLAN on the real database after the run
took ten minutes.

So row counts cannot guard this path, and neither can the prune's own
`max_batch_s`: that is the job timing itself. These tests measure the lock from
OUTSIDE - a second connection that asks for the write lock in a tight loop and
records the longest span over which it was refused - and count the work a batch
does while it holds it.

Every assertion is shown returning the other answer, against a-67's first
version AS IT WAS WRITTEN (`first_version`), on the same store, the same rows
and the same observer: the shipped statement passes and that one fails.

THE DEFECT REPRODUCES ON A FIXTURE, given the two things the live store has.
The earlier plan test (tests/test_logger_survives_lock.py) says it cannot be
reproduced on a small store. Measured 2026-10-08, it can - that test removed
both conditions:

  - the live store has NO `sqlite_stat1` (read 2026-10-08, mode=ro), so its
    planner works without statistics. The earlier test ran ANALYZE first, and
    with statistics the planner takes the primary key for either statement.
  - a real batch is 500 ids. The earlier test explained three, and with three
    the planner takes the primary key for either statement.

With no statistics and 500 ids the bare `source IN (...)` plans through
`ix_quotes_ingest (source=?)` on an EMPTY table, exactly as it did on the live
one. So nothing here runs ANALYZE, and every batch is a real one. `walked` is
the same access path forced with INDEXED BY, kept so the guard still
discriminates on a SQLite whose planner would choose differently.

What a fixture cannot show: the absolute figure on a 30M-row store. That was
0.055 s on 2026-10-06 and is on every `prune_quotes` health row. What IS pinned
is the property behind it - a batch's lock does not grow with the table.
"""
import sqlite3
import threading
import time

import pytest

import config
import store
from jobs import prune_quotes

DAY = 86400.0
N_OLD = 1500          # prunable rows: three batches of 500
BATCH = 500


@pytest.fixture
def env(tmp_path, monkeypatch):
    """An isolated store, root and leaf both pinned (STORAGE_DIR is computed
    once at import, so DB_PATH alone leaves the health row in the real one)."""
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setattr(config, "STORAGE_DIR", str(tmp_path))
    monkeypatch.setattr(config, "RAW_DIR", str(tmp_path / "raw"))
    store.init_db()
    store.reset_disk_cache()
    store.reset_quote_state()
    yield tmp_path
    store.reset_disk_cache()
    store.reset_quote_state()


def add_old(n=N_OLD):
    old = time.time() - 30 * DAY
    with store.db() as c:
        c.executemany("INSERT INTO quotes (ts, venue, market_id, source, ingest_ts) "
                      "VALUES (?,?,?,?,?)",
                      [(old, "kalshi", f"OLD{i % 7}", "live", old) for i in range(n)])


def add_live(n):
    """Live rows INSIDE the retention window. The prune must not touch them, and
    must not look at them either: they are what the first version walked."""
    new = time.time() - DAY
    with store.db() as c:
        c.executemany("INSERT INTO quotes (ts, venue, market_id, source, ingest_ts) "
                      "VALUES (?,?,?,?,?)",
                      ((new, "kalshi", f"NEW{i % 50}", "live", new) for i in range(n)))
    # No ANALYZE, on purpose: the live store has no sqlite_stat1.


def first_version(marks: str, ph: str, held_sql: str) -> str:
    """a-67's first batched delete, character for character but the `+`."""
    return (f"DELETE FROM quotes WHERE id IN ({marks}) "
            f"AND COALESCE(ingest_ts, ts) < ? AND source IN ({ph}) {held_sql}")


def walked(marks: str, ph: str, held_sql: str) -> str:
    """The same access path, forced. Same rows deleted."""
    return (f"DELETE FROM quotes INDEXED BY ix_quotes_ingest WHERE id IN ({marks}) "
            f"AND COALESCE(ingest_ts, ts) < ? AND source IN ({ph}) {held_sql}")


class LockProbe:
    """The longest span over which another connection was refused the write lock.

    `BEGIN IMMEDIATE` with no busy timeout either gets the write lock at once or
    is refused at once. A run of consecutive refusals is a span the lock was
    held by someone else; the probe gives its own lock straight back, so it
    cannot lengthen what it measures (it can only make the prune wait, which is
    not a span the prune holds)."""

    def __init__(self):
        self.longest = 0.0
        self.attempts = 0
        self.refused = 0
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run)

    def _run(self):
        con = sqlite3.connect(config.DB_PATH, timeout=0, isolation_level=None)
        since = None
        try:
            while not self._stop.is_set():
                self.attempts += 1
                try:
                    con.execute("BEGIN IMMEDIATE")
                    con.execute("ROLLBACK")
                    since = None
                except sqlite3.OperationalError:
                    self.refused += 1
                    now = time.perf_counter()
                    since = now if since is None else since
                    self.longest = max(self.longest, now - since)
        finally:
            con.close()

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._stop.set()
        self._thread.join()


def probed_prune():
    with LockProbe() as probe:
        stats = prune_quotes.run(batch=BATCH, pause_s=0.02)
    assert stats["deleted"] == N_OLD and stats["batches"] == N_OLD // BATCH, stats
    # the observer observed: it got in between batches and was refused during them
    assert probe.refused > 0 and probe.attempts > probe.refused, vars(probe)
    return probe.longest, stats


HELD = ("AND NOT EXISTS (SELECT 1 FROM quote_retention_hold h WHERE h.venue = "
        "quotes.venue AND h.market_id = quotes.market_id AND h.until_ts > ?)")


def plan_of(statement, n_ids=BATCH):
    sql = statement(",".join("?" * n_ids), "?", HELD)
    with store.db() as c:
        return " | ".join(r[3] for r in c.execute(
            "EXPLAIN QUERY PLAN " + sql,
            (*range(n_ids), time.time(), "live", time.time())))


def walks_the_table(statement) -> bool:
    return "ix_quotes_ingest" in plan_of(statement)


def test_a_real_batch_plans_through_the_primary_key_with_no_statistics(env):
    """The plan, under the live store's conditions: 500 ids, no sqlite_stat1."""
    add_old()
    with store.db() as c:
        assert not c.execute("SELECT 1 FROM sqlite_master WHERE name='sqlite_stat1'").fetchone()
    plan = plan_of(prune_quotes.delete_sql)
    assert "PRIMARY KEY" in plan and "ix_quotes_ingest" not in plan, plan
    assert "ix_quotes_ingest" in plan_of(walked)
    # a-67's first version, unforced. This is the line the earlier test could
    # not write; if a later SQLite plans it differently, say so rather than
    # pass on a planner that no longer reproduces the defect.
    if not walks_the_table(first_version):
        pytest.skip(f"SQLite {sqlite3.sqlite_version} does not plan the bare statement "
                    f"through ix_quotes_ingest here; the forced path still guards it")


def test_the_probe_sees_a_lock_of_known_length(env):
    """The observer first, on a lock whose length is known. A probe that read
    zero for everything would pass every ceiling below."""
    holder = sqlite3.connect(config.DB_PATH, timeout=5, isolation_level=None)
    try:
        with LockProbe() as probe:
            time.sleep(0.05)
            holder.execute("BEGIN IMMEDIATE")
            time.sleep(0.40)
            holder.execute("ROLLBACK")
            time.sleep(0.05)
    finally:
        holder.close()
    assert 0.35 < probe.longest < 0.80, probe.longest
    with LockProbe() as idle:
        time.sleep(0.10)
    assert idle.refused == 0 and idle.longest == 0.0 and idle.attempts > 0


def test_the_longest_write_lock_is_short_and_does_not_grow_with_the_table(env, monkeypatch):
    """The lock itself, timed from another connection on a store of 300,000
    live rows. Measured 2026-10-08: the shipped batch holds it 0.003 s here and
    the walked one 0.042 s (0.006 s at 20,000 rows, 0.015 s at 100,000 - it
    scales with the table, which is the defect).

    Each figure is the smallest of three runs' longest lock: a maximum is the
    statistic a single scheduler hiccup moves, and a hiccup has to land in all
    three runs to move this."""
    add_live(300_000)

    def longest_of(runs=3):
        seen, reported = [], []
        for _ in range(runs):
            add_old()
            longest, stats = probed_prune()
            seen.append(longest)
            reported.append(stats["max_batch_s"])
        return min(seen), seen, reported

    shipped, shipped_all, reported = longest_of()
    # a-67's first version as written, where this SQLite plans it the way the
    # live store did; its forced equivalent where it does not.
    regression = first_version if walks_the_table(first_version) else walked
    monkeypatch.setattr(prune_quotes, "delete_sql", regression)
    bad, bad_all, _ = longest_of()

    # 1. an absolute ceiling. A batch that keeps every other writer out for a
    #    quarter of a second on a store this size is broken whatever the cause.
    assert shipped < 0.25, shipped_all
    # 2. the regression guard: under half the known-bad path on the same store.
    #    Measured ratio 14x, so there is 7x of room before this fails - and a
    #    batch that had started walking the table again would sit at 1x.
    assert shipped < 0.5 * bad, (shipped_all, bad_all)
    # 3. the number the health row prints covers what an outside observer saw.
    #    `max_batch_s` times execute + commit; if someone moves that timer so it
    #    no longer spans the lock, the probe exceeds it.
    assert all(seen <= said + 0.02 for seen, said in zip(shipped_all, reported)), \
        (shipped_all, reported)


class StepCounter:
    """VM steps per DELETE statement, on every connection the prune opens.

    The clock above is the thing; this is the same property without a clock.
    SQLite calls the progress handler every `EVERY` virtual-machine
    instructions, a DELETE takes the write lock at its first instruction, and
    so the count for one DELETE is the work done while holding it."""
    EVERY = 20

    def __init__(self, monkeypatch):
        self.per_delete = []
        self._in_delete = False
        real = store._conn

        def conn():
            c = real()
            c.set_trace_callback(self._statement)
            c.set_progress_handler(self._tick, self.EVERY)
            return c

        monkeypatch.setattr(store, "_conn", conn)

    def _statement(self, sql):
        self._in_delete = sql.lstrip().upper().startswith("DELETE FROM QUOTES")
        if self._in_delete:
            self.per_delete.append(0)

    def _tick(self):
        if self._in_delete:
            self.per_delete[-1] += 1
        return 0


def steps_of_the_costliest_batch(monkeypatch, n_live):
    add_live(n_live)
    add_old()
    with monkeypatch.context() as m:
        counter = StepCounter(m)
        stats = prune_quotes.run(batch=BATCH, pause_s=0)
    assert stats["deleted"] == N_OLD
    assert len(counter.per_delete) == N_OLD // BATCH and min(counter.per_delete) > 0, \
        counter.per_delete
    return max(counter.per_delete)


@pytest.mark.parametrize("statement, grows",
                         [(None, False), (first_version, True), (walked, True)],
                         ids=["shipped", "first_version", "walked"])
def test_the_work_done_under_the_lock_does_not_grow_with_the_table(
        env, monkeypatch, statement, grows):
    """Ten times the live rows, the same three batches of 500. The shipped
    batch does the same work; the walked one does about ten times as much.
    Deterministic, so this is the half that cannot flake - and the walked case
    is the proof it can fail."""
    if statement is first_version and not walks_the_table(first_version):
        pytest.skip(f"SQLite {sqlite3.sqlite_version} does not plan the bare statement "
                    f"through ix_quotes_ingest here; the 'walked' case covers the path")
    if statement is not None:
        monkeypatch.setattr(prune_quotes, "delete_sql", statement)
    small = steps_of_the_costliest_batch(monkeypatch, 20_000)
    large = steps_of_the_costliest_batch(monkeypatch, 180_000)     # 200,000 in all
    ratio = large / small
    if grows:
        assert ratio > 4, (small, large)
    else:
        assert ratio < 1.5, (small, large)
