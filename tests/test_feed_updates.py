"""Tests for the scheduled feed updates in sensor.py."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.hungaromet_weather_and_forecast.daily_sensor import HungarometWeatherDailySensor
from custom_components.hungaromet_weather_and_forecast.hourly_sensor import HungarometWeatherHourlySensor
from custom_components.hungaromet_weather_and_forecast.national_radar import HungarometRadarImage
from custom_components.hungaromet_weather_and_forecast.sensor import _async_update_feed
from custom_components.hungaromet_weather_and_forecast.station_info_sensor import (
    HungarometStationInfoSensor,
)
from custom_components.hungaromet_weather_and_forecast.ten_minute_sensor import (
    HungarometWeatherTenMinutesSensor,
)
from custom_components.hungaromet_weather_and_forecast.weather_data import process_hourly_data


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
    temperature = _added(HungarometWeatherHourlySensor(hass, "T", 1.0, "°C", "t"))
    rain = _added(HungarometWeatherHourlySensor(hass, "R", 5.0, "mm", "r"))
    stations = _added(HungarometStationInfoSensor(hass, "S", [], "hourly"))
    daily = _added(HungarometWeatherDailySensor(hass, "D", 9.0, "°C", "t"))
    daily_stations = _added(HungarometStationInfoSensor(hass, "S", [], "daily"))

    await _async_update_feed(
        hass,
        [temperature, rain, stations, daily, daily_stations],
        "hourly",
        35,
        (47.5, 21.6),
    )

    hass.async_add_executor_job.assert_awaited_once_with(
        process_hourly_data, hass, 35, (47.5, 21.6)
    )
    assert temperature._state == 4.2
    assert rain._state == 0.0
    assert stations._station_info == rows
    stations.async_write_ha_state.assert_called_once()
    assert daily._state == 9.0
    assert daily_stations._station_info == []
    daily_stations.async_write_ha_state.assert_not_called()


@pytest.mark.asyncio
async def test_feed_update_keeps_value_when_feed_lacks_it():
    hass = _hass(({"average_t": None}, []))
    sensor = _added(HungarometWeatherTenMinutesSensor(hass, "T", 3.0, "°C", "t"))

    await _async_update_feed(hass, [sensor], "ten_minutes", 20)

    assert sensor._state == 3.0
    sensor.async_write_ha_state.assert_not_called()


@pytest.mark.asyncio
async def test_feed_update_skips_download_without_added_sensors():
    hass = _hass(({}, []))
    sensor = HungarometWeatherDailySensor(hass, "D", 1.0, "°C", "t")

    await _async_update_feed(hass, [sensor], "daily", 20)

    hass.async_add_executor_job.assert_not_awaited()


@pytest.mark.asyncio
async def test_feed_update_logs_download_errors(caplog):
    hass = SimpleNamespace(async_add_executor_job=AsyncMock(side_effect=OSError("down")))
    sensor = _added(HungarometWeatherDailySensor(hass, "D", 1.0, "°C", "t"))

    await _async_update_feed(hass, [sensor], "daily", 20)

    assert sensor._state == 1.0
    assert "Error updating daily sensors: down" in caplog.text


@pytest.mark.parametrize(
    "entity",
    [
        HungarometWeatherDailySensor(SimpleNamespace(), "D", 0, None, "t"),
        HungarometWeatherHourlySensor(SimpleNamespace(), "H", 0, None, "t"),
        HungarometWeatherTenMinutesSensor(SimpleNamespace(), "T", 0, None, "t"),
        HungarometStationInfoSensor(SimpleNamespace(), "S", [], "daily"),
        HungarometRadarImage(SimpleNamespace(data={}, bus=MagicMock())),
    ],
)
def test_entities_are_not_polled(entity):
    assert entity.should_poll is False
