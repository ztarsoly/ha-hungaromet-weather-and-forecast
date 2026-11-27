"""Downloading HungaroMet's observation feeds and averaging the nearby stations."""

import csv
import importlib
import io
import logging
import math
import statistics
import zipfile
from collections import Counter
from datetime import datetime, timedelta
from typing import Any

import requests

try:
    from .const import (
        DEFAULT_DISTANCE_KM,
        URL_DAILY,
        URL_HOURLY,
        URL_TEN_MINUTES,
    )
except ImportError:  # pragma: no cover - standalone CLI usage
    from const import (
        DEFAULT_DISTANCE_KM,
        URL_DAILY,
        URL_HOURLY,
        URL_TEN_MINUTES,
    )

from homeassistant.util import dt as dt_util

try:  # Optional local coordinates for CLI usage
    # By name: "from . import" would count as importing the whole integration
    _local_config = importlib.import_module(
        f"{__package__}.local_config" if __package__ else "local_config"
    )
except ImportError:  # pragma: no cover - optional helper
    _local_config = None  # pylint: disable=invalid-name

_LOGGER = logging.getLogger(__package__)
REQUEST_TIMEOUT = 15

# One station's reading: its values by column name, None where there is none
Row = dict[str, Any]

# Texts of a feed's field that hold no value
BLANKS = frozenset({"", "NA", "N/A", "n/a", "NaN", "nan", "null", "NULL", "None"})
# The feeds' "no data" values, in number and in text columns
NO_DATA = (-999, "-999")


# Compass directions are averaged as angles, not plain numbers
DIRECTION_COLUMNS = ("fsd", "fd", "fxd")

# HungaroMet's 16-point compass abbreviations and the English keys used as states
WIND_DIRECTIONS = {
    "É": "n",
    "ÉÉK": "nne",
    "ÉK": "ne",
    "KÉK": "ene",
    "K": "e",
    "KDK": "ese",
    "DK": "se",
    "DDK": "sse",
    "D": "s",
    "DDNy": "ssw",
    "DNy": "sw",
    "NyDNy": "wsw",
    "Ny": "w",
    "NyÉNy": "wnw",
    "ÉNy": "nw",
    "ÉÉNy": "nnw",
}


def _get_reference_coords(hass, location=None):
    if location is not None:
        return location
    if hass is not None and getattr(hass, "config", None) is not None:
        return hass.config.latitude, hass.config.longitude
    if _local_config is not None:  # pragma: no cover - standalone CLI usage
        return _local_config.ref_lat, _local_config.ref_lon
    _LOGGER.error(
        "Reference coordinates are unavailable. Provide Home Assistant config "
        "or local_config module."
    )
    raise ValueError(
        "Reference coordinates are unavailable. Provide Home Assistant config "
        "or local_config module."
    )


