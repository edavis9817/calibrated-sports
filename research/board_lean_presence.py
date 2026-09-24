"""Is every published lean on the board? (unit a-34)

    python -m research.board_lean_presence --replay --season 2026 --week 2 --dest D:/temp/a34/wk02
    python -m research.board_lean_presence --audit --dest D:/temp/a34/wk02

`--replay` re-runs the Board at its real cadence over a past (or in-progress)
week: a tick every config.BOARD_TICK_MIN minutes from the week's window opening,
reading whenever `jobs.board_read.due_reads` says the week is due, up to `--until`
(default: now) or 36h after the week's last kickoff. Read-only on the store
(`board_read.run` opens it `mode=ro`); it writes only the scratch `--dest`, which
must not already hold a Board tree.

`--audit` asks the question b-36 asked of a-26's files. For every lean the ledger
says was published for a week, is there a row in that week's LATEST read that
carries it - same `row_id` (claim + line) and the same `lean` side? A lean with no
such row is ABSENT, classified by why:

    line_moved    the claim is on the read at a different line
    lean_changed  the row is there at the lean's line, but leans another way or not at all
    claim_gone    no row for the claim at all

and the count identity the Board promises is checked on the rows themselves:
published leans for the week = rows carrying a lean in each of graded / upcoming /
live / void, with no remainder. Exits non-zero when any lean is absent or the
identity fails, so a clean audit is a result and not an absence of one.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
import time
from collections import Counter

import config
from core import board as B
from jobs import board_read as J

ROW_STATE = {B.UPCOMING: B.S_UPCOMING, B.LIVE: B.S_LIVE, B.VOID: B.S_VOID,
             B.CLEARED: B.S_GRADED, B.MISSED: B.S_GRADED, B.PUSH: B.S_GRADED}


def snapshot_times(season, week):
    """Every instant at which a benchmark book's prop snapshot for this week landed."""
    con = J.ro()
    try:
        games = J.week_games(con, season, week)
        events = J.oddsapi_events(con, games)
        out = set()
        for book in config.BOARD_BENCH_BOOKS:
            for eid in events:
                out.update(t for (t,) in con.execute(
                    "SELECT DISTINCT ts FROM quotes WHERE venue=? AND event_id=? "
                    "AND market_type='prop' AND source='live'", (f"oddsapi:{book}", eid)))
    finally:
        con.close()
    return sorted(out)


def replay(season, week, dest, until_ts=None, changes_only=False, log=print):
    """`changes_only`: of the ticks the cadence makes due, read only those with a
    benchmark-book snapshot or a kickoff since the last read (and the last one).
    Between those instants a read's lines, prices and statuses cannot differ from
    the previous read's, so no lean can move or vanish at a skipped tick; what
    can differ is kalshi_mid and each row's line_path tail, which this audit
    does not read. It exists because each read loads every quote up to its own
    instant, so a full-cadence week is ~180 reads at 11-30 s each."""
    if os.path.exists(J.ledger_path(dest)):
        raise SystemExit(f"{dest} already holds a Board tree - replay into an empty scratch dir")
    con = J.ro()
    try:
        games = J.week_games(con, season, week)
    finally:
        con.close()
    if not games:
        raise SystemExit(f"no schedule for {season} wk{week}")
    t = J.window_open_ts(games)
    last_kick = max(g["kickoff_ts"] for g in games.values())
    end = min(until_ts or time.time(), last_kick + 36 * 3600)
    step = config.BOARD_TICK_MIN * 60
    marks = sorted(set(snapshot_times(season, week) if changes_only else [])
                   | {g["kickoff_ts"] for g in games.values()})
    last_read = None
    reads, empty, skipped, started = 0, 0, 0, time.time()
    while t <= end:
        due = [w for w, _why in J.due_reads(season, t, dest) if w == week]
        if due and changes_only and last_read is not None and t + step <= end and not any(
                last_read < m <= t for m in marks):
            skipped += 1
            due = []
        if due:
            last_read = t
            try:
                J.run(season, week, dest, read_ts=t, log=lambda s: None)
                reads += 1
            except J.NoRows:
                empty += 1
        t += step
    log(json.dumps({"season": season, "week": week, "reads": reads, "no_rows": empty,
                    "skipped_unchanged": skipped, "changes_only": changes_only,
                    "seconds": round(time.time() - started)}))
    if reads == 0:
        raise SystemExit("replay wrote ZERO reads - nothing to audit")
    return reads


def audit_week(ledger, rows, season, week):
    """-> dict. `ledger` is every ledger event; `rows` the latest read's rows."""
    pubs = [e for e in ledger if e["event"] == "published"
            and e["season"] == season and e["week"] == week]
    by_row = {r["row_id"]: r for r in rows}
    claims = {}
    for r in rows:
        claims.setdefault(r["claim_id"], []).append(r)
    absent = Counter()
    examples = {}
    moved = []
    for p in pubs:
        r = by_row.get(p["row_id"])
        if r is not None and r.get("lean") == p["side"]:
            if r.get("line_moved_after_publication"):
                moved.append(p["row_id"])
            continue
        why = ("lean_changed" if r is not None else
               "line_moved" if p["claim_id"] in claims else "claim_gone")
        absent[why] += 1
        examples.setdefault(why, f"{p['row_id']} {p['side']}")
    leaning = [r for r in rows if r.get("lean")]
    by_state = Counter(ROW_STATE[r["status"]] for r in leaning)
    return {"published": len(pubs), "absent": sum(absent.values()), "absent_by_reason": dict(absent),
            "absent_example": examples, "rows_with_lean": len(leaning),
            "rows_with_lean_by_state": dict(by_state),
            "line_moved_after_publication": len(moved),
            "identity_holds": len(pubs) == sum(by_state.values()) and not absent}


def audit(dest, log=print):
    ledger = J.read_ledger(dest)
    if not ledger:
        raise SystemExit(f"no ledger at {J.ledger_path(dest)}")
    out = []
    for idx_path in sorted(glob.glob(os.path.join(dest, "board", "nfl", "*", "wk*", "index.json"))):
        idx = json.load(open(idx_path))
        doc = json.load(open(os.path.join(os.path.dirname(idx_path), J.read_name(idx["latest"]))))
        res = audit_week(ledger, doc["rows"], idx["season"], idx["week"])
        res.update(season=idx["season"], week=idx["week"], reads=len(idx["reads"]),
                   latest=idx["latest"], index_leans=idx["leans"])
        out.append(res)
        log(json.dumps(res))
    if not out:
        raise SystemExit(f"no Board week under {dest}")
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--replay", action="store_true")
    ap.add_argument("--audit", action="store_true")
    ap.add_argument("--season", type=int)
    ap.add_argument("--week", type=int)
    ap.add_argument("--until")
    ap.add_argument("--changes-only", action="store_true")
    ap.add_argument("--dest", required=True)
    a = ap.parse_args(argv)
    if a.replay:
        replay(a.season, a.week, a.dest, J.parse_iso(a.until) if a.until else None, a.changes_only)
    if a.audit or a.replay:
        res = audit(a.dest)
        if not all(r["identity_holds"] for r in res):
            sys.exit(1)


if __name__ == "__main__":
    main()
