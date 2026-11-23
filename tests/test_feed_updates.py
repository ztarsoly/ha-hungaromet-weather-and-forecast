"""Tests for the scheduled feed updates in sensor.py."""

# The tests check the integration's internals
# pylint: disable=protected-access

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.hungaromet_weather_and_forecast import sensor as sensor_platform
from custom_components.hungaromet_weather_and_forecast.national_radar import HungarometRadarImage
from custom_components.hungaromet_weather_and_forecast.sensor import (
    FEED_MAX_AGE,
    _async_update_feed,
    _expired,
)
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


async def test_feed_update_keeps_value_when_feed_lacks_it():
    """Test feed update keeps value when feed lacks it."""
    hass = _hass(({"average_t": None}, []))
    sensor = _added(_value(hass, "ten_minutes", "t", 3.0))

    await _async_update_feed(hass, [sensor], "ten_minutes", QUERY)

    assert sensor.native_value == 3.0


async def test_feed_update_skips_download_without_added_sensors():
    """Test feed update skips download without added sensors."""
    hass = _hass(({}, []))
    sensor = _value(hass, "daily", "t", 1.0)

    await _async_update_feed(hass, [sensor], "daily", QUERY)

    hass.async_add_executor_job.assert_not_awaited()


async def test_feed_update_logs_download_errors(caplog):
    """Test feed update logs download errors."""
    hass = SimpleNamespace(async_add_executor_job=AsyncMock(side_effect=OSError("down")))
    sensor = _added(_value(hass, "daily", "t", 1.0))

    await _async_update_feed(hass, [sensor], "daily", QUERY)

    assert sensor.native_value == 1.0
    assert "Error updating daily sensors: down" in caplog.text


async def test_feed_update_records_the_observation_time():
    """Test a feed update records the observation time shown."""
    hass = _hass(({"time": "2026-11-23T10:00:00+00:00", "average_t": 4.2}, []))
    sensor = _added(_value(hass, "hourly", "t", 1.0))
    observed = {}

    await _async_update_feed(hass, [sensor], "hourly", QUERY, observed)

    assert observed == {"hourly": "2026-11-23T10:00:00+00:00"}


TEN = "2026-11-23T10:00:00+00:00"


def _at(day, hour, minute):
    return datetime(2026, 11, day, hour, minute, tzinfo=UTC)


@pytest.mark.parametrize(
    ("data_type", "observed", "now", "expired"),
    [
        # An hour after the next file was due: the 10-minute file of 10:00
        # comes at 10:10, the next one at 10:20
        ("ten_minutes", TEN, _at(23, 11, 20), False),
        ("ten_minutes", TEN, _at(23, 11, 21), True),
        # The hourly file of 10:00 comes at 10:20, the next one at 11:20
        ("hourly", TEN, _at(23, 12, 20), False),
        ("hourly", TEN, _at(23, 12, 21), True),
        # The daily file of the 22nd comes on the 23rd at 09:35, the next one
        # on the 24th
        ("daily", "2026-11-22", _at(24, 10, 35), False),
        ("daily", "2026-11-22", _at(24, 10, 36), True),
        # Nothing shown, or an unreadable time: nothing to clear
        ("daily", None, datetime(2026, 11, 30, tzinfo=UTC), False),
        ("daily", "not a time", datetime(2026, 11, 30, tzinfo=UTC), False),
    ],
)
def test_expired(data_type, observed, now, expired):
    """Test values expire an hour after their feed's next file was due."""
    assert _expired(data_type, observed, now) is expired


def test_every_feed_has_a_max_age():
    """Test every feed has a max age."""
    assert set(FEED_MAX_AGE) == set(FEEDS)


async def _fail_at(now, sensors, observed):
    hass = SimpleNamespace(async_add_executor_job=AsyncMock(side_effect=OSError("down")))
    with patch.object(sensor_platform.dt_util, "utcnow", return_value=now):
        await _async_update_feed(hass, sensors, "hourly", QUERY, observed)


async def test_failed_downloads_clear_values_too_old_to_show():
    """Test a failed download clears the values once they are too old."""
    hass = SimpleNamespace()
    temperature = _added(_value(hass, "hourly", "t", 4.2))
    stations = _added(_stations(hass, "hourly"))
    stations._stations = [{"StationName": "Near"}]
    observed = {"hourly": "2026-11-23T10:00:00+00:00"}
    expiry = datetime(2026, 11, 23, 10, tzinfo=UTC) + FEED_MAX_AGE["hourly"]

    await _fail_at(expiry, [temperature, stations], observed)
    assert temperature.native_value == 4.2
    assert stations.native_value == 1
    temperature.async_write_ha_state.assert_not_called()

    await _fail_at(expiry + timedelta(minutes=1), [temperature, stations], observed)
    assert temperature.native_value is None
    assert stations.native_value == 0
    temperature.async_write_ha_state.assert_called_once()
    assert not observed


async def test_failed_downloads_without_values_clear_nothing():
    """Test a failed download clears nothing when no values were shown."""
    sensor = _added(_value(SimpleNamespace(), "hourly", "t", None))

    await _fail_at(datetime(2026, 11, 30, tzinfo=UTC), [sensor], {})

    sensor.async_write_ha_state.assert_not_called()


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
