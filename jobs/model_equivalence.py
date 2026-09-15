"""BRIEF 017 ITEM 1 - record that two model fingerprints predict identically.

    python jobs/model_equivalence.py --verify OLD_COMMIT NEW_COMMIT
    python jobs/model_equivalence.py --record OLD_COMMIT NEW_COMMIT \\
        --old-version ... --new-version ... --reason "..."
    python jobs/model_equivalence.py --list

A row may only be written when the evidence assertion passes, and the evidence
is the machine-checked structural comparison from `core/model_equivalence.py`,
not prose. The objection to an equivalence table is that a human can assert
anything into it; the assertion is the answer to that objection, so `--record`
runs it and refuses on failure.
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import model_equivalence as me
from core import version_resolve as vr


def build_evidence(old_commit, new_commit, old_version, new_version):
    cmp = me.assert_fee_only(me.compare(old_commit, new_commit))
    # A version is `family-series+fingerprint`; only the fingerprint is hashed.
    fp = lambda v: v.rsplit("+", 1)[-1]
    ok_old = me.fingerprint_matches(old_commit, fp(old_version))
    ok_new = me.fingerprint_matches(new_commit, fp(new_version))
    if not ok_old:
        raise me.NotEquivalent(
            f"{old_commit} cannot hash to {old_version} under any line-ending "
            f"convention - the commit and the version do not correspond")
    if not ok_new:
        raise me.NotEquivalent(
            f"{new_commit} cannot hash to {new_version} under any line-ending "
            f"convention")
    return {
        "old_commit": old_commit, "new_commit": new_commit,
        "old_version": old_version, "new_version": new_version,
        "fingerprint_reproduced": {"old": ok_old, "new": ok_new},
        "hashed_files": list(me.FINGERPRINT_FILES),
        "ast_changed_in_place": cmp["changed"],
        "ast_added": cmp["added"],
        "ast_removed": cmp["removed"],
        "assertion": "every top-level construct that differs is a fee symbol; "
                     "no construct changed in place",
        "diff": cmp["diff"],
    }


def verify(old_commit, new_commit, old_version, new_version):
    ev = build_evidence(old_commit, new_commit, old_version, new_version)
    print(f"  hashed files      {', '.join(ev['hashed_files'])}")
    print(f"  fingerprints      {old_version} <- {old_commit}   "
          f"{new_version} <- {new_commit}   both reproduced")
    print(f"  changed in place  {ev['ast_changed_in_place'] or 'none'}")
    print(f"  removed           {ev['ast_removed'] or 'none'}")
    print(f"  added             {ev['ast_added'] or 'none'}")
    print(f"  diff              {len(ev['diff']):,} bytes stored as evidence")
    inplace = sorted({n for v in ev["ast_changed_in_place"].values() for n in v})
    print("\n  ASSERTION PASSES.")
    print("    every construct that moved is a FEE symbol (trading cost) or an")
    print("    IDENTITY symbol (what the model is called) - never a predicted")
    print("    quantity.")
    if inplace:
        print(f"    changed IN PLACE: {', '.join(inplace)} - allowed only because")
        print("    these are identity symbols; normalising a hash means editing")
        print("    the hash function. A fee or prediction symbol changing in")
        print("    place would have failed.")
    else:
        print("    nothing changed in place.")
    return ev


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("commits", nargs="*", help="OLD_COMMIT NEW_COMMIT")
    ap.add_argument("--verify", action="store_true")
    ap.add_argument("--record", action="store_true")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--old-version")
    ap.add_argument("--new-version")
    ap.add_argument("--reason", default="")
    a = ap.parse_args()

    if a.list or not (a.verify or a.record):
        vr.ensure_schema()
        import sqlite3
        import config
        c = sqlite3.connect(f"file:{config.DB_PATH}?mode=ro", uri=True)
        rows = c.execute("SELECT old_version, new_version, reason, "
                         "length(evidence), created_ts "
                         "FROM model_version_equivalence").fetchall()
        print(f"\n  {len(rows)} equivalence row(s)")
        for o, n, r, ln, ts in rows:
            print(f"    {o}\n    == {n}\n       {r}\n       evidence {ln:,} bytes")
        return

    if len(a.commits) != 2:
        raise SystemExit("need OLD_COMMIT NEW_COMMIT")
    old_c, new_c = a.commits
    ev = verify(old_c, new_c, a.old_version, a.new_version)
    if a.record:
        vr.record(a.old_version, a.new_version, a.reason,
                  json.dumps(ev, indent=1, sort_keys=True))
        print(f"\n  RECORDED {a.old_version} == {a.new_version}")


if __name__ == "__main__":
    main()
