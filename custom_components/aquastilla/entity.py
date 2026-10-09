"""Base class shared by all Aquastilla entities."""

from homeassistant.helpers.device_registry import CONNECTION_NETWORK_MAC, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import AquastillaCoordinator, DeviceData
from .models import Device

DEFAULT_MANUFACTURER = "Waterlife"


def device_info(device: Device) -> DeviceInfo:
    """Registry data for a device. Used when the entry loads and by every entity."""
    connections = {(CONNECTION_NETWORK_MAC, device.mac_address)} if device.mac_address else set()
    # The device payload has a serial number in some firmware, but its field name is
    # unconfirmed, so it is left out until real traffic shows it (AQ-1.7).
    return DeviceInfo(
        identifiers={(DOMAIN, device.uuid)},
        connections=connections,
        manufacturer=device.brand or DEFAULT_MANUFACTURER,
        model=device.model,
        name=device.name,
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
