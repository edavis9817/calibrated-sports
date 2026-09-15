"""Brief 022 H1 - determination lag. NFL week 1 only.

    python -m research.sweep.h1_settlement

Pre-registered in docs/briefs/022-preregistration.md (H1), committed ae3895b.

Between the moment an outcome is DETERMINED and the moment the market stops
trading, the correct price is 0 or 1. This measures what Kalshi quoted in that
window and whether crossing toward the truth was executable after the fee.

    D   YES on a counting prop: the play on which the cumulative count reaches k.
        YES on a total: the play on which combined score first exceeds L.
        Everything else (NO on props/totals, spreads, moneylines): last play.
    G   guard for replay review / feed delay: 0, 60, 120 (primary), 300 s.

Settlement truth and timing come from Kalshi's own finalized market objects
(`close_time` is the ACTUAL close - props close early when the event occurs -
and `settlement_ts`), fetched once from the free public endpoint and archived
raw under `kalshi_settled_022`. This module reads only that archive.
"""
import bisect
import glob
import gzip
import json
import os
import statistics
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import config  # noqa: E402
from core.fees import fee_per_contract, series_multiplier  # noqa: E402
from research.sweep import common as S  # noqa: E402

SERIES = ("KXNFLREC", "KXNFLRSHATT", "KXNFLTOTAL", "KXNFLSPREAD", "KXNFLGAME")
PROP_STAT = {"KXNFLREC": "receptions", "KXNFLRSHATT": "rush_attempts"}
GUARDS = (0, 60, 120, 300)
PRIMARY_G = 120
SIZES = (10, 100)
MAX_STALE = 660.0
DEPTH_WINDOW = 60.0
SAME_TOUCH = 0.005
KINDS = ("yes_midgame", "at_end")
# The population (week 1 search set, or week 2 holdout A) is chosen in
# `common` from SWEEP_POPULATION; this module never names a week itself.
REGISTRY = S.registry_path("h1")
SETTLED_VENUE = "kalshi_settled_022"
# Frozen in phase 1: a share of profitable markets is >= 0 by construction, so
# it is DESCRIPTIVE, never a test - in every population, so week 2 matches.
SHARE_ROLE = "descriptive"

# --- CFB (holdout B / population run) ----------------------------------------
CFB_SERIES = ("KXNCAAFGAME", "KXNCAAFSPREAD", "KXNCAAFTOTAL", "KXNCAAFTEAMTOTAL")
CFB_VENUE = "cfb_kalshi"
CFB_SETTLED_VENUE = "kalshi_settled_022_cfb"
CFB_DETERMINE_AT = 0.97
CFB_REGISTRY = os.path.join(S.ROOT, "research", "sweep", "results", "h1_cfb.jsonl")


# =============================================================================
# pure pieces
# =============================================================================

