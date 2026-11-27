"""Tests for image.py platform setup"""

# The tests check the integration's internals
# pylint: disable=protected-access

from types import SimpleNamespace
from unittest.mock import MagicMock

from custom_components.hungaromet_weather_and_forecast.data import HungaroMetData
from custom_components.hungaromet_weather_and_forecast.image import (
    async_setup_entry,
    _setup_image_entities,
    HungarometRadarImage,
    HungarometRegionalRadarImage,
)


def _entry(entry_id, data=None):
    return SimpleNamespace(
        entry_id=entry_id,
        title="HungaroMet",
        data=data or {},
        options={},
        runtime_data=HungaroMetData(name="HungaroMet"),
    )


def _hass(entries):
    hass = MagicMock()
    hass.config.latitude, hass.config.longitude = 47.53, 21.64  # Debrecen
    hass.config_entries.async_entries.return_value = entries
    return hass


def _added(async_add_entities):
    return [e for call in async_add_entities.call_args_list for e in call.args[0]]


async def test_location_entry_gets_its_regional_radar():
    """A location entry gets only the regional radar for its location."""
    entry = _entry("first")
    hass = _hass([entry])
    async_add_entities = MagicMock()

    await async_setup_entry(hass, entry, async_add_entities)

    (regional,) = _added(async_add_entities)
    assert isinstance(regional, HungarometRegionalRadarImage)
    assert regional._quadrant == "ne"
    assert async_add_entities.call_args.args[1] is True


async def test_radar_entry_gets_the_national_radar():
    """The radar entry gets only the national radar image."""
    entry = _entry("radar", {"entry_type": "radar"})
    hass = _hass([entry])
    async_add_entities = MagicMock()

    await async_setup_entry(hass, entry, async_add_entities)

    (national,) = _added(async_add_entities)
    # Exactly the national radar, not its regional subclass
    assert type(national) is HungarometRadarImage  # pylint: disable=unidiomatic-typecheck


async def test_regional_radar_follows_the_settlement_location():
    """With the settlement as location, its quadrant is used instead of home's."""
    pecs = {"name": "Pécs", "kod": "9", "lat": 46.07, "lon": 18.23}
    entry = _entry("first", {"forecast": pecs, "location": "settlement"})
    hass = _hass([entry])
    async_add_entities = MagicMock()

    await async_setup_entry(hass, entry, async_add_entities)

    assert _added(async_add_entities)[0]._quadrant == "sw"


def test_setup_image_entities():
    """Test _setup_image_entities helper."""
    hass = MagicMock()
    async_add_entities = MagicMock()

    _setup_image_entities(hass, _entry("radar", {"entry_type": "radar"}), async_add_entities)

    async_add_entities.assert_called_once()
    args = async_add_entities.call_args[0]
    entities = args[0]
    assert len(entities) == 1
    assert entities[0].name is None  # its device's name
    # The unique ID stays the same as before the rename
    assert entities[0].unique_id == "hungaromet_radar_gif_hungaromet_radar"
