"""The preferred regeneration time.

The device keeps a wall-clock time. The service carries it as local digits labelled
UTC, so no zone arithmetic happens here in either direction: the hour shown is the hour
the device regenerates at, and the hour written is the hour it will use.
"""

from datetime import time

from homeassistant.components.time import TimeEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import AquastillaConfigEntry
from .controls import async_run
from .coordinator import AquastillaCoordinator
from .entity import GatedEntity, setup_device_entities
from .gates import Command
from .models import DeviceEventType


async def async_setup_entry(
    hass: HomeAssistant,
    entry: AquastillaConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    setup_device_entities(
        entry,
        async_add_entities,
        lambda coordinator, uuid: [RegenerationTime(coordinator, uuid)],
    )


class RegenerationTime(GatedEntity, TimeEntity):
    """The time of day the device prefers to regenerate at."""

    _attr_translation_key = "regeneration_time"
    _attr_entity_category = EntityCategory.CONFIG
    _command = Command.SETTING

    def __init__(self, coordinator: AquastillaCoordinator, uuid: str) -> None:
        super().__init__(coordinator, uuid)
        self._attr_unique_id = f"{uuid}_regeneration_time"

    @property
    def native_value(self) -> time | None:
        return self.device_data.settings.regeneration_time

    async def async_set_value(self, value: time) -> None:
        client, uuid = self.coordinator.client, self._uuid
        await async_run(
            self.coordinator,
            uuid,
            Command.SETTING,
            lambda: client.set_regeneration_time(uuid, value),
            event_type=DeviceEventType.SET_PREFERRED_REGENERATION_TIME,
        )
