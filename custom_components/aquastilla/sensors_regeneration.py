"""When the head last regenerated and when it is expected to regenerate next."""

from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass
from homeassistant.const import PERCENTAGE, EntityCategory

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
        key="regeneration_progress",
        translation_key="regeneration_progress",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        # Only meaningful while a regeneration runs; the device reports it always.
        value_fn=lambda data: data.state.regeneration_percent,
    ),
    AquastillaSensorDescription(
        key="next_regeneration",
        translation_key="next_regeneration",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda data: data.instant(data.state.expected_regeneration),
    ),
)
