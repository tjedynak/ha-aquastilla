"""Command tracking: acknowledged, never acknowledged, acknowledged too late."""

import asyncio
import json
from collections.abc import Awaitable, Callable
from datetime import timedelta
from typing import Any

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant

from custom_components.aquastilla.commands import (
    CommandPendingError,
    CommandStatus,
    CommandTracker,
)
from custom_components.aquastilla.coordinator import AquastillaCoordinator
from custom_components.aquastilla.models import DeviceEventType

from .common import load_fixture
from .fake_backend import DEVICE_UUID, FakeBackend
from .setup_helpers import make_entry, run_setup

STATE = f"/device/{DEVICE_UUID}/state"
CURRENT = f"/device/{DEVICE_UUID}/current_states"
SETTINGS = f"/device/{DEVICE_UUID}/settings"
VACATION = DeviceEventType.SET_VACATION_MODE
TIMEOUT = 0.3


@pytest.fixture
async def coordinator(hass: HomeAssistant, backend: FakeBackend) -> AquastillaCoordinator:
    entry = make_entry()
    entry.add_to_hass(hass)
    await run_setup(hass, entry)
    entry.mock_state(hass, ConfigEntryState.LOADED)
    coordinator: AquastillaCoordinator = entry.runtime_data
    backend.requests.clear()
    return coordinator


@pytest.fixture
def tracker(coordinator: AquastillaCoordinator) -> CommandTracker:
    return CommandTracker(coordinator, timeout=TIMEOUT)


def push(event_type: str, uuid: str = DEVICE_UUID, *, as_string: bool = True) -> Any:
    body = {**load_fixture("ws_event_seen"), "deviceEventType": event_type, "uuid": uuid}
    return json.dumps(body) if as_string else body


def paths(backend: FakeBackend) -> list[str]:
    return [r.path for r in backend.requests]


def no_send() -> Callable[[], Awaitable[None]]:
    async def send() -> None:
        return None

    return send


async def settle() -> None:
    for _ in range(10):
        await asyncio.sleep(0)


# ------------------------------------------------------------- acknowledged


async def test_an_acknowledged_command_reads_the_data_and_clears_pending(
    backend: FakeBackend, coordinator: AquastillaCoordinator, tracker: CommandTracker
) -> None:
    backend.replace_json(SETTINGS, {**load_fixture("settings"), "vacationMode": True})
    seen: list[bool] = []
    tracker.add_listener(lambda: seen.append(tracker.is_pending(DEVICE_UUID, VACATION)))

    async def send() -> None:
        assert tracker.is_pending(DEVICE_UUID, VACATION)
        asyncio.get_running_loop().call_soon(tracker.handle_push, push(VACATION))

    result = await tracker.async_send(DEVICE_UUID, VACATION, send)

    assert result.status is CommandStatus.ACKNOWLEDGED
    assert not tracker.is_pending(DEVICE_UUID, VACATION)
    assert seen == [True, False]
    assert coordinator.data.devices[DEVICE_UUID].settings.vacation_mode is True
    assert SETTINGS in paths(backend)
    assert STATE in paths(backend)  # vacation mode changes what the head does


async def test_an_acknowledgement_that_beats_the_http_answer_still_counts(
    tracker: CommandTracker,
) -> None:
    async def send() -> None:
        tracker.handle_push(push(VACATION))  # the device was faster than the HTTP response
        await asyncio.sleep(0.05)

    result = await tracker.async_send(DEVICE_UUID, VACATION, send)
    assert result.status is CommandStatus.ACKNOWLEDGED


async def test_the_data_is_read_even_right_after_a_poll(
    backend: FakeBackend, coordinator: AquastillaCoordinator, tracker: CommandTracker
) -> None:
    """An acknowledgement means something changed, so the refresh cooldown must not apply."""
    await coordinator.async_refresh_settings(DEVICE_UUID, force=True)  # a read a moment ago
    backend.requests.clear()

    async def send() -> None:
        asyncio.get_running_loop().call_soon(
            tracker.handle_push, push(DeviceEventType.SET_WATER_HARDNESS)
        )

    await tracker.async_send(DEVICE_UUID, DeviceEventType.SET_WATER_HARDNESS, send)
    assert paths(backend) == [SETTINGS]


