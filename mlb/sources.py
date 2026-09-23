"""The one MLB source, the terms that permit it, and the ones refused.

Terms were read BEFORE the code was written (c-03), because the contracts survey (F06)
found a licence blocker only after the data work was done.

RETROSHEET - https://www.retrosheet.org/notice.txt, fetched 2026-09-22:
    "Recipients of Retrosheet data are free to make any desired use of the information,
     including (but not limited to) selling it, giving it away, or producing a commercial
     product based upon the data. Retrosheet has one requirement for any such transfer
     of data or product development, which is that the following statement must appear
     prominently:"
The statement is `ATTRIBUTION` below, verbatim. Anything that publishes MLB data derived
from this store must carry it; that obligation is recorded as a limitation row so it
cannot live only in a docstring.

REFUSED, by host, with the reason:
  statsapi.mlb.com / baseballsavant.mlb.com / gdx.mlb.com - MLBAM's notice
      (http://gdx.mlb.com/components/copyright.txt, fetched 2026-09-22): "Only
      individual, non-commercial, non-bulk use of the Materials is permitted and any
      other use of the Materials is prohibited without prior written authorization from
      MLBAM." An ingest is bulk use.
  fangraphs.com / baseball-reference.com - both prohibit automated retrieval in their
      terms; not re-read tonight, refused on the standing reading rather than fetched.
"""
from urllib.parse import urlparse

RETROSHEET_HOST = "www.retrosheet.org"
NOTICE_URL = "https://www.retrosheet.org/notice.txt"

ATTRIBUTION = ("The information used here was obtained free of charge from and is "
               "copyrighted by Retrosheet.  Interested parties may contact Retrosheet at "
               "\"www.retrosheet.org\".")

SOURCE_NAME = "Retrosheet"
SOURCE_URL = "https://www.retrosheet.org"


def attribution_block() -> dict:
    """The shape filed to track A as A-C10 for `SportManifest.attribution`. The statement
    is `ATTRIBUTION` verbatim; the rest says whose it is and where the terms live."""
    return {"statement": ATTRIBUTION, "source": SOURCE_NAME, "source_url": SOURCE_URL,
            "terms_url": NOTICE_URL}


def notice_text() -> str:
    """`mlb/NOTICE.txt` in an export tree: the statement first, verbatim, then its origin."""
    return (f"{ATTRIBUTION}\n\n"
            f"MLB statistics in this tree are derived from {SOURCE_NAME} data "
            f"({SOURCE_URL}). Terms: {NOTICE_URL}\n")


# The per-season bundle: allplayers, gameinfo, teamstats, batting, pitching, fielding,
# plays. Measured 2026-09-22: 2024 and 2025 exist (~9.9 MB each); 2026 returns 404 -
# Retrosheet publishes a season after it ends, so there is NO current-season MLB data.
SEASON_URL = "https://www.retrosheet.org/downloads/{season}/{season}csvs.zip"
FEED = "retrosheet_csv"

DENIED_HOSTS = {
    "statsapi.mlb.com": "MLBAM: individual, non-commercial, non-bulk use only",
    "baseballsavant.mlb.com": "MLBAM: individual, non-commercial, non-bulk use only",
    "gdx.mlb.com": "MLBAM: individual, non-commercial, non-bulk use only",
    "www.mlb.com": "MLBAM: individual, non-commercial, non-bulk use only",
    "www.fangraphs.com": "terms prohibit automated retrieval",
    "fangraphs.com": "terms prohibit automated retrieval",
    "www.baseball-reference.com": "terms prohibit automated retrieval",
    "baseball-reference.com": "terms prohibit automated retrieval",
}

FIRST_SEASON = 1901     # earliest modern-era bundle; older ones exist and are not planned
LAST_PUBLISHED = 2025   # measured; bump only after fetching the next one succeeds


class DeniedSource(Exception):
    pass


def check_url(url: str) -> str:
    """Returns the URL it approved, or raises. Only Retrosheet is fetchable."""
    host = urlparse(url).hostname or ""
    if host in DENIED_HOSTS:
        raise DeniedSource(f"{host}: {DENIED_HOSTS[host]}")
    if host != RETROSHEET_HOST:
        raise DeniedSource(f"{host}: not an approved MLB source (only {RETROSHEET_HOST})")
    return url


def season_url(season: int) -> str:
    season = int(season)
    if not FIRST_SEASON <= season <= LAST_PUBLISHED:
        raise ValueError(f"MLB season {season} outside {FIRST_SEASON}-{LAST_PUBLISHED}: "
                         f"Retrosheet publishes a season only after it ends")
    return check_url(SEASON_URL.format(season=season))
