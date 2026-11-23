"""Tests for setting up an entry's weather station and forecast sensors."""

# The tests check the integration's internals
# pylint: disable=protected-access

import asyncio
from collections import Counter
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import numpy
import pytest
from homeassistant.util import dt as dt_util

from custom_components.hungaromet_weather_and_forecast import sensor, station_storage
from custom_components.hungaromet_weather_and_forecast.station_sensor import (
    StationQuery,
    StationSensor,
    StationsSensor,
)
from custom_components.hungaromet_weather_and_forecast.weather_data import (
    process_daily_data,
    process_hourly_data,
    process_ten_minutes_data,
)

STATIONS = [{"StationNumber": 1, "StationName": "Pécs"}]


class FeedData(dict):
    """A feed's values: 1.5 for every measurement that is not given."""
    def get(self, key, default=None):
        return super().get(key, 1.5)


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
        time = "2026-10-30" if func is process_daily_data else "2026-10-30T10:00:00+00:00"
        return FeedData(time=time), STATIONS

    data = {"hungaromet_weather_and_forecast": {"e1": forecast}} if forecast else {}
    return SimpleNamespace(data=data, async_add_executor_job=AsyncMock(side_effect=run))


def _store(stored=None):
    """A station store holding `stored`."""
    return MagicMock(async_load=AsyncMock(return_value=stored), async_save=AsyncMock())


async def _set_up(hass, entry, store=None):
    added = []
    schedule = MagicMock()
    with (
        patch.object(sensor, "RetryingSchedule", return_value=schedule) as schedules,
        patch.object(sensor, "station_store", return_value=store or _store()),
    ):
        await sensor.async_setup_entry(hass, entry, lambda e, *args: added.append((e, args)))
    return added, schedules, schedule


async def test_setup_adds_every_station_sensor():
    """Test setup adds every station sensor."""
    hass = _hass()
    added, _, _ = await _set_up(hass, _entry())

    ((sensors, args),) = added
    # Updated once before they are added
    assert args == (True,)
    assert Counter((type(s), s.feed.key) for s in sensors) == {
        (StationSensor, "daily"): 28,
        (StationSensor, "hourly"): 29,
        (StationSensor, "ten_minutes"): 25,
        (StationsSensor, "daily"): 1,
        (StationsSensor, "hourly"): 1,
        (StationsSensor, "ten_minutes"): 1,
    }
    assert sensors[0].native_value.isoformat() == "2026-10-30"
    assert sensors[1].native_value == 1.5
    assert len({s.unique_id for s in sensors}) == len(sensors)
    assert all(s.unique_id.startswith("e1_") for s in sensors)
    assert [c.args[0] for c in hass.async_add_executor_job.await_args_list] == [
        process_daily_data,
        process_hourly_data,
        process_ten_minutes_data,
    ]


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
    assert {s._query for s in sensors} == {
        StationQuery(35, (46.0, 18.2), sensors[0]._query.scope)
    }


async def test_setup_adds_the_forecast_sensors_first():
    """Test setup adds the forecast sensors first."""
    hass = _hass(forecast=MagicMock())
    forecast = hass.data["hungaromet_weather_and_forecast"]["e1"]
    with (
        patch.object(sensor, "forecast_sensors", return_value=["forecast"]) as make,
        patch.object(sensor, "warning_sensors", return_value=["warning"]) as warnings,
    ):
        added, _, _ = await _set_up(hass, _entry())

    make.assert_called_once_with(forecast)
    warnings.assert_called_once_with(forecast.warnings)
    assert added[0] == (["forecast", "warning"], ())
    assert len(added) == 2


async def test_setup_starts_the_feed_schedules():
    """Test setup starts the feed schedules."""
    entry = _entry()
    _, schedules, schedule = await _set_up(_hass(), entry)

    assert [c.args[1] for c in schedules.call_args_list] == ["daily", "hourly", "ten_minutes"]
    assert all(c.kwargs["config_entry"] is entry for c in schedules.call_args_list)
    assert schedule.start.call_count == 3
    # The updater and the three schedules stop when the entry unloads
    unloads = [c.args[0] for c in entry.async_on_unload.call_args_list]
    assert unloads[1:] == [schedule.start.return_value] * 3


