"""met.hu's settlement forecast: finding a settlement, reading its forecast
table, and working out the hours and days from the table's 6-hour rows.

met.hu's settlement search (GET /jquery/search.php?term=<name>) answers with
JSON suggestions holding each settlement's name ("value"), code ("kod") and
coordinates ("lat", "lon"). Posting a settlement to the forecast page with
c=tablazat returns its forecast table. The table's <tbody> has one <tr> for
each 6-hour period, in Hungarian local time. A <th class="naptar"> starts each
day, with the month, the day and the weekday in <div>s; only the first one is
read, see _read_rows. The <td>s are told apart by their first class:

ora       time, "HH:MM"
T         temperature [°C]; with a second class X or N, the day's maximum or
          minimum (blank in the other rows)
idoikon   weather symbol (after a spacer image); its tooltip describes the
          weather in a <div class=ktext>, and night symbols end in "e"
R         precipitation [mm]
C         cloud cover [%]
Wikon, Wd wind direction symbol and name; their tooltips give the bearing,
          "(41 fok)"
Wf        mean wind speed, then the gust [km/h]
P         sea-level pressure [hPa]

The issue time is in the page text: "HungaroMet: 2026. október 28. 09:46".
"""

from __future__ import annotations

import logging
import math
import re
from bisect import bisect_right
from collections import defaultdict
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, date, datetime, timedelta

import aiohttp
from astral import Observer
from astral.sun import elevation
from homeassistant.util import dt as dt_util

from .failures import FailureRecords
from .html_tree import Element, parse_html
from .rounding import round_half_away

_LOGGER = logging.getLogger(__package__)

SITE = "https://www.met.hu"
SEARCH_URL = f"{SITE}/jquery/search.php"
FORECAST_PAGE_URL = f"{SITE}/idojaras/elorejelzes/magyarorszagi_telepulesek/"
TABLE_URL = f"{FORECAST_PAGE_URL}main.php"
# The forecast page is where met.hu's own pages send these requests from
REQUEST_HEADERS = {"User-Agent": "Mozilla/5.0", "Referer": FORECAST_PAGE_URL}

# met.hu's times are Hungarian local time, whatever Home Assistant's time zone is
HUNGARY_TZ = dt_util.get_time_zone("Europe/Budapest")
# Each met.hu row covers the 6 hours from its time
PERIOD = timedelta(hours=6)

MONTHS = (
    "január", "február", "március", "április", "május", "június",
    "július", "augusztus", "szeptember", "október", "november", "december",
)

# A 16-point compass, like Home Assistant's weather cards ("n" is from 348.75°
# up to 11.25°)
SIXTEEN_POINTS = (
    "n", "nne", "ne", "ene", "e", "ese", "se", "sse",
    "s", "ssw", "sw", "wsw", "w", "wnw", "nw", "nnw",
)
# met.hu's names for the same points, in the same order
COMPASS_NAMES = (
    "északi", "észak-északkeleti", "északkeleti", "kelet-északkeleti",
    "keleti", "kelet-délkeleti", "délkeleti", "dél-délkeleti",
    "déli", "dél-délnyugati", "délnyugati", "nyugat-délnyugati",
    "nyugati", "nyugat-északnyugati", "északnyugati", "észak-északnyugati",
)
WIND_NAMES = dict(zip(COMPASS_NAMES, SIXTEEN_POINTS))
WIND_DIRECTION_STATES = list(SIXTEEN_POINTS)
# The Beaufort forces 0 to 12, and the mean wind speeds (m/s) where forces 1 to
# 12 start
WIND_STRENGTH_STATES = [
    "calm", "light_air", "light_breeze", "gentle_breeze", "moderate_breeze",
    "fresh_breeze", "strong_breeze", "near_gale", "gale", "strong_gale",
    "storm", "violent_storm", "hurricane",
]
BEAUFORT_LIMITS = (0.5, 1.5, 3.3, 5.5, 7.9, 10.7, 13.8, 17.1, 20.7, 24.4, 28.4, 32.6)

