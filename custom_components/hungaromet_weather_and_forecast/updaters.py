"""What the update action refreshes: every entry's feeds, forecast and radar images.

Each part registers its own refresh with its entry while it is loaded, and the
action runs those of every loaded entry.
"""

import logging
from collections.abc import Awaitable, Callable

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, callback

from .const import DOMAIN
from .data import HungaroMetConfigEntry

_LOGGER = logging.getLogger(__package__)


@callback
def async_register_updater(
    entry: HungaroMetConfigEntry, key: str, update: Callable[[], Awaitable]
) -> Callable[[], None]:
    """Let the update action run `update`; returns a function that removes it."""
    updaters = entry.runtime_data.updaters
    updaters[key] = update

    @callback
    def remove() -> None:
        if updaters.get(key) is update:
            del updaters[key]

    return remove


def loaded_entries(hass: HomeAssistant) -> list[HungaroMetConfigEntry]:
    """Return the entries that are loaded."""
    return [
        entry
        for entry in hass.config_entries.async_entries(DOMAIN)
        if entry.state is ConfigEntryState.LOADED
    ]


async def async_update_all(hass: HomeAssistant) -> None:
    """Run every loaded entry's refreshes; one failure does not stop the others."""
    updaters = [
        item
        for entry in loaded_entries(hass)
        for item in entry.runtime_data.updaters.items()
    ]
    for key, update in updaters:
        try:
            await update()
        except Exception:  # pylint: disable=broad-exception-caught
            _LOGGER.exception("Updating %s failed", key)
