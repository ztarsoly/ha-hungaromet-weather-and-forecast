"""met.hu's settlement forecast: finding a settlement, reading its forecast
table, and working out the hours and days from the table's 6-hour rows.

met.hu's settlement search (GET /jquery/search.php?term=<name>) answers with
JSON suggestions holding each settlement's name ("value"), code ("kod") and
coordinates ("lat", "lon"). Posting a settlement to the forecast page with
c=tablazat returns its forecast table. The table's <tbody> has one <tr> for
each 6-hour period, in Hungarian local time. A <th class="naptar"> starts each
day, with the month, the day and the weekday in <div>s. The <td>s are told
apart by their first class:

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
import re
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime, timedelta

import aiohttp
from bs4 import BeautifulSoup, Tag
from homeassistant.util import dt as dt_util

_LOGGER = logging.getLogger(__name__)

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

# A 16-point compass, like Home Assistant's weather cards ("n" is 348.75-11.25°)
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
CALM = "calm"
VARIABLE = "variable"
WIND_NAMES = {**dict(zip(COMPASS_NAMES, SIXTEEN_POINTS)), "szélcsend": CALM, "változó": VARIABLE}
WIND_DIRECTION_STATES = [*SIXTEEN_POINTS, CALM, VARIABLE]

# Words of met.hu's weather descriptions and the Home Assistant condition they
# mean, checked in this order, so that "havas eső" is not just rain and
# "derült, párás" is haze. "Derült" (clear) turns into clear-night at night.
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
    dict.fromkeys([*(c for _, c in CONDITION_WORDS), "clear-night", "exceptional"])
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
class ForecastPeriod:
    """The weather of one met.hu row, one interpolated hour or one day."""

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
    wind_direction: str | None = None  # a WIND_DIRECTION_STATES key
    pressure: float | None = None  # hPa


@dataclass
class ForecastData:
    """A settlement's forecast and what is worked out from it."""

    settlement: str = ""
    issued: datetime | None = None
    current: ForecastPeriod | None = None  # the current hour
    periods: list[ForecastPeriod] = field(default_factory=list)  # met.hu's rows
    hourly: list[ForecastPeriod] = field(default_factory=list)  # interpolated
    daily: list[ForecastPeriod] = field(default_factory=list)


async def lookup_settlement(
    session: aiohttp.ClientSession, name: str
) -> Settlement | None:
    """Find a settlement with met.hu's search; an exact name beats the first hit."""
    async with session.get(
        SEARCH_URL,
        params={"term": name},
        headers=REQUEST_HEADERS,
        timeout=aiohttp.ClientTimeout(total=15),
    ) as resp:
        resp.raise_for_status()
        suggestions = await resp.json(content_type=None)
    if not isinstance(suggestions, list) or not suggestions:
        return None

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
    except (KeyError, TypeError, ValueError):
        _LOGGER.error("met.hu's search gave an unusable settlement: %s", chosen)
        return None


async def fetch_forecast(
    session: aiohttp.ClientSession, settlement: Settlement
) -> ForecastData:
    """Download a settlement's forecast table and read it."""
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
    return parse_page(html, settlement.name)


def parse_page(html: str, settlement_name: str, now: datetime | None = None):
    """Read a forecast page; raise ValueError when it has no forecast rows."""
    now = now or dt_util.now()
    soup = BeautifulSoup(html, "html.parser")
    tbody = soup.find("tbody")
    rows = _read_rows(tbody, now) if tbody else []
    if not rows:
        raise ValueError(f"met.hu has no forecast table for {settlement_name}")
    data = ForecastData(
        settlement=settlement_name,
        issued=_issue_time(soup.get_text(" ", strip=True)),
        periods=rows,
    )
    _derive(data, now)
    return data


def _read_rows(tbody: Tag, now: datetime) -> list[ForecastPeriod]:
    """Read the table's rows, each on the day of the last day cell above it."""
    rows = []
    day = None
    for tr in tbody.find_all("tr", recursive=False):
        if (day_cell := tr.find("th", class_="naptar")) is not None:
            day = _calendar_day(day_cell, now)
        cells = _cells_by_class(tr)
        if "ora" in cells:
            rows.append(_row(cells, day or now.astimezone(HUNGARY_TZ).date()))
    return rows


def _cells_by_class(tr: Tag) -> dict[str, list[Tag]]:
    """Group a row's cells by their first class, in the row's order."""
    cells = defaultdict(list)
    for td in tr.find_all("td", recursive=False):
        if classes := td.get("class"):
            cells[classes[0]].append(td)
    return cells


