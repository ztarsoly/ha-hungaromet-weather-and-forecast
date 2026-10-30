"""Tests for the regional radar image from met.hu's quadrant images."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import requests

import custom_components.hungaromet_weather_and_forecast as integration
from custom_components.hungaromet_weather_and_forecast import national_radar, radar
from custom_components.hungaromet_weather_and_forecast import regional_radar as rr
from custom_components.hungaromet_weather_and_forecast.radar import frame_time
from custom_components.hungaromet_weather_and_forecast.scope import EntryScope

SCOPE = EntryScope("e1", "HungaroMet")
PAGE = (
    "<div style=\"background: url('/img/RccH/RccH20261028_1150.jpg')\"></div>"
    + "".join(f"Imgs[{i}]='RccH20261028_11{m:02d}.jpg';" for i, m in enumerate(range(0, 60, 5)))
)


@pytest.mark.parametrize(
    ("lat", "lon", "quadrant"),
    [
        (47.53, 21.64, "ne"),  # Debrecen
        (47.50, 19.04, "nw"),  # Budapest
        (46.07, 18.23, "sw"),  # Pécs
        (46.25, 20.15, "se"),  # Szeged
        (47.15, 19.50, "ne"),  # the split point itself
    ],
)
def test_quadrant_for(lat, lon, quadrant):
    assert rr.quadrant_for(lat, lon) == quadrant


def test_radar_starts_without_an_animation():
    """Nothing is read from disk: unavailable until the first rebuild."""
    entity = national_radar.HungarometRadarImage(SimpleNamespace(data={}, bus=MagicMock()))
    assert entity.available is False
    assert entity.image() is None


def test_radar_base_needs_a_frame_listing():
    hass = SimpleNamespace(data={}, bus=MagicMock())
    base = radar.RadarImage(hass, "base_device_base")
    assert base.unique_id == "base_device_base"
    with pytest.raises(NotImplementedError):
        base._rebuild_gif()


def test_saved_gifs_are_removed(tmp_path):
    """Animations saved in custom_components/www or /config/.storage are deleted."""
    legacy, storage = tmp_path / "www", tmp_path / "storage"
    for folder in (legacy, storage):
        folder.mkdir()
        for name in ("radar_animation.gif", "regional_radar_e1.gif"):
            (folder / name).write_bytes(b"gif")
    with patch.object(radar, "LEGACY_GIF_DIR", str(legacy)):
        radar.remove_saved_gifs(str(storage))
        assert not legacy.exists()
        assert not storage.exists()
        # Nothing left to remove is fine
        radar.remove_saved_gifs(str(storage))


def test_folder_with_other_files_is_kept(tmp_path):
    legacy = tmp_path / "www"
    legacy.mkdir()
    (legacy / "radar_animation.gif").write_bytes(b"gif")
    (legacy / "mine.png").write_bytes(b"png")
    with patch.object(radar, "LEGACY_GIF_DIR", str(legacy)):
        radar.remove_saved_gifs(str(tmp_path / "missing"))
    assert sorted(p.name for p in legacy.iterdir()) == ["mine.png"]


def test_frame_time_reads_met_hu_jpg_names():
    assert frame_time("https://www.met.hu/img/RccH/RccH20261028_1150.jpg").hour == 11


def test_latest_frame_urls_default_to_five():
    with patch.object(rr.requests, "get", return_value=MagicMock(text=PAGE)):
        urls = rr.latest_frame_urls("ne")
    assert [u[-8:-4] for u in urls] == ["1135", "1140", "1145", "1150", "1155"]


def test_latest_frame_urls():
    response = MagicMock(text=PAGE)
    with patch.object(rr.requests, "get", return_value=response) as get:
        urls = rr.latest_frame_urls("ne", count=3)

    assert get.call_args.kwargs["params"] == {
        "ful": "EK",
        "fulmenu": "aktualis_idojaras/radar",
    }
    response.raise_for_status.assert_called_once()
    assert urls == [
        "https://www.met.hu/img/RccH/RccH20261028_1145.jpg",
        "https://www.met.hu/img/RccH/RccH20261028_1150.jpg",
        "https://www.met.hu/img/RccH/RccH20261028_1155.jpg",
    ]


def _regional():
    hass = SimpleNamespace(data={}, bus=MagicMock(), async_add_executor_job=AsyncMock())
    return rr.HungarometRegionalRadarImage(hass, SCOPE, "ne")


def test_regional_radar_name_in_hungarian():
    entity = _regional()
    entity.hass.config = SimpleNamespace(language="hu")
    assert entity.name == "Regionális radarkép"


def test_regional_radar_entity():
    entity = _regional()
    assert entity.unique_id == "e1_hungaromet_regional_radar"
    # In Home Assistant's language (English by default)
    assert entity.name == "Regional radar image"
    assert entity.has_entity_name is False
    assert entity.should_poll is False
    assert entity.device_info["identifiers"] == {
        ("hungaromet_weather_and_forecast", "e1_hungaromet_regional_radar")
    }
    assert entity.device_info["translation_key"] == "regional_radar"
    assert entity.device_info["model"] == "Regional radar image"
    assert entity.device_info["translation_placeholders"] == {"name": "HungaroMet"}
    assert entity.extra_state_attributes["quadrant"] == "ne"
    assert entity.extra_state_attributes["update_counter"] == 0


def test_regional_radar_lists_its_own_quadrant():
    entity = _regional()
    with (
        patch.object(rr, "latest_frame_urls", return_value=["a.jpg", "b.jpg"]) as listing,
        patch.object(entity._frames, "update", return_value=("b.jpg", b"gif")) as update,
    ):
        assert entity._rebuild_gif() == ("b.jpg", b"gif")
    listing.assert_called_once_with("ne")
    update.assert_called_once_with(["a.jpg", "b.jpg"])


def test_regional_radar_network_error(caplog):
    entity = _regional()
    with patch.object(rr, "latest_frame_urls", side_effect=requests.ConnectionError("down")):
        assert entity._rebuild_gif() == (None, None)
    assert "Network error listing Regional radar frames: down" in caplog.text


@pytest.mark.asyncio
async def test_incomplete_animation_is_shown_but_retried():
    """A frame missing: the GIF is shown, but the run is not counted as done."""
    entity = _regional()
    entity._added = True
    entity.async_write_ha_state = MagicMock()

    async def rebuild(_):
        entity._frames._gif = b"partial"
        return None, b"partial"

    entity.hass.async_add_executor_job = AsyncMock(side_effect=rebuild)

    assert await entity.async_update_data() is None

    assert entity.image() == b"partial"
    assert entity._update_counter == 0
    entity.async_write_ha_state.assert_called_once()


@pytest.mark.asyncio
async def test_failed_rebuild_keeps_the_last_animation():
    entity = _regional()
    entity._added = True
    entity._frames._gif = b"old"
    entity.async_write_ha_state = MagicMock()
    entity.hass.async_add_executor_job = AsyncMock(return_value=(None, None))

    await entity.async_update_data()

    assert entity.image() == b"old"
    entity.async_write_ha_state.assert_not_called()
