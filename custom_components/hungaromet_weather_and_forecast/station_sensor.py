"""The weather station sensors of the daily, hourly and 10-minute feeds.

Each feed has its own device. A sensor shows one measurement, averaged over
the stations near the entry's location; the Stations sensor lists those
stations.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity

from .const import DEFAULT_DISTANCE_KM
from .device_classes import device_class_for
from .scope import EntryScope, device_info, scoped_id
from .weather_data import (
    WIND_DIRECTIONS,
    process_daily_data,
    process_hourly_data,
    process_ten_minutes_data,
)

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class Feed:
    """One of HungaroMet's observation feeds and the device that shows it."""

    # Also the device's translation key and its sensors' translation key prefix
    key: str
    device_id: str
    model: str
    process: Callable
    # The measurement time's device class: a date or a timestamp
    time_class: SensorDeviceClass


FEEDS = {
    feed.key: feed
    for feed in (
        Feed(
            "daily", "hungaromet_weather", "Daily weather",
            process_daily_data, SensorDeviceClass.DATE,
        ),
        Feed(
            "hourly", "hungaromet_weather_hourly", "Hourly weather",
            process_hourly_data, SensorDeviceClass.TIMESTAMP,
        ),
        Feed(
            "ten_minutes", "hungaromet_weather_ten_minutes", "10-minute weather",
            process_ten_minutes_data, SensorDeviceClass.TIMESTAMP,
        ),
    )
}


@dataclass(frozen=True)
class Measurement:
    """One value of a feed.

    The name is only part of the unique ID, which must not change; the entity
    name comes from the translations.
    """

    name: str
    key: str
    unit: str | None = None


@dataclass(frozen=True)
class StationQuery:
    """Which stations an entry's sensors average, and for which entry."""

    distance_km: float = DEFAULT_DISTANCE_KM
    # None means Home Assistant's home location
    location: tuple[float, float] | None = None
    scope: EntryScope | None = None


_m = Measurement

