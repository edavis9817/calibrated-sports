"""Brief 021 Part A - score the model against what actually happened.

    python -m research.score            # week 1 2026, the resolved model version

PRE-REGISTRATION - committed before any score, outcome or calibration number
below was computed. Nothing here may be added, removed or re-bucketed after
seeing a result.

  A1 SETTLE. predictions -> outcomes -> nflverse `nfl_player_week` (REG, the
  newest data_version), through `jobs.settle_outcomes` so the rule that decides
  a bet is the one the project already tests. Every claim is a half-integer
  "over", so a push is impossible. A prediction with no stat row is
  UNRESOLVABLE and excluded, never scored as zero, with the reason split:
  "game not in nflverse yet" (no row for either team that week) vs "player has
  no stat row" (team present, player absent).

  THE MODEL VERSION is whatever `core.version_resolve` resolves for the week -
  the same 935 rows every brief since S00 used. Other versions stored for the
  week are listed, not scored.

  A2 SCORE. Three forecasters, all for the Kalshi YES claim ("N+ receptions" =
  over N-0.5, the outcome's own line and side - asserted per row):
    model   `prob_over` at its as-of instant (created_ts)
    market  Kalshi MID at that same instant - last quote at or before it, both
            sides present. NOT de-vigged, though the brief says "de-vigged": an
            exchange mid IS the probability and there is no margin to remove
            (CLAUDE.md, settled). A one-sided book has no mid; those rows leave
            the common set and are counted.
    naive   the player's 2025 REG frequency of X > line over games with >= 1
            opportunity (targets for receptions, carries for rush attempts),
            Laplace (hits+1)/(games+2). Fewer than 4 such games -> the pooled
            (stat, line) rate over every 2025 REG player-game with >= 1
            opportunity, also Laplace. 2025 is wholly before the entry instant.
  Brier, and log loss with p clipped to [1e-4, 1-1e-4]. ONE common set: settled
  AND two-sided at entry. Pairwise differences model-market, model-naive,
  market-naive, each as the mean PAIRED per-prediction difference with a block
  bootstrap over GAMES (2000 draws, fixed seed). HEADLINE: Brier(model) -
  Brier(market); negative means the model is better. Secondary: model - naive
  on the full settled set, one-sided books included.

  A3 CALIBRATION. 10 equal-width bins, model and market. Per bin: n, mean
  forecast, realized rate, WILSON 95%. A bin with n < 30 is printed and flagged
  "not a data point". ECE for both.

  A4 SUBSETS - fixed here, Brier(model) - Brier(market), game block bootstrap:
    series        KXNFLREC | KXNFLRSHATT
    history       predictions.prior_games (recorded by the fit): thin 0-8 |
                  medium 9-24 | thick 25+
    price         entry mid <0.15 | 0.15-0.35 | 0.35-0.65 | 0.65-0.85 | >0.85
    books         single | multi - the fit's book inputs. models/baseline fits
                  on nflverse only; if no book field exists the subset is NOT
                  DEFINABLE and says so. No proxy.
    ladder        edge = lowest or highest threshold of that (player, stat)
                  ladder with any Kalshi quote at or before entry | interior
  A cell under 5 games or 30 predictions is printed and marked too few to read.
  Every cell interval counts as a hypothesis.

  A5 POWER. MDE at 80% power, two-sided 0.05: (1.96 + 0.84) x SE, SE = sd of
  the game-block bootstrap distribution of mean(Brier_model - Brier_market).
  Cross-check: cluster-robust analytic SE by game. Games needed for MDE of
  0.005 / 0.002 / 0.001 under SE ~ 1/sqrt(games), in weeks (16 games) and
  seasons (272 games).

  COUNT. Difference intervals: A2 3 pairs x (Brier, log loss) = 6, secondary 2,
  A4 cells. Reliability bin intervals are counted separately.

AMENDMENT a-58 (2026-09-29) - what "the market at entry" admits. It changes no
bucket, test or statistic above; it restores the meaning A2 always had.
  The market price is a LIVE quote (quotes.source = 'live') no older than
  ENTRY_MAX_AGE before the entry instant. Before a-58 the lookup took the last
  quote of ANY source, so when retention pruned the week-1 live rows (14 days on
  ingest_ts) it fell through to `backfill:kalshi_candles` - for all 935 rows, at
  a median 61,424 s (~17 h) before entry - and the served file compared the
  model with the previous day's price while calling it the market at entry.
  `check_entry` now RAISES on a non-live or stale quote; `load` catches it per
  row, so the row is excluded from every market figure with its reason counted
  (`market_excluded`), and nothing is substituted.
"""
import argparse
import math
import os
import random
import re
import sqlite3
import statistics
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
from research.longshot import wilson  # noqa: E402

