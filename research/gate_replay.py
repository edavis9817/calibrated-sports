"""How often would a-79's refusal have fired on what is already exported? (a-82)

    python -m research.gate_replay                      # WEB_EXPORT_DIR, read only
    python -m research.gate_replay --dest D:/x --out research/results/a82_gate_replay.json

Reads every `game/nfl/**.json` in an export tree and asks of each the one question
a-79's gate refuses on: is a registered figure it serves
(`research/results/f27_required_sentences.json`) still the one its sentence was
measured beside, at four decimals (`jobs.required_sentences.moved`)? Opens no
database and writes nothing but `--out`.

WHAT THIS CAN AND CANNOT SAY. An export tree holds ONE copy of each key: the
forecast, the three records and the index are overwritten every run, so they are
one observation each, whatever the number of runs. Only the matchup files
accumulate, one per game per week, and each of those repeats the record figures
and the stage figure as they stood when that week was last built. So the replay
covers every WEEK that still has matchup files on disk and exactly one build of
each; it does not cover the runs in between.

`sentence` is reported beside `moved` and is a different question: whether the
file would pass the WHOLE sentence gate (`require`). A file written by a tree
without a-75 carries no qualifier and fails it for that reason alone - that is
"written before the gate existed", not a freeze, and it is counted apart.
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from jobs import required_sentences as RS                   # noqa: E402

PREFIX = "game/nfl/"
WEEK = re.compile(r"matchup/(\d{4})_(\d{2})_")


def game_files(dest):
    """-> {key: path} for every *.json under dest/game/nfl."""
    root = os.path.join(dest, *PREFIX.strip("/").split("/"))
    out = {}
    for d, _dirs, names in os.walk(root):
        for fn in names:
            if fn.endswith(".json"):
                path = os.path.join(d, fn)
                out[os.path.relpath(path, dest).replace(os.sep, "/")] = path
    return out


def group(key):
    m = WEEK.search(key)
    return f"{m.group(1)} week {int(m.group(2))}" if m else "current (overwritten every run)"


def replay(dest):
    rows = []
    for key, path in sorted(game_files(dest).items()):
        row = {"key": key, "group": group(key), "generated_at": None, "figures": 0,
               "moved": [], "unread": None, "sentence": None}
        try:
            with open(path, encoding="utf-8") as f:
                payload = json.load(f)
            row["generated_at"] = payload.get("generated_at")
            row["figures"] = len(RS.located(key, payload))
            row["moved"] = RS.moved({key: payload})
        except Exception as e:  # noqa: BLE001 - an unread file is counted, never passed
            row["unread"] = f"{type(e).__name__}: {e}"
            rows.append(row)
            continue
        try:
            RS.require({key: payload})
            row["sentence"] = "passes"
        except RS.MissingSentence as e:
            msg = str(e)
            row["sentence"] = ("moved" if "has moved" in msg else
                               "no sentence" if "without its sentence" in msg else "other")
        rows.append(row)
    return rows


def summarise(dest, rows):
    by = collections.OrderedDict()
    for r in rows:
        g = by.setdefault(r["group"], {"files": 0, "figures": 0, "would_refuse": 0, "unread": 0,
                                       "made": set()})
        g["files"] += 1
        g["figures"] += r["figures"]
        g["would_refuse"] += bool(r["moved"])
        g["unread"] += r["unread"] is not None
        if r["generated_at"]:
            g["made"].add(r["generated_at"])
    for g in by.values():
        made = sorted(g.pop("made"))
        g["generated_at"] = [made[0], made[-1]] if made else None
    places = collections.Counter(mv["figure"] for r in rows for mv in r["moved"])
    return {"dest": dest, "carried": RS.CARRIED, "registered": list(RS.REQUIRED),
            "files": len(rows),
            "files_serving_a_registered_figure": sum(1 for r in rows if r["figures"]),
            "figure_readings": sum(r["figures"] for r in rows),
            "would_refuse": sum(1 for r in rows if r["moved"]),
            "would_refuse_at": dict(sorted(places.items())),
            "unread": [{"key": r["key"], "why": r["unread"]} for r in rows if r["unread"]],
            "sentence_gate": dict(collections.Counter(r["sentence"] for r in rows
                                                      if r["sentence"])),
            "by_group": by,
            "moved": [mv for r in rows for mv in r["moved"]]}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dest", help="an export tree; default WEB_EXPORT_DIR")
    ap.add_argument("--out", help="write the summary here as JSON")
    a = ap.parse_args(argv)
    dest = a.dest
    if dest is None:
        from jobs import export_web as E
        dest = E.require_setting("WEB_EXPORT_DIR")
    rows = replay(dest)
    if not rows:
        print(f"no game file under {dest}/{PREFIX} - nothing was replayed", file=sys.stderr)
        return 1
    s = summarise(dest, rows)
    print(f"{s['files']} game file(s) under {dest}; {s['files_serving_a_registered_figure']} "
          f"serve a registered figure, {s['figure_readings']} reading(s) in all")
    for name, g in s["by_group"].items():
        print(f"  {name:<34} files {g['files']:>3}  readings {g['figures']:>3}  "
              f"would refuse {g['would_refuse']:>3}  unread {g['unread']}  made {g['generated_at']}")
    print(f"would refuse: {s['would_refuse']} file(s) at {s['would_refuse_at'] or 'no place'}")
    print(f"whole sentence gate on the same files: {s['sentence_gate']}")
    for u in s["unread"]:
        print(f"UNREAD {u['key']}: {u['why']}", file=sys.stderr)
    if a.out:
        with open(a.out, "w", encoding="utf-8") as f:
            json.dump(s, f, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
