"""f-22: the ledger's kickoff_ts against nflverse's (nfl_games, read mode=ro), and each published
lean's read/event margin against the EARLIER of the two clocks."""
import sqlite3, sys, datetime as dt
import polars as pl
L = pl.read_parquet(sys.argv[1]).to_dicts()
pubs = [e for e in L if e["event"] == "published"]
con = sqlite3.connect("file:D:/calibrated-sports/data/market_log.db?mode=ro", uri=True)
g = {r[0]: r[1] for r in con.execute("select game_id, kickoff_ts from nfl_games where season=2026 and week in (3,4)")}
vers = con.execute("select count(*), count(distinct game_id) from nfl_games where season=2026 and week in (3,4)").fetchone()
con.close()
print("nfl_games rows wk3-4", vers)
def ts(s): return dt.datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
games = {}
for p in pubs: games.setdefault(p["game_id"], set()).add(p["kickoff_ts"])
print("games with leans", len(games), "games with >1 ledger kickoff", sum(len(v) > 1 for v in games.values()))
diffs = []
for gid, ks in sorted(games.items()):
    k = next(iter(ks)); n = g.get(gid)
    diffs.append((gid, k, n, None if n is None else k - n))
print("games absent from nfl_games", [d[0] for d in diffs if d[2] is None])
print("ledger - nflverse kickoff (s): distinct", sorted({d[3] for d in diffs if d[3] is not None}))
worst = min(min(g.get(p["game_id"], p["kickoff_ts"]), p["kickoff_ts"]) - max(ts(p["read_at"]), ts(p["event_at"])) for p in pubs)
print("min margin of read/event before the EARLIER kickoff (s):", worst, "=", round(worst/60, 1), "min")