EPS = 1e-4
BOOT = 2000
SEED = 2021
Z_ALPHA, Z_POWER = 1.96, 0.8416
MIN_GAMES, MIN_N = 5, 30
BIN_MIN_N = 30
NAIVE_MIN_GAMES = 4
GAMES_PER_WEEK, GAMES_PER_SEASON = 16, 272

STAT_SERIES = {"receptions": "KXNFLREC", "rush_attempts": "KXNFLRSHATT"}
STAT_COL = {"receptions": "receptions", "rush_attempts": "carries"}
OPP_COL = {"receptions": "targets", "rush_attempts": "carries"}
HISTORY = ((0, 8, "thin 0-8"), (9, 24, "medium 9-24"), (25, 10 ** 6, "thick 25+"))
PRICE = ((0.0, 0.15, "<0.15"), (0.15, 0.35, "0.15-0.35"), (0.35, 0.65, "0.35-0.65"),
         (0.65, 0.85, "0.65-0.85"), (0.85, 1.0001, ">0.85"))

# a-58. The only quote source that is a price AT an instant. Candles are
# per-period summaries backfilled after the fact; a book snapshot from another
# venue is a different claim. Neither is "Kalshi at entry".
ENTRY_SOURCE = "live"
# The slowest tier polls every POLL_COLD (600 s) and writes a row per poll.
# Measured by a-58 on 25 week-3 KXNFLREC/KXNFLRSHATT markets, 17,593 consecutive
# live rows: gap p50 302 s, p90 601 s, p99 601 s, max 712 s. 1.5 x POLL_COLD
# admits every observed gap and refuses anything a poll cycle cannot explain.
ENTRY_MAX_AGE = 1.5 * config.POLL_COLD

# Exclusion reasons, published verbatim as counts.
NO_QUOTE = "no quote at or before entry"
NON_LIVE = "entry quote is not live capture"
STALE = "live entry quote older than the age limit"
ONE_SIDED = "live entry quote is one-sided (no mid)"


class EntryQuoteRefused(ValueError):
    """The entry quote is not the market at entry. `.reason` is one of the
    exclusion reasons above; the row is excluded, never re-priced."""

    def __init__(self, reason, detail=""):
        super().__init__(f"{reason}{': ' + detail if detail else ''}")
        self.reason = reason


# =============================================================================
# pure pieces
# =============================================================================

def clip(p):
    return min(max(p, EPS), 1 - EPS)


def brier(p, y):
    return (p - y) ** 2


def logloss(p, y):
    p = clip(p)
    return -(y * math.log(p) + (1 - y) * math.log(1 - p))


def laplace(hits, n):
    return (hits + 1) / (n + 2)


def naive_prob(player_games, pooled, line):
    """player_games: [(stat, opp)] for 2025 REG. pooled: (hits, n) at this line."""
    eligible = [s for s, o in player_games if (o or 0) >= 1]
    if len(eligible) >= NAIVE_MIN_GAMES:
        return laplace(sum(1 for s in eligible if (s or 0) > line), len(eligible)), "player"
    return laplace(*pooled), "pooled"


def bucket(value, table):
    for lo, hi, name in table:
        if lo <= value <= hi if isinstance(lo, int) else lo <= value < hi:
            return name
    return None


