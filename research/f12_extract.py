"""F12 step 0: copy the line-bearing rows out of the live stores ONCE, read-only.

market_log.db is 16 GB, WAL, and written by the running logger. A read-only
session there still pins the WAL while it is open (CLAUDE.md, `mode=ro` row), and
a `venue LIKE 'oddsapi:%'` scan took ~5 minutes. So every query the inventory
needs is run here, once, and the rows land in a scratch SQLite file that the
inventory and the test then read as often as they like.

What is copied, and why:

    oddsapi_quotes   every quotes row whose venue is an Odds API book, all
                     sources (live snapshots 2026, oddsapi_historical 2023-25)
    kalshi_game      Kalshi quotes for the game-line series (KXNFLSPREAD,
                     KXNFLTOTAL, KXNFLGAME) - compact columns only
    kalshi_market_agg  per Kalshi market: rows, distinct ts, first/last ts
    markets          the markets table (kalshi + oddsapi)
    nfl_games        every versioned row - nflverse spread_line/total_line
    nflverse_versions the games dataset's version history
    outcome_settlement, outcomes   what the test needs to grade a prop
    cfb_game_lines, cfb_odds_quotes, cfb_odds_events, cfb_odds_snapshots,
    cfb_games        from cfb.db (also read-only)

Opened with mode=ro and nothing else. The scratch path is an argument with no
default: a diagnostic writes where it is told, never beside the store.

    python -m research.f12_extract --market-log <STORAGE_DIR>/market_log.db         --cfb-db <STORAGE_DIR>/cfb.db --out D:/temp/f12/extract.db

Both store paths are required arguments: this clone's .env points LOGGER_DB at an
unused file on purpose (track F never writes the logger's store), so config cannot
name the store to READ, and a literal path would choose the disk for every reader.
"""
import argparse
import os
import sqlite3
import time

GAME_SERIES = ("KXNFLSPREAD", "KXNFLTOTAL", "KXNFLGAME")


def ro(path):
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10)


def copy(src, dst, name, sql, params=()):
    t = time.time()
    cur = src.execute(sql, params)
    cols = [d[0] for d in cur.description]
    dst.execute(f"DROP TABLE IF EXISTS {name}")
    dst.execute(f"CREATE TABLE {name} ({', '.join(cols)})")
    n = 0
    while True:
        rows = cur.fetchmany(50_000)
        if not rows:
            break
        dst.executemany(f"INSERT INTO {name} VALUES ({','.join('?' * len(cols))})", rows)
        n += len(rows)
    dst.commit()
    print(f"  {name:<22} {n:>10,} rows  {time.time() - t:6.1f}s")
    if n == 0:
        raise SystemExit(f"{name}: zero rows copied - refusing to continue on an empty read")
    return n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--market-log", required=True)
    ap.add_argument("--cfb-db", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--skip-kalshi-rows", action="store_true")
    a = ap.parse_args()
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    dst = sqlite3.connect(a.out)
    src = ro(a.market_log)
    try:
        # markets.venue is plain 'oddsapi'; quotes.venue is 'oddsapi:<book>'. The
        # book list is read from the quotes index (skip-scan), not assumed.
        books = [r[0] for r in src.execute(
            "SELECT DISTINCT venue FROM quotes INDEXED BY ix_quotes_market_ts")
            if r[0].startswith("oddsapi:")]
        print(f"  {len(books)} Odds API books in quotes")
        ph = ",".join("?" * len(books))
        copy(src, dst, "oddsapi_quotes",
             f"SELECT ts, venue, event_id, market_id, market_type, subject, line, side, "
             f"mid, last, prob_devig, raw_ref, source, ingest_ts FROM quotes "
             f"WHERE venue IN ({ph})", books)
        copy(src, dst, "markets", "SELECT * FROM markets")
        copy(src, dst, "nfl_games", "SELECT * FROM nfl_games")
        copy(src, dst, "nflverse_versions", "SELECT * FROM nflverse_versions")
        copy(src, dst, "outcomes", "SELECT * FROM outcomes")
        copy(src, dst, "outcome_settlement", "SELECT * FROM outcome_settlement")
        copy(src, dst, "raw_shards", "SELECT * FROM raw_shards")
        copy(src, dst, "kalshi_market_agg",
             "SELECT market_id, source, count(*) n, count(DISTINCT ts) n_ts, min(ts) t0, "
             "max(ts) t1, min(ingest_ts) i0 FROM quotes WHERE venue='kalshi' "
             "GROUP BY market_id, source")
        if not a.skip_kalshi_rows:
            ids = [r[0] for r in src.execute(
                "SELECT market_id FROM markets WHERE venue='kalshi' AND ("
                + " OR ".join("market_id LIKE ?" for _ in GAME_SERIES) + ")",
                [s + "-%" for s in GAME_SERIES])]
            print(f"  {len(ids)} Kalshi game-line markets")
            dst.execute("DROP TABLE IF EXISTS kalshi_game")
            dst.execute("CREATE TABLE kalshi_game (market_id, ts, best_bid, best_ask, mid, "
                        "source, ingest_ts)")
            n = 0
            for i in range(0, len(ids), 200):
                chunk = ids[i:i + 200]
                rows = src.execute(
                    f"SELECT market_id, ts, best_bid, best_ask, mid, source, ingest_ts FROM quotes "
                    f"WHERE venue='kalshi' AND market_id IN ({','.join('?' * len(chunk))})",
                    chunk).fetchall()
                dst.executemany("INSERT INTO kalshi_game VALUES (?,?,?,?,?,?,?)", rows)
                n += len(rows)
            dst.commit()
            print(f"  kalshi_game            {n:>10,} rows")
    finally:
        src.close()
    src = ro(a.cfb_db)
    try:
        for t in ("cfb_game_lines", "cfb_odds_quotes", "cfb_odds_events",
                  "cfb_odds_snapshots", "cfb_games"):
            copy(src, dst, t, f"SELECT * FROM {t}")
    finally:
        src.close()
    dst.close()


if __name__ == "__main__":
    main()
