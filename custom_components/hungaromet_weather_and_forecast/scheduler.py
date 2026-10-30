"""Run an update on a UTC schedule and retry it until it brings fresh data."""

import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.event import async_call_later, async_track_utc_time_change
from homeassistant.util import dt as dt_util

_LOGGER = logging.getLogger(__name__)


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
        self._scheduled_for: datetime | None = None
        self._unsub_schedule: Callable[[], None] | None = None
        self._unsub_retry: Callable[[], None] | None = None

    def start(self) -> Callable[[], None]:
        """Start the schedule and return a function that stops it."""
        self._unsub_schedule = async_track_utc_time_change(
            self._hass, self._async_timed_run, **self._utc_pattern
        )
        return self.stop

    async def async_run_now(self) -> None:
        """Run the job immediately, retrying it like a scheduled run."""
        await self._async_scheduled_run(dt_util.utcnow())

    def stop(self) -> None:
        """Stop the schedule and any pending retry."""
        self._cancel_retry()
        if self._unsub_schedule is not None:
            self._unsub_schedule()
            self._unsub_schedule = None

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
        self._scheduled_for = now
        await self._async_attempt()

    async def _async_attempt(self, _now: datetime | None = None) -> None:
        self._unsub_retry = None
        if await self._job(self._scheduled_for):
            return
        _LOGGER.debug(
            "HungaroMet %s data not fresh yet; retrying in %s",
            self._name,
            self._retry_delay,
        )
        self._unsub_retry = async_call_later(
            self._hass, self._retry_delay, self._async_attempt
        )
