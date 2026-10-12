"""The device's note."""

from homeassistant.components.text import TextEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import AquastillaConfigEntry
from .controls import async_run
from .coordinator import AquastillaCoordinator
from .entity import AquastillaEntity, setup_device_entities
from .gates import Command

NOTE_MAX_LENGTH = 255


async def async_setup_entry(
    hass: HomeAssistant,
    entry: AquastillaConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    setup_device_entities(
        entry,
        async_add_entities,
        lambda coordinator, uuid: [DeviceNote(coordinator, uuid)],
    )


class DeviceNote(AquastillaEntity, TextEntity):
    """The free-text note of a device. An empty value removes it."""

    _attr_translation_key = "note"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_native_max = NOTE_MAX_LENGTH
    # A note can hold private text, and the recorder would keep it in its history.
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: AquastillaCoordinator, uuid: str) -> None:
        super().__init__(coordinator, uuid)
        self._attr_unique_id = f"{uuid}_note"

    @property
    def native_value(self) -> str | None:
        # The vendor has no limit, Home Assistant's state has one: cut, never raise.
        return (self.device_data.device.note or "")[:NOTE_MAX_LENGTH]

    async def async_set_value(self, value: str) -> None:
        client, uuid, coordinator = self.coordinator.client, self._uuid, self.coordinator
        text = value.strip()
        await async_run(
            coordinator,
            uuid,
            Command.SERVICE,
            (lambda: client.set_note(uuid, text)) if text else (lambda: client.delete_note(uuid)),
            refresh=coordinator.async_refresh_device_list,
        )