def ladder_key(market_id):
    return re.sub(r"-\d+$", "", market_id)


def rung(market_id):
    m = re.search(r"-(\d+)$", market_id)
    return int(m.group(1)) if m else None


def ladder_position(market_id, listed_ids):
    """edge if this rung is the lowest or highest threshold among the rungs of
    its ladder that were listed at entry (itself included)."""
    rungs = [rung(m) for m in listed_ids if ladder_key(m) == ladder_key(market_id)]
    rungs = [r for r in rungs if r is not None] + [rung(market_id)]
    return "edge" if rung(market_id) in (min(rungs), max(rungs)) else "interior"


def check_entry(q, entry_ts, max_age=None):
    """The Kalshi mid at entry, or EntryQuoteRefused. `q` is (ts, best_bid,
    best_ask, source) or None. Raises rather than warns and never substitutes:
    a row with no live price at entry is not scorable against the market."""
    max_age = ENTRY_MAX_AGE if max_age is None else max_age
    if q is None:
        raise EntryQuoteRefused(NO_QUOTE)
    ts, bid, ask, source = q
    if source != ENTRY_SOURCE:
        raise EntryQuoteRefused(NON_LIVE, f"source {source!r}, {entry_ts - ts:.0f}s before entry")
    if entry_ts - ts > max_age:
        raise EntryQuoteRefused(STALE, f"{entry_ts - ts:.0f}s > {max_age:.0f}s")
    if bid is None or ask is None:
        raise EntryQuoteRefused(ONE_SIDED)
    return (bid + ask) / 2


def entry_quote(c, market_id, ts):
    """The quote `check_entry` judges: the last LIVE quote with an ask at or
    before `ts`; failing that, the last quote of any source, so the refusal can
    name what was there instead of reporting a bare absence."""
    q = c.execute(
        "SELECT ts, best_bid, best_ask, source FROM quotes WHERE venue='kalshi' "
        "AND market_id=? AND ts <= ? AND source=? AND best_ask IS NOT NULL "
        "ORDER BY ts DESC LIMIT 1", (market_id, ts, ENTRY_SOURCE)).fetchone()
    return q or c.execute(
        "SELECT ts, best_bid, best_ask, source FROM quotes WHERE venue='kalshi' "
        "AND market_id=? AND ts <= ? AND best_ask IS NOT NULL "
        "ORDER BY ts DESC LIMIT 1", (market_id, ts)).fetchone()


def by_game(rows):
    g = defaultdict(list)
    for r in rows:
        g[r["game"]].append(r)
    return g


def boot_mean(rows, value, n=BOOT, seed=SEED):
    """Mean of value(row) with a block bootstrap over games. Returns est, lo,
    hi, sd of the bootstrap distribution, n, games."""
    g = by_game([r for r in rows if value(r) is not None])
    keys = list(g)
    flat = [value(r) for k in keys for r in g[k]]
    if len(keys) < 2 or not flat:
        return None
    sums = {k: (sum(value(r) for r in g[k]), len(g[k])) for k in keys}
    rng = random.Random(seed)
    draws = []
    for _ in range(n):
        s = c = 0
        for _ in keys:
            a, b = sums[keys[rng.randrange(len(keys))]]
            s += a
            c += b
        draws.append(s / c)
    draws.sort()
    return {"est": statistics.fmean(flat), "lo": draws[int(0.025 * n)],
            "hi": draws[min(n - 1, int(0.975 * n))], "sd": statistics.pstdev(draws),
            "n": len(flat), "games": len(keys)}


def cluster_se(rows, value):
    """Cluster-robust SE of a mean, clusters = games (CR1)."""
    vals = [(r["game"], value(r)) for r in rows if value(r) is not None]
    n = len(vals)
    mean = statistics.fmean(v for _, v in vals)
    s = defaultdict(float)
    for g, v in vals:
        s[g] += v - mean
    G = len(s)
    if G < 2:
        return None
    return math.sqrt(G / (G - 1) * sum(x * x for x in s.values())) / n


