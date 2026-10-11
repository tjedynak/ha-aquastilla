"""The integration survives the loss of the vendor cloud and a restart without help."""

import asyncio

from freezegun.api import FrozenDateTimeFactory
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from custom_components.aquastilla import const
from custom_components.aquastilla.const import DOMAIN
from custom_components.aquastilla.models import DeviceEventType

from .fake_backend import DEVICE_UUID, FakeBackend
from .setup_helpers import make_entry, run_setup, setup_entry

KEYS = ("sensor", "salt_level"), ("binary_sensor", "online"), ("sensor", "water_today")


def state_of(hass: HomeAssistant, domain: str, key: str) -> str:
    entity_id = er.async_get(hass).async_get_entity_id(domain, DOMAIN, f"{DEVICE_UUID}_{key}")
    assert entity_id is not None, key
    state = hass.states.get(entity_id)
    assert state is not None
    return state.state


async def test_a_total_outage_backs_off_and_ends_by_itself(
    hass: HomeAssistant, backend: FakeBackend, freezer: FrozenDateTimeFactory
) -> None:
    entry = make_entry()
    await setup_entry(hass, entry)
    coordinator = entry.runtime_data
    assert all(state_of(hass, d, k) not in {STATE_UNAVAILABLE, STATE_UNKNOWN} for d, k in KEYS)

    # The cloud is gone: the token no longer works and nothing answers.
    backend.expire_tokens()
    for method, path in (
        ("POST", "/login-company"),
        ("GET", "/device/all"),
        ("GET", f"/device/{DEVICE_UUID}/state"),
        ("GET", f"/device/{DEVICE_UUID}/current_states"),
    ):
        backend.fail_always(method, path, status=503)

    intervals = []
    for _ in range(4):
        freezer.tick(coordinator.update_interval or const.DEFAULT_SCAN_INTERVAL)
        await coordinator._async_refresh(scheduled=True)
        intervals.append(coordinator.update_interval)
    assert all(state_of(hass, d, k) == STATE_UNAVAILABLE for d, k in KEYS)  # never "unknown"
    assert coordinator.failures >= 3
    assert intervals == sorted(intervals)  # the delay only grows
    assert intervals[-1] > intervals[0]
    assert entry.state is ConfigEntryState.LOADED  # an outage is not a setup failure

    backend.heal()
    freezer.tick(coordinator.update_interval or const.DEFAULT_SCAN_INTERVAL)
    await coordinator._async_refresh(scheduled=True)
    await hass.async_block_till_done()
    assert coordinator.failures == 0
    assert coordinator.update_interval == const.DEFAULT_SCAN_INTERVAL
    assert all(state_of(hass, d, k) not in {STATE_UNAVAILABLE, STATE_UNKNOWN} for d, k in KEYS)


async def test_a_command_in_flight_does_not_survive_an_unload_and_leaves_nothing_behind(
    hass: HomeAssistant, backend: FakeBackend
) -> None:
    entry = make_entry()
    await setup_entry(hass, entry)
    old_tracker = entry.runtime_data.command_tracker
    assert old_tracker is not None
    release = asyncio.Event()

    async def never_answered() -> None:
        await release.wait()

    sending = asyncio.ensure_future(
        old_tracker.async_send(DEVICE_UUID, DeviceEventType.SET_VACATION_MODE, never_answered)
    )
    await asyncio.sleep(0)
    assert old_tracker.is_pending(DEVICE_UUID, DeviceEventType.SET_VACATION_MODE)

    # Home Assistant restarts: the entry is unloaded and loaded again.
    assert await hass.config_entries.async_unload(entry.entry_id)
    sending.cancel()
    await asyncio.gather(sending, return_exceptions=True)
    assert not old_tracker.pending(DEVICE_UUID)  # cancelled cleanly, nothing is stuck

    await run_setup(hass, entry)
    assert entry.state is ConfigEntryState.LOADED
    new_tracker = entry.runtime_data.command_tracker
    assert new_tracker is not None
    assert new_tracker is not old_tracker
    assert not new_tracker.pending(DEVICE_UUID)  # a fresh start never inherits a pending mark


async def test_the_integration_works_while_the_push_channel_is_refused(
    hass: HomeAssistant, backend: FakeBackend
) -> None:
    backend.ws_refuse = True
    entry = make_entry()
    await setup_entry(hass, entry)
    assert entry.state is ConfigEntryState.LOADED
    assert all(state_of(hass, d, k) not in {STATE_UNAVAILABLE, STATE_UNKNOWN} for d, k in KEYS)
    assert entry.runtime_data.websocket is not None
    assert not entry.runtime_data.websocket.connected

    await backend.wait_for(lambda: backend.ws_connect_attempts >= 1)  # it did try, in vain
    backend.ws_refuse = False  # the channel is back: it connects without any help
    await backend.wait_for(lambda: entry.runtime_data.websocket.connected, within=10)
    await backend.wait_for(lambda: entry.runtime_data.push.catch_ups >= 1)
    assert backend.ws_connect_attempts >= 2
    # The backoff did not turn into a storm while it was refused.
    assert backend.ws_connect_attempts < 10
