"""Tests for the translation files."""

import json
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
from PIL import Image

from custom_components.hungaromet_weather_and_forecast.forecast_scraper import (
    WIND_STRENGTH_STATES,
)
from custom_components.hungaromet_weather_and_forecast.sensor import (
    FORECAST_SENSORS,
    WARNING_SENSORS,
    StationSensor,
    StationsSensor,
)
from custom_components.hungaromet_weather_and_forecast.stations import (
    MEASUREMENTS as STATION_MEASUREMENTS,
    Measurement,
)
from custom_components.hungaromet_weather_and_forecast.warning_scraper import (
    LEVEL_STATES,
)
from custom_components.hungaromet_weather_and_forecast.weather_data import (
    process_daily_data,
    process_hourly_data,
    process_ten_minutes_data,
)

from .entries import feed_coordinator
from .rows import table

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


def _all_stations_rows(time):
    return table({
        "Time": [time],
        "StationNumber": [1],
        "StationName": ["A"],
        "Latitude": [47.5],
        "Longitude": [19.0],
        "Elevation": [100],
        **{col: [1.0] for col in MEASUREMENTS},
    })


@pytest.mark.parametrize("language", LANGUAGES)
def test_every_wind_strength_is_translated(language):
    """Test every wind strength is translated."""
    states = _load(language)["entity"]["sensor"]["forecast_wind_strength"]["state"]
    assert list(states) == WIND_STRENGTH_STATES


@pytest.mark.parametrize("language", LANGUAGES)
@pytest.mark.parametrize("description", WARNING_SENSORS, ids=lambda d: d.key)
def test_every_warning_level_is_translated(language, description):
    """Test every warning level is translated."""
    sensor = _load(language)["entity"]["sensor"][description.translation_key]
    assert list(sensor["state"]) == LEVEL_STATES
    assert list(sensor["state_attributes"]) == [description.key]


def test_only_created_sensors_are_translated():
    """No sensor name is left over from a sensor that is no longer created."""
    created = {"stations"} | {
        d.translation_key for d in (*FORECAST_SENSORS, *WARNING_SENSORS)
    } | {
        StationSensor(feed_coordinator(feed), m).translation_key
        for feed, measurements in STATION_MEASUREMENTS.items()
        for m in measurements
    }
    assert set(_load("en")["entity"]["sensor"]) == created


def test_languages_have_the_same_keys():
    """Every language translates the same strings."""
    assert _key_paths(_load("hu")) == _key_paths(_load("en"))


@pytest.mark.parametrize("language", LANGUAGES)
@pytest.mark.parametrize(
    ("process", "feed"),
    [
        (process_daily_data, "daily"),
        (process_hourly_data, "hourly"),
        (process_ten_minutes_data, "ten_minutes"),
    ],
)
def test_every_sensor_key_is_translated(language, process, feed):
    """Each value a feed's sensors show has a translated sensor name."""
    rows = _all_stations_rows(
        20240101 if process is process_daily_data else 202401011200
    )
    hass = Mock()
    hass.config.latitude = 47.5
    hass.config.longitude = 19.0
    with patch(
        "custom_components.hungaromet_weather_and_forecast.weather_data.fetch_data",
        return_value=rows,
    ):
        data, _ = process(hass, distance_km=20)

    names = _load(language)["entity"]["sensor"]
    device = _load(language)["device"]
    for key in data.keys() & {m.key for m in STATION_MEASUREMENTS[feed]}:
        sensor = StationSensor(feed_coordinator(feed), Measurement(key, key))
        assert sensor.translation_key in names, sensor.translation_key
        assert sensor.device_info["translation_key"] in device


@pytest.mark.parametrize("language", LANGUAGES)
def test_the_update_actions_error_is_translated(language):
    """The error of an update with nothing loaded has a message."""
    assert _load(language)["exceptions"]["no_loaded_entry"]["message"]


@pytest.mark.parametrize("language", LANGUAGES)
def test_station_info_sensor_is_translated(language):
    """The station list sensor and its device are translated."""
    sensor = StationsSensor(feed_coordinator("daily"))
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
