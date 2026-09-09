"""nflverse source registry: where each dataset lives, and which tier it is in.

VERIFIED AGAINST THE LIVE RELEASES 2026-09-09. Three things that are not
guessable and each cost time to find:

1. THE `player_stats` RELEASE IS FROZEN. Last asset update 2025-05-07, no 2025
   or 2026 files. The maintained release is `stats_player`, which carries
   `stats_player_week_{season}.parquet` for all 27 seasons. Point at the old
   one and the most important table in the project - receptions, targets,
   carries - silently stops at 2024 while every job keeps reporting healthy.

2. THE MISSING 2019 FILE IS AN ARTIFACT OF THAT FROZEN RELEASE. In
   `player_stats`, `stats_player_week_2019.parquet` 404s and you need the
   legacy `player_stats_2019.parquet`. In `stats_player`, 2019 is present like
   every other season. Using the right release, the special case evaporates.

3. `contracts/contracts.parquet` 404s; the file is `historical_contracts.parquet`.

Tiers are load-bearing, not documentation. `participation` refreshes only after
the postseason, so a feature that reads `route` or `was_pressure` works in April
against 2024 and is silently empty in October against the live season. That
failure is invisible - the column exists and is full of nulls - which is why
require_live() raises instead of returning them. See tier_of().
"""
import hashlib
from dataclasses import dataclass, field

import httpx

import config

LIVE = "live"              # refreshed in-season; usable on a Sunday
OFFSEASON = "offseason"    # backtest and research only


@dataclass(frozen=True)
class Dataset:
    name: str
    release: str
    filename: str          # may contain {season}
    tier: str
    normalize: bool = False           # flattened into SQLite, not just archived
    first_season: int = 1999
    fields: tuple = ()                # tier-guarded columns this dataset owns
    note: str = ""

    @property
    def seasonal(self) -> bool:
        return "{season}" in self.filename

    def url(self, season: int = None) -> str:
        name = self.filename.format(season=season) if self.seasonal else self.filename
        return f"{config.NFLVERSE_BASE}/{self.release}/{name}"

    def asset(self, season: int = None) -> str:
        return self.filename.format(season=season) if self.seasonal else self.filename


DATASETS = {
    # ---- live tier: refreshed in-season -------------------------------------
    "weekly_stats": Dataset(
        "weekly_stats", "stats_player", "stats_player_week_{season}.parquet",
        LIVE, normalize=True,
        fields=("receptions", "targets", "carries", "receiving_yards",
                "rushing_yards", "passing_yards", "target_share"),
        note="NOT the frozen player_stats release - see module docstring"),
    "games": Dataset(
        "games", "schedules", "games.parquet", LIVE, normalize=True,
        fields=("spread_line", "total_line", "home_moneyline", "away_moneyline"),
        note="schedules AND closing lines back to 1999"),
    "pbp": Dataset("pbp", "pbp", "play_by_play_{season}.parquet", LIVE),
    "snap_counts": Dataset(
        "snap_counts", "snap_counts", "snap_counts_{season}.parquet", LIVE,
        normalize=True, first_season=2012,
        fields=("offense_snaps", "offense_pct")),
    "ftn_charting": Dataset(
        "ftn_charting", "ftn_charting", "ftn_charting_{season}.parquet", LIVE,
        first_season=2022,
        fields=("is_motion", "is_play_action", "is_rpo", "n_blitzers",
                "is_drop", "is_contested_ball", "n_defense_box")),
    "ngs_receiving": Dataset("ngs_receiving", "nextgen_stats",
                             "ngs_receiving.parquet", LIVE, first_season=2016),
    "ngs_rushing": Dataset("ngs_rushing", "nextgen_stats",
                           "ngs_rushing.parquet", LIVE, first_season=2016),
    "ngs_passing": Dataset("ngs_passing", "nextgen_stats",
                           "ngs_passing.parquet", LIVE, first_season=2016),
    "weekly_rosters": Dataset("weekly_rosters", "weekly_rosters",
                              "roster_weekly_{season}.parquet", LIVE,
                              first_season=2002),
    "depth_charts": Dataset("depth_charts", "depth_charts",
                            "depth_charts_{season}.parquet", LIVE,
                            first_season=2001),

    # ---- offseason tier: DOES NOT REFRESH IN-SEASON -------------------------
    "participation": Dataset(
        "participation", "pbp_participation",
        "pbp_participation_{season}.parquet", OFFSEASON, first_season=2016,
        fields=("route", "defense_man_zone_type", "defense_coverage_type",
                "was_pressure", "offense_players", "defense_players",
                "offense_formation", "offense_personnel", "defenders_in_box",
                "number_of_pass_rushers", "time_to_throw", "ngs_air_yards"),
        note="refreshes only after the postseason"),

    # ---- reference: rarely changes, no season dimension ---------------------
    "contracts": Dataset("contracts", "contracts",
                         "historical_contracts.parquet", OFFSEASON,
                         note="NOT contracts.parquet - that 404s"),
    "draft_picks": Dataset("draft_picks", "draft_picks", "draft_picks.parquet",
                           OFFSEASON),
}

