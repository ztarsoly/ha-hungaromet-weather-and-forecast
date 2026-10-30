"""Tests for the met.hu settlement forecast."""

from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest
from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.hungaromet_weather_and_forecast import forecast, options_flow, scheduler
from custom_components.hungaromet_weather_and_forecast import weather as weather_platform
from custom_components.hungaromet_weather_and_forecast.const import (
    CONF_FORECAST,
    CONF_LOCATION,
    LOCATION_HOME,
    LOCATION_SETTLEMENT,
)
from custom_components.hungaromet_weather_and_forecast.scope import EntryScope
from custom_components.hungaromet_weather_and_forecast.weather_data import _get_reference_coords
from custom_components.hungaromet_weather_and_forecast.forecast_scraper import (
    CONDITION_STATES,
    HUNGARY_TZ,
    WIND_DIRECTION_STATES,
    ForecastData,
    ForecastPeriod,
    Settlement,
    aggregate_daily,
    condition_for,
    fetch_forecast,
    find_current,
    lookup_settlement,
    parse_page,
)
from custom_components.hungaromet_weather_and_forecast.forecast_sensor import (
    FORECAST_SENSORS,
    ForecastSensor,
    forecast_sensors,
)

NOW = datetime(2026, 10, 28, 10, 30, tzinfo=HUNGARY_TZ)
ENTRY = SimpleNamespace(entry_id="e1", title="HungaroMet")
DEBRECEN = Settlement(name="Debrecen", kod="725", lat=47.543, lon=21.648)


def _day(month, day, rows):
    """A day cell like met.hu's: month, day and weekday."""
    return (
        f"<th class='naptar' rowspan={rows}>"
        f"<div>{month}</div><div>{day}</div><div>szerda</div></th>"
    )


def _tip(html):
    """An onmouseover tooltip attribute, quoted like met.hu's."""
    return f"onmouseover=\"Tip('{html}')\""


def _forecast_row(
    time, temperature, symbol, description, wind, bearing, speeds, extreme=None, day=""
):
    """A forecast row shaped like met.hu's table.

    `extreme` is ("N", value) or ("X", value) for the day's minimum or maximum.
    """
    kind, value = extreme or ("N", "&nbsp;")
    bearing_tip = _tip(f"<div class=ikon-title><br>{wind}<br>({bearing} fok)</div>")
    return (
        f"<tr>{day}<td class='ora rbg'>{time}</td>"
        f"<td class='T {kind} rbg0'><P>{value}</P></td>"
        f"<td class='T rbg0'><P>{temperature}</P></td>"
        "<td class='idoikon rbg0'><img src='/images/spacer.png' border=0></td>"
        "<td class='R rbg0'><P>1.5</P></td>"
        "<td class='idoikon rbg0' "
        + _tip(f"<div class=title><div class=ktext>{description}</div></div>")
        + f"><img src='/images/idokepf24x/{symbol}.png' border=0></td>"
        "<td class='C rbg0'><P>40</P></td>"
        f"<td class='Wikon rbg0' {bearing_tip}><img src='/images/wind35png/wind_0.png'></td>"
        f"<td class='Wd rbg0' {bearing_tip}>{wind}</td>"
        f"<td class='Wf rbg0'><p>{speeds[0]}</p></td>"
        f"<td class='Wf rbg0'><p>{speeds[1]}</p></td>"
        "<td class='P rbg0'><P>1022</P></td></tr>"
    )


PAGE = (
    "<html><body><div>HungaroMet: 2026. október 28. 09:46 (08:46 &nbsp;UTC)</div>"
    "<table><tbody>"
    + _forecast_row(
        "07:00", 4, "w002", "kissé felhős", "északi", 12, (8, 23), ("N", -1),
        _day("október", 28, 3),
    )
    + _forecast_row("13:00", 16, "w103", "zápor", "északi", 12, (6, 20), ("X", 21))
    + _forecast_row("19:00", "-0", "w001e", "derült", "szélcsend", 12, (2, 9))
    + "<tr><th>no data row</th></tr>"
    + _forecast_row(
        "01:00", 3, "w999", "furcsa idő", "furcsa", 12, (4, 12), day=_day("október", 29, 1)
    )
    + "</tbody></table></body></html>"
)


