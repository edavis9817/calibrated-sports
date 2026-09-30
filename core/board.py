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


def _find(rows, rid):
    return next((r for r in rows if r["row_id"] == rid), None)


def _priced_at(rid, reads, start):
    """The read a row was priced at, for a row written before `priced_at` existed.
    `reads` is [(read_iso, rows)] newest first. Before a-34 an UPCOMING row was
    always the fresh row of its read, and a frozen (live / settled) row was copied
    verbatim from the read before - so walk back to the newest read that has the
    row upcoming. A row that cannot be followed back is refused, never guessed."""
    for read_iso, rows in reads[start:]:
        r = _find(rows, rid)
        if r is None:
            break
        if r.get("priced_at"):
            return r["priced_at"]
        if r["status"] == UPCOMING:
            return read_iso
    raise AssertionError(f"row {rid} has no priced_at and no read of the week shows it upcoming")


def restore_legacy(reads, published):
    """A week whose latest read was written before a-34, made whole. Pure.

    `reads` is [(read_iso, rows)] for the week, NEWEST FIRST, the latest read at
    index 0; `published` is the ledger's published events for the week.
    -> (rows, note). Two repairs, both derived from reads already on disk:

      priced_at      a pre-a-34 row has none, and the contract requires it. It is
                     the read the row was priced at (`_priced_at`), not a guess.
      lost leans     the pre-a-34 merge replaced a claim's row when its main line
                     moved, so a lean published at the old line vanished from
                     every later read while the ledger kept it (b-36). It is put
                     back as the NEWEST read that carried it, flagged exactly as
                     `merge_read` would have flagged it had a-34 been in force.

    A published lean that is on no read of the week is refused: there is no row
    to restore, and inventing one - or voiding it - would be a claim nothing on
    disk supports. `leans_on_board` is not relaxed; this puts the rows back so
    that it holds as written (a-50)."""
    rows = [dict(r) for r in reads[0][1]]
    backfilled = 0
    for r in rows:
        if not r.get("priced_at"):
            r["priced_at"] = _priced_at(r["row_id"], reads, 0)
            backfilled += 1
        r.setdefault("line_moved_after_publication", False)
        r.setdefault("lean_changed_after_publication", False)
    restored = []
    for p in sorted(published, key=lambda e: (e["read_at"], e["row_id"])):
        if any(r["row_id"] == p["row_id"] and r.get("lean") == p["side"] for r in rows):
            continue
        hit = next(((i, _find(rs, p["row_id"])) for i, (_iso, rs) in enumerate(reads)
                    if (_find(rs, p["row_id"]) or {}).get("lean") == p["side"]), None)
        if hit is None:
            raise AssertionError(f"published lean {p['row_id']} {p['side']} is on no read of the "
                                 "week - nothing on disk to restore it from")
        i, src = hit
        k = dict(src)
        k["priced_at"] = k.get("priced_at") or _priced_at(k["row_id"], reads, i)
        same = _find(rows, k["row_id"])
        others = [r for r in rows if r["claim_id"] == k["claim_id"] and r["row_id"] != k["row_id"]]
        moved = same is None and bool(others)
        k.update(is_main=not moved, line_moved_after_publication=moved,
                 lean_changed_after_publication=same is not None,
                 main_line_changed_since_open=bool(k.get("main_line_changed_since_open")) or moved)
        if same is not None:
            rows = [r for r in rows if r["row_id"] != k["row_id"]]
        rows.append(k)
        restored.append(f"{k['row_id']} {p['side']}")
    return rows, {"priced_at_backfilled": backfilled, "leans_restored": restored}


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

