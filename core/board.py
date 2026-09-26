"""The Board (audit 5.3 / 5.5, unit a-26): the definitions, as pure functions.

No I/O here. `jobs/board_read.py` reads the store and the previous board, and
everything that decides what a row SAYS is in this file, so it can be tested
without a database and read at a glance.

THE DEFINITIONS, each one a function below:

    main line     the listed threshold whose median de-vigged over probability
                  across the benchmark books is closest to 0.5 AT THIS READ.
                  `is_main` is a property of the read, not of the market: f-17
                  measured the main line moving between first read and close on
                  15.5% of player-markets (29.5% of rush attempts). A row carries
                  `main_line_changed_since_open` so a moved line is shown, not
                  smoothed over.
    market prob   median across DraftKings / FanDuel / BetMGM of each book's
                  multiplicatively de-vigged over probability, two-way quotes
                  only; `mkt_books` on every row. Flagged `longshot` outside
                  [BOARD_LONGSHOT_P, 1 - BOARD_LONGSHOT_P]. Kalshi is an
                  exchange: its mid is shown as-is and never de-vigged.
    gap           model minus market, probability points, signed.
    lean          over at gap >= +T, under at gap <= -T, else none.
    band          |gap| in [4,6), [6,8), [8,inf) - "leans this size".

ROW IDENTITY. `row_id` is the contract's `{season}-{week}-{away}-{home}:{gsis}:
{market}:{line}`, so it changes when the main line moves. The CLAIM - one player,
one market, one game - is `claim_id`; a read has one MAIN row per claim, and a
read's rows are unique by `row_id`. A LEAN is keyed on (claim, line, side) in the
ledger, so a lean published at 4.5 is still graded at 4.5 after the main line
moves to 5.5: it is never looked up by the line the board shows now (f-17's
warning, answered by carrying the line on the lean rather than by dropping it
from the key). And it stays ON THE BOARD (a-34): the row that carried it is kept,
frozen as priced, beside the new main row - see `merge_read`. Before a-34 the
ledger graded it and every later read had lost it.

THE LEDGER is append-only EVENTS, not mutable rows: a `published` event when a
lean first appears at a read, and exactly one terminal event later - `graded`
(cleared / missed / push) or `void` (with its reason). "Settled rows are never
rewritten" is then a property of the file, checked by `assert_append_only`.

THE INVARIANT (brief addendum 2, replacing "published = graded + upcoming"):
every published lean is in exactly one of {graded, upcoming, live, void}; the
four are disjoint and exhaust the ledger. `lean_states` computes it and raises if
a lean falls into none or two. `leans_on_board` asserts the same partition on a
READ's rows (a-34): every lean the ledger published for the week is on the read
as exactly one row, in its ledger state - so the page need not reconcile.
"""
from __future__ import annotations

import hashlib
import math
import statistics
from datetime import datetime, timezone

import config
from core import settlement
from core.stats import wilson

UPCOMING, LIVE, CLEARED, MISSED, PUSH, VOID = (
    "upcoming", "live", "cleared", "missed", "push", "void")
STATUSES = (UPCOMING, LIVE, CLEARED, MISSED, PUSH, VOID)
SETTLED = (CLEARED, MISSED, PUSH, VOID)

# Why a row or a lean is VOID. A void carries one of these, always, so a reader
# can tell a voided lean from a quietly dropped one.
VOID_MARKET_PULLED = "market_pulled"
VOID_INACTIVE = "inactive"
VOID_NO_SNAP = "no_snap"
VOID_REASONS = (VOID_MARKET_PULLED, VOID_INACTIVE, VOID_NO_SNAP)

# Ledger lean states - the four-way partition.
S_GRADED, S_UPCOMING, S_LIVE, S_VOID = "graded", "upcoming", "live", "void"

# Streak rules, fixed in advance and evaluated in THIS order; the first that
# fires is the row's streak. Definitions are F11's (research/f11_streak_null.py),
# applied to the posted line rather than F11's constructed median line.
STREAK_RULES = ("L7", "L5", "AWAY5", "H2H1")


# ------------------------------------------------------------------ prices

