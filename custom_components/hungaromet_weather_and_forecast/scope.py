"""Keep each config entry's entities and devices apart.

Several entries (for example home and another settlement) can run side by side,
so unique IDs and device identifiers carry the entry's ID, and device names
carry the entry's name. The shared national radar image stays unscoped.
"""

from dataclasses import dataclass

from .const import CONF_ENTRY_TYPE, DOMAIN, ENTRY_TYPE_RADAR


def is_radar_entry(entry) -> bool:
    """Return whether an entry is the national radar entry, not a location."""
    return entry.data.get(CONF_ENTRY_TYPE) == ENTRY_TYPE_RADAR


@dataclass(frozen=True)
class EntryScope:
    """The config entry an entity belongs to."""

    entry_id: str
    name: str

    @classmethod
    def from_entry(cls, entry) -> "EntryScope":
        """Return the scope of a config entry."""
        return cls(entry.entry_id, entry.title)


def scoped_id(scope: EntryScope | None, value: str) -> str:
    """Return a unique ID or device identifier, prefixed with the entry's ID."""
    return f"{scope.entry_id}_{value}" if scope else value


def device_info(
    scope: EntryScope | None, device_id: str, translation_key: str, model: str
) -> dict:
    """Return device info for one of an entry's devices."""
    info = {
        "identifiers": {(DOMAIN, scoped_id(scope, device_id))},
        "translation_key": translation_key,
        "manufacturer": "HungaroMet",
        "model": model,
        "entry_type": "service",
    }
    if scope:
        # Device names are translated as "{name} Daily Weather" and so on
        info["translation_placeholders"] = {"name": scope.name}
    return info
