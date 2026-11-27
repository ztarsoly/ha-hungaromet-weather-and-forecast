"""Image platform: the animated radar images.

The national radar entry has the national radar image, which is the same
everywhere, so its device is not scoped to an entry. Each location entry has the
regional radar image of the quadrant that covers its location.
"""

import logging
from datetime import timedelta

import requests
from homeassistant.components.image import ImageEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from .const import (
    DEVICE_REGIONAL_RADAR,
    DOMAIN,
    RADAR_BASE_URL,
    REGIONAL_DEVICE_ID,
)
from .data import HungaroMetConfigEntry
from .entity import HungaroMetEntity
from .failures import FailureLog
from .national_radar import get_latest_image_urls
from .radar import RadarFrames, frame_is_fresh
from .regional_radar import latest_frame_urls, quadrant_for
from .scheduler import RetryingSchedule
from .scope import (
    EntryScope,
    device_info,
    entry_devices,
    is_radar_entry,
    scoped_id,
)
from .settlement import entry_coordinates
from .updaters import async_register_updater

_LOGGER = logging.getLogger(__package__)

# met.hu's radar page, the radar devices' "Visit" link
RADAR_WEB_PAGE = "https://www.met.hu/idojaras/aktualis_idojaras/radar/"

# Rebuild every 5 minutes at second 59 (UTC) and retry each minute until the
# image due at that run is published, typically 5-7 minutes after its time
RADAR_SCHEDULE = {"minute": "/5", "second": 59}
RADAR_RETRY_DELAY = timedelta(minutes=1)

# The devices; also parts of the images' unique IDs, so they must not change
NATIONAL_DEVICE_ID = "hungaromet_radar_gif"


# pylint: disable-next=too-many-instance-attributes
class RadarImage(HungaroMetEntity, ImageEntity):
    """An animated radar GIF, rebuilt on its own 5-minute schedule.

    Subclasses give the device and where the newest frames are listed.
    """

    _attr_name = None  # Named after the device
    # Names the image in log messages
    _label = "HungaroMet radar"

    def __init__(self, hass, entry: HungaroMetConfigEntry, unique_id: str):
        super().__init__(hass)
        self.hass = hass
        self._entry = entry
        self._attr_unique_id = unique_id
        self._added = False
        # The animation is kept in memory only; it is built when the entity is
        # added, and the entity is unavailable until then
        self._frames = RadarFrames()
        self._download_failed = False
        # Why the last rebuild brought nothing, for the log
        self._problem = ""
        self._failure_log = FailureLog(
            _LOGGER, f"{self._label} download", entry.runtime_data.failures
        )
        # What stops the schedule and the update service's rebuilds
        self._unsubscribe: list = []

    @property
    def available(self) -> bool:
        """Return True if the last rebuild brought a complete animation."""
        return not self._download_failed and self._frames.gif is not None

    async def async_added_to_hass(self):
        self._added = True
        if not self._unsubscribe:
            schedule = RetryingSchedule(
                self.hass,
                "radar",
                self._async_scheduled_update,
                RADAR_RETRY_DELAY,
                config_entry=self._entry,
                **RADAR_SCHEDULE,
            )
            self._unsubscribe.append(schedule.start())
            # Build the GIF now instead of waiting up to 5 minutes for the first
            # scheduled run; in the background, so adding the entity is not delayed
            self.hass.async_create_task(
                schedule.async_run_now(), f"{self.entity_id or self._label} first update"
            )
            # The update service rebuilds this image along with everything else
            self._unsubscribe.append(
                async_register_updater(
                    self._entry, self.unique_id, self.async_update_data
                )
            )

    async def async_will_remove_from_hass(self):
        self._added = False
        while self._unsubscribe:
            self._unsubscribe.pop()()

    async def _async_scheduled_update(self, scheduled_for):
        """Rebuild the GIF; True once it holds the frame due at this run."""
        if not self._added:
            return True
        frame_url = await self.async_update_data()
        return frame_is_fresh(frame_url, scheduled_for)

    def _list_frames(self) -> list[str]:
        """Return the URLs of the newest FRAME_COUNT frames, oldest first."""
        raise NotImplementedError

    def _rebuild_gif(self) -> tuple[str | None, bytes | None]:
        """Rebuild the GIF (in the executor).

        Return the newest frame's URL and the GIF's bytes; both are None unless
        every listed frame arrived.
        """
        try:
            urls = self._list_frames()
        except requests.RequestException as err:
            self._problem = f"the frames could not be listed: {err}"
            return None, None
        if not urls:
            self._problem = "no frames are listed"
            return None, None
        self._problem = "not every frame could be downloaded and animated"
        return self._frames.update(urls)

    async def async_update_data(self):
        """Rebuild the GIF and return its newest frame's URL, or None."""
        if not self._added:
            return None
        error = None
        try:
            frame_url, gif = await self.hass.async_add_executor_job(
                self._rebuild_gif
            )
        except Exception as err:  # pylint: disable=broad-exception-caught
            # For example a broken image; it must not stop the schedule
            error = err
            frame_url, gif = None, None
        self._download_failed = gif is None
        if gif is None:
            if error is None:
                self._failure_log.failed(self._problem)
            elif isinstance(error, (OSError, ValueError)):
                self._failure_log.failed(error)
            else:
                # With the traceback
                self._failure_log.failed(repr(error), exc_info=error)
            frame_url = None
        else:
            self._failure_log.succeeded()
            # The state change also tells the frontend to reload the image
            self._attr_image_last_updated = dt_util.utcnow()
        self.async_write_ha_state()
        return frame_url

    async def async_update(self):
        """Rebuild the GIF (Home Assistant's update_entity)."""
        await self.async_update_data()

    async def async_image(self):
        return self.image()

    def image(self):
        """Return the radar animation's bytes, or None while unavailable."""
        return self._frames.gif if self.available else None


