"""Tests for the settlement's alerts and warnings."""

# The tests check the integration's internals
# pylint: disable=protected-access

from datetime import date, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.hungaromet_weather_and_forecast import warning, warning_scraper
from custom_components.hungaromet_weather_and_forecast.forecast_scraper import (
    HUNGARY_TZ,
    Settlement,
)
from custom_components.hungaromet_weather_and_forecast.warning_scraper import (
    ALERT_PHENOMENA,
    PHENOMENON_NAMES,
    WARNING_PHENOMENA,
    Warnings,
    _distance,
    county_of,
    fetch_warnings,
    find_district,
    highest_level,
    in_force,
    parse_snippet,
    today_warning_map,
)
from custom_components.hungaromet_weather_and_forecast.warning_sensor import (
    WARNING_SENSORS,
    warning_sensors,
)

DEBRECEN = Settlement(name="Debrecen", kod="725", lat=47.543, lon=21.648)

NOTHING = """
		<table cellspacing=1 cellpadding=0 class='stbl' width='100%'>
		<tr><th colspan=3>Debreceni járás</th></tr>
		<tr><td colspan=3>&nbsp;Nincs érvényben riasztás</td></tr>
	</table>"""

THUNDERSTORM_AND_WIND = """
		<table cellspacing=1 cellpadding=0 class='stbl' width='100%'>
		<tr><th colspan=3>Hajdú-Bihar megye</th></tr>
		<tr>
			<td class='row1' width=31><img src='/images/warningb/ts1.gif' border=0></td>
			<td class='row1' width=27><img src='/images/warningb/w1.gif' border=0></td>
			<td class='row1' width=170>Zivatar</td>
		</tr>
		<tr>
			<td class='row1' width=31><img src='/images/warningb/wind2.gif' border=0></td>
			<td class='row1' width=27><img src='/images/warningb/w2.gif' border=0></td>
			<td class='row1' width=170>Széllökés</td>
		</tr>
	</table>
	<div class='kt-friss'><div>Kiadva: 2026-11-17 09:57 (08:57 UTC)</div><div>[wbhx]</div></div>"""


@pytest.mark.parametrize(
    ("lat", "lon", "district"),
    [
        (47.543, 21.648, "903"),  # Debrecen: Debreceni járás
        (47.498, 19.040, "101"),  # Budapest
        (46.073, 18.233, "204"),  # Pécs: Pécsi járás
        (47.685, 16.590, "806"),  # Sopron: Soproni járás
    ],
)
def test_find_district(lat, lon, district):
    """Test find district."""
    assert find_district(lat, lon) == district


def test_a_place_outside_the_drawn_map_gets_the_nearest_district():
    """Test a place outside the drawn map gets the nearest district."""
    # Szob lies on the Danube, just outside the coarsely drawn polygons
    assert find_district(47.82, 18.87) == "1316"


def test_distance_from_a_border_with_a_repeated_point():
    """Test distance from a border with a repeated point."""
    # The map's polygons end with their first point twice
    assert _distance(3, 4, [0, 0, 0, 0]) == 5


@pytest.mark.parametrize(
    ("district", "county"),
    [("903", 9), ("204", 2), ("1316", 13), ("2003", 20), ("101", 13)],
)
def test_county_of(district, county):
    """Test county of."""
    assert county_of(district) == county


def test_nothing_in_force():
    """Test nothing in force."""
    assert parse_snippet(NOTHING, ALERT_PHENOMENA) == {
        "thunderstorm": "none",
        "wind_gust": "none",
        "cloudburst": "none",
        "freezing_rain": "none",
        "snowdrift": "none",
    }


def test_every_phenomenon_has_its_level():
    """Test every phenomenon has its level."""
    levels = parse_snippet(THUNDERSTORM_AND_WIND, WARNING_PHENOMENA)

    assert list(levels) == [
        "thunderstorm", "wind_gust", "cloudburst", "freezing_rain", "snowdrift",
        "rain", "snowfall", "fog", "extreme_cold", "high_mean_temperature",
    ]
    assert levels["thunderstorm"] == "yellow"
    assert levels["wind_gust"] == "orange"
    assert {levels[k] for k in list(levels)[2:]} == {"none"}


def test_unknown_icons_and_levels():
    """Test unknown icons and levels."""
    html = (
        "<table class='stbl'>"
        "<img src='/images/warningb/rain1.gif'>"  # a warning phenomenon in an alert
        "<img src='/images/warningb/fzra9.gif'>"  # above red
        "<img src='/images/warningb/fzra1.gif'>"  # the highest level wins
        "</table>"
    )
    levels = parse_snippet(html, ALERT_PHENOMENA)
    assert "rain" not in levels
    assert levels["freezing_rain"] == "red"


def test_a_page_without_the_area_table_is_an_error():
    """Test a page without the area table is an error."""
    with pytest.raises(ValueError, match="area table"):
        parse_snippet("<html>Hiba</html>", ALERT_PHENOMENA)


