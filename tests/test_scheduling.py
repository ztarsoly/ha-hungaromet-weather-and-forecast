"""Tests for the update schedules, retries and freshness checks."""

# The tests check the integration's internals
# pylint: disable=protected-access

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.hungaromet_weather_and_forecast import scheduler
from custom_components.hungaromet_weather_and_forecast.image import HungarometRadarImage
from custom_components.hungaromet_weather_and_forecast.radar import frame_is_fresh, frame_time
from custom_components.hungaromet_weather_and_forecast.coordinator import (
    FEED_SCHEDULES,
    _is_fresh,
)
from custom_components.hungaromet_weather_and_forecast.scheduler import RetryingSchedule
from custom_components.hungaromet_weather_and_forecast.weather_data import latest_observations

from .rows import column, table

from .entries import feed_coordinator, loaded_entry

RUN = datetime(2026, 10, 27, 8, 20, 59, tzinfo=UTC)


class FakeClock:
    """Capture scheduled callbacks so tests can fire them by hand."""
    def __init__(self, monkeypatch):
        self.tracked = []
        self.later = []
        self.cancelled = []
        monkeypatch.setattr(scheduler, "async_track_utc_time_change", self.track)
        monkeypatch.setattr(scheduler, "async_call_later", self.call_later)

    def track(self, hass, action, **pattern):
        """Record a time pattern instead of tracking it."""
        self.tracked.append((action, pattern))
        return lambda: self.cancelled.append("schedule")

    def call_later(self, hass, delay, action):
        """Record a delayed call instead of scheduling it."""
        handle = len(self.later)
        self.later.append((delay, action))
        return lambda: self.cancelled.append(f"retry{handle}")


async def test_schedule_runs_job_without_retry_when_fresh(monkeypatch):
    """Test schedule runs job without retry when fresh."""
    clock = FakeClock(monkeypatch)
    job = AsyncMock(return_value=True)
    RetryingSchedule(None, "test", job, timedelta(minutes=1), minute=5).start()

    action, pattern = clock.tracked[0]
    assert pattern == {"minute": 5}
    await action(RUN)

    job.assert_awaited_once_with(RUN)
    assert not clock.later


async def test_schedule_retries_with_same_run_time_until_fresh(monkeypatch):
    """Test schedule retries with same run time until fresh."""
    clock = FakeClock(monkeypatch)
    job = AsyncMock(side_effect=[False, False, True])
    RetryingSchedule(None, "test", job, timedelta(minutes=10)).start()
    action = clock.tracked[0][0]

    await action(RUN)
    delay, retry = clock.later[0]
    assert delay == timedelta(minutes=10)
    await retry(RUN + delay)
    await clock.later[1][1](RUN + 2 * delay)

    assert [c.args for c in job.await_args_list] == [(RUN,), (RUN,), (RUN,)]
    assert len(clock.later) == 2


async def test_next_scheduled_run_cancels_pending_retry(monkeypatch):
    """Test next scheduled run cancels pending retry."""
    clock = FakeClock(monkeypatch)
    job = AsyncMock(return_value=False)
    RetryingSchedule(None, "test", job, timedelta(minutes=1)).start()
    action = clock.tracked[0][0]

    await action(RUN)
    await action(RUN + timedelta(minutes=5))

    assert clock.cancelled == ["retry0"]
    assert job.await_args_list[-1].args == (RUN + timedelta(minutes=5),)


async def test_stop_cancels_schedule_and_retry(monkeypatch):
    """Test stop cancels schedule and retry."""
    clock = FakeClock(monkeypatch)
    stop = RetryingSchedule(
        None, "test", AsyncMock(return_value=False), timedelta(minutes=1)
    ).start()
    await clock.tracked[0][0](RUN)

    stop()
    stop()

    assert clock.cancelled == ["retry0", "schedule"]


async def test_disabled_polling_skips_scheduled_runs(monkeypatch):
    """System options: "Enable polling for updates" turned off."""
    clock = FakeClock(monkeypatch)
    entry = SimpleNamespace(pref_disable_polling=False)
    job = AsyncMock(return_value=False)
    schedule = RetryingSchedule(
        None, "test", job, timedelta(minutes=1), config_entry=entry, minute=5
    )
    schedule.start()
    action, pattern = clock.tracked[0]
    assert pattern == {"minute": 5}

    await action(RUN)
    assert job.await_count == 1

    # Turned off: no run, and the pending retry is cancelled
    entry.pref_disable_polling = True
    await action(RUN + timedelta(minutes=5))
    assert job.await_count == 1
    assert clock.cancelled == ["retry0"]

    # Running now (at startup) still works, and its failure is not retried
    await schedule.async_run_now()
    assert job.await_count == 2
    assert len(clock.later) == 1