def american_to_prob(odds):
    """Vig-inclusive implied probability of an American price."""
    if odds is None:
        return None
    o = float(odds)
    if o == 0 or (-100 < o < 100):
        return None                      # not a valid American price
    return (-o) / ((-o) + 100.0) if o < 0 else 100.0 / (o + 100.0)


def american_to_decimal(odds):
    """Decimal payout per unit staked, stake included."""
    o = float(odds)
    return 1.0 + (o / 100.0 if o > 0 else 100.0 / (-o))


def devig_mult(over_odds, under_odds):
    """Multiplicative de-vig of a two-way market -> P(over), or None.

    Biased at the extremes (the favourite-longshot pattern); rows are flagged
    rather than corrected, and Shin / power are for when there is settled data
    to choose between them.
    """
    po, pu = american_to_prob(over_odds), american_to_prob(under_odds)
    if po is None or pu is None or po + pu <= 0:
        return None
    return po / (po + pu)


def hold(over_odds, under_odds):
    po, pu = american_to_prob(over_odds), american_to_prob(under_odds)
    return None if po is None or pu is None else po + pu - 1.0


# ------------------------------------------------------------------ the ladder
# ladder = {line: {book: {"over": american, "under": american, "read_at": iso}}}

def book_probs(ladder, line, books=None):
    """{book: de-vigged P(over)} at one line, two-way quotes only."""
    books = config.BOARD_BENCH_BOOKS if books is None else books
    out = {}
    for book, q in (ladder.get(line) or {}).items():
        if book not in books:
            continue
        p = devig_mult(q.get("over"), q.get("under"))
        if p is not None:
            out[book] = p
    return out


def market_prob(ladder, line, books=None):
    """(median de-vigged P(over), book count) at one line; (None, 0) if no
    benchmark book quotes both sides."""
    ps = book_probs(ladder, line, books)
    if not ps:
        return None, 0
    return statistics.median(ps.values()), len(ps)


def main_line(ladder, books=None):
    """The listed threshold whose market P(over) is closest to 0.5, or None.

    Ties break toward MORE books, then the LOWER line - a fixed rule, so two
    runs over the same quotes cannot pick different rows.
    """
    best = None
    for line in ladder:
        p, n = market_prob(ladder, line, books)
        if p is None:
            continue
        key = (abs(p - 0.5), -n, line)
        if best is None or key < best[0]:
            best = (key, line)
    return None if best is None else best[1]


def is_longshot(p):
    lo = config.BOARD_LONGSHOT_P
    return p is not None and (p < lo or p > 1 - lo)


# ------------------------------------------------------------------ gap, lean, band

def gap_pp(model_p, mkt_p):
    if model_p is None or mkt_p is None:
        return None
    return round((model_p - mkt_p) * 100.0, 2)


def lean_threshold_at(read_at_iso, log=None):
    """T in force at a read: the latest log entry effective at or before it."""
    log = config.BOARD_LEAN_THRESHOLD_LOG if log is None else log
    t = None
    for eff, value, _why in log:
        if eff <= read_at_iso:
            t = value
    if t is None:
        raise ValueError(f"no lean threshold in force at {read_at_iso}; "
                         "the log's first entry must predate the first board")
    return t


def lean(gap, threshold):
    if gap is None:
        return None
    if gap >= threshold:
        return "over"
    if gap <= -threshold:
        return "under"
    return None


def band_for(gap, bands=None):
    """(lo, hi) of the |gap| band a lean falls in, or None below the first band.
    hi None means open-ended (8+)."""
    bands = config.BOARD_GAP_BANDS if bands is None else bands
    if gap is None:
        return None
    g = abs(gap)
    for lo, hi in bands:
        if g >= lo and (hi is None or g < hi):
            return (lo, hi)
    return None


