"""f-17: how often does a player prop's MAIN LINE move between the first read and the close?

a-26 defines the Board's main line as the listed threshold whose median de-vigged over
probability across DraftKings, FanDuel and BetMGM is nearest 0.5 AT READ TIME, and grades a
lean at its last read before kickoff. If a row's identity includes the line, a line that
migrates between reads orphans the earlier lean. This measures how often that happens, on
the 2026 forward Odds API capture (source='live', venues oddsapi:<book>).

Scope, stated so it travels with the number: 2026 weeks 1-2 only (the capture window), the
two markets the model covers (player_receptions, player_rush_attempts), non-alternate markets,
three books. A read is a 10-minute bucket. KICKOFF IS NOT STORED for these events, so the
"close" is each key's LAST snapshot in the capture - a proxy for T-5, not a kickoff join.

Opens market_log.db with mode=ro and one short query.

    python -m research.f17_mainline_migration
"""
from __future__ import annotations

import collections
import datetime as dt
import sqlite3
import statistics

import config

BOOKS = ("oddsapi:draftkings", "oddsapi:fanduel", "oddsapi:betmgm")
MARKETS = {"player_receptions", "player_rush_attempts"}
START_TS = 1789000000  # 2026-09-10, before the first forward snapshot


def load() -> list[tuple]:
    con = sqlite3.connect(f"file:{config.DB_PATH}?mode=ro", uri=True)
    try:
        q = (f"select venue, market_id, line, side, mid, ts from quotes where venue in ({','.join('?' * len(BOOKS))})"
             " and market_type='prop' and ts > ?")
        return con.execute(q, (*BOOKS, START_TS)).fetchall()
    finally:
        con.close()


def main() -> None:
    rows = load()
    if not rows:
        raise SystemExit("no forward prop quotes read - refusing to report a rate on nothing")
    d = collections.defaultdict(lambda: collections.defaultdict(lambda: collections.defaultdict(lambda: collections.defaultdict(dict))))
    for venue, market_id, line, side, mid, ts in rows:
        parts = market_id.split("|")
        if len(parts) < 4 or parts[1] not in MARKETS or line is None or mid is None:
            continue
        d[(parts[0], parts[1], parts[2])][round(ts / 600)][venue][line][side.lower()] = mid
    out = collections.Counter()
    per = collections.defaultdict(collections.Counter)
    spans, events = [], set()
    for key, snaps in d.items():
        events.add(key[0])
        mains = []
        for b in sorted(snaps):
            cand = collections.defaultdict(list)
            for lines in snaps[b].values():
                for L, s in lines.items():
                    if "over" in s and "under" in s and s["over"] + s["under"] > 0:
                        cand[L].append(s["over"] / (s["over"] + s["under"]))
            if cand:
                mains.append((b, min(sorted(cand), key=lambda L: abs(statistics.median(cand[L]) - 0.5))))
        if len(mains) < 2:
            out["single_read"] += 1
            continue
        spans.append((mains[-1][0] - mains[0][0]) / 6)
        seq = [m for _, m in mains]
        k = "changed" if seq[0] != seq[-1] else ("wandered_back" if len(set(seq)) > 1 else "stable")
        out[k] += 1
        per[key[1]][k] += 1
    tot = out["changed"] + out["wandered_back"] + out["stable"]
    print(f"rows read {len(rows):,}; events {len(events)}; capture {dt.datetime.fromtimestamp(min(r[5] for r in rows), dt.UTC):%Y-%m-%d} "
          f"to {dt.datetime.fromtimestamp(max(r[5] for r in rows), dt.UTC):%Y-%m-%d}")
    print(f"player-market keys with >=2 reads: {tot}  {dict(out)}")
    for m, c in sorted(per.items()):
        t = sum(c.values())
        print(f"  {m}: n={t} first!=last {c['changed'] / t:.3f}  any change {(c['changed'] + c['wandered_back']) / t:.3f}")
    print(f"all: first!=last {out['changed'] / tot:.3f}  any change across reads {(out['changed'] + out['wandered_back']) / tot:.3f}")
    print(f"first-to-last read span, hours: median {statistics.median(spans):.1f}")


if __name__ == "__main__":
    main()
