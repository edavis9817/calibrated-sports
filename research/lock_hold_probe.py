"""How long is a second connection actually DENIED the write lock during a batched
prune - separated from commit latency and from waiting on the test's own writer?
(unit a-84)

    python -m research.lock_hold_probe --arm test   --n 20 --out hold.jsonl
    python -m research.lock_hold_probe --arm prune  --n 20 --load disk:2
    python -m research.lock_hold_probe --arm writer --n 20 --load cpu:24
    python -m research.lock_hold_probe --arm test --procs 15 --n 4      # cold vs warm
    python -m research.lock_hold_probe --arm test --n 20 --writer-sync normal
    python -m research.lock_hold_probe --summarize hold.jsonl

WHY. `tests/test_logger_survives_lock.py::
test_a_writer_with_a_short_timeout_gets_in_between_batches` is named for a
property - a writer that waits only 0.5 s is never refused - and its last line
asserts a clock: `max_batch_s < 0.5`. `max_batch_s` is wall time around the
prune's `execute` + `commit` (jobs/prune_quotes.py), so it contains three
things that are not the prune holding the lock: the time the prune WAITED for
the lock, the time its commit spent on the disk, and whatever a checkpoint cost.
a-81 measured the test's pass rate under load and said so; this measures the
parts.

THREE CLOCKS, ONE RUN. Nothing in jobs/ or tests/ is edited; the probe looks on.

  prune    every DELETE's `execute` and its `commit` are timed separately, by
           handing store._conn a connection subclass. `took` is their sum, which
           is what `max_batch_s` reports.
  writer   the test's own writer, same SQL, same 0.5 s timeout, same 5 ms sleep.
           Its `execute` is the wait for the write lock (the INSERT itself is
           microseconds) and its `commit` is how long IT then holds the lock.
           `writer wait` is the direct answer to "how long was the second
           connection denied": the writer is refused when one wait reaches 0.5 s.
  prober   a THIRD connection with a zero busy timeout that asks for the write
           lock (`BEGIN IMMEDIATE`, then `ROLLBACK`) about once a millisecond
           and records every answer. A run of consecutive refusals is a denial
           as an outside connection sees it. Each run carries two widths: `lo`,
           first refusal to last refusal, and `hi`, last grant before to first
           grant after. The truth is between them, and when the prober itself
           was descheduled `hi` says so by growing.

ARMS. `test` is the test's configuration (prune + writer) with the prober;
`test_noprobe` is the same without it, which is how the prober's own
disturbance is measured rather than assumed; `prune` is the prune and the
prober with NO writer, so every refusal is the prune's; `writer` is the writer
and the prober with no prune, so every refusal is the writer's.

LOADS are started here, stopped by their own handles, and stop themselves when
this process stops touching a heartbeat file - never by name.

It writes only under `--root` (default: a fresh temp directory) and pins the
store at the root and the leaf, as the test's fixture does.
"""
import argparse
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time

from research.lock_timing_probe import _times, cpu_busy

T0_IMPORT = time.perf_counter()
BOUND_S = 0.5            # the test's bound and the writer's timeout; read, never written
pc = time.perf_counter

BEAT_STALE_S = 15.0
CPU_BURN = (
    "import os, sys, time\n"
    "beat = sys.argv[1]\n"
    "while True:\n"
    "    for _ in range(3_000_000):\n"
    "        pass\n"
    f"    if time.time() - os.path.getmtime(beat) > {BEAT_STALE_S}:\n"
    "        break\n"
)
DISK_BURN = (
    "import os, sys, time\n"
    "beat, p = sys.argv[1], sys.argv[2]\n"
    "b = os.urandom(8 << 20)\n"
    f"while time.time() - os.path.getmtime(beat) <= {BEAT_STALE_S}:\n"
    "    with open(p, 'wb') as f:\n"
    "        f.write(b); f.flush(); os.fsync(f.fileno())\n"
    "os.remove(p)\n"
)


