"""f-19: re-measure a-22's last_read values independently, mode=ro, with SQL written here rather than a-22's."""
import sqlite3, sys, time
db = sys.argv[1]
con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
iso = lambda t: None if t is None else time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))
print("now", iso(time.time()))
print("indexes on quotes:", con.execute("SELECT name, sql FROM sqlite_master WHERE type='index' AND tbl_name='quotes'").fetchall())
for src, ok, last_ok, upd in con.execute("SELECT source, ok, last_ok_ts, updated_ts FROM source_health WHERE source LIKE 'nflverse:%' OR source='predictions' ORDER BY source"):
    print(f"health {src:<28} ok={ok} last_ok={iso(last_ok)} updated={iso(upd)}")
# newest rows per venue family, walking the ingest index newest-first over the last 2 days only
since = time.time() - 2 * 86400
for fam in ("kalshi", "polymarket", "oddsapi"):
    r = con.execute("SELECT MAX(ingest_ts), COUNT(*) FROM quotes WHERE ingest_ts >= ? AND venue LIKE ?", (since, fam + "%")).fetchone()
    print(f"quotes {fam:<11} newest ingest in last 48h = {iso(r[0])}  rows in 48h = {r[1]}")
r = con.execute("SELECT source, COUNT(*), MAX(ingest_ts) FROM quotes WHERE venue LIKE 'oddsapi%' AND ingest_ts >= ? GROUP BY source", (since,)).fetchall()
print("oddsapi by source 48h:", [(s, n, iso(t)) for s, n, t in r])
print("market_trades:", con.execute("SELECT COUNT(*), MAX(ingest_ts) FROM market_trades").fetchone())
has = con.execute("SELECT name FROM sqlite_master WHERE name='pfr_alias'").fetchone()
print("pfr_alias table present:", bool(has), con.execute("SELECT COUNT(*), GROUP_CONCAT(DISTINCT method) FROM pfr_alias").fetchone() if has else "")
con.close()
