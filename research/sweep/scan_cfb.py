"""Brief 022 phase 2 - the scan on COLLEGE FOOTBALL (holdout B).

    python -m research.sweep.scan_cfb        # -> results/scan_cfb.jsonl

Two roles in one registry, kept apart by `role`:

  replication (population cfb) - the frozen scan candidates, by the exact record
      names `docs/briefs/022-candidates.md` gives them:
        cfb_rep_pricepath   "<CFB series> | <tier> | h=<s>s | slope" and
                            "... | top-decile net of cost" - axis 3's method.
        cfb_rep_constraints "C1 CFBGAME - SPREAD1.5 | <phase> | <metric>".
  search (population cfb) - axis 6: the four C01 coherence relations re-run as
      tests at a PRE-KICKOFF snapshot (last quote before CFBD start_ts).

Opens CFB only through `common.cfb_ro()` / `common.cfb_raw_dir()`, which refuse
until the candidates doc is committed. Blocks are CFB games (Kalshi game key).

Kickoffs come from `research.cfb_calibration.match` (CFBD start_ts); games it
cannot match are excluded and counted. Depth: `cfb_probe.db` stores no book, so
touch size for the executable C1 arm comes from the raw `/markets` payloads
(`yes_bid_size_fp` / `yes_ask_size_fp`); 43 of the probe's raw shards were
corrupted by a double writer (brief C01), so coverage is reported.
"""
import argparse
import glob
import gzip
import json
import os
import sys
import zlib
from collections import Counter, defaultdict
from contextlib import contextmanager

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from research import cfb_calibration as cal  # noqa: E402
from research import cfb_coherence as coh  # noqa: E402
from research.sweep import common as S  # noqa: E402
from research.sweep import scan as X  # noqa: E402

REG_PATH = os.path.join(S.ROOT, "research", "sweep", "results", "scan_cfb.jsonl")
NFL_REG = os.path.join(S.ROOT, "research", "sweep", "results", "scan.jsonl")
VENUE = "cfb_kalshi"
CFB_SERIES = ("KXNCAAFSPREAD", "KXNCAAFTOTAL", "KXNCAAFGAME")
NFL_OF = {"KXNCAAFSPREAD": "KXNFLSPREAD", "KXNCAAFTOTAL": "KXNFLTOTAL", "KXNCAAFGAME": "KXNFLGAME"}
C1_NAME = "C1 CFBGAME - SPREAD1.5"
RELATIONS = ("teamRef-teamOpp = spread [2-sided]", "teamA+teamB = game total",
             "Q1+Q2 = 1H", "Q1..Q4 = game")
POP = "cfb"


# =============================================================================
# pure pieces
# =============================================================================

def lowest_rungs(spread_rows):
    """[(market_id, team, line)] -> {team: (market_id, line)} at each team's
    lowest listed spread line."""
    out = {}
    for mid, team, line in spread_rows:
        if line is None:
            continue
        if team not in out or line < out[team][1]:
            out[team] = (mid, line)
    return out


def touch_ok(raw, t, side, price, contracts=X.TICKET, window=X.DEPTH_WINDOW):
    """Raw /markets record (ts, yes_bid, yes_bid_size, yes_ask, yes_ask_size)
    within `window` at or before t showing the same touch with >= contracts.
    buy_yes lifts the yes ask; buy_no at price p lifts the yes BID at 1 - p."""
    import bisect
    ts = [r[0] for r in raw]
    i = bisect.bisect_right(ts, t) - 1
    if i < 0 or t - raw[i][0] > window:
        return False
    _t, bid, bsz, ask, asz = raw[i]
    if side == "buy_yes":
        return ask is not None and abs(ask - price) <= 0.005 and (asz or 0) >= contracts
    return bid is not None and abs(bid - (1 - price)) <= 0.005 and (bsz or 0) >= contracts


def relation_rows(games, wanted=RELATIONS):
    rows, other = [], Counter()
    for key, g in games.items():
        for r in coh.relations(g):
            if r["name"] in wanted:
                rows.append({"game": key, "relation": r["name"], "dev": r["dev"],
                             "viol": 1.0 if abs(r["dev"]) > r["band"] else 0.0})
            else:
                other[r["name"]] += 1
    return rows, other