class Load:
    """CPU burners or write-and-fsync loops, alive only while we touch the beat."""

    def __init__(self, kind, root):
        self.procs, self.stop = [], threading.Event()
        self.beat = os.path.join(root, f"beat-{os.getpid()}")
        if kind == "none":
            return
        self._touch()
        if kind.startswith("cpu:"):
            for _ in range(int(kind.split(":")[1])):
                self.procs.append(subprocess.Popen(
                    [sys.executable, "-c", CPU_BURN, self.beat]))
        elif kind.startswith("disk"):
            n = int(kind.split(":")[1]) if ":" in kind else 1
            for i in range(n):
                self.procs.append(subprocess.Popen(
                    [sys.executable, "-c", DISK_BURN, self.beat,
                     os.path.join(root, f"burn-{os.getpid()}-{i}.bin")]))
        else:
            raise SystemExit(f"unknown --load {kind!r}: none, cpu:N, disk[:N]")
        threading.Thread(target=self._keep, daemon=True).start()

    def _touch(self):
        with open(self.beat, "w") as f:
            f.write(str(time.time()))

    def _keep(self):
        while not self.stop.wait(2.0):
            self._touch()

    def close(self):
        self.stop.set()
        for p in self.procs:              # by handle: the ones started above
            p.terminate()
        for p in self.procs:
            p.wait(timeout=30)
        root = os.path.dirname(self.beat)
        for f in os.listdir(root):        # a terminated burner leaves its file
            if f.startswith(f"burn-{os.getpid()}-"):
                try:
                    os.remove(os.path.join(root, f))
                except OSError:           # still held for a moment after the kill;
                    pass                  # a scratch file, not worth a failed run
        if os.path.exists(self.beat):
            os.remove(self.beat)


# --- the prune's clock --------------------------------------------------------

PRUNE_LOG = []           # ("x" | "c", t0, t1) for every DELETE and its commit
ONE_TX = False           # --one-transaction: swallow the per-batch commit, which is
                         # the arrangement that killed the logger. It exists to show
                         # what each clock reads when the lock really IS held.


class TimedConn(sqlite3.Connection):
    _pending = False

    def execute(self, sql, *a):
        if not sql.lstrip().startswith("DELETE FROM quotes"):
            return super().execute(sql, *a)
        t0 = pc()
        try:
            return super().execute(sql, *a)
        finally:
            PRUNE_LOG.append(("x", t0, pc()))
            self._pending = True

    def commit(self):
        t0 = pc()
        if ONE_TX and self._pending:      # lock kept until close rolls it back
            PRUNE_LOG.append(("c", t0, pc()))
            self._pending = False
            return
        super().commit()
        if self._pending:
            PRUNE_LOG.append(("c", t0, pc()))
            self._pending = False


class _Sqlite3Shim:
    """store._conn's own code, its own timeout and pragmas, on a timed connection."""

    def connect(self, *a, **k):
        return sqlite3.connect(*a, factory=TimedConn, **k)

    def __getattr__(self, name):
        return getattr(sqlite3, name)


# --- the other two clocks -----------------------------------------------------

def writer_loop(path, done, out, sync):
    """The test's writer, line for line, with a clock on each half."""
    con = sqlite3.connect(path, timeout=BOUND_S)
    if sync != "default":
        con.execute(f"PRAGMA synchronous={sync}")
    try:
        while not done.is_set():
            t0 = pc()
            try:
                con.execute("INSERT INTO poll_log (ts,venue,endpoint,n_markets,"
                            "n_quotes,ok,error,elapsed_s) VALUES (?,?,?,?,?,?,?,?)",
                            (time.time(), "kalshi", "t", 1, 1, 1, None, 0.0))
                t1 = pc()
                con.commit()
                out["ok"].append((t0, t1, pc()))
            except sqlite3.OperationalError as e:
                out["err"].append((t0, pc(), str(e)))
            time.sleep(0.005)
    finally:
        con.close()


def prober_loop(path, done, out):
    con = sqlite3.connect(path, timeout=0, isolation_level=None)
    try:
        while not done.is_set():
            t0 = pc()
            try:
                con.execute("BEGIN IMMEDIATE")
                t1 = pc()
                con.execute("ROLLBACK")
                out.append((t0, t1, 1))
            except sqlite3.OperationalError as e:
                out.append((t0, pc(), 0 if "locked" in str(e) else -1))
            time.sleep(0.001)
    finally:
        con.close()


