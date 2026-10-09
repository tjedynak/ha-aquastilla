"""Models parse every synthetic fixture and survive broken or unexpected input."""

import json
import math
from collections.abc import Callable
from datetime import UTC, datetime, time
from typing import Any

import pytest

from custom_components.aquastilla import models
from custom_components.aquastilla.models import (
    CurrentStates,
    Cycle,
    Device,
    DeviceEventType,
    DeviceHistory,
    DeviceSettings,
    DeviceState,
    EventPage,
    EventSeen,
    FirmwareProgress,
    LoginResponse,
    ModelError,
    UserDetails,
    VendorError,
    VendorEvent,
    WaterSeries,
)

from .common import FIXTURES, load_fixture
from .fake_backend import DEVICE_UUID

# fixture name -> function that parses it. Every fixture must appear here, so a new
# fixture cannot be added without a model that reads it.
PARSERS: dict[str, Callable[[Any], object]] = {
    "login_company": LoginResponse.from_json,
    "details": UserDetails.from_json,
    "device": Device.from_json,
    "device_all": models.parse_device_list,
    "state": DeviceState.from_json,
    "settings": DeviceSettings.from_json,
    "current_states": CurrentStates.from_json,
    "mode_all": models.parse_mode_list,
    "water_data": models.parse_water_data,
    "year_water_data": models.parse_year_water_data,
    "previous_current_water_usage": models.parse_usage_comparison,
    "event_pageable": EventPage.from_json,
    "error_device_detached": VendorError.from_json,
    "ws_device_event": VendorEvent.from_push,
    "ws_event_seen": EventSeen.from_json,
    "ws_percentage": FirmwareProgress.from_json,
}

# Parsers that read an object and fall back to all-``None`` on anything else.
OBJECT_MODELS = [
    LoginResponse,
    UserDetails,
    DeviceState,
    DeviceSettings,
    CurrentStates,
    EventPage,
    VendorError,
    VendorEvent,
    EventSeen,
    FirmwareProgress,
    DeviceHistory,
    WaterSeries,
]


def with_unknown_keys(value: Any) -> Any:
    """Copy of a JSON structure with a field the models have never heard of everywhere."""
    if isinstance(value, dict):
        return {**{k: with_unknown_keys(v) for k, v in value.items()}, "newVendorField": {"x": 1}}
    if isinstance(value, list):
        return [with_unknown_keys(v) for v in value]
    return value


# ------------------------------------------------------------ every fixture


def test_every_fixture_has_a_parser() -> None:
    names = {path.stem for path in FIXTURES.glob("*.json")}
    assert names == set(PARSERS)


@pytest.mark.parametrize("name", sorted(PARSERS))
def test_parser_accepts_its_fixture(name: str) -> None:
    assert PARSERS[name](load_fixture(name)) is not None


KEYED_FIXTURES = {"water_data", "year_water_data", "previous_current_water_usage"}
LIST_FIXTURES = {"mode_all", "device_all"}


@pytest.mark.parametrize("name", sorted(set(PARSERS) - KEYED_FIXTURES - LIST_FIXTURES))
def test_unknown_vendor_fields_do_not_change_the_result(name: str) -> None:
    original = load_fixture(name)
    assert PARSERS[name](with_unknown_keys(original)) == PARSERS[name](original)


def test_unknown_keys_in_list_items_are_ignored() -> None:
    items = with_unknown_keys(load_fixture("device_all"))
    assert models.parse_device_list(items) == models.parse_device_list(load_fixture("device_all"))


def test_unknown_fields_inside_series_and_pairs_are_ignored() -> None:
    series = {name: with_unknown_keys(value) for name, value in load_fixture("water_data").items()}
    assert models.parse_water_data(series) == models.parse_water_data(load_fixture("water_data"))
    usage = load_fixture("previous_current_water_usage")
    pairs = {name: with_unknown_keys(value) for name, value in usage.items()}
    assert models.parse_usage_comparison(pairs) == models.parse_usage_comparison(usage)


