"""How many requests the coordinator spends, and how it behaves after failures."""

import asyncio
from datetime import timedelta
from unittest.mock import patch

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant

from custom_components.aquastilla import const
from custom_components.aquastilla.coordinator import AquastillaCoordinator
from custom_components.aquastilla.models import DeviceState

from .common import load_fixture
from .fake_backend import DEVICE_UUID, FakeBackend
from .setup_helpers import make_entry, run_setup

STATE = f"/device/{DEVICE_UUID}/state"
CURRENT = f"/device/{DEVICE_UUID}/current_states"
SETTINGS = f"/device/{DEVICE_UUID}/settings"
FIVE_MINUTES = timedelta(minutes=5)

# One device, one hour of polling at the default interval. State and cycle every poll
# (12 x 2), settings twice, the device list once.
HOURLY_BUDGET = 30


@pytest.fixture
async def coordinator(
    hass: HomeAssistant,
    backend: FakeBackend,
    freezer: FrozenDateTimeFactory,
) -> AquastillaCoordinator:
    entry = make_entry()
    entry.add_to_hass(hass)
    await run_setup(hass, entry)
    assert entry.state is ConfigEntryState.LOADED
    coordinator: AquastillaCoordinator = entry.runtime_data
    backend.requests.clear()
    return coordinator


def paths(backend: FakeBackend) -> list[str]:
    return [r.path for r in backend.requests]


async def poll_after(
    coordinator: AquastillaCoordinator, freezer: FrozenDateTimeFactory, delta: timedelta
) -> None:
    """Let time pass and run the poll the timer would have run."""
    freezer.tick(delta)
    await coordinator._async_refresh(scheduled=True)


# ----------------------------------------------------------- what is polled


async def test_a_regular_poll_reads_only_state_and_cycle(
    backend: FakeBackend, coordinator: AquastillaCoordinator, freezer: FrozenDateTimeFactory
) -> None:
    await poll_after(coordinator, freezer, FIVE_MINUTES)
    assert paths(backend) == [STATE, CURRENT]


async def test_nothing_is_read_when_no_dataset_is_due(
    backend: FakeBackend, coordinator: AquastillaCoordinator, freezer: FrozenDateTimeFactory
) -> None:
    await poll_after(coordinator, freezer, timedelta(minutes=1))
    assert backend.requests == []


async def test_settings_are_read_every_half_hour(
    backend: FakeBackend, coordinator: AquastillaCoordinator, freezer: FrozenDateTimeFactory
) -> None:
    for _ in range(5):
        await poll_after(coordinator, freezer, FIVE_MINUTES)
    assert SETTINGS not in paths(backend)  # 25 minutes
    await poll_after(coordinator, freezer, FIVE_MINUTES)
    assert paths(backend).count(SETTINGS) == 1  # 30 minutes


async def test_the_device_list_is_read_every_hour(
    backend: FakeBackend, coordinator: AquastillaCoordinator, freezer: FrozenDateTimeFactory
) -> None:
    for _ in range(11):
        await poll_after(coordinator, freezer, FIVE_MINUTES)
    assert "/device/all" not in paths(backend)
    await poll_after(coordinator, freezer, FIVE_MINUTES)
    assert paths(backend).count("/device/all") == 1


async def test_an_hour_of_polling_stays_inside_the_budget(
    backend: FakeBackend, coordinator: AquastillaCoordinator, freezer: FrozenDateTimeFactory
) -> None:
    for _ in range(12):
        await poll_after(coordinator, freezer, FIVE_MINUTES)
    counts = {p: paths(backend).count(p) for p in set(paths(backend))}
    assert counts == {STATE: 12, CURRENT: 12, SETTINGS: 2, "/device/all": 1}
    assert len(backend.requests) <= HOURLY_BUDGET
    assert "/login-company" not in counts  # the session was reused the whole hour


async def test_the_timer_uses_the_current_interval(coordinator: AquastillaCoordinator) -> None:
    """Home Assistant schedules the next poll from ``update_interval``, so that is what counts."""
    assert coordinator.update_interval == const.DEFAULT_SCAN_INTERVAL
    coordinator.set_base_interval(timedelta(minutes=15))
    assert coordinator.update_interval == timedelta(minutes=15)


async def test_a_manual_refresh_rereads_the_state_but_not_the_slow_data(
    backend: FakeBackend, coordinator: AquastillaCoordinator
) -> None:
    await coordinator.async_refresh()
    assert paths(backend) == [STATE, CURRENT]


# ------------------------------------------------------------------ backoff


