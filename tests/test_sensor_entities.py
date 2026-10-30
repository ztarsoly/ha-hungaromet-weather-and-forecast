from datetime import UTC, date, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.components.sensor import SensorDeviceClass

from custom_components.hungaromet_weather_and_forecast.daily_sensor import HungarometWeatherDailySensor
from custom_components.hungaromet_weather_and_forecast.device_classes import WIND_DIRECTION
from custom_components.hungaromet_weather_and_forecast.hourly_sensor import HungarometWeatherHourlySensor
from custom_components.hungaromet_weather_and_forecast.national_radar import HungarometRadarImage
from custom_components.hungaromet_weather_and_forecast.station_info_sensor import HungarometStationInfoSensor
from custom_components.hungaromet_weather_and_forecast.ten_minute_sensor import (
    HungarometWeatherTenMinutesSensor,
)
from custom_components.hungaromet_weather_and_forecast.weather_data import (
    process_daily_data,
    process_hourly_data,
    process_ten_minutes_data,
)


class DummyBus:
    def __init__(self):
        self.calls = []

    def async_listen_once(self, event, callback):
        self.calls.append((event, callback))


@pytest.mark.asyncio
async def test_ten_minute_sensor_fetches_its_data():
    hass = SimpleNamespace(
        async_add_executor_job=AsyncMock(return_value=({"t": 12.34}, None)),
        data={},
        bus=DummyBus(),
    )
    sensor = HungarometWeatherTenMinutesSensor(
        hass,
        name="Tízperces hőmérséklet",
        value=None,
        unit="°C",
        key="t",
    )
    sensor._added = True
    sensor.async_write_ha_state = MagicMock()

    await sensor.async_update_data()

    assert sensor._state == 12.34
    hass.async_add_executor_job.assert_awaited_once()
    sensor.async_write_ha_state.assert_called_once()


@pytest.mark.asyncio
async def test_hourly_sensor_fetches_its_data():
    hass = SimpleNamespace(
        async_add_executor_job=AsyncMock(return_value=({"t": 7.89}, None))
    )
    sensor = HungarometWeatherHourlySensor(
        hass,
        name="Órás hőmérséklet",
        value=None,
        unit="°C",
        key="t",
    )
    sensor._added = True
    sensor.async_write_ha_state = MagicMock()

    await sensor.async_update_data()

    hass.async_add_executor_job.assert_awaited_once()
    assert sensor._state == 7.89
    sensor.async_write_ha_state.assert_called_once()


@pytest.mark.parametrize(
    "key", ["t", "tsn24", "et5", "et10", "et20", "et50", "et100"]
)
def test_daily_sensors_are_enabled_by_default(key):
    sensor = HungarometWeatherDailySensor(SimpleNamespace(), key, 0, "°C", key)

    assert sensor.entity_registry_enabled_default is True


@pytest.mark.asyncio
async def test_daily_sensor_skips_update_when_not_added():
    hass = SimpleNamespace(async_add_executor_job=AsyncMock())
    sensor = HungarometWeatherDailySensor(
        hass,
        name="Napi átlag",
        value=None,
        unit="°C",
        key="t",
    )

    await sensor.async_update_data()

    hass.async_add_executor_job.assert_not_called()


@pytest.mark.asyncio
async def test_daily_sensor_fetches_its_data():
    hass = SimpleNamespace(async_add_executor_job=AsyncMock(return_value=({"t": 42}, None)))
    sensor = HungarometWeatherDailySensor(
        hass,
        name="Napi átlag",
        value=None,
        unit="°C",
        key="t",
    )
    sensor._added = True
    sensor.async_write_ha_state = MagicMock()

    await sensor.async_update_data()

    assert sensor._state == 42
    hass.async_add_executor_job.assert_awaited_once()
    sensor.async_write_ha_state.assert_called_once()


@pytest.mark.asyncio
async def test_daily_sensor_async_update_delegates_to_update_data():
    sensor = HungarometWeatherDailySensor(SimpleNamespace(), "Daily", 0, "°C", "t")
    sensor.async_update_data = AsyncMock()

    await sensor.async_update()

    sensor.async_update_data.assert_awaited_once()


