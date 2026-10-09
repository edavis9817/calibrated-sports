"""f-36: tree_census.py's exit status, proved by doctoring a tree, not by reading the rule.

Each case builds two small trees (all five page kinds, 60 players a side), writes the
baseline the way a-78 did (the target's own tool, at the baseline's seed and sample), applies
one doctoring, and runs research/f30_cfb_build/tree_census.py as a separate process. The exit
code is the process's, read from the CompletedProcess - nothing sits between the two.

Every tree carries three DECLARED absences (a key college lacks, a key college carries empty,
a null parent with children beneath it), so the clean case is also the proof that a declared
absence does not turn the status red.

    python research/f36_exit_status/doctor.py --src <a-78 worktree> --src-opaque <a-72 worktree> \
        --work <an empty scratch directory> [--only case,case]

--src-opaque is a target whose tool treats player_summary.identity.ids as opaque (a-72's);
it is what makes the walker and the tool disagree. Writes only under --work. No store, no
network. Exits 1 if any case returns a status or a reason other than the one registered.
"""
import argparse
import copy
import json
import os
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
CENSUS = os.path.join(os.path.dirname(HERE), "f30_cfb_build", "tree_census.py")
SEED, SAMPLE, PLAYERS = 7, 50, 60
ENV = {"schema_version": 2}
ABSENCES = ["player_summary.identity.draft", "player_summary.identity.weight", "player_summary.prop_history"]
HEIGHT = "player_summary.identity.height"
GSIS = "player_summary.identity.ids.gsis"
TEAM_CITY = "team.city"


def dump(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f)


def build(root, sport, stamp, shape):
    """shape: what the COLLEGE side does differently. The first sport's side never changes."""
    cfb = sport == "cfb"
    env = {**ENV, "generated_at": stamp, "sport": sport}
    for i in range(PLAYERS):
        ident = {"name": "P%d" % i, "height": 70 + i % 5, "weight": 200, "draft": 1, "ids": {"gsis": "g%d" % i}}
        props = {"games": [{"line": 1.5}]}
        if cfb:
            del ident["draft"]                      # declared: a key college lacks
            ident["weight"] = None                  # declared: carried, empty in every file
            props = None                            # declared: a null parent
            if shape == "missing":
                del ident["height"]
            if shape == "undeclared":
                ident["height"] = None
            if shape == "ids":
                ident["ids"] = {"espn": "e%d" % i}
        d = os.path.join(root, sport, "players", "p%d" % i)
        dump(os.path.join(d, "summary.json"), {**env, "kind": "player_summary", "identity": ident,
                                               "career": {"stats": {"targets": 3 + i}}, "prop_history": props})
        dump(os.path.join(d, "2025.json"), {**env, "kind": "player_season",
                                            "periods": [{"stats": {"targets": 1 + i % 4}}]})
    dump(os.path.join(root, sport, "players", "index.json"),
         {**env, "kind": "player_index", "players": [{"id": "p%d" % i} for i in range(PLAYERS)]})
    dump(os.path.join(root, sport, "teams", "abc.json"),
         {**env, "kind": "team", "name": "Abc", "city": None if (cfb and shape == "team") else "Town"})
    dump(os.path.join(root, sport, "manifest.json"),
         {**env, "kind": "sport_manifest", "name": sport, **({"absences": [{"path": p} for p in ABSENCES]} if cfb else {})})


