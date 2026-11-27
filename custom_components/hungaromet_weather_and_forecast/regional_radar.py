"""The frames of met.hu's quadrant precipitation images.

met.hu's radar page shows Hungary in four overlapping quadrants, each a 640x480
precipitation intensity image refreshed every 5 minutes.
"""

import re

import requests

from .radar import FRAME_COUNT, REQUEST_TIMEOUT

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