def test_daily_sensor_sync_update_is_noop():
    sensor = HungarometWeatherDailySensor(SimpleNamespace(), "Daily", 0, "°C", "t")

    assert sensor.update() is None


@pytest.mark.asyncio
async def test_radar_image_updates_only_when_added():
    hass = SimpleNamespace(
        async_add_executor_job=AsyncMock(return_value=("x.png", b"gif data")),
        data={},
        bus=DummyBus(),
    )
    image = HungarometRadarImage(hass)
    image.async_write_ha_state = MagicMock()

    await image.async_update_data()
    hass.async_add_executor_job.assert_not_called()

    image._added = True
    await image.async_update_data()

    hass.async_add_executor_job.assert_awaited_once()
    assert image._update_counter == 1
    assert image.image() == b"gif data"
    image.async_write_ha_state.assert_called()


@pytest.mark.asyncio
async def test_ten_minute_sensor_no_update_when_not_added():
    hass = SimpleNamespace(async_add_executor_job=AsyncMock())
    sensor = HungarometWeatherTenMinutesSensor(
        hass,
        name="Tízperces hőmérséklet",
        value=None,
        unit="°C",
        key="t",
    )

    await sensor.async_update_data()

    hass.async_add_executor_job.assert_not_called()


@pytest.mark.asyncio
async def test_daily_sensor_uses_average_key_when_direct_missing():
    hass = SimpleNamespace(
        async_add_executor_job=AsyncMock(return_value=({"average_t": 18.5}, None))
    )
    sensor = HungarometWeatherDailySensor(
        hass,
        name="Napi átlag",
        value=None,
        unit="°C",
        key="t",
    )
    sensor._added = True
    sensor.async_write_ha_state = MagicMock()

    await sensor.async_update_data()

    assert sensor._state == 18.5
    sensor.async_write_ha_state.assert_called_once()


@pytest.mark.asyncio
async def test_station_info_sensor_updates_station_list():
    rows = [{"StationName": "AAA"}, {"StationName": "BBB"}]
    hass = SimpleNamespace(async_add_executor_job=AsyncMock(return_value=({}, rows)))
    sensor = HungarometStationInfoSensor(hass, "Stations", [], "daily")
    sensor._added = True
    sensor.async_write_ha_state = MagicMock()

    await sensor.async_update_data()

    hass.async_add_executor_job.assert_awaited_once()
    assert sensor.state == 2
    assert [s["StationName"] for s in sensor.extra_state_attributes["stations"]] == [
        "AAA",
        "BBB",
    ]
    sensor.async_write_ha_state.assert_called_once()


@pytest.mark.asyncio
async def test_radar_image_schedules_and_unsubscribes(monkeypatch):
    captured = {}

    def fake_track(hass, callback, **kwargs):
        captured["kwargs"] = kwargs
        captured["callback"] = callback

        def _unsub():
            captured["unsub"] = True

        return _unsub

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
    hass = SimpleNamespace(
        async_add_executor_job=AsyncMock(return_value=(None, None)),
        async_create_task=lambda coro, name: tasks.append((coro, name)),
        data={},
        bus=DummyBus(),
    )
    image = HungarometRadarImage(hass)

    await image.async_added_to_hass()
    assert image._unsub_update is not None
    assert captured["kwargs"] == {"minute": "/5", "second": 59}

    # The GIF is built straight away, in the background, with the usual retry
    (first_update, name), = tasks
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


def test_daily_sensor_state_and_metadata():
    hass = SimpleNamespace()
    sensor = HungarometWeatherDailySensor(
        hass,
        name="Átlaghőmérséklet",
        value=12.3456,
        unit="°C",
        key="t",
    )

    assert sensor.translation_key == "daily_t"
    assert sensor.device_class == SensorDeviceClass.TEMPERATURE
    assert sensor.native_value == 12.35
    sensor._state = None
    assert sensor.native_value is None
    assert sensor.native_unit_of_measurement == "°C"
    assert sensor.device_info["translation_key"] == "daily"
    assert sensor.unique_id.endswith("átlaghőmérséklet".replace(" ", "_"))


