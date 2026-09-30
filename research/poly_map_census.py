"""Polymarket mapping census, before and after a mapper change, off the live store (a-60).

    python -m research.poly_map_census build  D:/temp/a60/map_base.db
    python -m research.poly_map_census map    D:/temp/a60/map_base.db
    python -m research.poly_map_census census D:/temp/a60/map_base.db

A mapping change is judged by `jobs/cross_venue.py`'s standard: a mapping error
and a real disagreement look identical in a price and different in a census. So
the census is the evidence, and it has to be taken without writing to the
logger's database - `map_markets` writes `outcomes` and `market_outcome`, and
this unit's only sanctioned writes there are retention holds.

  build   copies, READ-ONLY, the tables the mappers read and write (markets,
          market_outcome, outcomes, nfl_games, player_xwalk, player_alias,
          nfl_player_week) into a scratch file
  map     runs `jobs.map_markets.run(venue='polymarket')` with LOGGER_DB pinned
          to the scratch file - whichever mapper code the interpreter imports,
          so a worktree of the old commit gives the "before"
  census  Polymarket markets mapped / unmapped, by reason and by week, and the
          outcomes shared with Kalshi, by week and market type

`census` also runs against the live store (`--live`), mode=ro, which is the
state production is actually in.
"""
import argparse
import json
import os
import re
import sqlite3
import sys
from collections import Counter, defaultdict

TABLES = ("markets", "market_outcome", "outcomes", "nfl_games", "player_xwalk",
          "player_alias", "nfl_player_week")
SLUG = re.compile(r"^nfl-[a-z0-9]+-[a-z0-9]+-(\d{4}-\d{2}-\d{2})(?:-.*)?$")


def live_db():
    """The logger's store as config resolves it, read before anything is pinned."""
    sys.path.insert(0, os.getcwd())
    import config
    return os.path.abspath(config.DB_PATH).replace("\\", "/")


def _pin(dest):
    import importlib
    os.environ["LOGGER_DB"] = os.path.abspath(dest)
    sys.path.insert(0, os.getcwd())
    import config
    importlib.reload(config)
    assert os.path.abspath(config.DB_PATH) == os.path.abspath(dest), config.DB_PATH
    return config


def build(dest):
    if os.path.exists(dest):
        raise SystemExit(f"refusing to overwrite {dest}; remove it first")
    live = live_db()
    _pin(dest)
    import store
    store.init_db()
    # uri=True on the MAIN connection: without it SQLite does not parse the
    # ATTACH filename as a URI, ignores mode=ro and creates a file literally
    # named "file:D:/...".
    c = sqlite3.connect(f"file:{os.path.abspath(dest)}", uri=True)
    c.execute(f"ATTACH DATABASE 'file:{live}?mode=ro' AS live")
    assert c.execute("PRAGMA live.query_only").fetchone() is not None
    for t in TABLES:
        cols = [r[1] for r in c.execute(f"PRAGMA main.table_info({t})")]
        lcols = {r[1] for r in c.execute(f"PRAGMA live.table_info({t})")}
        use = [x for x in cols if x in lcols]
        c.execute(f"DELETE FROM main.{t}")
        c.execute(f"INSERT INTO main.{t} ({','.join(use)}) SELECT {','.join(use)} FROM live.{t}")
        print(f"  {t:16s} {c.execute(f'SELECT COUNT(*) FROM main.{t}').fetchone()[0]:>9,}")
    c.commit()
    c.execute("DETACH DATABASE live")
    c.close()


def run_map(db):
    _pin(db)
    import store
    from jobs import map_markets
    store.init_db()
    from venues import polymarket
    print("mapper:", polymarket.__file__)
    res = map_markets.run(venue="polymarket")
    print(dict(res["stats"]))


def reason_class(r):
    """Collapse per-market detail (names, ids) so reasons can be counted."""
    if r is None:
        return "MAPPED"
    r = re.sub(r"polymarket-only claim .*", "polymarket-only claim (player line no other venue created)", r)
    r = re.sub(r"'.*", "'...", r)
    r = re.sub(r"near \d{4}-\d{2}-\d{2}.*", "near <date>...", r)
    r = re.sub(r"head-to-head \S+ vs \S+ is not game .*", "head-to-head is not this game", r)
    return r[:80]


