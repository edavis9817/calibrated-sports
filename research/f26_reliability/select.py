"""f-26 - pick this run's targets, count needs_ethan repeats, keep the marker.

    python research/f26_reliability/select.py <relay dir>                 # rank, print, write nothing
    python research/f26_reliability/select.py <relay dir> --needs-ethan   # the repeat count for the window
    python research/f26_reliability/select.py <relay dir> --record verdicts.json
                                                                         # append this run to the marker

Reads `reports/json/*.json` and `runlog.jsonl`; writes ONLY
`<relay>/state/f26-marker.json`, and only under --record. The rule is in
README.md and is the code below - nothing here asks a model which unit matters.

THE WINDOW. Every unit that finished after `window_start` and has no verdict in
the marker. `window_start` is fixed on the first run at f-23's finish (the last
adversarial unit before this one existed) and never moves, so a unit this run
ranked and did not reach is still there next run. A unit leaves the window only
by receiving a verdict or an explicit `declined` entry with its reason.

needs_ethan is counted over the window units NOT in the marker's `seen` list, so
an item is counted by exactly one run however long its unit waits for a verdict.

THE RANK. A unit is a TARGET only if its report states an estimate with an
interval ("+0.104 [+0.039, +0.176]") - without one there is no claim to attack
with this checklist. Score:
    +3  a page will publish it: the unit's own files_changed touch an export, the
        contract or a site route, OR a unit that does names this unit in its report
    +1  the report mentions its MDE (c-32 and c-27 both sat on theirs)
    +1  the summary's first interval excludes zero (a positive result has more
        ways to be wrong than a null)
Ties break newest first.
"""
import argparse
import datetime as dt
import glob
import json
import os
import re
import sys

WINDOW_START = "2026-09-30T01:38:40-04:00"          # f-23 finished
PRIOR_ATTACKS = {"a-57": "f-22", "c-27": "f-23"}    # attacked before the marker existed
SELF_TRACK_ATTACKS = re.compile(r"^f-(2[123]|30)$")   # attack units that left no file in this directory: f-21..f-23 (before it existed), f-30 (research/f30_cfb_build/)
AGENT_DIR = "research/f26_reliability/"


def is_attack_unit(unit, d):
    """The adversarial units themselves are not targets. f-31: this was a regex of unit numbers that every run had
    to extend by hand (it stopped at f-26 and ranked f-27; extended to f-27/f-29 it also swallowed f-28, which is
    NOT an attack unit, and still missed f-30 and f-31). The rule is now the evidence: a track-F unit whose own
    files_changed touch this directory ran the agent. The regex keeps only the units that rule cannot see."""
    if SELF_TRACK_ATTACKS.match(unit):
        return "listed"
    if unit.startswith("f-") and any(AGENT_DIR in f.replace("\\", "/") for f in d.get("files_changed") or []):
        return "its files_changed touch " + AGENT_DIR
    return None
INTERVAL = re.compile(r"([+-]?\d+\.\d+)(?:pp|c|%)?\s*\[\s*([+-]?\d+\.\d+)\s*,\s*([+-]?\d+\.\d+)\s*\]")
PUBLISHES = re.compile(r"(^|/)(jobs/[a-z_]*export[a-z_]*\.py|jobs/game_[a-z_]+\.py|web/contract/|app/|lib/)")
MERGE_ASK = re.compile(r"\bmerg(e|ing)\b|\bto (origin/)?main\b", re.I)
UNIT_ID = re.compile(r"\b([abcf]-\d{1,3})\b")
STOP = set("the a an of to in on for and or is are be was were it its this that with by as at from not no "
           "whether should which what how do does into than then there their has have had will would can could "
           "any all one two before after until yet still also only".split())


def marker_path(relay):
    return os.path.join(relay, "state", "f26-marker.json")


def load_marker(relay):
    p = marker_path(relay)
    if not os.path.exists(p):
        return {"window_start": WINDOW_START,
                "verdicts": {u: {"by": b, "run": None, "verdict": "attacked before f-26 existed"}
                             for u, b in PRIOR_ATTACKS.items()},
                "declined": {}, "runs": []}
    return json.load(open(p, encoding="utf-8"))


