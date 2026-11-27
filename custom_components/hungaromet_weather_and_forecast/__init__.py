"""HungaroMet Weather & Forecast: weather station sensors, a settlement forecast
and radar images from HungaroMet."""

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.typing import ConfigType

from .const import (
    CONF_DISTANCE_KM,
    CONF_FORECAST,
    CONF_LOCATION,
    DEVICE_REGIONAL_RADAR,
    DOMAIN,
    FORECAST_DEVICE_ID,
    RADAR_BASE_URL,
    REGIONAL_DEVICE_ID,
)
from .coordinator import (
    async_setup_feeds,
    async_setup_forecast,
    forecast_store,
    warning_store,
)
from .data import HungaroMetConfigEntry, HungaroMetData
from .national_radar import newest_frame_downloads
from .radar import remove_saved_gifs
from .regional_radar import latest_frame_urls, quadrant_for
from .scope import EntryScope, entry_devices, is_radar_entry, scoped_id
from .services import async_setup_services
from .settlement import entry_coordinates, stored_settlement
from .stations import FEEDS

PLATFORMS = ["sensor", "image", "weather"]
# The national radar entry only has the national radar image
RADAR_PLATFORMS = ["image"]

# The national radar image is shared, so its device stays unscoped
RADAR_DEVICE_ID = "hungaromet_radar_gif"
# The identifiers of the devices a location entry can be set up without
CHOSEN_DEVICE_IDS = {
    **{key: feed.device_id for key, feed in FEEDS.items()},
    DEVICE_REGIONAL_RADAR: REGIONAL_DEVICE_ID,
}

# What entries added before version 2.2 kept in their data instead of options
SETTINGS = (CONF_DISTANCE_KM, CONF_FORECAST, CONF_LOCATION)
# How the unique IDs end of the sensors version 2.2 no longer has: the global
# radiation in MJ/m², which repeated that in J/cm², and the daily water balance
DROPPED_SENSOR_SUFFIXES = ("_globálsugárzás_összeg_(mj/m²)", "_napi_vízegyenleg")

# Entries are only added in the UI
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)  # pylint: disable=invalid-name

def _platforms(entry: ConfigEntry) -> list[str]:
    return RADAR_PLATFORMS if is_radar_entry(entry) else PLATFORMS