async def test_a_retry_stops_once_polling_is_disabled(monkeypatch):
    """Test a retry that runs after polling was turned off is the last one."""
    clock = FakeClock(monkeypatch)
    entry = SimpleNamespace(pref_disable_polling=False)
    job = AsyncMock(return_value=False)
    schedule = RetryingSchedule(
        None, "test", job, timedelta(minutes=1), config_entry=entry, minute=5
    )
    await schedule.async_run_now()

    entry.pref_disable_polling = True
    await clock.later[0][1](None)

    assert job.await_count == 2
    assert len(clock.later) == 1


def test_feed_schedules_follow_hungaromet_publishing():
    """Test feed schedules follow HungaroMet publishing."""
    assert FEED_SCHEDULES == {
        "daily": ({"hour": 9, "minute": 35, "second": 59}, timedelta(hours=1)),
        "hourly": ({"minute": 20, "second": 59}, timedelta(minutes=10)),
        "ten_minutes": ({"minute": "/10", "second": 59}, timedelta(minutes=1)),
    }


@pytest.mark.parametrize(
    ("data_type", "observed", "scheduled_for", "fresh"),
    [
        ("daily", "2026-10-26", datetime(2026, 10, 27, 9, 35, 59, tzinfo=UTC), True),
        ("daily", "2026-10-25", datetime(2026, 10, 27, 9, 35, 59, tzinfo=UTC), False),
        ("hourly", "2026-10-27T08:00:00+00:00", RUN, True),
        ("hourly", "2026-10-27T07:00:00+00:00", RUN, False),
        ("ten_minutes", "2026-10-27T08:10:00+00:00", RUN, True),
        ("ten_minutes", "2026-10-27T08:00:00+00:00", RUN, False),
    ],
)
def test_is_fresh(data_type, observed, scheduled_for, fresh):
    """Test is fresh."""
    assert _is_fresh(data_type, observed, scheduled_for) is fresh


async def test_a_feed_is_retried_until_the_due_data_is_there(monkeypatch):
    """Test a feed's schedule retries a failed or old download."""
    clock = FakeClock(monkeypatch)
    coordinator = feed_coordinator("daily")
    download = AsyncMock(return_value=({"time": "2026-10-25"}, []))
    coordinator.hass.async_add_executor_job = download
    coordinator.schedule.start()
    assert len(clock.tracked) == 1
    run, pattern = clock.tracked[-1]
    assert pattern == {"hour": 9, "minute": 35, "second": 59}
    due = datetime(2026, 10, 27, 9, 35, 59, tzinfo=UTC)

    # No enabled sensor: no download and no retry
    await run(due)
    download.assert_not_awaited()
    assert not clock.later

    # Old data is tried again an hour later
    coordinator.async_add_listener(MagicMock())
    await run(due)
    assert clock.later[-1][0] == timedelta(hours=1)

    # So are a failure and data without a time
    for result in (OSError("down"), ({}, [])):
        download.side_effect = [result]
        await clock.later[-1][1](None)
    assert len(clock.later) == 3

    # The due data ends the retries
    download.side_effect = [({"time": "2026-10-26"}, [])]
    await clock.later[-1][1](None)
    assert len(clock.later) == 3

    # Outside a schedule any download will do
    download.side_effect = [({"time": "2026-10-25"}, [])]
    assert await coordinator._async_scheduled_download() is True


def test_frame_time_and_freshness():
    """Test frame time and freshness."""
    url = "https://x/radar_composite-refl2D_pscappi-20261027_0800.png"
    assert frame_time(url) == datetime(2026, 10, 27, 8, 0, tzinfo=UTC)
    assert frame_time("https://x/other.png") is None
    assert frame_time(None) is None
    assert frame_is_fresh(url, datetime(2026, 10, 27, 8, 5, 59, tzinfo=UTC))
    assert frame_is_fresh(url, datetime(2026, 10, 27, 8, 9, 59, tzinfo=UTC))
    assert not frame_is_fresh(url, datetime(2026, 10, 27, 8, 10, 59, tzinfo=UTC))
    assert not frame_is_fresh(None, datetime(2026, 10, 27, 8, 5, 59, tzinfo=UTC))


async def test_radar_failed_rebuild_is_unavailable():
    """Test a failed radar rebuild makes the image unavailable."""
    hass = SimpleNamespace(
        async_add_executor_job=AsyncMock(return_value=(None, None)), data={}, bus=MagicMock()
    )
    image = HungarometRadarImage(hass, loaded_entry())
    image._added = True
    image.async_write_ha_state = MagicMock()

    assert await image.async_update_data() is None

    assert image.image_last_updated is None
    assert image.available is False
    image.async_write_ha_state.assert_called_once()


def test_latest_observations_drops_late_corrections():
    """Test latest observations drops late corrections."""
    df = table({
        "Time": [202610270750, 202610270800, 202610270800],
        "StationName": ["Kismacs", "Kismacs", "Repulover"],
        "t": [8.2, 9.1, 9.7],
    })

    result = latest_observations(df)

    assert column(result, "t") == [9.1, 9.7]