def mde(se):
    return (Z_ALPHA + Z_POWER) * se


def games_for_mde(se, games, target):
    """SE ~ 1/sqrt(games): games needed so that (z_a + z_b) * SE <= target."""
    return games * (mde(se) / target) ** 2


def reliability(ps, ys, bins=10):
    out, N, ece = [], len(ps), 0.0
    for b in range(bins):
        lo, hi = b / bins, (b + 1) / bins
        idx = [i for i, p in enumerate(ps) if lo <= p < hi or (b == bins - 1 and p == 1.0)]
        if not idx:
            out.append({"lo": lo, "hi": hi, "n": 0})
            continue
        k = sum(ys[i] for i in idx)
        mf = statistics.fmean(ps[i] for i in idx)
        rate = k / len(idx)
        w = wilson(k, len(idx))
        ece += len(idx) / N * abs(rate - mf)
        out.append({"lo": lo, "hi": hi, "n": len(idx), "mean_p": mf, "rate": rate,
                    "wilson": w, "excludes": len(idx) >= BIN_MIN_N and not (w[0] <= mf <= w[1])})
    return out, ece


# =============================================================================
# loading
# =============================================================================

def ro():
    return sqlite3.connect(f"file:{config.DB_PATH}?mode=ro", uri=True)


def teams_of(game_id):
    parts = game_id.split("_")
    return parts[2], parts[3]


