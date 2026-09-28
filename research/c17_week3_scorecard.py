"""c-17: how the Board's published leans did in one week (2026 wk03). A scratch
scorecard - four questions, aggregates and intervals only.

    python -m research.c17_week3_scorecard --board D:/calibrated-sports/data/board_export \
        --db D:/calibrated-sports/data/market_log.db --season 2026 --week 3

READ-ONLY. The store is opened `mode=ro`; the Board tree is read from disk and
never written. Nothing is published. Licence (BET_LIST_RESTRICTION): no per-bet
series, no equity curve - every figure printed is an aggregate over >= 1 game.

  Q1  hit rate on the graded leans, against the break-even rate implied by the
      price the Board published (the median benchmark-book American price on the
      lean side at publication, vig INCLUDED - core.board.american_to_prob).
  Q2  Brier(model) - Brier(close), scored as research/walkforward.py scores the
      register: over side, receptions and rush attempts, pushes out, y = actual >
      line, 2,000-draw game block bootstrap (research.sweep.common.boot), the
      close = median multiplicative de-vig across draftkings / fanduel / betmgm at
      each book's last snapshot at or before kickoff.
      TWO DEPARTURES, both forced and both printed:
        - outcome_close has NO 2026 rows (it is built from the historical
          backfill only), so the close is recomputed here from the forward
          capture in `quotes` with the Board's own loader and de-vig
          (jobs.board_read.load_quotes / ladder_at, core.board.market_prob).
        - the forward schedule snapshots at ~T-20 min, so the register's
          lead <= 15 min admits only a handful of games. Primary: lead <= 20.
          Secondary: lead <= 15, as registered.
      And one population difference that is NOT a departure but must be read:
      the leans are SELECTED on |model - market| >= threshold, so they are the
      rows where model and market disagree most. The comparable population to
      the register's unselected 14,857 is the week's main-line rows with a
      model price, scored alongside.
  Q3  calibration: model P(over) in tenths, realised over rate, n per band
      (empty bands printed as n=0), Wilson interval; and the lean-side view.
  Q4  the leans flagged line_moved / lean_changed after publication against the
      rest; and, over every graded lean, the ones whose market moved AGAINST the
      lean between publication and the close against the ones it did not.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sqlite3
import statistics

import config
from core import board as B
from jobs import board_read as BR
from research.sweep import common as SW

# The register, as published. Two versions exist and both are printed:
# the brief quotes the PRE-settlement-fix figures from
# calibrated-sports/docs/findings/three-gaps-closed-and-settlement-bug.md;
# CLAUDE.md carries the restatement after the 2026-09-17 settlement fix.
REGISTER_PREFIX = {2023: (0.0272, None, None), 2024: (0.0265, 0.0210, 0.0318),
                   2025: (0.0218, 0.0162, 0.0277)}
REGISTER_RESTATED = {2023: (0.0229, 0.0170, 0.0290), 2024: (0.0237, 0.0188, 0.0288),
                     2025: (0.0195, 0.0144, 0.0253)}
STATS = ("receptions", "rush_attempts")
LEAD_PRIMARY_MIN, LEAD_REGISTERED_MIN = 20.0, 15.0


def ro(path):
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)


def brier(p, y):
    return (p - y) ** 2


def fmt(res, pct=False):
    if res is None:
        return "not estimable (< 2 games or < 20 valid draws)"
    k = 100 if pct else 1
    u = "pp" if pct else ""
    s = (f"{k * res['est']:+.4f}{u} [{k * res['lo']:+.4f}, {k * res['hi']:+.4f}]  "
         f"n={res['n']} games={res['games']}")
    if res["games"] < 5:
        s += "  (< 5 games: NOT READ)"
    return s


def spans_zero(res):
    return res is not None and res["lo"] <= 0 <= res["hi"]


def latest_read(board, season, week, name=None):
    paths = sorted(glob.glob(os.path.join(board, "board", "nfl", str(season),
                                          f"wk{week:02d}", name or "read-*.json")))
    if not paths:
        raise SystemExit(f"no read files for {season} wk{week:02d} under {board}")
    with open(paths[-1], encoding="utf-8") as f:
        return paths[-1], json.load(f)


def ledger_events(board, season, week):
    import csv
    path = os.path.join(board, "board", "nfl", "ledger.csv")
    with open(path, encoding="utf-8") as f:
        rows = [r for r in csv.DictReader(f)
                if int(r["season"]) == season and int(r["week"]) == week]
    if not rows:
        raise SystemExit(f"ledger has no rows for {season} wk{week}")
    return rows


def closes(con, season, week, rows):
    """{(game_id, market, line, name): (p_close_over, lead_min, n_books)} at each
    game's kickoff, from the forward capture - the Board's loader and de-vig."""
    games = BR.week_games(con, season, week)
    events = BR.oddsapi_events(con, games)
    inv = {v: k for k, v in config.BOARD_MARKETS.items()}
    want = {(r["game_id"], r["market"], r["name"]) for r in rows}
    out = {}
    by_game = {}
    for eid, g in events.items():
        by_game.setdefault(g["game_id"], []).append(eid)
    for gid, eids in by_game.items():
        kick = games[gid]["kickoff_ts"]
        claims, snaps = BR.load_quotes(con, eids, kick, config.BOARD_BENCH_BOOKS)
        for (eid, mkey, name), cb in claims.items():
            market = config.BOARD_MARKETS[mkey]
            if (gid, market, name) not in want:
                continue
            lad = BR.ladder_at(cb, snaps, eid, upto_ts=kick)
            books_used = [b for b in cb if snaps.get((eid, b))]
            last = max((max(t for t in snaps[(eid, b)] if t <= kick) for b in books_used
                        if any(t <= kick for t in snaps[(eid, b)])), default=None)
            for line in lad:
                p, n = B.market_prob(lad, line)
                if p is not None:
                    out[(gid, market, float(line), name)] = (p, (kick - last) / 60.0, n)
    assert inv  # BOARD_MARKETS loaded
    return out


def wilson(k, n):
    return (None, None) if n == 0 else SW.wilson(k, n)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--board", required=True, help="the Board tree root (BOARD_EXPORT_DIR)")
    ap.add_argument("--db", required=True, help="market_log.db, opened mode=ro")
    ap.add_argument("--season", type=int, required=True)
    ap.add_argument("--week", type=int, required=True)
    ap.add_argument("--read", help="pin one read file by name (default: the latest)")
    a = ap.parse_args(argv)
    out = print

    read_path, read = latest_read(a.board, a.season, a.week, a.read)
    rows = read["rows"]
    led = ledger_events(a.board, a.season, a.week)
    pub = {e["lean_id"]: e for e in led if e["event"] == "published"}
    graded = {e["lean_id"]: e for e in led if e["event"] == "graded"}
    void = {e["lean_id"] for e in led if e["event"] == "void"}
    out(f"c-17 scorecard  {a.season} wk{a.week:02d}")
    out(f"  read: {os.path.basename(read_path)} ({len(rows)} rows); ledger: "
        f"{len(pub)} published, {len(graded)} graded, {len(void)} void, "
        f"{len(pub) - len(graded) - len(void)} neither (upcoming)")
    assert len(pub) >= len(graded) > 0, "no graded leans - nothing to score"

    by_row = {r["row_id"]: r for r in rows}
    con = ro(a.db)
    cl = closes(con, a.season, a.week, rows)

    # ---------------------------------------------------------------- leans
    leans = []
    for lid, g in graded.items():
        p = pub[lid]
        r = by_row.get(p["row_id"])
        assert r is not None and r.get("lean") == p["side"], f"graded lean {lid} not on the read"
        actual = float(g["actual"])
        line = float(p["line"])
        assert actual != line, "a graded lean on a push"
        y_over = 1.0 if actual > line else 0.0
        side_over = p["side"] == "over"
        won = g["result"] == "cleared"
        assert won == ((y_over == 1.0) == side_over), f"ledger result disagrees with actual on {lid}"
        c = cl.get((p["game_id"], p["market"], line, r["name"]))
        leans.append({
            "game": p["game_id"], "stat": p["market"], "line": line, "side_over": side_over,
            "y": y_over, "won": 1.0 if won else 0.0,
            "be": B.american_to_prob(float(p["price"])) if p["price"] else None,
            "model": float(p["model_p_over"]), "mkt_pub": float(p["mkt_p_over"]),
            "p_close": c[0] if c else None, "lead": c[1] if c else None,
            "moved": bool(r.get("line_moved_after_publication")),
            "changed": bool(r.get("lean_changed_after_publication")),
            "row": r})
    n = len(leans)
    games = len({x["game"] for x in leans})
    out(f"  graded leans scored: {n} over {games} games "
        f"({sum(x['stat'] == 'receptions' for x in leans)} receptions, "
        f"{sum(x['stat'] == 'rush_attempts' for x in leans)} rush attempts)")

    # ---------------------------------------------------------------- Q1
    out("\nQ1  HIT RATE AGAINST THE BREAK-EVEN OF THE PUBLISHED PRICE")
    no_price = [x for x in leans if x["be"] is None]
    priced = [x for x in leans if x["be"] is not None]
    k = int(sum(x["won"] for x in leans))
    out(f"  cleared {k} of {n} = {k / n:.4f}; Wilson (treats leans as independent) "
        f"[{SW.wilson(k, n)[0]:.4f}, {SW.wilson(k, n)[1]:.4f}]")
    hit = SW.boot(leans, lambda rs: statistics.fmean(x["won"] for x in rs))
    out(f"  hit rate, game block bootstrap:            {fmt(hit)}")
    out(f"  mean break-even (vig-inclusive, published price): "
        f"{statistics.fmean(x['be'] for x in priced):.4f}  (n={len(priced)}; "
        f"{len(no_price)} leans carry no price)")
    edge = SW.boot(priced, lambda rs: statistics.fmean(x["won"] - x["be"] for x in rs))
    out(f"  hit - break-even, ONE bootstrapped quantity: {fmt(edge, pct=True)}")
    out(f"  -> interval {'SPANS' if spans_zero(edge) else 'EXCLUDES'} zero")
    q1 = {"hit": hit, "edge": edge, "k": k, "n": n}

    # ---------------------------------------------------------------- Q2
    out("\nQ2  BRIER(model) - BRIER(close), register method (see docstring for departures)")
    # Unselected comparator: one main-line row per claim, graded, model priced.
    main_rows = []
    for r in rows:
        if (r["market"] in STATS and r.get("is_main") and r.get("model_p_over") is not None
                and r["status"] in ("cleared", "missed") and r.get("result")):
            actual, line = float(r["result"]["value"]), float(r["line"])
            if actual == line:
                continue
            c = cl.get((r["game_id"], r["market"], line, r["name"]))
            main_rows.append({"game": r["game_id"], "stat": r["market"], "y": 1.0 if actual > line else 0.0,
                              "model": float(r["model_p_over"]), "mkt_pub": float(r["mkt_p_over"]),
                              "p_close": c[0] if c else None, "lead": c[1] if c else None,
                              "lean": bool(r.get("lean"))})
    claims_main = len({(r["game_id"], r["market"], r["gsis_id"]) for r in rows
                       if r["market"] in STATS and r.get("is_main")})
    out(f"  main-line rows with a model price, graded, not a push: {len(main_rows)} "
        f"(of {claims_main} rec/rush claims on the read); {sum(x['lean'] for x in main_rows)} of them lean")

    def diff(rs, a, b):
        return SW.boot(rs, lambda s: statistics.fmean(brier(x[a], x["y"]) - brier(x[b], x["y"]) for x in s)
                       if s else None)

    q2 = {}
    for label, pop in (("LEANS (selected on disagreement)", leans),
                       ("MAIN LINE, unselected (the register's analogue)", main_rows)):
        out(f"  -- {label}")
        have = [x for x in pop if x["p_close"] is not None]
        leads = sorted(x["lead"] for x in have)
        out(f"     close found for {len(have)} of {len(pop)}; lead to kickoff (min) "
            f"p10 {leads[len(leads) // 10]:.1f} median {statistics.median(leads):.1f} max {leads[-1]:.1f}"
            if leads else "     no close found")
        for lead_cap in (LEAD_PRIMARY_MIN, LEAD_REGISTERED_MIN):
            s = [x for x in have if x["lead"] <= lead_cap]
            if not s:
                out(f"     lead <= {lead_cap:.0f}: empty")
                continue
            lv = {k2: statistics.fmean(brier(x[k2], x["y"]) for x in s) for k2 in ("model", "p_close", "mkt_pub")}
            out(f"     lead <= {lead_cap:.0f} min: n={len(s)} games={len({x['game'] for x in s})}  "
                f"Brier model {lv['model']:.4f}  close {lv['p_close']:.4f}  market-at-publication {lv['mkt_pub']:.4f}")
            d = diff(s, "model", "p_close")
            out(f"       model - close          {fmt(d)}")
            out(f"       model - mkt@publication {fmt(diff(s, 'model', 'mkt_pub'))}")
            for st in STATS:
                ss = [x for x in s if x["stat"] == st]
                out(f"       model - close | {st:13s} {fmt(diff(ss, 'model', 'p_close'))}")
            q2[(label, lead_cap)] = d
        # model vs the publication-time market on everything, no close needed
        out(f"     model - mkt@publication, all {len(pop)}: {fmt(diff(pop, 'model', 'mkt_pub'))}")

    out("  -- against the register (Brier(model) - Brier(book close), per season)")
    for name, reg in (("as quoted in the brief (PRE-fix, findings doc)", REGISTER_PREFIX),
                      ("restated after the 2026-09-17 settlement fix (CLAUDE.md)", REGISTER_RESTATED)):
        out(f"     {name}: " + "; ".join(
            f"{s} {e:+.4f}" + (f" [{lo:+.4f}, {hi:+.4f}]" if lo is not None else "")
            for s, (e, lo, hi) in reg.items()))
    for key, d in q2.items():
        if d is None:
            continue
        pts = [e for reg in (REGISTER_PREFIX, REGISTER_RESTATED) for e, _, _ in reg.values()]
        ivs = [(lo, hi) for reg in (REGISTER_PREFIX, REGISTER_RESTATED) for _, lo, hi in reg.values() if lo is not None]
        reg_in_week = all(d["lo"] <= e <= d["hi"] for e in pts)
        week_in_reg = [lo <= d["est"] <= hi for lo, hi in ivs]
        out(f"     {key[0]}, lead <= {key[1]:.0f}: week-3 interval [{d['lo']:+.4f}, {d['hi']:+.4f}] "
            f"{'CONTAINS' if reg_in_week else 'does NOT contain'} every register point estimate "
            f"({min(pts):+.4f}..{max(pts):+.4f}); week-3 point {d['est']:+.4f} lies inside "
            f"{sum(week_in_reg)} of {len(ivs)} published season intervals; "
            f"interval {'SPANS' if spans_zero(d) else 'EXCLUDES'} zero")

    # ---------------------------------------------------------------- Q3
    out("\nQ3  CALIBRATION (Wilson intervals treat rows as independent; the effective n is lower)")

    def bands(pop, key, yk, lo0=0.0, width=0.1, nb=10):
        for i in range(nb):
            lo, hi = lo0 + i * width, lo0 + (i + 1) * width
            s = [x for x in pop if lo <= x[key] < hi or (i == nb - 1 and x[key] == hi)]
            if not s:
                out(f"     [{lo:.1f}, {hi:.1f})  n=0   -")
                continue
            kk = int(sum(x[yk] for x in s))
            w = SW.wilson(kk, len(s))
            fc = statistics.fmean(x[key] for x in s)
            inside = w[0] <= fc <= w[1]
            out(f"     [{lo:.1f}, {hi:.1f})  n={len(s):<4d} forecast {fc:.3f}  realised {kk / len(s):.3f} "
                f"[{w[0]:.3f}, {w[1]:.3f}]  {'' if inside else 'forecast OUTSIDE'}")

    for label, pop in (("leans", leans), ("main line, unselected", main_rows)):
        out(f"  -- model P(over), {label}")
        bands(pop, "model", "y")
    for x in leans:
        x["p_side"] = x["model"] if x["side_over"] else 1 - x["model"]
    out("  -- leans, model probability of the LEAN side vs lean realised")
    bands(leans, "p_side", "won", lo0=0.5, width=0.1, nb=5)

    # ---------------------------------------------------------------- Q4
    out("\nQ4  DID LEANS THAT MOVED AGAINST US DO WORSE?")
    flagged = [x for x in leans if x["moved"] or x["changed"]]
    out(f"  flagged on the read: {sum(x['moved'] for x in leans)} line moved, "
        f"{sum(x['changed'] for x in leans)} lean changed after publication "
        f"(graded only; the read counts every published lean)")
    # direction of each line move: current main line of the claim vs the lean's line
    mains = {r["claim_id"]: float(r["line"]) for r in rows if r.get("is_main")}
    against = with_ = unknown = 0
    for x in flagged:
        if not x["moved"]:
            continue
        cur = mains.get(x["row"]["claim_id"])
        if cur is None:
            unknown += 1
        elif (cur > x["line"]) == x["side_over"]:
            with_ += 1          # line moved toward the lean's side: the market agreed
        else:
            against += 1
    out(f"  line moves: {against} against the lean, {with_} toward it, {unknown} unknown")
    rest = [x for x in leans if not (x["moved"] or x["changed"])]
    for lbl, s in (("flagged", flagged), ("not flagged", rest)):
        kk = int(sum(x["won"] for x in s))
        w = wilson(kk, len(s))
        out(f"    {lbl:12s} cleared {kk} of {len(s)}"
            + (f" = {kk / len(s):.3f} Wilson [{w[0]:.3f}, {w[1]:.3f}]" if s else ""))
    for x in leans:
        x["flag"] = 1.0 if (x["moved"] or x["changed"]) else 0.0

    def contrast(field, yk="won"):
        def f(rs):
            a1 = [x[yk] for x in rs if x[field] == 1.0]
            a0 = [x[yk] for x in rs if x[field] == 0.0]
            return statistics.fmean(a1) - statistics.fmean(a0) if a1 and a0 else None
        return f

    out(f"    flagged - not flagged, ONE bootstrapped contrast: {fmt(SW.boot(leans, contrast('flag')), pct=True)}")
    # every graded lean: did the market move against it from publication to close?
    have = [x for x in leans if x["p_close"] is not None and x["lead"] <= LEAD_PRIMARY_MIN]
    for x in have:
        drift = (x["p_close"] - x["mkt_pub"]) * (1 if x["side_over"] else -1)
        x["drift"] = drift
        x["against"] = 1.0 if drift < 0 else 0.0
    out(f"  market drift publication -> close, at the lean's own line (lead <= {LEAD_PRIMARY_MIN:.0f}): "
        f"{len(have)} of {n} leans have a close")
    if have:
        out(f"    mean drift toward the lean {100 * statistics.fmean(x['drift'] for x in have):+.2f}pp; "
            f"drift toward the lean, bootstrapped: "
            f"{fmt(SW.boot(have, lambda rs: statistics.fmean(x['drift'] for x in rs)), pct=True)}")
        ad = sorted(abs(x["drift"]) for x in have)
        out(f"    |drift|: {sum(d == 0 for d in ad)} exactly zero, median {100 * statistics.median(ad):.2f}pp, "
            f"p90 {100 * ad[int(0.9 * len(ad))]:.2f}pp, max {100 * ad[-1]:.2f}pp")
        for lbl, s in (("close moved AGAINST the lean", [x for x in have if x["against"] == 1.0]),
                       ("close moved with it / flat", [x for x in have if x["against"] == 0.0])):
            kk = int(sum(x["won"] for x in s))
            w = wilson(kk, len(s))
            out(f"    {lbl:30s} cleared {kk} of {len(s)}"
                + (f" = {kk / len(s):.3f} Wilson [{w[0]:.3f}, {w[1]:.3f}]" if s else ""))
        out(f"    against - with, ONE bootstrapped contrast: {fmt(SW.boot(have, contrast('against')), pct=True)}")
    con.close()
    return q1, q2


if __name__ == "__main__":
    main()