@pytest.fixture
def data():
    return parse_page(PAGE, "Debrecen", NOW)


def test_parse_page_reads_every_row(data):
    assert data.settlement == "Debrecen"
    assert data.issued == datetime(2026, 10, 28, 9, 46, tzinfo=HUNGARY_TZ)
    assert [p.forecast_time.isoformat() for p in data.periods] == [
        "2026-10-28T07:00:00+01:00",
        "2026-10-28T13:00:00+01:00",
        "2026-10-28T19:00:00+01:00",
        "2026-10-29T01:00:00+01:00",
    ]
    morning, afternoon, evening, _ = data.periods
    assert (morning.temperature, morning.temperature_min, morning.temperature_max) == (
        4,
        -1,
        None,
    )
    assert (afternoon.temperature_min, afternoon.temperature_max) == (None, 21)
    # met.hu writes "-0"
    assert str(evening.temperature) == "0.0"
    assert (morning.precipitation, morning.cloud_cover, morning.pressure) == (1.5, 40, 1022)
    assert (morning.wind_speed, morning.wind_gust, morning.wind_bearing) == (8, 23, 12)


def test_parse_page_conditions_and_wind(caplog):
    data = parse_page(PAGE, "Debrecen", NOW)
    assert [p.weather_condition for p in data.periods] == [
        "partlycloudy",
        "pouring",
        "clear-night",
        "exceptional",
    ]
    # The direction comes from the bearing on 16 points (met.hu says "északi"
    # for 12°, which is NNE); calm stays calm, and an unknown name still gets
    # a direction from its bearing
    assert [p.wind_direction for p in data.periods] == ["nne", "nne", "calm", "nne"]
    assert "Unknown met.hu weather 'furcsa idő' (w999)" in caplog.text
    assert "Unknown met.hu wind direction furcsa" in caplog.text


@pytest.mark.parametrize(
    ("description", "night", "condition"),
    [
        ("derült", False, "sunny"),
        ("derült", True, "clear-night"),
        ("derült, párás", False, "fog"),
        ("kissé felhős", True, "partlycloudy"),
        ("fátyolfelhős", False, "partlycloudy"),
        ("erősen fátyolfelhős", False, "cloudy"),
        ("erősen felhős", False, "cloudy"),
        ("borult", False, "cloudy"),
        ("ködös", False, "fog"),
        ("szitálás", False, "rainy"),
        ("gyenge eső", False, "rainy"),
        ("zápor", False, "pouring"),
        ("zivatar", False, "lightning-rainy"),
        ("havas eső", False, "snowy-rainy"),
        ("ónos eső", False, "snowy-rainy"),
        ("hószállingózás", False, "snowy"),
        ("havazás", False, "snowy"),
        ("hózápor", False, "snowy"),
        ("jégeső", False, "hail"),
        ("", False, "exceptional"),
    ],
)
def test_condition_for(description, night, condition):
    assert condition_for(description, night) == condition
    assert condition in CONDITION_STATES


def test_current_period_is_the_current_hour(data):
    # 10:30 falls in the 10:00 hour, interpolated between 07:00 and 13:00
    assert data.current.forecast_time.hour == 10
    assert data.current.temperature == 10.0
    late = NOW + timedelta(days=5)
    assert find_current(data.periods, late) is data.periods[-1]


def test_daily_summary(data):
    today, tomorrow = data.daily
    assert today.forecast_time.hour == 12
    assert (today.temperature_min, today.temperature_max) == (-1, 21)
    assert today.temperature == round((4 + 16 + 0) / 3, 1)
    assert today.precipitation == 4.5
    assert (today.wind_speed, today.wind_gust) == (8, 23)
    assert today.weather_condition == "pouring"  # the middle row
    assert (tomorrow.temperature_min, tomorrow.temperature_max) == (3, 3)


