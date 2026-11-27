"""Tests for setting up an entry's weather station and forecast sensors."""

# The tests check the integration's internals
# pylint: disable=protected-access

from collections import Counter
from unittest.mock import AsyncMock, MagicMock, patch

from custom_components.hungaromet_weather_and_forecast import coordinator as coordinators
from custom_components.hungaromet_weather_and_forecast import sensor
from custom_components.hungaromet_weather_and_forecast.sensor import StationSensor, StationsSensor
from custom_components.hungaromet_weather_and_forecast.stations import StationQuery
from custom_components.hungaromet_weather_and_forecast.weather_data import (
    process_daily_data,
    process_hourly_data,
    process_ten_minutes_data,
)

from .entries import loaded_entry

STATIONS = [{"StationNumber": 1, "StationName": "Pécs", "Distance_km": 1.0}]
PROCESSES = [process_daily_data, process_hourly_data, process_ten_minutes_data]


class FeedData(dict):
    """A feed's values: 1.5 for every measurement that is not given."""
    def get(self, key, default=None):
        return super().get(key, 1.5)


def _entry(data=None, options=None, forecast=None):
    return loaded_entry("e1", "Pécs", data, options, forecast=forecast)


async def _download(func, *args):
    time = "2026-10-30" if func is process_daily_data else "2026-10-30T10:00:00+00:00"
    return FeedData(time=time), STATIONS


def _hass():
    return MagicMock(is_stopping=False, async_add_executor_job=AsyncMock(side_effect=_download))


async def _set_up(hass, entry):
    """Set up the feeds like the entry's setup, then the sensors."""
    added = []
    schedule = MagicMock()
    with patch.object(coordinators, "RetryingSchedule", return_value=schedule) as schedules:
        entry.runtime_data.feeds = await coordinators.async_setup_feeds(hass, entry)
    await sensor.async_setup_entry(hass, entry, lambda e, *args: added.append((e, args)))
    return added, schedules, schedule


async def test_setup_adds_every_station_sensor():
    """Test setup adds every station sensor."""
    hass = _hass()
    added, _, _ = await _set_up(hass, _entry())

    ((sensors, args),) = added
    # Their feeds are downloaded already, so they are not updated first
    assert args == ()
    assert Counter((type(s), s.coordinator.feed.key) for s in sensors) == {
        (StationSensor, "daily"): 26,
        (StationSensor, "hourly"): 28,
        (StationSensor, "ten_minutes"): 24,
        (StationsSensor, "daily"): 1,
        (StationsSensor, "hourly"): 1,
        (StationsSensor, "ten_minutes"): 1,
    }
    assert all(s.available for s in sensors)
    assert sensors[0].native_value.isoformat() == "2026-10-30"
    assert sensors[1].native_value == 1.5
    assert len({s.unique_id for s in sensors}) == len(sensors)
    assert all(s.unique_id.startswith("e1_") for s in sensors)
    # Each feed once, one after the other
    assert [c.args[0] for c in hass.async_add_executor_job.await_args_list] == PROCESSES


async def test_setup_uses_the_entry_settings():
    """Test setup uses the entry settings."""
    hass = _hass()
    entry = _entry(
        {"distance_km": 20, "location": "home"},
        {
            "distance_km": 35,
            "location": "settlement",
            "forecast": {"name": "Pécs", "kod": "9", "lat": 46.0, "lon": 18.2},
        },
    )
    ((sensors, _),) = (await _set_up(hass, entry))[0]

    # The options win over the setup data
    calls = hass.async_add_executor_job.await_args_list
    assert all(c.args[1:] == (hass, 35, (46.0, 18.2)) for c in calls)
    (query,) = {s.coordinator.query for s in sensors}
    assert query == StationQuery(35, (46.0, 18.2), query.scope)
    # What is logged is kept with the entry
    assert query.records is entry.runtime_data.failures


async def test_setup_adds_the_forecast_sensors_first():
    """Test setup adds the forecast sensors first."""
    forecast = MagicMock()
    with (
        patch.object(sensor, "forecast_sensors", return_value=["forecast"]) as make,
        patch.object(sensor, "warning_sensors", return_value=["warning"]) as warnings,
    ):
        added, _, _ = await _set_up(_hass(), _entry(forecast=forecast))

    make.assert_called_once_with(forecast)
    warnings.assert_called_once_with(forecast.warnings)
    assert added[0] == (["forecast", "warning"], ())
    assert len(added) == 2


async def test_the_feeds_are_scheduled_and_follow_the_update_service():
    """Test each feed has its schedule, stopped on unload, and its updater."""
    hass = _hass()
    entry = _entry()
    _, schedules, schedule = await _set_up(hass, entry)

    assert [c.args[1] for c in schedules.call_args_list] == ["daily", "hourly", "ten_minutes"]
    assert all(c.kwargs["config_entry"] is entry for c in schedules.call_args_list)
    assert schedule.start.call_count == 3
    unloads = [c.args[0] for c in entry.async_on_unload.call_args_list]
    assert unloads.count(schedule.start.return_value) == 3
    updaters = entry.runtime_data.updaters
    assert list(updaters) == ["e1_daily", "e1_hourly", "e1_ten_minutes"]

    # As with the sensors added
    for coordinator in entry.runtime_data.feeds.values():
        coordinator.async_add_listener(MagicMock())
    hass.async_add_executor_job.reset_mock()
    for update in list(updaters.values()):
        await update()

    assert [c.args[0] for c in hass.async_add_executor_job.await_args_list] == PROCESSES


async def test_a_failed_first_download_still_adds_the_sensors(caplog):
    """Test a failed feed has unavailable sensors, to be filled by a retry."""
    hass = _hass()

    async def offline_daily(func, *args):
        if func is process_daily_data:
            raise OSError("Failed to resolve 'odp.met.hu'")
        return await _download(func, *args)

    hass.async_add_executor_job.side_effect = offline_daily
    entry = _entry()
    ((sensors, _),) = (await _set_up(hass, entry))[0]

    daily = [s for s in sensors if s.coordinator.feed.key == "daily"]
    assert len(daily) == 27
    assert {s.native_value for s in daily} == {None, 0}  # no values, no stations
    # Unavailable, not unknown: nothing was downloaded since the restart
    assert not any(s.available for s in daily)
    assert sensors[-1].native_value == 1  # the other feeds have theirs
    assert all(s.available for s in sensors if s not in daily)
    assert "HungaroMet daily weather download failed" in caplog.text
    assert [c.downloaded for c in entry.runtime_data.feeds.values()] == [False, True, True]