# Words of met.hu's weather descriptions and the Home Assistant condition they
# mean, checked in this order, so that "havas eső" is not just rain and
# "derült, párás" is haze. "Derült" (clear) turns into clear-night at night; each
# hour then follows the sun instead, see _follow_the_sun.
CONDITION_WORDS = (
    ("zivatar", "lightning-rainy"),
    ("jégeső", "hail"),
    ("ónos", "snowy-rainy"),
    ("havas eső", "snowy-rainy"),
    ("hózápor", "snowy"),
    ("havazás", "snowy"),
    ("hószállingózás", "snowy"),
    ("hófúvás", "snowy"),
    ("zápor", "pouring"),
    ("eső", "rainy"),
    ("szitálás", "rainy"),
    ("köd", "fog"),
    ("párás", "fog"),
    ("borult", "cloudy"),
    ("erősen", "cloudy"),  # erősen felhős, erősen fátyolfelhős
    ("felhős", "partlycloudy"),
    ("derült", "sunny"),
)
CONDITION_STATES = list(
    dict.fromkeys([*(c for _, c in CONDITION_WORDS), "clear-night"])
)

NUMBER = re.compile(r"-?\d+(?:[.,]\d+)?")


@dataclass
class Settlement:
    """A settlement as met.hu's search knows it."""

    name: str
    kod: str
    lat: float
    lon: float


@dataclass
class ForecastValues:  # pylint: disable=too-many-instance-attributes
    """The weather of one day, and what a row or an hour has in common with it.

    One field for each forecast value, as stored and shown by the entities.
    """

    forecast_time: datetime | None = None
    temperature: float | None = None
    temperature_min: float | None = None
    temperature_max: float | None = None
    weather_condition: str | None = None
    precipitation: float | None = None  # mm
    cloud_cover: float | None = None  # %
    wind_speed: float | None = None  # km/h, mean
    wind_gust: float | None = None  # km/h
    wind_bearing: float | None = None  # degrees
    pressure: float | None = None  # hPa

    @property
    def wind_strength(self) -> str | None:
        """The Beaufort force of the mean wind, a WIND_STRENGTH_STATES key."""
        return None if self.wind_speed is None else beaufort_force(self.wind_speed)


@dataclass
class ForecastPeriod(ForecastValues):  # pylint: disable=too-many-instance-attributes
    """The weather of one met.hu row or one interpolated hour."""

    wind_direction: str | None = None  # a WIND_DIRECTION_STATES key


@dataclass
class ForecastData:
    """A settlement's forecast and what is worked out from it."""

    settlement: str = ""
    location: tuple[float, float] | None = None  # latitude, longitude
    issued: datetime | None = None
    current: ForecastPeriod | None = None  # the current hour
    periods: list[ForecastPeriod] = field(default_factory=list)  # met.hu's rows
    hourly: list[ForecastPeriod] = field(default_factory=list)  # interpolated
    daily: list[ForecastValues] = field(default_factory=list)


async def lookup_settlement(
    session: aiohttp.ClientSession, name: str
) -> Settlement | None:
    """Find a settlement with met.hu's search; an exact name beats the first hit.

    None if met.hu knows no such settlement (an empty list); ValueError if its
    answer cannot be used.
    """
    async with session.get(
        SEARCH_URL,
        params={"term": name},
        headers=REQUEST_HEADERS,
        timeout=aiohttp.ClientTimeout(total=15),
    ) as resp:
        resp.raise_for_status()
        suggestions = await resp.json(content_type=None)
    if suggestions == []:
        return None
    if not isinstance(suggestions, list) or not all(
        isinstance(s, dict) for s in suggestions
    ):
        raise ValueError(f"not a list of settlements: {str(suggestions)[:100]}")

    wanted = name.strip().casefold()
    chosen = next(
        (s for s in suggestions if str(s.get("value", "")).casefold() == wanted),
        suggestions[0],
    )
    try:
        return Settlement(
            name=chosen["value"],
            kod=str(chosen["kod"]),
            lat=float(chosen["lat"]),
            lon=float(chosen["lon"]),
        )
    except (KeyError, TypeError, ValueError) as err:
        raise ValueError(f"unusable settlement: {str(chosen)[:100]}") from err