def _calendar_day(th: Tag, now: datetime) -> date | None:
    """Read a day cell; the year is the one that puts the day nearest to now."""
    parts = [div.get_text(strip=True).lower() for div in th.find_all("div")]
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


def _row(cells: dict[str, list[Tag]], day: date) -> ForecastPeriod:
    """Read one forecast row."""
    period = ForecastPeriod(forecast_time=_row_time(_text(cells["ora"][0]), day))
    for td in cells.get("T", []):
        kind = (td.get("class") + [""])[1]
        name = {"X": "temperature_max", "N": "temperature_min"}.get(kind, "temperature")
        setattr(period, name, _number(_text(td)))
    period.precipitation = _first_number(cells, "R")
    cloud = _first_number(cells, "C")
    period.cloud_cover = None if cloud is None else round(cloud)
    speeds = [_number(_text(td)) for td in cells.get("Wf", [])]
    period.wind_speed, period.wind_gust = (speeds + [None, None])[:2]
    period.pressure = _first_number(cells, "P")
    period.wind_bearing, period.wind_direction = _wind(cells)
    period.weather_condition = _weather(cells)
    return period


def _text(td: Tag) -> str:
    return td.get_text(" ", strip=True)


def _number(text: str) -> float | None:
    """Read the number in a cell's text; blank cells have none."""
    match = NUMBER.search(text.replace("−", "-").replace("–", "-"))
    # Adding 0.0 turns met.hu's "-0" into 0.0
    return float(match[0].replace(",", ".")) + 0.0 if match else None


def _first_number(cells: dict[str, list[Tag]], name: str) -> float | None:
    return _number(_text(cells[name][0])) if cells.get(name) else None


def _row_time(text: str, day: date) -> datetime | None:
    """Put a row's "HH:MM" on its day, in Hungarian local time."""
    match = re.match(r"(\d{1,2}):(\d{2})", text)
    if match is None:
        return None
    hour, minute = int(match[1]), int(match[2])
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=HUNGARY_TZ)


def _wind(cells: dict[str, list[Tag]]) -> tuple[float | None, str | None]:
    """Return the wind's bearing and its direction on the 16-point compass.

    met.hu's direction names are coarser (12° is "északi"), so the direction
    comes from the bearing, like in the interpolated hours. Calm and variable
    wind keep met.hu's word.
    """
    tooltips = " ".join(
        td.get("onmouseover", "") for td in cells.get("Wikon", []) + cells.get("Wd", [])
    )
    match = re.search(r"\((\d+(?:\.\d+)?)\s*fok\)", tooltips)
    bearing = float(match[1]) if match else None
    name = _text(cells["Wd"][0]).lower() if cells.get("Wd") else ""
    direction = WIND_NAMES.get(name)
    if name and direction is None:
        _LOGGER.warning("Unknown met.hu wind direction %s", name)
    if bearing is None or direction in (CALM, VARIABLE):
        return bearing, direction
    return bearing, bearing_to_direction(bearing)


def _weather(cells: dict[str, list[Tag]]) -> str | None:
    """Return the Home Assistant condition of a row's weather symbol."""
    for td in cells.get("idoikon", []):
        img = td.find("img")
        symbol = re.search(r"(w\d+)(e?)\.\w+$", img.get("src", "")) if img else None
        if symbol is None:
            continue  # the spacer in front of the symbol
        tooltip = re.search(r"class=ktext>([^<]*)<", td.get("onmouseover", ""))
        description = tooltip[1].strip() if tooltip else ""
        condition = condition_for(description, night=symbol[2] == "e")
        if condition == "exceptional":
            _LOGGER.warning("Unknown met.hu weather %r (%s)", description, symbol[1])
        return condition
    return None


def condition_for(description: str, night: bool = False) -> str:
    """Return the Home Assistant condition for met.hu's weather description."""
    text = description.lower()
    for word, condition in CONDITION_WORDS:
        if word in text:
            return "clear-night" if condition == "sunny" and night else condition
    return "exceptional"


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
    data.hourly = interpolate_hourly(data.periods)
    data.current = current_period(data.hourly, data.periods, now)
    data.daily = aggregate_daily(data.periods, now)


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
    }


