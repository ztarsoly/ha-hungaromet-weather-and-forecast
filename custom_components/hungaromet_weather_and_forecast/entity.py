"""What the integration's entities share."""

from homeassistant.helpers.entity import Entity

from .const import FORECAST_DEVICE_ID
from .coordinator import ScheduledCoordinator
from .forecast_scraper import FORECAST_PAGE_URL
from .scope import EntryScope, device_info, scoped_id


class HungaroMetEntity(Entity):
    """The base of every entity: named after its device, never polled."""

    _attr_has_entity_name = True
    # Updated by the integration's own schedules, not Home Assistant polling
    _attr_should_poll = False


def forecast_device_info(scope: EntryScope) -> dict:
    """Device info shared by the forecast weather entity and sensors."""
    info = device_info(scope, FORECAST_DEVICE_ID, "forecast", "Weather forecast")
    info["configuration_url"] = FORECAST_PAGE_URL
    return info


class HungaroMetForecastEntity(HungaroMetEntity):
    """An entity of an entry's forecast device, fed by a coordinator."""

    def _join_forecast_device(self, coordinator: ScheduledCoordinator, key: str) -> None:
        """Set the unique ID and the device; the key must not change."""
        scope = EntryScope.from_entry(coordinator.config_entry)
        self._attr_unique_id = scoped_id(scope, f"hungaromet_forecast_{key}")
        self._attr_device_info = forecast_device_info(scope)
