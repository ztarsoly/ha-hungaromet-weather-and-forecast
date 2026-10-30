"""Tests for __init__.py"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.hungaromet_weather_and_forecast import (
    async_setup,
    async_setup_entry,
    async_unload_entry,
    _async_reload_entry,
)
from custom_components.hungaromet_weather_and_forecast.radar import (
    remove_saved_gifs,
)


@pytest.mark.asyncio
async def test_async_setup():
    """Test async_setup returns True."""
    hass = MagicMock()
    hass.async_add_executor_job = AsyncMock()
    hass.config.path = lambda *parts: "/".join(("/config", *parts))
    config = {}

    result = await async_setup(hass, config)

    assert result is True
    # The radar animations saved to disk by earlier versions are removed
    hass.async_add_executor_job.assert_awaited_once_with(
        remove_saved_gifs, "/config/.storage/hungaromet_weather_and_forecast"
    )


@pytest.fixture
def forecast_setup():
    """Replace the forecast setup with a stub returning a fake coordinator."""
    coordinator = MagicMock()
    coordinator.schedule.async_run_now = AsyncMock()
    with patch(
        "custom_components.hungaromet_weather_and_forecast.async_setup_forecast",
        AsyncMock(return_value=coordinator),
    ) as setup:
        yield setup, coordinator


@pytest.mark.asyncio
async def test_async_setup_entry(forecast_setup):
    """Test async_setup_entry sets up the forecast and forwards to platforms."""
    setup, coordinator = forecast_setup
    hass = MagicMock()
    hass.data = {}
    entry = MagicMock(entry_id="abc")

    hass.config_entries.async_forward_entry_setups = AsyncMock(return_value=True)

    result = await async_setup_entry(hass, entry)

    assert result is True
    setup.assert_awaited_once_with(hass, entry)
    assert hass.data["hungaromet_weather_and_forecast"]["abc"] is coordinator
    hass.config_entries.async_forward_entry_setups.assert_awaited_once_with(
        entry, ["sensor", "image", "weather"]
    )
    # The first forecast download comes once the entities are added
    coordinator.schedule.async_run_now.assert_awaited_once()


@pytest.mark.asyncio
async def test_async_setup_entry_without_forecast(forecast_setup):
    setup, _ = forecast_setup
    setup.return_value = None
    hass = MagicMock(data={})
    hass.config_entries.async_forward_entry_setups = AsyncMock(return_value=True)

    assert await async_setup_entry(hass, MagicMock(entry_id="abc")) is True
    assert hass.data["hungaromet_weather_and_forecast"]["abc"] is None


@pytest.mark.asyncio
async def test_async_setup_entry_reloads_on_options_change(forecast_setup):
    """Test async_setup_entry registers a reload listener."""
    hass = MagicMock()
    hass.data = {}
    entry = MagicMock()
    hass.config_entries.async_forward_entry_setups = AsyncMock(return_value=True)

    await async_setup_entry(hass, entry)

    entry.add_update_listener.assert_called_once_with(_async_reload_entry)
    entry.async_on_unload.assert_called_once_with(
        entry.add_update_listener.return_value
    )


@pytest.mark.asyncio
async def test_async_unload_entry():
    """Test async_unload_entry unloads the platforms."""
    hass = MagicMock()
    hass.data = {"hungaromet_weather_and_forecast": {"abc": "coordinator"}}
    entry = MagicMock(entry_id="abc")
    hass.config_entries.async_unload_platforms = AsyncMock(return_value=True)

    assert await async_unload_entry(hass, entry) is True
    hass.config_entries.async_unload_platforms.assert_awaited_once_with(
        entry, ["sensor", "image", "weather"]
    )
    assert hass.data["hungaromet_weather_and_forecast"] == {}


@pytest.mark.asyncio
async def test_failed_unload_keeps_the_forecast():
    """A failed unload leaves the forecast coordinator in place."""
    hass = MagicMock()
    hass.data = {"hungaromet_weather_and_forecast": {"abc": "coordinator"}}
    entry = MagicMock(entry_id="abc")
    hass.config_entries.async_unload_platforms = AsyncMock(return_value=False)

    assert await async_unload_entry(hass, entry) is False
    assert hass.data["hungaromet_weather_and_forecast"] == {"abc": "coordinator"}


@pytest.mark.asyncio
async def test_reload_entry_reloads_by_id():
    """Test the update listener reloads the config entry."""
    hass = MagicMock()
    entry = MagicMock(entry_id="abc")
    hass.config_entries.async_reload = AsyncMock()

    await _async_reload_entry(hass, entry)

    hass.config_entries.async_reload.assert_awaited_once_with("abc")


def _radar_entry(entry_id="radar"):
    return MagicMock(entry_id=entry_id, data={"entry_type": "radar"})


@pytest.mark.asyncio
async def test_radar_entry_sets_up_only_the_image(forecast_setup):
    """The radar entry has only the national radar image and no forecast."""
    setup, _ = forecast_setup
    hass = MagicMock()
    hass.data = {}
    hass.config_entries.async_forward_entry_setups = AsyncMock(return_value=True)
    entry = _radar_entry()

    assert await async_setup_entry(hass, entry) is True

    setup.assert_not_awaited()
    hass.config_entries.async_forward_entry_setups.assert_awaited_once_with(
        entry, ["image"]
    )
    hass.config_entries.flow.async_init.assert_not_called()


@pytest.mark.asyncio
async def test_location_entry_creates_the_missing_radar_entry(forecast_setup):
    """Without a radar entry, a location entry creates it in the background."""
    hass = MagicMock()
    hass.data = {}
    hass.config_entries.async_forward_entry_setups = AsyncMock(return_value=True)
    location = MagicMock(entry_id="loc", data={})
    hass.config_entries.async_entries.return_value = [location]

    await async_setup_entry(hass, location)

    hass.config_entries.flow.async_init.assert_called_once_with(
        "hungaromet_weather_and_forecast",
        context={"source": "import"},
        data={"entry_type": "radar"},
    )
    hass.async_create_task.assert_called_once_with(
        hass.config_entries.flow.async_init.return_value
    )


@pytest.mark.asyncio
async def test_existing_radar_entry_is_not_created_again(forecast_setup):
    """An existing radar entry, even a disabled one, is left alone."""
    hass = MagicMock()
    hass.data = {}
    hass.config_entries.async_forward_entry_setups = AsyncMock(return_value=True)
    location = MagicMock(entry_id="loc", data={})
    hass.config_entries.async_entries.return_value = [location, _radar_entry()]

    await async_setup_entry(hass, location)

    hass.config_entries.flow.async_init.assert_not_called()
    hass.async_create_task.assert_not_called()


@pytest.mark.asyncio
async def test_radar_entry_unloads_only_the_image():
    hass = MagicMock()
    hass.data = {}
    hass.config_entries.async_unload_platforms = AsyncMock(return_value=True)
    entry = _radar_entry()

    assert await async_unload_entry(hass, entry) is True
    hass.config_entries.async_unload_platforms.assert_awaited_once_with(
        entry, ["image"]
    )
