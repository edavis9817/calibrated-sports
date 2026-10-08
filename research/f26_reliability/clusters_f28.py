"""f-31: f-28's "554 items are 504 clusters", attacked. A count with no interval (steps 2, 4, 5 do
not apply; the through-the-target blocks test is NOT RUN - there is no bootstrap).

    python research/f26_reliability/clusters_f28.py --src <f-28 worktree> --relay <relay dir> --scratch <dir>

  1  REPRODUCE on the REGISTERED population. The pile has grown since, so the reports are replayed
     in finish order into a scratch relay folder until f-28's own population hash (09a2f5708b599ecd)
     is reached; f-28's own `relay.items` then clusters that folder. If no prefix reaches the hash
     the count is reported as NOT REPRODUCED and the nearest prefix is named.
  2  STABILITY: the same rule on every later prefix up to today - does adding reports re-cluster
     OLD items (idf moves), i.e. is 504 a property of the 554 or of the day?
  3  USE: every file under the relay folder (and f-28's docs) where a cluster count appears, with
     the line, so a reader can see whether it is being used as a count of distinct questions.
"""
import argparse
import glob
import json
import os
import re
import shutil
import sys

REGISTERED = {"hash": "09a2f5708b599ecd", "items": 554, "reports": 238, "clusters": 504, "lo": 480, "hi": 535}
COUNT_WORDS = re.compile(r"\b(504|480|535|425|430|509)\b[^\n]{0,40}\bcluster|\bcluster[^\n]{0,60}\b(504|480|535|425|430|509)\b|distinct question", re.I)


def finish_order(relay):
    times = {}
    p = os.path.join(relay, "runlog.jsonl")
    for line in open(p, encoding="utf-8-sig"):
        try:
            row = json.loads(line)
            times[row["unit"]] = row["ts"]
        except (ValueError, KeyError):
            continue
    out = []
    for f in glob.glob(os.path.join(relay, "reports", "json", "*.json")):
        d = json.load(open(f, encoding="utf-8-sig"))
        u = d.get("unit_id") or os.path.basename(f)[:-5]
        import datetime as dt
        ts = dt.datetime.fromisoformat(times[u]).timestamp() if u in times else os.path.getmtime(f)
        out.append((ts, u, f, len(d.get("needs_ethan") or [])))
    return sorted(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True)
    ap.add_argument("--relay", required=True)
    ap.add_argument("--scratch", required=True)
    a = ap.parse_args()
    sys.path.insert(0, os.path.abspath(a.src))
    from relay import items as im
    assert os.path.abspath(im.__file__).startswith(os.path.abspath(a.src)), im.__file__
    order = finish_order(a.relay)
    print("%d machine reports today, %d needs_ethan items" % (len(order), sum(o[3] for o in order)))
    dest = os.path.join(a.scratch, "reports", "json")
    shutil.rmtree(a.scratch, ignore_errors=True)
    os.makedirs(dest)
    shutil.copy(os.path.join(a.relay, "runlog.jsonl"), os.path.join(a.scratch, "runlog.jsonl"))

    rows, found, base_cid = [], None, None
    for n, (_ts, u, f, _k) in enumerate(order, 1):
        shutil.copy(f, os.path.join(dest, os.path.basename(f)))
        if n < REGISTERED["reports"] - 6:
            continue
        items, n_rep = im.load(a.scratch)
        h = im.population_hash(items)
        sims = im.similarities(items)
        cl = im.cluster(items, im.THRESHOLD, sims)
        sens = [len(im.cluster(items, t, sims)) for t in im.SENSITIVITY]
        cid = {it.id: frozenset(x.id for x in g) for g in cl for it in g}
        rows.append((n, u, len(items), h, len(cl), sens, cid))
        if h == REGISTERED["hash"]:
            found, base_cid = rows[-1], cid
    print("\n1. reproduce on the registered population (hash %s)" % REGISTERED["hash"])
    if found:
        n, u, k, h, c, sens, _ = found
        ok = (k, c) == (REGISTERED["items"], REGISTERED["clusters"])
        print("  prefix of %d reports (last added %s): %d items, hash %s, %d clusters at %.2f, sensitivity %s"
              % (n, u, k, h, c, im.THRESHOLD, sens))
        print("  published 554 items / 504 clusters / 480 and 535: %s" % ("REPRODUCES" if ok and sorted(sens) == [REGISTERED["lo"], REGISTERED["hi"]] else "DOES NOT REPRODUCE"))
    else:
        print("  NOT REPRODUCED: no finish-order prefix of today's reports hashes to the registered population.")
        for n, u, k, h, c, sens, _ in rows:
            if abs(k - REGISTERED["items"]) <= 12:
                print("    prefix %d (+%s): %d items, hash %s, %d clusters, sensitivity %s" % (n, u, k, h, c, sens))

    print("\n2. stability: the same rule as the pile grows")
    for n, u, k, h, c, sens, cid in rows:
        moved = ""
        if base_cid is not None and n >= found[0]:
            changed = sum(1 for i, g in base_cid.items() if frozenset(x for x in cid.get(i, ()) if x in base_cid) != g)
            moved = "  registered items whose cluster-mates (among the 554) changed: %d" % changed
        print("  %3d reports (+%-5s) %3d items -> %3d clusters (%.3f per item)%s" % (n, u, k, c, c / k, moved))

    print("\n3. where a cluster count is written, in the relay folder and f-28's docs")
    hits = 0
    paths = [p for p in glob.glob(os.path.join(a.relay, "*.md")) + glob.glob(os.path.join(a.relay, "queue", "units", "*.md"))
             + glob.glob(os.path.join(a.relay, "queue", "proposed", "*.md")) + glob.glob(os.path.join(a.relay, "tasks", "*.md"))
             + glob.glob(os.path.join(a.src, "docs", "F28*.md")) + [os.path.join(a.src, "relay", "answered.py")]]
    for p in sorted(paths):
        try:
            text = open(p, encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        for i, ln in enumerate(text.splitlines(), 1):
            if COUNT_WORDS.search(ln):
                hits += 1
                print("  %s:%d  %s" % (os.path.relpath(p, a.relay) if p.startswith(a.relay) else os.path.relpath(p, a.src), i, ln.strip()[:230]))
    print("  %d lines in %d files searched" % (hits, len(paths)))
    if not paths or len(paths) < 5:
        print("  SEARCHED TOO FEW FILES - this step is not a result")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
