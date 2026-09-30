"""c-29 - closing line value on every published lean. Pre-registered at
docs/C29-clv-every-week-preregistration.md, committed before this file.

    python -m research.clv_record --board D:/calibrated-sports/data/board_export
    python -m research.clv_record --board ... --dest D:/scratch/c29/web   # + record/nfl/clv.json

READ-ONLY. `market_log.db` is opened `mode=ro`; the Board tree is read, never
written. `--dest` must be a scratch tree: nothing here publishes.

WHAT IS MEASURED. For each lean the Board ledger published, the price its
benchmark books asked for the lean side at the lean's line when it was published,
against the price the SAME book asked for the SAME side at the SAME line at its
last Odds API capture strictly before kickoff:

    CLV_touch(b) = touch_close(b, side) - touch_entry(b, side)   <- the headline
    CLV_mid(b)   = fair_close(b, side)  - fair_entry(b, side)    value no bet could take
    CLV_held(b)  = fair_close(b, side)  - touch_entry(b, side)   a held bet, if the close is fair

averaged over the books on both ends, equal weight, then over leans. Signed, in
percentage points; positive means the lean got a better price than the close.

WHAT IT IS NOT. CLV measures price, not whether the lean was right. On a market
this project has already shown to be efficient a null is the expected result.

EVERY LEAN IS ACCOUNTED FOR. A lean is `scored` or carries exactly one named
exclusion, and `partition()` asserts the counts sum to the population. Dropping
the calls that lack a price is the fastest way to fake a good CLV.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
import os
import random
import statistics
import sys
import time
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
from core import board as B

SPORT = "nfl"
KEY = f"record/{SPORT}/clv.json"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESULT = os.path.join(ROOT, "research", "results", "clv_record.json")
PREREG = "docs/C29-clv-every-week-preregistration.md"

BOOT = 10000
SEED = 20260930
MIN_GAMES = 5                   # brief 020: an interval on < 5 games is not read
MDE_Z = 2.80                    # 80% power, two-sided alpha 0.05

# The a-58 analog for a book price. Forward capture only, and no older than the
# widest spacing the forward schedule uses inside the last half-day (T-12h ->
# T-6h), so an admitted quote was the latest scheduled observation.
ENTRY_SOURCE = "live"
ENTRY_MAX_AGE = 6 * 3600.0

ARMS = ("touch", "mid", "held")

# Exclusion reasons, in the order they are tested. Published verbatim.
X_NOT_PRE = "not published before kickoff (a-57 rule)"
X_PENDING = "pending: game not yet kicked off"
X_VOID = "void (the bet is refunded; CLV has no money meaning)"
X_NO_ROW = "no read-file row for the lean"
X_NO_EVENT = "no Odds API event for the game"
X_PRUNED = "quotes no longer in the store (retention pruned the live rows)"
X_STALE = "entry refused: every entry book stale (> 6 h) or non-live"
X_NO_CLOSE = "no close at the lean's line (line moved or player pulled)"
X_DISJOINT = "no book on both ends (entry and close books disjoint)"
EXCLUSIONS = (X_NOT_PRE, X_PENDING, X_VOID, X_NO_ROW, X_NO_EVENT, X_PRUNED, X_STALE,
              X_NO_CLOSE, X_DISJOINT)

LEAD = ((0, 6, "<6h"), (6, 24, "6-24h"), (24, 48, "24-48h"), (48, 1e9, ">=48h"))
AGE = ((0, 1, "<=1h"), (1, 6, "1-6h"), (6, 1e9, ">6h (refused)"))


class CLVError(AssertionError):
    """The CLV file would say something its sources do not support."""


# =============================================================================
# pure pieces
# =============================================================================

def iso(ts):
    return dt.datetime.fromtimestamp(ts, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(s):
    return dt.datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()


def pre_kickoff(pub):
    """a-57's rule (core.record.pre_kickoff on branch a-57-record-three-tiers),
    re-stated because a-57 is not on main: None if both `read_at` and `event_at`
    are strictly before `kickoff_ts`, else the reason."""
    try:
        kick = float(pub["kickoff_ts"])
    except (TypeError, ValueError, KeyError):
        return "no kickoff_ts"
    for field in ("read_at", "event_at"):
        v = pub.get(field)
        if v is None:
            return f"no {field}"
        try:
            t = parse_iso(v)
        except ValueError:
            return f"unparseable {field} {v!r}"
        if t >= kick:
            return f"{field} {v} is not before kickoff"
    return None


def other(side):
    return {"over": "under", "under": "over"}[side]


def touch(q, side):
    """Vig-inclusive implied probability of `side` in one book's quote."""
    return B.american_to_prob(q.get(side)) if q else None


