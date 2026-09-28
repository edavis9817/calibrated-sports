"""The Lab library, published as static results (unit a-32). No service.

    python -m jobs.lab_publish --universe D:/scratch/u1 --dest D:/scratch/lab
    python -m jobs.lab_publish --universe ... --dest ... --dry-run
    python -m jobs.lab_publish --check --keys --dest D:/scratch/lab

Writes, under `--dest` (there is NO default - config has no defaults, and a job
that can publish must be told where):

    lab/nfl/index.json               kind lab_index    - every launch preset, run or not
    lab/nfl/presets/{key}.json       kind lab_preset   - one per preset that runs
    lab/nfl/catalogue.json           kind lab_catalogue - every condition a rule can
                                     use, with its derived range (a-45)

WHY STATIC. a-27 built `lab.run(strategy, universe)` as a pure function and stood
up no service, because serving it (audit option B) turned on The Odds API's
display terms. f-17 read them (public, updated 31 Aug 2026): the word
"historical" appears zero times - SILENT on historical display, not permissive.
So the Library ships the conservative way: the launch presets run here, through
the same `lab.run` a user's rule would, and only AGGREGATES leave the server.

WHAT NEVER LEAVES THE SERVER, AND WHY IT IS NOT AN OVERSIGHT. No per-bet book
price (American or decimal), no per-bet book name, no per-bet stake or profit
(a flat winning bet's profit IS its decimal price minus one), and no PER-BET
equity chart (consecutive points of a cumulative-units curve difference back to
per-bet profit). Beyond that, a cell's ROI and units are published only where
they average at least MIN_CLEARED cleared bets' prices: an ROI over one cleared
bet, with the counts beside it, is that bet's price. `BET_LIST_RESTRICTION` is
the sentence every file carries so nobody "fixes" this later.

THE RETURNS SERIES (a-44). What the restriction forbids is a curve that
differences back to ONE bet's price, not a returns series as such. `returns`
is cumulative units by WEEK BLOCK (`lab.engine.block_series`), each step
holding at least MIN_CLEARED cleared bets - the same floor a published cell
meets - and never crossing a season, so neither consecutive points nor a
season cell less its steps isolates fewer. `verify_series` re-derives that
from the server-side bet list on every run and REFUSES to publish otherwise.
The luck band (`luck.paths`) is published at those steps only: at bet
granularity its first point was the 95th percentile of ONE random bet's profit,
which is a book price minus one. `drawdown.max_units` is published only when
`verify_drawdown` finds that nothing it combines with isolates fewer than
MIN_CLEARED cleared bets.

THE PREFIX. `lab/` is a top-level prefix this builder owns WHOLLY and fills
wholly: `sync_keys(dest, files, ["lab/"])` deletes any local `lab/` key it did
not produce this run (CLAUDE.md, one builder per prefix). The index is written
on every run and names every file, so a run that produced nothing has nothing to
own and refuses instead.

SOURCES. The files are built from the universe table, which `lab.universe`
reads from the store. The registry scans `lab.universe` as a side producer
(jobs.source_registry.SIDE_PRODUCERS), so what these kinds read is derived from
its SQL, not typed. The runtime authorizer does NOT see those reads - they happen
when the universe is built, usually in another process - so the static scan is
the whole check here.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from jobs import export_web as E  # noqa: E402
from jobs import source_registry as R  # noqa: E402

SPORT = "nfl"
PREFIX = "lab/"
INDEX_KEY = f"lab/{SPORT}/index.json"
CATALOGUE_KEY = f"lab/{SPORT}/catalogue.json"
MIN_CLEARED = 10

PROPS = ["receptions", "receiving_yards", "rush_attempts", "tackles_assists", "sacks"]
PROP_SEASONS = {"from": 2023, "to": 2025, "season_type": "REG", "weeks": {"from": 1, "to": 18}}
GAME_SEASONS = {"from": 1999, "to": 2025, "season_type": "REG", "weeks": {"from": 1, "to": 18}}

BET_LIST_RESTRICTION = (
    "Deliberately restricted: no bet carries its book price, its book, its stake or its "
    "profit, and the file has no bet-by-bet equity curve. The prices are The Odds API's, "
    "whose terms (read 2026-09-24, updated 31 Aug 2026) say nothing either way about "
    "displaying historical odds, so they stay on the server and only aggregates leave it. "
    "A winning flat bet's profit is its price minus one, and consecutive points of a "
    "bet-by-bet cumulative curve difference back to it, so those are withheld for the same "
    "reason. ROI and units appear only where they average at least %d cleared bets' "
    "prices. The returns series is allowed where that curve is not because it is the same "
    "kind of aggregate: cumulative units by week block, each step holding at least %d "
    "cleared bets and none crossing a season, so no two consecutive points - and no season "
    "total less its steps - difference back to fewer than %d prices. The worst drawdown's "
    "size is published only under the same test. Do not add these fields back, or make the "
    "steps finer, without a decision that the terms allow it."
    % (MIN_CLEARED, MIN_CLEARED, MIN_CLEARED))

LAUNCH_SOURCE = "AUDIT-2026-09-24, the Lab library launch set"

# The launch set, in the audit's order. Each is a lab.strategy/1 exactly as a
# user would enter it; everything unstated takes the Lab's defaults (main line,
# one bet per player per week, the three-book consensus close, flat 1 unit, the
# latest complete season HELD OUT and not revealed) - which is what "Open in Lab"
# would show. A preset the engine cannot run is still listed, with the engine's
# own refusal, so the Library shows it as known-and-excluded.
PRESETS = [
    {"key": "fade_every_over", "title": "Fade every over",
     "strategy": {"schema": "lab.strategy/1", "sport": "nfl", "bet_type": "prop",
                  "name": "Fade every over", "markets": PROPS,
                  "seasons": PROP_SEASONS, "side": "under"}},
    {"key": "rush_attempts_unders", "title": "Rush attempts unders",
     "strategy": {"schema": "lab.strategy/1", "sport": "nfl", "bet_type": "prop",
                  "name": "Rush attempts unders", "markets": ["rush_attempts"],
                  "seasons": PROP_SEASONS, "side": "under"}},
    # player.streak >= 3: the player went over THIS line in each of his last three
    # or more prior games. tackles_assists is absent because the streak feature is
    # refused on it (def_tackles_with_assist is reclassified upstream - a-27).
    {"key": "chase_the_streak", "title": "Chase the streak",
     "strategy": {"schema": "lab.strategy/1", "sport": "nfl", "bet_type": "prop",
                  "name": "Chase the streak",
                  "markets": ["receptions", "receiving_yards", "rush_attempts", "sacks"],
                  "seasons": PROP_SEASONS, "side": "over",
                  "conditions": [{"feature": "player.streak", "op": ">=", "value": 3}]}},
    {"key": "model_leans", "title": "Follow the model's leans",
     "strategy": {"schema": "lab.strategy/1", "sport": "nfl", "bet_type": "prop",
                  "name": "Follow the model's leans",
                  "markets": ["receptions", "rush_attempts"],
                  "seasons": PROP_SEASONS, "side": "model_lean"}},
    {"key": "buy_the_middle", "title": "Buy the middle", "register": "R16"},
    {"key": "arbitrage_between_books", "title": "Arbitrage between books", "register": "R16"},
    {"key": "home_underdogs_3_to_7", "title": "Home underdogs of 3 to 7",
     "strategy": {"schema": "lab.strategy/1", "sport": "nfl", "bet_type": "spread",
                  "name": "Home underdogs of 3 to 7", "markets": ["spread"],
                  "seasons": GAME_SEASONS, "side": "underdog",
                  "conditions": [{"feature": "game.home", "op": "==", "value": True},
                                 {"feature": "price.line", "op": "between", "value": [3, 7]}]}},
    {"key": "unders_in_the_wind", "title": "Unders in the wind",
     "strategy": {"schema": "lab.strategy/1", "sport": "nfl", "bet_type": "total",
                  "name": "Unders in the wind", "markets": ["total"],
                  "seasons": GAME_SEASONS, "side": "under",
                  "conditions": [{"feature": "weather.wind_forecast", "op": ">=",
                                  "value": 15}]}},
]


def contract_strategy(schema):
    """lab/strategy.schema.json -> the contract's `LabStrategy`, by exactly three
    mechanical changes, so the shape is written once and the copy is checked
    (tests/test_lab_publish.py), never maintained by hand:

      - `sport` becomes a plain string: the contract names no sport (the file's
        envelope carries it), and lab.strategy/1 still pins it where rules are
        validated;
      - `maxLength` is dropped: the site's type generator cannot read it, and it
        constrains a value, not a type. Every published strategy has already
        passed `lab.strategy.validate_shape`, which enforces it;
      - a condition's `value`, which lab.strategy/1 leaves unconstrained (`{}`),
        becomes a scalar or a list of scalars: the generator refuses a bare `{}`
        rather than typing it `any`, and every operator takes one of those two.
        This NARROWS the contract below the strategy schema - a rule whose value
        is an object would fail at export, loudly, which is the right place.
    """
    import copy

    def strip(node):
        if isinstance(node, dict):
            return {k: strip(v) for k, v in node.items() if k != "maxLength"}
        if isinstance(node, list):
            return [strip(v) for v in node]
        return node
    out = strip({k: v for k, v in copy.deepcopy(schema).items()
                 if k not in ("$schema", "$id", "title", "description")})
    out["properties"]["sport"] = {"type": "string"}
    scalar = [{"type": "number"}, {"type": "string"}, {"type": "boolean"}]
    cond = out["properties"]["conditions"]["items"]["properties"]
    cond["value"] = {"anyOf": scalar + [{"type": "array", "items": {"anyOf": scalar}}]}
    return out


def _not_expressible():
    from lab.presets import NOT_EXPRESSIBLE
    return NOT_EXPRESSIBLE


class NothingToPublish(SystemExit):
    pass


def iso_z(text):
    """'2026-09-24T17:03:11+00:00' -> '2026-09-24T17:03:11Z' (the contract's Timestamp)."""
    t = dt.datetime.fromisoformat(text)
    return t.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _rate(k, n, lo, hi):
    """Wilson hit rate as a closed object, or None when nothing was graded."""
    if not n or lo is None or hi is None:
        return None
    return {"value": round(k / n, 6), "ci": [lo, hi], "k": int(k), "n": int(n),
            "method": "wilson"}


def _boot(value, lo, hi, n, blocks, draws):
    if value is None or lo is None or hi is None:
        return None
    return {"value": value, "ci": [lo, hi], "n": int(n), "blocks": int(blocks),
            "method": "week_block_bootstrap", "draws": int(draws)}


def _withheld(cleared):
    if cleared >= MIN_CLEARED:
        return None
    return ("ROI and units withheld: they would average %d cleared bet price%s, fewer than "
            "%d, and could be read back to a book price" % (cleared, "" if cleared == 1 else "s",
                                                           MIN_CLEARED))


def _cell(c, draws):
    graded = c["cleared"] + c["missed"]
    staked = graded + c["push"]
    why = _withheld(c["cleared"])
    key = c["key"]
    return {"key": str(key), "bets": c["bets"], "cleared": c["cleared"],
            "missed": c["missed"], "push": c["push"], "void": c["void"],
            "hit_rate": _rate(c["cleared"], graded, c["hit_rate_lo"], c["hit_rate_hi"]),
            "roi": None if why else _boot(c["roi"], c["roi_lo"], c["roi_hi"], staked,
                                          c["weeks"], draws),
            "thin": bool(c["thin"]), "withheld": why}


def _unit(strategy):
    """The staking unit `lab.engine._stakes` divides profit by, re-stated here so
    the checks below re-derive units from the bet list rather than trusting it."""
    st = strategy["staking"]
    if st["method"] == "pct_bankroll":
        return st["pct"] * float(st["bankroll"])
    return st.get("unit", 1) or 1


def _ok_fragment(n_cleared):
    """A set of bets whose profit a reader can infer is safe when it averages at
    least MIN_CLEARED prices, or none at all (a set with no cleared bet sums to
    minus its misses: a count, no price)."""
    return n_cleared == 0 or n_cleared >= MIN_CLEARED


class SeriesCheck:
    """What `verify_series` approved. Refuses truth-testing: read `.statement`
    (CLAUDE.md, a guard returns the statement it approved)."""

    def __init__(self, statement, members):
        self.statement = statement
        self.members = members

    def __bool__(self):
        raise TypeError("SeriesCheck is not a boolean - read .statement; the check "
                        "raises when it refuses")


def verify_series(result):
    """Re-derive the published series' safety from the server-side bet list, and
    REFUSE (SystemExit) on any breach. Independent of `block_series`: it reads
    only the steps' member positions and the bet list's own season, week,
    outcome and profit.

      - the steps partition every bet exactly once, in order of step;
      - a step lies in ONE season and holds whole week blocks (no block split);
      - every step - the difference between consecutive points, the origin
        included - holds at least MIN_CLEARED cleared bets;
      - every cumulative point equals the bet list's own running profit / unit,
        and the last equals the summary's units.
    """
    ser = result["series"]
    bl = result["bet_list"]
    if ser is None:
        raise SystemExit("the result carries no series - run with series_min_cleared")
    if ser["withheld"]:
        return SeriesCheck("series withheld: " + ser["withheld"], [])
    steps = ser["steps"]
    unit = _unit(result["strategy"])
    seen = []
    for st in steps:
        seen += st["members"]
    if sorted(seen) != list(range(len(bl))) or len(seen) != len(bl):
        raise SystemExit("series steps do not partition the %d bets exactly once" % len(bl))
    block_of = [(b["season"], b["week"]) for b in bl]
    step_of = {}
    for k, st in enumerate(steps):
        for i in st["members"]:
            step_of[i] = k
    in_steps = {}
    for i, blk in enumerate(block_of):
        in_steps.setdefault(blk, set()).add(step_of[i])
    split = sorted(b for b, ks in in_steps.items() if len(ks) > 1)
    if split:
        raise SystemExit("week block %s is split across steps" % (split[0],))
    cum, low, n_so_far = 0.0, None, 0
    for k, st in enumerate(steps):
        rows = [bl[i] for i in st["members"]]
        seasons = {r["season"] for r in rows}
        if len(seasons) != 1:
            raise SystemExit("step %d crosses seasons %s" % (k, sorted(seasons)))
        c = sum(1 for r in rows if r["outcome"] == "cleared")
        if c < MIN_CLEARED:
            raise SystemExit("step %d (%s wk %s-%s) holds %d cleared bets, fewer than %d: "
                             "the point before it and this one difference back to %d prices"
                             % (k, st["season"], st["week_from"], st["week_to"], c,
                                MIN_CLEARED, c))
        low = c if low is None else min(low, c)
        cum += sum(r["profit"] for r in rows) / unit
        n_so_far += len(rows)
        # the bet list rounds each profit to 4 dp: 5e-5 per bet, plus the 3 dp point
        if abs(cum - st["units"]) > 5e-5 * n_so_far / unit + 1e-3:
            raise SystemExit("step %d units %s, the bet list says %.4f" % (k, st["units"], cum))
    total = result["summary"]["units"]
    if steps and total is not None and abs(steps[-1]["units"] - total) > 2e-3:
        raise SystemExit("the series ends at %s, the summary says %s"
                         % (steps[-1]["units"], total))
    return SeriesCheck(
        "%d steps over %d week blocks (%d merged into a neighbour in their season, none "
        "withheld); every step holds >= %d cleared bets (fewest %d), none crosses a season "
        "or splits a week, and every point re-derives from the bet list"
        % (len(steps), ser["week_blocks"], ser["merged"], MIN_CLEARED, low),
        [set(st["members"]) for st in steps])


def verify_drawdown(result, series_check):
    """-> (publish max_units?, statement). The drawdown's size is
    cum(peak) - cum(trough): the profit of the bets in (peak, trough]. It is
    published only if that set, and everything it isolates when combined with
    the other published sums, averages >= MIN_CLEARED prices or none:

      - D itself;
      - F = D less every published atom wholly inside it (atoms: the series
        steps, or the season cells when the series is withheld), and
        G = every atom touching D, less D - the two fragments D adds to the
        partition the series already publishes;
      - for each published cell C (season, market, the summary) nested with D,
        the difference between them.

    Checked against the TIME partition and the market cells, the two the bet
    list can name. The other segments (price bucket, home/away, dome, position)
    are not checked: a cell of one of those wholly inside a drawdown window
    would have to exist only inside it."""
    dd = result["drawdown"]
    bl = result["bet_list"]
    if dd.get("max_units") is None:
        return True, "no bets, so no drawdown"
    if dd.get("trough_index") is None:
        return True, ("the rule never fell below a previous high, so the drawdown is 0 and "
                      "reads back to no price")
    unit = _unit(result["strategy"])
    p = -1 if dd["peak_index"] is None else dd["peak_index"]
    t = dd["trough_index"]
    cum = [0.0]
    for b in bl:
        cum.append(cum[-1] + b["profit"] / unit)
    if abs((cum[p + 1] - cum[t + 1]) - dd["max_units"]) > 5e-5 * len(bl) / unit + 1e-6:
        raise SystemExit("drawdown %s does not re-derive from the bet list (%.6f)"
                         % (dd["max_units"], cum[p + 1] - cum[t + 1]))
    cleared = [b["outcome"] == "cleared" for b in bl]

    def n(s):
        return sum(1 for i in s if cleared[i])
    D = set(range(p + 1, t + 1))
    atoms = series_check.members
    if not atoms:
        atoms = [{i for i, b in enumerate(bl) if b["season"] == y}
                 for y in sorted({b["season"] for b in bl})]
    F = D - set().union(*[a for a in atoms if a <= D])
    G = set().union(*[a for a in atoms if a & D]) - D
    tests = [("the window itself", D), ("the window less the steps inside it", F),
             ("the steps it touches less the window", G)]
    s = result["summary"]
    cells = []
    if s["cleared"] >= MIN_CLEARED:
        cells.append(("the summary", set(range(len(bl)))))
    for c in result["by_season"]:
        if c["cleared"] >= MIN_CLEARED:
            cells.append(("season %s" % c["key"],
                          {i for i, b in enumerate(bl) if b["season"] == c["key"]}))
    for c in result["segments"].get("market", []):
        if c["cleared"] >= MIN_CLEARED:
            cells.append(("market %s" % c["key"],
                          {i for i, b in enumerate(bl) if b["market"] == c["key"]}))
    for name, C in cells:
        if C <= D:
            tests.append(("the window less %s" % name, D - C))
        elif D <= C:
            tests.append(("%s less the window" % name, C - D))
    bad = [(name, n(S)) for name, S in tests if S and not _ok_fragment(n(S))]
    if bad:
        name, k = bad[0]
        return False, ("worst drawdown withheld: %s holds %d cleared bet%s, fewer than %d, "
                       "so it would read back to %s price%s"
                       % (name, k, "" if k == 1 else "s", MIN_CLEARED, k, "" if k == 1 else "s"))
    return True, ("the %d bets in the drawdown window (%d cleared), and the %d fragments it "
                  "isolates against the published steps and cells, each hold >= %d cleared "
                  "bets or none" % (len(D), n(D), len(tests) - 1, MIN_CLEARED))


def returns_block(result, luck_band):
    """The published `returns`: the week-block series with the luck band at each
    step. `luck_band` maps a step's index to (p5, p95), or is None."""
    ser = result["series"]
    check = verify_series(result)
    flat = result["strategy"]["staking"]["method"] == "flat"
    steps = []
    for st in ([] if ser["withheld"] else ser["steps"]):
        b = (luck_band or {}).get(st["index"]) if flat else None
        steps.append({"season": st["season"], "week_from": st["week_from"],
                      "week_to": st["week_to"], "weeks": st["weeks"], "bets": st["bets"],
                      "cleared": st["cleared"], "missed": st["missed"], "push": st["push"],
                      "void": st["void"], "units": st["units"], "index": st["index"],
                      "null_p5": b[0] if b else None, "null_p95": b[1] if b else None})
    if not flat:
        band_note = ("no band: the random rules are flat-staked and this rule is not, so "
                     "their units are not on one scale")
    elif ser["withheld"]:
        band_note = "no band: the series is withheld"
    elif luck_band is None:
        band_note = "no band: " + (result["luck"].get("note") or "the luck check did not run")
    else:
        band_note = None
    return {"granularity": "week_block", "min_cleared": MIN_CLEARED,
            "week_blocks": ser["week_blocks"], "merged": ser["merged"],
            "withheld": ser["withheld"], "steps": steps, "band_note": band_note,
            "check": check.statement}, check


def project(result, preset, universe_meta):
    """lab.result/1 -> the lab_preset file body. The ONLY place a result becomes
    public, so the price restriction is enforced here, by construction: every field
    is named, nothing is copied through wholesale."""
    if not result["publishable"]:
        raise SystemExit("%s: the result is not publishable (a pre-fix universe?) - "
                         "refusing" % preset["key"])
    s = result["summary"]
    draws = result["strategy"]["evaluation"]["resamples"]
    graded = s["cleared"] + s["missed"]
    staked = graded + s["push"]
    why = _withheld(s["cleared"])
    units = None
    if not why and s["units"] is not None and s["units_lo"] is not None:
        units = _boot(s["units"], s["units_lo"], s["units_hi"], staked, s["weeks"], draws)
    summary = {
        "bets": s["bets"], "cleared": s["cleared"], "missed": s["missed"],
        "push": s["push"], "void": s["void"], "weeks": s["weeks"],
        "hit_rate": _rate(s["cleared"], graded, s["hit_rate_lo"], s["hit_rate_hi"]),
        "break_even": None if why else s["break_even"],
        "mean_devig_prob": s["mean_devig_prob"],
        "roi": None if why else _boot(s["roi"], s["roi_lo"], s["roi_hi"], staked,
                                      s["weeks"], draws),
        "units": units, "withheld": why,
    }
    by_season = [_cell(c, draws) for c in result["by_season"]]
    segments = {k: [_cell(c, draws) for c in v]
                for k, v in sorted(result["segments"].items())}
    lk = result["luck"]
    pct = lk.get("percentile")
    # The band is published at the series' steps only (a-44). At bet granularity
    # (`lk["band"]`, still on the server) its first point is a percentile of ONE
    # random bet's profit - a book price minus one.
    at = lk.get("band_at")
    band = None if at is None else {"index": at["index"], "p5": at["p5"], "p95": at["p95"]}
    returns, series_check = returns_block(
        result, None if band is None else
        {i: (a, b) for i, a, b in zip(band["index"], band["p5"], band["p95"])})
    if result["strategy"]["staking"]["method"] != "flat":
        band = None
    dd = result["drawdown"]
    dd_ok, dd_statement = verify_drawdown(result, series_check)
    drawdown = {"max_units": dd["max_units"] if dd_ok else None,
                "peak_date": dd.get("peak_date"), "trough_date": dd.get("trough_date"),
                "longest_losing_run": int(dd.get("longest_losing_run") or 0),
                "note": dd.get("note"),
                "withheld": None if dd_ok else dd_statement,
                "check": dd_statement if dd_ok else None}
    luck = {
        "percentile": pct,
        "band": (None if pct is None else
                 "below_5" if pct < 5 else "above_95" if pct > 95 else "inside_5_95"),
        "draws": int(lk.get("draws") or 0),
        "pool": lk.get("pool"), "n": lk.get("n"),
        "null_mean_roi": lk.get("null_mean_roi"),
        "null_roi_range": ([lk["null_roi_p5"], lk["null_roi_p95"]]
                           if lk.get("null_roi_p5") is not None else None),
        "rule_roi_flat": None if why else lk.get("rule_roi_flat"),
        "paths": band,
        "note": lk.get("note") or (None if band else returns["band_note"]),
    }
    ho = result["holdout"]
    cols = ["date", "season", "week", "game_id", "subject", "team", "opp", "market",
            "line", "side", "p_devig", "outcome", "actual"]
    rows = [[b.get(c) for c in cols] for b in result["bet_list"]]
    return {
        "key": preset["key"], "title": preset["title"],
        "strategy": result["strategy"], "strategy_hash": result["strategy_hash"],
        "statement": result["statement"], "notes": list(result["notes"]),
        "verdict": result["verdict"],
        "summary": summary, "by_season": by_season, "segments": segments,
        "luck": luck,
        "returns": returns,
        "drawdown": drawdown,
        "holdout": {"season": ho["season"], "revealed": bool(ho["revealed"]),
                    "note": ho.get("note")},
        "provenance": [{"season": int(k), "source": v["source"], "is_close": bool(v["is_close"]),
                        "note": v["note"]} for k, v in sorted(result["provenance"].items())],
        "universe_built": iso_z(universe_meta["built"]),
        "bet_list": {"restriction": BET_LIST_RESTRICTION, "columns": cols,
                     "n": len(rows), "rows": rows},
    }


# ------------------------------------------------------------------ the catalogue (a-45)
#
# The builder's condition picker. It is the universe's OWN `meta["features"]` -
# the dict `lab.strategy.check` approves or refuses a rule against - so what the
# page offers and what the engine accepts are one object, not two lists that
# agree today. Nothing here re-derives a range; it only says, structurally, what
# bound it.

_CLAUSE = {"from": re.compile(r"starts \d+: (.+?) is not usable before it"),
           "to": re.compile(r"ENDS \d+: (.+?) has no data after it")}


def _price_range(feature, coverage):
    """The price seasons `lab.catalogue.ranges` intersected with - same rule."""
    covered = [coverage[b] for b in feature["bet_types"] if b in coverage]
    if not covered:
        return None
    return {"from": int(min(c[0] for c in covered)), "to": int(max(c[1] for c in covered))}


def _inputs(req):
    return ["%s.%s" % (r[0], r[1]) for r in req]


def _bound(end, value, prices, inputs, note, where):
    """What set one end of a derived range: the prices, or named input columns.

    `prices` when the range ends where the price coverage does (an input may end
    there too - the prices bind either way). Otherwise an input bound it, and the
    columns are the ones `derive_range`'s note names in that end's clause, kept
    only if they are among this feature's own inputs. An input-bound end with no
    column recoverable REFUSES: publishing "bounded by the inputs" without naming
    one is the bare pair of years this field exists to replace, and it means the
    note's wording moved under this parser.
    """
    if value is None:
        return None
    if prices is not None and value == prices[end]:
        return {"by": "prices", "columns": []}
    m = _CLAUSE[end].search(note or "")
    named = [c.strip() for c in m.group(1).split(",")] if m else []
    cols = [c for c in inputs if c in named]
    if not cols:
        raise SystemExit("%s: season_%s %s is not the price bound %s, and the note names "
                         "none of its inputs %s - refusing to publish an unexplained range "
                         "(note: %r)" % (where, end, value, prices, inputs, note))
    return {"by": "inputs", "columns": cols}


def _range_fields(d, prices, inputs, where):
    none = d.get("availability") == "none"
    reason = (d.get("note") or "").strip() if none else None
    if none and not reason:
        raise SystemExit("%s: availability none with no reason - a builder cannot grey it "
                         "out with an explanation" % where)
    lo, hi = d.get("season_from"), d.get("season_to")
    return {"availability": "none" if none else "historical", "reason": reason,
            "note": d.get("note") or "",
            "season_from": None if none else lo, "season_to": None if none else hi,
            "bound_from": None if none else _bound("from", lo, prices, inputs,
                                                   d.get("note"), where),
            "bound_to": None if none else _bound("to", hi, prices, inputs,
                                                 d.get("note"), where),
            "inputs": inputs}


def catalogue_file(meta):
    """universe meta -> the lab_catalogue body, one entry per catalogue feature.

    Refuses a universe whose features are not exactly `lab.catalogue.BY_KEY`
    (built by an older catalogue: the page would offer what the engine no longer
    knows, or miss what it does), and one built without the column survey (every
    survey-ranged feature would publish as unavailable for a reason about this
    machine rather than about the feature).
    """
    from lab import catalogue as C
    from lab.universe import NO_SURVEY
    feats = meta.get("features") or {}
    if set(feats) != set(C.BY_KEY):
        raise SystemExit("the universe's features differ from lab.catalogue by %s - "
                         "rebuild the universe" % sorted(set(feats) ^ set(C.BY_KEY)))
    coverage = meta.get("price_coverage") or {}
    out = []
    for f in C.FEATURES:                                   # the catalogue's own order
        d = feats[f.key]
        for name, want in (("label", f.label), ("group", f.group), ("dtype", f.dtype),
                           ("bet_types", list(f.bet_types))):
            if d.get(name) != want:
                raise SystemExit("%s: universe %s %r, catalogue %r - rebuild the universe"
                                 % (f.key, name, d.get(name), want))
        per = d.get("by_market")
        notes = [d.get("note") or ""] + [v.get("note") or "" for v in (per or {}).values()]
        if any(NO_SURVEY in n for n in notes):
            raise SystemExit("%s: the universe was built without the column survey (%s) - "
                             "its range would publish as unavailable for that reason alone; "
                             "rebuild where analytics.db exists" % (f.key, NO_SURVEY))
        prices = _price_range(d, coverage)
        entry = {"key": f.key, "label": f.label, "group": f.group, "value_type": f.dtype,
                 "bet_types": list(f.bet_types), "price_seasons": prices}
        if per:
            by_market = [{"market": m, **_range_fields(v, prices,
                                                       _inputs(C.STAT_REQUIRES.get(m, ())),
                                                       "%s/%s" % (f.key, m))}
                         for m, v in sorted(per.items())]
            inputs = sorted({c for b in by_market for c in b["inputs"]})
            # The feature-level ends are the widest over its markets, so an input
            # bound there is carried by whichever markets set that end. Computed
            # from the markets, never parsed from the "per market: ..." summary.
            none = d.get("availability") == "none"
            entry.update({"availability": "none" if none else "historical",
                          "reason": (d.get("note") or "").strip() if none else None,
                          "note": d.get("note") or "",
                          "season_from": None if none else d.get("season_from"),
                          "season_to": None if none else d.get("season_to"),
                          "inputs": inputs})
            if none and not entry["reason"]:
                raise SystemExit("%s: availability none with no reason" % f.key)
            for end in ("from", "to"):
                v = entry["season_" + end]
                if v is None:
                    entry["bound_" + end] = None
                    continue
                setters = [m["bound_" + end] for m in by_market
                           if m["availability"] != "none" and m["season_" + end] == v]
                if any(b["by"] == "prices" for b in setters):
                    entry["bound_" + end] = {"by": "prices", "columns": []}
                else:
                    entry["bound_" + end] = {"by": "inputs", "columns": sorted(
                        {c for b in setters for c in b["columns"]})}
            entry["by_market"] = by_market
        else:
            entry.update(_range_fields(d, prices, _inputs(C.requirements(f, ())), f.key))
            entry["by_market"] = None
        out.append(entry)
    return {
        "universe_built": iso_z(meta["built"]),
        "price_coverage": [{"bet_type": b, "from": int(v[0]), "to": int(v[1])}
                           for b, v in sorted(coverage.items())],
        "features": out,
    }


def build(universe, generated_at, log=print):
    """-> {key: file}. Runs every launch preset through `lab.run`."""
    from lab import run
    from lab import strategy as S
    meta = universe["meta"]
    if not meta.get("settlement_fixed", False):
        raise SystemExit("the universe predates the settlement fix - refusing to publish")
    files, entries = {}, []
    for p in PRESETS:
        entry = {"key": p["key"], "title": p["title"]}
        if "strategy" not in p:
            ne = _not_expressible()[p["key"]]
            entry.update(status="unsupported", file=None, verdict=None, bets=None,
                         strategy_hash=None,
                         reasons=["not expressible in lab.strategy/1: " + ne["why"]],
                         register=ne["register"])
            entries.append(entry)
            log(f"  {p['key']:<26} UNSUPPORTED  (not expressible: two legs)")
            continue
        try:
            result = run(p["strategy"], universe, series_min_cleared=MIN_CLEARED)
        except S.Unsupported as e:
            entry.update(status="unsupported", file=None, verdict=None, bets=None,
                         strategy_hash=S.strategy_hash(S.with_defaults(
                             p["strategy"], meta.get("latest_complete_season"))),
                         reasons=["refused by lab.strategy.check: " + r for r in e.reasons],
                         register=None)
            entries.append(entry)
            log(f"  {p['key']:<26} UNSUPPORTED  {e.reasons}")
            continue
        key = f"lab/{SPORT}/presets/{p['key']}.json"
        body = project(result, p, meta)
        files[key] = {**E.envelope("lab_preset", generated_at, SPORT), **body}
        entry.update(status="published", file=key, verdict=result["verdict"],
                     bets=result["summary"]["bets"], strategy_hash=result["strategy_hash"],
                     reasons=[], register=None)
        entries.append(entry)
        sm = body["summary"]
        roi = sm["roi"]
        log(f"  {p['key']:<26} {result['verdict']:<22} bets {sm['bets']:>6}  "
            + ("roi %+.4f [%+.4f, %+.4f]" % (roi["value"], *roi["ci"]) if roi else "roi withheld"))
    if not files:
        raise NothingToPublish("no launch preset produced a result - refusing to write an "
                               "index that names nothing")
    # Written on EVERY run: `lab/` is owned wholly, so a run that skipped it
    # would delete the builder's picker.
    files[CATALOGUE_KEY] = {**E.envelope("lab_catalogue", generated_at, SPORT),
                            **catalogue_file(meta)}
    cat = files[CATALOGUE_KEY]["features"]
    log("  catalogue: %d features, %d unavailable" % (
        len(cat), sum(1 for c in cat if c["availability"] == "none")))
    files[INDEX_KEY] = {
        **E.envelope("lab_index", generated_at, SPORT),
        "launch_set": LAUNCH_SOURCE,
        "universe_built": iso_z(meta["built"]),
        "latest_complete_season": meta.get("latest_complete_season"),
        "min_cleared": MIN_CLEARED,
        "restriction": BET_LIST_RESTRICTION,
        "presets": entries,
    }
    return files


def publish(universe, dest, generated_at=None, dry_run=False, log=print):
    generated_at = generated_at or E.iso()
    files = build(universe, generated_at, log=log)
    written, deleted = E.sync_keys(dest, files, ["lab/"], dry_run=dry_run)
    out = {"files": len(files), "written": written, "deleted": deleted, "dry_run": dry_run,
           "published": sum(1 for p in files[INDEX_KEY]["presets"] if p["status"] == "published"),
           "unsupported": sum(1 for p in files[INDEX_KEY]["presets"]
                              if p["status"] == "unsupported")}
    log(json.dumps(out))
    return out


def check_tree(dest, show_keys=False, log=print):
    """Validate every `lab/` file in a tree against the contract and the source gate
    WITHOUT writing, and cross-check the index against the files. Refuses an empty
    tree: a check over zero files is not a check."""
    local = {k: p for k, p in E.local_keys(dest).items() if k.startswith(PREFIX)}
    if not local:
        raise SystemExit(f"no lab/ keys under {dest} - nothing to check")
    js = {}
    for k, p in local.items():
        with open(p, encoding="utf-8") as f:
            js[k] = json.load(f)
    E.validate_contract(js)
    R.require_declared(js)
    if INDEX_KEY not in js:
        raise E.ContractError(f"{INDEX_KEY} is missing")
    if CATALOGUE_KEY not in js:
        raise E.ContractError(f"{CATALOGUE_KEY} is missing")
    named = {e["file"] for e in js[INDEX_KEY]["presets"] if e["file"]}
    on_disk = set(js) - {INDEX_KEY, CATALOGUE_KEY}
    if named != on_disk:
        raise E.ContractError(f"index names {sorted(named - on_disk)} not on disk, and "
                              f"{sorted(on_disk - named)} on disk are not in the index")
    total = 0
    for k in sorted(local):
        size = os.path.getsize(local[k])
        total += size
        if show_keys:
            log(f"  {k:<48} {size} bytes")
    log(f"{len(local)} keys, {total} bytes, all validated against "
        f"{os.path.relpath(E.CONTRACT_PATH, E.ROOT)} and the source gate")
    return {"keys": len(local), "bytes": total}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--universe", help="a universe directory built by `python -m lab.universe --build`")
    ap.add_argument("--dest", required=True, help="the tree to write lab/ into; no default")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--check", action="store_true", help="validate the tree's lab/ keys and exit")
    ap.add_argument("--keys", action="store_true", help="with --check: list every key and its bytes")
    a = ap.parse_args(argv)
    if a.check:
        check_tree(a.dest, show_keys=a.keys)
        return 0
    if not a.universe:
        ap.error("--universe is required to publish")
    from lab import universe as U
    publish(U.load(a.universe), a.dest, dry_run=a.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
