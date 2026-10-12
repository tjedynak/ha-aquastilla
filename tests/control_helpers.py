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
from .setup_helpers import make_entry, setup_entry

BASE = f"/device/{DEVICE_UUID}"
SETTINGS = f"{BASE}/settings"


async def started(hass: HomeAssistant, backend: FakeBackend) -> MockConfigEntry:
    entry = make_entry()
    await setup_entry(hass, entry)
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
