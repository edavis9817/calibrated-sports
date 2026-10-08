"""a-78: which college player pages are not people. Reads `cfb.db` read-only (`mode=ro`).

    python -m research.cfb_scope_people                  # prints; writes nothing
    python -m research.cfb_scope_people --tree <staged>  # also checks a staged tree

0 requests. Every figure the a-78 report quotes about the scope rule comes from here.

The rule under test is `jobs.export_cfb_web.names_a_person`, written before this file
was first run: a page is in scope only if the feed names a person. This file does not
decide anything - it counts what the rule removes, lists it, and then looks for what the
rule might have MISSED, which is the half a count of exclusions cannot show:

  1 REMOVED   ids with offensive usage the feed names and never as a person, by id, with
              every name either feed gives them, their box rows and their seasons.
  2 MIXED     ids the feed names BOTH ways - a team row on one feed row and a person on
              another. The rule keeps these (some name is a person); listed so the
              choice is visible.
  3 MISSED?   names among the ids left IN scope that a reader would not call a person's:
              one word only, no letters, the team word anywhere, a digit. A census, not
              a filter - each line is a thing to look at.
  4 ROSTERS   current-season roster rows of exported teams that are team rows. These are
              on TEAM pages (`has_page` false) and this unit does not change them.
  5 TREE      with --tree: the staged index against 1-2 (nobody removed has a page;
              nobody with a page fails the rule; the manifest lists who was removed).
"""
import argparse
import json
import os
import re
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from jobs import export_cfb_web as X                              # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--tree", default=None, help="a staged export directory to check")
    a = ap.parse_args(argv)
    con = X.ro()
    ts = X.teams(con)
    ids = {t["team_id"] for t in ts}
    usage = X._usage_ids(con, ids, None)
    removed = X.team_rows(con, ids)
    scope = X.player_scope(con, ids)
    if not usage or not scope:
        raise SystemExit(f"read {len(usage)} ids with usage and {len(scope)} in scope - "
                         "the store is empty or the path is wrong; refusing to print")
    assert scope == usage - set(removed), "scope is not usage minus the removed ids"
    print(f"store {X.paths.db_path()}")
    print(f"exported teams {len(ids)}; floor {X.PLAYER_SCOPE_FROM}")
    print(f"ids with offensive usage for an exported team from the floor: {len(usage)}")
    print(f"in scope under the rule: {len(scope)}   removed by the rule: {len(removed)}")

    names = X.feed_names(con, usage)
    unnamed = sorted(aid for aid in scope if not names.get(aid))
    print(f"in scope with no name on either feed (no page; listed in unresolved_ids): "
          f"{len(unnamed)} {unnamed}")

    print(f"\n1 REMOVED ({len(removed)}) - id, names, box rows, seasons, roster rows")
    box = defaultdict(lambda: [0, set()])
    for aid, season in con.execute(
            "SELECT athlete_id, season FROM cfb_player_game_box WHERE valid_to_ts IS NULL "
            "AND athlete_id > 0"):
        if aid in removed:
            box[aid][0] += 1
            box[aid][1].add(season)
    ros = defaultdict(list)
    for aid, season, first, last, pos in con.execute(
            "SELECT athlete_id, season, first_name, last_name, position FROM cfb_rosters "
            "WHERE valid_to_ts IS NULL"):
        if aid in removed:
            ros[aid].append((season, first, last, pos))
    for aid in sorted(removed):
        print(f"   {aid}  names {removed[aid]!r}  box rows {box[aid][0]} in "
              f"{sorted(box[aid][1])}  roster (season, first, last, pos) {sorted(ros[aid])}")

    mixed = {aid: ns for aid, ns in names.items()
             if any(X.names_a_person(n) for n in ns)
             and not all(X.names_a_person(n) for n in ns)}
    print(f"\n2 MIXED - named as a person AND as a team row: {len(mixed)}")
    for aid in sorted(mixed):
        print(f"   {aid}  {mixed[aid]!r}")

    print("\n3 MISSED? - names among the ids in scope, by shape (the PUBLISHED name: the "
          "first feed name that names a person)")
    shapes = Counter()
    examples = defaultdict(list)
    for aid in sorted(scope):
        name = next((n for n in names.get(aid, []) if X.names_a_person(n)), None)
        if name is None:
            continue
        words = X.fold(name).split()
        tags = []
        if len(words) == 1:
            tags.append("one word")
        if X.TEAM_ROW_WORD in words:
            tags.append("contains the team word")
        if not re.search("[a-z]", X.fold(name)):
            tags.append("no letters")
        if re.search("[0-9]", name):
            tags.append("has a digit")
        if name != name.strip() or name.startswith("-"):
            tags.append("leading/trailing blank or dash")
        for t in tags or ["ordinary"]:
            shapes[t] += 1
            if t != "ordinary" and len(examples[t]) < 40:
                examples[t].append((aid, name))
    for t, n in shapes.most_common():
        print(f"   {t}: {n}")
        for aid, name in examples[t]:
            print(f"      {aid}  {name!r}")

    marks = ",".join("?" * len(ids))
    rows = [(aid, tid, name, pos) for aid, tid, name, pos in con.execute(
        f"SELECT athlete_id, team_id, full_name, position FROM cfb_rosters WHERE season=? "
        f"AND team_id IN ({marks}) AND valid_to_ts IS NULL",
        (X.CURRENT_SEASON, *sorted(ids)))]
    team_like = [r for r in rows if r[2] and r[2].strip() and not X.names_a_person(r[2])]
    print(f"\n4 ROSTERS - {X.CURRENT_SEASON} roster rows of exported teams: {len(rows)}; "
          f"named and not a person: {len(team_like)} (on team pages, has_page false; "
          "NOT changed by a-78)")
    for r in team_like:
        print(f"   athlete {r[0]} team {r[1]} name {r[2]!r} position {r[3]!r}")

    if a.tree:
        with open(os.path.join(a.tree, "cfb", "players", "index.json"), encoding="utf-8") as f:
            idx = json.load(f)
        with open(os.path.join(a.tree, "cfb", "manifest.json"), encoding="utf-8") as f:
            man = json.load(f)
        staged = {int(p["id"]): p for p in idx["players"]}
        if not staged:
            raise SystemExit("the staged index lists no players - nothing to check")
        dirs = {int(d) for d in os.listdir(os.path.join(a.tree, "cfb", "players"))
                if d.isdigit()}
        fails = [(i, p["name"]) for i, p in staged.items() if not X.names_a_person(p["name"])]
        paged = sorted(set(removed) & (set(staged) | dirs))
        listed = {int(u["id"]) for u in man["unresolved_ids"]
                  if u["reason"] == X.REASON_TEAM_ROW}
        print(f"\n5 TREE {a.tree}  generated {idx['generated_at']}")
        print(f"   pages in the index {len(staged)}; player directories on disk {len(dirs)}; "
              f"manifest counts.players {man['counts']['players']}")
        print(f"   pages whose published name fails the rule: {len(fails)} {fails[:20]}")
        print(f"   removed ids that still have an index entry or a directory: {len(paged)} "
              f"{paged}")
        print(f"   removed ids listed in manifest.unresolved_ids with the team-row reason: "
              f"{len(listed & set(removed))} of {len(removed)}; listed and NOT removed: "
              f"{sorted(listed - set(removed))}")
        print(f"   index == scope minus unnamed: "
              f"{set(staged) == scope - set(unnamed)}  (in scope not staged "
              f"{len(scope - set(unnamed) - set(staged))}, staged not in scope "
              f"{len(set(staged) - scope)})")
        ok = (not fails and not paged and listed == set(removed)
              and len(staged) == len(dirs) == man["counts"]["players"])
        print(f"   TREE {'CLEAN' if ok else 'NOT CLEAN'}")
        return 0 if ok else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