def fair(q, side):
    """Multiplicative de-vig of `side` against the other side, same book, same line."""
    if not q or q.get(side) is None or q.get(other(side)) is None:
        return None
    p = B.devig_mult(q["over"], q["under"])
    return None if p is None else (p if side == "over" else 1.0 - p)


def lean_clv(entry, close, side):
    """-> {arm: mean CLV in pp over the books on both ends, or None}, plus the
    books used. `entry` and `close` are {book: {"over", "under"}} at the lean's
    line. A book counts for an arm only if that arm is defined at both ends."""
    both = sorted(b for b in entry if b in close
                  and touch(entry[b], side) is not None and touch(close[b], side) is not None)
    out = {"books": both}
    per = {a: [] for a in ARMS}
    for b in both:
        te, tc = touch(entry[b], side), touch(close[b], side)
        fe, fc = fair(entry[b], side), fair(close[b], side)
        per["touch"].append(100 * (tc - te))
        if fe is not None and fc is not None:
            per["mid"].append(100 * (fc - fe))
        if fc is not None:
            per["held"].append(100 * (fc - te))
    for a in ARMS:
        out[a] = statistics.fmean(per[a]) if per[a] else None
    return out


def over_drift(entry, close, books):
    """Mean change in the de-vigged OVER probability, pp, over `books` two-way at
    both ends - the market's drift, independent of which side the lean took."""
    d = [100 * (fair(close[b], "over") - fair(entry[b], "over")) for b in books
         if fair(close[b], "over") is not None and fair(entry[b], "over") is not None]
    return statistics.fmean(d) if d else None


def by_game(rows):
    g = defaultdict(list)
    for r in rows:
        g[r["game_id"]].append(r)
    return g


def game_bootstrap(rows, field, draws=BOOT, seed=SEED):
    """Mean of `field` over leans, percentile bootstrap resampling GAMES. -> dict
    with est, lo, hi, se, mde, n, n_games, readable. Never a per-lean interval."""
    rows = [r for r in rows if r.get(field) is not None]
    out = {"n": len(rows), "n_games": len(by_game(rows)), "est": None, "lo": None,
           "hi": None, "se": None, "mde": None, "readable": False}
    if not rows:
        return out
    out["est"] = statistics.fmean(r[field] for r in rows)
    games = by_game(rows)
    keys = sorted(games)
    if len(keys) < 2:
        return out
    sums = {k: (sum(r[field] for r in games[k]), len(games[k])) for k in keys}
    rng = random.Random(seed)
    means = []
    for _ in range(draws):
        s = n = 0
        for _k in range(len(keys)):
            a, b = sums[keys[rng.randrange(len(keys))]]
            s += a
            n += b
        means.append(s / n)
    means.sort()
    out["lo"] = means[int(0.025 * (draws - 1))]
    out["hi"] = means[int(0.975 * (draws - 1))]
    out["se"] = statistics.pstdev(means)
    out["mde"] = MDE_Z * out["se"]
    out["readable"] = len(keys) >= MIN_GAMES
    return out


def verdict(iv):
    """Wording from the interval, able to produce every answer."""
    if iv["est"] is None:
        return "no scored leans"
    if not iv["readable"] or iv["lo"] is None:
        return f"no verdict: {iv['n_games']} game(s), fewer than {MIN_GAMES}"
    if iv["lo"] > 0:
        return "the leans beat the close"
    if iv["hi"] < 0:
        return "the close beat the leans"
    return (f"no measurable CLV; an effect smaller than {iv['mde']:.2f}pp "
            "could not have been seen")


