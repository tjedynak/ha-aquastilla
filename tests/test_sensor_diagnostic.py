"""Diagnostic sensors: all in the diagnostic category, with the address off by default."""

from datetime import UTC, datetime, timedelta

from freezegun.api import FrozenDateTimeFactory
from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util

from custom_components.aquastilla.const import DOMAIN

from .fake_backend import DEVICE_UUID, FakeBackend
from .setup_helpers import make_entry, setup_entry

KEYS = ("device_clock", "last_update", "ip_address")


def registry_entry(hass: HomeAssistant, key: str) -> er.RegistryEntry:
    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id("sensor", DOMAIN, f"{DEVICE_UUID}_{key}")
    assert entity_id is not None, key
    entry = registry.async_get(entity_id)
    assert entry is not None
    return entry


async def test_all_diagnostic_sensors_are_in_the_diagnostic_category(
    hass: HomeAssistant, backend: FakeBackend
) -> None:
    await setup_entry(hass, make_entry())
    for key in KEYS:
        assert registry_entry(hass, key).entity_category is EntityCategory.DIAGNOSTIC, key


async def test_the_address_is_disabled_until_asked_for(
    hass: HomeAssistant, backend: FakeBackend
) -> None:
    await setup_entry(hass, make_entry())
    assert registry_entry(hass, "ip_address").disabled_by is er.RegistryEntryDisabler.INTEGRATION
    assert registry_entry(hass, "device_clock").disabled_by is None
    assert registry_entry(hass, "last_update").disabled_by is None


async def test_the_device_clock_is_the_state_timestamp(
    hass: HomeAssistant, backend: FakeBackend
) -> None:
    await setup_entry(hass, make_entry())
    entry = registry_entry(hass, "device_clock")
    assert entry.original_device_class == SensorDeviceClass.TIMESTAMP
    state = hass.states.get(entry.entity_id)
    assert state is not None
    assert datetime.fromisoformat(state.state) == datetime(
        2026, 1, 2, 6, 34, tzinfo=UTC
    )  # 07:34 local in Warsaw, labelled UTC


async def test_last_update_moves_with_each_successful_poll(
    hass: HomeAssistant, backend: FakeBackend, freezer: FrozenDateTimeFactory
) -> None:
    entry = make_entry()
    await setup_entry(hass, entry)
    sensor = registry_entry(hass, "last_update")
    first = hass.states.get(sensor.entity_id)
    assert first is not None
    assert datetime.fromisoformat(first.state) == dt_util.utcnow().replace(microsecond=0)

    freezer.tick(timedelta(minutes=5))
    await entry.runtime_data._async_refresh(scheduled=True)
    await hass.async_block_till_done()
    second = hass.states.get(sensor.entity_id)
    assert second is not None
    assert datetime.fromisoformat(second.state) == dt_util.utcnow().replace(microsecond=0)
    assert datetime.fromisoformat(second.state) - datetime.fromisoformat(first.state) == timedelta(
        minutes=5
    )


async def test_a_failed_poll_does_not_move_last_update(
    hass: HomeAssistant, backend: FakeBackend, freezer: FrozenDateTimeFactory
) -> None:
    entry = make_entry()
    await setup_entry(hass, entry)
    coordinator = entry.runtime_data
    before = coordinator.data.devices[DEVICE_UUID].updated_at
    backend.fail_always("GET", f"/device/{DEVICE_UUID}/state", status=500)
    freezer.tick(timedelta(minutes=5))
    await coordinator._async_refresh(scheduled=True)
    assert coordinator.data.devices[DEVICE_UUID].updated_at == before


async def test_a_push_refresh_counts_as_an_update(
    hass: HomeAssistant, backend: FakeBackend, freezer: FrozenDateTimeFactory
) -> None:
    entry = make_entry()
    await setup_entry(hass, entry)
    coordinator = entry.runtime_data
    freezer.tick(timedelta(minutes=1))
    await coordinator.async_refresh_state(DEVICE_UUID, force=True)
    assert coordinator.data.devices[DEVICE_UUID].updated_at == dt_util.utcnow()
