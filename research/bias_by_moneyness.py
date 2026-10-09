"""c-22: does the closing-market over bias flip deep in the money?

Implements docs/C22-bias-by-moneyness-preregistration.md (committed at 93b9d37
before this file existed). R18's method - realised minus priced over rate, game
block percentile bootstrap, multiplicative de-vig - split by the over's priced
probability, by stat and by season, with the cost of taking each side printed
in the same row.

    python -m research.bias_by_moneyness                # both populations
    python -m research.bias_by_moneyness --out F.json   # also write aggregates

market_log.db is opened mode=ro only (research.calibration._ro). Output is
aggregates and intervals only: no per-outcome series leaves this script.
"""
import argparse
import json
import statistics
import zlib

import numpy as np

from research import calibration as cal

DRAWS = 2000
SEED = 22
EDGES = [0.0, 0.20, 0.30, 0.40, 0.45, 0.50, 0.55, 0.60, 0.70, 0.80, 1.0000001]
ZONES = (("longshot over p<0.40", 0.0, 0.40),
         ("near the money 0.40-0.60", 0.40, 0.60),
         ("deep ITM over p>=0.60", 0.60, 1.0000001))
MIN_GAMES = 5          # house rule (brief 020): fewer games -> printed, not read
THIN_N = 100
TAKER = 0.07

# R18 as published (research/results/market_calibration.json, a-36). The gate.
R18 = {"n": 44198, "games": 814, "est_pp": -2.4348, "lo_pp": -3.1302, "hi_pp": -1.7232}
GATE_TOL_PP = 0.15


def bucket_of(p):
    for i in range(len(EDGES) - 1):
        if EDGES[i] <= p < EDGES[i + 1]:
            return i
    raise ValueError("price %r outside [0, 1]" % p)


def bucket_label(i):
    hi = min(EDGES[i + 1], 1.0)
    return "%.2f-%.2f" % (EDGES[i], hi)


def zone_of(p):
    for name, lo, hi in ZONES:
        if lo <= p < hi:
            return name
    raise ValueError(p)


def fee(p):
    """Kalshi taker fee per contract, continuous (ceil-to-cent ignored at C>=100)."""
    return TAKER * p * (1 - p)


# --------------------------------------------------------------------------
# the game-block bootstrap
# --------------------------------------------------------------------------

def _seed(name):
    """Independent draws per slice - readers compare buckets side by side, so
    sharing draws across them would correlate their Monte Carlo error (CLAUDE.md,
    shared denominators). Derived from the slice's name, so reproducible."""
    return SEED * 1_000_003 + zlib.crc32(name.encode("utf-8"))


def _blocks(games, values_list):
    """Per-game sums of each value array, plus per-game counts, first-seen order."""
    order = {}
    for g in games:
        if g not in order:
            order[g] = len(order)
    gi = np.fromiter((order[g] for g in games), dtype=np.int64, count=len(games))
    n = np.bincount(gi, minlength=len(order)).astype(float)
    sums = [np.bincount(gi, weights=np.asarray(v, float), minlength=len(order))
            for v in values_list]
    return n, sums


def boot_mean(games, values, name, draws=DRAWS):
    """mean(values) with a game-block percentile interval, in pp."""
    if not len(values):
        return None
    n, (s,) = _blocks(games, [values])
    G = len(n)
    rng = np.random.default_rng(_seed(name))
    idx = rng.integers(0, G, size=(draws, G))
    b = np.sort(s[idx].sum(1) / n[idx].sum(1))
    est = float(np.sum(values)) / len(values)
    return {"n": len(values), "games": G, "est_pp": 100 * est,
            "lo_pp": 100 * float(b[int(0.025 * draws)]),
            "hi_pp": 100 * float(b[int(0.975 * draws) - 1]),
            "readable": G >= MIN_GAMES}


