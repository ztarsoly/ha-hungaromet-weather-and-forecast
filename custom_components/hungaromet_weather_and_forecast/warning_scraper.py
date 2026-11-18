"""HungaroMet's alerts and warnings for a settlement, from met.hu's warning map.

Alerts (riasztás) are issued for districts (járás) for the next few hours;
warnings (figyelmeztetés) for counties (megye) for a whole day. met.hu's map
shows each area's alerts or warnings in a small snippet: one row per
phenomenon, with an icon named after the phenomenon and its level, e.g.
ts1.gif for a level 1 thunderstorm.
"""

import json
import math
import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from functools import cache
from pathlib import Path

import aiohttp

from .forecast_scraper import HUNGARY_TZ, REQUEST_HEADERS, SITE

_LOGGER = logging.getLogger(__name__)

WARNING_PAGE_URL = f"{SITE}/idojaras/veszelyjelzes/"
SNIPPET_URL = f"{WARNING_PAGE_URL}hover.php"
ALERT_MAP = "wahx"  # alerts in force, by district
# The warning page's day tabs and their warnings by county. The first tab is
# usually today, but met.hu moves the tabs on only at 00:00 UTC, so after
# midnight in Hungary the first tab is still yesterday for an hour or two
WARNING_MAPS = {"b": "wbhx", "c": "wchx", "d": "wdhx", "e": "wehx"}
DAY_TAB = re.compile(r"href='index\.php\?c=([b-e])'>([^<]+)</a>")
WEEKDAYS = ("hétfő", "kedd", "szerda", "csütörtök", "péntek", "szombat", "vasárnap")

# met.hu's icon names, in the order of met.hu's legend
ALERT_PHENOMENA = {
    "ts": "thunderstorm",
    "wind": "wind_gust",
    "rainstorm": "cloudburst",
    "fzra": "freezing_rain",
    "snowdrift": "snowdrift",
}
WARNING_PHENOMENA = {
    **ALERT_PHENOMENA,
    "rain": "rain",
    "snow": "snowfall",
    "fog": "fog",
    "coldx": "extreme_cold",
    "hotx": "high_mean_temperature",
}
LEVEL_STATES = ["none", "yellow", "orange", "red"]

# Each phenomenon's name in English and in Hungarian (as on met.hu)
PHENOMENON_NAMES = {
    "thunderstorm": ("Thunderstorm", "Zivatar"),
    "wind_gust": ("Wind gust", "Széllökés"),
    "cloudburst": ("Cloudburst", "Felhőszakadás"),
    "freezing_rain": ("Freezing rain", "Ónos eső"),
    "snowdrift": ("Snowdrift", "Hófúvás"),
    "rain": ("Rain", "Eső"),
    "snowfall": ("Snowfall", "Havazás"),
    "fog": ("Persistent dense fog", "Tartós, sűrű köd"),
    "extreme_cold": ("Extreme cold", "Extrém hideg"),
    "high_mean_temperature": ("High mean temperature", "Magas középhőmérséklet"),
}

ICON = re.compile(r"/images/warningb/([a-z]+)(\d)\.gif")
AREA_TABLE = re.compile(r"<table[^>]*class='stbl'")

# The district map's polygons, in pixels of met.hu's 640 pixels wide map
DISTRICTS_FILE = Path(__file__).with_name("districts.json")
# Budapest has no county of its own; met.hu's county map shows it in Pest
BUDAPEST = "101"
PEST = 13


@dataclass
class Warnings:
    """The level of each phenomenon, for the district's alerts and the county's
    warnings for today."""

    alerts: dict[str, str] = field(default_factory=dict)
    warnings: dict[str, str] = field(default_factory=dict)


@cache
def _districts() -> dict[str, list[list[int]]]:
    return json.loads(DISTRICTS_FILE.read_text(encoding="utf-8"))


def map_point(lat: float, lon: float) -> tuple[float, float]:
    """Return where a place is on met.hu's map, in pixels.

    Fitted to the district seats: they fall inside their own district.
    """
    return (
        92.72 * lon + 0.19 * lat - 1498.8,
        -0.04 * lon - 154.32 * lat + 7502.6,
    )


def _points(polygon: list[int]) -> list[tuple[int, int]]:
    return list(zip(polygon[::2], polygon[1::2]))