def finish_times(relay):
    out = {}
    p = os.path.join(relay, "runlog.jsonl")
    if os.path.exists(p):
        for line in open(p, encoding="utf-8-sig"):
            try:
                row = json.loads(line)
                out[row["unit"]] = dt.datetime.fromisoformat(row["ts"]).timestamp()
            except (ValueError, KeyError):
                continue
    return out


def load_reports(relay):
    files = sorted(glob.glob(os.path.join(relay, "reports", "json", "*.json")))
    if not files:
        raise SystemExit("no machine reports under %s" % relay)
    times, out, bad = finish_times(relay), {}, []
    for f in files:
        try:
            d = json.load(open(f, encoding="utf-8-sig"))
        except ValueError:
            bad.append(os.path.basename(f))
            continue
        u = d.get("unit_id") or os.path.basename(f)[:-5]
        d["_ts"] = times.get(u) or os.path.getmtime(f)
        out[u] = d
    return out, bad


def text_of(d):
    return " ".join([d.get("summary", "")] + [f.get("what", "") for f in d.get("findings") or []]
                    + [v.get("claim", "") for v in d.get("verified_by") or []])


def score(unit, reports):
    """-> dict(score, reasons, intervals) or None when the unit states no interval."""
    d = reports[unit]
    ivs = INTERVAL.findall(text_of(d))
    if not ivs:
        return None
    s, why = 0, []
    own = [f for f in d.get("files_changed") or [] if PUBLISHES.search(f.replace("\\", "/"))]
    carriers = sorted(u for u, o in reports.items() if u != unit
                      and any(PUBLISHES.search(f.replace("\\", "/")) for f in o.get("files_changed") or [])
                      and unit in UNIT_ID.findall(text_of(o)))
    if own or carriers:
        s += 3
        why.append("published" + (" by " + ",".join(carriers) if carriers else " (own files)"))
    if re.search(r"\bMDE\b", text_of(d)):
        s += 1
        why.append("names its MDE")
    first = INTERVAL.search(d.get("summary", ""))
    if first and (float(first.group(2)) > 0 or float(first.group(3)) < 0):
        s += 1
        why.append("headline interval excludes zero")
    return {"unit": unit, "score": s, "why": why, "intervals": len(ivs), "ts": d["_ts"],
            "headline": (first.group(0) if first else ivs and "%s [%s, %s]" % ivs[0])}


def window(reports, marker):
    start = dt.datetime.fromisoformat(marker["window_start"]).timestamp()
    done = set(marker["verdicts"]) | set(marker["declined"])
    return sorted((u for u, d in reports.items() if d["_ts"] > start and u not in done
                   and not is_attack_unit(u, d)), key=lambda u: reports[u]["_ts"])


def rank(reports, marker):
    win = window(reports, marker)
    scored = [x for x in (score(u, reports) for u in win) if x]
    scored.sort(key=lambda x: (-x["score"], -x["ts"]))
    return win, scored, [u for u in win if u not in {x["unit"] for x in scored}]


# ------------------------------------------------------------------ needs_ethan

def tokens(s):
    # unit ids stay whole ("b-94"): without them "Merge order for b-94" and "Merge order
    # for c-24 and c-27" tokenise to the same two words and score 1.00
    return {w for w in re.findall(r"[a-z]-\d+|[a-z0-9_]+", s.lower()) if len(w) > 2 and w not in STOP}


def jaccard(a, b):
    return len(a & b) / len(a | b) if a and b else 0.0


