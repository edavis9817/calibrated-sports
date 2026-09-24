"""f-21 part 2: does a-31's contract ENCODE the Board's four-way partition, or only describe it?

Run against a checkout of a-31 (5675347) and a Board tree it produced:

    python research/f21_board_partition.py --a31 D:/temp/f21/a31 --tree D:/temp/f21/realtrees/board-wk2

Read-only on the tree (it is a copy). For every week in the tree:

  1. The ledger's partition as the producer computes it (core.board.lean_states at the
     latest read) against the index's `leans` counts.
  2. The leans a page can SEE on the latest read (rows with a `lean`, keyed by
     core.board.lean_id) against the leans the ledger published for that week. A
     published lean with no row on the latest read is an ORPHAN: the page has to count
     it from somewhere other than the read, or apologise for it.
  3. Plants, each validated with a-31's own export_web.validate_contract:
       - the index's counts rewritten to nonsense (all zero; graded inflated by 1,000)
       - a graded row removed from the latest read
     If a plant validates, the contract does not encode the property the plant breaks.

Prints one JSON document; exit 1 if the tree holds no week (an empty result is a failure).
"""
import argparse
import copy
import glob
import json
import os
import sys


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--a31", required=True)
    ap.add_argument("--tree", required=True)
    a = ap.parse_args()
    sys.path.insert(0, a.a31)
    from core import board as B
    from jobs import export_web as E
    from jobs import board_read as J
    import polars as pl

    ledger = pl.read_parquet(J.ledger_path(a.tree)).to_dicts()
    csv_rows = pl.read_csv(J.ledger_path(a.tree).replace(".parquet", ".csv"),
                           infer_schema_length=0).height
    out = {"tree": a.tree, "ledger_rows": len(ledger), "ledger_csv_rows": csv_rows, "weeks": []}
    for idx_path in sorted(glob.glob(os.path.join(a.tree, "board", "nfl", "*", "wk*", "index.json"))):
        wd = os.path.dirname(idx_path)
        idx = json.load(open(idx_path, encoding="utf-8"))
        latest_key = os.path.join(wd, J.read_name(idx["latest"]))
        doc = json.load(open(latest_key, encoding="utf-8"))
        season, week = idx["season"], idx["week"]
        read_ts = J.parse_iso(idx["latest"])
        # Only events that existed at the latest read (the ledger is shared across weeks and reads).
        events = [e for e in ledger if e["event_at"] <= idx["latest"]]
        states = B.lean_states(events, read_ts)
        wk_pub = {e["lean_id"]: e for e in events
                  if e["event"] == "published" and e["season"] == season and e["week"] == week}
        counts = {s: sum(1 for l in wk_pub if states[l] == s)
                  for s in (B.S_GRADED, B.S_UPCOMING, B.S_LIVE, B.S_VOID)}
        on_read = {B.lean_id(r["claim_id"], r["line"], r["lean"]): r
                   for r in doc["rows"] if r.get("lean")}
        orphans = sorted(set(wk_pub) - set(on_read))
        unledgered = sorted(set(on_read) - set(wk_pub))
        by_state = {}
        for l in orphans:
            by_state[states[l]] = by_state.get(states[l], 0) + 1
        # How the orphans left: same claim still on the read at another line?
        claims_on_read = {r["claim_id"]: r for r in doc["rows"]}
        why = {"claim_on_read_at_other_line": 0, "claim_not_on_read": 0}
        examples = []
        for l in orphans:
            p = wk_pub[l]
            r = claims_on_read.get(p["claim_id"])
            if r is not None:
                why["claim_on_read_at_other_line"] += 1
                if len(examples) < 5:
                    examples.append({"claim": p["claim_id"], "published_line": p["line"],
                                     "side": p["side"], "state": states[l],
                                     "read_line": r["line"], "read_lean": r.get("lean")})
            else:
                why["claim_not_on_read"] += 1

        # ---- plants against the producer's own validator
        def validates(obj, key):
            try:
                E.validate_contract({key: obj})
                return True
            except Exception as ex:  # noqa: BLE001 - the verdict is the point
                return f"refused: {type(ex).__name__}: {str(ex)[:160]}"

        idx_key = f"board/nfl/{season}/wk{week:02d}/index.json"
        read_key = f"board/nfl/{season}/wk{week:02d}/{J.read_name(idx['latest'])}"
        plants = {"real_index": validates(idx, idx_key), "real_read": validates(doc, read_key)}
        z = copy.deepcopy(idx)
        z["leans"] = {k: 0 for k in z["leans"]}
        plants["index_all_zero"] = validates(z, idx_key)
        inf = copy.deepcopy(idx)
        inf["leans"]["graded"] += 1000
        plants["index_graded_plus_1000"] = validates(inf, idx_key)
        settled = [i for i, r in enumerate(doc["rows"]) if r.get("lean") and r["status"] in B.SETTLED]
        if settled:
            d2 = copy.deepcopy(doc)
            d2["rows"].pop(settled[0])
            plants["read_minus_a_graded_lean_row"] = validates(d2, read_key)
        out["weeks"].append({
            "season": season, "week": week, "latest": idx["latest"], "rows_on_read": len(doc["rows"]),
            "index_leans": idx["leans"], "ledger_partition_at_latest": counts,
            "index_matches_ledger": counts == idx["leans"],
            "published_this_week": len(wk_pub), "leans_visible_on_latest_read": len(on_read),
            "orphans": len(orphans), "orphans_by_state": by_state, "orphans_why": why,
            "orphan_examples": examples, "on_read_but_not_in_ledger": len(unledgered),
            "plants": plants})
    print(json.dumps(out, indent=1, default=str))
    return 0 if out["weeks"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
