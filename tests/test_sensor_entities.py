"""Tests for the weather station sensors and the national radar image entity."""

# The tests check the integration's internals
# pylint: disable=protected-access

from datetime import UTC, date, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.components.sensor import SensorDeviceClass

from custom_components.hungaromet_weather_and_forecast.device_classes import WIND_DIRECTION
from custom_components.hungaromet_weather_and_forecast.image import HungarometRadarImage
from custom_components.hungaromet_weather_and_forecast.sensor import (
    StationSensor,
    StationsSensor,
)
from custom_components.hungaromet_weather_and_forecast.stations import (
    FEEDS,
    MEASUREMENTS,
    Measurement,
    StationQuery,
    reading,
)
from custom_components.hungaromet_weather_and_forecast.scope import EntryScope
from custom_components.hungaromet_weather_and_forecast.weather_data import (
    process_daily_data,
    process_hourly_data,
    process_ten_minutes_data,
)

from .entries import feed_coordinator, loaded_entry

QUERY = StationQuery(distance_km=35, location=(47.5, 21.6))


def _sensor(feed="hourly", key="t", value=1.234, unit="°C", name="X"):
    coordinator = feed_coordinator(feed, QUERY, ({key: value}, []))
    return StationSensor(coordinator, Measurement(name, key, unit))


def _download(sensor, value):
    """Give a sensor's feed a new download with this value of its measurement."""
    sensor.coordinator.data = ({sensor._measurement.key: value}, [])


class DummyBus:
    """An event bus that records the listeners."""
    def __init__(self):
        self.calls = []

    def async_listen_once(self, event, callback):
        """Record a listener."""
        self.calls.append((event, callback))


def _radar_hass(**extra):
    return SimpleNamespace(data={}, bus=DummyBus(), **extra)


# Station sensors


def test_feeds_and_their_devices():
    """Test feeds and their devices."""
    assert {key: feed.process for key, feed in FEEDS.items()} == {
        "daily": process_daily_data,
        "hourly": process_hourly_data,
        "ten_minutes": process_ten_minutes_data,
    }
    assert [len(MEASUREMENTS[key]) for key in FEEDS] == [26, 28, 24]


@pytest.mark.parametrize(
    ("feed", "name", "unique_id", "device_id", "model"),
    [
        (
            "daily",
            "Napi párolgás",
            "hungaromet_weather_napi_párolgás",
            "hungaromet_weather",
            "Daily weather",
        ),
        (
            "hourly",
            "Órás pillanatnyi hőmérséklet",
            "hungaromet_weather_hourly_órás_pillanatnyi_hőmérséklet",
            "hungaromet_weather_hourly",
            "Hourly weather",
        ),
        (
            "ten_minutes",
            "Tízperces 5 cm-es talajhőmérséklet",
            "hungaromet_weather_ten_minutes_tízperces_5_cm-es_talajhőmérséklet",
            "hungaromet_weather_ten_minutes",
            "10-minute weather",
        ),
    ],
)
def test_ids_and_devices_stay_as_before(feed, name, unique_id, device_id, model):
    """Test ids and devices stay as before."""
    sensor = _sensor(feed, name=name)
    assert sensor.unique_id == unique_id
    assert sensor.device_info["identifiers"] == {("hungaromet_weather_and_forecast", device_id)}
    assert sensor.device_info["translation_key"] == feed
    assert sensor.device_info["model"] == model
    assert sensor.translation_key == f"{feed}_t"


def test_value_sensor_values():
    """Test value sensor values."""
    sensor = _sensor()
    assert sensor.native_value == 1.23
    assert sensor.native_unit_of_measurement == "°C"
    assert sensor.device_class == SensorDeviceClass.TEMPERATURE
    assert sensor.options is None
    _download(sensor, None)
    assert sensor.native_value is None
    _download(sensor, "text")
    assert sensor.native_value == "text"
    # Nothing downloaded yet
    sensor.coordinator.data = None
    assert sensor.native_value is None
    # A sensor of an "average_" key is named after the measurement
    assert _sensor(key="average_upe", unit="mm").translation_key == "hourly_upe"


@pytest.mark.parametrize(
    ("value", "expected"),
    [(-2.345, -2.35), (2.345, 2.35), (-0.001, 0.0), (7, 7)],
)
def test_station_values_round_halves_away_from_zero(value, expected):
    """Two decimals, and a whole number stays one."""
    sensor = _sensor()
    _download(sensor, value)
    assert sensor.native_value == expected
    assert type(sensor.native_value) is type(expected)


