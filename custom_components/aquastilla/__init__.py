"""Home Assistant integration for the Aquastilla DUO Smart water softener."""

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import AquastillaClient
from .const import BASE_URL, CONF_EMAIL, CONF_PASSWORD
from .coordinator import AquastillaCoordinator
from .entity import device_info

PLATFORMS: list[Platform] = []

type AquastillaConfigEntry = ConfigEntry[AquastillaCoordinator]


async def async_setup_entry(hass: HomeAssistant, entry: AquastillaConfigEntry) -> bool:
    """Log in, fetch the first data set and start polling."""
    client = AquastillaClient(
        async_get_clientsession(hass),
        entry.data[CONF_EMAIL],
        entry.data[CONF_PASSWORD],
        base_url=BASE_URL,
    )
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
