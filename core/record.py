"""The record (unit a-57): three tiers of evidence, three files, never blended.

No I/O here. `jobs/record_export.py` reads each tier's ONE source and hands it
to ONE builder below. Everything that decides what a record file SAYS is in this
file, so it can be driven in a test with the other two sources absent.

THE RULE THE WHOLE MODULE EXISTS FOR. There are three kinds of evidence on this
project and they are not comparable:

    published   leans written to the Board ledger before kickoff, graded after
                -> "these were our calls"
    research    pre-registered hypotheses with a verdict
                -> "this is what we tested and what failed"
    backtest    replayed history, out of sample
                -> "the method was checked at scale"

Each builder takes exactly one source and no builder can see another's. No
figure here averages, pools or weights across tiers, and no file carries a
single "accuracy" that spans them. A reader who wants one number is told,
correctly, that there is not one.

PUBLISHED IS A PROJECTION OF THE LEDGER, NEVER A RE-DERIVATION. A published
call is one that existed in the ledger before its kickoff. A lean whose
publication cannot be shown to predate its own `kickoff_ts` - its `read_at` or
`event_at` at or after kickoff, or unparseable - is excluded and counted in
`excluded_not_pre_kickoff` by lean id, with its terminal event. Reconstructed
weeks (a-54's landing backfill) live in a different tree and never reach the
ledger; if one ever did, this is where it stops.

THE INTERVAL BLOCKS ON WEEK and on nothing finer. With one graded week there is
one block, the bootstrap has no variance to estimate, and the interval is
published as null with `informative: false` - never as a zero-width pair and
never narrowed by blocking on game or lean. `informative` stays false while
n_blocks < 3.

RESEARCH ROWS COME FROM THE DOCUMENTS. One row per tracked
`docs/*preregistration*.md`; its findings document is the tracked
`docs/findings/*.md` that cites it. The verdict is a judgement read off prose,
so it is DECLARED (docs/record/research-verdicts.json) - but a declaration may
not type a figure: the headline is parsed from a quote that must appear
verbatim in the findings document, and every number in a declared sentence
(question, power, note) must appear in that row's documents. A registered
hypothesis with no declaration is published as `open`, unclassified - absence
fails toward showing the row, not toward dropping it. A `retired` row with an
empty `power` is refused: a bare "retired" is a stronger claim than the data.

THE BACKTEST STATEMENT IS ONE STRING. Where the file says the model's loss to
the book close is mostly over-confidence, the same string says that a corrected
model and the close both barely beat a constant base rate, and that the close
still orders outcomes better - so no consumer can render the first clause
alone. Each clause's wording is computed from the figures it states, so each
can come out the other way.
"""
from __future__ import annotations

import datetime as dt
import random
import re
import statistics

from core import board as B

TIERS = ("published", "research", "backtest")
SUPPORTS = {
    "published": "these were our calls",
    "research": "this is what we tested and what failed",
    "backtest": "the method was checked at scale",
}
VERDICTS = ("supported", "retired", "open", "stopped_by_gate")
UNITS = ("pp", "brier", "auc", "slope")

BOOT_DRAWS = 2000
BOOT_SEED = 57
INFORMATIVE_MIN_BLOCKS = 3

CHAIN_ABSENT = ("a-48's ledger hash chain is not in this tree, so no chain head is published. "
                "No substitute integrity figure is published in its place.")


class RecordError(AssertionError):
    """A record file would say something its one source does not support."""


def _ts(iso):
    return dt.datetime.fromisoformat(str(iso).replace("Z", "+00:00")).timestamp()


def _r(x, nd=4):
    return None if x is None else round(float(x), nd)


# =============================================================================
# published - the ledger, projected
# =============================================================================