async def fetch_forecast(
    session: aiohttp.ClientSession,
    settlement: Settlement,
    records: FailureRecords | None = None,
) -> ForecastData:
    """Download a settlement's forecast table and read it.

    records: what is logged already, so unknown words are warned about once.
    """
    form = {
        "kod": settlement.kod,
        "lt": str(settlement.lat),
        "n": str(settlement.lon),
        "tel": settlement.name,
        "c": "tablazat",
    }
    async with session.post(
        TABLE_URL,
        data=form,
        headers=REQUEST_HEADERS,
        timeout=aiohttp.ClientTimeout(total=30),
    ) as resp:
        resp.raise_for_status()
        html = await resp.text(encoding="utf-8", errors="replace")
    return parse_page(
        html,
        settlement.name,
        location=(settlement.lat, settlement.lon),
        records=records,
    )


def parse_page(
    html: str,
    settlement_name: str,
    now: datetime | None = None,
    location: tuple[float, float] | None = None,
    records: FailureRecords | None = None,
):
    """Read a forecast page; raise ValueError when it has no forecast rows."""
    now = now or dt_util.now()
    page = parse_html(html)
    tbody = page.find("tbody")
    rows = _read_rows(tbody, now, records or FailureRecords()) if tbody else []
    if not rows:
        raise ValueError(f"met.hu has no forecast table for {settlement_name}")
    data = ForecastData(
        settlement=settlement_name,
        location=location,
        issued=_issue_time(page.text(" ")),
        periods=rows,
    )
    _derive(data, now)
    return data


def _read_rows(
    tbody: Element, now: datetime, records: FailureRecords
) -> list[ForecastPeriod]:
    """Read the table's rows; the first day cell dates the first row.

    A row is on the same day as the one before it while the hours rise, and on
    the next day when they do not (19:00, then 01:00). The later day cells are
    not used: from 00:00 UTC for about 25 minutes met.hu groups the rows by UTC
    days, so a day cell then sits above the wrong rows.
    """
    rows = []
    day = None
    last_clock = None
    for tr in tbody.find_all("tr", recursive=False):
        if day is None and (day_cell := tr.find("th", class_="naptar")) is not None:
            day = _calendar_day(day_cell, now)
        cells = _cells_by_class(tr)
        if "ora" not in cells:
            continue
        text = _text(cells["ora"][0])
        clock = _clock(text)
        day = day or now.astimezone(HUNGARY_TZ).date()
        if clock is not None:
            if last_clock is not None and clock <= last_clock:
                day += timedelta(days=1)
            last_clock = clock
        rows.append(_row(cells, day, records))
    return rows


def _cells_by_class(tr: Element) -> dict[str, list[Element]]:
    """Group a row's cells by their first class, in the row's order."""
    cells = defaultdict(list)
    for td in tr.find_all("td", recursive=False):
        if classes := td.classes:
            cells[classes[0]].append(td)
    return cells


def _calendar_day(th: Element, now: datetime) -> date | None:
    """Read a day cell; the year is the one that puts the day nearest to now."""
    parts = [div.text().lower() for div in th.find_all("div")]
    if len(parts) < 2 or parts[0] not in MONTHS or not parts[1].isdigit():
        return None
    month, day = MONTHS.index(parts[0]) + 1, int(parts[1])
    today = now.astimezone(HUNGARY_TZ).date()
    candidates = []
    for year in (today.year - 1, today.year, today.year + 1):
        try:
            candidates.append(date(year, month, day))
        except ValueError:  # 29 February outside leap years
            continue
    return min(candidates, key=lambda d: abs(d - today), default=None)


