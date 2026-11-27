"""What a loaded config entry keeps in its runtime_data."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from homeassistant.config_entries import ConfigEntry

from .failures import FailureRecords

if TYPE_CHECKING:
    from .coordinator import FeedCoordinator, ForecastCoordinator


@dataclass
class HungaroMetData:
    """What a config entry's parts share while it is loaded."""

    # The name the entry was set up with, to spot a renamed entry
    name: str
    # None without a forecast settlement, and for the radar entry
    forecast: ForecastCoordinator | None = None
    # A location entry's weather station feeds, by their keys
    feeds: dict[str, FeedCoordinator] = field(default_factory=dict)
    # What the update service refreshes, by key
    updaters: dict[str, Callable[[], Awaitable]] = field(default_factory=dict)
    # What is logged already, so that it is not logged again
    failures: FailureRecords = field(default_factory=FailureRecords)


type HungaroMetConfigEntry = ConfigEntry[HungaroMetData]
