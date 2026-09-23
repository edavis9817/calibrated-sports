"""Is the published CFB export what a reader actually gets? (c-12)

Reads the local export tree (WEB_EXPORT_DIR, read-only) and fetches the same keys
over HTTP from the public site - never from R2 - then reports:

  1. served     every key under a prefix, byte-compared against the local file
  2. private    the exporter's `_state/upload_state.json` must 404 publicly, under
                the encodings a path sanitiser could miss
  3. record     local tree vs `.upload_state.json`: counts by top-level prefix and
                keys whose bytes differ from what was last uploaded
  4. gaps       the colour and opponent-abbreviation gaps in the CFB team files,
                split by cause

Every count is asserted non-zero where zero would mean the walk read nothing.

    python -m research.cfb_served_check --host https://calibratedsports.com
    python -m research.cfb_served_check --host ... --prefix nfl --sample 300

Makes GET requests only. Writes nothing.
"""
import argparse
import collections
import glob
import hashlib
import json
import os
import random
import urllib.error
import urllib.request

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/128"
STATE_FILE = ".upload_state.json"
PRIVATE_PROBES = [
    "/data/_state/upload_state.json",
    "/data/%5Fstate/upload_state.json",
    "/data/%5fstate%2Fupload_state.json",
    "/data/nfl/../_state/upload_state.json",
    "/data/nfl/%2E%2E/_state/upload_state.json",
    "/data/_STATE/upload_state.json",
    "/data/_state/upload_state.json?x=1",
    "/data/.upload_state.json",
    "/_state/upload_state.json",
]


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def local_keys(root, prefix=""):
    out = {}
    for d, _, fs in os.walk(os.path.join(root, prefix)):
        for f in fs:
            if f == STATE_FILE:
                continue
            p = os.path.join(d, f)
            out[os.path.relpath(p, root).replace("\\", "/")] = p
    return out


def served(root, host, prefix, sample=None, seed=12):
    keys = sorted(local_keys(root, prefix))
    assert keys, f"walked zero local keys under {prefix!r}"
    if sample:
        keys = random.Random(seed).sample(keys, min(sample, len(keys)))
    res = collections.Counter()
    bad = []
    for k in keys:
        code, body = fetch(f"{host}/data/{k}")
        if code != 200:
            res[f"http {code}"] += 1
            bad.append((k, code))
        elif body == open(os.path.join(root, k), "rb").read():
            res["match"] += 1
        else:
            res["differ"] += 1
            bad.append((k, "bytes differ"))
    return len(keys), dict(res), bad[:20]


def private(host):
    # curl --path-as-is equivalent: urllib does not normalise dot segments.
    return {p: fetch(host + p)[0] for p in PRIVATE_PROBES}


def record(root):
    state = json.load(open(os.path.join(root, STATE_FILE)))
    local = local_keys(root)
    assert state and local, "empty upload record or empty tree"
    changed = [k for k, p in local.items() if k in state
               and hashlib.sha256(open(p, "rb").read()).hexdigest() != state[k]]
    top = lambda ks: dict(collections.Counter(k.split("/")[0] for k in ks))
    return {"recorded": len(state), "local": len(local), "recorded_by_prefix": top(state),
            "local_by_prefix": top(local), "changed": len(changed),
            "local_not_recorded": sum(k not in state for k in local),
            "recorded_not_local": sum(k not in local for k in state)}


def gaps(root):
    base = os.path.join(root, "cfb")
    m = json.load(open(os.path.join(base, "manifest.json")))
    colors = m["team_colors"]
    teams = m["teams"]
    files = sorted(glob.glob(os.path.join(base, "teams", "*.json")))
    assert len(files) == len(teams) > 0, (len(files), len(teams))
    games_of = {}
    docs = []
    for f in files:
        d = json.load(open(f))
        docs.append(d)
        games_of[d["identity"]["abbr"]] = {r["game_id"] for r in d["schedule"]}
    slugs = {t["slug"] for t in teams}
    out = collections.Counter()
    hit_teams, hit_teams_2026, other_school, blank = set(), set(), [], []
    for d in docs:
        s = d["identity"]["slug"]
        for r in d["schedule"]:
            out["rows"] += 1
            out["opponent_is_a_slug"] += r["opponent"] in slugs
            a = r["opponent_abbr"]
            if a == "" or a is None:
                blank.append((s, r["season"], r["label"], r["opponent"], a))
                continue
            if a not in colors:
                out["opponent_no_colour"] += 1
                hit_teams.add(s)
                if r["season"] == m["current"]["season"]:
                    hit_teams_2026.add(s)
            if a in games_of and r["game_id"] not in games_of[a]:
                other_school.append((s, r["season"], r["opponent"], a))
    return {
        "teams": len(teams),
        "teams_without_own_colour": sum(t["abbr"] not in colors for t in teams),
        "colour_entries": len(colors),
        "schedule": dict(out),
        "teams_with_a_colourless_opponent_row": len(hit_teams),
        "teams_with_one_in_current_season": sorted(hit_teams_2026),
        "blank_opponent_abbr_rows": blank,
        "rows_coloured_as_another_school": len(other_school),
        "teams_with_such_a_row": len({x[0] for x in other_school}),
        "another_school_examples": collections.Counter(
            (x[2], x[3]) for x in other_school).most_common(10),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", required=True)
    ap.add_argument("--root", default=os.environ.get("WEB_EXPORT_DIR"))
    ap.add_argument("--prefix", default="cfb")
    ap.add_argument("--sample", type=int)
    a = ap.parse_args()
    if not a.root:
        raise SystemExit("--root or WEB_EXPORT_DIR required; refusing to guess a path")
    print("served", a.prefix, served(a.root, a.host, a.prefix, a.sample))
    print("private", private(a.host))
    print("record", record(a.root))
    if a.prefix == "cfb":
        print("gaps", json.dumps(gaps(a.root), indent=1, default=str))


if __name__ == "__main__":
    main()