def fetch_data(url: str) -> list[Row]:
    """Download a zipped feed and read its CSV file."""
    response = requests.get(url, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    return parse_csv(_unzip_first_file(response.content).decode("utf-8-sig"))


def _unzip_first_file(content: bytes) -> bytes:
    """Return the content of a ZIP file's first file.

    An archive that cannot be read raises BadZipFile or ValueError.
    """
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            names = archive.namelist()
            first = archive.read(names[0]) if names else None
    except zipfile.BadZipFile:
        raise
    # A broken file raises many other errors too: an unknown ZIP version,
    # compression or encryption, or a decompressor's own error
    except Exception as err:
        raise ValueError(
            f"The feed's ZIP file cannot be read: {str(err) or type(err).__name__}"
        ) from err
    if first is None:
        raise ValueError("The feed's ZIP file is empty.")
    return first


def parse_csv(text: str) -> list[Row]:
    """Read a feed's CSV text: one row per line, by the header's column names.

    Fields are separated by semicolons and padded with spaces; lines starting
    with "/" are comments. A column's values all get the same type.
    """
    reader = csv.reader(
        (line for line in text.splitlines() if not line.startswith("/")),
        delimiter=";",
        skipinitialspace=True,
    )
    try:
        records = [record for record in reader if record]
    except csv.Error as err:
        raise ValueError(f"The feed is not valid CSV: {err}") from err
    if not records:
        raise ValueError("The feed is empty.")
    header, *records = records
    if any(len(record) > len(header) for record in records):
        raise ValueError("The feed has a line with more fields than its header.")
    columns = [
        _typed([record[i].strip() if i < len(record) else "" for record in records])
        for i in range(len(header))
    ]
    return [dict(zip(header, values)) for values in zip(*columns)]


def _typed(texts: list[str]) -> list:
    """Turn a column's texts into whole numbers, numbers or texts.

    None stands for a blank; a column of numbers with blanks has no whole numbers.
    """
    present = [text for text in texts if text not in BLANKS]
    kinds = (int, float) if len(present) == len(texts) else (float,)
    for kind in kinds:
        try:
            numbers = iter([kind(text) for text in present])
        except ValueError:
            continue
        return [None if text in BLANKS else next(numbers) for text in texts]
    return [None if text in BLANKS else text for text in texts]


def to_number(value) -> int | float | None:
    """Return a value as a number, or None if it is not one."""
    if isinstance(value, str):
        for kind in (int, float):
            try:
                value = kind(value)
                break
            except ValueError:
                continue
    if not isinstance(value, (int, float)) or math.isnan(value):
        return None
    return value


def clean_data(rows: list[Row]) -> list[Row]:
    """Turn the feed's "no data" values into None and trim column and station names."""
    cleaned = []
    for row in rows:
        clean = {
            name.strip(): None if value in NO_DATA else value
            for name, value in row.items()
        }
        if isinstance(clean.get("StationName"), str):
            clean["StationName"] = clean["StationName"].strip()
        cleaned.append(clean)
    return cleaned


def pick_columns(rows: list[Row], columns: list[str]) -> list[Row]:
    """Keep these columns, in this order; None where a row does not have one."""
    return [{column: row.get(column) for column in columns} for row in rows]


def add_distance_column(rows: list[Row], ref_lat: float, ref_lon: float) -> list[Row]:
    """Add each station's distance from the reference point, in kilometres.

    The distance is None for a station without usable coordinates.
    """
    result = []
    for row in rows:
        lat, lon = to_number(row.get("Latitude")), to_number(row.get("Longitude"))
        distance = (
            None
            if lat is None or lon is None
            else haversine(lat, lon, ref_lat, ref_lon)
        )
        result.append(
            {**row, "Latitude": lat, "Longitude": lon, "Distance_km": distance}
        )
    return result


def latest_observations(rows: list[Row]) -> list[Row]:
    """Keep only rows from the file's newest observation time.

    HungaroMet's files also repeat earlier readings that changed in the last 24
    hours, so a station can appear more than once with older times.
    """
    latest = max(
        (row["Time"] for row in rows if row.get("Time") is not None), default=None
    )
    return [row for row in rows if latest is not None and row.get("Time") == latest]


# The columns that identify and place a station, not measurements
NOT_MEASUREMENTS = {
    "Time", "StationNumber", "StationName", "Latitude", "Longitude", "Elevation",
    "Distance_km",
}


def select_stations(
    rows: list[Row], ref_lat: float, ref_lon: float, distance_km: float
) -> list[Row]:
    """Keep the stations with data within distance_km, or the nearest one with
    data if none are in range; the nearest one comes first.

    A station whose readings are all missing (-999) is not available, so it
    is left out like a station that is not in the file.
    """
    placed = sorted(
        (
            row
            for row in add_distance_column(rows, ref_lat, ref_lon)
            if row["Distance_km"] is not None
        ),
        key=lambda row: row["Distance_km"],
    )
    if not placed:
        raise ValueError("No HungaroMet station has valid coordinates.")
    measured = [column for column in placed[0] if column not in NOT_MEASUREMENTS]
    if measured:
        placed = [
            row for row in placed if any(row.get(c) is not None for c in measured)
        ]
        if not placed:
            raise ValueError("No HungaroMet station has data.")
    in_range = [row for row in placed if row["Distance_km"] <= distance_km]
    if in_range:
        return in_range
    nearest = placed[0]["StationNumber"]
    return [row for row in placed if row["StationNumber"] == nearest]


def station_records(rows: list[Row]) -> list[Row]:
    """Return each selected station's full row, nearest first, with JSON-safe values."""
    records: dict[tuple, Row] = {}
    for row in rows:
        record = {
            name: round(value, 10) if isinstance(value, float) else value
            for name, value in row.items()
        }
        records.setdefault(tuple(record.items()), record)
    return list(records.values())


def circular_mean(degrees) -> float:
    """Average compass directions so that, for example, 350° and 10° give 0°."""
    radians = [math.radians(value) for value in degrees]
    angle = math.atan2(
        sum(math.sin(r) for r in radians), sum(math.cos(r) for r in radians)
    )
    # Rounding first keeps readings around north at 0° instead of 359.999...
    return round(math.degrees(angle), 6) % 360


def calculate_mean_values(rows: list[Row], numeric_columns: list) -> dict:
    """Average each column over the selected stations; None when no station has it."""
    means = {}
    for col in numeric_columns:
        if not any(col in row for row in rows):
            continue
        values = [
            number for row in rows if (number := to_number(row.get(col))) is not None
        ]
        if not values:
            means[col] = None
        elif col in DIRECTION_COLUMNS:
            means[col] = circular_mean(values)
        else:
            means[col] = statistics.fmean(values)
    return means


def nearest_value(rows: list[Row], column: str):
    """Return the nearest station's value of a column that cannot be averaged."""
    return next(
        (str(row[column]).strip() for row in rows if row.get(column) is not None), None
    )


def most_common(values: list):
    """Return the most frequent value, the first of them on a tie; None if empty."""
    counts = Counter(values)
    return counts.most_common(1)[0][0] if counts else None


def prevailing_direction(rows: list[Row]):
    """Return the most common prevailing wind direction; the nearest wins ties."""
    return most_common([
        direction
        for row in rows
        if (direction := WIND_DIRECTIONS.get(str(row.get("fud")).strip()))
    ])


def ten_minute_gust_time(rows: list[Row]):
    """Return the nearest station's maximum gust time as local HH:MM:SS.

    The 10-minute file gives the gust's minute of the hour and second, within the
    10 minutes that end at the observation time (UTC). A gust in the period's
    last minute carries the end minute itself, for example 50 for 12:40-12:50.
    """
    for row in rows:
        minute, second = to_number(row.get("fxm")), to_number(row.get("fxs"))
        if minute is not None and second is not None:
            break
    else:
        return None
    period_end = datetime.strptime(str(int(row["Time"])), "%Y%m%d%H%M").replace(
        tzinfo=dt_util.UTC
    )
    gust = period_end.replace(minute=int(minute) % 60, second=int(second))
    # Minutes 50-59 in the period ending on the hour belong to the previous hour
    if gust > period_end + timedelta(minutes=1):
        gust -= timedelta(hours=1)
    return dt_util.as_local(gust).strftime("%H:%M:%S")


def haversine(lat1, lon1, lat2, lon2):
    """Return the great-circle distance of two points in kilometres."""
    earth_radius_km = 6371
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = (
        math.sin(dphi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    )
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return earth_radius_km * c


def process_daily_data(hass=None, distance_km=DEFAULT_DISTANCE_KM, location=None):
    """Return the daily feed's averages over the nearby stations, and the stations."""
    rows = clean_data(fetch_data(URL_DAILY))
    columns = [
        "Time",
        "StationNumber",
        "StationName",
        "Latitude",
        "Longitude",
        "Elevation",
        "rau",
        "upe",
        "t",
        "tn",
        "tx",
        "sr",
        "et5",
        "et10",
        "et20",
        "et50",
        "et100",
        "tsn24",
        "v",
        "p",
        "p0",
        "u",
        "sg",
        "suv",
        "fs",
        "f",
        "fud",
        "fx",
        "fxd",
        "fxdat",
        "tviz",
    ]
    rows = latest_observations(pick_columns(rows, columns))

    ref_lat, ref_lon = _get_reference_coords(hass, location)

    rows = select_stations(rows, ref_lat, ref_lon, distance_km)

    numeric_columns = [
        "Latitude",
        "Longitude",
        "Elevation",
        "rau",
        "upe",
        "t",
        "tn",
        "tx",
        "sr",
        "et5",
        "et10",
        "et20",
        "et50",
        "et100",
        "tsn24",
        "v",
        "p",
        "p0",
        "u",
        "sg",
        "suv",
        "fs",
        "f",
        "fx",
        "fxd",
        "tviz",
    ]
    means = calculate_mean_values(rows, numeric_columns)

    date_str = str(rows[0]["Time"])
    date_iso = datetime.strptime(date_str, "%Y%m%d").date().isoformat()
    station_info_list = station_records(rows)
    result = {
        "time": date_iso,
        **{f"average_{col}": means[col] for col in numeric_columns},
        "fud": prevailing_direction(rows),
        "fxdat": nearest_value(rows, "fxdat"),
    }
    return result, station_info_list


def process_hourly_data(hass=None, distance_km=DEFAULT_DISTANCE_KM, location=None):
    """Return the hourly feed's averages over the nearby stations, and the stations."""
    rows = clean_data(fetch_data(URL_HOURLY))
    columns = [
        "Time",
        "StationNumber",
        "StationName",
        "Latitude",
        "Longitude",
        "Elevation",
        "r",
        "t",
        "ta",
        "tn",
        "tx",
        "u",
        "sg",
        "sr",
        "suv",
        "fs",
        "fsd",
        "fx",
        "fxd",
        "f",
        "fd",
        "we",
        "et5",
        "et10",
        "et20",
        "et50",
        "et100",
        "tsn",
        "tviz",
        "v",
        "p",
        "p0",
        "fxdat",
    ]
    rows = latest_observations(pick_columns(rows, columns))

    ref_lat, ref_lon = _get_reference_coords(hass, location)

    rows = select_stations(rows, ref_lat, ref_lon, distance_km)

    numeric_columns = [
        "Latitude",
        "Longitude",
        "Elevation",
        "r",
        "t",
        "ta",
        "tn",
        "tx",
        "u",
        "sg",
        "sr",
        "suv",
        "fs",
        "fsd",
        "fx",
        "fxd",
        "f",
        "fd",
        "et5",
        "et10",
        "et20",
        "et50",
        "et100",
        "tsn",
        "tviz",
        "we",
        "v",
        "p",
        "p0",
    ]
    means = calculate_mean_values(rows, numeric_columns)

    date_str = str(rows[0]["Time"])
    dt_utc = datetime.strptime(date_str, "%Y%m%d%H%M").replace(tzinfo=dt_util.UTC)
    station_info_list = station_records(rows)
    we_value = most_common([row["we"] for row in rows if row["we"] is not None])
    result = {
        "time": dt_utc.isoformat(),
        **{f"average_{col}": means[col] for col in numeric_columns},
        "we": we_value,
        "fxdat": nearest_value(rows, "fxdat"),
    }
    return result, station_info_list


def process_ten_minutes_data(hass=None, distance_km=DEFAULT_DISTANCE_KM, location=None):
    """Return the 10-minute feed's averages over the nearby stations, and the stations."""
    rows = clean_data(fetch_data(URL_TEN_MINUTES))
    columns = [
        "Time",
        "StationNumber",
        "StationName",
        "Latitude",
        "Longitude",
        "Elevation",
        "r",
        "t",
        "ta",
        "tn",
        "tx",
        "u",
        "sg",
        "sr",
        "suv",
        "fs",
        "fsd",
        "fx",
        "fxd",
        "et5",
        "et10",
        "et20",
        "et50",
        "et100",
        "tsn",
        "tviz",
        "v",
        "p",
        "fxm",
        "fxs",
    ]
    rows = latest_observations(pick_columns(rows, columns))

    ref_lat, ref_lon = _get_reference_coords(hass, location)

    rows = select_stations(rows, ref_lat, ref_lon, distance_km)

    numeric_columns = [
        "Latitude",
        "Longitude",
        "Elevation",
        "r",
        "t",
        "ta",
        "tn",
        "tx",
        "u",
        "sg",
        "sr",
        "suv",
        "fs",
        "fsd",
        "fx",
        "fxd",
        "et5",
        "et10",
        "et20",
        "et50",
        "et100",
        "tsn",
        "tviz",
        "v",
        "p",
    ]
    means = calculate_mean_values(rows, numeric_columns)

    date_str = str(rows[0]["Time"])
    dt_utc = datetime.strptime(date_str, "%Y%m%d%H%M").replace(tzinfo=dt_util.UTC)
    station_info_list = station_records(rows)

    result = {
        "time": dt_utc.isoformat(),
        **{f"average_{col}": means[col] for col in numeric_columns},
        "fxdat": ten_minute_gust_time(rows),
    }
    return result, station_info_list


if __name__ == "__main__":  # pragma: no cover
    # daily_data, daily_stations = process_daily_data()
    # print("Daily Data:", daily_data)
    # print("Daily Stations:", daily_stations)

    # hourly_data, hourly_stations = process_hourly_data()
    # print("Hourly Data:", hourly_data)
    # print("Hourly Stations:", hourly_stations)

    ten_minutes_data, ten_minutes_stations = process_ten_minutes_data()
    for key, value in ten_minutes_data.items():
        print(f"{key}: {value}")
    print(f"10 perces szélsebesség: {ten_minutes_data.get('average_fs')}")
    # print("10 Minutes Stations:", ten_minutes_stations)