def denial_runs(attempts, t_from, t_to):
    """[(lo, hi, start)] for every run of consecutive refusals inside the window."""
    a = [x for x in attempts if t_from <= x[0] <= t_to]
    runs, i = [], 0
    while i < len(a):
        if a[i][2] == 1:
            i += 1
            continue
        j = i
        while j + 1 < len(a) and a[j + 1][2] != 1:
            j += 1
        before = a[i - 1][1] if i > 0 else t_from
        after = a[j + 1][0] if j + 1 < len(a) else t_to
        runs.append((a[j][1] - a[i][0], after - before, a[i][0]))
        i = j + 1
    return a, runs


def q(v, p):
    v = sorted(v)
    return round(v[min(len(v) - 1, int(p * len(v)))], 4) if v else None


def overlap(a0, a1, spans):
    return sum(max(0.0, min(a1, b1) - max(a0, b0)) for b0, b1 in spans)


def one_run(dirpath, arm, sync):
    import config
    import store
    from jobs import prune_quotes
    from tests import test_logger_survives_lock as T

    config.DB_PATH = os.path.join(dirpath, "t.db")
    config.STORAGE_DIR = dirpath
    config.RAW_DIR = os.path.join(dirpath, "raw")
    store.init_db()
    store.reset_disk_cache()
    store.reset_quote_state()
    T.seed(n_old=6000)

    del PRUNE_LOG[:]
    done = threading.Event()
    w, probes, threads = {"ok": [], "err": []}, [], []
    if arm in ("test", "test_noprobe", "writer"):
        threads.append(threading.Thread(
            target=writer_loop, args=(config.DB_PATH, done, w, sync)))
    if arm in ("test", "prune", "writer"):
        threads.append(threading.Thread(
            target=prober_loop, args=(config.DB_PATH, done, probes)))

    s = None
    c0 = _times()
    for t in threads:
        t.start()
    t0 = pc()
    real = store.sqlite3
    try:
        if arm == "writer":
            time.sleep(1.5)               # about one prune's length, no prune
        else:
            store.sqlite3 = _Sqlite3Shim()
            s = prune_quotes.run(batch=100, pause_s=0.01)
    finally:
        store.sqlite3 = real
        t1 = pc()
        done.set()
        for t in threads:
            t.join()
    c1 = _times()
    store.reset_disk_cache()
    store.reset_quote_state()

    r = {"arm": arm, "writer_sync": sync, "wall_s": round(t1 - t0, 3),
         "cpu_busy": cpu_busy(c0, c1)}

    # the prune, batch by batch
    ex = [(a, b) for k, a, b in PRUNE_LOG if k == "x"]
    cm = [(a, b) for k, a, b in PRUNE_LOG if k == "c"]
    w_ok = [x for x in w["ok"] if t0 <= x[0] <= t1]
    w_hold = [(x[1], x[2]) for x in w_ok]         # granted -> committed
    if s is not None:
        if len(ex) != s["batches"] or len(cm) != s["batches"]:
            raise SystemExit(f"FAILED: timed {len(ex)} deletes and {len(cm)} commits "
                             f"for {s['batches']} batches - the clock missed some")
        took = [c[1] - x[0] for x, c in zip(ex, cm)]
        k = max(range(len(took)), key=took.__getitem__)
        r.update({
            "max_batch_s": s["max_batch_s"], "deleted": s["deleted"],
            "batches": s["batches"], "delete_s": s["delete_s"], "read_s": s["read_s"],
            "took_max": round(took[k], 4), "took_max_batch": k,
            "took_max_exec": round(ex[k][1] - ex[k][0], 4),
            "took_max_commit": round(cm[k][1] - cm[k][0], 4),
            # of the slowest batch's execute, how much the writer held the lock
            "took_max_exec_under_writer": round(overlap(*ex[k], w_hold), 4),
            "exec_p50": q([b - a for a, b in ex], .5),
            "exec_max": q([b - a for a, b in ex], 1),
            "commit_p50": q([b - a for a, b in cm], .5),
            "commit_max": q([b - a for a, b in cm], 1),
            # each batch less the time the writer held the lock inside its execute:
            # an UPPER bound on the prune's own hold (busy-handler sleeps remain)
            "took_net_max": round(max(
                t - overlap(*x, w_hold) for t, x in zip(took, ex)), 4),
            "exec_s": [round(b - a, 4) for a, b in ex],
            "commit_s": [round(b - a, 4) for a, b in cm],
            "took_first": round(took[0], 4), "took_p50": q(took, .5),
            "took_rest_max": round(max(took[1:]), 4) if len(took) > 1 else None,
        })

    # the writer: its wait IS how long a second connection was denied
    if "writer" in arm or arm.startswith("test"):
        waits = [x[1] - x[0] for x in w_ok]
        holds = [x[2] - x[1] for x in w_ok]
        if not w_ok:
            raise SystemExit("FAILED: the writer landed nothing inside the window")
        r.update({
            "writer_landed": len(w_ok), "writer_errors": len(w["err"]),
            "writer_wait_p50": q(waits, .5), "writer_wait_p99": q(waits, .99),
            "writer_wait_max": q(waits, 1),
            "writer_hold_p50": q(holds, .5), "writer_hold_p99": q(holds, .99),
            "writer_hold_max": q(holds, 1),
            "writer_err_wait_max": q([e[1] - e[0] for e in w["err"]], 1),
            # every refusal: when, how long it waited, what it was told, and which
            # prune batches (execute start to commit end) were in progress
            "writer_err": [{
                "at": round(e[0] - t0, 3), "waited": round(e[1] - e[0], 3),
                "msg": e[2],
                "batches": [i for i, (x, c) in enumerate(zip(ex, cm))
                            if x[0] < e[1] and c[1] > e[0]]} for e in w["err"]][:8],
        })

    # the prober: refusals as a third connection sees them
    if arm in ("test", "prune", "writer"):
        a, runs = denial_runs(probes, t0, t1)
        if len(a) < 5:
            raise SystemExit(f"FAILED: the prober made {len(a)} attempts - it saw nothing")
        # under CPU saturation the prober's 1 ms sleep takes a scheduler quantum,
        # so it asks tens of times, not hundreds: probe_gap_max is its resolution
        # for that run and `hi` already carries it.
        gaps = [y[0] - x[0] for x, y in zip(a, a[1:])]
        r.update({
            "probe_attempts": len(a), "probe_refused": sum(x[2] == 0 for x in a),
            "probe_other_errors": sum(x[2] == -1 for x in a),
            "probe_gap_p50": q(gaps, .5), "probe_gap_max": q(gaps, 1),
            "denial_runs": len(runs),
            "denial_lo_p50": q([x[0] for x in runs], .5),
            "denial_lo_max": q([x[0] for x in runs], 1),
            "denial_hi_p50": q([x[1] for x in runs], .5),
            "denial_hi_p99": q([x[1] for x in runs], .99),
            "denial_hi_max": q([x[1] for x in runs], 1),
            "denied_s_lo": round(sum(x[0] for x in runs), 4),
            # the five longest refusals, each with who was inside it
            "denial_top": [{
                "at": round(st - t0, 3), "lo": round(lo, 4), "hi": round(hi, 4),
                "under_writer": round(overlap(st, st + lo, w_hold), 4),
                "batches": [i for i, (x, c) in enumerate(zip(ex, cm))
                            if x[0] < st + lo and c[1] > st]}
                for lo, hi, st in sorted(runs, key=lambda x: -x[1])[:5]],
        })
        if runs and (ex or w_hold):
            lo, hi, st = max(runs, key=lambda x: x[1])
            # who was inside the longest refusal: the prune's batches or the writer
            r["denial_max_under_prune"] = round(overlap(
                st, st + lo, [(x[0], c[1]) for x, c in zip(ex, cm)]), 4)
            r["denial_max_under_writer"] = round(overlap(st, st + lo, w_hold), 4)
    return r