def band_stats(records):
    """Historical base rate for one (market, band): records are dicts with
    `cleared` (bool) and `payout` (decimal odds at the price that stood, stake
    included). Pushes and voids are excluded before this is called.

    ROI interval: normal approximation on the per-bet return. It is labelled as
    such; a game-block bootstrap is the better interval and needs the game key,
    which `jobs/board_bands` carries and uses when it has it.
    """
    n = len(records)
    k = sum(1 for r in records if r["cleared"])
    lo, hi = wilson(k, n)
    rets = [(r["payout"] - 1.0) if r["cleared"] else -1.0 for r in records]
    roi = statistics.fmean(rets) if rets else None
    if n >= 2:
        se = statistics.stdev(rets) / math.sqrt(n)
        roi_ci = [roi - 1.96 * se, roi + 1.96 * se]
    else:
        roi_ci = None
    return {"n": n, "k": k, "cleared": (k / n) if n else None,
            "ci": [lo, hi] if n else None, "roi": roi, "roi_ci": roi_ci}


# ------------------------------------------------------------------ identity

def claim_id(game_id, gsis_id, market):
    return f"{game_id}:{gsis_id}:{market}"


def _fmt_line(line):
    return f"{float(line):g}"


def row_id(season, week, away, home, gsis_id, market, line):
    """The contract's row id: `2026-03-NYJ-DET:00-0036963:receptions:7.5`."""
    return f"{season}-{int(week):02d}-{away}-{home}:{gsis_id}:{market}:{_fmt_line(line)}"


def lean_id(claim, line, side):
    """A lean is one side of one line of one claim. Hash, so it is a stable key
    in a parquet file and a URL."""
    raw = f"{claim}|{_fmt_line(line)}|{side}"
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


# ------------------------------------------------------------------ streaks

def streak(history, line, next_away, next_opp):
    """The first fixed-order rule that fires against TODAY'S line, or None.

    history: the player's graded prior games, oldest first, each
             {"value": x, "away": bool, "opp": "DAL"}. A value exactly on the
             line is a push and is not graded (F11's construction).
    """
    graded = [dict(g, hit=g["value"] > line) for g in history
              if g.get("value") is not None and g["value"] != line]
    last7 = graded[-7:]
    if len(last7) == 7 and sum(g["hit"] for g in last7) >= 6:
        k = sum(g["hit"] for g in last7)
        return {"rule": "L7", "k": k, "n": 7, "display_rate": k / 7}
    last5 = graded[-5:]
    if len(last5) == 5 and all(g["hit"] for g in last5):
        return {"rule": "L5", "k": 5, "n": 5, "display_rate": 1.0}
    if next_away:
        away5 = [g for g in graded if g.get("away")][-5:]
        if len(away5) == 5 and all(g["hit"] for g in away5):
            return {"rule": "AWAY5", "k": 5, "n": 5, "display_rate": 1.0}
    h2h = [g for g in graded if next_opp and g.get("opp") == next_opp]
    if len(h2h) == 1 and h2h[0]["hit"]:
        return {"rule": "H2H1", "k": 1, "n": 1, "display_rate": 1.0}
    return None


# ------------------------------------------------------------------ grading

def grade(line, settle_result, void_reason, lean_side):
    """-> (status, lean_result). `settle_result` is core.settlement's
    over / under / push / void / unsettled for THIS line."""
    if settle_result == settlement.VOID:
        return VOID, None
    if settle_result == settlement.UNSETTLED or settle_result is None:
        return None, None
    status = {settlement.OVER: CLEARED, settlement.UNDER: MISSED,
              settlement.PUSH: PUSH}[settle_result]
    if lean_side is None:
        return status, None
    if status == PUSH:
        return status, "push"
    won = (status == CLEARED) == (lean_side == "over")
    return status, ("cleared" if won else "missed")


def void_reason_from_settlement(reason):
    """core.settlement's did_not_play / inactive -> the Board's vocabulary."""
    if reason == settlement.INACTIVE:
        return VOID_INACTIVE
    if reason == settlement.DID_NOT_PLAY:
        return VOID_NO_SNAP
    raise ValueError(f"unknown settlement void reason {reason!r}")


# ------------------------------------------------------------------ merging reads

