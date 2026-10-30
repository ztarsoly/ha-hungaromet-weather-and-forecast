import logging

from homeassistant.components.sensor import SensorEntity

from .const import DEFAULT_DISTANCE_KM
from .scope import device_info, scoped_id
from .weather_data import (
    process_daily_data,
    process_hourly_data,
    process_ten_minutes_data,
)

_LOGGER = logging.getLogger(__name__)

PROCESSORS = {
    "hourly": process_hourly_data,
    "ten_minutes": process_ten_minutes_data,
}

# Device identifier, device translation key and model for each feed's device
DEVICES = {
    "daily": ("hungaromet_weather", "daily", "Daily weather"),
    "hourly": ("hungaromet_weather_hourly", "hourly", "Hourly weather"),
    "ten_minutes": (
        "hungaromet_weather_ten_minutes",
        "ten_minutes",
        "10-minute weather",
    ),
}

# Fields shown in the short "stations" list; "raw_data" keeps every column
STATION_FIELDS = ("StationNumber", "StationName", "Latitude", "Longitude", "Elevation")


class HungarometStationInfoSensor(SensorEntity):
    _attr_has_entity_name = True
    # Updated by the integration's own schedules, not Home Assistant polling
    _attr_should_poll = False
    # Raw rows can exceed the recorder's attribute size limit and change constantly
    _unrecorded_attributes = frozenset({"raw_data"})

    def __init__(
        self,
        hass,
        name,
        station_info,
        sensor_type="daily",
        distance_km=DEFAULT_DISTANCE_KM,
        location=None,
        scope=None,
    ):
        self.hass = hass
        self._name = name
        self._station_info = station_info
        self._device_id, self._device_key, self._model = DEVICES.get(
            sensor_type, DEVICES["daily"]
        )
        self._sensor_type = sensor_type
        self._distance_km = distance_km
        self._location = location
        self._scope = scope
        # Kept on the daily device's prefix so existing entities keep their IDs
        self._unique_id = scoped_id(
            scope, f"hungaromet_weather_station_info_{sensor_type}"
        )
        self._attr_translation_key = "stations"
        self._added = False

    @property
    def state(self):
        return len(self._station_info) if self._station_info is not None else 0

    @property
    def extra_state_attributes(self):
        rows = self._station_info or []
        return {
            "stations": [
                {field: row.get(field) for field in STATION_FIELDS} for row in rows
            ],
            "raw_data": rows,
        }

    @property
    def device_info(self):
        return device_info(self._scope, self._device_id, self._device_key, self._model)

    @property
    def unique_id(self):
        return self._unique_id

    async def async_added_to_hass(self):
        self._added = True
        _LOGGER.debug(
            "Entity %s added to hass with unique_id %s",
            self._name,
            self._unique_id,
        )

    async def async_will_remove_from_hass(self):
        self._added = False
        _LOGGER.debug(
            "Entity %s removed from hass; skipping scheduled updates",
            self._name,
        )

    async def async_update_data(self):
        if not self._added:
            return
        process = PROCESSORS.get(self._sensor_type, process_daily_data)
        _, stations = await self.hass.async_add_executor_job(
            process, self.hass, self._distance_km, self._location
        )
        self._station_info = stations
        self.async_write_ha_state()

    async def async_update(self):
        await self.async_update_data()

    def update(self):
        pass
