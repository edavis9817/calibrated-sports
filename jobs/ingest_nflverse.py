"""Mirror nflverse into the raw archive and the normalized store.

    python -m jobs.ingest_nflverse --season 2024
    python -m jobs.ingest_nflverse --season 2024 --week 3
    python -m jobs.ingest_nflverse --dataset weekly_stats --season 1999-2025
    python -m jobs.ingest_nflverse --tier live --season 2026
    python -m jobs.ingest_nflverse --status

Mirror rather than fetch on demand, because nflverse applies NFL stat
corrections retroactively on Thursdays. A copy you overwrite is a history that
silently mutates, and then you cannot prove your Week 3 model used only Week 3
information. That is invariant #5, and it is the real reason this job exists.

How a stat correction is made legible:

    fetch -> sha256 -> unchanged? record the CHECK and stop
                    -> changed?   archive a new DATED copy, normalize it as a
                                  new data_version, leave every older version
                                  exactly as it was

So `nflverse_versions` grows a row only when upstream actually moved, and the
normalized tables carry every version side by side. "What did we know on
2026-09-14?" is `WHERE data_version <= '2026-09-14'`.

Without the hash check this would be ~265GB/year of identical daily copies,
with the handful of real corrections buried among 364 duplicates each.

--week scopes the NORMALIZE step only. nflverse publishes season-grained files,
so the fetch is always a whole season; there is no way to ask for less.
"""
import argparse
import io
import time
from datetime import datetime, timezone

import httpx

import config
import nflverse
import store

SOURCE = "nflverse"


def _pl():
    """polars, imported lazily so the logger never pays for it."""
    import polars as pl
    return pl


def today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


# ---- normalizers ------------------------------------------------------------
# Each returns (table, cols, rows). They resolve player identity to gsis_id
# HERE, at ingest, so no analysis query ever has to know that nflverse calls it
# `player_id` in one file and `pfr_player_id` in another.

def _col(df, name, default=None):
    """A column if the frame has it, else a constant. nflverse adds and renames
    columns between seasons; a missing one must not fail a 26-season backfill."""
    pl = _pl()
    return df[name] if name in df.columns else pl.lit(default)


def normalize_weekly_stats(data: bytes, version: str, week=None):
    pl = _pl()
    df = pl.read_parquet(io.BytesIO(data))
    if week is not None:
        df = df.filter(pl.col("week") == week)
    now = time.time()
    out = df.select([
        _col(df, "player_id").alias("gsis_id"),
        pl.col("season").cast(pl.Int64),
        pl.col("week").cast(pl.Int64),
        _col(df, "season_type", "REG").alias("season_type"),
        _col(df, "player_display_name").alias("player_name"),
        _col(df, "position").alias("position"),
        _col(df, "team").alias("team"),
        _col(df, "opponent_team").alias("opponent"),
        _col(df, "receptions"), _col(df, "targets"),
        _col(df, "receiving_yards"), _col(df, "receiving_tds"),
        _col(df, "target_share"),
        _col(df, "carries"), _col(df, "rushing_yards"), _col(df, "rushing_tds"),
        _col(df, "attempts"), _col(df, "completions"),
        _col(df, "passing_yards"), _col(df, "passing_tds"),
        _col(df, "passing_interceptions").alias("interceptions"),
        _col(df, "fantasy_points_ppr"),
    ]).drop_nulls("gsis_id")

    cols = ("sport", "gsis_id", "season", "week", "season_type", "data_version",
            "player_name", "position", "team", "opponent", "receptions",
            "targets", "receiving_yards", "receiving_tds", "target_share",
            "carries", "rushing_yards", "rushing_tds", "attempts", "completions",
            "passing_yards", "passing_tds", "interceptions", "fantasy_points_ppr",
            "source", "ingested_ts")
    rows = [("nfl", r["gsis_id"], r["season"], r["week"], r["season_type"],
             version, r["player_name"], r["position"], r["team"], r["opponent"],
             _f(r["receptions"]), _f(r["targets"]), _f(r["receiving_yards"]),
             _f(r["receiving_tds"]), _f(r["target_share"]), _f(r["carries"]),
             _f(r["rushing_yards"]), _f(r["rushing_tds"]), _f(r["attempts"]),
             _f(r["completions"]), _f(r["passing_yards"]), _f(r["passing_tds"]),
             _f(r["interceptions"]), _f(r["fantasy_points_ppr"]),
             SOURCE, now)
            for r in out.iter_rows(named=True)]
    return "nfl_player_week", cols, rows