async def test_the_uuid_in_a_push_may_differ_in_case(tracker: CommandTracker) -> None:
    async def send() -> None:
        asyncio.get_running_loop().call_soon(
            tracker.handle_push, push(VACATION, DEVICE_UUID.upper())
        )

    result = await tracker.async_send(DEVICE_UUID, VACATION, send)
    assert result.status is CommandStatus.ACKNOWLEDGED


async def test_a_push_object_body_works_like_a_string(tracker: CommandTracker) -> None:
    async def send() -> None:
        asyncio.get_running_loop().call_soon(tracker.handle_push, push(VACATION, as_string=False))

    assert (
        await tracker.async_send(DEVICE_UUID, VACATION, send)
    ).status is CommandStatus.ACKNOWLEDGED


# ---------------------------------------------------------- never acknowledged


async def test_an_unacknowledged_command_ends_unconfirmed_and_shows_reality(
    backend: FakeBackend, coordinator: AquastillaCoordinator, tracker: CommandTracker
) -> None:
    """The device never applied it: the entity data still says what the device reports."""
    result = await tracker.async_send(DEVICE_UUID, VACATION, no_send())

    assert result.status is CommandStatus.UNCONFIRMED
    assert not tracker.is_pending(DEVICE_UUID, VACATION)
    assert coordinator.data.devices[DEVICE_UUID].settings.vacation_mode is False
    assert SETTINGS in paths(backend)  # it looked again instead of trusting the request


async def test_a_command_the_device_applied_but_never_confirmed_shows_the_new_value(
    backend: FakeBackend, coordinator: AquastillaCoordinator, tracker: CommandTracker
) -> None:
    backend.replace_json(SETTINGS, {**load_fixture("settings"), "vacationMode": True})
    result = await tracker.async_send(DEVICE_UUID, VACATION, no_send())
    assert result.status is CommandStatus.UNCONFIRMED
    assert coordinator.data.devices[DEVICE_UUID].settings.vacation_mode is True


async def test_the_tracker_never_edits_the_data_itself(
    coordinator: AquastillaCoordinator, tracker: CommandTracker
) -> None:
    before = coordinator.data
    seen_during: list[Any] = []

    async def send() -> None:
        seen_during.append(coordinator.data)

    await tracker.async_send(DEVICE_UUID, VACATION, send)
    assert seen_during == [before]  # nothing optimistic while the command is in flight


# ---------------------------------------------------------- acknowledged late


async def test_a_late_acknowledgement_is_harmless_and_triggers_a_read(
    hass: HomeAssistant,
    backend: FakeBackend,
    coordinator: AquastillaCoordinator,
    tracker: CommandTracker,
) -> None:
    result = await tracker.async_send(DEVICE_UUID, VACATION, no_send())
    assert result.status is CommandStatus.UNCONFIRMED
    backend.requests.clear()
    backend.replace_json(SETTINGS, {**load_fixture("settings"), "vacationMode": True})

    tracker.handle_push(push(VACATION))
    await hass.async_block_till_done()

    assert not tracker.is_pending(DEVICE_UUID, VACATION)
    assert coordinator.data.devices[DEVICE_UUID].settings.vacation_mode is True


# ---------------------------------------------------------------- failures


async def test_a_failed_request_clears_pending_and_raises(
    backend: FakeBackend, tracker: CommandTracker
) -> None:
    async def send() -> None:
        msg = "HTTP 500"
        raise RuntimeError(msg)

    with pytest.raises(RuntimeError, match="HTTP 500"):
        await tracker.async_send(DEVICE_UUID, VACATION, send)
    assert not tracker.is_pending(DEVICE_UUID, VACATION)
    assert backend.requests == []  # nothing was sent that needs reading back


async def test_a_second_identical_command_is_refused_while_one_is_pending(
    tracker: CommandTracker,
) -> None:
    sent: list[int] = []
    release = asyncio.Event()

    async def slow_send() -> None:
        sent.append(1)
        await release.wait()

    first = asyncio.create_task(tracker.async_send(DEVICE_UUID, VACATION, slow_send))
    await settle()
    assert tracker.is_pending(DEVICE_UUID, VACATION)
    with pytest.raises(CommandPendingError):
        await tracker.async_send(DEVICE_UUID, VACATION, slow_send)
    assert sent == [1]
    release.set()
    await first


