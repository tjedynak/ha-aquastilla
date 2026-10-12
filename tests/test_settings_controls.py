"""Thresholds, regeneration time, volume unit, note, notification flags, default device."""

import asyncio
from datetime import time
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError

from custom_components.aquastilla.api import AquastillaClient
from custom_components.aquastilla.models import DeviceEventType

from .common import load_fixture
from .control_helpers import (
    BASE,
    SETTINGS,
    ack,
    command_and_ack,
    enable_entities,
    entity_id,
    started,
    state_of,
)
from .fake_backend import DEVICE_UUID, FakeBackend


async def set_number(hass: HomeAssistant, key: str, value: float) -> None:
    await hass.services.async_call(
        "number",
        "set_value",
        {"entity_id": entity_id(hass, "number", key), "value": value},
        blocking=True,
    )


# ------------------------------------------------------------------- numbers


async def test_the_thresholds_show_what_the_device_reports(
    hass: HomeAssistant, backend: FakeBackend
) -> None:
    await started(hass, backend)
    assert float(state_of(hass, "number", "salt_alarm_level")) == 20
    assert float(state_of(hass, "number", "leak_continuous_flow_time")) == 60
    assert float(state_of(hass, "number", "leak_single_outflow")) == 0
    assert float(state_of(hass, "number", "leak_max_flow")) == 30


async def test_the_salt_threshold_is_a_bare_number(
    hass: HomeAssistant, backend: FakeBackend
) -> None:
    await started(hass, backend)
    path = f"{BASE}/settings/alarm/salt"
    await command_and_ack(
        hass,
        backend,
        lambda: set_number(hass, "salt_alarm_level", 35),
        path,
        DeviceEventType.SET_MINIMUM_SALT_LEVEL,
    )
    assert backend.requests_to("POST", path)[0].body == 35


@pytest.mark.parametrize(
    ("key", "expected"),
    [
        ("leak_continuous_flow_time", {"continuousFlowTime": 90, "threshold": 0, "maxFlow": 30}),
        ("leak_single_outflow", {"continuousFlowTime": 60, "threshold": 90, "maxFlow": 30}),
        ("leak_max_flow", {"continuousFlowTime": 60, "threshold": 0, "maxFlow": 90}),
    ],
)
async def test_a_leak_threshold_is_sent_with_the_other_two(
    hass: HomeAssistant, backend: FakeBackend, key: str, expected: dict[str, int]
) -> None:
    await started(hass, backend)
    path = f"{BASE}/settings/alarm/flood"
    task = asyncio.ensure_future(set_number(hass, key, 90))
    await backend.wait_for(lambda: bool(backend.requests_to("PATCH", path)))
    await ack(backend, DeviceEventType.SET_FLOOD_SETTINGS)
    await task
    assert backend.requests_to("PATCH", path)[0].body == expected


async def test_a_leak_threshold_is_refused_while_the_others_are_unknown(
    hass: HomeAssistant, backend: FakeBackend
) -> None:
    settings = load_fixture("settings")
    settings["floodAlarmSettings"] = {"continuousFlowTime": None, "threshold": 5, "maxFlow": 30}
    backend.replace_json(SETTINGS, settings)
    await started(hass, backend)
    with pytest.raises(HomeAssistantError, match="not known yet"):
        await set_number(hass, "leak_max_flow", 40)
    assert backend.requests_to("PATCH", f"{BASE}/settings/alarm/flood") == []


async def test_the_thresholds_have_the_ranges_of_the_app(
    hass: HomeAssistant, backend: FakeBackend
) -> None:
    await started(hass, backend)
    ranges = {
        key: (
            hass.states.get(entity_id(hass, "number", key)).attributes["min"],  # type: ignore[union-attr]
            hass.states.get(entity_id(hass, "number", key)).attributes["max"],  # type: ignore[union-attr]
        )
        for key in (
            "salt_alarm_level",
            "leak_continuous_flow_time",
            "leak_single_outflow",
            "leak_max_flow",
        )
    }
    assert ranges == {
        "salt_alarm_level": (1, 99),
        "leak_continuous_flow_time": (1, 14400),
        "leak_single_outflow": (1, 10000),
        "leak_max_flow": (1, 500),
    }


async def test_a_value_outside_the_range_never_reaches_the_device(
    hass: HomeAssistant, backend: FakeBackend
) -> None:
    await started(hass, backend)
    with pytest.raises(ServiceValidationError, match="outside valid range"):
        await set_number(hass, "salt_alarm_level", 0)
    assert backend.requests_to("POST", f"{BASE}/settings/alarm/salt") == []


