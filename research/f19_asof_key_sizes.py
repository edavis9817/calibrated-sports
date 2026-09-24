"""f-19: size of a-21's as-of residual keys on the wire. Built from a copy of a-21's scratch analytics.db
by a-21's own analytics.export.build() at ff9cde3, serialised exactly as analytics.export.sync writes it."""
import gzip, json, sys, time, zlib
sys.path.insert(0, r"D:\temp\f19\a21")
from analytics import export as X, paths
try:
    import brotli
except ImportError:
    brotli = None
con = paths.connect(read_only=True)
print("db:", paths.db_path())
t = time.time(); out, dropped = X.build(con); print("build s", round(time.time() - t, 1), "keys", len(out))
rows = []
for key in sorted(out):
    if "opportunity_residual" not in key:
        continue
    raw = (json.dumps(out[key], indent=1, sort_keys=False) + "\n").encode("utf-8")
    compact = json.dumps(out[key], separators=(",", ":")).encode("utf-8")
    g6 = len(gzip.compress(raw, 6)); g9 = len(gzip.compress(raw, 9))
    br = len(brotli.compress(raw, quality=4)) if brotli else None
    br11 = len(brotli.compress(raw, quality=11)) if brotli else None
    nvals = len(out[key].get("values", []))
    rows.append((key, len(raw), len(compact), g6, g9, br, br11, nvals))
print(f"{'key':<62}{'raw':>12}{'compact':>12}{'gzip6':>11}{'gzip9':>11}{'br4':>11}{'br11':>11}{'values':>9}")
for r in rows:
    print(f"{r[0]:<62}" + "".join(f"{(x if x is not None else -1):>12,}" if i < 2 else f"{(x if x is not None else -1):>11,}" for i, x in enumerate(r[1:7])) + f"{r[7]:>9,}")
json.dump(rows, open(sys.argv[1], "w"), indent=1)
