"""Readings that help to debug: the device's clock, our last update, its address."""

from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.const import EntityCategory

from .sensor_description import AquastillaSensorDescription

DIAGNOSTIC_SENSORS: tuple[AquastillaSensorDescription, ...] = (
    AquastillaSensorDescription(
        key="device_clock",
        translation_key="device_clock",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: data.state.timestamp,
    ),
    AquastillaSensorDescription(
        key="last_update",
        translation_key="last_update",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: data.updated_at,
    ),
    AquastillaSensorDescription(
        key="ip_address",
        translation_key="ip_address",
        entity_category=EntityCategory.DIAGNOSTIC,
        # An address on the owner's network: off until asked for.
        entity_registry_enabled_default=False,
        value_fn=lambda data: data.device.ip_address,
    ),
)