async def test_settings_are_refused_in_service_mode_and_still_show_their_value(
    hass: HomeAssistant, backend: FakeBackend
) -> None:
    backend.replace_json(SETTINGS, {**load_fixture("settings"), "serviceMode": True})
    await started(hass, backend)
    for domain, key in (
        ("number", "salt_alarm_level"),
        ("time", "regeneration_time"),
        ("select", "volume_unit"),
    ):
        state = hass.states.get(entity_id(hass, domain, key))
        assert state is not None
        assert state.state != STATE_UNAVAILABLE
        assert state.attributes["blocked_reason"] == "service_mode"
    with pytest.raises(HomeAssistantError, match="remote service mode"):
        await set_number(hass, "salt_alarm_level", 30)
    assert backend.requests_to("POST", f"{BASE}/settings/alarm/salt") == []


# ------------------------------------------------------------ the regeneration time


async def test_the_regeneration_time_is_the_wall_clock_of_the_device(
    hass: HomeAssistant, backend: FakeBackend
) -> None:
    await started(hass, backend)
    # The fixture says 00:01 labelled UTC: the digits are the device's local time.
    assert state_of(hass, "time", "regeneration_time") == "00:01:00"


async def test_a_regeneration_time_goes_out_with_the_same_digits(
    hass: HomeAssistant, backend: FakeBackend
) -> None:
    await started(hass, backend)
    path = f"{BASE}/settings/regeneration_time"
    await command_and_ack(
        hass,
        backend,
        lambda: hass.services.async_call(
            "time",
            "set_value",
            {"entity_id": entity_id(hass, "time", "regeneration_time"), "time": time(3, 45)},
            blocking=True,
        ),
        path,
        DeviceEventType.SET_PREFERRED_REGENERATION_TIME,
    )
    body = backend.requests_to("POST", path)[0].body
    assert isinstance(body, str)
    assert body.endswith("T03:45:00.000Z")


# ------------------------------------------------------------------ volume unit


async def test_the_volume_unit_is_shown_as_an_option(
    hass: HomeAssistant, backend: FakeBackend
) -> None:
    await started(hass, backend)
    assert state_of(hass, "select", "volume_unit") == "m3"  # what the fixture says


async def test_a_volume_unit_is_sent_as_bare_text_and_the_settings_are_read_again(
    hass: HomeAssistant, backend: FakeBackend
) -> None:
    await started(hass, backend)
    reads = len(backend.requests_to("GET", SETTINGS))
    await hass.services.async_call(
        "select",
        "select_option",
        {"entity_id": entity_id(hass, "select", "volume_unit"), "option": "l"},
        blocking=True,
    )
    request = backend.requests_to("POST", f"{BASE}/settings/volume_unit")[0]
    assert request.body == "l"
    assert len(backend.requests_to("GET", SETTINGS)) > reads


async def test_an_unknown_unit_has_no_option(hass: HomeAssistant, backend: FakeBackend) -> None:
    backend.replace_json(SETTINGS, {**load_fixture("settings"), "unitOfVolume": "gallons"})
    await started(hass, backend)
    assert state_of(hass, "select", "volume_unit") == STATE_UNKNOWN


# ----------------------------------------------------------------------- note


async def test_the_note_is_off_by_default(hass: HomeAssistant, backend: FakeBackend) -> None:
    await started(hass, backend)
    assert hass.states.get(entity_id(hass, "text", "note")) is None  # it can hold private text


async def test_the_note_is_shown_and_set(hass: HomeAssistant, backend: FakeBackend) -> None:
    device = load_fixture("device")
    device["note"] = "Brine tank is behind the door"
    backend.replace_json("/device/all", [device])
    await enable_entities(hass, backend, await started(hass, backend), ("text", "note"))
    assert state_of(hass, "text", "note") == "Brine tank is behind the door"

    await hass.services.async_call(
        "text",
        "set_value",
        {"entity_id": entity_id(hass, "text", "note"), "value": "  New note "},
        blocking=True,
    )
    request = backend.requests_to("POST", f"{BASE}/settings/note")[0]
    assert request.body == "New note"
    assert request.headers["Content-Type"].startswith("text/plain")
    assert len(backend.requests_to("GET", "/device/all")) >= 2  # the list was read again


async def test_a_note_longer_than_a_state_can_hold_is_cut_not_raised(
    hass: HomeAssistant, backend: FakeBackend
) -> None:
    device = load_fixture("device")
    device["note"] = "x" * 400
    backend.replace_json("/device/all", [device])
    await enable_entities(hass, backend, await started(hass, backend), ("text", "note"))
    assert len(state_of(hass, "text", "note")) == 255


async def test_an_empty_note_removes_it(hass: HomeAssistant, backend: FakeBackend) -> None:
    await enable_entities(hass, backend, await started(hass, backend), ("text", "note"))
    await hass.services.async_call(
        "text",
        "set_value",
        {"entity_id": entity_id(hass, "text", "note"), "value": ""},
        blocking=True,
    )
    assert len(backend.requests_to("DELETE", f"{BASE}/settings/note")) == 1
    assert backend.requests_to("POST", f"{BASE}/settings/note") == []


# ------------------------------------------------------- notification switches


