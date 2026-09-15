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
REGISTRY = os.path.join(S.ROOT, "research", "sweep", "results", "h1.jsonl")
SETTLED_VENUE = "kalshi_settled_022"


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
            "FROM nfl_games WHERE season=2026 AND week=1 ORDER BY data_version"):
        g[gid] = {"game": gid, "kick": kick, "home": home, "away": away, "hs": hs, "as": as_}
    return g


def load_pbp(games):
    import polars as pl
    f = sorted(glob.glob(os.path.join(config.RAW_DIR, "nflverse", "*", "play_by_play_2026.parquet")))[-1]
    df = (pl.read_parquet(f, columns=["game_id", "week", "play_id", "time_of_day", "complete_pass",
                                      "receiver_player_id", "rush_attempt", "rusher_player_id",
                                      "total_home_score", "total_away_score"])
          .filter(pl.col("week") == 1).sort(["game_id", "play_id"]))
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
                                 "WHERE season=2026 AND week=1 AND season_type='REG' ORDER BY data_version"):
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

def observe(c, m, q, G, C):
    """One market at D+G, order size C."""
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
    d = depth_asof(c, m["market"], side, t0)
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
            win = depth_window(c, m["market"], side, t0, t0 + dur + DEPTH_WINDOW)
            sizes = [s for ts, p, s in win if p is not None and net_per_contract(p, C, m["taker_m"]) is not None
                     and net_per_contract(p, C, m["taker_m"]) > 0]
            o["lift_max"] = max(sizes) if sizes else d[2]
    return o


def q_(v, p):
    v = sorted(v)
    return v[min(len(v) - 1, int(p * len(v)))] if v else float("nan")


def run():
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

    print("=" * 78 + "\nH1 - DETERMINATION LAG, NFL WEEK 1 (search set)\n" + "=" * 78)
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
                reg.add("H1_net_mean", f"{s}|{k}|G{G}|C{C}", rm, role="search", unit="pp", scale=100.0,
                        note="mean executable net per contract over depth-confirmed markets")
                reg.add("H1_net_share", f"{s}|{k}|G{G}|C{C}", rs, role="search", unit="share",
                        note="DEGENERATE NULL: a share is >= 0 by construction, so p against 0 only "
                             "tests whether ANY market was profitable")
                n_tests += 2
                if C == 10:
                    line += (f" | G{G}: " + ("n/a" if not rm else f"{100 * rm['est']:+.2f}[{100 * rm['lo']:+.2f},{100 * rm['hi']:+.2f}]")
                             + f" / {sum(o['win'] for o in os_):.0f}/{len(os_)}")
        print(line)
    print(f"\n  registered tests: {n_tests} -> {REGISTRY}")

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


if __name__ == "__main__":
    run()