def test_daily_summary_tolerates_missing_values():
    period = ForecastPeriod(forecast_time=NOW)
    (day,) = aggregate_daily([period, ForecastPeriod()])
    assert day.temperature is None and day.precipitation is None
    assert day.temperature_min is None and day.wind_speed is None


@pytest.mark.parametrize(
    "html",
    ["<html></html>", "<table><tbody><tr><th>x</th></tr></tbody></table>"],
)
def test_parse_page_without_forecast_raises(html):
    with pytest.raises(ValueError):
        parse_page(html, "Sehol", NOW)


def test_dates_take_the_nearest_year():
    page = (
        "<table><tbody>"
        + _forecast_row("07:00", 1, "w002", "x", "keleti", 90, (1, 2), day=_day("január", 2, 2))
        + _forecast_row("bad", 1, "w002", "x", "keleti", 90, (1, 2))
        + "</tbody></table><p>HungaroMet: 2027. hónap 2. 09:46</p>"
    )
    december = datetime(2026, 12, 30, 12, 0, tzinfo=HUNGARY_TZ)
    data = parse_page(page, "X", december)
    assert data.periods[0].forecast_time.year == 2027
    assert data.periods[1].forecast_time is None
    assert data.issued is None


@pytest.mark.parametrize(
    "day",
    [
        "<th class='naptar'><div>x</div></th>",
        _day("smarch", 1, 1),
        _day("május", "x", 1),
        _day("február", 29, 1),
    ],
)
def test_unreadable_days_fall_back_to_today(day):
    page = (
        "<table><tbody>"
        + _forecast_row("07:00", 1, "w002", "x", "keleti", 90, (1, 2), day=day)
        + "</tbody></table>"
    )
    (period,) = parse_page(page, "X", NOW).periods
    assert period.forecast_time.date() == NOW.date()


def test_row_without_symbol_wind_or_values():
    page = (
        "<table><tbody><tr><td class='ora'>07:00</td>"
        "<td class='idoikon'><img src='/images/spacer.png'></td><td class='idoikon'></td>"
        "<td class='C'>&nbsp;</td><td class='Wikon'></td><td>no class</td>"
        "</tr></tbody></table>"
    )
    (period,) = parse_page(page, "X", NOW).periods
    assert period.weather_condition is None
    assert period.cloud_cover is None
    assert (period.wind_bearing, period.wind_direction) == (None, None)
    assert (period.wind_speed, period.wind_gust, period.pressure) == (None, None, None)


class FakeResponse:
    def __init__(self, json_data=None, text=""):
        self._json = json_data
        self._text = text

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    def raise_for_status(self):
        return None

    async def json(self, content_type=None):
        return self._json

    async def text(self, encoding=None, errors=None):
        return self._text


def _session(json_data=None, text=""):
    response = FakeResponse(json_data, text)
    return SimpleNamespace(
        get=MagicMock(return_value=response), post=MagicMock(return_value=response)
    )


@pytest.mark.asyncio
async def test_lookup_settlement_prefers_the_exact_name():
    session = _session([
        {"kod": 726, "value": "Debrecen-Téglás", "lon": "21.625", "lat": "47.532"},
        {"kod": 725, "value": "Debrecen", "lon": "21.648", "lat": "47.543"},
    ])

    assert await lookup_settlement(session, " debrecen ") == DEBRECEN
    assert session.get.call_args.kwargs["params"] == {"term": " debrecen "}


@pytest.mark.asyncio
async def test_lookup_settlement_takes_the_first_suggestion_otherwise():
    session = _session([{"kod": 9, "value": "Pécs", "lon": "18", "lat": "46"}])
    assert (await lookup_settlement(session, "Pec")).name == "Pécs"


@pytest.mark.asyncio
@pytest.mark.parametrize("json_data", [[], None, {"error": 1}])
async def test_lookup_settlement_without_suggestions(json_data):
    assert await lookup_settlement(_session(json_data), "X") is None


