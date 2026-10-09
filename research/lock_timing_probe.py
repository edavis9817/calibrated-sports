"""How often does the prune's longest batch reach the lock test's 0.5 s bound, and
under what machine load? (unit a-81)

    python -m research.lock_timing_probe --n 30 --load none --out probe.jsonl
    python -m research.lock_timing_probe --n 20 --load cpu:24
    python -m research.lock_timing_probe --n 20 --load disk

WHAT IT MEASURES. `tests/test_logger_survives_lock.py::
test_a_writer_with_a_short_timeout_gets_in_between_batches` asserts four things
about one prune of 6,001 rows in 61 batches with a second connection writing
throughout; a-76 saw it fail on `assert 0.862 < 0.5`, the last of the four. A
passing test prints nothing, so its pass rate says how often the bound holds and
not how close it came. This runs the same body - the test module's own `seed`,
the same writer, the same `prune_quotes.run(batch=100, pause_s=0.01)` - and
records every run's `max_batch_s` beside the CPU the machine was using.

WHAT IT DOES NOT DO. It changes no test and no bound, and it is not the test:
the pass rate of the test is measured by running the test. A run here is marked
`would_fail` by re-applying the test's four assertions to what it recorded.

`max_batch_s` IS WALL TIME AROUND `execute` + `commit` (jobs/prune_quotes.py), so
it includes any time the prune WAITED for the test's own writer to release the
lock and any time its commit waited on the disk. It is an upper bound on how
long the prune held the write lock, not a measurement of it.

It writes only under `--root` (default: a fresh temp directory): the store is
pinned at the root and the leaf, as the test's fixture pins it. Load processes
are started here and stopped by their own handles, never by name.
"""
import argparse
import ctypes
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time

BOUND_S = 0.5            # the test's bound, read here and never written back

CPU_BURN = "while True:\n    pass\n"
DISK_BURN = (
    "import os, sys\n"
    "p = sys.argv[1]\n"
    "b = os.urandom(8 << 20)\n"
    "while True:\n"
    "    with open(p, 'wb') as f:\n"
    "        f.write(b); f.flush(); os.fsync(f.fileno())\n"
)


def _times():
    """(idle, total) processor time in 100 ns units, summed over every core."""
    if os.name != "nt":
        with open("/proc/stat") as f:
            v = [int(x) for x in f.readline().split()[1:]]
        return v[3] + v[4], sum(v)
    idle, kern, user = (ctypes.c_ulonglong() for _ in range(3))
    ok = ctypes.windll.kernel32.GetSystemTimes(
        ctypes.byref(idle), ctypes.byref(kern), ctypes.byref(user))
    if not ok:
        raise OSError("GetSystemTimes failed")
    return idle.value, kern.value + user.value      # kernel time includes idle


def cpu_busy(a, b):
    """Share of all cores busy between two `_times()` readings."""
    total = b[1] - a[1]
    return None if total <= 0 else round(1.0 - (b[0] - a[0]) / total, 3)


def start_load(kind, root):
    procs = []
    if kind.startswith("cpu:"):
        for _ in range(int(kind.split(":")[1])):
            procs.append(subprocess.Popen([sys.executable, "-c", CPU_BURN]))
    elif kind.startswith("disk"):
        n = int(kind.split(":")[1]) if ":" in kind else 1
        for i in range(n):
            procs.append(subprocess.Popen(
                [sys.executable, "-c", DISK_BURN, os.path.join(root, f"burn{i}.bin")]))
    elif kind != "none":
        raise SystemExit(f"unknown --load {kind!r}: none, cpu:N, disk[:N]")
    return procs


def stop_load(procs):
    for p in procs:                   # by handle: these are the ones started above
        p.terminate()
    for p in procs:
        p.wait(timeout=30)


def one_run(dirpath):
    """The test's body, returning what it asserts on instead of asserting."""
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
    c0 = _times()
    t.start()
    t0 = time.time()
    try:
        s = prune_quotes.run(batch=100, pause_s=0.01)
    finally:
        t1 = time.time()
        done.set()
        t.join()
    c1 = _times()
    store.reset_disk_cache()
    store.reset_quote_state()

    inside = sorted(x for x in landed if t0 < x < t1)
    gaps = [b - a for a, b in zip(inside, inside[1:])]
    failed = [name for name, ok in (
        ("deleted/batches", s["deleted"] == 6001 and s["batches"] == 61),
        ("errors == []", errors == []),
        ("landed >= 5", len(inside) >= 5),
        ("max_batch_s < 0.5", s["max_batch_s"] < BOUND_S)) if not ok]
    return {"max_batch_s": s["max_batch_s"], "delete_s": s["delete_s"],
            "prune_wall_s": round(t1 - t0, 3), "deleted": s["deleted"],
            "batches": s["batches"], "writer_landed": len(inside),
            "writer_errors": len(errors),
            "writer_max_gap_s": round(max(gaps), 3) if gaps else None,
            "cpu_busy": cpu_busy(c0, c1), "would_fail": failed}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--n", type=int, default=30)
    ap.add_argument("--load", default="none", help="none, cpu:N, disk[:N]")
    ap.add_argument("--root", default=None, help="scratch directory (default: a new temp dir)")
    ap.add_argument("--out", default=None, help="append one JSON line per run")
    args = ap.parse_args(argv)

    root = args.root or tempfile.mkdtemp(prefix="lock_timing_probe_")
    os.makedirs(root, exist_ok=True)
    procs = start_load(args.load, root)
    rows = []
    try:
        if procs:
            time.sleep(3.0)                      # let the load reach steady state
        for i in range(args.n):
            d = os.path.join(root, f"run{i:03d}")
            os.makedirs(d)
            r = one_run(d)
            r.update({"i": i, "load": args.load, "at": round(time.time(), 1),
                      "cores": os.cpu_count()})
            rows.append(r)
            if args.out:
                with open(args.out, "a", encoding="utf-8") as f:
                    f.write(json.dumps(r) + "\n")
            shutil.rmtree(d, ignore_errors=True)
    finally:
        stop_load(procs)

    if len(rows) != args.n:
        print(f"FAILED: {len(rows)} runs recorded, {args.n} asked for")
        return 1
    mx = sorted(r["max_batch_s"] for r in rows)
    busy = sorted(r["cpu_busy"] for r in rows if r["cpu_busy"] is not None)
    fails = [r for r in rows if r["would_fail"]]
    q = lambda v, p: v[min(len(v) - 1, int(p * len(v)))]
    print(f"  load {args.load}: {len(rows)} runs on {os.cpu_count()} cores, "
          f"cpu busy median {q(busy, 0.5) if busy else 'n/a'}")
    print(f"  max_batch_s  min {mx[0]}  median {q(mx, 0.5)}  p90 {q(mx, 0.9)}  max {mx[-1]}")
    print(f"  at or over the {BOUND_S}s bound: {sum(m >= BOUND_S for m in mx)} of {len(mx)}")
    print(f"  would fail any of the test's four assertions: {len(fails)} of {len(rows)}")
    for r in fails:
        print(f"    run {r['i']}: {r['would_fail']} max_batch_s={r['max_batch_s']} "
              f"cpu_busy={r['cpu_busy']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