def week_of(con):
    """slug date -> week: a game's slug carries the kickoff's UTC date, so the
    date or the day before is one of the week's game dates."""
    import datetime as dt
    days = {}
    for w, d in con.execute("SELECT DISTINCT week, gameday FROM nfl_games WHERE season = 2026"):
        days[d] = w
    def f(eid):
        m = SLUG.match((eid or "").lower())
        if not m:
            return "non-game"
        d = m.group(1)
        prev = (dt.date.fromisoformat(d) - dt.timedelta(days=1)).isoformat()
        return days.get(d, days.get(prev, "?"))
    return f


def census(db, live=False, json_out=None):
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    wk = week_of(con)
    rows = con.execute(
        "SELECT m.market_id, m.event_id, m.market_type, mo.market_id IS NOT NULL, "
        "mo.outcome_id, mo.unmapped_reason, mo.method, o.key "
        "FROM markets m LEFT JOIN market_outcome mo "
        "  ON mo.venue = m.venue AND mo.market_id = m.market_id "
        "LEFT JOIN outcomes o ON o.outcome_id = mo.outcome_id "
        "WHERE m.venue = 'polymarket'").fetchall()
    total = len(rows)
    has_row = sum(r[3] for r in rows)
    mapped = sum(r[4] is not None for r in rows)
    print(f"polymarket markets {total:,}  with a market_outcome row {has_row:,}  "
          f"mapped {mapped:,}  unmapped-with-reason {has_row - mapped:,}  "
          f"never-mapped {total - has_row:,}")
    reasons = Counter()
    by_week = defaultdict(Counter)
    methods = Counter()
    for mid, eid, mt, hr, oid, why, method, key in rows:
        cls = "NO ROW (never mapped)" if not hr else reason_class(why if oid is None else None)
        reasons[cls] += 1
        by_week[wk(eid)][cls if cls in ("MAPPED", "NO ROW (never mapped)") else "unmapped"] += 1
        if oid:
            methods[method] += 1
    print("\nby reason:")
    for k, v in reasons.most_common(25):
        print(f"  {v:>7,}  {k}")
    print("\nmapped by method:")
    for k, v in methods.most_common():
        print(f"  {v:>7,}  {k}")
    print("\nby week (game slugs; 'non-game' = season-long and weekly-leader slugs):")
    for w in sorted(by_week, key=lambda x: (isinstance(x, str), str(x).zfill(3))):
        c = by_week[w]
        print(f"  week {str(w):9s} mapped {c['MAPPED']:>6,}  unmapped {c['unmapped']:>6,}  "
              f"never-mapped {c['NO ROW (never mapped)']:>6,}")

    # outcomes shared with Kalshi: the thing the lead study needs
    shared = con.execute(
        "SELECT o.week, o.key FROM outcomes o WHERE o.season = 2026 AND o.outcome_id IN ("
        "  SELECT outcome_id FROM market_outcome WHERE outcome_id IS NOT NULL "
        "  GROUP BY outcome_id HAVING SUM(venue = 'kalshi') > 0 AND SUM(venue = 'polymarket') > 0)"
    ).fetchall()
    sh = defaultdict(Counter)
    for w, key in shared:
        mt = key.split("|")[3]
        stat = key.split("|")[5]
        sh[w][mt if mt != "player_prop" else f"prop:{stat}"] += 1
    print("\noutcomes quoted on BOTH Kalshi and Polymarket, by week:")
    for w in sorted(sh):
        print(f"  week {w}: " + ", ".join(f"{k} {v}" for k, v in sorted(sh[w].items())))
    con.close()
    if json_out:
        with open(json_out, "w") as f:
            json.dump({"total": total, "has_row": has_row, "mapped": mapped,
                       "reasons": dict(reasons), "methods": dict(methods),
                       "by_week": {str(k): dict(v) for k, v in by_week.items()},
                       "shared": {str(k): dict(v) for k, v in sh.items()}}, f, indent=1)
    if total == 0:
        raise SystemExit("no polymarket markets read - refusing to report an empty census")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("cmd", choices=["build", "map", "census"])
    ap.add_argument("db", nargs="?")
    ap.add_argument("--live", action="store_true", help="census the live store, mode=ro")
    ap.add_argument("--json")
    a = ap.parse_args()
    if a.cmd == "build":
        build(a.db)
    elif a.cmd == "map":
        run_map(a.db)
    else:
        census(live_db() if a.live else a.db, live=a.live, json_out=a.json)


if __name__ == "__main__":
    main()