async def test_the_notification_flags_reflect_the_vendor_side(
    hass: HomeAssistant, backend: FakeBackend
) -> None:
    await started(hass, backend)
    assert state_of(hass, "switch", "salt_alarm_push") == STATE_ON
    assert state_of(hass, "switch", "salt_alarm_email") == STATE_OFF
    assert state_of(hass, "switch", "flood_alarm_push") == STATE_ON
    assert state_of(hass, "switch", "flood_alarm_email") == STATE_OFF


@pytest.mark.parametrize(
    ("key", "path", "expected"),
    [
        (
            "salt_alarm_email",
            "/settings/notification/salt",
            {"pushNotification": True, "emailNotification": True},
        ),
        (
            "flood_alarm_email",
            "/settings/notification/flood",
            {"pushNotification": True, "emailNotification": True},
        ),
    ],
)
async def test_turning_a_flag_on_sends_the_whole_object(
    hass: HomeAssistant, backend: FakeBackend, key: str, path: str, expected: dict[str, bool]
) -> None:
    await started(hass, backend)
    await hass.services.async_call(
        "switch", "turn_on", {"entity_id": entity_id(hass, "switch", key)}, blocking=True
    )
    assert backend.requests_to("POST", f"{BASE}{path}")[0].body == expected


async def test_fields_the_client_does_not_know_are_sent_back(
    hass: HomeAssistant, backend: FakeBackend
) -> None:
    device = load_fixture("device")
    device["saltAlarmNotificationSettings"] = {
        "pushNotification": True,
        "emailNotification": False,
        "smsNotification": False,
    }
    backend.replace_json("/device/all", [device])
    await started(hass, backend)
    await hass.services.async_call(
        "switch",
        "turn_off",
        {"entity_id": entity_id(hass, "switch", "salt_alarm_push")},
        blocking=True,
    )
    body = backend.requests_to("POST", f"{BASE}/settings/notification/salt")[0].body
    assert body == {"pushNotification": False, "emailNotification": False, "smsNotification": False}


async def test_a_flag_that_is_not_known_cannot_be_changed(
    hass: HomeAssistant, backend: FakeBackend
) -> None:
    device = load_fixture("device")
    device["saltAlarmNotificationSettings"] = {"pushNotification": True}
    backend.replace_json("/device/all", [device])
    await started(hass, backend)
    with pytest.raises(HomeAssistantError, match="not known yet"):
        await hass.services.async_call(
            "switch",
            "turn_on",
            {"entity_id": entity_id(hass, "switch", "salt_alarm_push")},
            blocking=True,
        )


# --------------------------------------------------------------- default device


async def test_the_default_device_can_be_made_but_not_cleared(
    hass: HomeAssistant, backend: FakeBackend
) -> None:
    device = load_fixture("device")
    device["isDefault"] = False
    backend.replace_json("/device/all", [device])
    await started(hass, backend)
    assert state_of(hass, "switch", "default_device") == STATE_OFF
    await hass.services.async_call(
        "switch",
        "turn_on",
        {"entity_id": entity_id(hass, "switch", "default_device")},
        blocking=True,
    )
    assert len(backend.requests_to("POST", f"/device/default/{DEVICE_UUID}")) == 1

    with pytest.raises(HomeAssistantError, match="another one"):
        await hass.services.async_call(
            "switch",
            "turn_off",
            {"entity_id": entity_id(hass, "switch", "default_device")},
            blocking=True,
        )


# ------------------------------------------------------------- read only


async def test_the_hardness_is_a_reading_and_not_yet_a_control(
    hass: HomeAssistant, backend: FakeBackend
) -> None:
    await started(hass, backend)
    assert float(state_of(hass, "sensor", "water_hardness")) == 18
    state = hass.states.get(entity_id(hass, "sensor", "water_hardness"))
    assert state is not None
    assert state.attributes["unit_of_measurement"] == "dH"


async def test_two_flags_changed_at_once_do_not_undo_each_other(
    hass: HomeAssistant, backend: FakeBackend
) -> None:
    await started(hass, backend)
    device = load_fixture("device")
    sent: list[dict[str, bool]] = []

    async def write(uuid: str, payload: dict[str, bool]) -> None:
        # What the vendor would now answer with when the list is read again.
        sent.append(dict(payload))
        backend.replace_json(
            "/device/all", [{**device, "saltAlarmNotificationSettings": dict(payload)}]
        )

    with patch.object(AquastillaClient, "set_salt_notifications", new=AsyncMock(side_effect=write)):
        await asyncio.gather(
            hass.services.async_call(
                "switch",
                "turn_off",
                {"entity_id": entity_id(hass, "switch", "salt_alarm_push")},
                blocking=True,
            ),
            hass.services.async_call(
                "switch",
                "turn_on",
                {"entity_id": entity_id(hass, "switch", "salt_alarm_email")},
                blocking=True,
            ),
        )
    assert len(sent) == 2
    # Whichever went second started from the result of the first: both changes are there.
    assert sent[-1] == {"pushNotification": False, "emailNotification": True}