def test_hourly_sensor_time_is_a_timestamp():
    hass = SimpleNamespace()
    sensor = HungarometWeatherHourlySensor(
        hass,
        name="Idő",
        value="2024-01-01T12:34:00+00:00",
        unit=None,
        key="time",
    )

    assert sensor.device_class == SensorDeviceClass.TIMESTAMP
    assert sensor.native_value == datetime(2024, 1, 1, 12, 34, tzinfo=UTC)
    sensor._state = "invalid"
    assert sensor.native_value is None


@pytest.mark.asyncio
async def test_hourly_sensor_uses_average_if_needed():
    hass = SimpleNamespace(
        async_add_executor_job=AsyncMock(return_value=({"average_t": 21.5}, None))
    )
    sensor = HungarometWeatherHourlySensor(
        hass,
        name="Órás hőmérséklet",
        value=None,
        unit="°C",
        key="t",
    )
    sensor._added = True
    sensor.async_write_ha_state = MagicMock()

    await sensor.async_update_data()

    assert sensor._state == 21.5


def test_hourly_sensor_metadata_properties():
    hass = SimpleNamespace()
    sensor = HungarometWeatherHourlySensor(
        hass,
        name="Órás",
        value=1.234,
        unit="°C",
        key="t",
    )

    assert sensor.native_unit_of_measurement == "°C"
    assert sensor.device_class == SensorDeviceClass.TEMPERATURE
    assert sensor.device_info["manufacturer"] == "HungaroMet"
    assert sensor.native_value == 1.23


def test_hourly_sensor_returns_none_without_state():
    hass = SimpleNamespace()
    sensor = HungarometWeatherHourlySensor(hass, "Órás", None, "°C", "tn")

    assert sensor.native_value is None


def test_hourly_sensor_time_conversion_failure_returns_none():
    hass = SimpleNamespace()
    sensor = HungarometWeatherHourlySensor(hass, "Time", "bad", None, "time")

    assert sensor.native_value is None


def test_hourly_sensor_name_and_unique_id():
    hass = SimpleNamespace()
    sensor = HungarometWeatherHourlySensor(hass, "Órás", 0, "°C", "t")

    assert sensor.translation_key == "hourly_t"
    assert sensor.unique_id.startswith("hungaromet_weather_hourly_")
    assert sensor.device_info["translation_key"] == "hourly"


@pytest.mark.asyncio
async def test_hourly_sensor_skips_update_when_not_added():
    hass = SimpleNamespace(async_add_executor_job=AsyncMock())
    sensor = HungarometWeatherHourlySensor(hass, "Órás", None, "°C", "t")

    await sensor.async_update_data()

    hass.async_add_executor_job.assert_not_called()


@pytest.mark.asyncio
async def test_hourly_sensor_async_update_delegates_to_update_data():
    sensor = HungarometWeatherHourlySensor(SimpleNamespace(), "Órás", 0, "°C", "t")
    sensor.async_update_data = AsyncMock()

    await sensor.async_update()

    sensor.async_update_data.assert_awaited_once()


def test_hourly_sensor_sync_update_is_noop():
    sensor = HungarometWeatherHourlySensor(SimpleNamespace(), "Órás", 0, "°C", "t")

    assert sensor.update() is None


@pytest.mark.asyncio
async def test_ten_minute_sensor_uses_average_if_needed():
    hass = SimpleNamespace(
        async_add_executor_job=AsyncMock(return_value=({"average_t": 11.1}, None))
    )
    sensor = HungarometWeatherTenMinutesSensor(
        hass,
        name="Tízperces hőmérséklet",
        value=None,
        unit="°C",
        key="t",
    )
    sensor._added = True
    sensor.async_write_ha_state = MagicMock()

    await sensor.async_update_data()

    assert sensor._state == 11.1
    sensor.async_write_ha_state.assert_called_once()


def test_ten_minute_sensor_time_is_a_timestamp():
    hass = SimpleNamespace()
    sensor = HungarometWeatherTenMinutesSensor(
        hass,
        name="Idő",
        value="2024-01-01T06:00:00+00:00",
        unit=None,
        key="time",
    )

    assert sensor.device_class == SensorDeviceClass.TIMESTAMP
    assert sensor.native_value == datetime(2024, 1, 1, 6, 0, tzinfo=UTC)


