import voluptuous as vol
from homeassistant import config_entries
from homeassistant.helpers.selector import (
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

from .const import (
    CONF_DISTANCE_KM,
    CONF_FORECAST,
    CONF_FORECAST_SETTLEMENT,
    CONF_LOCATION,
    DEFAULT_DISTANCE_KM,
    LOCATION_HOME,
    LOCATION_SETTLEMENT,
)
from .forecast import async_resolve_settlement, settlement_to_dict, stored_settlement

# Dropdown choosing where the weather station sensors measure from
LOCATION_SELECTOR = SelectSelector(
    SelectSelectorConfig(
        options=[LOCATION_HOME, LOCATION_SETTLEMENT],
        translation_key=CONF_LOCATION,
        mode=SelectSelectorMode.DROPDOWN,
    )
)


async def async_validate_forecast(hass, user_input: dict):
    """Resolve the settlement and check the location choice.

    Returns (settlement, errors keyed by form field).
    """
    settlement, error = await async_resolve_settlement(
        hass, user_input.get(CONF_FORECAST_SETTLEMENT)
    )
    if error:
        return None, {CONF_FORECAST_SETTLEMENT: error}
    if user_input.get(CONF_LOCATION) == LOCATION_SETTLEMENT and settlement is None:
        return None, {CONF_LOCATION: "settlement_required"}
    return settlement, {}


class HungaroMetOptionsFlowHandler(config_entries.OptionsFlow):
    async def async_step_init(self, user_input=None):
        errors = {}
        if user_input is not None:
            settlement, errors = await async_validate_forecast(self.hass, user_input)
            if not errors:
                return self.async_create_entry(
                    title="",
                    data={
                        CONF_DISTANCE_KM: user_input[CONF_DISTANCE_KM],
                        CONF_FORECAST: settlement_to_dict(settlement),
                        CONF_LOCATION: user_input[CONF_LOCATION],
                    },
                )

        entry = self.config_entry
        current = stored_settlement(entry)
        defaults = user_input or {
            CONF_DISTANCE_KM: entry.options.get(
                CONF_DISTANCE_KM, entry.data.get(CONF_DISTANCE_KM, DEFAULT_DISTANCE_KM)
            ),
            CONF_FORECAST_SETTLEMENT: current.name if current else "",
            CONF_LOCATION: entry.options.get(
                CONF_LOCATION, entry.data.get(CONF_LOCATION, LOCATION_HOME)
            ),
        }
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema({
                vol.Optional(
                    CONF_DISTANCE_KM, default=defaults[CONF_DISTANCE_KM]
                ): vol.All(vol.Coerce(float), vol.Range(min=1, max=100)),
                vol.Optional(
                    CONF_FORECAST_SETTLEMENT,
                    default=defaults.get(CONF_FORECAST_SETTLEMENT, ""),
                ): str,
                vol.Required(
                    CONF_LOCATION, default=defaults.get(CONF_LOCATION, LOCATION_HOME)
                ): LOCATION_SELECTOR,
            }),
            errors=errors,
        )
