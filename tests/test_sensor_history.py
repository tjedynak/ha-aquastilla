"""The five history timestamps, with the regeneration one from its own group."""

from datetime import UTC, datetime

import pytest
from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.const import STATE_UNKNOWN, EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from custom_components.aquastilla.const import DOMAIN

from .common import load_fixture
from .fake_backend import DEVICE_UUID, FakeBackend
from .setup_helpers import make_entry, setup_entry

HISTORY_KEYS = (
    "last_regeneration",
    "last_salt_refill",
    "last_inspection",
    "last_leak",
    "last_head_error",
)


def read(hass: HomeAssistant, key: str) -> tuple[str, er.RegistryEntry]:
    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id("sensor", DOMAIN, f"{DEVICE_UUID}_{key}")
    assert entity_id is not None, key
    state = hass.states.get(entity_id)
    assert state is not None
    entry = registry.async_get(entity_id)
    assert entry is not None
    return state.state, entry


async def test_all_five_history_timestamps_exist_and_are_diagnostic_timestamps(
    hass: HomeAssistant, backend: FakeBackend
) -> None:
    await setup_entry(hass, make_entry())
    for key in HISTORY_KEYS:
        _, entry = read(hass, key)
        assert entry.original_device_class == SensorDeviceClass.TIMESTAMP, key
        assert entry.entity_category is EntityCategory.DIAGNOSTIC, key


async def test_values_come_from_the_matching_history_fields(
    hass: HomeAssistant, backend: FakeBackend
) -> None:
    device = load_fixture("device")
    device["deviceHistory"] = {
        "regeneration": "2026-01-02T00:01:00Z",
        "saltReplenishment": "2025-12-20T10:00:00Z",
        "inspection": "2025-06-01T08:30:00Z",
        "flood": "2025-11-11T03:00:00Z",
        "headError": "2025-09-09T09:09:00Z",
    }
    backend.replace_json("/device/all", [device])
    await setup_entry(hass, make_entry())

    expected = {
        "last_regeneration": datetime(2026, 1, 1, 23, 1, tzinfo=UTC),  # Warsaw is UTC+1 in January
        "last_salt_refill": datetime(2025, 12, 20, 9, 0, tzinfo=UTC),
        "last_inspection": datetime(2025, 6, 1, 6, 30, tzinfo=UTC),  # June: UTC+2
        "last_leak": datetime(2025, 11, 11, 2, 0, tzinfo=UTC),
        "last_head_error": datetime(2025, 9, 9, 7, 9, tzinfo=UTC),  # September: UTC+2
    }
    for key, moment in expected.items():
        assert datetime.fromisoformat(read(hass, key)[0]) == moment, key


async def test_events_that_never_happened_are_unknown(
    hass: HomeAssistant, backend: FakeBackend
) -> None:
    await setup_entry(hass, make_entry())
    for key in ("last_inspection", "last_leak", "last_head_error"):  # null in the fixture
        assert read(hass, key)[0] == STATE_UNKNOWN, key


@pytest.mark.parametrize("marker", ["", "No data", "Brak danych", 0])
async def test_every_no_data_marker_is_unknown(
    hass: HomeAssistant, backend: FakeBackend, marker: object
) -> None:
    device = load_fixture("device")
    device["deviceHistory"] = dict.fromkeys(
        ("regeneration", "saltReplenishment", "inspection", "flood", "headError"), marker
    )
    backend.replace_json("/device/all", [device])
    await setup_entry(hass, make_entry())
    for key in HISTORY_KEYS:
        assert read(hass, key)[0] == STATE_UNKNOWN, key