@pytest.mark.parametrize(
    ("levels", "highest"),
    [
        ({}, "none"),
        ({"a": "none", "b": "none"}, "none"),
        ({"a": "yellow", "b": "none"}, "yellow"),
        ({"a": "yellow", "b": "red", "c": "orange"}, "red"),
    ],
)
def test_highest_level(levels, highest):
    """Test highest level."""
    assert highest_level(levels) == highest


class FakeResponse:
    """An aiohttp response with fixed text."""
    def __init__(self, text):
        self._text = text

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    def raise_for_status(self):
        """Never raise; the response is a success."""
        return None

    async def text(self, encoding=None, errors=None):
        """Return the text."""
        return self._text


# The day tabs of met.hu's warning page, as on Tuesday until 00:00 UTC
TABS_FROM_TUESDAY = (
    "<dl><dd class='bg0'><a href='index.php?c=a'>Riasztás</a></dd>"
    "<dd class='bg0'><a href='index.php?c=b'>kedd</a></dd>"
    "<dd class='bg1' onmouseover=\"Tip('<div class=title>Figyelmeztetés holnapra</div>')\">"
    "<a href='index.php?c=c'>szerda</a></dd>"
    "<dd class='bg0'><a href='index.php?c=d'>csütörtök</a></dd>"
    "<dd class='bg0'><a href='index.php?c=e'>péntek</a></dd></dl>"
)
TUESDAY = date(2026, 11, 17)


@pytest.mark.parametrize(
    ("today", "warning_map"),
    [
        (TUESDAY, "wbhx"),
        (date(2026, 11, 18), "wchx"),  # after midnight, before met.hu moves the tabs
        (date(2026, 11, 20), "wehx"),
        (date(2026, 11, 21), "wbhx"),  # no tab for Saturday: the first tab
    ],
)
def test_today_warning_map(today, warning_map):
    """Test today warning map."""
    assert today_warning_map(TABS_FROM_TUESDAY, today) == warning_map


def test_today_warning_map_without_tabs():
    """Test today warning map without tabs."""
    assert today_warning_map("<html></html>", TUESDAY) == "wbhx"


def _warning_session():
    return SimpleNamespace(
        get=MagicMock(
            side_effect=[
                FakeResponse(NOTHING),
                FakeResponse(TABS_FROM_TUESDAY),
                FakeResponse(THUNDERSTORM_AND_WIND),
            ]
        )
    )


async def test_fetch_warnings():
    """Test fetch warnings."""
    session = _warning_session()

    data = await fetch_warnings(session, "903", date(2026, 11, 18))

    # The district's alerts, the warning page's day tabs, then the county's
    # warnings from Wednesday's tab
    assert [c.args[0] for c in session.get.call_args_list] == [
        "https://www.met.hu/idojaras/veszelyjelzes/hover.php",
        "https://www.met.hu/idojaras/veszelyjelzes/",
        "https://www.met.hu/idojaras/veszelyjelzes/hover.php",
    ]
    assert [c.kwargs["params"] for c in session.get.call_args_list] == [
        {"id": "wahx", "kod": "903"},
        None,
        {"id": "wchx", "kod": "9"},
    ]
    assert highest_level(data.alerts) == "none"
    assert data.warnings["wind_gust"] == "orange"


async def test_fetch_warnings_for_today_in_hungary():
    """Test fetch warnings for today in hungary."""
    session = _warning_session()
    # 23:30 UTC on Tuesday is already Wednesday in Hungary
    now = datetime(2026, 11, 18, 0, 30, tzinfo=HUNGARY_TZ)
    with patch.object(warning_scraper, "datetime") as clock:
        clock.now.return_value = now
        await fetch_warnings(session, "903")

    clock.now.assert_called_once_with(HUNGARY_TZ)
    assert session.get.call_args_list[2].kwargs["params"]["id"] == "wchx"


def _coordinator():
    coordinator = object.__new__(warning.WarningCoordinator)
    coordinator.settlement = DEBRECEN
    coordinator.district = None
    coordinator.hass = MagicMock()
    coordinator.hass.async_add_executor_job = AsyncMock(return_value="903")
    return coordinator


async def test_coordinator_finds_the_district_once():
    """Test coordinator finds the district once."""
    coordinator = _coordinator()
    data = Warnings()
    with (
        patch.object(warning, "async_get_clientsession") as session,
        patch.object(warning, "fetch_warnings", AsyncMock(return_value=data)) as fetch,
    ):
        assert await coordinator._async_update_data() is data
        assert await coordinator._async_update_data() is data

    coordinator.hass.async_add_executor_job.assert_awaited_once_with(
        find_district, 47.543, 21.648
    )
    fetch.assert_awaited_with(session.return_value, "903")


@pytest.mark.parametrize("error", [aiohttp.ClientError("x"), TimeoutError(), ValueError("x")])
async def test_coordinator_update_failure(error):
    """Test coordinator update failure."""
    coordinator = _coordinator()
    with (
        patch.object(warning, "async_get_clientsession"),
        patch.object(warning, "fetch_warnings", AsyncMock(side_effect=error)),
        pytest.raises(UpdateFailed, match="Debrecen"),
    ):
        await coordinator._async_update_data()


