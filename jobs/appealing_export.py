"""What this week's market is pricing, beside our own numbers, ranked (a-70).

    python -m jobs.appealing_export --check --board D:/x/board_export     # build, gate; write nothing
    python -m jobs.appealing_export --write --dest D:/scratch/board --board D:/x/board_export

    Scheduled: `board_read --tick` runs `publish()` after the record step, into the
    Board's own tree, and the next tick's Board upload ships it. Non-fatal.

    board/nfl/appealing.json    kind board_appealing

ONE ROW PER PRICED OUTCOME OF THE WEEK WHOSE GAME HAS NOT KICKED OFF, from four
parts. Each part is built on its own; one that fails or has nothing is listed in
`parts` with the reason and the others still publish.

    game_exchange   Kalshi KXNFLGAME / KXNFLSPREAD / KXNFLTOTAL rungs, against the
                    game model (c-28's moneyline, c-30's key-number margin, c-31's
                    total) priced at the rung's own line
    game_books      the benchmark books' moneyline, spread and total (Odds API
                    forward capture), against the same three objects
    prop_books      the Board's latest read: the books' main line against the prop
                    model's fit, exactly as the Board published it
    prop_exchange   Kalshi's rung on that same claim and line, against that same fit

A row states a claim, the market's price, our number, the difference, what it
costs to take, the depth where an exchange shows one, the gap band, and the key
of the record of the model that produced the number. NOTHING IS A PICK: no row
carries a verdict about itself. `side` is arithmetic - which side of the claim
the model's number is above the market's on - and is what `cost` is computed for.

THE RANKING IS BY DIFFERENCE NET OF COST, and that is a sort key, not a finding.
The raw difference is known to be the wrong one: c-24 measured the model ordering
outcomes worse than the market in every population. So `ordering` carries what
has been measured about disagreement size - the model's AUC against the close,
the walk-forward return by gap band, and the Board's own graded leans by band -
each as figures with a verdict COMPUTED from them, and every row points at it
(`ordering_note`). Ties sort by cost, cheapest first, then by row id.

COST IS NEVER A FLAT NUMBER. An exchange rung costs half its quoted spread plus
the taker fee for its series (core.fees, whose multipliers come from Kalshi's
`/series` fee_type), computed on a TICKET-contract order and divided. A book line
costs the side's own vig: its offered implied probability minus its de-vigged
one, the median across the books quoting that line - which scales with price
(c-22: 4.6-6.2c deep in the money).

THE GAME MODEL IS game_export's, AND IS CHECKED AGAINST WHAT IS PUBLISHED. The
objects come from `game_matchup.prepare` / `game_objects`; a game whose
probability does not round to the published game/nfl/forecast.json's is dropped.
The three game records are READ from the published files, never restated.

Reads `market_log.db` with `mode=ro`, the Board's tree and the web tree. Writes
through `export_web.sync_keys(dest, files, [])`: it owns no prefix and deletes
nothing. A build with no rows at all writes nothing, so the previous file stays.
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config                                           # noqa: E402
from core import board as B                             # noqa: E402
from core import fees as F                              # noqa: E402
from core.stats import wilson                           # noqa: E402

SPORT = "nfl"
KEY = f"board/{SPORT}/appealing.json"
KIND = "board_appealing"
PARTS = ("game_exchange", "game_books", "prop_books", "prop_exchange")
TICKET = 100                 # contracts the exchange fee is computed on, then divided
MAX_QUOTE_AGE_S = 1800       # an exchange quote older than this is not a price
MAX_BOOK_AGE_S = 36 * 3600   # the forward capture is snapshot-scheduled, not polled
MAX_BOARD_AGE_S = 6 * 3600   # the Board's latest read must be this recent
GAME_FILES = {"moneyline": "game/nfl/record.json", "spread": "game/nfl/record_spread.json",
              "total": "game/nfl/record_total.json"}
FORECAST_FILE = "game/nfl/forecast.json"
PUBLISHED_RECORD = "record/nfl/published.json"
BANDS_FILE = "research/results/board_bands.json"
BOOK_MARKETS = {"h2h": "moneyline", "spreads": "spread", "totals": "total"}
ORDERING_NOTE = "disagreement_size"


class NothingToRank(RuntimeError):
    """No part produced a row. Nothing is written and the previous file stays."""


def r4(x):
    return None if x is None else round(float(x), 4) + 0.0


def pp(x):
    """A probability difference in points, 2 dp, never -0.0."""
    return None if x is None else round(float(x) * 100.0, 2) + 0.0


def iso(ts):
    return B.iso(ts)


def ro(db=None):
    import sqlite3
    return sqlite3.connect(f"file:{db or config.DB_PATH}?mode=ro", uri=True)


def load_json(path):
    if not path or not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


# =============================================================================
# pure: cost, the row, the ranking
# =============================================================================

def exchange_cost(bid, ask, side, market_id, ticket=None):
    """Half the quoted spread plus the taker fee, per contract, in points.
    `side` is `claim` (buy YES at the ask) or `against` (buy NO at 1 - bid)."""
    ticket = TICKET if ticket is None else ticket
    price = ask if side == "claim" else 1.0 - bid
    _maker, taker = F.series_multiplier(market_id)
    fee = F.fee_per_contract(round(price, 4), ticket, "taker", taker)
    half = (ask - bid) / 2.0
    return {"total_pp": pp(half + fee), "half_spread_pp": pp(half), "fee_pp": pp(fee),
            "book_margin_pp": None, "basis": "exchange_touch_taker", "ticket": ticket,
            "fee_series": F.series_of(market_id), "fee_multiplier": float(taker),
            "price": r4(price)}


def book_cost(quotes, side):
    """The side's own vig: offered implied probability minus the de-vigged one,
    the median across the books quoting both sides. `quotes` is
    [{"over": american, "under": american}] with `over` the claim's side.
    -> (cost dict, median offered price) or (None, None)."""
    mine, other = ("over", "under") if side == "claim" else ("under", "over")
    margins, prices = [], []
    for q in quotes:
        a, b = B.american_to_prob(q.get(mine)), B.american_to_prob(q.get(other))
        if a is None or b is None:
            continue
        margins.append(a - a / (a + b))
        prices.append(a)
    if not margins:
        return None, None
    m = statistics.median(margins)
    return ({"total_pp": pp(m), "half_spread_pp": None, "fee_pp": None,
             "book_margin_pp": pp(m), "basis": "book_devig", "ticket": None,
             "fee_series": None, "fee_multiplier": None,
             "price": r4(statistics.median(prices))}, len(margins))


def band_of(diff_pp):
    b = B.band_for(diff_pp)
    return None if b is None else {"lo_pp": b[0], "hi_pp": b[1]}


def band_key(b):
    return None if b is None else (b["lo_pp"], b["hi_pp"])


def make_row(*, row_id, market, venue, game_id, kickoff, claim, p_market, market_price,
             p_model, model, cost_for, depth_for=None):
    """One row. `cost_for(side)` -> cost dict or None; a claim with no cost on the
    side the difference falls on is not a row (returns None)."""
    diff = p_model - p_market
    side = "claim" if diff >= 0 else "against"
    cost = cost_for(side)
    if cost is None:
        return None
    d = pp(diff)
    return {"row_id": row_id, "market": market, "venue": venue, "game_id": game_id,
            "kickoff": kickoff, "claim": claim,
            "market_price": dict(market_price, p=r4(p_market)),
            "model": dict(model, p=r4(p_model)),
            "difference_pp": d, "side": side, "cost": cost,
            "net_pp": round(abs(d) - cost["total_pp"], 2) + 0.0,
            "depth": depth_for(side) if depth_for else None,
            "gap_band": band_of(d), "flagged": False,
            "record": "prop" if market == "prop" else market,
            "ordering_note": ORDERING_NOTE}


def rank(rows):
    """Net of cost, largest first; ties to the cheaper cost, then the row id - a
    fixed rule, so two builds over the same rows cannot order them differently."""
    out = sorted(rows, key=lambda r: (-r["net_pp"], r["cost"]["total_pp"], r["row_id"]))
    for i, r in enumerate(out, 1):
        r["rank"] = i
    return out


# =============================================================================
# pure: the gap bands and what is measured about disagreement size
# =============================================================================

def ledger_bands(leans):
    """The Board's own graded leans by |gap| band: rate cleared with its Wilson
    interval, the mean break-even of the prices they stood at, and `flagged` -
    COMPUTED: the whole interval sits below that break-even. A band with no graded
    lean is listed with nulls and is never flagged."""
    by = defaultdict(lambda: {"k": 0, "n": 0, "be": [], "weeks": set()})
    for x in leans:
        if x.get("result") not in ("cleared", "missed"):
            continue
        b = B.band_for(x.get("gap_pp"))
        if b is None:
            continue
        s = by[b]
        s["n"] += 1
        s["k"] += x["result"] == "cleared"
        s["weeks"].add((x["season"], x["week"]))
        if x.get("breakeven") is not None:
            s["be"].append(x["breakeven"])
    out = []
    for lo, hi in config.BOARD_GAP_BANDS:
        s = by.get((lo, hi))
        if not s:
            out.append({"lo_pp": lo, "hi_pp": hi, "graded": 0, "cleared": None, "rate": None,
                        "interval": None, "interval_method": "wilson_95", "weeks": 0,
                        "breakeven": None, "priced": 0, "flagged": False})
            continue
        ci = wilson(s["k"], s["n"])
        be = (sum(s["be"]) / len(s["be"])) if s["be"] else None
        out.append({"lo_pp": lo, "hi_pp": hi, "graded": s["n"], "cleared": s["k"],
                    "rate": r4(s["k"] / s["n"]), "interval": [r4(ci[0]), r4(ci[1])],
                    "interval_method": "wilson_95", "weeks": len(s["weeks"]),
                    "breakeven": r4(be), "priced": len(s["be"]),
                    "flagged": be is not None and ci[1] < be})
    return out


def walkforward_bands(bands_doc):
    """research/results/board_bands.json -> one row per (market, band), with where
    its return interval lies relative to zero."""
    out = []
    for key, s in sorted(((bands_doc or {}).get("bands") or {}).items()):
        market, band = key.split("|")
        lo, hi = (float(band[:-1]), None) if band.endswith("+") else map(float, band.split("-"))
        rl, rh = s["roi_ci"]
        out.append({"market": market, "lo_pp": lo, "hi_pp": hi, "n": s["n"], "games": s["games"],
                    "cleared": s["cleared"], "interval": s["ci"], "roi": s["roi"],
                    "roi_interval": s["roi_ci"],
                    "roi_verdict": "above" if rl > 0 else ("below" if rh < 0 else "contains_zero")})
    return out


def auc_words(auc):
    if auc is None:
        return None
    est, lo, hi = auc
    v = "below" if hi < 0 else ("above" if lo > 0 else "contains_zero")
    word = {"below": "worse than", "above": "better than", "contains_zero": "no better than"}[v]
    return {"estimate": est, "interval": [lo, hi], "verdict": v,
            "statement": f"The model orders outcomes {word} the closing price "
                         f"(AUC difference {est:+.3f}, interval {lo:+.3f} to {hi:+.3f})."}


def ordering_block(auc, wf, live):
    """What has been measured about the size of a disagreement, and the sentence a
    page prints beside a row - worded from these figures, so it can read the other
    way when they do. Three measurements, none pooled with another."""
    pos = [w for w in wf if w["roi_verdict"] == "above"]
    top = [w for w in wf if w["hi_pp"] is None]
    top_pos = [w for w in top if w["roi_verdict"] == "above"]
    parts = []
    a = auc_words(auc)
    if a:
        parts.append(a["statement"])
    if wf:
        parts.append(
            f"Walk-forward, {len(pos)} of {len(wf)} gap bands returned above zero with the "
            f"interval clear of it; of the {len(top)} largest-gap bands, {len(top_pos)} did.")
    graded = [b for b in live if b["graded"]]
    if graded:
        clear = [b for b in graded if b["breakeven"] is not None and b["interval"][0] > b["breakeven"]]
        parts.append(
            f"Of the {len(graded)} gap bands the Board has graded this season, {len(clear)} "
            f"cleared at a rate whose interval is above break-even.")
    larger_better = bool(top) and len(top_pos) == len(top)
    return {"id": ORDERING_NOTE,
            "larger_gap_has_meant_better": larger_better,
            "rule": ("true only when every largest-gap band's walk-forward return interval is "
                     "above zero"),
            "model_vs_close_auc": a,
            "walkforward_bands": wf,
            "walkforward_source": BANDS_FILE,
            "walkforward_scope": ("receptions and rush attempts, 2023 to 2025, the lean the Board "
                                  "would have shown at the close, at the price that stood"),
            "live_scope": ("the Board's published book-prop leans this season, graded; leans in "
                           "one game are not independent and the intervals do not allow for it"),
            "not_on_this_branch": ["docs/findings/open-line-move.md (c-32: movement toward the "
                                   "model by disagreement band) is on an unmerged branch and no "
                                   "figure of it is carried here"],
            "statement": " ".join(parts) if parts else None}


def apply_flags(rows, live):
    """A row is flagged when it is a BOOK PROP in a band the ledger flags - the only
    population that band record was measured on. Every other row carries the band
    and `flagged` false."""
    flagged = {(b["lo_pp"], b["hi_pp"]) for b in live if b["flagged"]}
    for r in rows:
        r["flagged"] = (r["market"] == "prop" and r["venue"] == "books"
                        and band_key(r["gap_band"]) in flagged)
    return rows


# =============================================================================
# the records - read, never restated
# =============================================================================

def game_records(web_dir):
    """-> ({market: record ref}, {market: reason}). The three published record
    files, as the matchup files copy them (jobs.game_matchup.numbers)."""
    from jobs import game_matchup as GM
    from jobs.game_export import compared
    out, missing = {}, {}
    for market, key in GAME_FILES.items():
        doc = load_json(os.path.join(web_dir, *key.split("/"))) if web_dir else None
        if doc is None:
            missing[market] = f"{key} is not in the web tree"
            continue
        if market == "moneyline":
            mc = doc["market_comparison"]
            beats = [{"id": b["id"], "d_brier": b["d_brier"], "compared": b["compared"]}
                     for b in doc["baselines"]]
            vs = {"d_brier": mc["d_brier"], "compared": compared(mc["d_brier"]["verdict"]),
                  "path": "market_comparison.d_brier"}
        else:
            beats = [{"id": b["id"], "d_brier": b["d_brier"], "compared": b["compared"]}
                     for b in doc["against_baselines"]]
            vs = {"d_brier": doc["vs_close"]["d_brier"], "compared": doc["vs_close"]["compared"],
                  "path": "vs_close.d_brier"}
        out[market] = {"file": key, "generated_at": doc["generated_at"], "beats": beats,
                       "beats_reason": None if beats else
                       "no simple baseline is registered for this market; see the record file",
                       "vs_close": dict(vs, display=GM.SHOW_CLOSE),
                       "published": None}
    return out, missing


def prop_record(pub):
    """The prop model's record: R15's walk-forward loss to the book close (the
    figure the Board's index carries) and the Board's own graded leans."""
    from jobs import board_read as BR
    v = BR.verdict()
    est, lo, hi = v["brier_gap"], v["lo"], v["hi"]
    verdict = "above" if lo > 0 else ("below" if hi < 0 else "contains_zero")
    from jobs.game_export import compared
    rec = (pub or {}).get("record") or {}
    return {"file": PUBLISHED_RECORD, "generated_at": None, "beats": [],
            "beats_reason": ("no baseline comparison for the prop model is held in a committed "
                             "result file, so none is carried"),
            "vs_close": {"d_brier": {"estimate": est, "interval": [lo, hi], "se": None,
                                     "verdict": verdict},
                         "compared": compared(verdict), "path": None, "display": True,
                         "register": v["id"], "season": v["season"], "games": v["games"],
                         "source": v["source"]},
            "published": {"graded": (pub or {}).get("n_graded"),
                          "published": (pub or {}).get("n_published"),
                          "cleared": rec.get("cleared"), "missed": rec.get("missed"),
                          "rate": rec.get("hit_rate"),
                          "breakeven": rec.get("breakeven")} if pub else None}


# =============================================================================
# reads
# =============================================================================

def current_week(con, now_ts):
    """(season, week, {game_id: game}) of the REG week holding the next kickoff."""
    from jobs import board_read as BR
    r = con.execute("SELECT season, week FROM nfl_games WHERE game_type='REG' "
                    "GROUP BY game_id HAVING MAX(kickoff_ts) > ? "
                    "ORDER BY MAX(kickoff_ts) LIMIT 1", (now_ts,)).fetchone()
    if r is None:
        return None, None, {}
    return r[0], r[1], BR.week_games(con, r[0], r[1])


def kalshi_markets(con, season, week, kinds):
    """Mapped Kalshi markets of the week: [(market_id, key parts, entity, line, game_id)]."""
    q = ("SELECT mo.market_id, o.key, o.entity_type, o.entity_id, o.stat, o.line, o.event_id "
         "FROM outcomes o JOIN market_outcome mo USING (outcome_id) WHERE o.sport='nfl' AND "
         "o.season=? AND o.week=? AND mo.venue='kalshi' AND o.entity_type IN (%s) "
         "AND o.side IN ('over','yes')" % ",".join("?" * len(kinds)))
    return con.execute(q, (season, week, *kinds)).fetchall()


def kalshi_quote(con, market_id, now_ts):
    """(ts, bid, ask) of the latest live quote at or before the build, or None."""
    return con.execute("SELECT ts, best_bid, best_ask FROM quotes WHERE venue='kalshi' AND "
                       "market_id=? AND ts<=? AND source='live' ORDER BY ts DESC LIMIT 1",
                       (market_id, now_ts)).fetchone()


def kalshi_depth(con, market_id, now_ts):
    """{buy_yes / buy_no: depth row} from the latest depth read, or {}."""
    rows = con.execute(
        "SELECT ts, side, touch_price, touch_size, vwap_100, vwap_500, vwap_1000 FROM "
        "market_depth WHERE venue='kalshi' AND market_id=? AND ts<=? AND ts>=? AND ts = "
        "(SELECT MAX(ts) FROM market_depth WHERE venue='kalshi' AND market_id=? AND ts<=?)",
        (market_id, now_ts, now_ts - MAX_QUOTE_AGE_S, market_id, now_ts)).fetchall()
    return {r[1]: r for r in rows}


def depth_block(depth, side):
    d = depth.get("buy_yes" if side == "claim" else "buy_no")
    if d is None:
        return None
    ts, _s, touch, size, v100, v500, v1000 = d
    return {"touch_price": r4(touch), "touch_size": round(float(size), 2),
            "fills_ticket": size >= TICKET, "vwap_100": r4(v100), "vwap_500": r4(v500),
            "vwap_1000": r4(v1000),
            "slip_100_pp": pp(v100 - touch) if v100 is not None else None,
            "read_at": iso(ts)}


def exchange_row(con, now_ts, counts, market_id, p_model, **kw):
    """A Kalshi rung -> row, or None with the reason counted."""
    q = kalshi_quote(con, market_id, now_ts)
    if q is None:
        counts["no quote"] += 1
        return None
    ts, bid, ask = q
    if now_ts - ts > MAX_QUOTE_AGE_S:
        counts["quote older than the age limit"] += 1
        return None
    if bid is None or ask is None or not (0 < bid < ask < 1):
        counts["book not two-sided"] += 1
        return None
    depth = kalshi_depth(con, market_id, now_ts)
    mid = (bid + ask) / 2.0
    return make_row(
        venue="kalshi", p_market=mid, p_model=p_model,
        market_price={"basis": "exchange_mid", "bid": r4(bid), "ask": r4(ask), "books": None,
                      "read_at": iso(ts), "instrument": market_id},
        cost_for=lambda side: exchange_cost(bid, ask, side, market_id),
        depth_for=lambda side: depth_block(depth, side), **kw)


# =============================================================================
# the parts
# =============================================================================

def game_state(now_ts, web_dir, log=print):
    """The game model for the forecast week, checked against the published
    forecast. -> {"P", "m", "forecast", "as_of"}; raises with the reason."""
    from jobs import game_matchup as GM
    from jobs import season_model as S
    from research import game_forecast as GF
    forecast = load_json(os.path.join(web_dir, *FORECAST_FILE.split("/"))) if web_dir else None
    if forecast is None:
        raise RuntimeError(f"{FORECAST_FILE} is not in the web tree, so there is no published "
                           "game forecast to price a rung against")
    con = S.market_log_ro()
    try:
        games, _groupings, versions = S.load(con)
        walk = GF.Walk(games, S.game_losses(games, S.grid()), mov=True)
        year = max(g["season"] for g in games if g["game_type"] == "REG")
        m = {"games": games, "versions": versions, "walk": walk, "year": year}
        if forecast["season"] != year:
            raise RuntimeError(f"the published forecast is for {forecast['season']}, the store's "
                               f"season is {year}")
        P = GM.prepare(m, forecast["week"], now_ts, con=con)
    finally:
        con.close()
    return {"P": P, "m": m, "forecast": forecast}


def r1(x):
    return None if x is None else round(float(x), 1) + 0.0


def game_models(gs, game_ids, counts, web_dir=None):
    """{game_id: (g, fc, km, tot, tot_why)} for games the published forecast holds
    AND whose probability here rounds to the published one. The spread and total
    objects are kept only where the game's PUBLISHED matchup file carries that
    number and this build reproduces it: a rung priced off a total the matchup
    page says is withheld would be a figure the site itself contradicts."""
    from jobs import game_matchup as GM
    fc_by = {r["game_id"]: r for r in gs["forecast"]["games"]}
    out = {}
    for gid in game_ids:
        row = fc_by.get(gid)
        if row is None:
            counts["game not in the published forecast"] += 1
            continue
        _i, g, fc, km, tot, _f, why = GM.game_objects(gs["P"], gs["m"], gid,
                                                      gs["forecast"]["as_of"]["instant"])
        if r4(fc.p_home) != row["p_home_win"]:
            counts["model differs from the published forecast"] += 1
            continue
        mu = load_json(os.path.join(web_dir, *f"{GM.MATCHUP_DIR}{gid}.json".split("/")))             if web_dir else None
        nums = (mu or {}).get("numbers")
        if nums is None:
            counts["no published matchup: spread and total withheld"] += 1
            km = tot = None
        else:
            pub_m = nums["spread"]["model"]["home_margin_mean"]
            if km is not None and pub_m is None:
                counts["spread withheld by the published matchup"] += 1
                km = None
            elif km is not None and r1(km.margin_mean()) != pub_m:
                counts["spread differs from the published matchup"] += 1
                km = None
            pub_t = nums["total"]["model"]["mean"]
            if tot is not None and pub_t is None:
                counts["total withheld by the published matchup"] += 1
                tot = None
            elif tot is not None and r1(tot.mean()) != pub_t:
                counts["total differs from the published matchup"] += 1
                tot = None
        out[gid] = (g, fc, km, tot, why)
    return out


def part_game_exchange(con, now_ts, season, week, games, models, counts):
    rows = []
    as_of = iso(now_ts)
    for market_id, key, etype, eid, _stat, line, gid in kalshi_markets(
            con, season, week, ("team", "game")):
        kind = key.split("|")[3]
        g = games.get(gid)
        if kind not in GAME_FILES or g is None or g["kickoff_ts"] <= now_ts:
            counts["not a pre-kickoff moneyline, spread or total"] += 1
            continue
        if gid not in models:
            counts["no checked game model for the game"] += 1
            continue
        _g, fc, km, tot, _why = models[gid]
        if kind == "moneyline":
            p = fc.p_home if eid == g["home"] else 1.0 - fc.p_home
            claim = {"text": f"{eid} wins", "subject_type": "team", "subject": eid,
                     "name": eid, "stat": None, "line": None, "direction": "wins"}
            model = "game_moneyline"
        elif kind == "spread":
            if km is None:
                counts["spread model withheld"] += 1
                continue
            if eid not in (g["home"], g["away"]):
                counts["team is not in the game"] += 1
                continue
            p = km.prob_team_by_over(eid, float(line))
            claim = {"text": f"{eid} wins by more than {line:g}", "subject_type": "team",
                     "subject": eid, "name": eid, "stat": "margin", "line": float(line),
                     "direction": "by_more_than"}
            model = "game_spread"
        else:
            if tot is None:
                counts["total model withheld"] += 1
                continue
            p = tot.prob_total_over(float(line))
            claim = {"text": f"{g['away']} at {g['home']}: more than {line:g} points",
                     "subject_type": "game", "subject": gid, "name": f"{g['away']} at {g['home']}",
                     "stat": "total_points", "line": float(line), "direction": "over"}
            model = "game_total"
        row = exchange_row(con, now_ts, counts, market_id, p,
                           row_id=f"kalshi:{market_id}", market=kind, game_id=gid,
                           kickoff=iso(g["kickoff_ts"]), claim=claim,
                           model={"model": model, "as_of": as_of})
        if row:
            rows.append(row)
    return rows


def book_snapshots(con, eid, now_ts, books):
    """{book: [(market key, outcome name, line, american price, read_at)]} from each
    benchmark book's LATEST snapshot of the event. A book with no snapshot inside
    the age limit contributes nothing."""
    out = {}
    for book in books:
        rows = con.execute(
            "SELECT market_id, line, last, ts, source_ts FROM quotes WHERE venue=? AND event_id=? "
            "AND ts<=? AND ts>=? AND market_type='game' AND source='live'",
            (f"oddsapi:{book}", eid, now_ts, now_ts - MAX_BOOK_AGE_S)).fetchall()
        if not rows:
            continue
        latest = max(r[3] for r in rows)
        for mid, line, price, ts, sts in rows:
            parts = (mid or "").split("|")
            if ts == latest and len(parts) == 4 and parts[1] in BOOK_MARKETS:
                out.setdefault(book, []).append((parts[1], parts[3], line, price, iso(sts or ts)))
    return out


def part_game_books(con, now_ts, games, models, counts):
    from jobs import board_read as BR
    from venues.mapping import team_abbr
    books = config.BOARD_BENCH_BOOKS
    rows = []
    as_of = iso(now_ts)
    for eid, g in sorted(BR.oddsapi_events(con, games).items()):
        gid = g["game_id"]
        if g["kickoff_ts"] <= now_ts:
            continue
        if gid not in models:
            counts["no checked game model for the game"] += 1
            continue
        _g, fc, km, tot, _why = models[gid]
        raw = book_snapshots(con, eid, now_ts, books)
        ladders = {k: defaultdict(dict) for k in BOOK_MARKETS}
        for book, quoted in raw.items():
            for mkey, name, line, price, read_at in quoted:
                if mkey == "totals":
                    side = {"Over": "over", "Under": "under"}.get(name)
                    lk = None if line is None else float(line)
                else:
                    t = team_abbr(name)
                    side = "over" if t == g["home"] else ("under" if t == g["away"] else None)
                    # a spread is keyed as the margin the HOME team must win by
                    lk = 0.0 if mkey == "h2h" else (
                        None if line is None else (-float(line) if side == "over" else float(line)))
                if side is None or lk is None:
                    counts["book quote not attributable to a side"] += 1
                    continue
                cell = ladders[mkey][lk].setdefault(book, {})
                cell[side] = price
                cell["read_at"] = read_at
        for mkey, market in BOOK_MARKETS.items():
            ladder = {k: v for k, v in ladders[mkey].items()}
            line = B.main_line(ladder, books)
            if line is None:
                counts[f"no two-way benchmark {market}"] += 1
                continue
            p_mkt, n = B.market_prob(ladder, line, books)
            quotes = [q for q in ladder[line].values() if "over" in q and "under" in q]
            read_at = max(q["read_at"] for q in quotes)
            if market == "moneyline":
                p = fc.p_home
                claim = {"text": f"{g['home']} wins", "subject_type": "team", "subject": g["home"],
                         "name": g["home"], "stat": None, "line": None, "direction": "wins"}
                model = "game_moneyline"
            elif market == "spread":
                if km is None:
                    counts["spread model withheld"] += 1
                    continue
                p = km.prob_cover(line)
                # `line` is the margin the home team must win by; below zero it is
                # getting points, and "wins by more than -7" is not a sentence
                text = (f"{g['home']} wins by more than {line:g}" if line > 0 else
                        f"{g['home']} wins, or loses by fewer than {-line:g}")
                claim = {"text": text, "subject_type": "team",
                         "subject": g["home"], "name": g["home"], "stat": "margin", "line": line,
                         "direction": "by_more_than"}
                model = "game_spread"
            else:
                if tot is None:
                    counts["total model withheld"] += 1
                    continue
                p = tot.prob_over_push_void(line)
                claim = {"text": f"{g['away']} at {g['home']}: more than {line:g} points",
                         "subject_type": "game", "subject": gid,
                         "name": f"{g['away']} at {g['home']}", "stat": "total_points",
                         "line": line, "direction": "over"}
                model = "game_total"
            row = make_row(
                row_id=f"books:{gid}:{market}:{line:g}", market=market, venue="books",
                game_id=gid, kickoff=iso(g["kickoff_ts"]), claim=claim, p_market=p_mkt,
                market_price={"basis": "median_devig_mult", "bid": None, "ask": None, "books": n,
                              "read_at": read_at, "instrument": None},
                p_model=p, model={"model": model, "as_of": as_of},
                cost_for=lambda side, q=quotes: book_cost(q, side)[0])
            if row:
                rows.append(row)
            else:
                counts["no book price on the side of the difference"] += 1
    return rows


def board_latest(board_dir, season, week, now_ts):
    from jobs import board_read as BR
    prev, idx = BR.previous_rows(board_dir, season, week)
    if not idx or not idx.get("latest"):
        raise RuntimeError(f"the Board has no read for {season} week {week}")
    age = now_ts - BR.parse_iso(idx["latest"])
    if age > MAX_BOARD_AGE_S:
        raise RuntimeError(f"the Board's latest read ({idx['latest']}) is {age / 3600:.1f} h old")
    return prev, idx


def prop_claim(r):
    return {"text": f"{r['name']}: more than {r['line']:g} {r['market'].replace('_', ' ')}",
            "subject_type": "player", "subject": r["gsis_id"], "name": r["name"],
            "stat": r["market"], "line": float(r["line"]), "direction": "over"}


def part_prop_books(board_rows, idx, now_ts, counts):
    rows = []
    for r in board_rows:
        if r["status"] != B.UPCOMING or r["kickoff_ts"] <= now_ts or not r.get("is_main", True):
            counts["not an upcoming main-line row"] += 1
            continue
        if r["model_p_over"] is None or r["mkt_p_over"] is None:
            counts["no model number for the stat"] += 1
            continue
        quotes = [{"over": b["over"], "under": b["under"]} for b in r["books"]]
        row = make_row(
            row_id=f"books:{r['row_id']}", market="prop", venue="books", game_id=r["game_id"],
            kickoff=r["kickoff"], claim=prop_claim(r), p_market=r["mkt_p_over"],
            market_price={"basis": "median_devig_mult", "bid": None, "ask": None,
                          "books": r["mkt_books"],
                          "read_at": max((b["read_at"] for b in r["books"] if b.get("read_at")),
                                         default=idx["latest"]),
                          "instrument": None},
            p_model=r["model_p_over"],
            model={"model": "prop_baseline", "as_of": r.get("priced_at") or idx["latest"]},
            cost_for=lambda side, q=quotes: book_cost(q, side)[0])
        if row:
            rows.append(row)
        else:
            counts["no book price on the side of the difference"] += 1
    return rows


def part_prop_exchange(con, board_rows, idx, now_ts, season, week, counts):
    rows = []
    for r in board_rows:
        if r["status"] != B.UPCOMING or r["kickoff_ts"] <= now_ts or r["model_p_over"] is None:
            continue
        m = con.execute(
            "SELECT mo.market_id FROM outcomes o JOIN market_outcome mo USING (outcome_id) "
            "WHERE o.entity_id=? AND o.season=? AND o.week=? AND o.stat=? AND o.line=? "
            "AND o.side IN ('over','yes') AND mo.venue='kalshi' LIMIT 1",
            (r["gsis_id"], season, week, r["market"], float(r["line"]))).fetchone()
        if m is None:
            counts["no Kalshi rung on the Board's line"] += 1
            continue
        row = exchange_row(con, now_ts, counts, m[0], r["model_p_over"],
                           row_id=f"kalshi:{m[0]}", market="prop", game_id=r["game_id"],
                           kickoff=r["kickoff"], claim=prop_claim(r),
                           model={"model": "prop_baseline",
                                  "as_of": r.get("priced_at") or idx["latest"]})
        if row:
            rows.append(row)
    return rows


# =============================================================================
# assembly
# =============================================================================

def backtest_auc(log=print):
    """c-24's AUC difference, model minus close, from the committed findings the
    record's backtest tier parses - or None with the reason logged."""
    try:
        from core import record as R
        from jobs import record_export as RE
        text, src = RE.load_backtest()
        body = R.build_backtest(text, src)
        for part in body.get("statement_parts") or []:
            fig = (part.get("figures") or {}).get("auc_model_minus_close")
            if fig:
                return [float(x) for x in fig]
    except Exception as e:  # noqa: BLE001 - the figure is absent, the file still builds
        log(f"appealing: c-24's AUC figure could not be read ({type(e).__name__}: {e})")
    return None


def published_leans(board_dir, now_ts):
    from core import record as R
    from jobs import record_export as RE
    rows, src = RE.load_ledger(board_dir)
    if rows is None:
        return None
    return R.build_published(rows, now_ts, chain=None, source=src)


def build(now_ts, board_dir, web_dir, db=None, log=print, game_state_fn=None, auc=None):
    """-> {KEY: payload}. Raises NothingToRank when no part produced a row."""
    from jobs import export_web as E
    con = ro(db)
    parts, rows = [], []
    try:
        season, week, games = current_week(con, now_ts)
        if season is None:
            raise NothingToRank("no regular-season game is left to kick off")
        pub = published_leans(board_dir, now_ts) if board_dir else None
        live = ledger_bands((pub or {}).get("leans") or [])
        records, missing = game_records(web_dir)
        records["prop"] = prop_record(pub)

        def run(pid, fn):
            counts = Counter()
            try:
                got = fn(counts)
                parts.append({"id": pid, "rows": len(got), "reason": None if got else
                              "no priced outcome survived", "dropped": dict(sorted(counts.items()))})
                rows.extend(got)
            except Exception as e:  # noqa: BLE001 - that part's failure is that part's
                parts.append({"id": pid, "rows": 0, "reason": f"{type(e).__name__}: {e}",
                              "dropped": dict(sorted(counts.items()))})

        models, gs_err, mcounts = {}, None, Counter()
        try:
            if missing:
                raise RuntimeError("; ".join(sorted(missing.values())))
            gs = (game_state_fn or game_state)(now_ts, web_dir, log=log)
            models = game_models(gs, [g for g, v in games.items() if v["kickoff_ts"] > now_ts],
                                 mcounts, web_dir)
            if not models:
                raise RuntimeError(f"no game model passed the check ({dict(mcounts)})")
        except Exception as e:  # noqa: BLE001 - the game parts are absent with this reason
            gs_err = f"{type(e).__name__}: {e}"
        for pid, fn in (
                ("game_exchange", lambda c: part_game_exchange(con, now_ts, season, week, games,
                                                               models, c)),
                ("game_books", lambda c: part_game_books(con, now_ts, games, models, c))):
            if gs_err:
                parts.append({"id": pid, "rows": 0, "reason": gs_err,
                              "dropped": dict(sorted(mcounts.items()))})
            else:
                run(pid, fn)
                for k, n in mcounts.items():        # games, not rungs
                    parts[-1]["dropped"][f"games: {k}"] = n
        board = {}

        def board_rows():
            if "v" not in board:
                if not board_dir:
                    raise RuntimeError("no Board tree was given")
                board["v"] = board_latest(board_dir, season, week, now_ts)
            return board["v"]
        run("prop_books", lambda c: part_prop_books(*board_rows(), now_ts, c))
        run("prop_exchange", lambda c: part_prop_exchange(con, *board_rows(), now_ts, season,
                                                         week, c))
    finally:
        con.close()
    if not rows:
        raise NothingToRank("no part produced a row: " + "; ".join(
            f"{p['id']}: {p['reason']}" for p in parts))
    seen = Counter(r["row_id"] for r in rows)
    dup = sorted(k for k, n in seen.items() if n > 1)
    if dup:
        raise RuntimeError(f"{len(dup)} row id(s) appear twice, e.g. {dup[0]}")
    rows = rank(apply_flags(rows, live))
    wf = walkforward_bands(load_json(os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), *BANDS_FILE.split("/"))))
    auc = backtest_auc(log) if auc is None else auc
    used = {r["record"] for r in rows}
    body = {
        "season": season, "week": week,
        "as_of": {"instant": iso(now_ts),
                  "kickoffs_after": iso(now_ts)},
        "ranking": {"by": "net_pp", "order": "descending",
                    "ties": ["cost.total_pp ascending", "row_id ascending"],
                    "is_a_finding": False, "ordering_note": ORDERING_NOTE},
        "ordering": ordering_block(auc, wf, live),
        "gap_bands": live,
        "gap_bands_scope": {"population": "the Board's published book-prop leans, graded",
                            "flag_rule": ("a band is flagged when the whole 95% interval of its "
                                          "cleared rate is below the mean break-even of the prices "
                                          "its leans stood at"),
                            "flag_applies_to": "book prop rows only",
                            "source": PUBLISHED_RECORD},
        "records": {k: v for k, v in records.items() if k in used},
        "parts": parts,
        "rows": rows,
        "not_covered": [
            "Polymarket: no fee schedule is held in this repo, so no cost can be stated.",
            "Kalshi prop rungs other than the one on the Board's main line: the prop model's "
            "number is published per main line, not per rung.",
            "Games that have kicked off: an in-game price is not compared with a pre-game "
            "number.",
            "Receiving yards: the Board carries no model number for it."],
        "definitions": {
            "difference_pp": "our number minus the market's, on the claim as worded, in points",
            "side": "claim when our number is at or above the market's, against when below; "
                    "cost and depth are for that side",
            "cost": "exchange: half the quoted spread plus the taker fee for the series on a "
                    f"{TICKET}-contract order, per contract. Books: the side's offered implied "
                    "probability minus its de-vigged probability, the median across the books "
                    "quoting the line",
            "net_pp": "the size of the difference minus the cost, in points",
            "market_price": "exchange: the midpoint of the best bid and ask, never de-vigged. "
                            "Books: the median multiplicative de-vig across DraftKings, FanDuel "
                            "and BetMGM at the main line",
            "depth": "exchange only: the size at the touch on that side and the average price "
                     "to fill 100, 500 and 1,000 contracts; slip_100_pp is that average for 100 "
                     "minus the touch",
            "gap_band": "the band the size of the difference falls in; null below the first band"},
    }
    payload = {KEY: {**E.envelope(KIND, E.iso(), SPORT), **body}}
    return payload


