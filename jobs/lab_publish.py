"""The Lab library, published as static results (unit a-32). No service.

    python -m jobs.lab_publish --universe D:/scratch/u1 --dest D:/scratch/lab
    python -m jobs.lab_publish --universe ... --dest ... --dry-run
    python -m jobs.lab_publish --check --keys --dest D:/scratch/lab

Writes, under `--dest` (there is NO default - config has no defaults, and a job
that can publish must be told where):

    lab/nfl/index.json               kind lab_index    - every launch preset, run or not
    lab/nfl/presets/{key}.json       kind lab_preset   - one per preset that runs

WHY STATIC. a-27 built `lab.run(strategy, universe)` as a pure function and stood
up no service, because serving it (audit option B) turned on The Odds API's
display terms. f-17 read them (public, updated 31 Aug 2026): the word
"historical" appears zero times - SILENT on historical display, not permissive.
So the Library ships the conservative way: the launch presets run here, through
the same `lab.run` a user's rule would, and only AGGREGATES leave the server.

WHAT NEVER LEAVES THE SERVER, AND WHY IT IS NOT AN OVERSIGHT. No per-bet book
price (American or decimal), no per-bet book name, no per-bet stake or profit
(a flat winning bet's profit IS its decimal price minus one), and no equity
chart (consecutive points of a cumulative-units curve difference back to
per-bet profit). Beyond that, a cell's ROI and units are published only where
they average at least MIN_CLEARED cleared bets' prices: an ROI over one cleared
bet, with the counts beside it, is that bet's price. `BET_LIST_RESTRICTION` is
the sentence every file carries so nobody "fixes" this later.

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
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from jobs import export_web as E  # noqa: E402
from jobs import source_registry as R  # noqa: E402

SPORT = "nfl"
PREFIX = "lab/"
INDEX_KEY = f"lab/{SPORT}/index.json"
MIN_CLEARED = 10

PROPS = ["receptions", "receiving_yards", "rush_attempts", "tackles_assists", "sacks"]
PROP_SEASONS = {"from": 2023, "to": 2025, "season_type": "REG", "weeks": {"from": 1, "to": 18}}
GAME_SEASONS = {"from": 1999, "to": 2025, "season_type": "REG", "weeks": {"from": 1, "to": 18}}

BET_LIST_RESTRICTION = (
    "Deliberately restricted: no bet carries its book price, its book, its stake or its "
    "profit, and the file has no equity curve. The prices are The Odds API's, whose terms "
    "(read 2026-09-24, updated 31 Aug 2026) say nothing either way about displaying "
    "historical odds, so they stay on the server and only aggregates leave it. A winning "
    "flat bet's profit is its price minus one, and consecutive points of a cumulative "
    "curve difference back to it, so those are withheld for the same reason. ROI and "
    "units appear only where they average at least %d cleared bets' prices. Do not add "
    "these fields back without a decision that the terms allow it." % MIN_CLEARED)

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
    band = lk.get("band")
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
        "paths": ({"index": band["index"], "p5": band["p5"], "p95": band["p95"]}
                  if band else None),
        "note": lk.get("note"),
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
        "holdout": {"season": ho["season"], "revealed": bool(ho["revealed"]),
                    "note": ho.get("note")},
        "provenance": {k: {"source": v["source"], "is_close": bool(v["is_close"]),
                           "note": v["note"]} for k, v in result["provenance"].items()},
        "universe_built": iso_z(universe_meta["built"]),
        "bet_list": {"restriction": BET_LIST_RESTRICTION, "columns": cols,
                     "n": len(rows), "rows": rows},
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
            result = run(p["strategy"], universe)
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
    named = {e["file"] for e in js[INDEX_KEY]["presets"] if e["file"]}
    on_disk = set(js) - {INDEX_KEY}
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
