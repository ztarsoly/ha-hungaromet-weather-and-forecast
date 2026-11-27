"""The frames of the radar animations.

Both animations keep their newest frames, download each new frame once and
animate them into a GIF kept in memory.
"""

import logging
import os
import re
import threading
from datetime import datetime, timedelta, timezone
from io import BytesIO

import requests
from PIL import Image

_LOGGER = logging.getLogger(__package__)

REQUEST_TIMEOUT = 10

# Both radar animations: the newest 6 frames (30 minutes), 1 s each, and the
# newest frame held for 2 s before the loop starts again
FRAME_COUNT = 6
FRAME_DURATION_MS = 1000
END_DELAY_MS = 2000

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


def frame_is_fresh(frame_url, scheduled_for):
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