@pytest.mark.parametrize(
    ("feed", "value", "expected"),
    [
        ("daily", "2024-01-01", date(2024, 1, 1)),
        ("hourly", "2024-01-01T12:34:00+00:00", datetime(2024, 1, 1, 12, 34, tzinfo=UTC)),
        ("ten_minutes", "2024-01-01T06:00:00+00:00", datetime(2024, 1, 1, 6, tzinfo=UTC)),
    ],
)
def test_measurement_times(feed, value, expected, caplog):
    """Test measurement times."""
    sensor = _sensor(feed, "time", value, None)
    assert sensor.device_class == (
        SensorDeviceClass.DATE if feed == "daily" else SensorDeviceClass.TIMESTAMP
    )
    assert sensor.native_value == expected
    _download(sensor, "bad")
    assert sensor.native_value is None
    assert not sensor.available
    assert caplog.text.count(f"Reading the {feed} measurement time failed") == 1
    _download(sensor, value)
    assert sensor.native_value == expected
    assert f"Reading the {feed} measurement time works again" in caplog.text


def test_prevailing_wind_direction_is_an_enum():
    """Test prevailing wind direction is an enum."""
    sensor = _sensor("daily", "fud", "ssw", None)
    assert sensor.device_class == SensorDeviceClass.ENUM
    assert "ssw" in sensor.options
    assert sensor.native_value == "ssw"


@pytest.mark.parametrize(
    ("key", "unit", "device_class"),
    [
        ("et50", "°C", SensorDeviceClass.TEMPERATURE),
        ("rau", "mm", SensorDeviceClass.PRECIPITATION),
        ("average_upe", "mm", SensorDeviceClass.PRECIPITATION),
        ("u", "%", SensorDeviceClass.HUMIDITY),
        ("p", "hPa", SensorDeviceClass.PRESSURE),
        ("p0", "hPa", SensorDeviceClass.ATMOSPHERIC_PRESSURE),
        ("fx", "m/s", SensorDeviceClass.WIND_SPEED),
        ("fxd", "°", WIND_DIRECTION),
        ("v", "m", SensorDeviceClass.DISTANCE),
        ("sr", "J/cm²", None),
        ("sr", "W/m²", SensorDeviceClass.IRRADIANCE),
        ("suv", "MED/h", None),
        ("sg", "nSv/h", None),
    ],
)
def test_station_sensor_device_classes(key, unit, device_class):
    """Test station sensor device classes."""
    for feed in FEEDS:
        assert _sensor(feed, key, 1.0, unit).device_class == device_class


def test_only_the_ten_minute_radiation_values_are_rates():
    """The daily and hourly radiation values are sums, the 10-minute ones rates."""
    units = {
        feed: [m.unit for m in MEASUREMENTS[feed] if m.key in ("sr", "suv")]
        for feed in FEEDS
    }
    assert units == {
        "daily": ["J/cm²", "MED"],
        "hourly": ["J/cm²", "MED"],
        "ten_minutes": ["W/m²", "MED/h"],
    }


def test_numeric_station_sensors_show_one_decimal():
    """Test numeric station sensors show one decimal."""
    assert _sensor().suggested_display_precision == 1
    assert _sensor(key="fud", value="N", unit=None).suggested_display_precision is None
    assert _sensor(key="time", value=None, unit=None).suggested_display_precision is None


def test_reading_prefers_the_key_then_its_average():
    """Test reading prefers the key then its average."""
    assert reading({"t": 1, "average_t": 2}, "t") == 1
    assert reading({"average_t": 2}, "t") == 2
    assert reading({}, "t") is None


def test_stations_sensor():
    """Test stations sensor."""
    rows = [
        {
            "StationNumber": 1,
            "StationName": "A",
            "Latitude": 47.5,
            "Longitude": 19.0,
            "Elevation": 100,
            "t": 10.5,
            "Distance_km": 2.0,
        }
    ]
    query = StationQuery(scope=EntryScope("e1", "P"))
    sensor = StationsSensor(feed_coordinator("hourly", query, ({}, rows)))
    assert sensor.translation_key == "stations"
    assert sensor.unique_id == "e1_hungaromet_weather_station_info_hourly"
    assert sensor.device_info == _sensor().device_info | {
        "identifiers": {("hungaromet_weather_and_forecast", "e1_hungaromet_weather_hourly")},
        "translation_placeholders": {"name": "P"},
    }
    assert sensor.native_value == 1
    assert sensor.extra_state_attributes == {
        "stations": [
            {
                "StationNumber": 1,
                "StationName": "A",
                "Latitude": 47.5,
                "Longitude": 19.0,
                "Elevation": 100,
            }
        ],
        "raw_data": rows,
    }
    sensor.coordinator.data = None
    assert sensor.native_value == 0
    assert sensor.extra_state_attributes == {"stations": [], "raw_data": []}


# National radar image


