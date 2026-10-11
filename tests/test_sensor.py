"""Sensors: values, units, classes, availability and discovery of new devices."""

from datetime import timedelta
from typing import Any

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry
from syrupy.assertion import SnapshotAssertion

from custom_components.aquastilla.const import DEVICE_LIST_INTERVAL, DOMAIN

from .common import load_fixture
from .fake_backend import DEVICE_UUID, FakeBackend
from .setup_helpers import make_entry, setup_entry

OTHER = "00000000-0000-4000-8000-0000000000bb"
SALT_KEYS = ("salt_level", "salt_days_left", "water_left")  # the ones that have a state
ALL_SALT_KEYS = (*SALT_KEYS, "water_left_max")  # the capacity is off by default


@pytest.fixture
async def entry(hass: HomeAssistant, backend: FakeBackend) -> MockConfigEntry:
    entry = make_entry()
    await setup_entry(hass, entry)
    return entry


def entity_id(hass: HomeAssistant, key: str, uuid: str = DEVICE_UUID) -> str:
    found = er.async_get(hass).async_get_entity_id("sensor", DOMAIN, f"{uuid}_{key}")
    assert found is not None, f"no sensor {key}"
    return found


def attributes(hass: HomeAssistant, key: str) -> dict[str, Any]:
    state = hass.states.get(entity_id(hass, key))
    assert state is not None
    return dict(state.attributes)


def value(hass: HomeAssistant, key: str, uuid: str = DEVICE_UUID) -> str:
    state = hass.states.get(entity_id(hass, key, uuid))
    assert state is not None
    return state.state


def ascii_safe(value: Any) -> Any:
    """A plain structure of strings, numbers and booleans with only ASCII characters.

    Snapshots are read back with the platform's encoding, and a runner whose locale
    is not UTF-8 then fails to find them. Enum members become their text.
    """
    if isinstance(value, dict):
        return {ascii_safe(str(k)): ascii_safe(v) for k, v in value.items()}
    if isinstance(value, bool | int | float) or value is None:
        return value
    return str(value).encode("ascii", "backslashreplace").decode("ascii")


def snapshot_of(hass: HomeAssistant, entry: MockConfigEntry, domain: str) -> dict[str, Any]:
    """States, attributes and names of every entity of the integration on one platform."""
    registry = er.async_get(hass)
    result: dict[str, Any] = {}
    items = er.async_entries_for_config_entry(registry, entry.entry_id)
    for item in sorted(items, key=lambda e: e.unique_id):
        if item.domain != domain:
            continue
        state = hass.states.get(item.entity_id)  # a disabled entity has no state
        result[item.unique_id.replace(DEVICE_UUID, "<device>")] = ascii_safe(
            {
                "state": state.state if state else None,
                "attributes": dict(state.attributes) if state else {},
                "entity_category": item.entity_category,
                "disabled_by": item.disabled_by,
            }
        )
    return result


# ------------------------------------------------------------------- salt