def test_ten_minute_sensor_name_and_unique_id():
    hass = SimpleNamespace()
    sensor = HungarometWeatherTenMinutesSensor(hass, "Ten", 0, "°C", "t")

    assert sensor.translation_key == "ten_minutes_t"
    assert sensor.device_info["translation_key"] == "ten_minutes"
    assert sensor.unique_id.startswith("hungaromet_weather_ten_minutes_")
    assert sensor.native_unit_of_measurement == "°C"
    assert sensor.device_class == SensorDeviceClass.TEMPERATURE
    assert sensor.device_info["manufacturer"] == "HungaroMet"


def test_ten_minute_sensor_time_conversion_failure_returns_none():
    hass = SimpleNamespace()
    sensor = HungarometWeatherTenMinutesSensor(hass, "Idő", "invalid", None, "time")

    assert sensor.native_value is None


def test_ten_minute_sensor_state_none_when_missing():
    hass = SimpleNamespace()
    sensor = HungarometWeatherTenMinutesSensor(hass, "Ten", None, "°C", "f")

    assert sensor.native_value is None


def test_ten_minute_sensor_rounds_numeric_state():
    hass = SimpleNamespace()
    sensor = HungarometWeatherTenMinutesSensor(hass, "Ten", 1.2345, "°C", "t")

    assert sensor.native_value == 1.23


@pytest.mark.asyncio
async def test_ten_minute_sensor_async_update_delegates_to_update_data():
    sensor = HungarometWeatherTenMinutesSensor(SimpleNamespace(), "Ten", 0, "°C", "t")
    sensor.async_update_data = AsyncMock()

    await sensor.async_update()

    sensor.async_update_data.assert_awaited_once()


def test_ten_minute_sensor_sync_update_is_noop():
    sensor = HungarometWeatherTenMinutesSensor(SimpleNamespace(), "Ten", 0, "°C", "t")

    assert sensor.update() is None


@pytest.mark.asyncio
async def test_station_info_lifecycle(monkeypatch):
    hass = SimpleNamespace(async_add_executor_job=AsyncMock(return_value=({}, [])))
    sensor = HungarometStationInfoSensor(hass, "Stations", [], "daily")

    await sensor.async_added_to_hass()
    assert sensor._added is True

    await sensor.async_will_remove_from_hass()
    assert sensor._added is False


def test_station_info_properties():
    hass = SimpleNamespace(async_add_executor_job=AsyncMock())
    row = {
        "StationNumber": 1,
        "StationName": "A",
        "Latitude": 47.5,
        "Longitude": 19.0,
        "Elevation": 100,
        "t": 10.5,
        "Distance_km": 2.0,
    }
    sensor = HungarometStationInfoSensor(hass, "Stations", [row], "daily")

    assert sensor.translation_key == "stations"
    assert sensor.state == 1
    assert sensor.extra_state_attributes["stations"] == [
        {
            "StationNumber": 1,
            "StationName": "A",
            "Latitude": 47.5,
            "Longitude": 19.0,
            "Elevation": 100,
        }
    ]
    assert sensor.extra_state_attributes["raw_data"] == [row]
    assert "raw_data" in sensor._unrecorded_attributes
    assert sensor.device_info["translation_key"] == "daily"
    assert sensor.unique_id.endswith("daily")


@pytest.mark.asyncio
async def test_station_info_sensor_skips_update_when_not_added():
    hass = SimpleNamespace(async_add_executor_job=AsyncMock())
    sensor = HungarometStationInfoSensor(hass, "Stations", [], "daily")

    await sensor.async_update_data()

    hass.async_add_executor_job.assert_not_called()


@pytest.mark.asyncio
async def test_station_info_sensor_async_update_delegates_to_update_data():
    sensor = HungarometStationInfoSensor(SimpleNamespace(), "Stations", [], "daily")
    sensor.async_update_data = AsyncMock()

    await sensor.async_update()

    sensor.async_update_data.assert_awaited_once()


def test_station_info_sensor_sync_update_is_noop():
    sensor = HungarometStationInfoSensor(SimpleNamespace(), "Stations", [], "daily")

    assert sensor.update() is None


@pytest.mark.asyncio
async def test_radar_async_image_missing():
    hass = SimpleNamespace(async_add_executor_job=AsyncMock(), data={}, bus=DummyBus())
    image = HungarometRadarImage(hass)

    assert await image.async_image() is None


