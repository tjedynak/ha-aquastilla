"""Home Assistant integration for the Aquastilla DUO Smart water softener."""

from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr

from .const import (
    CONF_EMAIL,
    CONF_PASSWORD,
    CONF_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    MAX_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
)
from .coordinator import AquastillaCoordinator
from .entity import device_info
from .helpers import create_client

PLATFORMS: list[Platform] = []

type AquastillaConfigEntry = ConfigEntry[AquastillaCoordinator]


def poll_interval(entry: ConfigEntry) -> timedelta:
    """The poll interval from the options, kept between the minimum and the maximum.

    A value that is missing or not a number means the default.
    """
    raw = entry.options.get(CONF_SCAN_INTERVAL)
    try:
        interval = timedelta(minutes=float(raw)) if raw is not None else DEFAULT_SCAN_INTERVAL
    except TypeError, ValueError, OverflowError:
        interval = DEFAULT_SCAN_INTERVAL
    return min(max(interval, MIN_SCAN_INTERVAL), MAX_SCAN_INTERVAL)


async def _async_options_updated(hass: HomeAssistant, entry: AquastillaConfigEntry) -> None:
    """Apply a changed poll interval without reloading the entry.

    Called for every change to the entry, including a new password, so it acts only
    when the interval is actually different.
    """
    coordinator = entry.runtime_data
    interval = poll_interval(entry)
    if interval != coordinator.base_interval:
        coordinator.set_base_interval(interval)


async def async_setup_entry(hass: HomeAssistant, entry: AquastillaConfigEntry) -> bool:
    """Log in, fetch the first data set and start polling."""
    client = create_client(hass, entry.data[CONF_EMAIL], entry.data[CONF_PASSWORD])
    coordinator = AquastillaCoordinator(hass, entry, client, poll_interval(entry))
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    registry = dr.async_get(hass)
    for data in coordinator.data.devices.values():
        registry.async_get_or_create(config_entry_id=entry.entry_id, **device_info(data.device))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: AquastillaConfigEntry) -> bool:
    """Unload the platforms. The coordinator stops with the entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