@callback
def _async_remove_unused_devices(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Delete the devices, with their entities, that the entry no longer has.

    Those are the unticked ones, and the forecast's without a settlement.
    """
    chosen = entry_devices(entry)
    unused = [
        device_id
        for key, device_id in CHOSEN_DEVICE_IDS.items()
        if key not in chosen
    ]
    if stored_settlement(entry) is None:
        unused.append(FORECAST_DEVICE_ID)
    scope = EntryScope.from_entry(entry)
    identifiers = {(DOMAIN, scoped_id(scope, device_id)) for device_id in unused}
    registry = dr.async_get(hass)
    for device in dr.async_entries_for_config_entry(registry, entry.entry_id):
        if identifiers & device.identifiers:
            registry.async_remove_device(device.id)


async def async_setup(hass: HomeAssistant, _config: ConfigType) -> bool:
    """Register the update action and delete radar files older versions saved."""
    async_setup_services(hass)
    # The radar animations are kept in memory; delete the ones saved before
    await hass.async_add_executor_job(
        remove_saved_gifs, hass.config.path(".storage", DOMAIN)
    )
    return True


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Migrate older entries to the current version."""
    if entry.version > 2:
        return False
    if entry.version == 1:
        await _async_scope_registry(hass, entry)
        hass.config_entries.async_update_entry(entry, version=2)
    if entry.minor_version < 2:
        _async_remove_dropped_sensors(hass, entry)
        _async_move_settings_to_options(hass, entry)
    return True


@callback
def _async_remove_dropped_sensors(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Delete the entities of the sensors that version 2.2 no longer has."""
    registry = er.async_get(hass)
    for entity in er.async_entries_for_config_entry(registry, entry.entry_id):
        if entity.unique_id.endswith(DROPPED_SENSOR_SUFFIXES):
            registry.async_remove(entity.entity_id)


@callback
def _async_move_settings_to_options(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Move an entry's settings from its data to its options (version 2.2).

    Settings already saved in the options are newer, so they are kept.
    """
    data = dict(entry.data)
    settings = {key: data.pop(key) for key in SETTINGS if key in data}
    hass.config_entries.async_update_entry(
        entry, data=data, options={**settings, **entry.options}, minor_version=2
    )


async def _async_scope_registry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Prefix a version 1 entry's unique IDs and device identifiers with its ID.

    Version 1 used the same IDs for every entry, so a second entry's entities
    collided with the first one's. Entity IDs, names and history are kept.
    """
    prefix = f"{entry.entry_id}_"

    @callback
    def scope_entity(entity_entry: er.RegistryEntry):
        if entity_entry.domain == "image" or entity_entry.unique_id.startswith(prefix):
            return None
        return {"new_unique_id": prefix + entity_entry.unique_id}

    await er.async_migrate_entries(hass, entry.entry_id, scope_entity)

    dev_reg = dr.async_get(hass)
    ent_reg = er.async_get(hass)
    for device in dr.async_entries_for_config_entry(dev_reg, entry.entry_id):
        # A device can also be linked to an empty duplicate entry; only the entry
        # that owns its entities rewrites it
        owned = er.async_entries_for_device(
            ent_reg, device.id, include_disabled_entities=True
        )
        if not any(e.config_entry_id == entry.entry_id for e in owned):
            continue
        identifiers = {
            (domain, prefix + value)
            if domain == DOMAIN
            and value != RADAR_DEVICE_ID
            and not value.startswith(prefix)
            else (domain, value)
            for domain, value in device.identifiers
        }
        if identifiers != device.identifiers:
            dev_reg.async_update_device(device.id, new_identifiers=identifiers)


async def async_setup_entry(
    hass: HomeAssistant, entry: HungaroMetConfigEntry
) -> bool:
    """Set up the national radar entry, or a location entry and its forecast."""
    data = entry.runtime_data = HungaroMetData(name=entry.title)
    if is_radar_entry(entry):
        await _async_check_radar(hass, newest_frame_downloads, RADAR_BASE_URL)
        await hass.config_entries.async_forward_entry_setups(entry, RADAR_PLATFORMS)
        return True

    # Even when this entry has to be set up again later
    _async_remove_unused_devices(hass, entry)
    # No warnings until it is clear that the setup is not put off
    data.failures.hold()
    # The forecast coordinator is shared by the sensor and weather platforms;
    # it is None when no forecast settlement is configured
    data.forecast = await async_setup_forecast(hass, entry)
    data.feeds = await async_setup_feeds(hass, entry)
    await _async_check_setup(hass, entry, data)
    data.failures.release()

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    for feed in data.feeds.values():
        # A feed whose download failed is also tried every minute until one works
        entry.async_on_unload(feed.async_retry_after_restart())
    if data.forecast is not None:
        for coordinator in (data.forecast, data.forecast.warnings):
            # Now that its entities are added; a failed download is retried, and
            # must not stop the weather station sensors from loading
            if not coordinator.downloaded:
                await coordinator.schedule.async_run_now()
            entry.async_on_unload(coordinator.async_retry_after_restart())
    entry.async_on_unload(entry.add_update_listener(_async_reload_entry))
    return True


async def _async_check_radar(hass: HomeAssistant, find_frames, source: str) -> None:
    """Have an entry set up again later while it cannot get a radar frame."""
    try:
        frames = await hass.async_add_executor_job(find_frames, source)
    except (OSError, ValueError) as err:
        raise ConfigEntryNotReady(
            f"HungaroMet's radar images cannot be downloaded: {err}"
        ) from err
    if not frames:
        raise ConfigEntryNotReady("HungaroMet's radar images cannot be downloaded")


async def _async_check_setup(
    hass: HomeAssistant, entry: HungaroMetConfigEntry, data: HungaroMetData
) -> None:
    """Have the entry set up again later if it would have nothing to show.

    That is when every download fails and no saved forecast covers the current
    hour. Otherwise the entry loads, and retries what failed by itself. The
    regional radar image is checked only for an entry without station devices.
    """
    if any(feed.downloaded for feed in data.feeds.values()):
        return
    if (forecast := data.forecast) is not None:
        if forecast.has_saved_current_hour:
            return
        for coordinator in (forecast, forecast.warnings):
            await coordinator.async_refresh()
            if coordinator.downloaded:
                return
    if not data.feeds and DEVICE_REGIONAL_RADAR in entry_devices(entry):
        quadrant = quadrant_for(*entry_coordinates(hass, entry))
        await _async_check_radar(hass, latest_frame_urls, quadrant)
        return
    raise ConfigEntryNotReady("Nothing could be downloaded from HungaroMet")


async def async_unload_entry(
    hass: HomeAssistant, entry: HungaroMetConfigEntry
) -> bool:
    """Unload an entry's platforms."""
    unloaded = await hass.config_entries.async_unload_platforms(
        entry, _platforms(entry)
    )
    if unloaded:
        # A download that finishes after this is not logged
        entry.runtime_data.failures.closed = True
    return unloaded


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Delete the entry's saved forecast, alerts and warnings."""
    await forecast_store(hass, entry.entry_id).async_remove()
    await warning_store(hass, entry.entry_id).async_remove()


async def _async_reload_entry(
    hass: HomeAssistant, entry: HungaroMetConfigEntry
) -> None:
    """Reload the entry so changed options or a new name take effect."""
    old_name = entry.runtime_data.name
    if old_name != entry.title:
        _async_rename_devices(hass, entry, old_name)
    # Recorded now, as another change can call this before the reload is done
    entry.runtime_data.name = entry.title
    await hass.config_entries.async_reload(entry.entry_id)


@callback
def _async_rename_devices(hass: HomeAssistant, entry: ConfigEntry, old_name: str) -> None:
    """Put an entry's new name into the device names the user gave.

    Devices with their default name ("{name} Daily Weather") get the new name
    when the entry reloads. A name the user gave is changed only where it holds
    the old entry name.
    """
    dev_reg = dr.async_get(hass)
    for device in dr.async_entries_for_config_entry(dev_reg, entry.entry_id):
        if device.name_by_user and old_name in device.name_by_user:
            dev_reg.async_update_device(
                device.id, name_by_user=device.name_by_user.replace(old_name, entry.title)
            )
