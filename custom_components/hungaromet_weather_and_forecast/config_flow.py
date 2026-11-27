"""Config flow for Hungaromet Weather."""

import logging

import voluptuous as vol
from homeassistant.config_entries import (
    SOURCE_IMPORT,
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
)
from homeassistant.const import CONF_NAME
from homeassistant.core import callback

from .const import (
    CONF_DEVICES,
    CONF_DISTANCE_KM,
    CONF_ENTRY_TYPE,
    CONF_FORECAST,
    CONF_FORECAST_SETTLEMENT,
    CONF_LOCATION,
    CONF_NATIONAL_RADAR,
    DEFAULT_DISTANCE_KM,
    DEVICES,
    DOMAIN,
    ENTRY_TYPE_RADAR,
    LOCATION_HOME,
    RADAR_BASE_URL,
    RADAR_ENTRY_TITLE,
    RADAR_ENTRY_UNIQUE_ID,
    URL_DAILY,
    URL_HOURLY,
    URL_TEN_MINUTES,
)
from .forecast_scraper import Settlement
from .national_radar import newest_frame_downloads
from .options_flow import (
    DISTANCE_SELECTOR,
    LOCATION_SELECTOR,
    HungaroMetOptionsFlowHandler,
    async_validate_forecast,
    devices_field,
)
from .regional_radar import latest_frame_urls, quadrant_for
from .scope import is_radar_entry
from .settlement import coordinates, place, place_conflicts, settlement_to_dict
from .stations import EXPECTED_ERRORS
from .weather_data import fetch_data

_LOGGER = logging.getLogger(__package__)


# The measurement feeds by their device, the smallest first
FEED_URLS = {
    "ten_minutes": URL_TEN_MINUTES,
    "hourly": URL_HOURLY,
    "daily": URL_DAILY,
}


async def _async_source_error(hass, devices, point) -> str | None:
    """Download from a chosen source; return the form error if it fails.

    One measurement feed is enough; without stations, the regional radar image
    of the point is checked. A forecast alone needs no check, as looking its
    settlement up has reached met.hu.
    """
    url = next((url for key, url in FEED_URLS.items() if key in devices), None)
    source = "measurements" if url else "radar images"
    try:
        if url:
            await hass.async_add_executor_job(fetch_data, url)
        elif devices and not await hass.async_add_executor_job(
            latest_frame_urls, quadrant_for(*point)
        ):
            raise ValueError("No radar images are listed")
    except EXPECTED_ERRORS as err:
        _LOGGER.error("Reading HungaroMet's %s failed: %s", source, err)
        return "cannot_connect"
    return None


async def _async_radar_error(hass) -> str | None:
    """Download the newest national radar frame; return the form error if it fails."""
    try:
        if not await hass.async_add_executor_job(
            newest_frame_downloads, RADAR_BASE_URL
        ):
            raise ValueError("The newest frame cannot be downloaded")
    except EXPECTED_ERRORS as err:
        _LOGGER.error("Reading HungaroMet's %s failed: %s", "radar images", err)
        return "cannot_connect"
    return None


def _has_radar(hass) -> bool:
    """Return True if there is a national radar entry, a disabled one included."""
    return any(
        entry.unique_id == RADAR_ENTRY_UNIQUE_ID or is_radar_entry(entry)
        for entry in hass.config_entries.async_entries(DOMAIN)
    )


def _radar_field(hass, checked: bool) -> dict:
    """Return the radar checkbox, left out while there is a radar entry."""
    if _has_radar(hass):
        return {}
    # A plain bool is shown as a checkbox, a BooleanSelector as a switch
    return {vol.Optional(CONF_NATIONAL_RADAR, default=checked): bool}


@callback
def _async_add_radar(hass) -> None:
    """Add the national radar entry in the background if there is none."""
    if _has_radar(hass):
        return
    hass.async_create_task(
        hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_IMPORT}
        )
    )