@contextmanager
def snapshot_before(ts_by_game):
    """Run `coh.build` at a per-game snapshot without editing cfb_coherence:
    `build` calls the module-level `load_snapshot`, so it is swapped for one
    that passes `ts_by_game`, and restored however the block exits."""
    orig = coh.load_snapshot

    def patched(conn, series, lo, hi, ts_by_game_=None):
        return orig(conn, series, lo, hi, ts_by_game)
    coh.load_snapshot = patched
    try:
        yield
    finally:
        coh.load_snapshot = orig


def wilson_res(k, n, games):
    lo, hi = S.wilson(k, n)
    return {"est": k / n, "lo": lo, "hi": hi, "se": 0.0, "p": 1.0, "n": n, "games": games}


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# =============================================================================
# loading
# =============================================================================

def load(conn):
    games, unmatched = cal.match(conn)
    kick = {k: g["start_ts"] for k, g in games.items()}
    lo, hi = coh.probe_window(conn)
    markets = defaultdict(list)
    for series in CFB_SERIES:
        for mid, eid, line in conn.execute(
                "SELECT market_id, event_id, line FROM markets WHERE venue=? AND market_id >= ? "
                "AND market_id < ?", (VENUE, series + "-", series + ".")):
            k = coh.game_key(eid)
            if k in kick:
                markets[k].append({"market": mid, "series": series, "line": line,
                                   "team": coh.team_of(mid), "game": k})
    return games, unmatched, kick, (lo, hi), markets


_Q = {}


def quotes(conn, mid):
    if mid not in _Q:
        _Q[mid] = X.Quotes(conn.execute(
            "SELECT ts, best_bid, best_ask FROM quotes WHERE venue=? AND market_id=? ORDER BY ts",
            (VENUE, mid)).fetchall())
    return _Q[mid]


def load_raw_touch(tickers):
    raw_dir = S.cfb_raw_dir()
    out, census = defaultdict(list), Counter()
    needles = tuple(f'"{t}"' for t in sorted({t.split("-")[0] for t in tickers}))
    for fn in sorted(glob.glob(os.path.join(raw_dir, "*", "*.jsonl.gz"))):
        census["shards"] += 1
        try:
            with gzip.open(fn, "rt", encoding="utf-8") as f:
                for line in f:
                    if not any(n[:-1] in line for n in needles):
                        continue
                    rec = json.loads(line)
                    p = rec.get("payload")
                    for m in (p.get("markets") or []) if isinstance(p, dict) else []:
                        t = m.get("ticker")
                        if t in tickers:
                            out[t].append((rec.get("ts"), _f(m.get("yes_bid_dollars")),
                                           _f(m.get("yes_bid_size_fp")), _f(m.get("yes_ask_dollars")),
                                           _f(m.get("yes_ask_size_fp"))))
            census["readable"] += 1
        except (OSError, EOFError, zlib.error, json.JSONDecodeError, UnicodeDecodeError):
            census["unreadable (lines before the break kept)"] += 1
    for v in out.values():
        v.sort(key=lambda r: r[0])
    return out, census


# =============================================================================
# replication 1 - price path
# =============================================================================