def _row(
    cells: dict[str, list[Element]], day: date, records: FailureRecords
) -> ForecastPeriod:
    """Read one forecast row."""
    period = ForecastPeriod(forecast_time=_row_time(_text(cells["ora"][0]), day))
    for td in cells.get("T", []):
        kind = (td.classes + [""])[1]
        name = {"X": "temperature_max", "N": "temperature_min"}.get(kind, "temperature")
        setattr(period, name, _number(_text(td)))
    period.precipitation = _first_number(cells, "R")
    cloud = _first_number(cells, "C")
    period.cloud_cover = None if cloud is None else round_half_away(cloud, 2)
    speeds = [_number(_text(td)) for td in cells.get("Wf", [])]
    period.wind_speed, period.wind_gust = (speeds + [None, None])[:2]
    period.pressure = _first_number(cells, "P")
    period.wind_bearing, period.wind_direction = _wind(cells, records)
    period.weather_condition = _weather(cells, records)
    return period


def _text(td: Element) -> str:
    return td.text(" ")


def _number(text: str) -> float | None:
    """Read the number in a cell's text; blank cells have none."""
    match = NUMBER.search(text.replace("−", "-").replace("–", "-"))
    # Adding 0.0 turns met.hu's "-0" into 0.0
    return float(match[0].replace(",", ".")) + 0.0 if match else None


def _first_number(cells: dict[str, list[Element]], name: str) -> float | None:
    return _number(_text(cells[name][0])) if cells.get(name) else None


def _clock(text: str) -> tuple[int, int] | None:
    """Read a row's "HH:MM" as (hour, minute)."""
    match = re.match(r"(\d{1,2}):(\d{2})", text)
    return (int(match[1]), int(match[2])) if match else None


def _row_time(text: str, day: date) -> datetime | None:
    """Put a row's "HH:MM" on its day, in Hungarian local time."""
    if (clock := _clock(text)) is None:
        return None
    return datetime(day.year, day.month, day.day, *clock, tzinfo=HUNGARY_TZ)


def _wind(
    cells: dict[str, list[Element]], records: FailureRecords
) -> tuple[float | None, str | None]:
    """Return the wind's bearing and its direction on the 16-point compass.

    met.hu's direction names are coarser (12° is "északi"), so the direction
    comes from the bearing, like in the interpolated hours.
    """
    tooltips = " ".join(
        td.get("onmouseover", "") for td in cells.get("Wikon", []) + cells.get("Wd", [])
    )
    match = re.search(r"\((\d+(?:\.\d+)?)\s*fok\)", tooltips)
    bearing = float(match[1]) if match else None
    name = _text(cells["Wd"][0]).lower() if cells.get("Wd") else ""
    direction = WIND_NAMES.get(name)
    if name and direction is None:
        records.warn_once(_LOGGER, "Unknown met.hu wind direction %r", name)
    if bearing is None:
        return bearing, direction
    return bearing, bearing_to_direction(bearing)


def _weather(
    cells: dict[str, list[Element]], records: FailureRecords
) -> str | None:
    """Return the Home Assistant condition of a row's weather symbol."""
    for td in cells.get("idoikon", []):
        img = td.find("img")
        symbol = re.search(r"(w\d+)(e?)\.\w+$", img.get("src", "")) if img else None
        if symbol is None:
            continue  # the spacer in front of the symbol
        tooltip = re.search(r"class=ktext>([^<]*)<", td.get("onmouseover", ""))
        description = tooltip[1].strip() if tooltip else ""
        condition = condition_for(description, night=symbol[2] == "e")
        if condition is None:
            records.warn_once(_LOGGER, "Unknown met.hu weather %r", description)
        return condition
    return None


