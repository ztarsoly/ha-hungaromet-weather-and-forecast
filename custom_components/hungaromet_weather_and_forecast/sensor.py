"""Sensor platform: an entry's weather station, forecast and warning sensors.

Each observation feed has its own device. A station sensor shows one
measurement, averaged over the stations near the entry's location; the feed's
Stations sensor lists those stations. The forecast sensors and the alert and
warning sensors are for the entry's forecast settlement.
"""

from __future__ import annotations

import logging
from datetime import date, datetime

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
)
from homeassistant.const import (
    DEGREE,
    PERCENTAGE,
    UnitOfPrecipitationDepth,
    UnitOfPressure,
    UnitOfSpeed,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .coordinator import FeedCoordinator, ForecastCoordinator, WarningCoordinator
from .data import HungaroMetConfigEntry
from .device_classes import WIND_DIRECTION, device_class_for
from .entity import HungaroMetEntity, HungaroMetForecastEntity
from .failures import FailureLog
from .forecast_scraper import (
    CONDITION_STATES,
    WIND_DIRECTION_STATES,
    WIND_STRENGTH_STATES,
    ForecastData,
    ForecastValues,
)
from .scope import device_info, scoped_id
from .stations import MEASUREMENTS, Measurement, reading
from .warning_scraper import LEVEL_STATES, highest_level, in_force
from .weather_data import WIND_DIRECTIONS

_LOGGER = logging.getLogger(__package__)

# Fields shown in the short "stations" list; "raw_data" keeps every column
STATION_FIELDS = ("StationNumber", "StationName", "Latitude", "Longitude", "Elevation")

# These read today's summary instead of the current 6-hour period
DAILY_KEYS = frozenset({"temperature_min", "temperature_max"})


def _measurement(key, unit, device_class=None):
    # No state class: forecasts are kept out of the long-term statistics
    return SensorEntityDescription(
        key=key,
        translation_key=f"forecast_{key}",
        native_unit_of_measurement=unit,
        device_class=device_class,
        suggested_display_precision=1,
    )


FORECAST_SENSORS = (
    _measurement("temperature", UnitOfTemperature.CELSIUS, SensorDeviceClass.TEMPERATURE),
    _measurement(
        "temperature_min", UnitOfTemperature.CELSIUS, SensorDeviceClass.TEMPERATURE
    ),
    _measurement(
        "temperature_max", UnitOfTemperature.CELSIUS, SensorDeviceClass.TEMPERATURE
    ),
    _measurement(
        "precipitation",
        UnitOfPrecipitationDepth.MILLIMETERS,
        SensorDeviceClass.PRECIPITATION,
    ),
    _measurement("cloud_cover", PERCENTAGE),
    _measurement("wind_speed", UnitOfSpeed.KILOMETERS_PER_HOUR, SensorDeviceClass.WIND_SPEED),
    _measurement("wind_gust", UnitOfSpeed.KILOMETERS_PER_HOUR, SensorDeviceClass.WIND_SPEED),
    _measurement("wind_bearing", DEGREE, WIND_DIRECTION),
    # met.hu forecasts the sea-level pressure, like the stations' p0
    _measurement(
        "pressure", UnitOfPressure.HPA, SensorDeviceClass.ATMOSPHERIC_PRESSURE
    ),
    SensorEntityDescription(
        key="wind_direction",
        translation_key="forecast_wind_direction",
        device_class=SensorDeviceClass.ENUM,
        options=WIND_DIRECTION_STATES,
    ),
    # Worked out from the mean wind speed; met.hu gives no wind strength
    SensorEntityDescription(
        key="wind_strength",
        translation_key="forecast_wind_strength",
        device_class=SensorDeviceClass.ENUM,
        options=WIND_STRENGTH_STATES,
    ),
    SensorEntityDescription(
        key="weather_condition",
        translation_key="forecast_weather_condition",
        device_class=SensorDeviceClass.ENUM,
        options=CONDITION_STATES,
    ),
    SensorEntityDescription(
        key="issued",
        translation_key="forecast_issued",
        device_class=SensorDeviceClass.TIMESTAMP,
    ),
)


def _today(data: ForecastData) -> ForecastValues | None:
    """Return the daily summary for the current period's day."""
    if data.current is None or data.current.forecast_time is None:
        return None
    day = data.current.forecast_time.date()
    return next(
        (d for d in data.daily if d.forecast_time and d.forecast_time.date() == day),
        None,
    )


# The key is also the Warnings field and the attribute listing what is in force
WARNING_SENSORS = (
    SensorEntityDescription(
        key="alerts",
        translation_key="forecast_alert",
        device_class=SensorDeviceClass.ENUM,
        options=LEVEL_STATES,
    ),
    SensorEntityDescription(
        key="warnings",
        translation_key="forecast_warning",
        device_class=SensorDeviceClass.ENUM,
        options=LEVEL_STATES,
    ),
)


class ForecastSensor(
    HungaroMetForecastEntity, CoordinatorEntity[ForecastCoordinator], SensorEntity
):
    """One value of the settlement forecast."""

    entity_description: SensorEntityDescription

    def __init__(
        self, coordinator: ForecastCoordinator, description: SensorEntityDescription
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._join_forecast_device(coordinator, description.key)

    @property
    def available(self) -> bool:
        """Be unavailable without a value.

        A failed download leaves the last forecast to take the value from.
        """
        return self.native_value is not None

    @property
    def native_value(self):
        data = self.coordinator.data
        if data is None:
            return None
        key = self.entity_description.key
        if key == "issued":
            return data.issued
        source = _today(data) if key in DAILY_KEYS else data.current
        return getattr(source, key, None) if source else None


class WarningSensor(
    HungaroMetForecastEntity, CoordinatorEntity[WarningCoordinator], SensorEntity
):
    """The highest alert or warning level in force, with the phenomena in force."""

    entity_description: SensorEntityDescription

    def __init__(
        self, coordinator: WarningCoordinator, description: SensorEntityDescription
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._join_forecast_device(coordinator, description.key)

    @property
    def available(self) -> bool:
        """Be unavailable after a failed download, or without levels."""
        return super().available and self.native_value is not None

    def _levels(self) -> dict[str, str] | None:
        data = self.coordinator.data
        return getattr(data, self.entity_description.key) if data else None

    @property
    def native_value(self) -> str | None:
        levels = self._levels()
        return highest_level(levels) if levels is not None else None

    @property
    def extra_state_attributes(self) -> dict | None:
        """List the phenomena in force, named in Home Assistant's language."""
        levels = self._levels()
        if levels is None:
            return None
        return {
            self.entity_description.key: in_force(levels, self.hass.config.language)
        }


class FeedSensor(HungaroMetEntity, CoordinatorEntity[FeedCoordinator], SensorEntity):
    """A sensor of one feed's device; unavailable after a failed download."""

    def __init__(self, coordinator: FeedCoordinator) -> None:
        super().__init__(coordinator)
        self._feed = coordinator.feed
        self._query = coordinator.query

    @property
    def device_info(self):
        return device_info(
            self._query.scope, self._feed.device_id, self._feed.key, self._feed.model
        )


class StationSensor(FeedSensor):
    """One measurement of a feed, averaged over the selected stations."""

    def __init__(
        self, coordinator: FeedCoordinator, measurement: Measurement
    ) -> None:
        super().__init__(coordinator)
        self._measurement = measurement
        self._time_log = FailureLog(
            _LOGGER,
            f"Reading the {self._feed.key} measurement time",
            self._query.records,
        )

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
        return device_class_for(self._base_key, self._measurement.unit)

    @property
    def options(self) -> list[str] | None:
        return list(WIND_DIRECTIONS.values()) if self._base_key == "fud" else None

    @property
    def native_unit_of_measurement(self) -> str | None:
        return self._measurement.unit

    @property
    def suggested_display_precision(self) -> int | None:
        # Only the numeric measurements have a unit
        return 1 if self._measurement.unit else None

    @property
    def available(self) -> bool:
        """Be unavailable, not unknown, without a value."""
        return super().available and self.native_value is not None

    @property
    def native_value(self):
        data = self.coordinator.data
        value = reading(data[0], self._measurement.key) if data else None
        if self._base_key == "time" and value:
            return self._time(value)
        if isinstance(value, (int, float)):
            return round(value, 2)
        return value

    def _time(self, value: str) -> date | datetime | None:
        """Return the measurement time: a date (daily) or a UTC time."""
        parse = (
            date if self._feed.time_class == SensorDeviceClass.DATE else datetime
        ).fromisoformat
        try:
            time = parse(value)
        except ValueError as err:
            self._time_log.failed(err)
            return None
        self._time_log.succeeded()
        return time


class StationsSensor(FeedSensor):
    """The stations whose measurements a feed's sensors average."""

    _attr_translation_key = "stations"
    # Raw rows can exceed the recorder's attribute size limit and change constantly
    _unrecorded_attributes = frozenset({"raw_data"})

    @property
    def unique_id(self) -> str:
        # On the daily device's prefix, so existing entities keep their IDs
        return scoped_id(self._query.scope, f"hungaromet_weather_station_info_{self._feed.key}")

    def _rows(self) -> list[dict]:
        data = self.coordinator.data
        return data[1] if data else []

    @property
    def native_value(self) -> int:
        return len(self._rows())

    @property
    def extra_state_attributes(self):
        rows = self._rows()
        return {
            "stations": [
                {field: row.get(field) for field in STATION_FIELDS} for row in rows
            ],
            "raw_data": rows,
        }


def forecast_sensors(coordinator: ForecastCoordinator) -> list[ForecastSensor]:
    """Create every forecast sensor for a coordinator."""
    return [ForecastSensor(coordinator, d) for d in FORECAST_SENSORS]


def warning_sensors(coordinator: WarningCoordinator) -> list[WarningSensor]:
    """Create the alert and warning sensors."""
    return [WarningSensor(coordinator, d) for d in WARNING_SENSORS]


def station_sensors(coordinator: FeedCoordinator) -> list[FeedSensor]:
    """Create a feed's measurement sensors and its Stations sensor."""
    return [
        *(StationSensor(coordinator, m) for m in MEASUREMENTS[coordinator.feed.key]),
        StationsSensor(coordinator),
    ]


async def async_setup_entry(
    hass: HomeAssistant, entry: HungaroMetConfigEntry, async_add_entities
):
    """Add an entry's forecast sensors and weather station sensors."""
    if forecast := entry.runtime_data.forecast:
        async_add_entities(
            [*forecast_sensors(forecast), *warning_sensors(forecast.warnings)]
        )
    async_add_entities(
        [
            sensor
            for coordinator in entry.runtime_data.feeds.values()
            for sensor in station_sensors(coordinator)
        ]
    )
