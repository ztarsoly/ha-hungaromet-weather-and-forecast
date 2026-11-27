"""Weather entity for the met.hu settlement forecast."""

from homeassistant.components.weather import (
    Forecast,
    SingleCoordinatorWeatherEntity,
    WeatherEntityFeature,
)
from homeassistant.const import (
    UnitOfPrecipitationDepth,
    UnitOfPressure,
    UnitOfSpeed,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import ForecastCoordinator
from .data import HungaroMetConfigEntry
from .entity import HungaroMetForecastEntity
from .forecast_scraper import ForecastPeriod, ForecastValues


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HungaroMetConfigEntry,
    async_add_entities: AddEntitiesCallback,
):
    """Add the forecast weather entity when a settlement is configured."""
    coordinator = entry.runtime_data.forecast
    if coordinator is not None:
        async_add_entities([HungarometForecastWeather(coordinator)])


def _forecast(p: ForecastValues, daily: bool) -> Forecast:
    """Convert a period or a day's summary to Home Assistant's forecast format."""
    forecast: Forecast = {
        "datetime": p.forecast_time.isoformat(),
        "condition": p.weather_condition,
    }
    temperature = p.temperature_max if daily else p.temperature
    values = {
        "native_temperature": temperature,
        "native_templow": p.temperature_min if daily else None,
        "native_precipitation": p.precipitation,
        "native_wind_speed": p.wind_speed,
        "native_wind_gust_speed": p.wind_gust,
        "wind_bearing": p.wind_bearing,
        "cloud_coverage": p.cloud_cover,
        "native_pressure": p.pressure,
    }
    forecast.update({k: v for k, v in values.items() if v is not None})
    return forecast


# Home Assistant's weather base classes are deep, and the twice-daily forecast is
# optional: it is only requested when the feature flag says it is supported
class HungarometForecastWeather(  # pylint: disable=too-many-ancestors,abstract-method
    HungaroMetForecastEntity, SingleCoordinatorWeatherEntity[ForecastCoordinator]
):
    """Current conditions and the daily and 6-hourly forecast from met.hu."""

    _attr_name = None  # Named after the device
    _attr_attribution = "Weather forecast by HungaroMet"
    _attr_native_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_native_precipitation_unit = UnitOfPrecipitationDepth.MILLIMETERS
    _attr_native_wind_speed_unit = UnitOfSpeed.KILOMETERS_PER_HOUR
    _attr_native_pressure_unit = UnitOfPressure.HPA
    _attr_supported_features = (
        WeatherEntityFeature.FORECAST_DAILY | WeatherEntityFeature.FORECAST_HOURLY
    )

    def __init__(self, coordinator: ForecastCoordinator) -> None:
        super().__init__(coordinator)
        self._join_forecast_device(coordinator, "weather")

    @property
    def available(self) -> bool:
        """Be unavailable without a condition.

        A failed download leaves the last forecast to take it from.
        """
        return self.condition is not None

    @property
    def _current(self) -> ForecastPeriod | None:
        data = self.coordinator.data
        return data.current if data else None

    @property
    def condition(self):
        return self._current.weather_condition if self._current else None

    @property
    def native_temperature(self):
        return self._current.temperature if self._current else None

    @property
    def native_pressure(self):
        return self._current.pressure if self._current else None

    @property
    def native_wind_speed(self):
        return self._current.wind_speed if self._current else None

    @property
    def native_wind_gust_speed(self):
        return self._current.wind_gust if self._current else None

    @property
    def wind_bearing(self):
        return self._current.wind_bearing if self._current else None

    @property
    def cloud_coverage(self):
        return self._current.cloud_cover if self._current else None

    @property
    def extra_state_attributes(self):
        if self.coordinator.data is None:
            return None
        return {
            # Home Assistant's weather entity has no current precipitation of its own
            "precipitation": self._current.precipitation if self._current else None,
        }

    @callback
    def _async_forecast_daily(self) -> list[Forecast] | None:
        if not self.available:
            return None
        return [_forecast(p, daily=True) for p in self.coordinator.data.daily]

    @callback
    def _async_forecast_hourly(self) -> list[Forecast] | None:
        """One entry per hour from the current hour on, interpolated between rows."""
        if not self.available:
            return None
        data = self.coordinator.data
        start = data.current.forecast_time
        return [
            _forecast(p, daily=False)
            for p in data.hourly
            if p.forecast_time >= start
        ]