def condition_for(description: str, night: bool = False) -> str | None:
    """Return the Home Assistant condition for met.hu's weather description.

    None for a description it does not recognise.
    """
    text = description.lower()
    for word, condition in CONDITION_WORDS:
        if word in text:
            return "clear-night" if condition == "sunny" and night else condition
    return None


def _issue_time(text: str) -> datetime | None:
    """Find "HungaroMet: 2026. október 28. 09:46" in the page text."""
    match = re.search(
        r"HungaroMet:\s*(\d{4})\.\s*([^\W\d]+)\s+(\d{1,2})\.\s*(\d{1,2}):(\d{2})", text
    )
    if match is None or match[2].lower() not in MONTHS:
        return None
    return datetime(
        int(match[1]),
        MONTHS.index(match[2].lower()) + 1,
        int(match[3]),
        int(match[4]),
        int(match[5]),
        tzinfo=HUNGARY_TZ,
    )


def _derive(data: ForecastData, now: datetime) -> None:
    """Work out the hourly values, current hour and daily summary from the rows."""
    data.hourly = fill_to_first_row(interpolate_hourly(data.periods), now)
    if data.location:
        _follow_the_sun(data.hourly, data.location)
    data.current = find_current_hour(data.hourly, now)
    data.daily = aggregate_daily(data.periods, now)


def _follow_the_sun(hours: list[ForecastPeriod], location: tuple[float, float]) -> None:
    """Make the clear hours sunny or clear-night by the sun at the start of the hour.

    met.hu only has a night symbol for its 19:00 and 01:00 rows, whenever the sun
    really sets and rises.
    """
    observer = Observer(*location)
    for hour in hours:
        if hour.weather_condition in ("sunny", "clear-night"):
            sun_is_up = elevation(observer, hour.forecast_time.astimezone(UTC)) > 0
            hour.weather_condition = "sunny" if sun_is_up else "clear-night"


def forecast_to_dict(data: ForecastData) -> dict:
    """Return a forecast's met.hu rows in a JSON-friendly form, for storage."""

    def period(p: ForecastPeriod) -> dict:
        values = asdict(p)
        values["forecast_time"] = p.forecast_time.isoformat() if p.forecast_time else None
        return values

    return {
        "settlement": data.settlement,
        "issued": data.issued.isoformat() if data.issued else None,
        "periods": [period(p) for p in data.periods],
        "hourly": [period(p) for p in data.hourly],
    }


def forecast_from_dict(
    stored: dict, now: datetime, location: tuple[float, float] | None = None
) -> ForecastData:
    """Rebuild a stored forecast.

    The hours are the ones saved, as they were published and planned; a forecast
    saved without hours has them worked out from its rows.
    """

    def period(values: dict) -> ForecastPeriod:
        time = values.get("forecast_time")
        condition = values.get("weather_condition")
        return ForecastPeriod(
            **{
                **values,
                "forecast_time": datetime.fromisoformat(time) if time else None,
                # Earlier versions saved conditions that are no states now
                "weather_condition": (
                    condition if condition in CONDITION_STATES else None
                ),
            }
        )

    issued = stored.get("issued")
    data = ForecastData(
        settlement=stored["settlement"],
        location=location,
        issued=datetime.fromisoformat(issued) if issued else None,
        periods=[period(p) for p in stored["periods"]],
    )
    _derive(data, now)
    if hourly := stored.get("hourly"):
        data.hourly = fill_to_first_row([period(p) for p in hourly], now)
        if location:
            _follow_the_sun(data.hourly, location)
        data.current = find_current_hour(data.hourly, now)
    return data


