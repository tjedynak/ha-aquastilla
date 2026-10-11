"""Water consumption sensors: today, and the last 31 days."""

import pytest
from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass
from homeassistant.const import STATE_UNKNOWN, UnitOfVolume
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from custom_components.aquastilla.const import DOMAIN

from .common import load_fixture
from .fake_backend import DEVICE_UUID, FakeBackend
from .setup_helpers import make_entry, setup_entry


def entity(hass: HomeAssistant, key: str) -> tuple[str, str | None, er.RegistryEntry]:
    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id("sensor", DOMAIN, f"{DEVICE_UUID}_{key}")
    assert entity_id is not None, key
    state = hass.states.get(entity_id)
    entry = registry.async_get(entity_id)
    assert state is not None
    assert entry is not None
    return state.state, state.attributes.get("unit_of_measurement"), entry


async def test_today_is_a_total_that_resets_each_day(
    hass: HomeAssistant, backend: FakeBackend
) -> None:
    backend.replace_json(
        f"/device/{DEVICE_UUID}/state", {**load_fixture("state"), "todayWaterUsage": 137.5}
    )
    await setup_entry(hass, make_entry())
    value, _, entry = entity(hass, "water_today")
    assert float(value) == 137.5
    assert entry.original_device_class == SensorDeviceClass.WATER
    assert entry.capabilities is not None
    assert entry.capabilities["state_class"] == SensorStateClass.TOTAL_INCREASING


async def test_the_window_is_the_sum_of_the_31_daily_values(
    hass: HomeAssistant, backend: FakeBackend
) -> None:
    series = load_fixture("water_data")["WATER_MONTH_DATA"]["data"]
    await setup_entry(hass, make_entry())
    value, _, entry = entity(hass, "water_31_days")
    assert float(value) == pytest.approx(sum(series))
    assert len(series) == 31
    # A sliding window can fall, so it is a plain measurement and not a total.
    assert entry.capabilities is not None
    assert entry.capabilities["state_class"] == SensorStateClass.MEASUREMENT
    assert entry.original_device_class is None


@pytest.mark.parametrize(
    ("setting", "unit"),
    [
        ("l", UnitOfVolume.LITERS),
        ("m3", UnitOfVolume.CUBIC_METERS),
        ("M3", UnitOfVolume.CUBIC_METERS),
        ("m³", UnitOfVolume.CUBIC_METERS),
        (None, UnitOfVolume.LITERS),
        ("gallons", UnitOfVolume.LITERS),
    ],
)
async def test_the_unit_follows_the_volume_setting(
    hass: HomeAssistant, backend: FakeBackend, setting: str | None, unit: str
) -> None:
    backend.replace_json(
        f"/device/{DEVICE_UUID}/settings", {**load_fixture("settings"), "unitOfVolume": setting}
    )
    await setup_entry(hass, make_entry())
    for key in ("water_today", "water_31_days"):
        assert entity(hass, key)[1] == unit, key


async def test_a_missing_series_leaves_the_window_unknown(
    hass: HomeAssistant, backend: FakeBackend
) -> None:
    data = load_fixture("water_data")
    del data["WATER_MONTH_DATA"]
    backend.replace_json(f"/device/{DEVICE_UUID}/water_data", data)
    await setup_entry(hass, make_entry())
    assert entity(hass, "water_31_days")[0] == STATE_UNKNOWN


async def test_a_series_of_gaps_is_unknown_and_not_zero(
    hass: HomeAssistant, backend: FakeBackend
) -> None:
    data = load_fixture("water_data")
    data["WATER_MONTH_DATA"]["data"] = [None] * 31
    backend.replace_json(f"/device/{DEVICE_UUID}/water_data", data)
    await setup_entry(hass, make_entry())
    assert entity(hass, "water_31_days")[0] == STATE_UNKNOWN


async def test_a_detached_device_does_not_break_the_water_sensors(
    hass: HomeAssistant, backend: FakeBackend, caplog: pytest.LogCaptureFixture
) -> None:
    entry = make_entry()
    await setup_entry(hass, entry)
    backend.fail_always(
        "GET",
        f"/device/{DEVICE_UUID}/state",
        status=400,
        json_body=load_fixture("error_device_detached"),
    )
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert entity(hass, "water_today")[0] == "unavailable"
    assert entity(hass, "water_31_days")[0] == "unavailable"
    assert "Unexpected error" not in caplog.text