async def test_failures_double_the_interval_up_to_the_cap(
    backend: FakeBackend, coordinator: AquastillaCoordinator, freezer: FrozenDateTimeFactory
) -> None:
    backend.fail_always("GET", STATE, status=500)
    seen = []
    for _ in range(6):
        await poll_after(coordinator, freezer, coordinator.update_interval or FIVE_MINUTES)
        seen.append(coordinator.update_interval)
    assert seen == [
        timedelta(minutes=10),
        timedelta(minutes=20),
        timedelta(minutes=40),
        const.MAX_BACKOFF,
        const.MAX_BACKOFF,
        const.MAX_BACKOFF,
    ]


async def test_the_first_success_restores_the_normal_interval(
    backend: FakeBackend, coordinator: AquastillaCoordinator, freezer: FrozenDateTimeFactory
) -> None:
    backend.fail_next("GET", STATE, status=500)
    backend.fail_next("GET", STATE, status=500)
    await poll_after(coordinator, freezer, FIVE_MINUTES)
    await poll_after(coordinator, freezer, timedelta(minutes=10))
    assert coordinator.update_interval == timedelta(minutes=20)
    await poll_after(coordinator, freezer, timedelta(minutes=20))
    assert coordinator.last_update_success is True
    assert coordinator.update_interval == FIVE_MINUTES


async def test_a_failed_poll_does_not_make_a_dataset_look_fresh(
    backend: FakeBackend, coordinator: AquastillaCoordinator, freezer: FrozenDateTimeFactory
) -> None:
    """State was read, then settings failed: the retry must read both again."""
    for _ in range(5):
        await poll_after(coordinator, freezer, FIVE_MINUTES)
    backend.requests.clear()
    backend.fail_next("GET", SETTINGS, status=500)
    await poll_after(coordinator, freezer, FIVE_MINUTES)
    assert coordinator.last_update_success is False
    backend.requests.clear()
    await poll_after(coordinator, freezer, timedelta(minutes=10))
    assert paths(backend) == [STATE, CURRENT, SETTINGS]


async def test_the_backoff_is_never_shorter_than_the_users_interval(
    coordinator: AquastillaCoordinator,
) -> None:
    coordinator.set_base_interval(timedelta(hours=2))
    coordinator._failures = 1
    assert coordinator.update_interval == timedelta(hours=2)  # unchanged until the next poll
    assert coordinator._backoff_interval() == timedelta(hours=2)


async def test_the_interval_has_a_floor(coordinator: AquastillaCoordinator) -> None:
    coordinator.set_base_interval(timedelta(seconds=5))
    assert coordinator.base_interval == const.MIN_SCAN_INTERVAL
    assert coordinator.update_interval == const.MIN_SCAN_INTERVAL


async def test_a_longer_user_interval_slows_the_state_poll(
    backend: FakeBackend, coordinator: AquastillaCoordinator, freezer: FrozenDateTimeFactory
) -> None:
    coordinator.set_base_interval(timedelta(minutes=15))
    await poll_after(coordinator, freezer, timedelta(minutes=10))
    assert backend.requests == []
    await poll_after(coordinator, freezer, timedelta(minutes=5))
    assert paths(backend) == [STATE, CURRENT]


# ----------------------------------------------------------- detached device


async def test_a_detached_device_forces_a_device_list_refresh(
    backend: FakeBackend, coordinator: AquastillaCoordinator, freezer: FrozenDateTimeFactory
) -> None:
    backend.fail_next("GET", STATE, status=400, json_body=load_fixture("error_device_detached"))
    await poll_after(coordinator, freezer, FIVE_MINUTES)
    assert coordinator.data.devices == {}
    backend.requests.clear()
    await poll_after(coordinator, freezer, FIVE_MINUTES)
    assert "/device/all" in paths(backend)  # the list is re-read at once, not in an hour


# --------------------------------------------------------- pushes (targeted)


async def test_a_state_push_reads_state_and_cycle_only(
    backend: FakeBackend, coordinator: AquastillaCoordinator, freezer: FrozenDateTimeFactory
) -> None:
    freezer.tick(timedelta(minutes=1))
    backend.replace_json(STATE, {**load_fixture("state"), "saltDays": 12})
    await coordinator.async_refresh_state(DEVICE_UUID)
    assert paths(backend) == [STATE, CURRENT]
    assert coordinator.data.devices[DEVICE_UUID].state.salt_days == 12


async def test_a_settings_push_reads_settings_only(
    backend: FakeBackend, coordinator: AquastillaCoordinator, freezer: FrozenDateTimeFactory
) -> None:
    freezer.tick(timedelta(minutes=1))
    backend.replace_json(SETTINGS, {**load_fixture("settings"), "vacationMode": True})
    await coordinator.async_refresh_settings(DEVICE_UUID)
    assert paths(backend) == [SETTINGS]
    assert coordinator.data.devices[DEVICE_UUID].settings.vacation_mode is True