async def test_radar_image_updates_only_when_added():
    """Test radar image updates only when added."""
    hass = _radar_hass(async_add_executor_job=AsyncMock(return_value=("x.png", b"gif data")))
    image = HungarometRadarImage(hass, loaded_entry())
    image.async_write_ha_state = MagicMock()
    image._frames._gif = b"gif data"

    assert await image.async_update_data() is None
    hass.async_add_executor_job.assert_not_called()

    image._added = True
    assert await image.async_update_data() == "x.png"

    hass.async_add_executor_job.assert_awaited_once()
    assert image.image_last_updated is not None
    assert image.state == image.image_last_updated.isoformat()
    image.async_write_ha_state.assert_called_once()


async def test_radar_image_schedules_and_unsubscribes(monkeypatch):
    """Test radar image schedules and unsubscribes."""
    captured = {}

    def fake_track(hass, callback, **kwargs):
        captured["kwargs"] = kwargs
        return lambda: captured.setdefault("unsub", True)

    monkeypatch.setattr(
        "custom_components.hungaromet_weather_and_forecast.scheduler.async_track_utc_time_change",
        fake_track,
    )
    retries = []
    monkeypatch.setattr(
        "custom_components.hungaromet_weather_and_forecast.scheduler.async_call_later",
        lambda hass, delay, action: retries.append(delay) or (lambda: None),
    )
    tasks = []
    hass = _radar_hass(
        async_add_executor_job=AsyncMock(return_value=(None, None)),
        async_create_task=lambda coro, name: tasks.append((coro, name)),
    )
    entry = loaded_entry()
    image = HungarometRadarImage(hass, entry)
    image.async_write_ha_state = MagicMock()

    await image.async_added_to_hass()
    assert captured["kwargs"] == {"minute": "/5", "second": 59}
    # Added again: no second schedule
    await image.async_added_to_hass()
    assert len(tasks) == 1

    # The GIF is built straight away, in the background, with the usual retry
    first_update, name = tasks[0]
    assert "first update" in name
    await first_update
    hass.async_add_executor_job.assert_awaited_once_with(image._rebuild_gif)
    assert len(retries) == 1

    # The update service can rebuild this image too
    registered = entry.runtime_data.updaters
    assert registered == {image.unique_id: image.async_update_data}

    await image.async_will_remove_from_hass()
    assert captured.get("unsub") is True
    assert registered == {}


async def test_radar_image_shows_the_last_animation():
    """Test radar image shows the last animation."""
    image = HungarometRadarImage(_radar_hass(), loaded_entry())
    assert image.image() is None
    assert await image.async_image() is None
    assert image.available is False

    image._frames._gif = b"binary"
    assert image.image() == b"binary"
    assert await image.async_image() == b"binary"
    assert image.available is True


def test_radar_image_names_and_ids():
    """Test radar image names and ids."""
    hass = _radar_hass()
    image = HungarometRadarImage(hass, loaded_entry())

    # Named after its device, which the translations name
    assert image.has_entity_name is True
    assert image.name is None
    assert image.unique_id == "hungaromet_radar_gif_hungaromet_radar"
    assert image.device_info["model"] == "National radar image"
    assert image.device_info["configuration_url"] == (
        "https://www.met.hu/idojaras/aktualis_idojaras/radar/"
    )
    assert image.device_info["translation_key"] == "radar"
    assert image.state is None
    assert image.extra_state_attributes is None


async def test_radar_update_entity_rebuilds_the_animation():
    """Test radar update entity rebuilds the animation."""
    image = HungarometRadarImage(_radar_hass(), loaded_entry())
    image.async_update_data = AsyncMock()

    await image.async_update()

    image.async_update_data.assert_awaited_once()


async def test_radar_scheduled_update_reports_freshness(monkeypatch):
    """Test radar scheduled update reports freshness."""
    image = HungarometRadarImage(_radar_hass(), loaded_entry())
    frame = "https://x/radar_composite-refl2D_pscappi-20261027_0800.png"
    called = AsyncMock(return_value=frame)
    monkeypatch.setattr(image, "async_update_data", called)

    # Not added: nothing to do, and no retry
    assert await image._async_scheduled_update(None) is True
    called.assert_not_awaited()

    image._added = True
    # At 08:05:59 the 08:00 image is due; at 08:10:59 the 08:05 one is
    assert await image._async_scheduled_update(datetime(2026, 10, 27, 8, 5, 59, tzinfo=UTC))
    assert not await image._async_scheduled_update(datetime(2026, 10, 27, 8, 10, 59, tzinfo=UTC))
    called.return_value = None
    assert not await image._async_scheduled_update(datetime(2026, 10, 27, 8, 5, 59, tzinfo=UTC))
    assert called.await_count == 3
