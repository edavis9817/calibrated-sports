"""f-30: what the evidence says about a-72's two irreversible choices. Decides neither.

  slug form   a-72 stages `{slugify(name)}-{athlete id}` with NO registry and calls it
              "stable for as long as the feed's id is". The name is the NEWEST roster
              season's full_name, so the slug also moves when the feed respells a name.
              Counted here: scoped players whose roster name slugifies differently across
              seasons (the URL a publish a year earlier would have minted), and whose
              current-season row was restated in place (valid_to_ts history).
  scope floor who the 14,327 are: position mix, and how thin the thinnest pages are.

cfb.db mode=ro, one short session; the staged tree is read from disk.

    python research/f30_cfb_build/slug_evidence.py --src <a-72 worktree> \
        --db D:/calibrated-sports/data/cfb.db --cfb D:/temp/a72/new
"""
import argparse, json, os, sqlite3, sys
from collections import Counter, defaultdict

ap = argparse.ArgumentParser()
ap.add_argument("--src", required=True); ap.add_argument("--db", required=True); ap.add_argument("--cfb", required=True)
a = ap.parse_args()
src = os.path.abspath(a.src); sys.path.insert(0, src); os.chdir(src)
from jobs.export_web import slugify
idx = json.load(open(os.path.join(a.cfb, "cfb", "players", "index.json"), encoding="utf-8"))
players = idx["players"]
assert len(players) > 10000, len(players)
ids = {int(p["id"]) for p in players}
slug_now = {int(p["id"]): p["slug"] for p in players}
print("staged index: %d players; generated %s" % (len(players), idx["generated_at"]))
bad = [p["slug"] for p in players if not p["slug"].endswith("-" + p["id"])]
print("slugs not ending in their own id: %d %s" % (len(bad), bad[:3]))
bare = Counter(slugify(p["name"]) for p in players)
dup = {k: v for k, v in bare.items() if v > 1}
print("bare-name slugs shared by 2+ players: %d names covering %d players (%.1f%% of pages); largest %s"
      % (len(dup), sum(dup.values()), 100.0 * sum(dup.values()) / len(players), bare.most_common(3)))
print("position: %s" % dict(Counter(p.get("position") for p in players).most_common(14)))

con = sqlite3.connect("file:" + a.db + "?mode=ro", uri=True, timeout=5)
live, hist = defaultdict(dict), defaultdict(set)
for aid, season, name, vto in con.execute("SELECT athlete_id, season, full_name, valid_to_ts FROM cfb_rosters"):
    if aid in ids and name:
        if vto is None:
            live[aid][season] = name
        hist[(aid, season)].add(name)
con.close()
print("scoped players on a roster: %d of %d" % (len(live), len(ids)))
multi = {aid: s for aid, s in live.items() if len({slugify(n) for n in s.values()}) > 1}
moved_last = {aid for aid, s in multi.items() if len(s) >= 2 and
              slugify(s[sorted(s)[-1]]) != slugify(s[sorted(s)[-2]])}
print("roster name slugifies differently across the player's own seasons: %d (%.2f%% of pages)"
      % (len(multi), 100.0 * len(multi) / len(ids)))
print("   of those, the NEWEST season differs from the one before it (a publish one season earlier minted a different URL): %d" % len(moved_last))
for aid in sorted(multi)[:6]:
    print("      %s: %s -> staged slug %s" % (aid, json.dumps(multi[aid]), slug_now.get(aid)))
restated = {k for k, v in hist.items() if len({slugify(n) for n in v}) > 1}
print("(player, season) roster rows whose name was restated IN PLACE across ingests (slug-changing): %d, players %d"
      % (len(restated), len({k[0] for k in restated})))
mism = [aid for aid, s in live.items() if not slug_now[aid].startswith(slugify(s[sorted(s)[-1]]))]
print("check: staged slug is not the newest roster name's slug for %d players (0 = the rule is as read)" % len(mism))