@pytest.mark.asyncio
async def test_lookup_settlement_with_an_unusable_suggestion(caplog):
    session = _session([{"value": "X", "lat": "1", "lon": "2"}])
    assert await lookup_settlement(session, "X") is None
    assert "unusable settlement" in caplog.text


@pytest.mark.asyncio
async def test_fetch_forecast_posts_the_settlement():
    session = _session(text=PAGE)

    data = await fetch_forecast(session, DEBRECEN)

    assert session.post.call_args.kwargs["data"] == {
        "kod": "725",
        "lt": "47.543",
        "n": "21.648",
        "tel": "Debrecen",
        "c": "tablazat",
    }
    assert len(data.periods) == 4


def test_stored_settlement_prefers_options():
    stored = {"name": "Pécs", "kod": "9", "lat": 46.0, "lon": 18.0}
    entry = SimpleNamespace(data={CONF_FORECAST: forecast.settlement_to_dict(DEBRECEN)}, options={})
    assert forecast.stored_settlement(entry) == DEBRECEN

    entry.options = {CONF_FORECAST: stored}
    assert forecast.stored_settlement(entry).name == "Pécs"

    entry.options = {CONF_FORECAST: None}
    assert forecast.stored_settlement(entry) is None
    assert forecast.settlement_to_dict(None) is None


@pytest.mark.parametrize(
    ("data", "options", "expected"),
    [
        # Home is the default, and also the fallback without a settlement
        ({}, {}, None),
        ({CONF_LOCATION: LOCATION_SETTLEMENT}, {}, None),
        ({CONF_FORECAST: {"name": "Debrecen", "kod": "725", "lat": 47.5, "lon": 21.6}}, {}, None),
        (
            {
                CONF_FORECAST: {"name": "Debrecen", "kod": "725", "lat": 47.5, "lon": 21.6},
                CONF_LOCATION: LOCATION_SETTLEMENT,
            },
            {},
            (47.5, 21.6),
        ),
        # Options win over the setup data
        (
            {CONF_LOCATION: LOCATION_HOME},
            {
                CONF_FORECAST: {"name": "Pécs", "kod": "9", "lat": 46.0, "lon": 18.2},
                CONF_LOCATION: LOCATION_SETTLEMENT,
            },
            (46.0, 18.2),
        ),
        (
            {CONF_LOCATION: LOCATION_SETTLEMENT},
            {
                CONF_FORECAST: {"name": "Pécs", "kod": "9", "lat": 46.0, "lon": 18.2},
                CONF_LOCATION: LOCATION_HOME,
            },
            None,
        ),
    ],
)
def test_sensor_location(data, options, expected):
    entry = SimpleNamespace(data=data, options=options)
    assert forecast.sensor_location(entry) == expected


def test_reference_coords_prefer_the_given_location():
    hass = SimpleNamespace(config=SimpleNamespace(latitude=47.0, longitude=19.0))
    assert _get_reference_coords(hass) == (47.0, 19.0)
    assert _get_reference_coords(hass, (46.0, 18.2)) == (46.0, 18.2)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("user_input", "resolved", "expected"),
    [
        ({CONF_LOCATION: LOCATION_HOME}, (None, None), (None, {})),
        ({CONF_LOCATION: LOCATION_SETTLEMENT}, (None, None), (None, {CONF_LOCATION: "settlement_required"})),
        ({CONF_LOCATION: LOCATION_SETTLEMENT}, (DEBRECEN, None), (DEBRECEN, {})),
        ({CONF_LOCATION: LOCATION_HOME}, (None, "cannot_connect"), (None, {"forecast_settlement": "cannot_connect"})),
    ],
)
async def test_validate_forecast(user_input, resolved, expected):
    with patch.object(options_flow, "async_resolve_settlement", AsyncMock(return_value=resolved)):
        assert await options_flow.async_validate_forecast(MagicMock(), user_input) == expected


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("name", "lookup", "expected"),
    [
        ("  ", None, (None, None)),
        (None, None, (None, None)),
        ("Debrecen", AsyncMock(return_value=DEBRECEN), (DEBRECEN, None)),
        ("Sehol", AsyncMock(return_value=None), (None, "settlement_not_found")),
        ("Debrecen", AsyncMock(side_effect=aiohttp.ClientError), (None, "cannot_connect")),
        ("Debrecen", AsyncMock(side_effect=TimeoutError), (None, "cannot_connect")),
    ],
)
async def test_resolve_settlement(name, lookup, expected):
    with (
        patch.object(forecast, "async_get_clientsession"),
        patch.object(forecast, "lookup_settlement", lookup),
    ):
        assert await forecast.async_resolve_settlement(MagicMock(), name) == expected