def rep_pricepath(conn, reg, kick, window, markets, out):
    _lo, hi = window
    cells = defaultdict(list)
    for gkey, ms in markets.items():
        k = kick[gkey]
        for m in ms:
            q = quotes(conn, m["market"])
            if len(q) < 3:
                continue
            for tier, w0, w1 in (("pre", q.ts[0], min(k, hi)),
                                 ("in", k, min(k + X.LIVE_WINDOW, q.ts[-1] + X.MAX_AGE))):
                for h in X.HORIZONS:
                    for t in X.grid_times(w0, w1, h):
                        a, b, n_ = q.asof(t - h), q.asof(t), q.asof(t + h)
                        if a is None or b is None or n_ is None:
                            continue
                        mb = X.mid(b)
                        fee = X.fee_pp(mb, market_id=m["market"])
                        if fee is None:
                            continue
                        cells[(m["series"], tier, h)].append(
                            {"game": gkey, "x": 100 * (mb - X.mid(a)), "y": 100 * (X.mid(n_) - mb),
                             "cost": 50 * (b[2] - b[1]) + fee})
    for series in CFB_SERIES:
        for tier in ("pre", "in"):
            for h in X.HORIZONS:
                rs = cells[(series, tier, h)]
                rec = reg.add("cfb_rep_pricepath", f"{series} | {tier} | h={h}s | slope",
                              X.slope_boot(rs, "x", "y"), role="replication", population=POP, unit="slope",
                              note="CFB poll cadence ~41s median; h=60s sits near the resolution floor"
                              if h == 60 else "")
                d = X.sign(rec.get("est") or 0)
                top = [dict(r) for r in X.top_decile(rs, "x")]
                for r in top:
                    r["net"] = d * X.sign(r["x"]) * r["y"] - r["cost"]
                rec2 = reg.add("cfb_rep_pricepath", f"{series} | {tier} | h={h}s | top-decile net of cost",
                               X.mean_boot(top, "net"), role="replication", population=POP)
                out.append((rec, rec2))


# =============================================================================
# replication 2 - C1
# =============================================================================

