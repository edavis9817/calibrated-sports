"""f-22: for every published lean, recompute the lean-side price from the per-book quotes in the
Board read the ledger names (read_at), and compare the ledger's median-of-American-odds against a
median taken in probability space. Finds the whole straddle class, not only the rows that land
inside (-100, 100)."""
import json, glob, os, sys, statistics
import polars as pl
BOARD = sys.argv[1]; LED = sys.argv[2]
L = pl.read_parquet(LED).to_dicts()
pubs = {e["lean_id"]: e for e in L if e["event"] == "published"}
term = {e["lean_id"]: e for e in L if e["event"] in ("graded", "void")}
reads = {}
for f in glob.glob(os.path.join(BOARD, "board/nfl/2026/wk*/read-*.json")):
    d = json.load(open(f, encoding="utf-8")); reads[d["read_at"]] = {r["row_id"]: r for r in d["rows"]}
print("reads", len(reads))
b0 = None
for r in reads.values():
    for x in r.values():
        if x.get("books"): b0 = x["books"][0]; break
    if b0: break
print("book element:", b0)
def a2p(a):
    if a is None or -100 < a < 100: return None
    return 100/(a+100) if a > 0 else -a/(-a+100)
def p2a(p): return -100*p/(1-p) if p >= 0.5 else 100*(1-p)/p
rows = []
miss = 0
for lid, p in pubs.items():
    rd = reads.get(p["read_at"], {}).get(p["row_id"])
    if rd is None: miss += 1; continue
    side = p["side"]
    prices = []
    for b in rd["books"]:
        v = b.get(f"{side}_price", b.get(side))
        if isinstance(v, dict): v = v.get("price")
        if v is not None: prices.append(float(v))
    if not prices: miss += 1; continue
    med_am = statistics.median(prices)
    med_p = statistics.median(a2p(x) for x in prices)
    straddle = min(prices) < 0 < max(prices) if len(prices) % 2 == 0 else False
    rows.append(dict(lid=lid, n=len(prices), prices=prices, ledger=p["price"], med_am=med_am,
                     be_ledger=a2p(p["price"]), be_prob=med_p, straddle=straddle,
                     res=(term.get(lid) or {}).get("result"), ev=(term.get(lid) or {}).get("event")))
print("leans matched to their read", len(rows), "unmatched", miss)
print("ledger price == median of American odds on", sum(1 for r in rows if abs(r["ledger"] - r["med_am"]) < 1e-9), "of", len(rows))
st = [r for r in rows if r["straddle"]]
print("even-count straddles of even money (the whole defect class):", len(st))
for r in st:
    print(f"  {r['lid']} prices {r['prices']} ledger {r['ledger']} be_ledger {r['be_ledger']} be_prob {r['be_prob']:.4f} -> {r['ev']} {r['res']}")
wrong_valid = [r for r in st if r["be_ledger"] is not None]
print("straddles that land OUTSIDE (-100,100) and pass as a price:", len(wrong_valid))
d = [abs(r["be_ledger"] - r["be_prob"]) for r in rows if r["be_ledger"] is not None]
print("|be_ledger - be_prob| over valid-priced leans: max", round(max(d), 4), "n>0.005", sum(1 for x in d if x > 0.005))
# the record recomputed with break-even from the probability-space median, all graded included
g = [r for r in rows if r["ev"] == "graded"]
h = sum(r["res"] == "cleared" for r in g) / len(g); be = statistics.fmean(r["be_prob"] for r in g)
def dec(p): return 1/p
u = sum((dec(r["be_prob"]) - 1) if r["res"] == "cleared" else -1 for r in g)
print(f"ALL {len(g)} graded, break-even from probability-space median: hit {h:.4f} be {be:.4f} margin {100*(h-be):+.2f}pp units {u:+.2f}")
