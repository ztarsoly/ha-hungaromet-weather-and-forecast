"""What the national and regional radar images share.

Both keep their newest frames, download each new frame once, animate them into
a GIF kept in memory, and rebuild it every 5 minutes until the frame due at
that run is in it.
"""

import logging
import os
import re
import threading
from datetime import datetime, timedelta, timezone
from io import BytesIO

import requests
from homeassistant.components.image import ImageEntity
from homeassistant.util import dt as dt_util
from PIL import Image

from .failures import FailureLog
from .scheduler import RetryingSchedule
from .updaters import async_register_updater

_LOGGER = logging.getLogger(__name__)

REQUEST_TIMEOUT = 10

# met.hu's radar page, the radar devices' "Visit" link
RADAR_WEB_PAGE = "https://www.met.hu/idojaras/aktualis_idojaras/radar/"

# Both radar animations: the newest 6 frames (30 minutes), 1 s each, and the
# newest frame held for 2 s before the loop starts again
FRAME_COUNT = 6
FRAME_DURATION_MS = 1000
END_DELAY_MS = 2000

# Rebuild every 5 minutes at second 59 (UTC) and retry each minute until the
# image due at that run is published, typically 5-7 minutes after its time
RADAR_SCHEDULE = {"minute": "/5", "second": 59}
RADAR_RETRY_DELAY = timedelta(minutes=1)

# Where earlier versions saved the animations; see remove_saved_gifs
LEGACY_GIF_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "www")


def remove_saved_gifs(storage_gif_dir: str) -> None:
    """Delete the radar animations earlier versions saved to disk.

    They were saved in custom_components/www, then in /config/.storage/<domain>;
    each folder goes too once nothing else is in it.
    """
    for folder in (LEGACY_GIF_DIR, storage_gif_dir):
        if not os.path.isdir(folder):
            continue
        for name in os.listdir(folder):
            if name == "radar_animation.gif" or (
                name.startswith("regional_radar_") and name.endswith(".gif")
            ):
                os.remove(os.path.join(folder, name))
        if not os.listdir(folder):
            os.rmdir(folder)


def localized(hass, names: dict[str, str]) -> str:
    """Return the name in Home Assistant's language, or in English."""
    language = getattr(getattr(hass, "config", None), "language", None) or "en"
    return names.get(language.split("-")[0], names["en"])


def download_image(url):
    """Download one radar frame; return it as a PIL image, or None."""
    try:
        response = requests.get(url, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        image = Image.open(BytesIO(response.content))
        image.load()
        return image
    except requests.RequestException as err:
        _LOGGER.debug("Failed to download %s: %s", url, err)
        return None


class RadarFrames:
    """The newest frames of one radar animation, kept between rebuilds.

    Each rebuild downloads only the frames it does not have yet (normally just
    the newest one) and drops those no longer among the newest.
    """

    def __init__(self) -> None:
        self._images: dict[str, Image.Image] = {}
        self._gif: bytes | None = None
        # Scheduled rebuilds and the update service run in executor threads
        self._lock = threading.Lock()

    @property
    def gif(self) -> bytes | None:
        """Return the last animation built, kept in memory only."""
        return self._gif

    def update(self, urls: list[str]) -> tuple[str | None, bytes | None]:
        """Bring the frames up to the listed newest ones (oldest first).

        Return the newest frame's URL and the new animation's bytes; both are
        None unless every listed frame is here. Frames already downloaded are
        kept for the next try.
        """
        with self._lock:
            for url in urls:
                if url not in self._images and (image := download_image(url)):
                    self._images[url] = image
            # Only the listed frames stay, so the oldest one drops out
            self._images = {url: self._images[url] for url in urls if url in self._images}
            if len(self._images) != len(urls):
                return None, None
            gif = create_gif(list(self._images.values()))
            if gif is None:
                return None, None
            self._gif = gif
            return urls[-1], gif


def create_gif(images, duration=FRAME_DURATION_MS, end_delay=END_DELAY_MS):
    """Animate a list of PIL images; return the GIF's bytes, or None.

    The last frame will have a longer delay (end_delay).
    """
    try:
        if not images:
            _LOGGER.debug("No images to create GIF.")
            return None
        durations = [duration] * (len(images) - 1) + [end_delay]
        gif = BytesIO()
        images[0].save(
            gif,
            format="GIF",
            save_all=True,
            append_images=images[1:],
            duration=durations,
            loop=0,
        )
        _LOGGER.debug("GIF built (frames: %s, end delay: %s ms)", len(images), end_delay)
        return gif.getvalue()
    except (OSError, ValueError) as err:  # pragma: no cover - defensive logging
        _LOGGER.debug("Error creating GIF: %s", err)
        return None


def frame_time(url):
    """Return the UTC time of a radar frame from its file name, or None."""
    match = re.search(r"(\d{8}_\d{4})\.(?:png|jpg)$", url or "")
    if match is None:
        return None
    return datetime.strptime(match.group(1), "%Y%m%d_%H%M").replace(
        tzinfo=timezone.utc
    )


def _frame_is_fresh(frame_url, scheduled_for):
    """Return whether the newest frame is the one due at a scheduled run."""
    frame = frame_time(frame_url)
    if frame is None:
        return False
    run_slot = scheduled_for.replace(
        minute=scheduled_for.minute - scheduled_for.minute % 5,
        second=0,
        microsecond=0,
    )
    return frame >= run_slot - timedelta(minutes=5)


class RadarImage(ImageEntity):  # pylint: disable=too-many-instance-attributes
    """An animated radar GIF, rebuilt on its own schedule.

    Subclasses give the name, the device and where the newest frames are listed.
    """

    # Rebuilt on its own 5-minute schedule, not Home Assistant polling
    _attr_should_poll = False
    # Named on its own, without its device's name in front
    _attr_has_entity_name = False
    # Names the image in log messages
    _label = "Radar"

    def __init__(self, hass, unique_id: str):
        super().__init__(hass)
        self.hass = hass
        self._attr_unique_id = unique_id
        self._added = False
        # The animation is kept in memory only; it is built when the entity is
        # added, and the entity is unavailable until then
        self._frames = RadarFrames()
        self._download_failed = False
        # Why the last rebuild brought nothing, for the log
        self._problem = ""
        self._failure_log = FailureLog(_LOGGER, f"{self._label} download")
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
                config_entry=self.platform.config_entry if self.platform else None,
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
                async_register_updater(self.hass, self.unique_id, self.async_update_data)
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
        return _frame_is_fresh(frame_url, scheduled_for)

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
        try:
            frame_url, gif = await self.hass.async_add_executor_job(
                self._rebuild_gif
            )
        except Exception as err:  # pylint: disable=broad-exception-caught
            # For example a broken image; it must not stop the schedule
            self._problem = str(err)
            frame_url, gif = None, None
        self._download_failed = gif is None
        if gif is None:
            self._failure_log.failed(self._problem)
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
