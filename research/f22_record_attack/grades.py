"""f-22: each graded lean's result against its own ledgered actual, and the actual against
nfl_player_week (market_log.db, mode=ro)."""
import sqlite3, sys
import polars as pl
L = pl.read_parquet(sys.argv[1]).to_dicts()
g = [e for e in L if e["event"] == "graded"]
bad = [e for e in g if e["result"] != ("cleared" if ((e["actual"] > e["line"]) == (e["side"] == "over")) else "missed")]
print("graded", len(g), "result inconsistent with ledgered actual/line/side:", len(bad))
con = sqlite3.connect("file:D:/calibrated-sports/data/market_log.db?mode=ro", uri=True)
cols = [c[1] for c in con.execute("pragma table_info(nfl_player_week)")]
col = {"receptions": "receptions", "rush_attempts": "carries" if "carries" in cols else "rushing_attempts"}
print("stat columns used", col, "version col?", [c for c in cols if "version" in c or "ingest" in c][:3])
mism, missing = [], 0
for e in g:
    rows = con.execute(f"select {col[e['market']]} from nfl_player_week where gsis_id=? and season=? and week=?",
                       (e["gsis_id"], e["season"], e["week"])).fetchall()
    if not rows: missing += 1; continue
    vals = {r[0] for r in rows}
    if e["actual"] not in vals: mism.append((e["lean_id"], e["market"], e["actual"], vals))
con.close()
print("no stat row", missing, "actual disagrees with nfl_player_week", len(mism), mism[:5])
