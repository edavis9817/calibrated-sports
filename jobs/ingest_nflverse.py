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
import hashlib
import io
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

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
        # Standard fantasy scoring that this table published as NULL until
        # 2026-09-16. nflverse has carried all of it since 1999; nothing here
        # was ever filtered, it was simply never mapped.
        #
        # fumbles_lost_total, NOT the sum of the three component columns:
        # measured on 2025, 39 of 19,422 rows disagree and the totals are 267
        # against 228, because a fumble can be lost on a play that is neither a
        # rush, a reception nor a sack. Summing the parts undercounts by ~15%.
        _col(df, "fumbles_lost_total").alias("fumbles_lost"),
        (_col(df, "passing_2pt_conversions", 0).fill_null(0)
         + _col(df, "rushing_2pt_conversions", 0).fill_null(0)
         + _col(df, "receiving_2pt_conversions", 0).fill_null(0)).alias("two_pt_conversions"),
        # special_teams_tds ALONE. CORRECTED 2026-09-23 (a-14). This used to add
        # pt_return_tds on the reading that it was punt-return touchdowns the
        # player scored. It is not: `pt_` is the PUNTING block (pt_att,
        # pt_yards, pt_inside_20, ... pt_return_yards, pt_return_tds), and
        # pt_return_tds is touchdowns ALLOWED on this player's punts. Measured
        # on the raw archive 1999-2026: 330 of 330 non-zero rows are P or K,
        # and nflverse's own fantasy_points_ppr credits none of them (max 1.1
        # on those rows). The earlier note - "all 15 punt-return touchdowns in
        # 2025 carry special_teams_tds = 0" - was this, read the other way
        # round: those 15 were punters. The export published a return TD, worth
        # 6 fantasy points, on 300 punter-weeks across 93 player pages.
        # Returners' touchdowns ARE in special_teams_tds (2025: WR 16, RB 4,
        # CB 3, ...).
        _col(df, "special_teams_tds", 0).fill_null(0).alias("return_tds"),
        *[_col(df, c).alias(c) for c in store.DEF_COLS],
        *[_col(df, c).alias(c) for c in store.ST_COLS],
        *[_col(df, c).alias(c) for c in store.AIR_COLS],
    ]).drop_nulls("gsis_id")

    cols = ("sport", "gsis_id", "season", "week", "season_type", "data_version",
            "player_name", "position", "team", "opponent", "receptions",
            "targets", "receiving_yards", "receiving_tds", "target_share",
            "carries", "rushing_yards", "rushing_tds", "attempts", "completions",
            "passing_yards", "passing_tds", "interceptions", "fantasy_points_ppr",
            "fumbles_lost", "two_pt_conversions", "return_tds",
            *store.DEF_COLS, *store.ST_COLS, *store.AIR_COLS, "source", "ingested_ts")
    rows = [("nfl", r["gsis_id"], r["season"], r["week"], r["season_type"],
             version, r["player_name"], r["position"], r["team"], r["opponent"],
             _f(r["receptions"]), _f(r["targets"]), _f(r["receiving_yards"]),
             _f(r["receiving_tds"]), _f(r["target_share"]), _f(r["carries"]),
             _f(r["rushing_yards"]), _f(r["rushing_tds"]), _f(r["attempts"]),
             _f(r["completions"]), _f(r["passing_yards"]), _f(r["passing_tds"]),
             _f(r["interceptions"]), _f(r["fantasy_points_ppr"]),
             _f(r["fumbles_lost"]), _f(r["two_pt_conversions"]), _f(r["return_tds"]),
             *[_f(r[c]) for c in store.DEF_COLS],
             *[_f(r[c]) for c in store.ST_COLS],
             *[_f(r[c]) for c in store.AIR_COLS],
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
            "away_spread_odds", "roof", "surface", "stadium",
            "home_coach", "away_coach", "source", "ingested_ts")
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
                     r.get("home_coach"), r.get("away_coach"), SOURCE, now))
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


# The columns normalize_pbp reads. Selected at read so a 28-season rebuild does
# not load ~370 columns per play to use nine.
PBP_COLS = ("game_id", "season", "week", "season_type", "play_type", "two_point_attempt",
            "receiver_player_id", "rusher_player_id", "yardline_100", "posteam")
RED_ZONE = 20        # yardline_100 <= RED_ZONE: at or inside the opponent's 20


