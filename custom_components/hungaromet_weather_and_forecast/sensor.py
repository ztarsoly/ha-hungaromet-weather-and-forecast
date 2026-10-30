"""Home Assistant custom component for HungaroMet weather sensors."""

import logging
from datetime import date, datetime, timedelta
from functools import partial

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import CONF_DISTANCE_KM, DEFAULT_DISTANCE_KM, DOMAIN
from .daily_sensor import HungarometWeatherDailySensor
from .forecast import sensor_location
from .forecast_sensor import forecast_sensors
from .hourly_sensor import HungarometWeatherHourlySensor
from .scheduler import RetryingSchedule
from .scope import EntryScope
from .updaters import async_register_updater
from .station_info_sensor import HungarometStationInfoSensor
from .ten_minute_sensor import HungarometWeatherTenMinutesSensor
from .weather_data import (
    process_daily_data,
    process_hourly_data,
    process_ten_minutes_data,
)

_LOGGER = logging.getLogger(__name__)

WE_CODES = {
    1: "derült",
    2: "kissé felhős",
    3: "közepesen felhős",
    4: "erősen felhős",
    5: "borult",
    6: "fátyolfelhős",
    7: "ködös",
    9: "derült, párás",
    10: "közepesen felhős, párás",
    11: "borult, párás",
    12: "erősen fátyolfelhős",
    101: "szitálás",
    102: "eső",
    103: "zápor",
    104: "zivatar esővel",
    105: "ónos szitálás",
    106: "ónos eső",
    107: "hószállingózás",
    108: "havazás",
    109: "hózápor",
    110: "havaseső",
    112: "hózivatar",
    202: "erős eső",
    203: "erős zápor",
    208: "erős havazás",
    209: "erős hózápor",
    304: "zivatar záporral",
    310: "havaseső zápor",
    500: "hófúvás",
    600: "jégeső",
    601: "dörgés",
}


FEED_PROCESSORS = {
    "daily": process_daily_data,
    "hourly": process_hourly_data,
    "ten_minutes": process_ten_minutes_data,
}

FEED_SENSOR_CLASSES = {
    "daily": HungarometWeatherDailySensor,
    "hourly": HungarometWeatherHourlySensor,
    "ten_minutes": HungarometWeatherTenMinutesSensor,
}


def _feed_sensors(sensors, data_type):
    """Return the added value sensors and Stations sensors that show a feed."""
    feed_cls = FEED_SENSOR_CLASSES[data_type]
    active = [s for s in sensors if s.hass is not None and getattr(s, "_added", False)]
    value_sensors = [s for s in active if isinstance(s, feed_cls)]
    station_sensors = [
        s
        for s in active
        if isinstance(s, HungarometStationInfoSensor) and s._sensor_type == data_type
    ]
    return value_sensors, station_sensors


async def _async_update_feed(hass, sensors, data_type, distance_km, location=None):
    """Download one feed once and update every added sensor that shows it.

    Returns the feed's observation time, or None if nothing was updated.
    """
    value_sensors, station_sensors = _feed_sensors(sensors, data_type)
    if not value_sensors and not station_sensors:
        _LOGGER.debug(
            "HungaroMet: skipping %s update because no active entities are enabled",
            data_type,
        )
        return None

    try:
        data, station_info = await hass.async_add_executor_job(
            FEED_PROCESSORS[data_type], hass, distance_km, location
        )
    except Exception as err:  # pylint: disable=broad-exception-caught
        _LOGGER.error("Error updating %s sensors: %s", data_type, err)
        return None

    for sensor in value_sensors:
        value = data.get(sensor._key)
        if value is None:
            value = data.get(f"average_{sensor._key}")
        if value is None:
            continue
        sensor._state = value
        sensor.async_write_ha_state()
    for sensor in station_sensors:
        sensor._station_info = station_info
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