def keep_dropped_row(
    data: ForecastData, previous: ForecastData | None, now: datetime
) -> ForecastData:
    """Put back the rows met.hu has dropped from a new download.

    met.hu removes a row 4 hours after its time (the 13:00 row goes at 17:00),
    although its period lasts 6 hours. Without it, the hours until the next row
    could not be worked out. Today's earlier rows are kept too: they hold the
    day's minimum (07:00) and maximum (13:00), so by the evening today's
    summary would otherwise come from the last row alone. The rows come from
    the previous download.

    If the new rows start after now and none of those starts by now, the last
    earlier row that does is put back too, however old it is, so that the hours
    up to the new rows are interpolated from it.
    """
    if previous is None or not data.periods or data.periods[0].forecast_time is None:
        return data
    first = data.periods[0].forecast_time.astimezone(UTC)
    now_utc = now.astimezone(UTC)
    today = now.astimezone(HUNGARY_TZ).date()
    earlier = [
        p
        for p in previous.periods
        if p.forecast_time and p.forecast_time.astimezone(UTC) < first
    ]
    kept = [
        p
        for p in earlier
        if p.forecast_time.astimezone(HUNGARY_TZ).date() == today
        or now_utc < p.forecast_time.astimezone(UTC) + PERIOD
    ]
    if first > now_utc and not any(
        p.forecast_time.astimezone(UTC) <= now_utc for p in kept
    ):
        started = [p for p in earlier if p.forecast_time.astimezone(UTC) <= now_utc]
        kept = [*started[-1:], *kept]
    if not kept:
        return data
    data.periods = [*kept, *data.periods]
    _derive(data, now)
    return data


# What changes smoothly from hour to hour; the condition and precipitation belong
# to the row that starts their period
CONTINUOUS = ("temperature", "cloud_cover", "wind_speed", "wind_gust", "pressure")


def continue_from_recorded(
    data: ForecastData, previous: ForecastData | None, now: datetime
) -> ForecastData:
    """Start the hours from the values already published, not from the new rows.

    A new met.hu forecast has other values for the hour that has just passed, and
    working this hour out from its rows would make the values jump. Instead the
    hours up to the last one before `now` stay as they were recorded in the
    previous download, and the hours from there to the next met.hu row change
    in a straight line from the recorded value to the row's. From that row on
    the hours follow the new rows as before.
    """
    if previous is None or not data.hourly:
        return data
    now_hour = now.astimezone(UTC).replace(minute=0, second=0, microsecond=0)
    last_hour = now_hour - timedelta(hours=1)
    recorded = {p.forecast_time.astimezone(UTC): p for p in previous.hourly}
    anchor = recorded.get(last_hour)
    target = next(
        (
            p
            for p in data.periods
            if p.forecast_time and p.forecast_time.astimezone(UTC) >= now_hour
        ),
        None,
    )
    target_time = target.forecast_time.astimezone(UTC) if target else None
    steps = round((target_time - last_hour).total_seconds() / 3600) if target else 0
    hourly = []
    for hour in data.hourly:
        time = hour.forecast_time.astimezone(UTC)
        if time <= last_hour and time in recorded:
            hour = recorded[time]
        elif anchor and target and last_hour < time < target_time:
            step = round((time - last_hour).total_seconds() / 3600)
            hour = _towards(hour, anchor, target, step, steps)
        hourly.append(hour)
    data.hourly = hourly
    data.current = find_current_hour(hourly, now)
    return data


def _towards(
    hour: ForecastPeriod,
    anchor: ForecastPeriod,
    target: ForecastPeriod,
    step: int,
    steps: int,
) -> ForecastPeriod:
    """Return `hour` with its changing values `step` of `steps` hours on the way
    from `anchor`'s values to `target`'s."""
    fraction = step / steps
    values = {
        name: _lerp(getattr(anchor, name), getattr(target, name), fraction)
        for name in CONTINUOUS
        if getattr(anchor, name) is not None and getattr(target, name) is not None
    }
    if anchor.wind_bearing is not None and target.wind_bearing is not None:
        values["wind_bearing"] = _lerp_bearing(
            anchor.wind_bearing, target.wind_bearing, fraction
        )
        values["wind_direction"] = bearing_to_direction(values["wind_bearing"])
    return replace(hour, **values)


