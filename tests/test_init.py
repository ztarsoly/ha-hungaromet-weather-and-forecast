"""Tests for __init__.py"""

import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import requests
from homeassistant.exceptions import ConfigEntryNotReady

from custom_components.hungaromet_weather_and_forecast import (
    async_setup,
    async_setup_entry,
    async_unload_entry,
    _async_reload_entry,
)
import custom_components.hungaromet_weather_and_forecast as integration
from custom_components.hungaromet_weather_and_forecast.radar import remove_saved_gifs
from custom_components.hungaromet_weather_and_forecast.stations import FEEDS

from .entries import loaded_entry

_LOGGER = logging.getLogger("hungaromet_test")


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


def _coordinator(downloaded=False, works=False):
    """A forecast or warning coordinator whose download works or not."""
    coordinator = MagicMock(downloaded=downloaded, has_saved_current_hour=False)
    coordinator.schedule.async_run_now = AsyncMock()

    async def refresh():
        coordinator.downloaded = works

    coordinator.async_refresh = AsyncMock(side_effect=refresh)
    return coordinator


def _forecast(**kwargs):
    forecast = _coordinator(**kwargs)
    forecast.warnings = _coordinator(**kwargs)
    return forecast


@pytest.fixture
def forecast_setup():
    """Replace the forecast setup with a stub returning a fake coordinator."""
    coordinator = _forecast()
    with patch.object(
        integration, "async_setup_forecast", AsyncMock(return_value=coordinator)
    ) as setup:
        yield setup, coordinator


@pytest.fixture(autouse=True)
def feeds():
    """Replace the setup of the station feeds with fake, downloaded ones."""
    coordinators = {key: MagicMock(downloaded=True) for key in FEEDS}
    with patch.object(
        integration, "async_setup_feeds", AsyncMock(return_value=coordinators)
    ) as setup:
        yield SimpleNamespace(coordinators=coordinators, setup=setup)


def _all_fail(feeds):
    for coordinator in feeds.coordinators.values():
        coordinator.downloaded = False


@pytest.fixture(autouse=True)
def radar_frame():
    """Replace the radar entry's check of the newest frame with a working one."""
    with patch.object(integration, "newest_frame_downloads", return_value=True) as check:
        yield check


def _hass():
    hass = MagicMock()
    hass.config_entries.async_forward_entry_setups = AsyncMock(return_value=True)
    hass.async_add_executor_job = AsyncMock(side_effect=lambda func, *args: func(*args))
    return hass


def _entry(entry_id="abc", data=None):
    return MagicMock(entry_id=entry_id, title="Home", data=data or {}, options={})


async def test_async_setup_entry(forecast_setup, feeds, radar_frame):
    """Test async_setup_entry sets up the forecast and forwards to platforms."""
    setup, coordinator = forecast_setup
    hass = _hass()
    entry = _entry()

    assert await async_setup_entry(hass, entry) is True

    setup.assert_awaited_once_with(hass, entry)
    assert entry.runtime_data.forecast is coordinator
    assert entry.runtime_data.feeds is feeds.coordinators
    feeds.setup.assert_awaited_once_with(hass, entry)
    # Each feed is also tried every minute until its first download works
    for feed in feeds.coordinators.values():
        feed.async_retry_after_restart.assert_called_once()
        entry.async_on_unload.assert_any_call(feed.async_retry_after_restart.return_value)
    # Only the radar entry waits for the radar
    radar_frame.assert_not_called()
    hass.config_entries.async_forward_entry_setups.assert_awaited_once_with(
        entry, ["sensor", "image", "weather"]
    )
    # The first forecast and warnings downloads come once the entities are added
    coordinator.schedule.async_run_now.assert_awaited_once()
    coordinator.warnings.schedule.async_run_now.assert_awaited_once()
    # Both are also tried every minute until their first download works
    coordinator.async_retry_after_restart.assert_called_once()
    coordinator.warnings.async_retry_after_restart.assert_called_once()
    # Nothing is downloaded just to check the setup
    coordinator.async_refresh.assert_not_awaited()


@pytest.mark.parametrize("put_off", [True, False])
async def test_download_failures_are_warnings_only_once_the_setup_goes_on(
    forecast_setup, feeds, caplog, put_off
):
    """A put off setup logs at debug level only: Home Assistant says why."""
    caplog.set_level(logging.DEBUG)
    setup, _ = forecast_setup
    setup.return_value = None
    if put_off:
        _all_fail(feeds)
    entry = _entry()

    async def set_up(*_args):
        entry.runtime_data.failures.warn(_LOGGER, "Download failed")
        return feeds.coordinators

    feeds.setup.side_effect = set_up

    if put_off:
        with pytest.raises(ConfigEntryNotReady):
            await async_setup_entry(_hass(), entry)
    else:
        await async_setup_entry(_hass(), entry)

    logged = [r.levelname for r in caplog.records if r.name == _LOGGER.name]
    assert logged == (["DEBUG"] if put_off else ["DEBUG", "WARNING"])