def test_unknown_series_names_are_kept_not_fatal() -> None:
    data = {
        **load_fixture("water_data"),
        "NEW_SERIES": {"data": [1], "chartLine": 2, "timestamp": None},
    }
    parsed = models.parse_water_data(data)
    assert len(parsed) == 9
    assert parsed["NEW_SERIES"].values == (1.0,)
    assert (
        parsed["WATER_DAY_DATA"]
        == models.parse_water_data(load_fixture("water_data"))["WATER_DAY_DATA"]
    )


# ------------------------------------------------------------ fixture values


def test_device_values() -> None:
    device = Device.from_json(load_fixture("device"))
    assert device.uuid == DEVICE_UUID
    assert device.name == "Synthetic softener"
    assert device.note is None  # an empty note is "no note"
    assert device.mac_address == "00:00:5E:00:53:01"
    assert device.model == "SYNTHETIC DUO"
    assert device.brand == "SYNTHETIC"
    assert device.history.regeneration == datetime(2026, 1, 2, 0, 1, tzinfo=UTC)
    assert device.history.inspection is None
    assert device.salt_notifications.push is True
    assert device.flood_notifications.email is False


def test_state_values() -> None:
    state = DeviceState.from_json(load_fixture("state"))
    assert state.uuid == DEVICE_UUID
    assert state.cycle is Cycle.SOFTENING
    assert state.is_online is True
    assert state.is_update is False
    assert state.water_left == pytest.approx(4.046)
    assert state.salt_percent == 50
    assert state.salt_days == 41
    assert state.regeneration_percent is None
    assert state.expected_regeneration == datetime(2026, 1, 5, 0, 1, tzinfo=UTC)
    assert state.timestamp == datetime(2026, 1, 2, 7, 34, tzinfo=UTC)


def test_settings_values() -> None:
    settings = DeviceSettings.from_json(load_fixture("settings"))
    assert settings.vacation_mode is False
    assert settings.water_flow is True
    assert settings.service_mode_ends is None
    assert settings.water_hardness == 18
    assert settings.water_hardness_unit == "dH"
    assert settings.volume_unit == "m3"
    assert settings.regeneration_time == time(0, 1)
    assert settings.salt_alarm_level == 20
    assert settings.flood_alarm.max_flow == 30
    assert settings.flood_alarm.threshold == 0
    assert settings.timezone == "Europe/Warsaw"


def test_current_states_and_modes_use_one_vocabulary() -> None:
    states = CurrentStates.from_json(load_fixture("current_states"))
    assert states.current is Cycle.SOFTENING
    assert states.next is Cycle.BRINE_REFILL
    modes = models.parse_mode_list(load_fixture("mode_all"))
    assert modes[0] is Cycle.SOFTENING
    assert Cycle.BRINE_REFILL in modes
    assert len(modes) == 6


def test_water_series_keep_the_vendor_order() -> None:
    series = models.parse_water_data(load_fixture("water_data"))
    assert set(series) == {
        f"{k}_{p}_DATA" for k in ("WATER", "FLOW") for p in ("HOUR", "DAY", "MONTH", "YEAR")
    }
    hour = series["WATER_HOUR_DATA"]
    assert len(hour.values) == 24
    assert hour.values[:4] == (0.0, 0.0, 0.0, 12.0)
    assert hour.chart_line == 20
    assert hour.timestamp == datetime(2026, 1, 2, 7, 0, tzinfo=UTC)


def test_year_water_data_groups_by_key() -> None:
    year = models.parse_year_water_data(load_fixture("year_water_data"))
    assert set(year) == {"2025-01-01", "2024-01-01"}
    assert len(year["2025-01-01"]) == 8
    assert len(year["2024-01-01"]) == 4


def test_usage_comparison() -> None:
    usage = models.parse_usage_comparison(load_fixture("previous_current_water_usage"))
    assert usage["WATER_MONTH_DATA"].first == 3340
    assert usage["WATER_MONTH_DATA"].second == 28


def test_event_page() -> None:
    page = EventPage.from_json(load_fixture("event_pageable"))
    assert page.unseen == 1
    assert [e.event_id for e in page.events] == ["evt-3", "evt-2", "evt-1"]
    assert page.events[0].seen is False
    assert page.events[0].device_name == "Synthetic softener"
    assert page.events[0].date == datetime(2026, 1, 2, 5, 10, tzinfo=UTC)


