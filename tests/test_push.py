"""The push channel inside the integration: notifications, update progress, catch-up."""

import asyncio
from collections.abc import Callable, Iterable
from datetime import timedelta
from typing import Any

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, State
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity import Entity

from custom_components.aquastilla import event as event_platform
from custom_components.aquastilla import sensor as sensor_platform
from custom_components.aquastilla.const import DOMAIN, EVENT_LOG_SIZE, EVENT_REFRESH_INTERVAL
from custom_components.aquastilla.event import event_type
from custom_components.aquastilla.models import VendorEvent
from custom_components.aquastilla.push import PushHub

from .common import load_fixture
from .fake_backend import DEVICE_UUID, FakeBackend
from .setup_helpers import make_entry, setup_entry

STATE_PATH = f"/device/{DEVICE_UUID}/state"
OTHER = "00000000-0000-4000-8000-0000000000bb"


async def until(predicate: Callable[[], bool], tries: int = 20000) -> None:
    """Let the loop run until ``predicate`` holds. It never sleeps, so frozen time is fine."""
    for _ in range(tries):
        if predicate():
            return
        await asyncio.sleep(0)
    msg = "the condition did not become true"
    raise AssertionError(msg)


async def started(hass: HomeAssistant, backend: FakeBackend) -> tuple[ConfigEntry, PushHub]:
    entry = make_entry()
    await setup_entry(hass, entry)
    hub = entry.runtime_data.push
    assert hub is not None
    await until(lambda: hub.catch_ups >= 1)  # connected and caught up
    return entry, hub


def entity_state(hass: HomeAssistant, domain: str, key: str, uuid: str = DEVICE_UUID) -> State:
    entity_id = er.async_get(hass).async_get_entity_id(domain, DOMAIN, f"{uuid}_{key}")
    assert entity_id is not None, key
    state = hass.states.get(entity_id)
    assert state is not None
    return state


def notification(**changes: Any) -> dict[str, Any]:
    return {**load_fixture("ws_device_event"), "eventId": "evt-9", **changes}


# ------------------------------------------------------------ the event entity


@pytest.mark.parametrize(
    ("vendor", "expected"),
    [
        ("Normal", "normal"),
        ("Critical", "critical"),
        ("ForcedByService", "forced_by_service"),
        ("ForcedByUser", "forced_by_user"),
        ("forced_by_user", "forced_by_user"),
        ("Surprise", "other"),
        (None, "other"),
    ],
)
def test_vendor_types_map_to_event_types(vendor: str | None, expected: str) -> None:
    assert event_type(vendor) == expected


async def test_a_pushed_notification_fires_an_event_with_its_type(
    hass: HomeAssistant, backend: FakeBackend
) -> None:
    await started(hass, backend)
    assert entity_state(hass, "event", "notification").attributes["event_type"] is None

    await backend.push("/device/event", notification(type="Critical", title="Leak"))
    await until(
        lambda: entity_state(hass, "event", "notification").attributes["event_type"] is not None
    )
    attributes = entity_state(hass, "event", "notification").attributes
    assert attributes["event_type"] == "critical"
    assert attributes["title"] == "Leak"
    assert attributes["event_id"] == "evt-9"
    assert attributes["vendor_type"] == "Critical"


async def test_a_notification_already_in_the_log_does_not_fire_twice(
    hass: HomeAssistant, backend: FakeBackend
) -> None:
    _, hub = await started(hass, backend)
    known = load_fixture("ws_device_event")  # evt-3 is the newest of the log
    assert known["eventId"] in {e.event_id for e in hub.events}
    await backend.push("/device/event", known)
    await backend.push("/device/event", notification())  # a marker that is processed after
    await until(lambda: hub.events[0].event_id == "evt-9")
    assert [e.event_id for e in hub.events].count("evt-3") == 1
    assert entity_state(hass, "event", "notification").attributes["event_id"] == "evt-9"


