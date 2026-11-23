"""The forecast settlement's alerts and warnings, refreshed every hour."""

import logging
from dataclasses import replace
from datetime import datetime, timedelta

import aiohttp
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import UpdateFailed
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .forecast_scraper import HUNGARY_TZ, Settlement
from .scheduler import RetryingSchedule, ScheduledCoordinator
from .updaters import async_register_updater
from .warning_scraper import Warnings, fetch_warnings, find_district

_LOGGER = logging.getLogger(__name__)

# Checked at every full hour, and retried soon after a failed download
WARNING_SCHEDULE = {"minute": 0, "second": 59}
WARNING_RETRY_DELAY = timedelta(minutes=10)

STORAGE_VERSION = 1

# The alerts are for the next 3 hours; the warnings are for the day
ALERTS_MAX_AGE = timedelta(hours=3)


def warning_store(hass: HomeAssistant, entry_id: str) -> Store:
    """Return where an entry's last alerts and warnings are saved (in /config/.storage)."""
    return Store(hass, STORAGE_VERSION, f"{DOMAIN}.warnings.{entry_id}")


def levels_as_of(data: Warnings, fetched: datetime, now: datetime) -> Warnings:
    """Return the levels downloaded at `fetched` that still hold at `now`.

    The alerts hold for ALERTS_MAX_AGE, the warnings until the end of the day
    in Hungary; after that they are unknown (None).
    """
    same_day = fetched.astimezone(HUNGARY_TZ).date() == now.astimezone(HUNGARY_TZ).date()
    return replace(
        data,
        alerts=data.alerts if now - fetched <= ALERTS_MAX_AGE else None,
        warnings=data.warnings if same_day else None,
    )


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
        # When the levels shown were downloaded
        self._fetched: datetime | None = None
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

        They show until the first download. Levels saved for another settlement
        are not used, nor those no longer holding (see levels_as_of).
        """
        stored = await self._store.async_load()
        if not stored or stored.get("code") != self.settlement.kod:
            return
        try:
            fetched = datetime.fromisoformat(stored["fetched"])
            saved = Warnings(
                alerts=dict(stored["alerts"]), warnings=dict(stored["warnings"])
            )
        except (KeyError, TypeError, ValueError) as err:
            _LOGGER.warning("Ignoring the saved warnings, which are unreadable: %s", err)
            return
        data = levels_as_of(saved, fetched, dt_util.now())
        if data.alerts is None and data.warnings is None:
            return
        self.data = data
        self._fetched = fetched

    async def _async_scheduled_download(self, _scheduled_for=None) -> bool:
        """Download the levels; if that fails, drop those no longer holding."""
        fresh = await super()._async_scheduled_download(_scheduled_for)
        if not fresh and self.data is not None and self._fetched is not None:
            self.data = levels_as_of(self.data, self._fetched, dt_util.now())
            self.async_update_listeners()
        return fresh

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
        self._fetched = dt_util.now()
        await self._store.async_save(
            {
                "code": self.settlement.kod,
                "fetched": self._fetched.isoformat(),
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
