"""Buttons: the one-shot commands.

Two of them are guarded and off until the owner enables them:

* advancing the cycle moves the head at once, whatever it was doing;
* closing the water flow cannot be undone from here. The app says reopening is only
  possible at the device. It is a button, never a switch or a valve, so it cannot be
  toggled by a scene, a voice assistant or a dashboard that shows it as a toggle.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import AquastillaConfigEntry
from .api import AquastillaClient
from .controls import async_run
from .coordinator import AquastillaCoordinator
from .entity import GatedEntity, setup_device_entities
from .gates import Command
from .models import DeviceEventType


@dataclass(frozen=True, kw_only=True)
class AquastillaButtonDescription(ButtonEntityDescription):
    """A command: how it is gated, which acknowledgement it waits for and how it is sent."""

    command: Command
    event_type: DeviceEventType
    press_fn: Callable[[AquastillaClient, str], Awaitable[None]]


BUTTONS: tuple[AquastillaButtonDescription, ...] = (
    AquastillaButtonDescription(
        key="force_regeneration",
        translation_key="force_regeneration",
        entity_registry_enabled_default=False,
        command=Command.FORCE_REGENERATION,
        event_type=DeviceEventType.SET_REGENERATION_VACATION_MODE,
        press_fn=lambda client, uuid: client.force_regeneration(uuid),
    ),
    AquastillaButtonDescription(
        key="delay_regeneration",
        translation_key="delay_regeneration",
        entity_registry_enabled_default=False,
        command=Command.DELAY_REGENERATION,
        event_type=DeviceEventType.SET_DELAY_REGENERATION,
        press_fn=lambda client, uuid: client.delay_regeneration(uuid),
    ),
    # Guarded: the head moves immediately.
    AquastillaButtonDescription(
        key="advance_cycle",
        translation_key="advance_cycle",
        entity_category=EntityCategory.CONFIG,
        entity_registry_enabled_default=False,
        command=Command.ADVANCE_CYCLE,
        event_type=DeviceEventType.SET_DEVICE_MODE,
        press_fn=lambda client, uuid: client.advance_cycle(uuid),
    ),
    # Guarded: cannot be undone remotely.
    AquastillaButtonDescription(
        key="close_water_flow",
        translation_key="close_water_flow",
        entity_category=EntityCategory.CONFIG,
        entity_registry_enabled_default=False,
        command=Command.CLOSE_WATER_FLOW,
        event_type=DeviceEventType.SET_WATER_FLOW,
        press_fn=lambda client, uuid: client.close_water_flow(uuid),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: AquastillaConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    setup_device_entities(
        entry,
        async_add_entities,
        lambda coordinator, uuid: (
            AquastillaButton(coordinator, uuid, description) for description in BUTTONS
        ),
    )


class AquastillaButton(GatedEntity, ButtonEntity):
    """One command of one device."""

    entity_description: AquastillaButtonDescription

    def __init__(
        self,
        coordinator: AquastillaCoordinator,
        uuid: str,
        description: AquastillaButtonDescription,
    ) -> None:
        super().__init__(coordinator, uuid)
        self.entity_description = description
        self._command = description.command
        self._attr_unique_id = f"{uuid}_{description.key}"

    async def async_press(self) -> None:
        description = self.entity_description
        client = self.coordinator.client
        uuid = self._uuid
        await async_run(
            self.coordinator,
            uuid,
            description.command,
            lambda: description.press_fn(client, uuid),
            event_type=description.event_type,
        )