def normalize_games(data: bytes, version: str, week=None, seasons=None):
    """games.parquet is ONE file covering 1999-2026, so unlike the seasonal
    datasets the season filter has to be applied here rather than by picking a
    different asset. Without it, `--season 2024` would rewrite all 7,548 rows."""
    pl = _pl()
    df = pl.read_parquet(io.BytesIO(data))
    wanted = [s for s in (seasons or []) if s is not None]
    if wanted:
        df = df.filter(pl.col("season").is_in(wanted))
    if week is not None:
        df = df.filter(pl.col("week") == week)
    now = time.time()
    cols = ("sport", "game_id", "data_version", "season", "week", "game_type",
            "gameday", "kickoff_ts", "home_team", "away_team", "home_score",
            "away_score", "spread_line", "total_line", "home_moneyline",
            "away_moneyline", "over_odds", "under_odds", "home_spread_odds",
            "away_spread_odds", "roof", "surface", "stadium", "source",
            "ingested_ts")
    rows = []
    for r in df.iter_rows(named=True):
        rows.append(("nfl", r.get("game_id"), version, r.get("season"),
                     r.get("week"), r.get("game_type"), r.get("gameday"),
                     _kickoff(r), r.get("home_team"), r.get("away_team"),
                     _f(r.get("home_score")), _f(r.get("away_score")),
                     _f(r.get("spread_line")), _f(r.get("total_line")),
                     _f(r.get("home_moneyline")), _f(r.get("away_moneyline")),
                     _f(r.get("over_odds")), _f(r.get("under_odds")),
                     _f(r.get("home_spread_odds")), _f(r.get("away_spread_odds")),
                     r.get("roof"), r.get("surface"), r.get("stadium"),
                     SOURCE, now))
    return "nfl_games", cols, rows


def normalize_snap_counts(data: bytes, version: str, week=None):
    pl = _pl()
    df = pl.read_parquet(io.BytesIO(data))
    if week is not None:
        df = df.filter(pl.col("week") == week)
    now = time.time()
    cols = ("sport", "pfr_player_id", "game_id", "data_version", "season",
            "week", "player", "position", "team", "offense_snaps", "offense_pct",
            "defense_snaps", "defense_pct", "st_snaps", "st_pct", "source",
            "ingested_ts")
    rows = []
    for r in df.iter_rows(named=True):
        pid = r.get("pfr_player_id")
        if not pid:
            continue
        rows.append(("nfl", pid, r.get("game_id"), version, r.get("season"),
                     r.get("week"), r.get("player"), r.get("position"),
                     r.get("team"), _f(r.get("offense_snaps")),
                     _f(r.get("offense_pct")), _f(r.get("defense_snaps")),
                     _f(r.get("defense_pct")), _f(r.get("st_snaps")),
                     _f(r.get("st_pct")), SOURCE, now))
    return "nfl_snap_counts", cols, rows


NORMALIZERS = {
    "weekly_stats": normalize_weekly_stats,
    "games": normalize_games,
    "snap_counts": normalize_snap_counts,
}

KEY_COLS = {
    "nfl_player_week": ("gsis_id", "season", "week", "season_type", "data_version"),
    "nfl_games": ("game_id", "data_version"),
    "nfl_snap_counts": ("pfr_player_id", "game_id", "data_version"),
}


def _f(v):
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


def _kickoff(r):
    day, t = r.get("gameday"), r.get("gametime")
    if not day:
        return None
    try:
        stamp = f"{day} {t or '00:00'}"
        return datetime.strptime(stamp, "%Y-%m-%d %H:%M").replace(
            tzinfo=timezone.utc).timestamp()
    except ValueError:
        return None


# ---- the job ----------------------------------------------------------------

def ingest_one(ds: nflverse.Dataset, season=None, week=None, version=None,
               client=None, force=False, seasons=None) -> dict:
    """Fetch, archive if changed, normalize if changed. Idempotent by content."""
    version = version or today()
    label = f"{ds.name}{f' {season}' if season else ''}"
    res = {"dataset": ds.name, "season": season, "status": "?", "rows": 0,
           "bytes": 0, "version": version}

    try:
        data, digest = nflverse.fetch(ds, season, client)
    except nflverse.NotPublished:
        # Normal in the run-up to a season: the file simply does not exist yet.
        res["status"] = "not-published"
        return res

    res["bytes"] = len(data)
    prev = store.latest_version(ds.name, season)
    if prev and prev[1] == digest and config.NFLVERSE_DEDUPE_BY_HASH and not force:
        # Upstream unchanged. Record that we looked; do not store a 20MB
        # duplicate, and do not manufacture a data_version that means nothing.
        store.touch_version(ds.name, season, prev[0])
        res["status"] = "unchanged"
        res["version"] = prev[0]
        return res

    rel = store.archive_file(SOURCE, ds.asset(season), data, day=version)

    rows_written = None
    if ds.normalize and ds.name in NORMALIZERS:
        fn = NORMALIZERS[ds.name]
        kwargs = {"week": week}
        if ds.name == "games":
            kwargs["seasons"] = seasons if seasons else ([season] if season else None)
        table, cols, rows = fn(data, version, **kwargs)
        rows_written = store.replace_rows(table, cols, rows, KEY_COLS[table])
        res["rows"] = rows_written

    store.record_version(ds.name, season, version, digest, len(data), rel,
                         rows=rows_written, tier=ds.tier)
    res["status"] = "updated" if prev else "new"
    return res


