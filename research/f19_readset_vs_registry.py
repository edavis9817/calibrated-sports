"""f-19: f-17's measured read-set (server readKey log + browser /data/ requests, 62 routes, b-30 render)
against a-22's registry at c1d93f7. Kinds are resolved with a-22's own kind_for_key over the contract."""
import json, re, sqlite3, sys, collections
sys.path.insert(0, r"D:\temp\f19\a22")
from jobs import export_web as E, source_registry as R
EV = r"C:\Users\Ethan Davis\code\_relay\reports\f-17-evidence"
keys = collections.Counter()
for line in open(EV + r"\server-reads.txt", encoding="utf-8"):
    p = line.split()
    if len(p) == 3: keys[("server", p[2])] += 1
routes = json.load(open(EV + r"\render-b30-index.json", encoding="utf-8"))
key_routes = collections.defaultdict(set)
for r in routes:
    for k in r["keys"]:
        k = k.split("/data/", 1)[-1]
        keys[("client", k)] += 1; key_routes[k].add(r["route"])
assert len(keys) > 20, len(keys)
by_kind = collections.defaultdict(set)
unresolved = []
for (side, k) in keys:
    kind, sport = E.kind_for_key(k)
    if kind is None: unresolved.append(k); continue
    by_kind[kind].add((side, re.sub(r"\d{2}-\d{7}", "{id}", k)))
print("distinct keys read:", len(keys), " unresolved to a kind:", unresolved)
read_kinds = set(by_kind)
print("\nKINDS READ BY A PAGE (f-17) ->", len(read_kinds))
con = sqlite3.connect(":memory:")
files = {s: R.build_sources(s, "2026-09-24T00:00:00Z", con, E.envelope) for s in ("nfl", "cfb", "mlb")}
for kind in sorted(read_kinds):
    sports = sorted({E.kind_for_key(k)[0] and k.split("/")[0] for _, k in by_kind[kind]})
    print(f"  {kind:<22} declared={kind in R.KIND_SOURCES}  keys e.g. {sorted(by_kind[kind])[:2]}")
    for s in sports:
        if s in files:
            print(f"      as {s}/sources.json would list it: {files[s]['kinds'][kind]}")
declared_unread = sorted(set(R.KIND_SOURCES) - read_kinds)
print("\nDECLARED KINDS NO CRAWLED PAGE READ:", declared_unread)
for s in ("cfb", "mlb"):
    print(f"\n{s}/sources.json (if built): sources listed = {[r['source_id'] for r in files[s]['sources']]}")
print("\ncfb/mlb manifest read on routes:", {k: sorted(v) for k, v in key_routes.items() if k.startswith(("cfb/", "mlb/"))})
print("\nregistered ids:", sorted(R.SOURCES))
print("ESPN scoreboard registered:", any("espn" in i for i in R.SOURCES), "| any source naming ESPN:",
      [i for i, s in R.SOURCES.items() if "espn" in (s["name"] + str(s["provides"]) + s["used_for"]).lower()])
print("live.prices readers:", [i for i, ids in R.KIND_SOURCES.items() if i == "live.prices"], R.KIND_SOURCES["live.prices"])