def _lerp(start: float | None, end: float | None, fraction: float):
    """Interpolate linearly; without both ends, keep the start value."""
    if start is None or end is None:
        return start
    return round_half_away(start + (end - start) * fraction)


def _lerp_bearing(start: float | None, end: float | None, fraction: float):
    """Interpolate a compass bearing the shorter way round (350° -> 10° via 0°)."""
    if start is None or end is None:
        return start
    turn = (end - start + 180) % 360 - 180
    # Rounded as a compass bearing, 0-360, which 359.5 leaves as 0
    return round_half_away((start + turn * fraction) % 360) % 360


def bearing_to_direction(bearing: float) -> str:
    """Return the 16-point compass key for a bearing in degrees."""
    return SIXTEEN_POINTS[int(round_half_away(bearing / 22.5)) % 16]


def mean_bearing(bearings: list[float]) -> float | None:
    """Return the compass average of bearings, as a whole number.

    None when they cancel out.
    """
    radians = [math.radians(bearing) for bearing in bearings]
    east = sum(math.sin(r) for r in radians)
    north = sum(math.cos(r) for r in radians)
    if math.hypot(east, north) < 1e-9:
        return None
    return round_half_away(math.degrees(math.atan2(east, north)) % 360) % 360


def beaufort_force(speed: float) -> str:
    """Return the WIND_STRENGTH_STATES key for a mean wind speed in km/h."""
    return WIND_STRENGTH_STATES[bisect_right(BEAUFORT_LIMITS, speed / 3.6)]


def _hour(start: ForecastPeriod, end: ForecastPeriod, step: int, steps: int):
    """Return the value of the hour `step` hours after `start`, towards `end`.

    met.hu's rows describe the 6 hours that start at their time ("délután" is
    13:00-19:00), so the condition and precipitation belong to `start`'s period;
    temperature, cloud cover, wind and pressure change linearly between the rows.
    """
    fraction = step / steps
    time = (
        start.forecast_time.astimezone(UTC) + timedelta(hours=step)
    ).astimezone(HUNGARY_TZ)
    bearing = _lerp_bearing(start.wind_bearing, end.wind_bearing, fraction)
    both_bearings = start.wind_bearing is not None and end.wind_bearing is not None
    return ForecastPeriod(
        forecast_time=time,
        temperature=_lerp(start.temperature, end.temperature, fraction),
        weather_condition=start.weather_condition,
        precipitation=(
            round_half_away(start.precipitation / steps, 2)
            if start.precipitation is not None
            else None
        ),
        cloud_cover=_lerp(start.cloud_cover, end.cloud_cover, fraction),
        wind_speed=_lerp(start.wind_speed, end.wind_speed, fraction),
        wind_gust=_lerp(start.wind_gust, end.wind_gust, fraction),
        wind_bearing=bearing,
        wind_direction=(
            bearing_to_direction(bearing) if both_bearings else start.wind_direction
        ),
        pressure=_lerp(start.pressure, end.pressure, fraction),
    )


def interpolate_hourly(periods: list[ForecastPeriod]) -> list[ForecastPeriod]:
    """Fill the hours between met.hu's 6-hour rows by linear interpolation."""
    timed = [p for p in periods if p.forecast_time]
    hourly = []
    for start, end in zip(timed, timed[1:]):
        # Count real hours in UTC: Python subtracts times sharing a time zone by
        # wall clock, which would lose or gain the daylight saving hour
        elapsed = end.forecast_time.astimezone(UTC) - start.forecast_time.astimezone(UTC)
        steps = round(elapsed.total_seconds() / 3600)
        hourly.extend(_hour(start, end, step, steps) for step in range(steps))
    if timed:
        # The last row has nothing after it: keep it as one hour of its period
        hourly.append(_hour(timed[-1], timed[-1], 0, 6))
    return hourly


