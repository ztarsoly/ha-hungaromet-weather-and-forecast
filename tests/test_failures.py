"""Tests for logging failures once and retrying after a restart."""

# The tests check the integration's internals
# pylint: disable=protected-access

import logging
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.hungaromet_weather_and_forecast import (
    coordinator as coordinators,
    failures,
    scheduler,
)

_LOGGER = logging.getLogger("hungaromet_test")


class _Coordinator(coordinators.ScheduledCoordinator[str]):
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
        ("WARNING", "Forecast download failed: TimeoutError"),
    ]


async def test_an_unexpected_error_is_logged_with_its_traceback(caplog):
    """Test a bug is logged with its traceback, an expected failure without."""
    coordinator = _Coordinator(
        _hass(), _LOGGER, name="Forecast", place="Debrecen", config_entry=None
    )
    coordinator.results = [UpdateFailed("down"), "data", KeyError("bug")]

    for _ in range(3):
        await coordinator.async_refresh()

    expected, _, unexpected = [r for r in caplog.records if r.levelname == "WARNING"]
    assert expected.getMessage() == "Forecast download for Debrecen failed: down"
    assert not expected.exc_info
    assert unexpected.getMessage().endswith("failed: KeyError('bug')")
    assert unexpected.exc_info


async def test_logs_of_a_name_share_the_records(caplog):
    """Test a new log of the same download does not repeat the warning."""
    caplog.set_level(logging.INFO)
    records = failures.FailureRecords()
    failures.FailureLog(_LOGGER, "Forecast", records).failed("down")

    again = failures.FailureLog(_LOGGER, "Forecast", records)
    again.failed("still down")
    again.succeeded()
    # A log without records starts anew
    failures.FailureLog(_LOGGER, "Radar").failed("down")
    failures.FailureLog(_LOGGER, "Radar").failed("down")

    assert [r.getMessage() for r in caplog.records] == [
        "Forecast failed: down",
        "Forecast works again",
        "Radar failed: down",
        "Radar failed: down",
    ]


async def test_closed_records_log_nothing(caplog):
    """A download finishing after its entry has unloaded is not logged."""
    caplog.set_level(logging.INFO)
    records = failures.FailureRecords()
    radar = failures.FailureLog(_LOGGER, "Radar", records)
    radar.failed("offline")
    records.closed = True

    radar.succeeded()
    failures.FailureLog(_LOGGER, "Forecast", records).failed("timeout")
    records.warn_once(_LOGGER, "Unknown %r", "a")

    assert [r.getMessage() for r in caplog.records] == ["Radar failed: offline"]


async def test_held_warnings_wait_for_the_release(caplog):
    """While held, warnings are debug messages; the release logs them."""
    caplog.set_level(logging.DEBUG)
    records = failures.FailureRecords()
    records.hold()
    radar = failures.FailureLog(_LOGGER, "Radar", records)
    error = ValueError("broken")

    radar.failed(error, exc_info=error)
    radar.failed("still broken")
    records.warn_once(_LOGGER, "Unknown %r", "a")

    assert [(r.levelname, r.getMessage()) for r in caplog.records] == [
        ("DEBUG", "Radar failed: broken"),
        ("DEBUG", "Unknown 'a'"),
    ]

    caplog.clear()
    records.release()
    radar.succeeded()

    assert [(r.levelname, r.getMessage()) for r in caplog.records] == [
        ("WARNING", "Radar failed: broken"),
        ("WARNING", "Unknown 'a'"),
        ("WARNING", "Radar works again"),
    ]
    assert caplog.records[0].exc_info[1] is error


async def test_warn_once(caplog):
    """Test a warning is logged once for each set of arguments."""
    records = failures.FailureRecords()

    for value in ("a", "a", "b"):
        records.warn_once(_LOGGER, "Unknown %r", value)

    assert [r.getMessage() for r in caplog.records] == [
        "Unknown 'a'",
        "Unknown 'b'",
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
    assert coordinator.downloaded is True


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


async def test_no_retries_while_polling_is_disabled():
    """Test "Enable polling for updates" turned off stops the retries too."""
    entry = MagicMock(pref_disable_polling=True)
    entry.runtime_data.failures = failures.FailureRecords()
    coordinator = _Coordinator(_hass(), _LOGGER, name="Forecast", config_entry=entry)

    with patch.object(scheduler, "async_call_later") as call_later:
        coordinator.async_retry_after_restart()()

    call_later.assert_not_called()


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
