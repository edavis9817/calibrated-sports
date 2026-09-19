"""P1: which markets the books actually list on a college game, per book, per game.

    python -m research.cfb_p1_markets

0 requests. Reads what `jobs/ingest_cfb --odds-p1` bought on 2026-09-19 (74 credits,
1 per event, the approved one-off) out of `cfb_odds_event_markets`.

WHY IT WAS BOUGHT. The free endpoints can say which GAMES are listed and nothing about
which MARKETS, so "coverage is partial" could be neither confirmed nor refuted without
spending. 74 credits answers it for a whole slate, against the 4,475-19,070 an options
B or C pull would cost - which is why P1 came first.

The endpoint's own caveat, quoted: it "only returns recently seen market keys for each
bookmaker - it is not a comprehensive list", and returns more as kickoff approaches.
Every count here is therefore a LOWER bound on what exists and an upper bound on what
was visible ~3 hours before the first kickoff.
"""
import os
import sqlite3
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cfb import paths

PLAYER = "player_"


def rows(con):
    return con.execute(
        "SELECT event_id, bookmaker, market_key, commence_ts FROM cfb_odds_event_markets"
    ).fetchall()


def main(argv=None):
    con = sqlite3.connect(f"file:{paths.db_path()}?mode=ro", uri=True)
    data = rows(con)
    if not data:
        raise SystemExit("no P1 rows: run jobs.ingest_cfb --odds-p1 (74 credits, approved once)")
    events = {r[0] for r in data}
    books = {r[1] for r in data}
    keys = {r[2] for r in data}
    player_keys = {k for k in keys if k.startswith(PLAYER)}
    print(f"P1: {len(events)} events, {len(books)} books, {len(keys)} distinct market keys")
    print(f"     player-prop keys: {len(player_keys)}")

    per_event = defaultdict(set)
    per_book = defaultdict(set)
    book_player = Counter()
    for eid, book, key, _ts in data:
        per_event[eid].add(key)
        per_book[book].add(key)
        if key.startswith(PLAYER):
            book_player[book] += 1
    with_player = [e for e, ks in per_event.items() if any(k.startswith(PLAYER) for k in ks)]
    print(f"     events listing ANY player prop: {len(with_player)} of {len(events)}")

    if player_keys:
        print("\nplayer-prop keys by events listing them:")
        c = Counter(k for _e, _b, k, _t in data if k.startswith(PLAYER))
        ev = defaultdict(set)
        for e, _b, k, _t in data:
            if k.startswith(PLAYER):
                ev[k].add(e)
        for k in sorted(ev, key=lambda k: -len(ev[k])):
            print(f"  {k:36} events {len(ev[k]):>3}  book-rows {c[k]:>4}")

    print("\nbooks, by how many market keys each lists:")
    for book in sorted(per_book, key=lambda b: -len(per_book[b])):
        print(f"  {book:16} keys {len(per_book[book]):>3}  player-prop rows {book_player[book]:>4}")

    print("\nmarkets per event: min %d, median %d, max %d" % (
        min(len(v) for v in per_event.values()),
        sorted(len(v) for v in per_event.values())[len(per_event) // 2],
        max(len(v) for v in per_event.values())))

    # What a pull would cost, using the docs' formula on what P1 measured.
    n = len(events)
    print(f"\nCOST, from the docs' formula and these counts (regions=us):")
    print(f"  historical event odds bill 10 x markets RETURNED x regions, per event")
    print(f"  a 5-market usage pull over {n} events:            <= {10 * 5 * n:>7,}")
    print(f"  every distinct key seen here ({len(keys)}) over {n} events: <= {10 * len(keys) * n:>7,}")
    if player_keys:
        print(f"  player props only ({len(player_keys)} keys) over "
              f"{len(with_player)} events:   <= {10 * len(player_keys) * len(with_player):>7,}")
    con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
