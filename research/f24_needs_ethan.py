"""How many `needs_ethan` items sit in the relay's machine reports (unit f-24).

    python -m research.f24_needs_ethan "C:/Users/.../code/_relay"

Counts every `needs_ethan` entry in `reports/json/*.json`, by track and by age.
Age is the unit's last `runlog.jsonl` timestamp, falling back to the report
file's mtime.

WHAT THIS CANNOT SAY. The report schema has no field recording that an item was
answered, so "unactioned" is not measurable from these files: this counts items
RAISED. The only actioned-marker anywhere is prose in DECISIONS.md and
NEEDS-ETHAN.md, and matching prose to items by text is the proxy this project
keeps getting burned by. The count is therefore an upper bound on what is open.
"""
import collections
import datetime as dt
import glob
import json
import os
import sys

BUCKETS = ((2, "0-2 days"), (7, "3-7 days"), (14, "8-14 days"), (10 ** 6, "15+ days"))


def unit_times(relay):
    out = {}
    path = os.path.join(relay, "runlog.jsonl")
    if os.path.exists(path):
        for line in open(path, encoding="utf-8-sig"):
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
                out[row["unit"]] = dt.datetime.fromisoformat(row["ts"]).timestamp()
            except (ValueError, KeyError):
                continue
    return out


def count(relay, now):
    files = sorted(glob.glob(os.path.join(relay, "reports", "json", "*.json")))
    if not files:
        raise SystemExit("no machine reports under %s" % relay)
    times, rows, unreadable = unit_times(relay), [], []
    for f in files:
        try:
            d = json.load(open(f, encoding="utf-8-sig"))
        except ValueError:
            unreadable.append(os.path.basename(f))
            continue
        unit = d.get("unit_id") or os.path.basename(f)[:-5]
        ts = times.get(unit) or os.path.getmtime(f)
        for n in d.get("needs_ethan") or []:
            rows.append({"unit": unit, "track": d.get("track"), "age_days": (now - ts) / 86400,
                         "irreversible": bool(n.get("irreversible")),
                         "spends_money": bool(n.get("spends_money")), "what": n.get("what", "")})
    return files, rows, unreadable


def bucket(days):
    return next(label for limit, label in BUCKETS if days <= limit)


def main(argv):
    relay = argv[1]
    now = dt.datetime.now(dt.timezone.utc).timestamp()
    files, rows, unreadable = count(relay, now)
    units = {r["unit"] for r in rows}
    print("%d machine reports read, %d unreadable %s" % (len(files) - len(unreadable), len(unreadable), unreadable))
    print("%d needs_ethan items across %d units (%d irreversible, %d spend money)"
          % (len(rows), len(units), sum(r["irreversible"] for r in rows), sum(r["spends_money"] for r in rows)))
    table = collections.Counter((r["track"], bucket(r["age_days"])) for r in rows)
    labels = [b for _, b in BUCKETS]
    print("%-6s" % "track" + "".join("%12s" % b for b in labels) + "%8s" % "total")
    for t in sorted({r["track"] for r in rows}):
        print("%-6s" % t + "".join("%12d" % table[(t, b)] for b in labels)
              + "%8d" % sum(table[(t, b)] for b in labels))
    print("%-6s" % "all" + "".join("%12d" % sum(table[(t, b)] for t in {r["track"] for r in rows}) for b in labels)
          + "%8d" % len(rows))
    ages = sorted(r["age_days"] for r in rows)
    print("age in days: median %.1f, oldest %.1f, newest %.1f" % (ages[len(ages) // 2], ages[-1], ages[0]))
    print("NOT MEASURED: how many were answered. The schema has no field for it.")


if __name__ == "__main__":
    main(sys.argv)