class HungarometConfigFlow(ConfigFlow, domain=DOMAIN):
    """Config flow for Hungaromet Weather."""

    # Version 2 scopes unique IDs and devices to the entry (see async_migrate_entry)
    VERSION = 2
    # Version 2.2 keeps the settings in the entry's options, and has no water
    # balance sensor and no global radiation sensors in MJ/m²
    MINOR_VERSION = 2

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: ConfigEntry,
    ) -> HungaroMetOptionsFlowHandler:
        """Return the options flow handler."""
        return HungaroMetOptionsFlowHandler()

    @classmethod
    @callback
    def async_supports_options_flow(cls, config_entry: ConfigEntry) -> bool:
        """Only location entries have settings; the radar entry has none."""
        return not is_radar_entry(config_entry)

    async def async_step_import(self, _import_data=None) -> ConfigFlowResult:
        """Create the national radar entry; the setup form starts this."""
        return await self._async_create_radar_entry()

    async def _async_create_radar_entry(self) -> ConfigFlowResult:
        """Create the national radar entry, unless there is one."""
        await self.async_set_unique_id(RADAR_ENTRY_UNIQUE_ID)
        self._abort_if_unique_id_configured()
        return self.async_create_entry(
            title=RADAR_ENTRY_TITLE, data={CONF_ENTRY_TYPE: ENTRY_TYPE_RADAR}
        )

    async def async_step_user(self, user_input=None):
        """Show the setup form, and create a location entry from it.

        Without a radar entry the form has a checkbox that adds one too, or only
        that one if the name and the settlement are empty and no device is ticked.
        """
        errors = {}
        user_input = user_input or {}
        if user_input:
            name = user_input.get(CONF_NAME, "").strip()
            location = user_input.get(CONF_LOCATION, LOCATION_HOME)
            devices = user_input.get(CONF_DEVICES, [])
            # Not sent by a form that had no checkbox
            add_radar = user_input.get(CONF_NATIONAL_RADAR, False)
            forecast = user_input.get(CONF_FORECAST_SETTLEMENT, "").strip()
            if add_radar and not (name or forecast or devices):
                if error := await _async_radar_error(self.hass):
                    return self._show_user_form(user_input, {"base": error})
                return await self._async_create_radar_entry()
            settlement, errors = await async_validate_forecast(self.hass, user_input)
            # The longer message also tells how to add only the radar
            if errors.get(CONF_DEVICES) and not _has_radar(self.hass):
                errors[CONF_DEVICES] = "nothing_or_radar"
            if not name:
                errors[CONF_NAME] = "name_required"
            if not errors:
                errors = await self._async_new_entry_errors(
                    settlement, location, devices
                )
            if not errors:
                if add_radar:
                    _async_add_radar(self.hass)
                return self.async_create_entry(
                    title=name,
                    data={CONF_NAME: name},
                    options={
                        CONF_DISTANCE_KM: user_input.get(
                            CONF_DISTANCE_KM, DEFAULT_DISTANCE_KM
                        ),
                        CONF_FORECAST: settlement_to_dict(settlement),
                        CONF_LOCATION: location,
                        CONF_DEVICES: devices,
                    },
                )
        return self._show_user_form(user_input, errors)

    def _show_user_form(
        self, user_input: dict, errors: dict[str, str]
    ) -> ConfigFlowResult:
        """Show the setup form, holding what was sent."""
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({
                # No default name; keep what was typed when the form is shown
                # again with an error. Empty only to add the radar alone
                vol.Optional(
                    CONF_NAME,
                    description={"suggested_value": user_input.get(CONF_NAME)},
                ): str,
                vol.Required(
                    CONF_DISTANCE_KM,
                    default=user_input.get(CONF_DISTANCE_KM, DEFAULT_DISTANCE_KM),
                ): DISTANCE_SELECTOR,
                vol.Optional(
                    CONF_FORECAST_SETTLEMENT,
                    default=user_input.get(CONF_FORECAST_SETTLEMENT, ""),
                ): str,
                vol.Required(
                    CONF_LOCATION,
                    default=user_input.get(CONF_LOCATION, LOCATION_HOME),
                ): LOCATION_SELECTOR,
                # Everything is ticked until the form is sent
                **devices_field(
                    user_input.get(CONF_DEVICES, []) if user_input else DEVICES
                ),
                **_radar_field(self.hass, user_input.get(CONF_NATIONAL_RADAR, True)),
            }),
            errors=errors,
        )

    async def _async_new_entry_errors(
        self, settlement: Settlement | None, location: str, devices: list[str]
    ) -> dict[str, str]:
        """Return the form errors that keep a new entry from being added."""
        if errors := self._duplicate_errors(settlement, location, devices):
            return errors
        point = coordinates(self.hass, settlement, location)
        if error := await _async_source_error(self.hass, devices, point):
            return {"base": error}
        # Again: another form may have added an entry during the download
        return self._duplicate_errors(settlement, location, devices)

    def _duplicate_errors(
        self, settlement: Settlement | None, location: str, devices: list[str]
    ) -> dict[str, str]:
        """Abort if an entry has these settings; return what it shares with one."""
        self._async_abort_entries_match({
            CONF_FORECAST: settlement_to_dict(settlement),
            CONF_LOCATION: location,
        })
        return place_conflicts(self.hass, place(settlement, location, devices))

    def is_matching(self, other_flow: "HungarometConfigFlow") -> bool:
        """Return True if another flow is considered a duplicate."""
        return self.source == other_flow.source