def test_radar_sync_image_returns_the_animation():
    hass = SimpleNamespace(async_add_executor_job=AsyncMock(), data={}, bus=DummyBus())
    image = HungarometRadarImage(hass)
    image._gif = b"binary"

    assert image.image() == b"binary"
    assert image.available is True


def test_radar_image_property_helpers():
    hass = SimpleNamespace(async_add_executor_job=AsyncMock(), data={}, bus=DummyBus())
    image = HungarometRadarImage(hass, name="Custom Radar")

    # Its own name, without the device name ("National Radar Image") in front
    assert image.name == "National radar image"
    assert image.has_entity_name is False
    hass.config = SimpleNamespace(language="hu")
    assert image.name == "Országos radarkép"
    hass.config.language = "de"  # no German names: English
    assert image.name == "National radar image"
    assert image.unique_id.endswith("custom_radar")
    assert image.device_info["model"] == "National radar image"
    assert image.device_info["translation_key"] == "radar"
    assert image.state == "unknown"
    assert image.available is False
    assert image.extra_state_attributes == {"last_updated": None, "update_counter": 0}


@pytest.mark.asyncio
async def test_radar_handle_scheduled_update_ignores_when_not_added(monkeypatch):
    hass = SimpleNamespace(async_add_executor_job=AsyncMock(), data={}, bus=DummyBus())
    image = HungarometRadarImage(hass)
    image.async_update_data = AsyncMock()

    assert await image._async_scheduled_update(None) is True

    image.async_update_data.assert_not_awaited()


@pytest.mark.asyncio
async def test_radar_async_update_delegates_to_update_data():
    hass = SimpleNamespace(async_add_executor_job=AsyncMock(), data={}, bus=DummyBus())
    image = HungarometRadarImage(hass)
    image.async_update_data = AsyncMock()

    await image.async_update()

    image.async_update_data.assert_awaited_once()


@pytest.mark.asyncio
async def test_radar_async_image_returns_the_animation():
    hass = SimpleNamespace(async_add_executor_job=AsyncMock(), data={}, bus=DummyBus())
    image = HungarometRadarImage(hass)
    image._gif = b"async"

    assert await image.async_image() == b"async"


def test_radar_sync_image_missing_returns_none():
    hass = SimpleNamespace(async_add_executor_job=AsyncMock(), data={}, bus=DummyBus())
    image = HungarometRadarImage(hass)

    assert image.image() is None


@pytest.mark.asyncio
async def test_radar_handle_scheduled_update(monkeypatch):
    hass = SimpleNamespace(async_add_executor_job=AsyncMock(), data={}, bus=DummyBus())
    image = HungarometRadarImage(hass)
    image._added = True
    frame = "https://x/radar_composite-refl2D_pscappi-20261027_0800.png"
    called = AsyncMock(return_value=frame)
    monkeypatch.setattr(image, "async_update_data", called)

    # At 08:05:59 the 08:00 image is due; at 08:10:59 the 08:05 one is
    assert await image._async_scheduled_update(
        datetime(2026, 10, 27, 8, 5, 59, tzinfo=UTC)
    )
    assert not await image._async_scheduled_update(
        datetime(2026, 10, 27, 8, 10, 59, tzinfo=UTC)
    )
    called.return_value = None
    assert not await image._async_scheduled_update(
        datetime(2026, 10, 27, 8, 5, 59, tzinfo=UTC)
    )
    assert called.await_count == 3


@pytest.mark.asyncio
async def test_daily_sensor_lifecycle():
    hass = SimpleNamespace()
    sensor = HungarometWeatherDailySensor(hass, "Daily", 0, "°C", "t")

    await sensor.async_added_to_hass()
    assert sensor._added is True

    await sensor.async_will_remove_from_hass()
    assert sensor._added is False


@pytest.mark.asyncio
async def test_hourly_sensor_lifecycle():
    hass = SimpleNamespace()
    sensor = HungarometWeatherHourlySensor(hass, "Hourly", 0, "°C", "t")

    await sensor.async_added_to_hass()
    assert sensor._added is True

    await sensor.async_will_remove_from_hass()
    assert sensor._added is False


