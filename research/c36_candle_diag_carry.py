import sys, sqlite3, numpy as np, bisect
from collections import Counter
sys.path.insert(0, ".")
from research import win_total_drift as W
c, nh, nd = W.load_candles("D:/calibrated-sports/data/research_raw/c36")
con = W.ro("D:/calibrated-sports/data/market_log.db")
import datetime as dt
Ts = {"S_3": W.S_OF[3], "S_4": W.S_OF[4], "S'_3": W.S_OF[3]+86400, "S'_4": W.S_OF[4]+86400, "Sat 10-03 20:00": W.utc(2026,10,3,20), "Mon 09-28 06:00": W.utc(2026,9,28,6)}
for lab, T in Ts.items():
    res = Counter(); ages = {"exact": [0,0], "<=3h": [0,0], "3-24h": [0,0], ">24h": [0,0]}
    for t in c:
        r = con.execute("SELECT best_bid, best_ask FROM quotes WHERE venue='kalshi' AND market_id=? AND side='yes' AND ts<=? AND ts>=? ORDER BY ts DESC LIMIT 1",(t,T,T-660)).fetchone()
        if not r or r[0] is None or r[1] is None: continue
        ks = sorted(c[t]["h"]); i = bisect.bisect_right(ks, T) - 1
        if i < 0: res["no candle before T"] += 1; continue
        row = c[t]["h"][ks[i]]; age = (T - ks[i]) / 3600
        b = "exact" if age == 0 else "<=3h" if age <= 3 else "3-24h" if age <= 24 else ">24h"
        ok = row[0] is not None and row[1] is not None and abs(row[0]-r[0]) <= 0.0101 and abs(row[1]-r[1]) <= 0.0101
        ages[b][0] += ok; ages[b][1] += 1
    tot = sum(v[1] for v in ages.values()); ok = sum(v[0] for v in ages.values())
    print(lab, "live two-value rungs", tot, "carry-forward agrees 1c", ok, f"{ok/max(tot,1):.1%}", {k: f"{v[0]}/{v[1]}" for k, v in ages.items()}, dict(res))
