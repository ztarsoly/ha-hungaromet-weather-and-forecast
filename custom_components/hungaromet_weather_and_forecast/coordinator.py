"""The coordinators of the observation feeds, the forecast and the warnings.

Each is refreshed on its own schedule, never polled.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import date, datetime, timedelta
from typing import TypeVar

import aiohttp
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
    UpdateFailed,
)
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .data import HungaroMetConfigEntry
from .failures import FailureLog, FailureRecords
from .forecast_scraper import (
    ForecastData,
    Settlement,
    aggregate_daily,
    continue_from_recorded,
    fetch_forecast,
    find_current_hour,
    forecast_from_dict,
    forecast_to_dict,
    keep_dropped_row,
)
from .scheduler import RetryingSchedule, async_retry_every_minute
from .scope import entry_devices
from .settlement import stored_settlement
from .stations import (
    EXPECTED_ERRORS,
    FEEDS,
    Feed,
    StationQuery,
    station_query,
    warn_if_out_of_range,
)
from .updaters import async_register_updater
from .warning_scraper import Warnings, fetch_warnings, find_district

_LOGGER = logging.getLogger(__package__)

_DataT = TypeVar("_DataT")

# When each feed is checked (UTC, matching HungaroMet's publishing times) and how
# long to wait before trying again while its data is not fresh yet
FEED_SCHEDULES = {
    "daily": ({"hour": 9, "minute": 35, "second": 59}, timedelta(hours=1)),
    "hourly": ({"minute": 20, "second": 59}, timedelta(minutes=10)),
    "ten_minutes": ({"minute": "/10", "second": 59}, timedelta(minutes=1)),
}
# met.hu reissues the forecast during the day; check at every full hour, and
# retry soon after a failed download
FORECAST_SCHEDULE = {"minute": 0, "second": 59}
FORECAST_RETRY_DELAY = timedelta(minutes=10)
# The alerts and warnings too
WARNING_SCHEDULE = {"minute": 0, "second": 59}
WARNING_RETRY_DELAY = timedelta(minutes=10)

STORAGE_VERSION = 1


def forecast_store(hass: HomeAssistant, entry_id: str) -> Store:
    """Return where an entry's last forecast is saved (in /config/.storage)."""
    return Store(hass, STORAGE_VERSION, f"{DOMAIN}.forecast.{entry_id}")


def warning_store(hass: HomeAssistant, entry_id: str) -> Store:
    """Return where versions up to 1.3.8 saved an entry's alerts and warnings."""
    return Store(hass, STORAGE_VERSION, f"{DOMAIN}.warnings.{entry_id}")


class _DebugLogger(logging.LoggerAdapter):
    """Move every message of a logger to the debug level.

    For the coordinator's own messages: its failures and recoveries are
    logged as warnings by the coordinator's FailureLog instead.
    """

    def log(self, level, msg, *args, **kwargs):
        super().log(logging.DEBUG, msg, *args, **kwargs)


class ScheduledCoordinator(DataUpdateCoordinator[_DataT]):
    """A coordinator refreshed by a RetryingSchedule and the update service."""

    def __init__(
        self,
        hass: HomeAssistant,
        logger: logging.Logger,
        *,
        name: str,
        place: str | None = None,
        **kwargs,
    ) -> None:
        super().__init__(hass, _DebugLogger(logger, {}), name=name, **kwargs)
        label = name.replace(DOMAIN, "HungaroMet")
        where = f" for {place}" if place else ""
        what = f"{label} download{where}"
        entry = kwargs.get("config_entry")
        self._failure_log = FailureLog(
            logger,
            what,
            entry.runtime_data.failures if entry else FailureRecords(),
        )
        # Whether a download has worked since the setup
        self._downloaded = False

    @callback
    def _async_refresh_finished(self) -> None:
        """Warn about the first failed download, and about the recovery."""
        if self.last_update_success:
            self._downloaded = True
            self._failure_log.succeeded()
        elif isinstance(self.last_exception, (UpdateFailed, TimeoutError)):
            self._failure_log.failed(self.last_exception)
        else:
            # Unexpected: with the traceback
            self._failure_log.failed(
                repr(self.last_exception), exc_info=self.last_exception
            )

    @property
    def downloaded(self) -> bool:
        """Return whether a download has worked since the setup."""
        return self._downloaded

    @callback
    def async_retry_after_restart(self) -> Callable[[], None]:
        """Until the first download works, also try it every minute.

        Not while the entry's "Enable polling for updates" system option is off.
        Returns a function that stops the retries.
        """
        entry = self.config_entry
        if self._downloaded or (entry and entry.pref_disable_polling):
            return lambda: None

        async def attempt() -> bool:
            if not self._downloaded and self._listeners:
                await self.async_refresh()
            return self._downloaded or not self._listeners

        return async_retry_every_minute(self.hass, attempt)

    async def _async_scheduled_download(self, _scheduled_for=None) -> bool:
        """Download the data; return False to have it retried.

        Nothing is downloaded while no enabled entity shows the data, e.g.
        when the Forecast device is disabled.
        """
        if not self._listeners:
            return True
        await self.async_refresh()
        return self.last_update_success

    async def async_update_now(self) -> None:
        """Download the data for the update service."""
        await self._async_scheduled_download()


