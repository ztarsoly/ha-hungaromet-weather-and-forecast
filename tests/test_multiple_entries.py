"""Tests for running several config entries side by side."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import custom_components.hungaromet_weather_and_forecast as integration
from custom_components.hungaromet_weather_and_forecast.daily_sensor import HungarometWeatherDailySensor
from custom_components.hungaromet_weather_and_forecast.hourly_sensor import HungarometWeatherHourlySensor
from custom_components.hungaromet_weather_and_forecast.scope import EntryScope, device_info, scoped_id
from custom_components.hungaromet_weather_and_forecast.station_info_sensor import (
    HungarometStationInfoSensor,
)
from custom_components.hungaromet_weather_and_forecast.ten_minute_sensor import (
    HungarometWeatherTenMinutesSensor,
)

SCOPE = EntryScope("e1", "HungaroMet Pécs")


def test_scope_from_entry_and_ids():
    entry = SimpleNamespace(entry_id="e1", title="HungaroMet Pécs")
    assert EntryScope.from_entry(entry) == SCOPE
    assert scoped_id(SCOPE, "hungaromet_weather") == "e1_hungaromet_weather"
    assert scoped_id(None, "hungaromet_weather") == "hungaromet_weather"


def test_device_info_with_and_without_scope():
    scoped = device_info(SCOPE, "hungaromet_weather", "daily", "Daily weather")
    assert scoped["identifiers"] == {("hungaromet_weather_and_forecast", "e1_hungaromet_weather")}
    assert scoped["translation_placeholders"] == {"name": "HungaroMet Pécs"}

    plain = device_info(None, "hungaromet_weather", "daily", "Daily weather")
    assert plain["identifiers"] == {("hungaromet_weather_and_forecast", "hungaromet_weather")}
    assert "translation_placeholders" not in plain


@pytest.mark.parametrize(
    ("entity", "unique_id", "device_id"),
    [
        (
            HungarometWeatherDailySensor(None, "Napi párolgás", 0, "mm", "upe", scope=SCOPE),
            "e1_hungaromet_weather_napi_párolgás",
            "e1_hungaromet_weather",
        ),
        (
            HungarometWeatherHourlySensor(None, "Órás t", 0, "°C", "t", scope=SCOPE),
            "e1_hungaromet_weather_hourly_órás_t",
            "e1_hungaromet_weather_hourly",
        ),
        (
            HungarometWeatherTenMinutesSensor(None, "T", 0, "°C", "t", scope=SCOPE),
            "e1_hungaromet_weather_ten_minutes_t",
            "e1_hungaromet_weather_ten_minutes",
        ),
        (
            HungarometStationInfoSensor(None, "S", [], "hourly", scope=SCOPE),
            "e1_hungaromet_weather_station_info_hourly",
            "e1_hungaromet_weather_hourly",
        ),
    ],
)
def test_sensors_are_scoped_to_their_entry(entity, unique_id, device_id):
    assert entity.unique_id == unique_id
    assert entity.device_info["identifiers"] == {("hungaromet_weather_and_forecast", device_id)}
    assert entity.device_info["translation_placeholders"] == {"name": "HungaroMet Pécs"}


def _registry_entry(unique_id, domain="sensor", config_entry_id="e1"):
    return SimpleNamespace(
        unique_id=unique_id, domain=domain, config_entry_id=config_entry_id
    )


def _device(device_id, identifiers):
    return SimpleNamespace(id=device_id, identifiers=identifiers)


@pytest.fixture
def registries():
    """Fake entity and device registries for the migration."""
    devices = {
        "d1": _device("d1", {("hungaromet_weather_and_forecast", "hungaromet_weather")}),
        "radar": _device("radar", {("hungaromet_weather_and_forecast", "hungaromet_radar_gif")}),
        "shared": _device("shared", {("hungaromet_weather_and_forecast", "hungaromet_weather_hourly")}),
        "done": _device("done", {("hungaromet_weather_and_forecast", "e1_hungaromet_forecast")}),
    }
    owners = {
        "d1": [_registry_entry("x")],
        "radar": [_registry_entry("r", "image")],
        "shared": [_registry_entry("y", config_entry_id="other")],
        "done": [_registry_entry("z")],
    }
    dev_reg = MagicMock()
    with (
        patch.object(integration.er, "async_migrate_entries", AsyncMock()) as migrate,
        patch.object(integration.er, "async_get"),
        patch.object(
            integration.er,
            "async_entries_for_device",
            side_effect=lambda reg, device_id, include_disabled_entities: owners[device_id],
        ),
        patch.object(integration.dr, "async_get", return_value=dev_reg),
        patch.object(
            integration.dr,
            "async_entries_for_config_entry",
            return_value=list(devices.values()),
        ),
    ):
        yield migrate, dev_reg


@pytest.mark.asyncio
async def test_migration_scopes_version_1_entities_and_devices(registries):
    migrate, dev_reg = registries
    hass = MagicMock()
    entry = SimpleNamespace(entry_id="e1", version=1)

    assert await integration.async_migrate_entry(hass, entry) is True

    hass.config_entries.async_update_entry.assert_called_once_with(entry, version=2)
    # Entities: prefixed, except the shared radar and already migrated ones
    scope_entity = migrate.await_args.args[2]
    assert scope_entity(_registry_entry("hungaromet_weather_napi_t")) == {
        "new_unique_id": "e1_hungaromet_weather_napi_t"
    }
    assert scope_entity(_registry_entry("hungaromet_radar_gif_x", "image")) is None
    assert scope_entity(_registry_entry("e1_hungaromet_forecast_weather")) is None
    # Devices: only the one this entry owns and that is not scoped yet changes
    dev_reg.async_update_device.assert_called_once_with(
        "d1", new_identifiers={("hungaromet_weather_and_forecast", "e1_hungaromet_weather")}
    )


@pytest.mark.asyncio
async def test_migration_of_current_and_future_versions(registries):
    migrate, _ = registries
    hass = MagicMock()

    assert await integration.async_migrate_entry(hass, SimpleNamespace(version=2)) is True
    assert await integration.async_migrate_entry(hass, SimpleNamespace(version=3)) is False
    migrate.assert_not_awaited()
    hass.config_entries.async_update_entry.assert_not_called()

