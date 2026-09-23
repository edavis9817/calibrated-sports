"""Every feed this job is allowed to fetch, enumerated before a request is made.

FREE AND KEYLESS, all three: nflverse and sportsdataverse GitHub release assets, the
public RSS documents themselves, and Open-Meteo (no key, no account). Nothing here
spends an API credit.

WHAT IS DENIED, by name, so a future session has to argue rather than import:
  * any full-text or article-body endpoint. News is headline, source, timestamp, link.
  * any "current injury status" scrape. The official report is what the site can say was
    KNOWN; a current-state table is worth little and is wrong about the past.
"""
from dataclasses import dataclass

NFLVERSE_REPO = "nflverse/nflverse-data"
SDV_REPO = "sportsdataverse/sportsdataverse-data"

OPEN_METEO_ARCHIVE = "https://archive-api.open-meteo.com/v1/archive"
OPEN_METEO_FORECAST = "https://api.open-meteo.com/v1/forecast"
# The hourly variables asked for, in one place: the parser indexes them by name and the
# units are pinned on the request so a provider default cannot change what a number means.
OPEN_METEO_HOURLY = ("temperature_2m", "relative_humidity_2m", "precipitation",
                     "wind_speed_10m", "wind_gusts_10m", "cloud_cover")
OPEN_METEO_UNITS = {"temperature_unit": "fahrenheit", "wind_speed_unit": "mph",
                    "precipitation_unit": "inch", "timezone": "UTC"}
# Open-Meteo's free tier is "10,000 API calls per day" with no key
# (open-meteo.com/en/terms). Coordinates batch: one call can carry many venues.
OPEN_METEO_MAX_COORDS = 50
# The archive lags real weather by ~5 days; inside that window the forecast endpoint
# still answers for past hours (`past_days`). Measured, not assumed - see the job.
ARCHIVE_LAG_DAYS = 6


@dataclass(frozen=True)
class Release:
    feed: str
    repo: str
    tag: str
    asset: str              # "{season}" substituted for seasonal assets
    first_season: int
    table: str | None

    def asset_name(self, season):
        return self.asset.format(season=season)


RELEASES = {
    # The official injury report, 2009+. 16 columns, the Wed-Fri report, and NO capture
    # time of its own - which is why the store versions it by ingestion time.
    "injuries": Release("injuries", NFLVERSE_REPO, "injuries", "injuries_{season}.parquet",
                        2009, "injury_reports"),
    # Venue coordinates, elevation, timezone and a DOME flag, per season, per team.
    # The reason weather is possible at all: no NFL feed we trust carries stadium
    # coordinates (nfldata's airports.csv is an AIRPORT, tens of km from the stadium).
    # The NFL schedule: one file, 1999 onward, carrying `stadium_id`, `stadium` and the
    # per-game `roof`. Read for kickoffs and venues; it is NOT written to a table here -
    # the logger's `nfl_games` is the schedule of record, and a second copy would be a
    # second answer. `table` is None for that reason.
    "nfl_schedule": Release("nfl_schedule", NFLVERSE_REPO, "schedules", "games.parquet",
                            1999, None),
    "cfb_venues": Release("cfb_venues", SDV_REPO, "cfb_team_info",
                          "cfb_team_info_{season}.parquet", 2001, "venues"),
}

# feed name -> (sport, url). Public RSS documents, fetched verbatim and archived.
RSS_FEEDS = {
    "espn_nfl": ("nfl", "https://www.espn.com/espn/rss/nfl/news"),
    "espn_cfb": ("cfb", "https://www.espn.com/espn/rss/ncf/news"),
    "cbs_nfl": ("nfl", "https://www.cbssports.com/rss/headlines/nfl/"),
    "cbs_cfb": ("cfb", "https://www.cbssports.com/rss/headlines/college-football/"),
}

DENIED = {
    "article_text": ("full article text is never stored or summarised: headline, source, "
                     "timestamp and link only. The only words this project publishes "
                     "about someone else's reporting are the ones they put in the "
                     "headline"),
    "injury_current_status": ("a current-state injury table cannot say what was known on "
                              "Sunday morning, which is the whole value; the official "
                              "report, versioned by ingestion time, can"),
}


def release(name) -> Release:
    if name in DENIED:
        raise ValueError(f"{name} is denied: {DENIED[name]}")
    return RELEASES[name]
