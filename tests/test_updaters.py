"""Tests for the update action and what it refreshes."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.exceptions import ServiceValidationError

from custom_components.hungaromet_weather_and_forecast import (
    async_setup,
    services,
    updaters,
)
from custom_components.hungaromet_weather_and_forecast import weather as weather_platform
from custom_components.hungaromet_weather_and_forecast.forecast_scraper import (
    ForecastData,
    ForecastPeriod,
)
from custom_components.hungaromet_weather_and_forecast.sensor import forecast_sensors

from .entries import loaded_entry

ENTRY = SimpleNamespace(entry_id="e1", title="HungaroMet")


def _entry(state=ConfigEntryState.LOADED):
    entry = loaded_entry()
    entry.state = state
    return entry


def _hass(*entries):
    hass = MagicMock()
    hass.config_entries.async_entries.return_value = list(entries)
    return hass


def test_register_and_remove_updaters():
    """Test register and remove updaters."""
    entry = _entry()
    first, second = AsyncMock(), AsyncMock()

    remove_first = updaters.async_register_updater(entry, "a", first)
    updaters.async_register_updater(entry, "b", second)
    assert entry.runtime_data.updaters == {"a": first, "b": second}

    remove_first()
    remove_first()  # removing twice is harmless
    assert entry.runtime_data.updaters == {"b": second}


def test_removing_does_not_drop_a_newer_updater_with_the_same_key():
    """Test removing does not drop a newer updater with the same key."""
    entry = _entry()
    old, new = AsyncMock(), AsyncMock()
    remove_old = updaters.async_register_updater(entry, "radar", old)
    updaters.async_register_updater(entry, "radar", new)  # e.g. added again

    remove_old()

    assert entry.runtime_data.updaters == {"radar": new}


async def test_update_all_runs_everything_even_after_a_failure(caplog):
    """Test update all runs every loaded entry's updates, even after a failure."""
    first, second = _entry(), _entry()
    feeds = AsyncMock(side_effect=RuntimeError("met.hu down"))
    forecast = AsyncMock()
    radar = AsyncMock()
    updaters.async_register_updater(first, "feeds", feeds)
    updaters.async_register_updater(first, "forecast", forecast)
    updaters.async_register_updater(second, "radar", radar)
    hass = _hass(first, second)

    await updaters.async_update_all(hass)

    hass.config_entries.async_entries.assert_called_once_with(
        "hungaromet_weather_and_forecast"
    )
    feeds.assert_awaited_once()
    forecast.assert_awaited_once()
    radar.assert_awaited_once()
    assert "Updating feeds failed" in caplog.text


async def test_update_all_leaves_out_entries_that_are_not_loaded():
    """An entry that is not loaded has nothing to update."""
    # Without runtime data, like a disabled entry
    disabled = SimpleNamespace(state=ConfigEntryState.NOT_LOADED)

    await updaters.async_update_all(_hass(disabled))
    await updaters.async_update_all(_hass())


def _update_action(hass):
    """Register the integration's actions; return the update action."""
    services.async_setup_services(hass)
    domain, service, handler = hass.services.async_register.call_args.args
    assert (domain, service) == ("hungaromet_weather_and_forecast", "update")
    return handler


async def test_update_action_runs_every_updater():
    """Test the update action runs every updater."""
    entry = _entry()
    update = AsyncMock()
    updaters.async_register_updater(entry, "x", update)

    await _update_action(_hass(entry))(None)

    update.assert_awaited_once()


async def test_update_action_fails_without_a_loaded_entry():
    """With no loaded entry the action reports that nothing can be updated."""
    disabled = SimpleNamespace(state=ConfigEntryState.NOT_LOADED)

    with pytest.raises(ServiceValidationError) as error:
        await _update_action(_hass(disabled))(None)

    assert error.value.translation_domain == "hungaromet_weather_and_forecast"
    assert error.value.translation_key == "no_loaded_entry"


async def test_setup_registers_the_actions():
    """Test setup registers the actions."""
    hass = MagicMock(async_add_executor_job=AsyncMock())
    with patch(
        "custom_components.hungaromet_weather_and_forecast.async_setup_services"
    ) as register:
        assert await async_setup(hass, {}) is True
    register.assert_called_once_with(hass)


def _coordinator(data, success):
    return MagicMock(data=data, last_update_success=success, config_entry=ENTRY)


_FORECAST = ForecastData(
    settlement="X",
    current=ForecastPeriod(temperature=5.0, weather_condition="sunny"),
)


@pytest.mark.parametrize(
    ("data", "success", "available"),
    [
        (_FORECAST, True, True),
        # A failed download leaves the last forecast
        (_FORECAST, False, True),
        # Nothing downloaded yet
        (None, True, False),
        # A download without the current hour
        (ForecastData(settlement="X"), True, False),
    ],
)
def test_forecast_entities_are_unavailable_without_a_download_or_value(
    data, success, available
):
    """Test forecast entities are unavailable without a download or a value."""
    coordinator = _coordinator(data, success)
    sensors = {s.entity_description.key: s for s in forecast_sensors(coordinator)}
    assert sensors["temperature"].available is available
    assert sensors["precipitation"].available is False  # no value

    with patch.object(
        weather_platform.SingleCoordinatorWeatherEntity, "__init__", return_value=None
    ):
        entity = weather_platform.HungarometForecastWeather(coordinator)
    entity.coordinator = coordinator
    assert entity.available is available
    if not available:
        # No old forecast either
        # pylint: disable=protected-access
        assert entity._async_forecast_daily() is None
        assert entity._async_forecast_hourly() is None
