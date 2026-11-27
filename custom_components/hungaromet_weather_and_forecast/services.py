"""The integration's actions."""

from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import ServiceValidationError

from .const import DOMAIN
from .updaters import async_update_all, loaded_entries

SERVICE_UPDATE = "update"


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    """Register the integration's actions."""

    async def handle_update(_call: ServiceCall) -> None:
        if not loaded_entries(hass):
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="no_loaded_entry"
            )
        await async_update_all(hass)

    hass.services.async_register(DOMAIN, SERVICE_UPDATE, handle_update)