def test_coordinator_init():
    """Test coordinator init."""
    entry = MagicMock()
    with (
        patch.object(warning.ScheduledCoordinator, "__init__", return_value=None) as init,
        patch.object(warning, "RetryingSchedule") as cls,
    ):
        coordinator = warning.WarningCoordinator("hass", entry, DEBRECEN)

    assert init.call_args.kwargs["update_interval"] is None
    assert init.call_args.kwargs["config_entry"] is entry
    assert coordinator.settlement == DEBRECEN
    assert coordinator.district is None
    # Every full hour, retried after 10 minutes
    args, kwargs = cls.call_args
    assert args[1:] == (
        "warnings",
        coordinator._async_scheduled_download,
        warning.WARNING_RETRY_DELAY,
    )
    assert warning.WARNING_RETRY_DELAY.total_seconds() == 600
    assert kwargs == {"config_entry": entry, "minute": 0, "second": 59}
    assert coordinator.schedule is cls.return_value


def test_setup_schedules_and_registers_the_update():
    """Test setup schedules and registers the update."""
    entry = MagicMock(entry_id="e1")
    hass = MagicMock(data={})
    coordinator = MagicMock()
    with patch.object(warning, "WarningCoordinator", return_value=coordinator) as cls:
        assert warning.async_setup_warnings(hass, entry, DEBRECEN) is coordinator

    cls.assert_called_once_with(hass, entry, DEBRECEN)
    unloads = [c.args[0] for c in entry.async_on_unload.call_args_list]
    assert unloads[0] is coordinator.schedule.start.return_value
    registered = hass.data["hungaromet_weather_and_forecast_updaters"]
    assert registered == {"e1_warnings": coordinator.async_update_now}
    unloads[1]()
    assert registered == {}
    # The first download is left until the entities are added
    coordinator.schedule.async_run_now.assert_not_called()


def _sensors(data, language="en"):
    coordinator = MagicMock(data=data)
    coordinator.config_entry = SimpleNamespace(entry_id="e1", title="Home")
    sensors = warning_sensors(coordinator)
    for sensor in sensors:
        sensor.hass = SimpleNamespace(config=SimpleNamespace(language=language))
    return sensors


def test_sensors_show_the_highest_level_and_what_is_in_force():
    """Test sensors show the highest level and what is in force."""
    data = Warnings(
        alerts=parse_snippet(NOTHING, ALERT_PHENOMENA),
        warnings=parse_snippet(THUNDERSTORM_AND_WIND, WARNING_PHENOMENA),
    )
    alert, warning_today = _sensors(data)

    assert alert.native_value == "none"
    assert alert.extra_state_attributes == {"alerts": []}
    assert warning_today.native_value == "orange"
    assert warning_today.extra_state_attributes == {
        "warnings": [
            {"label": "Wind gust", "type": "wind_gust", "level": "orange"},
            {"label": "Thunderstorm", "type": "thunderstorm", "level": "yellow"},
        ]
    }
    assert alert.available and warning_today.available
    assert alert.unique_id == "e1_hungaromet_forecast_alerts"
    assert warning_today.unique_id == "e1_hungaromet_forecast_warnings"
    # On the Forecast device
    assert alert.device_info["identifiers"] == {
        ("hungaromet_weather_and_forecast", "e1_hungaromet_forecast")
    }


def test_sensors_name_the_phenomena_in_hungarian():
    """Test sensors name the phenomena in hungarian."""
    data = Warnings(alerts={"thunderstorm": "red"}, warnings={})
    alert, _ = _sensors(data, "hu")
    assert alert.extra_state_attributes == {
        "alerts": [{"label": "Zivatar", "type": "thunderstorm", "level": "red"}]
    }


@pytest.mark.parametrize(
    ("language", "label"),
    [
        ("hu", "Tartós, sűrű köd"),
        ("hu-HU", "Tartós, sűrű köd"),
        ("en-GB", "Persistent dense fog"),
        ("de", "Persistent dense fog"),
    ],
)
def test_in_force_language(language, label):
    """Test in force language."""
    assert in_force({"fog": "yellow"}, language)[0]["label"] == label


def test_in_force_puts_the_highest_level_first():
    """Test in force puts the highest level first."""
    levels = {"rain": "yellow", "thunderstorm": "yellow", "fog": "none", "snowfall": "red"}
    assert [w["type"] for w in in_force(levels, "en")] == ["snowfall", "rain", "thunderstorm"]


def test_every_phenomenon_has_a_name():
    """Test every phenomenon has a name."""
    assert set(PHENOMENON_NAMES) == set(WARNING_PHENOMENA.values())


def test_sensors_before_the_first_download():
    """Test sensors before the first download."""
    for sensor in _sensors(None):
        assert not sensor.available
        assert sensor.native_value is None
        assert sensor.extra_state_attributes is None


def test_sensor_descriptions():
    """Test sensor descriptions."""
    assert [(d.key, d.translation_key) for d in WARNING_SENSORS] == [
        ("alerts", "forecast_alert"),
        ("warnings", "forecast_warning"),
    ]
    assert all(d.options == ["none", "yellow", "orange", "red"] for d in WARNING_SENSORS)