def rep_c1(conn, reg, kick, window, markets, out_lines):
    _lo, hi = window
    rows, rung_census, teams_used = [], Counter(), 0
    for gkey, ms in markets.items():
        k = kick[gkey]
        gm = {m["team"]: m["market"] for m in ms if m["series"] == "KXNCAAFGAME"}
        low = lowest_rungs([(m["market"], m["team"], m["line"]) for m in ms if m["series"] == "KXNCAAFSPREAD"])
        teams = [t for t in gm if t in low]
        for t in teams:
            rung_census[low[t][1]] += 1
        teams_used += len(teams)
        legs = [gm[t] for t in teams] + [low[t][0] for t in teams]
        firsts = [quotes(conn, x).ts[0] for x in legs if len(quotes(conn, x))]
        if not firsts:
            continue
        pre = [k - 3600 * i for i in range(1, 24 * 14) if max(firsts) <= k - 3600 * i <= hi]
        ing = [k + 300 * i for i in range(0, X.LIVE_WINDOW // 300 + 1) if k + 300 * i <= hi]
        for phase, grid in (("pre", pre), ("in", ing)):
            for t in grid:
                for team in teams:
                    qg, qs = quotes(conn, gm[team]).asof(t), quotes(conn, low[team][0]).asof(t)
                    if qg is None or qs is None:
                        continue
                    viol = X.c1_violation(qg[2], qs[1])
                    rows.append({"game": gkey, "phase": phase, "t": t, "dev": 100 * (X.mid(qg) - X.mid(qs)),
                                 "viol": 1.0 if viol else 0.0, "net": None, "gm": gm[team],
                                 "sp": low[team][0], "ask": qg[2], "bid": qs[1]})
    viols = [r for r in rows if r["viol"]]
    raw_census = Counter()
    if viols:
        raw, raw_census = load_raw_touch({r["gm"] for r in viols} | {r["sp"] for r in viols})
        for r in viols:
            if (touch_ok(raw.get(r["gm"], []), r["t"], "buy_yes", r["ask"])
                    and touch_ok(raw.get(r["sp"], []), r["t"], "buy_no", 1 - r["bid"])):
                r["net"] = (100 * (r["bid"] - r["ask"]) - X.fee_pp(r["ask"], market_id=r["gm"])
                            - X.fee_pp(1 - r["bid"], market_id=r["sp"]))
    note = (f"lowest KXNCAAFSPREAD rung per team: {dict(sorted(rung_census.items()))}; "
            f"raw touch shards {dict(raw_census) or 'not scanned (no violations)'}")
    out_lines.append(f"  C1 legs: {teams_used} team-sides in {len(markets)} games; {note}")
    recs = []
    for phase in ("pre", "in"):
        rs = [r for r in rows if r["phase"] == phase]
        for metric, scale, unit in (("dev", 1.0, "pp"), ("viol", 100.0, "% of instants"), ("net", 1.0, "pp")):
            res = X.mean_boot(rs, metric)
            extra = ""
            if metric == "net" and res is None:
                extra = ("no violations" if not any(r["viol"] for r in rs)
                         else "no violation had depth >= 10 contracts on both legs in a readable raw shard")
            recs.append(reg.add("cfb_rep_constraints", f"{C1_NAME} | {phase} | {metric}", res,
                                role="replication", population=POP, unit=unit, scale=scale,
                                note="; ".join(x for x in (note, extra) if x)))
        out_lines.append(f"  C1 {phase}: instants {len(rs)}, violations {int(sum(r['viol'] for r in rs))}, "
                         f"depth-verified {sum(1 for r in rs if r['net'] is not None)}")
    return recs


# =============================================================================
# axis 6 - C01 relations as tests
# =============================================================================

def axis6(conn, reg, kick, out_lines):
    ts_by_game = {k: v - 1e-3 for k, v in kick.items()}
    with snapshot_before(ts_by_game):
        games = coh.build(conn)
    rows, other = relation_rows(games)
    recs = []
    out_lines.append(f"  axis 6: games with a pre-kickoff snapshot {len(games)} of {len(kick)} matched; "
                     f"relations not in the four (not registered): {dict(other)}")
    for rel in RELATIONS:
        rs = [r for r in rows if r["relation"] == rel]
        recs.append(reg.add("axis6_cfb_constraints", f"{rel} | signed deviation", X.mean_boot(rs, "dev"),
                            role="search", population=POP, unit="points"))
        recs.append(reg.add("axis6_cfb_constraints", f"{rel} | violation rate beyond summed bands",
                            X.mean_boot(rs, "viol"), role="search", population=POP, unit="% of games", scale=100.0))
        k, n = int(sum(r["viol"] for r in rs)), len(rs)
        recs.append(reg.add("axis6_cfb_constraints", f"{rel} | violation rate (Wilson)",
                            wilson_res(k, n, len({r['game'] for r in rs})) if n else None,
                            role="descriptive", population=POP, unit="% of games", scale=100.0,
                            note=f"{k}/{n} games; Wilson, not a test"))
    return recs


# =============================================================================
# main
# =============================================================================

def main():
    argparse.ArgumentParser(description=__doc__.splitlines()[0]).parse_args()
    conn = S.cfb_ro()
    if os.path.exists(REG_PATH):
        os.remove(REG_PATH)
    reg = S.Registry(REG_PATH)
    games, unmatched, kick, window, markets = load(conn)
    lines = [f"CFB: matched games {len(games)}, unmatched {len(unmatched)}, with SPREAD/TOTAL/GAME markets "
             f"{len(markets)}; probe window {window}"]
    pp = []
    rep_pricepath(conn, reg, kick, window, markets, pp)
    rep_c1(conn, reg, kick, window, markets, lines)
    axis6(conn, reg, kick, lines)
    print("\n".join(lines))
    nfl = {r["name"]: r for r in S.load_registries([NFL_REG])} if os.path.exists(NFL_REG) else {}
    print("\nREPLICATION (CFB) vs NFL week 1")
    for r in S.load_registries([REG_PATH]):
        nname = r["name"]
        for c, n in NFL_OF.items():
            nname = nname.replace(c, n)
        nname = nname.replace(C1_NAME, "C1 GAME - SPREAD1.5")
        nr = nfl.get(nname)
        f = (lambda x: "n/a" if not x or not x.get("estimable") else
             f"{x['est']:+.3f} [{x['lo']:+.3f},{x['hi']:+.3f}] g={x['games']}")
        print(f"  {r['role']:<11} {r['family']:<22} {r['name']:<60} CFB {f(r):<34} NFL {f(nr) if nr else '-'}"
              + (f"  | {r['note']}" if r.get("note") and r["role"] != "replication" else ""))
    print("\nregistry", REG_PATH, dict(Counter(r["role"] for r in S.load_registries([REG_PATH]))))


if __name__ == "__main__":
    main()
