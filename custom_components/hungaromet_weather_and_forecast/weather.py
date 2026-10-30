"""Weather entity for the met.hu settlement forecast."""

from homeassistant.components.weather import (
    Forecast,
    SingleCoordinatorWeatherEntity,
    WeatherEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    UnitOfPrecipitationDepth,
    UnitOfPressure,
    UnitOfSpeed,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .forecast import ForecastCoordinator, forecast_device_info, forecast_scope
from .forecast_scraper import ForecastPeriod
from .scope import scoped_id


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
):
    """Add the forecast weather entity when a settlement is configured."""
    coordinator = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    if coordinator is not None:
        async_add_entities([HungarometForecastWeather(coordinator)])


def _forecast(p: ForecastPeriod, daily: bool) -> Forecast:
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
    SingleCoordinatorWeatherEntity[ForecastCoordinator]
):
    """Current conditions and the daily and 6-hourly forecast from met.hu."""

    _attr_has_entity_name = True
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
        scope = forecast_scope(coordinator)
        self._attr_unique_id = scoped_id(scope, "hungaromet_forecast_weather")
        self._attr_device_info = forecast_device_info(scope)

    @property
    def available(self) -> bool:
        """Keep showing the last forecast when a download fails."""
        return self.coordinator.data is not None

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
        data = self.coordinator.data
        if data is None:
            return None
        return {
            "settlement": data.settlement,
            "issued": data.issued.isoformat() if data.issued else None,
        }

    @callback
    def _async_forecast_daily(self) -> list[Forecast] | None:
        data = self.coordinator.data
        return [_forecast(p, daily=True) for p in data.daily] if data else None

    @callback
    def _async_forecast_hourly(self) -> list[Forecast] | None:
        """One entry per hour from the current hour on, interpolated between rows."""
        data = self.coordinator.data
        if data is None:
            return None
        start = data.current.forecast_time if data.current else None
        return [
            _forecast(p, daily=False)
            for p in data.hourly or data.periods
            if start is None or p.forecast_time >= start
        ]