def merge_read(prev_rows, fresh_rows, read_at_ts, read_at_iso):
    """The rows of a new read, given the previous read's rows and what the
    books show now. Pure; `fresh_rows` are one per claim, at this read's main
    line; the result is keyed by `row_id` (claim + line), unique.

      settled in prev              copied verbatim - never edited again
      kicked off (prev)            prev row frozen, status LIVE, no lean change
      upcoming, still listed       the fresh row (main line may have moved)
      upcoming, gone from books    VOID market_pulled, pulled_at = this read
      new claim, not kicked off    the fresh row

    NOTHING THAT LEANED LEAVES THE BOARD (a-34). A row carrying a lean was
    published to the ledger at the read that first carried it, and is graded
    there on its OWN line. If the fresh read no longer reproduces that lean -
    the main line moved off it, or the row at that line now leans another way
    or not at all - the row is KEPT, frozen as it was priced (`priced_at`), and
    says why: `line_moved_after_publication` / `lean_changed_after_publication`.
    It then goes live, is graded and is voided exactly like any other row.
    Before a-34 the fresh row replaced it by claim and the lean vanished from
    every later read while the ledger still graded it (b-36, St. Brown 7.5).

    One row per (claim, line): where a frozen lean holds a line, a fresh row at
    the same line that leans differently is not shown, so an opposite lean at
    one line is never published while the first stands.

    A fresh row for a claim whose game has started is ignored: nothing a book
    says after kickoff reaches the board.
    """
    prev = {}
    for r in prev_rows:
        prev.setdefault(r["claim_id"], []).append(r)
    fresh = {r["claim_id"]: r for r in fresh_rows}
    out = []
    for cid, ps in prev.items():
        f = fresh.get(cid)
        emit_fresh = False
        held = set()
        for p in ps:
            p = dict(p)
            p.setdefault("line_moved_after_publication", False)
            p.setdefault("lean_changed_after_publication", False)
            if p["status"] in SETTLED:
                out.append(p)
            elif p["kickoff_ts"] <= read_at_ts:
                out.append(dict(p, status=LIVE))
            elif f is None:
                out.append(dict(p, status=VOID, void_reason=VOID_MARKET_PULLED,
                                pulled_at=read_at_iso, lean_result=None))
            else:
                emit_fresh = True
                if not p.get("lean") or (f["row_id"] == p["row_id"]
                                         and f.get("lean") == p["lean"]):
                    continue                    # the fresh row carries it on
                moved = f["line"] != p["line"]
                out.append(dict(p, is_main=not moved,
                                line_moved_after_publication=moved,
                                lean_changed_after_publication=not moved,
                                main_line_changed_since_open=(
                                    p.get("main_line_changed_since_open", False)
                                    or f["line"] != p.get("opened_line", p["line"]))))
                held.add(p["row_id"])
        if emit_fresh and f["row_id"] not in held:
            opened = ps[0].get("opened_line", ps[0]["line"])
            out.append(dict(f, opened_line=opened, priced_at=f.get("priced_at", read_at_iso),
                            main_line_changed_since_open=(
                                any(p.get("main_line_changed_since_open", False) for p in ps)
                                or f["line"] != opened),
                            line_moved_after_publication=False,
                            lean_changed_after_publication=False))
    for cid, f in fresh.items():
        if cid in prev or f["kickoff_ts"] <= read_at_ts:
            continue
        f = dict(f)
        f.setdefault("priced_at", read_at_iso)
        f.setdefault("opened_line", f["line"])
        f.setdefault("main_line_changed_since_open", False)
        f.setdefault("line_moved_after_publication", False)
        f.setdefault("lean_changed_after_publication", False)
        out.append(f)
    ids = [r["row_id"] for r in out]
    if len(ids) != len(set(ids)):
        raise AssertionError(f"merge_read produced a duplicate row_id: "
                             f"{sorted(i for i in ids if ids.count(i) > 1)[:3]}")
    return sorted(out, key=lambda r: (r["kickoff_ts"], r["claim_id"], r["line"]))


def apply_grades(rows, settle_fn):
    """Grade every LIVE row whose settlement is available. `settle_fn(row)` ->
    (settle_result, actual, void_reason) from core.settlement. Settled rows are
    returned untouched."""
    out = []
    for r in rows:
        if r["status"] != LIVE:
            out.append(r)
            continue
        res, actual, reason = settle_fn(r)
        status, lean_result = grade(r["line"], res, reason, r.get("lean"))
        if status is None:
            out.append(r)
            continue
        g = dict(r, status=status, lean_result=lean_result,
                 result={"value": actual, "cleared": status == CLEARED
                         if status in (CLEARED, MISSED) else None})
        if status == VOID:
            g["void_reason"] = void_reason_from_settlement(reason)
            g["result"] = None
        out.append(g)
    return out


