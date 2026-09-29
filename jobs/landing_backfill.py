"""Fill the landing archive with PLAYED periods, from the store (unit a-54).

    python -m jobs.landing_backfill --season 2026 --weeks 1 2 3        # write
    python -m jobs.landing_backfill --season 2026 --weeks 1 2 3 --check
    python -m jobs.landing_backfill --auto                             # the refresh's call

WHY. The landing's showpieces walk back through a server-side archive of market
files (a-52, `jobs.landing_export`). That archive was only ever filled by the
landing job copying the CURRENT period's served files, and the export's market
builder writes only UNPLAYED games (`home_score is None and kickoff_ts > now`),
so re-running the export for a played week produces nothing. That filter is
right for the live export and is not touched here.

WHAT THIS WRITES. For each requested period, the same per-player market file the
live export writes - same builder (`export_web.build_market`, `played=True`),
same contract kind, same key shape `nfl/market/<gsis>/<season>-<index>.json` -
into the ARCHIVE directory, never into WEB_EXPORT_DIR. So a-52's reader needs no
change, and nothing here is served: only the landing's carried parts reach the
site, under a-52's rules.

THE INSTANT. Every rung is the last two-sided quote STRICTLY BEFORE KICKOFF - the
closing read, the same instant CLV and the Board grade against. A played game's
kickoff is in the past, so the live builder's own cap `min(now, kickoff)` is
kickoff; `played=True` only changes WHICH games are taken (the kicked-off ones,
the exact complement of the live export's).

AS-OF. The builder's TD history and a player's latest team are read from the
player-week rows. The live export only ever sees rows before its period; a
backfill run later would also see the period itself and the ones after it. So
the rows are cut at the period before the builder sees them (invariant 5).
What is NOT as-of: the crosswalk's position and display name are current, as
they are everywhere in the export (a known debt, CLAUDE.md).

WHAT A RE-RUN DOES. A file is compared through `export_web._canonical` (which
ignores `generated_at`). Unchanged -> not touched, so the bytes on disk stay the
bytes. Changed -> the previous file is MOVED ASIDE to
`<archive>/_superseded/<utc stamp>/...` (outside the `nfl/market/` tree the
landing globs, so it can never be walked) and the new one written, and the run
prints what moved: the read instant (`as_of`) or, at the same instant, the
content. A file this run did not rebuild is kept, never removed.

THE STORE IS READ mode=ro, through `export_web.ro()`. No quote is fetched, no
credit spent, nothing polled.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import shutil
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
from jobs import export_web as E  # noqa: E402

SPORT = E.SPORT
SUPERSEDED = "_superseded"


class BackfillError(AssertionError):
    """A period would be archived in a shape the landing cannot trust."""


def default_archive():
    """The same directory `jobs.landing_export` walks, by the same call."""
    from jobs import landing_export as L
    return L.default_archive()


def as_of_weeks(weeks, season, index):
    """The player-week rows the live export would have seen for this period: every
    earlier season, and this season's weeks BEFORE `index`."""
    return [r for r in weeks
            if r["season"] < season or (r["season"] == season and r["week"] < index)]


def period_of(games, season, index):
    """The `current`-shaped dict build_market takes, for any period."""
    gs = [g for g in games.values() if g["season"] == season and g["week"] == index]
    if not gs:
        raise BackfillError(f"{season} week {index} has no games in nfl_games")
    return {"season": season,
            "period": {"index": index, "label": E.period_label(gs[0].get("game_type"), index),
                       "key": E.period_key(season, index)}}


def kicked_off(games, season, index, now_ts):
    return sum(1 for g in games.values() if g["season"] == season and g["week"] == index
               and g["kickoff_ts"] and g["kickoff_ts"] <= now_ts)


def build_period(con, games, weeks, xwalk, slugs, season, index, now_ts, generated_at,
                 n_sims=None):
    """-> ({key: market file}, census). Validated against the contract."""
    n_sims = E.N_SIMS if n_sims is None else n_sims
    cur = period_of(games, season, index)
    files, _, census, _ = E.build_market(con, games, as_of_weeks(weeks, season, index), xwalk,
                                         slugs, cur, now_ts, generated_at, n_sims=n_sims,
                                         played=True)
    if files:
        E.assert_stats_defined(files, E.STAT_DEFINITIONS)
        E.validate_contract(files)
        bad = [k for k, f in files.items() if f["period"]["key"] != cur["period"]["key"]]
        if bad:
            raise BackfillError(f"{len(bad)} file(s) name a period other than "
                                f"{cur['period']['key']}: {bad[:3]}")
    return files, census


