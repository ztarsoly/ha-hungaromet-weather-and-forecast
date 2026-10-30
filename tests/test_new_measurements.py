"""Tests for pressure, visibility, wind direction and gust time handling."""

from types import SimpleNamespace
from unittest.mock import Mock, patch

import pandas as pd
import pytest
from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.util import dt as dt_util

from custom_components.hungaromet_weather_and_forecast.station_sensor import FEEDS, Measurement, StationQuery, StationSensor
from custom_components.hungaromet_weather_and_forecast.weather_data import (
    WIND_DIRECTIONS,
    calculate_mean_values,
    circular_mean,
    clean_data,
    nearest_value,
    prevailing_direction,
    process_daily_data,
    process_ten_minutes_data,
    ten_minute_gust_time,
)


def _hass():
    hass = Mock()
    hass.config.latitude = 47.5
    hass.config.longitude = 19.0
    return hass


def test_circular_mean_wraps_around_north():
    assert circular_mean([350, 10]) == pytest.approx(0, abs=1e-9)
    assert circular_mean([80, 100]) == pytest.approx(90)


def test_direction_columns_use_circular_mean():
    df = pd.DataFrame({"fxd": [355, 5], "fx": [355, 5]})

    means = calculate_mean_values(df, ["fxd", "fx", "missing"])

    assert means["fxd"] == pytest.approx(0, abs=1e-9)
    assert means["fx"] == 180
    assert "missing" not in means


def test_clean_data_treats_text_minus_999_as_missing():
    df = pd.DataFrame({"fud": ["DDNy", "-999"], "t": [1.0, -999]})

    result = clean_data(df)

    assert result["fud"].isna().tolist() == [False, True]
    assert result["t"].isna().tolist() == [False, True]


def test_prevailing_direction_takes_most_common_nearest_first_on_ties():
    df = pd.DataFrame({"fud": ["Ny", "DDNy", "DDNy", "Ny", "É", None, "?"]})
    assert prevailing_direction(df) == "w"

    df = pd.DataFrame({"fud": ["ÉÉK", "DDNy", "DDNy"]})
    assert prevailing_direction(df) == "ssw"

    assert prevailing_direction(pd.DataFrame({"fud": [None, "?"]})) is None


def test_every_hungarian_compass_point_has_an_english_key():
    assert len(WIND_DIRECTIONS) == 16
    assert len(set(WIND_DIRECTIONS.values())) == 16


def test_nearest_value_skips_missing():
    df = pd.DataFrame({"fxdat": [None, " 12:06", "23:50"]})

    assert nearest_value(df, "fxdat") == "12:06"
    assert nearest_value(pd.DataFrame({"fxdat": [None]}), "fxdat") is None


def test_ten_minute_gust_time_is_local(monkeypatch):
    monkeypatch.setattr(
        dt_util, "DEFAULT_TIME_ZONE", dt_util.get_time_zone("Europe/Budapest")
    )
    df = pd.DataFrame({
        "Time": [202610270920, 202610270920],
        "fxm": [None, 14],
        "fxs": [None, 58],
    })

    # Period 09:10-09:20 UTC, gust at minute 14, second 58 -> 09:14:58 UTC
    assert ten_minute_gust_time(df) == "10:14:58"
    assert ten_minute_gust_time(df.iloc[:1]) is None


@pytest.mark.parametrize(
    ("time", "fxm", "fxs", "expected"),
    [
        # A gust in the last minute carries the end minute itself
        (202610271250, 50, 0, "13:50:00"),
        (202610271250, 40, 1, "13:40:01"),
        # The period ending on the hour reports minutes 50-59 and 0
        (202610271300, 55, 30, "13:55:30"),
        (202610271300, 0, 0, "14:00:00"),
    ],
)
def test_ten_minute_gust_time_at_period_edges(monkeypatch, time, fxm, fxs, expected):
    monkeypatch.setattr(
        dt_util, "DEFAULT_TIME_ZONE", dt_util.get_time_zone("Europe/Budapest")
    )
    df = pd.DataFrame({"Time": [time], "fxm": [fxm], "fxs": [fxs]})

    assert ten_minute_gust_time(df) == expected


@patch("custom_components.hungaromet_weather_and_forecast.weather_data.fetch_data")
def test_daily_feed_returns_new_measurements(mock_fetch):
    mock_fetch.return_value = pd.DataFrame({
        "Time": [20261025, 20261025],
        "StationNumber": [1, 2],
        "StationName": ["Near", "Far"],
        "Latitude": [47.51, 47.6],
        "Longitude": [19.0, 19.0],
        "Elevation": [100, 110],
        "p0": [1014.0, 1016.0],
        "v": [15000, -999],
        "fud": ["DDNy", "-999"],
        "fxdat": ["-999", "13:27"],
        "fxd": [350, 10],
    })

    data, rows = process_daily_data(_hass(), distance_km=50)

    assert data["average_p0"] == 1015.0
    assert data["average_v"] == 15000
    assert data["average_fxd"] == pytest.approx(0, abs=1e-9)
    assert data["average_t"] is None
    assert data["fud"] == "ssw"
    assert data["fxdat"] == "13:27"
    assert rows[0]["p0"] == 1014.0
    assert rows[0]["fud"] == "DDNy"


@patch("custom_components.hungaromet_weather_and_forecast.weather_data.fetch_data")
def test_ten_minute_feed_keeps_gust_columns_in_raw_data(mock_fetch):
    mock_fetch.return_value = pd.DataFrame({
        "Time": [202610270920],
        "StationNumber": [1],
        "StationName": ["Near"],
        "Latitude": [47.51],
        "Longitude": [19.0],
        "Elevation": [100],
        "p": [1000.5],
        "fxm": [4],
        "fxs": [58],
    })

    data, rows = process_ten_minutes_data(_hass(), distance_km=50)

    assert data["average_p"] == 1000.5
    assert data["fxdat"] is not None
    assert rows[0]["fxm"] == 4
    assert rows[0]["fxs"] == 58


def test_prevailing_direction_sensor_is_a_translated_enum():
    sensor = StationSensor(
        SimpleNamespace(), FEEDS["daily"], Measurement("U", "fud"), "ssw", StationQuery()
    )

    assert sensor.device_class == SensorDeviceClass.ENUM
    assert sensor.options == list(WIND_DIRECTIONS.values())
    assert sensor.translation_key == "daily_fud"
    assert sensor.native_value == "ssw"


def test_other_daily_sensors_are_not_enums():
    sensor = StationSensor(
        SimpleNamespace(), FEEDS["daily"], Measurement("P", "p0", "hPa"), 1014.0, StationQuery()
    )

    assert sensor.device_class == SensorDeviceClass.ATMOSPHERIC_PRESSURE
