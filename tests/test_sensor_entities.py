"""Tests for the weather station sensors and the national radar image entity."""

from datetime import UTC, date, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.components.sensor import SensorDeviceClass

from custom_components.hungaromet_weather_and_forecast.device_classes import WIND_DIRECTION
from custom_components.hungaromet_weather_and_forecast.national_radar import HungarometRadarImage
from custom_components.hungaromet_weather_and_forecast.scope import EntryScope
from custom_components.hungaromet_weather_and_forecast.station_sensor import (
    FEEDS,
    MEASUREMENTS,
    FeedSensor,
    Measurement,
    StationQuery,
    StationSensor,
    StationsSensor,
    reading,
)
from custom_components.hungaromet_weather_and_forecast.weather_data import (
    process_daily_data,
    process_hourly_data,
    process_ten_minutes_data,
)

QUERY = StationQuery(distance_km=35, location=(47.5, 21.6))


def _sensor(feed="hourly", key="t", value=1.234, unit="°C", hass=None, name="X"):
    return StationSensor(
        hass or SimpleNamespace(), FEEDS[feed], Measurement(name, key, unit), value, QUERY
    )


def _added(entity):
    entity._added = True
    entity.async_write_ha_state = MagicMock()
    return entity


class DummyBus:
    def __init__(self):
        self.calls = []

    def async_listen_once(self, event, callback):
        self.calls.append((event, callback))


def _radar_hass(**extra):
    return SimpleNamespace(data={}, bus=DummyBus(), **extra)


# Station sensors


def test_feeds_and_their_devices():
    assert {key: feed.process for key, feed in FEEDS.items()} == {
        "daily": process_daily_data,
        "hourly": process_hourly_data,
        "ten_minutes": process_ten_minutes_data,
    }
    assert [len(MEASUREMENTS[key]) for key in FEEDS] == [28, 29, 25]


