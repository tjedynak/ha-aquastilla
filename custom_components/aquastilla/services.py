"""Service actions that have no entity (ADR-3, ADR-12).

These are the things that must not be reachable by a stray automation or a dashboard
toggle: handing the vendor remote access, pairing, removing. Every one of them needs an
explicit ``confirm: true``, goes through the gates and the command runner, and reports
failures as readable errors.

Pairing and removing a device cannot be undone and are not registered at all until the
owner switches them on in the integration's options.
"""

from collections.abc import Awaitable, Callable
from typing import Any

import probatio as vol
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import device_registry as dr

from .api import AquastillaError
from .const import CONF_ENABLE_DEVICE_MANAGEMENT, DOMAIN
from .controls import async_run, refusal
from .coordinator import AquastillaCoordinator
from .gates import Command
from .models import DeviceEventType

SERVICE_RENAME = "rename_device"
SERVICE_START_REMOTE = "start_remote_service"
SERVICE_END_REMOTE = "end_remote_service"
SERVICE_PAIR = "pair_device"
SERVICE_DELETE = "delete_device"

ALWAYS = (SERVICE_RENAME, SERVICE_START_REMOTE, SERVICE_END_REMOTE)
GUARDED = (SERVICE_PAIR, SERVICE_DELETE)

ATTR_DEVICE_ID = "device_id"
ATTR_CONFIRM = "confirm"
ATTR_NAME = "name"
ATTR_HOURS = "hours"
ATTR_CODE = "code"
ATTR_CONFIG_ENTRY = "config_entry_id"

REMOTE_SERVICE_MAX_HOURS = 23

# An entry that is being set up already counts: the options are there.
_ACTIVE = {ConfigEntryState.LOADED, ConfigEntryState.SETUP_IN_PROGRESS}


def _confirmed(value: Any) -> bool:
    """The confirmation has to be an explicit ``true``: nothing else counts."""
    if value is True:
        return True
    msg = "confirm must be set to true to run this action"
    raise vol.Invalid(msg)


_SCHEMAS: dict[str, vol.Schema] = {
    SERVICE_RENAME: vol.Schema(
        {
            vol.Required(ATTR_DEVICE_ID): str,
            vol.Required(ATTR_NAME): vol.All(str, vol.Length(min=1)),
        }
    ),
    SERVICE_START_REMOTE: vol.Schema(
        {
            vol.Required(ATTR_DEVICE_ID): str,
            vol.Required(ATTR_HOURS): vol.All(int, vol.Range(min=1, max=REMOTE_SERVICE_MAX_HOURS)),
            vol.Required(ATTR_CONFIRM): _confirmed,
        }
    ),
    SERVICE_END_REMOTE: vol.Schema({vol.Required(ATTR_DEVICE_ID): str}),
    SERVICE_PAIR: vol.Schema(
        {
            vol.Required(ATTR_CONFIG_ENTRY): str,
            vol.Required(ATTR_CODE): vol.All(str, vol.Length(min=1)),
            vol.Required(ATTR_CONFIRM): _confirmed,
        }
    ),
    SERVICE_DELETE: vol.Schema(
        {vol.Required(ATTR_DEVICE_ID): str, vol.Required(ATTR_CONFIRM): _confirmed}
    ),
}


def _coordinators(hass: HomeAssistant) -> list[AquastillaCoordinator]:
    return [
        entry.runtime_data
        for entry in hass.config_entries.async_entries(DOMAIN)
        if getattr(entry, "runtime_data", None) is not None
    ]


def _resolve(hass: HomeAssistant, device_id: str) -> tuple[AquastillaCoordinator, str]:
    """The coordinator and the vendor uuid of a Home Assistant device."""
    device = dr.async_get(hass).async_get(device_id)
    if device is None:
        msg = f"Unknown device {device_id}"
        raise ServiceValidationError(msg)
    uuids = [ident for domain, ident in device.identifiers if domain == DOMAIN]
    if not uuids:
        msg = "That device does not belong to Aquastilla"
        raise ServiceValidationError(msg)
    for coordinator in _coordinators(hass):
        if uuids[0] in coordinator.data.devices or uuids[0] in coordinator.data.listed:
            return coordinator, uuids[0]
    raise refusal("device_unavailable")


