"""f-31: a-74's uptime figure, attacked. A census (no interval): steps 2, 4, 5 of the checklist do
not apply and the through-the-target blocks test is NOT RUN.

    F26_NFL_STORE=<market_log.db> python research/f26_reliability/uptime_a74.py --src <a-74 worktree>

The store is opened HERE, mode=ro, for one read of poll_log (four columns), and closed before
anything is computed. a-74's own `find_gaps` / `uptime` are then called on those rows in memory,
so its pipeline is exercised without its ~60 s market_depth read.

  1  re-count: gaps over 300 s between consecutive ok=1 polls, by THIS file's loop, against a-74's
     functions on the same rows and against the figures a-74 reported (99.43%, 10 gaps, 84.4, 83.8)
  2  the two outages on record (a-65 37.7 min from 2026-10-04 14:50Z; a-67 83.8 min from
     2026-10-06 03:24Z) must each be a gap; a planted outage must appear and a planted clean
     record must not show one
  3  WHAT "UP" DOES NOT MEAN. The rule is any venue, any endpoint. Measured: with the same 300 s
     rule applied to ONE venue's successful polls, how long was that venue silent while the
     published figure read "up"? And planted: one venue silent for three hours while another
     polls must (by the rule as written) read 100% up.
"""
import argparse
import os
import sqlite3
import sys
from datetime import datetime, timezone

OUTAGES = {"a-65": (datetime(2026, 10, 4, 14, 50, tzinfo=timezone.utc).timestamp(), 37.7),
           "a-67": (datetime(2026, 10, 6, 3, 24, tzinfo=timezone.utc).timestamp(), 83.8)}
REPORTED = {"up_share": 0.9943, "gaps": 10, "longest": 84.4}


def iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")