def pre_kickoff(pub):
    """-> None if this published event can be shown to predate its kickoff,
    else the reason it cannot. Both the read it names and the moment the event
    was written must be strictly before kickoff."""
    try:
        kick = float(pub["kickoff_ts"])
    except (TypeError, ValueError, KeyError):
        return "no kickoff_ts"
    for field in ("read_at", "event_at"):
        v = pub.get(field)
        if v is None:
            return f"no {field}"
        try:
            t = _ts(v)
        except ValueError:
            return f"unparseable {field} {v!r}"
        if t >= kick:
            return f"{field} {v} is not before kickoff"
    return None


def _outcome(res):
    return {"cleared": "cleared", "missed": "missed", "push": "push"}.get(res)


def _units(price, res):
    """Profit on a one-unit stake at the published price."""
    if res == "cleared":
        return B.american_to_decimal(price) - 1.0
    if res == "missed":
        return -1.0
    return 0.0


def _summ(leans):
    """cleared/missed/push/void/ungraded and the rate against break-even."""
    c = {k: 0 for k in ("cleared", "missed", "push", "void", "ungraded")}
    for x in leans:
        c[x["_bucket"]] += 1
    decided = [x for x in leans if x["_bucket"] in ("cleared", "missed")]
    priced = [x for x in decided if x["breakeven"] is not None]
    staked = [x for x in leans if x["_bucket"] in ("cleared", "missed", "push")
              and x["price"] is not None]
    hit = c["cleared"] / len(decided) if decided else None
    be = statistics.fmean(x["breakeven"] for x in priced) if priced else None
    # the margin is measured on the priced decided leans only, so rate and
    # break-even describe the same rows
    hit_p = (sum(1 for x in priced if x["_bucket"] == "cleared") / len(priced)) if priced else None
    units = sum(_units(x["price"], x["_bucket"]) for x in staked) if staked else None
    invalid = sum(1 for x in leans if x["price"] is None and x["price_ledgered"] is not None)
    return dict(c, graded=c["cleared"] + c["missed"] + c["push"], n_price_invalid=invalid,
                hit_rate=_r(hit), breakeven=_r(be),
                margin_pp=_r(100 * (hit_p - be), 2) if priced else None,
                n_priced=len(priced), units=_r(units, 3),
                roi=_r(units / len(staked)) if staked else None)


def week_block_interval(leans, draws=BOOT_DRAWS, seed=BOOT_SEED):
    """Percentile bootstrap of the hit rate and the margin over break-even,
    resampling WEEKS with replacement. -> the interval block.

    A week is a block because a slate shares a scoring environment and one
    player's rungs are one claim. With fewer than two blocks there is nothing
    to resample: the interval is null, not the point repeated."""
    decided = [x for x in leans if x["_bucket"] in ("cleared", "missed")]
    weeks = sorted({(x["season"], x["week"]) for x in decided})
    out = {"method": "percentile bootstrap, resampling weeks with replacement",
           "block": "week", "n_blocks": len(weeks), "draws": draws, "seed": seed,
           "hit_rate": None, "margin_pp": None,
           "informative": len(weeks) >= INFORMATIVE_MIN_BLOCKS}
    if len(weeks) < 2:
        out["why"] = (f"{len(weeks)} graded week(s): a week-block bootstrap has one block and no "
                      "variance to estimate, so no interval is published. Blocking on game or "
                      "lean instead would print a narrower interval than the data supports.")
        return out
    by = {w: [x for x in decided if (x["season"], x["week"]) == w] for w in weeks}
    rng = random.Random(seed)
    hits, margins = [], []
    for _ in range(draws):
        rows = [x for w in (rng.choice(weeks) for _ in weeks) for x in by[w]]
        hits.append(sum(1 for x in rows if x["_bucket"] == "cleared") / len(rows))
        pr = [x for x in rows if x["breakeven"] is not None]
        if pr:
            margins.append(100 * (sum(1 for x in pr if x["_bucket"] == "cleared") / len(pr)
                                  - statistics.fmean(x["breakeven"] for x in pr)))

    def pct(v):
        v = sorted(v)
        return [_r(v[int(0.025 * (len(v) - 1))]), _r(v[int(0.975 * (len(v) - 1))])]
    out["hit_rate"] = pct(hits)
    out["margin_pp"] = pct(margins) if margins else None
    out["why"] = (None if out["informative"] else
                  f"{len(weeks)} graded weeks: fewer than {INFORMATIVE_MIN_BLOCKS} blocks, so the "
                  "interval is shown for completeness and is not informative")
    return out