def decompose(rows):
    """mean(sgn x drift) = cov(sgn, drift) + mean(sgn) x mean(drift), population
    moments, on the de-vigged over drift. The second term is a directional lean
    times market-wide drift and would read as CLV from nothing."""
    xs = [(1.0 if r["side"] == "over" else -1.0, r["over_drift"]) for r in rows
          if r.get("over_drift") is not None]
    if not xs:
        return None
    ms = statistics.fmean(s for s, _ in xs)
    md = statistics.fmean(d for _, d in xs)
    total = statistics.fmean(s * d for s, d in xs)
    return {"n": len(xs), "mean_sgn_x_drift": total, "cov": total - ms * md,
            "mean_sgn": ms, "mean_drift_over_pp": md, "mechanical": ms * md}


def band_of(x, bands):
    for lo, hi, name in bands:
        if lo <= x < hi:
            return name
    return None


STRATA = {
    "week": lambda r: f"{r['season']}-wk{r['week']:02d}",
    "market": lambda r: r["market"],
    "side": lambda r: r["side"],
    "lead": lambda r: band_of(r["lead_h"], LEAD),
    "entry_books": lambda r: str(r["entry_books"]),
    "books_both_ends": lambda r: str(len(r["books"])),
    "entry_age": lambda r: band_of(r["entry_age_h"], AGE),
    "close_lag": lambda r: "<=5min" if r["close_lag_min"] <= 5 else ">5min",
    "band": lambda r: r["band"] or "none",
}


def strata(rows, arms=("touch", "mid")):
    out, tests = {}, 0
    for name, key in STRATA.items():
        cells = defaultdict(list)
        for r in rows:
            cells[key(r)].append(r)
        out[name] = {}
        for cell in sorted(cells, key=str):
            out[name][cell] = {}
            for a in arms:
                iv = game_bootstrap(cells[cell], a, draws=2000)
                tests += 1
                out[name][cell][a] = dict(iv, verdict=verdict(iv))
    return out, tests


def partition(population, scored, excluded):
    n_x = sum(len(v) for v in excluded.values())
    if len(scored) + n_x != len(population):
        raise CLVError(f"{len(population)} leans != {len(scored)} scored + {n_x} excluded")
    ids = [r["lean_id"] for r in scored] + [i for v in excluded.values() for i in v]
    if len(set(ids)) != len(ids):
        raise CLVError("a lean is both scored and excluded, or excluded twice")


# =============================================================================
# I/O - read only
# =============================================================================

def ro(path):
    return __import__("sqlite3").connect(f"file:{path}?mode=ro", uri=True)


def read_ledger(board):
    import polars as pl
    path = os.path.join(board, "board", SPORT, "ledger.parquet")
    with open(path, "rb") as f:
        digest = hashlib.sha256(f.read()).hexdigest()
    rows = pl.read_parquet(path).to_dicts()
    for r in rows:
        for k, v in r.items():
            if isinstance(v, float) and v != v:
                r[k] = None
    return rows, {"key": f"board/{SPORT}/ledger.parquet", "rows": len(rows), "sha256": digest}


def book_captures(con, event_id, book, upto, strict):
    """Distinct capture instants of one book's prop quotes for one event, live only."""
    op = "<" if strict else "<="
    return sorted(t for (t,) in con.execute(
        f"SELECT DISTINCT ts FROM quotes WHERE venue=? AND event_id=? AND ts {op} ? "
        "AND market_type='prop' AND source=?", (f"oddsapi:{book}", event_id, upto, ENTRY_SOURCE)))


def quotes_at(con, event_id, book, ts, mkey, name, line):
    """{"over": american, "under": american} for one claim at one capture, or None."""
    q = {}
    for mid, side, price in con.execute(
            "SELECT market_id, side, last FROM quotes WHERE venue=? AND event_id=? AND ts=? "
            "AND market_type='prop' AND source=? AND line=?",
            (f"oddsapi:{book}", event_id, ts, ENTRY_SOURCE, float(line))):
        parts = (mid or "").split("|")
        if len(parts) != 4 or parts[1] != mkey or parts[2] != name:
            continue
        sd = (side or "").lower()
        if sd in ("over", "under"):
            q[sd] = price
    return q or None


