import io
import json
import logging
import math
import zipfile
from datetime import datetime, timedelta

import pandas as pd
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

try:  # Optional local fallback for CLI usage
    from . import local_config as _local_config  # type: ignore
except ImportError:  # pragma: no cover - optional helper
    try:
        import local_config as _local_config  # type: ignore
    except ImportError:  # pragma: no cover - optional helper
        _local_config = None

from homeassistant.util import dt as dt_util

_LOGGER = logging.getLogger(__name__)
REQUEST_TIMEOUT = 15


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


def fetch_data(url: str) -> pd.DataFrame:
    response = requests.get(url, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(response.content)) as z:
        csv_filename = z.namelist()[0]
        with z.open(csv_filename) as csvfile:
            df = pd.read_csv(csvfile, sep=";", comment="/", skipinitialspace=True)
    return df


def clean_data(df: pd.DataFrame) -> pd.DataFrame:
    # Numbers and text columns (wind direction, gust time) both use -999 for no data
    df.replace([-999, "-999"], pd.NA, inplace=True)
    df.columns = df.columns.str.strip()
    if "StationName" in df.columns:
        df["StationName"] = df["StationName"].str.strip()
    return df


def add_distance_column(
    df: pd.DataFrame, ref_lat: float, ref_lon: float
) -> pd.DataFrame:
    df["Latitude"] = pd.to_numeric(df["Latitude"], errors="coerce")
    df["Longitude"] = pd.to_numeric(df["Longitude"], errors="coerce")
    df["Distance_km"] = df.apply(
        lambda row: haversine(row["Latitude"], row["Longitude"], ref_lat, ref_lon),
        axis=1,
    )
    return df


def latest_observations(df: pd.DataFrame) -> pd.DataFrame:
    """Keep only rows from the file's newest observation time.

    HungaroMet's files also repeat earlier readings that changed in the last 24
    hours, so a station can appear more than once with older times.
    """
    return df[df["Time"] == df["Time"].max()]


def select_stations(
    df: pd.DataFrame, ref_lat: float, ref_lon: float, distance_km: float
) -> pd.DataFrame:
    """Keep stations within distance_km, or the nearest one if none are in range."""
    df = add_distance_column(df, ref_lat, ref_lon)
    df = df.dropna(subset=["Distance_km"]).sort_values(by="Distance_km")
    if df.empty:
        raise ValueError("No HungaroMet station has valid coordinates.")
    in_range = df[df["Distance_km"] <= distance_km]
    if not in_range.empty:
        return in_range
    nearest = df.iloc[0]
    _LOGGER.warning(
        "No HungaroMet station within %s km; using nearest station %s (%.1f km)",
        distance_km,
        nearest["StationName"],
        nearest["Distance_km"],
    )
    return df[df["StationNumber"] == nearest["StationNumber"]]


def station_records(df: pd.DataFrame) -> list[dict]:
    """Return each selected station's full row, nearest first, with JSON-safe values."""
    return json.loads(df.drop_duplicates().to_json(orient="records"))


def circular_mean(degrees) -> float:
    """Average compass directions so that, for example, 350° and 10° give 0°."""
    radians = [math.radians(value) for value in degrees]
    angle = math.atan2(
        sum(math.sin(r) for r in radians), sum(math.cos(r) for r in radians)
    )
    # Rounding first keeps readings around north at 0° instead of 359.999...
    return round(math.degrees(angle), 6) % 360


def calculate_mean_values(df: pd.DataFrame, numeric_columns: list) -> dict:
    means = {}
    for col in numeric_columns:
        if col not in df.columns:
            continue
        values = pd.to_numeric(df[col], errors="coerce").dropna()
        if values.empty:
            means[col] = None
        elif col in DIRECTION_COLUMNS:
            means[col] = circular_mean(values)
        else:
            means[col] = values.mean()
    return means


def nearest_value(df: pd.DataFrame, column: str):
    """Return the nearest station's value of a column that cannot be averaged."""
    values = df[column].dropna()
    return str(values.iloc[0]).strip() if not values.empty else None


def prevailing_direction(df: pd.DataFrame):
    """Return the most common prevailing wind direction; the nearest wins ties."""
    directions = df["fud"].dropna().map(lambda v: WIND_DIRECTIONS.get(str(v).strip()))
    directions = directions.dropna()
    if directions.empty:
        return None
    counts = directions.value_counts()
    most_common = set(counts[counts == counts.max()].index)
    return next(d for d in directions if d in most_common)


