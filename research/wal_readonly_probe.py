"""What does `mode=ro` actually guarantee on a WAL database? (unit a-07)

    python -m research.wal_readonly_probe [--dir SCRATCH]

Every "read-only" claim in this project's stop rules rests on
`file:...?mode=ro`. c-04 observed that such an open CREATES `-wal`/`-shm`
files. This probe measures the whole contract on a scratch database it builds
itself - it never opens a real store - and prints one line per property:

  P1  the main database file's bytes are unchanged by a mode=ro session
  P2  a mode=ro session on a database with no -wal/-shm CREATES them
  P3  ...and LEAVES them after close (a read-only connection cannot checkpoint)
  P4  a mode=ro connection cannot write (a CREATE raises)
  P5  a mode=ro reader SEES commits that are still only in the WAL
  P6  an open mode=ro read transaction PINS the WAL: a writer's
      wal_checkpoint(TRUNCATE) cannot reset it, so the -wal file keeps growing
      for as long as the reader holds its snapshot
  P7  immutable=1 (the usual "no side effects" suggestion) creates no files
      but MISSES commits that are still in the WAL - it reads a stale database

Exit 0 only if every property was observed; a probe that measured nothing is a
failed probe.
"""
import argparse
import hashlib
import os
import shutil
import sqlite3
import sys
import tempfile


def _sha(p):
    with open(p, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def _side(db):
    return {s: os.path.exists(db + s) for s in ("-wal", "-shm")}


def _ro(db, extra=""):
    uri = "file:" + db.replace("\\", "/") + "?mode=ro" + extra
    return sqlite3.connect(uri, uri=True, isolation_level=None)


def probe(root):
    out = {}
    db = os.path.join(root, "t.db")
    w = sqlite3.connect(db, isolation_level=None)
    w.execute("PRAGMA journal_mode=WAL")
    w.execute("CREATE TABLE t(x)")
    w.execute("INSERT INTO t VALUES (1)")
    w.close()                                   # last close: checkpoint, -wal/-shm deleted
    assert _side(db) == {"-wal": False, "-shm": False}, _side(db)
    before = _sha(db)

    r = _ro(db)
    n = r.execute("SELECT COUNT(*) FROM t").fetchone()[0]
    during = _side(db)
    try:
        r.execute("CREATE TABLE y(z)")
        out["P4 ro connection cannot write"] = False
    except sqlite3.OperationalError as e:
        out["P4 ro connection cannot write"] = "readonly" in str(e)
    r.close()
    after = _side(db)
    out["P1 main db bytes unchanged"] = _sha(db) == before and n == 1
    out["P2 ro open creates -wal/-shm"] = during == {"-wal": True, "-shm": True}
    out["P3 ...and leaves them after close"] = after == {"-wal": True, "-shm": True}

    # P5: a commit that is only in the WAL. Keep the writer open so nothing
    # checkpoints it into the main file.
    w = sqlite3.connect(db, isolation_level=None)
    w.execute("PRAGMA wal_autocheckpoint=0")
    w.execute("INSERT INTO t VALUES (2)")
    r = _ro(db)
    out["P5 ro sees WAL-only commits"] = r.execute("SELECT COUNT(*) FROM t").fetchone()[0] == 2

    # P7 before P6 so the WAL still holds the uncheckpointed row.
    main_rows = None
    im = _ro(db, "&immutable=1")
    main_rows = im.execute("SELECT COUNT(*) FROM t").fetchone()[0]
    im.close()
    out["P7 immutable=1 misses WAL-only commits"] = main_rows == 1

    # P6: reader holds a snapshot; writer keeps writing and tries to truncate.
    r.execute("BEGIN")
    r.execute("SELECT COUNT(*) FROM t").fetchone()
    for i in range(200):
        w.execute("INSERT INTO t VALUES (?)", (i,))
    busy, _log, _ckpt = w.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
    pinned_size = os.path.getsize(db + "-wal")
    r.execute("COMMIT")
    r.close()
    busy2, _l2, _c2 = w.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
    freed_size = os.path.getsize(db + "-wal")
    w.close()
    out["P6 open ro snapshot pins the WAL"] = (busy == 1 and pinned_size > 0
                                               and busy2 == 0 and freed_size == 0)
    out["_detail"] = {"pinned_wal_bytes": pinned_size, "after_release_bytes": freed_size,
                      "truncate_busy_while_pinned": busy, "truncate_busy_after": busy2,
                      "immutable_saw_rows": main_rows}

    # P7's other half: immutable creates nothing. Fresh copy with no sidecars.
    db2 = os.path.join(root, "u.db")
    shutil.copyfile(db, db2)
    for s in ("-wal", "-shm"):
        if os.path.exists(db2 + s):
            os.remove(db2 + s)
    im = _ro(db2, "&immutable=1")
    im.execute("SELECT COUNT(*) FROM t").fetchone()
    im.close()
    out["P7b immutable=1 creates no sidecars"] = _side(db2) == {"-wal": False, "-shm": False}
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dir", help="scratch directory (default: a fresh temp dir)")
    a = ap.parse_args()
    root = a.dir or tempfile.mkdtemp(prefix="wal_ro_probe_")
    os.makedirs(root, exist_ok=True)
    res = probe(root)
    detail = res.pop("_detail")
    for k, v in res.items():
        print(f"  {k:45s} {v}")
    print(f"  detail {detail}")
    print(f"  sqlite {sqlite3.sqlite_version}, scratch {root}")
    if len(res) != 8:
        print(f"FAILED: expected 8 properties, measured {len(res)}")
        return 1
    return 0 if all(res.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
