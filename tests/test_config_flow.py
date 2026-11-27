"""Tests for the setup form and the Configure form, run by Home Assistant itself."""

import asyncio
import zipfile
from unittest.mock import AsyncMock, patch

import aiohttp
import pytest
from homeassistant import bootstrap, config_entries, loader
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType, InvalidData
from homeassistant.helpers.selector import NumberSelector

from custom_components.hungaromet_weather_and_forecast import config_flow, settlement
from custom_components.hungaromet_weather_and_forecast.config_flow import HungarometConfigFlow
from custom_components.hungaromet_weather_and_forecast.const import (
    DOMAIN,
    RADAR_BASE_URL,
    URL_DAILY,
    URL_HOURLY,
    URL_TEN_MINUTES,
)
from custom_components.hungaromet_weather_and_forecast.forecast_scraper import Settlement

PECS = Settlement(name="Pécs", kod="9", lat=46.0, lon=18.2)
PECS_DICT = {"name": "Pécs", "kod": "9", "lat": 46.0, "lon": 18.2}
EGER = Settlement(name="Eger", kod="5", lat=47.9, lon=20.4)
INTEGRATION = "custom_components.hungaromet_weather_and_forecast"
ALL = ["daily", "hourly", "ten_minutes", "regional_radar"]


@pytest.fixture
async def hass(tmp_path):
    """A real Home Assistant, so the forms run in its own flow managers."""
    instance = HomeAssistant(str(tmp_path))
    instance.config.skip_pip = True
    loader.async_setup(instance)
    instance.config_entries = config_entries.ConfigEntries(instance, {})
    assert await bootstrap.async_load_base_functionality(instance)
    yield instance
    await instance.async_stop(force=True)


@pytest.fixture(autouse=True)
def setup_entry():
    """The new entries are not set up: these tests are about the forms."""
    with patch(f"{INTEGRATION}.async_setup_entry", AsyncMock(return_value=True)) as mock:
        yield mock


@pytest.fixture(autouse=True)
def lookup():
    """met.hu's settlement search, which knows Pécs."""
    with (
        patch.object(settlement, "async_get_clientsession"),
        patch.object(settlement, "lookup_settlement", AsyncMock(return_value=PECS)) as mock,
    ):
        yield mock


@pytest.fixture(autouse=True)
def download():
    """HungaroMet's measurements, which can be downloaded."""
    with patch.object(config_flow, "fetch_data", return_value=[]) as mock:
        yield mock


@pytest.fixture(autouse=True)
def radar():
    """HungaroMet's national radar, whose newest frame can be downloaded."""
    with patch.object(config_flow, "newest_frame_downloads", return_value=True) as mock:
        yield mock


def _form_defaults(form):
    """Return each form field's suggested value, or its default."""
    defaults = {}
    for key in form["data_schema"].schema:
        default = key.default() if callable(key.default) else None
        suggested = (key.description or {}).get("suggested_value")
        defaults[str(key)] = suggested if suggested is not None else default
    return defaults


def _input(
    settlement_name="Pécs",
    location="home",
    name="Pécs",
    distance_km=20.0,
    devices=None,
):
    return {
        "name": name,
        "distance_km": distance_km,
        "forecast_settlement": settlement_name,
        "location": location,
        "devices": ALL if devices is None else devices,
    }


def _shown(form):
    """Return what a form shows, without the radar checkbox of the setup form."""
    shown = _form_defaults(form)
    shown.pop("national_radar", None)
    return shown


def _locations(hass):
    """Return the location entries, in the order they were added."""
    return [
        entry
        for entry in hass.config_entries.async_entries(DOMAIN)
        if entry.unique_id is None
    ]


def _radar_entries(hass):
    return [
        entry
        for entry in hass.config_entries.async_entries(DOMAIN)
        if entry.unique_id == "national_radar"
    ]


async def _open(hass):
    return await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})


async def _submit(hass, form, user_input):
    return await hass.config_entries.flow.async_configure(form["flow_id"], user_input)


async def _add(hass, user_input):
    """Fill in a new setup form."""
    return await _submit(hass, await _open(hass), user_input)