def build_published(ledger, now_ts, chain=None, source=None):
    """The published tier, from the ledger's rows ALONE. -> the payload body
    (envelope added by the job). `ledger` is the list of ledger events in file
    order; `chain` is a-48's {head, rows_verified, verified_at} or None."""
    states = B.lean_states(ledger, now_ts)      # raises on a broken partition
    pubs = {e["lean_id"]: e for e in ledger if e["event"] == "published"}
    term = {e["lean_id"]: e for e in ledger if e["event"] in ("graded", "void")}
    excluded, leans = [], []
    for lid, p in pubs.items():
        why = pre_kickoff(p)
        if why is not None:
            excluded.append({"lean_id": lid, "reason": why,
                             "terminal_event": (term.get(lid) or {}).get("event")})
            continue
        t = term.get(lid)
        state = states[lid]
        if state == B.S_GRADED:
            bucket = _outcome(t.get("result"))
            if bucket is None:
                raise RecordError(f"graded lean {lid} carries result {t.get('result')!r}")
        elif state == B.S_VOID:
            bucket = "void"
        else:
            bucket = "ungraded"
        raw = p.get("price")
        raw = None if raw is None or raw != raw else float(raw)
        # The ledger's price is a MEDIAN OF AMERICAN ODDS (jobs/board_read.py), and
        # with an even number of books straddling even money the median lands in
        # (-100, 100), which is not a price: -2.5 read as American odds is a 41x
        # payout. Such a row is published with its ledgered value and no price.
        price = raw if B.american_to_prob(raw) is not None else None
        mp = p.get("model_p_over")
        leans.append({
            "lean_id": lid, "season": int(p["season"]), "week": int(p["week"]),
            "game_id": p["game_id"], "gsis_id": p["gsis_id"], "market": p["market"],
            "line": float(p["line"]), "side": p["side"], "read_at": p["read_at"],
            "kickoff_ts": float(p["kickoff_ts"]), "price": price, "price_ledgered": raw,
            "breakeven": _r(B.american_to_prob(price)) if price is not None else None,
            "mkt_p_over": _r(p.get("mkt_p_over")), "model_p_over": _r(mp),
            "model_p_side": _r(mp if p["side"] == "over" else 1 - mp) if mp is not None else None,
            "gap_pp": _r(p.get("gap_pp"), 2), "band": p.get("band"),
            "model_version": p.get("model_version"), "state": state,
            "result": bucket if bucket in ("cleared", "missed", "push") else None,
            "actual": _r((t or {}).get("actual")) if state == B.S_GRADED else None,
            "void_reason": (t or {}).get("void_reason") if state == B.S_VOID else None,
            "settled_at": (t or {}).get("event_at"),
            "_bucket": bucket})
    leans.sort(key=lambda x: (x["season"], x["week"], x["kickoff_ts"], x["game_id"],
                              x["gsis_id"], x["market"], x["line"]))
    weeks = []
    for s, w in sorted({(x["season"], x["week"]) for x in leans}):
        rows = [x for x in leans if (x["season"], x["week"]) == (s, w)]
        weeks.append({"season": s, "week": w, "n": len(rows), **_summ(rows)})
    total = _summ(leans)
    n_pub = len(leans)
    if total["graded"] + total["void"] + total["ungraded"] != n_pub:
        raise RecordError("published leans do not partition into graded / void / ungraded")
    body = {
        "tier": "published", "supports": SUPPORTS["published"],
        "source": source,
        "chain": chain,
        "chain_note": None if chain is not None else CHAIN_ABSENT,
        "n_published": n_pub, "n_graded": total["graded"], "n_void": total["void"],
        "n_ungraded": total["ungraded"],
        "weeks_published": [{"season": x["season"], "week": x["week"]} for x in weeks],
        "weeks_graded": [{"season": x["season"], "week": x["week"]}
                         for x in weeks if x["graded"] > 0],
        "excluded_not_pre_kickoff": {"n": len(excluded),
                                     "rule": ("a lean is a published call only if both its read_at and "
                                              "its event_at are strictly before its own kickoff_ts"),
                                     "rows": sorted(excluded, key=lambda e: e["lean_id"])},
        "record": {**{k: total[k] for k in ("cleared", "missed", "push", "void", "hit_rate",
                                             "breakeven", "margin_pp", "n_priced",
                                             "n_price_invalid", "units", "roi")},
                   "price_rule": ("break-even is the vig-inclusive probability of the lean-side "
                                  "price the ledger published; a ledgered price inside (-100, 100) "
                                  "is not an American price and is left out of break-even and "
                                  "units, counted in n_price_invalid"),
                   "interval": week_block_interval(leans)},
        "weeks": [{k: v for k, v in x.items() if k != "ungraded"} | {"ungraded": x["ungraded"]}
                  for x in weeks],
        "leans": [{k: v for k, v in x.items() if not k.startswith("_")} for x in leans],
    }
    return body