async def _rename(hass: HomeAssistant, call: ServiceCall) -> None:
    coordinator, uuid = _resolve(hass, call.data[ATTR_DEVICE_ID])
    name = str(call.data[ATTR_NAME]).strip()
    await async_run(
        coordinator,
        uuid,
        Command.SERVICE,
        lambda: coordinator.client.rename_device(uuid, name),
        refresh=coordinator.async_refresh_device_list,
    )


async def _start_remote(hass: HomeAssistant, call: ServiceCall) -> None:
    coordinator, uuid = _resolve(hass, call.data[ATTR_DEVICE_ID])
    hours = int(call.data[ATTR_HOURS])
    await async_run(
        coordinator,
        uuid,
        Command.SERVICE,
        lambda: coordinator.client.start_remote_service(uuid, hours),
        event_type=DeviceEventType.SET_SERVICE_MODE,
    )


async def _end_remote(hass: HomeAssistant, call: ServiceCall) -> None:
    coordinator, uuid = _resolve(hass, call.data[ATTR_DEVICE_ID])
    await async_run(
        coordinator,
        uuid,
        Command.SERVICE,
        lambda: coordinator.client.end_remote_service(uuid),
        refresh=lambda: coordinator.async_refresh_settings(uuid, force=True),
    )


async def _pair(hass: HomeAssistant, call: ServiceCall) -> None:
    entry = hass.config_entries.async_get_entry(call.data[ATTR_CONFIG_ENTRY])
    coordinator = getattr(entry, "runtime_data", None)
    if entry is None or entry.domain != DOMAIN or coordinator is None:
        msg = "Unknown Aquastilla account"
        raise ServiceValidationError(msg)
    try:
        details = await coordinator.client.get_user_details()
        if details.user_id is None:
            raise refusal("device_unavailable")
        await coordinator.client.pair_device(details.user_id, str(call.data[ATTR_CODE]).strip())
    except AquastillaError as err:
        raise refusal("command_failed", error=str(err)) from err
    await coordinator.async_refresh_device_list()


async def _delete(hass: HomeAssistant, call: ServiceCall) -> None:
    coordinator, uuid = _resolve(hass, call.data[ATTR_DEVICE_ID])
    try:
        await coordinator.client.delete_device(uuid)
    except AquastillaError as err:
        raise refusal("command_failed", error=str(err)) from err
    await coordinator.async_refresh_device_list()


_HANDLERS: dict[str, Callable[[HomeAssistant, ServiceCall], Awaitable[None]]] = {
    SERVICE_RENAME: _rename,
    SERVICE_START_REMOTE: _start_remote,
    SERVICE_END_REMOTE: _end_remote,
    SERVICE_PAIR: _pair,
    SERVICE_DELETE: _delete,
}


def _register(hass: HomeAssistant, name: str) -> None:
    handler = _HANDLERS[name]

    async def run(call: ServiceCall) -> None:
        await handler(hass, call)

    hass.services.async_register(DOMAIN, name, run, schema=_SCHEMAS[name])


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    """Register the actions that are always there."""
    for name in ALWAYS:
        _register(hass, name)


@callback
def async_sync_guarded_services(hass: HomeAssistant) -> None:
    """Register pairing and removal while some account allows them, and only then."""
    allowed = any(
        entry.options.get(CONF_ENABLE_DEVICE_MANAGEMENT) is True
        for entry in hass.config_entries.async_entries(DOMAIN)
        if entry.state in _ACTIVE
    )
    for name in GUARDED:
        if allowed == hass.services.has_service(DOMAIN, name):
            continue
        if allowed:
            _register(hass, name)
        else:
            hass.services.async_remove(DOMAIN, name)