async def _entry(hass, data=None, options=None, **user_input):
    """Return an added entry, optionally with other stored values."""
    entry = (await _add(hass, _input(**user_input)))["result"]
    if data is not None:
        hass.config_entries.async_update_entry(entry, data=data, options=options or {})
    return entry


async def _add_radar(hass):
    return await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "import"}
    )


# --- the setup form -------------------------------------------------------------


async def test_setup_form_starts_empty(hass, lookup, download):
    """The first form has the defaults, and nothing is checked yet."""
    form = await _open(hass)

    assert form["type"] is FlowResultType.FORM
    assert form["step_id"] == "user"
    assert form["errors"] == {}
    assert _form_defaults(form) == {
        "name": None,
        "distance_km": 20,
        "forecast_settlement": "",
        "location": "home",
        # Everything is ticked
        "devices": ALL,
        "national_radar": True,
    }
    lookup.assert_not_awaited()
    download.assert_not_called()


async def test_setup_form_creates_the_entry(hass, download, setup_entry):
    """The name goes in the entry's data, the settings in its options."""
    result = await _add(hass, _input("pécs", "settlement", " Pécs ", 30.0))
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Pécs"
    assert result["data"] == {"name": "Pécs"}
    assert result["options"] == {
        "distance_km": 30.0,
        "forecast": PECS_DICT,
        "location": "settlement",
        "devices": ALL,
    }
    # Location entries are told apart by their place, not by a unique ID
    assert result["result"].unique_id is None
    assert (result["result"].version, result["result"].minor_version) == (2, 2)
    download.assert_called_once_with(URL_TEN_MINUTES)
    # The location and, as its box was left ticked, the radar entry
    assert setup_entry.await_count == 2
    assert len(_radar_entries(hass)) == 1


async def test_setup_form_without_forecast_uses_the_defaults(hass, lookup):
    """Only the name and a device are needed."""
    result = await _add(hass, {"name": "Otthon", "devices": ["hourly"]})

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["options"] == {
        "distance_km": 20,
        "forecast": None,
        "location": "home",
        "devices": ["hourly"],
    }
    assert result["result"].unique_id is None
    lookup.assert_not_awaited()


@pytest.mark.parametrize(
    ("first_input", "answer", "errors"),
    [
        (_input(name="  "), PECS, {"name": "name_required"}),
        (_input("Pécz"), None, {"forecast_settlement": "settlement_not_found"}),
        (
            _input("Pécz", name=" "),
            None,
            {"forecast_settlement": "settlement_not_found", "name": "name_required"},
        ),
        (_input(), TimeoutError(), {"forecast_settlement": "cannot_connect"}),
        (
            _input(),
            aiohttp.ClientError("offline"),
            {"forecast_settlement": "cannot_connect"},
        ),
        (
            _input(),
            ValueError("not JSON"),
            {"forecast_settlement": "invalid_response"},
        ),
        (_input("", "settlement"), PECS, {"location": "settlement_required"}),
    ],
)
async def test_setup_form_recovers_from_an_error(
    hass, lookup, download, first_input, answer, errors
):
    """After an error the input is kept, and correct input creates the entry."""
    lookup.side_effect = [answer, PECS]

    form = await _add(hass, first_input)

    assert form["type"] is FlowResultType.FORM
    assert form["errors"] == errors
    assert _shown(form) == first_input
    # Nothing else is checked while something is wrong
    download.assert_not_called()

    lookup.side_effect = None
    result = await _submit(hass, form, _input())

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["options"]["forecast"] == PECS_DICT


@pytest.mark.parametrize(
    "error", [OSError("offline"), ValueError("empty"), zipfile.BadZipFile("broken")]
)
async def test_setup_form_checks_the_measurements_can_be_read(
    hass, download, caplog, error
):
    """The entry is created only once HungaroMet's measurements can be read."""
    download.side_effect = error
    user_input = _input("")

    form = await _add(hass, user_input)

    assert form["errors"] == {"base": "cannot_connect"}
    assert _shown(form) == user_input
    assert "Reading HungaroMet's measurements failed" in caplog.text

    download.side_effect = None
    result = await _submit(hass, form, user_input)

    assert result["type"] is FlowResultType.CREATE_ENTRY