def boot_contrast(games, values, in_a, in_b, name, draws=DRAWS):
    """mean(values | a) - mean(values | b), ONE quantity over shared game draws."""
    va = np.where(in_a, values, 0.0)
    vb = np.where(in_b, values, 0.0)
    n, (sa, sb, na, nb) = _blocks(games, [va, vb, in_a.astype(float), in_b.astype(float)])
    G = len(n)
    rng = np.random.default_rng(_seed(name))
    idx = rng.integers(0, G, size=(draws, G))
    with np.errstate(invalid="ignore", divide="ignore"):
        b = sa[idx].sum(1) / na[idx].sum(1) - sb[idx].sum(1) / nb[idx].sum(1)
    b = np.sort(b[np.isfinite(b)])
    est = va.sum() / in_a.sum() - vb.sum() / in_b.sum()
    return {"n_a": int(in_a.sum()), "n_b": int(in_b.sum()), "games": G,
            "est_pp": 100 * float(est),
            "lo_pp": 100 * float(b[int(0.025 * len(b))]),
            "hi_pp": 100 * float(b[int(0.975 * len(b)) - 1])}


# --------------------------------------------------------------------------
# closing raw prices (the cost line)
# --------------------------------------------------------------------------

_CLOSE_SQL = """
WITH oq AS (
  SELECT mo.outcome_id AS oid, q.ts AS ts, substr(q.venue, 9) AS book,
         q.mid AS raw, q.prob_devig AS p
    FROM quotes q
    JOIN market_outcome mo ON mo.venue = q.venue AND mo.market_id = q.market_id
   WHERE q.source = ? AND q.prob_devig IS NOT NULL AND mo.outcome_id IS NOT NULL
),
kick AS (
  SELECT o.outcome_id AS oid, g.kickoff_ts AS k
    FROM outcomes o JOIN nfl_games g ON g.game_id = o.event_id
),
last AS (
  SELECT oq.oid AS oid, MAX(oq.ts) AS ts FROM oq JOIN kick ON kick.oid = oq.oid
   WHERE oq.ts <= kick.k GROUP BY oq.oid
)
SELECT oq.oid, oq.book, oq.raw, oq.p
  FROM oq JOIN last ON last.oid = oq.oid AND last.ts = oq.ts
"""


def closing_quotes():
    """outcome_id -> [(book, raw, devig)] at the SAME closing snapshot
    calibration.build() medians over (same SQL, plus the raw mid)."""
    con = cal._ro()
    out = {}
    for oid, book, raw, p in con.execute(_CLOSE_SQL, (cal.SOURCE,)):
        out.setdefault(oid, []).append((book, raw, p))
    pair = {}
    for oid, key, ent, stat, line, side, ev in con.execute(
            "SELECT outcome_id, key, entity_id, stat, line, side, event_id FROM outcomes"):
        pair.setdefault((ev, ent, stat, line), {})[side] = oid
    con.close()
    under_of = {}
    for sides in pair.values():
        if "over" in sides and "under" in sides:
            under_of[sides["over"]] = sides["under"]
    return out, under_of


def raw_price(quotes, price):
    """Median raw (vig-inclusive) implied price over the books the population's
    close is a median of: DK/FD/MGM for bench, every book for all."""
    if not quotes:
        return None
    v = [raw for book, raw, _ in quotes
         if raw is not None and (price == "all" or book in cal.BENCH_BOOKS)]
    return statistics.median(v) if v else None


# --------------------------------------------------------------------------
# the study
# --------------------------------------------------------------------------

def load(price, close=None):
    rows, game, _post = cal._register_population(price)
    k = cal._key(price)
    if close is None:
        close = closing_quotes()
    quotes, under_of = close
    out = []
    for r in rows:
        p = getattr(r, k)
        ro = raw_price(quotes.get(r.oid), price)
        uo = under_of.get(r.oid)
        ru = raw_price(quotes.get(uo), price) if uo else None
        out.append({"game": game.get(r.oid), "stat": r.stat, "season": r.season,
                    "p": p, "hit": r.hit, "raw_over": ro, "raw_under": ru})
    if any(x["game"] is None for x in out):
        raise RuntimeError("an outcome has no event_id; cannot block by game")
    return out


def gate(rows):
    games = [x["game"] for x in rows]
    gap = np.array([x["hit"] - x["p"] for x in rows])
    b = boot_mean(games, gap, "gate")
    ok = (b["n"] == R18["n"] and b["games"] == R18["games"]
          and abs(b["est_pp"] - R18["est_pp"]) < 0.005
          and abs(b["lo_pp"] - R18["lo_pp"]) <= GATE_TOL_PP
          and abs(b["hi_pp"] - R18["hi_pp"]) <= GATE_TOL_PP)
    if not ok:
        raise RuntimeError("reproduction gate FAILED: %r against R18 %r" % (b, R18))
    return b