class HungarometRadarImage(RadarImage):
    """The national radar image."""

    _label = "HungaroMet national radar"

    def __init__(self, hass, entry):
        # Made from the device and an old name, so it must not change
        super().__init__(hass, entry, f"{NATIONAL_DEVICE_ID}_hungaromet_radar")

    @property
    def device_info(self):
        return {
            "identifiers": {(DOMAIN, NATIONAL_DEVICE_ID)},
            "translation_key": "radar",
            "manufacturer": "HungaroMet",
            "model": "National radar image",
            "entry_type": "service",
            "configuration_url": RADAR_WEB_PAGE,
        }

    def _list_frames(self):
        return get_latest_image_urls(RADAR_BASE_URL)


class HungarometRegionalRadarImage(RadarImage):
    """Animated met.hu radar image of the quadrant covering an entry's location."""

    _label = "HungaroMet regional radar"

    def __init__(self, hass, entry, quadrant: str):
        scope = EntryScope.from_entry(entry)
        super().__init__(hass, entry, scoped_id(scope, REGIONAL_DEVICE_ID))
        self._scope = scope
        self._quadrant = quadrant

    @property
    def device_info(self):
        return {
            **device_info(
                self._scope, REGIONAL_DEVICE_ID, "regional_radar", "Regional radar image"
            ),
            "configuration_url": RADAR_WEB_PAGE,
        }

    def _list_frames(self):
        return latest_frame_urls(self._quadrant)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HungaroMetConfigEntry,
    async_add_entities: AddEntitiesCallback,
):
    """
    Set up the radar image of a config entry.

    The national radar entry has the national radar image; a location entry
    has the regional radar image for its location, if chosen.
    """
    if is_radar_entry(entry):
        _setup_image_entities(hass, entry, async_add_entities)
        return
    if DEVICE_REGIONAL_RADAR not in entry_devices(entry):
        return
    quadrant = quadrant_for(*entry_coordinates(hass, entry))
    async_add_entities(
        [HungarometRegionalRadarImage(hass, entry, quadrant)],
        True,
    )


def _setup_image_entities(hass, entry, async_add_entities):
    entity = HungarometRadarImage(hass, entry)
    async_add_entities([entity], True)
