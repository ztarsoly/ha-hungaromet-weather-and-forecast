"""The forecast settlement's alerts and warnings, refreshed every hour."""

import logging
from datetime import timedelta

import aiohttp
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import UpdateFailed

from .const import DOMAIN
from .forecast_scraper import Settlement
from .scheduler import RetryingSchedule, ScheduledCoordinator
from .updaters import async_register_updater
from .warning_scraper import Warnings, fetch_warnings, find_district

_LOGGER = logging.getLogger(__name__)

# Checked at every full hour, and retried soon after a failed download
WARNING_SCHEDULE = {"minute": 0, "second": 59}
WARNING_RETRY_DELAY = timedelta(minutes=10)

STORAGE_VERSION = 1


def warning_store(hass: HomeAssistant, entry_id: str) -> Store:
    """Return where an entry's last alerts and warnings are saved (in /config/.storage)."""
    return Store(hass, STORAGE_VERSION, f"{DOMAIN}.warnings.{entry_id}")


class WarningCoordinator(ScheduledCoordinator[Warnings]):
    """Hold the latest alerts and warnings; refreshed by a schedule, never polled."""

    def __init__(
        self, hass: HomeAssistant, entry: ConfigEntry, settlement: Settlement
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} warnings",
            update_interval=None,
        )
        self.settlement = settlement
        self.district: str | None = None
        self._store = warning_store(hass, entry.entry_id)
        self.schedule = RetryingSchedule(
            hass,
            "warnings",
            self._async_scheduled_download,
            WARNING_RETRY_DELAY,
            config_entry=entry,
            **WARNING_SCHEDULE,
        )

    async def async_load_stored(self) -> None:
        """Start from the levels saved before a restart, if they are still ours.

        They show until the first download, however old. Levels saved for
        another settlement are not used.
        """
        stored = await self._store.async_load()
        if not stored or stored.get("code") != self.settlement.kod:
            return
        try:
            self.data = Warnings(
                alerts=dict(stored["alerts"]), warnings=dict(stored["warnings"])
            )
        except (KeyError, TypeError, ValueError) as err:
            _LOGGER.warning("Ignoring the saved warnings, which are unreadable: %s", err)

    async def _async_update_data(self) -> Warnings:
        if self.district is None:
            self.district = await self.hass.async_add_executor_job(
                find_district, self.settlement.lat, self.settlement.lon
            )
        session = async_get_clientsession(self.hass)
        try:
            data = await fetch_warnings(session, self.district)
        except (aiohttp.ClientError, TimeoutError, ValueError) as err:
            raise UpdateFailed(
                f"met.hu warnings for {self.settlement.name} failed: {err}"
            ) from err
        await self._store.async_save(
            {
                "code": self.settlement.kod,
                "alerts": data.alerts,
                "warnings": data.warnings,
            }
        )
        return data


async def async_setup_warnings(
    hass: HomeAssistant, entry: ConfigEntry, settlement: Settlement
) -> WarningCoordinator:
    """Create and schedule the warning coordinator of a forecast settlement.

    The first download is left to the caller, once the entities are added.
    """
    coordinator = WarningCoordinator(hass, entry, settlement)
    await coordinator.async_load_stored()
    entry.async_on_unload(coordinator.schedule.start())
    entry.async_on_unload(
        async_register_updater(
            hass, f"{entry.entry_id}_warnings", coordinator.async_update_now
        )
    )
    return coordinator
