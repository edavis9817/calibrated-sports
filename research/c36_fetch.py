"""c-36 raw fetch: Kalshi public, unauthenticated, free endpoints only. Verbatim bodies archived
before any parsing (invariant 2). Resumable: a ticker with a file on disk is skipped."""
import httpx, time, json, gzip, os, sqlite3, sys, threading, datetime as dt
B = "https://api.elections.kalshi.com/trade-api/v2"
OUT = "D:/calibrated-sports/data/research_raw/c36"
UA = {"User-Agent": "calibratedsports-research/1.0 python-httpx/0.28"}
con = sqlite3.connect("file:D:/calibrated-sports/data/market_log.db?mode=ro", uri=True, timeout=5)
tick = sorted(r[0] for r in con.execute("SELECT market_id FROM markets WHERE venue='kalshi' AND market_id LIKE 'KXNFLWINS-27%'"))
con.close()
OPEN = int(dt.datetime(2026, 4, 20, tzinfo=dt.timezone.utc).timestamp())
SEASON = int(dt.datetime(2026, 9, 7, tzinfo=dt.timezone.utc).timestamp())
NOW = int(time.time()) // 3600 * 3600
stat = {"candles": 0, "books": 0, "err": 0, "429": 0}

def get(c, url, params, gap):
    pen = 0.0
    for _ in range(8):
        time.sleep(gap + pen)
        try:
            r = c.get(url, params=params)
        except Exception as e:
            pen = min(max(pen * 2, 1.0), 30); continue
        if r.status_code == 429 or r.status_code >= 500:
            stat["429"] += 1; pen = min(max(pen * 2, 1.0), 30); continue
        return r
    return None

def candles():
    c = httpx.Client(timeout=40, headers=UA)
    for t in tick:
        p = f"{OUT}/candles/{t}.json.gz"
        if os.path.exists(p): continue
        calls = []
        s = OPEN
        while s < SEASON:
            e = min(s + 80 * 86400, SEASON); calls.append((s, e, 1440)); s = e
        calls.append((SEASON, NOW, 60))
        bodies = []; ok = True
        for s, e, per in calls:
            r = get(c, f"{B}/series/KXNFLWINS/markets/{t}/candlesticks", {"start_ts": s, "end_ts": e, "period_interval": per}, 0.45)
            if r is None or r.status_code != 200:
                ok = False; stat["err"] += 1; break
            bodies.append({"request": {"ticker": t, "start_ts": s, "end_ts": e, "period_interval": per}, "fetched_ts": time.time(), "status": r.status_code, "body": r.text})
        if ok:
            with gzip.open(p + ".tmp", "wt", encoding="utf-8") as f: json.dump(bodies, f)
            os.replace(p + ".tmp", p); stat["candles"] += 1

def books():
    c = httpx.Client(timeout=40, headers=UA)
    r = get(c, f"{B}/series/KXNFLWINS", {}, 0.3)
    json.dump({"fetched_ts": time.time(), "status": r.status_code, "body": r.text}, open(f"{OUT}/series_KXNFLWINS.json", "w"))
    for t in tick:
        p = f"{OUT}/books/{t}.json"
        if os.path.exists(p): continue
        r = get(c, f"{B}/markets/{t}/orderbook", {}, 0.35)
        r2 = get(c, f"{B}/markets/{t}", {}, 0.35)
        if r is None or r.status_code != 200 or r2 is None or r2.status_code != 200:
            stat["err"] += 1; continue
        json.dump({"ticker": t, "fetched_ts": time.time(), "orderbook": r.text, "market": r2.text}, open(p, "w")); stat["books"] += 1

os.makedirs(f"{OUT}/candles", exist_ok=True); os.makedirs(f"{OUT}/books", exist_ok=True)
print(len(tick), "tickers", flush=True)
th = [threading.Thread(target=candles), threading.Thread(target=books)]
[x.start() for x in th]
while any(x.is_alive() for x in th):
    time.sleep(30); print(time.strftime("%H:%M:%S"), stat, flush=True)
print("DONE", stat, flush=True)
