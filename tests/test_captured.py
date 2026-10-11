"""Anonymised captures from a real device, and what the integration makes of them.

The files in tests/fixtures/captured/ come from one read-only session against the real
service. Identifiers are placeholders and dates are shifted by whole days into summer,
so the device clock keeps its two-hour lead on UTC.
"""

import json
from datetime import UTC, datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

from custom_components.aquastilla import models
from custom_components.aquastilla.coordinator import DeviceData
from custom_components.aquastilla.models import Cycle, DeviceEventType

CAPTURED = Path(__file__).parent / "fixtures" / "captured"
WARSAW = ZoneInfo("Europe/Warsaw")


def captured(name: str) -> object:
    return json.loads((CAPTURED / f"{name}.json").read_text(encoding="utf-8"))


def test_there_is_a_capture_of_every_read_endpoint() -> None:
    names = {p.stem for p in CAPTURED.glob("*.json")}
    assert {
        "details", "device", "device_all", "state", "settings", "current_states", "mode_all",
        "water_data", "year_water_data", "previous_current_water_usage", "event_pageable",
        "ws_authenticate_response", "ws_event_seen", "ws_ping",
    } <= names  # fmt: skip


def test_the_device_has_a_serial_and_its_product_names() -> None:
    device = models.Device.from_json(captured("device"))
    assert device.serial == "SYNTHETIC0000001"
    assert device.model == "AQUASTILLA DUO SMART"
    assert device.brand == "WATERLIFE"
    assert device.mac_address == "00:00:5E:00:53:01"
    assert [d.uuid for d in models.parse_device_list(captured("device_all"))] == [device.uuid]


def test_state_has_the_usage_fields_and_a_known_cycle() -> None:
    state = models.DeviceState.from_json(captured("state"))
    assert state.cycle is Cycle.SOFTENING
    assert state.is_online is True
    assert state.salt_percent == 50
    assert state.salt_days == 35
    assert state.salt_days_max == 70
    assert state.today_water_usage == 0.0
    assert state.current_water_usage == 0.0
    assert state.water_left == 3.5  # cubic metres, although the unit setting says litres
    assert state.water_left_max == 3.6


def test_settings_report_litres_and_the_device_timezone() -> None:
    settings = models.DeviceSettings.from_json(captured("settings"))
    assert settings.volume_unit == "l"
    assert settings.timezone == "Europe/Warsaw"
    assert settings.regeneration_time == time(0, 1)
    assert settings.water_hardness_unit == "dH"
    assert settings.salt_alarm_level == 10
    assert settings.vacation_mode is False
    assert settings.water_flow is True


def test_the_mode_list_of_a_real_device_maps_to_known_cycles() -> None:
    modes = models.parse_mode_list(captured("mode_all"))
    assert modes == [
        Cycle.INITIALIZE,
        Cycle.SOFTENING,
        Cycle.VACATION_MODE,  # "deviceStateVacation"
        Cycle.FLOW_CLOSED,  # "deviceStateClosed"
        Cycle.BRINE_REFILL,
        Cycle.SALT_DISSOLVE,
        Cycle.BACKWASH,
        Cycle.BRINE_COLLECT,
        Cycle.FAST_WASH,
        "deviceStateNum",
    ]


def test_current_states_use_the_display_vocabulary() -> None:
    states = models.CurrentStates.from_json(captured("current_states"))
    assert states.current is Cycle.SOFTENING
    assert states.next is Cycle.BRINE_REFILL


def test_water_series_are_the_four_the_service_returns() -> None:
    series = models.parse_water_data(captured("water_data"))
    assert {k: len(v.values) for k, v in series.items()} == {
        "WATER_HOUR_DATA": 24,
        "WATER_DAY_DATA": 7,
        "WATER_MONTH_DATA": 31,
        "WATER_12_MONTH_DATA": 12,
    }
    assert all(v.timestamp is not None for v in series.values())


def test_the_year_endpoint_answers_with_one_series_under_a_date() -> None:
    year = models.parse_year_water_data(captured("year_water_data"))
    assert len(year) == 1
    assert len(next(iter(year.values())).values) == 12


def test_the_empty_event_page_parses() -> None:
    page = models.EventPage.from_json(captured("event_pageable"))
    assert page.events == ()
    assert page.unseen == 0


def test_the_event_seen_push_has_epoch_seconds_and_a_text_params() -> None:
    frame = captured("ws_event_seen")
    assert isinstance(frame, dict)
    event = models.EventSeen.from_json(frame["body"])  # the body is a JSON string
    assert event.event_type is DeviceEventType.NEW_STATE_ARRIVED
    assert event.params == ""
    assert event.timestamp is not None
    assert event.timestamp.tzinfo is not None
    assert event.timestamp.year == 2026
    assert event.uuid == "000000000000000000000001"


def test_the_authenticate_reply_and_the_ping_have_the_shapes_the_client_expects() -> None:
    reply = captured("ws_authenticate_response")
    ping = captured("ws_ping")
    assert isinstance(reply, dict)
    assert isinstance(ping, dict)
    assert reply["path"] == "/authenticate/response"  # not "/authenticate"
    assert "responseFor" in reply["headers"]
    assert reply["body"] is None
    assert ping["path"] == "/ping"
    assert ping["headers"] == {}
    assert ping["body"] is None


# ----------------------------------------------------------------- time


def device_data() -> DeviceData:
    return DeviceData(
        device=models.Device.from_json(captured("device")),
        state=models.DeviceState.from_json(captured("state")),
        settings=models.DeviceSettings.from_json(captured("settings")),
        current_states=models.CurrentStates.from_json(captured("current_states")),
    )


def test_the_device_clock_is_local_time_labelled_utc() -> None:
    """The captured clock reads 20:40 with a +00:00 label; the real instant is 18:40 UTC."""
    data = device_data()
    assert data.state.timestamp == datetime(
        2026, 7, 3, 20, 40, tzinfo=UTC
    )  # as the service wrote it
    assert data.timezone == WARSAW
    assert data.instant(data.state.timestamp) == datetime(2026, 7, 3, 18, 40, tzinfo=UTC)


def test_the_expected_regeneration_is_the_users_local_time() -> None:
    data = device_data()
    expected = data.instant(data.state.expected_regeneration)
    assert expected is not None
    assert expected.astimezone(WARSAW).time() == time(0, 1)  # the hour the user chose
    assert data.settings.regeneration_time == time(0, 1)


def test_the_push_timestamp_is_not_shifted() -> None:
    frame = captured("ws_event_seen")
    assert isinstance(frame, dict)
    event = models.EventSeen.from_json(frame["body"])
    # Real UTC already: 18:41, a minute after the device clock's real instant of 18:40.
    assert event.timestamp == datetime(2026, 7, 3, 18, 41, 0, tzinfo=UTC)
