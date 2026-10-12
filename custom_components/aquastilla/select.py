"""The unit the device reports volumes in.

Changing it changes the unit of the consumption sensors. The long-term statistics are
always stored in litres, so their history is not affected.
"""

from homeassistant.components.select import SelectEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import AquastillaConfigEntry
from .controls import async_run
from .coordinator import AquastillaCoordinator
from .entity import GatedEntity, setup_device_entities
from .gates import Command

LITRES = "l"
CUBIC_METRES = "m3"
OPTIONS = [LITRES, CUBIC_METRES]


def option_of(volume_unit: str | None) -> str | None:
    """The option for what the device reports, or ``None`` for something unknown."""
    text = (volume_unit or "").lower()
    if text in {"m3", "m³"}:
        return CUBIC_METRES
    if text in {"l", "litre", "liter", "litres", "liters"}:
        return LITRES
    return None


async def async_setup_entry(
    hass: HomeAssistant,
    entry: AquastillaConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    setup_device_entities(
        entry,
        async_add_entities,
        lambda coordinator, uuid: [VolumeUnit(coordinator, uuid)],
    )


class VolumeUnit(GatedEntity, SelectEntity):
    """Litres or cubic metres."""

    _attr_translation_key = "volume_unit"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_options = OPTIONS
    _command = Command.SETTING

    def __init__(self, coordinator: AquastillaCoordinator, uuid: str) -> None:
        super().__init__(coordinator, uuid)
        self._attr_unique_id = f"{uuid}_volume_unit"

    @property
    def current_option(self) -> str | None:
        return option_of(self.device_data.settings.volume_unit)

    async def async_select_option(self, option: str) -> None:
        client, uuid, coordinator = self.coordinator.client, self._uuid, self.coordinator
        await async_run(
            coordinator,
            uuid,
            Command.SETTING,
            lambda: client.set_volume_unit(uuid, option),
            # The app sets no pending flag for this one and waits for nothing.
            refresh=lambda: coordinator.async_refresh_settings(uuid, force=True),
        )
