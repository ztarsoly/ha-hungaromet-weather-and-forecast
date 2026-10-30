"""What the update service refreshes: every entry's feeds, forecast and radar images.

Each part registers its own refresh here while it is loaded, and the service runs
all of them.
"""

import logging
from collections.abc import Awaitable, Callable

from homeassistant.core import HomeAssistant, ServiceCall, callback

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

UPDATERS = f"{DOMAIN}_updaters"
SERVICE_UPDATE = "update"


@callback
def async_register_updater(
    hass: HomeAssistant, key: str, update: Callable[[], Awaitable]
) -> Callable[[], None]:
    """Let the update service run `update`; returns a function that removes it."""
    updaters = hass.data.setdefault(UPDATERS, {})
    updaters[key] = update

    @callback
    def remove() -> None:
        if updaters.get(key) is update:
            del updaters[key]

    return remove


async def async_update_all(hass: HomeAssistant) -> None:
    """Run every registered refresh; one failure does not stop the others."""
    for key, update in list(hass.data.get(UPDATERS, {}).items()):
        try:
            await update()
        except Exception:  # pylint: disable=broad-exception-caught
            _LOGGER.exception("Updating %s failed", key)


@callback
def async_register_update_service(hass: HomeAssistant) -> None:
    """Register the update service once for the integration."""

    async def handle_update(call: ServiceCall) -> None:
        await async_update_all(hass)

    hass.services.async_register(DOMAIN, SERVICE_UPDATE, handle_update)
