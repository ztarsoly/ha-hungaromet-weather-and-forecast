"""Tests for choosing a location entry's devices."""

# The tests check the integration's internals
# pylint: disable=protected-access

from dataclasses import asdict
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest
import requests
from homeassistant.exceptions import ConfigEntryNotReady

import custom_components.hungaromet_weather_and_forecast as integration
from custom_components.hungaromet_weather_and_forecast import coordinator as coordinators
from custom_components.hungaromet_weather_and_forecast.data import HungaroMetData
from custom_components.hungaromet_weather_and_forecast.forecast_scraper import Settlement
from custom_components.hungaromet_weather_and_forecast.image import (
    async_setup_entry as async_setup_images,
)
from custom_components.hungaromet_weather_and_forecast.scope import entry_devices
from custom_components.hungaromet_weather_and_forecast.settlement import place

from .entries import loaded_entry

DOMAIN = "hungaromet_weather_and_forecast"
ALL = ("daily", "hourly", "ten_minutes", "regional_radar")
PECS = Settlement(name="Pécs", kod="9", lat=46.0, lon=18.2)


def _location(entry_id="loc", **options):
    return loaded_entry(entry_id, "Home", {}, options)


def _hass(*entries):
    hass = MagicMock()
    hass.config.latitude, hass.config.longitude = 47.53, 21.64  # Debrecen
    hass.config_entries.async_entries.return_value = list(entries)
    hass.async_add_executor_job = AsyncMock(side_effect=lambda func, *args: func(*args))
    return hass


@pytest.mark.parametrize(
    ("options", "devices"),
    [
        # Entries added before the choice have everything
        ({}, ALL),
        ({"devices": []}, ()),
        # In the integration's order, whatever order they were chosen in
        ({"devices": ["regional_radar", "daily"]}, ("daily", "regional_radar")),
    ],
)
def test_entry_devices(options, devices):
    """An entry has the devices chosen for it."""
    assert entry_devices(_location(**options)) == devices


@pytest.mark.parametrize(
    ("settlement", "location", "devices", "expected"),
    [
        (PECS, "settlement", ALL, ("9", "settlement")),
        (None, "home", ["daily"], (None, "home")),
        # The regional radar is measured from the location too
        (PECS, "home", ["regional_radar"], ("9", "home")),
        # A forecast alone measures nowhere
        (PECS, "home", [], ("9", None)),
    ],
)
def test_place(settlement, location, devices, expected):
    """Only an entry with devices measures somewhere."""
    assert place(settlement, location, devices) == expected


# --- what is created -------------------------------------------------------------


async def test_only_the_chosen_feeds_are_set_up():
    """An unchosen feed is not downloaded."""
    hass = _hass()
    entry = _location(devices=["ten_minutes", "regional_radar"])
    with (
        patch.object(coordinators, "FeedCoordinator") as create,
        patch.object(coordinators, "station_query"),
        patch.object(coordinators, "async_register_updater"),
    ):
        create.return_value.async_refresh = AsyncMock()
        feeds = await coordinators.async_setup_feeds(hass, entry)

    assert list(feeds) == ["ten_minutes"]
    create.assert_called_once()


@pytest.mark.parametrize("devices", [["daily"], []])
async def test_an_unchosen_regional_radar_is_not_added(devices):
    """Without the box ticked there is no regional radar image."""
    add = MagicMock()

    await async_setup_images(_hass(), _location(devices=devices), add)

    add.assert_not_called()


@pytest.mark.parametrize(
    ("devices", "forecast", "removed"),
    [
        (["hourly"], PECS, ["hungaromet_weather", "hungaromet_regional_radar"]),
        # The forecast device stays with a settlement, whatever is ticked
        (
            [],
            PECS,
            [
                "hungaromet_weather",
                "hungaromet_weather_hourly",
                "hungaromet_regional_radar",
            ],
        ),
        (list(ALL), PECS, []),
        # Without a settlement it goes
        (list(ALL), None, ["hungaromet_forecast"]),
    ],
)
def test_unused_devices_are_removed(devices, forecast, removed):
    """Their devices go, and with them their entities."""
    # The 10-minute device was never created
    existing = [
        SimpleNamespace(id=name, identifiers={(DOMAIN, f"loc_{name}")})
        for name in (
            "hungaromet_weather",
            "hungaromet_weather_hourly",
            "hungaromet_regional_radar",
            "hungaromet_forecast",
        )
    ]
    registry = MagicMock()
    with (
        patch.object(integration.dr, "async_get", return_value=registry),
        patch.object(
            integration.dr, "async_entries_for_config_entry", return_value=existing
        ) as listed,
    ):
        integration._async_remove_unused_devices(
            _hass(),
            _location(devices=devices, forecast=forecast and asdict(forecast)),
        )

    listed.assert_called_once_with(registry, "loc")
    assert registry.async_remove_device.call_args_list == [
        call(name) for name in removed
    ]


# --- the setup check without station devices -------------------------------------


def _failed_forecast():
    forecast = MagicMock(downloaded=False, has_saved_current_hour=False)
    forecast.async_refresh = AsyncMock()
    forecast.warnings = MagicMock(downloaded=False, async_refresh=AsyncMock())
    return forecast


@pytest.mark.parametrize("with_forecast", [True, False])
async def test_a_regional_radar_alone_is_enough_to_set_up(with_forecast):
    """Without station devices, a downloadable radar image will do."""
    data = HungaroMetData(
        name="Home", forecast=_failed_forecast() if with_forecast else None
    )
    with patch.object(integration, "latest_frame_urls", return_value=["url"]) as find:
        await integration._async_check_setup(
            _hass(), _location(devices=["regional_radar"]), data
        )

    find.assert_called_once_with("ne")


@pytest.mark.parametrize(
    "answer", [[], requests.ConnectionError("offline"), ValueError("bad page")]
)
async def test_a_failing_regional_radar_alone_puts_the_setup_off(answer):
    """The entry is set up again later while there is no image either."""
    behaviour = (
        {"return_value": answer}
        if isinstance(answer, list)
        else {"side_effect": answer}
    )
    with (
        patch.object(integration, "latest_frame_urls", **behaviour),
        pytest.raises(ConfigEntryNotReady, match="radar images cannot be downloaded"),
    ):
        await integration._async_check_setup(
            _hass(),
            _location(devices=["regional_radar"]),
            HungaroMetData(name="Home"),
        )


@pytest.mark.parametrize(
    ("devices", "feeds"),
    [
        # With station devices the radar image is not checked
        (list(ALL), {"daily": MagicMock(downloaded=False)}),
        # An unchosen radar image is not checked either
        ([], {}),
    ],
)
async def test_the_regional_radar_is_checked_only_without_station_devices(
    devices, feeds
):
    """Otherwise the downloads of the stations and the forecast decide."""
    data = HungaroMetData(name="Home", forecast=_failed_forecast(), feeds=feeds)
    with (
        patch.object(integration, "latest_frame_urls") as find,
        pytest.raises(ConfigEntryNotReady, match="Nothing could be downloaded"),
    ):
        await integration._async_check_setup(
            _hass(), _location(devices=devices), data
        )

    find.assert_not_called()