@pytest.mark.parametrize("sent", [{"devices": []}, {}])
async def test_setup_form_needs_a_device_or_a_forecast(hass, download, sent):
    """With no box ticked, there must be a forecast."""
    user_input = {**_input(""), **sent}
    if not sent:
        del user_input["devices"]

    form = await _add(hass, user_input)

    assert form["errors"] == {"devices": "nothing_or_radar"}
    download.assert_not_called()

    result = await _submit(hass, form, _input(devices=[]))

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["options"]["devices"] == []
    # Looking the settlement up has reached met.hu already
    download.assert_not_called()


@pytest.mark.parametrize(
    ("devices", "url"),
    [
        (ALL, URL_TEN_MINUTES),
        (["daily", "hourly"], URL_HOURLY),
        (["daily", "regional_radar"], URL_DAILY),
    ],
)
async def test_setup_form_checks_a_chosen_feed(hass, download, devices, url):
    """The smallest of the chosen feeds is downloaded."""
    result = await _add(hass, _input("", devices=devices))

    assert result["type"] is FlowResultType.CREATE_ENTRY
    download.assert_called_once_with(url)


@pytest.mark.parametrize(
    "answer", [OSError("offline"), ValueError("bad page"), []]
)
async def test_setup_form_checks_a_regional_radar_alone(
    hass, download, caplog, answer
):
    """Without station devices the radar image of the location is checked."""
    user_input = _input("", devices=["regional_radar"])
    behaviour = (
        {"return_value": answer}
        if isinstance(answer, list)
        else {"side_effect": answer}
    )
    hass.config.latitude, hass.config.longitude = 47.53, 21.64
    with patch.object(config_flow, "latest_frame_urls", **behaviour) as find:
        form = await _add(hass, user_input)

    assert form["errors"] == {"base": "cannot_connect"}
    assert "Reading HungaroMet's radar images failed" in caplog.text
    find.assert_called_once_with("ne")

    with patch.object(config_flow, "latest_frame_urls", return_value=["url"]):
        result = await _submit(hass, form, user_input)

    assert result["type"] is FlowResultType.CREATE_ENTRY
    download.assert_not_called()