def read_lag_minutes(files):
    """-> (median, max) minutes from each rung's quote to its game's kickoff, or
    (None, None). Printed on every run because it is the one number that says
    whether a period's reads ARE the close: a rung whose closing quotes were
    pruned falls back to an older one and still builds. Measured 2026-09-29:
    week 1 of 2026 reads a median ~87h before kickoff - its live quotes are gone
    to the 14-day retention and the candle backfill ends 2026-09-10 03:00Z."""
    lags = sorted((f["kickoff_ts"] - r["quote_ts"]) / 60.0 for f in files.values()
                  for c in f["components"] for r in (c.get("rungs") or []))
    if not lags:
        return None, None
    return lags[len(lags) // 2], lags[-1]


def _load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def archive_period(files, archive_dir, stamp, dry_run=False):
    """Write one period's files into the archive, idempotently.
    -> {"new": n, "unchanged": n, "replaced": [(key, why)]}"""
    out = {"new": 0, "unchanged": 0, "replaced": []}
    for key, obj in sorted(files.items()):
        path = E.local_path(archive_dir, key)
        old = None
        if os.path.isfile(path):
            try:
                old = _load(path)
            except (OSError, ValueError):
                old = {"as_of": "(unreadable)"}
        if old is not None and E._canonical(old) == E._canonical(obj):
            out["unchanged"] += 1
            continue
        if old is None:
            out["new"] += 1
        else:
            why = ("read moved %s -> %s" % (old.get("as_of"), obj["as_of"])
                   if old.get("as_of") != obj["as_of"]
                   else "same read %s, content moved (%s)"
                   % (obj["as_of"], ", ".join(sorted(
                       k for k in set(old) | set(obj)
                       if k != "generated_at" and old.get(k) != obj.get(k)))))
            out["replaced"].append((key, why))
            if not dry_run:
                aside = E.local_path(os.path.join(archive_dir, SUPERSEDED, stamp), key)
                os.makedirs(os.path.dirname(aside), exist_ok=True)
                shutil.copy2(path, aside)
        if not dry_run:
            E.write_if_changed(path, obj)
    return out


def kept_unrebuilt(archive_dir, pkey, files):
    """Archived files for this period that this run did not rebuild - kept."""
    root = os.path.join(archive_dir, SPORT, "market")
    if not os.path.isdir(root):
        return []
    have = {f"{SPORT}/market/{pid}/{pkey}.json" for pid in os.listdir(root)
            if os.path.isfile(os.path.join(root, pid, f"{pkey}.json"))}
    return sorted(have - set(files))


def auto_periods(games, weeks, now_ts, fallback_periods=None):
    """The refresh's periods: the current one and every earlier one the landing
    can walk to (config.LANDING_FALLBACK_PERIODS), each with a kicked-off game.
    Nothing older is built: the landing never reads it."""
    from jobs import landing_export as L
    cur = E.current_period(games, weeks, now_ts)
    man = {"current": {"season": cur["season"], "period": cur["period"]}}
    return [(s, i) for _, s, i, _ in L.window(man, fallback_periods)
            if kicked_off(games, s, i, now_ts)]


def run(periods=None, auto=False, archive_dir=None, dry_run=False, now_ts=None, log=print,
        n_sims=None):
    """-> {pkey: {players, files, new, unchanged, replaced, kept, census}}."""
    E.assert_numeric_stack()
    archive_dir = archive_dir or default_archive()
    now_ts = time.time() if now_ts is None else now_ts
    generated_at = E.iso(now_ts)
    stamp = dt.datetime.fromtimestamp(now_ts, dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    con = E.ro()
    try:
        games = E.load_games(con)
        weeks = E.load_player_weeks(con)
        xwalk, _ = E.load_xwalk(con)
        # READ-ONLY: the backfill never appends to the slug registry, which is
        # permanent and belongs to the export. A player with no slug yet gets
        # null, exactly as the live builder writes it.
        slugs = E.load_slug_registry(E.slug_registry_path())
        if auto:
            periods = auto_periods(games, weeks, now_ts)
        if not periods:
            log("landing backfill: no period to build")
            return {}
        report = {}
        for season, index in periods:
            n_ko = kicked_off(games, season, index, now_ts)
            t0 = time.time()
            files, census = build_period(con, games, weeks, xwalk, slugs, season, index, now_ts,
                                         generated_at, n_sims=n_sims)
            pkey = E.period_key(season, index)
            moved = archive_period(files, archive_dir, stamp, dry_run=dry_run)
            kept = kept_unrebuilt(archive_dir, pkey, files)
            players = len({f["identity"]["id"] for f in files.values()})
            lag50, lagmax = read_lag_minutes(files)
            report[pkey] = dict(moved, players=players, files=len(files), kept=len(kept),
                                kicked_off=n_ko, census=census,
                                read_lag_min={"median": lag50, "max": lagmax})
            log("landing backfill %s: %d kicked-off game(s), %d player(s), %d file(s) - "
                "%s %d new, %d unchanged, %d replaced; %d archived file(s) not rebuilt, kept "
                "(%.0fs)" % (pkey, n_ko, players, len(files),
                             "would write" if dry_run else "wrote", moved["new"],
                             moved["unchanged"], len(moved["replaced"]), len(kept),
                             time.time() - t0))
            if lag50 is not None:
                log("  read to kickoff: median %.0f min, max %.0f min%s"
                    % (lag50, lagmax, "" if lag50 <= 60 else
                       " - NOT the close: the store holds no quote nearer kickoff "
                       "(quotes pruned or never captured)"))
            for key, why in moved["replaced"][:10]:
                log(f"  replaced {key}: {why}")
            if len(moved["replaced"]) > 10:
                log(f"  ... and {len(moved['replaced']) - 10} more replaced")
            if census:
                log("  census: " + ", ".join(f"{k} {v}" for k, v in sorted(census.items())))
        return report
    finally:
        con.close()


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--season", type=int)
    ap.add_argument("--weeks", type=int, nargs="+")
    ap.add_argument("--auto", action="store_true",
                    help="the current period and every earlier one the landing can walk to")
    ap.add_argument("--archive", help="the landing archive (default <STORAGE>/landing/archive)")
    ap.add_argument("--check", action="store_true", help="build and compare, write nothing")
    a = ap.parse_args(argv)
    if a.auto == bool(a.season and a.weeks):
        ap.error("give either --auto or both --season and --weeks")
    periods = None if a.auto else [(a.season, w) for w in a.weeks]
    report = run(periods, auto=a.auto, archive_dir=a.archive, dry_run=a.check)
    print(json.dumps({k: {x: (len(v[x]) if x == "replaced" else v[x])
                          for x in ("players", "files", "new", "unchanged", "replaced", "kept")}
                      for k, v in report.items()}, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