# Each feed's sensors, in the order they are added
MEASUREMENTS = {
    "daily": (
        _m("Napi mérési időpont", "time"),
        _m("Napi párolgás", "average_upe", "mm"),
        _m("Napi csapadékösszeg", "average_rau", "mm"),
        _m("Napi vízegyenleg", "average_water_balance", "mm"),
        _m("Napi átlaghőmérséklet", "t", "°C"),
        _m("Napi minimumhőmérséklet", "tn", "°C"),
        _m("Napi maximumhőmérséklet", "tx", "°C"),
        _m("Napi globálsugárzás összeg", "sr", "J/cm²"),
        _m("Napi globálsugárzás összeg (MJ/m²)", "sr_mj", "MJ/m²"),
        _m("Napi átlagos 5 cm-es talajhőmérséklet", "et5", "°C"),
        _m("Napi átlagos 10 cm-es talajhőmérséklet", "et10", "°C"),
        _m("Napi átlagos 20 cm-es talajhőmérséklet", "et20", "°C"),
        _m("Napi átlagos 50 cm-es talajhőmérséklet", "et50", "°C"),
        _m("Napi átlagos 100 cm-es talajhőmérséklet", "et100", "°C"),
        _m("Felszínközeli hőmérséklet napi minimuma", "tsn24", "°C"),
        _m("Napi átlagos látástávolság", "v", "m"),
        _m("Napi átlagos műszerszinti légnyomás", "p", "hPa"),
        _m("Napi átlagos tengerszinti légnyomás", "p0", "hPa"),
        _m("Napi átlagos relatív nedvesség", "u", "%"),
        _m("Napi átlagos gammadózis", "sg", "nSv/h"),
        _m("Napi UV sugárzás összeg", "suv", "MED"),
        _m("Napi átlagos szinoptikus szélsebesség", "fs", "m/s"),
        _m("Napi átlagos szélsebesség", "f", "m/s"),
        _m("Napi maximális széllökés sebessége", "fx", "m/s"),
        _m("Napi maximális széllökés iránya", "fxd", "°"),
        _m("Napi maximális széllökés ideje", "fxdat"),
        _m("Napi uralkodó szélirány", "fud"),
        _m("Napi átlagos vízhőmérséklet", "tviz", "°C"),
    ),
    "hourly": (
        _m("Órás mérési időpont", "time"),
        _m("Órás csapadékösszeg", "r", "mm"),
        _m("Órás pillanatnyi hőmérséklet", "t", "°C"),
        _m("Órás átlaghőmérséklet", "ta", "°C"),
        _m("Órás minimumhőmérséklet", "tn", "°C"),
        _m("Órás maximumhőmérséklet", "tx", "°C"),
        _m("Órás pillanatnyi relatív nedvesség", "u", "%"),
        _m("Órás átlagos gammadózis", "sg", "nSv/h"),
        _m("Órás globálsugárzás összeg", "sr", "J/cm²"),
        _m("Órás globálsugárzás összeg (MJ/m²)", "sr_mj", "MJ/m²"),
        _m("Órás UV sugárzás összeg", "suv", "MED"),
        _m("Órás szinoptikus szélsebesség", "fs", "m/s"),
        _m("Órás szinoptikus szélirány", "fsd", "°"),
        _m("Órás maximális széllökés sebessége", "fx", "m/s"),
        _m("Órás maximális széllökés iránya", "fxd", "°"),
        _m("Órás átlagos szélsebesség", "f", "m/s"),
        _m("Órás átlagos szélirány", "fd", "°"),
        _m("Órás felszínközeli hőmérséklet minimuma", "tsn", "°C"),
        _m("Órás pillanatnyi vízhőmérséklet", "tviz", "°C"),
        _m("Órás pillanatnyi időkép kódja", "we"),
        _m("Órás pillanatnyi látástávolság", "v", "m"),
        _m("Órás pillanatnyi műszerszinti légnyomás", "p", "hPa"),
        _m("Órás pillanatnyi tengerszinti légnyomás", "p0", "hPa"),
        _m("Órás maximális széllökés ideje", "fxdat"),
        _m("Órás pillanatnyi 5 cm-es talajhőmérséklet", "et5", "°C"),
        _m("Órás pillanatnyi 10 cm-es talajhőmérséklet", "et10", "°C"),
        _m("Órás pillanatnyi 20 cm-es talajhőmérséklet", "et20", "°C"),
        _m("Órás pillanatnyi 50 cm-es talajhőmérséklet", "et50", "°C"),
        _m("Órás pillanatnyi 100 cm-es talajhőmérséklet", "et100", "°C"),
    ),
    "ten_minutes": (
        _m("Tízperces mérési időpont", "time"),
        _m("Tízperces csapadékösszeg", "r", "mm"),
        _m("Tízperces pillanatnyi hőmérséklet", "t", "°C"),
        _m("Tízperces átlaghőmérséklet", "ta", "°C"),
        _m("Tízperces minimumhőmérséklet", "tn", "°C"),
        _m("Tízperces maximumhőmérséklet", "tx", "°C"),
        _m("Tízperces pillanatnyi relatív nedvesség", "u", "%"),
        _m("Tízperces átlagos gammadózis", "sg", "nSv/h"),
        _m("Tízperces globálsugárzás összeg", "sr", "J/cm²"),
        _m("Tízperces globálsugárzás összeg (MJ/m²)", "sr_mj", "MJ/m²"),
        _m("Tízperces UV sugárzás összeg", "suv", "MED"),
        _m("Tízperces maximális széllökés sebessége", "fx", "m/s"),
        _m("Tízperces maximális széllökés iránya", "fxd", "°"),
        _m("Tízperces átlagos szélsebesség", "fs", "m/s"),
        _m("Tízperces átlagos szélirány", "fsd", "°"),
        _m("Tízperces felszínközeli hőmérséklet minimuma", "tsn", "°C"),
        _m("Tízperces pillanatnyi vízhőmérséklet", "tviz", "°C"),
        _m("Tízperces pillanatnyi látástávolság", "v", "m"),
        _m("Tízperces pillanatnyi műszerszinti légnyomás", "p", "hPa"),
        _m("Tízperces maximális széllökés ideje", "fxdat"),
        _m("Tízperces 5 cm-es talajhőmérséklet", "et5", "°C"),
        _m("Tízperces 10 cm-es talajhőmérséklet", "et10", "°C"),
        _m("Tízperces 20 cm-es talajhőmérséklet", "et20", "°C"),
        _m("Tízperces 50 cm-es talajhőmérséklet", "et50", "°C"),
        _m("Tízperces 100 cm-es talajhőmérséklet", "et100", "°C"),
    ),
}

# Fields shown in the short "stations" list; "raw_data" keeps every column
STATION_FIELDS = ("StationNumber", "StationName", "Latitude", "Longitude", "Elevation")