async def test_async_setup_entry_without_forecast(forecast_setup):
    """Test async setup entry without forecast."""
    setup, _ = forecast_setup
    setup.return_value = None
    entry = _entry()

    assert await async_setup_entry(_hass(), entry) is True
    assert entry.runtime_data.forecast is None


async def test_async_setup_entry_reloads_on_options_change(forecast_setup):
    """Test async_setup_entry registers a reload listener."""
    entry = _entry()

    await async_setup_entry(_hass(), entry)

    entry.add_update_listener.assert_called_once_with(_async_reload_entry)
    # The name it was set up with, to spot a renamed entry
    assert entry.runtime_data.name == "Home"
    entry.async_on_unload.assert_any_call(
        entry.add_update_listener.return_value
    )


@pytest.mark.parametrize("with_forecast", [True, False])
async def test_setup_is_tried_again_when_nothing_can_be_downloaded(
    forecast_setup, feeds, with_forecast
):
    """Without any download or saved forecast there is nothing to show."""
    setup, coordinator = forecast_setup
    if not with_forecast:
        setup.return_value = None
    _all_fail(feeds)
    hass = _hass()
    entry = _entry()

    with pytest.raises(ConfigEntryNotReady, match="Nothing could be downloaded"):
        await async_setup_entry(hass, entry)

    hass.config_entries.async_forward_entry_setups.assert_not_awaited()
    entry.add_update_listener.assert_not_called()
    if with_forecast:
        coordinator.async_refresh.assert_awaited_once()
        coordinator.warnings.async_refresh.assert_awaited_once()


async def test_one_working_feed_is_enough_to_set_up(forecast_setup, feeds):
    """The entry loads if only one station feed can be downloaded."""
    _, coordinator = forecast_setup
    _all_fail(feeds)
    feeds.coordinators["hourly"].downloaded = True

    assert await async_setup_entry(_hass(), _entry()) is True

    coordinator.async_refresh.assert_not_awaited()


async def test_a_saved_forecast_is_enough_to_set_up(forecast_setup, feeds):
    """A saved forecast can be shown while nothing can be downloaded."""
    _, coordinator = forecast_setup
    coordinator.has_saved_current_hour = True
    _all_fail(feeds)

    assert await async_setup_entry(_hass(), _entry()) is True

    coordinator.async_refresh.assert_not_awaited()
    coordinator.schedule.async_run_now.assert_awaited_once()


@pytest.mark.parametrize("working", ["forecast", "warnings"])
async def test_one_working_download_is_enough_to_set_up(
    forecast_setup, feeds, working
):
    """The entry loads if only the forecast or the warnings can be downloaded."""
    setup, _ = forecast_setup
    forecast = _forecast()
    source = forecast if working == "forecast" else forecast.warnings

    async def refresh():
        source.downloaded = True

    source.async_refresh.side_effect = refresh
    setup.return_value = forecast
    _all_fail(feeds)

    assert await async_setup_entry(_hass(), _entry()) is True

    # What was downloaded for the check is not downloaded again right away
    other = forecast.warnings if working == "forecast" else forecast
    source.schedule.async_run_now.assert_not_awaited()
    other.schedule.async_run_now.assert_awaited_once()


@pytest.mark.parametrize("unloaded", [True, False])
async def test_async_unload_entry(unloaded):
    """Test async_unload_entry unloads the platforms."""
    hass = MagicMock()
    entry = loaded_entry("abc", "Home", {})
    hass.config_entries.async_unload_platforms = AsyncMock(return_value=unloaded)

    assert await async_unload_entry(hass, entry) is unloaded
    hass.config_entries.async_unload_platforms.assert_awaited_once_with(
        entry, ["sensor", "image", "weather"]
    )
    # Nothing is logged after an unload; a loaded entry goes on logging
    assert entry.runtime_data.failures.closed is unloaded


async def test_reload_entry_reloads_by_id():
    """Test the update listener reloads the config entry."""
    hass = MagicMock()
    hass.config_entries.async_reload = AsyncMock()
    entry = loaded_entry("abc", "Home")

    await _async_reload_entry(hass, entry)

    hass.config_entries.async_reload.assert_awaited_once_with("abc")


