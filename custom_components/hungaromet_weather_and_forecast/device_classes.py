"""Device classes of the weather station sensors.

Home Assistant's History draws sensors on one graph only when they share the
unit and the device class, so the station temperatures, for example, need the
temperature class to appear next to other temperatures.
"""

from homeassistant.components.sensor import SensorDeviceClass

# Home Assistant versions before the wind direction class show the bearings
# without a device class
WIND_DIRECTION = getattr(SensorDeviceClass, "WIND_DIRECTION", None)

# By the feed's column name, without the "average_" prefix
DEVICE_CLASSES = {
    **dict.fromkeys(
        ("t", "ta", "tn", "tx", "tsn", "tsn24", "tviz", "et5", "et10", "et20", "et50", "et100"),
        SensorDeviceClass.TEMPERATURE,
    ),
    # Evaporation and the water balance are millimetres of water too
    **dict.fromkeys(("r", "rau", "upe", "water_balance"), SensorDeviceClass.PRECIPITATION),
    "u": SensorDeviceClass.HUMIDITY,
    "p": SensorDeviceClass.PRESSURE,
    "p0": SensorDeviceClass.ATMOSPHERIC_PRESSURE,
    **dict.fromkeys(("f", "fs", "fx"), SensorDeviceClass.WIND_SPEED),
    **dict.fromkeys(("fd", "fsd", "fxd"), WIND_DIRECTION),
    "v": SensorDeviceClass.DISTANCE,
}


def device_class_for(base_key: str) -> SensorDeviceClass | None:
    """Return the device class of a station measurement, if it has one."""
    return DEVICE_CLASSES.get(base_key)
