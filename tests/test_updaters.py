"""Tests for the update service and what it refreshes."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.hungaromet_weather_and_forecast import updaters
from custom_components.hungaromet_weather_and_forecast import weather as weather_platform
from custom_components.hungaromet_weather_and_forecast.forecast_scraper import (
    ForecastData,
)
from custom_components.hungaromet_weather_and_forecast.forecast_sensor import (
    forecast_sensors,
)

ENTRY = SimpleNamespace(entry_id="e1", title="HungaroMet")


def test_register_and_remove_updaters():
    hass = SimpleNamespace(data={})
    first, second = AsyncMock(), AsyncMock()

    remove_first = updaters.async_register_updater(hass, "a", first)
    updaters.async_register_updater(hass, "b", second)
    assert hass.data[updaters.UPDATERS] == {"a": first, "b": second}

    remove_first()
    remove_first()  # removing twice is harmless
    assert hass.data[updaters.UPDATERS] == {"b": second}


def test_removing_does_not_drop_a_newer_updater_with_the_same_key():
    hass = SimpleNamespace(data={})
    old, new = AsyncMock(), AsyncMock()
    remove_old = updaters.async_register_updater(hass, "radar", old)
    updaters.async_register_updater(hass, "radar", new)  # e.g. after a reload

    remove_old()

    assert hass.data[updaters.UPDATERS] == {"radar": new}


@pytest.mark.asyncio
async def test_update_all_runs_everything_even_after_a_failure(caplog):
    hass = SimpleNamespace(data={})
    feeds = AsyncMock(side_effect=RuntimeError("met.hu down"))
    forecast = AsyncMock()
    radar = AsyncMock()
    for key, update in (("feeds", feeds), ("forecast", forecast), ("radar", radar)):
        updaters.async_register_updater(hass, key, update)

    await updaters.async_update_all(hass)

    feeds.assert_awaited_once()
    forecast.assert_awaited_once()
    radar.assert_awaited_once()
    assert "Updating feeds failed" in caplog.text


@pytest.mark.asyncio
async def test_update_all_with_nothing_registered():
    await updaters.async_update_all(SimpleNamespace(data={}))


@pytest.mark.asyncio
async def test_update_service_runs_every_updater():
    hass = MagicMock(data={})
    update = AsyncMock()
    updaters.async_register_updater(hass, "x", update)

    updaters.async_register_update_service(hass)

    domain, service, handler = hass.services.async_register.call_args.args
    assert (domain, service) == ("hungaromet_weather_and_forecast", "update")
    await handler(None)
    update.assert_awaited_once()


@pytest.mark.asyncio
async def test_setup_registers_the_update_service():
    from custom_components.hungaromet_weather_and_forecast import async_setup

    hass = MagicMock(async_add_executor_job=AsyncMock())
    with patch(
        "custom_components.hungaromet_weather_and_forecast.async_register_update_service"
    ) as register:
        assert await async_setup(hass, {}) is True
    register.assert_called_once_with(hass)


def _coordinator(data, success):
    return MagicMock(data=data, last_update_success=success, config_entry=ENTRY)


@pytest.mark.parametrize(
    ("data", "success", "available"),
    [
        (ForecastData(settlement="X"), True, True),
        # A failed download keeps the last forecast showing
        (ForecastData(settlement="X"), False, True),
        # Nothing downloaded yet
        (None, False, False),
    ],
)
def test_forecast_entities_keep_their_last_values(data, success, available):
    coordinator = _coordinator(data, success)
    assert all(s.available is available for s in forecast_sensors(coordinator))

    with patch.object(
        weather_platform.SingleCoordinatorWeatherEntity, "__init__", return_value=None
    ):
        entity = weather_platform.HungarometForecastWeather(coordinator)
    entity.coordinator = coordinator
    assert entity.available is available
