"""Tests for saving the last forecast so it survives a restart."""

# The tests check the integration's internals
# pylint: disable=protected-access

import asyncio
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import custom_components.hungaromet_weather_and_forecast as integration
from custom_components.hungaromet_weather_and_forecast.failures import FailureLog
from custom_components.hungaromet_weather_and_forecast import coordinator as coordinators, scheduler
from custom_components.hungaromet_weather_and_forecast.forecast_scraper import (
    HUNGARY_TZ,
    ForecastData,
    ForecastPeriod,
    _derive,
    forecast_from_dict,
    forecast_to_dict,
    keep_dropped_row,
)

NOW = datetime(2026, 10, 28, 17, 22, tzinfo=HUNGARY_TZ)
DEBRECEN = SimpleNamespace(name="Debrecen", kod="725", lat=47.5, lon=21.6)


def _at(hour, day=28, **values):
    return ForecastPeriod(
        forecast_time=datetime(2026, 10, day, hour, tzinfo=HUNGARY_TZ), **values
    )


def _forecast():
    data = ForecastData(
        settlement="Debrecen",
        issued=datetime(2026, 10, 28, 10, 46, tzinfo=HUNGARY_TZ),
        periods=[
            _at(13, temperature=16, temperature_max=16, weather_condition="sunny",
                wind_direction="n", cloud_cover=34, precipitation=0.0),
            _at(19, temperature=10, wind_bearing=352.0),
        ],
    )
    _derive(data, NOW)
    return data


def test_forecast_round_trip():
    """Test forecast round trip."""
    stored = forecast_to_dict(_forecast())

    assert stored["issued"] == "2026-10-28T10:46:00+01:00"
    assert stored["periods"][0]["forecast_time"] == "2026-10-28T13:00:00+01:00"
    assert stored["periods"][0]["temperature_max"] == 16

    data = forecast_from_dict(stored, NOW)
    assert data.periods == _forecast().periods
    assert data.issued == _forecast().issued
    # The hours are worked out again for the time it is loaded
    assert (data.current.forecast_time.hour, data.current.temperature) == (17, 12.0)


def test_saved_hours_come_back_as_they_were_saved():
    """Test saved hours come back as they were saved."""
    stored = forecast_to_dict(_forecast())
    assert len(stored["hourly"]) == 7
    stored["hourly"][0]["temperature"] = 99.0

    data = forecast_from_dict(stored, NOW)

    assert data.hourly[0].temperature == 99.0
    assert data.hourly[1:] == _forecast().hourly[1:]


def test_forecast_saved_without_hours_gets_them_from_its_rows():
    """Test forecast saved without hours gets them from its rows."""
    stored = forecast_to_dict(_forecast())
    del stored["hourly"]

    data = forecast_from_dict(stored, NOW)

    assert data.hourly == _forecast().hourly


def test_a_saved_condition_that_is_no_state_comes_back_as_no_value():
    """Test the "exceptional" saved by earlier versions is dropped."""
    stored = forecast_to_dict(_forecast())
    for row in (*stored["periods"], *stored["hourly"]):
        row["weather_condition"] = "exceptional"

    data = forecast_from_dict(stored, NOW)

    assert {p.weather_condition for p in (*data.periods, *data.hourly)} == {None}
    assert data.current.weather_condition is None


def test_forecast_round_trip_without_times():
    """Test forecast round trip without times."""
    data = ForecastData(settlement="X", periods=[ForecastPeriod(temperature=1)])
    rebuilt = forecast_from_dict(forecast_to_dict(data), NOW)
    assert rebuilt.issued is None
    assert rebuilt.periods == [ForecastPeriod(temperature=1)]


def test_saved_forecast_gives_back_the_dropped_row_after_a_restart():
    """Restarted at 17:22: met.hu's new table starts at 19:00."""
    saved = forecast_from_dict(forecast_to_dict(_forecast()), NOW)
    new = ForecastData(
        settlement="Debrecen", periods=[_at(19, temperature=10), _at(1, 29, temperature=8)]
    )
    _derive(new, NOW)

    data = keep_dropped_row(new, saved, NOW)

    assert data.current.forecast_time.hour == 17


def _coordinator(stored):
    coordinator = object.__new__(coordinators.ForecastCoordinator)
    coordinator.settlement = DEBRECEN
    coordinator.data = None
    coordinator._saved = None
    coordinator.last_update_success = True
    coordinator._save_lock = asyncio.Lock()
    coordinator._failure_log = FailureLog(MagicMock(), "Forecast")
    coordinator._store = MagicMock(async_load=AsyncMock(return_value=stored))
    return coordinator


