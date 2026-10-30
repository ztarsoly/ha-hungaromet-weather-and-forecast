"""Sensor platform: an entry's weather station sensors and forecast sensors."""

import logging
from datetime import date, datetime, timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import CONF_DISTANCE_KM, DEFAULT_DISTANCE_KM, DOMAIN
from .forecast import sensor_location
from .forecast_sensor import forecast_sensors
from .scheduler import RetryingSchedule
from .scope import EntryScope
from .station_sensor import (
    FEEDS,
    MEASUREMENTS,
    FeedSensor,
    StationQuery,
    StationSensor,
    StationsSensor,
    reading,
)
from .updaters import async_register_updater

_LOGGER = logging.getLogger(__name__)


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

    try:
        data, stations = await hass.async_add_executor_job(
            FEEDS[data_type].process, hass, query.distance_km, query.location
        )
    except Exception as err:  # pylint: disable=broad-exception-caught
        # A failed download (network, missing file, unexpected CSV) must not
        # stop the schedule; it is retried
        _LOGGER.error("Error updating %s sensors: %s", data_type, err)
        return None

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


async def _async_station_sensors(hass, query: StationQuery) -> list[FeedSensor]:
    """Download each feed once and create its sensors with the first values."""
    sensors: list[FeedSensor] = []
    for feed in FEEDS.values():
        data, stations = await hass.async_add_executor_job(
            feed.process, hass, query.distance_km, query.location
        )
        sensors.extend(
            StationSensor(hass, feed, measurement, reading(data, measurement.key), query)
            for measurement in MEASUREMENTS[feed.key]
        )
        sensors.append(StationsSensor(hass, feed, stations, query))
    return sensors


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities
):
    """Add an entry's forecast sensors and weather station sensors."""
    # Forecast sensors first, so they do not depend on the station data loading
    if forecast := hass.data.get(DOMAIN, {}).get(entry.entry_id):
        async_add_entities(forecast_sensors(forecast))

    query = StationQuery(
        distance_km=entry.options.get(
            CONF_DISTANCE_KM, entry.data.get(CONF_DISTANCE_KM, DEFAULT_DISTANCE_KM)
        ),
        location=sensor_location(entry),
        scope=EntryScope.from_entry(entry),
    )
    try:
        sensors = await _async_station_sensors(hass, query)
    except Exception as err:  # pragma: no cover  # pylint: disable=broad-exception-caught
        # Without the first download there are no sensors; the entry stays set up
        # for its forecast and radar
        _LOGGER.error("Failed to fetch/process weather data: %s", err)
        return
    async_add_entities(sensors, True)

    async def update_entry():
        for data_type in FEEDS:
            await _async_update_feed(hass, sensors, data_type, query)

    # The update service refreshes these feeds along with everything else
    entry.async_on_unload(
        async_register_updater(hass, f"{entry.entry_id}_feeds", update_entry)
    )

    for stop in _async_start_feed_schedules(hass, sensors, query, config_entry=entry):
        entry.async_on_unload(stop)
