"""Tests for downloading the observation feeds and showing them."""

# The tests check the integration's internals
# pylint: disable=protected-access

from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.hungaromet_weather_and_forecast.image import HungarometRadarImage
from custom_components.hungaromet_weather_and_forecast.sensor import (
    StationSensor,
    StationsSensor,
)
from custom_components.hungaromet_weather_and_forecast.stations import (
    Measurement,
    StationQuery,
)
from custom_components.hungaromet_weather_and_forecast.weather_data import process_hourly_data

from .entries import feed_coordinator, loaded_entry

QUERY = StationQuery(distance_km=35, location=(47.5, 21.6))
NEAR = {"StationName": "Near", "Distance_km": 1.0}


def _coordinator(feed="hourly", query=QUERY, result=None, error=None):
    """A feed's coordinator with an enabled entity, downloading as told."""
    coordinator = feed_coordinator(feed, query)
    _downloads(coordinator, result, error)
    coordinator.async_add_listener(MagicMock())
    return coordinator


def _downloads(coordinator, result=None, error=None):
    coordinator.hass.async_add_executor_job = AsyncMock(return_value=result, side_effect=error)


def _value(coordinator, key, unit="°C"):
    return StationSensor(coordinator, Measurement(key, key, unit))


async def test_a_download_is_shown_by_all_its_feeds_sensors():
    """Test one download gives every sensor of the feed its value."""
    rows = [NEAR]
    coordinator = _coordinator(result=({"average_t": 4.2, "average_r": 0.0}, rows))
    temperature = _value(coordinator, "t")
    rain = _value(coordinator, "r", "mm")
    stations = StationsSensor(coordinator)

    await coordinator.async_update_now()

    coordinator.hass.async_add_executor_job.assert_awaited_once_with(
        process_hourly_data, coordinator.hass, 35, (47.5, 21.6)
    )
    assert temperature.available and temperature.native_value == 4.2
    assert rain.native_value == 0.0
    assert stations.available and stations.native_value == 1
    assert stations.extra_state_attributes["raw_data"] == rows


async def test_a_value_missing_from_a_download_makes_the_sensor_unavailable():
    """Test a value missing from a download makes the sensor unavailable."""
    coordinator = _coordinator(result=({"average_t": 3.0}, []))
    sensor = _value(coordinator, "t")
    await coordinator.async_update_now()
    assert sensor.available

    _downloads(coordinator, ({"average_t": None}, []))
    await coordinator.async_update_now()

    assert sensor.native_value is None
    assert not sensor.available


async def test_nothing_is_downloaded_without_enabled_sensors():
    """Test a feed that no enabled sensor shows is not downloaded."""
    coordinator = feed_coordinator("daily", QUERY)
    _downloads(coordinator, ({}, []))

    await coordinator.async_update_now()
    assert await coordinator._async_scheduled_download() is True

    coordinator.hass.async_add_executor_job.assert_not_awaited()


async def test_using_a_station_out_of_range_is_warned_about_once(caplog):
    """The nearest station is used when none is in the radius; said once."""
    far = {"StationName": "Mid", "Distance_km": 22.24}
    query = StationQuery(distance_km=5)

    for feed in ("daily", "daily", "hourly"):
        coordinator = _coordinator(feed, query, ({"average_t": 4.2}, [far]))
        # The entry's feeds share their records
        await coordinator.async_update_now()

    assert [(r.levelname, r.getMessage()) for r in caplog.records] == [
        (
            "WARNING",
            "No HungaroMet station with data within 5 km; "
            "using nearest station Mid (22.2 km)",
        )
    ]


async def test_failed_downloads_are_logged_once_and_so_is_the_recovery(caplog):
    """Test failed downloads make sensors unavailable and are logged once."""
    coordinator = _coordinator("daily", StationQuery(), ({"t": 1.0}, [NEAR]))
    sensor = _value(coordinator, "t")
    stations = StationsSensor(coordinator)
    await coordinator.async_update_now()

    _downloads(coordinator, error=OSError("down"))
    await coordinator.async_update_now()
    await coordinator.async_update_now()

    # Unavailable although the last download is still held
    assert not sensor.available
    assert not stations.available
    assert [(r.levelname, r.getMessage()) for r in caplog.records] == [
        ("WARNING", "HungaroMet daily weather download failed: down")
    ]

    caplog.clear()
    _downloads(coordinator, ({"t": 2.0}, []))
    await coordinator.async_update_now()
    await coordinator.async_update_now()

    assert sensor.available and sensor.native_value == 2.0
    assert [(r.levelname, r.getMessage()) for r in caplog.records] == [
        ("WARNING", "HungaroMet daily weather download works again")
    ]


@pytest.mark.parametrize(
    ("error", "message", "traceback"),
    [
        (TimeoutError(), "TimeoutError", False),
        (ValueError("broken file"), "broken file", False),
        # A bug in a download
        (KeyError("bug"), "KeyError('bug')", True),
    ],
)
async def test_how_a_failed_download_is_logged(caplog, error, message, traceback):
    """Test only an unexpected error is logged with its traceback."""
    coordinator = _coordinator("ten_minutes", StationQuery(), error=error)

    await coordinator.async_update_now()

    [record] = caplog.records
    assert record.getMessage() == f"HungaroMet 10-minute weather download failed: {message}"
    assert bool(record.exc_info) is traceback


@pytest.mark.parametrize(
    "entity",
    [
        _value(feed_coordinator("daily"), "t"),
        _value(feed_coordinator("hourly"), "t"),
        _value(feed_coordinator("ten_minutes"), "t"),
        StationsSensor(feed_coordinator("daily")),
        HungarometRadarImage(MagicMock(data={}), loaded_entry()),
    ],
)
def test_entities_are_not_polled(entity):
    """Test entities are not polled."""
    assert entity.should_poll is False