async def test_a_frame_without_content_is_ignored(
    hass: HomeAssistant, backend: FakeBackend
) -> None:
    _, hub = await started(hass, backend)
    before = hub.events
    await backend.push("/device/event", {"type": "Normal"})
    await backend.push("/device/event", notification())
    await until(lambda: hub.events[0].event_id == "evt-9")
    assert hub.events[1:] == before


async def test_the_log_is_capped(hass: HomeAssistant, backend: FakeBackend) -> None:
    _, hub = await started(hass, backend)
    for n in range(EVENT_LOG_SIZE + 5):
        await backend.push("/device/event", notification(eventId=f"extra-{n}"))
    await until(lambda: hub.events[0].event_id == f"extra-{EVENT_LOG_SIZE + 4}")
    assert len(hub.events) == EVENT_LOG_SIZE


async def test_notifications_are_never_acknowledged_upstream(
    hass: HomeAssistant, backend: FakeBackend
) -> None:
    _, hub = await started(hass, backend)
    await backend.push("/device/event", notification())
    await until(lambda: hub.events[0].event_id == "evt-9")
    forbidden = [r for r in backend.requests if r.path.startswith("/event/set_")]
    assert forbidden == []


# --------------------------------------------------------- which device it is


async def test_with_several_devices_the_name_decides(
    hass: HomeAssistant, backend: FakeBackend
) -> None:
    backend.add_device(OTHER)
    _, hub = await started(hass, backend)
    named = hub.events[0]
    assert sorted(hub.devices_for(named)) == sorted([DEVICE_UUID, OTHER])  # both have that name
    nobody = notification(additionalData={"deviceName": "Somebody else"})
    assert hub.devices_for(VendorEvent.from_json(nobody)) == []
    assert hub.devices_for(VendorEvent.from_json(notification(additionalData={}))) == []


# ------------------------------------------------------------ the last event


async def test_the_last_notification_is_read_from_the_log_at_start(
    hass: HomeAssistant, backend: FakeBackend
) -> None:
    await started(hass, backend)
    state = entity_state(hass, "sensor", "last_notification")
    assert state.state == "Synthetic leak alarm"
    events = state.attributes["events"]
    assert events[0]["title"] == "Synthetic leak alarm"
    assert len(events) == 3
    assert state.attributes["unseen"] == 1


async def test_a_push_moves_the_last_notification_on(
    hass: HomeAssistant, backend: FakeBackend
) -> None:
    await started(hass, backend)
    await backend.push("/device/event", notification(title="Salt is low"))
    await until(lambda: entity_state(hass, "sensor", "last_notification").state == "Salt is low")
    assert len(entity_state(hass, "sensor", "last_notification").attributes["events"]) == 4


async def test_without_notifications_the_sensor_is_unknown(
    hass: HomeAssistant, backend: FakeBackend
) -> None:
    backend.replace_json("/event/pageable", load_fixture("captured/event_pageable"))
    await started(hass, backend)
    state = entity_state(hass, "sensor", "last_notification")
    assert state.state == "unknown"
    assert state.attributes["events"] == []


async def test_a_long_title_is_cut_for_the_state(hass: HomeAssistant, backend: FakeBackend) -> None:
    await started(hass, backend)
    await backend.push("/device/event", notification(title="x" * 400))
    await until(lambda: entity_state(hass, "sensor", "last_notification").state.startswith("xxx"))
    assert len(entity_state(hass, "sensor", "last_notification").state) == 255


# ------------------------------------------------------------ update progress


def updating(percentage: float | None) -> dict[str, Any]:
    return {**load_fixture("state"), "isUpdate": True, "firmwareUpdatePercentage": percentage}


async def test_idle_is_zero(hass: HomeAssistant, backend: FakeBackend) -> None:
    await started(hass, backend)
    assert float(entity_state(hass, "sensor", "firmware_update_progress").state) == 0.0


