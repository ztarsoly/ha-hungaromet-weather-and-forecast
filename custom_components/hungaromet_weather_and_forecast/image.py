"""
Home Assistant image platform for HungaroMet radar GIF.
"""

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .forecast import sensor_location
from .national_radar import HungarometRadarImage
from .regional_radar import HungarometRegionalRadarImage, quadrant_for
from .scope import EntryScope, is_radar_entry

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
):
    """
    Set up the radar image of a config entry.

    The national radar entry has the national radar image; each location entry
    has the regional radar image for its location.
    """
    if is_radar_entry(entry):
        _setup_image_entities(hass, async_add_entities)
        return
    lat, lon = sensor_location(entry) or (hass.config.latitude, hass.config.longitude)
    async_add_entities(
        [
            HungarometRegionalRadarImage(
                hass, EntryScope.from_entry(entry), quadrant_for(lat, lon)
            )
        ],
        True,
    )


def _setup_image_entities(hass, async_add_entities):
    entity = HungarometRadarImage(hass)
    async_add_entities([entity], True)
