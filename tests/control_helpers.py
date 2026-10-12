"""Helpers shared by the tests of the write entities."""

import asyncio
from collections.abc import Callable
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.aquastilla.const import DOMAIN
from custom_components.aquastilla.models import DeviceEventType

from .fake_backend import DEVICE_UUID, FakeBackend
from .setup_helpers import make_entry, run_setup

BASE = f"/device/{DEVICE_UUID}"
SETTINGS = f"{BASE}/settings"


# Entities the integration adds switched off. The tests of their behaviour switch them on
# in the registry before the entry is set up, which is what a user does in the UI.
GUARDED: tuple[tuple[str, str], ...] = (
    ("button", "force_regeneration"),
    ("button", "delay_regeneration"),
    ("number", "leak_continuous_flow_time"),
    ("number", "leak_single_outflow"),
    ("number", "leak_max_flow"),
    ("time", "regeneration_time"),
    ("select", "volume_unit"),
    ("switch", "salt_alarm_push"),
    ("switch", "salt_alarm_email"),
    ("switch", "flood_alarm_push"),
    ("switch", "flood_alarm_email"),
    ("switch", "default_device"),
)


async def started(
    hass: HomeAssistant,
    backend: FakeBackend,
    enable: tuple[tuple[str, str], ...] = GUARDED,
) -> MockConfigEntry:
    entry = make_entry()
    entry.add_to_hass(hass)
    registry = er.async_get(hass)
    for domain, key in enable:
        registry.async_get_or_create(
            domain, DOMAIN, f"{DEVICE_UUID}_{key}", config_entry=entry, disabled_by=None
        )
    await run_setup(hass, entry)
    hub = entry.runtime_data.push
    await backend.wait_for(lambda: hub.catch_ups >= 1)  # the push channel is up
    return entry


def entity_id(hass: HomeAssistant, domain: str, key: str) -> str:
    found = er.async_get(hass).async_get_entity_id(domain, DOMAIN, f"{DEVICE_UUID}_{key}")
    assert found is not None, key
    return found


def state_of(hass: HomeAssistant, domain: str, key: str) -> str:
    state = hass.states.get(entity_id(hass, domain, key))
    assert state is not None
    return state.state


async def ack(backend: FakeBackend, event_type: DeviceEventType) -> None:
    await backend.push(
        "/device/event_seen",
        {"deviceEventType": str(event_type), "uuid": DEVICE_UUID, "timestamp": 1767342000},
    )


async def call(hass: HomeAssistant, domain: str, service: str, key: str) -> None:
    await hass.services.async_call(
        domain, service, {"entity_id": entity_id(hass, domain, key)}, blocking=True
    )


async def command_and_ack(
    hass: HomeAssistant,
    backend: FakeBackend,
    action: Callable[[], Any],
    path: str,
    event_type: DeviceEventType,
) -> None:
    task = asyncio.ensure_future(action())
    await backend.wait_for(lambda: bool(backend.requests_to("POST", path)))
    await ack(backend, event_type)
    await task


async def enable_entities(
    hass: HomeAssistant, backend: FakeBackend, entry: MockConfigEntry, *keys: tuple[str, str]
) -> MockConfigEntry:
    """Switch entities on that are off by default and load the entry again."""
    registry = er.async_get(hass)
    for domain, key in keys:
        found = registry.async_get_entity_id(domain, DOMAIN, f"{DEVICE_UUID}_{key}")
        assert found is not None, key
        registry.async_update_entity(found, disabled_by=None)
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    reloaded = hass.config_entries.async_get_entry(entry.entry_id)
    assert isinstance(reloaded, MockConfigEntry)
    hub = reloaded.runtime_data.push
    await backend.wait_for(lambda: hub.catch_ups >= 1)
    return reloaded
