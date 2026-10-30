"""Tests for the update schedules, retries and freshness checks."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd
import pytest

from custom_components.hungaromet_weather_and_forecast import scheduler
from custom_components.hungaromet_weather_and_forecast.daily_sensor import HungarometWeatherDailySensor
from custom_components.hungaromet_weather_and_forecast.national_radar import HungarometRadarImage
from custom_components.hungaromet_weather_and_forecast.radar import _frame_is_fresh, frame_time
from custom_components.hungaromet_weather_and_forecast.scheduler import RetryingSchedule
from custom_components.hungaromet_weather_and_forecast.sensor import (
    FEED_SCHEDULES,
    _async_start_feed_schedules,
    _is_fresh,
)
from custom_components.hungaromet_weather_and_forecast.weather_data import latest_observations

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
        self.tracked.append((action, pattern))
        return lambda: self.cancelled.append("schedule")

    def call_later(self, hass, delay, action):
        handle = len(self.later)
        self.later.append((delay, action))
        return lambda: self.cancelled.append(f"retry{handle}")


@pytest.mark.asyncio
async def test_schedule_runs_job_without_retry_when_fresh(monkeypatch):
    clock = FakeClock(monkeypatch)
    job = AsyncMock(return_value=True)
    RetryingSchedule(None, "test", job, timedelta(minutes=1), minute=5).start()

    action, pattern = clock.tracked[0]
    assert pattern == {"minute": 5}
    await action(RUN)

    job.assert_awaited_once_with(RUN)
    assert clock.later == []


@pytest.mark.asyncio
async def test_schedule_retries_with_same_run_time_until_fresh(monkeypatch):
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


@pytest.mark.asyncio
async def test_next_scheduled_run_cancels_pending_retry(monkeypatch):
    clock = FakeClock(monkeypatch)
    job = AsyncMock(return_value=False)
    RetryingSchedule(None, "test", job, timedelta(minutes=1)).start()
    action = clock.tracked[0][0]

    await action(RUN)
    await action(RUN + timedelta(minutes=5))

    assert clock.cancelled == ["retry0"]
    assert job.await_args_list[-1].args == (RUN + timedelta(minutes=5),)


@pytest.mark.asyncio
async def test_stop_cancels_schedule_and_retry(monkeypatch):
    clock = FakeClock(monkeypatch)
    stop = RetryingSchedule(
        None, "test", AsyncMock(return_value=False), timedelta(minutes=1)
    ).start()
    await clock.tracked[0][0](RUN)

    stop()
    stop()

    assert clock.cancelled == ["retry0", "schedule"]


@pytest.mark.asyncio
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

    # Running now (startup, update service) still works
    await schedule.async_run_now()
    assert job.await_count == 2


def test_feed_schedules_follow_hungaromet_publishing():
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
    assert _is_fresh(data_type, observed, scheduled_for) is fresh


@pytest.mark.asyncio
async def test_feed_schedule_jobs_report_freshness(monkeypatch):
    clock = FakeClock(monkeypatch)
    hass = SimpleNamespace(
        async_add_executor_job=AsyncMock(return_value=({"time": "2026-10-25"}, []))
    )
    daily = HungarometWeatherDailySensor(hass, "D", 1.0, "°C", "t")
    daily.async_write_ha_state = MagicMock()

    stops = _async_start_feed_schedules(hass, [daily], 20)
    assert len(stops) == 3
    # Without a config entry there is no polling option to follow
    assert all(s.__self__._config_entry is None for s in stops)
    daily_run, hourly_run = clock.tracked[0][0], clock.tracked[1][0]

    # Nothing added yet: no download and no retry
    await daily_run(datetime(2026, 10, 27, 9, 35, 59, tzinfo=UTC))
    hass.async_add_executor_job.assert_not_awaited()
    assert clock.later == []

    # Stale daily data schedules a retry an hour later
    daily._added = True
    await daily_run(datetime(2026, 10, 27, 9, 35, 59, tzinfo=UTC))
    assert clock.later[0][0] == timedelta(hours=1)

    # Hourly has no added sensors, so it neither downloads nor retries
    await hourly_run(RUN)
    assert len(clock.later) == 1


def test_frame_time_and_freshness():
    url = "https://x/radar_composite-refl2D_pscappi-20261027_0800.png"
    assert frame_time(url) == datetime(2026, 10, 27, 8, 0, tzinfo=UTC)
    assert frame_time("https://x/other.png") is None
    assert frame_time(None) is None
    assert _frame_is_fresh(url, datetime(2026, 10, 27, 8, 5, 59, tzinfo=UTC))
    assert _frame_is_fresh(url, datetime(2026, 10, 27, 8, 9, 59, tzinfo=UTC))
    assert not _frame_is_fresh(url, datetime(2026, 10, 27, 8, 10, 59, tzinfo=UTC))
    assert not _frame_is_fresh(None, datetime(2026, 10, 27, 8, 5, 59, tzinfo=UTC))


@pytest.mark.asyncio
async def test_radar_failed_rebuild_keeps_previous_state():
    hass = SimpleNamespace(
        async_add_executor_job=AsyncMock(return_value=(None, None)), data={}, bus=MagicMock()
    )
    image = HungarometRadarImage(hass)
    image._added = True
    image.async_write_ha_state = MagicMock()

    assert await image.async_update_data() is None

    assert image._update_counter == 0
    image.async_write_ha_state.assert_not_called()


def test_latest_observations_drops_late_corrections():
    df = pd.DataFrame({
        "Time": [202610270750, 202610270800, 202610270800],
        "StationName": ["Kismacs", "Kismacs", "Repulover"],
        "t": [8.2, 9.1, 9.7],
    })

    result = latest_observations(df)

    assert list(result["t"]) == [9.1, 9.7]
