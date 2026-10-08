"""f-29 target adapter: a-73's census claim (no interval - steps 2, 4, 5 do not apply).

    CLAIM  556 of 556 week-5 Kalshi REC/RSHATT markets carried an outcome before
           their own kickoff (read 2026-10-08 17:24Z); 388 props listed 17:15:38Z
           were mapped by the production logger 131 to 137 s later.

READ-ONLY: one short `mode=ro` session on the live store, nothing written.

    python research/f26_reliability/census_a73.py --db D:/calibrated-sports/data/market_log.db \
        --week 5 --asof 2026-10-08T17:24:00Z --out D:/temp/f29/a73.json

This is the ATTACKER'S OWN count, not a-73's script: the week is placed from the
ticker by this file's pattern and cross-checked against the week on the mapped
outcome, and three things a-73's `in_time` leans on are each measured:

  P1  `outcomes.created_ts` is the market's first-mapped time only if THIS market
      created the outcome. A book line or an earlier pass that created it first
      makes a late mapping read early. Detector: created_ts < the market's own
      first_seen (an outcome older than the market cannot have come from it).
  P2  `first_seen` is when the LOGGER discovered the market, not when Kalshi
      listed it. `markets.open_ts` is the venue's own open time. "Mapped 137 s
      after listing" is a claim about discovery unless the two agree.
  P3  "before its own kickoff" is vacuous while every kickoff is still ahead of
      the reading: any mapped market passes. Counted: how many of the week's
      kickoffs had passed at --asof, and at the time of this run.

--plant moves one market's kickoff to before its outcome and one outcome to
before its market, in memory, and the script must report both.
"""
import argparse
import datetime as dt
import json
import re
import sqlite3
import sys
import time
from collections import Counter

TICKER = re.compile(r"^(KXNFLREC|KXNFLRSHATT)-(\d\d)([A-Z]{3})(\d\d)([A-Z]+)-")
MON = dict(JAN=1, FEB=2, MAR=3, APR=4, MAY=5, JUN=6, JUL=7, AUG=8, SEP=9, OCT=10, NOV=11, DEC=12)
ALT = {"LA": ("LA", "LAR"), "JAX": ("JAX", "JAC"), "WAS": ("WAS", "WSH"), "LV": ("LV", "LVR")}


