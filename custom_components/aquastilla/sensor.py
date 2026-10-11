"""Sensors read from the coordinator's data. Which ones exist is in ``descriptions``."""

from collections.abc import Mapping
from datetime import datetime
from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.typing import StateType

from . import AquastillaConfigEntry
from .coordinator import AquastillaCoordinator
from .entity import AquastillaEntity, setup_device_entities
from .sensor_description import AquastillaSensorDescription
from .sensors_cycle import CYCLE_SENSORS
from .sensors_diagnostic import DIAGNOSTIC_SENSORS
from .sensors_history import HISTORY_SENSORS
from .sensors_push import FirmwareUpdateProgress, LastNotification
from .sensors_regeneration import REGENERATION_SENSORS
from .sensors_salt import SALT_SENSORS
from .sensors_water import WATER_SENSORS

# One line per group of sensors.
SENSORS: tuple[AquastillaSensorDescription, ...] = (
    *SALT_SENSORS,
    *CYCLE_SENSORS,
    *REGENERATION_SENSORS,
    *HISTORY_SENSORS,
    *DIAGNOSTIC_SENSORS,
    *WATER_SENSORS,
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: AquastillaConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the sensors of every device, and of devices that appear later."""

    def build(coordinator: AquastillaCoordinator, uuid: str) -> list[SensorEntity]:
        entities: list[SensorEntity] = [
            AquastillaSensor(coordinator, uuid, description) for description in SENSORS
        ]
        if coordinator.push is not None:
            entities += [
                FirmwareUpdateProgress(coordinator, uuid, coordinator.push),
                LastNotification(coordinator, uuid, coordinator.push),
            ]
        return entities

    setup_device_entities(entry, async_add_entities, build)


class AquastillaSensor(AquastillaEntity, SensorEntity):
    """One reading of one device."""

    entity_description: AquastillaSensorDescription

    def __init__(
        self,
        coordinator: AquastillaCoordinator,
        uuid: str,
        description: AquastillaSensorDescription,
    ) -> None:
        super().__init__(coordinator, uuid)
        self.entity_description = description
        self._attr_unique_id = f"{uuid}_{description.key}"

    @property
    def native_unit_of_measurement(self) -> str | None:
        # Home Assistant asks for the unit even while the entity is unavailable, and a
        # device the account no longer answers for has no data to take it from.
        if (
            self.entity_description.unit_fn is not None
            and self._uuid in self.coordinator.data.devices
        ):
            return self.entity_description.unit_fn(self.device_data)
        return self.entity_description.native_unit_of_measurement

    @property
    def native_value(self) -> StateType | datetime:
        return self.entity_description.value_fn(self.device_data)

    @property
    def extra_state_attributes(self) -> Mapping[str, Any] | None:
        if self.entity_description.attributes_fn is None:
            return None
        return self.entity_description.attributes_fn(self.device_data)
