"""a-16: what widening the export scope does to the committed slug registry.

Reads a store READ-ONLY (export_web.ro(), mode=ro) and the committed
web/slugs/{sport}.json, runs the export's own slug assignment for the default
and the extended scope WITHOUT writing anything, and reports:

  1. existing registry entries whose slug the run would change (by
     construction of assign_slugs this must be 0 - asserted, not assumed);
  2. every newcomer that arrives SUFFIXED because an existing registry holder
     already owns the bare slug, and the subset where the newcomer has more
     regular-season games than that holder (a-14's "49");
  3. for each of those, whether the bare holder's page is published - a key
     in the export's .upload_state.json.

    LOGGER_DB=<store> python -m research.slug_scope_diff [--upload-state PATH] [--json OUT]

LOGGER_DB must be set before import: export_web.ro() reads config.DB_PATH.
"""
import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import config  # noqa: E402
from jobs import export_web as ew  # noqa: E402


def reg_games(rows):
    return sum(1 for r in rows if r["season_type"] == "REG")


def analyse(weeks, xwalk, registry, published_ids):
    out = {}
    all_by = ew.players_by_id(weeks, {r["gsis_id"] for r in weeks})
    for label, extended in (("default", False), ("extended", True)):
        scope = ew.player_scope(weeks, extended=extended)
        by_player, nameless = ew.drop_nameless(ew.players_by_id(weeks, scope), xwalk)
        entries = ew.slug_entries(by_player, xwalk)
        new_reg, added = ew.assign_slugs(entries, registry)
        changed = sorted(p for p in registry if new_reg.get(p) != registry[p])
        if changed:
            raise AssertionError(f"{label}: {len(changed)} existing slugs changed: {changed[:5]}")
        holder_of = {s: p for p, s in registry.items()}
        suffixed = []
        for pid, slug in sorted(added.items()):
            base = ew.slugify(entries[pid]["name"]) or ew.slugify(pid)
            if slug == base:
                continue
            holder = holder_of.get(base)
            # The bare slug went to someone: an existing registry holder, or a
            # namesake arriving in this same run (who, by the rule, has at
            # least as many games).
            if holder is None:
                holder = next(p for p, s in added.items() if s == base)
                holder_kind = "same-run"
            else:
                holder_kind = "registry"
            hg = reg_games(all_by.get(holder, []))
            suffixed.append({
                "id": pid, "name": entries[pid]["name"], "slug": slug, "games": entries[pid]["reg_games"],
                "first_season": entries[pid]["first_season"], "bare": base, "holder": holder,
                "holder_kind": holder_kind, "holder_games": hg,
                "holder_name": ew.resolved_name(holder, all_by.get(holder, [{}]), xwalk)
                if all_by.get(holder) else None,
                "holder_published": holder in published_ids,
                "newcomer_published": pid in published_ids,
            })
        inverted = [s for s in suffixed if s["holder_kind"] == "registry" and s["games"] > s["holder_games"]]
        out[label] = {
            "scope": len(scope), "nameless": len(nameless), "in_scope_named": len(by_player),
            "already_registered": sum(1 for p in by_player if p in registry),
            "added": len(added), "suffixed": len(suffixed),
            "suffixed_vs_registry": sum(1 for s in suffixed if s["holder_kind"] == "registry"),
            "suffixed_vs_same_run": sum(1 for s in suffixed if s["holder_kind"] == "same-run"),
            "existing_changed": len(changed),
            "inverted": inverted,
            "inverted_holder_published": sum(1 for s in inverted if s["holder_published"]),
        }
    return out


def published_player_ids(state_path, sport=None):
    sport = ew.SPORT if sport is None else sport
    if not state_path or not os.path.exists(state_path):
        return None
    with open(state_path, encoding="utf-8") as f:
        state = json.load(f)
    pre = f"{sport}/players/"
    ids = {k[len(pre):].split("/")[0] for k in state if k.startswith(pre) and k.endswith("/summary.json")}
    if not ids:
        raise SystemExit(f"{state_path}: no {pre}*/summary.json keys - wrong file?")
    return ids


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--registry", default=ew.slug_registry_path())
    ap.add_argument("--upload-state", default=None)
    ap.add_argument("--json", default=None)
    a = ap.parse_args(argv)
    registry = ew.load_slug_registry(a.registry)
    if not registry:
        raise SystemExit(f"empty registry at {a.registry}")
    published = published_player_ids(a.upload_state)
    con = ew.ro()
    try:
        weeks = ew.load_player_weeks(con)
        xwalk, _ = ew.load_xwalk(con)
    finally:
        con.close()
    if not weeks:
        raise SystemExit(f"no nfl_player_week rows in {config.DB_PATH}")
    res = analyse(weeks, xwalk, registry, published or set())
    print(f"store {config.DB_PATH}")
    print(f"registry {a.registry}: {len(registry)} entries; "
          f"published player ids: {'n/a' if published is None else len(published)}")
    for label, r in res.items():
        print(f"{label}: scope {r['scope']}  named {r['in_scope_named']}  registered {r['already_registered']}  "
              f"added {r['added']}  suffixed {r['suffixed']} (vs registry {r['suffixed_vs_registry']}, "
              f"vs same run {r['suffixed_vs_same_run']})  existing changed {r['existing_changed']}  "
              f"newcomer-outranks-holder {len(r['inverted'])} (holder published {r['inverted_holder_published']})")
    inv = res["extended"]["inverted"]
    for s in sorted(inv, key=lambda s: -(s["games"] - s["holder_games"])):
        print(f"  {s['slug']:32} {s['games']:4}g  vs bare {s['bare']} = {s['holder']} "
              f"{s['holder_name']!s:24} {s['holder_games']:4}g  holder published={s['holder_published']}")
    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump(res, f, indent=1)
    return res


if __name__ == "__main__":
    main()