async def test_progress_follows_the_push_and_returns_to_idle(
    hass: HomeAssistant, backend: FakeBackend
) -> None:
    entry, hub = await started(hass, backend)
    backend.replace_json(STATE_PATH, updating(10))
    await entry.runtime_data.async_refresh_state(DEVICE_UUID, force=True)
    await hass.async_block_till_done()
    assert float(entity_state(hass, "sensor", "firmware_update_progress").state) == 10.0

    await backend.push("/device/percentage", load_fixture("ws_percentage"))  # 42, upper-case id
    await until(lambda: hub.progress.get(DEVICE_UUID) == 42.0)
    assert float(entity_state(hass, "sensor", "firmware_update_progress").state) == 42.0

    # Finished: the push says 100 and the next read says the update is over.
    backend.replace_json(STATE_PATH, load_fixture("state"))
    await backend.push("/device/percentage", {**load_fixture("ws_percentage"), "percentage": 100})
    await until(lambda: len(backend.requests_to("GET", STATE_PATH)) >= 2)
    await hass.async_block_till_done()
    await until(
        lambda: float(entity_state(hass, "sensor", "firmware_update_progress").state) == 0.0
    )
    assert DEVICE_UUID not in hub.progress


async def test_progress_is_clamped_and_unknown_devices_are_ignored(
    hass: HomeAssistant, backend: FakeBackend
) -> None:
    backend.replace_json(STATE_PATH, updating(5))
    _, hub = await started(hass, backend)
    await backend.push("/device/percentage", {"uuid": DEVICE_UUID, "percentage": 250})
    await backend.push("/device/percentage", {"uuid": OTHER, "percentage": 50})
    await backend.push("/device/percentage", {"uuid": DEVICE_UUID, "percentage": "n/a"})
    await until(lambda: hub.progress.get(DEVICE_UUID) == 100.0)
    assert OTHER not in hub.progress


async def test_an_update_without_a_percentage_is_unknown_not_zero(
    hass: HomeAssistant, backend: FakeBackend
) -> None:
    backend.replace_json(STATE_PATH, updating(None))
    await started(hass, backend)
    assert entity_state(hass, "sensor", "firmware_update_progress").state == "unknown"


# ----------------------------------------------------- losing and finding it


