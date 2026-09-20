"""Parquet -> rows in schema order, with every drop counted.

Same contract as `cfb.normalize`: a structurally wrong file is REFUSED rather than
row-filtered, because a filter hides the structure being wrong.
"""
import io

import polars as pl

from feeds import schema


class RefusedFile(Exception):
    pass


def _col(df, name):
    return df[name] if name in df.columns else pl.Series([None] * len(df))


def _s(v):
    return None if v is None else str(v)


def _i(v):
    try:
        return None if v is None else int(v)
    except (TypeError, ValueError):
        return None


def _f(v):
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


def _ts(v):
    """Upstream publishes a tz-aware datetime; anything else is refused rather than
    coerced, because a wrong as-of is worse than a missing one."""
    if v is None:
        return None
    if hasattr(v, "timestamp"):
        return float(v.timestamp())
    raise RefusedFile(f"date_modified is {type(v).__name__}, expected a datetime: {v!r}")


def injuries(body: bytes, season: int):
    """nflverse `injuries_<season>.parquet` -> `injury_reports` rows.

    TWO ERAS, MEASURED 2026-09-19. 2009-2024 carry `date_modified` (an upstream capture
    time) and NO `season_type`; 2025 and 2026 carry `season_type` and NO `date_modified`.
    Both are 16 columns, which is why a column count is not a schema check. Where upstream
    publishes a capture time it is kept as `upstream_asof_ts`; where it does not, our
    ingestion stamp is the only as-of and the versioning is what makes the report
    answerable as of an instant.
    """
    df = pl.read_parquet(io.BytesIO(body))
    need = {"season", "team", "week", "gsis_id"}
    missing = need - set(df.columns)
    if missing:
        raise RefusedFile(f"injuries {season}: missing {sorted(missing)}")
    unknown_time = [c for c in df.columns
                    if c.lower() in ("scraped_at", "asof", "as_of", "captured_at")]
    if unknown_time:
        # A capture column this parser does not read is a capture column nobody reads.
        raise RefusedFile(f"injuries {season}: unrecognised capture-time column(s) "
                          f"{unknown_time} - read them or refuse, do not ignore them")
    rows, dropped = [], {}
    for r in df.iter_rows(named=True):
        if not r.get("gsis_id") or r.get("week") is None:
            dropped["no_player_or_week"] = dropped.get("no_player_or_week", 0) + 1
            continue
        rows.append((
            # season_type is absent before 2025 and game_type is absent nowhere, so the
            # key falls back rather than carrying a null into a primary key.
            "nfl", _i(r.get("season")), _s(r.get("season_type")) or _s(r.get("game_type")),
            _i(r.get("week")),
            _s(r.get("team")), _s(r.get("gsis_id")), _s(r.get("full_name")),
            _s(r.get("position")), _s(r.get("report_primary_injury")),
            _s(r.get("report_secondary_injury")), _s(r.get("report_status")),
            _s(r.get("practice_primary_injury")), _s(r.get("practice_secondary_injury")),
            _s(r.get("practice_status")), _s(r.get("game_type")), _ts(r.get("date_modified")),
        ))
    return _dedupe(rows, "injury_reports", dropped), dropped


def venues(body: bytes, season: int):
    """sportsdataverse `cfb_team_info_<season>.parquet` -> `venues` rows.

    One row per VENUE, not per team: two teams can share a stadium, and a venue with two
    sets of coordinates would be two answers to one question."""
    df = pl.read_parquet(io.BytesIO(body))
    need = {"venue_id", "latitude", "longitude"}
    missing = need - set(df.columns)
    if missing:
        raise RefusedFile(f"cfb_team_info {season}: missing {sorted(missing)}")
    rows, dropped, seen = [], {}, {}
    for r in df.iter_rows(named=True):
        vid = _i(r.get("venue_id"))
        lat, lon = _f(r.get("latitude")), _f(r.get("longitude"))
        if vid is None:
            dropped["no_venue_id"] = dropped.get("no_venue_id", 0) + 1
            continue
        if lat is None or lon is None:
            dropped["no_coordinates"] = dropped.get("no_coordinates", 0) + 1
            continue
        row = ("cfb", season, vid, _s(r.get("venue_name")), _s(r.get("city")),
               _s(r.get("state")), _s(r.get("country_code")), lat, lon,
               _f(r.get("elevation")), _s(r.get("timezone")),
               None if r.get("dome") is None else int(bool(r.get("dome"))),
               None if r.get("grass") is None else int(bool(r.get("grass"))),
               _i(r.get("capacity")))
        if vid in seen and seen[vid][7:9] != row[7:9]:
            dropped["venue_with_two_coordinates"] = dropped.get(
                "venue_with_two_coordinates", 0) + 1
            continue
        seen[vid] = row
    rows = list(seen.values())
    return _dedupe(rows, "venues", dropped), dropped


def _dedupe(rows, table, dropped):
    """A repeated key has no honest single value: drop both and count them."""
    cols = schema.columns(table)
    kidx = [cols.index(k) for k in schema.keys(table)]
    counts = {}
    for r in rows:
        k = tuple(r[i] for i in kidx)
        counts[k] = counts.get(k, 0) + 1
    dup = {k for k, n in counts.items() if n > 1}
    if dup:
        dropped["duplicate_key_rows"] = sum(counts[k] for k in dup)
    return [r for r in rows if tuple(r[i] for i in kidx) not in dup]