def _inside(x: float, y: float, polygon: list[int]) -> bool:
    points = _points(polygon)
    inside = False
    for (x1, y1), (x2, y2) in zip(points, points[1:] + points[:1]):
        if (y1 > y) != (y2 > y) and x < x1 + (y - y1) * (x2 - x1) / (y2 - y1):
            inside = not inside
    return inside


def _distance(x: float, y: float, polygon: list[int]) -> float:
    """Return the distance of a point from a polygon's border."""
    points = _points(polygon)
    best = math.inf
    for (x1, y1), (x2, y2) in zip(points, points[1:] + points[:1]):
        dx, dy = x2 - x1, y2 - y1
        length = dx * dx + dy * dy
        t = 0.0 if length == 0 else max(0.0, min(1.0, ((x - x1) * dx + (y - y1) * dy) / length))
        best = min(best, math.hypot(x - x1 - t * dx, y - y1 - t * dy))
    return best


def find_district(lat: float, lon: float) -> str:
    """Return the code of the district a place is in.

    A place outside every polygon (the map is drawn coarsely) gets the nearest
    district. Reads the bundled map, so it is run in the executor.
    """
    x, y = map_point(lat, lon)
    districts = _districts()
    for code, polygons in districts.items():
        if any(_inside(x, y, polygon) for polygon in polygons):
            return code
    return min(
        districts,
        key=lambda code: min(_distance(x, y, p) for p in districts[code]),
    )


def county_of(district: str) -> int:
    """Return the county code of a district: its code without the last two digits."""
    return PEST if district == BUDAPEST else int(district) // 100


def parse_snippet(html: str, phenomena: dict[str, str]) -> dict[str, str]:
    """Return the level of every phenomenon in an area's snippet.

    Phenomena not in the snippet are "none".
    """
    if not AREA_TABLE.search(html):
        raise ValueError("met.hu's warning snippet has no area table")
    levels = dict.fromkeys(phenomena.values(), 0)
    for icon, level in ICON.findall(html):
        if icon in phenomena:
            key = phenomena[icon]
            levels[key] = max(levels[key], min(int(level), len(LEVEL_STATES) - 1))
    return {key: LEVEL_STATES[level] for key, level in levels.items()}


def highest_level(levels: dict[str, str]) -> str:
    """Return the highest level in force, "none" if nothing is."""
    return max(levels.values(), key=LEVEL_STATES.index, default="none")


def in_force(levels: dict[str, str], language: str) -> list[dict[str, str]]:
    """Return the phenomena in force, the highest level first.

    Each has its name in the language (Hungarian or English), its type and
    its level.
    """
    hungarian = language.split("-")[0] == "hu"
    return [
        {"label": PHENOMENON_NAMES[key][hungarian], "type": key, "level": level}
        for key, level in sorted(
            levels.items(), key=lambda item: -LEVEL_STATES.index(item[1])
        )
        if level != "none"
    ]


def today_warning_map(html: str, today: date) -> str:
    """Return the warning map of the warning page's tab named after today.

    Falls back to the first tab if no tab is named after today.
    """
    wanted = WEEKDAYS[today.weekday()]
    for tab, name in DAY_TAB.findall(html):
        if name.strip() == wanted:
            return WARNING_MAPS[tab]
    _LOGGER.debug("No warning tab for %s on met.hu; using the first one", wanted)
    return WARNING_MAPS["b"]


async def _fetch_text(session: aiohttp.ClientSession, url: str, params=None) -> str:
    async with session.get(
        url,
        params=params,
        headers={**REQUEST_HEADERS, "Referer": WARNING_PAGE_URL},
        timeout=aiohttp.ClientTimeout(total=15),
    ) as resp:
        resp.raise_for_status()
        return await resp.text(encoding="utf-8", errors="replace")


async def _fetch_snippet(session: aiohttp.ClientSession, map_id: str, area) -> str:
    return await _fetch_text(session, SNIPPET_URL, {"id": map_id, "kod": str(area)})


async def fetch_warnings(
    session: aiohttp.ClientSession, district: str, today: date | None = None
) -> Warnings:
    """Download the district's alerts and its county's warnings for today."""
    today = today or datetime.now(HUNGARY_TZ).date()
    alerts = await _fetch_snippet(session, ALERT_MAP, district)
    page = await _fetch_text(session, WARNING_PAGE_URL)
    warnings = await _fetch_snippet(
        session, today_warning_map(page, today), county_of(district)
    )
    return Warnings(
        alerts=parse_snippet(alerts, ALERT_PHENOMENA),
        warnings=parse_snippet(warnings, WARNING_PHENOMENA),
    )
