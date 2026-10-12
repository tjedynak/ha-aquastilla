"""The vacation mode switch.

The state is always what the device reports. Turning the switch does not flip it: the
command is sent, the switch keeps its value until the device acknowledges, and the data
is read again either way. A command that is never confirmed therefore ends with the
switch showing reality, not the wish.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import AquastillaConfigEntry
from .controls import async_run, refusal
from .coordinator import AquastillaCoordinator
from .entity import AquastillaEntity, setup_device_entities
from .gates import Command, blocked_reason
from .models import DeviceEventType, NotificationSettings


async def async_setup_entry(
    hass: HomeAssistant,
    entry: AquastillaConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    setup_device_entities(
        entry,
        async_add_entities,
        lambda coordinator, uuid: [
            VacationModeSwitch(coordinator, uuid),
            DefaultDeviceSwitch(coordinator, uuid),
            *(NotificationSwitch(coordinator, uuid, d) for d in NOTIFICATIONS),
        ],
    )


@dataclass(frozen=True, kw_only=True)
class NotificationSwitchDescription(SwitchEntityDescription):
    """One delivery flag of one vendor-side alarm notification."""

    alarm: Literal["salt", "flood"]
    channel: Literal["push", "email"]


# What the vendor sends to the owner's phone and mailbox. This does not change what the
# alarm detects, only whether it is announced (F0.5), so it is a plain setting.
NOTIFICATIONS: tuple[NotificationSwitchDescription, ...] = tuple(
    NotificationSwitchDescription(
        key=f"{alarm}_alarm_{channel}",
        translation_key=f"{alarm}_alarm_{channel}",
        entity_category=EntityCategory.CONFIG,
        alarm=alarm,
        channel=channel,
    )
    for alarm in ("salt", "flood")
    for channel in ("push", "email")
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
    def extra_state_attributes(self) -> Mapping[str, Any]:
        # Turning it on is what the app blocks during a regeneration; turning it off is not.
        command = Command.VACATION_MODE if self.is_on else Command.VACATION_MODE_ON
        reason = blocked_reason(self.device_data, command)
        pending = self.coordinator.is_command_pending(self._uuid, DeviceEventType.SET_VACATION_MODE)
        return {"pending": pending, "blocked_reason": None if reason is None else str(reason)}

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._set(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._set(False)

    async def _set(self, enabled: bool) -> None:
        client, uuid = self.coordinator.client, self._uuid
        await async_run(
            self.coordinator,
            uuid,
            Command.VACATION_MODE_ON if enabled else Command.VACATION_MODE,
            lambda: client.set_vacation_mode(uuid, enabled),
            event_type=DeviceEventType.SET_VACATION_MODE,
        )


class NotificationSwitch(AquastillaEntity, SwitchEntity):
    """Whether the vendor sends one kind of alarm by push or by e-mail."""

    entity_description: NotificationSwitchDescription

    def __init__(
        self,
        coordinator: AquastillaCoordinator,
        uuid: str,
        description: NotificationSwitchDescription,
    ) -> None:
        super().__init__(coordinator, uuid)
        self.entity_description = description
        self._attr_unique_id = f"{uuid}_{description.key}"

    def _settings(self) -> NotificationSettings:
        device = self.device_data.device
        return (
            device.salt_notifications
            if self.entity_description.alarm == "salt"
            else device.flood_notifications
        )

    @property
    def is_on(self) -> bool | None:
        settings = self._settings()
        return settings.push if self.entity_description.channel == "push" else settings.email

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._set(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._set(False)

    async def _set(self, enabled: bool) -> None:
        description = self.entity_description
        client, uuid, coordinator = self.coordinator.client, self._uuid, self.coordinator
        send = (
            client.set_salt_notifications
            if description.alarm == "salt"
            else client.set_flood_notifications
        )
        flag = "pushNotification" if description.channel == "push" else "emailNotification"
        async with coordinator.write_lock(uuid, f"notification_{description.alarm}"):
            # Read inside the lock: a write that just finished has refreshed the list.
            settings = self._settings()
            if settings.push is None or settings.email is None:
                raise refusal("settings_unknown")  # the other flag is sent back as it is
            payload = {**settings.raw, flag: enabled}
            await async_run(
                coordinator,
                uuid,
                Command.SERVICE,
                lambda: send(uuid, payload),
                refresh=coordinator.async_refresh_device_list,
            )


class DefaultDeviceSwitch(AquastillaEntity, SwitchEntity):
    """Which device the vendor's app opens first. It can only be moved, not cleared."""

    _attr_translation_key = "default_device"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: AquastillaCoordinator, uuid: str) -> None:
        super().__init__(coordinator, uuid)
        self._attr_unique_id = f"{uuid}_default_device"

    @property
    def is_on(self) -> bool | None:
        return self.device_data.device.is_default

    async def async_turn_on(self, **kwargs: Any) -> None:
        client, uuid, coordinator = self.coordinator.client, self._uuid, self.coordinator
        await async_run(
            coordinator,
            uuid,
            Command.SERVICE,
            lambda: client.set_default_device(uuid),
            refresh=coordinator.async_refresh_device_list,
        )

    async def async_turn_off(self, **kwargs: Any) -> None:
        # The service has no call for this: another device has to become the default.
        raise refusal("default_device_cannot_be_cleared")
