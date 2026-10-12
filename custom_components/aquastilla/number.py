"""The alarm thresholds, as numbers.

Ranges follow the app, which enforces 1 to 99 % for salt and 1 to 14400 minutes,
1 to 10000 litres and 1 to 500 litres per minute for the leak alarm. The app's own text
says 0 turns a leak alarm off, but its validator refuses 0, and whether the backend
accepts it (and what an alarm does to the valve) is not known (#55, #65). So the
minimum here is 1 and an existing 0 is shown as it is.

A leak alarm is always written as the full triple, whichever value changed, because the
service takes all three together.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from homeassistant.components.number import (
    NumberEntity,
    NumberEntityDescription,
    NumberMode,
)
from homeassistant.const import (
    PERCENTAGE,
    EntityCategory,
    UnitOfTime,
    UnitOfVolume,
    UnitOfVolumeFlowRate,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import AquastillaConfigEntry
from .api import AquastillaClient
from .controls import async_run, refusal
from .coordinator import AquastillaCoordinator, DeviceData
from .entity import AquastillaEntity, setup_device_entities
from .gates import Command, blocked_reason
from .models import DeviceEventType


@dataclass(frozen=True, kw_only=True)
class AquastillaNumberDescription(NumberEntityDescription):
    """One threshold: how it is read and how it is written."""

    value_fn: Callable[[DeviceData], float | None]
    set_fn: Callable[[AquastillaClient, str, DeviceData, int], Awaitable[None]]
    event_type: DeviceEventType


async def _set_salt(client: AquastillaClient, uuid: str, data: DeviceData, value: int) -> None:
    await client.set_salt_alarm(uuid, value)


def _set_flood(field: str) -> Callable[[AquastillaClient, str, DeviceData, int], Awaitable[None]]:
    async def write(client: AquastillaClient, uuid: str, data: DeviceData, value: int) -> None:
        alarm = data.settings.flood_alarm
        triple: dict[str, float | None] = {
            "continuous_flow_time": alarm.continuous_flow_time,
            "threshold": alarm.threshold,
            "max_flow": alarm.max_flow,
        }
        if any(v is None for v in triple.values()):
            # The two that stay as they are have to be sent back, so they must be known.
            raise refusal("settings_unknown")
        triple[field] = value
        await client.set_flood_alarm(
            uuid,
            continuous_flow_time=int(triple["continuous_flow_time"] or 0),
            threshold=int(triple["threshold"] or 0),
            max_flow=int(triple["max_flow"] or 0),
        )

    return write


NUMBERS: tuple[AquastillaNumberDescription, ...] = (
    AquastillaNumberDescription(
        key="salt_alarm_level",
        translation_key="salt_alarm_level",
        native_unit_of_measurement=PERCENTAGE,
        native_min_value=1,
        native_max_value=99,
        value_fn=lambda data: data.settings.salt_alarm_level,
        set_fn=_set_salt,
        event_type=DeviceEventType.SET_MINIMUM_SALT_LEVEL,
    ),
    AquastillaNumberDescription(
        key="leak_continuous_flow_time",
        translation_key="leak_continuous_flow_time",
        native_unit_of_measurement=UnitOfTime.MINUTES,
        native_min_value=1,
        native_max_value=14400,
        value_fn=lambda data: data.settings.flood_alarm.continuous_flow_time,
        set_fn=_set_flood("continuous_flow_time"),
        event_type=DeviceEventType.SET_FLOOD_SETTINGS,
    ),
    AquastillaNumberDescription(
        key="leak_single_outflow",
        translation_key="leak_single_outflow",
        native_unit_of_measurement=UnitOfVolume.LITERS,
        native_min_value=1,
        native_max_value=10000,
        value_fn=lambda data: data.settings.flood_alarm.threshold,
        set_fn=_set_flood("threshold"),
        event_type=DeviceEventType.SET_FLOOD_SETTINGS,
    ),
    AquastillaNumberDescription(
        key="leak_max_flow",
        translation_key="leak_max_flow",
        native_unit_of_measurement=UnitOfVolumeFlowRate.LITERS_PER_MINUTE,
        native_min_value=1,
        native_max_value=500,
        value_fn=lambda data: data.settings.flood_alarm.max_flow,
        set_fn=_set_flood("max_flow"),
        event_type=DeviceEventType.SET_FLOOD_SETTINGS,
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
            AquastillaNumber(coordinator, uuid, description) for description in NUMBERS
        ),
    )


class AquastillaNumber(AquastillaEntity, NumberEntity):
    """One alarm threshold of one device."""

    entity_description: AquastillaNumberDescription
    _attr_entity_category = EntityCategory.CONFIG
    _attr_mode = NumberMode.BOX
    _attr_native_step = 1

    def __init__(
        self,
        coordinator: AquastillaCoordinator,
        uuid: str,
        description: AquastillaNumberDescription,
    ) -> None:
        super().__init__(coordinator, uuid)
        self.entity_description = description
        self._attr_unique_id = f"{uuid}_{description.key}"

    @property
    def native_value(self) -> float | None:
        return self.entity_description.value_fn(self.device_data)

    @property
    def available(self) -> bool:
        if not super().available:
            return False
        return blocked_reason(self.device_data, Command.SETTING) is None

    async def async_set_native_value(self, value: float) -> None:
        description = self.entity_description
        client, uuid, data = self.coordinator.client, self._uuid, self.device_data
        await async_run(
            self.coordinator,
            uuid,
            Command.SETTING,
            lambda: description.set_fn(client, uuid, data, int(value)),
            event_type=description.event_type,
        )
