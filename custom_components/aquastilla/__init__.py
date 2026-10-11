"""Home Assistant integration for the Aquastilla DUO Smart water softener."""

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr

from .const import CONF_EMAIL, CONF_PASSWORD
from .coordinator import AquastillaCoordinator
from .entity import device_info
from .helpers import create_client

PLATFORMS: list[Platform] = []

type AquastillaConfigEntry = ConfigEntry[AquastillaCoordinator]


async def async_setup_entry(hass: HomeAssistant, entry: AquastillaConfigEntry) -> bool:
    """Log in, fetch the first data set and start polling."""
    client = create_client(hass, entry.data[CONF_EMAIL], entry.data[CONF_PASSWORD])
    coordinator = AquastillaCoordinator(hass, entry, client)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    registry = dr.async_get(hass)
    for data in coordinator.data.devices.values():
        registry.async_get_or_create(config_entry_id=entry.entry_id, **device_info(data.device))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: AquastillaConfigEntry) -> bool:
    """Unload the platforms. The coordinator stops with the entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
