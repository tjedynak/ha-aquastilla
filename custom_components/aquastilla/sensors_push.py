"""Sensors that exist only because of the push channel: update progress, last notification."""

from collections.abc import Mapping
from typing import Any

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.const import PERCENTAGE, EntityCategory
from homeassistant.core import callback

from .coordinator import AquastillaCoordinator
from .entity import AquastillaEntity
from .push import PushHub

STATE_MAX_LENGTH = 255
LOG_ATTRIBUTE_SIZE = 10


class _PushSensor(AquastillaEntity, SensorEntity):
    def __init__(self, coordinator: AquastillaCoordinator, uuid: str, hub: PushHub) -> None:
        super().__init__(coordinator, uuid)
        self._hub = hub

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(self._hub.add_listener(self._changed))

    @callback
    def _changed(self) -> None:
        self.async_write_ha_state()


class FirmwareUpdateProgress(_PushSensor):
    """How far a firmware update is. Zero while the device is not updating."""

    _attr_translation_key = "firmware_update_progress"
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: AquastillaCoordinator, uuid: str, hub: PushHub) -> None:
        super().__init__(coordinator, uuid, hub)
        self._attr_unique_id = f"{uuid}_firmware_update_progress"

    @property
    def native_value(self) -> float | None:
        state = self.device_data.state
        if state.is_update is None:
            return None
        if not state.is_update:
            return 0.0  # idle, whatever a push said earlier
        pushed = self._hub.progress.get(self._uuid)
        return pushed if pushed is not None else state.firmware_update_percentage


class LastNotification(_PushSensor):
    """The title of the newest notification, with the recent ones as attributes."""

    _attr_translation_key = "last_notification"

    def __init__(self, coordinator: AquastillaCoordinator, uuid: str, hub: PushHub) -> None:
        super().__init__(coordinator, uuid, hub)
        self._attr_unique_id = f"{uuid}_last_notification"

    @property
    def native_value(self) -> str | None:
        events = self._hub.events_of(self._uuid)
        if not events or events[0].title is None:
            return None
        return events[0].title[:STATE_MAX_LENGTH]

    @property
    def extra_state_attributes(self) -> Mapping[str, Any]:
        events = self._hub.events_of(self._uuid)[:LOG_ATTRIBUTE_SIZE]
        data = self.device_data
        return {
            "unseen": self._hub.unseen,
            "events": [
                {
                    "type": event.kind,
                    "title": event.title,
                    "body": event.body,
                    "date": None if (m := data.instant(event.date)) is None else m.isoformat(),
                    "seen": event.seen,
                }
                for event in events
            ],
        }


class UnreadNotifications(_PushSensor):
    """How many notifications the account has not marked as seen in the vendor app."""

    _attr_translation_key = "unread_notifications"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: AquastillaCoordinator, uuid: str, hub: PushHub) -> None:
        super().__init__(coordinator, uuid, hub)
        self._attr_unique_id = f"{uuid}_unread_notifications"

    @property
    def native_value(self) -> int | None:
        return self._hub.unseen
