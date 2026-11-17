"""The alert and warning sensors of the forecast settlement."""

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
)
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .forecast import forecast_device_info
from .scope import EntryScope, scoped_id
from .warning import WarningCoordinator
from .warning_scraper import LEVEL_STATES, highest_level, in_force

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


class WarningSensor(CoordinatorEntity[WarningCoordinator], SensorEntity):
    """The highest alert or warning level in force, with the phenomena in force."""

    _attr_has_entity_name = True
    entity_description: SensorEntityDescription

    def __init__(
        self, coordinator: WarningCoordinator, description: SensorEntityDescription
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        scope = EntryScope.from_entry(coordinator.config_entry)
        self._attr_unique_id = scoped_id(scope, f"hungaromet_forecast_{description.key}")
        self._attr_device_info = forecast_device_info(scope)

    @property
    def available(self) -> bool:
        """Keep showing the last levels when a download fails."""
        return self.coordinator.data is not None

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


def warning_sensors(coordinator: WarningCoordinator) -> list[WarningSensor]:
    """Create the alert and warning sensors."""
    return [WarningSensor(coordinator, d) for d in WARNING_SENSORS]