@pytest.mark.parametrize("key", ["code", "kod"])  # "kod" up to 1.3.4
async def test_load_stored_forecast(key):
    """Test load stored forecast."""
    stored = {key: "725", "forecast": forecast_to_dict(_forecast())}
    coordinator = _coordinator(stored)

    with patch.object(coordinators.dt_util, "now", return_value=NOW):
        await coordinator.async_load_stored()

    # Never shown by itself: only used to fill the first download
    assert coordinator.data is None
    assert coordinator._saved.settlement == "Debrecen"
    assert coordinator._saved.current.forecast_time.hour == 17
    # The 13:00 row is sunny, but the sun has set by 17:00 at the settlement
    assert coordinator._saved.current.weather_condition == "clear-night"
    assert coordinator.has_saved_current_hour is True


@pytest.mark.parametrize(
    "stored",
    [
        None,  # nothing saved yet
        {"code": "2209", "forecast": {}},  # another settlement (e.g. after Configure)
    ],
)
async def test_load_ignores_missing_or_other_settlements(stored):
    """Test load ignores missing or other settlements."""
    coordinator = _coordinator(stored)
    await coordinator.async_load_stored()
    assert coordinator._saved is None
    assert coordinator.has_saved_current_hour is False


async def test_load_ignores_unreadable_forecast(caplog):
    """Test load ignores unreadable forecast."""
    coordinator = _coordinator({"code": "725", "forecast": {"periods": []}})
    await coordinator.async_load_stored()
    assert coordinator._saved is None
    assert "Ignoring the saved forecast" in caplog.text


async def test_each_download_is_saved():
    """Test each download is saved."""
    coordinator = _coordinator(None)
    coordinator.hass = MagicMock()
    coordinator._store.async_save = AsyncMock()
    with (
        patch.object(coordinators, "async_get_clientsession"),
        patch.object(coordinators, "fetch_forecast", AsyncMock(return_value=_forecast())),
    ):
        data = await coordinator._async_update_data()

    coordinator._store.async_save.assert_awaited_once_with(
        {"code": "725", "forecast": forecast_to_dict(data)}
    )


def _later_download():
    """A new table without the rows before 19:00."""
    new = ForecastData(
        settlement="Debrecen", periods=[_at(19, temperature=10), _at(1, 29, temperature=8)]
    )
    _derive(new, NOW)
    return new


async def _download(coordinator):
    coordinator.hass = MagicMock()
    coordinator._store.async_save = AsyncMock()
    with (
        patch.object(coordinators, "async_get_clientsession"),
        patch.object(coordinators, "fetch_forecast", AsyncMock(return_value=_later_download())),
        patch.object(coordinators.dt_util, "now", return_value=NOW),
        patch.object(coordinators, "continue_from_recorded", side_effect=lambda d, *_: d) as go_on,
    ):
        return await coordinator._async_update_data(), go_on


async def test_the_saved_forecast_fills_the_first_download():
    """Test saved rows fill the first download, without changing its values."""
    coordinator = _coordinator(None)
    coordinator._saved = _forecast()

    data, go_on = await _download(coordinator)

    assert data.current.forecast_time.hour == 17
    # The hours only continue from a download, never from saved values
    assert go_on.call_args.args[1] is None


async def test_without_anything_saved_the_next_row_is_shown():
    """A first download at 17:22 starts at 19:00, and shows that row early."""
    data, _ = await _download(_coordinator(None))

    assert (data.current.forecast_time.hour, data.current.temperature) == (19, 10)
    assert [p.forecast_time.hour for p in data.periods] == [19, 1]


@pytest.mark.parametrize("success", [True, False])
async def test_old_values_only_fill_a_download_after_a_failure(success):
    """Test the hours continue from the last download only if it worked."""
    coordinator = _coordinator(None)
    coordinator.data = _forecast()
    coordinator.last_update_success = success

    data, go_on = await _download(coordinator)

    # The missing rows come back either way
    assert data.current.forecast_time.hour == 17
    assert go_on.called is success


def _failing(saved):
    """A real coordinator whose downloads fail, with `saved` loaded."""
    hass = MagicMock()
    hass.is_stopping = False
    entry = MagicMock(entry_id="e1", pref_disable_polling=False)
    with patch.object(coordinators, "forecast_store"):
        coordinator = coordinators.ForecastCoordinator(hass, entry, DEBRECEN)
    coordinator._saved = saved
    coordinator._listeners = {1: (MagicMock(), None)}
    coordinator._schedule_refresh = MagicMock()
    coordinator.async_update_listeners = MagicMock()
    return coordinator


async def _fail(coordinator, now):
    with (
        patch.object(coordinators, "async_get_clientsession"),
        patch.object(coordinators, "fetch_forecast", AsyncMock(side_effect=TimeoutError())),
        patch.object(coordinators.dt_util, "now", return_value=now),
    ):
        await coordinator.async_refresh()


