"""a-35: f-21's partition plants, re-run on a REAL Board tree, validating each index
TOGETHER with the read it names - which is the batch the producer validates.

    python -m research.a35_board_partition_plants --tree D:/temp/a35/replay-wk2

Read-only on the tree. For every week's index:
  - the real (index, latest read) pair must validate;
  - four plants must each be REFUSED by export_web.validate_contract:
      index counts all zero; every count + 1000; graded + 1; a graded lean row
      removed from the read;
  - and the per-week orphan count: published leans (ledger, as of the latest read)
    with no row carrying that lean on the latest read.

Prints one JSON document. Exit 1 if the tree holds no index (a check over zero weeks
is not a check) or if the real pair is refused or any plant is accepted.
"""
import argparse
import copy
import glob
import json
import os
import sys

from core import board as B
from jobs import board_read as J
from jobs import export_web as E


def attempt(files):
    try:
        E.validate_contract(files)
        return "accepted"
    except E.ContractError as e:
        return "refused: " + str(e).split("\n", 1)[-1].strip()[:240]


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--tree", required=True)
    a = ap.parse_args(argv)
    import polars as pl
    led = pl.read_parquet(J.ledger_path(a.tree)).to_dicts()
    csv_rows = pl.read_csv(J.ledger_csv_path(a.tree), infer_schema_length=0).height
    out = {"tree": a.tree, "ledger_rows": len(led), "ledger_csv_rows": csv_rows,
           "pair": J.pair_ledger(a.tree) if csv_rows == len(led) else "SPLIT", "weeks": []}
    bad = []
    for ipath in sorted(glob.glob(os.path.join(a.tree, "board", "nfl", "*", "wk*", "index.json"))):
        idx = json.load(open(ipath, encoding="utf-8"))
        ik = J.index_key(idx["season"], idx["week"])
        rk = J.read_key(idx["season"], idx["week"], idx["latest"])
        doc = json.load(open(E.local_path(a.tree, rk), encoding="utf-8"))
        pubs = {(e["row_id"], e["side"]) for e in led if e["event"] == "published"
                and e["event_at"] <= idx["latest"]
                and (e["season"], e["week"]) == (idx["season"], idx["week"])}
        on_read = {(r["row_id"], r["lean"]) for r in doc["rows"] if r.get("lean")}
        w = {"season": idx["season"], "week": idx["week"], "latest": idx["latest"],
             "reads": len(idx["reads"]), "rows_on_latest_read": len(doc["rows"]),
             "index_leans": idx["leans"], "published": len(pubs),
             "orphans": len(pubs - on_read), "on_read_not_published": len(on_read - pubs),
             "kept_line_moved": sum(1 for r in doc["rows"] if r.get("line_moved_after_publication")),
             "kept_lean_changed": sum(1 for r in doc["rows"]
                                      if r.get("lean_changed_after_publication")),
             "real_pair": attempt({ik: idx, rk: doc}), "plants": {}}
        zero = copy.deepcopy(idx)
        zero["leans"] = {s: 0 for s in zero["leans"]}
        plus = copy.deepcopy(idx)
        plus["leans"] = {s: v + 1000 for s, v in plus["leans"].items()}
        one = copy.deepcopy(idx)
        one["leans"]["graded"] += 1
        w["plants"]["index_all_zero"] = attempt({ik: zero, rk: doc})
        w["plants"]["index_plus_1000"] = attempt({ik: plus, rk: doc})
        w["plants"]["index_graded_plus_1"] = attempt({ik: one, rk: doc})
        graded = [r for r in doc["rows"] if r.get("lean") and B.ROW_LEAN_STATE[r["status"]] == B.S_GRADED]
        if graded:
            cut = dict(doc, rows=[r for r in doc["rows"] if r["row_id"] != graded[0]["row_id"]])
            w["plants"]["read_minus_a_graded_lean_row"] = attempt({ik: idx, rk: cut})
        else:
            w["plants"]["read_minus_a_graded_lean_row"] = "not run: no graded lean on the read"
        w["index_alone"] = attempt({ik: idx})
        if w["real_pair"] != "accepted":
            bad.append(f"{ik}: real pair refused")
        for name, v in w["plants"].items():
            if v == "accepted":
                bad.append(f"{ik}: plant {name} accepted")
        out["weeks"].append(w)
    out["problems"] = bad
    print(json.dumps(out, indent=1))
    if not out["weeks"]:
        print("no board_index in the tree - nothing checked", file=sys.stderr)
        return 1
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
