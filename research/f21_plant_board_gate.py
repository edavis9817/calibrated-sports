"""f-21 part 3: plant f-19's surviving bypass shapes INTO THE BOARD JOB and see whether
a-31's source gate notices. Edits the real jobs/board_read.py in a SCRATCH checkout of
a-31, runs one fixture read, restores with `git checkout`, and verifies the restore by
reading the file back (a restore is checked, never trusted).

    python research/f21_plant_board_gate.py --a31 D:/temp/f21/a31 --python <venv python> --out <dir>
"""
import argparse
import json
import os
import shutil
import subprocess

ANCHOR = "def week_games(con, season, week):\n"
MARK = "# F21-PLANT"
PLANTS = {
    "baseline": None,
    # a mapped table outside board_read's sources, named through an f-string
    "fstring_mapped": '    con.execute(f"SELECT COUNT(*) FROM {\'nfl\' + \'_teams\'}").fetchall()  ' + MARK,
    # the same table, lower-case literal SQL (a-30 made the scan case-insensitive)
    "lowercase_mapped": '    con.execute("select count(*) from nfl_teams").fetchall()  ' + MARK,
    # an UNMAPPED table on the watched connection, f-string (runtime should catch it)
    "fstring_unmapped_watched": '    con.execute(f"SELECT COUNT(*) FROM {\'source\' + \'_health\'}").fetchall()  ' + MARK,
    # the same unmapped read on a SECOND connection the watch never sees
    "fstring_unmapped_second_connection":
        '    sqlite3.connect(f"file:{config.DB_PATH}?mode=ro", uri=True).execute('
        'f"SELECT COUNT(*) FROM {\'source\' + \'_health\'}").fetchall()  ' + MARK,
    # an unmapped read on a second connection, the SQL assembled without an f-string
    "joined_unmapped_second_connection":
        '    sqlite3.connect(f"file:{config.DB_PATH}?mode=ro", uri=True).execute('
        '" ".join(["SELECT COUNT(*)", "FROM", "source_health"])).fetchall()  ' + MARK,
    # an unmapped read through an existing store.py helper, which opens its own
    # connection (store is imported at module level, as any real use would)
    "store_helper_unmapped": '    store.health()  ' + MARK,
    # a MAPPED table outside board_read's sources, read through a store.py helper on the
    # watched connection (f-19's V3 shape)
    "store_helper_mapped_on_watched": '    store.pfr_gsis(con)  ' + MARK,
}
# plants that need `import store` at module level (board_read does not import it)
NEEDS_STORE = {"store_helper_unmapped", "store_helper_mapped_on_watched"}
IMPORT_ANCHOR = "import config\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--a31", required=True)
    ap.add_argument("--python", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    os.makedirs(os.path.join(os.path.dirname(a.a31), "bt-gate"), exist_ok=True)
    here = os.path.dirname(os.path.abspath(__file__))
    shutil.copy(os.path.join(here, "f21_gate_probe.py"),
                os.path.join(a.a31, "tests", "test_f21_gate.py"))
    target = os.path.join(a.a31, "jobs", "board_read.py")
    pristine = open(target, encoding="utf-8").read()
    assert ANCHOR in pristine and MARK not in pristine
    results = {}
    for name, line in PLANTS.items():
        if line is not None:
            text = pristine.replace(ANCHOR, ANCHOR + line + "\n", 1)
            if name in NEEDS_STORE:
                assert IMPORT_ANCHOR in text
                text = text.replace(IMPORT_ANCHOR, IMPORT_ANCHOR + "import store  " + MARK + "\n", 1)
            with open(target, "w", encoding="utf-8", newline="") as f:
                f.write(text)
        out = os.path.join(a.out, f"gate-{name}.json")
        if os.path.exists(out):
            os.remove(out)
        env = {k: v for k, v in os.environ.items() if k not in ("LOGGER_DB", "DB_PATH")}
        env.update(F21_PLANT=name, F21_GATE_OUT=out)
        p = subprocess.run([a.python, "-m", "pytest", "-q", "tests/test_f21_gate.py",
                            "-p", "no:cacheprovider", "--basetemp",
                            os.path.join(os.path.dirname(a.a31), "bt-gate", name)],
                           cwd=a.a31, env=env, capture_output=True, text=True)
        subprocess.run(["git", "checkout", "--", "jobs/board_read.py"], cwd=a.a31, check=True)
        restored = open(target, encoding="utf-8").read()
        if restored != pristine or MARK in restored:
            raise SystemExit(f"RESTORE FAILED after {name}")
        results[name] = json.load(open(out)) if os.path.exists(out) else \
            {"no_record": True, "pytest_tail": (p.stdout + p.stderr)[-1500:]}
        results[name]["pytest_rc"] = p.returncode
    status = subprocess.run(["git", "status", "--porcelain", "--", "jobs/"], cwd=a.a31,
                            capture_output=True, text=True).stdout
    results["_restore"] = {"jobs_porcelain": status, "clean": status.strip() == ""}
    print(json.dumps(results, indent=1))
    # exit 0 only when every plant left a record AND the restore is clean
    ok = all(not v.get("no_record") for k, v in results.items() if k != "_restore")
    return 0 if ok and results["_restore"]["clean"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
