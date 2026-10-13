"""Binary sensors: connectivity, regeneration and the three alarm states.

The service has no field that says an alarm is active. The three alarms are derived,
and how well that matches the app is to be confirmed against real traffic (#54):

* salt: the salt level is at or below the threshold the user set;
* leak and head error: the history holds an event of that kind from the last day.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime

from homeassistant.components.binary_sensor import (
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from . import AquastillaConfigEntry
from .const import ALARM_WINDOW, CLOCK_SKEW
from .coordinator import AquastillaCoordinator, DeviceData
from .entity import AquastillaEntity, setup_device_entities
from .gates import flow_closed, is_regenerating
from .models import VendorEvent
from .push import PushHub

try:
    from homeassistant.components.binary_sensor.const import BinarySensorDeviceClass
except ImportError:  # pragma: no cover  # Home Assistant before 2026.10
    from homeassistant.components.binary_sensor import (  # type: ignore[attr-defined]
        BinarySensorDeviceClass,
    )


@dataclass(frozen=True, kw_only=True)
class AquastillaBinarySensorDescription(BinarySensorEntityDescription):
    """A binary sensor derived from one device's data. ``None`` means unknown."""

    is_on_fn: Callable[[DeviceData], bool | None]
    # For states that also depend on the notifications the device has sent.
    events_fn: Callable[[DeviceData, Sequence[VendorEvent]], bool | None] | None = None


def _water_flow(data: DeviceData) -> bool | None:
    """Open, unless the setting says closed or the head reports the closed cycle.

    An alarm closes the valve without touching the setting (seen on a real leak
    alarm), so the cycle is the only place that shows it."""
    closed = flow_closed(data)
    return None if closed is None else not closed


def _leak(data: DeviceData, events: Sequence[VendorEvent]) -> bool | None:
    """A leak that nobody has acknowledged, or a recent leak in the device's history.

    A critical notification of the last day keeps the alarm on until it is seen, in the
    vendor's app or anywhere else: marking it seen is how the app clears it, and the
    history keeps its entry for the day, so it alone would hold a settled alarm up. The
    history decides only when there is no critical notification to go by (the log was
    not read, or the notification is older than a day)."""
    critical = [
        e for e in events if (e.kind or "").lower() == "critical" and _recent(data.instant(e.date))
    ]
    if critical:
        return any(e.seen is not True for e in critical)
    return _recent(data.instant(data.device.history.flood))


def _salt_low(data: DeviceData) -> bool | None:
    level, threshold = data.state.salt_percent, data.settings.salt_alarm_level
    if level is None or threshold is None:
        return None
    return level <= threshold


def _recent(moment: datetime | None) -> bool:
    if moment is None:
        return False
    return -CLOCK_SKEW <= dt_util.utcnow() - moment <= ALARM_WINDOW


BINARY_SENSORS: tuple[AquastillaBinarySensorDescription, ...] = (
    AquastillaBinarySensorDescription(
        key="online",
        translation_key="online",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        is_on_fn=lambda data: data.state.is_online,
    ),
    AquastillaBinarySensorDescription(
        key="regenerating",
        translation_key="regenerating",
        device_class=BinarySensorDeviceClass.RUNNING,
        is_on_fn=is_regenerating,
    ),
    AquastillaBinarySensorDescription(
        key="water_flow",
        translation_key="water_flow",
        is_on_fn=_water_flow,
    ),
    AquastillaBinarySensorDescription(
        key="firmware_updating",
        translation_key="firmware_updating",
        device_class=BinarySensorDeviceClass.UPDATE,
        is_on_fn=lambda data: data.state.is_update,
    ),
    AquastillaBinarySensorDescription(
        key="remote_service",
        translation_key="remote_service",
        is_on_fn=lambda data: data.settings.service_mode,
    ),
    AquastillaBinarySensorDescription(
        key="salt_low",
        translation_key="salt_low",
        device_class=BinarySensorDeviceClass.PROBLEM,
        is_on_fn=_salt_low,
    ),
    AquastillaBinarySensorDescription(
        key="leak",
        translation_key="leak",
        device_class=BinarySensorDeviceClass.PROBLEM,
        is_on_fn=lambda data: _leak(data, ()),
        events_fn=_leak,
    ),
    AquastillaBinarySensorDescription(
        key="head_error",
        translation_key="head_error",
        device_class=BinarySensorDeviceClass.PROBLEM,
        is_on_fn=lambda data: _recent(data.instant(data.device.history.head_error)),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: AquastillaConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the binary sensors of every device, and of devices that appear later."""

    def build(coordinator: AquastillaCoordinator, uuid: str) -> list[BinarySensorEntity]:
        entities: list[BinarySensorEntity] = []
        for description in BINARY_SENSORS:
            sensor = AquastillaBinarySensor(coordinator, uuid, description)
            sensor.hub = coordinator.push
            entities.append(sensor)
        if coordinator.push is not None:
            entities.append(CloudConnection(coordinator, uuid, coordinator.push))
        return entities

    setup_device_entities(entry, async_add_entities, build)


class AquastillaBinarySensor(AquastillaEntity, BinarySensorEntity):
    """One yes or no about one device."""

    entity_description: AquastillaBinarySensorDescription

    def __init__(
        self,
        coordinator: AquastillaCoordinator,
        uuid: str,
        description: AquastillaBinarySensorDescription,
    ) -> None:
        super().__init__(coordinator, uuid)
        self.entity_description = description
        self._attr_unique_id = f"{uuid}_{description.key}"

    hub: PushHub | None = None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if self.hub is not None and self.entity_description.events_fn is not None:
            self.async_on_remove(self.hub.add_listener(self.async_write_ha_state))

    @property
    def is_on(self) -> bool | None:
        description = self.entity_description
        if description.events_fn is not None and self.hub is not None:
            return description.events_fn(self.device_data, self.hub.events_of(self._uuid))
        return description.is_on_fn(self.device_data)


class CloudConnection(AquastillaEntity, BinarySensorEntity):
    """Whether the push channel is up. Polling goes on either way, so this is a hint
    about latency and not about the data."""

    _attr_translation_key = "cloud_connection"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: AquastillaCoordinator, uuid: str, hub: PushHub) -> None:
        super().__init__(coordinator, uuid)
        self._hub = hub
        self._attr_unique_id = f"{uuid}_cloud_connection"

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(self._hub.add_listener(self.async_write_ha_state))

    @property
    def is_on(self) -> bool:
        return self._hub.connected