def fill_to_first_row(
    hourly: list[ForecastPeriod], now: datetime
) -> list[ForecastPeriod]:
    """Add the hours from now up to a forecast's first one, with its values.

    met.hu drops a row 4 hours after its time, and a new issue can start with a
    later row still, so a forecast may start after the current hour. The rows of
    an earlier forecast fill that in first, see keep_dropped_row; the hours no
    row covers get the first one's values.
    """
    if not hourly:
        return hourly
    hour = now.astimezone(UTC).replace(minute=0, second=0, microsecond=0)
    ahead = hourly[0].forecast_time.astimezone(UTC) - hour
    # Nothing to add when the first hour is this one or an earlier one
    missing = [
        replace(
            hourly[0],
            forecast_time=(hour + timedelta(hours=step)).astimezone(HUNGARY_TZ),
        )
        for step in range(round(ahead.total_seconds() / 3600))
    ]
    return [*missing, *hourly]


def find_current_hour(hourly: list[ForecastPeriod], now: datetime):
    """Return the interpolated hour that contains now, if the forecast covers it."""
    hour = now.astimezone(UTC).replace(minute=0, second=0, microsecond=0)
    return next(
        (p for p in hourly if p.forecast_time.astimezone(UTC) == hour), None
    )


def aggregate_daily(
    periods: list[ForecastPeriod], now: datetime | None = None
) -> list[ForecastValues]:
    """Sum the rows up into one forecast for each calendar day from `now`'s day.

    Earlier days are left out: last evening's row is kept until 01:00 for the
    hourly forecast, but that day is over.
    """
    first_day = now.astimezone(HUNGARY_TZ).date() if now else date.min
    days: dict[date, list[ForecastPeriod]] = defaultdict(list)
    for p in periods:
        if p.forecast_time and p.forecast_time.date() >= first_day:
            days[p.forecast_time.date()].append(p)
    return [_day_summary(rows) for rows in days.values()]


def _values(rows: list[ForecastPeriod], name: str) -> list[float]:
    return [value for row in rows if (value := getattr(row, name)) is not None]


def _average(rows: list[ForecastPeriod], name: str) -> float | None:
    """Return the rows' average of a value, as a whole number like the rows'."""
    values = _values(rows, name)
    return round_half_away(sum(values) / len(values)) if values else None


def _day_summary(rows: list[ForecastPeriod]) -> ForecastValues:
    """Summarise one day's rows; the middle row stands for its weather condition.

    A day is never clear-night: met.hu's middle row has a night symbol when its
    six hours run past sunset.
    """
    temperatures = _values(rows, "temperature")
    # met.hu's own minimum and maximum win over the 6-hour temperatures
    lows = _values(rows, "temperature_min") or temperatures
    highs = _values(rows, "temperature_max") or temperatures
    precipitation = _values(rows, "precipitation")
    middle = rows[len(rows) // 2].weather_condition
    return ForecastValues(
        # The start of the day, in Hungarian time
        forecast_time=rows[0].forecast_time.replace(hour=0, minute=0, second=0, microsecond=0),
        temperature=(
            round_half_away(sum(temperatures) / len(temperatures), 2)
            if temperatures
            else None
        ),
        temperature_min=min(lows, default=None),
        temperature_max=max(highs, default=None),
        weather_condition="sunny" if middle == "clear-night" else middle,
        precipitation=(
            round_half_away(sum(precipitation), 2) if precipitation else None
        ),
        cloud_cover=_average(rows, "cloud_cover"),
        wind_speed=_average(rows, "wind_speed"),
        wind_gust=max(_values(rows, "wind_gust"), default=None),
        wind_bearing=mean_bearing(_values(rows, "wind_bearing")),
        pressure=_average(rows, "pressure"),
    )