def load(season=2026, week=1):
    from core import version_resolve
    from jobs.settle_outcomes import settle_one, UNSETTLED, OVER
    from models import baseline

    mv, note = version_resolve.resolve(season, week, baseline.MODEL_VERSION)
    c = ro()
    versions = c.execute(
        "SELECT p.model_version, COUNT(*), MIN(p.created_ts), MAX(p.created_ts) "
        "FROM predictions p JOIN outcomes o USING (outcome_id) WHERE o.season=? "
        "AND o.week=? GROUP BY 1", (season, week)).fetchall()
    raw = c.execute(
        "SELECT p.prediction_id, p.prob_over, p.created_ts, p.prior_games, p.params_json, "
        "o.outcome_id, o.key, o.sport, o.season, o.week, o.entity_type, o.entity_id, "
        "o.stat, o.line, o.side, o.push_possible, o.event_id, mo.market_id "
        "FROM predictions p JOIN outcomes o USING (outcome_id) "
        "JOIN market_outcome mo ON mo.outcome_id = p.outcome_id AND mo.venue='kalshi' "
        "WHERE o.season=? AND o.week=? AND p.model_version=?", (season, week, mv)).fetchall()

    teams_present = {t for (t,) in c.execute(
        "SELECT DISTINCT team FROM nfl_player_week WHERE season=? AND week=? "
        "AND season_type='REG'", (season, week))}
    kick = {g: k for g, k in c.execute(
        "SELECT game_id, MAX(kickoff_ts) FROM nfl_games GROUP BY game_id")}

    # naive inputs: 2025 REG, newest version per player-week
    hist = defaultdict(list)
    for gsis, wk, rec, tgt, car in c.execute(
            "SELECT gsis_id, week, receptions, targets, carries FROM nfl_player_week w "
            "WHERE season=? AND season_type='REG' AND data_version = (SELECT MAX(data_version) "
            "FROM nfl_player_week v WHERE v.gsis_id=w.gsis_id AND v.season=w.season "
            "AND v.week=w.week AND v.season_type='REG')", (season - 1,)):
        hist[(gsis, "receptions")].append((rec, tgt))
        hist[(gsis, "rush_attempts")].append((car, car))
    pooled_rows = defaultdict(list)
    for (gsis, stat), games in hist.items():
        pooled_rows[stat] += [s for s, o in games if (o or 0) >= 1]

    rows, census, fields = [], Counter(), set()
    for (pid, prob, created, prior_games, params_json, oid, key, sport, s, w, etype,
         entity, stat, line, side, push, game, market_id) in raw:
        fields |= set(__import__("json").loads(params_json or "{}"))
        assert side == "over", f"{pid}: side {side} - orientation assumption broken"
        assert market_id.startswith(STAT_SERIES[stat] + "-"), f"{pid}: {market_id} vs {stat}"
        assert rung(market_id) is not None and rung(market_id) - 0.5 == line, \
            f"{pid}: Kalshi rung {market_id} is not over {line}"
        result, actual, _v, _void = settle_one(
            c, (oid, key, sport, s, w, etype, entity, stat, line, side, push))
        if result == UNSETTLED:
            home, away = teams_of(game)[1], teams_of(game)[0]
            census["unresolvable: game not in nflverse yet" if not ({home, away} & teams_present)
                   else "unresolvable: player has no stat row"] += 1
            continue
        census["settled"] += 1
        y = 1.0 if result == OVER else 0.0
        q = entry_quote(c, market_id, created)
        try:
            mid, refused = check_entry(q, created), None
        except EntryQuoteRefused as e:
            mid, refused = None, e.reason
        pooled = pooled_rows[stat]
        np_, nsrc = naive_prob(hist.get((entity, stat), []),
                               (sum(1 for x in pooled if (x or 0) > line), len(pooled)), line)
        rows.append({"pid": pid, "game": game, "market": market_id, "stat": stat,
                     "series": STAT_SERIES[stat], "line": line, "y": y,
                     "model": prob, "market_p": mid, "market_excluded": refused,
                     "entry_quote_source": q[3] if q else None,
                     "entry_quote_age": created - q[0] if q else None,
                     "naive": np_, "naive_src": nsrc,
                     "prior_games": prior_games, "entry_ts": created,
                     "kickoff": kick.get(game)})

    listed = {}
    for r in rows:
        k = (ladder_key(r["market"]), r["entry_ts"])
        if k not in listed:
            ids = [m for (m,) in c.execute(
                "SELECT market_id FROM markets WHERE venue='kalshi' AND market_id LIKE ?",
                (ladder_key(r["market"]) + "-%",))]
            listed[k] = [m for m in ids if ladder_key(m) == ladder_key(r["market"]) and
                         c.execute("SELECT 1 FROM quotes WHERE venue='kalshi' AND market_id=? "
                                   "AND ts <= ? LIMIT 1", (m, r["entry_ts"])).fetchone()]
        r["ladder"] = ladder_position(r["market"], listed[k])
    book_fields = sorted(f for f in fields if "book" in f.lower())
    return rows, census, mv, note, versions, book_fields, sorted(fields)


def published(rows, registered=None):
    """The block the site publishes as research/calibration.json (a-36).

    Brier and ECE are computed HERE, together, on ONE set - the common set of
    `rows` where Kalshi was two-sided at entry - so a page reading the file gets
    both from the same rows. Before a-36 the Method page cited its ECE from
    DECISIONS.md (the n=706 set) beside a chart reading this computation (n=935):
    two calibration errors for one claim, one of them from a different
    population. `ece` duplicates `series[].ece` on purpose, from the SAME value,
    so a consumer never has to search an array for a headline figure; the
    metric-registry gate asserts the two agree.

    Values are full precision; rounding is the exporter's display decision.

    a-58: the market comparison is PUBLISHED only when at least one row has a
    live entry quote (`check_entry`), and then every figure is on that scorable
    set, as before. Otherwise it is WITHDRAWN: the market series, the market
    Brier and ECE and the model-market interval are None, and the model-only
    figures - which need no price - stand on every settled row. Either way the
    refusals are counted by reason in `market_comparison`. `registered` is the
    register's R10 row, carried as history; it is compared, never substituted.
    """
    scorable = [r for r in rows if r["market_p"] is not None]
    published_ = bool(scorable)
    pop = scorable if published_ else rows
    fields = (("model", "model"), ("market", "market_p")) if published_ else (("model", "model"),)
    series, ece = [], {"model": None, "market": None}
    for name, field in fields:
        table, e = reliability([r[field] for r in pop], [r["y"] for r in pop])
        ece[name] = e
        series.append({"name": name, "ece": e, "bins": table})
    br = {f: statistics.fmean(brier(r[k], r["y"]) for r in pop) if pop else None
          for f, k in (("model", "model"), ("naive", "naive"))}
    br["market"] = (statistics.fmean(brier(r["market_p"], r["y"]) for r in pop)
                    if published_ else None)
    head = (boot_mean(pop, lambda r: brier(r["model"], r["y"]) - brier(r["market_p"], r["y"]))
            if published_ else None)
    # ON `pop`, NOT on a different set - see export_web.build_research.
    nv = boot_mean(pop, lambda r: brier(r["model"], r["y"]) - brier(r["naive"], r["y"]))
    return {"n": len(pop), "games": len(by_game(pop)), "series": series,
            "brier": br, "ece": ece, "model_minus_market": head, "model_minus_naive": nv,
            "market_comparison": market_comparison(rows, head, registered)}


