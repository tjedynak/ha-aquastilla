"""Salt and the volume left until the next regeneration."""

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfTime, UnitOfVolume

from .sensor_description import AquastillaSensorDescription

SALT_SENSORS: tuple[AquastillaSensorDescription, ...] = (
    AquastillaSensorDescription(
        key="salt_level",
        translation_key="salt_level",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=lambda data: data.state.salt_percent,
    ),
    AquastillaSensorDescription(
        key="salt_days_left",
        translation_key="salt_days_left",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.DAYS,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: data.state.salt_days,
        attributes_fn=lambda data: {"max_days": data.state.salt_days_max},
    ),
    AquastillaSensorDescription(
        key="water_left",
        translation_key="water_left",
        device_class=SensorDeviceClass.WATER,
        # The vendor app shows cubic metres and multiplies by 1000 for litres. Home
        # Assistant converts to the unit the user prefers. Unconfirmed until real
        # traffic is captured (#63).
        native_unit_of_measurement=UnitOfVolume.CUBIC_METERS,
        suggested_display_precision=2,
        value_fn=lambda data: data.state.water_left,
    ),
    AquastillaSensorDescription(
        key="water_left_max",
        translation_key="water_left_max",
        device_class=SensorDeviceClass.WATER,
        native_unit_of_measurement=UnitOfVolume.CUBIC_METERS,
        suggested_display_precision=2,
        entity_category=EntityCategory.DIAGNOSTIC,
        # A capacity does not change from one reading to the next; off until wanted.
        entity_registry_enabled_default=False,
        value_fn=lambda data: data.state.water_left_max,
    ),
)
