"""Tests for the setup form and the Configure form."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.hungaromet_weather_and_forecast import config_flow, options_flow
from custom_components.hungaromet_weather_and_forecast.config_flow import HungarometConfigFlow
from custom_components.hungaromet_weather_and_forecast.forecast_scraper import Settlement
from custom_components.hungaromet_weather_and_forecast.options_flow import (
    HungaroMetOptionsFlowHandler,
)

PECS = Settlement(name="Pécs", kod="9", lat=46.0, lon=18.2)
PECS_DICT = {"name": "Pécs", "kod": "9", "lat": 46.0, "lon": 18.2}


def _form_defaults(form):
    """Return each form field's default, or its suggested value."""
    defaults = {}
    for key in form["data_schema"].schema:
        default = key.default() if callable(key.default) else None
        suggested = (key.description or {}).get("suggested_value")
        defaults[str(key)] = default if default is not None else suggested
    return defaults


def _flow(handler, validated, module=config_flow):
    """Set up a form handler; returns the patch that answers its validation."""
    handler.hass = MagicMock()
    handler.async_show_form = lambda **form: {"type": "form", **form}
    handler.async_create_entry = lambda **entry: {"type": "create_entry", **entry}
    return patch.object(
        module, "async_validate_forecast", AsyncMock(return_value=validated)
    )


@pytest.mark.asyncio
async def test_setup_form_starts_empty():
    flow = HungarometConfigFlow()
    with _flow(flow, (None, {})) as validate:
        form = await flow.async_step_user()

    validate.assert_not_awaited()
    assert form["step_id"] == "user"
    assert form["errors"] == {}
    assert _form_defaults(form) == {
        "name": None,
        "distance_km": 20,
        "forecast_settlement": "",
        "location": "home",
    }


@pytest.mark.asyncio
async def test_setup_form_creates_the_entry():
    flow = HungarometConfigFlow()
    user_input = {
        "name": "Pécs",
        "distance_km": 30.0,
        "forecast_settlement": "pécs",
        "location": "settlement",
    }
    with _flow(flow, (PECS, {})) as validate:
        result = await flow.async_step_user(user_input)

    validate.assert_awaited_once_with(flow.hass, user_input)
    assert result == {
        "type": "create_entry",
        "title": "Pécs",
        "data": {
            "name": "Pécs",
            "distance_km": 30.0,
            "forecast": PECS_DICT,
            "location": "settlement",
        },
    }


@pytest.mark.asyncio
async def test_setup_form_without_forecast_uses_the_defaults():
    flow = HungarometConfigFlow()
    with _flow(flow, (None, {})):
        result = await flow.async_step_user({"name": "Otthon"})

    assert result["data"] == {
        "name": "Otthon",
        "distance_km": 20,
        "forecast": None,
        "location": "home",
    }


@pytest.mark.asyncio
async def test_setup_form_shows_errors_and_keeps_the_input():
    flow = HungarometConfigFlow()
    user_input = {
        "name": "Pécs",
        "distance_km": 30.0,
        "forecast_settlement": "Pécz",
        "location": "home",
    }
    errors = {"forecast_settlement": "settlement_not_found"}
    with _flow(flow, (None, errors)):
        form = await flow.async_step_user(user_input)

    assert form["type"] == "form"
    assert form["errors"] == errors
    assert _form_defaults(form) == user_input


def test_location_entries_get_the_configure_form():
    entry = SimpleNamespace(data={"distance_km": 20})
    assert isinstance(
        HungarometConfigFlow.async_get_options_flow(entry), HungaroMetOptionsFlowHandler
    )


def test_flows_from_the_same_source_are_duplicates():
    user, other_user, imported = (HungarometConfigFlow() for _ in range(3))
    for flow, source in ((user, "user"), (other_user, "user"), (imported, "import")):
        flow.context = {"source": source}
    assert user.is_matching(other_user)
    assert not user.is_matching(imported)


def _configure(entry, validated):
    handler = HungaroMetOptionsFlowHandler()
    return handler, _flow(handler, validated, options_flow), patch.object(
        HungaroMetOptionsFlowHandler, "config_entry", entry
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("data", "options", "expected"),
    [
        # A new entry shows its setup values
        (
            {"distance_km": 30.0, "forecast": PECS_DICT, "location": "settlement"},
            {},
            {"distance_km": 30.0, "forecast_settlement": "Pécs", "location": "settlement"},
        ),
        # Saved options win over the setup values
        (
            {"distance_km": 30.0, "forecast": PECS_DICT, "location": "settlement"},
            {"distance_km": 50.0, "forecast": None, "location": "home"},
            {"distance_km": 50.0, "forecast_settlement": "", "location": "home"},
        ),
        # An entry without settings gets the defaults
        ({}, {}, {"distance_km": 20, "forecast_settlement": "", "location": "home"}),
    ],
)
async def test_configure_form_shows_the_current_settings(data, options, expected):
    entry = SimpleNamespace(data=data, options=options)
    handler, flow, current = _configure(entry, (None, {}))
    with flow as validate, current:
        form = await handler.async_step_init()

    validate.assert_not_awaited()
    assert form["step_id"] == "init"
    assert _form_defaults(form) == expected


@pytest.mark.asyncio
async def test_configure_form_saves_the_settings():
    entry = SimpleNamespace(data={"distance_km": 20}, options={})
    user_input = {"distance_km": 40.0, "forecast_settlement": "Pécs", "location": "settlement"}
    handler, flow, current = _configure(entry, (PECS, {}))
    with flow, current:
        result = await handler.async_step_init(user_input)

    assert result == {
        "type": "create_entry",
        "title": "",
        "data": {"distance_km": 40.0, "forecast": PECS_DICT, "location": "settlement"},
    }


@pytest.mark.asyncio
async def test_configure_form_shows_errors_and_keeps_the_input():
    entry = SimpleNamespace(data={"distance_km": 20}, options={})
    user_input = {"distance_km": 40.0, "forecast_settlement": "", "location": "settlement"}
    errors = {"location": "settlement_required"}
    handler, flow, current = _configure(entry, (None, errors))
    with flow, current:
        form = await handler.async_step_init(user_input)

    assert form["errors"] == errors
    assert _form_defaults(form) == user_input
