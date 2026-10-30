from homeassistant.config_entries import SOURCE_IMPORT, ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.typing import ConfigType

from .const import CONF_ENTRY_TYPE, DOMAIN, ENTRY_TYPE_RADAR
from .forecast import async_setup_forecast, forecast_store
from .radar import remove_saved_gifs
from .scope import is_radar_entry
from .updaters import async_register_update_service

PLATFORMS = ["sensor", "image", "weather"]
# The national radar entry only has the national radar image
RADAR_PLATFORMS = ["image"]

# The national radar image is shared, so its device stays unscoped
RADAR_DEVICE_ID = "hungaromet_radar_gif"


def _platforms(entry: ConfigEntry) -> list[str]:
    return RADAR_PLATFORMS if is_radar_entry(entry) else PLATFORMS


@callback
def _async_ensure_radar_entry(hass: HomeAssistant) -> None:
    """Create the national radar entry in the background if it does not exist.

    A deleted radar entry comes back the next time a location entry starts; a
    disabled one still exists, so it stays disabled.
    """
    if any(is_radar_entry(e) for e in hass.config_entries.async_entries(DOMAIN)):
        return
    hass.async_create_task(
        hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": SOURCE_IMPORT},
            data={CONF_ENTRY_TYPE: ENTRY_TYPE_RADAR},
        )
    )


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    async_register_update_service(hass)
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
    return True


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


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    if is_radar_entry(entry):
        await hass.config_entries.async_forward_entry_setups(entry, RADAR_PLATFORMS)
        return True

    # The forecast coordinator is shared by the sensor and weather platforms;
    # it is None when no forecast settlement is configured
    forecast = await async_setup_forecast(hass, entry)
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = forecast
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    if forecast is not None:
        # Now that its entities are added; a failed download is retried, and
        # must not stop the weather station sensors from loading
        await forecast.schedule.async_run_now()
    entry.async_on_unload(entry.add_update_listener(_async_reload_entry))
    _async_ensure_radar_entry(hass)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(
        entry, _platforms(entry)
    )
    if unloaded:
        hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    return unloaded


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Delete the entry's saved forecast."""
    await forecast_store(hass, entry.entry_id).async_remove()


async def _async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the entry so changed options take effect."""
    await hass.config_entries.async_reload(entry.entry_id)