async def test_a_failed_first_download_still_adds_the_sensors_and_retries(caplog):
    """Test a failed first download still adds the sensors and retries it.

    Without the retry the daily sensors would wait for the next day's run.
    """
    hass = _hass()
    run = hass.async_add_executor_job.side_effect

    async def offline_daily(func, *args):
        if func is process_daily_data:
            raise OSError("Failed to resolve 'odp.met.hu'")
        return await run(func, *args)

    hass.async_add_executor_job.side_effect = offline_daily
    entry = _entry()
    with patch.object(sensor, "async_call_later") as call_later:
        ((sensors, _),) = (await _set_up(hass, entry))[0]

    daily = [s for s in sensors if s.feed.key == "daily"]
    assert len(daily) == 29
    assert {s.native_value for s in daily} == {None, 0}  # no values, no stations
    assert sensors[-1].native_value == 1  # the other feeds have theirs
    assert "Error updating daily sensors" in caplog.text
    ((hass_arg, delay, attempt),) = [c.args for c in call_later.call_args_list]
    assert (hass_arg, delay) == (hass, sensor.FIRST_RETRY_DELAY)
    # The retries stop when the entry unloads
    stop = entry.async_on_unload.call_args_list[0].args[0]
    stop()
    call_later.return_value.assert_called_once()
    assert attempt is not None


def _retry(sensors):
    """Start the retries of the daily feed with call_later patched."""
    with patch.object(sensor, "async_call_later") as call_later:
        stop = sensor._async_retry_first_download(
            MagicMock(), sensors, "daily", StationQuery()
        )
    return stop, call_later


async def test_first_download_is_retried_until_it_works():
    """Test first download is retried until it works."""
    daily = MagicMock(hass="hass", added=True, feed=SimpleNamespace(key="daily"))
    update = AsyncMock(side_effect=[None, "2026-10-30"])
    stop, call_later = _retry([daily])
    attempt = call_later.call_args.args[2]

    with (
        patch.object(sensor, "async_call_later") as again,
        patch.object(sensor, "_async_update_feed", update),
    ):
        await attempt(None)  # fails: tried again
        assert again.call_count == 1
        await attempt(None)  # works: no more tries
        assert again.call_count == 1

    assert update.await_count == 2
    stop()  # nothing left to stop
    call_later.return_value.assert_not_called()


async def test_first_download_is_not_retried_without_enabled_sensors():
    """Test first download is not retried without enabled sensors."""
    disabled = MagicMock(hass="hass", added=False, feed=SimpleNamespace(key="daily"))
    update = AsyncMock()
    _, call_later = _retry([disabled])

    with (
        patch.object(sensor, "async_call_later") as again,
        patch.object(sensor, "_async_update_feed", update),
    ):
        await call_later.call_args.args[2](None)

    update.assert_not_awaited()
    again.assert_not_called()


def _offline(hass, feed_process):
    """Make `feed_process` fail, like a download without DNS."""
    run = hass.async_add_executor_job.side_effect

    async def offline(func, *args):
        if func is feed_process:
            raise OSError("Failed to resolve 'odp.met.hu'")
        return await run(func, *args)

    hass.async_add_executor_job.side_effect = offline


async def test_first_downloads_are_saved():
    """Test first downloads are saved, with the stations they average."""
    store = _store()
    later = datetime(2026, 11, 23, 10, 0, tzinfo=dt_util.UTC)
    with patch.object(station_storage.dt_util, "utcnow", return_value=later):
        await _set_up(_hass(), _entry(), store)

    saved = store.async_save.await_args.args[0]
    assert saved["query"] == {"distance_km": 20, "location": None}
    assert list(saved["feeds"]) == ["daily", "hourly", "ten_minutes"]
    daily = saved["feeds"]["daily"]
    assert daily["saved"] == "2026-11-23T10:00:00+00:00"
    assert daily["data"] == {"time": "2026-10-30"}
    assert daily["stations"] == STATIONS


