"""The source registry: every file the CFB ingest is allowed to fetch.

The work list is ENUMERATED from this registry before a single request is made
- (dataset x season) - so no loop anywhere walks "until empty". A dataset that
is not named here cannot be fetched, and one named in DENIED raises.

Sources, measured 2026-09-16 (`research/cfb_sources_audit.py` reproduces the
coverage figures):

- sportsdataverse-data GitHub releases, built from ESPN's college API. Free and
  unmetered; the GitHub REST API itself is "60 requests per hour"
  unauthenticated (docs.github.com/en/rest/using-the-rest-api/rate-limits-for-
  the-rest-api) and this job spends one call per dataset per run. No documented
  limit on release-asset downloads.
- nflverse `players` release, for the ESPN athlete id -> gsis_id crosswalk.

Upstream re-uploads every historical season many times a day (player_box_2004
had `updated_at` 2026-09-16T09:41Z), so `updated_at` is NOT a change signal.
Change is decided by content hash after download - see `cfb.versioning`.
"""
from dataclasses import dataclass

SDV_REPO = "sportsdataverse/sportsdataverse-data"
NFLVERSE_REPO = "nflverse/nflverse-data"

CURRENT_SEASON = 2026


@dataclass(frozen=True)
class Dataset:
    name: str            # our name, also the raw subdirectory
    repo: str
    tag: str
    asset: str           # "{season}" is substituted for seasonal datasets
    first_season: int | None
    last_season: int | None
    table: str

    @property
    def seasonal(self) -> bool:
        return "{season}" in self.asset

    def seasons(self):
        if not self.seasonal:
            return [None]
        return list(range(self.first_season, self.last_season + 1))

    def asset_name(self, season) -> str:
        return self.asset.format(season=season) if self.seasonal else self.asset


DATASETS = {d.name: d for d in (
    # Games, with division and conference PER GAME. `cfb_schedules` (CFBD +
    # ESPN) is used rather than `espn_cfb_schedules` because only it carries
    # home/away division and conference - 3,831 games in 2025 across every
    # division, against 958 FBS-involved games in the ESPN-only file.
    Dataset("games", SDV_REPO, "cfb_schedules", "cfb_schedules_{season}.parquet",
            2001, CURRENT_SEASON, "cfb_games"),
    # Team identity per season: conference membership moves, so it is keyed
    # (season, team_id) and never carried across seasons.
    Dataset("teams", SDV_REPO, "espn_cfb_teams", "cfb_teams_{season}.parquet",
            2001, CURRENT_SEASON, "cfb_teams"),
    Dataset("rosters", SDV_REPO, "espn_cfb_rosters", "cfb_rosters_{season}.parquet",
            2004, CURRENT_SEASON, "cfb_rosters"),
    Dataset("game_rosters", SDV_REPO, "espn_cfb_game_rosters",
            "game_rosters_{season}.parquet", 2004, CURRENT_SEASON, "cfb_game_rosters"),
    Dataset("player_box", SDV_REPO, "espn_cfb_player_box",
            "player_box_{season}.parquet", 2004, CURRENT_SEASON, "cfb_player_game_box"),
    # Targets live HERE, not in the box score: ESPN's receiving box has no
    # targets column. Built from play-by-play upstream.
    Dataset("player_usage", SDV_REPO, "espn_cfb_adv_player_usage",
            "adv_player_usage_{season}.parquet", 2004, CURRENT_SEASON,
            "cfb_player_game_usage"),
    Dataset("nfl_players", NFLVERSE_REPO, "players", "players.parquet",
            None, None, "cfb_player_xwalk"),
)}

# Datasets that must never be ingested, with the reason. Checked by name AND by
# tag, so a new Dataset pointed at a denied tag under another name still fails.
DENIED = {
    # Ethan, 2026-09-17, on the C02 survey: CFB play-by-play is cfbfastR, 2014+.
    # Re-opening this needs a new argument, not a new import.
    "espn_cfb_pbp": (
        "carries 14 SILENT-ZERO runs against cfbfastR's zero - `touchdown` is non-null "
        "on 100% of rows and exactly 0.000 for 2005-2013, `xp_attempt`/`xp_made` for "
        "2004-2013, `qb_hurry` zero at BOTH ends of a populated middle - so a sum over "
        "those seasons succeeds and returns a wrong number. It reaches 2004 against "
        "cfbfastR's 2014, but a college roster turns over completely in four years, so "
        "pre-2014 play-by-play has little bearing on current analysis and the box-score "
        "history already runs to 2001. docs/C02-cfb-pbp-survey.md; "
        "research/cfb_pbp_survey.py surveys it without ingesting it."),
    "espn_cfb_betting": (
        "fabricates lines: all 6,411 rows with odds_source='default' carry a spread "
        "of 2.5 and 6,395 a total of 55.5 - 6,276 of 6,277 games 2004-2011 and "
        "0-20 games a season since. Historical CFB lines come from "
        "CFBD /lines. research/cfb_sources_audit.py reproduces the counts."),
}


def get(name: str) -> Dataset:
    if name in DENIED:
        raise ValueError(f"{name} is denied: {DENIED[name]}")
    if name not in DATASETS:
        raise KeyError(f"unknown CFB dataset {name!r}; known: {sorted(DATASETS)}")
    d = DATASETS[name]
    if d.tag in DENIED:
        raise ValueError(f"{name} points at denied tag {d.tag}: {DENIED[d.tag]}")
    return d


def plan(names=None, seasons=None):
    """The complete, finite work list: [(Dataset, season)].

    `seasons` restricts seasonal datasets; non-seasonal ones (the nflverse
    players file) are always included when named, because they have no season
    to filter on.
    """
    out = []
    for name in (names or list(DATASETS)):
        d = get(name)
        for s in d.seasons():
            if s is not None and seasons is not None and s not in seasons:
                continue
            out.append((d, s))
    return out
