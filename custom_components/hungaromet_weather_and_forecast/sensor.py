"""Sensor platform: an entry's weather station sensors and forecast sensors."""

import logging
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.event import async_call_later
from homeassistant.util import dt as dt_util

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
from .warning_sensor import warning_sensors

_LOGGER = logging.getLogger(__name__)


def _feed_sensors(sensors: list[FeedSensor], data_type: str) -> list[FeedSensor]:
    """Return the added sensors, value and Stations sensors, that show a feed."""
    return [
        s for s in sensors if s.hass is not None and s.added and s.feed.key == data_type
    ]


# How long after its observation a feed's values are shown while its downloads
# fail: until an hour after its next file was due (see FEED_SCHEDULES). The
# daily feed's age counts from the start of its day (UTC).
FEED_MAX_AGE = {
    "daily": timedelta(days=2, hours=10, minutes=35),
    "hourly": timedelta(hours=2, minutes=20),
    "ten_minutes": timedelta(hours=1, minutes=20),
}


def _expired(data_type: str, observed: str | None, now: datetime) -> bool:
    """Return whether values observed at `observed` are too old to show at `now`."""
    if not observed:
        return False
    try:
        moment = datetime.fromisoformat(observed)
    except ValueError:
        return False
    if moment.tzinfo is None:
        # The daily feed's day
        moment = moment.replace(tzinfo=UTC)
    return now - moment > FEED_MAX_AGE[data_type]


async def _async_update_feed(
    hass, sensors, data_type, query: StationQuery, observed: dict | None = None
):
    """Download one feed once and update every added sensor that shows it.

    `observed` holds each feed's observation time shown. When the download
    fails and the values shown are over FEED_MAX_AGE, the sensors are cleared.
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
        if observed is not None and _expired(
            data_type, observed.get(data_type), dt_util.utcnow()
        ):
            _LOGGER.info("Clearing the %s values, which are too old", data_type)
            del observed[data_type]
            for sensor in feed_sensors:
                sensor.clear()
                sensor.async_write_ha_state()
        return None

    for sensor in feed_sensors:
        sensor.show(data, stations)
        sensor.async_write_ha_state()
    if observed is not None:
        observed[data_type] = data.get("time")
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


def _async_start_feed_schedules(
    hass, sensors, query: StationQuery, config_entry=None, observed=None
):
    """Start each feed's schedule and return the functions that stop them."""
    stops = []
    for data_type, (pattern, retry_delay) in FEED_SCHEDULES.items():

        async def job(scheduled_for, data_type=data_type):
            if not _feed_sensors(sensors, data_type):
                return True
            latest = await _async_update_feed(hass, sensors, data_type, query, observed)
            return latest is not None and _is_fresh(data_type, latest, scheduled_for)

        schedule = RetryingSchedule(
            hass, data_type, job, retry_delay, config_entry=config_entry, **pattern
        )
        stops.append(schedule.start())
    return stops


async def _async_station_sensors(
    hass, query: StationQuery, observed: dict | None = None
) -> tuple[list[FeedSensor], list[str]]:
    """Download each feed once and create its sensors with the first values.

    A feed whose download fails still gets its sensors, without values. Returns
    the sensors and the keys of the feeds that failed; `observed` gets the
    observation times of the others.
    """
    sensors: list[FeedSensor] = []
    failed: list[str] = []
    for feed in FEEDS.values():
        try:
            data, stations = await hass.async_add_executor_job(
                feed.process, hass, query.distance_km, query.location
            )
        except Exception as err:  # pylint: disable=broad-exception-caught
            _LOGGER.error(
                "Error updating %s sensors: %s; retrying in %s",
                feed.key, err, FIRST_RETRY_DELAY,
            )
            failed.append(feed.key)
            data, stations = {}, []
        else:
            if observed is not None:
                observed[feed.key] = data.get("time")
        sensors.extend(
            StationSensor(hass, feed, measurement, reading(data, measurement.key), query)
            for measurement in MEASUREMENTS[feed.key]
        )
        sensors.append(StationsSensor(hass, feed, stations, query))
    return sensors, failed


# How long to wait before downloading a feed again whose first download failed
FIRST_RETRY_DELAY = timedelta(minutes=1)


def _async_retry_first_download(
    hass, sensors, data_type, query: StationQuery, observed: dict | None = None
) -> Callable[[], None]:
    """Download a feed again until it works, for sensors left without values.

    Its own schedule may be hours away (the daily feed's is once a day). Returns
    a function that stops the retries.
    """
    unsub: Callable[[], None] | None = None

    async def attempt(_now) -> None:
        nonlocal unsub
        unsub = None
        if not _feed_sensors(sensors, data_type):
            return
        if await _async_update_feed(hass, sensors, data_type, query, observed) is None:
            unsub = async_call_later(hass, FIRST_RETRY_DELAY, attempt)

    unsub = async_call_later(hass, FIRST_RETRY_DELAY, attempt)

    def stop() -> None:
        if unsub is not None:
            unsub()

    return stop


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
    # Each feed's observation time shown, to clear values too old to show
    observed: dict[str, str | None] = {}
    sensors, failed = await _async_station_sensors(hass, query, observed)
    async_add_entities(sensors, True)
    for data_type in failed:
        entry.async_on_unload(
            _async_retry_first_download(hass, sensors, data_type, query, observed)
        )

    async def update_entry():
        for data_type in FEEDS:
            await _async_update_feed(hass, sensors, data_type, query, observed)

    # The update service refreshes these feeds along with everything else
    entry.async_on_unload(
        async_register_updater(hass, f"{entry.entry_id}_feeds", update_entry)
    )

    for stop in _async_start_feed_schedules(
        hass, sensors, query, config_entry=entry, observed=observed
    ):
        entry.async_on_unload(stop)