def claim_capture(con, event_id, book, upto, mkey, name, line):
    """The latest live capture at or before `upto` that quotes this claim at this
    line - the instant the published entry price was actually observed."""
    for ts, mid in con.execute(
            "SELECT ts, market_id FROM quotes WHERE venue=? AND event_id=? AND ts <= ? "
            "AND market_type='prop' AND source=? AND line=? ORDER BY ts DESC",
            (f"oddsapi:{book}", event_id, upto, ENTRY_SOURCE, float(line))):
        parts = (mid or "").split("|")
        if len(parts) == 4 and parts[1] == mkey and parts[2] == name:
            return ts
    return None


def claim_listed(con, event_id, book, ts, mkey, name):
    """Does this book's capture at `ts` list the claim at ANY line?"""
    for (mid,) in con.execute(
            "SELECT market_id FROM quotes WHERE venue=? AND event_id=? AND ts=? "
            "AND market_type='prop' AND source=?", (f"oddsapi:{book}", event_id, ts, ENTRY_SOURCE)):
        parts = (mid or "").split("|")
        if len(parts) == 4 and parts[1] == mkey and parts[2] == name:
            return True
    return False


def load(board, db, now_ts):
    """-> (population, scored rows, {reason: [lean ids]}, census, sources)."""
    from jobs import board_read as BR
    ledger, src = read_ledger(board)
    con = ro(db)
    inv = {v: k for k, v in config.BOARD_MARKETS.items()}
    books = config.BOARD_BENCH_BOOKS
    pubs = [e for e in ledger if e["event"] == "published"]
    term = {e["lean_id"]: e for e in ledger if e["event"] in ("graded", "void")}
    excluded = {x: [] for x in EXCLUSIONS}
    # printed every run, zero included: a count that reads 0 most weeks is what
    # makes the week it reads 1 visible
    census = Counter({k: 0 for k in (
        "entry price differs from the store at its capture",
        "entry book with no live capture at or before the read",
        "entry book-quotes refused as stale",
        "read row lean differs from ledger side",
        "stale-excluded leans with an unrestricted touch CLV")})
    scored, reads, events, caps = [], {}, {}, {}
    unres_rows = []                 # stale-excluded leans, for the sensitivity row only
    last_capture = con.execute(
        "SELECT MAX(ts) FROM quotes WHERE venue LIKE 'oddsapi:%' AND source=? "
        "AND ts > ?", (ENTRY_SOURCE, now_ts - 30 * 86400)).fetchone()[0]
    for p in sorted(pubs, key=lambda e: (e["season"], e["week"], e["kickoff_ts"], e["lean_id"])):
        lid, s, w = p["lean_id"], int(p["season"]), int(p["week"])
        if pre_kickoff(p) is not None:
            excluded[X_NOT_PRE].append(lid); continue
        kick = float(p["kickoff_ts"])
        # Pending means the close cannot exist YET: the game is in the future, or
        # after the newest Odds API capture in the store. An empty store is not
        # pending - that is the pruned case below.
        if kick > now_ts or (last_capture is not None and kick > last_capture):
            excluded[X_PENDING].append(lid); continue
        if (term.get(lid) or {}).get("event") == "void":
            excluded[X_VOID].append(lid); continue
        rk = (s, w, p["read_at"])
        if rk not in reads:
            path = os.path.join(board, BR.read_key(s, w, p["read_at"]))
            reads[rk] = json.load(open(path, encoding="utf-8")) if os.path.exists(path) else None
        rf = reads[rk]
        rows = [r for r in (rf or {}).get("rows", []) if r["row_id"] == p["row_id"]]
        if len(rows) != 1:
            excluded[X_NO_ROW].append(lid); continue
        row = rows[0]
        if row.get("lean") not in (None, p["side"]):
            census["read row lean differs from ledger side"] += 1
        if (s, w) not in events:
            ev = BR.oddsapi_events(con, BR.week_games(con, s, w))
            events[(s, w)] = {g["game_id"]: e for e, g in ev.items()}
        eid = events[(s, w)].get(p["game_id"])
        if eid is None:
            excluded[X_NO_EVENT].append(lid); continue
        side, line, mkey, name = p["side"], float(p["line"]), inv[p["market"]], row["name"]
        read_ts = parse_iso(p["read_at"])

        # ---- entry: the published per-book prices, and the capture they came from
        entry, ages, uncaptured = {}, {}, 0
        for b in row.get("books", []):
            if b["book"] not in books or b.get(side) is None:
                continue
            cap = claim_capture(con, eid, b["book"], read_ts, mkey, name, line)
            if cap is None:
                census["entry book with no live capture at or before the read"] += 1
                uncaptured += 1
                continue
            live = quotes_at(con, eid, b["book"], cap, mkey, name, line)
            if live is None or live.get(side) != b.get(side):
                census["entry price differs from the store at its capture"] += 1
            age = read_ts - cap
            ages[b["book"]] = age
            if age > ENTRY_MAX_AGE:
                census["entry book-quotes refused as stale"] += 1
                continue
            entry[b["book"]] = {"over": b.get("over"), "under": b.get("under")}
        n_entry_quoted = sum(1 for b in row.get("books", []) if b.get(side) is not None
                             and b["book"] in books)

        # ---- close: each book's latest capture strictly before kickoff
        close, lags, close_captured = {}, {}, 0
        for b in books:
            ck = (eid, b, kick, True)
            if ck not in caps:
                caps[ck] = book_captures(con, eid, b, kick, strict=True)
            if not caps[ck]:
                continue
            close_captured += 1
            cap = caps[ck][-1]
            # THE ASSERTION. A close at or after kickoff is a live in-game price.
            if not cap < kick:
                raise CLVError(f"{lid}: close capture {cap} is not before kickoff {kick}")
            if not claim_listed(con, eid, b, cap, mkey, name):
                continue                       # the book has pulled the player
            q = quotes_at(con, eid, b, cap, mkey, name, line)
            if q and q.get(side) is not None:
                close[b], lags[b] = q, (kick - cap) / 60.0

        # an unrestricted entry, stale books admitted, for the sensitivity row
        entry_all = {b["book"]: {"over": b.get("over"), "under": b.get("under")}
                     for b in row.get("books", []) if b["book"] in books and b.get(side) is not None}
        unres = lean_clv(entry_all, close, side)["touch"] if close else None

        # Retention (invariant 8) deletes live rows 14 days after ingestion. A lean
        # whose quotes are GONE is not a stale lean and not a pulled player, and
        # counting it as either would misreport why it has no CLV.
        if (n_entry_quoted and uncaptured == n_entry_quoted) or close_captured == 0:
            excluded[X_PRUNED].append(lid); continue
        if not entry:
            excluded[X_STALE].append(lid)
            census["stale-excluded leans with an unrestricted touch CLV"] += unres is not None
            unres_rows.append({"lean_id": lid, "game_id": p["game_id"],
                               "touch_unrestricted": unres})
            continue
        if not close:
            excluded[X_NO_CLOSE].append(lid); continue
        c = lean_clv(entry, close, side)
        if not c["books"]:
            excluded[X_DISJOINT].append(lid); continue
        both = c["books"]
        r = {"lean_id": lid, "season": s, "week": w, "game_id": p["game_id"],
             "gsis_id": p["gsis_id"], "market": p["market"], "line": line, "side": side,
             "band": p.get("band"), "read_at": p["read_at"], "kickoff": iso(kick),
             "price_ledgered": p.get("price"),
             "books": both, "entry_books": n_entry_quoted,
             "lead_h": (kick - read_ts) / 3600.0,
             "entry_age_h": max(ages[b] for b in both) / 3600.0,
             "close_lag_min": max(lags[b] for b in both),
             "touch": c["touch"], "mid": c["mid"], "held": c["held"],
             "touch_unrestricted": unres,
             "over_drift": over_drift(entry, close, both),
             "result": (term.get(lid) or {}).get("result")}
        scored.append(r)
    partition(pubs, scored, excluded)
    return pubs, scored, excluded, census, src, unres_rows, last_capture


