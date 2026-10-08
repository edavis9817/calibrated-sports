"""f-29: the Receipts hit rate b-99 read off production (496 graded, 256-240, 51.6% [47.2, 56.0]),
re-derived from the Board ledger and re-drawn under game, player-game and kickoff-slot blocks.
READ-ONLY: reads board/nfl/ledger.parquet. Paths are this machine's; run under the 3.12 venv."""
import sys, math, json
import numpy as np, polars as pl
sys.path.insert(0, r"C:\Users\Ethan Davis\code\cs-analytics\research\f26_reliability")
import f26lib as L
df = pl.read_parquet("D:/calibrated-sports/data/board_export/board/nfl/ledger.parquet").filter(pl.col("result").is_not_null())
print("graded rows", df.height, "distinct lean_id", df["lean_id"].n_unique(), "events", df["event"].value_counts().to_dicts(), "weeks", df["week"].value_counts().sort("week").to_dicts())
y = (df["result"] == "cleared").to_numpy().astype(float); n = len(y); p = y.mean()
z = 1.959964; den = 1 + z*z/n; c = (p + z*z/(2*n))/den; h = z*math.sqrt(p*(1-p)/n + z*z/(4*n*n))/den
print("hit rate %.4f  Wilson per lean [%.4f, %.4f]  normal [%.4f, %.4f]  (b-99 read 51.6 [47.2, 56.0] off production)" % (p, c-h, c+h, p-z*math.sqrt(p*(1-p)/n), p+z*math.sqrt(p*(1-p)/n)))
stat = lambda idx: float(y[idx].mean())
labs = {"lean (iid)": list(range(n)), "game": df["game_id"].to_list(), "player-game": [a+"|"+b for a, b in zip(df["game_id"].to_list(), df["gsis_id"].to_list())],
        "kickoff slot": [str(int(k)) for k in df["kickoff_ts"].to_list()], "week": df["week"].to_list()}
R = L.alt_blocks(stat, n, labs, seed=12)
for k, r in R.items(): print("  %-12s (%3d blocks) %.4f [%.4f, %.4f] SE %.4f width x%.2f of per-lean%s" % (k, r["n_blocks"], r["est"], r["lo"], r["hi"], r["se"], r["width"]/R["lean (iid)"]["width"], "" if r["read"] else "  NOT READ (<5 blocks)"))
pg = len(set(labs["player-game"])); print("leans per player-game %.2f, per game %.1f" % (n/pg, n/len(set(labs["game"]))))
json.dump({k: v for k, v in R.items()}, open("D:/temp/f29/receipts.json", "w"), indent=1, default=str)