# =============================================================================
# research - pre-registrations and their findings, as documented
# =============================================================================

_NUM = re.compile(r"\d[\d,]*(?:\.\d+)?")


def norm_text(s):
    """Markdown and typography folded so a quote can be matched: emphasis and
    code marks dropped, unicode minus and dashes to ASCII, whitespace collapsed."""
    s = s.replace("−", "-").replace("–", "-").replace("—", "-")
    s = s.replace("**", "").replace("`", "").replace("±", "+/-")
    return re.sub(r"\s+", " ", s).strip()


def numbers_in(s):
    return {m.group(0).replace(",", "") for m in _NUM.finditer(norm_text(s))}


def parse_figure(quote):
    """'−0.60pp per contract [−9.18, +8.54]' -> (est, lo, hi). The interval is
    the first bracketed pair; the estimate is the LAST number before it, so a
    label's own digits ("end of Q3", "P1 pooled") are never read as the value."""
    q = norm_text(quote)
    m = re.search(r"\[\s*([+-]?\d[\d,]*\.?\d*)\s*,\s*([+-]?\d[\d,]*\.?\d*)\s*\]", q)
    before = re.findall(r"[+-]?\d[\d,]*\.?\d*", q[:m.start()]) if m else []
    if not m or not before:
        raise RecordError(f"no 'estimate [lo, hi]' figure in the quote {quote!r}")
    est = float(before[-1].replace(",", ""))
    lo, hi = (float(g.replace(",", "")) for g in m.groups())
    if not lo <= hi:
        raise RecordError(f"interval out of order in {quote!r}")
    return est, lo, hi


def cited_prereg(text, prereg_paths):
    """The pre-registration a findings document cites, by path. -> path or None."""
    hits = [p for p in prereg_paths if p.split("/")[-1] in text]
    return hits[0] if len(hits) == 1 else None