def iso_ts(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp() if s else None


def split_event_teams(suffix, abbr):
    """'26SEP13BUFHOU' -> ('BUF','HOU') resolved to nflverse abbreviations.
    Kalshi concatenates two codes with no separator; every split is tried and
    exactly one must resolve on both sides."""
    blob = suffix[7:]
    hits = []
    for i in range(2, len(blob) - 1):
        a, b = abbr(blob[:i]), abbr(blob[i:])
        if a and b and a != b:
            hits.append((a, b))
    if len(hits) != 1:
        raise ValueError(f"ambiguous or unresolvable team blob {suffix!r}: {hits}")
    return hits[0]


def event_day(suffix):
    return datetime.strptime(suffix[:7], "%y%b%d").date()


def prop_determination(event_times, k, game_end):
    """event_times: ascending wall-clock times of the player's counted plays.
    Returns (truth, D, kind)."""
    if len(event_times) >= k:
        d = event_times[k - 1]
        return 1.0, d, ("yes_midgame" if d < game_end else "at_end")
    return 0.0, game_end, "at_end"


def total_determination(score_path, line, game_end):
    """score_path: ascending [(t, combined_score_after_play)]."""
    for t, s in score_path:
        if s > line:
            return 1.0, t, ("yes_midgame" if t < game_end else "at_end")
    return 0.0, game_end, "at_end"


def truth_ask(truth, bid, ask):
    """The price of buying the side that is already true."""
    if truth == 1.0:
        return ask
    return None if bid is None else 1.0 - bid


def net_per_contract(ask, contracts, taker_m):
    if ask is None or not (0 < ask < 1):
        return None
    return 1.0 - ask - fee_per_contract(ask, contracts, "taker", taker_m)


def asof_quote(qts, q, t, close_ts, max_stale=MAX_STALE):
    """Last quote at or before t that is still a price: not stale, market open."""
    if close_ts is not None and t >= close_ts:
        return None, "closed"
    i = bisect.bisect_right(qts, t) - 1
    if i < 0:
        return None, "no quote"
    if t - qts[i] > max_stale:
        return None, "stale"
    return q[i], "ok"


def persistence(quotes_after, t0, truth, contracts, taker_m, close_ts, max_stale=MAX_STALE):
    """Seconds the quote-based net stays > 0 from t0 (lower bound) and why it ended.
    `quotes_after`: [(ts, bid, ask)] with ts > t0, ascending."""
    last_true = t0
    last_seen = t0
    for ts, bid, ask in quotes_after:
        if close_ts is not None and ts >= close_ts:
            return last_true - t0, "market closed"
        if ts - last_seen > max_stale:
            return last_true - t0, "quotes went stale"
        last_seen = ts
        n = net_per_contract(truth_ask(truth, bid, ask), contracts, taker_m)
        if n is None or n <= 0:
            return last_true - t0, "price moved"
        last_true = ts
    return last_true - t0, "quotes ended"


def parse_settled(records):
    """Raw /markets payloads -> {ticker: settlement fields}."""
    out = {}
    for rec in records:
        for m in (rec.get("payload") or {}).get("markets") or []:
            out[m["ticker"]] = {
                "status": m.get("status"), "result": m.get("result"),
                "close_ts": iso_ts(m.get("close_time")),
                "settle_ts": iso_ts(m.get("settlement_ts")),
                "floor_strike": m.get("floor_strike"), "strike_type": m.get("strike_type"),
                "yes_sub_title": m.get("yes_sub_title"), "event": m.get("event_ticker")}
    return out


def check_rung(series, market_id, outcome_line, side, stat, floor_strike):
    """Kalshi 'k+' == outcomes line k-0.5, over, on the series' stat. Raises."""
    if stat != PROP_STAT[series] or side != "over":
        raise AssertionError(f"{market_id}: stat/side {stat}/{side} does not match {series}")
    if floor_strike is None or abs(float(floor_strike) - float(outcome_line)) > 1e-9:
        raise AssertionError(f"{market_id}: floor_strike {floor_strike} != outcome line {outcome_line}")
    k = int(round(float(outcome_line) + 0.5))
    if not market_id.endswith(f"-{k}"):
        raise AssertionError(f"{market_id}: ticker rung != k={k}")
    return k


# =============================================================================
# loading (week 1 only, fenced)
# =============================================================================

def load_settlements(raw_dir=None):
    raw_dir = raw_dir or os.path.join(config.RAW_DIR, SETTLED_VENUE)
    recs = []
    for fn in sorted(glob.glob(os.path.join(raw_dir, "*", "*.jsonl.gz"))):
        with gzip.open(fn, "rt", encoding="utf-8") as f:
            recs += [json.loads(line) for line in f]
    out = parse_settled(recs)
    for t in out:
        S.assert_search_set(t, 0)
    return out


def load_games(c):
    g = {}
    for gid, kick, home, away, hs, as_ in c.execute(
            "SELECT game_id, kickoff_ts, home_team, away_team, home_score, away_score "
            "FROM nfl_games WHERE season=? AND week=? ORDER BY data_version", (S.SEASON, S.WEEK)):
        g[gid] = {"game": gid, "kick": kick, "home": home, "away": away, "hs": hs, "as": as_}
    return g


def load_pbp(games):
    import polars as pl
    f = sorted(glob.glob(os.path.join(config.RAW_DIR, "nflverse", "*",
                                      f"play_by_play_{S.SEASON}.parquet")))[-1]
    df = (pl.read_parquet(f, columns=["game_id", "week", "play_id", "time_of_day", "complete_pass",
                                      "receiver_player_id", "rush_attempt", "rusher_player_id",
                                      "total_home_score", "total_away_score"])
          .filter(pl.col("week") == S.WEEK).sort(["game_id", "play_id"]))
    per = {}
    for (gid,), g in df.group_by("game_id", maintain_order=True):
        if gid not in games:
            continue
        rows = g.to_dicts()
        # A play without a wall clock takes the NEXT known clock: later, so the
        # determination is never dated earlier than it could have been known.
        nxt = None
        for r in reversed(rows):
            t = iso_ts(r["time_of_day"])
            nxt = t if t is not None else nxt
            r["t"] = nxt
        rows = [r for r in rows if r["t"] is not None]
        end = max(r["t"] for r in rows)
        rec, car = defaultdict(list), defaultdict(list)
        path = []
        for r in rows:
            if r["complete_pass"] == 1 and r["receiver_player_id"]:
                rec[r["receiver_player_id"]].append(r["t"])
            if r["rush_attempt"] == 1 and r["rusher_player_id"]:
                car[r["rusher_player_id"]].append(r["t"])
            if r["total_home_score"] is not None:
                path.append((r["t"], r["total_home_score"] + r["total_away_score"]))
        per[gid] = {"end": end, "receptions": rec, "rush_attempts": car, "score_path": path}
    return per


def verify_counts(c, pbp):
    """pbp counts against nfl_player_week. Mismatched player-stats are excluded."""
    pw = {}
    for g, rec, car in c.execute("SELECT gsis_id, receptions, carries FROM nfl_player_week "
                                 "WHERE season=? AND week=? AND season_type='REG' ORDER BY data_version",
                                 (S.SEASON, S.WEEK)):
        pw[g] = {"receptions": rec or 0, "rush_attempts": car or 0}
    bad = set()
    for gid, p in pbp.items():
        for stat in ("receptions", "rush_attempts"):
            for pid, ts in p[stat].items():
                if len(ts) != pw.get(pid, {}).get(stat, 0):
                    bad.add((pid, stat))
    return bad, pw


def map_events(c, games):
    from venues.mapping import team_abbr
    by_day = defaultdict(list)
    for g in games.values():
        by_day[datetime.fromtimestamp(g["kick"], S.ET).date()].append(g)
    out = {}
    sql, params = S.week1_filter_sql("event_id")
    for (eid,) in c.execute(f"SELECT DISTINCT event_id FROM markets WHERE venue='kalshi' AND {sql}", params):
        suffix = eid.split("-", 1)[1]
        try:
            a, b = split_event_teams(suffix, team_abbr)
            day = event_day(suffix)
        except ValueError:
            continue
        for dd in (0, -1, 1):
            for g in by_day.get(day + timedelta(days=dd), []):
                if {g["home"], g["away"]} == {a, b}:
                    out[suffix] = g["game"]
    return out


def load_markets(c, games, settled, pbp, bad_counts):
    from venues.mapping import team_abbr
    ev_game = map_events(c, games)
    drops = Counter()
    out = []
    sql, params = S.week1_filter_sql()
    props = {}
    for mid, line, side, stat, gid in c.execute(
            f"SELECT mo.market_id, o.line, o.side, o.stat, o.event_id FROM market_outcome mo "
            f"JOIN outcomes o USING (outcome_id) WHERE mo.venue='kalshi' AND {sql.replace('market_id', 'mo.market_id')}",
            params):
        props[mid] = (line, side, stat, gid)
    gsis_of = {mid: e for mid, e in c.execute(
        f"SELECT mo.market_id, o.entity_id FROM market_outcome mo JOIN outcomes o USING (outcome_id) "
        f"WHERE mo.venue='kalshi' AND {sql.replace('market_id', 'mo.market_id')}", params)}
    for mid, subject, line in c.execute(
            f"SELECT market_id, subject, line FROM markets WHERE venue='kalshi' AND {sql}", params):
        series = mid.split("-")[0]
        if series not in SERIES:
            continue
        S.assert_search_set(mid, 0)
        st = settled.get(mid)
        if not st or st["status"] not in ("finalized", "settled") or st["result"] not in ("yes", "no"):
            drops[f"{series}: no finalized Kalshi result"] += 1
            continue
        gid = ev_game.get(mid.split("-")[1])
        if series in PROP_STAT:
            if mid not in props:
                drops[f"{series}: no outcome mapping (market_outcome)"] += 1
                continue
            oline, side, stat, ogid = props[mid]
            gid = ogid or gid
            k = check_rung(series, mid, oline, side, stat, st["floor_strike"])
            pid = gsis_of[mid]
            if (pid, stat) in bad_counts:
                drops[f"{series}: pbp count disagrees with nfl_player_week"] += 1
                continue
            if gid not in pbp:
                drops[f"{series}: game not in play-by-play"] += 1
                continue
            truth, D, kind = prop_determination(pbp[gid][stat].get(pid, []), k, pbp[gid]["end"])
        else:
            if gid not in pbp:
                drops[f"{series}: event not mapped to a week-1 game"] += 1
                continue
            g, end = games[gid], pbp[gid]["end"]
            if series == "KXNFLTOTAL":
                L = float(st["floor_strike"] if st["floor_strike"] is not None else line)
                truth, D, kind = total_determination(pbp[gid]["score_path"], L, end)
            else:
                team = team_abbr((subject or "").split(" wins by")[0])
                if team not in (g["home"], g["away"]):
                    drops[f"{series}: subject team not in game"] += 1
                    continue
                margin = (g["hs"] - g["as"]) if team == g["home"] else (g["as"] - g["hs"])
                L = 0.0 if series == "KXNFLGAME" else float(st["floor_strike"] if st["floor_strike"] is not None else line)
                truth, D, kind = (1.0 if margin > L else 0.0), end, "at_end"
        out.append({"market": mid, "series": series, "game": gid, "truth": truth, "D": D,
                    "kind": kind, "kalshi_yes": 1.0 if st["result"] == "yes" else 0.0,
                    "close_ts": st["close_ts"], "settle_ts": st["settle_ts"],
                    "end": pbp[gid]["end"], "kick": games[gid]["kick"],
                    "taker_m": series_multiplier(mid)[1]})
    return out, drops


def load_quotes(c, markets):
    by = defaultdict(list)
    want = {m["market"] for m in markets}
    sql, params = S.week1_filter_sql()
    for s in SERIES:
        for mid, ts, bid, ask in c.execute(
                f"SELECT market_id, ts, best_bid, best_ask FROM quotes WHERE venue='kalshi' "
                f"AND source='live' AND market_id >= ? AND market_id < ? AND {sql} ORDER BY market_id, ts",
                [s + "-", s + "."] + params):
            if mid in want:
                by[mid].append((ts, bid, ask))
    return by


def depth_asof(c, market_id, side, t):
    r = c.execute("SELECT ts, touch_price, touch_size FROM market_depth WHERE venue='kalshi' "
                  "AND market_id=? AND side=? AND ts <= ? ORDER BY ts DESC LIMIT 1",
                  (market_id, side, t)).fetchone()
    return r if r and t - r[0] <= DEPTH_WINDOW else None


def depth_window(c, market_id, side, t0, t1):
    return c.execute("SELECT ts, touch_price, touch_size FROM market_depth WHERE venue='kalshi' "
                     "AND market_id=? AND side=? AND ts BETWEEN ? AND ? ORDER BY ts",
                     (market_id, side, t0, t1)).fetchall()


# =============================================================================
# observations
# =============================================================================

def observe(c, m, q, G, C, depth=None, window=None):
    """One market at D+G, order size C.

    `depth(market, side, t) -> (ts, touch_price, touch_size) | None` and
    `window(market, side, t0, t1) -> [(ts, price, size)]` default to the NFL
    `market_depth` table; CFB passes readers over its raw order books."""
    depth = depth or (lambda mk, sd, t: depth_asof(c, mk, sd, t))
    window = window or (lambda mk, sd, a, b: depth_window(c, mk, sd, a, b))
    qts = [r[0] for r in q]
    t0 = m["D"] + G
    o = {"game": m["game"], "market": m["market"], "series": m["series"], "kind": m["kind"],
         "G": G, "C": C, "status": "", "net": None, "qnet": None, "gap": None,
         "persist": None, "persist_why": None, "lift": None, "lift_max": None}
    quote, why = asof_quote(qts, q, t0, m["close_ts"])
    if quote is None:
        o["status"] = why
        return o
    _, bid, ask = quote
    if bid is not None and ask is not None:
        o["gap"] = abs((bid + ask) / 2 - m["truth"])
    price = truth_ask(m["truth"], bid, ask)
    if price is None:
        o["status"] = "no ask on the true side"
        return o
    o["qnet"] = net_per_contract(price, C, m["taker_m"])
    side = "buy_yes" if m["truth"] == 1.0 else "buy_no"
    d = depth(m["market"], side, t0)
    if d is None:
        o["status"] = "no depth within 60s"
    elif d[1] is None or abs(d[1] - price) > SAME_TOUCH:
        o["status"] = "depth shows a different book"
    elif (d[2] or 0) < C:
        o["status"] = f"touch holds < {C}"
    else:
        o["status"] = "executable"
        o["net"] = o["qnet"]
    if o["qnet"] is not None and o["qnet"] > 0:
        j = bisect.bisect_right(qts, t0)
        dur, why2 = persistence(q[j:], t0, m["truth"], C, m["taker_m"], m["close_ts"])
        o["persist"], o["persist_why"] = dur, why2
        if o["status"] == "executable":
            o["lift"] = d[2]
            win = window(m["market"], side, t0, t0 + dur + DEPTH_WINDOW)
            sizes = [s for ts, p, s in win if p is not None and net_per_contract(p, C, m["taker_m"]) is not None
                     and net_per_contract(p, C, m["taker_m"]) > 0]
            o["lift_max"] = max(sizes) if sizes else d[2]
    return o


def q_(v, p):
    v = sorted(v)
    return v[min(len(v) - 1, int(p * len(v)))] if v else float("nan")


def register_cell(reg, family, name, rm, rs, role, population, share_family=None):
    """One (series, kind, G, C) cell: the mean net is a test in `role`, the
    share of profitable markets is always descriptive."""
    reg.add(family, name, rm, role=role, unit="pp", scale=100.0, population=population,
            note="mean executable net per contract over depth-confirmed markets")
    reg.add(share_family or family.replace("net_mean", "net_share"), name, rs, role=SHARE_ROLE,
            unit="share", population=population,
            note="DESCRIPTIVE: a share is >= 0 by construction; not a test")


def run():
    S.open_population()
    if os.path.exists(REGISTRY):
        os.remove(REGISTRY)
    reg = S.Registry(REGISTRY)
    c = S.live_ro()
    games = load_games(c)
    settled = load_settlements()
    pbp = load_pbp(games)
    bad, _pw = verify_counts(c, pbp)
    markets, drops = load_markets(c, games, settled, pbp, bad)
    quotes = load_quotes(c, markets)

    print("=" * 78 + f"\nH1 - DETERMINATION LAG, NFL {S.SEASON} WEEK {S.WEEK} "
          f"(population {S.POPULATION}, role {S.ROLE})\n" + "=" * 78)
    print(f"  games {len(games)}  pbp games {len(pbp)}  Kalshi finalized week-1 markets on disk {len(settled)}")
    print(f"  pbp-vs-nfl_player_week mismatched player-stats excluded: {len(bad)}")
    print(f"  markets determined and kept: {len(markets)}")
    for k, v in sorted(drops.items()):
        print(f"    dropped  {k:<52} {v:>5}")

    dis = [m for m in markets if m["kalshi_yes"] != m["truth"]]
    lo, hi = S.wilson(len(dis), len(markets))
    print(f"\n  SETTLEMENT vs BOX SCORE: {len(dis)} of {len(markets)} disagree  "
          f"(Wilson {100 * lo:.2f}%-{100 * hi:.2f}%)")
    for m in dis[:10]:
        print(f"    {m['market']}  kalshi={m['kalshi_yes']:.0f} nflverse={m['truth']:.0f}")

    obs = {(G, C): [observe(c, m, quotes.get(m["market"], []), G, C) for m in markets]
           for G in GUARDS for C in SIZES}

    cells = [(s, k) for s in SERIES for k in KINDS if any(m["series"] == s and m["kind"] == k for m in markets)]
    print("\n  H1 TABLE at D+120s  (net in pp per contract; executable = quote price confirmed by depth)")
    hdr = ("series", "kind", "mkts", "games", "med|mid-truth|", "share net>0 C=10 [Wilson]",
           "mean net C=10", "mean net C=100", "med persist", "med lift", "close-D med", "settle-D med/p90")
    print("  " + " | ".join(hdr))
    for s, k in cells:
        ms = [m for m in markets if m["series"] == s and m["kind"] == k]
        o10 = [o for o in obs[(PRIMARY_G, 10)] if o["series"] == s and o["kind"] == k]
        o100 = [o for o in obs[(PRIMARY_G, 100)] if o["series"] == s and o["kind"] == k]
        gaps = [o["gap"] for o in o10 if o["gap"] is not None]
        wins = sum(1 for o in o10 if o["net"] is not None and o["net"] > 0)
        wl, wh = S.wilson(wins, len(o10))
        r10 = S.boot([o for o in o10 if o["net"] is not None], S.mean_of("net"))
        r100 = S.boot([o for o in o100 if o["net"] is not None], S.mean_of("net"))
        pers = [o["persist"] for o in o10 if o["persist"] is not None]
        lift = [o["lift"] for o in o10 if o["lift"] is not None]
        cl = [m["close_ts"] - m["D"] for m in ms if m["close_ts"]]
        se = [m["settle_ts"] - m["D"] for m in ms if m["settle_ts"]]
        fmt = lambda r: "n/a" if not r else (f"{100 * r['est']:+.2f} [{100 * r['lo']:+.2f},{100 * r['hi']:+.2f}] "
                                             f"p={r['p']:.2g} n={r['n']} g={r['games']}")
        print(f"  {s} | {k} | {len(ms)} | {len({m['game'] for m in ms})} | "
              f"{statistics.median(gaps) * 100 if gaps else float('nan'):.1f}pp | "
              f"{wins}/{len(o10)} [{100 * wl:.1f},{100 * wh:.1f}]% | {fmt(r10)} | {fmt(r100)} | "
              f"{statistics.median(pers) if pers else float('nan'):.0f}s | "
              f"{statistics.median(lift) if lift else float('nan'):.0f} | "
              f"{statistics.median(cl) / 60 if cl else float('nan'):.1f}m | "
              f"{statistics.median(se) / 60 if se else float('nan'):.1f}m / {q_(se, .9) / 60 if se else float('nan'):.1f}m")
        stat_counts = Counter(o["status"] for o in o10)
        print(f"      status at D+120 (C=10): {dict(stat_counts)}")
        qn = [o["qnet"] for o in o10 if o["qnet"] is not None]
        print(f"      CONTEXT, not pre-registered, not a test: quote-only net (no depth check) "
              f"n={len(qn)} share>0 {sum(x > 0 for x in qn)}/{len(qn)} mean {100 * statistics.fmean(qn) if qn else float('nan'):+.2f}pp; "
              f"persistence ended by {dict(Counter(o['persist_why'] for o in o10 if o['persist_why']))}")

    # registered tests: every G x C x cell, mean net and share net>0
    n_tests = 0
    print("\n  G SENSITIVITY (C=10): mean executable net pp [interval] / share net>0")
    for s, k in cells:
        line = f"    {s:<12} {k:<12}"
        for G in GUARDS:
            for C in SIZES:
                os_ = [o for o in obs[(G, C)] if o["series"] == s and o["kind"] == k]
                for o in os_:
                    o["win"] = 1.0 if (o["net"] is not None and o["net"] > 0) else 0.0
                rm = S.boot([o for o in os_ if o["net"] is not None], S.mean_of("net"))
                rs = S.boot(os_, S.mean_of("win"))
                register_cell(reg, "H1_net_mean", f"{s}|{k}|G{G}|C{C}", rm, rs,
                              role=S.ROLE, population=S.POPULATION)
                n_tests += 1
                if C == 10:
                    line += (f" | G{G}: " + ("n/a" if not rm else f"{100 * rm['est']:+.2f}[{100 * rm['lo']:+.2f},{100 * rm['hi']:+.2f}]")
                             + f" / {sum(o['win'] for o in os_):.0f}/{len(os_)}")
        print(line)
    print(f"\n  registered tests: {n_tests} {S.ROLE} + {n_tests} descriptive shares -> {REGISTRY}")

    # "Too good is a bug": every executable net above 25pp once the guard is
    # past the play. Not a test - the rows a reader must see before believing
    # any mean above.
    print("\n  ANOMALIES: executable net > 25pp at G >= 60 (C=10)")
    by_mkt = {m["market"]: m for m in markets}
    seen = set()
    for G in GUARDS[1:]:
        for o in obs[(G, 10)]:
            if o["net"] is not None and o["net"] > 0.25 and o["market"] not in seen:
                seen.add(o["market"])
                m = by_mkt[o["market"]]
                fmt = lambda t: datetime.fromtimestamp(t, S.ET).strftime("%a %H:%M:%S") if t else "-"
                print(f"    {o['market']}  G{G} truth={m['truth']:.0f} kalshi={m['kalshi_yes']:.0f} "
                      f"net {100 * o['net']:+.1f}pp  D {fmt(m['D'])}  close {fmt(m['close_ts'])}  "
                      f"settled {fmt(m['settle_ts'])}")
    if not seen:
        print("    none")
    return reg


# =============================================================================
# CFB - post-game determination only (pre-registration H1, "CFB" paragraph)
# =============================================================================
#
# There is no CFB play clock on disk, so D is the first instant AT OR AFTER
# KICKOFF that the CFBD winner's Kalshi moneyline mid is >= 0.97. "At or after
# kickoff" is the pre-registered "post-game only" applied as a floor: 20 of 85
# games first quote >= 0.97 BEFORE kickoff (heavy favourites), which determines
# nothing. Every market in the game shares that D and is kind "at_end".
#
# CFB has no `market_depth`. The touch comes from raw `orderbooks` payloads
# (median 27s per ticker) rather than raw `/markets` (median 360s per ticker,
# which would almost never fall within the 60s depth window).

def cfb_truth(series, subject, line, g):
    """Truth of a CFB market from CFBD final scores. `g` is a cfb_calibration
    match row (nh/na normalised names, hp/ap points). Returns 1.0/0.0 or raises
    ValueError when the subject names neither team."""
    from research import cfb_calibration as CC
    by_team = {g["nh"]: g["hp"], g["na"]: g["ap"]}
    if series == "KXNCAAFTOTAL":
        return 1.0 if g["hp"] + g["ap"] > float(line) else 0.0
    subj = subject or ""
    if series == "KXNCAAFGAME":
        team = CC.norm(subj)
    elif series == "KXNCAAFSPREAD":
        team = CC.norm(subj.split(" wins by")[0])
    elif series == "KXNCAAFTEAMTOTAL":
        team = CC.norm(subj.split(" over ")[0])
    else:
        raise ValueError(series)
    if team not in by_team:
        raise ValueError(f"subject {subject!r} names neither {g['nh']!r} nor {g['na']!r}")
    other = g["na"] if team == g["nh"] else g["nh"]
    if series == "KXNCAAFGAME":
        return 1.0 if by_team[team] > by_team[other] else 0.0
    if series == "KXNCAAFSPREAD":
        return 1.0 if by_team[team] - by_team[other] > float(line) else 0.0
    return 1.0 if by_team[team] > float(line) else 0.0


def cfb_determination(winner_quotes, loser_quotes, kickoff, at=CFB_DETERMINE_AT):
    """(D, loser_hit_first). D = first mid >= `at` on the WINNER's leg at/after
    kickoff, or None. `loser_hit_first` flags a game whose LOSER quoted >= `at`
    after kickoff before the winner did - a comeback the rule must not call."""
    def first(q):
        return next((t for t, b, a in q if t >= kickoff and b is not None and a is not None
                     and (b + a) / 2 >= at), None)
    d, lo = first(winner_quotes), first(loser_quotes)
    return d, (lo is not None and (d is None or lo < d))


def book_touch(snapshot, side):
    """(ts, touch_price, touch_size) for buying `side` from a raw book snapshot
    (ts, yes_bid, yes_bid_size, no_bid, no_bid_size). A YES buy lifts the best NO
    bid at 1 - no_bid; a NO buy lifts the best YES bid at 1 - yes_bid."""
    ts, yb, ys, nb, ns = snapshot
    if side == "buy_yes":
        return None if nb is None else (ts, 1.0 - nb, ns)
    return None if yb is None else (ts, 1.0 - yb, ys)


def book_top(levels):
    """Best (highest) bid level of one side of a Kalshi `orderbook_fp`."""
    if not levels:
        return None, None
    return max(((float(p), float(s)) for p, s in levels), key=lambda x: x[0])


def unreadable_at(t, cover):
    """Is instant `t` inside an hour whose raw shard could not be read up to
    `t`? `cover`: {hour_start_ts: (last_readable_ts, complete)}."""
    hour = int(t // 3600) * 3600
    if hour not in cover:
        return True
    last, complete = cover[hour]
    return (not complete) and (last is None or t > last)


def load_cfb_books(series=CFB_SERIES):
    """Raw cfb_kalshi order books -> ({ticker: [snapshot]}, {hour_ts: (last_ts, complete)},
    shard census). Reads each shard up to its first corrupt byte and no further."""
    import zlib
    root = S.cfb_raw_dir()
    prefixes = tuple(s + "-" for s in series)
    books, cover, census = defaultdict(list), {}, []
    for fn in sorted(glob.glob(os.path.join(root, "*", "*.jsonl.gz"))):
        day, hh = os.path.basename(os.path.dirname(fn)), os.path.basename(fn)[:2]
        hour = int(datetime.strptime(f"{day} {hh}", "%Y-%m-%d %H")
                   .replace(tzinfo=__import__("datetime").timezone.utc).timestamp())
        n, last, complete = 0, None, True
        try:
            with gzip.open(fn, "rt", encoding="utf-8") as fh:
                for line in fh:
                    r = json.loads(line)
                    n += 1
                    last = r["ts"]
                    if not r.get("endpoint", "").startswith("orderbooks"):
                        continue
                    for b in (r.get("payload") or {}).get("orderbooks") or []:
                        t = b.get("ticker", "")
                        if t.startswith(prefixes):
                            ob = b.get("orderbook_fp") or {}
                            yb, ys = book_top(ob.get("yes_dollars"))
                            nb, ns = book_top(ob.get("no_dollars"))
                            books[t].append((r["ts"], yb, ys, nb, ns))
        except (OSError, EOFError, zlib.error, json.JSONDecodeError):
            complete = False
        cover[hour] = (last, complete)
        census.append((f"{day}/{hh}", n, complete))
    for v in books.values():
        v.sort()
    return books, cover, census


def cfb_depth_readers(books):
    idx = {t: [s[0] for s in v] for t, v in books.items()}

    def depth(market, side, t):
        i = bisect.bisect_right(idx.get(market, []), t) - 1
        if i < 0 or t - idx[market][i] > DEPTH_WINDOW:
            return None
        return book_touch(books[market][i], side)

    def window(market, side, t0, t1):
        v = books.get(market, [])
        lo, hi = bisect.bisect_left(idx.get(market, []), t0), bisect.bisect_right(idx.get(market, []), t1)
        return [x for x in (book_touch(s, side) for s in v[lo:hi]) if x is not None]
    return depth, window


def run_cfb():
    from research import cfb_calibration as CC
    S.require_committed(S.CANDIDATES_DOC)
    if os.path.exists(CFB_REGISTRY):
        os.remove(CFB_REGISTRY)
    reg = S.Registry(CFB_REGISTRY)
    c = S.cfb_ro()
    games, unmatched = CC.match(c)
    settled = load_settlements(os.path.join(config.RAW_DIR, CFB_SETTLED_VENUE))
    drops, markets, comebacks, dmins = Counter(), [], 0, []
    for gkey, g in games.items():
        if not g["completed"] or g["hp"] is None or g["ap"] is None or g["hp"] == g["ap"]:
            drops["game: no CFBD final (or tied)"] += 1
            continue
        winner = g["nh"] if g["hp"] > g["ap"] else g["na"]
        legs = {m: CC.norm(s) for m, s in c.execute(
            "SELECT market_id, subject FROM markets WHERE venue=? AND market_id LIKE ?",
            (CFB_VENUE, f"KXNCAAFGAME-{gkey}-%"))}
        wm = [m for m, n in legs.items() if n == winner]
        lm = [m for m, n in legs.items() if n != winner]
        if len(wm) != 1 or len(lm) != 1:
            drops["game: moneyline legs do not resolve to winner/loser"] += 1
            continue
        qq = lambda m: c.execute("SELECT ts, best_bid, best_ask FROM quotes WHERE venue=? AND market_id=? "
                                 "ORDER BY ts", (CFB_VENUE, m)).fetchall()
        D, loser_first = cfb_determination(qq(wm[0]), qq(lm[0]), g["start_ts"])
        comebacks += loser_first
        if D is None:
            drops["game: winner never >= 0.97 after kickoff"] += 1
            continue
        dmins.append((D - g["start_ts"]) / 60)
        for mid, subject, line in c.execute(
                "SELECT market_id, subject, line FROM markets WHERE venue=? AND (" +
                " OR ".join("market_id LIKE ?" for _ in CFB_SERIES) + ")",
                [CFB_VENUE] + [f"{s}-{gkey}-%" for s in CFB_SERIES]):
            series = mid.split("-")[0]
            st = settled.get(mid)
            if not st or st["status"] not in ("finalized", "settled") or st["result"] not in ("yes", "no"):
                drops[f"{series}: no finalized Kalshi result"] += 1
                continue
            if series != "KXNCAAFGAME" and (st["floor_strike"] is None or line is None
                                            or abs(float(st["floor_strike"]) - float(line)) > 1e-9):
                drops[f"{series}: Kalshi floor_strike != stored line"] += 1
                continue
            try:
                truth = cfb_truth(series, subject, line, g)
            except ValueError:
                drops[f"{series}: subject names neither team"] += 1
                continue
            markets.append({"market": mid, "series": series, "game": gkey, "truth": truth, "D": D,
                            "kind": "at_end", "kalshi_yes": 1.0 if st["result"] == "yes" else 0.0,
                            "close_ts": st["close_ts"], "settle_ts": st["settle_ts"],
                            "kick": g["start_ts"], "taker_m": series_multiplier(mid)[1]})
    quotes = defaultdict(list)
    want = {m["market"] for m in markets}
    for s in CFB_SERIES:
        for mid, ts, bid, ask in c.execute(
                "SELECT market_id, ts, best_bid, best_ask FROM quotes WHERE venue=? AND market_id >= ? "
                "AND market_id < ? ORDER BY market_id, ts", (CFB_VENUE, s + "-", s + ".")):
            if mid in want:
                quotes[mid].append((ts, bid, ask))
    books, cover, census = load_cfb_books()
    depth, window = cfb_depth_readers(books)

    print("=" * 78 + "\nH1 - DETERMINATION LAG, CFB (holdout B replication + population run)\n" + "=" * 78)
    n_complete = sum(1 for _, _, ok in census if ok)
    print(f"  raw cfb_kalshi shards {len(census)}: fully readable {n_complete}, "
          f"truncated at first corrupt byte {len(census) - n_complete} "
          f"({100 * n_complete / len(census):.0f}% fully readable)")
    print(f"  matched games {len(games)} (unmatched {len(unmatched)}); games with D {len({m['game'] for m in markets})}; "
          f"comebacks (loser >= 0.97 after kickoff first) {comebacks}")
    qd = sorted(dmins)
    print("  D minus kickoff, minutes: " + "  ".join(
        f"p{int(100 * p)} {qd[int(p * (len(qd) - 1))]:.0f}" for p in (0, .1, .25, .5, .75, .9, 1)))
    print("  (a CFB game runs ~210 min: a D well below that is a BLOWOUT mid-game, when spreads and"
          " totals are not yet determined - their 'truth' at D is look-ahead)")
    print(f"  markets kept {len(markets)}")
    for k, v in sorted(drops.items()):
        print(f"    dropped  {k:<52} {v:>5}")
    dis = [m for m in markets if m["kalshi_yes"] != m["truth"]]
    lo, hi = S.wilson(len(dis), len(markets))
    print(f"\n  SETTLEMENT vs CFBD: {len(dis)} of {len(markets)} disagree (Wilson {100 * lo:.2f}%-{100 * hi:.2f}%)")
    for m in dis[:10]:
        print(f"    {m['market']}  kalshi={m['kalshi_yes']:.0f} cfbd={m['truth']:.0f}")

    obs = {(G, C): [observe(None, m, quotes.get(m["market"], []), G, C, depth, window) for m in markets]
           for G in GUARDS for C in SIZES}
    inst = [m["D"] + PRIMARY_G for m in markets]
    unread = sum(unreadable_at(t, cover) for t in inst)
    print(f"  H1 instants (D+120s) inside an unreadable raw region: {unread} of {len(inst)} "
          f"({100 * unread / max(len(inst), 1):.1f}%) - those markets cannot be depth-confirmed; not imputed")

    print("\n  H1 TABLE at D+120s  (net in pp per contract; executable = quote confirmed by raw book)")
    print("  series | kind | mkts | games | med|mid-truth| | share net>0 C=10 [Wilson] | mean net C=10 | "
          "mean net C=100 | med persist | med lift | close-D med | settle-D med/p90")
    fmt = lambda r: "n/a" if not r else (f"{100 * r['est']:+.2f} [{100 * r['lo']:+.2f},{100 * r['hi']:+.2f}] "
                                         f"p={r['p']:.2g} n={r['n']} g={r['games']}")
    nan = float("nan")
    for s in CFB_SERIES:
        ms = [m for m in markets if m["series"] == s]
        if not ms:
            continue
        o10 = [o for o in obs[(PRIMARY_G, 10)] if o["series"] == s]
        o100 = [o for o in obs[(PRIMARY_G, 100)] if o["series"] == s]
        gaps = [o["gap"] for o in o10 if o["gap"] is not None]
        wins = sum(1 for o in o10 if o["net"] is not None and o["net"] > 0)
        wl, wh = S.wilson(wins, len(o10))
        r10 = S.boot([o for o in o10 if o["net"] is not None], S.mean_of("net"))
        r100 = S.boot([o for o in o100 if o["net"] is not None], S.mean_of("net"))
        pers = [o["persist"] for o in o10 if o["persist"] is not None]
        lift = [o["lift"] for o in o10 if o["lift"] is not None]
        cl = [m["close_ts"] - m["D"] for m in ms if m["close_ts"]]
        se = [m["settle_ts"] - m["D"] for m in ms if m["settle_ts"]]
        print(f"  {s} | at_end | {len(ms)} | {len({m['game'] for m in ms})} | "
              f"{statistics.median(gaps) * 100 if gaps else nan:.1f}pp | "
              f"{wins}/{len(o10)} [{100 * wl:.1f},{100 * wh:.1f}]% | {fmt(r10)} | {fmt(r100)} | "
              f"{statistics.median(pers) if pers else nan:.0f}s | {statistics.median(lift) if lift else nan:.0f} | "
              f"{statistics.median(cl) / 60 if cl else nan:.1f}m | "
              f"{statistics.median(se) / 60 if se else nan:.1f}m / {q_(se, .9) / 60 if se else nan:.1f}m")
        print(f"      status at D+120 (C=10): {dict(Counter(o['status'] for o in o10))}")

    n_tests = 0
    print("\n  G SENSITIVITY (C=10): mean executable net pp [interval] / share net>0")
    for s in CFB_SERIES:
        line = f"    {s:<18} at_end"
        for G in GUARDS:
            for C in SIZES:
                os_ = [o for o in obs[(G, C)] if o["series"] == s]
                if not os_:
                    continue
                for o in os_:
                    o["win"] = 1.0 if (o["net"] is not None and o["net"] > 0) else 0.0
                rm = S.boot([o for o in os_ if o["net"] is not None], S.mean_of("net"))
                rs = S.boot(os_, S.mean_of("win"))
                name = f"{s}|at_end|G{G}|C{C}"
                register_cell(reg, "H1_net_mean", name, rm, rs, role="replication", population="cfb")
                register_cell(reg, "H1_net_mean cfb population", name, rm, rs, role="search",
                              population="cfb", share_family="H1_net_share cfb population")
                n_tests += 1
                if C == 10:
                    line += (f" | G{G}: " + ("n/a" if not rm else
                             f"{100 * rm['est']:+.2f}[{100 * rm['lo']:+.2f},{100 * rm['hi']:+.2f}]")
                             + f" / {sum(o['win'] for o in os_):.0f}/{len(os_)}")
        print(line)
    print(f"\n  registered: {n_tests} replication + {n_tests} search (cfb population) mean-net records, "
          f"{2 * n_tests} descriptive share records -> {CFB_REGISTRY}")

    print("\n  ANOMALIES: executable net > 25pp at G >= 60 (C=10) - look-ahead or a stale book, not money")
    by_mkt = {m["market"]: m for m in markets}
    seen = set()
    for G in GUARDS[1:]:
        for o in obs[(G, 10)]:
            if o["net"] is not None and o["net"] > 0.25 and o["market"] not in seen:
                seen.add(o["market"])
                m = by_mkt[o["market"]]
                fmt_t = lambda t: datetime.fromtimestamp(t, S.ET).strftime("%a %H:%M:%S") if t else "-"
                print(f"    {o['market']}  G{G} truth={m['truth']:.0f} net {100 * o['net']:+.1f}pp  "
                      f"D {fmt_t(m['D'])} (kick+{(m['D'] - m['kick']) / 60:.0f}m)  close {fmt_t(m['close_ts'])}")
    print(f"    {len(seen)} markets" if seen else "    none")
    return reg


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--cfb", action="store_true", help="run H1 on CFB (holdout B) instead of NFL")
    if ap.parse_args().cfb:
        run_cfb()
    else:
        run()
