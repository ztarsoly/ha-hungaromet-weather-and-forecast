"""Tests for saving the last forecast so it survives a restart."""

# The tests check the integration's internals
# pylint: disable=protected-access

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import custom_components.hungaromet_weather_and_forecast as integration
from custom_components.hungaromet_weather_and_forecast import forecast
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
    coordinator = object.__new__(forecast.ForecastCoordinator)
    coordinator.settlement = DEBRECEN
    coordinator.data = None
    coordinator._store = MagicMock(async_load=AsyncMock(return_value=stored))
    return coordinator


@pytest.mark.parametrize("key", ["code", "kod"])  # "kod" up to 1.3.4
async def test_load_stored_forecast(key):
    """Test load stored forecast."""
    stored = {key: "725", "forecast": forecast_to_dict(_forecast())}
    coordinator = _coordinator(stored)

    with patch.object(forecast.dt_util, "now", return_value=NOW):
        await coordinator.async_load_stored()

    assert coordinator.data.settlement == "Debrecen"
    assert coordinator.data.current.forecast_time.hour == 17
    # The 13:00 row is sunny, but the sun has set by 17:00 at the settlement
    assert coordinator.data.current.weather_condition == "clear-night"


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
    assert coordinator.data is None


async def test_load_ignores_unreadable_forecast(caplog):
    """Test load ignores unreadable forecast."""
    coordinator = _coordinator({"code": "725", "forecast": {"periods": []}})
    await coordinator.async_load_stored()
    assert coordinator.data is None
    assert "Ignoring the saved forecast" in caplog.text


async def test_each_download_is_saved():
    """Test each download is saved."""
    coordinator = _coordinator(None)
    coordinator.hass = MagicMock()
    coordinator._store.async_save = AsyncMock()
    with (
        patch.object(forecast, "async_get_clientsession"),
        patch.object(forecast, "fetch_forecast", AsyncMock(return_value=_forecast())),
    ):
        data = await coordinator._async_update_data()

    coordinator._store.async_save.assert_awaited_once_with(
        {"code": "725", "forecast": forecast_to_dict(data)}
    )


def _failing_coordinator(data):
    """A coordinator whose downloads fail, showing `data`."""
    coordinator = _coordinator(None)
    coordinator.data = data
    coordinator._listeners = {1: (MagicMock(), None)}
    coordinator.async_refresh = AsyncMock()
    coordinator.last_update_success = False
    coordinator.async_update_listeners = MagicMock()
    return coordinator


async def test_a_failed_download_moves_on_to_the_current_hour():
    """Test a failed download moves on to the current hour of the saved forecast.

    Without it the sensors would stay on the hour of the last download.
    """
    coordinator = _failing_coordinator(_forecast())
    assert coordinator.data.current.forecast_time.hour == 17
    later = datetime(2026, 10, 28, 18, 0, 59, tzinfo=HUNGARY_TZ)

    with patch.object(forecast.dt_util, "now", return_value=later):
        assert await coordinator._async_scheduled_download(None) is False

    current = coordinator.data.current
    assert (current.forecast_time.hour, current.temperature) == (18, 11.0)
    coordinator.async_update_listeners.assert_called_once()


async def test_a_working_download_does_not_move_the_hour_itself():
    """Test a working download leaves the hour to the downloaded forecast."""
    coordinator = _failing_coordinator(_forecast())
    coordinator.last_update_success = True

    assert await coordinator._async_scheduled_download(None) is True

    coordinator.async_update_listeners.assert_not_called()


async def test_a_failed_download_without_a_forecast_changes_nothing():
    """Test a failed download without a forecast changes nothing."""
    coordinator = _failing_coordinator(None)

    assert await coordinator._async_scheduled_download(None) is False

    assert coordinator.data is None
    coordinator.async_update_listeners.assert_not_called()


def test_store_is_per_entry():
    """Test store is per entry."""
    with patch.object(forecast, "Store") as store:
        forecast.forecast_store("hass", "e1")
    store.assert_called_once_with(
        "hass", forecast.STORAGE_VERSION, "hungaromet_weather_and_forecast.forecast.e1"
    )


def test_coordinator_creates_its_store():
    """Test coordinator creates its store."""
    entry = SimpleNamespace(entry_id="e1")
    with (
        patch.object(forecast.ScheduledCoordinator, "__init__", return_value=None),
        patch.object(forecast, "forecast_store") as store,
    ):
        coordinator = forecast.ForecastCoordinator("hass", entry, DEBRECEN)
    store.assert_called_once_with("hass", "e1")
    assert coordinator._store is store.return_value


async def test_removing_an_entry_deletes_everything_it_saved():
    """Test removing an entry deletes its saved forecast, warnings and station values."""
    hass = MagicMock()
    stores = {
        name: MagicMock(async_remove=AsyncMock())
        for name in ("forecast_store", "warning_store", "station_store")
    }
    with (
        patch.object(integration, "forecast_store", return_value=stores["forecast_store"]),
        patch.object(integration, "warning_store", return_value=stores["warning_store"]),
        patch.object(integration, "station_store", return_value=stores["station_store"]),
    ):
        await integration.async_remove_entry(hass, SimpleNamespace(entry_id="e1"))

    for store in stores.values():
        store.async_remove.assert_awaited_once()
