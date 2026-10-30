"""Tests for the translation files."""

import json
from pathlib import Path
from unittest.mock import Mock, patch

import pandas as pd
import pytest
from PIL import Image

from custom_components.hungaromet_weather_and_forecast.daily_sensor import HungarometWeatherDailySensor
from custom_components.hungaromet_weather_and_forecast.hourly_sensor import HungarometWeatherHourlySensor
from custom_components.hungaromet_weather_and_forecast.station_info_sensor import HungarometStationInfoSensor
from custom_components.hungaromet_weather_and_forecast.ten_minute_sensor import (
    HungarometWeatherTenMinutesSensor,
)
from custom_components.hungaromet_weather_and_forecast.weather_data import (
    process_daily_data,
    process_hourly_data,
    process_ten_minutes_data,
)

TRANSLATIONS = Path(__file__).resolve().parents[1] / (
    "custom_components/hungaromet_weather_and_forecast/translations"
)
LANGUAGES = ["en", "hu"]

MEASUREMENTS = [
    "rau", "upe", "t", "tn", "tx", "sr", "et5", "et10", "et20", "et50",
    "et100", "tsn24", "r", "ta", "u", "sg", "suv", "fs", "fsd", "fx", "fxd",
    "f", "fd", "we", "tsn", "tviz",
]


def _load(language):
    return json.loads((TRANSLATIONS / f"{language}.json").read_text("utf-8"))


def _key_paths(data, prefix=""):
    if not isinstance(data, dict):
        return {prefix}
    paths = set()
    for key, value in data.items():
        paths |= _key_paths(value, f"{prefix}.{key}" if prefix else key)
    return paths


def _all_stations_df():
    return pd.DataFrame({
        "Time": [202401011200],
        "StationNumber": [1],
        "StationName": ["A"],
        "Latitude": [47.5],
        "Longitude": [19.0],
        "Elevation": [100],
        **{col: [1.0] for col in MEASUREMENTS},
    })


def test_languages_have_the_same_keys():
    """Every language translates the same strings."""
    assert _key_paths(_load("hu")) == _key_paths(_load("en"))


@pytest.mark.parametrize("language", LANGUAGES)
@pytest.mark.parametrize(
    ("process", "sensor_cls"),
    [
        (process_daily_data, HungarometWeatherDailySensor),
        (process_hourly_data, HungarometWeatherHourlySensor),
        (process_ten_minutes_data, HungarometWeatherTenMinutesSensor),
    ],
)
def test_every_sensor_key_is_translated(language, process, sensor_cls):
    """Each value a feed can produce has a translated sensor name."""
    df = _all_stations_df()
    if process is process_daily_data:
        df["Time"] = [20240101]
    hass = Mock()
    hass.config.latitude = 47.5
    hass.config.longitude = 19.0
    with patch(
        "custom_components.hungaromet_weather_and_forecast.weather_data.fetch_data", return_value=df
    ):
        data, _ = process(hass, distance_km=20)

    names = _load(language)["entity"]["sensor"]
    device = _load(language)["device"]
    for key in data:
        sensor = sensor_cls(hass, key, None, None, key)
        assert sensor.translation_key in names, sensor.translation_key
        assert sensor.device_info["translation_key"] in device


@pytest.mark.parametrize("language", LANGUAGES)
def test_station_info_sensor_is_translated(language):
    """The station list sensor and its device are translated."""
    sensor = HungarometStationInfoSensor(Mock(), "Stations", [], "daily")
    translations = _load(language)

    assert sensor.translation_key in translations["entity"]["sensor"]
    assert sensor.device_info["translation_key"] in translations["device"]


def _load_icons():
    return json.loads((TRANSLATIONS.parent / "icons.json").read_text("utf-8"))


def test_every_translated_sensor_has_an_icon():
    """Icons cover exactly the sensors that have translated names."""
    icons = _load_icons()["entity"]["sensor"]

    assert set(icons) == set(_load("en")["entity"]["sensor"])
    assert all(icon["default"].startswith("mdi:") for icon in icons.values())


def test_update_service_has_an_icon():
    """The update service has an icon."""
    assert _load_icons()["services"]["update"]["service"] == "mdi:refresh"


@pytest.mark.parametrize(("image", "size"), [("icon.png", 256), ("icon@2x.png", 512)])
def test_brand_icons_are_square_pngs(image, size):
    """Brand icons follow Home Assistant's required sizes."""
    with Image.open(TRANSLATIONS.parent / "brand" / image) as icon:
        assert icon.format == "PNG"
        assert icon.size == (size, size)