def forecast_from_dict(stored: dict, now: datetime) -> ForecastData:
    """Rebuild a stored forecast, working out its hours for `now` again."""

    def period(values: dict) -> ForecastPeriod:
        time = values.get("forecast_time")
        return ForecastPeriod(
            **{**values, "forecast_time": datetime.fromisoformat(time) if time else None}
        )

    issued = stored.get("issued")
    data = ForecastData(
        settlement=stored["settlement"],
        issued=datetime.fromisoformat(issued) if issued else None,
        periods=[period(p) for p in stored["periods"]],
    )
    _derive(data, now)
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
    """
    if previous is None or not data.periods or data.periods[0].forecast_time is None:
        return data
    first = data.periods[0].forecast_time.astimezone(UTC)
    now_utc = now.astimezone(UTC)
    today = now.astimezone(HUNGARY_TZ).date()
    kept = [
        p
        for p in previous.periods
        if p.forecast_time
        and p.forecast_time.astimezone(UTC) < first
        and (
            p.forecast_time.astimezone(HUNGARY_TZ).date() == today
            or now_utc < p.forecast_time.astimezone(UTC) + PERIOD
        )
    ]
    if not kept:
        return data
    data.periods = [*kept, *data.periods]
    _derive(data, now)
    return data


def current_period(hourly, periods, now: datetime) -> ForecastPeriod | None:
    """Return the current hour, or met.hu's nearest row outside the hourly range."""
    return find_current_hour(hourly, now) or (
        find_current(periods, now) if periods else None
    )


def advance_to_now(data: ForecastData, now: datetime) -> None:
    """Point the forecast's current hour at `now`, without downloading again.

    The daily forecast is worked out again too, so it starts at the new day
    after midnight.
    """
    data.current = current_period(data.hourly, data.periods, now)
    data.daily = aggregate_daily(data.periods, now)


def find_current(periods: list[ForecastPeriod], now: datetime):
    """Return the first row at most 3 hours old, or the last row."""
    since = now - timedelta(hours=3)
    return next(
        (p for p in periods if p.forecast_time and p.forecast_time >= since),
        periods[-1],
    )


def _lerp(start: float | None, end: float | None, fraction: float):
    """Interpolate linearly; without both ends, keep the start value."""
    if start is None or end is None:
        return start
    return round(start + (end - start) * fraction, 2)


def _lerp_bearing(start: float | None, end: float | None, fraction: float):
    """Interpolate a compass bearing the shorter way round (350° -> 10° via 0°)."""
    if start is None or end is None:
        return start
    turn = (end - start + 180) % 360 - 180
    return round((start + turn * fraction) % 360, 2)


def bearing_to_direction(bearing: float) -> str:
    """Return the 16-point compass key for a bearing in degrees."""
    return SIXTEEN_POINTS[round(bearing / 22.5) % 16]


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
            round(start.precipitation / steps, 2)
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


def find_current_hour(hourly: list[ForecastPeriod], now: datetime):
    """Return the interpolated hour that contains now, if the forecast covers it."""
    hour = now.astimezone(UTC).replace(minute=0, second=0, microsecond=0)
    return next(
        (p for p in hourly if p.forecast_time.astimezone(UTC) == hour), None
    )


def aggregate_daily(
    periods: list[ForecastPeriod], now: datetime | None = None
) -> list[ForecastPeriod]:
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


def _day_summary(rows: list[ForecastPeriod]) -> ForecastPeriod:
    """Summarise one day's rows; the middle row stands for its sky and wind."""
    temperatures = _values(rows, "temperature")
    # met.hu's own minimum and maximum win over the 6-hour temperatures
    lows = _values(rows, "temperature_min") or temperatures
    highs = _values(rows, "temperature_max") or temperatures
    precipitation = _values(rows, "precipitation")
    pressures = _values(rows, "pressure")
    middle = rows[len(rows) // 2]
    return ForecastPeriod(
        forecast_time=rows[0].forecast_time.replace(hour=12, minute=0),
        temperature=(
            round(sum(temperatures) / len(temperatures), 1) if temperatures else None
        ),
        temperature_min=min(lows, default=None),
        temperature_max=max(highs, default=None),
        weather_condition=middle.weather_condition,
        precipitation=round(sum(precipitation), 1) if precipitation else None,
        cloud_cover=middle.cloud_cover,
        wind_speed=max(_values(rows, "wind_speed"), default=None),
        wind_gust=max(_values(rows, "wind_gust"), default=None),
        wind_bearing=middle.wind_bearing,
        wind_direction=middle.wind_direction,
        pressure=round(sum(pressures) / len(pressures), 1) if pressures else None,
    )
