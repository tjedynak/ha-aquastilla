"""Base class shared by all Aquastilla entities."""

from collections.abc import Callable, Iterable

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import callback
from homeassistant.helpers.device_registry import CONNECTION_NETWORK_MAC, DeviceInfo
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import AquastillaCoordinator, DeviceData
from .models import Device

DEFAULT_MANUFACTURER = "Waterlife"
DEFAULT_NAME = "Aquastilla"


def device_info(device: Device) -> DeviceInfo:
    """Registry data for a device. Used when the entry loads and by every entity."""
    connections = {(CONNECTION_NETWORK_MAC, device.mac_address)} if device.mac_address else set()
    return DeviceInfo(
        identifiers={(DOMAIN, device.uuid)},
        connections=connections,
        manufacturer=device.brand or DEFAULT_MANUFACTURER,
        model=device.model,
        serial_number=device.serial,
        name=device.name or DEFAULT_NAME,
    )


class AquastillaEntity(CoordinatorEntity[AquastillaCoordinator]):
    """An entity that belongs to one device on the account."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: AquastillaCoordinator, uuid: str) -> None:
        super().__init__(coordinator)
        self._uuid = uuid
        self._attr_device_info = device_info(self.device_data.device)

    @property
    def device_data(self) -> DeviceData:
        return self.coordinator.data.devices[self._uuid]

    @property
    def available(self) -> bool:
        return super().available and self._uuid in self.coordinator.data.devices


def setup_device_entities(
    entry: ConfigEntry[AquastillaCoordinator],
    async_add_entities: AddConfigEntryEntitiesCallback,
    build: Callable[[AquastillaCoordinator, str], Iterable[Entity]],
) -> None:
    """Add a platform's entities for every device, and for devices that show up later.

    A device the account stops listing loses its entities with its registry entry, so it
    is forgotten here as well and gets them again if it returns. A device that is only
    detached stays known: its entities are still there and turn available again.
    """
    coordinator = entry.runtime_data
    known: set[str] = set()

    @callback
    def add_new_devices() -> None:
        listed = coordinator.data.listed
        if listed:  # an empty listing is not trusted, as in the registry sync
            known.intersection_update(listed)
        new = set(coordinator.data.devices) - known
        if not new:
            return
        known.update(new)
        async_add_entities(entity for uuid in sorted(new) for entity in build(coordinator, uuid))

    add_new_devices()
    entry.async_on_unload(coordinator.async_add_listener(add_new_devices))