def market_comparison(rows, head, registered=None):
    """What happened to the market comparison, in fields and in one sentence
    generated from them. `head` is the model-market bootstrap on the scorable
    set, or None when nothing was scorable."""
    scorable = [r for r in rows if r["market_p"] is not None]
    refused = [r for r in rows if r["market_p"] is None]
    reasons = Counter(r["market_excluded"] for r in refused)
    sources = Counter(r["entry_quote_source"] for r in refused if r["entry_quote_source"])
    ages = [r["entry_quote_age"] for r in refused if r["entry_quote_age"] is not None
            and r["market_excluded"] in (NON_LIVE, STALE)]
    med_age = statistics.median(ages) if ages else None
    out = {"status": "published" if scorable else "withdrawn",
           "entry_rule": (f"a live Kalshi quote (quotes.source = '{ENTRY_SOURCE}') with both "
                          f"sides, at most {ENTRY_MAX_AGE:.0f}s before the prediction instant"),
           "settled": len(rows), "scorable": len(scorable),
           "excluded": [{"reason": k, "n": v} for k, v in sorted(reasons.items())],
           "refused_quote_sources": [{"source": k, "n": v} for k, v in sorted(sources.items())],
           "refused_quote_median_age_s": med_age, "registered": None}
    if registered:
        same = bool(head) and len(scorable) == registered["n"] and \
            round(head["est"], 4) == round(registered["estimate"], 4)
        if same:
            why = "re-derived from the live entry quotes at the registered n and estimate"
        else:
            src = ", ".join(f"{s['source']} {s['n']}" for s in out["refused_quote_sources"]) or "none"
            why = (f"{len(refused)} of {len(rows)} settled predictions have no live Kalshi quote "
                   f"at entry - live quotes are pruned {config.QUOTES_RETENTION_DAYS:g} days after "
                   f"ingestion - so {len(scorable)} are scorable against the market, against "
                   f"{registered['n']} when the figure was registered. What remains before entry "
                   f"is not a live price (refused quote sources: {src}"
                   + (f"; median {med_age:,.0f}s before entry" if med_age is not None else "")
                   + ").")
        out["registered"] = {**registered, "re_derivable": same, "why": why}
    return out


# =============================================================================
# report
# =============================================================================

def _hdr(t):
    print("\n" + "=" * 78 + "\n" + t + "\n" + "=" * 78)


def iv(res, scale=1.0, dp=4):
    if not res:
        return "n/a"
    star = "*" if res["lo"] > 0 or res["hi"] < 0 else " "
    few = "" if res["games"] >= MIN_GAMES and res["n"] >= MIN_N else "  <- too few to read"
    return (f"{scale * res['est']:+.{dp}f} [{scale * res['lo']:+.{dp}f}, "
            f"{scale * res['hi']:+.{dp}f}]{star} n={res['n']} games={res['games']}{few}")


