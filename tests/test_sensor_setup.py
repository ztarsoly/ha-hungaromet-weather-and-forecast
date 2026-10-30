"""Tests for setting up an entry's weather station and forecast sensors."""

from collections import Counter, defaultdict
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.hungaromet_weather_and_forecast import sensor
from custom_components.hungaromet_weather_and_forecast.daily_sensor import HungarometWeatherDailySensor
from custom_components.hungaromet_weather_and_forecast.hourly_sensor import HungarometWeatherHourlySensor
from custom_components.hungaromet_weather_and_forecast.station_info_sensor import (
    HungarometStationInfoSensor,
)
from custom_components.hungaromet_weather_and_forecast.ten_minute_sensor import (
    HungarometWeatherTenMinutesSensor,
)
from custom_components.hungaromet_weather_and_forecast.weather_data import (
    process_daily_data,
    process_hourly_data,
    process_ten_minutes_data,
)

STATIONS = [{"StationNumber": 1, "StationName": "Pécs"}]


def _entry(data=None, options=None):
    return SimpleNamespace(
        entry_id="e1",
        title="Pécs",
        data=data if data is not None else {"distance_km": 20},
        options=options or {},
        async_on_unload=MagicMock(),
    )


def _hass(forecast=None):
    async def run(func, *args):
        # Every feed reads 1.5 for each of its measurements
        return defaultdict(lambda: 1.5, {"time": "2026-10-30T10:00:00+00:00"}), STATIONS

    data = {"hungaromet_weather_and_forecast": {"e1": forecast}} if forecast else {}
    return SimpleNamespace(data=data, async_add_executor_job=AsyncMock(side_effect=run))


async def _set_up(hass, entry):
    added = []
    schedule = MagicMock()
    with patch.object(sensor, "RetryingSchedule", return_value=schedule) as schedules:
        await sensor.async_setup_entry(hass, entry, lambda e, *args: added.append((e, args)))
    return added, schedules, schedule


@pytest.mark.asyncio
async def test_setup_adds_every_station_sensor():
    hass = _hass()
    added, _, _ = await _set_up(hass, _entry())

    ((sensors, args),) = added
    # Updated once before they are added
    assert args == (True,)
    assert Counter(type(s) for s in sensors) == {
        HungarometWeatherDailySensor: 28,
        HungarometWeatherHourlySensor: 29,
        HungarometWeatherTenMinutesSensor: 25,
        HungarometStationInfoSensor: 3,
    }
    assert len({s.unique_id for s in sensors}) == len(sensors)
    assert all(s.unique_id.startswith("e1_") for s in sensors)
    assert [c.args[0] for c in hass.async_add_executor_job.await_args_list] == [
        process_daily_data,
        process_hourly_data,
        process_ten_minutes_data,
    ]


@pytest.mark.asyncio
async def test_setup_uses_the_entry_settings():
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
    assert {(s._distance_km, s._location) for s in sensors} == {(35, (46.0, 18.2))}


@pytest.mark.asyncio
async def test_setup_adds_the_forecast_sensors_first():
    hass = _hass(forecast=MagicMock())
    with patch.object(sensor, "forecast_sensors", return_value=["forecast"]) as make:
        added, _, _ = await _set_up(hass, _entry())

    make.assert_called_once_with(hass.data["hungaromet_weather_and_forecast"]["e1"])
    assert added[0] == (["forecast"], ())
    assert len(added) == 2


@pytest.mark.asyncio
async def test_setup_starts_the_feed_schedules():
    entry = _entry()
    _, schedules, schedule = await _set_up(_hass(), entry)

    assert [c.args[1] for c in schedules.call_args_list] == ["daily", "hourly", "ten_minutes"]
    assert all(c.kwargs["config_entry"] is entry for c in schedules.call_args_list)
    assert schedule.start.call_count == 3
    # The updater and the three schedules stop when the entry unloads
    unloads = [c.args[0] for c in entry.async_on_unload.call_args_list]
    assert unloads[1:] == [schedule.start.return_value] * 3


@pytest.mark.asyncio
async def test_update_service_updates_every_feed():
    hass = _hass()
    await _set_up(hass, _entry())
    (updater,) = hass.data["hungaromet_weather_and_forecast_updaters"].values()

    with patch.object(sensor, "_async_update_feed", AsyncMock()) as update:
        await updater()

    assert [c.args[2] for c in update.await_args_list] == ["daily", "hourly", "ten_minutes"]
