"""Read-only readings of the device's settings.

The hardness is not writable yet: the app puts no range on it and the backend's limits
are unknown, so a wrong write could not be told from a typing mistake (#65).
"""

from homeassistant.components.sensor import SensorStateClass
from homeassistant.const import EntityCategory

from .sensor_description import AquastillaSensorDescription

SETTINGS_SENSORS: tuple[AquastillaSensorDescription, ...] = (
    AquastillaSensorDescription(
        key="water_hardness",
        translation_key="water_hardness",
        entity_category=EntityCategory.DIAGNOSTIC,
        state_class=SensorStateClass.MEASUREMENT,
        unit_fn=lambda data: data.settings.water_hardness_unit,
        value_fn=lambda data: data.settings.water_hardness,
    ),
)