def report(season, week):
    rows, census, mv, note, versions, book_fields, param_fields = load(season, week)
    tests, bins_counted = [], 0

    _hdr("A1 - SETTLEMENT")
    if note:
        print(f"  {note}")
    print(f"  model version scored: {mv}")
    for v, n, lo, hi in versions:
        print(f"    stored for this week: {v:<36} {n:>5} rows{'  <- scored' if v == mv else ''}")
    total = sum(census.values())
    print(f"  predictions {total}")
    for k, v in sorted(census.items()):
        print(f"    {k:<48}{v:>5}")
    common = [r for r in rows if r["market_p"] is not None]
    for reason, n in sorted(Counter(r["market_excluded"] for r in rows
                                    if r["market_excluded"]).items()):
        print(f"    settled, excluded from the market: {reason:<48}{n:>5}")
    print(f"    -> common set {len(common)}, games {len(by_game(common))}, "
          f"(player, stat) fits {len({(ladder_key(r['market'])) for r in common})}")
    if not common:
        # a-58: nothing is scorable against the market at entry. Say so and stop
        # rather than scoring a substitute price.
        mc = market_comparison(rows, None)
        print(f"\n  MARKET COMPARISON WITHDRAWN - {mc['entry_rule']} exists for 0 of "
              f"{len(rows)} settled predictions. Refused quote sources: "
              f"{mc['refused_quote_sources']}, median age {mc['refused_quote_median_age_s']}s.")
        for metric, fn in (("Brier", brier), ("log loss", logloss)):
            print(f"    {metric:<8} model-naive (full settled)  "
                  f"{iv(boot_mean(rows, lambda r, fn=fn: fn(r['model'], r['y']) - fn(r['naive'], r['y'])))}")
        table, ece = reliability([r["model"] for r in rows], [r["y"] for r in rows])
        print(f"    model ECE on the full settled set {ece:.4f}")
        return
    print(f"  naive source: {dict(Counter(r['naive_src'] for r in common))}")
    print(f"  base rate (common set): {statistics.fmean(r['y'] for r in common):.4f}")

    _hdr("A2 - THREE-WAY SCORE on the common set")
    for name, f in (("model", "model"), ("market", "market_p"), ("naive", "naive")):
        b = statistics.fmean(brier(r[f], r["y"]) for r in common)
        ll = statistics.fmean(logloss(r[f], r["y"]) for r in common)
        print(f"    {name:<7} Brier {b:.4f}   log loss {ll:.4f}")
    print("\n  paired differences (negative = first forecaster better), game block bootstrap")
    for a, fa, b_, fb in (("model", "model", "market", "market_p"),
                          ("model", "model", "naive", "naive"),
                          ("market", "market_p", "naive", "naive")):
        for metric, fn in (("Brier", brier), ("log loss", logloss)):
            res = boot_mean(common, lambda r, fa=fa, fb=fb, fn=fn: fn(r[fa], r["y"]) - fn(r[fb], r["y"]))
            tests.append((f"A2 {metric} {a}-{b_}", res))
            print(f"    {metric:<8} {a}-{b_:<7} {iv(res)}")
    head = boot_mean(common, lambda r: brier(r["model"], r["y"]) - brier(r["market_p"], r["y"]))
    print(f"\n  HEADLINE Brier(model) - Brier(market) = {iv(head)}")
    print("\n  secondary: model - naive on the FULL settled set (one-sided books included)")
    for metric, fn in (("Brier", brier), ("log loss", logloss)):
        res = boot_mean(rows, lambda r, fn=fn: fn(r["model"], r["y"]) - fn(r["naive"], r["y"]))
        tests.append((f"A2 secondary {metric} model-naive (full settled)", res))
        print(f"    {metric:<8} model-naive  {iv(res)}")

    _hdr("A3 - CALIBRATION (10 equal-width bins, Wilson 95%)")
    for name, f in (("model", "model"), ("market", "market_p")):
        table, ece = reliability([r[f] for r in common], [r["y"] for r in common])
        print(f"\n  [{name}]  ECE {ece:.4f}")
        print(f"    {'bin':<11}{'n':>5}{'mean p':>9}{'realized':>10}   wilson 95%")
        for t in table:
            if not t["n"]:
                print(f"    {t['lo']:.1f}-{t['hi']:.1f}  {0:>5}")
                continue
            bins_counted += 1
            flag = ("  not a data point (n<30)" if t["n"] < BIN_MIN_N else
                    "  <- priced value OUTSIDE the interval" if t["excludes"] else "")
            print(f"    {t['lo']:.1f}-{t['hi']:.1f}  {t['n']:>5}{t['mean_p']:>9.3f}{t['rate']:>10.3f}"
                  f"   [{t['wilson'][0]:.3f}, {t['wilson'][1]:.3f}]{flag}")

    _hdr("A4 - PRE-SPECIFIED SUBSETS: Brier(model) - Brier(market)")
    diff = lambda r: brier(r["model"], r["y"]) - brier(r["market_p"], r["y"])  # noqa: E731
    cells = [("series", lambda r: r["series"], ("KXNFLREC", "KXNFLRSHATT")),
             ("history", lambda r: bucket(r["prior_games"] or 0, HISTORY), [h[2] for h in HISTORY]),
             ("price", lambda r: bucket(r["market_p"], PRICE), [p[2] for p in PRICE]),
             ("ladder", lambda r: r["ladder"], ("edge", "interior"))]
    for fam, key, names in cells:
        print(f"\n  {fam}")
        for nm in names:
            sub = [r for r in common if key(r) == nm]
            res = boot_mean(sub, diff) if sub else None
            tests.append((f"A4 {fam}={nm}", res))
            print(f"    {nm:<12} " + (iv(res) if res else f"n={len(sub)}: empty or one game - not estimable"))
    print("\n  books")
    if book_fields:
        print(f"    book-like fit fields found: {book_fields} - subset needs defining from them")
    else:
        print(f"    NOT DEFINABLE - the fit's recorded params are {param_fields} and none "
              "names a book; models/baseline fits on nflverse only. No proxy substituted.")

    _hdr("A5 - POWER")
    se_b = head["sd"]
    se_c = cluster_se(common, diff)
    G = head["games"]
    print(f"  effective sample: {len(common)} predictions, "
          f"{len({ladder_key(r['market']) for r in common})} (player, stat) fits, {G} games")
    print(f"  SE of mean Brier difference: bootstrap {se_b:.5f}   cluster-robust {se_c:.5f}")
    print(f"  MDE at 80% power, alpha 0.05 two-sided: {mde(se_b):.4f} (bootstrap)   "
          f"{mde(se_c):.4f} (cluster-robust)")
    print(f"  observed difference {head['est']:+.4f}")
    for target in (0.005, 0.002, 0.001):
        g = games_for_mde(se_b, G, target)
        print(f"    MDE {target:.3f} needs ~{g:,.0f} games = {g / GAMES_PER_WEEK:,.1f} weeks "
              f"= {g / GAMES_PER_SEASON:.2f} seasons")

    _hdr("HYPOTHESIS COUNT")
    est = [t for t in tests if t[1]]
    print(f"  difference intervals {len(tests)} (estimable {len(est)}, excluding zero "
          f"{sum(1 for _, r in est if r['lo'] > 0 or r['hi'] < 0)}, of those readable "
          f"{sum(1 for _, r in est if (r['lo'] > 0 or r['hi'] < 0) and r['games'] >= MIN_GAMES and r['n'] >= MIN_N)})")
    print(f"  reliability bin intervals {bins_counted}; books subset not definable (0)")
    for name, r in tests:
        print(f"    {name:<44} {iv(r)}")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--season", type=int, default=2026)
    ap.add_argument("--week", type=int, default=1)
    a = ap.parse_args()
    report(a.season, a.week)


if __name__ == "__main__":
    main()