def test_push_bodies_parse_as_strings_and_as_objects() -> None:
    body = load_fixture("ws_event_seen")
    from_object = EventSeen.from_json(body)
    from_string = EventSeen.from_json(json.dumps(body))
    assert from_object == from_string
    assert from_object.event_type is DeviceEventType.SET_VACATION_MODE
    assert from_object.uuid == DEVICE_UUID
    assert from_object.params is None
    event = VendorEvent.from_push(json.dumps(load_fixture("ws_device_event")))
    assert event.kind == "Critical"
    assert event == VendorEvent.from_json(load_fixture("ws_device_event"))


def test_firmware_progress_lowercases_the_uuid() -> None:
    progress = FirmwareProgress.from_json(json.dumps({"uuid": "ABCDEF-01", "percentage": 42}))
    assert progress.uuid == "abcdef-01"
    assert progress.percentage == 42
    assert FirmwareProgress.from_json(load_fixture("ws_percentage")).uuid == DEVICE_UUID


def test_device_detached_error() -> None:
    error = VendorError.from_json(load_fixture("error_device_detached"))
    assert error.code == -3
    assert error.is_device_detached
    assert not VendorError.from_json({"code": -2}).is_device_detached


# ---------------------------------------------------------- broken input


@pytest.mark.parametrize("model", OBJECT_MODELS)
@pytest.mark.parametrize("garbage", [None, [], "text", 5, True])
def test_non_objects_parse_like_an_empty_object(model: Any, garbage: object) -> None:
    assert model.from_json(garbage) == model.from_json({})


def test_empty_object_gives_none_everywhere() -> None:
    state = DeviceState.from_json({})
    assert all(getattr(state, f) is None for f in state.__slots__)
    settings = DeviceSettings.from_json({})
    assert settings.regeneration_time is None
    assert settings.flood_alarm.max_flow is None


@pytest.mark.parametrize("marker", [None, "", "  ", "No data", "Brak danych", 0, "0"])
def test_no_data_never_becomes_a_date(marker: object) -> None:
    history = DeviceHistory.from_json({"regeneration": marker, "headError": marker})
    assert history.regeneration is None
    assert history.head_error is None
    state = DeviceState.from_json({"timestamp": marker, "expectedRegenerationDate": marker})
    assert state.timestamp is None
    assert state.expected_regeneration is None


def test_wrong_types_become_none() -> None:
    state = DeviceState.from_json(
        {"isOnline": "yes", "saltDays": "soon", "saltPercent": [1], "waterLeft": True, "state": 7}
    )
    assert state.is_online is None
    assert state.salt_days is None
    assert state.salt_percent is None
    assert state.water_left is None
    assert state.cycle is None


def test_numeric_strings_are_accepted_and_non_finite_numbers_are_not() -> None:
    assert DeviceState.from_json({"saltPercent": " 42.5 "}).salt_percent == 42.5
    assert DeviceState.from_json({"saltDays": "41"}).salt_days == 41
    for bad in (math.nan, math.inf, -math.inf, "nan", "inf"):
        assert DeviceState.from_json({"waterLeft": bad}).water_left is None


def test_zero_is_a_real_value_not_missing() -> None:
    settings = DeviceSettings.from_json({"floodAlarmSettings": {"threshold": 0, "maxFlow": 0}})
    assert settings.flood_alarm.threshold == 0
    assert settings.flood_alarm.max_flow == 0
    assert DeviceState.from_json({"saltDays": 0}).salt_days == 0


def test_device_without_uuid_is_an_error() -> None:
    payloads: list[object] = [{}, {"uuid": ""}, {"name": "x"}, None, [], "x"]
    for payload in payloads:
        with pytest.raises(ModelError):
            Device.from_json(payload)


def test_device_list_skips_unusable_items_and_non_lists() -> None:
    items = [load_fixture("device"), {"name": "no uuid"}, "junk", None]
    assert [d.uuid for d in models.parse_device_list(items)] == [DEVICE_UUID]
    assert models.parse_device_list({"not": "a list"}) == []
    assert models.parse_device_list(None) == []