def gate(files):
    from jobs import export_web as E
    from jobs import source_registry as SR
    E.validate_contract(files)
    SR.require_declared(files)
    doc = files[KEY]
    rows = doc["rows"]
    if [r["rank"] for r in rows] != list(range(1, len(rows) + 1)):
        raise RuntimeError("ranks are not 1..n in file order")
    if rows != rank([dict(r) for r in rows]):
        raise RuntimeError("rows are not in ranking order")
    for r in rows:
        if round(abs(r["difference_pp"]) - r["cost"]["total_pp"], 2) + 0.0 != r["net_pp"]:
            raise RuntimeError(f"{r['row_id']}: net_pp is not |difference| minus cost")
        if r["record"] not in doc["records"]:
            raise RuntimeError(f"{r['row_id']}: record {r['record']!r} is not in the file")
    return f"{KEY}: {len(rows)} rows, ranked, every row's record present"


def publish(board_dir, dest=None, web_dir=None, now_ts=None, log=print, db=None):
    """What the Board tick runs. NEVER RAISES: a failure is logged and listed and
    the previously written file stays exactly where it was. -> summary."""
    now_ts = time.time() if now_ts is None else now_ts
    out = {"built": [], "failed": [], "written": 0, "rows": 0, "parts": {}}
    try:
        from jobs import export_web as E
        dest = dest or board_dir
        web_dir = web_dir or getattr(config, "WEB_EXPORT_DIR", None) or os.getenv("WEB_EXPORT_DIR")
        files = build(now_ts, board_dir, web_dir, db=db, log=log)
        log("appealing: " + gate(files))
        out["built"] = sorted(files)
        out["rows"] = len(files[KEY]["rows"])
        out["parts"] = {p["id"]: p["rows"] for p in files[KEY]["parts"]}
        out["written"], _deleted = E.sync_keys(dest, files, [])
    except (Exception, SystemExit) as e:  # noqa: BLE001 - the step failed, not the tick
        out["failed"].append({"file": KEY, "error": f"{type(e).__name__}: {e}"})
    for f in out["failed"]:
        log(f"!!! APPEALING FILE FAILED ({f['file']}): {f['error']} - the previously written "
            "file is left in place; the Board tick is unaffected")
    log(f"appealing: built {out['built']} rows {out['rows']} parts {out['parts']} "
        f"wrote {out['written']} failed {len(out['failed'])}")
    return out