def my_gaps(ts, as_of, thr=300):
    out = [(a, b) for a, b in zip(ts, ts[1:]) if b - a > thr]
    if ts and as_of - ts[-1] > thr:
        out.append((ts[-1], as_of))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True)
    ap.add_argument("--as-of", type=float, help="unix ts; default the last poll on record (a closed read)")
    a = ap.parse_args()
    store = os.path.abspath(os.environ["F26_NFL_STORE"])
    con = sqlite3.connect("file:%s?mode=ro" % store.replace("\\", "/"), uri=True, timeout=30)
    try:
        polls = con.execute("SELECT ts, venue, endpoint, ok FROM poll_log ORDER BY ts").fetchall()
    finally:
        con.close()
    polls = [(ts, v, e or "(none)", bool(ok)) for ts, v, e, ok in polls]
    as_of = a.as_of or polls[-1][0]
    polls = [p for p in polls if p[0] <= as_of]
    ok_ts = [p[0] for p in polls if p[3]]
    print("poll_log: %d rows, %d ok, first %s, read closes %s" % (len(polls), len(ok_ts), iso(polls[0][0]), iso(as_of)))

    os.environ.setdefault("LOGGER_DB", store)
    sys.path.insert(0, os.path.abspath(a.src))
    from jobs import logger_activity as LA
    assert os.path.abspath(LA.__file__).startswith(os.path.abspath(a.src)), LA.__file__
    bad = 0

    print("\n1. re-count")
    mine = my_gaps(ok_ts, as_of)
    theirs = LA.find_gaps(ok_ts, as_of)
    span = as_of - ok_ts[0]
    down = sum(b - x for x, b in mine)
    tot = LA.window_uptime(theirs, ok_ts[0], as_of)
    print("  this file: %d gaps over 300 s, %.1f min down of %.0f, up share %.4f, longest %.1f min"
          % (len(mine), down / 60, span / 60, 1 - down / span, max(b - x for x, b in mine) / 60))
    print("  a-74's find_gaps/window_uptime on the same rows: %d gaps, up share %s, longest %s min"
          % (len(theirs), tot["up_share"], tot["longest_gap_min"]))
    same = len(mine) == len(theirs) and abs((1 - down / span) - tot["up_share"]) < 1e-5
    bad += not same
    print("  the two agree: %s" % same)
    print("  a-74 REPORTED at 2026-10-08 21:08Z: %.2f%% up, %d gaps, longest %.1f. Today's read: %.2f%%, %d gaps, longest %.1f"
          % (100 * REPORTED["up_share"], REPORTED["gaps"], REPORTED["longest"], 100 * (1 - down / span), len(mine),
             max(b - x for x, b in mine) / 60))
    for x, b in mine:
        print("    gap %s -> %s  %6.1f min%s" % (iso(x), iso(b), (b - x) / 60, "  (cadence floor, <= 330 s)" if b - x <= 330 else ""))

    print("\n2. the outages on record, and plants")
    for unit, (start, minutes) in OUTAGES.items():
        hit = [(x, b) for x, b in mine if x <= start + 120 and b >= start + 60 * minutes - 120]
        near = [(x, b) for x, b in mine if abs(x - start) < 600]
        bad += not near
        print("  %s (%s, %.1f min): a gap starts within 10 min of it: %s %s" % (
            unit, iso(start), minutes, bool(near),
            ", ".join("%s %.1f min" % (iso(x), (b - x) / 60) for x, b in near)))
        if near and abs((near[0][1] - near[0][0]) / 60 - minutes) > 1.0:
            print("     LENGTH DIFFERS from the %.1f min on record by more than a minute" % minutes)
        del hit
    cut0 = ok_ts[len(ok_ts) // 2]
    planted = [t for t in ok_ts if not (cut0 <= t < cut0 + 3600)]
    g_pl = LA.find_gaps(planted, as_of)
    fired = any(g["start"] < cut0 + 1 and g["end"] >= cut0 + 3600 for g in g_pl)
    bad += not fired
    print("  PLANT one hour of successful polls removed at %s: a-74 finds it: %s (%d gaps, was %d)" % (iso(cut0), fired, len(g_pl), len(theirs)))
    dense = [ok_ts[0] + 60 * i for i in range(int(span // 60) + 1)]
    g_cl = LA.find_gaps(dense, dense[-1])
    bad += bool(g_cl)
    print("  PLANT a poll every 60 s for the whole window: gaps %d (must be 0)" % len(g_cl))
    failing = [(t, v, e, False) for t, v, e, _ok in polls if cut0 <= t < cut0 + 3600] + [p for p in polls if not (cut0 <= p[0] < cut0 + 3600)]
    failing.sort()
    up_f = LA.uptime(failing, [], as_of)
    fired = any(abs(g["minutes"] - 60) < 6 for g in up_f["gaps"])
    bad += not fired
    print("  PLANT the same hour with every poll present but ok=0 (a LIVE process recording failures): reads as a gap: %s" % fired)

    print("\n3. what 'up' does not mean: the same 300 s rule on one venue's successful polls")
    venues = sorted({p[1] for p in polls})
    any_gap = mine

    def overlap(x, b):
        return sum(max(0.0, min(b, gb) - max(x, ga)) for ga, gb in any_gap)
    for v in venues:
        vt = [p[0] for p in polls if p[3] and p[1] == v]
        if len(vt) < 2:
            print("  %-12s %d successful polls - not read" % (v, len(vt)))
            continue
        for thr in (300, 900, 3600):
            g = my_gaps(vt, as_of, thr)
            silent = sum(b - x for x, b in g)
            hidden = silent - sum(overlap(x, b) for x, b in g)
            print("  %-12s silent > %4d s: %4d spans, %8.1f min; of that %8.1f min while the published figure reads UP (longest such span %.1f min)"
                  % (v, thr, len(g), silent / 60, hidden / 60, max([(b - x) / 60 for x, b in g if overlap(x, b) < 1] or [0.0])))
    # the planted version: venue A silent 3 h, venue B polling every 60 s
    t0 = 1_800_000_000.0
    toy = [(t0 + 60 * i, "B", "quotes:hot", True) for i in range(600)] + \
          [(t0 + 60 * i, "A", "quotes:hot", True) for i in range(600) if not (120 <= i < 300)]
    toy.sort()
    up_t = LA.uptime(toy, [], t0 + 60 * 599)
    print("  PLANT venue A silent for 180 min while venue B polls every 60 s: a-74 up_share %s, gaps %d"
          % (up_t["total"]["up_share"], len(up_t["gaps"])))
    print("     -> by the rule as written this reads fully up. It is the rule, not a bug; it is why the figure is not a capture figure.")
    print("\n4. the wording that travels with the figure (DEFINITIONS['uptime'], exported in the file):")
    print("     " + LA.DEFINITIONS["uptime"])
    print("   the one-line summary a log reader sees: " + LA.summary.__doc__ if LA.summary.__doc__ else "   summary() prints 'up 99.xx% of N min; ...' with no definition beside it")
    print("\nchecks that came out the wrong way: %d" % bad)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
