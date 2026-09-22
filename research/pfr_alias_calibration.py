"""How often does the participation rule name the WRONG player? Measured on known pairs.

    python -m research.pfr_alias_calibration [--seasons 2016-2025] [--seed 1]

Read-only: the store `mode=ro`, raw parquet from the archive.

`jobs.build_pfr_alias.participation_match` selects a gsis id with no name in the
loop - the unique player on the team whose on-field play counts sit within TOL
of the snap row in every checked game - then checks the survivor's roster,
name token and own pfr id. Before it is allowed to write an identity, its error
rate has to be known. This runs the EXACT function on snap-count ids whose
gsis_id player_xwalk already carries, pretends not to know it, and counts how
often the rule resolves, and how often a resolution is wrong.

Subjects are drawn the way the orphans look: k games sampled from one player's
snap rows inside the participation window, k = 1..4 (27 of the 28 orphans sit
at 1-41 games; the thin end is where the rule is weakest). A "wrong" answer
may be an xwalk error rather than a rule error - each is listed so it can be
read, not assumed.

Error bounds are Wilson 95% upper limits, not normal approximations: at a rate
this close to zero a normal interval runs through it.
"""
import argparse
import math
import random
import sqlite3
from collections import Counter, defaultdict

import polars as pl

import config
from jobs import build_pfr_alias as b


def wilson_hi(k, n, z=1.96):
    if n == 0:
        return float("nan")
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    return (c + z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))) / d


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--seasons", default="2016-2025")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--tol", type=int, default=b.TOL)
    a = ap.parse_args(argv)
    lo, hi = map(int, a.seasons.split("-"))
    seasons = [s for s in range(lo, hi + 1) if s in set(b.participation_seasons())]
    assert seasons, "no participation seasons in range - RAW_DIR is wrong"

    con = sqlite3.connect(f"file:{config.DB_PATH}?mode=ro", uri=True)
    xw = dict(con.execute("SELECT pfr_id, gsis_id FROM player_xwalk WHERE pfr_id IS NOT NULL"))
    names = dict(con.execute("SELECT gsis_id, display_name FROM player_xwalk"))
    con.close()

    counts, snaps = {}, []
    for s in seasons:
        counts.update(b.participation_counts(s))
        f = b.latest_file(f"snap_counts_{s}.parquet")
        snaps.append(pl.read_parquet(f, columns=[
            "game_id", "season", "week", "team", "player", "pfr_player_id", "position",
            "offense_snaps", "defense_snaps", "st_snaps"]))
    rosters = b.roster_index(set(seasons))
    by = defaultdict(list)
    for r in pl.concat(snaps).iter_rows(named=True):
        if r["pfr_player_id"] in xw:
            by[r["pfr_player_id"]].append(r)
    print(f"seasons {seasons[0]}-{seasons[-1]}, tol {a.tol}: {len(by):,} known pfr ids, "
          f"{sum(map(len, by.values())):,} snap rows, {len(counts):,} team-games counted")
    assert len(by) > 1000, "too few known subjects - the join or the archive is wrong"

    rng = random.Random(a.seed)
    print(f"\n{'k':>2} {'subjects':>8} {'resolved':>8} {'wrong':>5} {'wrong/resolved':>14} "
          f"{'wilson95 hi':>11}  unresolved-by-reason")
    wrong_all = []
    for k in (1, 2, 3, 4):
        st, why = Counter(), Counter()
        for pfr in sorted(by):
            rows = by[pfr]
            if len(rows) < k:
                continue
            sub = rng.sample(rows, k)
            g, ss, detail = b.participation_match(sub, counts, rosters, names, {},
                                                  tol=a.tol, min_games=1, check_own_pfr=False)
            st["n"] += 1
            if g is None:
                why[detail.split(" - ")[0].split(",")[0][:40] if "survivor" not in detail
                    else detail.split(" ")[0] + " " + " ".join(detail.split(" ")[2:5])] += 1
                continue
            st["res"] += 1
            if g != xw[pfr]:
                st["wrong"] += 1
                wrong_all.append((k, pfr, xw[pfr], g, names.get(xw[pfr]), names.get(g), detail))
        n, res, w = st["n"], st["res"], st["wrong"]
        print(f"{k:>2} {n:8,} {res:8,} {w:5} {w / res if res else float('nan'):14.5f} "
              f"{wilson_hi(w, res):11.5f}  {dict(why.most_common(4))}")
    print("\nwrong resolutions (k, pfr, xwalk gsis, rule gsis, xwalk name, rule name):")
    for x in wrong_all:
        print("  ", x[:6])

    # draft_slot: on the draft rows that DO carry both ids, blank the gsis and
    # ask the slot join for it. Every draft class the archive holds.
    drafts, slots = b.draft_rows(), b.slot_index()
    st, bad = Counter(), []
    for pfr, rows in drafts.items():
        truth = {g for *_, g, _ in rows if g}
        if len(truth) != 1:
            continue
        blanked = {(s, p, r, t, None, rel) for s, p, r, t, _, rel in rows}
        g, detail = b.draft_slot_match(blanked, slots)
        st["n"] += 1
        if g is None:
            st["unresolved"] += 1
        elif g in truth:
            st["right"] += 1
        else:
            st["wrong"] += 1
            bad.append((pfr, sorted(truth)[0], g, names.get(sorted(truth)[0]), names.get(g)))
    res = st["right"] + st["wrong"]
    print(f"\ndraft_slot on {st['n']:,} draft rows carrying both ids: resolved {res:,}, "
          f"wrong {st['wrong']}, unresolved {st['unresolved']:,}; wrong/resolved "
          f"{st['wrong'] / res if res else float('nan'):.5f}, wilson95 hi {wilson_hi(st['wrong'], res):.5f}")
    assert st["n"] > 1000, "too few draft rows carrying both ids - the archive is wrong"
    for x in bad[:20]:
        print("   wrong:", x)


if __name__ == "__main__":
    main()
