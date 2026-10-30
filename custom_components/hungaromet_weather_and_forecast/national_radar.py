"""National radar animation from HungaroMet's open-data composite images.

The national radar entry has this one image; it is the same everywhere, so its
device is not scoped to an entry.
"""

import requests
from bs4 import BeautifulSoup
from bs4.element import Tag

from .const import DOMAIN, RADAR_BASE_URL
from .radar import FRAME_COUNT, REQUEST_TIMEOUT, RadarImage, localized

# The radar images' own names, in Home Assistant's language. Translated entity
# names always follow the device name ("National Radar Image National radar
# image"), so these are picked here instead.
NATIONAL_RADAR_NAMES = {"en": "National radar image", "hu": "Országos radarkép"}


def get_latest_image_urls(base_url, count=FRAME_COUNT):
    """Return the URLs of the newest PNG frames listed at base_url, oldest first."""
    response = requests.get(base_url, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")

    png_files = []
    for a in soup.find_all("a"):
        if isinstance(a, Tag):
            href = a.get("href")
            if isinstance(href, str) and href.endswith(".png"):
                png_files.append(href)

    # Remove duplicates while preserving order
    seen = set()
    unique_png_files = []
    for fname in png_files:
        if fname not in seen:
            unique_png_files.append(fname)
            seen.add(fname)
    unique_png_files.sort(reverse=True)

    latest_files = unique_png_files[:count]
    latest_files.sort()
    return [base_url + fname for fname in latest_files]


DEVICE_ID = "hungaromet_radar_gif"


class HungarometRadarImage(RadarImage):
    """The national radar image."""

    _label = "HungaroMet Radar"

    def __init__(self, hass):
        # Made from the device and an old name, so it must not change
        super().__init__(hass, f"{DEVICE_ID}_hungaromet_radar")

    @property
    def name(self):
        return localized(self.hass, NATIONAL_RADAR_NAMES)

    @property
    def device_info(self):
        return {
            "identifiers": {(DOMAIN, DEVICE_ID)},
            "translation_key": "radar",
            "manufacturer": "HungaroMet",
            "model": "National radar image",
            "entry_type": "service",
        }

    def _list_frames(self):
        return get_latest_image_urls(RADAR_BASE_URL)
