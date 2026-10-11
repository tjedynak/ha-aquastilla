"""When things last happened to the device, from its history.

The last regeneration is the sensor of the regeneration group, which reads the same
field, so it is not repeated here: together the five timestamps of the app are present.
"""

from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.const import EntityCategory

from .sensor_description import AquastillaSensorDescription

HISTORY_SENSORS: tuple[AquastillaSensorDescription, ...] = (
    AquastillaSensorDescription(
        key="last_salt_refill",
        translation_key="last_salt_refill",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: data.instant(data.device.history.salt_replenishment),
    ),
    AquastillaSensorDescription(
        key="last_inspection",
        translation_key="last_inspection",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: data.instant(data.device.history.inspection),
    ),
    AquastillaSensorDescription(
        key="last_leak",
        translation_key="last_leak",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: data.instant(data.device.history.flood),
    ),
    AquastillaSensorDescription(
        key="last_head_error",
        translation_key="last_head_error",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: data.instant(data.device.history.head_error),
    ),
)