def summary(doc):
    lines = [f"{KEY}: {doc['season']} week {doc['week']}, {len(doc['rows'])} rows as of "
             f"{doc['as_of']['instant']}"]
    for p in doc["parts"]:
        lines.append(f"  {p['id']:<14} {p['rows']:>4} rows"
                     + (f"  ({p['reason']})" if p["reason"] else "")
                     + (f"  dropped {p['dropped']}" if p["dropped"] else ""))
    for b in doc["gap_bands"]:
        lines.append(f"  band {b['lo_pp']:g}-{b['hi_pp'] if b['hi_pp'] is not None else ''}: "
                     f"{b['cleared']} of {b['graded']} cleared, interval {b['interval']}, "
                     f"break-even {b['breakeven']}, flagged {b['flagged']}")
    lines.append("  ordering: " + str(doc["ordering"]["statement"]))
    for r in doc["rows"][:8]:
        lines.append(f"  #{r['rank']:<3} net {r['net_pp']:+6.2f}  diff {r['difference_pp']:+6.2f}  "
                     f"cost {r['cost']['total_pp']:5.2f}  {r['venue']:<6} {r['market']:<9} "
                     f"{r['claim']['text']} [{r['side']}]")
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true", help="build and gate; write nothing")
    mode.add_argument("--write", action="store_true")
    ap.add_argument("--dest", help="the tree to write into (required with --write)")
    ap.add_argument("--board", help="the Board's tree (default BOARD_EXPORT_DIR)")
    ap.add_argument("--web", help="the web tree holding game/nfl/* (default WEB_EXPORT_DIR)")
    ap.add_argument("--now", type=float, help="unix seconds; default the wall clock")
    a = ap.parse_args(argv)
    from jobs import export_web as E
    E.assert_numeric_stack()
    board = a.board or getattr(config, "BOARD_EXPORT_DIR", None) or os.getenv("BOARD_EXPORT_DIR")
    web = a.web or getattr(config, "WEB_EXPORT_DIR", None) or os.getenv("WEB_EXPORT_DIR")
    now = time.time() if a.now is None else a.now
    if a.check:
        files = build(now, board, web)
        print(gate(files))
        print(summary(files[KEY]))
        return 0
    if not a.dest:
        ap.error("--write needs --dest")
    out = publish(board, dest=a.dest, web_dir=web, now_ts=now)
    return 1 if out["failed"] or not out["built"] else 0


if __name__ == "__main__":
    sys.exit(main())