# ------------------------------------------------------------------ the ledger

LEDGER_COLUMNS = (
    "event", "lean_id", "claim_id", "row_id", "season", "week", "game_id",
    "gsis_id", "market", "line", "side", "read_at", "kickoff_ts",
    "mkt_p_over", "mkt_books", "model_p_over", "gap_pp", "price", "band",
    "model_version", "lean_threshold_pp", "result", "actual", "void_reason",
    "event_at")
# Column types, in the contract's vocabulary (web/contract/v2 x-contract.tables
# .board_ledger.columns). jobs/board_read.ledger_schema asserts the two agree.
LEDGER_DTYPES = {c: "string" for c in LEDGER_COLUMNS}
LEDGER_DTYPES.update({c: "int64" for c in ("season", "week", "mkt_books")})
LEDGER_DTYPES.update({c: "float64" for c in ("line", "kickoff_ts", "mkt_p_over", "model_p_over",
                                             "gap_pp", "price", "lean_threshold_pp", "actual")})


def ledger_events(ledger, rows, read_at_iso, settle_lean, pulled_claims,
                  model_version, threshold):
    """New events to APPEND for this read. Never returns an edit.

    published   a lean on a board row at this read that the ledger has never
                seen, priced at this read's lean-side median price
    graded      a published lean with no terminal event whose settlement is now
                available - settled on ITS OWN line, not the board's current one
    void        market pulled before kickoff, or settlement voided (inactive,
                no snap)

    `settle_lean(ev)` -> (settle_result, actual, void_reason) or None.
    `pulled_claims`: {claim_id: pulled_at} for claims pulled before kickoff.
    """
    seen = {}
    terminal = set()
    for e in ledger:
        if e["event"] == "published":
            seen[e["lean_id"]] = e
        else:
            terminal.add(e["lean_id"])
    new = []
    for r in rows:
        side = r.get("lean")
        if not side or r["status"] != UPCOMING:
            continue
        lid = lean_id(r["claim_id"], r["line"], side)
        if lid in seen:
            continue
        ev = {c: None for c in LEDGER_COLUMNS}
        band = r.get("band") or {}
        ev.update(event="published", lean_id=lid, claim_id=r["claim_id"],
                  row_id=r["row_id"], season=r["season"], week=r["week"],
                  game_id=r["game_id"], gsis_id=r["gsis_id"], market=r["market"],
                  line=float(r["line"]), side=side, read_at=read_at_iso,
                  kickoff_ts=float(r["kickoff_ts"]), mkt_p_over=r["mkt_p_over"],
                  mkt_books=r["mkt_books"], model_p_over=r["model_p_over"],
                  gap_pp=r["gap_pp"], price=r.get("lean_price"),
                  band=(f"{band.get('lo_pp'):g}-{band.get('hi_pp'):g}"
                        if band.get("hi_pp") is not None else
                        f"{band.get('lo_pp'):g}+" if band else None),
                  model_version=model_version, lean_threshold_pp=threshold,
                  event_at=read_at_iso)
        new.append(ev)
        seen[lid] = ev
    for lid, pub in seen.items():
        if lid in terminal:
            continue
        pulled_at = pulled_claims.get(pub["claim_id"])
        if pulled_at is not None:
            new.append(dict(pub, event="void", void_reason=VOID_MARKET_PULLED,
                            event_at=pulled_at))
            terminal.add(lid)
            continue
        s = settle_lean(pub)
        if not s:
            continue
        res, actual, reason = s
        status, lean_result = grade(pub["line"], res, reason, pub["side"])
        if status is None:
            continue
        if status == VOID:
            new.append(dict(pub, event="void", void_reason=void_reason_from_settlement(reason),
                            event_at=read_at_iso))
        else:
            new.append(dict(pub, event="graded", result=lean_result, actual=actual,
                            event_at=read_at_iso))
        terminal.add(lid)
    return new


