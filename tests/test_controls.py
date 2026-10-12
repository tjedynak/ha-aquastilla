"""Buttons and the vacation switch: gates, acknowledgement, errors and the guarded ones."""

import asyncio
from dataclasses import replace
from datetime import timedelta
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNAVAILABLE, EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er

from custom_components.aquastilla.api import AquastillaClient
from custom_components.aquastilla.const import DOMAIN
from custom_components.aquastilla.controls import async_run
from custom_components.aquastilla.gates import Command
from custom_components.aquastilla.models import DeviceEventType

from .common import load_fixture
from .control_helpers import ack, call, command_and_ack, entity_id, started, state_of
from .fake_backend import DEVICE_UUID, FakeBackend

BASE = f"/device/{DEVICE_UUID}"
SETTINGS = f"{BASE}/settings"


# ------------------------------------------------------------------- buttons


@pytest.mark.parametrize(
    ("key", "path", "event"),
    [
        (
            "force_regeneration",
            f"{BASE}/vacation_mode/force_regeneration",
            DeviceEventType.SET_REGENERATION_VACATION_MODE,
        ),
        (
            "delay_regeneration",
            f"{BASE}/delay_regeneration",
            DeviceEventType.SET_DELAY_REGENERATION,
        ),
    ],
)
async def test_a_regeneration_button_sends_the_command_and_waits_for_the_device(
    hass: HomeAssistant, backend: FakeBackend, key: str, path: str, event: DeviceEventType
) -> None:
    await started(hass, backend)
    reads = len(backend.requests_to("GET", f"{BASE}/state"))
    await command_and_ack(hass, backend, lambda: call(hass, "button", "press", key), path, event)
    assert backend.requests_to("POST", path)[0].body is None
    assert len(backend.requests_to("GET", f"{BASE}/state")) > reads  # read again afterwards


async def test_a_request_that_fails_is_a_readable_error(
    hass: HomeAssistant, backend: FakeBackend
) -> None:
    await started(hass, backend)
    backend.fail_next("POST", f"{BASE}/delay_regeneration", status=500)
    with pytest.raises(HomeAssistantError, match="could not be sent"):
        await call(hass, "button", "press", "delay_regeneration")


async def test_the_same_command_is_refused_while_it_waits(
    hass: HomeAssistant, backend: FakeBackend
) -> None:
    await started(hass, backend)
    path = f"{BASE}/delay_regeneration"
    first = asyncio.ensure_future(call(hass, "button", "press", "delay_regeneration"))
    await backend.wait_for(lambda: bool(backend.requests_to("POST", path)))
    with pytest.raises(HomeAssistantError, match="still waiting"):
        await call(hass, "button", "press", "delay_regeneration")
    await ack(backend, DeviceEventType.SET_DELAY_REGENERATION)
    await first


async def test_a_command_the_device_never_confirms_is_not_an_error(
    hass: HomeAssistant, backend: FakeBackend, caplog: pytest.LogCaptureFixture
) -> None:
    entry = await started(hass, backend)
    entry.runtime_data.command_tracker._timeout = 0.2
    reads = len(backend.requests_to("GET", f"{BASE}/state"))
    await call(hass, "button", "press", "delay_regeneration")  # returns, no exception
    assert "did not confirm" in caplog.text
    assert len(backend.requests_to("GET", f"{BASE}/state")) > reads


# --------------------------------------------------------------------- gates


@pytest.mark.parametrize(
    ("settings", "key", "reason", "message"),
    [
        ({"serviceMode": True}, "force_regeneration", "service_mode", "remote service mode"),
        ({"waterFlow": False}, "delay_regeneration", "water_flow_closed", "water flow is closed"),
        ({"vacationMode": True}, "force_regeneration", "vacation_mode", "Vacation mode is on"),
        ({"vacationMode": True}, "delay_regeneration", "vacation_mode", "Vacation mode is on"),
    ],
)
async def test_a_refused_command_says_so_and_the_entity_says_why(  # noqa: PLR0917
    hass: HomeAssistant,
    backend: FakeBackend,
    settings: dict[str, object],
    key: str,
    reason: str,
    message: str,
) -> None:
    backend.replace_json(SETTINGS, {**load_fixture("settings"), **settings})
    await started(hass, backend)
    # Still available: an unavailable entity would be skipped by a service call, silently.
    state = hass.states.get(entity_id(hass, "button", key))
    assert state is not None
    assert state.state != STATE_UNAVAILABLE
    assert state.attributes["blocked_reason"] == reason
    with pytest.raises(HomeAssistantError, match=message):
        await call(hass, "button", "press", key)


async def test_a_command_that_can_go_through_reports_no_reason(
    hass: HomeAssistant, backend: FakeBackend
) -> None:
    await started(hass, backend)
    state = hass.states.get(entity_id(hass, "button", "force_regeneration"))
    assert state is not None
    assert state.attributes["blocked_reason"] is None