TEST = ("tests/test_logger_survives_lock.py::"
        "test_a_writer_with_a_short_timeout_gets_in_between_batches")


def run_test(n, root, out, load):
    """The TEST ITSELF, one pytest process per run, with research.lock_hold_plugin
    recording the `max_batch_s` it asserted on - so a pass has a number too."""
    codes = []
    for i in range(n):
        log = os.path.join(root, f"plugin-{os.getpid()}-{i}.jsonl")
        env = dict(os.environ, A84_PRUNE_LOG=log)
        c0 = _times()
        p = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", TEST, "-p", "no:cacheprovider",
             "-p", "research.lock_hold_plugin",
             "--basetemp", os.path.join(root, f"bt-{os.getpid()}")],
            capture_output=True, text=True, env=env)
        rows = []
        if os.path.exists(log):
            with open(log, encoding="utf-8") as f:
                rows = [json.loads(x) for x in f]
            os.remove(log)
        if len(rows) != 1:
            raise SystemExit(f"FAILED: the plugin logged {len(rows)} prunes for one "
                             f"test run (exit {p.returncode}):\n{p.stdout[-800:]}")
        r = {"arm": "pytest", "i": i, "load": load, "exit": p.returncode,
             "max_batch_s": rows[0]["max_batch_s"], "delete_s": rows[0]["delete_s"],
             "cpu_busy": cpu_busy(c0, _times()), "at": round(time.time(), 1)}
        codes.append(p.returncode)
        print(f"    test run {i}: exit {p.returncode} max_batch_s {r['max_batch_s']}")
        if out:
            with open(out, "a", encoding="utf-8") as f:
                f.write(json.dumps(r) + "\n")
    odd = [c for c in codes if c not in (0, 1)]
    print(f"  load {load}: the test passed {codes.count(0)} of {len(codes)}, "
          f"failed {codes.count(1)}, other exit codes {odd}")
    return 1 if odd or len(codes) != n else 0


