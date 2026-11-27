"""Stand-ins for config entries and what is set up with them."""

from types import SimpleNamespace
from unittest.mock import MagicMock

from custom_components.hungaromet_weather_and_forecast.coordinator import FeedCoordinator
from custom_components.hungaromet_weather_and_forecast.data import HungaroMetData
from custom_components.hungaromet_weather_and_forecast.stations import FEEDS, StationQuery


def loaded_entry(entry_id="e1", title="HungaroMet", data=None, options=None, **runtime):
    """Return an entry with runtime data, as while it is set up."""
    return SimpleNamespace(
        entry_id=entry_id,
        title=title,
        data=data if data is not None else {"distance_km": 20},
        options=options or {},
        pref_disable_polling=False,
        async_on_unload=MagicMock(),
        runtime_data=HungaroMetData(name=title, **runtime),
    )


def feed_coordinator(feed="hourly", query=None, data=None, hass=None):
    """Return a feed's coordinator, holding `data` as its last download."""
    query = query or StationQuery()
    coordinator = FeedCoordinator(
        hass or MagicMock(is_stopping=False),
        loaded_entry(failures=query.records),
        FEEDS[feed],
        query,
    )
    coordinator.data = data
    return coordinator