async def test_an_offline_device_refuses_the_physical_commands(
    hass: HomeAssistant, backend: FakeBackend
) -> None:
    backend.replace_json(f"{BASE}/state", {**load_fixture("state"), "isOnline": False})
    await started(hass, backend)
    with pytest.raises(HomeAssistantError, match="offline"):
        await call(hass, "button", "press", "force_regeneration")
    assert backend.requests_to("POST", f"{BASE}/vacation_mode/force_regeneration") == []


async def test_the_gates_read_fresh_data_before_a_command(
    hass: HomeAssistant, backend: FakeBackend, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Service mode started in the vendor app since the last poll must still stop it."""
    monkeypatch.setattr(
        "custom_components.aquastilla.coordinator.PUSH_REFRESH_COOLDOWN", timedelta(0)
    )
    await started(hass, backend)
    backend.replace_json(SETTINGS, {**load_fixture("settings"), "serviceMode": True})
    with pytest.raises(HomeAssistantError, match="remote service mode"):
        await call(hass, "button", "press", "force_regeneration")
    assert backend.requests_to("POST", f"{BASE}/vacation_mode/force_regeneration") == []


async def test_a_device_that_goes_away_while_the_data_is_read_is_refused(
    hass: HomeAssistant, backend: FakeBackend
) -> None:
    entry = await started(hass, backend)
    coordinator = entry.runtime_data

    async def vanish(uuid: str, *, force: bool = False) -> None:
        coordinator.async_set_updated_data(replace(coordinator.data, devices={}))

    with (
        patch.object(coordinator, "async_refresh_state", new=vanish),
        pytest.raises(HomeAssistantError, match="not available"),
    ):
        await async_run(coordinator, DEVICE_UUID, Command.SERVICE, AsyncMock())


async def test_the_runner_refuses_before_sending_anything(
    hass: HomeAssistant, backend: FakeBackend
) -> None:
    backend.replace_json(SETTINGS, {**load_fixture("settings"), "serviceMode": True})
    entry = await started(hass, backend)
    sent = AsyncMock()
    with pytest.raises(HomeAssistantError, match="remote service mode"):
        await async_run(
            entry.runtime_data,
            DEVICE_UUID,
            Command.FORCE_REGENERATION,
            sent,
            event_type=DeviceEventType.SET_REGENERATION_VACATION_MODE,
        )
    sent.assert_not_awaited()


async def test_an_unknown_device_is_refused(hass: HomeAssistant, backend: FakeBackend) -> None:
    entry = await started(hass, backend)
    with pytest.raises(HomeAssistantError, match="not available"):
        await async_run(entry.runtime_data, "no-such-device", Command.SERVICE, AsyncMock())


async def test_a_command_without_an_acknowledgement_just_refreshes(
    hass: HomeAssistant, backend: FakeBackend
) -> None:
    entry = await started(hass, backend)
    sent = AsyncMock()
    assert await async_run(entry.runtime_data, DEVICE_UUID, Command.SERVICE, sent) is None
    sent.assert_awaited_once()


# -------------------------------------------------------------------- switch


async def test_vacation_mode_cannot_be_turned_on_during_a_regeneration(
    hass: HomeAssistant, backend: FakeBackend
) -> None:
    backend.replace_json(
        f"{BASE}/current_states",
        {"first": "deviceStateRegenBackwash", "second": "deviceStateSoftening"},
    )
    await started(hass, backend)
    with pytest.raises(HomeAssistantError, match="regeneration is running"):
        await call(hass, "switch", "turn_on", "vacation_mode")
    assert backend.requests_to("POST", f"{BASE}/vacation_mode") == []


@pytest.mark.parametrize(("service", "expected"), [("turn_on", True), ("turn_off", False)])
async def test_the_vacation_switch_sends_a_bare_boolean(
    hass: HomeAssistant, backend: FakeBackend, service: str, expected: bool
) -> None:
    await started(hass, backend)
    path = f"{BASE}/vacation_mode"
    await command_and_ack(
        hass,
        backend,
        lambda: call(hass, "switch", service, "vacation_mode"),
        path,
        DeviceEventType.SET_VACATION_MODE,
    )
    assert backend.requests_to("POST", path)[0].body is expected


async def test_the_switch_shows_what_the_device_reports_and_not_what_was_asked(
    hass: HomeAssistant, backend: FakeBackend
) -> None:
    entry = await started(hass, backend)
    entry.runtime_data.command_tracker._timeout = 0.2
    assert state_of(hass, "switch", "vacation_mode") == STATE_OFF
    await call(hass, "switch", "turn_on", "vacation_mode")  # never confirmed
    assert state_of(hass, "switch", "vacation_mode") == STATE_OFF  # the fake did not change it


async def test_the_switch_follows_the_device(hass: HomeAssistant, backend: FakeBackend) -> None:
    backend.replace_json(SETTINGS, {**load_fixture("settings"), "vacationMode": True})
    await started(hass, backend)
    assert state_of(hass, "switch", "vacation_mode") == STATE_ON


async def test_the_switch_says_when_a_command_is_pending(
    hass: HomeAssistant, backend: FakeBackend
) -> None:
    await started(hass, backend)
    path = f"{BASE}/vacation_mode"
    task = asyncio.ensure_future(call(hass, "switch", "turn_on", "vacation_mode"))
    await backend.wait_for(lambda: bool(backend.requests_to("POST", path)))
    state = hass.states.get(entity_id(hass, "switch", "vacation_mode"))
    assert state is not None
    assert state.attributes["pending"] is True
    await ack(backend, DeviceEventType.SET_VACATION_MODE)
    await task
    state = hass.states.get(entity_id(hass, "switch", "vacation_mode"))
    assert state is not None
    assert state.attributes["pending"] is False


# --------------------------------------------------------------- the guarded


@pytest.mark.parametrize("key", ["advance_cycle", "close_water_flow"])
async def test_the_dangerous_buttons_are_off_until_enabled(
    hass: HomeAssistant, backend: FakeBackend, key: str
) -> None:
    await started(hass, backend)
    registry = er.async_get(hass)
    registry_id = registry.async_get_entity_id("button", DOMAIN, f"{DEVICE_UUID}_{key}")
    assert registry_id is not None
    entry = registry.async_get(registry_id)
    assert entry is not None
    assert entry.disabled_by is er.RegistryEntryDisabler.INTEGRATION
    assert entry.entity_category is EntityCategory.CONFIG
    assert hass.states.get(registry_id) is None


async def test_closing_the_water_is_a_button_and_never_a_switch_or_a_valve(
    hass: HomeAssistant, backend: FakeBackend
) -> None:
    await started(hass, backend)
    registry = er.async_get(hass)
    owned = [e for e in registry.entities.values() if e.platform == DOMAIN]
    closing = [e for e in owned if "close_water_flow" in e.unique_id]
    assert [e.domain for e in closing] == ["button"]
    assert not [e for e in owned if e.domain in {"valve", "lock"}]
    assert f"{DEVICE_UUID}_close_water_flow" not in {
        e.unique_id for e in owned if e.domain != "button"
    }


async def test_the_guarded_buttons_work_once_enabled_without_reaching_the_endpoint(
    hass: HomeAssistant, backend: FakeBackend
) -> None:
    entry = await started(hass, backend)
    registry = er.async_get(hass)
    for key in ("advance_cycle", "close_water_flow"):
        eid = registry.async_get_entity_id("button", DOMAIN, f"{DEVICE_UUID}_{key}")
        assert eid is not None
        registry.async_update_entity(eid, disabled_by=None)
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    reloaded = hass.config_entries.async_get_entry(entry.entry_id)
    assert reloaded is not None
    entry = reloaded
    hub = entry.runtime_data.push
    await backend.wait_for(lambda: hub.catch_ups >= 1)

    with patch.object(AquastillaClient, "close_water_flow", new=AsyncMock()) as close:
        task = asyncio.ensure_future(call(hass, "button", "press", "close_water_flow"))
        await backend.wait_for(lambda: close.await_count == 1)
        await ack(backend, DeviceEventType.SET_WATER_FLOW)
        await task
    close.assert_awaited_once_with(DEVICE_UUID)
    assert backend.forbidden == []  # the real endpoint was never reached
    assert backend.requests_to("POST", f"{BASE}/water_flow") == []


async def test_the_closed_flow_is_reported_by_a_binary_sensor(
    hass: HomeAssistant, backend: FakeBackend
) -> None:
    await started(hass, backend)
    assert state_of(hass, "binary_sensor", "water_flow") == STATE_ON
    backend.replace_json(SETTINGS, {**load_fixture("settings"), "waterFlow": False})
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    await entry.runtime_data.async_refresh_settings(DEVICE_UUID, force=True)
    await hass.async_block_till_done()
    assert state_of(hass, "binary_sensor", "water_flow") == STATE_OFF


async def test_without_a_tracker_nothing_is_pending(
    hass: HomeAssistant, backend: FakeBackend
) -> None:
    entry = await started(hass, backend)
    coordinator = entry.runtime_data
    coordinator.command_tracker = None
    assert coordinator.is_command_pending(DEVICE_UUID, DeviceEventType.SET_VACATION_MODE) is False
    coordinator.add_command_listener(lambda: None)()  # a no-op that can still be removed
