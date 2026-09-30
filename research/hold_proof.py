"""Prove a retention hold keeps rows the prune would otherwise delete (a-60).

    python -m research.hold_proof D:/temp/a60/prune_copy.db --days 0.5

A retention mechanism nobody has exercised is not a retention mechanism. This
copies, READ-ONLY from the logger's store, the whole `quote_retention_hold`
table plus every quote row of three groups of markets into a scratch file, then
runs the REAL `jobs.prune_quotes.run` against the scratch file with a window
short enough that every copied row is past it:

  weeks-2-3  the 2026 week-2/3 Kalshi REC/RSHATT/GAME/SPREAD/TOTAL markets and
             Polymarket game-slug markets that `jobs.hold_weeks` holds
  week-4     the same selection one week later: the export holds some of its
             Kalshi markets on a rolling window and nothing holds the rest

Every group is reported split by the hold state it has in the copied table, so
the check is on hold state, not on the label. `--every K` copies every K-th
market of each group (default 5) to keep the copy small; the sample is
deterministic (sorted ids).

The claim is two-sided, so the check is too: held rows must ALL survive and
unheld live rows past the window must ALL go. A result where both groups
survive (the prune never ran) or both go (the hold is ignored) fails loudly.

The live store is opened `mode=ro` only. `prune_quotes.run` writes a
`source_health` row; LOGGER_DB is pinned to the scratch file BEFORE `config`
is imported, so that write lands in the copy.
"""
import argparse
import os
import sqlite3
import sys
import time

LIVE = os.environ.get("LOGGER_DB", "D:/calibrated-sports/data/market_log.db")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("dest")
    ap.add_argument("--days", type=float, default=0.5)
    ap.add_argument("--live", default=LIVE)
    ap.add_argument("--every", type=int, default=5)
    a = ap.parse_args()
    if os.path.exists(a.dest):
        raise SystemExit(f"refusing to overwrite {a.dest}; remove it first")
    live = os.path.abspath(a.live).replace("\\", "/")
    dest = os.path.abspath(a.dest)
    os.environ["LOGGER_DB"] = dest            # before config is imported
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    import config
    import store
    from jobs import hold_weeks, prune_quotes
    assert os.path.abspath(config.DB_PATH) == dest, (config.DB_PATH, dest)
    store.init_db()

    ro = sqlite3.connect(f"file:{live}?mode=ro", uri=True, timeout=30)
    groups = {}
    for w in (2, 3, 4):
        g = "week-4" if w == 4 else "weeks-2-3"
        for (venue, kind), ids in hold_weeks.select(ro, 2026, w).items():
            groups.setdefault(g, set()).update((venue, m) for m in ids)
    groups = {g: set(sorted(p)[::a.every]) for g, p in groups.items()}
    holds = ro.execute("SELECT venue, market_id, until_ts, reason, held_ts "
                       "FROM quote_retention_hold").fetchall()
    cols = [r[1] for r in ro.execute("PRAGMA table_info(quotes)")]
    out = sqlite3.connect(dest)
    out.executemany("INSERT INTO quote_retention_hold VALUES (?,?,?,?,?)", holds)
    ph = ",".join("?" * len(cols))
    t0 = time.time()
    for g, pairs in groups.items():
        for venue, m in sorted(pairs):
            rows = ro.execute(f"SELECT {','.join(cols)} FROM quotes "
                              "WHERE venue = ? AND market_id = ?", (venue, m)).fetchall()
            out.executemany(f"INSERT INTO quotes ({','.join(cols)}) VALUES ({ph})", rows)
    out.commit()
    ro.close()
    print(f"copied {len(holds):,} holds and the quotes of "
          f"{sum(len(p) for p in groups.values()):,} markets in {time.time() - t0:.0f}s")

    now = time.time()
    cutoff = prune_quotes.cutoff_ts(a.days)

    def census(label):
        res = {}
        for g, pairs in sorted(groups.items()):
            for held in (True, False):
                n = past = 0
                for venue, m in pairs:
                    h = out.execute("SELECT until_ts FROM quote_retention_hold "
                                    "WHERE venue = ? AND market_id = ?", (venue, m)).fetchone()
                    if bool(h and h[0] > now) != held:
                        continue
                    r = out.execute(
                        "SELECT COUNT(*), SUM(source = 'live' AND COALESCE(ingest_ts, ts) < ?) "
                        "FROM quotes WHERE venue = ? AND market_id = ?", (cutoff, venue, m)).fetchone()
                    n += r[0]
                    past += r[1] or 0
                res[(g, held)] = (n, past)
                print(f"  {label:6s} {g:8s} {'held' if held else 'unheld':6s} "
                      f"rows {n:9,}  live rows past the window {past:9,}")
        return res

    print(f"window {a.days:g}d -> cutoff "
          f"{time.strftime('%Y-%m-%d %H:%MZ', time.gmtime(cutoff))}")
    before = census("before")
    stats = prune_quotes.run(days=a.days)
    print(f"prune_quotes.run: deleted {stats['deleted']:,}, held {stats['held']:,}, "
          f"candidates {stats['candidates']:,}")
    after = census("after")

    fails = []
    for key, (n0, past0) in before.items():
        n1, past1 = after[key]
        g, held = key
        if held and n1 != n0:
            fails.append(f"{g} held: {n0 - n1:,} rows deleted")
        if not held and past1 != 0:
            fails.append(f"{g} unheld: {past1:,} rows past the window survived")
    if not any(past for (g, held), (n, past) in before.items() if held):
        fails.append("no held row was past the window - the test cannot discriminate")
    if not any(past for (g, held), (n, past) in before.items() if not held):
        fails.append("no unheld row was past the window - the test cannot discriminate")
    if stats["deleted"] == 0:
        fails.append("the prune deleted nothing")
    out.close()
    if fails:
        print("FAIL: " + "; ".join(fails))
        raise SystemExit(1)
    print("PASS: every held row survived; every unheld live row past the window was deleted")


if __name__ == "__main__":
    main()
