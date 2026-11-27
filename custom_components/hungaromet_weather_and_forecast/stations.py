"""HungaroMet's daily, hourly and 10-minute station observation feeds.

What each feed measures, and which stations an entry averages.
"""

from __future__ import annotations

import logging
import zipfile
from collections.abc import Callable
from dataclasses import dataclass, field

from homeassistant.components.sensor import SensorDeviceClass

from .const import CONF_DISTANCE_KM, DEFAULT_DISTANCE_KM
from .data import HungaroMetConfigEntry
from .failures import FailureRecords
from .scope import EntryScope
from .settlement import sensor_location
from .weather_data import (
    process_daily_data,
    process_hourly_data,
    process_ten_minutes_data,
)

_LOGGER = logging.getLogger(__package__)
# A download that can fail like this: the network, the file or its content
EXPECTED_ERRORS = (OSError, ValueError, zipfile.BadZipFile)


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
    # What is logged already: the entry's records, or the query's own
    records: FailureRecords = field(
        default_factory=FailureRecords, compare=False, repr=False
    )


def station_query(entry: HungaroMetConfigEntry) -> StationQuery:
    """Return which stations a location entry's sensors average."""
    return StationQuery(
        distance_km=entry.options.get(
            CONF_DISTANCE_KM, entry.data.get(CONF_DISTANCE_KM, DEFAULT_DISTANCE_KM)
        ),
        location=sensor_location(entry),
        scope=EntryScope.from_entry(entry),
        records=entry.runtime_data.failures,
    )


_m = Measurement

# Each feed's sensors, in the order they are added
MEASUREMENTS = {
    "daily": (
        _m("Napi mérési időpont", "time"),
        _m("Napi párolgás", "average_upe", "mm"),
        _m("Napi csapadékösszeg", "average_rau", "mm"),
        _m("Napi átlaghőmérséklet", "t", "°C"),
        _m("Napi minimumhőmérséklet", "tn", "°C"),
        _m("Napi maximumhőmérséklet", "tx", "°C"),
        _m("Napi globálsugárzás összeg", "sr", "J/cm²"),
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
        # Averages over the 10 minutes, unlike the daily and hourly sums; the
        # names stay, as the sensors' unique IDs come from them
        _m("Tízperces globálsugárzás összeg", "sr", "W/m²"),
        _m("Tízperces UV sugárzás összeg", "suv", "MED/h"),
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


def reading(data: dict, key: str):
    """Return a feed's value for a measurement: by its key, or its average."""
    value = data.get(key)
    return value if value is not None else data.get(f"average_{key}")


def warn_if_out_of_range(query: StationQuery, stations: list[dict]) -> None:
    """Warn once when the nearest station is used, as none is in the radius."""
    # The nearest one comes first
    if stations and stations[0]["Distance_km"] > query.distance_km:
        query.records.warn_once(
            _LOGGER,
            "No HungaroMet station with data within %s km; "
            "using nearest station %s (%.1f km)",
            query.distance_km,
            stations[0]["StationName"],
            stations[0]["Distance_km"],
        )