def assert_append_only(old, new):
    """The new ledger must be the old one plus rows at the end, byte for byte
    on every column. Raises otherwise - settled rows are never rewritten."""
    if len(new) < len(old):
        raise AssertionError(f"ledger shrank: {len(old)} -> {len(new)} rows")
    for i, (a, b) in enumerate(zip(old, new)):
        if {k: a.get(k) for k in LEDGER_COLUMNS} != {k: b.get(k) for k in LEDGER_COLUMNS}:
            raise AssertionError(f"ledger row {i} was rewritten: {a.get('lean_id')}")
    return True


def lean_states(ledger, now_ts):
    """{lean_id: state}, the four-way partition. Raises if a lean has two
    terminal events or a terminal event with no publication."""
    pubs, term = {}, {}
    for e in ledger:
        if e["event"] == "published":
            if e["lean_id"] in pubs:
                raise AssertionError(f"lean {e['lean_id']} published twice")
            pubs[e["lean_id"]] = e
        elif e["event"] in ("graded", "void"):
            if e["lean_id"] in term:
                raise AssertionError(f"lean {e['lean_id']} has two terminal events")
            term[e["lean_id"]] = e
        else:
            raise AssertionError(f"unknown ledger event {e['event']!r}")
    orphans = set(term) - set(pubs)
    if orphans:
        raise AssertionError(f"terminal events with no publication: {sorted(orphans)[:3]}")
    out = {}
    for lid, p in pubs.items():
        t = term.get(lid)
        if t is not None:
            if t["event"] == "void" and t.get("void_reason") not in VOID_REASONS:
                raise AssertionError(f"void lean {lid} carries no reason")
            out[lid] = S_VOID if t["event"] == "void" else S_GRADED
        else:
            out[lid] = S_UPCOMING if now_ts < p["kickoff_ts"] else S_LIVE
    return out


ROW_LEAN_STATE = {UPCOMING: S_UPCOMING, LIVE: S_LIVE, VOID: S_VOID,
                  CLEARED: S_GRADED, MISSED: S_GRADED, PUSH: S_GRADED}
LEAN_STATES = (S_GRADED, S_UPCOMING, S_LIVE, S_VOID)


def read_file_name(read_iso):
    """The file one read is written to, beside its week's index.json."""
    return f"read-{read_iso.replace(':', '')}.json"


def row_partition(rows):
    """{state: n} over the rows that carry a lean, in the four-way partition."""
    counts = {s: 0 for s in LEAN_STATES}
    for r in rows:
        if r.get("lean"):
            counts[ROW_LEAN_STATE[r["status"]]] += 1
    return counts


def index_reconciles(index, read):
    """-> the statement it approved; raises otherwise. The FILE-level half of the
    partition (a-35), checkable with no ledger: a week's index and the read it
    names as `latest` must agree. Since a-34 every published lean is on every
    later read as exactly one row in its ledger state, so the index's four counts
    ARE the lean-carrying rows of its latest read, partitioned by status - an
    index of all zeros, or of +1000, or a read with a counted lean row removed,
    cannot both be true. Also: the read is the one the index names, for the same
    week, and its rows are unique by row_id and belong to that week."""
    problems = []
    if read.get("read_at") != index.get("latest"):
        problems.append(f"index latest {index.get('latest')} but the read is {read.get('read_at')}")
    if index.get("latest") not in (index.get("reads") or []):
        problems.append(f"index latest {index.get('latest')} is not in its own reads list")
    for f in ("season", "week", "sport"):
        if read.get(f) != index.get(f):
            problems.append(f"index {f} {index.get(f)!r} but the read's is {read.get(f)!r}")
    rows = read.get("rows") or []
    seen, dup, stray = set(), [], []
    for r in rows:
        if r.get("row_id") in seen:
            dup.append(r.get("row_id"))
        seen.add(r.get("row_id"))
        if (r.get("season"), r.get("week")) != (read.get("season"), read.get("week")):
            stray.append(r.get("row_id"))
    if dup:
        problems.append(f"row_id on the read more than once: {dup[:3]}")
    if stray:
        problems.append(f"{len(stray)} row(s) belong to another week, e.g. {stray[0]}")
    got = row_partition(rows)
    want = {s: (index.get("leans") or {}).get(s) for s in LEAN_STATES}
    if got != want:
        diff = ", ".join(f"{s} index {want[s]} / read {got[s]}" for s in LEAN_STATES
                         if got[s] != want[s])
        problems.append(f"lean counts do not reconcile with the latest read's rows: {diff}")
    if problems:
        raise AssertionError("; ".join(problems))
    return (f"{index['season']} wk{int(index['week']):02d}: {sum(got.values())} leans = "
            f"{got[S_GRADED]} graded + {got[S_UPCOMING]} upcoming + {got[S_LIVE]} live + "
            f"{got[S_VOID]} void, index and latest read ({index['latest']}) agree")


