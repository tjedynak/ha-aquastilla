"""Cycle sensors: both vocabularies, unknown values and the raw attribute."""

import pytest
from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.const import STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from custom_components.aquastilla.const import DOMAIN
from custom_components.aquastilla.models import Cycle
from custom_components.aquastilla.sensors_cycle import CYCLE_OPTIONS, cycle_option

from .common import load_fixture
from .fake_backend import DEVICE_UUID, FakeBackend
from .setup_helpers import make_entry, setup_entry


def sensor(hass: HomeAssistant, key: str):  # type: ignore[no-untyped-def]
    entity_id = er.async_get(hass).async_get_entity_id("sensor", DOMAIN, f"{DEVICE_UUID}_{key}")
    assert entity_id is not None
    state = hass.states.get(entity_id)
    assert state is not None
    return state


async def setup_with(hass: HomeAssistant, backend: FakeBackend, **cycles: object) -> None:
    if "state" in cycles:
        backend.replace_json(
            f"/device/{DEVICE_UUID}/state", {**load_fixture("state"), "state": cycles["state"]}
        )
    if "first" in cycles or "second" in cycles:
        backend.replace_json(
            f"/device/{DEVICE_UUID}/current_states",
            {
                **load_fixture("current_states"),
                **{k: v for k, v in cycles.items() if k in {"first", "second"}},
            },
        )
    await setup_entry(hass, make_entry())


async def test_current_and_next_cycle(hass: HomeAssistant, backend: FakeBackend) -> None:
    await setup_with(hass, backend)
    current = sensor(hass, "cycle")
    assert current.state == "softening"
    assert current.attributes["device_class"] == SensorDeviceClass.ENUM
    assert current.attributes["options"] == CYCLE_OPTIONS
    assert current.attributes["raw_value"] == "Softening"
    assert sensor(hass, "next_cycle").state == "brine_refill"


@pytest.mark.parametrize(
    ("first", "expected"),
    [
        ("deviceStateRegenBackwash", "backwash"),  # the state endpoint's vocabulary
        ("Brine collect", "brine_collect"),  # the display vocabulary
        ("Fast_wash", "fast_wash"),
        ("Vacation mode", "vacation_mode"),
    ],
)
async def test_both_vocabularies_give_the_same_options(
    hass: HomeAssistant, backend: FakeBackend, first: str, expected: str
) -> None:
    await setup_with(hass, backend, first=first)
    assert sensor(hass, "cycle").state == expected


async def test_an_unrecognised_cycle_is_other_with_the_raw_value(
    hass: HomeAssistant, backend: FakeBackend
) -> None:
    await setup_with(hass, backend, first="Turbo rinse", second="Mode5")
    current = sensor(hass, "cycle")
    assert current.state == "other"
    assert current.attributes["raw_value"] == "Turbo rinse"
    assert sensor(hass, "next_cycle").state == "other"
    assert sensor(hass, "next_cycle").attributes["raw_value"] == "Mode5"


async def test_the_state_endpoint_fills_in_when_the_cycle_endpoint_is_empty(
    hass: HomeAssistant, backend: FakeBackend
) -> None:
    await setup_with(hass, backend, first=None, state="deviceStateRegenSaltDissolve")
    assert sensor(hass, "cycle").state == "salt_dissolve"


async def test_no_cycle_at_all_is_unknown(hass: HomeAssistant, backend: FakeBackend) -> None:
    await setup_with(hass, backend, first=None, second=None, state=None)
    assert sensor(hass, "cycle").state == STATE_UNKNOWN
    assert sensor(hass, "next_cycle").state == STATE_UNKNOWN
    assert sensor(hass, "cycle").attributes["raw_value"] is None


def test_every_known_cycle_has_an_option_and_other_is_the_fallback() -> None:
    assert {cycle_option(c) for c in Cycle} == {c.value.lower() for c in Cycle}
    assert "other" in CYCLE_OPTIONS
    assert cycle_option("anything new") == "other"
    assert cycle_option(None) is None
    assert len(CYCLE_OPTIONS) == len(set(CYCLE_OPTIONS))