def _is_fresh(data_type: str, observed: str, scheduled_for: datetime) -> bool:
    """Return whether a feed's observation time is the one due at a scheduled run.

    Daily files hold yesterday, hourly files the hour 20 minutes before they are
    published, and 10-minute files the slot 10 minutes before they are published.
    """
    if data_type == "daily":
        return date.fromisoformat(observed) >= scheduled_for.date() - timedelta(days=1)
    if data_type == "hourly":
        expected = scheduled_for.replace(minute=0, second=0, microsecond=0)
    else:
        expected = scheduled_for.replace(
            minute=scheduled_for.minute - scheduled_for.minute % 10,
            second=0,
            microsecond=0,
        ) - timedelta(minutes=10)
    return datetime.fromisoformat(observed) >= expected


class FeedCoordinator(ScheduledCoordinator[tuple[dict, list[dict]]]):
    """Hold the latest download of an observation feed: its values and stations."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: HungaroMetConfigEntry,
        feed: Feed,
        query: StationQuery,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} {feed.model.lower()}",
            update_interval=None,
        )
        self.feed = feed
        self.query = query
        pattern, retry_delay = FEED_SCHEDULES[feed.key]
        self.schedule = RetryingSchedule(
            hass,
            feed.key,
            self._async_scheduled_download,
            retry_delay,
            config_entry=entry,
            **pattern,
        )

    async def _async_update_data(self) -> tuple[dict, list[dict]]:
        try:
            data, stations = await self.hass.async_add_executor_job(
                self.feed.process,
                self.hass,
                self.query.distance_km,
                self.query.location,
            )
        except EXPECTED_ERRORS as err:
            raise UpdateFailed(str(err) or type(err).__name__) from err
        warn_if_out_of_range(self.query, stations)
        return data, stations

    async def _async_scheduled_download(self, scheduled_for=None) -> bool:
        """Download the feed; return False to have it retried.

        After a scheduled run it is retried until the data due at that run is
        there.
        """
        downloaded = await super()._async_scheduled_download()
        if not downloaded or scheduled_for is None or not self._listeners:
            return downloaded
        observed = self.data[0].get("time")
        return observed is not None and _is_fresh(
            self.feed.key, observed, scheduled_for
        )


class ForecastCoordinator(ScheduledCoordinator[ForecastData]):
    """Hold the latest forecast; refreshed by a schedule, never polled."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: HungaroMetConfigEntry,
        settlement: Settlement,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} forecast",
            place=settlement.name,
            update_interval=None,
        )
        self.settlement = settlement
        # The settlement's alerts and warnings, set up with the forecast
        self.warnings: WarningCoordinator | None = None
        # None once the entry has unloaded; the lock lets a save under way finish
        self._store: Store | None = forecast_store(hass, entry.entry_id)
        self._save_lock = asyncio.Lock()
        # The forecast saved before a restart; shown only while downloads fail
        self._saved: ForecastData | None = None
        self.schedule = RetryingSchedule(
            hass,
            "forecast",
            self._async_scheduled_download,
            FORECAST_RETRY_DELAY,
            config_entry=entry,
            **FORECAST_SCHEDULE,
        )

    async def async_load_stored(self) -> None:
        """Load the forecast saved before a restart, if it is still ours.

        It stands in while the downloads fail, and gives the first download
        the rows met.hu has dropped meanwhile.
        """
        stored = await self._store.async_load()
        # Saved as "kod" up to 1.3.4
        if not stored:
            return
        code = stored.get("code", stored.get("kod"))
        if code != self.settlement.kod:
            _LOGGER.debug("Saved data is for %s, not used", code)
            return
        try:
            self._saved = forecast_from_dict(
                stored["forecast"],
                dt_util.now(),
                (self.settlement.lat, self.settlement.lon),
            )
        except (KeyError, TypeError, ValueError) as err:
            self._failure_log.records.warn_once(
                _LOGGER, "Ignoring the saved forecast, which is unreadable: %s", err
            )

    @property
    def has_saved_current_hour(self) -> bool:
        """Return whether the saved forecast has something to show now."""
        return self._saved is not None and self._saved.current is not None

    @callback
    def _async_refresh_finished(self) -> None:
        """After a failed download, go on with the forecast of the current hour.

        The last forecast (after a restart the saved one) holds every hour, so
        the entities follow it while met.hu cannot be reached. They become
        unavailable once it has no current hour.
        """
        super()._async_refresh_finished()
        if self.last_update_success:
            return
        self.data = self.data or self._saved
        if self.data is None:
            return
        now = dt_util.now()
        self.data.current = find_current_hour(self.data.hourly, now)
        self.data.daily = aggregate_daily(self.data.periods, now)
        # The coordinator itself only tells the entities about the first failure
        self.async_update_listeners()

    async def _async_update_data(self) -> ForecastData:
        session = async_get_clientsession(self.hass)
        try:
            data = await fetch_forecast(
                session, self.settlement, self._failure_log.records
            )
        except (aiohttp.ClientError, TimeoutError, ValueError) as err:
            raise UpdateFailed(str(err) or type(err).__name__) from err
        now = dt_util.now()
        data = keep_dropped_row(data, self.data or self._saved, now)
        # The hours continue from those shown only while the downloads work:
        # saved or old values only fill what a download lacks
        if self.last_update_success:
            data = continue_from_recorded(data, self.data, now)
        await self._async_save(data)
        return data

    async def _async_save(self, data: ForecastData) -> None:
        """Save a downloaded forecast, unless the entry has unloaded."""
        async with self._save_lock:
            if self._store is not None:
                await self._store.async_save(
                    {"code": self.settlement.kod, "forecast": forecast_to_dict(data)}
                )

    async def async_close(self) -> None:
        """Save nothing more; return once a save under way has finished.

        A download that finishes after the entry is removed must not write the
        deleted file back.
        """
        async with self._save_lock:
            self._store = None


