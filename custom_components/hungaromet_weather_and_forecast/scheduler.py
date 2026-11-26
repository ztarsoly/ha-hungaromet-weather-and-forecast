"""Run an update on a UTC schedule and retry it until it brings fresh data."""

import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from functools import partial
from typing import TypeVar

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import async_call_later, async_track_utc_time_change
from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
    UpdateFailed,
)
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .failures import FailureLog

_LOGGER = logging.getLogger(__package__)

_DataT = TypeVar("_DataT")

# How often a download that failed after a restart or reload is tried again,
# besides its schedule, until it works
STARTUP_RETRY_DELAY = timedelta(minutes=1)


def async_retry_every_minute(
    hass: HomeAssistant, attempt: Callable[[], Awaitable[bool]]
) -> Callable[[], None]:
    """Run `attempt` every minute until it returns True.

    The first run is a minute from now. Returns a function that stops it.
    """
    unsub: Callable[[], None] | None = None
    stopped = False

    async def run(_now) -> None:
        nonlocal unsub
        unsub = None
        if not await attempt() and not stopped:
            unsub = async_call_later(hass, STARTUP_RETRY_DELAY, run)

    unsub = async_call_later(hass, STARTUP_RETRY_DELAY, run)

    def stop() -> None:
        nonlocal stopped
        stopped = True
        if unsub is not None:
            unsub()

    return stop


class RetryingSchedule:
    """Run a job at a UTC time pattern and retry it after a delay while it fails.

    The job receives the time of the scheduled run and returns True once the data
    it fetched is fresh for that run. Retries stop at the next scheduled run.

    Scheduled runs are skipped while the config entry's "Enable polling for
    updates" system option is off; running now (at startup, or from the update
    service) still works.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        name: str,
        job: Callable[[datetime], Awaitable[bool]],
        retry_delay: timedelta,
        config_entry: ConfigEntry | None = None,
        **utc_pattern,
    ) -> None:
        self._hass = hass
        self._config_entry = config_entry
        self._name = name
        self._job = job
        self._retry_delay = retry_delay
        self._utc_pattern = utc_pattern
        self._unsub_retry: Callable[[], None] | None = None

    def start(self) -> Callable[[], None]:
        """Start the schedule and return a function that stops it and any retry."""
        unsub_schedule = async_track_utc_time_change(
            self._hass, self._async_timed_run, **self._utc_pattern
        )

        def stop() -> None:
            nonlocal unsub_schedule
            self._cancel_retry()
            if unsub_schedule is not None:
                unsub_schedule()
                unsub_schedule = None

        return stop

    async def async_run_now(self) -> None:
        """Run the job immediately, retrying it like a scheduled run."""
        await self._async_scheduled_run(dt_util.utcnow())

    def _cancel_retry(self) -> None:
        if self._unsub_retry is not None:
            self._unsub_retry()
            self._unsub_retry = None

    async def _async_timed_run(self, now: datetime) -> None:
        if self._config_entry is not None and self._config_entry.pref_disable_polling:
            self._cancel_retry()
            _LOGGER.debug("HungaroMet %s update skipped: polling is disabled", self._name)
            return
        await self._async_scheduled_run(now)

    async def _async_scheduled_run(self, now: datetime) -> None:
        self._cancel_retry()
        await self._async_attempt(now)

    async def _async_attempt(
        self, scheduled_for: datetime, _now: datetime | None = None
    ) -> None:
        """Run the job for the run scheduled at `scheduled_for`; retry if it fails."""
        self._unsub_retry = None
        if await self._job(scheduled_for):
            return
        _LOGGER.debug(
            "HungaroMet %s data not fresh yet; retrying in %s",
            self._name,
            self._retry_delay,
        )
        self._unsub_retry = async_call_later(
            self._hass, self._retry_delay, partial(self._async_attempt, scheduled_for)
        )


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
        self._failure_log = FailureLog(logger, f"{label} download{where}")
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

    @callback
    def async_retry_after_restart(self) -> Callable[[], None]:
        """Until the first download works, also try it every minute.

        Returns a function that stops the retries.
        """
        if self._downloaded:
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
