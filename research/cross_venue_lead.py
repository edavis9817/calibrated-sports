"""Which venue moves first on a claim quoted in two places (a-59). Read-only.

    python -m research.cross_venue_lead --extract D:/temp/a59/xv.db   # pull from the store, mode=ro
    python -m research.cross_venue_lead --cache D:/temp/a59/xv.db     # every table below
    python -m research.cross_venue_lead --cache ... --json out.json   # the same, machine-readable

The store's cross-venue join (`market_outcome`, a-53) names the claims two
venues share. `jobs/cross_venue.py` asks whether two prices DISAGREE; this asks
which of them MOVED SECOND, and how long after. It needs no view about football.

WHAT A LEAD IS NOT. A measured lead is not an edge. It says which venue to read
and which to quote into - an input to a maker question, never a trade on its own.

RESOLUTION COMES FIRST, because it bounds everything after it. The logger polls
on a tier keyed to kickoff (CLAUDE.md, "Polling tiers") and writes a row only on
a change or a QUOTE_HEARTBEAT_SEC heartbeat. So a change is timed to the POLL
that saw it: the gap between consecutive polls of that tier is the resolution,
not the gap between rows. That poll gap is measured here from `poll_log`, per
venue and tier, not taken from config. The Odds API is not polled at all - it is
snapshot-scheduled at fixed offsets before kickoff - so a book's price is known
only at a handful of instants per game and a book move is bracketed between two
snapshots, hours apart. Every figure below carries the tier it came from.

THE EXTRACT. `--extract` copies what the analysis needs from the store into a
scratch SQLite file in short, separate read statements (no long read
transaction pins the logger's WAL), and never writes the store. Everything else
reads the scratch file, so the analysis re-runs without touching the store.
The store prunes live quotes at 14 days on ingest_ts, so a later extract sees
less: the extract records when it was taken.

THE SPREAD FOLD. Kalshi keys a spread rung "BUF wins by over 1.5" as line +1.5;
Polymarket keys "Spread: Bills (-1.5)" as line -1.5. Same claim, two outcome ids,
so the store join never pairs them. This script pairs them explicitly, labelled
`join=folded`, and lists the fold as a mapping work item: it does not fix the
store.
"""
import argparse
import bisect
import json
import math
import os
import random
import sqlite3
import statistics
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config  # noqa: E402
from venues import oddsapi  # noqa: E402

SEASON = 2026

# --- tiers, as the logger defines them (CLAUDE.md "Polling tiers") -----------
HOT_S = 240 * 60
LIVE_S = 240 * 60
COLD_S = 24 * 3600


def tier(secs_to_kick):
    if secs_to_kick is None:
        return "unknown"
    if 0 < secs_to_kick < HOT_S:
        return "hot"
    if -LIVE_S < secs_to_kick <= 0:
        return "live"
    if 0 < secs_to_kick <= COLD_S:
        return "game"
    return "cold"


def kick_bucket(secs_to_kick):
    """Time to kickoff, for the split the brief asks for."""
    s = secs_to_kick
    if s is None:
        return "unknown"
    if s <= 0:
        return "in-game" if s > -LIVE_S else "post-game"
    if s <= 3600:
        return "0-1h"
    if s <= 6 * 3600:
        return "1-6h"
    if s <= 24 * 3600:
        return "6-24h"
    return ">24h"


# =============================================================================
# extract (the only part that touches the store, and only mode=ro)
# =============================================================================

def ro():
    return sqlite3.connect(f"file:{config.DB_PATH}?mode=ro", uri=True)


def venue_class(venue):
    return "book" if venue.startswith("oddsapi:") else venue


def parse_key(key):
    sport, season, wk, mt, entity, stat, line, side = key.split("|")
    return {"market_type": mt, "entity": entity, "stat": stat,
            "line": None if line == "na" else float(line), "side": side}


def cross_pairs(con, season=SEASON):
    """[(outcome_id, key, week, event_id, venue, market_id, join)] for every
    claim quoted by more than one venue CLASS (kalshi / polymarket / book),
    plus the folded spread pairs. `join` is 'store' or 'folded'."""
    rows = con.execute(
        "SELECT mo.outcome_id, o.key, o.week, o.event_id, mo.venue, mo.market_id "
        "FROM market_outcome mo JOIN outcomes o USING (outcome_id) "
        "WHERE o.season = ? AND mo.outcome_id IS NOT NULL", (season,)).fetchall()
    by = defaultdict(list)
    for r in rows:
        by[r[0]].append(r)
    out = []
    for oid, rs in by.items():
        if len({venue_class(r[4]) for r in rs}) > 1:
            out += [(*r, "store") for r in rs]
    # the fold: polymarket spread|team|-L|over  ==  kalshi spread|team|+L|over
    kal = {}
    for oid, rs in by.items():
        for r in rs:
            if r[4] == "kalshi" and "|spread|" in r[1]:
                kal[r[1]] = r
    for oid, rs in by.items():
        for r in rs:
            if r[4] != "polymarket" or "|spread|" not in r[1]:
                continue
            p = r[1].split("|")
            try:
                line = float(p[6])
            except ValueError:
                continue
            p[6] = repr(-line) if -line != int(-line) else str(-line)
            twin = kal.get("|".join(p))
            if twin is None:
                continue
            fid = "fold:" + twin[0]
            out.append((fid, twin[1], twin[2], twin[3], "kalshi", twin[5], "folded"))
            out.append((fid, twin[1], r[2], r[3], "polymarket", r[5], "folded"))
    return out


