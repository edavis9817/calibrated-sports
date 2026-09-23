"""a-13: what moving `components` from OPTIONAL_PARTS into PARTS would cost.

    python -m research.a13_components_cost --scratch D:/temp/a13 [--repeats 2]

Measures, and changes nothing:

  1. SIZE - builds the components part for real into a scratch tree
     (`only=["components"]`, a fresh `dest`, a COPY of the slug registry) and
     reports every season file's bytes on disk, raw and gzipped.
  2. EXPORT COST - times a default export (PARTS) against PARTS + components,
     same inputs, interleaved `repeats` times each, both as DRY RUNS.

Why dry runs for the timing. A non-dry run that includes the `market` part
renews `quote_retention_hold` in the store (`hold_published_markets`), which is
a write to the logger's database - out of bounds for this unit. A dry run still
builds and contract-validates every file; what it skips is the disk write, so
the write cost of the new part is measured separately, from the components-only
real run in step 1.

Why a scratch slug registry. `scope_slugs` appends to the committed registry on
any non-dry run that meets a new player. Pointing it at a copy keeps this
script from editing a published file.
"""
import argparse
import gzip
import json
import os
import shutil
import statistics
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from jobs import export_web as E  # noqa: E402


def sizes(dest):
    out = {}
    root = os.path.join(dest, E.SPORT, "components")
    for fn in sorted(os.listdir(root)):
        with open(os.path.join(root, fn), "rb") as f:
            data = f.read()
        out[fn] = {"raw": len(data), "gzip": len(gzip.compress(data, compresslevel=6))}
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--scratch", required=True)
    ap.add_argument("--repeats", type=int, default=2)
    a = ap.parse_args(argv)
    if os.path.exists(a.scratch):
        raise SystemExit(f"{a.scratch} exists - refusing to reuse a scratch tree")
    os.makedirs(a.scratch)
    reg = os.path.join(a.scratch, "slugs", "nfl.json")
    os.makedirs(os.path.dirname(reg))
    shutil.copy(E.slug_registry_path(), reg)
    quiet = lambda *_a, **_k: None  # noqa: E731

    # 1. SIZE, from a real write.
    t = time.time()
    s = E.export(only=["components"], dest=os.path.join(a.scratch, "components_only"),
                 registry_path=reg, log=quiet)
    comp_only_wall = time.time() - t
    sz = sizes(os.path.join(a.scratch, "components_only"))
    if not sz:
        raise SystemExit("components-only export wrote no files - nothing measured")
    current = s["current"]["season"]
    print(json.dumps({"components_only": {"wall_s": round(comp_only_wall, 1),
                                          "runtime_s": s["runtime_s"],
                                          "written_deleted": s["components"],
                                          "census": s["components_census"],
                                          "refreshed": s["refreshed"]}}, indent=1))
    print(json.dumps({"current_season": current,
                      "current_file": sz.get(f"{current}.json"),
                      "files": len(sz),
                      "total_raw": sum(v["raw"] for v in sz.values()),
                      "total_gzip": sum(v["gzip"] for v in sz.values()),
                      "largest": max(sz.items(), key=lambda kv: kv[1]["raw"]),
                      "per_file": sz}, indent=1))

    # 2. EXPORT COST, interleaved so drift in the machine hits both arms.
    arms = {"default": list(E.PARTS), "with_components": list(E.PARTS) + ["components"]}
    times = {k: [] for k in arms}
    refreshed = {}
    for i in range(a.repeats):
        for name, parts in arms.items():
            dest = os.path.join(a.scratch, f"dry_{name}_{i}")
            t = time.time()
            s = E.export(only=parts, dry_run=True, dest=dest, registry_path=reg, log=quiet)
            times[name].append(time.time() - t)
            refreshed[name] = s["refreshed"]
            if os.path.exists(dest) and os.listdir(dest):
                raise SystemExit(f"dry run wrote into {dest}")
    print(json.dumps({"timing_s": {k: [round(x, 1) for x in v] for k, v in times.items()},
                      "median_s": {k: round(statistics.median(v), 1) for k, v in times.items()},
                      "refreshed": refreshed}, indent=1))


if __name__ == "__main__":
    main()