def run(datasets=None, seasons=None, week=None, tier=None, force=False,
        version=None) -> dict:
    """Ingest a set of datasets. Never raises; records health per dataset."""
    names = datasets or list(nflverse.DATASETS)
    if tier:
        names = [n for n in names if nflverse.DATASETS[n].tier == tier]
    seasons = seasons or [None]
    stats = {"ok": 0, "unchanged": 0, "not_published": 0, "failed": 0,
             "rows": 0, "bytes": 0, "results": []}

    with httpx.Client(timeout=config.NFLVERSE_TIMEOUT, follow_redirects=True,
                      headers={"User-Agent": config.USER_AGENT}) as client:
        for name in names:
            ds = nflverse.DATASETS[name]
            wanted = [s for s in seasons if s is None or s >= ds.first_season] \
                if ds.seasonal else [None]
            if ds.seasonal and not wanted:
                continue
            per = {"updated": 0, "unchanged": 0, "not-published": 0, "failed": 0}
            for season in wanted:
                try:
                    r = ingest_one(ds, season, week, version, client, force,
                                   seasons=seasons)
                except Exception as e:
                    r = {"dataset": name, "season": season, "status": "failed",
                         "rows": 0, "bytes": 0, "error": f"{type(e).__name__}: {e}"}
                    stats["failed"] += 1
                stats["results"].append(r)
                st = r["status"]
                per[st if st in per else "updated" if st == "new" else "failed"] = \
                    per.get(st if st in per else "updated", 0) + 1
                stats["rows"] += r.get("rows", 0)
                stats["bytes"] += r.get("bytes", 0)
                if st in ("new", "updated"):
                    stats["ok"] += 1
                elif st == "unchanged":
                    stats["unchanged"] += 1
                elif st == "not-published":
                    stats["not_published"] += 1
                print(f"  {st:14s} {name:16s} {season or '':>6} "
                      f"{r.get('rows', 0) or '':>7} rows "
                      f"{r.get('bytes', 0)/1e6:7.2f}MB"
                      + (f"  {r.get('error', '')}" if st == "failed" else ""))

            # One health row per dataset, on success AND failure, per the brief.
            ok = per["failed"] == 0
            store.record_health(
                f"nflverse:{name}", ok,
                f"{per['updated']} updated, {per['unchanged']} unchanged, "
                f"{per['not-published']} not published, {per['failed']} failed "
                f"({ds.tier} tier)",
                watermark=time.time() if ok else None)
    return stats


def status():
    rows = store.versions()
    print(f"{'dataset':18s} {'season':>6} {'version':12s} {'rows':>8} "
          f"{'MB':>7}  tier")
    for ds, season, ver, _sha, byts, nrows, tier, _ing, _chk in rows:
        print(f"{ds:18s} {season or '':>6} {ver:12s} {nrows or 0:8d} "
              f"{(byts or 0)/1e6:7.2f}  {tier}")
    if not rows:
        print("  (nothing ingested yet)")
    print()
    for h in store.health() or []:
        if h[0].startswith("nflverse"):
            print(f"  health {h[0]:26s} ok={h[1]} {h[2]}")


def _seasons(spec: str):
    if not spec:
        return None
    if "-" in spec:
        a, b = spec.split("-", 1)
        return list(range(int(a), int(b) + 1))
    return [int(spec)]


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--season", help="2024, or a range like 1999-2025")
    ap.add_argument("--week", type=int, help="scopes the normalize step only")
    ap.add_argument("--dataset", action="append",
                    help=f"one of: {', '.join(nflverse.DATASETS)}")
    ap.add_argument("--tier", choices=(nflverse.LIVE, nflverse.OFFSEASON))
    ap.add_argument("--force", action="store_true",
                    help="re-archive even if the bytes are unchanged")
    ap.add_argument("--version", help="override the data_version (testing)")
    ap.add_argument("--status", action="store_true")
    args = ap.parse_args()

    store.init_db()
    if args.status:
        status()
        return

    bad = [d for d in (args.dataset or []) if d not in nflverse.DATASETS]
    if bad:
        raise SystemExit(f"unknown dataset(s): {', '.join(bad)}")

    t0 = time.time()
    s = run(datasets=args.dataset, seasons=_seasons(args.season), week=args.week,
            tier=args.tier, force=args.force, version=args.version)
    print(f"\nupdated={s['ok']} unchanged={s['unchanged']} "
          f"not-published={s['not_published']} failed={s['failed']} "
          f"rows={s['rows']} fetched={s['bytes']/1e6:.1f}MB "
          f"in {time.time()-t0:.1f}s")


if __name__ == "__main__":
    main()