def build_research(docs, declarations):
    """The research tier, from the documents ALONE. `docs` is
    {path: {"text", "commit", "author_date"}} for every TRACKED pre-registration
    and findings document; `declarations` is the verdict file's rows."""
    prereg = sorted(p for p in docs if re.fullmatch(r"docs/[^/]*preregistration[^/]*\.md", p))
    findings = sorted(p for p in docs if re.fullmatch(r"docs/findings/[^/]+\.md", p))
    link = {}
    unlinked = []
    for f in findings:
        p = cited_prereg(docs[f]["text"], prereg)
        if p is None:
            unlinked.append(f)
        else:
            link.setdefault(p, []).append(f)
    by_prereg = {}
    for d in declarations:
        by_prereg.setdefault(d["prereg"], []).append(d)
    stray = sorted(set(by_prereg) - set(prereg))
    if stray:
        raise RecordError(f"verdicts declared for pre-registrations not in the tree: {stray}")
    rows, unclassified = [], []
    for p in prereg:
        fdocs = link.get(p, [])
        fdoc = fdocs[0] if len(fdocs) == 1 else None
        base_id = re.match(r"docs/([A-Za-z]+\d+)", p)
        base_id = base_id.group(1) if base_id else p.rsplit("/", 1)[-1][:-3]
        reg = {"commit": docs[p]["commit"], "author_date": docs[p]["author_date"]}
        decl = by_prereg.get(p)
        if not decl:
            unclassified.append(p)
            rows.append({"id": base_id, "prereg": p, "findings": fdoc, "question": None,
                         "registered_at": reg, "verdict": "open", "headline": None,
                         "power": None, "note": "registered; no verdict has been recorded for it",
                         "classified": False})
            continue
        corpus = docs[p]["text"] + "\n" + (docs[fdoc]["text"] if fdoc else "")
        known = numbers_in(corpus)
        for d in decl:
            if d["verdict"] not in VERDICTS:
                raise RecordError(f"{d['id']}: verdict {d['verdict']!r} not in {VERDICTS}")
            if d.get("findings") not in (None, fdoc):
                raise RecordError(f"{d['id']}: declared findings {d.get('findings')!r} but the "
                                  f"document citing {p} is {fdoc!r}")
            power = (d.get("power") or "").strip()
            if d["verdict"] == "retired" and not power:
                raise RecordError(f"{d['id']}: a retired row must say what it was retired on "
                                  "(venue, window, sample, interval) - a bare 'retired' is refused")
            for field in ("question", "power", "note"):
                missing = numbers_in(d.get(field) or "") - known
                if missing:
                    raise RecordError(f"{d['id']}: {field} states {sorted(missing)}, which appear in "
                                      f"neither {p} nor {fdoc}")
            head = None
            if d.get("headline"):
                h = d["headline"]
                if h.get("unit") not in UNITS:
                    raise RecordError(f"{d['id']}: headline unit {h.get('unit')!r} not in {UNITS}")
                if norm_text(h["quote"]) not in norm_text(corpus):
                    raise RecordError(f"{d['id']}: headline quote not found verbatim in {p} or "
                                      f"{fdoc}: {h['quote']!r}")
                est, lo, hi = parse_figure(h["quote"])
                head = {"label": h["label"], "estimate": est, "lo": lo, "hi": hi,
                        "unit": h["unit"], "quote": h["quote"]}
            if d["verdict"] == "stopped_by_gate" and head is not None and not d.get("note"):
                raise RecordError(f"{d['id']}: a gated row that prints a figure must say whose "
                                  "figure it is")
            rows.append({"id": d["id"], "prereg": p, "findings": fdoc,
                         "question": d["question"], "registered_at": reg,
                         "verdict": d["verdict"], "headline": head,
                         "power": power or None, "note": d.get("note"), "classified": True})
    counts = {v: sum(1 for r in rows if r["verdict"] == v) for v in VERDICTS}
    return {"tier": "research", "supports": SUPPORTS["research"],
            "n_rows": len(rows), "by_verdict": counts,
            "rows": rows, "unclassified": unclassified, "findings_unlinked": unlinked}


# =============================================================================
# backtest - the at-scale figures, from their one findings document
# =============================================================================

def md_tables(text):
    """Every markdown table in `text` -> [(header cells, rows)], cells folded
    by `norm_text` so a quoted figure parses the same way a verbatim one does."""
    lines = text.splitlines()
    out, i = [], 0
    while i < len(lines):
        ln = lines[i].strip()
        if ln.startswith("|") and i + 1 < len(lines) and re.match(r"^\|[\s:|-]+\|$", lines[i + 1].strip()):
            head = [norm_text(c) for c in ln.strip("|").split("|")]
            rows, j = [], i + 2
            while j < len(lines) and lines[j].strip().startswith("|"):
                rows.append([norm_text(c) for c in lines[j].strip().strip("|").split("|")])
                j += 1
            out.append((head, rows))
            i = j
        else:
            i += 1
    return out


