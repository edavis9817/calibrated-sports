"""f-22: independent reconciliation of record/nfl/published.json against the raw ledger.
Does NOT import core.record. Reads the ledger parquet copy only."""
import json, sys, statistics, datetime as dt
import polars as pl
L = pl.read_parquet(sys.argv[1]).to_dicts()
P = json.load(open(sys.argv[2], encoding="utf-8"))
def ts(s): return dt.datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
def a2p(a):
    if a is None or a != a or -100 < a < 100: return None
    return 100/(a+100) if a > 0 else -a/(-a+100)
def a2d(a): return 1 + (a/100 if a > 0 else 100/-a)
pubs = {}; dup = []
for e in L:
    if e["event"] == "published":
        if e["lean_id"] in pubs: dup.append(e["lean_id"])
        pubs[e["lean_id"]] = e
term = {}
for e in L:
    if e["event"] in ("graded", "void"):
        assert e["lean_id"] not in term, "two terminal events"
        term[e["lean_id"]] = e
print("published", len(pubs), "dup published", len(dup), "terminal", len(term),
      "terminal w/o published", len(set(term) - set(pubs)))
# pre-kickoff margins
m = [(p["kickoff_ts"] - ts(p["read_at"]), p["kickoff_ts"] - ts(p["event_at"]), lid) for lid, p in pubs.items()]
print("min read_at margin (s)", min(x[0] for x in m), "min event_at margin (s)", min(x[1] for x in m))
print("read_at > event_at rows", sum(1 for p in pubs.values() if ts(p["read_at"]) > ts(p["event_at"])))
# terminal rows: do their published-copy fields agree with the published row?
keys = ["season","week","game_id","gsis_id","market","line","side","read_at","kickoff_ts","price","mkt_p_over","model_p_over"]
drift = [(lid, k) for lid, t in term.items() for k in keys if t[k] != pubs[lid][k] and not (t[k] != t[k] and pubs[lid][k] != pubs[lid][k])]
print("terminal rows whose copied fields differ from their published row", len(drift), drift[:5])
res = {lid: (term[lid]["event"], term[lid]["result"]) for lid in term}
from collections import Counter
print("results", Counter(res.values()))
graded = [lid for lid, (ev, r) in res.items() if ev == "graded"]
cl = sum(1 for lid in graded if res[lid][1] == "cleared"); mi = sum(1 for lid in graded if res[lid][1] == "missed")
print("graded", len(graded), "cleared", cl, "missed", mi, "hit", round(cl/len(graded), 4))
priced = [lid for lid in graded if a2p(pubs[lid]["price"]) is not None]
bad = [lid for lid in graded if a2p(pubs[lid]["price"]) is None]
clp = sum(1 for lid in priced if res[lid][1] == "cleared")
be = statistics.fmean(a2p(pubs[lid]["price"]) for lid in priced)
units = sum((a2d(pubs[l]["price"]) - 1) if res[l][1] == "cleared" else -1 for l in priced)
print("priced", len(priced), "hit_priced", round(clp/len(priced), 4), "breakeven", round(be, 4),
      "margin_pp", round(100*(clp/len(priced)-be), 2), "units", round(units, 3), "roi", round(units/len(priced), 4))
print("dropped graded (invalid price):")
for l in bad:
    p = pubs[l]; print("  ", l, p["game_id"], p["market"], p["line"], p["side"], "price", p["price"], "books", p["mkt_books"], "mkt_p_over", p["mkt_p_over"], "->", res[l][1])
allbad = [l for l, p in pubs.items() if a2p(p["price"]) is None]
print("all invalid-price published", len(allbad), "states", Counter(res.get(l, ("ungraded", None))[0] for l in allbad))
# sensitivity: include the dropped 7 at a break-even from the ledger's own de-vigged market prob (lean side)
def side_p(p): return p["mkt_p_over"] if p["side"] == "over" else 1 - p["mkt_p_over"]
for label, bef in [("dropped at de-vig mkt prob (no vig)", side_p), ("dropped at 0.5238 (-110)", lambda p: 110/210), ("dropped at 0.50", lambda p: 0.5)]:
    rows = [(res[l][1] == "cleared", a2p(pubs[l]["price"])) for l in priced] + [(res[l][1] == "cleared", bef(pubs[l])) for l in bad]
    h = sum(r[0] for r in rows)/len(rows); b = statistics.fmean(r[1] for r in rows)
    print(f"  all {len(rows)} with {label}: hit {h:.4f} be {b:.4f} margin {100*(h-b):+.2f}pp")
# worst case for the record: dropped cleared leans at a price that pays least
print("published.json record:", {k: P["record"][k] for k in ("cleared","missed","push","void","hit_rate","hit_rate_priced","breakeven","margin_pp","n_priced","n_price_invalid","units","roi")})
print("published.json counts:", P["n_published"], P["n_graded"], P["n_void"], P["n_ungraded"], "excluded", P["excluded_not_pre_kickoff"]["n"])
# per-lean agreement
pl_ = {x["lean_id"]: x for x in P["leans"]}
assert set(pl_) == set(pubs), "lean set differs"
mism = [l for l in pubs if (pl_[l]["result"] or None) != (res.get(l, (None, None))[1])]
print("per-lean result mismatches", len(mism))