async def test_an_entry_without_devices_leaves_home_free(hass, lookup):
    """A forecast alone measures nowhere, so another entry can measure at Home."""
    await _add(hass, _input(devices=[]))
    lookup.return_value = EGER

    result = await _add(hass, _input("Eger", name="Eger"))

    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_configure_form_refuses_devices_where_home_is_taken(hass, lookup):
    """Ticking a device makes an entry measure at its location."""
    await _entry(hass)
    lookup.return_value = EGER
    entry = await _entry(hass, settlement_name="Eger", devices=[])

    form = await _save(hass, await _configure(hass, entry), _input("Eger"))

    assert form["errors"] == {"location": "home_in_use"}

    result = await _save(
        hass, form, _input("Eger", "settlement", devices=["daily"])
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options["devices"] == ["daily"]


async def test_configure_form_changes_the_ticked_boxes(hass):
    """The saved boxes are shown and can be changed; there is no radar box."""
    entry = await _entry(hass)

    result = await _save(
        hass, await _configure(hass, entry), _input(devices=["hourly"])
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options["devices"] == ["hourly"]
    shown = _form_defaults(await _configure(hass, entry))
    assert list(shown["devices"]) == ["hourly"]
    assert "national_radar" not in shown


# --- the national radar checkbox ------------------------------------------------


async def test_two_forms_add_one_radar_entry(hass, lookup):
    """A form opened before the radar entry was added can still have the box."""
    first, second = await _open(hass), await _open(hass)
    await _submit(hass, first, _input())
    await hass.async_block_till_done()
    lookup.return_value = EGER

    result = await _submit(hass, second, _input("Eger", "settlement", "Eger"))
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert len(_radar_entries(hass)) == 1


@pytest.mark.parametrize(
    ("ticked", "radars"),
    [({}, 1), ({"national_radar": True}, 1), ({"national_radar": False}, 0)],
)
async def test_setup_form_adds_the_radar_entry_if_ticked(hass, ticked, radars):
    """The box is ticked unless it is unticked."""
    result = await _add(hass, {**_input(), **ticked})
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert "national_radar" not in result["options"]
    assert len(_radar_entries(hass)) == radars
    # The box is offered again only while there is no radar entry
    assert ("national_radar" in _form_defaults(await _open(hass))) is not radars


async def test_a_deleted_radar_entry_is_offered_again(hass):
    """It does not come back by itself; the next setup form has the box."""
    await _add_radar(hass)
    assert "national_radar" not in _form_defaults(await _open(hass))

    await hass.config_entries.async_remove(_radar_entries(hass)[0].entry_id)

    assert _form_defaults(await _open(hass))["national_radar"] is True
    assert _radar_entries(hass) == []


@pytest.mark.parametrize("name", ["", "  "])
async def test_setup_form_adds_only_the_radar(
    hass, download, lookup, radar, name
):
    """Without a name, a settlement and devices there is only the radar to add."""
    result = await _add(hass, {"name": name, "devices": [], "national_radar": True})

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Radar"
    assert result["data"] == {"entry_type": "radar"}
    assert _locations(hass) == []
    radar.assert_called_once_with(RADAR_BASE_URL)
    download.assert_not_called()
    lookup.assert_not_awaited()


@pytest.mark.parametrize(
    "answer", [OSError("offline"), ValueError("bad page"), False]
)
async def test_setup_form_checks_the_radar_added_alone(
    hass, caplog, radar, answer
):
    """The radar entry is added only once its newest frame can be downloaded."""
    user_input = {**_input("", name="", devices=[]), "national_radar": True}
    if answer is False:
        radar.return_value = False
    else:
        radar.side_effect = answer

    form = await _add(hass, user_input)
    await hass.async_block_till_done()

    assert form["type"] is FlowResultType.FORM
    assert form["errors"] == {"base": "cannot_connect"}
    assert _form_defaults(form) == user_input
    assert "Reading HungaroMet's radar images failed" in caplog.text
    assert hass.config_entries.async_entries(DOMAIN) == []

    radar.side_effect = None
    radar.return_value = True
    result = await _submit(hass, form, user_input)

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert len(_radar_entries(hass)) == 1


@pytest.mark.parametrize(
    ("user_input", "errors"),
    [
        # A device is ticked, so the name was forgotten
        (_input("", name=""), {"name": "name_required"}),
        (_input("Pécs", name="", devices=[]), {"name": "name_required"}),
        (_input("", devices=[]), {"devices": "nothing_or_radar"}),
        (
            {**_input("", name="", devices=[]), "national_radar": False},
            {"devices": "nothing_or_radar", "name": "name_required"},
        ),
    ],
)
async def test_setup_form_with_an_error_adds_no_radar(hass, user_input, errors):
    """The radar is added only with an accepted form."""
    form = await _add(hass, user_input)
    await hass.async_block_till_done()

    assert form["errors"] == errors
    assert hass.config_entries.async_entries(DOMAIN) == []


async def test_nothing_selected_message_with_a_radar_entry(hass):
    """With the radar added, only a device or a forecast is asked for."""
    await _add_radar(hass)

    form = await _add(hass, _input("", devices=[]))

    assert form["errors"] == {"devices": "nothing_selected"}


async def test_setup_form_aborts_a_second_entry_of_a_place(hass, download):
    """The same forecast settlement and sensor location cannot be added twice."""
    await _entry(hass, name="Otthon")
    download.reset_mock()

    result = await _add(hass, _input(name="Másik"))

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    download.assert_not_called()


async def test_setup_form_refuses_a_settlement_stored_differently(hass, lookup):
    """With other coordinates saved the settings differ, but share both parts."""
    saved = {**PECS_DICT, "lat": 46.1}
    await _entry(hass, {"name": "Régi"}, {"forecast": saved, "location": "home"})

    form = await _add(hass, _input())

    assert form["errors"] == {
        "forecast_settlement": "settlement_in_use",
        "location": "home_in_use",
    }

    lookup.return_value = EGER
    result = await _submit(hass, form, _input("Eger", "settlement", "Eger"))

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["result"].unique_id is None


@pytest.mark.parametrize(
    ("first", "second", "answer", "errors"),
    [
        # The same forecast, measured elsewhere
        (
            _input(),
            _input(location="settlement"),
            PECS,
            {"forecast_settlement": "settlement_in_use"},
        ),
        (
            _input(location="settlement"),
            _input(),
            PECS,
            {"forecast_settlement": "settlement_in_use"},
        ),
        # Another forecast, both measured at Home
        (_input(), _input("Eger"), EGER, {"location": "home_in_use"}),
        # Without a forecast the measurements are taken at Home
        (_input(), _input(""), None, {"location": "home_in_use"}),
        (_input(""), _input("Eger"), EGER, {"location": "home_in_use"}),
    ],
)
async def test_setup_form_refuses_what_another_entry_has(
    hass, lookup, download, first, second, answer, errors
):
    """Entries share no forecast settlement, and one measures at Home."""
    lookup.side_effect = [PECS, answer] if first["forecast_settlement"] else [answer]
    await _add(hass, first)
    download.reset_mock()

    form = await _add(hass, second)

    assert form["type"] is FlowResultType.FORM
    assert form["errors"] == errors
    assert _shown(form) == second
    download.assert_not_called()

    lookup.side_effect = None
    lookup.return_value = EGER
    result = await _submit(hass, form, _input("Eger", "settlement", "Eger"))

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["result"].unique_id is None
    assert len(_locations(hass)) == 2


async def test_setup_form_sees_an_entry_not_migrated_yet(hass):
    """An older entry, such as a disabled one, still has its settings in its data."""
    await _entry(hass, {"name": "Régi", "forecast": PECS_DICT, "location": "home"})

    result = await _add(hass, _input())

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_setup_form_aborts_a_second_entry_without_forecast(hass):
    """Only one entry without a forecast can be added; the radar entry is none."""
    await _add_radar(hass)

    result = await _add(hass, _input(""))
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["result"].unique_id is None

    result = await _add(hass, _input("", distance_km=50.0))
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


@pytest.mark.parametrize("settlement_name", ["Pécs", ""])
async def test_setup_forms_sent_together_add_a_place_once(hass, settlement_name):
    """The place is checked again after the download, which takes a while."""
    downloading, finish = [], asyncio.Event()

    async def slow_download(*_args):
        downloading.append(True)
        await finish.wait()

    with patch.object(config_flow, "_async_source_error", slow_download):
        sent = [
            asyncio.create_task(_add(hass, _input(settlement_name, name=name)))
            for name in ("A", "B")
        ]
        # Both have passed the first check before either entry is added
        while len(downloading) < 2:
            await asyncio.sleep(0)
        finish.set()
        results = await asyncio.gather(*sent)

    created, aborted = sorted(results, key=lambda r: r["type"] is FlowResultType.ABORT)
    assert created["type"] is FlowResultType.CREATE_ENTRY
    assert created["result"].unique_id is None
    assert aborted["type"] is FlowResultType.ABORT
    assert aborted["reason"] == "already_configured"
    assert len(_locations(hass)) == 1


async def test_setup_forms_sent_together_share_nothing(hass, lookup):
    """What two entries would share is checked again after the download too."""
    downloading, finish = [], asyncio.Event()

    async def slow_download(*_args):
        downloading.append(True)
        await finish.wait()

    with patch.object(config_flow, "_async_source_error", slow_download):
        sent = []
        for count, answer in enumerate((PECS, EGER), 1):
            lookup.return_value = answer
            sent.append(
                asyncio.create_task(_add(hass, _input(answer.name, name=answer.name)))
            )
            while len(downloading) < count:
                await asyncio.sleep(0)
        finish.set()
        results = await asyncio.gather(*sent)

    created, refused = sorted(results, key=lambda r: r["type"] is FlowResultType.FORM)
    assert created["type"] is FlowResultType.CREATE_ENTRY
    assert created["result"].unique_id is None
    assert refused["errors"] == {"location": "home_in_use"}
    assert len(_locations(hass)) == 1

    # The refused form, for Eger, then measures there
    result = await _submit(hass, refused, _input("Eger", "settlement", "Eger"))

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["result"].unique_id is None
    assert len(_locations(hass)) == 2


async def test_radar_entry_is_created_once_without_settings(hass):
    """The import step adds the national radar entry, which has no settings."""
    result = await _add_radar(hass)

    assert result["type"] is FlowResultType.CREATE_ENTRY
    entry = result["result"]
    assert entry.unique_id == "national_radar"
    assert (dict(entry.data), dict(entry.options)) == ({"entry_type": "radar"}, {})
    assert entry.supports_options is False

    result = await _add_radar(hass)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


def test_flows_from_the_same_source_are_duplicates():
    """Test flows from the same source are duplicates."""
    user, other_user, imported = (HungarometConfigFlow() for _ in range(3))
    for flow, source in ((user, "user"), (other_user, "user"), (imported, "import")):
        flow.context = {"source": source}
    assert user.is_matching(other_user)
    assert not user.is_matching(imported)


# --- the search radius ----------------------------------------------------------


@pytest.fixture(params=["setup", "configure"])
async def radius_form(request, hass):
    """Either form with the radius, and the function that submits it."""
    if request.param == "setup":
        return await _open(hass), _submit
    entry = await _entry(hass)
    return await _configure(hass, entry), _save


def _radius_field(form):
    return next(
        value for key, value in form["data_schema"].schema.items() if key == "distance_km"
    )


async def test_radius_is_a_number_selector_in_km(radius_form):
    """The forms ask for the radius with Home Assistant's number field."""
    selector = _radius_field(radius_form[0])

    assert isinstance(selector, NumberSelector)
    assert selector.config == {
        "min": 1,
        "max": 100,
        "step": "any",
        "unit_of_measurement": "km",
        "mode": "box",
    }


@pytest.mark.parametrize("distance_km", [0.5, 101])
async def test_radius_outside_its_range_is_refused(hass, radius_form, distance_km):
    """Home Assistant refuses it; the form can then be sent again."""
    form, submit = radius_form

    with pytest.raises(InvalidData):
        await submit(hass, form, _input("", name="Otthon", distance_km=distance_km))
    result = await submit(hass, form, _input("", name="Otthon", distance_km=12.5))

    assert result["type"] is FlowResultType.CREATE_ENTRY
    entry = _locations(hass)[-1]
    assert entry.options["distance_km"] == 12.5
    assert entry.unique_id is None


# --- the Configure form ---------------------------------------------------------


async def _configure(hass, entry):
    return await hass.config_entries.options.async_init(entry.entry_id)


async def _save(hass, form, user_input):
    return await hass.config_entries.options.async_configure(form["flow_id"], user_input)


@pytest.mark.parametrize(
    ("data", "options", "expected"),
    [
        # The saved settings
        (
            {},
            {"distance_km": 30.0, "forecast": PECS_DICT, "location": "settlement"},
            {"distance_km": 30.0, "forecast_settlement": "Pécs", "location": "settlement"},
        ),
        # An entry not migrated yet still has them in its data
        (
            {"distance_km": 30.0, "forecast": PECS_DICT, "location": "settlement"},
            {},
            {"distance_km": 30.0, "forecast_settlement": "Pécs", "location": "settlement"},
        ),
        # Saved options win over the data
        (
            {"distance_km": 30.0, "forecast": PECS_DICT, "location": "settlement"},
            {"distance_km": 50.0, "forecast": None, "location": "home"},
            {"distance_km": 50.0, "forecast_settlement": "", "location": "home"},
        ),
        # An entry without settings gets the defaults
        ({}, {}, {"distance_km": 20, "forecast_settlement": "", "location": "home"}),
    ],
)
async def test_configure_form_shows_the_current_settings(
    hass, data, options, expected
):
    """The form starts with the entry's name and settings."""
    entry = await _entry(hass, data, options)

    form = await _configure(hass, entry)

    assert form["type"] is FlowResultType.FORM
    assert form["step_id"] == "init"
    assert _form_defaults(form) == {
        "name": "Pécs",
        **expected,
        # Older settings have everything ticked
        "devices": ALL,
    }


async def test_configure_form_saves_the_settings(hass):
    """The settings are saved in the options; the same name stays."""
    entry = await _entry(hass, settlement_name="")

    result = await _save(
        hass, await _configure(hass, entry), _input("Pécs", "settlement", distance_km=40.0)
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert dict(entry.options) == {
        "distance_km": 40.0,
        "forecast": PECS_DICT,
        "location": "settlement",
        "devices": ALL,
    }
    assert (entry.title, dict(entry.data)) == ("Pécs", {"name": "Pécs"})
    assert entry.unique_id is None


async def test_configure_form_renames_the_entry(hass):
    """A new name becomes the entry's title."""
    entry = await _entry(hass)

    result = await _save(hass, await _configure(hass, entry), _input(name=" Mecsek "))

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert (entry.title, dict(entry.data)) == ("Mecsek", {"name": "Mecsek"})
    assert entry.unique_id is None


@pytest.mark.parametrize(
    ("first_input", "answer", "errors"),
    [
        (_input(name=" "), PECS, {"name": "name_required"}),
        (_input("Pécz"), None, {"forecast_settlement": "settlement_not_found"}),
        (
            _input(),
            aiohttp.ClientError("offline"),
            {"forecast_settlement": "cannot_connect"},
        ),
        (
            _input(),
            ValueError("not JSON"),
            {"forecast_settlement": "invalid_response"},
        ),
        (_input("", "settlement"), PECS, {"location": "settlement_required"}),
    ],
)
async def test_configure_form_recovers_from_an_error(
    hass, lookup, first_input, answer, errors
):
    """After an error the input is kept, and correct input saves the settings."""
    entry = await _entry(hass)
    lookup.side_effect = [answer]

    form = await _save(hass, await _configure(hass, entry), first_input)

    assert form["type"] is FlowResultType.FORM
    assert form["errors"] == errors
    assert _shown(form) == first_input
    assert entry.title == "Pécs"

    lookup.side_effect = None
    result = await _save(hass, form, _input(distance_km=40.0))

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options["distance_km"] == 40.0


@pytest.mark.parametrize(
    ("changed", "errors"),
    [
        (
            _input(),
            {"forecast_settlement": "settlement_in_use", "location": "home_in_use"},
        ),
        (_input(location="settlement"), {"forecast_settlement": "settlement_in_use"}),
        (_input("Eger"), {"location": "home_in_use"}),
        (_input(""), {"location": "home_in_use"}),
    ],
)
async def test_configure_form_refuses_what_another_entry_has(
    hass, lookup, changed, errors
):
    """An entry cannot change to share a forecast or Home; free ones are accepted."""
    await _entry(hass)
    lookup.return_value = EGER
    entry = await _entry(hass, settlement_name="Eger", location="settlement")
    lookup.return_value = {"Pécs": PECS, "Eger": EGER, "": None}[
        changed["forecast_settlement"]
    ]

    form = await _save(hass, await _configure(hass, entry), changed)

    assert form["errors"] == errors
    assert _shown(form) == changed

    lookup.return_value = EGER
    result = await _save(
        hass, form, _input("Eger", "settlement", distance_km=40.0)
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY


@pytest.mark.parametrize("shared", ["both", "forecast", "home"])
async def test_configure_form_saves_an_entry_that_already_shares_with_another(
    hass, lookup, shared
):
    """Entries added by earlier versions can still change their other settings."""
    first = await _entry(hass)
    lookup.return_value = EGER
    second = await _entry(hass, settlement_name="Eger", location="settlement")
    settings = {
        "both": (PECS, "home"),
        "forecast": (PECS, "settlement"),
        "home": (EGER, "home"),
    }[shared]
    lookup.return_value = settings[0]
    hass.config_entries.async_update_entry(
        second,
        options={
            **first.options,
            "forecast": {**PECS_DICT, "name": settings[0].name, "kod": settings[0].kod},
            "location": settings[1],
        },
    )

    result = await _save(
        hass,
        await _configure(hass, second),
        _input(settings[0].name, settings[1], distance_km=50.0),
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert second.options["distance_km"] == 50.0
    assert second.unique_id is None
