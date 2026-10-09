"""Reading NFL weather back out of the feeds store: a FORECAST and a RECORD are two
functions, and neither can return the other (a-66).

WHY TWO FUNCTIONS AND NOT A `kind` ARGUMENT. c-31's total was scored with the wind
recorded at the game, which nothing has before kickoff. A pre-game figure that reads
recorded weather is look-ahead, and it is invisible: the number is plausible and the
backtest improves. So the pre-game reader takes no argument that could select a
record, and it applies two tests of its own to every row:

  * `kind = 'forecast'`. The reanalysis (`archive`) is what happened.
  * `fetched_ts < kickoff_ts`. The forecast endpoint also answers for PAST hours
    (Open-Meteo's archive lags about five days), so a row of kind `forecast` fetched
    after the game is a model's account of an hour that has already gone - not a
    forecast of it. The kind alone does not say which; the fetch time does.

and a third from the caller: `fetched_ts <= as_of_ts`, so a rebuild "as of Thursday"
reads Thursday's forecast and not Saturday's.

EVERY VERSION IS A CANDIDATE, not only the current one. The store versions a forecast
by ingestion time, so Tuesday's row is closed when Saturday's arrives; reading only
`valid_to_ts IS NULL` would answer every as-of question with the newest forecast.

`lead_hours` is returned on every forecast row: a wind forecast six days out and one
six hours out are different claims about the same kickoff.
"""
import sqlite3

from feeds import paths

SPORT = "nfl"
COLS = ("game_id", "kickoff_ts", "fetched_ts", "observed_hour_ts", "temperature_f",
        "wind_speed_mph", "wind_gusts_mph", "precipitation_in", "playing_conditions",
        "roof_type", "coord_offset_km", "grid_offset_km")


def connect_ro(db_path=None):
    path = (db_path or paths.db_path()).replace("\\", "/")
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)


def pregame_forecasts(con, as_of_ts, game_ids=None):
    """{game_id: row} - per game, the LATEST forecast taken before both its kickoff and
    `as_of_ts`. A game with no such row is absent; nothing falls back to a record.

    row: COLS plus `lead_hours` (kickoff minus fetch) and `kind`, always 'forecast'."""
    if as_of_ts is None:
        raise ValueError("a pre-game forecast is read as of an instant; None is not one")
    q = (f"SELECT {', '.join(COLS)} FROM weather_at_kickoff WHERE sport = ? "
         "AND kind = 'forecast' AND fetched_ts IS NOT NULL AND kickoff_ts IS NOT NULL "
         "AND fetched_ts < kickoff_ts AND fetched_ts <= ? ORDER BY fetched_ts")
    wanted = None if game_ids is None else {str(g) for g in game_ids}
    out = {}
    for r in con.execute(q, (SPORT, as_of_ts)):
        row = dict(zip(COLS, r))
        if wanted is not None and row["game_id"] not in wanted:
            continue
        row["kind"] = "forecast"
        row["lead_hours"] = (row["kickoff_ts"] - row["fetched_ts"]) / 3600.0
        out[row["game_id"]] = row          # ordered by fetched_ts: the latest wins
    return out


def recorded(con, game_ids=None):
    """{game_id: row} - the reanalysis for games already played. NEVER an input to
    anything computed before a kickoff; it is here so the two are read by name."""
    q = (f"SELECT {', '.join(COLS)} FROM weather_at_kickoff WHERE sport = ? "
         "AND kind = 'archive' AND valid_to_ts IS NULL")
    wanted = None if game_ids is None else {str(g) for g in game_ids}
    out = {}
    for r in con.execute(q, (SPORT,)):
        row = dict(zip(COLS, r))
        if wanted is None or row["game_id"] in wanted:
            row["kind"] = "archive"
            out[row["game_id"]] = row
    return out
