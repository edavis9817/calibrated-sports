"""Where a Board ledger price that is not an American price comes from (a-80).

    python -m research.board_price_gap --site https://calibratedsports.com --cache D:/temp/a80/live
    python -m research.board_price_gap --tree D:/path/to/board-tree

An American price is at or below -100 or at or above +100. The Board's ledger
carried `price` values strictly between them. This script takes every
`published` lean in a ledger, opens the read file that published it, recomputes
the lean-side median from that row's own `books`, and reports:

  - whether the recomputed median equals the recorded price (it must, for the
    read to be the cause);
  - how many books quoted the lean side, and whether the two middle prices sit
    on opposite sides of even money (the only way a median of valid prices can
    leave the valid range);
  - the same two-book straddle where the mean happened to land OUTSIDE the gap
    (+400 and -110 give +145: a valid-looking number that is no book's price and
    no meaningful average). The (-100, +100) gate cannot see those.

It reads published files only. It opens no store and writes nothing but its
own cache of the files it fetched. Measured 2026-10-08 against the served
ledger (1,142 rows, 630 published leans): 17 in the gap - 15 graded (7 in week
3, 8 in week 4) and 2 open in week 5 - all 17 two books, opposite signs, median
reproduced exactly; 0 straddles outside the gap; 630 of 630 medians reproduced.
"""
from __future__ import annotations

import argparse
import collections
import csv
import json
import os
import statistics
import urllib.request


def in_gap(p):
    return p is not None and -100 < p < 100


def num(x):
    return None if x in ("", None) else float(x)


def read_file_name(read_iso):
    return "read-" + read_iso.replace(":", "") + ".json"


class Files:
    """The ledger and the read files, from a local Board tree or from the site."""

    def __init__(self, tree, site, cache):
        self.tree, self.site, self.cache = tree, site, cache
        self._docs = {}

    def _get(self, key):
        if self.tree:
            return os.path.join(self.tree, *key.split("/"))
        path = os.path.join(self.cache, key.replace("/", "__"))
        if not os.path.exists(path):
            os.makedirs(self.cache, exist_ok=True)
            # Name the client rather than send urllib's default User-Agent.
            req = urllib.request.Request(f"{self.site.rstrip('/')}/data/{key}",
                                         headers={"User-Agent": "calibratedsports-research/1.0 python-urllib"})
            with urllib.request.urlopen(req, timeout=60) as r, open(path, "wb") as f:
                f.write(r.read())
        return path

    def ledger(self):
        with open(self._get("board/nfl/ledger.csv"), encoding="utf-8", newline="") as f:
            return list(csv.DictReader(f))

    def read(self, season, week, read_iso):
        key = f"board/nfl/{season}/wk{int(week):02d}/{read_file_name(read_iso)}"
        if key not in self._docs:
            with open(self._get(key), encoding="utf-8") as f:
                self._docs[key] = json.load(f)
        return self._docs[key]


def trace(files):
    rows = files.ledger()
    if not rows:
        raise SystemExit("the ledger has no rows - nothing to trace")
    events = collections.Counter(r["event"] for r in rows)
    pub = {r["lean_id"]: r for r in rows if r["event"] == "published"}
    if len(pub) != events["published"]:
        raise SystemExit("a lean_id is published twice - this script assumes one published row per lean")
    term = {r["lean_id"]: r for r in rows if r["event"] != "published"}
    out = {"rows": len(rows), "events": dict(events), "published": len(pub),
           "graded": sum(t["event"] == "graded" for t in term.values()),
           "in_gap": [], "straddle_outside_gap": [], "not_reproduced": [],
           "books_on_lean_side": collections.Counter()}
    for lid, p in pub.items():
        doc = files.read(p["season"], p["week"], p["read_at"])
        match = [r for r in doc["rows"] if r["row_id"] == p["row_id"] and r.get("lean") == p["side"]]
        state = term[lid]["event"] if lid in term else "open"
        rec = {"lean_id": lid, "week": int(p["week"]), "state": state, "market": p["market"],
               "side": p["side"], "line": num(p["line"]), "recorded": num(p["price"]),
               "read_at": p["read_at"]}
        if len(match) != 1:
            out["not_reproduced"].append(dict(rec, why=f"{len(match)} rows in the read carry this lean"))
            continue
        prices = sorted(b[p["side"]] for b in match[0]["books"] if b.get(p["side"]) is not None)
        median = statistics.median(prices) if prices else None
        rec.update(lean_side_prices=prices, books=[b["book"] for b in match[0]["books"]
                                                   if b.get(p["side"]) is not None])
        if median != rec["recorded"] or match[0]["lean_price"] != rec["recorded"]:
            out["not_reproduced"].append(dict(rec, why=f"median of the row's books is {median}"))
            continue
        n = len(prices)
        out["books_on_lean_side"][n] += 1
        straddle = n > 0 and n % 2 == 0 and prices[n // 2 - 1] < 0 < prices[n // 2]
        rec["middle_pair_straddles_even_money"] = straddle
        if in_gap(rec["recorded"]):
            out["in_gap"].append(rec)
        elif straddle:
            out["straddle_outside_gap"].append(rec)
    out["terminal_rows_in_gap"] = sum(in_gap(num(t["price"])) for t in term.values())
    out["books_on_lean_side"] = dict(sorted(out["books_on_lean_side"].items()))
    return out


def report(out, log=print):
    log(f"ledger: {out['rows']} rows, {out['events']}; {out['published']} published leans, "
        f"{out['graded']} graded")
    log(f"medians reproduced from the publishing read's own books: "
        f"{out['published'] - len(out['not_reproduced'])} of {out['published']}")
    log(f"books quoting the lean side, per published lean: {out['books_on_lean_side']}")
    gap = out["in_gap"]
    log(f"published leans with a price strictly between -100 and +100: {len(gap)}")
    by = collections.Counter((g["week"], g["state"]) for g in gap)
    for (week, state), n in sorted(by.items()):
        log(f"  week {week} {state}: {n}")
    causes = collections.Counter((len(g["lean_side_prices"]), g["middle_pair_straddles_even_money"])
                                 for g in gap)
    for (n, straddle), k in sorted(causes.items()):
        log(f"  cause: {n} book(s) on the lean side, middle pair straddles even money = {straddle}: {k}")
    for g in gap:
        log(f"    {g['lean_id']} wk{g['week']} {g['state']:<6} {g['market']:<13} {g['side']:<5} "
            f"{g['line']:>5} {g['books']} {g['lean_side_prices']} -> {g['recorded']}")
    log(f"terminal rows carrying such a price (each a copy of its published row): "
        f"{out['terminal_rows_in_gap']}")
    log(f"two-book straddles whose mean landed OUTSIDE the gap (invisible to the gate): "
        f"{len(out['straddle_outside_gap'])}")
    for g in out["not_reproduced"]:
        log(f"  NOT REPRODUCED {g['lean_id']}: {g['why']}")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--tree", help="a local Board tree (the directory holding board/)")
    src.add_argument("--site", help="the site's origin; files are read from /data/board/...")
    ap.add_argument("--cache", help="where --site keeps the files it fetched (required with --site)")
    ap.add_argument("--json", help="also write the full result here")
    a = ap.parse_args(argv)
    if a.site and not a.cache:
        ap.error("--site needs --cache: name a scratch directory, there is no default")
    out = trace(Files(a.tree, a.site, a.cache))
    report(out)
    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump(out, f, indent=1)
    return out


if __name__ == "__main__":
    main()
