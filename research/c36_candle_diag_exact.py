import sys, sqlite3, numpy as np
from collections import Counter
sys.path.insert(0, ".")
from research import win_total_drift as W
c, nh, nd = W.load_candles("D:/calibrated-sports/data/research_raw/c36")
tape = W.Tape(c)
con = W.ro("D:/calibrated-sports/data/market_log.db")
n_h = sorted(len(v["h"]) for v in c.values()); print("hourly per ticker: min/med/max", n_h[0], n_h[len(n_h)//2], n_h[-1], "zero:", sum(1 for x in n_h if x==0))
for k in (3,4):
    T = W.S_OF[k]; why = Counter()
    for t in c:
        r = con.execute("SELECT best_bid, best_ask FROM quotes WHERE venue='kalshi' AND market_id=? AND side='yes' AND ts<=? AND ts>=? ORDER BY ts DESC LIMIT 1",(t,T,T-660)).fetchone()
        row = c[t]["h"].get(T)
        why[("live:" + ("none" if not r else "nullside" if r[0] is None or r[1] is None else "ok"), "candle:" + ("none" if row is None else "nullside" if row[0] is None or row[1] is None else "ok"))] += 1
    print(k, dict(why))
# example of a no-candle ticker
ex = [t for t in c if c[t]["h"].get(W.S_OF[4]) is None][:5]; print(ex)
for t in ex[:2]:
    ks = sorted(c[t]["h"]); print(t, len(ks), W.iso(ks[0]) if ks else None, W.iso(ks[-1]) if ks else None, [c[t]["h"][x] for x in ks[-2:]])
    print("  live:", con.execute("SELECT ts,best_bid,best_ask FROM quotes WHERE venue='kalshi' AND market_id=? AND ts<=? ORDER BY ts DESC LIMIT 1",(t,W.S_OF[4])).fetchone())