async def test_a_push_notifies_listeners(
    backend: FakeBackend, coordinator: AquastillaCoordinator, freezer: FrozenDateTimeFactory
) -> None:
    calls = []
    coordinator.async_add_listener(lambda: calls.append(1))
    freezer.tick(timedelta(minutes=1))
    await coordinator.async_refresh_state(DEVICE_UUID)
    assert calls == [1]


async def test_a_burst_of_pushes_becomes_one_request_set(
    backend: FakeBackend, coordinator: AquastillaCoordinator, freezer: FrozenDateTimeFactory
) -> None:
    freezer.tick(timedelta(minutes=1))
    for _ in range(20):
        await coordinator.async_refresh_state(DEVICE_UUID)
    assert paths(backend) == [STATE, CURRENT]
    freezer.tick(const.PUSH_REFRESH_COOLDOWN)
    await coordinator.async_refresh_state(DEVICE_UUID)
    assert paths(backend) == [STATE, CURRENT, STATE, CURRENT]


async def test_a_push_refresh_counts_as_a_fresh_read_for_the_next_poll(
    backend: FakeBackend, coordinator: AquastillaCoordinator, freezer: FrozenDateTimeFactory
) -> None:
    freezer.tick(timedelta(minutes=4))
    await coordinator.async_refresh_state(DEVICE_UUID)
    backend.requests.clear()
    freezer.tick(timedelta(minutes=1))
    await coordinator._async_refresh(scheduled=True)
    assert backend.requests == []  # state was read a minute ago


async def test_a_failed_push_refresh_is_quiet_and_leaves_the_data(
    backend: FakeBackend, coordinator: AquastillaCoordinator, freezer: FrozenDateTimeFactory
) -> None:
    freezer.tick(timedelta(minutes=1))
    backend.fail_next("GET", STATE, status=500)
    await coordinator.async_refresh_state(DEVICE_UUID)
    assert coordinator.data.devices[DEVICE_UUID].state.salt_days == 41
    assert coordinator.last_update_success is True
    backend.requests.clear()
    freezer.tick(const.PUSH_REFRESH_COOLDOWN)
    await coordinator.async_refresh_state(
        DEVICE_UUID
    )  # a failed attempt does not start the cooldown
    assert paths(backend) == [STATE, CURRENT]


async def test_a_push_for_an_unknown_device_does_nothing(
    backend: FakeBackend, coordinator: AquastillaCoordinator
) -> None:
    await coordinator.async_refresh_state("not-ours")
    await coordinator.async_refresh_settings("not-ours")
    assert backend.requests == []


# --------------------------------------------------------- poll versus push


async def test_a_poll_and_a_push_refresh_never_overwrite_each_other(
    coordinator: AquastillaCoordinator, freezer: FrozenDateTimeFactory
) -> None:
    """The poll starts first and reads old data; a push arrives while it is in flight.

    Without serialisation the push finishes first and the older poll result then
    replaces it. With it, the push waits, re-reads and ends up as the newest data.
    """
    state = load_fixture("state")
    gate = asyncio.Event()
    started = asyncio.Event()
    calls: list[str] = []

    async def get_state(uuid: str) -> DeviceState:
        calls.append("poll" if not started.is_set() else "push")
        if not started.is_set():
            started.set()
            await gate.wait()
            return DeviceState.from_json({**state, "saltDays": 1})
        return DeviceState.from_json({**state, "saltDays": 2})

    with patch.object(coordinator.client, "get_state", get_state):
        freezer.tick(FIVE_MINUTES)
        poll = asyncio.create_task(coordinator._async_refresh(scheduled=True))
        await started.wait()
        freezer.tick(const.PUSH_REFRESH_COOLDOWN * 2)
        push = asyncio.create_task(coordinator.async_refresh_state(DEVICE_UUID))
        for _ in range(10):
            await asyncio.sleep(0)
        assert calls == ["poll"]  # the push is waiting for the poll
        gate.set()
        await asyncio.gather(poll, push)

    assert calls == ["poll", "push"]
    assert coordinator.data.devices[DEVICE_UUID].state.salt_days == 2


async def test_a_push_right_after_a_poll_is_absorbed_by_the_cooldown(
    backend: FakeBackend, coordinator: AquastillaCoordinator, freezer: FrozenDateTimeFactory
) -> None:
    await poll_after(coordinator, freezer, FIVE_MINUTES)
    backend.requests.clear()
    await coordinator.async_refresh_state(DEVICE_UUID)
    assert backend.requests == []
