"""Tests for the automatically created national radar entry."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.hungaromet_weather_and_forecast.config_flow import (
    HungarometConfigFlow,
)
from custom_components.hungaromet_weather_and_forecast.scope import is_radar_entry


def test_is_radar_entry():
    assert is_radar_entry(SimpleNamespace(data={"entry_type": "radar"}))
    assert not is_radar_entry(SimpleNamespace(data={}))
    assert not is_radar_entry(SimpleNamespace(data={"distance_km": 20}))


@pytest.mark.asyncio
async def test_import_step_creates_the_radar_entry_once():
    flow = HungarometConfigFlow()
    flow.async_set_unique_id = AsyncMock()
    flow._abort_if_unique_id_configured = MagicMock()
    flow.async_create_entry = MagicMock(return_value="created")

    assert await flow.async_step_import({"entry_type": "radar"}) == "created"

    flow.async_set_unique_id.assert_awaited_once_with("national_radar")
    flow._abort_if_unique_id_configured.assert_called_once()
    flow.async_create_entry.assert_called_once_with(
        title="Radar", data={"entry_type": "radar"}
    )


def test_only_location_entries_have_options():
    radar = SimpleNamespace(data={"entry_type": "radar"})
    location = SimpleNamespace(data={"distance_km": 20})
    assert HungarometConfigFlow.async_supports_options_flow(radar) is False
    assert HungarometConfigFlow.async_supports_options_flow(location) is True


def test_radar_device_names():
    import json
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / (
        "custom_components/hungaromet_weather_and_forecast/translations"
    )
    en = json.loads((root / "en.json").read_text("utf-8"))
    hu = json.loads((root / "hu.json").read_text("utf-8"))
    assert en["device"]["radar"]["name"] == "National Radar Image"
    assert hu["device"]["radar"]["name"] == "Országos Radarkép"