# THE HASH CHAIN (a-48). Every ledger row carries `prev_hash` (the row before's
# `row_hash`, or CHAIN_GENESIS on row 0) and `row_hash` = sha256 over the row's
# LEDGER_COLUMNS plus `prev_hash`. So editing or removing any row breaks its own
# hash and every hash after it, and a reader who recorded the head (index, hash)
# yesterday can check today's file still carries it at that index.
#
# WHAT IT COVERS: ALL of LEDGER_COLUMNS - the lean, line, side, price, gap, both
# probabilities, the threshold, the result and the void reason. Nothing in the
# ledger legitimately changes after it is written, because the ledger is EVENTS:
# grading appends a `graded` row and never touches the `published` one
# (`assert_append_only`). The fields that DO change on grading - a row's
# status / result / lean_result - live on the READ files, which are not chained.
# A column added to LEDGER_COLUMNS is covered by construction; tests pin that.
CHAIN_COLUMNS = ("prev_hash", "row_hash")
LEDGER_DTYPES.update({c: "string" for c in CHAIN_COLUMNS})
CHAIN_TAG = "cs-board-ledger-chain-v1"
CHAIN_GENESIS = "0" * 64

# WRITTEN_AT (a-62). `event_at` is the READ's time, and `board_read --at` lets a
# caller name that - so a replayed week wrote events that were pre-kickoff by
# construction, and f-22 spliced one onto the live ledger as 208 "published"
# calls. `written_at` is when `jobs.board_read.write_ledger` wrote the row, from
# the WALL CLOCK, and is never accepted from a caller (invariant 8's `ingest_ts`
# rule, applied to the ledger). Rows written before a-62 carry null.
#
# It is NOT one of LEDGER_COLUMNS, on purpose. The chain hashes every
# LEDGER_COLUMNS cell, so adding a 26th would put one more null into every
# existing row's payload and break all 411 hashes already published. Instead a
# row that CARRIES written_at is hashed under CHAIN_TAG_STAMPED with the stamp
# after the event cells; a row with none is hashed exactly as a-48 hashed it.
# So the stamp is tamper-evident where it exists and no existing hash moves.
STAMP_COLUMN = "written_at"
LEDGER_DTYPES[STAMP_COLUMN] = "string"
CHAIN_TAG_STAMPED = "cs-board-ledger-chain-v2"
# The columns a ledger file written earlier may lack, in file order. A file
# without them is NARROWER, not different: every cell it has is unchanged.
WIDENING_COLUMNS = (STAMP_COLUMN,) + CHAIN_COLUMNS


def ledger_file_columns():
    """The ledger FILE's columns: the event columns, the write stamp, then the
    chain. A function, not a constant, so it reads LEDGER_COLUMNS as it is when
    called."""
    return tuple(LEDGER_COLUMNS) + (STAMP_COLUMN,) + CHAIN_COLUMNS


def is_narrower_file(columns):
    """True if `columns` are the ledger file's columns with some (not none) of
    WIDENING_COLUMNS absent and the rest in order - a file written before a-48
    and/or a-62. Anything else is a different file, not an older one."""
    full = ledger_file_columns()
    cols = list(columns)
    if cols == list(full):
        return False
    missing = [c for c in full if c not in cols]
    return (bool(missing) and all(c in WIDENING_COLUMNS for c in missing)
            and cols == [c for c in full if c not in missing])


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
    cols = tuple(LEDGER_COLUMNS) + (STAMP_COLUMN,)      # a-62: the stamp is never rewritten either
    for i, (a, b) in enumerate(zip(old, new)):
        if {k: a.get(k) for k in cols} != {k: b.get(k) for k in cols}:
            raise AssertionError(f"ledger row {i} was rewritten: {a.get('lean_id')}")
    return True


# ------------------------------------------------------------------ the hash chain (a-48)