async def test_salt_sensors_report_the_state(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    assert float(value(hass, "salt_level")) == 50
    assert float(value(hass, "salt_days_left")) == 41
    assert float(value(hass, "water_left")) == pytest.approx(4.046, abs=0.01)


async def test_salt_sensors_have_units_and_classes(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    level = attributes(hass, "salt_level")
    assert level["unit_of_measurement"] == "%"
    assert level["state_class"] == SensorStateClass.MEASUREMENT

    days = attributes(hass, "salt_days_left")
    assert days["device_class"] == SensorDeviceClass.DURATION
    assert days["unit_of_measurement"] == "d"
    assert days["max_days"] == 82

    water = attributes(hass, "water_left")
    assert water["device_class"] == SensorDeviceClass.WATER
    assert water["unit_of_measurement"] == "m³"
    assert "state_class" not in water  # a decreasing remainder is not a counter
    assert "capacity" not in water  # the capacity is its own sensor, so the unit converts too


async def test_sensors_match_the_snapshot(
    hass: HomeAssistant, entry: MockConfigEntry, snapshot: SnapshotAssertion
) -> None:
    assert snapshot_of(hass, entry, "sensor") == snapshot


# ------------------------------------------------------------ availability


async def test_missing_values_are_unknown_not_zero(
    hass: HomeAssistant, backend: FakeBackend
) -> None:
    state = {**load_fixture("state"), "saltPercent": None, "saltDays": "n/a", "waterLeft": None}
    backend.replace_json(f"/device/{DEVICE_UUID}/state", state)
    await setup_entry(hass, make_entry())
    for key in SALT_KEYS:
        assert value(hass, key) == STATE_UNKNOWN


async def test_losing_the_service_makes_sensors_unavailable_not_unknown(
    hass: HomeAssistant, backend: FakeBackend, entry: MockConfigEntry
) -> None:
    backend.fail_always("GET", f"/device/{DEVICE_UUID}/state", status=500)
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    for key in SALT_KEYS:
        assert value(hass, key) == STATE_UNAVAILABLE


async def test_sensors_recover_by_themselves(
    hass: HomeAssistant, backend: FakeBackend, entry: MockConfigEntry
) -> None:
    backend.fail_next("GET", f"/device/{DEVICE_UUID}/state", status=500)
    await entry.runtime_data.async_refresh()
    assert value(hass, "salt_level") == STATE_UNAVAILABLE
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert float(value(hass, "salt_level")) == 50


async def test_a_new_value_reaches_the_sensor(
    hass: HomeAssistant, backend: FakeBackend, entry: MockConfigEntry
) -> None:
    backend.replace_json(
        f"/device/{DEVICE_UUID}/state", {**load_fixture("state"), "saltPercent": 18}
    )
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert float(value(hass, "salt_level")) == 18


# --------------------------------------------------------------- devices


async def test_unique_ids_are_stable_and_per_device(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    registry = er.async_get(hass)
    ids = [e.unique_id for e in er.async_entries_for_config_entry(registry, entry.entry_id)]
    assert {f"{DEVICE_UUID}_{key}" for key in ALL_SALT_KEYS} <= set(ids)
    assert len(ids) == len(set(ids))
    assert all(unique.startswith(f"{DEVICE_UUID}_") for unique in ids)


async def test_a_device_added_later_gets_its_sensors(
    hass: HomeAssistant,
    backend: FakeBackend,
    entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    backend.add_device(OTHER)
    freezer.tick(timedelta(seconds=DEVICE_LIST_INTERVAL.total_seconds()))
    await entry.runtime_data._async_refresh(scheduled=True)
    await hass.async_block_till_done()
    assert float(value(hass, "salt_level", OTHER)) == 50
    assert float(value(hass, "salt_level")) == 50


async def test_sensors_belong_to_one_registered_device(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    items = er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
    assert len({item.device_id for item in items}) == 1
    assert items[0].device_id is not None


async def test_the_capacity_is_a_sensor_of_its_own_and_off_by_default(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id("sensor", DOMAIN, f"{DEVICE_UUID}_water_left_max")
    assert entity_id is not None
    item = registry.async_get(entity_id)
    assert item is not None
    assert item.disabled_by is er.RegistryEntryDisabler.INTEGRATION
    assert item.original_device_class == SensorDeviceClass.WATER


async def test_a_device_without_a_name_still_gets_named_entities(
    hass: HomeAssistant, backend: FakeBackend
) -> None:
    unnamed = {**load_fixture("device"), "name": None}
    backend.replace_json("/device/all", [unnamed])
    await setup_entry(hass, make_entry())
    state = hass.states.get(entity_id(hass, "salt_level"))
    assert state is not None
    assert state.attributes["friendly_name"] == "Aquastilla Salt level"


async def test_a_device_that_leaves_the_account_and_returns_gets_its_sensors_back(
    hass: HomeAssistant, backend: FakeBackend, freezer: FrozenDateTimeFactory
) -> None:
    backend.add_device(OTHER)  # keeps the listing from being empty
    entry = make_entry()
    await setup_entry(hass, entry)
    coordinator = entry.runtime_data
    registry = er.async_get(hass)
    assert registry.async_get_entity_id("sensor", DOMAIN, f"{DEVICE_UUID}_salt_level") is not None

    backend.remove_device(DEVICE_UUID)
    for _ in range(2):  # the read that answers -3, then the list without the device
        freezer.tick(timedelta(minutes=5))
        await coordinator._async_refresh(scheduled=True)
    await hass.async_block_till_done()
    assert registry.async_get_entity_id("sensor", DOMAIN, f"{DEVICE_UUID}_salt_level") is None

    backend.extra_devices.add(DEVICE_UUID)  # attached again; the fake lists it once more
    backend._removed.discard(DEVICE_UUID)
    freezer.tick(timedelta(seconds=DEVICE_LIST_INTERVAL.total_seconds()))
    await coordinator._async_refresh(scheduled=True)
    await hass.async_block_till_done()

    assert registry.async_get_entity_id("sensor", DOMAIN, f"{DEVICE_UUID}_salt_level") is not None
    assert float(value(hass, "salt_level")) == 50
