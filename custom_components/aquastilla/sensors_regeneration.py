"""When the head last regenerated and when it is expected to regenerate next."""

from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.const import EntityCategory

from .sensor_description import AquastillaSensorDescription

REGENERATION_SENSORS: tuple[AquastillaSensorDescription, ...] = (
    AquastillaSensorDescription(
        key="last_regeneration",
        translation_key="last_regeneration",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: data.instant(data.device.history.regeneration),
    ),
    AquastillaSensorDescription(
        key="next_regeneration",
        translation_key="next_regeneration",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda data: data.instant(data.state.expected_regeneration),
    ),
)
