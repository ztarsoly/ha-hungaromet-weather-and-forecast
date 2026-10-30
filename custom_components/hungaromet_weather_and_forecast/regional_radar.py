"""Regional radar animation from met.hu's quadrant precipitation images.

met.hu's radar page shows Hungary in four overlapping quadrants, each a 640x480
precipitation intensity image refreshed every 5 minutes. Each location entry
animates the quadrant that covers its location.
"""

import re

import requests

from .radar import FRAME_COUNT, REQUEST_TIMEOUT, RadarImage, localized
from .scope import EntryScope, device_info, scoped_id

RADAR_PAGE_URL = "https://www.met.hu/idojaras/aktualis_idojaras/radar/main.php"
FRAME_URL = "https://www.met.hu/img/{prefix}/{name}"

# met.hu's radar page tab and image series for each quadrant
QUADRANTS = {
    "nw": ("ENY", "RccE"),
    "ne": ("EK", "RccH"),
    "sw": ("DNY", "RccF"),
    "se": ("DK", "RccG"),
}
# The middle of Hungary; the quadrant images overlap well beyond it
SPLIT_LAT = 47.15
SPLIT_LON = 19.5

REGIONAL_RADAR_NAMES = {"en": "Regional radar image", "hu": "Regionális radarkép"}


def quadrant_for(lat: float, lon: float) -> str:
    """Return the quadrant whose image covers a location."""
    return ("n" if lat >= SPLIT_LAT else "s") + ("e" if lon >= SPLIT_LON else "w")


def latest_frame_urls(quadrant: str, count: int = FRAME_COUNT) -> list[str]:
    """Return the URLs of a quadrant's newest frames, oldest first."""
    tab, prefix = QUADRANTS[quadrant]
    response = requests.get(
        RADAR_PAGE_URL,
        params={"ful": tab, "fulmenu": "aktualis_idojaras/radar"},
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    names = sorted(set(re.findall(rf"{prefix}\d{{8}}_\d{{4}}\.jpg", response.text)))
    return [FRAME_URL.format(prefix=prefix, name=name) for name in names[-count:]]


class HungarometRegionalRadarImage(RadarImage):
    """Animated met.hu radar image of the quadrant covering an entry's location."""

    def __init__(self, hass, scope: EntryScope, quadrant: str):
        super().__init__(hass, "Regional radar", "hungaromet_regional_radar")
        self._scope = scope
        self._quadrant = quadrant
        self._unique_id = scoped_id(scope, "hungaromet_regional_radar")

    @property
    def name(self):
        return localized(self.hass, REGIONAL_RADAR_NAMES)

    @property
    def device_info(self):
        return device_info(
            self._scope, self._device_id, "regional_radar", "Regional radar image"
        )

    @property
    def extra_state_attributes(self):
        return {**super().extra_state_attributes, "quadrant": self._quadrant}

    def _list_frames(self):
        return latest_frame_urls(self._quadrant)