def ten_minute_gust_time(df: pd.DataFrame):
    """Return the nearest station's maximum gust time as local HH:MM:SS.

    The 10-minute file gives the gust's minute of the hour and second, within the
    10 minutes that end at the observation time (UTC). A gust in the period's
    last minute carries the end minute itself, for example 50 for 12:40-12:50.
    """
    gusts = df.assign(
        fxm=pd.to_numeric(df["fxm"], errors="coerce"),
        fxs=pd.to_numeric(df["fxs"], errors="coerce"),
    ).dropna(subset=["fxm", "fxs"])
    if gusts.empty:
        return None
    row = gusts.iloc[0]
    # A row of mixed columns turns Time into a float, so convert it back first
    period_end = datetime.strptime(str(int(row["Time"])), "%Y%m%d%H%M").replace(
        tzinfo=dt_util.UTC
    )
    gust = period_end.replace(minute=int(row["fxm"]) % 60, second=int(row["fxs"]))
    # Minutes 50-59 in the period ending on the hour belong to the previous hour
    if gust > period_end + timedelta(minutes=1):
        gust -= timedelta(hours=1)
    return dt_util.as_local(gust).strftime("%H:%M:%S")


# Haversine function to calculate distance
def haversine(lat1, lon1, lat2, lon2):
    R = 6371
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = (
        math.sin(dphi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    )
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c


def process_daily_data(hass=None, distance_km=DEFAULT_DISTANCE_KM, location=None):
    df = fetch_data(URL_DAILY)
    df = clean_data(df)
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
    df = df.reindex(columns=columns)
    df = latest_observations(df)

    ref_lat, ref_lon = _get_reference_coords(hass, location)

    df = select_stations(df, ref_lat, ref_lon, distance_km)

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
    means = calculate_mean_values(df, numeric_columns)
    means["water_balance"] = (
        means["rau"] - means["upe"]
        if means["rau"] is not None and means["upe"] is not None
        else None
    )
    means["sr_mj"] = means["sr"] * 0.01 if means["sr"] is not None else None
    numeric_columns.append("water_balance")
    numeric_columns.append("sr_mj")

    date_iso = datetime.strptime(str(df["Time"].iloc[0]), "%Y%m%d").date().isoformat()
    station_info_list = station_records(df)
    result = {
        "time": date_iso,
        **{f"average_{col}": means[col] for col in numeric_columns},
        "fud": prevailing_direction(df),
        "fxdat": nearest_value(df, "fxdat"),
    }
    return result, station_info_list


def process_hourly_data(hass=None, distance_km=DEFAULT_DISTANCE_KM, location=None):
    df = fetch_data(URL_HOURLY)
    df = clean_data(df)
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
    df = df.reindex(columns=columns)
    df = latest_observations(df)

    ref_lat, ref_lon = _get_reference_coords(hass, location)

    df = select_stations(df, ref_lat, ref_lon, distance_km)

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
    means = calculate_mean_values(df, numeric_columns)
    means["sr_mj"] = means["sr"] * 0.01 if means["sr"] is not None else None
    numeric_columns.append("sr_mj")

    date_str = str(df["Time"].head(1).iloc[0])
    dt_utc = datetime.strptime(date_str, "%Y%m%d%H%M").replace(tzinfo=dt_util.UTC)
    station_info_list = station_records(df)
    we_value = None
    if not df.empty and "we" in df.columns:
        we_counts = df["we"].value_counts(dropna=True)
        if not we_counts.empty:
            we_value = we_counts.idxmax()
    result = {
        "time": dt_utc.isoformat(),
        **{f"average_{col}": means[col] for col in numeric_columns},
        "we": we_value,
        "fxdat": nearest_value(df, "fxdat"),
    }
    return result, station_info_list


def process_ten_minutes_data(hass=None, distance_km=DEFAULT_DISTANCE_KM, location=None):
    df = fetch_data(URL_TEN_MINUTES)
    df = clean_data(df)
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
    df = df.reindex(columns=columns)
    df = latest_observations(df)

    ref_lat, ref_lon = _get_reference_coords(hass, location)

    df = select_stations(df, ref_lat, ref_lon, distance_km)

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
    means = calculate_mean_values(df, numeric_columns)
    means["sr_mj"] = means["sr"] * 0.01 if means["sr"] is not None else None
    numeric_columns.append("sr_mj")

    date_str = str(df["Time"].head(1).iloc[0])
    dt_utc = datetime.strptime(date_str, "%Y%m%d%H%M").replace(tzinfo=dt_util.UTC)
    station_info_list = station_records(df)

    result = {
        "time": dt_utc.isoformat(),
        **{f"average_{col}": means[col] for col in numeric_columns},
        "fxdat": ten_minute_gust_time(df),
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