# =============================================================================
# build
# =============================================================================

def rnd(x, nd=4):
    return None if x is None else round(float(x), nd)


def iv_out(iv):
    return {k: (rnd(v) if isinstance(v, float) else v) for k, v in iv.items()}


def build(board, db, now_ts):
    pubs, scored, excluded, census, src, unres_rows, last_cap = load(board, db, now_ts)
    arms = {a: game_bootstrap(scored, a) for a in ARMS}
    head = arms["touch"]
    # sensitivity: the stale entries admitted, same rule otherwise
    sens_rows = [{"game_id": r["game_id"], "touch_unrestricted": r["touch_unrestricted"]}
                 for r in scored] + unres_rows
    sens = game_bootstrap(sens_rows, "touch_unrestricted")
    st, tests = strata(scored)
    dec = decompose(scored)
    graded = [r for r in scored if r["result"] in ("cleared", "missed")]
    weeks_scored = sorted({(r["season"], r["week"]) for r in scored})
    statement = (f"{len(scored)} of {len(pubs)} published leans have a price at entry and a "
                 f"close on the same book, line and side; touch CLV "
                 f"{head['est']:+.2f}pp [{head['lo']:+.2f}, {head['hi']:+.2f}] over "
                 f"{head['n_games']} games: {verdict(head)}."
                 if head["est"] is not None and head["lo"] is not None else
                 f"{len(scored)} of {len(pubs)} published leans scored; no interval.")
    body = {
        "tier": "published",
        "supports": "the prices our calls got, against the close - not whether they were right",
        "beside": ("record/nfl/published.json carries the win rate. CLV sits beside it and "
                   "does not replace it: CLV is price, the record is outcome."),
        "preregistration": PREREG,
        "source": {
            "entry": {"ledger": src, "read_files": f"board/{SPORT}/{{season}}/wk{{week}}/read-{{iso}}.json",
                      "rule": ("the lean-side American price each benchmark book showed at the "
                               "lean's line in the read the ledger row names")},
            "close": {"store": "market_log.db quotes, venue oddsapi:<book>, source 'live', mode=ro",
                      "rule": ("each benchmark book's quote at the lean's line at its latest Odds "
                               "API capture strictly before kickoff; asserted capture_ts < kickoff_ts"),
                      "last_capture_in_store": iso(last_cap) if last_cap else None},
            "books": list(config.BOARD_BENCH_BOOKS),
            "blended_with": None,
        },
        "entry_rule": (f"a live Odds API capture (source '{ENTRY_SOURCE}') no more than "
                       f"{ENTRY_MAX_AGE / 3600:g}h before the read; an older book-quote is refused, "
                       "never re-priced"),
        "definitions": {
            "touch": "vig-inclusive implied probability of the lean side, close minus entry, same book",
            "mid": "de-vigged (multiplicative) lean-side probability, close minus entry, same book",
            "held": "de-vigged close minus vig-inclusive entry: a held bet's value if the close is fair",
            "unit": "percentage points, signed, positive = better price than the close",
            "aggregation": "mean over books on both ends within a lean, then mean over leans",
        },
        "n_published": len(pubs), "n_scored": len(scored),
        "n_scored_graded": len(graded),
        "weeks_scored": [{"season": s, "week": w} for s, w in weeks_scored],
        "exclusions": [{"reason": x, "n": len(excluded[x]), "lean_ids": sorted(excluded[x])}
                       for x in EXCLUSIONS],
        "census": dict(census),
        "headline": dict(iv_out(head), arm="touch", verdict=verdict(head), statement=statement,
                         interval=("percentile bootstrap resampling games, "
                                   f"{BOOT} draws, seed {SEED}"),
                         power=(f"MDE at 80% power = {MDE_Z} x bootstrap SE"),
                         informative=head["readable"]),
        "arms": {a: dict(iv_out(arms[a]), verdict=verdict(arms[a])) for a in ARMS},
        "sensitivity_stale_admitted": dict(iv_out(sens), verdict=verdict(sens),
                                           why="the entry rule's effect, shown rather than absorbed"),
        "mechanical_term": {k: rnd(v) if isinstance(v, float) else v for k, v in (dec or {}).items()},
        "strata": {k: {c: {a: iv_out(v) | {"verdict": v["verdict"]} for a, v in arms_.items()}
                       for c, arms_ in cells.items()} for k, cells in st.items()},
        "strata_tests": tests,
        "strata_note": (f"{tests} stratum intervals; none is a finding on its own - an interval on "
                        f"fewer than {MIN_GAMES} games is not read, and at this count several "
                        "would exclude zero by chance"),
        "gaming": [
            "Entering earlier gives prices more time to move either way: more CLV and more risk. "
            "See strata.lead.",
            "Entering on a stale quote gives CLV no order could have taken. Refused above the "
            "entry age limit; see sensitivity_stale_admitted and strata.entry_age.",
            "A mid-based CLV counts the vig as value. The touch leads; mid is beneath it.",
            "Dropping calls with no close flatters the mean. Every exclusion is counted above.",
        ],
        "leans": [{k: (rnd(v) if isinstance(v, float) else v) for k, v in r.items()}
                  for r in scored],
    }
    return body


