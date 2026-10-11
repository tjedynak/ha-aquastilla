"""Regeneration timestamps: values, categories and "no data"."""

from datetime import UTC, datetime

from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.const import STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from custom_components.aquastilla.const import DOMAIN

from .common import load_fixture
from .fake_backend import DEVICE_UUID, FakeBackend
from .setup_helpers import make_entry, setup_entry


def lookup(hass: HomeAssistant, key: str) -> tuple[str, er.RegistryEntry]:
    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id("sensor", DOMAIN, f"{DEVICE_UUID}_{key}")
    assert entity_id is not None
    state = hass.states.get(entity_id)
    assert state is not None
    entry = registry.async_get(entity_id)
    assert entry is not None
    return state.state, entry


async def test_both_regeneration_timestamps_are_read(
    hass: HomeAssistant, backend: FakeBackend
) -> None:
    await setup_entry(hass, make_entry())
    last, last_entry = lookup(hass, "last_regeneration")
    following, next_entry = lookup(hass, "next_regeneration")

    assert datetime.fromisoformat(last) == datetime(
        2026, 1, 1, 23, 1, tzinfo=UTC
    )  # 00:01 local, labelled UTC
    assert datetime.fromisoformat(following) == datetime(2026, 1, 4, 23, 1, tzinfo=UTC)
    assert last_entry.original_device_class == SensorDeviceClass.TIMESTAMP
    assert next_entry.original_device_class == SensorDeviceClass.TIMESTAMP


async def test_the_history_one_is_diagnostic_and_the_prediction_is_not(
    hass: HomeAssistant, backend: FakeBackend
) -> None:
    await setup_entry(hass, make_entry())
    assert lookup(hass, "last_regeneration")[1].entity_category is not None
    assert lookup(hass, "next_regeneration")[1].entity_category is None


async def test_no_data_is_unknown_not_the_epoch(hass: HomeAssistant, backend: FakeBackend) -> None:
    device = {**load_fixture("device"), "deviceHistory": {"regeneration": "Brak danych"}}
    backend.replace_json("/device/all", [device])
    backend.replace_json(
        f"/device/{DEVICE_UUID}/state", {**load_fixture("state"), "expectedRegenerationDate": ""}
    )
    await setup_entry(hass, make_entry())
    assert lookup(hass, "last_regeneration")[0] == STATE_UNKNOWN
    assert lookup(hass, "next_regeneration")[0] == STATE_UNKNOWN


async def test_a_zero_timestamp_is_unknown_too(hass: HomeAssistant, backend: FakeBackend) -> None:
    backend.replace_json(
        f"/device/{DEVICE_UUID}/state", {**load_fixture("state"), "expectedRegenerationDate": 0}
    )
    await setup_entry(hass, make_entry())
    assert lookup(hass, "next_regeneration")[0] == STATE_UNKNOWN