@pytest.mark.asyncio
async def test_ten_minute_sensor_lifecycle():
    hass = SimpleNamespace()
    sensor = HungarometWeatherTenMinutesSensor(hass, "Ten", 0, "°C", "t")

    await sensor.async_added_to_hass()
    assert sensor._added is True

    await sensor.async_will_remove_from_hass()
    assert sensor._added is False


@pytest.mark.asyncio
async def test_daily_sensor_fetches_with_configured_distance(monkeypatch):
    hass = SimpleNamespace(
        async_add_executor_job=AsyncMock(return_value=({"t": 1.0}, None))
    )
    sensor = HungarometWeatherDailySensor(
        hass, "Daily", None, "°C", "t", 42, (47.5, 21.6)
    )
    sensor._added = True
    sensor.async_write_ha_state = MagicMock()

    await sensor.async_update_data()

    hass.async_add_executor_job.assert_awaited_once_with(
        process_daily_data, hass, 42, (47.5, 21.6)
    )


@pytest.mark.parametrize(
    ("sensor_type", "process"),
    [
        ("daily", process_daily_data),
        ("hourly", process_hourly_data),
        ("ten_minutes", process_ten_minutes_data),
    ],
)
@pytest.mark.asyncio
async def test_station_info_sensor_fetches_own_feed_with_distance(
    sensor_type, process
):
    hass = SimpleNamespace(
        async_add_executor_job=AsyncMock(return_value=({}, ["AAA"]))
    )
    sensor = HungarometStationInfoSensor(
        hass, "Stations", [], sensor_type, distance_km=35, location=(47.5, 21.6)
    )
    sensor._added = True
    sensor.async_write_ha_state = MagicMock()

    await sensor.async_update_data()

    hass.async_add_executor_job.assert_awaited_once_with(
        process, hass, 35, (47.5, 21.6)
    )


@pytest.mark.parametrize(
    "entity",
    [
        HungarometWeatherDailySensor(SimpleNamespace(), "D", 0, None, "t"),
        HungarometWeatherHourlySensor(SimpleNamespace(), "H", 0, None, "t"),
        HungarometWeatherTenMinutesSensor(SimpleNamespace(), "T", 0, None, "t"),
        HungarometStationInfoSensor(SimpleNamespace(), "S", [], "daily"),
        HungarometRadarImage(SimpleNamespace(data={}, bus=DummyBus())),
    ],
)
def test_device_identifiers_include_domain(entity):
    (identifier,) = entity.device_info["identifiers"]
    assert identifier[0] == "hungaromet_weather_and_forecast"


@pytest.mark.parametrize(
    ("sensor_type", "feed_sensor"),
    [
        ("daily", HungarometWeatherDailySensor),
        ("hourly", HungarometWeatherHourlySensor),
        ("ten_minutes", HungarometWeatherTenMinutesSensor),
    ],
)
def test_station_info_sensor_shares_its_feeds_device(sensor_type, feed_sensor):
    stations = HungarometStationInfoSensor(SimpleNamespace(), "S", [], sensor_type)
    feed = feed_sensor(SimpleNamespace(), "F", 0, None, "t")

    assert stations.device_info == feed.device_info
    assert stations.unique_id == f"hungaromet_weather_station_info_{sensor_type}"


def test_daily_measurement_date_is_a_date():
    sensor = HungarometWeatherDailySensor(SimpleNamespace(), "Idő", "2024-01-01", None, "time")

    assert sensor.device_class == SensorDeviceClass.DATE
    assert sensor.native_value == date(2024, 1, 1)
    sensor._state = "bad"
    assert sensor.native_value is None


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
        ("v", "m", SensorDeviceClass.DISTANCE),
        ("sr", "J/cm²", None),
        ("sg", "nSv/h", None),
    ],
)
def test_station_sensor_device_classes(key, unit, device_class):
    for cls in (
        HungarometWeatherDailySensor,
        HungarometWeatherHourlySensor,
        HungarometWeatherTenMinutesSensor,
    ):
        assert cls(SimpleNamespace(), "X", 1.0, unit, key).device_class == device_class


def test_wind_directions_use_the_wind_direction_class():
    sensor = HungarometWeatherHourlySensor(SimpleNamespace(), "X", 180.0, "°", "fxd")

    assert sensor.device_class == WIND_DIRECTION