SCRATCH_DDL = """
CREATE TABLE meta (k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE pairs (outcome_id TEXT, key TEXT, week INTEGER, event_id TEXT,
                    venue TEXT, market_id TEXT, joined TEXT);
CREATE TABLE games (event_id TEXT PRIMARY KEY, kickoff_ts REAL, home TEXT, away TEXT);
CREATE TABLE q (venue TEXT, market_id TEXT, ts REAL, bid REAL, ask REAL, mid REAL,
                source TEXT, ingest_ts REAL, source_ts REAL, line REAL, side TEXT);
CREATE INDEX ix_q ON q(venue, market_id, ts);
CREATE TABLE depth (market_id TEXT, ts REAL, side TEXT, touch_size REAL);
CREATE INDEX ix_depth ON depth(market_id, side, ts);
CREATE TABLE polls (ts REAL, venue TEXT, endpoint TEXT, ok INTEGER, n_markets INTEGER);
"""


def extract(dest):
    """Copy what the analysis needs into a scratch file. Store read-only."""
    if os.path.exists(dest):
        raise SystemExit(f"refusing to overwrite {dest}; remove it first")
    src = ro()
    out = sqlite3.connect(dest)
    out.executescript(SCRATCH_DDL)
    t0 = time.time()
    pairs = cross_pairs(src)
    out.executemany("INSERT INTO pairs VALUES (?,?,?,?,?,?,?)", pairs)
    events = {p[3] for p in pairs if p[3]}
    for eid in events:
        g = src.execute("SELECT game_id, MAX(kickoff_ts), home_team, away_team FROM nfl_games "
                        "WHERE game_id = ? GROUP BY game_id", (eid,)).fetchone()
        if g:
            out.execute("INSERT INTO games VALUES (?,?,?,?)", g)
    n_q = 0
    seen_book = set()
    for venue, mid in sorted({(p[4], p[5]) for p in pairs}):
        if venue.startswith("oddsapi:"):
            qmid, line = oddsapi.split_prop_market_id(mid)
            head, _, side = qmid.rpartition("|")
            for s in ("Over", "Under"):
                qid = f"{head}|{s}"
                if (venue, qid, line) in seen_book:
                    continue
                seen_book.add((venue, qid, line))
                rows = src.execute(
                    "SELECT ts, best_bid, best_ask, mid, source, ingest_ts, source_ts, line, side "
                    "FROM quotes WHERE venue = ? AND market_id = ? AND line = ?",
                    (venue, qid, line)).fetchall()
                # stored under the INSTRUMENT id of the over, so over and under travel together
                out.executemany("INSERT INTO q VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                                [(venue, f"{head}|Over|{line!r}", *r) for r in rows])
                n_q += len(rows)
        else:
            rows = src.execute(
                "SELECT ts, best_bid, best_ask, mid, source, ingest_ts, source_ts, line, side "
                "FROM quotes WHERE venue = ? AND market_id = ?", (venue, mid)).fetchall()
            out.executemany("INSERT INTO q VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                            [(venue, mid, *r) for r in rows])
            n_q += len(rows)
            if venue == "kalshi":
                out.executemany("INSERT INTO depth VALUES (?,?,?,?)", src.execute(
                    "SELECT market_id, ts, side, touch_size FROM market_depth "
                    "WHERE venue = 'kalshi' AND market_id = ?", (mid,)).fetchall())
    out.executemany("INSERT INTO polls VALUES (?,?,?,?,?)", src.execute(
        "SELECT ts, venue, endpoint, ok, n_markets FROM poll_log"))
    out.executemany("INSERT INTO meta VALUES (?,?)", [
        ("extracted_ts", repr(time.time())), ("db_path", str(config.DB_PATH)),
        ("pairs", str(len(pairs))), ("quote_rows", str(n_q)),
        ("seconds", f"{time.time() - t0:.1f}")])
    out.commit()
    out.close()
    src.close()
    print(f"extract: {len(pairs):,} pair rows, {n_q:,} quote rows, {time.time() - t0:.0f}s -> {dest}")


# =============================================================================
# analysis (reads the scratch file only)
# =============================================================================

THETA = 0.05                  # a material move: the mid moves >= 5pp between two polls
THETAS = (0.03, 0.05, 0.10)   # sensitivity, reported beside the primary
FOLLOW = 0.5                  # a follower's move need only be half of theta
SPREAD_CAP = 0.05             # a move counts only on a book <= 5c wide at both rows
WINDOW = 900.0                # +-15 min around a polled move
MIN_DEPTH = 100.0             # contracts at the touch, both sides, within DEPTH_AGE of the move
DEPTH_AGE = 60.0
HEARTBEAT = 300.0
POLL = {"live": 10.0, "hot": 15.0, "game": 60.0, "cold": 600.0}   # re-measured below
DRAWS = 2000
SEED = 59


def max_gap(t):
    """The longest row gap still consistent with continuous polling: a
    heartbeat row lands every HEARTBEAT on an unchanged price, and the cold
    tier polls less often than that."""
    return max(HEARTBEAT, POLL.get(t, 600.0)) * 1.2 + 30.0


def series_of(venue, market_id, key):
    if venue == "kalshi":
        return market_id.split("-")[0]
    if venue.startswith("oddsapi:"):
        return market_id.split("|")[1]
    return "poly:" + parse_key(key)["market_type" if "|player_prop|" not in key else "stat"]


def market_kind(key):
    k = parse_key(key)
    return "prop" if k["market_type"] == "player_prop" else k["market_type"]


def load(path):
    c = sqlite3.connect(path)
    meta = dict(c.execute("SELECT k, v FROM meta"))
    kick = {e: k for e, k in c.execute("SELECT event_id, kickoff_ts FROM games")}
    pairs = defaultdict(lambda: {"venues": defaultdict(list)})
    for oid, key, week, eid, venue, mid, joined in c.execute("SELECT * FROM pairs"):
        o = pairs[oid]
        o.update(key=key, week=week, event_id=eid, kick=kick.get(eid), joined=joined)
        o["venues"][venue_class(venue)].append((venue, mid))
    quotes = defaultdict(list)
    for row in c.execute("SELECT venue, market_id, ts, bid, ask, mid, source, source_ts, side "
                         "FROM q WHERE source = 'live' ORDER BY ts"):
        quotes[(row[0], row[1])].append(row[2:])
    depth = defaultdict(list)
    for mid, ts, side, size in c.execute("SELECT market_id, ts, side, touch_size FROM depth ORDER BY ts"):
        depth[(mid, side)].append((ts, size))
    polls = defaultdict(list)
    for ts, venue, ep in c.execute("SELECT ts, venue, endpoint FROM polls WHERE ok = 1 ORDER BY ts"):
        polls[(venue, ep)].append(ts)
    c.close()
    return meta, dict(pairs), quotes, depth, polls


def polled_obs(rows, kick):
    """[(ts, mid, spread, tier)] two-sided rows of a polled venue."""
    out = []
    for ts, bid, ask, _mid, _src, _sts, _side in rows:
        if bid is None or ask is None or not (0 <= bid < ask <= 1):
            continue
        out.append((ts, (bid + ask) / 2, ask - bid, tier(None if kick is None else kick - ts)))
    return out


def book_series(quotes, instruments):
    """[(snapshot ts, {book: price})] for one claim.

    A book's price is its multiplicative de-vig where it quotes BOTH sides of
    the line, else its raw vig-inclusive over price. 12,333 of 15,071 book
    instruments here are over-only alternates, so a de-vig-only series would
    drop four lines in five. For TIMING a move the raw price is fine - a
    near-constant margin does not move a level shift - but it is not a
    probability, and nothing here reads it as one. `basis` counts which."""
    per = defaultdict(list)
    basis = Counter()
    for venue, mid in instruments:
        snaps = defaultdict(dict)
        for ts, _b, _a, p, _src, _sts, side in quotes.get((venue, mid), []):
            snaps[round(ts)][side] = p
        for ts, sides in snaps.items():
            o, u = sides.get("Over"), sides.get("Under")
            if o is None:
                continue
            if u is not None and o + u > 0:
                per[ts].append((venue, o / (o + u)))
                basis["devig"] += 1
            else:
                per[ts].append((venue, o))
                basis["raw over"] += 1
    # books in one Odds API call share a capture instant; merge instants a few
    # seconds apart into one snapshot
    out, cur = [], None
    for ts in sorted(per):
        if cur and ts - cur[0] <= 30:
            cur[1].update(dict(per[ts]))
        else:
            cur = [ts, dict(per[ts])]
            out.append(cur)
    return [(ts, books) for ts, books in out], basis


def asof(obs, t, gap):
    ts = [o[0] for o in obs]
    i = bisect.bisect_right(ts, t) - 1
    if i < 0 or t - obs[i][0] > gap:
        return None
    return obs[i]


def jumps(obs, theta, cap=None):
    """Material moves on one polled venue: consecutive two-sided rows whose mid
    moved >= theta, on a book no wider than SPREAD_CAP at BOTH rows, with the
    rows close enough together that the logger was polling throughout.

    The spread cap is what makes a move a price change rather than a book
    flickering: a Polymarket jump happens on a median 13c book against a 4c
    book overall, and a mid that jumps because one side emptied is the
    midpoint of an absence (jobs/cross_venue.py's own warning)."""
    cap = SPREAD_CAP if cap is None else cap
    out, why = [], Counter()
    for a, b in zip(obs, obs[1:]):
        d = b[1] - a[1]
        if abs(d) < theta:
            continue
        if a[2] > cap or b[2] > cap:
            why["wide book"] += 1
            continue
        if b[0] - a[0] > max_gap(b[3]):
            why["logger gap"] += 1
            continue
        out.append({"t": b[0], "d": 1 if d > 0 else -1, "size": abs(d), "tier": b[3],
                    "pre": a[1], "post": b[1], "spread": b[2]})
    return out, why


def crossing(obs, level, d, lo, hi):
    """First observation time in (lo, hi] at which the mid sits at or beyond
    `level` in direction d."""
    for o in obs:
        if o[0] <= lo:
            continue
        if o[0] > hi:
            break
        if d * (o[1] - level) >= 0:
            return o[0]
    return None


def had_depth(depth, market_id, t):
    """Both touches carried >= MIN_DEPTH contracts at the latest depth read
    within DEPTH_AGE before t. None when no read is that recent."""
    sizes = []
    for side in ("buy_yes", "buy_no"):
        rows = depth.get((market_id, side), [])
        i = bisect.bisect_right([r[0] for r in rows], t) - 1
        if i < 0 or t - rows[i][0] > DEPTH_AGE:
            return None
        sizes.append(rows[i][1] or 0)
    return min(sizes) >= MIN_DEPTH


def classify_gap(k, p, ks, ps):
    from jobs.cross_venue import classify
    return classify(k - p, ks, ps)


def polled_episodes(o, quotes, depth, theta):
    """Kalshi against Polymarket, both polled. A move of >= theta on one venue
    is matched to the NEAREST same-direction move of >= FOLLOW x theta on the
    other within +-WINDOW, one-to-one, in time order. The lead is the gap
    between the two poll instants that saw them. A follower that drifts in by
    small steps is not a jump and counts as "alone" - stated, not hidden.

    The first version matched on the follower's mid CROSSING a level set at
    t - WINDOW. Slow in-game drift crossed it anywhere in the window and the
    lead piled up at the window edges (+-600-900s) - a lead manufactured by
    the window, not measured by it."""
    kv, pv = o["venues"]["kalshi"][0], o["venues"]["polymarket"][0]
    K = polled_obs(quotes.get(kv, []), o["kick"])
    P = polled_obs(quotes.get(pv, []), o["kick"])
    if not K or not P:
        return [], Counter({"no_live_quotes": 1})
    notes = Counter()
    jk, wk = jumps(K, theta)
    jp, wp = jumps(P, theta)
    fk, _ = jumps(K, FOLLOW * theta)
    fp, _ = jumps(P, FOLLOW * theta)
    notes.update({f"kalshi {k}": v for k, v in wk.items()})
    notes.update({f"polymarket {k}": v for k, v in wp.items()})
    allj = sorted([("kalshi", j) for j in jk] + [("polymarket", j) for j in jp], key=lambda x: x[1]["t"])
    used = set()
    eps = []
    for venue, j in allj:
        if (venue, j["t"]) in used:
            continue
        used.add((venue, j["t"]))
        other, oobs, fol = (("polymarket", P, fp) if venue == "kalshi" else ("kalshi", K, fk))
        cands = [f for f in fol if f["d"] == j["d"] and abs(f["t"] - j["t"]) <= WINDOW
                 and (other, f["t"]) not in used]
        quoted = asof(oobs, j["t"], max_gap(j["tier"])) is not None
        match = min(cands, key=lambda f: abs(f["t"] - j["t"])) if cands else None
        if match is not None:
            used.add((other, match["t"]))
        res = max(POLL.get(j["tier"], 600.0), POLL.get(match["tier"], 600.0) if match else 0.0)
        if match is None:
            outcome = f"{venue} alone" if quoted else "other venue not quoted"
            lead_k = None
            t_first, first_venue = j["t"], venue
        else:
            lead = match["t"] - j["t"]             # > 0: the anchor venue moved first
            lead_k = lead if venue == "kalshi" else -lead
            if abs(lead) <= res:
                outcome = "tie within resolution"
            else:
                outcome = "kalshi first" if lead_k > 0 else "polymarket first"
            t_first = min(j["t"], match["t"])
            first_venue = venue if lead >= 0 else other
        # the gap at the instant BEFORE the first mover moved, in jobs/cross_venue.py's buckets
        k_at, p_at = asof(K, t_first - 1, 3600), asof(P, t_first - 1, 3600)
        bucket = (classify_gap(k_at[1], p_at[1], k_at[2], p_at[2])
                  if k_at and p_at else "not both quoted")
        dep = had_depth(depth, kv[1], t_first) if first_venue == "kalshi" else None
        # an "alone" move: did the other venue drift the same way anyway, by
        # small steps or on a book too wide to count? Its mid over the window,
        # whatever the spread - a diagnostic of what "alone" hides, not a lead.
        drift = None
        if match is None and quoted:
            a0, a1 = asof(oobs, j["t"] - 1, max_gap(j["tier"])), asof(oobs, j["t"] + WINDOW, max_gap(j["tier"]))
            if a0 and a1:
                drift = j["d"] * (a1[1] - a0[1]) >= FOLLOW * theta
        eps.append({"outcome_id": o["id"], "event_id": o["event_id"], "pair": "kalshi-polymarket",
                    "other_drifted": drift,
                    "kind": market_kind(o["key"]), "joined": o["joined"], "anchor": venue,
                    "t": j["t"], "size": j["size"], "tier": j["tier"], "resolution_s": res,
                    "kick_bucket": kick_bucket(None if o["kick"] is None else o["kick"] - t_first),
                    "outcome": outcome, "lead_kalshi_s": lead_k, "first": first_venue,
                    "depth": {True: "depth", False: "thin", None: "no depth read"}[dep],
                    "bucket": bucket})
    return eps, notes


def bracket_episodes(o, quotes, theta):
    """Kalshi against the books, at SNAPSHOT resolution.

    Between consecutive snapshots s0 < s1 each side has one net move: the
    books' is the median change over books quoting both snapshots, Kalshi's is
    its two-sided mid as-of s1 minus as-of s0 (both <= SPREAD_CAP wide). An
    episode opens at a bracket where either side moved >= theta. The other
    side is then found moving (>= FOLLOW x theta, same direction) in the
    SAME bracket (order unresolvable - the only honest answer at hours of
    resolution), the bracket BEFORE (it led), the bracket AFTER (it followed)
    or not at all."""
    kv = o["venues"]["kalshi"][0]
    K = polled_obs(quotes.get(kv, []), o["kick"])
    B, basis = book_series(quotes, o["venues"]["book"])
    notes = Counter({f"book basis {k}": v for k, v in basis.items()})
    if not K or len(B) < 2:
        notes["claims with no series"] += 1
        return [], notes
    moves = []
    for (s0, b0), (s1, b1) in zip(B, B[1:]):
        common = [b1[v] - b0[v] for v in b0 if v in b1]
        bm = statistics.median(common) if common else None
        k0 = asof(K, s0, max_gap(tier(None if o["kick"] is None else o["kick"] - s0)))
        k1 = asof(K, s1, max_gap(tier(None if o["kick"] is None else o["kick"] - s1)))
        km = None
        if k0 and k1:
            if k0[2] <= SPREAD_CAP and k1[2] <= SPREAD_CAP:
                km = k1[1] - k0[1]
            else:
                notes["brackets with a kalshi book wider than the cap"] += 1
        else:
            notes["brackets with kalshi unquoted at an end"] += 1
        moves.append({"s0": s0, "s1": s1, "book": bm, "kalshi": km, "n_books": len(common)})
    eps, used = [], set()
    for i, m in enumerate(moves):
        for leader, other in (("book", "kalshi"), ("kalshi", "book")):
            v = m[leader]
            if v is None or abs(v) < theta or (leader, i) in used:
                continue
            used.add((leader, i))
            d = 1 if v > 0 else -1

            def moved(j):
                if not 0 <= j < len(moves) or (other, j) in used:
                    return False
                w = moves[j][other]
                return w is not None and d * w >= FOLLOW * theta

            if m[other] is None:
                where, first = f"{other} not comparable in the bracket", None
            elif moved(i):
                where, first = "same bracket (order unresolvable)", None
                used.add((other, i))
            elif moved(i - 1):
                where, first = f"{other} first (a bracket earlier)", other
                used.add((other, i - 1))
            elif moved(i + 1):
                where, first = f"{leader} first ({other} followed a bracket later)", leader
                used.add((other, i + 1))
            else:
                where, first = f"{leader} alone", leader
            eps.append({"outcome_id": o["id"], "event_id": o["event_id"], "pair": "kalshi-book",
                        "kind": market_kind(o["key"]), "stat": parse_key(o["key"])["stat"],
                        "anchor": leader, "bracket_s": m["s1"] - m["s0"], "size": abs(v),
                        "n_books": m["n_books"], "first": first,
                        "kick_bucket": kick_bucket(None if o["kick"] is None else o["kick"] - m["s1"]),
                        "where": where})
    return eps, notes


def boot(eps, stat, draws=DRAWS, seed=SEED):
    """Game-block bootstrap: resample games, keep each game's episodes whole."""
    by = defaultdict(list)
    for e in eps:
        by[e["event_id"]].append(e)
    games = sorted(by)
    est = stat(eps)
    if est is None or len(games) < 2:
        return {"est": est, "lo": None, "hi": None, "games": len(games), "n": len(eps)}
    rng = random.Random(seed)
    vals = []
    for _ in range(draws):
        sample = [e for g in (rng.choice(games) for _ in games) for e in by[g]]
        v = stat(sample)
        if v is not None:
            vals.append(v)
    vals.sort()
    return {"est": est, "lo": vals[int(0.025 * len(vals))], "hi": vals[int(0.975 * len(vals)) - 1],
            "games": len(games), "n": len(eps), "informative": len(games) >= 5}


def share(outcome):
    def f(eps):
        return (sum(e["outcome"] == outcome for e in eps) / len(eps)) if eps else None
    return f


def median_lead(eps):
    v = [e["lead_kalshi_s"] for e in eps if e["lead_kalshi_s"] is not None]
    return statistics.median(v) if v else None


def resolution_table(pairs, quotes, polls):
    """Per venue, series and tier: the poll cadence actually achieved (from
    poll_log) and what the rows of THESE markets show."""
    cadence = {}
    for (venue, ep), ts in polls.items():
        if not ep.startswith("quotes:"):
            continue
        g = sorted(b - a for a, b in zip(ts, ts[1:]) if b - a < 3 * 600)
        if g:
            cadence[(venue, ep.split(":")[1])] = (g[len(g) // 2], len(ts))
    rows = defaultdict(lambda: {"markets": set(), "rows": 0, "gaps": []})
    for oid, o in pairs.items():
        for cls, insts in o["venues"].items():
            if cls == "book":
                continue
            for venue, mid in insts:
                obs = quotes.get((venue, mid), [])
                prev = None
                for r in obs:
                    t = tier(None if o["kick"] is None else o["kick"] - r[0])
                    k = (venue, series_of(venue, mid, o["key"]), o["week"], t)
                    rows[k]["markets"].add(mid)
                    rows[k]["rows"] += 1
                    if prev is not None and prev[1] == t:
                        rows[k]["gaps"].append(r[0] - prev[0])
                    prev = (r[0], t)
    out = []
    for (venue, series, week, t), v in sorted(rows.items(), key=lambda kv: tuple(str(x) for x in kv[0])):
        g = sorted(v["gaps"])
        cad = cadence.get((venue, t))
        out.append({"venue": venue, "series": series, "week": week, "tier": t,
                    "poll_cadence_s": None if not cad else round(cad[0], 1),
                    "markets": len(v["markets"]), "rows": v["rows"],
                    "rows_per_market": round(v["rows"] / max(1, len(v["markets"])), 1),
                    "row_gap_p05_s": None if not g else round(g[int(0.05 * len(g))], 1),
                    "row_gap_p50_s": None if not g else round(g[len(g) // 2], 1)})
    return out, cadence


def book_resolution(pairs, quotes):
    """Books: the distance between consecutive snapshots of one game IS the
    resolution, by how far before kickoff the later snapshot fell."""
    seen = {}
    for o in pairs.values():
        if "book" not in o["venues"] or o["kick"] is None:
            continue
        for venue, mid in o["venues"]["book"]:
            for r in quotes.get((venue, mid), []):
                seen.setdefault(o["event_id"], set()).add(round(r[0] / 30) * 30)
    by = defaultdict(list)
    kick = {o["event_id"]: o["kick"] for o in pairs.values()}
    for eid, ts in seen.items():
        ts = sorted(ts)
        merged = [ts[0]]
        for t in ts[1:]:
            if t - merged[-1] > 120:
                merged.append(t)
        for a, b in zip(merged, merged[1:]):
            by[round((kick[eid] - b) / 600) * 10].append(b - a)
    return [{"snapshot_min_before_kick": k, "n": len(v), "bracket_p50_min": round(sorted(v)[len(v) // 2] / 60, 1)}
            for k, v in sorted(by.items(), reverse=True)]


def mapping_check(pairs, quotes):
    """Claims whose two venues' prices sit so far apart, with tight books, that
    they cannot be the same claim. Kalshi vs Polymarket: median signed gap at
    co-quoted instants with both spreads <= 5c. Kalshi vs books: the median gap
    between the book consensus and Kalshi's mid at each snapshot, Kalshi
    spread <= 5c. Flag above 0.25 (the threshold jobs/cross_venue.py uses).
    Returns (flagged, checked): `checked` is every claim the test could be run
    on, so a count of zero flagged can be told apart from a test that ran on
    nothing. Book prices here include one-sided RAW over prices, which carry
    the margin, so a book gap of a few points is the vig, not a mapping."""
    out, checked = [], []
    for oid, o in pairs.items():
        if "kalshi" not in o["venues"]:
            continue
        kv = o["venues"]["kalshi"][0]
        K = polled_obs(quotes.get(kv, []), o["kick"])
        if not K:
            continue
        diffs = []
        other = None
        if "polymarket" in o["venues"]:
            other = "polymarket"
            P = polled_obs(quotes.get(o["venues"]["polymarket"][0], []), o["kick"])
            for t, pm, ps, _t in P:
                k = asof(K, t, 330)
                if k and k[2] <= 0.05 and ps <= 0.05:
                    diffs.append(k[1] - pm)
        elif "book" in o["venues"]:
            other = "book"
            B, _ = book_series(quotes, o["venues"]["book"])
            for t, books in B:
                if o["kick"] is not None and t > o["kick"]:
                    continue
                bp = statistics.median(books.values())
                k = asof(K, t, 700)
                if k and k[2] <= 0.05:
                    diffs.append(k[1] - bp)
        if len(diffs) >= 3:
            med = statistics.median(diffs)
            checked.append((other, abs(med)))
            if abs(med) > 0.25:
                out.append({"outcome_id": oid, "key": o["key"], "joined": o["joined"], "vs": other,
                            "kalshi_market": kv[1], "other_markets": [m for _v, m in o["venues"][other]][:3],
                            "median_gap": round(med, 3), "n_instants": len(diffs)})
    return sorted(out, key=lambda r: -abs(r["median_gap"])), checked


def report(path, json_out=None):
    meta, pairs, quotes, depth, polls = load(path)
    for oid, o in pairs.items():
        o["id"] = oid
    res = {"extract": meta, "definitions": {
        "theta": THETA, "follow_fraction": FOLLOW, "spread_cap": SPREAD_CAP, "window_s": WINDOW, "min_depth": MIN_DEPTH,
        "heartbeat_s": HEARTBEAT, "bootstrap": f"game block, {DRAWS} draws, seed {SEED}"}}

    print("=" * 78 + "\n1. ACHIEVABLE RESOLUTION - read this before any lead\n" + "=" * 78)
    table, cadence = resolution_table(pairs, quotes, polls)
    for (venue, t), (g, n) in sorted(cadence.items()):
        POLL[t] = max(POLL.get(t, 0), g) if venue in ("kalshi", "polymarket") else POLL.get(t)
    print("  poll cadence measured from poll_log (median gap between polls of one tier):")
    for (venue, t), (g, n) in sorted(cadence.items()):
        print(f"    {venue:<11} {t:<8} {g:7.1f}s   over {n:,} polls")
    print("\n  rows of the cross-venue markets, by venue / series / week / tier:")
    print(f"    {'venue':<11}{'series':<18}{'wk':>3} {'tier':<8}{'poll':>7}{'mkts':>6}{'rows/mkt':>9}{'gap p05':>9}{'gap p50':>9}")
    for r in table:
        print(f"    {r['venue']:<11}{r['series']:<18}{r['week']:>3} {r['tier']:<8}"
              f"{(r['poll_cadence_s'] or 0):>7.1f}{r['markets']:>6}{r['rows_per_market']:>9}"
              f"{(r['row_gap_p05_s'] or 0):>9}{(r['row_gap_p50_s'] or 0):>9}")
    btab = book_resolution(pairs, quotes)
    print("\n  books: snapshot-scheduled, so the resolution is the gap to the previous snapshot:")
    for r in btab:
        print(f"    snapshot at T-{r['snapshot_min_before_kick']:>5} min   n={r['n']:>4}   bracket p50 {r['bracket_p50_min']:>7} min")
    res["resolution"] = {"poll_cadence": {f"{v}:{t}": g for (v, t), (g, _n) in cadence.items()},
                         "polled": table, "books": btab}

    print("\n" + "=" * 78 + "\n2. KALSHI vs POLYMARKET - both polled\n" + "=" * 78)
    res["polled"] = {}
    for theta in THETAS:
        eps, notes = [], Counter()
        for o in pairs.values():
            if "kalshi" in o["venues"] and "polymarket" in o["venues"]:
                e, n = polled_episodes(o, quotes, depth, theta)
                eps += e
                notes.update(n)
        block = summarise_polled(eps, theta, notes, verbose=(theta == THETA))
        res["polled"][str(theta)] = block

    print("\n" + "=" * 78 + "\n3. KALSHI vs THE BOOKS - bracketed by snapshots\n" + "=" * 78)
    res["bracketed"] = {}
    for theta in THETAS:
        eps, notes = [], Counter()
        for o in pairs.values():
            if "kalshi" in o["venues"] and "book" in o["venues"]:
                e, n = bracket_episodes(o, quotes, theta)
                eps += e
                notes.update(n)
        res["bracketed"][str(theta)] = summarise_bracketed(eps, theta, notes, verbose=(theta == THETA))

    print("\n" + "=" * 78 + "\n4. MAPPING CHECK - claims whose venues cannot be the same claim\n" + "=" * 78)
    bad, checked = mapping_check(pairs, quotes)
    for vs in ("polymarket", "book"):
        g = sorted(x for v, x in checked if v == vs)
        if g:
            print(f"  vs {vs:<10} claims checked {len(g):>5}   |median gap| p50 {g[len(g) // 2]:.3f} "
                  f"p90 {g[int(0.9 * len(g))]:.3f} p99 {g[int(0.99 * len(g))]:.3f} max {g[-1]:.3f}")
        else:
            print(f"  vs {vs:<10} claims checked     0")
    res["mapping_checked"] = Counter(v for v, _ in checked)
    for r in bad:
        print(f"  {r['outcome_id']:<24} {r['key']:<52} vs {r['vs']:<10} gap {r['median_gap']:+.3f} n={r['n_instants']}")
    print(f"  {len(bad)} flagged")
    res["mapping_suspects"] = bad
    if json_out:
        Path(json_out).write_text(json.dumps(res, indent=1, default=str), encoding="utf-8")
        print(f"\nwrote {json_out}")
    return res


def _fmt_ci(b, scale=1.0, unit=""):
    if b["est"] is None:
        return "n/a"
    s = f"{b['est'] * scale:+.2f}{unit}"
    if b["lo"] is not None:
        s += f" [{b['lo'] * scale:+.2f}, {b['hi'] * scale:+.2f}]"
    s += f" n={b['n']} games={b['games']}"
    if not b.get("informative", False):
        s += " (<5 games: not read)"
    return s


def summarise_polled(eps, theta, notes, verbose):
    out = {"episodes": len(eps), "notes": dict(notes), "cells": []}
    print(f"\n  theta {theta:.2f}: {len(eps):,} episodes; set aside: "
          + ", ".join(f"{k} {v:,}" for k, v in sorted(notes.items())))
    splits = [("all", lambda e: True)]
    if verbose:
        splits += [(f"kind={k}", (lambda k: lambda e: e["kind"] == k)(k)) for k in sorted({e["kind"] for e in eps})]
        splits += [(f"tier={k}", (lambda k: lambda e: e["tier"] == k)(k)) for k in ("cold", "game", "hot", "live")]
        splits += [(f"kick={k}", (lambda k: lambda e: e["kick_bucket"] == k)(k))
                   for k in (">24h", "6-24h", "1-6h", "0-1h", "in-game", "post-game")]
        splits += [(f"leader depth={k}", (lambda k: lambda e: e["depth"] == k)(k)) for k in ("depth", "thin", "no depth read")]
        splits += [(f"join={k}", (lambda k: lambda e: e["joined"] == k)(k)) for k in ("store", "folded")]
        splits += [("bucket=real disagreement", lambda e: e["bucket"] == "real disagreement")]
        print("  cross_venue.classify at the first mover's instant: "
              + ", ".join(f"{k} {v}" for k, v in Counter(e["bucket"] for e in eps).most_common()))
    alone = [e for e in eps if e["other_drifted"] is not None]
    out["alone_other_drifted"] = {"n": len(alone), "drifted": sum(e["other_drifted"] for e in alone)}
    print(f"  of {len(alone)} 'alone' moves with the other venue quoted at both ends, the other venue's mid "
          f"drifted >= {FOLLOW * theta:.3f} the same way within {WINDOW:.0f}s in "
          f"{out['alone_other_drifted']['drifted']} (by small steps or on a wide book)")
    for name, f in splits:
        sub = [e for e in eps if f(e)]
        if not sub:
            print(f"    {name:<32} 0 episodes")
            out["cells"].append({"split": name, "n": 0})
            continue
        c = Counter(e["outcome"] for e in sub)
        cell = {"split": name, "n": len(sub), "counts": dict(c),
                "kalshi_first": boot(sub, share("kalshi first")),
                "polymarket_first": boot(sub, share("polymarket first")),
                "tie": boot(sub, share("tie within resolution")),
                "median_lead_kalshi_s": boot(sub, median_lead),
                "tiers": dict(Counter(e["tier"] for e in sub))}
        out["cells"].append(cell)
        print(f"    {name:<32} n={len(sub):>5}  " + ", ".join(f"{k} {v}" for k, v in c.most_common()))
        print(f"      kalshi first {_fmt_ci(cell['kalshi_first'], 100, '%')}")
        print(f"      poly first   {_fmt_ci(cell['polymarket_first'], 100, '%')}")
        print(f"      tie          {_fmt_ci(cell['tie'], 100, '%')}")
        print(f"      median lead (kalshi +) {_fmt_ci(cell['median_lead_kalshi_s'], 1, 's')}   tiers {cell['tiers']}")
    return out


def summarise_bracketed(eps, theta, notes, verbose):
    out = {"episodes": len(eps), "notes": dict(notes), "cells": []}
    print(f"\n  theta {theta:.2f}: {len(eps):,} episodes; " + ", ".join(f"{k} {v:,}" for k, v in sorted(notes.items())))
    splits = [("all", lambda e: True)]
    if verbose:
        splits += [(f"stat={k}", (lambda k: lambda e: e["stat"] == k)(k)) for k in sorted({e["stat"] for e in eps})]
        splits += [(f"kick={k}", (lambda k: lambda e: e["kick_bucket"] == k)(k))
                   for k in (">24h", "6-24h", "1-6h", "0-1h", "in-game")]
        splits += [("comparable only", lambda e: "not comparable" not in e["where"])]
    for name, f in splits:
        sub = [e for e in eps if f(e)]
        c = Counter(e["where"] for e in sub)
        cell = {"split": name, "n": len(sub), "counts": dict(c),
                "bracket_p50_min": None if not sub else round(statistics.median(e["bracket_s"] for e in sub) / 60, 1),
                "kalshi_first": boot(sub, lambda es: (sum(e["first"] == "kalshi" for e in es) / len(es)) if es else None),
                "book_first": boot(sub, lambda es: (sum(e["first"] == "book" for e in es) / len(es)) if es else None),
                "same_bracket": boot(sub, lambda es: (sum(e["where"].startswith("same") for e in es) / len(es)) if es else None)}
        out["cells"].append(cell)
        print(f"    {name:<24} n={len(sub):>5} bracket p50 {cell['bracket_p50_min']} min   "
              + ", ".join(f"{k} {v}" for k, v in c.most_common()))
        if sub:
            print(f"      kalshi first (incl. alone) {_fmt_ci(cell['kalshi_first'], 100, '%')}")
            print(f"      book first (incl. alone)   {_fmt_ci(cell['book_first'], 100, '%')}")
            print(f"      same bracket               {_fmt_ci(cell['same_bracket'], 100, '%')}")
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--extract")
    ap.add_argument("--cache")
    ap.add_argument("--json")
    a = ap.parse_args()
    if a.extract:
        extract(a.extract)
    if a.cache:
        report(a.cache, a.json)


if __name__ == "__main__":
    main()