def canon_float(x):
    """A float as ECMAScript's Number.prototype.toString writes it - shortest
    round-trip digits, `1790528400` not `1790528400.0`, `1e-7` not `1e-07` - so a
    browser can recompute a row hash with `String(n)` and no Python in sight.
    Python's repr gives the same shortest digits; only the layout differs."""
    import decimal
    x = float(x)
    if not math.isfinite(x):
        raise ValueError(f"the ledger chain cannot hash a non-finite float ({x!r})")
    if x == 0:
        return "0"                                  # -0 too, as String(-0) is "0"
    sign = "-" if x < 0 else ""
    t = decimal.Decimal(repr(abs(x))).as_tuple()
    digits = "".join(map(str, t.digits))
    stripped = digits.rstrip("0")
    exp = t.exponent + (len(digits) - len(stripped))
    s, k = stripped, len(stripped)
    n = k + exp                                      # value = 0.s x 10^n
    if k <= n <= 21:
        out = s + "0" * (n - k)
    elif 0 < n <= 21:
        out = s[:n] + "." + s[n:]
    elif -6 < n <= 0:
        out = "0." + "0" * (-n) + s
    else:
        e = n - 1
        out = (s if k == 1 else s[0] + "." + s[1:]) + "e" + ("+" if e >= 0 else "-") + str(abs(e))
    return sign + out


def canon_value(v, dtype):
    """One cell as the chain hashes it. Accepts the parquet's typed value or the
    CSV's text, so both files of the pair hash identically. Null and the empty
    string are one value: CSV cannot tell them apart, and neither can the site's
    reader (lib/ledger.ts `cellFrom`)."""
    if v is None or (isinstance(v, str) and v == ""):
        return None
    if isinstance(v, float) and math.isnan(v):
        return None
    if dtype == "string":
        return str(v)
    if dtype == "int64":
        if isinstance(v, float) and not v.is_integer():
            raise ValueError(f"int64 cell holds {v!r}")
        return str(int(v) if not isinstance(v, str) else int(v, 10))
    if dtype == "float64":
        return canon_float(v)
    raise ValueError(f"no canonical form for dtype {dtype!r}")


def chain_payload(row, prev_hash):
    """The exact bytes a row hash is taken over: a JSON array, no whitespace -
    [CHAIN_TAG, every LEDGER_COLUMNS cell in contract order (text or null),
    prev_hash], or for a row carrying `written_at` (a-62)
    [CHAIN_TAG_STAMPED, the same cells, written_at, prev_hash]."""
    import json
    cells = [canon_value(row.get(c), LEDGER_DTYPES[c]) for c in LEDGER_COLUMNS]
    stamp = canon_value(row.get(STAMP_COLUMN), LEDGER_DTYPES[STAMP_COLUMN])
    if stamp is None:
        payload = [CHAIN_TAG, *cells, prev_hash]
    else:           # a-62: [CHAIN_TAG_STAMPED, every event cell, written_at, prev_hash]
        payload = [CHAIN_TAG_STAMPED, *cells, stamp, prev_hash]
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def row_hash(row, prev_hash):
    return hashlib.sha256(chain_payload(row, prev_hash)).hexdigest()


def chain(rows, prev=CHAIN_GENESIS):
    """`rows` with prev_hash / row_hash set, in order. Pure; any hash a row
    already carries is overwritten - use `extend_chain` to append to a ledger."""
    out = []
    for r in rows:
        h = row_hash(r, prev)
        out.append(dict(r, prev_hash=prev, row_hash=h))
        prev = h
    return out


def is_chained(rows):
    """True if every row carries a chain, False if none does (a ledger written
    before a-48). A ledger where SOME rows carry one is refused: no writer here
    produces it, so it was edited."""
    have = [bool(r.get("row_hash")) for r in rows]
    if all(have):
        return True
    if not any(have):
        return False
    raise AssertionError(f"ledger is partly chained: {sum(have)} of {len(have)} rows carry a "
                         "row_hash - no writer produces that; refusing")


