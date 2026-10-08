"""How many distinct questions the `needs_ethan` pile holds (unit f-28).

    python -m research.f28_clusters <relay> [--audit-dir DIR]

Runs the rule pre-registered in `docs/F28-answered-ledger-prereg.md` exactly as
`relay.items` implements it and prints the headline cluster count, the two
declared sensitivity counts, the chaining diagnostics and the merge-class count.
`--audit-dir` writes the two audit samples the pre-registration names, for a
person to read; this script grades nothing itself.
"""
import collections
import os
import random
import sys

from relay import items as im


def main(argv):
    relay = argv[1]
    audit = argv[argv.index("--audit-dir") + 1] if "--audit-dir" in argv else None
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    items, n_reports = im.load(relay)
    if not items:
        raise SystemExit("no needs_ethan items found - refusing to report a count of nothing")
    sims = im.similarities(items)
    print("%d items in %d reports, %d units raising at least one; population hash %s"
          % (len(items), n_reports, len({it.unit for it in items}), im.population_hash(items)))
    print("empty-token items: %d" % sum(1 for it in items if not im.tokens(it.what)))
    clusters = im.cluster(items, im.THRESHOLD, sims)
    multi = [g for g in clusters if len(g) > 1]
    print("HEADLINE  threshold %.2f: %d clusters (%d multi-item holding %d items, %d singletons)"
          % (im.THRESHOLD, len(clusters), len(multi), sum(len(g) for g in multi), len(clusters) - len(multi)))
    for t in im.SENSITIVITY:
        print("sensitivity threshold %.2f: %d clusters" % (t, len(im.cluster(items, t, sims))))
    sizes = collections.Counter(len(g) for g in clusters)
    print("cluster sizes: " + ", ".join("%dx%d" % (n, s) for s, n in sorted(sizes.items())))
    big = max(clusters, key=len)
    print("largest cluster %s: %d items, min internal cosine %.3f"
          % (im.cluster_name(big), len(big), im.min_internal(big, items, sims)))
    print("multi-item clusters whose min internal cosine is below %.2f (chained): %d of %d"
          % (im.THRESHOLD, sum(1 for g in multi if im.min_internal(g, items, sims) < im.THRESHOLD), len(multi)))
    merge = [it for it in items if im.is_merge_class(it.what)]
    print("merge-class items (a kind of ask, not a question): %d of %d, in %d clusters"
          % (len(merge), len(items), len({im.cluster_name(g) for g in clusters for it in g if im.is_merge_class(it.what)})))
    by_track = collections.Counter(it.track for it in items)
    print("by track: " + ", ".join("%s %d" % kv for kv in sorted(by_track.items())))
    if audit:
        os.makedirs(audit, exist_ok=True)
        rng = random.Random(28)
        chosen = multi if len(multi) <= 80 else rng.sample(multi, 40)
        with open(os.path.join(audit, "false_merge.txt"), "w", encoding="utf-8") as fh:
            fh.write("%d of %d multi-item clusters\n" % (len(chosen), len(multi)))
            for g in chosen:
                fh.write("\n%s  n=%d  min cos %.3f\n" % (im.cluster_name(g), len(g), im.min_internal(g, items, sims)))
                for it in g:
                    fh.write("  %-8s %s\n" % (it.id, it.what))
        cid = {it.id: im.cluster_name(g) for g in clusters for it in g}
        near = sorted((p for p, s in sims.items() if 0.30 <= s < im.THRESHOLD
                       and cid[items[p[0]].id] != cid[items[p[1]].id]))
        rng = random.Random(28)
        sample = rng.sample(near, min(60, len(near)))
        with open(os.path.join(audit, "missed_merge.txt"), "w", encoding="utf-8") as fh:
            fh.write("%d of %d cross-cluster pairs with cosine in [0.30, %.2f)\n" % (len(sample), len(near), im.THRESHOLD))
            for n, (i, j) in enumerate(sample, 1):
                fh.write("\n#%d cos %.3f\n  %-8s %s\n  %-8s %s\n" % (n, sims[(i, j)], items[i].id, items[i].what,
                                                                  items[j].id, items[j].what))
        print("audit samples written to %s: %d clusters, %d of %d near pairs" % (audit, len(chosen), len(sample), len(near)))


if __name__ == "__main__":
    main(sys.argv)