class WarningCoordinator(ScheduledCoordinator[Warnings]):
    """Hold the latest alerts and warnings; refreshed by a schedule, never polled."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: HungaroMetConfigEntry,
        settlement: Settlement,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} warnings",
            place=settlement.name,
            update_interval=None,
        )
        self.settlement = settlement
        self.district: str | None = None
        self.schedule = RetryingSchedule(
            hass,
            "warnings",
            self._async_scheduled_download,
            WARNING_RETRY_DELAY,
            config_entry=entry,
            **WARNING_SCHEDULE,
        )

    async def _async_update_data(self) -> Warnings:
        if self.district is None:
            self.district = await self.hass.async_add_executor_job(
                find_district, self.settlement.lat, self.settlement.lon
            )
        session = async_get_clientsession(self.hass)
        try:
            return await fetch_warnings(session, self.district)
        except (aiohttp.ClientError, TimeoutError, ValueError) as err:
            raise UpdateFailed(str(err) or type(err).__name__) from err


async def async_setup_feeds(
    hass: HomeAssistant, entry: HungaroMetConfigEntry
) -> dict[str, FeedCoordinator]:
    """Create, schedule and download each chosen feed, one after the other.

    A failed download leaves the feed's sensors unavailable; it is the caller's
    to have it retried.
    """
    query = station_query(entry)
    devices = entry_devices(entry)
    coordinators = {}
    for feed in (feed for feed in FEEDS.values() if feed.key in devices):
        coordinator = FeedCoordinator(hass, entry, feed, query)
        entry.async_on_unload(coordinator.schedule.start())
        entry.async_on_unload(
            async_register_updater(
                entry, f"{entry.entry_id}_{feed.key}", coordinator.async_update_now
            )
        )
        await coordinator.async_refresh()
        coordinators[feed.key] = coordinator
    return coordinators


async def async_setup_forecast(
    hass: HomeAssistant, entry: HungaroMetConfigEntry
) -> ForecastCoordinator | None:
    """Create and schedule the forecast and warning coordinators if a settlement is set.

    The first download is left to the caller, once the entities are added:
    without them it would be skipped.
    """
    settlement = stored_settlement(entry)
    if settlement is None:
        return None
    coordinator = ForecastCoordinator(hass, entry, settlement)
    await coordinator.async_load_stored()

    entry.async_on_unload(coordinator.async_close)
    entry.async_on_unload(coordinator.schedule.start())
    entry.async_on_unload(
        async_register_updater(
            entry, f"{entry.entry_id}_forecast", coordinator.async_update_now
        )
    )
    coordinator.warnings = await async_setup_warnings(hass, entry, settlement)
    return coordinator


async def async_setup_warnings(
    hass: HomeAssistant, entry: HungaroMetConfigEntry, settlement: Settlement
) -> WarningCoordinator:
    """Create and schedule the warning coordinator of a forecast settlement.

    The first download is left to the caller, once the entities are added.
    """
    coordinator = WarningCoordinator(hass, entry, settlement)
    # A download is complete or fails, so there is nothing saved values could
    # fill in: delete the file earlier versions saved
    await warning_store(hass, entry.entry_id).async_remove()
    entry.async_on_unload(coordinator.schedule.start())
    entry.async_on_unload(
        async_register_updater(
            entry, f"{entry.entry_id}_warnings", coordinator.async_update_now
        )
    )
    return coordinator