def leans_on_board(ledger, rows, season, week, now_ts):
    """-> the statement it approved; raises otherwise. The read-level half of the
    partition (a-34): every lean the ledger published for this week is on this
    read as EXACTLY ONE row - same row_id, same side - in the state the ledger
    gives it, and no row carries a lean the ledger never published. So
    published = graded + upcoming + live + void holds on the rows themselves,
    with no remainder, and a page can check it rather than count what is missing."""
    states = lean_states(ledger, now_ts)
    pubs = {e["lean_id"]: e for e in ledger if e["event"] == "published"
            and e["season"] == season and e["week"] == week}
    by_row = {}
    for r in rows:
        by_row.setdefault(r["row_id"], []).append(r)
    dup = [k for k, v in by_row.items() if len(v) > 1]
    if dup:
        raise AssertionError(f"row_id on the read more than once: {dup[:3]}")
    counts = {S_GRADED: 0, S_UPCOMING: 0, S_LIVE: 0, S_VOID: 0}
    for lid, p in pubs.items():
        r = by_row.get(p["row_id"], [None])[0]
        if r is None or r.get("lean") != p["side"]:
            raise AssertionError(
                f"published lean {p['row_id']} {p['side']} is not on the read"
                + ("" if r is None else f" (the row there leans {r.get('lean')!r})"))
        got = ROW_LEAN_STATE[r["status"]]
        if got != states[lid]:
            raise AssertionError(f"lean {p['row_id']} {p['side']} is {got} on the read and "
                                 f"{states[lid]} on the ledger")
        counts[got] += 1
    published = {(p["row_id"], p["side"]) for p in pubs.values()}
    phantom = [r["row_id"] for r in rows if r.get("lean") and (r["row_id"], r["lean"]) not in published]
    if phantom:
        raise AssertionError(f"rows carry a lean the ledger never published: {phantom[:3]}")
    moved = sum(1 for r in rows if r.get("lean") and r.get("line_moved_after_publication"))
    changed = sum(1 for r in rows if r.get("lean") and r.get("lean_changed_after_publication"))
    return (f"{season} wk{int(week):02d}: {len(pubs)} published = {counts[S_GRADED]} graded + "
            f"{counts[S_UPCOMING]} upcoming + {counts[S_LIVE]} live + {counts[S_VOID]} void, "
            f"every one on the read ({moved} line moved, {changed} lean changed after publication)")


# ------------------------------------------------------------------ cadence

def iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def read_due(now_ts, last_read_ts, kickoffs, window_open_ts):
    """Is a board read due? Hourly from `window_open_ts` (Tuesday 12:00 ET) to
    the last kickoff; every 15 minutes inside the last two hours before ANY
    upcoming kickoff slot. After the last kickoff, the grading cadence applies
    (`grade_due`)."""
    if now_ts < window_open_ts:
        return False
    upcoming = [k for k in kickoffs if k > now_ts]
    if not upcoming:
        return False
    close = any(0 < k - now_ts <= config.BOARD_CLOSE_WINDOW_MIN * 60 for k in upcoming)
    every = (config.BOARD_CLOSE_READ_EVERY_MIN if close else config.BOARD_READ_EVERY_MIN) * 60
    return last_read_ts is None or now_ts - last_read_ts >= every


def grade_due(now_ts, last_read_ts, live_rows):
    """A grading read is due hourly while any row is LIVE (kicked off, not yet
    settled) - so grading lands within an hour of the stats arriving."""
    if not live_rows:
        return False
    return last_read_ts is None or now_ts - last_read_ts >= config.BOARD_GRADE_EVERY_MIN * 60