def tool_report(py, src, nfl, cfb, tmp):
    """The target's own tool at SEED / SAMPLE -> its JSON report (how a baseline is stated)."""
    out = os.path.join(tmp, "tool.json")
    code = ("import sys; sys.path.insert(0, %r); from research import cfb_contract_diff as D; D.SAMPLE = %d; "
            "raise SystemExit(D.main(['--nfl', %r, '--cfb', %r, '--json', %r, '--seed', '%d']))"
            % (src, SAMPLE, nfl, cfb, out, SEED))
    r = subprocess.run([py, "-c", code], cwd=src, capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit("the target's tool failed while stating a baseline:\n" + r.stderr[-2000:])
    with open(out, encoding="utf-8") as f:
        rep = json.load(f)
    for k in ("sport_manifest", "team", "player_index", "player_summary", "player_season"):
        if not rep["kinds"].get(k, {}).get("compared"):
            raise SystemExit("the synthetic tree has no comparable %s - the tool would compare nothing" % k)
    return rep


def baseline_from(rep, stamps, note):
    return {"source": "f-36 doctor.py: the target's own tool on this synthetic tree. " + note,
            "trees": stamps, "seed": SEED, "sample": SAMPLE,
            "totals": {k: rep["totals"].get(k, 0) for k in ("missing", "undeclared", "declared", "cfb_only")},
            "files": {s: {k: rep["kinds"][k][s + "_files"] for k in ("player_summary", "player_season")}
                      for s in ("cfb", "nfl")}}


def known(lst, path):
    return {"list": lst, "path": path, "source": "f-36 doctor.py, planted"}


# ---- doctoring applied AFTER the baseline was stated --------------------------------------
def b_total(b, cfb):
    b["totals"]["undeclared"] += 1


def b_known(*entries):
    def f(b, cfb):
        b["known"] = [known(*e) for e in entries]
    return f


def b_no_source(b, cfb):
    b["known"] = [{"list": "missing", "path": HEIGHT}]


def t_drop_player(b, cfb):
    shutil.rmtree(os.path.join(cfb, "cfb", "players", "p3"))


def t_one_file(b, cfb):
    p = os.path.join(cfb, "cfb", "players", "p3", "summary.json")
    with open(p, encoding="utf-8") as f:
        o = json.load(f)
    o["identity"]["height"] = None           # still filled in 59 other files: no path changes state
    o["identity"]["name"] = "SOMEBODY ELSE"
    dump(p, o)


def t_every_value(b, cfb):
    for i in range(PLAYERS):
        for name in ("summary.json", "2025.json"):
            p = os.path.join(cfb, "cfb", "players", "p%d" % i, name)
            with open(p, encoding="utf-8") as f:
                o = json.load(f)
            if name == "summary.json":
                o["career"]["stats"]["targets"] = 999999
                o["identity"]["name"] = "REWRITTEN %d" % i
            else:
                o["periods"][0]["stats"]["targets"] = 999999
            dump(p, o)


#  name                 shape         target    doctoring           exit  each must appear in the log
CASES = [
    ("clean",              None,         "src",    None,               0, ["EXIT 0", "3 beneath a declared absence + 0 MISSING"]),
    ("missing",            "missing",    "src",    None,               1, ["EXIT 1: the census reports MISSING " + HEIGHT]),
    ("missing-known",      "missing",    "src",    b_known(("missing", HEIGHT)), 0,
     ["MISSING " + HEIGHT, "acknowledged by the baseline: missing " + HEIGHT, "EXIT 0"]),
    ("missing-wrong-list", "missing",    "src",    b_known(("undeclared", HEIGHT)), 1,
     ["EXIT 1: the census reports MISSING " + HEIGHT, "'known' names undeclared " + HEIGHT]),
    ("undeclared",         "undeclared", "src",    None,               1, ["EXIT 1: the census reports UNDECLARED " + HEIGHT]),
    ("team-undeclared",    "team",       "src",    None,               1, ["EXIT 1: the tool's full run reports undeclared " + TEAM_CITY]),
    ("disagree",           "ids",        "opaque", None,               1,
     ["agrees with the tool's full run on missing and undeclared: False", "EXIT 1: the census reports MISSING " + GSIS,
      "EXIT 1: player_summary: the census and the tool's full run disagree on"]),
    ("disagree-known",     "ids",        "opaque", b_known(("missing", GSIS)), 0,
     ["agrees with the tool's full run on missing and undeclared: False", "acknowledged by the baseline: missing " + GSIS, "EXIT 0"]),
    ("step1",              None,         "src",    b_total,            1, ["step 1 -> DOES NOT REPRODUCE", "EXIT 1: step 1 DOES NOT REPRODUCE"]),
    ("stale-known",        None,         "src",    b_known(("missing", HEIGHT)), 1,
     ["EXIT 1: 'known' names missing " + HEIGHT]),
    ("known-no-source",    None,         "src",    b_no_source,        1, ["known[0] must carry exactly list, path and source"]),
    # the unit's step 4: the tree changes while the manifest (and so its stamp) does not
    ("stale-count",        None,         "src",    t_drop_player,      1, ["<- DIFFERS", "EXIT 1: step 1 DOES NOT REPRODUCE"]),
    ("stale-one-file",     None,         "src",    t_one_file,         0, ["(match)", "step 1 -> REPRODUCES", "EXIT 0"]),
    ("stale-every-value",  None,         "src",    t_every_value,      0, ["(match)", "step 1 -> REPRODUCES", "EXIT 0"]),
]


def run_case(case, a):
    name, shape, target, doctoring, want_rc, want_text = case
    src = os.path.abspath(a.src if target == "src" else a.src_opaque)
    work = os.path.join(os.path.abspath(a.work), name)
    if os.path.exists(work):
        raise SystemExit("%s exists - give --work an empty directory" % work)
    nfl, cfb, tmp = (os.path.join(work, d) for d in ("nfl_tree", "cfb_tree", "tmp"))
    os.makedirs(tmp)
    stamps = {"nfl": "f36-nfl-" + name, "cfb": "f36-cfb-" + name}
    build(nfl, "nfl", stamps["nfl"], None)
    build(cfb, "cfb", stamps["cfb"], shape)
    base = baseline_from(tool_report(sys.executable, src, nfl, cfb, tmp), stamps, "case %s." % name)
    stated = copy.deepcopy(base)
    if doctoring:
        doctoring(base, cfb)
    bpath, opath, lpath = (os.path.join(work, f) for f in ("baseline.json", "census.json", "census.log"))
    dump(bpath, base)
    env = {k: v for k, v in os.environ.items() if k not in ("LOGGER_DB", "DB_PATH")}
    env["F30_TMP"] = tmp
    r = subprocess.run([sys.executable, CENSUS, "--src", src, "--nfl", nfl, "--cfb", cfb,
                        "--baseline", bpath, "--out", opath], capture_output=True, text=True, env=env)
    log = r.stdout + r.stderr
    with open(lpath, "w", encoding="utf-8") as f:
        f.write(log)
    lacking = [t for t in want_text if t not in log]
    written = None
    if os.path.exists(opath):
        with open(opath, encoding="utf-8") as f:
            written = json.load(f)["exit"]["code"]
    # the status in the written file is the status the process returned, or there is no file
    file_ok = written == r.returncode if written is not None else name == "known-no-source"
    return {"case": name, "rc": r.returncode, "want_rc": want_rc, "written_code": written, "lacking": lacking,
            "ok": r.returncode == want_rc and not lacking and file_ok,
            "baseline_totals": stated["totals"], "log": lpath,
            "exit_lines": [ln.strip() for ln in log.splitlines() if "EXIT " in ln or ln.startswith("baseline:")][:8]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True)
    ap.add_argument("--src-opaque", required=True)
    ap.add_argument("--work", required=True)
    ap.add_argument("--only", default="")
    ap.add_argument("--jobs", type=int, default=6)
    a = ap.parse_args()
    only = {c for c in a.only.split(",") if c}
    cases = [c for c in CASES if not only or c[0] in only]
    if only - {c[0] for c in cases}:
        raise SystemExit("no such case: %s" % sorted(only - {c[0] for c in cases}))
    with ThreadPoolExecutor(a.jobs) as ex:
        results = list(ex.map(lambda c: run_case(c, a), cases))
    for r in results:
        print("%-19s exit %s (registered %s)  written %s  -> %s" % (
            r["case"], r["rc"], r["want_rc"], r["written_code"], "AS REGISTERED" if r["ok"] else "NOT AS REGISTERED"))
        for ln in r["exit_lines"]:
            print("      " + ln[:230])
        for t in r["lacking"]:
            print("      LACKS: " + t)
    bad = [r["case"] for r in results if not r["ok"]]
    if len(results) != len(cases) or not results:
        raise SystemExit("ran %d of %d cases" % (len(results), len(cases)))
    print("\n%d cases; %d as registered; not as registered: %s" % (len(results), len(results) - len(bad), bad or "none"))
    with open(os.path.join(os.path.abspath(a.work), "doctor.json"), "w", encoding="utf-8") as f:
        json.dump(results, f, indent=1)
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
