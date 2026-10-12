"""The entities the parity matrix asks for that came after the first sensors."""

from collections.abc import Iterable
from datetime import UTC, datetime

from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity import Entity

from custom_components.aquastilla import binary_sensor as platform
from custom_components.aquastilla.const import DOMAIN

from .common import load_fixture
from .control_helpers import GUARDED, SETTINGS, entity_id, started, state_of
from .fake_backend import DEVICE_UUID, FakeBackend

STATE = f"/device/{DEVICE_UUID}/state"
EXTRA = (
    ("binary_sensor", "cloud_connection"),
    ("sensor", "unread_notifications"),
)


async def test_the_push_channel_has_a_connection_sensor(
    hass: HomeAssistant, backend: FakeBackend
) -> None:
    entry = await started(hass, backend, GUARDED + EXTRA)
    assert state_of(hass, "binary_sensor", "cloud_connection") == STATE_ON
    await entry.runtime_data.websocket.stop()
    await hass.async_block_till_done()
    assert state_of(hass, "binary_sensor", "cloud_connection") == STATE_OFF


async def test_the_unread_count_comes_from_the_log(
    hass: HomeAssistant, backend: FakeBackend
) -> None:
    await started(hass, backend, GUARDED + EXTRA)
    assert state_of(hass, "sensor", "unread_notifications") == "1"


async def test_a_firmware_update_in_progress_is_a_binary_sensor(
    hass: HomeAssistant, backend: FakeBackend
) -> None:
    backend.replace_json(STATE, {**load_fixture("state"), "isUpdate": True})
    await started(hass, backend)
    assert state_of(hass, "binary_sensor", "firmware_updating") == STATE_ON


async def test_remote_service_is_a_state_and_an_end_time(
    hass: HomeAssistant, backend: FakeBackend
) -> None:
    ends = "2026-01-02T20:00:00.000Z"
    backend.replace_json(
        SETTINGS, {**load_fixture("settings"), "serviceMode": True, "serviceModeEndingTime": ends}
    )
    await started(hass, backend)
    assert state_of(hass, "binary_sensor", "remote_service") == STATE_ON
    assert datetime.fromisoformat(state_of(hass, "sensor", "remote_service_end")) == datetime(
        2026, 1, 2, 20, tzinfo=UTC
    )


async def test_without_remote_service_there_is_no_end_time(
    hass: HomeAssistant, backend: FakeBackend
) -> None:
    await started(hass, backend)
    assert state_of(hass, "binary_sensor", "remote_service") == STATE_OFF
    assert state_of(hass, "sensor", "remote_service_end") == STATE_UNKNOWN


async def test_the_regeneration_progress_is_a_percentage(
    hass: HomeAssistant, backend: FakeBackend
) -> None:
    backend.replace_json(STATE, {**load_fixture("state"), "regenPercentage": 40})
    await started(hass, backend)
    assert float(state_of(hass, "sensor", "regeneration_progress")) == 40.0
    assert (
        hass.states.get(entity_id(hass, "sensor", "regeneration_progress")).attributes[  # type: ignore[union-attr]
            "unit_of_measurement"
        ]
        == "%"
    )


async def test_the_device_links_to_the_vendors_service_page(
    hass: HomeAssistant, backend: FakeBackend
) -> None:
    entry = await started(hass, backend)
    device = dr.async_get(hass).async_get_device_by_identifier(
        (DOMAIN, DEVICE_UUID), entry.entry_id
    )
    assert device is not None
    assert device.configuration_url == load_fixture("device")["brand"]["serviceUrl"]


async def test_a_service_link_that_is_not_a_web_address_is_dropped(
    hass: HomeAssistant, backend: FakeBackend
) -> None:
    device = load_fixture("device")
    device["brand"] = {**device["brand"], "serviceUrl": "javascript:alert(1)"}
    backend.replace_json("/device/all", [device])
    entry = await started(hass, backend)
    registered = dr.async_get(hass).async_get_device_by_identifier(
        (DOMAIN, DEVICE_UUID), entry.entry_id
    )
    assert registered is not None
    assert registered.configuration_url is None


async def test_the_volume_unit_in_litres_is_an_option(
    hass: HomeAssistant, backend: FakeBackend
) -> None:
    backend.replace_json(SETTINGS, {**load_fixture("settings"), "unitOfVolume": "l"})
    await started(hass, backend)
    assert state_of(hass, "select", "volume_unit") == "l"
    registry = er.async_get(hass)
    assert registry.async_get_entity_id("select", DOMAIN, f"{DEVICE_UUID}_volume_unit")


async def test_without_a_push_hub_there_is_no_connection_sensor(
    hass: HomeAssistant, backend: FakeBackend
) -> None:
    entry = await started(hass, backend)
    entry.runtime_data.push = None
    added: list[Entity] = []

    def collect(
        new_entities: Iterable[Entity],
        update_before_add: bool = False,
        *,
        config_subentry_id: str | None = None,
    ) -> None:
        added.extend(new_entities)

    await platform.async_setup_entry(hass, entry, collect)
    assert added
    assert not [e for e in added if type(e).__name__ == "CloudConnection"]
