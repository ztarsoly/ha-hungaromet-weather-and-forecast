import logging
from datetime import date

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity

from .weather_data import WIND_DIRECTIONS, process_daily_data
from .const import DEFAULT_DISTANCE_KM
from .device_classes import device_class_for
from .scope import device_info, scoped_id

_LOGGER = logging.getLogger(__name__)


class HungarometWeatherDailySensor(SensorEntity):
    _attr_has_entity_name = True
    # Updated by the integration's own schedules, not Home Assistant polling
    _attr_should_poll = False

    def __init__(
        self,
        hass,
        name,
        value,
        unit,
        key,
        distance_km=DEFAULT_DISTANCE_KM,
        location=None,
        scope=None,
    ):
        self.hass = hass
        self._name = name
        self._state = value
        self._unit = unit
        self._key = key
        self._device_id = "hungaromet_weather"
        self._scope = scope
        self._unique_id = scoped_id(
            scope, f"{self._device_id}_{self._name.lower().replace(' ', '_')}"
        )
        base_key = key.removeprefix("average_").lower()
        self._attr_translation_key = f"daily_{base_key}"
        if base_key == "fud":
            # Compass points, translated through the entity's state translations
            self._attr_device_class = SensorDeviceClass.ENUM
            self._attr_options = list(WIND_DIRECTIONS.values())
        elif base_key == "time":
            self._attr_device_class = SensorDeviceClass.DATE
        else:
            self._attr_device_class = device_class_for(base_key)
        self._added = False
        self._distance_km = distance_km
        # None means Home Assistant's home location
        self._location = location

    @property
    def native_value(self):
        if self._key == "time" and self._state:
            try:
                return date.fromisoformat(self._state)
            except ValueError as e:
                _LOGGER.warning("Failed to convert date for daily sensor: %s", e)
                return None
        if isinstance(self._state, (int, float)):
            return round(self._state, 2)
        return self._state if self._state is not None else None

    @property
    def native_unit_of_measurement(self):
        return self._unit

    @property
    def device_info(self):
        return device_info(self._scope, self._device_id, "daily", "Daily weather")

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

        data, _ = await self.hass.async_add_executor_job(
            process_daily_data, self.hass, self._distance_km, self._location
        )

        value = data.get(self._key)
        if value is None:
            value = data.get(f"average_{self._key}")
        if value is not None:
            self._state = value
        self.async_write_ha_state()

    async def async_update(self):
        await self.async_update_data()

    def update(self):
        pass