def extend_chain(old, new):
    """old + new, chained. The OLD rows' chain is verified first and refused if it
    is broken: extending a broken chain would re-hash the tampering into a valid
    one. An unchained old ledger (pre-a-48) is chained from genesis, which
    changes no LEDGER_COLUMNS cell - that is the one-time migration."""
    if old and is_chained(old):
        rep = verify_chain(old)
        if not rep.holds:
            raise AssertionError(f"refusing to extend a broken ledger chain: {rep.statement}")
        prev = old[-1]["row_hash"]
        return [dict(r) for r in old] + chain(new, prev)
    return chain(list(old) + list(new))


class ChainReport:
    """What `verify_chain` found. `.holds` is the verdict and `.statement` the
    one line to log. Refuses truth-testing, so `if verify_chain(x):` and
    `assert verify_chain(x)` raise instead of passing on an object that is
    always truthy (the proxy table's `__bool__` row)."""

    def __init__(self, rows, first_break, broken, reason, head):
        self.rows, self.first_break, self.broken = rows, first_break, broken
        self.reason, self.head = reason, head
        self.holds = first_break is None and reason is None

    @property
    def statement(self):
        if self.reason and self.first_break is None:
            return self.reason
        if self.holds:
            h = self.head
            return (f"chain holds: {self.rows} rows, head #{h['index']} {h['row_hash']}"
                    if h else "chain holds: empty ledger")
        return (f"chain BROKEN at row {self.first_break} (0-based) of {self.rows}: {self.reason}; "
                f"{self.broken} of the {self.rows - self.first_break} rows from there fail")

    def __bool__(self):
        raise TypeError("a ChainReport is not a boolean - read .holds for the verdict "
                        "and .statement for what it found")

    def __repr__(self):
        return f"ChainReport({self.statement!r})"


def verify_chain(rows, genesis=CHAIN_GENESIS):
    """Walk the ledger recomputing every hash from the row's own cells and the
    RECOMPUTED hash before it. -> ChainReport naming the first index that fails.
    Because each recomputation feeds the next, one edited or removed row fails
    itself and every row after it."""
    if not rows:
        return ChainReport(0, None, 0, None, None)
    if not is_chained(rows):
        return ChainReport(len(rows), None, 0,
                           f"no chain: {len(rows)} rows carry no prev_hash / row_hash "
                           "(a ledger written before a-48)", None)
    prev, first, broken, reason = genesis, None, 0, None
    for i, r in enumerate(rows):
        h = row_hash(r, prev)
        bad = None
        if r.get("prev_hash") != prev:
            bad = (f"prev_hash {str(r.get('prev_hash'))[:12]}... is not the hash of the row "
                   f"before ({prev[:12]}...)")
        elif r.get("row_hash") != h:
            bad = f"row_hash {str(r.get('row_hash'))[:12]}... is not the hash of its cells ({h[:12]}...)"
        if bad:
            broken += 1
            if first is None:
                first, reason = i, bad
        prev = h
    head = {"index": len(rows) - 1, "rows": len(rows), "row_hash": rows[-1].get("row_hash"),
            "event_at": rows[-1].get("event_at")}
    return ChainReport(len(rows), first, broken, reason, head)


def chain_head(rows):
    """The head a reader records: the last row's index (0-based), the row
    count, its row_hash and its event_at. None for an empty ledger."""
    if not rows:
        return None
    last = rows[-1]
    return {"index": len(rows) - 1, "rows": len(rows), "row_hash": last["row_hash"],
            "event_at": last["event_at"]}


def check_head(rows, index, recorded_hash):
    """-> the statement it approved; raises otherwise. A head recorded earlier
    must still be in the ledger at the same index. This is what catches a
    ledger cut short from the END, which an internally valid chain cannot: drop
    the last rows and what remains still verifies."""
    if index >= len(rows):
        raise AssertionError(f"recorded head #{index} is past the end of the ledger "
                             f"({len(rows)} rows) - rows were removed")
    got = rows[index].get("row_hash")
    if got != recorded_hash:
        raise AssertionError(f"row #{index} hashes {got} now, and {recorded_hash} was recorded")
    return f"recorded head #{index} {recorded_hash[:12]}... is still row #{index}"


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