async def test_failed_downloads_follow_the_saved_forecast_hour_by_hour():
    """Test the saved forecast moves with the hours while downloads fail."""
    coordinator = _failing(_forecast())  # saved at 17:22

    await _fail(coordinator, NOW)

    assert coordinator.last_update_success is False
    assert coordinator.data.current.forecast_time.hour == 17

    coordinator.async_update_listeners.reset_mock()
    await _fail(coordinator, datetime(2026, 10, 28, 18, 0, 59, tzinfo=HUNGARY_TZ))

    current = coordinator.data.current
    assert (current.forecast_time.hour, current.temperature) == (18, 11.0)
    # The entities hear about a repeated failure too
    coordinator.async_update_listeners.assert_called()


async def test_the_followed_forecast_ends_with_its_last_hour():
    """Test there is no current hour or past day once the saved forecast is over."""
    coordinator = _failing(_forecast())

    await _fail(coordinator, datetime(2026, 10, 29, 9, tzinfo=HUNGARY_TZ))

    assert coordinator.data.current is None
    assert not coordinator.data.daily


async def test_a_failed_download_without_a_forecast_shows_nothing():
    """Test there is nothing to follow without a downloaded or saved forecast."""
    coordinator = _failing(None)

    await _fail(coordinator, NOW)

    assert coordinator.data is None
    # Only the coordinator's own news of the first failure
    coordinator.async_update_listeners.assert_called_once()


async def test_a_working_download_is_shown_as_it_is():
    """Test a working download replaces the saved forecast."""
    coordinator = _failing(_forecast())
    coordinator._store.async_save = AsyncMock()
    new = _later_download()
    with (
        patch.object(coordinators, "async_get_clientsession"),
        patch.object(coordinators, "fetch_forecast", AsyncMock(return_value=new)),
        patch.object(coordinators.dt_util, "now", return_value=NOW),
    ):
        await coordinator.async_refresh()

    assert coordinator.last_update_success is True
    assert coordinator.data is new
    coordinator.async_update_listeners.assert_called_once()


async def test_the_saved_forecast_does_not_stop_the_retries():
    """Test the one-minute retries go on while only the saved forecast shows."""
    coordinator = _failing(_forecast())
    await _fail(coordinator, NOW)

    with patch.object(scheduler, "async_call_later") as call_later:
        coordinator.async_retry_after_restart()

    call_later.assert_called_once()


async def test_a_download_finishing_after_the_unload_is_not_saved():
    """Otherwise it would write the file of a removed entry back."""
    coordinator = _coordinator(None)
    store = coordinator._store
    store.async_save = AsyncMock()
    coordinator.hass = MagicMock()
    await coordinator.async_close()

    with (
        patch.object(coordinators, "async_get_clientsession"),
        patch.object(coordinators, "fetch_forecast", AsyncMock(return_value=_forecast())),
    ):
        data = await coordinator._async_update_data()

    assert data.current is not None
    store.async_save.assert_not_awaited()


async def test_closing_waits_for_a_save_under_way():
    """The entry's removal deletes the file only after the save has finished."""
    coordinator = _coordinator(None)
    started, finish = asyncio.Event(), asyncio.Event()

    async def save(_data):
        started.set()
        await finish.wait()

    store = coordinator._store
    store.async_save = AsyncMock(side_effect=save)
    saving = asyncio.create_task(coordinator._async_save(_forecast()))
    await started.wait()

    close = asyncio.create_task(coordinator.async_close())
    await asyncio.sleep(0)
    assert not close.done()

    finish.set()
    await close
    await saving
    store.async_save.assert_awaited_once()


def test_store_is_per_entry():
    """Test store is per entry."""
    with patch.object(coordinators, "Store") as store:
        coordinators.forecast_store("hass", "e1")
    store.assert_called_once_with(
        "hass", coordinators.STORAGE_VERSION, "hungaromet_weather_and_forecast.forecast.e1"
    )


def test_coordinator_creates_its_store():
    """Test coordinator creates its store."""
    entry = SimpleNamespace(entry_id="e1")
    with (
        patch.object(coordinators.ScheduledCoordinator, "__init__", return_value=None),
        patch.object(coordinators, "forecast_store") as store,
    ):
        coordinator = coordinators.ForecastCoordinator("hass", entry, DEBRECEN)
    store.assert_called_once_with("hass", "e1")
    assert coordinator._store is store.return_value


async def test_removing_an_entry_deletes_its_saved_forecast_and_warnings():
    """Test removing an entry deletes its saved forecast and warnings."""
    hass = MagicMock()
    store = MagicMock(async_remove=AsyncMock())
    saved_warnings = MagicMock(async_remove=AsyncMock())
    with (
        patch.object(integration, "forecast_store", return_value=store) as get_store,
        patch.object(
            integration, "warning_store", return_value=saved_warnings
        ) as get_warnings,
    ):
        await integration.async_remove_entry(
            hass, SimpleNamespace(entry_id="e1", data={})
        )

    get_store.assert_called_once_with(hass, "e1")
    store.async_remove.assert_awaited_once()
    get_warnings.assert_called_once_with(hass, "e1")
    saved_warnings.async_remove.assert_awaited_once()
