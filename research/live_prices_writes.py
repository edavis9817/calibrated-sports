"""How many R2 writes would the live-prices publisher have made? (unit a-09)

    python -m research.live_prices_writes [--every 15] [--heartbeat 300]

Replays `jobs.publish_live_prices.Publisher.due()` - the real policy object, not
a transcription of it - over the logger's own Kalshi poll history in `poll_log`
(read-only). Each successful quotes poll on a tier that carries game-winner
markets (game, hot, live, cold) counts as a newer read.

AN UPPER BOUND, and says why: `poll_log` records how many markets a poll
covered, not which, so a poll is assumed to include a game-winner market. For
hot/live/game that is true whenever the game in question has a game-winner
market; for cold it is true whenever any game is more than 24 h out. The
futures tier never carries one and is excluded.

Per UTC day. The ceiling is 86400 / every regardless of any history.
"""
import argparse
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timezone

import config
from jobs import publish_live_prices as L

TIERS = ("quotes:game", "quotes:hot", "quotes:live", "quotes:cold")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--every", type=float, default=15)
    ap.add_argument("--heartbeat", type=float, default=300)
    a = ap.parse_args(argv)

    con = sqlite3.connect(f"file:{config.DB_PATH}?mode=ro", uri=True)
    try:
        polls = [r[0] for r in con.execute(
            f"SELECT ts FROM poll_log WHERE venue='kalshi' AND ok=1 AND n_markets>0 "
            f"AND endpoint IN ({','.join('?' * len(TIERS))}) ORDER BY ts", TIERS)]
    finally:
        con.close()
    if not polls:
        print("REFUSING: no kalshi quote polls in poll_log", file=sys.stderr)
        return 1

    class Book:                      # the only thing due() asks of a book
        newest = 0.0

        def newest_read(self):
            return self.newest

    book = Book()
    pub = L.Publisher(book, client=object(), bucket="x", sport="x",
                      every_s=a.every, heartbeat_s=a.heartbeat)
    start = polls[0] - polls[0] % 86400
    end = polls[-1]
    writes, reasons = defaultdict(int), defaultdict(lambda: defaultdict(int))
    i, t = 0, start
    while t <= end:
        while i < len(polls) and polls[i] <= t:
            book.newest = polls[i]
            i += 1
        why = pub.due(t)
        if why:
            day = datetime.fromtimestamp(t, tz=timezone.utc).strftime("%Y-%m-%d %a")
            writes[day] += 1
            reasons[day][why] += 1
            pub.last_write_ts = t
            pub.last_newest_read = book.newest
        t += 1.0                     # the logger's worker ticks at LOOP_TICK = 1 s

    print(f"policy: every {a.every:g}s, heartbeat {a.heartbeat:g}s; ceiling "
          f"{86400 / a.every:,.0f}/day; {len(polls):,} polls "
          f"{datetime.fromtimestamp(polls[0], tz=timezone.utc):%Y-%m-%d %H:%M} to "
          f"{datetime.fromtimestamp(polls[-1], tz=timezone.utc):%Y-%m-%d %H:%M} UTC")
    days = sorted(writes)
    # The first and last days are partial; they are printed and marked, not dropped.
    for n, day in enumerate(days):
        mark = "  (partial day)" if n in (0, len(days) - 1) else ""
        r = reasons[day]
        print(f"  {day}  {writes[day]:5d} writes  (newer read {r['newer read']:5d}, "
              f"heartbeat {r['heartbeat']:4d}){mark}")
    full = [writes[d] for d in days[1:-1]]
    if full:
        print(f"full days: n={len(full)}, min {min(full)}, max {max(full)}, "
              f"sum {sum(full)}, mean {sum(full) / len(full):.0f}/day")
    return 0


if __name__ == "__main__":
    sys.exit(main())
