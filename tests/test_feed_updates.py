"""Tests for the scheduled feed updates in sensor.py."""

# The tests check the integration's internals
# pylint: disable=protected-access

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


async def test_feed_update_downloads_once_and_updates_its_sensors():
    """Test feed update downloads once and updates its sensors."""
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


async def test_a_value_missing_from_a_download_makes_the_sensor_unavailable():
    """Test a value missing from a download makes the sensor unavailable."""
    hass = _hass(({"average_t": None}, []))
    sensor = _added(_value(hass, "ten_minutes", "t", 3.0))

    await _async_update_feed(hass, [sensor], "ten_minutes", QUERY)

    assert sensor.native_value is None
    assert not sensor.available


async def test_feed_update_skips_download_without_added_sensors():
    """Test feed update skips download without added sensors."""
    hass = _hass(({}, []))
    sensor = _value(hass, "daily", "t", 1.0)

    await _async_update_feed(hass, [sensor], "daily", QUERY)

    hass.async_add_executor_job.assert_not_awaited()


async def test_feed_update_makes_waiting_sensors_available():
    """Test a download makes sensors waiting for their first one available."""
    hass = _hass(({"average_t": 4.2}, [{"StationName": "Near"}]))
    temperature = _added(_value(hass, "hourly", "t", None))
    stations = _added(_stations(hass, "hourly"))
    for sensor in (temperature, stations):
        sensor.mark_download_failed()
        assert not sensor.available

    await _async_update_feed(hass, [temperature, stations], "hourly", QUERY)

    assert temperature.available and temperature.native_value == 4.2
    assert stations.available and stations.native_value == 1


async def test_failed_downloads_are_logged_once_and_so_is_the_recovery(caplog):
    """Test failed downloads make sensors unavailable and are logged once."""
    hass = SimpleNamespace(async_add_executor_job=AsyncMock(side_effect=OSError("down")))
    query = StationQuery()
    sensor = _added(_value(hass, "daily", "t", 1.0))

    await _async_update_feed(hass, [sensor], "daily", query)
    await _async_update_feed(hass, [sensor], "daily", query)

    assert not sensor.available
    assert sensor.async_write_ha_state.call_count == 2
    assert [(r.levelname, r.getMessage()) for r in caplog.records] == [
        ("WARNING", "HungaroMet daily weather download failed: down")
    ]

    caplog.clear()
    hass.async_add_executor_job = AsyncMock(return_value=({"t": 2.0}, []))
    await _async_update_feed(hass, [sensor], "daily", query)
    await _async_update_feed(hass, [sensor], "daily", query)

    assert sensor.available and sensor.native_value == 2.0
    assert [(r.levelname, r.getMessage()) for r in caplog.records] == [
        ("WARNING", "HungaroMet daily weather download works again")
    ]


async def test_an_unexpected_error_is_logged_with_its_traceback(caplog):
    """Test a bug in a download is logged with its traceback."""
    hass = SimpleNamespace(async_add_executor_job=AsyncMock(side_effect=KeyError("bug")))
    sensor = _added(_value(hass, "daily", "t", 1.0))

    await _async_update_feed(hass, [sensor], "daily", StationQuery())

    [record] = caplog.records
    assert record.getMessage() == (
        "HungaroMet daily weather download failed: KeyError('bug')"
    )
    assert record.exc_info


async def test_a_failed_entity_update_makes_the_sensor_unavailable():
    """Test a failed update_entity makes the sensor unavailable."""
    hass = SimpleNamespace(async_add_executor_job=AsyncMock(side_effect=OSError("down")))
    sensor = _added(_value(hass, "daily", "t", 1.0))

    await sensor.async_update()

    assert not sensor.available
    sensor.async_write_ha_state.assert_called_once()


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
    """Test entities are not polled."""
    assert entity.should_poll is False
