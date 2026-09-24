"""f-19: plant faults into a scratch clone of a-22 (c1d93f7) and record whether a-22's tests catch each.
Every mutation is applied to a tracked file, the test file is run, and the file is restored with
`git checkout` - then `git status --porcelain` is asserted EMPTY, so a restore that did nothing is caught."""
import json, re, subprocess, sys, os
CLONE = r"D:\temp\f19\a22"
PY = r"C:\Users\Ethan Davis\code\calibrated-sports\.venv\Scripts\python.exe"
REG, EXP, CON = "jobs/source_registry.py", "jobs/export_web.py", "web/contract/v2/contract.schema.json"

def edit(path, old, new):
    p = os.path.join(CLONE, path)
    s = open(p, encoding="utf-8").read()
    assert s.count(old) == 1, (path, old, s.count(old))
    open(p, "w", encoding="utf-8", newline="").write(s.replace(old, new))

def append(path, text):
    with open(os.path.join(CLONE, path), "a", encoding="utf-8", newline="") as f:
        f.write(text)

def contract_add_kind(_):
    p = os.path.join(CLONE, CON)
    s = open(p, encoding="utf-8").read()
    old = '"sources": "SourcesFile"'
    assert s.count(old) == 1
    open(p, "w", encoding="utf-8", newline="").write(s.replace(old, old + ',\n      "boards": "SourcesFile"'))

PLANTED_FN = '\n\ndef load_f19_planted(con):\n    return con.execute({sql}).fetchall()\n'
FAULTS = [
  # the four a-22 claims
  ("C1 undeclared contract kind", CON, contract_add_kind, "claimed"),
  ("C2 unregistered id", REG, lambda _: edit(REG, '"live.prices": ("kalshi.ladders",),',
                                             '"live.prices": ("kalshi.ladders", "espn.scoreboard"),'), "claimed"),
  ("C3 unread source", REG, lambda _: edit(REG, '    "rss.headlines": dict(',
      '    "espn.scoreboard": dict(name="ESPN", sports=("nfl",), layer="FACTS", provides="-",\n'
      '        used_for="-", last_read=("elsewhere", "none")),\n    "rss.headlines": dict('), "claimed"),
  ("C4 export_web table with no source", EXP,
      lambda _: append(EXP, PLANTED_FN.format(sql='"SELECT team, status FROM nfl_injury_week"')), "claimed"),
  # variants the author did not claim, each a way a real read could arrive
  ("V1 same read, lower-case SQL", EXP,
      lambda _: append(EXP, PLANTED_FN.format(sql='"select team, status from nfl_injury_week"')), "variant"),
  ("V2 same read, table name in an f-string", EXP,
      lambda _: append(EXP, '\n\nF19_T = "nfl_injury_week"\n' + PLANTED_FN.format(sql='f"SELECT team FROM {F19_T}"')), "variant"),
  ("V3 same read, SQL held in store.py and called from export_web", "store.py",
      lambda _: append("store.py", '\n\ndef f19_injuries(con):\n    return con.execute("SELECT team FROM nfl_injury_week").fetchall()\n'), "variant"),
  ("V4 a MAPPED table (outcomes: Odds API) read by a kind that does not declare it", EXP,
      lambda _: append(EXP, PLANTED_FN.format(sql='"SELECT line FROM outcomes WHERE entity_type = \'team\'"')), "variant"),
]

def run_tests():
    env = {k: v for k, v in os.environ.items() if k != "LOGGER_DB"}
    r = subprocess.run([PY, "-m", "pytest", "tests/test_source_registry.py", "-q", "-p", "no:cacheprovider",
                        r"--basetemp=D:\temp\f19\bt\plant"], cwd=CLONE, capture_output=True, text=True, env=env)
    tail = r.stdout.strip().splitlines()[-1] if r.stdout.strip() else r.stderr[-300:]
    failed = re.findall(r"^FAILED (\S+)", r.stdout, re.M)
    return r.returncode, tail, failed

def status():
    return subprocess.run(["git", "status", "--porcelain"], cwd=CLONE, capture_output=True, text=True).stdout.strip()

assert status() == "", "clone dirty before start: " + status()
rc, tail, failed = run_tests()
out = [{"fault": "BASELINE (no mutation)", "rc": rc, "tail": tail, "failed": failed}]
assert rc == 0, tail
for name, path, fn, cls in FAULTS:
    fn(None)
    assert status() != "", f"{name}: mutation did not change the tree"
    rc, tail, failed = run_tests()
    subprocess.run(["git", "checkout", "--", path], cwd=CLONE, check=True)
    assert status() == "", f"{name}: restore left {status()}"
    out.append({"fault": name, "class": cls, "file": path, "rc": rc, "caught": rc != 0, "tail": tail, "failed": failed})
for o in out:
    print(f"{o['fault']:<80} rc={o['rc']} caught={o.get('caught','-')}  | {o['tail']}")
    for f in o["failed"]:
        print("      FAILED", f)
json.dump(out, open(sys.argv[1], "w"), indent=1)
