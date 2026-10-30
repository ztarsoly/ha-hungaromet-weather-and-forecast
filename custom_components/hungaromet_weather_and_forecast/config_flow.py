"""Config flow for Hungaromet Weather."""

import logging

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigFlow
from homeassistant.const import CONF_NAME
from homeassistant.core import callback

from .const import (
    CONF_DISTANCE_KM,
    CONF_ENTRY_TYPE,
    CONF_FORECAST,
    CONF_FORECAST_SETTLEMENT,
    CONF_LOCATION,
    DEFAULT_DISTANCE_KM,
    DOMAIN,
    ENTRY_TYPE_RADAR,
    LOCATION_HOME,
    RADAR_ENTRY_TITLE,
    RADAR_ENTRY_UNIQUE_ID,
)
from .forecast import settlement_to_dict
from .options_flow import (
    LOCATION_SELECTOR,
    HungaroMetOptionsFlowHandler,
    async_validate_forecast,
)
from .scope import is_radar_entry

_LOGGER = logging.getLogger(__name__)


class HungarometConfigFlow(ConfigFlow, domain=DOMAIN):
    """Config flow for Hungaromet Weather."""

    # Version 2 scopes unique IDs and devices to the entry (see async_migrate_entry)
    VERSION = 2

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

    async def async_step_import(self, import_data):
        """Create the national radar entry; location entries start this."""
        await self.async_set_unique_id(RADAR_ENTRY_UNIQUE_ID)
        self._abort_if_unique_id_configured()
        return self.async_create_entry(
            title=RADAR_ENTRY_TITLE, data={CONF_ENTRY_TYPE: ENTRY_TYPE_RADAR}
        )

    async def async_step_user(self, user_input=None):
        errors = {}
        user_input = user_input or {}
        if user_input:
            settlement, errors = await async_validate_forecast(self.hass, user_input)
            if not errors:
                return self.async_create_entry(
                    title=user_input[CONF_NAME],
                    data={
                        "name": user_input[CONF_NAME],
                        CONF_DISTANCE_KM: user_input.get(
                            CONF_DISTANCE_KM, DEFAULT_DISTANCE_KM
                        ),
                        CONF_FORECAST: settlement_to_dict(settlement),
                        CONF_LOCATION: user_input.get(CONF_LOCATION, LOCATION_HOME),
                    },
                )
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({
                # No default name; keep what was typed when the form is shown
                # again with an error
                vol.Required(
                    CONF_NAME,
                    description={"suggested_value": user_input.get(CONF_NAME)},
                ): str,
                vol.Required(
                    CONF_DISTANCE_KM,
                    default=user_input.get(CONF_DISTANCE_KM, DEFAULT_DISTANCE_KM),
                ): vol.All(vol.Coerce(float), vol.Range(min=1, max=100)),
                vol.Optional(
                    CONF_FORECAST_SETTLEMENT,
                    default=user_input.get(CONF_FORECAST_SETTLEMENT, ""),
                ): str,
                vol.Required(
                    CONF_LOCATION,
                    default=user_input.get(CONF_LOCATION, LOCATION_HOME),
                ): LOCATION_SELECTOR,
            }),
            errors=errors,
        )

    def is_matching(self, other_flow: "HungarometConfigFlow") -> bool:
        """Return True if another flow is considered a duplicate."""
        return self.source == other_flow.source
