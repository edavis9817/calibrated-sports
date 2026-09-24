"""a-30: re-plant f-19's four gate faults and the bypass variants into a scratch clone, and
record whether tests/test_source_registry.py catches each.

    python research/a30_replant_gate_faults.py CLONE PYTHON OUT.json

Modelled on f-19's research/f19_plant_gate_faults.py (branch f-19-attack-sources-gate).
Every mutation edits a TRACKED file in CLONE, the test file is run, the file is restored
with `git checkout`, and `git status --porcelain` is asserted EMPTY - so a restore that did
nothing is caught, not trusted. The clone must be clean at start.

`expect` is what a-30 claims for each: "caught", or "passes by design" with the reason. A
row whose outcome differs from its expectation is printed as a MISMATCH and exits 1.
"""
import json
import os
import re
import subprocess
import sys

CLONE, PY, OUT = sys.argv[1], sys.argv[2], sys.argv[3]
REG, EXP, CON = "jobs/source_registry.py", "jobs/export_web.py", "web/contract/v2/contract.schema.json"
LF, CRLF = chr(10), chr(13) + chr(10)


def edit(path, old, new):
    p = os.path.join(CLONE, path)
    with open(p, encoding="utf-8", newline="") as f:
        s = f.read()
    if CRLF in s:           # a checkout under core.autocrlf: match its line endings
        old, new = old.replace(LF, CRLF), new.replace(LF, CRLF)
    assert s.count(old) == 1, (path, old, s.count(old))
    with open(p, "w", encoding="utf-8", newline="") as f:
        f.write(s.replace(old, new))


def append(path, text):
    with open(os.path.join(CLONE, path), "a", encoding="utf-8", newline="") as f:
        f.write(text)


LOADER = '\n\ndef load_f19_planted(con):\n    return con.execute({sql}).fetchall()\n'
STORE_FN = ('\n\ndef f19_injuries(con):\n'
            '    return con.execute("SELECT team FROM nfl_injury_week").fetchall()\n')

FAULTS = [
    # f-19's four claimed faults. C2 and C3 used "espn.scoreboard", which a-30 registers,
    # so they use ids nobody registers instead.
    ("C1 undeclared contract kind", [CON], lambda: edit(
        CON, '"sources": "SourcesFile"', '"sources": "SourcesFile",\n      "boards": "SourcesFile"'),
     "caught"),
    ("C2 unregistered id", [REG], lambda: edit(
        REG, '"live.prices": ("kalshi.ladders",),',
        '"live.prices": ("kalshi.ladders", "f19.unregistered"),'), "caught"),
    ("C3 unread source", [REG], lambda: edit(
        REG, '    "rss.headlines": dict(',
        '    "f19.unread": dict(name="Nobody", sports=("nfl",), layer="FACTS", provides="-",\n'
        '        used_for="-", last_read=("elsewhere", "none")),\n    "rss.headlines": dict('),
     "caught"),
    ("C4 export_web table with no source", [EXP], lambda: append(
        EXP, LOADER.format(sql='"SELECT team, status FROM nfl_injury_week"')), "caught"),
    # f-19's four variants, as f-19 planted them
    ("V1 same read, lower-case SQL", [EXP], lambda: append(
        EXP, LOADER.format(sql='"select team, status from nfl_injury_week"')), "caught"),
    ("V2 same read, table name in an f-string", [EXP], lambda: append(
        EXP, '\n\nF19_T = "nfl_injury_week"\n' + LOADER.format(sql='f"SELECT team FROM {F19_T}"')),
     "caught"),
    ("V3 as f-19 planted it: SQL in store.py that no export calls", ["store.py"],
     lambda: append("store.py", STORE_FN),
     "passes by design: no export reads it; store.py is the logger's module and mapping every "
     "table it reads would be a false declaration"),
    ("V4 a MAPPED table (outcomes) read by a new function", [EXP], lambda: append(
        EXP, LOADER.format(sql='"SELECT line FROM outcomes WHERE entity_type = \'team\'"')),
     "caught"),
    # the forms of V3/V4 that are actual reads by the export
    ("V3b SQL in store.py CALLED from an export_web function", ["store.py", EXP], lambda: (
        append("store.py", STORE_FN),
        append(EXP, '\n\ndef load_f19(con):\n    return store.f19_injuries(con)\n')), "caught"),
    ("V4b the has_market flow undeclared (KIND_INPUTS player_index removed)", [REG], lambda: edit(
        REG, '        "player_index": ("market",),\n', ''), "caught"),
    # a read the static scan cannot see at all: only the runtime ledger can
    ("R1 obfuscated SQL inside a loader every export runs", [EXP], lambda: edit(
        EXP, 'def load_headshots(con):\n',
        'def load_headshots(con):\n'
        '    con.execute("".join(["sel", "ect 1 fr", "om source_health"])).fetchall()\n'), "caught"),
]


def run_tests():
    env = {k: v for k, v in os.environ.items() if k not in ("LOGGER_DB", "DB_PATH", "WEB_EXPORT_DIR")}
    r = subprocess.run([PY, "-m", "pytest", "tests/test_source_registry.py", "-q", "-p",
                        "no:cacheprovider", "--basetemp=" + os.path.join(CLONE, "..", "bt_plant")],
                       cwd=CLONE, capture_output=True, text=True, env=env)
    lines = r.stdout.strip().splitlines()
    tail = lines[-1] if lines else r.stderr[-300:]
    failed = re.findall(r"^FAILED (\S+)", r.stdout, re.M)
    errors = re.findall(r"^ERROR (\S+)", r.stdout, re.M)
    named = len(re.findall(r"SourceRegistryError", r.stdout))
    return r.returncode, tail, failed, errors, named


def status():
    return subprocess.run(["git", "status", "--porcelain"], cwd=CLONE, capture_output=True,
                          text=True).stdout.strip()


assert status() == "", "clone dirty before start: " + status()
head = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=CLONE, capture_output=True,
                      text=True).stdout.strip()
rc, tail, failed, errors, named = run_tests()
out = [{"fault": "BASELINE (no mutation)", "head": head, "rc": rc, "tail": tail}]
assert rc == 0, tail
mismatch = 0
for name, paths, fn, expect in FAULTS:
    fn()
    assert status() != "", f"{name}: mutation did not change the tree"
    rc, tail, failed, errors, named = run_tests()
    subprocess.run(["git", "checkout", "--", *paths], cwd=CLONE, check=True)
    assert status() == "", f"{name}: restore left {status()}"
    caught = rc != 0
    ok = caught == (expect == "caught")
    mismatch += not ok
    out.append({"fault": name, "files": paths, "rc": rc, "caught": caught, "expect": expect,
                "as_expected": ok, "collection_errors": errors,
                "sourceregistryerror_mentions": named, "tail": tail, "failed": failed})
for o in out:
    flag = "" if o.get("as_expected", True) else "   <-- MISMATCH"
    print(f"{o['fault']:<72} rc={o['rc']} caught={o.get('caught', '-')}{flag}  | {o['tail']}")
    if o.get("collection_errors"):
        print("      COLLECTION ERROR", o["collection_errors"])
    for f in o.get("failed", [])[:6]:
        print("      FAILED", f)
with open(OUT, "w", encoding="utf-8") as f:
    json.dump(out, f, indent=1)
sys.exit(1 if mismatch else 0)
