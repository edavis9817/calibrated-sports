"""f-31: a-75's required-sentence attachment, attacked. a-75 computed no interval of its own, so
this is a census-style attack (README, "Claims without an interval"): steps 2, 4, 5 do not apply
and the through-the-target blocks test is NOT RUN.

    python research/f26_reliability/sentences_a75.py --src <a-75 worktree> --out <a-75's export dir> \
        [--served <dir of files fetched from production>]

Three questions, each driven to its OTHER answer:
  A  are the carried figures the ones track F's logs hold?  (F's own run files in THIS repo are
     the reference; a planted figure must be reported missing)
  B  can served_matches read False, and does anything refuse when it does?
  C  where is the weeks 1-4 figure served, and which of those places carries the sentence?
"""
import argparse
import copy
import glob
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
RUN1, RUN2 = (os.path.join(HERE, "runs", d) for d in ("2026-10-08-run1", "2026-10-08-run2-f27"))


def nums(s):
    return [float(x) for x in re.findall(r"[+-]?\d+\.\d+", s)]


def walk(o, path=""):
    if isinstance(o, dict):
        yield path, o
        for k, v in o.items():
            yield from walk(v, path + "." + k if path else k)
    elif isinstance(o, list):
        for i, v in enumerate(o):
            yield from walk(v, "%s[%d]" % (path, i))


def floats_of(o):
    out = []
    for _p, d in walk(o):
        for k, val in d.items():
            if isinstance(val, float):
                out.append((k, val))
            elif isinstance(val, list):
                out += [(k, x) for x in val if isinstance(x, float)]
    return out


