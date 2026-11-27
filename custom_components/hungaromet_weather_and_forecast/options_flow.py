"""The Configure form of a location entry."""

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_NAME
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

from .const import (
    CONF_DEVICES,
    CONF_DISTANCE_KM,
    CONF_FORECAST,
    CONF_FORECAST_SETTLEMENT,
    CONF_LOCATION,
    DEFAULT_DISTANCE_KM,
    DEVICES,
    LOCATION_HOME,
    LOCATION_SETTLEMENT,
)
from .scope import entry_devices
from .settlement import (
    async_resolve_settlement,
    entry_place,
    place,
    place_conflicts,
    settlement_to_dict,
    stored_settlement,
)

# The station search radius; "any" step also allows decimals, such as 12.5
DISTANCE_SELECTOR = NumberSelector(
    NumberSelectorConfig(
        min=1,
        max=100,
        step="any",
        unit_of_measurement="km",
        mode=NumberSelectorMode.BOX,
    )
)

# Dropdown choosing where the weather station sensors measure from
LOCATION_SELECTOR = SelectSelector(
    SelectSelectorConfig(
        options=[LOCATION_HOME, LOCATION_SETTLEMENT],
        translation_key=CONF_LOCATION,
        mode=SelectSelectorMode.DROPDOWN,
    )
)


# The station devices and the regional radar image of an entry, as checkboxes
DEVICES_SELECTOR = SelectSelector(
    SelectSelectorConfig(
        options=list(DEVICES),
        translation_key=CONF_DEVICES,
        multiple=True,
        mode=SelectSelectorMode.LIST,
    )
)


def devices_field(devices) -> dict:
    """Return the form field that chooses an entry's devices, these ticked."""
    # The form sends nothing, or an empty list, when no box is ticked
    key = vol.Optional(
        CONF_DEVICES, default=[], description={"suggested_value": list(devices)}
    )
    return {key: DEVICES_SELECTOR}


async def async_validate_forecast(hass, user_input: dict):
    """Resolve the settlement and check the location and device choices.

    Returns (settlement, errors keyed by form field).
    """
    settlement, error = await async_resolve_settlement(
        hass, user_input.get(CONF_FORECAST_SETTLEMENT)
    )
    if error:
        return None, {CONF_FORECAST_SETTLEMENT: error}
    if user_input.get(CONF_LOCATION) == LOCATION_SETTLEMENT and settlement is None:
        return None, {CONF_LOCATION: "settlement_required"}
    if settlement is None and not user_input.get(CONF_DEVICES):
        return None, {CONF_DEVICES: "nothing_selected"}
    return settlement, {}


class HungaroMetOptionsFlowHandler(config_entries.OptionsFlow):
    """The Configure form: an entry's name and settings."""

    async def async_step_init(self, user_input=None):
        """Show the form with the current settings, and save the new ones."""
        errors = {}
        if user_input is not None:
            name = user_input.get(CONF_NAME, self.config_entry.title).strip()
            settlement, errors = await async_validate_forecast(self.hass, user_input)
            if not name:
                errors[CONF_NAME] = "name_required"
            devices = user_input.get(CONF_DEVICES, [])
            if not errors:
                errors = self._new_conflicts(
                    place(settlement, user_input[CONF_LOCATION], devices)
                )
            if not errors:
                self._async_rename_entry(name)
                return self.async_create_entry(
                    title="",
                    data={
                        CONF_DISTANCE_KM: user_input[CONF_DISTANCE_KM],
                        CONF_FORECAST: settlement_to_dict(settlement),
                        CONF_LOCATION: user_input[CONF_LOCATION],
                        CONF_DEVICES: devices,
                    },
                )

        entry = self.config_entry
        current = stored_settlement(entry)
        defaults = user_input or {
            CONF_NAME: entry.title,
            CONF_DISTANCE_KM: entry.options.get(
                CONF_DISTANCE_KM, entry.data.get(CONF_DISTANCE_KM, DEFAULT_DISTANCE_KM)
            ),
            CONF_FORECAST_SETTLEMENT: current.name if current else "",
            CONF_LOCATION: entry.options.get(
                CONF_LOCATION, entry.data.get(CONF_LOCATION, LOCATION_HOME)
            ),
            CONF_DEVICES: entry_devices(entry),
        }
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema({
                vol.Required(CONF_NAME, default=defaults.get(CONF_NAME, entry.title)): str,
                vol.Optional(
                    CONF_DISTANCE_KM, default=defaults[CONF_DISTANCE_KM]
                ): DISTANCE_SELECTOR,
                vol.Optional(
                    CONF_FORECAST_SETTLEMENT,
                    default=defaults.get(CONF_FORECAST_SETTLEMENT, ""),
                ): str,
                vol.Required(
                    CONF_LOCATION, default=defaults.get(CONF_LOCATION, LOCATION_HOME)
                ): LOCATION_SELECTOR,
                **devices_field(defaults.get(CONF_DEVICES, [])),
            }),
            errors=errors,
        )

    def _new_conflicts(
        self, wanted: tuple[str | None, str | None]
    ) -> dict[str, str]:
        """Return the errors for what the entry would newly share with another one.

        What it shares already, as entries added by earlier versions can, may stay.
        """
        entry = self.config_entry
        errors = place_conflicts(self.hass, wanted, entry.entry_id)
        kod, location = entry_place(entry)
        if wanted[0] == kod:
            errors.pop(CONF_FORECAST_SETTLEMENT, None)
        if wanted[1] == location:
            errors.pop(CONF_LOCATION, None)
        return errors

    def _async_rename_entry(self, name: str) -> None:
        """Give the entry a new name; its devices follow when it reloads."""
        entry = self.config_entry
        if name != entry.title:
            self.hass.config_entries.async_update_entry(
                entry, title=name, data={**entry.data, CONF_NAME: name}
            )