@pytest.mark.parametrize(
    ("feed", "name", "unique_id", "device_id", "model"),
    [
        ("daily", "Napi párolgás", "hungaromet_weather_napi_párolgás", "hungaromet_weather", "Daily weather"),
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
    sensor = _sensor(feed, name=name)
    assert sensor.unique_id == unique_id
    assert sensor.device_info["identifiers"] == {("hungaromet_weather_and_forecast", device_id)}
    assert sensor.device_info["translation_key"] == feed
    assert sensor.device_info["model"] == model
    assert sensor.translation_key == f"{feed}_t"


def test_value_sensor_values():
    sensor = _sensor()
    assert sensor.native_value == 1.23
    assert sensor.native_unit_of_measurement == "°C"
    assert sensor.device_class == SensorDeviceClass.TEMPERATURE
    assert sensor.options is None
    sensor._value = None
    assert sensor.native_value is None
    sensor._value = "text"
    assert sensor.native_value == "text"
    # A sensor of an "average_" key is named after the measurement
    assert _sensor(key="average_upe", unit="mm").translation_key == "hourly_upe"


@pytest.mark.parametrize(
    ("feed", "value", "expected"),
    [
        ("daily", "2024-01-01", date(2024, 1, 1)),
        ("hourly", "2024-01-01T12:34:00+00:00", datetime(2024, 1, 1, 12, 34, tzinfo=UTC)),
        ("ten_minutes", "2024-01-01T06:00:00+00:00", datetime(2024, 1, 1, 6, tzinfo=UTC)),
    ],
)
def test_measurement_times(feed, value, expected, caplog):
    sensor = _sensor(feed, "time", value, None)
    assert sensor.device_class == (
        SensorDeviceClass.DATE if feed == "daily" else SensorDeviceClass.TIMESTAMP
    )
    assert sensor.native_value == expected
    sensor._value = "bad"
    assert sensor.native_value is None
    assert f"Failed to read the {feed} measurement time" in caplog.text


def test_prevailing_wind_direction_is_an_enum():
    sensor = _sensor("daily", "fud", "ssw", None)
    assert sensor.device_class == SensorDeviceClass.ENUM
    assert "ssw" in sensor.options
    assert sensor.native_value == "ssw"


@pytest.mark.parametrize(
    ("key", "unit", "device_class"),
    [
        ("et50", "°C", SensorDeviceClass.TEMPERATURE),
        ("rau", "mm", SensorDeviceClass.PRECIPITATION),
        ("average_water_balance", "mm", SensorDeviceClass.PRECIPITATION),
        ("u", "%", SensorDeviceClass.HUMIDITY),
        ("p", "hPa", SensorDeviceClass.PRESSURE),
        ("p0", "hPa", SensorDeviceClass.ATMOSPHERIC_PRESSURE),
        ("fx", "m/s", SensorDeviceClass.WIND_SPEED),
        ("fxd", "°", WIND_DIRECTION),
        ("v", "m", SensorDeviceClass.DISTANCE),
        ("sr", "J/cm²", None),
        ("sg", "nSv/h", None),
    ],
)
def test_station_sensor_device_classes(key, unit, device_class):
    for feed in FEEDS:
        assert _sensor(feed, key, 1.0, unit).device_class == device_class


def test_reading_prefers_the_key_then_its_average():
    assert reading({"t": 1, "average_t": 2}, "t") == 1
    assert reading({"average_t": 2}, "t") == 2
    assert reading({}, "t") is None


def test_stations_sensor():
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
    sensor = StationsSensor(None, FEEDS["hourly"], rows, StationQuery(scope=EntryScope("e1", "P")))
    assert sensor.translation_key == "stations"
    assert sensor.unique_id == "e1_hungaromet_weather_station_info_hourly"
    assert sensor.device_info == _sensor().device_info | {
        "identifiers": {("hungaromet_weather_and_forecast", "e1_hungaromet_weather_hourly")},
        "translation_placeholders": {"name": "P"},
    }
    assert sensor.native_value == 1
    assert sensor.extra_state_attributes == {
        "stations": [
            {"StationNumber": 1, "StationName": "A", "Latitude": 47.5, "Longitude": 19.0, "Elevation": 100}
        ],
        "raw_data": rows,
    }
    sensor._stations = None
    assert sensor.native_value == 0
    assert sensor.extra_state_attributes == {"stations": [], "raw_data": []}


@pytest.mark.asyncio
async def test_sensors_update_only_once_added():
    hass = SimpleNamespace(async_add_executor_job=AsyncMock(return_value=({"average_t": 4.5}, ["S"])))
    value = _sensor(hass=hass)
    stations = StationsSensor(hass, FEEDS["hourly"], [], QUERY)

    await value.async_added_to_hass()
    assert value.added
    await value.async_will_remove_from_hass()
    assert not value.added
    await value.async_update()
    hass.async_add_executor_job.assert_not_awaited()

    for sensor in (_added(value), _added(stations)):
        await sensor.async_update()
        sensor.async_write_ha_state.assert_called_once()
    assert hass.async_add_executor_job.await_args.args == (process_hourly_data, hass, 35, (47.5, 21.6))
    assert value.native_value == 4.5
    assert stations.native_value == 1


def test_feed_sensors_must_say_what_they_show():
    with pytest.raises(NotImplementedError):
        FeedSensor(None, FEEDS["daily"], QUERY).show({}, [])


# National radar image


@pytest.mark.asyncio
async def test_radar_image_updates_only_when_added():
    hass = _radar_hass(async_add_executor_job=AsyncMock(return_value=("x.png", b"gif data")))
    image = HungarometRadarImage(hass)
    image.async_write_ha_state = MagicMock()
    image._frames._gif = b"gif data"

    assert await image.async_update_data() is None
    hass.async_add_executor_job.assert_not_called()

    image._added = True
    assert await image.async_update_data() == "x.png"

    hass.async_add_executor_job.assert_awaited_once()
    assert image._update_counter == 1
    assert image.image_last_updated is not None
    assert image.extra_state_attributes["last_updated"] == image.image_last_updated.isoformat()
    assert image.state == image.image_last_updated.isoformat()
    image.async_write_ha_state.assert_called_once()


@pytest.mark.asyncio
async def test_radar_image_schedules_and_unsubscribes(monkeypatch):
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
    image = HungarometRadarImage(hass)

    await image.async_added_to_hass()
    assert captured["kwargs"] == {"minute": "/5", "second": 59}
    # Added again: no second schedule
    await image.async_added_to_hass()
    assert len(tasks) == 1

    # The GIF is built straight away, in the background, with the usual retry
    ((first_update, name),) = tasks
    assert "first update" in name
    await first_update
    hass.async_add_executor_job.assert_awaited_once_with(image._rebuild_gif)
    assert len(retries) == 1

    # The update service can rebuild this image too
    registered = hass.data["hungaromet_weather_and_forecast_updaters"]
    assert registered == {image.unique_id: image.async_update_data}

    await image.async_will_remove_from_hass()
    assert captured.get("unsub") is True
    assert registered == {}


@pytest.mark.asyncio
async def test_radar_image_shows_the_last_animation():
    image = HungarometRadarImage(_radar_hass())
    assert image.image() is None
    assert await image.async_image() is None
    assert image.available is False

    image._frames._gif = b"binary"
    assert image.image() == b"binary"
    assert await image.async_image() == b"binary"
    assert image.available is True


def test_radar_image_names_and_ids():
    hass = _radar_hass()
    image = HungarometRadarImage(hass)

    # Its own name, without the device name ("National Radar Image") in front
    assert image.name == "National radar image"
    assert image.has_entity_name is False
    hass.config = SimpleNamespace(language="hu")
    assert image.name == "Országos radarkép"
    hass.config.language = "de"  # no German names: English
    assert image.name == "National radar image"
    assert image.unique_id == "hungaromet_radar_gif_hungaromet_radar"
    assert image.device_info["model"] == "National radar image"
    assert image.device_info["translation_key"] == "radar"
    assert image.state is None
    assert image.extra_state_attributes == {"last_updated": None, "update_counter": 0}


@pytest.mark.asyncio
async def test_radar_update_entity_rebuilds_the_animation():
    image = HungarometRadarImage(_radar_hass())
    image.async_update_data = AsyncMock()

    await image.async_update()

    image.async_update_data.assert_awaited_once()


@pytest.mark.asyncio
async def test_radar_scheduled_update_reports_freshness(monkeypatch):
    image = HungarometRadarImage(_radar_hass())
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