def test_water_series_with_bad_values() -> None:
    series = WaterSeries.from_json({"data": [1, "2", None, "x", True, 3.5], "chartLine": "n/a"})
    assert series.values == (1.0, 2.0, None, None, None, 3.5)
    assert series.chart_line is None
    assert WaterSeries.from_json({"data": "not a list"}).values == ()
    assert models.parse_water_data("nope") == {}
    assert models.parse_year_water_data([]) == {}


def test_event_page_with_bad_content() -> None:
    assert EventPage.from_json({"page": {"content": "x"}, "unseen": "3"}) == EventPage(
        events=(), unseen=3
    )
    assert EventPage.from_json({"page": []}).events == ()


def test_push_body_that_is_not_json_yields_empty_models() -> None:
    assert models.decode_push_body("{broken") is None
    event = EventSeen.from_json("{broken")
    assert event.event_type is None
    assert event.uuid is None
    assert FirmwareProgress.from_json("{broken") == FirmwareProgress(uuid=None, percentage=None)


# -------------------------------------------------------------- enumerations


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Softening", Cycle.SOFTENING),
        ("deviceStateSoftening", Cycle.SOFTENING),
        ("deviceStateRegenBrineRefill", Cycle.BRINE_REFILL),
        ("Brine refill", Cycle.BRINE_REFILL),
        ("Brine_refill", Cycle.BRINE_REFILL),
        ("deviceStateRegenSaltDissolve", Cycle.SALT_DISSOLVE),
        ("deviceStateRegenBackwash", Cycle.BACKWASH),
        ("deviceStateRegenBrineCollect", Cycle.BRINE_COLLECT),
        ("deviceStateRegenFastwash", Cycle.FAST_WASH),
        ("Fast wash", Cycle.FAST_WASH),
        ("Vacation mode", Cycle.VACATION_MODE),
        ("Flow closed", Cycle.FLOW_CLOSED),
        ("Initialize", Cycle.INITIALIZE),
        ("  softening  ", Cycle.SOFTENING),
    ],
)
def test_both_cycle_vocabularies_map_to_the_enum(raw: str, expected: Cycle) -> None:
    assert models.parse_cycle(raw) is expected


def test_unknown_cycle_is_kept_raw() -> None:
    assert models.parse_cycle("Mode5") == "Mode5"
    assert not isinstance(models.parse_cycle("Mode5"), Cycle)
    assert models.parse_cycle(" Turbo Rinse ") == "Turbo Rinse"


@pytest.mark.parametrize("raw", [None, "", "   ", 5, [], True])
def test_missing_cycle_is_none(raw: object) -> None:
    assert models.parse_cycle(raw) is None


def test_event_types() -> None:
    assert models.parse_event_type("SET_WATER_FLOW") is DeviceEventType.SET_WATER_FLOW
    assert models.parse_event_type("SOMETHING_NEW") == "SOMETHING_NEW"
    assert models.parse_event_type(None) is None
    assert models.parse_event_type("") is None
    assert len(DeviceEventType) == 14


def test_models_are_immutable() -> None:
    state = DeviceState.from_json(load_fixture("state"))
    with pytest.raises(AttributeError):
        state.salt_days = 1  # type: ignore[misc]


def test_mode_list_keeps_order_and_drops_blanks() -> None:
    raw = ["deviceStateSoftening", "", None, "Mode5", 7, "deviceStateRegenBackwash"]
    assert models.parse_mode_list(raw) == [Cycle.SOFTENING, "Mode5", Cycle.BACKWASH]
    assert models.parse_mode_list(None) == []
    assert models.parse_mode_list({"a": 1}) == []


@pytest.mark.parametrize("blank", ["", " ", "\t\n"])
def test_blank_strings_count_as_missing(blank: str) -> None:
    assert UserDetails.from_json({"email": blank, "userId": blank}).email is None
    with pytest.raises(ModelError):
        Device.from_json({"uuid": blank})


def test_event_kind_comes_from_the_type_field() -> None:
    assert VendorEvent.from_json({"type": "ForcedByUser"}).kind == "ForcedByUser"