def envelope(body, now_ts):
    return {"schema_version": 2, "generated_at": iso(now_ts), "kind": "record.clv",
            "sport": SPORT, **body}


def report(body):
    h = body["headline"]
    print(f"published {body['n_published']}  scored {body['n_scored']}  "
          f"(graded among scored {body['n_scored_graded']})")
    for x in body["exclusions"]:
        print(f"  excluded  {x['n']:>4}  {x['reason']}")
    for k, v in sorted(body["census"].items()):
        print(f"  census    {v:>4}  {k}")

    def f(iv):
        if iv["est"] is None:
            return "n/a"
        if iv["lo"] is None:
            return f"{iv['est']:+.2f}pp (n {iv['n']}, games {iv['n_games']})"
        return (f"{iv['est']:+.2f}pp [{iv['lo']:+.2f}, {iv['hi']:+.2f}] se {iv['se']:.2f} "
                f"MDE {iv['mde']:.2f}  n {iv['n']} games {iv['n_games']}")
    print(f"\nHEADLINE touch  {f(h)}\n  {h['verdict']}")
    for a in ("mid", "held"):
        print(f"  {a:<6} {f(body['arms'][a])}   {body['arms'][a]['verdict']}")
    print(f"  touch, stale entries admitted  {f(body['sensitivity_stale_admitted'])}")
    print(f"  mechanical term {body['mechanical_term']}")
    for name, cells in body["strata"].items():
        print(f"\n  by {name}")
        for cell, arms in cells.items():
            print(f"    {cell:<16} touch {f(arms['touch'])}   | mid {f(arms['mid'])}")
    print(f"\n  {body['strata_note']}")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--board", default=getattr(config, "BOARD_EXPORT_DIR", None),
                    help="the Board tree (holds board/nfl/ledger.parquet)")
    ap.add_argument("--db", default=config.DB_PATH, help="market_log.db, opened mode=ro")
    ap.add_argument("--dest", help="scratch tree to write record/nfl/clv.json into")
    ap.add_argument("--out", default=RESULT)
    ap.add_argument("--now", type=float)
    a = ap.parse_args(argv)
    if not a.board:
        ap.error("--board is required (BOARD_EXPORT_DIR unset)")
    now = a.now if a.now is not None else time.time()
    body = build(a.board, a.db, now)
    if body["n_published"] == 0:
        print("no published leans read - refusing", file=sys.stderr)
        return 2
    report(body)
    env = envelope(body, now)
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    with open(a.out, "w", encoding="utf-8", newline="\n") as f:
        json.dump(env, f, indent=1, sort_keys=False)
        f.write("\n")
    print(f"\nwrote {a.out}")
    if a.dest:
        path = os.path.join(a.dest, *KEY.split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            json.dump(env, f, indent=1)
            f.write("\n")
        print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