def test_forecast_device_info():
    info = forecast.forecast_device_info(EntryScope.from_entry(ENTRY))
    assert info["identifiers"] == {("hungaromet_weather_and_forecast", "e1_hungaromet_forecast")}
    assert info["translation_key"] == "forecast"
    assert info["translation_placeholders"] == {"name": "HungaroMet"}
    assert info["configuration_url"].startswith("https://www.met.hu/")


def _coordinator():
    coordinator = object.__new__(forecast.ForecastCoordinator)
    coordinator.hass = MagicMock()
    coordinator.settlement = DEBRECEN
    coordinator.data = None  # nothing downloaded before
    coordinator._store = MagicMock(async_save=AsyncMock(), async_load=AsyncMock())
    return coordinator


@pytest.mark.asyncio
async def test_coordinator_update_fetches_forecast(data):
    coordinator = _coordinator()
    with (
        patch.object(forecast, "async_get_clientsession"),
        patch.object(forecast, "fetch_forecast", AsyncMock(return_value=data)),
    ):
        assert await coordinator._async_update_data() is data


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [aiohttp.ClientError("x"), TimeoutError(), ValueError("x")])
async def test_coordinator_update_failure(error):
    coordinator = _coordinator()
    with (
        patch.object(forecast, "async_get_clientsession"),
        patch.object(forecast, "fetch_forecast", AsyncMock(side_effect=error)),
        pytest.raises(UpdateFailed, match="Debrecen"),
    ):
        await coordinator._async_update_data()


def test_coordinator_init():
    hass = MagicMock()
    entry = MagicMock()
    with patch.object(forecast.DataUpdateCoordinator, "__init__", return_value=None) as init:
        coordinator = forecast.ForecastCoordinator(hass, entry, DEBRECEN)
    assert coordinator.settlement == DEBRECEN
    assert init.call_args.kwargs["update_interval"] is None
    assert init.call_args.kwargs["config_entry"] is entry


@pytest.mark.asyncio
async def test_setup_forecast_without_settlement():
    entry = SimpleNamespace(data={}, options={})
    assert await forecast.async_setup_forecast(MagicMock(), entry) is None


@pytest.mark.asyncio
async def test_setup_forecast_schedules_refreshes():
    entry = MagicMock(
        entry_id="e1",
        data={CONF_FORECAST: forecast.settlement_to_dict(DEBRECEN)},
        options={},
    )
    hass = MagicMock(data={})
    coordinator = MagicMock(async_load_stored=AsyncMock())
    schedule = coordinator.schedule
    with (
        patch.object(forecast, "ForecastCoordinator", return_value=coordinator),
        patch.object(forecast, "async_track_utc_time_change") as track,
    ):
        assert await forecast.async_setup_forecast(hass, entry) is coordinator

    # The current hour moves on at every full hour, without downloading
    track.assert_called_once_with(
        hass, coordinator.async_advance_hour, minute=0, second=0
    )

    unloads = [c.args[0] for c in entry.async_on_unload.call_args_list]
    assert unloads[0] is schedule.start.return_value
    # The update service can refresh this forecast too
    registered = hass.data["hungaromet_weather_and_forecast_updaters"]
    assert registered == {"e1_forecast": coordinator.async_update_now}
    unloads[1]()
    assert registered == {}
    assert unloads[2] is track.return_value
    # The first download is left until the entities are added
    schedule.async_run_now.assert_not_called()
    # The forecast saved before a restart is loaded first
    coordinator.async_load_stored.assert_awaited_once()


