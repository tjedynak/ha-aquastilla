"""Binary sensors: connectivity, regeneration and the three alarm states.

The service has no field that says an alarm is active. The three alarms are derived,
and how well that matches the app is to be confirmed against real traffic (#54):

* salt: the salt level is at or below the threshold the user set;
* leak and head error: the history holds an event of that kind from the last day.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from homeassistant.components.binary_sensor import (
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.components.binary_sensor.const import BinarySensorDeviceClass
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from . import AquastillaConfigEntry
from .coordinator import AquastillaCoordinator, DeviceData
from .entity import AquastillaEntity
from .models import Cycle

# How long an event in the history keeps its alarm on.
ALARM_WINDOW = timedelta(days=1)

_REGENERATING = {
    Cycle.BRINE_REFILL,
    Cycle.SALT_DISSOLVE,
    Cycle.BACKWASH,
    Cycle.BRINE_COLLECT,
    Cycle.FAST_WASH,
}


@dataclass(frozen=True, kw_only=True)
class AquastillaBinarySensorDescription(BinarySensorEntityDescription):
    """A binary sensor derived from one device's data. ``None`` means unknown."""

    is_on_fn: Callable[[DeviceData], bool | None]


def _regenerating(data: DeviceData) -> bool | None:
    cycle = data.current_states.current or data.state.cycle
    if cycle is None:
        return None
    if not isinstance(cycle, Cycle):
        return None  # a cycle we do not know: neither yes nor no
    return cycle in _REGENERATING


def _salt_low(data: DeviceData) -> bool | None:
    level, threshold = data.state.salt_percent, data.settings.salt_alarm_level
    if level is None or threshold is None:
        return None
    return level <= threshold


# Clocks differ a little. More than this in the future is a wrong zone, not an event.
_CLOCK_SKEW = timedelta(minutes=5)


def _recent(moment: datetime | None) -> bool:
    if moment is None:
        return False
    return -_CLOCK_SKEW <= dt_util.utcnow() - moment <= ALARM_WINDOW


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
        is_on_fn=_regenerating,
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
        is_on_fn=lambda data: _recent(data.instant(data.device.history.flood)),
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
    coordinator = entry.runtime_data
    known: set[str] = set()

    @callback
    def add_new_devices() -> None:
        new = set(coordinator.data.devices) - known
        if not new:
            return
        known.update(new)
        async_add_entities(
            AquastillaBinarySensor(coordinator, uuid, description)
            for uuid in sorted(new)
            for description in BINARY_SENSORS
        )

    add_new_devices()
    entry.async_on_unload(coordinator.async_add_listener(add_new_devices))


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

    @property
    def is_on(self) -> bool | None:
        return self.entity_description.is_on_fn(self.device_data)