def slice_stats(rows, name):
    """Gap, book cost both sides, net at book and at exchange for the favoured side."""
    games = [x["game"] for x in rows]
    p = np.array([x["p"] for x in rows])
    hit = np.array([x["hit"] for x in rows], float)
    g = boot_mean(games, hit - p, name + "|gap")
    out = {"n": len(rows), "games": g["games"], "priced": float(p.mean()),
           "realized": float(hit.mean()), "gap": g,
           "thin": len(rows) < THIN_N, "readable": g["readable"]}
    # the favoured side: over if realised beat priced, else under
    side = "over" if g["est_pp"] > 0 else "under"
    out["side"] = side
    have_o = [i for i, x in enumerate(rows) if x["raw_over"] is not None]
    have_u = [i for i, x in enumerate(rows) if x["raw_under"] is not None]
    out["book_cost_over_pp"] = (100 * float(np.median(
        [rows[i]["raw_over"] - rows[i]["p"] for i in have_o])) if have_o else None)
    out["book_cost_under_pp"] = (100 * float(np.median(
        [rows[i]["raw_under"] - (1 - rows[i]["p"]) for i in have_u])) if have_u else None)
    out["overround_median"] = (float(np.median(
        [rows[i]["raw_over"] + rows[i]["raw_under"] for i in have_o
         if rows[i]["raw_under"] is not None])) if have_o and have_u else None)
    if side == "over":
        idx = have_o
        net_book = [hit[i] - rows[i]["raw_over"] for i in idx]
        ps = p
        win = hit
    else:
        idx = have_u
        net_book = [(1 - hit[i]) - rows[i]["raw_under"] for i in idx]
        ps = 1 - p
        win = 1 - hit
    out["n_priced_at_book"] = len(idx)
    out["net_book"] = (boot_mean([games[i] for i in idx], np.array(net_book),
                                 name + "|book") if idx else None)
    out["fee_pp"] = 100 * float(np.mean(fee(ps)))
    out["net_exchange"] = boot_mean(games, win - ps - fee(ps), name + "|exch")
    return out


def run(price, close):
    rows = load(price, close)
    res = {"price": price, "n": len(rows),
           "games": len({x["game"] for x in rows})}
    if price == "bench":
        res["gate"] = gate(rows)
    res["buckets"] = []
    for i in range(len(EDGES) - 1):
        sub = [x for x in rows if bucket_of(x["p"]) == i]
        if sub:
            res["buckets"].append(dict(bucket=bucket_label(i),
                                       **slice_stats(sub, "%s|b%d" % (price, i))))
        else:
            res["buckets"].append({"bucket": bucket_label(i), "n": 0})
    if sum(b["n"] for b in res["buckets"]) != len(rows):
        raise RuntimeError("buckets do not partition the population")
    res["zones"] = []
    for zname, lo, hi in ZONES:
        z = [x for x in rows if lo <= x["p"] < hi]
        entry = {"zone": zname, "all": slice_stats(z, "%s|%s" % (price, zname)),
                 "by_stat": {}, "by_season": {}}
        for s in sorted({x["stat"] for x in z}):
            sub = [x for x in z if x["stat"] == s]
            entry["by_stat"][s] = slice_stats(sub, "%s|%s|%s" % (price, zname, s))
        for s in sorted({x["season"] for x in z}):
            sub = [x for x in z if x["season"] == s]
            entry["by_season"][str(s)] = slice_stats(sub, "%s|%s|%d" % (price, zname, s))
        res["zones"].append(entry)
    games = [x["game"] for x in rows]
    gap = np.array([x["hit"] - x["p"] for x in rows])
    p = np.array([x["p"] for x in rows])
    deep, atm = p >= 0.60, (p >= 0.40) & (p < 0.60)
    res["T2_contrast_deep_minus_atm"] = boot_contrast(games, gap, deep, atm,
                                                      price + "|T2")
    return res


# --------------------------------------------------------------------------
# printing
# --------------------------------------------------------------------------

