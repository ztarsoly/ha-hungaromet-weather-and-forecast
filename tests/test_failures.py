"""Tests for logging failures once and retrying after a restart."""

# The tests check the integration's internals
# pylint: disable=protected-access

import logging
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.hungaromet_weather_and_forecast import scheduler

_LOGGER = logging.getLogger("hungaromet_test")


class _Coordinator(scheduler.ScheduledCoordinator[str]):
    """A coordinator returning or raising what it is told to."""

    results: list

    async def _async_update_data(self) -> str:
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def _hass():
    hass = MagicMock()
    hass.is_stopping = False
    return hass


async def test_a_coordinator_warns_once_per_failure_and_recovery(caplog):
    """Test only the first failure and the recovery are logged, as warnings."""
    caplog.set_level(logging.INFO)
    coordinator = _Coordinator(_hass(), _LOGGER, name="Forecast", config_entry=None)
    coordinator.results = [
        UpdateFailed("down"),
        UpdateFailed("still down"),
        ValueError("broken"),
        "data",
        "more data",
        TimeoutError(),
    ]

    for _ in range(6):
        await coordinator.async_refresh()

    assert [(r.levelname, r.getMessage()) for r in caplog.records] == [
        ("WARNING", "Forecast download failed: down"),
        ("WARNING", "Forecast download works again"),
        ("WARNING", "Forecast download failed: "),
    ]


async def test_retries_every_minute_until_the_first_download():
    """Test a coordinator is tried every minute until a download works."""
    coordinator = _Coordinator(_hass(), _LOGGER, name="Forecast", config_entry=None)
    coordinator.results = [UpdateFailed("down"), "data"]
    coordinator._listeners = {1: (MagicMock(), None)}
    coordinator.async_update_listeners = MagicMock()

    with patch.object(scheduler, "async_call_later") as call_later:
        coordinator.async_retry_after_restart()
        hass, delay, attempt = call_later.call_args.args
        assert (hass, delay.total_seconds()) == (coordinator.hass, 60)

        await attempt(None)  # fails: tried again
        assert call_later.call_count == 2
        await attempt(None)  # works: no more tries
        assert call_later.call_count == 2

    assert coordinator.data == "data"


async def test_no_extra_retries_when_not_needed():
    """Test there are no retries after a download, or while nothing shows it."""
    coordinator = _Coordinator(_hass(), _LOGGER, name="Forecast", config_entry=None)
    coordinator.async_refresh = AsyncMock()

    with patch.object(scheduler, "async_call_later") as call_later:
        coordinator.async_retry_after_restart()
        await call_later.call_args.args[2](None)  # no listeners
        assert call_later.call_count == 1
        coordinator.async_refresh.assert_not_awaited()

        coordinator._downloaded = True
        coordinator.async_retry_after_restart()()
        assert call_later.call_count == 1


async def test_stopping_the_retries():
    """Test stopping cancels the pending try and any try after a running one."""
    with patch.object(scheduler, "async_call_later") as call_later:
        stop = scheduler.async_retry_every_minute("hass", AsyncMock(return_value=False))
        attempt = call_later.call_args.args[2]
        stop()
        call_later.return_value.assert_called_once()

        await attempt(None)  # was already running when stopped
        assert call_later.call_count == 1
        stop()
        call_later.return_value.assert_called_once()