async def test_a_cancelled_send_leaves_nothing_pending(tracker: CommandTracker) -> None:
    release = asyncio.Event()

    async def hang() -> None:
        await release.wait()

    task = asyncio.create_task(tracker.async_send(DEVICE_UUID, VACATION, hang))
    await settle()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not tracker.is_pending(DEVICE_UUID, VACATION)


# --------------------------------------------------------------- matching


async def test_commands_of_different_types_and_devices_are_independent(
    tracker: CommandTracker,
) -> None:
    release = asyncio.Event()

    async def hang() -> None:
        await release.wait()

    first = asyncio.create_task(tracker.async_send(DEVICE_UUID, VACATION, hang))
    second = asyncio.create_task(
        tracker.async_send(DEVICE_UUID, DeviceEventType.SET_DEVICE_MODE, hang)
    )
    await settle()
    assert tracker.pending(DEVICE_UUID) == {str(VACATION), str(DeviceEventType.SET_DEVICE_MODE)}
    assert tracker.pending("other") == frozenset()
    release.set()
    await asyncio.gather(first, second)


async def test_an_acknowledgement_for_something_else_does_not_complete_the_command(
    tracker: CommandTracker,
) -> None:
    async def send() -> None:
        tracker.handle_push(push(DeviceEventType.SET_WATER_HARDNESS))  # another command
        tracker.handle_push(push(VACATION, "another-device"))  # another device

    result = await tracker.async_send(DEVICE_UUID, VACATION, send)
    assert result.status is CommandStatus.UNCONFIRMED


@pytest.mark.parametrize(
    "body", [None, "{broken", "[]", {}, {"uuid": DEVICE_UUID}, {"deviceEventType": "X"}]
)
async def test_garbage_pushes_are_ignored(
    backend: FakeBackend, hass: HomeAssistant, tracker: CommandTracker, body: object
) -> None:
    tracker.handle_push(body)
    await hass.async_block_till_done()
    assert backend.requests == []


# ------------------------------------------------------ device notifications


async def test_a_state_notification_reads_the_state_once(
    hass: HomeAssistant,
    backend: FakeBackend,
    coordinator: AquastillaCoordinator,
    tracker: CommandTracker,
    freezer: FrozenDateTimeFactory,
) -> None:
    freezer.tick(timedelta(minutes=1))
    backend.replace_json(STATE, {**load_fixture("state"), "saltDays": 9})
    tracker.handle_push(push(DeviceEventType.NEW_STATE_ARRIVED))
    await hass.async_block_till_done()
    assert paths(backend) == [STATE, CURRENT]
    assert coordinator.data.devices[DEVICE_UUID].state.salt_days == 9


async def test_a_burst_of_device_notifications_is_absorbed_by_the_cooldown(
    hass: HomeAssistant,
    backend: FakeBackend,
    tracker: CommandTracker,
    freezer: FrozenDateTimeFactory,
) -> None:
    freezer.tick(timedelta(minutes=1))
    for _ in range(10):
        tracker.handle_push(push(DeviceEventType.NEW_STATE_ARRIVED))
    await hass.async_block_till_done()
    assert paths(backend) == [STATE, CURRENT]


async def test_a_settings_notification_reads_settings_and_state(
    hass: HomeAssistant,
    backend: FakeBackend,
    tracker: CommandTracker,
    freezer: FrozenDateTimeFactory,
) -> None:
    freezer.tick(timedelta(minutes=1))
    tracker.handle_push(push(DeviceEventType.NEW_SETTINGS_ARRIVED))
    await hass.async_block_till_done()
    assert sorted(paths(backend)) == sorted([STATE, CURRENT, SETTINGS])


async def test_an_unknown_event_type_reads_the_state_only(
    hass: HomeAssistant,
    backend: FakeBackend,
    tracker: CommandTracker,
    freezer: FrozenDateTimeFactory,
) -> None:
    freezer.tick(timedelta(minutes=1))
    tracker.handle_push(push("SOMETHING_NEW"))
    await hass.async_block_till_done()
    assert paths(backend) == [STATE, CURRENT]


async def test_a_failing_listener_does_not_break_the_tracker(tracker: CommandTracker) -> None:
    def broken() -> None:
        msg = "listener bug"
        raise RuntimeError(msg)

    unsubscribe = tracker.add_listener(broken)
    result = await tracker.async_send(DEVICE_UUID, VACATION, no_send())
    assert result.status is CommandStatus.UNCONFIRMED
    unsubscribe()