def iso(ts):
    return dt.datetime.fromtimestamp(ts, dt.timezone.utc).strftime("%m-%d %H:%M:%SZ") if ts else "-"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--week", type=int, default=5)
    ap.add_argument("--season", type=int, default=2026)
    ap.add_argument("--asof", default="2026-10-08T17:24:00Z")
    ap.add_argument("--listing", default="2026-10-08T17:15:38Z")
    ap.add_argument("--out", required=True)
    ap.add_argument("--plant", action="store_true")
    a = ap.parse_args()
    asof = dt.datetime.fromisoformat(a.asof.replace("Z", "+00:00")).timestamp()
    listing = dt.datetime.fromisoformat(a.listing.replace("Z", "+00:00")).timestamp()
    now = time.time()
    con = sqlite3.connect("file:" + a.db.replace("\\", "/") + "?mode=ro", uri=True, timeout=5)
    try:
        sched = {}
        for gid, week, day, home, away, kick in con.execute(
                "SELECT game_id, week, gameday, home_team, away_team, MAX(kickoff_ts) FROM nfl_games "
                "WHERE sport='nfl' AND season=? AND kickoff_ts IS NOT NULL GROUP BY game_id", (a.season,)):
            for x in ALT.get(away, (away,)):
                for y in ALT.get(home, (home,)):
                    sched[(day, x + y)] = (gid, week, kick)
        rows = con.execute(
            "SELECT m.market_id, m.open_ts, m.first_seen, mo.market_id IS NOT NULL, mo.outcome_id, mo.mapped_ts, "
            "mo.method, o.created_ts, o.week, o.season FROM markets m "
            "LEFT JOIN market_outcome mo ON mo.venue=m.venue AND mo.market_id=m.market_id "
            "LEFT JOIN outcomes o ON o.outcome_id=mo.outcome_id "
            "WHERE m.venue='kalshi' AND (m.market_id LIKE 'KXNFLREC-%' OR m.market_id LIKE 'KXNFLRSHATT-%')").fetchall()
        links = Counter(r[0] for r in con.execute(
            "SELECT outcome_id FROM market_outcome WHERE outcome_id IS NOT NULL AND venue != 'kalshi'"))
        klinks = Counter(r[0] for r in con.execute(
            "SELECT outcome_id FROM market_outcome WHERE outcome_id IS NOT NULL AND venue = 'kalshi'"))
    finally:
        con.close()
    out = lambda s="": print(s, flush=True)  # noqa: E731
    out("read %d Kalshi REC/RSHATT markets at %s (now); a-73 read at %s" % (len(rows), iso(now), iso(asof)))
    if not rows:
        raise SystemExit("0 markets read - refusing to report a census of nothing")
    wk, unplaced, week_disagrees = [], 0, 0
    for mid, open_ts, seen, has_row, oid, mapped_ts, method, created, oweek, oseason in rows:
        m = TICKER.match(mid)
        hit = m and sched.get(("20%s-%02d-%s" % (m.group(2), MON.get(m.group(3), 0), m.group(4)), m.group(5)))
        if not hit:
            unplaced += 1
            continue
        if oid and (oweek != hit[1] or oseason != a.season):
            week_disagrees += 1
        if hit[1] == a.week:
            wk.append(dict(mid=mid, open=open_ts, seen=seen, row=bool(has_row), oid=oid, mapped=mapped_ts,
                           method=method, created=created, kick=hit[2], game=hit[0]))
    if a.plant:
        mapped_rows = [r for r in wk if r["oid"]]
        mapped_rows[0]["kick"] = mapped_rows[0]["created"] - 60          # a late mapping
        mapped_rows[1]["created"] = mapped_rows[1]["seen"] - 3600        # an outcome older than its market
    out("unplaced tickers %d of %d; ticker week != outcome week on %d mapped markets" % (unplaced, len(rows), week_disagrees))

    def census(pop, label):
        n = len(pop)
        mapped = [r for r in pop if r["oid"]]
        in_time = [r for r in mapped if r["created"] is not None and r["created"] < r["kick"]]
        late = [r for r in mapped if not (r["created"] is not None and r["created"] < r["kick"])]
        out("%-34s listed %4d  unexamined %3d  refused %3d  mapped %4d  outcome before kickoff %4d  late %d"
            % (label, n, sum(not r["row"] for r in pop), sum(r["row"] and not r["oid"] for r in pop),
               len(mapped), len(in_time), len(late)))
        return {"listed": n, "mapped": len(mapped), "in_time": len(in_time), "late": [r["mid"] for r in late]}

    R = {"now": now, "asof": asof, "week": a.week, "planted": a.plant}
    then = [r for r in wk if r["seen"] <= asof]
    R["as_of_claim"] = census(then, "week %d, first seen by %s" % (a.week, iso(asof)[6:]))
    R["now_all"] = census(wk, "week %d, everything listed now" % a.week)
    after = [r for r in wk if r["seen"] > asof]
    R["listed_after_claim"] = census(after, "  of which first seen after") if after else {"listed": 0}

    out("\nP1  is outcomes.created_ts this market's own first mapping?")
    mapped = [r for r in wk if r["oid"]]
    older = [r for r in mapped if r["created"] < r["seen"]]
    shared_k = [r for r in mapped if klinks[r["oid"]] > 1]
    linked_other = [r for r in mapped if links[r["oid"]] > 0]
    out("    outcome created BEFORE the market was first seen: %d of %d mapped" % (len(older), len(mapped)))
    out("    outcome shared with another Kalshi market: %d; outcome also linked by a non-Kalshi venue: %d"
        % (len(shared_k), len(linked_other)))
    gap = [r["mapped"] - r["created"] for r in mapped if r["mapped"] is not None]
    out("    market_outcome.mapped_ts - outcomes.created_ts: min %.0f s, median %.0f s, max %.0f s (mapped_ts is "
        "rewritten by a full pass, so it bounds the first mapping from ABOVE)" % (min(gap), sorted(gap)[len(gap) // 2], max(gap)))
    late_by_mapped = [r for r in mapped if r["mapped"] is not None and r["mapped"] >= r["kick"]]
    out("    markets whose LATEST mapping write is at or after kickoff: %d (0 means in-time holds on the upper bound too)"
        % len(late_by_mapped))
    R["p1"] = {"mapped": len(mapped), "outcome_older_than_market": len(older), "shared_kalshi": len(shared_k),
               "linked_by_other_venue": len(linked_other), "latest_mapping_after_kickoff": len(late_by_mapped),
               "older_examples": [r["mid"] for r in older[:5]]}

    out("\nP2  the %s listing: discovery against the venue's own open time" % iso(listing)[6:])
    batch = [r for r in wk if abs(r["seen"] - listing) < 5]
    R["p2"] = {"batch": len(batch)}
    if batch:
        lag = sorted(r["created"] - r["seen"] for r in batch if r["oid"])
        disc = sorted(r["seen"] - r["open"] for r in batch if r["open"])
        out("    %d markets first seen within 5 s of it; %d mapped; created - first_seen %.0f to %.0f s"
            % (len(batch), len(lag), lag[0], lag[-1]))
        out("    first_seen - Kalshi open_ts: min %.0f s, median %.0f s, max %.0f s (n %d); created - open_ts max %.0f s"
            % (disc[0], disc[len(disc) // 2], disc[-1], len(disc),
               max(r["created"] - r["open"] for r in batch if r["oid"] and r["open"])))
        out("    methods on the batch: %s" % dict(Counter(r["method"] for r in batch)))
        R["p2"].update(mapped=len(lag), lag_min=lag[0], lag_max=lag[-1], discovery_min=disc[0],
                       discovery_median=disc[len(disc) // 2], discovery_max=disc[-1])
    else:
        out("    NO market first seen within 5 s of the stated listing time")
    alld = sorted(r["seen"] - r["open"] for r in wk if r["open"])
    out("    whole week: first_seen - open_ts min %.0f s, median %.0f s, p90 %.0f s, max %.0f s"
        % (alld[0], alld[len(alld) // 2], alld[int(len(alld) * .9)], alld[-1]))
    R["p2"]["week_discovery_p90"] = alld[int(len(alld) * .9)]

    out("\nP3  could 'before its own kickoff' have failed at the reading?")
    kicks = sorted({(r["kick"], r["game"]) for r in wk})
    out("    week-%d games carrying a listed prop: %d; kicked off by the a-73 reading: %d; by now: %d; first kickoff %s"
        % (a.week, len(kicks), sum(k <= asof for k, _ in kicks), sum(k <= now for k, _ in kicks), iso(kicks[0][0])))
    per = Counter(r["game"] for r in wk)
    out("    listed props per game: %s" % dict(sorted(per.items())))
    R["p3"] = {"games": len(kicks), "kicked_at_claim": sum(k <= asof for k, _ in kicks),
               "kicked_now": sum(k <= now for k, _ in kicks), "first_kickoff": kicks[0][0], "per_game": dict(per)}
    json.dump(R, open(a.out, "w"), indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