def _saved(saved_at, distance_km=20):
    """A station file with the daily feed saved at `saved_at`."""
    return {
        "query": {"distance_km": distance_km, "location": None},
        "feeds": {
            "daily": {
                "saved": saved_at.isoformat(),
                "data": {"time": "2026-11-22", "average_t": 4.25},
                "stations": STATIONS,
            }
        },
    }


NOW = datetime(2026, 11, 23, 10, 0, tzinfo=dt_util.UTC)


async def _set_up_offline(stored):
    """Set up with the daily download failing and `stored` saved."""
    hass = _hass()
    _offline(hass, process_daily_data)
    with (
        patch.object(sensor, "async_call_later"),
        patch.object(station_storage.dt_util, "utcnow", return_value=NOW),
    ):
        ((sensors, _),) = (await _set_up(hass, _entry(), _store(stored)))[0]
    return {s.translation_key: s.native_value for s in sensors if s.feed.key == "daily"}


async def test_a_failed_first_download_shows_the_values_saved_within_the_hour():
    """Test a failed first download shows the values saved within the hour."""
    values = await _set_up_offline(_saved(NOW - timedelta(minutes=59)))

    assert values["daily_t"] == 4.25
    assert values["daily_time"].isoformat() == "2026-11-22"
    assert values["stations"] == 1


@pytest.mark.parametrize(
    "stored",
    [
        None,  # nothing saved
        _saved(NOW - timedelta(minutes=61)),  # more than an hour ago
        _saved(NOW, distance_km=35),  # for other stations (e.g. after Configure)
        {"query": {"distance_km": 20, "location": None}, "feeds": None},
        {"query": {"distance_km": 20, "location": None}, "feeds": {"daily": {}}},
    ],
)
async def test_a_failed_first_download_leaves_out_old_or_other_values(stored):
    """Test a failed first download leaves out old, other or unreadable values."""
    values = await _set_up_offline(stored)

    assert values["daily_t"] is None
    assert values["stations"] == 0


async def test_scheduled_downloads_are_saved():
    """Test scheduled downloads are saved."""
    hass = _hass()
    daily = MagicMock(hass="hass", added=True, feed=SimpleNamespace(key="daily"))
    saved = MagicMock(async_save=AsyncMock())

    await sensor._async_update_feed(hass, [daily], "daily", StationQuery(), saved)

    saved.async_save.assert_awaited_once_with(
        "daily", daily.show.call_args.args[0], STATIONS
    )


def test_saved_averages_are_plain_numbers():
    """Test saved averages are plain numbers, which JSON can hold."""
    store = _store()
    feeds = station_storage.SavedFeeds(store, StationQuery(location=(47.5, 21.6)))

    asyncio.run(feeds.async_save("daily", {"average_t": numpy.float64(4.25)}, []))

    saved = store.async_save.call_args.args[0]
    assert saved["query"]["location"] == [47.5, 21.6]
    value = saved["feeds"]["daily"]["data"]["average_t"]
    assert isinstance(value, float) and not isinstance(value, numpy.float64)


def test_station_store_is_per_entry():
    """Test station store is per entry."""
    with patch.object(station_storage, "Store") as store:
        station_storage.station_store("hass", "e1")
    store.assert_called_once_with(
        "hass", station_storage.STORAGE_VERSION, "hungaromet_weather_and_forecast.stations.e1"
    )


async def test_update_service_updates_every_feed():
    """Test update service updates every feed."""
    hass = _hass()
    await _set_up(hass, _entry())
    (updater,) = hass.data["hungaromet_weather_and_forecast_updaters"].values()

    with patch.object(sensor, "_async_update_feed", AsyncMock()) as update:
        await updater()

    assert [c.args[2] for c in update.await_args_list] == ["daily", "hourly", "ten_minutes"]