def test_coordinator_schedule():
    entry = SimpleNamespace(entry_id="e1")
    with (
        patch.object(forecast.DataUpdateCoordinator, "__init__", return_value=None),
        patch.object(forecast, "forecast_store"),
        patch.object(forecast, "RetryingSchedule") as cls,
    ):
        coordinator = forecast.ForecastCoordinator("hass", entry, DEBRECEN)

    args, kwargs = cls.call_args
    assert args[:2] == ("hass", "forecast")
    assert args[2] == coordinator._async_scheduled_download
    assert args[3] == forecast.FORECAST_RETRY_DELAY
    assert kwargs == {"config_entry": entry, **forecast.FORECAST_SCHEDULE}
    assert coordinator.schedule is cls.return_value


def _scheduled_coordinator(listeners):
    coordinator = object.__new__(forecast.ForecastCoordinator)
    coordinator._listeners = listeners
    coordinator.async_refresh = AsyncMock()
    coordinator.last_update_success = False
    return coordinator


@pytest.mark.asyncio
async def test_scheduled_download_reports_whether_it_worked():
    coordinator = _scheduled_coordinator({1: (MagicMock(), None)})

    assert await coordinator._async_scheduled_download(None) is False
    coordinator.last_update_success = True
    assert await coordinator._async_scheduled_download(None) is True
    assert coordinator.async_refresh.await_count == 2


@pytest.mark.asyncio
async def test_disabled_forecast_is_not_downloaded():
    """No enabled entity shows the forecast: nothing to download or retry."""
    coordinator = _scheduled_coordinator({})

    assert await coordinator._async_scheduled_download(None) is True
    await coordinator.async_update_now()

    coordinator.async_refresh.assert_not_awaited()


@pytest.mark.asyncio
async def test_update_service_downloads_a_shown_forecast():
    coordinator = _scheduled_coordinator({1: (MagicMock(), None)})
    await coordinator.async_update_now()
    coordinator.async_refresh.assert_awaited_once()


@pytest.mark.asyncio
async def test_schedule_run_now_retries_like_a_scheduled_run(monkeypatch):
    later = []
    monkeypatch.setattr(scheduler, "async_call_later", lambda h, d, a: later.append(d))
    job = AsyncMock(return_value=False)

    await scheduler.RetryingSchedule(None, "test", job, timedelta(minutes=10)).async_run_now()

    job.assert_awaited_once()
    assert later == [timedelta(minutes=10)]


def _sensor(key, data):
    description = next(d for d in FORECAST_SENSORS if d.key == key)
    return ForecastSensor(MagicMock(data=data, config_entry=ENTRY), description)


def test_forecast_sensors_cover_every_description():
    sensors = forecast_sensors(MagicMock(config_entry=ENTRY))
    assert [s.entity_description.key for s in sensors] == [d.key for d in FORECAST_SENSORS]
    assert sensors[0].unique_id == "e1_hungaromet_forecast_temperature"
    assert sensors[0].device_info["translation_key"] == "forecast"


def test_forecast_sensor_values(data):
    assert _sensor("temperature", data).native_value == 10.0
    assert _sensor("temperature_min", data).native_value == -1
    assert _sensor("temperature_max", data).native_value == 21
    assert _sensor("wind_direction", data).native_value == "nne"
    assert _sensor("weather_condition", data).native_value == "partlycloudy"
    assert _sensor("precipitation", data).native_value == 0.25
    assert _sensor("issued", data).native_value == data.issued


def test_forecast_sensor_attributes(data):
    attrs = _sensor("temperature", data).extra_state_attributes
    assert attrs["settlement"] == "Debrecen"
    assert attrs["forecast_time"] == "2026-10-28T10:00:00+01:00"
    assert [a["value"] for a in attrs["forecast"]] == [4, 16, 0.0, 3]
    assert attrs["daily_forecast"][0] == {"date": "2026-10-28", "value": 6.7}
    assert _sensor("issued", data).extra_state_attributes is None