# field -> dataset, built once. Two datasets claiming the same field name would
# be an ambiguity we want to hear about at import, not at 3pm on a Sunday.
_FIELD_TIER = {}
for _ds in DATASETS.values():
    for _f in _ds.fields:
        if _f in _FIELD_TIER and _FIELD_TIER[_f][1] != _ds.tier:
            raise RuntimeError(f"field {_f!r} claimed by two tiers")
        _FIELD_TIER[_f] = (_ds.name, _ds.tier)


class TierViolation(RuntimeError):
    """An offseason-tier field was requested in a live context."""


def tier_of(field_name: str) -> str:
    """Which tier does this column belong to? None if we do not own it."""
    hit = _FIELD_TIER.get(field_name)
    return hit[1] if hit else None


def dataset_of(field_name: str) -> str:
    hit = _FIELD_TIER.get(field_name)
    return hit[0] if hit else None


def require_live(*field_names):
    """Raise unless every field is usable in-season.

    Deliberately loud. participation carries `route`, `was_pressure` and the
    coverage columns, and it does not refresh until the postseason - so a model
    built in April on 2024 data works perfectly and the same code in October
    reads nulls for the current week. Nulls do not look like a bug; they look
    like a quiet player. Raising is the only version of this that gets noticed.
    """
    bad = [(f, _FIELD_TIER[f][0]) for f in field_names
           if _FIELD_TIER.get(f, (None, LIVE))[1] == OFFSEASON]
    if bad:
        names = ", ".join(f"{f} (from {ds})" for f, ds in bad)
        raise TierViolation(
            f"offseason-tier field(s) requested in live context: {names}. "
            f"These refresh only after the postseason, so in-season they are "
            f"silently empty. Use them for backtests only.")
    return True


def live_fields() -> tuple:
    return tuple(f for f, (_, t) in _FIELD_TIER.items() if t == LIVE)


def offseason_fields() -> tuple:
    return tuple(f for f, (_, t) in _FIELD_TIER.items() if t == OFFSEASON)


# ---- fetching ---------------------------------------------------------------

class NotPublished(LookupError):
    """Upstream has no file for this season yet. Normal, not an error.

    On the day before a season opener, pbp/snaps/FTN/participation have no file
    for the new season at all. A live-tier job that treats that as a failure
    pages somebody every night in August.
    """


def fetch(dataset: Dataset, season: int = None, client: httpx.Client = None) -> tuple:
    """Download one asset. Returns (bytes, sha256). Raises NotPublished on 404."""
    url = dataset.url(season)
    close = client is None
    client = client or httpx.Client(timeout=config.NFLVERSE_TIMEOUT,
                                    follow_redirects=True,
                                    headers={"User-Agent": config.USER_AGENT})
    try:
        r = client.get(url)
        if r.status_code == 404:
            raise NotPublished(f"{dataset.name} {season or ''}: no asset at {url}")
        r.raise_for_status()
        data = r.content
        return data, hashlib.sha256(data).hexdigest()
    finally:
        if close:
            client.close()


def current_season(now=None) -> int:
    """The NFL season year for a given date.

    A season is named for the calendar year it starts in and runs into
    February, so January and February belong to the previous season's year.
    March is the boundary: the league year turns over then and nflverse starts
    publishing next-season files.
    """
    from datetime import datetime, timezone
    now = now or datetime.now(timezone.utc)
    return now.year if now.month >= 3 else now.year - 1


def seasons_for(dataset: Dataset, start: int = None, end: int = None):
    """Season range this dataset could plausibly have, clamped to its debut."""
    start = max(start or config.NFLVERSE_FIRST_SEASON, dataset.first_season)
    end = end or start
    return range(start, end + 1) if dataset.seasonal else [None]
