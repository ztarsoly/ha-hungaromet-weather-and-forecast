"""The station feeds' last downloads, saved for a restart or reload.

The sensors get their first values from a download during setup. When met.hu
cannot be reached then, a feed's sensors show its saved values instead, if they
were saved within the last hour; older values are left out, as the sensors
would show them as current.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import DOMAIN

if TYPE_CHECKING:
    from .station_sensor import StationQuery

STORAGE_VERSION = 1
# How old saved values can be and still be shown
MAX_AGE = timedelta(hours=1)


def station_store(hass: HomeAssistant, entry_id: str) -> Store:
    """Return where an entry's station feeds are saved (in /config/.storage)."""
    return Store(hass, STORAGE_VERSION, f"{DOMAIN}.stations.{entry_id}")


def _query(query: StationQuery) -> dict:
    """Return which stations a query averages, as saved."""
    return {
        "distance_km": query.distance_km,
        "location": list(query.location) if query.location else None,
    }


def _plain(value):
    # The averages are numpy floats
    return float(value) if isinstance(value, float) else value


class SavedFeeds:
    """The last download of each station feed, for one entry's stations."""

    def __init__(self, store: Store, query: StationQuery) -> None:
        self._store = store
        self._query = _query(query)
        self._feeds: dict[str, dict] = {}

    async def async_load(self) -> None:
        """Load the saved feeds, unless they are for other stations.

        Feeds saved for another distance or location (e.g. after Configure)
        averaged other stations, so they are not used.
        """
        stored = await self._store.async_load()
        if not stored or stored.get("query") != self._query:
            return
        feeds = stored.get("feeds")
        self._feeds = feeds if isinstance(feeds, dict) else {}

    def recent(self, key: str) -> tuple[dict, list[dict]] | None:
        """Return a feed's saved values and stations if saved within MAX_AGE."""
        feed = self._feeds.get(key)
        try:
            saved = datetime.fromisoformat(feed["saved"])
            data, stations = dict(feed["data"]), list(feed["stations"])
        except (KeyError, TypeError, ValueError):
            return None
        if dt_util.utcnow() - saved > MAX_AGE:
            return None
        return data, stations

    async def async_save(self, key: str, data: dict, stations: list[dict]) -> None:
        """Save a feed's download."""
        self._feeds[key] = {
            "saved": dt_util.utcnow().isoformat(),
            "data": {name: _plain(value) for name, value in data.items()},
            "stations": stations,
        }
        await self._store.async_save({"query": self._query, "feeds": self._feeds})
