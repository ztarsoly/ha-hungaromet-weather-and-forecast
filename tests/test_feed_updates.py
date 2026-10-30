"""Tests for the scheduled feed updates in sensor.py."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.hungaromet_weather_and_forecast.national_radar import HungarometRadarImage
from custom_components.hungaromet_weather_and_forecast.sensor import _async_update_feed
from custom_components.hungaromet_weather_and_forecast.station_sensor import (
    FEEDS,
    Measurement,
    StationQuery,
    StationSensor,
    StationsSensor,
)
from custom_components.hungaromet_weather_and_forecast.weather_data import process_hourly_data

QUERY = StationQuery(distance_km=35, location=(47.5, 21.6))


def _value(hass, feed, key, value, unit="°C"):
    return StationSensor(hass, FEEDS[feed], Measurement(key, key, unit), value, QUERY)


def _stations(hass, feed):
    return StationsSensor(hass, FEEDS[feed], [], QUERY)


def _added(sensor):
    sensor._added = True
    sensor.async_write_ha_state = MagicMock()
    return sensor


def _hass(result):
    return SimpleNamespace(async_add_executor_job=AsyncMock(return_value=result))


@pytest.mark.asyncio
async def test_feed_update_downloads_once_and_updates_its_sensors():
    rows = [{"StationName": "Near"}]
    hass = _hass(({"average_t": 4.2, "average_r": 0.0}, rows))
    temperature = _added(_value(hass, "hourly", "t", 1.0))
    rain = _added(_value(hass, "hourly", "r", 5.0, "mm"))
    stations = _added(_stations(hass, "hourly"))
    daily = _added(_value(hass, "daily", "t", 9.0))
    daily_stations = _added(_stations(hass, "daily"))

    await _async_update_feed(
        hass, [temperature, rain, stations, daily, daily_stations], "hourly", QUERY
    )

    hass.async_add_executor_job.assert_awaited_once_with(
        process_hourly_data, hass, 35, (47.5, 21.6)
    )
    assert temperature.native_value == 4.2
    assert rain.native_value == 0.0
    assert stations.native_value == 1
    assert stations.extra_state_attributes["raw_data"] == rows
    stations.async_write_ha_state.assert_called_once()
    assert daily.native_value == 9.0
    assert daily_stations.native_value == 0
    daily_stations.async_write_ha_state.assert_not_called()


@pytest.mark.asyncio
async def test_feed_update_keeps_value_when_feed_lacks_it():
    hass = _hass(({"average_t": None}, []))
    sensor = _added(_value(hass, "ten_minutes", "t", 3.0))

    await _async_update_feed(hass, [sensor], "ten_minutes", QUERY)

    assert sensor.native_value == 3.0


@pytest.mark.asyncio
async def test_feed_update_skips_download_without_added_sensors():
    hass = _hass(({}, []))
    sensor = _value(hass, "daily", "t", 1.0)

    await _async_update_feed(hass, [sensor], "daily", QUERY)

    hass.async_add_executor_job.assert_not_awaited()


@pytest.mark.asyncio
async def test_feed_update_logs_download_errors(caplog):
    hass = SimpleNamespace(async_add_executor_job=AsyncMock(side_effect=OSError("down")))
    sensor = _added(_value(hass, "daily", "t", 1.0))

    await _async_update_feed(hass, [sensor], "daily", QUERY)

    assert sensor.native_value == 1.0
    assert "Error updating daily sensors: down" in caplog.text


@pytest.mark.parametrize(
    "entity",
    [
        _value(SimpleNamespace(), "daily", "t", 0),
        _value(SimpleNamespace(), "hourly", "t", 0),
        _value(SimpleNamespace(), "ten_minutes", "t", 0),
        _stations(SimpleNamespace(), "daily"),
        HungarometRadarImage(SimpleNamespace(data={}, bus=MagicMock())),
    ],
)
def test_entities_are_not_polled(entity):
    assert entity.should_poll is False
