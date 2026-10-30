"""Sensors for the met.hu settlement forecast."""

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
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .device_classes import WIND_DIRECTION
from .forecast import ForecastCoordinator, forecast_device_info, forecast_scope
from .forecast_scraper import (
    CONDITION_STATES,
    WIND_DIRECTION_STATES,
    ForecastData,
    ForecastPeriod,
)
from .scope import scoped_id


# These read today's summary instead of the current 6-hour period
DAILY_KEYS = frozenset({"temperature_min", "temperature_max"})


def _measurement(key, unit, device_class=None):
    # No state class: forecasts are kept out of the long-term statistics
    return SensorEntityDescription(
        key=key,
        translation_key=f"forecast_{key}",
        native_unit_of_measurement=unit,
        device_class=device_class,
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
    _measurement("pressure", UnitOfPressure.HPA, SensorDeviceClass.PRESSURE),
    SensorEntityDescription(
        key="wind_direction",
        translation_key="forecast_wind_direction",
        device_class=SensorDeviceClass.ENUM,
        options=WIND_DIRECTION_STATES,
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


def _today(data: ForecastData) -> ForecastPeriod | None:
    """Return the daily summary for the current period's day."""
    if data.current is None or data.current.forecast_time is None:
        return None
    day = data.current.forecast_time.date()
    return next(
        (d for d in data.daily if d.forecast_time and d.forecast_time.date() == day),
        None,
    )


class ForecastSensor(CoordinatorEntity[ForecastCoordinator], SensorEntity):
    """One value of the settlement forecast, with its upcoming values attached."""

    _attr_has_entity_name = True
    # The forecast lists are long and change with every reissue
    _unrecorded_attributes = frozenset({"forecast", "daily_forecast"})
    entity_description: SensorEntityDescription

    def __init__(
        self, coordinator: ForecastCoordinator, description: SensorEntityDescription
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        scope = forecast_scope(coordinator)
        self._attr_unique_id = scoped_id(scope, f"hungaromet_forecast_{description.key}")
        self._attr_device_info = forecast_device_info(scope)

    @property
    def available(self) -> bool:
        """Keep showing the last forecast when a download fails."""
        return self.coordinator.data is not None

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

    @property
    def extra_state_attributes(self):
        data = self.coordinator.data
        key = self.entity_description.key
        if data is None or key == "issued":
            return None
        attrs = {"settlement": data.settlement}
        if data.current and data.current.forecast_time:
            attrs["forecast_time"] = data.current.forecast_time.isoformat()
        attrs["forecast"] = [
            {"datetime": p.forecast_time.isoformat(), "value": getattr(p, key)}
            for p in data.periods
            if p.forecast_time and getattr(p, key) is not None
        ]
        attrs["daily_forecast"] = [
            {"date": p.forecast_time.date().isoformat(), "value": getattr(p, key)}
            for p in data.daily
            if p.forecast_time and getattr(p, key) is not None
        ]
        return attrs


def forecast_sensors(coordinator: ForecastCoordinator) -> list[ForecastSensor]:
    """Create every forecast sensor for a coordinator."""
    return [ForecastSensor(coordinator, d) for d in FORECAST_SENSORS]