FIELDS = ("max_batch_s", "took_max_exec", "took_max_commit",
          "took_max_exec_under_writer", "took_net_max", "took_first", "took_rest_max",
          "writer_wait_max", "writer_hold_max", "denial_hi_max", "denial_lo_max")


def summarize(paths, pool=False):
    rows = []
    for p in paths:
        with open(p, encoding="utf-8") as f:
            rows += [json.loads(x) for x in f if x.strip()]
    if not rows:
        print("FAILED: no rows read")
        return 1
    keys = {}
    for r in rows:
        keys.setdefault(("pooled" if pool else r.get("tag", ""), r["load"], r["arm"],
                         r.get("writer_sync", "")), []).append(r)
    for (tag, load, arm, sync), g in sorted(keys.items()):
        busy = [r["cpu_busy"] for r in g if r.get("cpu_busy") is not None]
        print(f"\n[{tag or '-'}] load={load} arm={arm} sync={sync or '-'}  "
              f"n={len(g)}  cpu busy median {q(busy, .5)}")
        if arm == "pytest":
            print(f"  the test passed {sum(r['exit'] == 0 for r in g)} of {len(g)}")
        if any("writer_errors" in r for r in g):
            print(f"  runs with a refused writer: "
                  f"{sum(r.get('writer_errors', 0) > 0 for r in g)} of {len(g)}")
        if any("max_batch_s" in r for r in g):
            print(f"  runs with max_batch_s >= {BOUND_S}: "
                  f"{sum(r['max_batch_s'] >= BOUND_S for r in g if 'max_batch_s' in r)}")
        for k in FIELDS:
            v = [r[k] for r in g if r.get(k) is not None]
            if v:
                print(f"  {k:28s} min {q(v, 0):<8} p50 {q(v, .5):<8} p90 {q(v, .9):<8} "
                      f"max {q(v, 1):<8} >= {BOUND_S}: {sum(x >= BOUND_S for x in v)}")
        over = [r for r in g if r.get("max_batch_s", 0) >= BOUND_S and "took_max_exec" in r]
        if over:
            # where the slowest batch of an over-the-bound run spent its time
            ex_ = sum(r["took_max_exec"] > r["took_max_commit"] for r in over)
            uw = sum(r["took_max_exec_under_writer"] >= 0.5 * r["took_max"] for r in over)
            print(f"  of {len(over)} runs over the bound: slowest batch mostly in "
                  f"execute {ex_}, mostly in commit {len(over) - ex_}; writer held the "
                  f"lock for half or more of it in {uw}")
            seen = [r for r in over if r.get("denial_hi_max") is not None]
            if seen:
                print(f"  and the third connection saw a refusal >= {BOUND_S}s in "
                      f"{sum(r['denial_hi_max'] >= BOUND_S for r in seen)} of those "
                      f"{len(seen)}")
        det = [r for r in g if r.get("commit_s") and r.get("denial_top")]
        if det and arm == "prune":
            # which batch the slowest commit was, and which the longest refusal
            slow = [max(range(len(r["commit_s"])), key=r["commit_s"].__getitem__)
                    for r in det]
            top = [r["denial_top"][0]["batches"] for r in det]
            print(f"  slowest commit is batch {max(set(slow), key=slow.count)} in "
                  f"{slow.count(max(set(slow), key=slow.count))} of {len(det)} runs; "
                  f"longest refusal is inside batch 0 in "
                  f"{sum(b == [0] for b in top)} of {len(det)}, inside the "
                  f"slowest-commit batch in "
                  f"{sum(k in b for k, b in zip(slow, top))}")
            print(f"  first batch's commit   p50 {q([r['commit_s'][0] for r in det], .5)} "
                  f"max {q([r['commit_s'][0] for r in det], 1)};   slowest commit   "
                  f"p50 {q([max(r['commit_s']) for r in det], .5)} "
                  f"max {q([max(r['commit_s']) for r in det], 1)}")
        cold = [r for r in g if r.get("i") == 0 and r.get("fresh")]
        warm = [r for r in g if r.get("i", 0) > 0 and r.get("fresh")]
        if cold and warm:
            for k in ("max_batch_s", "took_first", "took_rest_max", "writer_wait_max"):
                a = [r[k] for r in cold if r.get(k) is not None]
                b = [r[k] for r in warm if r.get(k) is not None]
                if a and b:
                    print(f"  first run in a process vs later  {k:18s} "
                          f"p50 {q(a, .5)} / {q(b, .5)}   max {q(a, 1)} / {q(b, 1)}   "
                          f"n {len(a)} / {len(b)}")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--arm", default="test",
                    choices=("test", "test_noprobe", "prune", "writer"))
    ap.add_argument("--n", type=int, default=20, help="runs per process")
    ap.add_argument("--procs", type=int, default=0,
                    help="spawn this many fresh processes of --n runs each")
    ap.add_argument("--load", default="none", help="none, cpu:N, disk[:N]")
    ap.add_argument("--writer-sync", default="default",
                    choices=("default", "normal", "full"))
    ap.add_argument("--root", default=None)
    ap.add_argument("--out", default=None, help="append one JSON line per run")
    ap.add_argument("--tag", default="", help="a label carried on every row")
    ap.add_argument("--run-test", action="store_true",
                    help="run the test itself under the load, one process per run")
    ap.add_argument("--one-transaction", action="store_true",
                    help="swallow the prune's per-batch commits (the old arrangement)")
    ap.add_argument("--child", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--summarize", nargs="+", default=None)
    ap.add_argument("--pool", action="store_true",
                    help="with --summarize: one group per load and arm, across tags")
    args = ap.parse_args(argv)

    if args.summarize:
        return summarize(args.summarize, args.pool)
    if args.one_transaction:
        global ONE_TX
        ONE_TX = True
        if args.procs or args.run_test:
            raise SystemExit("--one-transaction runs in this process only")
        args.tag = args.tag or "one-transaction"

    root = args.root or tempfile.mkdtemp(prefix="lock_hold_probe_")
    os.makedirs(root, exist_ok=True)
    load = None if args.child else Load(args.load, root)
    try:
        if load and load.procs:
            time.sleep(3.0)                   # let the load reach steady state
        if args.run_test:
            return run_test(args.n, root, args.out, args.load)
        if args.procs:
            for j in range(args.procs):
                cmd = [sys.executable, "-m", "research.lock_hold_probe", "--child",
                       "--arm", args.arm, "--n", str(args.n), "--load", args.load,
                       "--writer-sync", args.writer_sync, "--root", root,
                       "--tag", args.tag]
                if args.out:
                    cmd += ["--out", args.out]
                rc = subprocess.run(cmd).returncode
                if rc != 0:
                    print(f"FAILED: child process {j} exited {rc}")
                    return 1
            return 0
        for i in range(args.n):
            d = os.path.join(root, f"run-{os.getpid()}-{i:03d}")
            os.makedirs(d)
            r = one_run(d, args.arm, args.writer_sync)
            r.update({"i": i, "fresh": args.child, "load": args.load, "tag": args.tag,
                      "proc_age_s": round(pc() - T0_IMPORT, 2),
                      "at": round(time.time(), 1), "cores": os.cpu_count()})
            if args.out:
                with open(args.out, "a", encoding="utf-8") as f:
                    f.write(json.dumps(r) + "\n")
            else:
                print(json.dumps(r))
            shutil.rmtree(d, ignore_errors=True)
    finally:
        if load:
            load.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