def pbp_looks(df):
    """Play-by-play frame -> one row per (player, season, week, season_type).

    THE DEFINITION IS nflverse's OWN, not a new one. A target is a play with
    play_type 'pass' (or NULL) naming a receiver; a carry is 'run' or
    'qb_kneel' (or NULL) naming a rusher; two-point attempts count as neither.
    That reproduces stats_player_week's `targets` and `carries` per player-week
    (measured 2026-09-23: exact in 1999, 2010, 2020, 2025, 2026; at most three
    player-weeks off by one elsewhere), so a red-zone look is a subset of the
    same looks the page already totals - never a third count that disagrees
    with both. Kneels ARE carries there, so they are here.

    NULL play_type is load-bearing: 1999-2000 carry 176-206 carries and ~155
    targets a season on plays nflverse left untyped, and dropping them broke the
    reconciliation in exactly those seasons.
    """
    pl = _pl()
    base = df.filter(pl.col("two_point_attempt").fill_null(0) != 1)
    pt = pl.col("play_type")
    tgt = base.filter((pt.is_null() | (pt == "pass")) & pl.col("receiver_player_id").is_not_null()) \
        .select(pl.col("receiver_player_id").alias("gsis_id"), "season", "week", "season_type",
                "posteam", "game_id", "yardline_100", pl.lit(1).alias("is_tgt"),
                pl.lit(0).alias("is_car"))
    car = base.filter((pt.is_null() | pt.is_in(["run", "qb_kneel"]))
                      & pl.col("rusher_player_id").is_not_null()) \
        .select(pl.col("rusher_player_id").alias("gsis_id"), "season", "week", "season_type",
                "posteam", "game_id", "yardline_100", pl.lit(0).alias("is_tgt"),
                pl.lit(1).alias("is_car"))
    looks = pl.concat([tgt, car])
    rz = pl.col("yardline_100").is_not_null() & (pl.col("yardline_100") <= RED_ZONE)
    return (looks.group_by(["gsis_id", "season", "week", "season_type"])
            .agg(pl.col("posteam").drop_nulls().first().alias("team"),
                 pl.col("game_id").drop_nulls().first().alias("game_id"),
                 pl.col("is_tgt").sum().alias("targets"),
                 pl.col("is_car").sum().alias("carries"),
                 (pl.col("is_tgt") * rz.cast(pl.Int64)).sum().alias("rz_targets"),
                 (pl.col("is_car") * rz.cast(pl.Int64)).sum().alias("rz_carries"),
                 pl.col("yardline_100").is_null().sum().alias("no_yardline"))
            .sort(["gsis_id", "season", "week", "season_type"]))


def normalize_pbp(data: bytes, version: str, week=None):
    """play_by_play -> nfl_pbp_looks (a-15, A-B8: red-zone looks)."""
    pl = _pl()
    df = pl.read_parquet(io.BytesIO(data), columns=list(PBP_COLS))
    if week is not None:
        df = df.filter(pl.col("week") == week)
    now = time.time()
    out = pbp_looks(df)
    cols = ("sport", "gsis_id", "season", "week", "season_type", "team", "game_id",
            "data_version", "targets", "carries", "rz_targets", "rz_carries", "no_yardline", "source",
            "ingested_ts")
    rows = [("nfl", r["gsis_id"], r["season"], r["week"], r["season_type"], r["team"] or None,
             r["game_id"], version, int(r["targets"]), int(r["carries"]), int(r["rz_targets"]),
             int(r["rz_carries"]), int(r["no_yardline"]), SOURCE, now)
            for r in out.iter_rows(named=True)]
    return "nfl_pbp_looks", cols, rows


def normalize_weekly_rosters(data: bytes, version: str, week=None):
    """Weekly rosters -> nfl_roster_week (a-14, A-B4: the per-season jersey
    number). Source grain; the season's number is chosen at READ time by the
    export, not here."""
    pl = _pl()
    df = pl.read_parquet(io.BytesIO(data))
    if week is not None:
        df = df.filter(pl.col("week") == week)
    now = time.time()
    cols = ("sport", "gsis_id", "season", "week", "game_type", "team",
            "data_version", "position", "jersey_number", "status", "source",
            "ingested_ts")
    rows = []
    for r in df.iter_rows(named=True):
        gid, team, wk = r.get("gsis_id"), r.get("team"), r.get("week")
        if not gid or not team or wk is None:
            continue                   # no join key, or no place in the PK
        # Verbatim, as text: String in 2002-2015, Int32 from 2016, and '69B' in
        # 2004 - see the nfl_roster_week DDL. Parsing is the reader's decision.
        jersey = r.get("jersey_number")
        rows.append(("nfl", gid, r.get("season"), wk, r.get("game_type"), team,
                     version, r.get("position"),
                     None if jersey is None else str(jersey).strip(),
                     r.get("status"), SOURCE, now))
    return "nfl_roster_week", cols, rows


def normalize_players(data: bytes, version: str, week=None):
    """The gsis_id crosswalk. Writes player_xwalk + player_alias directly - it
    is two tables, not one, so it does not fit the (table, cols, rows) shape."""
    from venues.mapping import build_crosswalk
    n_players, n_aliases = build_crosswalk(data, version)
    print(f"       crosswalk: {n_players:,} players, {n_aliases:,} aliases")
    return None, None, [None] * n_players       # row count only, already written