def _iv(b):
    if b is None:
        return "%-24s" % "-"
    s = "%+6.2f [%+6.2f,%+6.2f]" % (b["est_pp"], b["lo_pp"], b["hi_pp"])
    if not b.get("readable", True):
        s += "*"
    return "%-24s" % s


def _verdict(b):
    if b is None or not b.get("readable", True):
        return "NOT READ (<5 games)"
    if b["lo_pp"] > 0:
        return "excludes zero, positive"
    if b["hi_pp"] < 0:
        return "excludes zero, negative"
    return "spans zero - not a finding"


def _row(label, s):
    c_o = s.get("book_cost_over_pp")
    c_u = s.get("book_cost_under_pp")
    return ("  %-26s %6s %5s %6.4f %6.4f %s %-26s | %5s %5s %s | %5.2f %s%s" % (
        label, format(s["n"], ","), s["games"], s["priced"], s["realized"],
        _iv(s["gap"]), _verdict(s["gap"]),
        "%.2f" % c_o if c_o is not None else "-", "%.2f" % c_u if c_u is not None else "-",
        _iv(s["net_book"]), s["fee_pp"], _iv(s["net_exchange"]),
        "  THIN" if s["thin"] else ""))


HEAD = ("  %-26s %6s %5s %6s %6s %-24s %-26s | %5s %5s %-24s | %5s %-24s" % (
    "slice", "n", "games", "priced", "real.", "gap pp [95%]", "reading",
    "c.ovr", "c.und", "net @book, fav side", "fee", "net @exch (0 spread)"))


def print_result(res):
    print("\n" + "=" * 100)
    print("POPULATION %s: n %s, %s games%s" % (
        res["price"].upper(), format(res["n"], ","), res["games"],
        "  (R18's own - SEEN before pre-registration)" if res["price"] == "bench"
        else "  (REGISTERED)"))
    if "gate" in res:
        g = res["gate"]
        print("  reproduction gate: %+.2f [%+.2f, %+.2f] on %s / %s games - PASSED"
              % (g["est_pp"], g["lo_pp"], g["hi_pp"], format(g["n"], ","), g["games"]))
    print("=" * 100)
    print(HEAD)
    for b in res["buckets"]:
        if b["n"] == 0:
            print("  %-26s      0" % b["bucket"])
        else:
            print(_row(b["bucket"], b))
    for z in res["zones"]:
        print("\n  -- %s --" % z["zone"])
        print(_row("ALL", z["all"]))
        for s, v in z["by_stat"].items():
            print(_row(s, v))
        for s, v in z["by_season"].items():
            print(_row(s, v))
    t = res["T2_contrast_deep_minus_atm"]
    print("\n  T2 gap(p>=0.60) - gap(0.40-0.60), one quantity: %+.2f [%+.2f, %+.2f]"
          " (n %s vs %s, %s games) - %s" % (
              t["est_pp"], t["lo_pp"], t["hi_pp"], format(t["n_a"], ","),
              format(t["n_b"], ","), t["games"], _verdict(t)))
    print("  * = fewer than 5 games: printed, not read.  c.ovr / c.und = median book"
          " cost (raw - de-vigged) of taking that side, pp.")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", help="write the aggregates to this JSON file")
    args = ap.parse_args()
    close = closing_quotes()
    results = [run("bench", close), run("all", close)]
    for r in results:
        print_result(r)
    n_iv = sum(1 for r in results for b in r["buckets"] if b["n"]
               for k in ("gap", "net_book", "net_exchange") if b.get(k))
    n_iv += sum(1 + len(z["by_stat"]) + len(z["by_season"])
                for r in results for z in r["zones"])
    print("\nintervals printed (buckets x 3 + zone gaps incl. splits): %d" % n_iv)
    if args.out:
        with open(args.out, "w", encoding="utf-8", newline="\n") as fh:
            json.dump({"generated_by": "research/bias_by_moneyness.py",
                       "preregistration": "docs/C22-bias-by-moneyness-preregistration.md @ 93b9d37",
                       "draws": DRAWS, "seed": SEED, "results": results}, fh, indent=1)
            fh.write("\n")
        print("wrote %s" % args.out)


if __name__ == "__main__":
    main()