def test_forecast_sensor_without_data():
    assert _sensor("temperature", None).native_value is None
    assert _sensor("temperature", None).extra_state_attributes is None
    empty = ForecastData(settlement="X")
    assert _sensor("temperature_min", empty).native_value is None
    assert _sensor("temperature", empty).native_value is None
    assert _sensor("temperature", empty).extra_state_attributes == {
        "settlement": "X",
        "forecast": [],
        "daily_forecast": [],
    }


def test_today_without_matching_day(data):
    data.daily = []
    assert _sensor("temperature_max", data).native_value is None


def test_enum_sensors_list_their_states():
    by_key = {d.key: d for d in FORECAST_SENSORS}
    assert by_key["weather_condition"].device_class == SensorDeviceClass.ENUM
    assert by_key["weather_condition"].options == CONDITION_STATES
    assert by_key["wind_direction"].options == WIND_DIRECTION_STATES
    assert by_key["wind_bearing"].device_class == SensorDeviceClass.WIND_DIRECTION
    assert "exceptional" in CONDITION_STATES
    assert {"calm", "variable", "nne"} <= set(WIND_DIRECTION_STATES)


def _weather(data):
    with patch.object(weather_platform.SingleCoordinatorWeatherEntity, "__init__", return_value=None):
        entity = weather_platform.HungarometForecastWeather(
            MagicMock(config_entry=ENTRY)
        )
    entity.coordinator = MagicMock(data=data)
    return entity


def test_weather_current_conditions(data):
    entity = _weather(data)
    assert entity.unique_id == "e1_hungaromet_forecast_weather"
    assert entity.device_info["identifiers"] == {
        ("hungaromet_weather_and_forecast", "e1_hungaromet_forecast")
    }
    assert entity.condition == "partlycloudy"
    assert entity.native_temperature == 10.0
    assert entity.native_pressure == 1022
    assert entity.native_wind_speed == 7.0
    assert entity.native_wind_gust_speed == 21.5
    assert entity.wind_bearing == 12
    assert entity.cloud_coverage == 40
    assert entity.extra_state_attributes == {
        "settlement": "Debrecen",
        "issued": "2026-10-28T09:46:00+01:00",
    }


def test_weather_forecasts(data):
    entity = _weather(data)
    daily = entity._async_forecast_daily()
    hourly = entity._async_forecast_hourly()
    assert daily[0]["native_temperature"] == 21
    assert daily[0]["native_templow"] == -1
    assert daily[0]["datetime"] == "2026-10-28T12:00:00+01:00"
    # One entry per hour from the current hour (10:00) to the last row at 01:00
    assert len(hourly) == 16
    assert hourly[0]["datetime"] == "2026-10-28T10:00:00+01:00"
    assert hourly[0]["native_temperature"] == 10.0
    assert hourly[3]["native_temperature"] == 16
    assert "native_templow" not in hourly[0]
    assert hourly[3]["native_wind_gust_speed"] == 20


def test_weather_without_data():
    entity = _weather(None)
    assert entity.condition is None
    assert entity.native_temperature is None
    assert entity.native_pressure is None
    assert entity.native_wind_speed is None
    assert entity.native_wind_gust_speed is None
    assert entity.wind_bearing is None
    assert entity.cloud_coverage is None
    assert entity.extra_state_attributes is None
    assert entity._async_forecast_daily() is None
    assert entity._async_forecast_hourly() is None
    empty = _weather(ForecastData(settlement="X"))
    assert empty.extra_state_attributes == {"settlement": "X", "issued": None}


@pytest.mark.asyncio
async def test_weather_platform_setup():
    add = MagicMock()
    hass = SimpleNamespace(data={"hungaromet_weather_and_forecast": {"abc": MagicMock()}})

    await weather_platform.async_setup_entry(hass, SimpleNamespace(entry_id="abc"), add)
    add.assert_called_once()

    add.reset_mock()
    hass.data = {"hungaromet_weather_and_forecast": {"abc": None}}
    await weather_platform.async_setup_entry(hass, SimpleNamespace(entry_id="abc"), add)
    add.assert_not_called()