def _async_start_feed_schedules(
    hass, sensors, distance_km, location=None, config_entry=None
):
    """Start each feed's schedule and return the functions that stop them."""
    stops = []
    for data_type, (pattern, retry_delay) in FEED_SCHEDULES.items():

        async def job(scheduled_for, data_type=data_type):
            if not any(_feed_sensors(sensors, data_type)):
                return True
            observed = await _async_update_feed(
                hass, sensors, data_type, distance_km, location
            )
            return observed is not None and _is_fresh(
                data_type, observed, scheduled_for
            )

        schedule = RetryingSchedule(
            hass, data_type, job, retry_delay, config_entry=config_entry, **pattern
        )
        stops.append(schedule.start())
    return stops


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities
):
    # Forecast sensors first, so they do not depend on the station data loading
    if forecast := hass.data.get(DOMAIN, {}).get(entry.entry_id):
        async_add_entities(forecast_sensors(forecast))

    distance_km = entry.options.get(
        CONF_DISTANCE_KM, entry.data.get(CONF_DISTANCE_KM, DEFAULT_DISTANCE_KM)
    )
    location = sensor_location(entry)
    sensor_args = {
        "distance_km": distance_km,
        "location": location,
        "scope": EntryScope.from_entry(entry),
    }
    daily_sensor = partial(HungarometWeatherDailySensor, **sensor_args)
    hourly_sensor = partial(HungarometWeatherHourlySensor, **sensor_args)
    ten_minute_sensor = partial(HungarometWeatherTenMinutesSensor, **sensor_args)
    station_info_sensor = partial(HungarometStationInfoSensor, **sensor_args)
    sensors = []
    try:
        data, station_info = await hass.async_add_executor_job(
            process_daily_data, hass, distance_km, location
        )
        sensors.append(
            daily_sensor(
                hass, "Napi mérési időpont", data["time"], None, "time"
            )
        )
        sensors.append(
            daily_sensor(
                hass, "Napi párolgás", data["average_upe"], "mm", "average_upe"
            )
        )
        sensors.append(
            daily_sensor(
                hass, "Napi csapadékösszeg", data["average_rau"], "mm", "average_rau"
            )
        )
        sensors.append(
            daily_sensor(
                hass,
                "Napi vízegyenleg",
                data["average_water_balance"],
                "mm",
                "average_water_balance",
            )
        )
        sensors.append(
            daily_sensor(
                hass, "Napi átlaghőmérséklet", data["average_t"], "°C", "t"
            )
        )
        sensors.append(
            daily_sensor(
                hass, "Napi minimumhőmérséklet", data["average_tn"], "°C", "tn"
            )
        )
        sensors.append(
            daily_sensor(
                hass, "Napi maximumhőmérséklet", data["average_tx"], "°C", "tx"
            )
        )
        sensors.append(
            daily_sensor(
                hass, "Napi globálsugárzás összeg", data["average_sr"], "J/cm²", "sr"
            )
        )
        sensors.append(
            daily_sensor(
                hass,
                "Napi globálsugárzás összeg (MJ/m²)",
                data["average_sr_mj"],
                "MJ/m²",
                "sr_mj",
            )
        )
        sensors.append(
            daily_sensor(
                hass,
                "Napi átlagos 5 cm-es talajhőmérséklet",
                data["average_et5"],
                "°C",
                "et5",
            )
        )
        sensors.append(
            daily_sensor(
                hass,
                "Napi átlagos 10 cm-es talajhőmérséklet",
                data["average_et10"],
                "°C",
                "et10",
            )
        )
        sensors.append(
            daily_sensor(
                hass,
                "Napi átlagos 20 cm-es talajhőmérséklet",
                data["average_et20"],
                "°C",
                "et20",
            )
        )
        sensors.append(
            daily_sensor(
                hass,
                "Napi átlagos 50 cm-es talajhőmérséklet",
                data["average_et50"],
                "°C",
                "et50",
            )
        )
        sensors.append(
            daily_sensor(
                hass,
                "Napi átlagos 100 cm-es talajhőmérséklet",
                data["average_et100"],
                "°C",
                "et100",
            )
        )
        sensors.append(
            daily_sensor(
                hass,
                "Felszínközeli hőmérséklet napi minimuma",
                data["average_tsn24"],
                "°C",
                "tsn24",
            )
        )
        sensors.append(
            daily_sensor(
                hass,
                "Napi átlagos látástávolság",
                data.get("average_v"),
                "m",
                "v",
            )
        )
        sensors.append(
            daily_sensor(
                hass,
                "Napi átlagos műszerszinti légnyomás",
                data.get("average_p"),
                "hPa",
                "p",
            )
        )
        sensors.append(
            daily_sensor(
                hass,
                "Napi átlagos tengerszinti légnyomás",
                data.get("average_p0"),
                "hPa",
                "p0",
            )
        )
        sensors.append(
            daily_sensor(
                hass,
                "Napi átlagos relatív nedvesség",
                data.get("average_u"),
                "%",
                "u",
            )
        )
        sensors.append(
            daily_sensor(
                hass,
                "Napi átlagos gammadózis",
                data.get("average_sg"),
                "nSv/h",
                "sg",
            )
        )
        sensors.append(
            daily_sensor(
                hass,
                "Napi UV sugárzás összeg",
                data.get("average_suv"),
                "MED",
                "suv",
            )
        )
        sensors.append(
            daily_sensor(
                hass,
                "Napi átlagos szinoptikus szélsebesség",
                data.get("average_fs"),
                "m/s",
                "fs",
            )
        )
        sensors.append(
            daily_sensor(
                hass,
                "Napi átlagos szélsebesség",
                data.get("average_f"),
                "m/s",
                "f",
            )
        )
        sensors.append(
            daily_sensor(
                hass,
                "Napi maximális széllökés sebessége",
                data.get("average_fx"),
                "m/s",
                "fx",
            )
        )
        sensors.append(
            daily_sensor(
                hass,
                "Napi maximális széllökés iránya",
                data.get("average_fxd"),
                "°",
                "fxd",
            )
        )
        sensors.append(
            daily_sensor(
                hass,
                "Napi maximális széllökés ideje",
                data.get("fxdat"),
                None,
                "fxdat",
            )
        )
        sensors.append(
            daily_sensor(
                hass,
                "Napi uralkodó szélirány",
                data.get("fud"),
                None,
                "fud",
            )
        )
        sensors.append(
            daily_sensor(
                hass,
                "Napi átlagos vízhőmérséklet",
                data.get("average_tviz"),
                "°C",
                "tviz",
            )
        )
        sensors.append(
            station_info_sensor(
                hass, "HungaroMet Állomások", station_info, "daily"
            )
        )

        data, station_info = await hass.async_add_executor_job(
            process_hourly_data, hass, distance_km, location
        )
        sensors.append(
            hourly_sensor(
                hass, "Órás mérési időpont", data["time"], None, "time"
            )
        )
        sensors.append(
            hourly_sensor(
                hass, "Órás csapadékösszeg", data["average_r"], "mm", "r"
            )
        )
        sensors.append(
            hourly_sensor(
                hass, "Órás pillanatnyi hőmérséklet", data["average_t"], "°C", "t"
            )
        )
        sensors.append(
            hourly_sensor(
                hass, "Órás átlaghőmérséklet", data["average_ta"], "°C", "ta"
            )
        )
        sensors.append(
            hourly_sensor(
                hass, "Órás minimumhőmérséklet", data["average_tn"], "°C", "tn"
            )
        )
        sensors.append(
            hourly_sensor(
                hass, "Órás maximumhőmérséklet", data["average_tx"], "°C", "tx"
            )
        )
        sensors.append(
            hourly_sensor(
                hass, "Órás pillanatnyi relatív nedvesség", data["average_u"], "%", "u"
            )
        )
        sensors.append(
            hourly_sensor(
                hass, "Órás átlagos gammadózis", data["average_sg"], "nSv/h", "sg"
            )
        )
        sensors.append(
            hourly_sensor(
                hass, "Órás globálsugárzás összeg", data["average_sr"], "J/cm²", "sr"
            )
        )
        sensors.append(
            hourly_sensor(
                hass,
                "Órás globálsugárzás összeg (MJ/m²)",
                data["average_sr_mj"],
                "MJ/m²",
                "sr_mj",
            )
        )
        sensors.append(
            hourly_sensor(
                hass, "Órás UV sugárzás összeg", data["average_suv"], "MED", "suv"
            )
        )
        sensors.append(
            hourly_sensor(
                hass, "Órás szinoptikus szélsebesség", data["average_fs"], "m/s", "fs"
            )
        )
        sensors.append(
            hourly_sensor(
                hass, "Órás szinoptikus szélirány", data["average_fsd"], "°", "fsd"
            )
        )
        sensors.append(
            hourly_sensor(
                hass,
                "Órás maximális széllökés sebessége",
                data["average_fx"],
                "m/s",
                "fx",
            )
        )
        sensors.append(
            hourly_sensor(
                hass, "Órás maximális széllökés iránya", data["average_fxd"], "°", "fxd"
            )
        )
        sensors.append(
            hourly_sensor(
                hass, "Órás átlagos szélsebesség", data["average_f"], "m/s", "f"
            )
        )
        sensors.append(
            hourly_sensor(
                hass, "Órás átlagos szélirány", data["average_fd"], "°", "fd"
            )
        )
        sensors.append(
            hourly_sensor(
                hass,
                "Órás felszínközeli hőmérséklet minimuma",
                data["average_tsn"],
                "°C",
                "tsn",
            )
        )
        sensors.append(
            hourly_sensor(
                hass,
                "Órás pillanatnyi vízhőmérséklet",
                data["average_tviz"],
                "°C",
                "tviz",
            )
        )
        sensors.append(
            hourly_sensor(
                hass, "Órás pillanatnyi időkép kódja", data["we"], None, "we"
            )
        )
        sensors.append(
            hourly_sensor(
                hass,
                "Órás pillanatnyi látástávolság",
                data.get("average_v"),
                "m",
                "v",
            )
        )
        sensors.append(
            hourly_sensor(
                hass,
                "Órás pillanatnyi műszerszinti légnyomás",
                data.get("average_p"),
                "hPa",
                "p",
            )
        )
        sensors.append(
            hourly_sensor(
                hass,
                "Órás pillanatnyi tengerszinti légnyomás",
                data.get("average_p0"),
                "hPa",
                "p0",
            )
        )
        sensors.append(
            hourly_sensor(
                hass,
                "Órás maximális széllökés ideje",
                data.get("fxdat"),
                None,
                "fxdat",
            )
        )
        sensors.append(
            hourly_sensor(
                hass,
                "Órás pillanatnyi 5 cm-es talajhőmérséklet",
                data.get("average_et5"),
                "°C",
                "et5",
            )
        )
        sensors.append(
            hourly_sensor(
                hass,
                "Órás pillanatnyi 10 cm-es talajhőmérséklet",
                data.get("average_et10"),
                "°C",
                "et10",
            )
        )
        sensors.append(
            hourly_sensor(
                hass,
                "Órás pillanatnyi 20 cm-es talajhőmérséklet",
                data.get("average_et20"),
                "°C",
                "et20",
            )
        )
        sensors.append(
            hourly_sensor(
                hass,
                "Órás pillanatnyi 50 cm-es talajhőmérséklet",
                data.get("average_et50"),
                "°C",
                "et50",
            )
        )
        sensors.append(
            hourly_sensor(
                hass,
                "Órás pillanatnyi 100 cm-es talajhőmérséklet",
                data.get("average_et100"),
                "°C",
                "et100",
            )
        )
        sensors.append(
            station_info_sensor(
                hass, "HungaroMet Állomások", station_info, "hourly"
            )
        )

        data, station_info = await hass.async_add_executor_job(
            process_ten_minutes_data, hass, distance_km, location
        )
        sensors.append(
            ten_minute_sensor(
                hass, "Tízperces mérési időpont", data["time"], None, "time"
            )
        )
        sensors.append(
            ten_minute_sensor(
                hass, "Tízperces csapadékösszeg", data["average_r"], "mm", "r"
            )
        )
        sensors.append(
            ten_minute_sensor(
                hass, "Tízperces pillanatnyi hőmérséklet", data["average_t"], "°C", "t"
            )
        )
        sensors.append(
            ten_minute_sensor(
                hass, "Tízperces átlaghőmérséklet", data["average_ta"], "°C", "ta"
            )
        )
        sensors.append(
            ten_minute_sensor(
                hass, "Tízperces minimumhőmérséklet", data["average_tn"], "°C", "tn"
            )
        )
        sensors.append(
            ten_minute_sensor(
                hass, "Tízperces maximumhőmérséklet", data["average_tx"], "°C", "tx"
            )
        )
        sensors.append(
            ten_minute_sensor(
                hass,
                "Tízperces pillanatnyi relatív nedvesség",
                data["average_u"],
                "%",
                "u",
            )
        )
        sensors.append(
            ten_minute_sensor(
                hass, "Tízperces átlagos gammadózis", data["average_sg"], "nSv/h", "sg"
            )
        )
        sensors.append(
            ten_minute_sensor(
                hass,
                "Tízperces globálsugárzás összeg",
                data["average_sr"],
                "J/cm²",
                "sr",
            )
        )
        sensors.append(
            ten_minute_sensor(
                hass,
                "Tízperces globálsugárzás összeg (MJ/m²)",
                data["average_sr_mj"],
                "MJ/m²",
                "sr_mj",
            )
        )
        sensors.append(
            ten_minute_sensor(
                hass, "Tízperces UV sugárzás összeg", data["average_suv"], "MED", "suv"
            )
        )
        sensors.append(
            ten_minute_sensor(
                hass,
                "Tízperces maximális széllökés sebessége",
                data["average_fx"],
                "m/s",
                "fx",
            )
        )
        sensors.append(
            ten_minute_sensor(
                hass,
                "Tízperces maximális széllökés iránya",
                data["average_fxd"],
                "°",
                "fxd",
            )
        )
        sensors.append(
            ten_minute_sensor(
                hass, "Tízperces átlagos szélsebesség", data["average_fs"], "m/s", "fs"
            )
        )
        sensors.append(
            ten_minute_sensor(
                hass, "Tízperces átlagos szélirány", data["average_fsd"], "°", "fsd"
            )
        )
        sensors.append(
            ten_minute_sensor(
                hass,
                "Tízperces felszínközeli hőmérséklet minimuma",
                data["average_tsn"],
                "°C",
                "tsn",
            )
        )
        sensors.append(
            ten_minute_sensor(
                hass,
                "Tízperces pillanatnyi vízhőmérséklet",
                data["average_tviz"],
                "°C",
                "tviz",
            )
        )
        sensors.append(
            ten_minute_sensor(
                hass,
                "Tízperces pillanatnyi látástávolság",
                data.get("average_v"),
                "m",
                "v",
            )
        )
        sensors.append(
            ten_minute_sensor(
                hass,
                "Tízperces pillanatnyi műszerszinti légnyomás",
                data.get("average_p"),
                "hPa",
                "p",
            )
        )
        sensors.append(
            ten_minute_sensor(
                hass,
                "Tízperces maximális széllökés ideje",
                data.get("fxdat"),
                None,
                "fxdat",
            )
        )
        sensors.append(
            ten_minute_sensor(
                hass,
                "Tízperces 5 cm-es talajhőmérséklet",
                data.get("average_et5"),
                "°C",
                "et5",
            )
        )
        sensors.append(
            ten_minute_sensor(
                hass,
                "Tízperces 10 cm-es talajhőmérséklet",
                data.get("average_et10"),
                "°C",
                "et10",
            )
        )
        sensors.append(
            ten_minute_sensor(
                hass,
                "Tízperces 20 cm-es talajhőmérséklet",
                data.get("average_et20"),
                "°C",
                "et20",
            )
        )
        sensors.append(
            ten_minute_sensor(
                hass,
                "Tízperces 50 cm-es talajhőmérséklet",
                data.get("average_et50"),
                "°C",
                "et50",
            )
        )
        sensors.append(
            ten_minute_sensor(
                hass,
                "Tízperces 100 cm-es talajhőmérséklet",
                data.get("average_et100"),
                "°C",
                "et100",
            )
        )
        sensors.append(
            station_info_sensor(
                hass, "HungaroMet Állomások", station_info, "ten_minutes"
            )
        )

    except Exception as err:  # pragma: no cover - defensive logging
        _LOGGER.error("Failed to fetch/process weather data: %s", err)
        return
    async_add_entities(sensors, True)

    async def update_sensors_by_type(data_type: str):
        await _async_update_feed(hass, sensors, data_type, distance_km, location)

    async def update_entry():
        for data_type in FEED_PROCESSORS:
            await update_sensors_by_type(data_type)

    # The update service refreshes these feeds along with everything else
    entry.async_on_unload(
        async_register_updater(hass, f"{entry.entry_id}_feeds", update_entry)
    )

    for stop in _async_start_feed_schedules(
        hass, sensors, distance_km, location, config_entry=entry
    ):
        entry.async_on_unload(stop)