def _table(text, must):
    """The first table whose header contains every string in `must`."""
    for head, rows in md_tables(text):
        joined = " | ".join(head)
        if all(m in joined for m in must):
            return rows
    raise RecordError(f"the backtest source has no table with header {must}")


def _num(s):
    return float(norm_text(s).replace(",", "").replace("+", ""))


def _row(rows, key):
    for r in rows:
        if r[0] == key:
            return r
    raise RecordError(f"no row {key!r} in the backtest source's table")


# The pair the register carried before the 2026-09-17 settlement fix. It cannot be
# re-derived from today's store - that is what superseded means - so it is carried
# as a record, with where it was last stated, and never as a current figure.
SUPERSEDED = ({
    "text": "+0.0218 to +0.0272 over 14,857", "lo": 0.0218, "hi": 0.0272, "n": 14857,
    "reason": ("the pre-settlement-fix population (c-24): it predates the 2026-09-17 settlement "
               "fix, under which an outcome whose player played with no stat row settles at 0 "
               "instead of being dropped. The current figure is the same walk-forward re-run "
               "after the fix."),
    "last_stated": ("brief 023 (06d26eb); docs/findings/three-gaps-closed-and-settlement-bug.md; "
                    "DECISIONS.md 2026-09-17"),
},)


def build_backtest(text, source):
    """The backtest tier, from c-24's findings document ALONE (brief 023's
    walk-forward against the book close, reproduced there to 4 dp). -> body."""
    t = norm_text(text)
    m = re.search(r"(\d{4})-(\d{4}) regular season, ([\d,]+) outcomes over ([\d,]+) games", t)
    if not m:
        raise RecordError("the backtest source does not state P1's seasons, outcomes and games")
    s0, s1 = int(m.group(1)), int(m.group(2))
    n_all, games = int(m.group(3).replace(",", "")), int(m.group(4).replace(",", ""))
    seasons = list(range(s0, s1 + 1))
    gate = _table(text, ["population", "n", "Brier diff"])        # the reproduction gate
    per = [{"season": s, "n": int(_num(_row(gate, f"P1 {s}")[1])),
            "brier_model_minus_close": _num(_row(gate, f"P1 {s}")[2])} for s in seasons]
    if sum(x["n"] for x in per) != n_all:
        raise RecordError(f"per-season n {[x['n'] for x in per]} do not sum to the stated {n_all}")
    lo = min(x["brier_model_minus_close"] for x in per)
    hi = max(x["brier_model_minus_close"] for x in per)

    # clause 1: the share of each season's Brier gap that is miscalibration (CORP)
    shares = _table(text, ["population", "dMCB", "share of dBS"])
    corp = {s: float(_row(shares, f"P1 {s}")[3].split("/")[0]) for s in seasons}
    # clause 2: the recalibrated model and the close, each against a constant
    post = _table(text, ["split", "constant", "market - constant"])
    const = [{"season": int(r[0].split()[1]),
              "model_recal_minus_constant": parse_figure(r[2]),
              "close_minus_constant": parse_figure(r[3])}
             for r in post if re.fullmatch(r"R1 \d{4}", r[0])]
    if not const:
        raise RecordError("the backtest source has no R1 constant-forecast rows")
    # clause 3: ordering, pooled over the seasons
    disc = _table(text, ["population", "AUC model", "AUC market", "dAUC"])
    auc = parse_figure(_row(disc, "P1 pooled")[3])
    parts = statement_parts(corp, const, auc, lo, seasons)
    return {
        "tier": "backtest", "supports": SUPPORTS["backtest"], "source": source,
        "population": {
            "sport": "nfl", "markets": ["receptions", "rush_attempts"], "side": "over",
            "benchmark": "the de-vigged DraftKings / FanDuel / BetMGM median close (p_bench)",
            "seasons": seasons, "out_of_sample_outcomes": n_all, "games": games,
            "held_out": {"rule": ("walk-forward: every season T is predicted with every fitted "
                                  "constant refit on seasons <= T-1 and features strictly as-of "
                                  "kickoff (research/walkforward.py)"),
                         "final_season": seasons[-1]},
            "replay_rows": None, "settled_props": None,
            "not_in_source": [
                "replay_rows: the walk-forward ledger is scratch and never committed; the source "
                "states only the outcomes scored against a close",
                "settled_props: settled outcomes without a close are not stated in the source"]},
        "seasons": per,
        "register_figure": {"lo": lo, "hi": hi, "n": n_all,
                            "text": f"{lo:+.4f} to {hi:+.4f} over {n_all:,}"},
        "superseded": [dict(x) for x in SUPERSEDED],
        "statement": " ".join(p["text"] for p in parts),
        "statement_parts": parts,
    }


