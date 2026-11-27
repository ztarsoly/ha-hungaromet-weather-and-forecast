"""An entry's forecast settlement, and where its weather sensors measure from."""

import logging
from dataclasses import asdict

import aiohttp
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
    CONF_FORECAST,
    CONF_FORECAST_SETTLEMENT,
    CONF_LOCATION,
    DOMAIN,
    LOCATION_HOME,
    LOCATION_SETTLEMENT,
)
from .forecast_scraper import Settlement, lookup_settlement
from .scope import entry_devices, is_radar_entry

_LOGGER = logging.getLogger(__package__)


def stored_settlement(entry: ConfigEntry) -> Settlement | None:
    """Return the configured forecast settlement.

    It is in the options; an entry not migrated yet, such as a disabled one, still
    has it in its data.
    """
    if CONF_FORECAST in entry.options:
        data = entry.options[CONF_FORECAST]
    else:
        data = entry.data.get(CONF_FORECAST)
    return Settlement(**data) if data else None


def sensor_location(entry: ConfigEntry) -> tuple[float, float] | None:
    """Return the settlement's coordinates if the sensors should use them.

    None means the weather station sensors use Home Assistant's home location.
    """
    settlement = stored_settlement(entry)
    if _location_choice(entry) != LOCATION_SETTLEMENT or settlement is None:
        return None
    return settlement.lat, settlement.lon


def _location_choice(entry: ConfigEntry) -> str:
    """Return the chosen sensor location; options win over setup data."""
    if CONF_LOCATION in entry.options:
        return entry.options[CONF_LOCATION]
    return entry.data.get(CONF_LOCATION, LOCATION_HOME)


def coordinates(
    hass: HomeAssistant, settlement: Settlement | None, location: str
) -> tuple[float, float]:
    """Return where these settings measure from: the settlement, or home."""
    if location == LOCATION_SETTLEMENT and settlement is not None:
        return settlement.lat, settlement.lon
    return hass.config.latitude, hass.config.longitude


def entry_coordinates(hass: HomeAssistant, entry: ConfigEntry) -> tuple[float, float]:
    """Return where a location entry measures from."""
    return coordinates(hass, stored_settlement(entry), _location_choice(entry))


def place(
    settlement: Settlement | None, location: str, devices
) -> tuple[str | None, str | None]:
    """Return the forecast settlement and where the entry's devices measure from.

    The settlement is its code, or None without a forecast. The location is None
    without station and regional radar devices, as nothing is measured then.
    """
    kod = settlement.kod if settlement else None
    if not devices:
        return kod, None
    return kod, location if settlement else LOCATION_HOME


def entry_place(entry: ConfigEntry) -> tuple[str | None, str | None]:
    """Return the place of a location entry."""
    return place(
        stored_settlement(entry), _location_choice(entry), entry_devices(entry)
    )


def place_conflicts(
    hass: HomeAssistant,
    wanted: tuple[str | None, str | None],
    except_entry_id: str | None = None,
) -> dict[str, str]:
    """Return the form errors for what a place shares with another entry's.

    A forecast settlement belongs to one entry, and only one entry's devices
    measure at Home: entries sharing either would repeat each other's entities.
    """
    kod, location = wanted
    others = _other_places(hass, except_entry_id)
    errors = {}
    if kod is not None and any(kod == other for other, _ in others):
        errors[CONF_FORECAST_SETTLEMENT] = "settlement_in_use"
    if location == LOCATION_HOME and any(other == location for _, other in others):
        errors[CONF_LOCATION] = "home_in_use"
    return errors


def _other_places(
    hass: HomeAssistant, except_entry_id: str | None
) -> list[tuple[str | None, str | None]]:
    """Return the places of the location entries other than the given one."""
    return [
        entry_place(entry)
        for entry in hass.config_entries.async_entries(DOMAIN)
        if not is_radar_entry(entry) and entry.entry_id != except_entry_id
    ]


def settlement_to_dict(settlement: Settlement | None) -> dict | None:
    """Return a settlement in the form stored in the config entry."""
    return asdict(settlement) if settlement else None


async def async_resolve_settlement(hass: HomeAssistant, name: str | None):
    """Look a typed settlement name up on met.hu.

    Returns (settlement, error key); an empty name means no forecast: (None, None).
    """
    name = (name or "").strip()
    if not name:
        return None, None
    try:
        settlement = await lookup_settlement(async_get_clientsession(hass), name)
    except (aiohttp.ClientError, TimeoutError) as err:
        _LOGGER.error(str(err) or type(err).__name__)
        return None, "cannot_connect"
    except ValueError as err:
        _LOGGER.error("met.hu's search gave an unreadable answer: %s", err)
        return None, "invalid_response"
    if settlement is None:
        return None, "settlement_not_found"
    return settlement, None
