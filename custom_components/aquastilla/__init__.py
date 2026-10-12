"""Home Assistant integration for the Aquastilla DUO Smart water softener."""

from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.typing import ConfigType

from . import const
from .commands import CommandTracker
from .const import (
    CONF_EMAIL,
    CONF_PASSWORD,
    CONF_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MAX_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
)
from .coordinator import AquastillaCoordinator
from .entity import device_info
from .helpers import create_client
from .push import PushHub
from .services import async_setup_services, async_sync_guarded_services
from .water_statistics import WaterStatistics
from .websocket import AquastillaWebSocket

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.EVENT,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.TEXT,
    Platform.TIME,
]

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
    async_sync_guarded_services(hass)


def _sync_devices(
    hass: HomeAssistant, entry: ConfigEntry, coordinator: AquastillaCoordinator
) -> None:
    """Keep the device registry in step with the account.

    A device that answers is registered, so one added later shows up without a reload.
    A device the account no longer lists is removed. A device that is listed but detached
    (HTTP 400, code -3) stays, with unavailable entities and a repair issue, because the
    cause is not known and it may be attached again.
    """
    registry = dr.async_get(hass)
    data = coordinator.data
    for device_data in data.devices.values():
        registry.async_get_or_create(
            config_entry_id=entry.entry_id, **device_info(device_data.device)
        )
    if not data.listed:
        # An empty listing is more likely a hiccup than an account that lost everything,
        # and pruning on it would drop every device and its entities. Nothing is removed.
        return
    for device_entry in dr.async_entries_for_config_entry(registry, entry.entry_id):
        uuids = {ident for domain, ident in device_entry.identifiers if domain == DOMAIN}
        if uuids and not uuids & data.listed:
            registry.async_update_device(device_entry.id, remove_config_entry_id=entry.entry_id)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the service actions. They are the same for every account."""
    async_setup_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: AquastillaConfigEntry) -> bool:
    """Log in, fetch the first data set and start polling."""
    client = create_client(hass, entry.data[CONF_EMAIL], entry.data[CONF_PASSWORD])
    coordinator = AquastillaCoordinator(hass, entry, client, poll_interval(entry))
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    _sync_devices(hass, entry, coordinator)
    # Repair issues describe a running entry: they go when it unloads or is removed.
    entry.async_on_unload(coordinator.async_clear_issues)
    statistics = WaterStatistics(hass, coordinator)
    websocket = AquastillaWebSocket(async_get_clientsession(hass), client, url=const.WS_URL)
    tracker = CommandTracker(coordinator)
    hub = PushHub(hass, entry, coordinator, websocket, tracker)
    coordinator.websocket, coordinator.command_tracker, coordinator.push = websocket, tracker, hub

    def _on_update() -> None:
        _sync_devices(hass, entry, coordinator)
        hub.on_coordinator_update()
        entry.async_create_background_task(
            hass, statistics.async_update(), "aquastilla water statistics"
        )

    # Also what makes the coordinator poll on its own: it only does so while listened to.
    entry.async_on_unload(coordinator.async_add_listener(_on_update))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(hub.async_stop)
    await hub.async_read_events()  # the log, once, before the channel opens
    hub.start()
    _on_update()
    async_sync_guarded_services(hass)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: AquastillaConfigEntry) -> bool:
    """Unload the platforms. The coordinator stops with the entry."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    async_sync_guarded_services(hass)  # an entry that is unloaded no longer counts
    return unloaded
