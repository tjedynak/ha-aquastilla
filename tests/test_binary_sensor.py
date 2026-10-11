"""Binary sensors: connectivity, regeneration and the three derived alarms."""

from datetime import timedelta

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.components.binary_sensor.const import BinarySensorDeviceClass
from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util

from custom_components.aquastilla.const import DOMAIN

from .common import load_fixture
from .fake_backend import DEVICE_UUID, FakeBackend
from .setup_helpers import make_entry, setup_entry

KEYS = ("online", "regenerating", "salt_low", "leak", "head_error")


def state_of(hass: HomeAssistant, key: str) -> str:
    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id("binary_sensor", DOMAIN, f"{DEVICE_UUID}_{key}")
    assert entity_id is not None, key
    state = hass.states.get(entity_id)
    assert state is not None
    return state.state


def device_class(hass: HomeAssistant, key: str) -> object:
    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id("binary_sensor", DOMAIN, f"{DEVICE_UUID}_{key}")
    assert entity_id is not None
    entry = registry.async_get(entity_id)
    assert entry is not None
    return entry.original_device_class


async def setup_with(
    hass: HomeAssistant,
    backend: FakeBackend,
    *,
    state: dict[str, object] | None = None,
    settings: dict[str, object] | None = None,
    history: dict[str, object] | None = None,
    cycle: str | None = None,
) -> None:
    if state:
        backend.replace_json(f"/device/{DEVICE_UUID}/state", {**load_fixture("state"), **state})
    if settings:
        backend.replace_json(
            f"/device/{DEVICE_UUID}/settings", {**load_fixture("settings"), **settings}
        )
    if history is not None:
        device = load_fixture("device")
        device["deviceHistory"] = {**device["deviceHistory"], **history}
        backend.replace_json("/device/all", [device])
    if cycle is not None:
        backend.replace_json(
            f"/device/{DEVICE_UUID}/current_states", {"first": cycle, "second": "Softening"}
        )
    await setup_entry(hass, make_entry())


# ----------------------------------------------------------------- classes


async def test_all_five_exist_with_the_right_device_classes(
    hass: HomeAssistant, backend: FakeBackend
) -> None:
    await setup_with(hass, backend)
    assert device_class(hass, "online") == BinarySensorDeviceClass.CONNECTIVITY
    assert device_class(hass, "regenerating") == BinarySensorDeviceClass.RUNNING
    for key in ("salt_low", "leak", "head_error"):
        assert device_class(hass, key) == BinarySensorDeviceClass.PROBLEM, key


async def test_a_healthy_device_reads_online_and_quiet(
    hass: HomeAssistant, backend: FakeBackend
) -> None:
    await setup_with(hass, backend)
    assert state_of(hass, "online") == STATE_ON
    assert state_of(hass, "regenerating") == STATE_OFF
    assert state_of(hass, "salt_low") == STATE_OFF  # 50 % against a threshold of 20
    assert state_of(hass, "leak") == STATE_OFF
    assert state_of(hass, "head_error") == STATE_OFF


# ------------------------------------------------------------------ online


async def test_an_offline_device_is_off_not_unavailable(
    hass: HomeAssistant, backend: FakeBackend
) -> None:
    """The cloud answered; the device is the one that is away. That is a state, not a gap."""
    await setup_with(hass, backend, state={"isOnline": False})
    assert state_of(hass, "online") == STATE_OFF


async def test_losing_the_service_is_unavailable_not_unknown(
    hass: HomeAssistant, backend: FakeBackend
) -> None:
    entry = make_entry()
    await setup_entry(hass, entry)
    backend.fail_always("GET", f"/device/{DEVICE_UUID}/state", status=500)
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    for key in KEYS:
        assert state_of(hass, key) == STATE_UNAVAILABLE, key


async def test_an_unreported_online_flag_is_unknown(
    hass: HomeAssistant, backend: FakeBackend
) -> None:
    await setup_with(hass, backend, state={"isOnline": None})
    assert state_of(hass, "online") == STATE_UNKNOWN


# ------------------------------------------------------------ regenerating


@pytest.mark.parametrize(
    ("cycle", "expected"),
    [
        ("Softening", STATE_OFF),
        ("Brine refill", STATE_ON),
        ("Salt_dissolve", STATE_ON),
        ("deviceStateRegenBackwash", STATE_ON),
        ("Brine collect", STATE_ON),
        ("Fast_wash", STATE_ON),
        ("Vacation mode", STATE_OFF),
        ("Flow closed", STATE_OFF),
        ("Turbo rinse", STATE_UNKNOWN),  # a cycle we do not know is neither yes nor no
    ],
)
async def test_regenerating_follows_the_cycle(
    hass: HomeAssistant, backend: FakeBackend, cycle: str, expected: str
) -> None:
    await setup_with(hass, backend, cycle=cycle)
    assert state_of(hass, "regenerating") == expected


# ---------------------------------------------------------------- salt low


@pytest.mark.parametrize(
    ("level", "threshold", "expected"),
    [
        (50, 20, STATE_OFF),
        (21, 20, STATE_OFF),
        (20, 20, STATE_ON),  # at the threshold counts as low
        (5, 20, STATE_ON),
        (0, 20, STATE_ON),
        (None, 20, STATE_UNKNOWN),
        (50, None, STATE_UNKNOWN),
    ],
)
async def test_salt_low_compares_the_level_with_the_users_threshold(
    hass: HomeAssistant,
    backend: FakeBackend,
    level: int | None,
    threshold: int | None,
    expected: str,
) -> None:
    settings: dict[str, object] = {"saltAlarmSettings": {"minimumSaltLevelPerDays": threshold}}
    await setup_with(hass, backend, state={"saltPercent": level}, settings=settings)
    assert state_of(hass, "salt_low") == expected


# --------------------------------------------------------- leak, head error


@pytest.mark.parametrize(("key", "field"), [("leak", "flood"), ("head_error", "headError")])
async def test_an_event_from_the_last_day_turns_the_alarm_on_and_it_expires(
    hass: HomeAssistant,
    backend: FakeBackend,
    freezer: FrozenDateTimeFactory,
    key: str,
    field: str,
) -> None:
    recent = (dt_util.utcnow() - timedelta(hours=3)).isoformat()
    await setup_with(hass, backend, history={field: recent})
    assert state_of(hass, key) == STATE_ON

    freezer.tick(timedelta(days=1))
    coordinator = hass.config_entries.async_entries(DOMAIN)[0].runtime_data
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert state_of(hass, key) == STATE_OFF  # four hours past the window


@pytest.mark.parametrize(("key", "field"), [("leak", "flood"), ("head_error", "headError")])
async def test_an_old_or_missing_event_is_no_alarm(
    hass: HomeAssistant, backend: FakeBackend, key: str, field: str
) -> None:
    old = (dt_util.utcnow() - timedelta(days=30)).isoformat()
    await setup_with(hass, backend, history={field: old})
    assert state_of(hass, key) == STATE_OFF


async def test_each_alarm_reads_its_own_field(hass: HomeAssistant, backend: FakeBackend) -> None:
    recent = (dt_util.utcnow() - timedelta(hours=1)).isoformat()
    await setup_with(hass, backend, history={"flood": recent})
    assert state_of(hass, "leak") == STATE_ON
    assert state_of(hass, "head_error") == STATE_OFF