def statement_parts(corp, const, auc, loss_lo, seasons):
    """The three clauses, each WORDED FROM ITS FIGURES, so each can come out
    the other way. -> [{clause, text, figures}].

    "Barely" is not a word typed here: it is said only when the close's best
    edge over a constant is under a quarter of the smallest season's Brier loss
    of the model to the close (`loss_lo`), i.e. small against the very gap the
    file reports. Otherwise the clause says the close beats the constant."""
    lo_s, hi_s = min(corp.values()), max(corp.values())
    if lo_s > 0.5:
        c1 = (f"Against the book close, most of the model's Brier loss is over-confidence: "
              f"{lo_s:.0%} to {hi_s:.0%} of it, season by season, is miscalibration.")
    elif hi_s < 0.5:
        c1 = (f"Against the book close, most of the model's Brier loss is NOT calibration: "
              f"miscalibration is only {lo_s:.0%} to {hi_s:.0%} of it.")
    else:
        c1 = (f"Against the book close, miscalibration is {lo_s:.0%} to {hi_s:.0%} of the "
              "model's Brier loss, the majority in some seasons and not in others.")
    worst_model = max(abs(c["model_recal_minus_constant"][0]) for c in const)
    close_beats = [c for c in const if c["close_minus_constant"][2] < 0]
    close_ties = [c for c in const if c["close_minus_constant"][2] >= 0]
    bits = []
    if close_beats:
        bits.append("the close beats that constant by "
                    + ", ".join(f"{-c['close_minus_constant'][0]:.4f} in {c['season']}"
                                for c in close_beats))
    if close_ties:
        bits.append("in " + ", ".join(str(c["season"]) for c in close_ties)
                    + " the close does not measurably beat it at all")
    edge = max([-c["close_minus_constant"][0] for c in const] + [0.0])
    tail = ("both barely beat a constant" if edge < loss_lo / 4 else
            f"the close beats a constant by up to {edge:.4f}, which is not small against the "
            f"model's {loss_lo:.4f} loss")
    c2 = (f"Corrected for that, the model scores within {worst_model:.4f} Brier of a constant "
          f"base-rate forecast, and " + "; ".join(bits) + f" - {tail}.")
    est, lo, hi = auc
    if hi < 0:
        c3 = (f"And the close still orders outcomes better: the model's AUC is {-est:.3f} lower "
              f"[{-hi:.3f}, {-lo:.3f}], pooled {seasons[0]}-{seasons[-1]}.")
    elif lo > 0:
        c3 = f"And the model orders outcomes better than the close: AUC {est:+.3f} [{lo:+.3f}, {hi:+.3f}]."
    else:
        c3 = (f"And neither orders outcomes measurably better: AUC difference {est:+.3f} "
              f"[{lo:+.3f}, {hi:+.3f}].")
    return [
        {"clause": "over_confidence", "text": c1,
         "figures": {"calibration_share": {str(k): v for k, v in sorted(corp.items())}}},
        {"clause": "constant_base_rate", "text": c2,
         "figures": {"by_season": [{"season": c["season"],
                                    "model_recal_minus_constant": list(c["model_recal_minus_constant"]),
                                    "close_minus_constant": list(c["close_minus_constant"])}
                                   for c in const]}},
        {"clause": "ordering", "text": c3,
         "figures": {"auc_model_minus_close": [est, lo, hi]}},
    ]