def normalize_teams(data: bytes, version: str, week=None):
    """Team reference data - the only CSV in the set, and the only non-seasonal
    normalized table.

    KEYED ON THE PUBLISHED ABBREVIATION. The release carries 36 rows for 32
    current teams because a relocation gets its own row (STL and LA, SD and
    LAC, OAK and LV). Folding those into the current franchise would be the
    colour layer undoing the work that makes a trade visible: a 2015 Rams game
    is a St. Louis game, and it should carry St. Louis' colours.

    COLOURS ONLY. team_logo_espn, team_wordmark and the rest are trademarked
    images; a hex value is a fact about a team, a logo is someone's mark, and
    this repository is public.
    """
    pl = _pl()
    df = pl.read_csv(io.BytesIO(data))
    now = time.time()
    cols = ("sport", "team_abbr", "data_version", "team_name", "team_conf",
            "team_division", "team_color", "team_color2", "team_color3",
            "team_color4", "source", "ingested_ts")
    rows = []
    for r in df.iter_rows(named=True):
        abbr = r.get("team_abbr")
        if not abbr:
            continue
        rows.append(("nfl", abbr, version, r.get("team_name"), r.get("team_conf"),
                     r.get("team_division"), r.get("team_color"), r.get("team_color2"),
                     r.get("team_color3"), r.get("team_color4"), SOURCE, now))
    return "nfl_teams", cols, rows


NORMALIZERS = {
    "players": normalize_players,
    "weekly_stats": normalize_weekly_stats,
    "games": normalize_games,
    "snap_counts": normalize_snap_counts,
    "teams": normalize_teams,
    "weekly_rosters": normalize_weekly_rosters,
    "pbp": normalize_pbp,
}


def _f(v):
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


# nflverse publishes `gametime` in US/Eastern, not UTC. Stamping it as UTC puts
# every kickoff 4-5 hours early, which is invisible in a schedule listing and
# catastrophic anywhere that asks "has this game started yet" - the 20:20 ET
# opener reads as 16:20 ET and a pre-kickoff prediction window silently closes
# before the market has even moved. DST matters too: September is EDT (-4),
# January is EST (-5), so a fixed offset is wrong for half the postseason.
EASTERN = ZoneInfo("America/New_York")


def _kickoff(r):
    day, t = r.get("gameday"), r.get("gametime")
    if not day:
        return None
    try:
        stamp = f"{day} {t or '00:00'}"
        naive = datetime.strptime(stamp, "%Y-%m-%d %H:%M")
        return naive.replace(tzinfo=EASTERN).timestamp()
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
        rows_written = (len(rows) if table is None
                        else store.replace_rows(table, cols, rows))
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


def rebuild_from_archive(datasets=None, seasons=None) -> dict:
    """Re-derive the normalized tables from the LOCAL archive. No network.

    This is invariant #2 collecting on its promise: the parquet bytes nflverse
    published are on disk, so widening a projection is a re-parse, not a
    re-fetch. It also keeps the versioning honest - each shard is re-normalized
    under the data_version it was originally pulled as, so no new version is
    manufactured for what is only a parser change.
    """
    stats = {"rebuilt": 0, "rows": 0, "missing": 0, "skipped": 0,
             "duplicate": 0}
    rows = store.versions()
    # The ledger can hold several rows for one all-season (NULL-season) key -
    # see store.latest_version. There is ONE file on disk per key, so re-derive
    # it once; re-recording each duplicate used to insert yet another row.
    seen = set()
    for ds_name, season, version, _sha, _bytes, _rows, _tier, _i, _c in rows:
        if (ds_name, season, version) in seen:
            stats["duplicate"] += 1
            continue
        seen.add((ds_name, season, version))
        if ds_name not in NORMALIZERS:
            stats["skipped"] += 1
            continue
        if datasets and ds_name not in datasets:
            continue
        if seasons and season is not None and season not in seasons:
            continue
        ds = nflverse.DATASETS[ds_name]
        rel = f"{SOURCE}/{version}/{ds.asset(season)}"
        try:
            data = store.read_archived(rel)
        except OSError:
            print(f"  MISSING  {rel}")
            stats["missing"] += 1
            continue
        fn = NORMALIZERS[ds_name]
        kwargs = {"week": None}
        if ds_name == "games":
            kwargs["seasons"] = [season] if season else None
        table, cols, out = fn(data, version, **kwargs)
        n = (len(out) if table is None
             else store.replace_rows(table, cols, out))
        # Record the hash of the bytes just normalized, not the ledger's: on a
        # duplicated key the row read first may name a same-day pull whose
        # file was since overwritten.
        store.record_version(ds_name, season, version,
                             hashlib.sha256(data).hexdigest(), len(data), rel,
                             rows=n, tier=ds.tier)
        stats["rebuilt"] += 1
        stats["rows"] += n
        print(f"  rebuilt  {ds_name:14s} {season or '':>6} {version}  {n:>7,} rows")
    store.record_health("nflverse:rebuild", stats["missing"] == 0,
                        f"rebuilt {stats['rebuilt']} shards, {stats['rows']} rows, "
                        f"{stats['missing']} missing from archive",
                        watermark=time.time())
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
    ap.add_argument("--from-archive", action="store_true",
                    help="re-derive normalized tables from data/raw, no network")
    args = ap.parse_args()

    store.init_db()
    if args.status:
        status()
        return
    if args.from_archive:
        t0 = time.time()
        s = rebuild_from_archive(args.dataset, _seasons(args.season))
        print(f"\nrebuilt={s['rebuilt']} rows={s['rows']} missing={s['missing']} "
              f"in {time.time()-t0:.1f}s")
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