def needs_ethan(reports, units, threshold=0.5):
    """For each needs_ethan item raised by `units`: the most similar item raised by a unit
    that finished EARLIER, by token-set Jaccard on `what`. A TEXT PROXY - see README."""
    pool = [(d["_ts"], u, n.get("what", ""), tokens(n.get("what", "")))
            for u, d in reports.items() for n in d.get("needs_ethan") or []]
    rows = []
    for u in units:
        for n in reports[u].get("needs_ethan") or []:
            t = tokens(n.get("what", ""))
            best = max(((jaccard(t, pt), pu, pw) for ts, pu, pw, pt in pool
                        if ts < reports[u]["_ts"] and pu != u), default=(0.0, None, ""))
            rows.append({"unit": u, "what": n.get("what", ""), "best_sim": best[0], "earlier_unit": best[1],
                         "earlier_what": best[2], "repeat": best[0] >= threshold})
    return rows


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("relay")
    ap.add_argument("--needs-ethan", action="store_true")
    ap.add_argument("--record")
    a = ap.parse_args(argv[1:])
    reports, bad = load_reports(a.relay)
    marker = load_marker(a.relay)
    win, scored, no_claim = rank(reports, marker)
    print("%d machine reports (%d unreadable %s); window opens %s; %d units in it, %d with an interval claim"
          % (len(reports), len(bad), bad, marker["window_start"], len(win), len(scored)))
    if not win:
        print("EMPTY WINDOW: nothing finished since the last run that has no verdict. That is a result, not an error.")
    for x in scored:
        print("  %-5s score %d  %-52s %2d intervals  headline %s"
              % (x["unit"], x["score"], "; ".join(x["why"]) or "-", x["intervals"], x["headline"]))
    print("  no interval stated (not targets for this checklist): %s" % ", ".join(no_claim))
    excl = {u: is_attack_unit(u, d) for u, d in reports.items() if is_attack_unit(u, d)}
    print("  excluded as the agent's own units (never targets): %s"
          % ", ".join("%s (%s)" % kv for kv in sorted(excl.items(), key=lambda kv: int(kv[0][2:]))))
    if a.needs_ethan:
        seen = set(marker.get("seen") or [])
        new_units = [u for u in win if u not in seen]      # counted once: a unit an earlier run saw is not recounted
        rows = needs_ethan(reports, new_units)
        print("\n%d of the %d window units are new since the last recorded run" % (len(new_units), len(win)))
        total_all = sum(len(d.get("needs_ethan") or []) for d in reports.values())
        print("needs_ethan: %d items raised by those %d units (%d across all %d reports)"
              % (len(rows), len(new_units), total_all, len(reports)))
        merges = [r for r in rows if MERGE_ASK.search(r["what"])]
        print("  of them 'merge / merge order / deploy this branch' asks (one CLASS, a different branch each): %d"
              % len(merges))
        for th in (0.4, 0.5, 0.6):
            print("  repeat an earlier unit's item at Jaccard >= %.1f: %d" % (th, sum(r["best_sim"] >= th for r in rows)))
        for r in sorted(rows, key=lambda r: -r["best_sim"]):
            if r["best_sim"] >= 0.25:
                print("  %.2f %-5s %s\n       ~ %-5s %s" % (r["best_sim"], r["unit"], r["what"][:150],
                                                              r["earlier_unit"], r["earlier_what"][:150]))
    if a.record:
        run = json.load(open(a.record, encoding="utf-8"))
        for u, v in run["verdicts"].items():
            if u not in reports:
                raise SystemExit("verdict for %s, which has no machine report" % u)
            marker["verdicts"][u] = {"by": run.get("by", "f-26"), "run": run["run_id"], "verdict": v}
        for u, why in (run.get("declined") or {}).items():
            marker["declined"][u] = {"run": run["run_id"], "why": why}
        marker["seen"] = sorted(set(marker.get("seen") or []) | set(win))
        marker["runs"].append({"run_id": run["run_id"], "at": run["at"], "code": run.get("code"), "window_units": win,
                               "attacked": sorted(run["verdicts"]),
                               "ranked_not_reached": [x["unit"] for x in scored if x["unit"] not in run["verdicts"]]})
        with open(marker_path(a.relay), "w", encoding="utf-8") as f:
            json.dump(marker, f, indent=1)
        print("\nmarker written: %s (%d verdicts, %d runs)" % (marker_path(a.relay), len(marker["verdicts"]),
                                                                len(marker["runs"])))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
