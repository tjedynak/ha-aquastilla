"""The device's notifications as a native event entity (ADR-13).

Automations filter on the type, not on the free text of ``title`` and ``body``.
"""

from homeassistant.components.event import EventEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import AquastillaConfigEntry
from .coordinator import AquastillaCoordinator, DeviceData
from .entity import AquastillaEntity, setup_device_entities
from .models import VendorEvent
from .push import PushHub

OTHER = "other"
# The vendor's ``type`` values and what they are called here.
EVENT_TYPES: dict[str, str] = {
    "normal": "normal",
    "critical": "critical",
    "forcedbyservice": "forced_by_service",
    "forcedbyuser": "forced_by_user",
}


def event_type(vendor_type: str | None) -> str:
    """The Home Assistant event type of a vendor type. A type we do not know is ``other``."""
    key = (vendor_type or "").replace("_", "").replace(" ", "").lower()
    return EVENT_TYPES.get(key, OTHER)


def event_attributes(event: VendorEvent, data: DeviceData) -> dict[str, str | None]:
    """The attributes of a notification. Its ``date`` is local time of the device with a
    UTC label, like the other device timestamps (seen on a real leak alarm: 18:02 local,
    pushed at 16:02 UTC), so it is read in the device's zone."""
    moment = data.instant(event.date)
    return {
        "event_id": event.event_id,
        "title": event.title,
        "body": event.body,
        "date": None if moment is None else moment.isoformat(),
        "vendor_type": event.kind,
    }


async def async_setup_entry(
    hass: HomeAssistant,
    entry: AquastillaConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    hub = entry.runtime_data.push
    if hub is None:  # the channel was not started
        return
    setup_device_entities(
        entry,
        async_add_entities,
        lambda coordinator, uuid: [AquastillaNotification(coordinator, uuid, hub)],
    )


class AquastillaNotification(AquastillaEntity, EventEntity):
    """Fires once for every notification the device sends."""

    _attr_translation_key = "notification"

    def __init__(self, coordinator: AquastillaCoordinator, uuid: str, hub: PushHub) -> None:
        super().__init__(coordinator, uuid)
        self._hub = hub
        self._attr_unique_id = f"{uuid}_notification"
        self._attr_event_types = [*dict.fromkeys(EVENT_TYPES.values()), OTHER]

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(self._hub.add_event_listener(self._handle))

    @callback
    def _handle(self, uuid: str, event: VendorEvent) -> None:
        if uuid != self._uuid:
            return
        self._trigger_event(event_type(event.kind), event_attributes(event, self.device_data))
        self.async_write_ha_state()