def reading(data: dict, key: str):
    """Return a feed's value for a measurement: by its key, or its average."""
    value = data.get(key)
    return value if value is not None else data.get(f"average_{key}")


class FeedSensor(SensorEntity):
    """A sensor of one feed's device, updated by the integration's schedules."""

    _attr_has_entity_name = True
    # Updated by the integration's own schedules, not Home Assistant polling
    _attr_should_poll = False

    def __init__(self, hass, feed: Feed, query: StationQuery) -> None:
        self.hass = hass
        self._feed = feed
        self._query = query
        self._added = False

    @property
    def feed(self) -> Feed:
        """Return the feed this sensor shows."""
        return self._feed

    @property
    def added(self) -> bool:
        """Return whether Home Assistant has added the sensor (it is enabled)."""
        return self._added

    @property
    def device_info(self):
        return device_info(
            self._query.scope, self._feed.device_id, self._feed.key, self._feed.model
        )

    async def async_added_to_hass(self) -> None:
        self._added = True

    async def async_will_remove_from_hass(self) -> None:
        self._added = False

    async def async_update(self) -> None:
        """Download the feed and show it (Home Assistant's update_entity)."""
        if not self._added:
            return
        data, stations = await self.hass.async_add_executor_job(
            self._feed.process, self.hass, self._query.distance_km, self._query.location
        )
        self.show(data, stations)
        self.async_write_ha_state()

    def show(self, data: dict, stations: list[dict]) -> None:
        """Take this sensor's value from a download of its feed."""
        raise NotImplementedError


class StationSensor(FeedSensor):
    """One measurement of a feed, averaged over the selected stations."""

    def __init__(
        self, hass, feed: Feed, measurement: Measurement, value, query: StationQuery
    ) -> None:
        super().__init__(hass, feed, query)
        self._measurement = measurement
        self._value = value

    @property
    def _base_key(self) -> str:
        return self._measurement.key.removeprefix("average_").lower()

    @property
    def unique_id(self) -> str:
        name = self._measurement.name.lower().replace(" ", "_")
        return scoped_id(self._query.scope, f"{self._feed.device_id}_{name}")

    @property
    def translation_key(self) -> str:
        return f"{self._feed.key}_{self._base_key}"

    @property
    def device_class(self) -> SensorDeviceClass | None:
        if self._base_key == "time":
            return self._feed.time_class
        if self._base_key == "fud":
            # Compass points, translated through the entity's state translations
            return SensorDeviceClass.ENUM
        return device_class_for(self._base_key)

    @property
    def options(self) -> list[str] | None:
        return list(WIND_DIRECTIONS.values()) if self._base_key == "fud" else None

    @property
    def native_unit_of_measurement(self) -> str | None:
        return self._measurement.unit

    @property
    def native_value(self):
        if self._base_key == "time" and self._value:
            return self._time()
        if isinstance(self._value, (int, float)):
            return round(self._value, 2)
        return self._value

    def _time(self) -> date | datetime | None:
        """Return the measurement time: a date (daily) or a UTC time."""
        try:
            if self._feed.time_class == SensorDeviceClass.DATE:
                return date.fromisoformat(self._value)
            return datetime.fromisoformat(self._value)
        except ValueError as err:
            _LOGGER.warning("Failed to read the %s measurement time: %s", self._feed.key, err)
            return None

    def show(self, data: dict, stations: list[dict]) -> None:
        # A value missing from this download keeps the last one
        value = reading(data, self._measurement.key)
        if value is not None:
            self._value = value


class StationsSensor(FeedSensor):
    """The stations whose measurements a feed's sensors average."""

    _attr_translation_key = "stations"
    # Raw rows can exceed the recorder's attribute size limit and change constantly
    _unrecorded_attributes = frozenset({"raw_data"})

    def __init__(self, hass, feed: Feed, stations: list[dict], query: StationQuery) -> None:
        super().__init__(hass, feed, query)
        self._stations = stations

    @property
    def unique_id(self) -> str:
        # On the daily device's prefix, so existing entities keep their IDs
        return scoped_id(self._query.scope, f"hungaromet_weather_station_info_{self._feed.key}")

    @property
    def native_value(self) -> int:
        return len(self._stations or [])

    @property
    def extra_state_attributes(self):
        rows = self._stations or []
        return {
            "stations": [
                {field: row.get(field) for field in STATION_FIELDS} for row in rows
            ],
            "raw_data": rows,
        }

    def show(self, data: dict, stations: list[dict]) -> None:
        self._stations = stations