async def test_a_reconnect_reads_the_log_and_the_data_again(
    hass: HomeAssistant, backend: FakeBackend, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The cooldown would make a reconnect seconds after a poll free. Take it away.
    monkeypatch.setattr("custom_components.aquastilla.push.PUSH_REFRESH_COOLDOWN", timedelta(0))
    monkeypatch.setattr(
        "custom_components.aquastilla.coordinator.PUSH_REFRESH_COOLDOWN", timedelta(0)
    )
    _, hub = await started(hass, backend)
    reads = len(backend.requests_to("GET", "/event/pageable"))
    states = len(backend.requests_to("GET", STATE_PATH))
    await backend.drop_connections()
    await backend.wait_for(lambda: hub.catch_ups >= 2, within=10)  # one real second of backoff
    assert len(backend.requests_to("GET", "/event/pageable")) == reads + 1
    assert len(backend.requests_to("GET", STATE_PATH)) == states + 1


async def test_while_the_channel_is_down_the_log_is_read_now_and_then(
    hass: HomeAssistant, backend: FakeBackend, freezer: FrozenDateTimeFactory
) -> None:
    entry, hub = await started(hass, backend)
    assert entry.runtime_data.websocket is not None
    await entry.runtime_data.websocket.stop()
    reads = len(backend.requests_to("GET", "/event/pageable"))

    hub.on_coordinator_update()
    await hass.async_block_till_done(wait_background_tasks=True)
    assert len(backend.requests_to("GET", "/event/pageable")) == reads  # too soon

    freezer.tick(EVENT_REFRESH_INTERVAL + timedelta(seconds=1))
    hub.on_coordinator_update()
    await hass.async_block_till_done(wait_background_tasks=True)
    assert len(backend.requests_to("GET", "/event/pageable")) == reads + 1


async def test_while_the_channel_is_up_the_log_is_not_polled(
    hass: HomeAssistant, backend: FakeBackend, freezer: FrozenDateTimeFactory
) -> None:
    _, hub = await started(hass, backend)
    reads = len(backend.requests_to("GET", "/event/pageable"))
    freezer.tick(EVENT_REFRESH_INTERVAL * 3)
    hub.on_coordinator_update()
    await hass.async_block_till_done(wait_background_tasks=True)
    assert len(backend.requests_to("GET", "/event/pageable")) == reads


async def test_an_unreadable_log_is_not_fatal(hass: HomeAssistant, backend: FakeBackend) -> None:
    backend.fail_always("GET", "/event/pageable", status=500)
    entry = make_entry()
    await setup_entry(hass, entry)
    assert entry.state.value == "loaded"
    assert entity_state(hass, "sensor", "last_notification").state == "unknown"


async def test_rejected_credentials_on_the_channel_start_reauthentication(
    hass: HomeAssistant, backend: FakeBackend
) -> None:
    entry = make_entry()
    await setup_entry(hass, entry)
    backend.expire_tokens()
    backend.fail_always("POST", "/login-company", status=401, json_body={"code": -1})
    await backend.drop_connections()
    await backend.wait_for(
        lambda: any(
            flow["context"]["source"] == "reauth"
            for flow in hass.config_entries.flow.async_progress()
        ),
        within=20,
    )


# ------------------------------------------------------------- robustness


async def test_a_failing_listener_does_not_stop_the_others(
    hass: HomeAssistant, backend: FakeBackend
) -> None:
    _, hub = await started(hass, backend)

    def broken(*_: object) -> None:
        raise RuntimeError

    hub.add_event_listener(broken)
    hub.add_listener(broken)
    await backend.push("/device/event", notification(title="After the failure"))
    await until(
        lambda: entity_state(hass, "sensor", "last_notification").state == "After the failure"
    )
    assert entity_state(hass, "event", "notification").attributes["title"] == "After the failure"


async def test_a_percentage_without_a_device_is_ignored(
    hass: HomeAssistant, backend: FakeBackend
) -> None:
    _, hub = await started(hass, backend)
    await backend.push("/device/percentage", {"percentage": 40})
    await backend.push("/device/event", notification())  # processed after the one above
    await until(lambda: hub.events[0].event_id == "evt-9")
    assert hub.progress == {}


async def test_two_devices_with_the_same_name_each_get_the_notification(
    hass: HomeAssistant, backend: FakeBackend
) -> None:
    backend.add_device(OTHER)
    await started(hass, backend)
    await backend.push("/device/event", notification(type="Normal", title="For both"))
    await until(
        lambda: all(
            entity_state(hass, "event", "notification", uuid).attributes["event_type"] == "normal"
            for uuid in (DEVICE_UUID, OTHER)
        )
    )


async def test_without_a_push_hub_no_push_entities_are_made(
    hass: HomeAssistant, backend: FakeBackend
) -> None:
    entry, _ = await started(hass, backend)
    entry.runtime_data.push = None
    added: list[Any] = []

    def collect(
        new_entities: Iterable[Entity],
        update_before_add: bool = False,
        *,
        config_subentry_id: str | None = None,
    ) -> None:
        added.extend(new_entities)

    await event_platform.async_setup_entry(hass, entry, collect)
    assert added == []
    await sensor_platform.async_setup_entry(hass, entry, collect)
    assert added
    assert not [
        e for e in added if type(e).__name__ in {"FirmwareUpdateProgress", "LastNotification"}
    ]


async def test_a_state_that_does_not_say_whether_it_is_updating_is_unknown(
    hass: HomeAssistant, backend: FakeBackend
) -> None:
    state = load_fixture("state")
    del state["isUpdate"]
    backend.replace_json(STATE_PATH, state)
    await started(hass, backend)
    assert entity_state(hass, "sensor", "firmware_update_progress").state == "unknown"
