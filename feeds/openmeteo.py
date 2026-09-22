"""Open-Meteo: the weather at a kickoff, keyed to the venue's own coordinates.

FREE, KEYLESS, AND BATCHED. Coordinates are comma-separated, so one call carries up to
`OPEN_METEO_MAX_COORDS` venues for one date and the response is a LIST in the order
asked. Units are pinned on every request (fahrenheit, mph, inch, UTC) so a provider
default cannot silently change what a number means.

TWO ENDPOINTS, AND WHICH ONE IS A FACT ABOUT THE DATE. The archive lags real weather by
about five days; inside that window the forecast endpoint answers for past hours through
`past_days`. `kind` records which endpoint a row came from, because a forecast for a
kickoff three days out and a reanalysis of the same kickoff a week later are different
claims about the same hour, and the store keeps both.

THE KICKOFF HOUR IS FLOORED, not interpolated: the provider publishes hourly values, and
inventing a minute-level number from them would be a figure the source does not have.
"""
import time
from datetime import datetime, timezone

from feeds import sources

HOUR = 3600


def day(ts):
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d")


def floor_hour(ts):
    return float(int(ts // HOUR) * HOUR)


def endpoint_for(kickoff_ts, now=None):
    """('archive'|'forecast', url). Archive for anything older than the lag, forecast
    otherwise - including past days inside the lag, which it still answers."""
    now = time.time() if now is None else now
    if kickoff_ts < now - sources.ARCHIVE_LAG_DAYS * 86400:
        return "archive", sources.OPEN_METEO_ARCHIVE
    return "forecast", sources.OPEN_METEO_FORECAST


def build_params(points, date, kind):
    """points: [(lat, lon)]. One date, many coordinates, one call."""
    if not points:
        raise ValueError("no coordinates: a weather call over nothing is not a call")
    if len(points) > sources.OPEN_METEO_MAX_COORDS:
        raise ValueError(f"{len(points)} coordinates exceeds the "
                         f"{sources.OPEN_METEO_MAX_COORDS} batched per call")
    params = {
        "latitude": ",".join(f"{lat:.4f}" for lat, _ in points),
        "longitude": ",".join(f"{lon:.4f}" for _, lon in points),
        "hourly": ",".join(sources.OPEN_METEO_HOURLY),
        **sources.OPEN_METEO_UNITS,
    }
    if kind == "archive":
        params["start_date"] = params["end_date"] = date
    else:
        # The forecast endpoint takes a window rather than a range; asking for the day
        # either side covers a kickoff near a UTC boundary.
        params["start_date"] = params["end_date"] = date
    return params


def _as_list(payload):
    return payload if isinstance(payload, list) else [payload]


def at_hour(payload, index, hour_ts):
    """The provider's values for one coordinate at one hour, or None if it did not
    publish that hour. Never the nearest hour: a value from two hours away is a
    different claim, and the caller should see the absence."""
    blocks = _as_list(payload)
    if index >= len(blocks):
        return None
    block = blocks[index]
    hourly = block.get("hourly") or {}
    times = hourly.get("time") or []
    want = datetime.fromtimestamp(hour_ts, timezone.utc).strftime("%Y-%m-%dT%H:00")
    if want not in times:
        return None
    i = times.index(want)

    def v(name):
        series = hourly.get(name) or []
        return series[i] if i < len(series) else None

    return {
        "observed_hour_ts": hour_ts,
        "temperature_f": v("temperature_2m"),
        "relative_humidity_pct": v("relative_humidity_2m"),
        "precipitation_in": v("precipitation"),
        "wind_speed_mph": v("wind_speed_10m"),
        "wind_gusts_mph": v("wind_gusts_10m"),
        "cloud_cover_pct": v("cloud_cover"),
        "provider_elevation_m": block.get("elevation"),
        # The grid cell the provider answered from, as it reports it - not the point asked.
        "provider_latitude": block.get("latitude"),
        "provider_longitude": block.get("longitude"),
    }


def grid_offset_km(lat, lon, measured):
    """Distance from the point asked for to the grid cell that answered, or None if the
    provider did not say which cell."""
    plat, plon = measured.get("provider_latitude"), measured.get("provider_longitude")
    if plat is None or plon is None:
        return None
    from feeds.nfl_venues import haversine_km
    return round(haversine_km(lat, lon, plat, plon), 3)


def row(sport, game_id, kind, venue, kickoff_ts, measured, *, fetched_ts=None,
        coord_source=None, coord_ref=None, coord_offset_km=None, roof_type=None,
        game_roof=None, playing_conditions=None):
    """Schema order for `weather_at_kickoff`. `fetched_ts` is kept on forecast rows only:
    it is what makes a forecast's horizon answerable, and an archive row has none."""
    return (sport, str(game_id), "open-meteo", kind, venue["venue_id"],
            venue["latitude"], venue["longitude"], kickoff_ts,
            measured["observed_hour_ts"], measured["temperature_f"],
            measured["relative_humidity_pct"], measured["precipitation_in"],
            measured["wind_speed_mph"], measured["wind_gusts_mph"],
            measured["cloud_cover_pct"], measured["provider_elevation_m"],
            measured.get("provider_latitude"), measured.get("provider_longitude"),
            grid_offset_km(venue["latitude"], venue["longitude"], measured),
            coord_source, coord_ref, coord_offset_km,
            fetched_ts if kind == "forecast" else None,
            roof_type, game_roof, playing_conditions)
