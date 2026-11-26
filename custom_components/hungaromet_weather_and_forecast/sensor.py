"""Sensor platform: an entry's weather station sensors and forecast sensors."""

import logging
from collections.abc import Callable
from datetime import date, datetime, timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import CONF_DISTANCE_KM, DEFAULT_DISTANCE_KM, DOMAIN
from .forecast import sensor_location
from .forecast_sensor import forecast_sensors
from .scheduler import RetryingSchedule, async_retry_every_minute
from .scope import EntryScope
from .station_sensor import (
    FEEDS,
    MEASUREMENTS,
    FeedSensor,
    StationQuery,
    StationSensor,
    StationsSensor,
    async_download_feed,
    reading,
)
from .updaters import async_register_updater
from .warning_sensor import warning_sensors

_LOGGER = logging.getLogger(__package__)


def _feed_sensors(sensors: list[FeedSensor], data_type: str) -> list[FeedSensor]:
    """Return the added sensors, value and Stations sensors, that show a feed."""
    return [
        s for s in sensors if s.hass is not None and s.added and s.feed.key == data_type
    ]


async def _async_update_feed(hass, sensors, data_type, query: StationQuery):
    """Download one feed once and update every added sensor that shows it.

    Returns the feed's observation time, or None if nothing was updated.
    """
    feed_sensors = _feed_sensors(sensors, data_type)
    if not feed_sensors:
        _LOGGER.debug(
            "HungaroMet: skipping %s update because no active entities are enabled",
            data_type,
        )
        return None

    result = await async_download_feed(hass, FEEDS[data_type], query)
    if result is None:
        for sensor in feed_sensors:
            sensor.mark_download_failed()
            sensor.async_write_ha_state()
        return None

    data, stations = result
    for sensor in feed_sensors:
        sensor.show(data, stations)
        sensor.async_write_ha_state()
    return data.get("time")


# When each feed is checked (UTC, matching HungaroMet's publishing times) and how
# long to wait before trying again while its data is not fresh yet
FEED_SCHEDULES = {
    "daily": ({"hour": 9, "minute": 35, "second": 59}, timedelta(hours=1)),
    "hourly": ({"minute": 20, "second": 59}, timedelta(minutes=10)),
    "ten_minutes": ({"minute": "/10", "second": 59}, timedelta(minutes=1)),
}


def _is_fresh(data_type, observed, scheduled_for):
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


def _async_start_feed_schedules(hass, sensors, query: StationQuery, config_entry=None):
    """Start each feed's schedule and return the functions that stop them."""
    stops = []
    for data_type, (pattern, retry_delay) in FEED_SCHEDULES.items():

        async def job(scheduled_for, data_type=data_type):
            if not _feed_sensors(sensors, data_type):
                return True
            observed = await _async_update_feed(hass, sensors, data_type, query)
            return observed is not None and _is_fresh(
                data_type, observed, scheduled_for
            )

        schedule = RetryingSchedule(
            hass, data_type, job, retry_delay, config_entry=config_entry, **pattern
        )
        stops.append(schedule.start())
    return stops


async def _async_station_sensors(
    hass, query: StationQuery
) -> tuple[list[FeedSensor], list[str]]:
    """Download each feed once and create its sensors with the first values.

    A feed whose download fails still gets its sensors, unavailable until a
    download works. Returns the sensors and the keys of the feeds that failed.
    """
    sensors: list[FeedSensor] = []
    failed: list[str] = []
    for feed in FEEDS.values():
        result = await async_download_feed(hass, feed, query)
        if result is None:
            failed.append(feed.key)
        data, stations = result or ({}, [])
        feed_sensors: list[FeedSensor] = [
            StationSensor(hass, feed, measurement, reading(data, measurement.key), query)
            for measurement in MEASUREMENTS[feed.key]
        ]
        feed_sensors.append(StationsSensor(hass, feed, stations, query))
        if feed.key in failed:
            for sensor in feed_sensors:
                sensor.mark_download_failed()
        sensors.extend(feed_sensors)
    return sensors, failed


def _async_retry_first_download(
    hass, sensors, data_type, query: StationQuery
) -> Callable[[], None]:
    """Download a feed every minute until it works, besides its schedule.

    The schedule's next run may be hours away (the daily feed's is once a day).
    Returns a function that stops the retries.
    """

    async def attempt() -> bool:
        if not _feed_sensors(sensors, data_type):
            return True
        return await _async_update_feed(hass, sensors, data_type, query) is not None

    return async_retry_every_minute(hass, attempt)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities
):
    """Add an entry's forecast sensors and weather station sensors."""
    # Forecast sensors first, so they do not depend on the station data loading
    if forecast := hass.data.get(DOMAIN, {}).get(entry.entry_id):
        async_add_entities(
            [*forecast_sensors(forecast), *warning_sensors(forecast.warnings)]
        )

    query = StationQuery(
        distance_km=entry.options.get(
            CONF_DISTANCE_KM, entry.data.get(CONF_DISTANCE_KM, DEFAULT_DISTANCE_KM)
        ),
        location=sensor_location(entry),
        scope=EntryScope.from_entry(entry),
    )
    sensors, failed = await _async_station_sensors(hass, query)
    async_add_entities(sensors, True)
    for data_type in failed:
        entry.async_on_unload(
            _async_retry_first_download(hass, sensors, data_type, query)
        )

    async def update_entry():
        for data_type in FEEDS:
            await _async_update_feed(hass, sensors, data_type, query)

    # The update service refreshes these feeds along with everything else
    entry.async_on_unload(
        async_register_updater(hass, f"{entry.entry_id}_feeds", update_entry)
    )

    for stop in _async_start_feed_schedules(hass, sensors, query, config_entry=entry):
        entry.async_on_unload(stop)