def reference():
    """F's committed run files, re-read by THIS script."""
    v2 = json.load(open(os.path.join(RUN2, "verdicts.json"), encoding="utf-8"))
    text = {}
    for d, n in ((RUN1, "c31.log"), (RUN2, "c31_served.log"), (RUN2, "c30.log"), (RUN1, "c28.log"), (RUN2, "c28.log")):
        p = os.path.join(d, n)
        if os.path.exists(p):
            text[os.path.basename(d) + "/" + n] = open(p, encoding="utf-8", errors="replace").read()
    return v2, text


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--served")
    a = ap.parse_args()
    sys.path.insert(0, os.path.abspath(a.src))
    from jobs import required_sentences as RS
    assert os.path.abspath(RS.__file__).startswith(os.path.abspath(a.src)), RS.__file__
    bad = 0
    carried = RS.carried()
    v2, text = reference()
    blob = json.dumps(v2) + "\n".join(text.values())
    have = {round(abs(x), 4) for x in nums(blob)}

    print("A. carried figures against F's committed run files (%d files, %d distinct magnitudes)" % (len(text) + 1, len(have)))
    n_fig = n_found = 0
    for e in carried["sentences"]:
        want = floats_of({"measured_against": e["measured_against"], "figures": e["figures"]})
        miss = [(k, x) for k, x in want if round(abs(x), 4) not in have]
        n_fig += len(want)
        n_found += len(want) - len(miss)
        print("  %-62s %2d floats, %d not found in F's files %s" % (RS.figure_id(e["file"], e["holder"]), len(want), len(miss), miss or ""))
    print("  -> %d of %d carried floats appear somewhere in F's run files (a VALUE match, not an identity: A2 is the identity)" % (n_found, n_fig))
    checks = [
        ("vs_close served", "c31_served.log", [0.0040, 0.0019, 0.0063]),
        ("vs_close no-wind", "c31_served.log", [0.0056, 0.0035, 0.0079]),
        ("vs_close wind_worth", "c31_served.log", [0.0016, 0.0004, 0.0027]),
        ("league: registered | no-wind", "run1/c31.log", [0.0075, 0.0065, 0.0078, 0.0052]),
        ("season_avg: registered | no-wind", "run1/c31.log", [0.0063, 0.0053, 0.0067, 0.0040]),
        ("pooled dBrier(E-N0)", "c30.log", [0.0009, 0.0003, 0.0016]),
    ]
    for name, f, want in checks:
        t = "\n".join(v for k, v in text.items() if k.endswith(f))
        lines = [ln for ln in t.splitlines() if all(any(abs(abs(x) - w) < 5e-5 for x in nums(ln)) for w in want)]
        bad += not lines
        print("  A2 %-34s one line of %-15s holds %s: %s" % (name, f, want, "YES" if lines else "NO"))
        for ln in lines[:1]:
            print("       | " + ln.strip()[:240])
    c30 = "\n".join(v for k, v in text.items() if k.endswith("c30.log"))
    for key, pat in (("p 0.3786", r"0\.378\d"), ("ratio 1.00", r"\b1\.00\b"), ("72", r"\b72\b")):
        ok = bool(re.search(pat, c30))
        bad += not ok
        print("  A2 run-2 c30.log carries %-12s %s" % (key, ok))
    vd = v2.get("verdicts", v2)
    a63 = json.dumps(vd.get("a-63", ""))
    c28 = json.dumps(vd.get("c-28", "")) + json.dumps(json.load(open(os.path.join(RUN1, "verdicts.json"), encoding="utf-8")))
    for w in (-0.0008, -0.0024, 0.0009, -0.0017, -0.0031, -0.0004):
        ok = any(abs(x - w) < 5e-5 for x in nums(a63))
        bad += not ok
        print("  A2 run-2 a-63 verdict holds %+.4f: %s" % (w, ok))
    for pat in (r"25 of 25", r"\b40\b", r"\b120\b", r"one seed"):
        ok = bool(re.search(pat, a63 + c28))
        bad += not ok
        print("  A2 the a-63 / c-28 verdicts carry %-10r %s" % (pat, ok))
    planted = round(0.0056 + 0.0111, 4)
    print("  PLANT a carried no-wind of %.4f is found in F's files: %s (must be False)" % (planted, planted in have))
    bad += planted in have

    print("\nB. served_matches and the gate, driven to the other answer")
    for e in carried["sentences"]:
        served = {"estimate": e["measured_against"]["estimate"], "interval": e["measured_against"]["interval"]}
        q = RS.qualifier(e["file"], e["holder"], served)
        q2 = RS.qualifier(e["file"], e["holder"], dict(served, estimate=round(served["estimate"] + 0.0002, 4)))
        q3 = RS.qualifier(e["file"], e["holder"], dict(served, estimate=served["estimate"] + 0.00004))
        q4 = RS.qualifier(e["file"], e["holder"], dict(served, interval=[-0.9, 0.9]))
        print("  %-62s same %s | est +0.0002 %s | est +0.00004 %s | interval replaced by [-0.9, +0.9] %s"
              % (RS.figure_id(e["file"], e["holder"]), q["served_matches"], q2["served_matches"], q3["served_matches"],
                 q4["served_matches"]))
        bad += (q["served_matches"] is not True) or (q2["served_matches"] is not False)
    files = {}
    for f in glob.glob(os.path.join(a.out, "game", "nfl", "**", "*.json"), recursive=True):
        files[os.path.relpath(f, a.out).replace("\\", "/")] = json.load(open(f, encoding="utf-8"))
    print("  a-75's own export output: %d files" % len(files))
    try:
        print("  gate on it, untouched: " + RS.require(files))
    except Exception as x:                                            # noqa: BLE001
        bad += 1
        print("  gate on it, untouched: RAISED %s" % str(x)[:200])
    rt, rs_ = "game/nfl/record_total.json", "game/nfl/record_spread.json"

    def gate(mut, label, must_raise):
        nonlocal bad
        f2 = copy.deepcopy(files)
        mut(f2)
        try:
            RS.require(f2)
            raised, msg = False, ""
        except RS.MissingSentence as x:
            raised, msg = True, str(x)[:100]
        bad += raised != must_raise
        print("  gate PLANT %-76s raises %-5s (must %s) %s" % (label, raised, must_raise, msg))

    gate(lambda f: f[rt]["vs_close"].__setitem__("qualifier", None), "record_total vs_close: sentence removed", True)
    gate(lambda f: f[rs_]["against_shape"]["qualifier"].__setitem__("statement", "x"), "record_spread: sentence reworded by hand", True)

    def move_served(f):
        f[rt]["vs_close"]["d_brier"]["estimate"] = 0.0090
    gate(move_served, "record_total vs_close: served estimate moved to +0.0090, OLD qualifier left", True)

    def move_and_rebuild(f):
        b = f[rt]["vs_close"]
        b["d_brier"]["estimate"] = 0.0090
        b["qualifier"] = RS.qualifier(rt, "vs_close", b["d_brier"])
    gate(move_and_rebuild, "same move, qualifier REBUILT as a real export would rebuild it", False)
    b = copy.deepcopy(files[rt]["vs_close"])
    b["d_brier"]["estimate"] = 0.0090
    q = RS.qualifier(rt, "vs_close", b["d_brier"])
    print("     -> that file passes the gate with served_matches=%s, wording: ...%s" % (q["served_matches"], q["statement"][-190:]))
    print("     -> served_matches CAN read False; when it does NOTHING refuses. It is a flag in the file, not a gate.")
    n_true = sum(1 for k, p in files.items() for _p, d in walk(p) if isinstance(d.get("qualifier"), dict) and d["qualifier"]["served_matches"] is True)
    n_q = sum(1 for k, p in files.items() for _p, d in walk(p) if isinstance(d.get("qualifier"), dict))
    print("  in a-75's export: %d qualifier blocks, served_matches true on %d" % (n_q, n_true))

    print("\nC. where the weeks 1-4 figure is served in a-75's own export, and where its sentence is")
    by_file = {}
    for key, payload in sorted(files.items()):
        for _path, d in walk(payload):
            if d.get("stage") == "weeks_1_4" and "vs_elo_nomov" in d:
                by_file.setdefault("game/nfl/matchup/*" if "/matchup/" in key else key, []).append(d.get("qualifier") is not None)
    for k, v in sorted(by_file.items()):
        print("  %-28s %2d holder(s) of the stage figure, %d with the sentence" % (k, len(v), sum(v)))
    rec = files.get("game/nfl/record.json")
    if rec:
        w = rec["by_stage"]["weeks_1_4"]
        print("  record.json by_stage.weeks_1_4.vs.elo_nomov = %s" % json.dumps(w["vs"]["elo_nomov"])[:260])
        print("  a `qualifier` key anywhere under record.json by_stage.weeks_1_4: %s ; anywhere in record.json: %s"
              % (any("qualifier" in d for _p, d in walk(w)), any("qualifier" in d for _p, d in walk(rec))))
        print("  REQUIRED (the figures the gate knows) = %s" % list(RS.REQUIRED))
        print("  record.json among them: %s  -> the gate cannot refuse record.json for it" % any("record.json" in r for r in RS.REQUIRED))
    n_tot = bare = 0
    for k, p in files.items():
        if "/matchup/" in k and not k.endswith("index.json"):
            n_tot += 1
            r = (((p.get("numbers") or {}).get("total") or {}).get("record") or {})
            bare += bool(r) and not any(d.get("qualifier") for _p, d in walk(r))
    print("  matchup files whose numbers.total.record carries total figures with NO sentence: %d of %d" % (bare, n_tot))

    if a.served:
        print("\nD. what PRODUCTION serves (fetched over HTTP into %s)" % a.served)
        for name in ("record.json", "record_total.json", "record_spread.json", "forecast.json"):
            p = os.path.join(a.served, name)
            if not os.path.exists(p):
                print("  %-20s NOT FETCHED" % name)
                continue
            try:
                d = json.load(open(p, encoding="utf-8"))
            except ValueError:
                print("  %-20s not JSON (%d bytes)" % (name, os.path.getsize(p)))
                continue
            nq = sum(1 for _p, o in walk(d) if o.get("qualifier") is not None)
            print("  %-20s generated_at %s  objects with a non-null qualifier: %d" % (name, d.get("generated_at"), nq))
            if name == "record.json":
                print("     by_stage.weeks_1_4.vs.elo_nomov = %s" % json.dumps(d["by_stage"]["weeks_1_4"]["vs"]["elo_nomov"])[:260])
            if name == "forecast.json":
                st = d.get("season_stage") or {}
                for lab, blk in (("season_stage", st), ("season_stage.other", st.get("other"))):
                    if blk:
                        print("     %s stage=%s vs_elo_nomov=%s\n        statement=%r" % (lab, blk.get("stage"), json.dumps(blk.get("vs_elo_nomov"))[:130], (blk.get("statement") or "")[:200]))
    print("\nchecks that came out the wrong way: %d" % bad)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
