"""A leak found by the wireless sensor, the valve closed by an alarm, the time of a notification.

All three were learnt from a real leak simulation: the notification came with a date in
the device's local time, the history of the device did not change, and the valve closed
without the setting changing; only the cycle said so.
"""

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from homeassistant.const import STATE_OFF, STATE_ON
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from .common import load_fixture
from .control_helpers import BASE, SETTINGS, call, started, state_of
from .fake_backend import DEVICE_UUID, FakeBackend

CURRENT = f"{BASE}/current_states"


def at(monkeypatch: pytest.MonkeyPatch, moment: datetime) -> None:
    """The clock the binary sensors use. Home Assistant's own clock is left alone."""
    monkeypatch.setattr(
        "custom_components.aquastilla.binary_sensor.dt_util",
        SimpleNamespace(utcnow=lambda: moment),
    )


async def test_a_critical_notification_makes_the_leak_sensor_go_on(
    hass: HomeAssistant, backend: FakeBackend, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The fixture's log has a critical notification at 05:10 local, which is 04:10 UTC.
    at(monkeypatch, datetime(2026, 1, 2, 12, 0, tzinfo=UTC))
    await started(hass, backend)
    assert state_of(hass, "binary_sensor", "leak") == STATE_ON


async def test_an_old_notification_does_not(
    hass: HomeAssistant, backend: FakeBackend, monkeypatch: pytest.MonkeyPatch
) -> None:
    at(monkeypatch, datetime(2026, 1, 5, 12, 0, tzinfo=UTC))
    await started(hass, backend)
    assert state_of(hass, "binary_sensor", "leak") == STATE_OFF


async def test_a_notification_that_is_not_critical_does_not(
    hass: HomeAssistant, backend: FakeBackend, monkeypatch: pytest.MonkeyPatch
) -> None:
    page = load_fixture("event_pageable")
    for item in page["page"]["content"]:
        item["type"] = "Normal"
    backend.replace_json("/event/pageable", page)
    at(monkeypatch, datetime(2026, 1, 2, 12, 0, tzinfo=UTC))
    await started(hass, backend)
    assert state_of(hass, "binary_sensor", "leak") == STATE_OFF


async def test_the_date_of_a_pushed_notification_is_read_as_local_time(
    hass: HomeAssistant, backend: FakeBackend
) -> None:
    await started(hass, backend)
    await backend.push(
        "/device/event",
        {
            **load_fixture("ws_device_event"),
            "eventId": "evt-new",
            "date": "2026-01-02T18:02:44.200Z",
        },
    )
    await backend.wait_for(
        lambda: (
            (state := hass.states.get("event.synthetic_softener_notification")) is not None
            and state.attributes.get("event_id") == "evt-new"
        )
    )
    state = hass.states.get("event.synthetic_softener_notification")
    assert state is not None
    # 18:02 in Warsaw in January is 17:02 UTC.
    assert state.attributes["date"] == "2026-01-02T17:02:44.200000+00:00"


async def test_the_valve_closed_by_an_alarm_shows_in_the_cycle_and_not_in_the_setting(
    hass: HomeAssistant, backend: FakeBackend
) -> None:
    backend.replace_json(CURRENT, {"first": "deviceStateClosed", "second": "deviceStateSoftening"})
    await started(hass, backend)
    assert state_of(hass, "sensor", "cycle") == "flow_closed"
    assert state_of(hass, "binary_sensor", "water_flow") == STATE_OFF  # the setting says open


async def test_commands_are_refused_while_the_head_reports_the_flow_closed(
    hass: HomeAssistant, backend: FakeBackend
) -> None:
    backend.replace_json(CURRENT, {"first": "deviceStateClosed", "second": "deviceStateSoftening"})
    await started(hass, backend)
    with pytest.raises(HomeAssistantError, match="water flow is closed"):
        await call(hass, "button", "press", "delay_regeneration")


async def test_an_open_flow_with_a_normal_cycle_is_on(
    hass: HomeAssistant, backend: FakeBackend
) -> None:
    backend.replace_json(SETTINGS, {**load_fixture("settings"), "waterFlow": True})
    await started(hass, backend)
    assert state_of(hass, "binary_sensor", "water_flow") == STATE_ON
    assert DEVICE_UUID  # the fixture device
