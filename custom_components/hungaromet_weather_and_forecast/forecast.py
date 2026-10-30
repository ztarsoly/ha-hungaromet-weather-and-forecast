"""Settlement forecast from met.hu, refreshed on the integration's own schedule."""

import logging
from dataclasses import asdict
from datetime import datetime, timedelta

import aiohttp
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_track_utc_time_change
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .const import (
    CONF_FORECAST,
    CONF_LOCATION,
    DOMAIN,
    LOCATION_HOME,
    LOCATION_SETTLEMENT,
)
from .forecast_scraper import (
    FORECAST_PAGE_URL,
    ForecastData,
    Settlement,
    advance_to_now,
    fetch_forecast,
    forecast_from_dict,
    forecast_to_dict,
    keep_dropped_row,
    lookup_settlement,
)
from .scheduler import RetryingSchedule
from .scope import EntryScope, device_info
from .updaters import async_register_updater

_LOGGER = logging.getLogger(__name__)

# met.hu reissues the forecast during the day; check at every full hour, and
# retry soon after a failed download
FORECAST_SCHEDULE = {"minute": 0, "second": 0}
FORECAST_RETRY_DELAY = timedelta(minutes=10)


STORAGE_VERSION = 1


def forecast_store(hass: HomeAssistant, entry_id: str) -> Store:
    """Return where an entry's last forecast is saved (in /config/.storage)."""
    return Store(hass, STORAGE_VERSION, f"{DOMAIN}.forecast.{entry_id}")


def stored_settlement(entry: ConfigEntry) -> Settlement | None:
    """Return the configured forecast settlement; options win over setup data."""
    if CONF_FORECAST in entry.options:
        data = entry.options[CONF_FORECAST]
    else:
        data = entry.data.get(CONF_FORECAST)
    return Settlement(**data) if data else None


def sensor_location(entry: ConfigEntry) -> tuple[float, float] | None:
    """Return the settlement's coordinates if the sensors should use them.

    None means the weather station sensors use Home Assistant's home location.
    """
    if CONF_LOCATION in entry.options:
        choice = entry.options[CONF_LOCATION]
    else:
        choice = entry.data.get(CONF_LOCATION, LOCATION_HOME)
    settlement = stored_settlement(entry)
    if choice != LOCATION_SETTLEMENT or settlement is None:
        return None
    return settlement.lat, settlement.lon


def settlement_to_dict(settlement: Settlement | None) -> dict | None:
    """Return a settlement in the form stored in the config entry."""
    return asdict(settlement) if settlement else None


async def async_resolve_settlement(hass: HomeAssistant, name: str | None):
    """Look a typed settlement name up on met.hu.

    Returns (settlement, error key); an empty name means no forecast: (None, None).
    """
    name = (name or "").strip()
    if not name:
        return None, None
    try:
        settlement = await lookup_settlement(async_get_clientsession(hass), name)
    except (aiohttp.ClientError, TimeoutError):
        return None, "cannot_connect"
    if settlement is None:
        return None, "settlement_not_found"
    return settlement, None


def forecast_scope(coordinator: "ForecastCoordinator") -> EntryScope:
    """Return the scope of the entry a forecast belongs to."""
    return EntryScope.from_entry(coordinator.config_entry)


def forecast_device_info(scope: EntryScope) -> dict:
    """Device info shared by the forecast weather entity and sensors."""
    info = device_info(scope, "hungaromet_forecast", "forecast", "Weather forecast")
    info["configuration_url"] = FORECAST_PAGE_URL
    return info


class ForecastCoordinator(DataUpdateCoordinator[ForecastData]):
    """Hold the latest forecast; refreshed by a schedule, never polled."""

    def __init__(
        self, hass: HomeAssistant, entry: ConfigEntry, settlement: Settlement
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} forecast",
            update_interval=None,
        )
        self.settlement = settlement
        self._store = forecast_store(hass, entry.entry_id)
        self.schedule = RetryingSchedule(
            hass,
            "forecast",
            self._async_scheduled_download,
            FORECAST_RETRY_DELAY,
            config_entry=entry,
            **FORECAST_SCHEDULE,
        )

    async def async_load_stored(self) -> None:
        """Start from the forecast saved before a restart, if it is still ours.

        It shows the last forecast until the first download, and gives that
        download the row met.hu may have dropped meanwhile.
        """
        stored = await self._store.async_load()
        if not stored or stored.get("kod") != self.settlement.kod:
            return
        try:
            self.data = forecast_from_dict(stored["forecast"], dt_util.now())
        except (KeyError, TypeError, ValueError) as err:
            _LOGGER.warning("Ignoring the saved forecast, which is unreadable: %s", err)

    async def _async_scheduled_download(self, _scheduled_for=None) -> bool:
        """Download the forecast; return False to have it retried.

        Nothing is downloaded while no enabled entity shows the forecast, e.g.
        when the Forecast device is disabled.
        """
        if not self._listeners:
            return True
        await self.async_refresh()
        return self.last_update_success

    async def async_update_now(self) -> None:
        """Download the forecast for the update service."""
        await self._async_scheduled_download()

    @callback
    def async_advance_hour(self, now: datetime) -> None:
        """Move the current hour on at each full hour, from the stored forecast.

        The download at the same time replaces this when it succeeds; this keeps
        the current-hour sensors and the forecasts on time when it fails.
        """
        if self.data is None:
            return
        advance_to_now(self.data, now)
        self.async_update_listeners()

    async def _async_update_data(self) -> ForecastData:
        session = async_get_clientsession(self.hass)
        try:
            data = await fetch_forecast(session, self.settlement)
        except (aiohttp.ClientError, TimeoutError, ValueError) as err:
            raise UpdateFailed(
                f"met.hu forecast for {self.settlement.name} failed: {err}"
            ) from err
        data = keep_dropped_row(data, self.data, dt_util.now())
        await self._store.async_save(
            {"kod": self.settlement.kod, "forecast": forecast_to_dict(data)}
        )
        return data


async def async_setup_forecast(
    hass: HomeAssistant, entry: ConfigEntry
) -> ForecastCoordinator | None:
    """Create and schedule the forecast coordinator if a settlement is set.

    The first download is left to the caller, once the entities are added:
    without them it would be skipped.
    """
    settlement = stored_settlement(entry)
    if settlement is None:
        return None
    coordinator = ForecastCoordinator(hass, entry, settlement)
    await coordinator.async_load_stored()

    entry.async_on_unload(coordinator.schedule.start())
    entry.async_on_unload(
        async_register_updater(
            hass, f"{entry.entry_id}_forecast", coordinator.async_update_now
        )
    )
    entry.async_on_unload(
        async_track_utc_time_change(
            hass, coordinator.async_advance_hour, minute=0, second=0
        )
    )
    return coordinator
