"""Tests for running several config entries side by side."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

import custom_components.hungaromet_weather_and_forecast as integration
from custom_components.hungaromet_weather_and_forecast.scope import (
    EntryScope,
    device_info,
    scoped_id,
)
from custom_components.hungaromet_weather_and_forecast.sensor import StationSensor, StationsSensor
from custom_components.hungaromet_weather_and_forecast.stations import (
    Measurement,
    StationQuery,
)

from .entries import feed_coordinator

SCOPE = EntryScope("e1", "HungaroMet Pécs")
QUERY = StationQuery(scope=SCOPE)


def _sensor(feed, name, key, unit):
    return StationSensor(feed_coordinator(feed, QUERY), Measurement(name, key, unit))


def test_scope_from_entry_and_ids():
    """Test scope from entry and ids."""
    entry = SimpleNamespace(entry_id="e1", title="HungaroMet Pécs")
    assert EntryScope.from_entry(entry) == SCOPE
    assert scoped_id(SCOPE, "hungaromet_weather") == "e1_hungaromet_weather"
    assert scoped_id(None, "hungaromet_weather") == "hungaromet_weather"


def test_device_info_with_and_without_scope():
    """Test device info with and without scope."""
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
            _sensor("daily", "Napi párolgás", "upe", "mm"),
            "e1_hungaromet_weather_napi_párolgás",
            "e1_hungaromet_weather",
        ),
        (
            _sensor("hourly", "Órás t", "t", "°C"),
            "e1_hungaromet_weather_hourly_órás_t",
            "e1_hungaromet_weather_hourly",
        ),
        (
            _sensor("ten_minutes", "T", "t", "°C"),
            "e1_hungaromet_weather_ten_minutes_t",
            "e1_hungaromet_weather_ten_minutes",
        ),
        (
            StationsSensor(feed_coordinator("hourly", QUERY)),
            "e1_hungaromet_weather_station_info_hourly",
            "e1_hungaromet_weather_hourly",
        ),
    ],
)
def test_sensors_are_scoped_to_their_entry(entity, unique_id, device_id):
    """Test sensors are scoped to their entry."""
    assert entity.unique_id == unique_id
    assert entity.device_info["identifiers"] == {("hungaromet_weather_and_forecast", device_id)}
    assert entity.device_info["translation_placeholders"] == {"name": "HungaroMet Pécs"}


def _registry_entry(unique_id, domain="sensor", config_entry_id="e1"):
    return SimpleNamespace(
        unique_id=unique_id, domain=domain, config_entry_id=config_entry_id
    )


def _config_entry(version=2, minor_version=1, data=None, options=None):
    return SimpleNamespace(
        entry_id="e1",
        version=version,
        minor_version=minor_version,
        data=data or {},
        options=options or {},
    )


def _device(device_id, identifiers):
    return SimpleNamespace(id=device_id, identifiers=identifiers)


@pytest.fixture
def registries():
    """Fake entity and device registries for the migration."""
    devices = {
        "d1": _device("d1", {("hungaromet_weather_and_forecast", "hungaromet_weather")}),
        "radar": _device("radar", {("hungaromet_weather_and_forecast", "hungaromet_radar_gif")}),
        "shared": _device(
            "shared", {("hungaromet_weather_and_forecast", "hungaromet_weather_hourly")}
        ),
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


async def test_migration_scopes_version_1_entities_and_devices(registries):
    """Test migration scopes version 1 entities and devices."""
    migrate, dev_reg = registries
    hass = MagicMock()
    entry = _config_entry(version=1, data={"distance_km": 30})

    assert await integration.async_migrate_entry(hass, entry) is True

    assert hass.config_entries.async_update_entry.call_args_list == [
        call(entry, version=2),
        call(entry, data={}, options={"distance_km": 30}, minor_version=2),
    ]
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


async def test_migration_of_current_and_future_versions(registries):
    """Test migration of current and future versions."""
    migrate, _ = registries
    hass = MagicMock()

    current = _config_entry(minor_version=2)
    assert await integration.async_migrate_entry(hass, current) is True
    future = _config_entry(version=3)
    assert await integration.async_migrate_entry(hass, future) is False
    migrate.assert_not_awaited()
    hass.config_entries.async_update_entry.assert_not_called()


async def test_migration_removes_the_dropped_sensors():
    """Version 2.2 has no water balance, and global radiation in J/cm² only."""
    entities = [
        SimpleNamespace(entity_id=f"sensor.{number}", unique_id=unique_id)
        for number, unique_id in enumerate((
            "e1_hungaromet_weather_napi_globálsugárzás_összeg",
            "e1_hungaromet_weather_napi_globálsugárzás_összeg_(mj/m²)",
            "e1_hungaromet_weather_hourly_órás_globálsugárzás_összeg_(mj/m²)",
            "e1_hungaromet_weather_ten_minutes_tízperces_globálsugárzás_összeg_(mj/m²)",
            "e1_hungaromet_weather_ten_minutes_tízperces_globálsugárzás_összeg",
            "e1_hungaromet_weather_napi_vízegyenleg",
            "e1_hungaromet_weather_napi_párolgás",
        ))
    ]
    with (
        patch.object(integration.er, "async_get") as get_registry,
        patch.object(
            integration.er, "async_entries_for_config_entry", return_value=entities
        ) as listed,
    ):
        hass = MagicMock()
        assert await integration.async_migrate_entry(hass, _config_entry()) is True

    listed.assert_called_once_with(get_registry.return_value, "e1")
    removed = get_registry.return_value.async_remove.call_args_list
    assert removed == [call(f"sensor.{number}") for number in (1, 2, 3, 5)]


_SETTINGS = {"distance_km": 30.0, "forecast": None, "location": "home"}


@pytest.mark.parametrize(
    ("data", "options", "new_data", "new_options"),
    [
        # An entry that was never configured again
        ({"name": "Pécs", **_SETTINGS}, {}, {"name": "Pécs"}, _SETTINGS),
        # Settings saved with the Configure form are newer than the setup's
        (
            {"name": "Pécs", **_SETTINGS},
            {"distance_km": 50.0, "forecast": None, "location": "home"},
            {"name": "Pécs"},
            {"distance_km": 50.0, "forecast": None, "location": "home"},
        ),
        # The radar entry has no settings
        ({"entry_type": "radar"}, {}, {"entry_type": "radar"}, {}),
    ],
)
async def test_migration_moves_the_settings_to_the_options(
    data, options, new_data, new_options
):
    """Version 2.2 keeps an entry's settings in its options."""
    hass = MagicMock()
    entry = _config_entry(data=data, options=options)

    assert await integration.async_migrate_entry(hass, entry) is True

    hass.config_entries.async_update_entry.assert_called_once_with(
        entry, data=new_data, options=new_options, minor_version=2
    )
