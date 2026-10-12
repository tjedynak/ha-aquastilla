"""The vacation mode switch.

The state is always what the device reports. Turning the switch does not flip it: the
command is sent, the switch keeps its value until the device acknowledges, and the data
is read again either way. A command that is never confirmed therefore ends with the
switch showing reality, not the wish.
"""

from collections.abc import Mapping
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import AquastillaConfigEntry
from .controls import async_run
from .coordinator import AquastillaCoordinator
from .entity import AquastillaEntity, setup_device_entities
from .gates import Command, blocked_reason
from .models import DeviceEventType


async def async_setup_entry(
    hass: HomeAssistant,
    entry: AquastillaConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    setup_device_entities(
        entry,
        async_add_entities,
        lambda coordinator, uuid: [VacationModeSwitch(coordinator, uuid)],
    )


class VacationModeSwitch(AquastillaEntity, SwitchEntity):
    """Vacation mode of one device."""

    _attr_translation_key = "vacation_mode"

    def __init__(self, coordinator: AquastillaCoordinator, uuid: str) -> None:
        super().__init__(coordinator, uuid)
        self._attr_unique_id = f"{uuid}_vacation_mode"

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        # The pending mark appears and disappears without the data changing.
        self.async_on_remove(self.coordinator.add_command_listener(self.async_write_ha_state))

    @property
    def is_on(self) -> bool | None:
        return self.device_data.settings.vacation_mode

    @property
    def available(self) -> bool:
        if not super().available:
            return False
        return blocked_reason(self.device_data, Command.VACATION_MODE) is None

    @property
    def extra_state_attributes(self) -> Mapping[str, Any]:
        pending = self.coordinator.is_command_pending(self._uuid, DeviceEventType.SET_VACATION_MODE)
        return {"pending": pending}

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._set(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._set(False)

    async def _set(self, enabled: bool) -> None:
        client, uuid = self.coordinator.client, self._uuid
        await async_run(
            self.coordinator,
            uuid,
            Command.VACATION_MODE,
            lambda: client.set_vacation_mode(uuid, enabled),
            event_type=DeviceEventType.SET_VACATION_MODE,
        )