def _radar_entry(entry_id="radar"):
    return MagicMock(entry_id=entry_id, title="Radar", data={"entry_type": "radar"})


async def test_radar_entry_sets_up_only_the_image(forecast_setup, feeds, radar_frame):
    """The radar entry has only the national radar image and no forecast."""
    setup, _ = forecast_setup
    hass = _hass()
    entry = _radar_entry()

    assert await async_setup_entry(hass, entry) is True

    setup.assert_not_awaited()
    feeds.setup.assert_not_awaited()
    radar_frame.assert_called_once_with(
        "https://odp.met.hu/weather/radar/composite/png/refl2D_pscappi/"
    )
    assert entry.runtime_data.forecast is None
    hass.config_entries.async_forward_entry_setups.assert_awaited_once_with(
        entry, ["image"]
    )
    hass.config_entries.flow.async_init.assert_not_called()


@pytest.mark.parametrize(
    ("answer", "reason"),
    [
        (requests.ConnectionError("offline"), ": offline"),
        (requests.HTTPError("503 Server Error"), ": 503 Server Error"),
        (OSError("cannot identify image file"), ": cannot identify image file"),
        (ValueError("bad listing"), ": bad listing"),
        # Nothing listed, or the newest frame does not download
        (False, ""),
    ],
)
async def test_radar_setup_is_tried_again_without_a_radar_frame(
    radar_frame, caplog, answer, reason
):
    """The radar entry is put off while it cannot get an image; HA says why."""
    caplog.set_level(logging.DEBUG)
    if answer is False:
        radar_frame.return_value = False
    else:
        radar_frame.side_effect = answer
    hass = _hass()

    with pytest.raises(ConfigEntryNotReady) as error:
        await async_setup_entry(hass, _radar_entry())

    assert str(error.value) == f"HungaroMet's radar images cannot be downloaded{reason}"
    hass.config_entries.async_forward_entry_setups.assert_not_awaited()
    assert not [r for r in caplog.records if r.levelno > logging.DEBUG]


@pytest.mark.parametrize("radar_exists", [True, False])
async def test_a_location_entry_never_creates_the_radar_entry(
    forecast_setup, radar_exists
):
    """Only the setup form adds it, so a deleted one stays deleted."""
    hass = _hass()
    location = _entry("loc")
    hass.config_entries.async_entries.return_value = [
        location, *([_radar_entry()] if radar_exists else [])
    ]

    await async_setup_entry(hass, location)

    hass.config_entries.flow.async_init.assert_not_called()
    hass.async_create_task.assert_not_called()


async def test_radar_entry_unloads_only_the_image():
    """Test radar entry unloads only the image."""
    hass = MagicMock()
    hass.config_entries.async_unload_platforms = AsyncMock(return_value=True)
    entry = _radar_entry()

    assert await async_unload_entry(hass, entry) is True
    hass.config_entries.async_unload_platforms.assert_awaited_once_with(
        entry, ["image"]
    )


def _device(device_id, name_by_user=None):
    return SimpleNamespace(id=device_id, name_by_user=name_by_user)


async def test_renamed_entry_renames_the_devices_the_user_named():
    """Test renamed entry renames the devices the user named."""
    hass = MagicMock()
    hass.config_entries.async_reload = AsyncMock()
    entry = loaded_entry("abc", "Home")
    entry.title = "Cottage"
    devices = [
        _device("default"),  # renamed by the reload, from the translations
        _device("named", "Home kert hőmérő"),
        _device("own", "Kert"),
    ]
    dev_reg = MagicMock()
    with (
        patch.object(integration.dr, "async_get", return_value=dev_reg),
        patch.object(integration.dr, "async_entries_for_config_entry", return_value=devices),
    ):
        await _async_reload_entry(hass, entry)
        # Another change before the reload is done renames nothing again
        await _async_reload_entry(hass, entry)

    dev_reg.async_update_device.assert_called_once_with(
        "named", name_by_user="Cottage kert hőmérő"
    )
    assert entry.runtime_data.name == "Cottage"
    assert hass.config_entries.async_reload.await_count == 2


async def test_unchanged_name_renames_nothing():
    """Test unchanged name renames nothing."""
    hass = MagicMock()
    hass.config_entries.async_reload = AsyncMock()
    with patch.object(integration.dr, "async_get") as get_registry:
        await _async_reload_entry(hass, loaded_entry("abc", "Home"))

    get_registry.assert_not_called()
