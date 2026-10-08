"""Every `needs_ethan` item in the relay's machine reports: its id, and its cluster.

THE ID. `<unit_id>#<index>` - the report's `unit_id` and the entry's 0-based
position in that report's `needs_ethan` array. `a-68#0` is the first item a-68
raised. It is derived from the reports alone, so anyone can re-derive it, and it
is stable for as long as the report is not edited. `what_sha` (12 hex of the
SHA-256 of `what`) travels beside it so an edit under an id shows.

THE CLUSTER. Pre-registered in `docs/F28-answered-ledger-prereg.md` before any
pair was scored: idf-weighted cosine over the token SET of `what`, linked at
>= 0.50, connected components. Read that file before changing a line of
`tokens()` or `cluster()` - the count this produces is quoted, and a change here
is a post hoc change to it.

A cluster's name is the id of its oldest item, so it does not move when a newer
duplicate arrives. It CAN move when a new item bridges two clusters: answers are
therefore recorded against item ids, never against cluster names.
"""
import collections
import datetime as dt
import glob
import hashlib
import json
import math
import os
import re

THRESHOLD = 0.50
SENSITIVITY = (0.40, 0.60)

STOPWORDS = frozenset(
    "the an to of and or in on for is it be by at as with from that this then before "
    "after whether which so not are do into its if".split())

_UNIT = re.compile(r"\b([abcf])-(\d{1,3})(?:-[a-z0-9]+)*\b")
_TOKEN = re.compile(r"[a-z0-9_]+")
_MERGE = re.compile(r"^\s*(merge|merging|push|land|cherry-pick)\b")

Item = collections.namedtuple(
    "Item", "id unit track index what why recommended irreversible spends_money what_sha raised_ts")


def what_sha(what):
    return hashlib.sha256(what.encode("utf-8")).hexdigest()[:12]


def unit_key(unit):
    m = re.match(r"([a-z])-(\d+)$", unit)
    return (m.group(1), int(m.group(2))) if m else (unit, 0)


def _unit_times(relay):
    out = {}
    path = os.path.join(relay, "runlog.jsonl")
    if os.path.exists(path):
        for line in open(path, encoding="utf-8-sig"):
            try:
                row = json.loads(line)
                out[row["unit"]] = dt.datetime.fromisoformat(row["ts"]).timestamp()
            except (ValueError, KeyError):
                continue
    return out


def load(relay):
    """Every item, oldest unit first. Raises when the folder holds no report - an
    empty pile and a wrong path must not look alike."""
    files = glob.glob(os.path.join(relay, "reports", "json", "*.json"))
    if not files:
        raise SystemExit("no machine reports under %s" % os.path.join(relay, "reports", "json"))
    times, items = _unit_times(relay), []
    for f in files:
        d = json.load(open(f, encoding="utf-8-sig"))
        unit = d["unit_id"]
        ts = times.get(unit) or os.path.getmtime(f)
        for i, n in enumerate(d.get("needs_ethan") or []):
            what = n.get("what", "")
            items.append(Item("%s#%d" % (unit, i), unit, d.get("track"), i, what, n.get("why", ""),
                              n.get("recommended", ""), bool(n.get("irreversible")),
                              bool(n.get("spends_money")), what_sha(what), ts))
    items.sort(key=lambda it: (unit_key(it.unit), it.index))
    return items, len(files)


def population_hash(items):
    h = hashlib.sha256()
    for it in sorted(items, key=lambda it: it.id):
        h.update(("%s\t%s\n" % (it.id, it.what)).encode("utf-8"))
    return h.hexdigest()[:16]


def tokens(what):
    text = _UNIT.sub(lambda m: "u_%s_%d" % (m.group(1), int(m.group(2))), what.lower())
    return frozenset(t for t in _TOKEN.findall(text) if len(t) > 1 and t not in STOPWORDS)


def is_merge_class(what):
    low = what.lower()
    return bool(_MERGE.match(low)) or "merge order" in low


def vectors(items):
    toks = [tokens(it.what) for it in items]
    df = collections.Counter(t for ts in toks for t in ts)
    n = len(items)
    out = []
    for ts in toks:
        v = {t: math.log(n / df[t]) for t in ts}
        norm = math.sqrt(sum(w * w for w in v.values()))
        out.append({t: w / norm for t, w in v.items()} if norm else {})
    return out


def cosine(a, b):
    if len(b) < len(a):
        a, b = b, a
    return sum(w * b[t] for t, w in a.items() if t in b)


def similarities(items):
    """{(i, j): cosine} for every pair sharing a token, i < j."""
    vecs = vectors(items)
    by_token = collections.defaultdict(list)
    for i, v in enumerate(vecs):
        for t in v:
            by_token[t].append(i)
    pairs = set()
    for idx in by_token.values():
        for a in range(len(idx)):
            for b in range(a + 1, len(idx)):
                pairs.add((idx[a], idx[b]))
    return {p: cosine(vecs[p[0]], vecs[p[1]]) for p in pairs}


def cluster(items, threshold=None, sims=None):
    """Connected components at `threshold`. Returns clusters, each a list of
    Items oldest first; the list itself is ordered by its newest item, newest
    first."""
    threshold = THRESHOLD if threshold is None else threshold
    sims = similarities(items) if sims is None else sims
    parent = list(range(len(items)))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for (i, j), s in sims.items():
        if s >= threshold:
            parent[find(i)] = find(j)
    groups = collections.defaultdict(list)
    for i, it in enumerate(items):
        groups[find(i)].append(it)
    out = [sorted(g, key=lambda it: (it.raised_ts, unit_key(it.unit), it.index)) for g in groups.values()]
    out.sort(key=lambda g: (-max(it.raised_ts for it in g), g[0].id))
    return out


def cluster_name(group):
    return "Q:" + group[0].id


def min_internal(group, items, sims):
    pos = {it.id: i for i, it in enumerate(items)}
    idx = sorted(pos[it.id] for it in group)
    return min((sims.get((a, b), 0.0) for n, a in enumerate(idx) for b in idx[n + 1:]), default=1.0)
