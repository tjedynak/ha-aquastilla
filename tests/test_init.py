"""Setting up the integration end to end against the fake backend."""

import json
import logging
import re
from pathlib import Path

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.loader import async_get_integration

from custom_components.aquastilla import const
from custom_components.aquastilla.const import DOMAIN
from custom_components.aquastilla.coordinator import AquastillaCoordinator
from custom_components.aquastilla.entity import AquastillaEntity

from .common import load_fixture
from .fake_backend import DEVICE_UUID, FakeBackend
from .setup_helpers import make_entry, setup_entry

MANIFEST = Path(__file__).parent.parent / "custom_components" / DOMAIN / "manifest.json"


# ------------------------------------------------------------- manifest


def test_manifest_has_what_home_assistant_requires() -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    for key in (
        "domain", "name", "version", "documentation", "issue_tracker",
        "codeowners", "requirements", "iot_class", "integration_type",
    ):  # fmt: skip
        assert key in manifest, key
    assert manifest["domain"] == const.DOMAIN
    assert manifest["iot_class"] == "cloud_polling"
    assert re.fullmatch(r"\d+\.\d+\.\d+", manifest["version"])
    assert manifest["documentation"].startswith("https://")
    assert manifest["issue_tracker"].startswith("https://")


async def test_home_assistant_can_load_the_integration(hass: HomeAssistant) -> None:
    integration = await async_get_integration(hass, DOMAIN)
    assert integration.domain == DOMAIN
    assert str(integration.version) == json.loads(MANIFEST.read_text(encoding="utf-8"))["version"]


# --------------------------------------------------------------- setup


async def test_setup_registers_the_device(hass: HomeAssistant, backend: FakeBackend) -> None:
    entry = make_entry()
    await setup_entry(hass, entry)

    devices = dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)
    assert len(devices) == 1
    device = devices[0]
    assert device.identifiers == {(DOMAIN, DEVICE_UUID)}
    assert device.name == "Synthetic softener"
    assert device.manufacturer == "SYNTHETIC"
    assert device.model == "SYNTHETIC DUO"
    assert device.serial_number == "SYNTHETIC0000001"
    assert (dr.CONNECTION_NETWORK_MAC, "00:00:5e:00:53:01") in device.connections
    assert device.config_entry_id == entry.entry_id


async def test_setting_up_again_does_not_duplicate_the_device(
    hass: HomeAssistant, backend: FakeBackend
) -> None:
    entry = make_entry()
    await setup_entry(hass, entry)
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    assert len(dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)) == 1


async def test_setup_stores_the_coordinator_on_the_entry(
    hass: HomeAssistant, backend: FakeBackend
) -> None:
    entry = make_entry()
    await setup_entry(hass, entry)
    assert isinstance(entry.runtime_data, AquastillaCoordinator)
    assert entry.runtime_data.update_interval == const.DEFAULT_SCAN_INTERVAL


async def test_first_poll_cost_is_one_login_one_list_and_four_reads(
    hass: HomeAssistant, backend: FakeBackend
) -> None:
    await setup_entry(hass, make_entry())
    paths = [(r.method, r.path) for r in backend.requests]
    assert paths == [
        ("POST", "/login-company"),
        ("GET", "/device/all"),
        ("GET", f"/device/{DEVICE_UUID}/state"),
        ("GET", f"/device/{DEVICE_UUID}/current_states"),
        ("GET", f"/device/{DEVICE_UUID}/settings"),
        ("GET", f"/device/{DEVICE_UUID}/water_data"),
    ]


async def test_unload_succeeds(hass: HomeAssistant, backend: FakeBackend) -> None:
    entry = make_entry()
    await setup_entry(hass, entry)
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.NOT_LOADED


async def test_wrong_credentials_fail_setup_as_an_auth_failure(
    hass: HomeAssistant, backend: FakeBackend
) -> None:
    entry = make_entry(password="wrong")  # noqa: S106  # pragma: allowlist secret
    await setup_entry(hass, entry)
    assert entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert [f["context"]["source"] for f in flows] == ["reauth"]
    assert [r.path for r in backend.requests] == ["/login-company"]


async def test_backend_outage_makes_setup_retry_later(
    hass: HomeAssistant, backend: FakeBackend
) -> None:
    backend.fail_always("GET", "/device/all", status=503)
    entry = make_entry()
    await setup_entry(hass, entry)
    assert entry.state is ConfigEntryState.SETUP_RETRY


# --------------------------------------------------------- coordinator


async def coordinator_of(hass: HomeAssistant, backend: FakeBackend) -> AquastillaCoordinator:
    entry = make_entry()
    await setup_entry(hass, entry)
    assert entry.state is ConfigEntryState.LOADED
    coordinator: AquastillaCoordinator = entry.runtime_data
    return coordinator


async def test_data_is_parsed_into_models(hass: HomeAssistant, backend: FakeBackend) -> None:
    coordinator = await coordinator_of(hass, backend)
    data = coordinator.data.devices[DEVICE_UUID]
    assert data.device.name == "Synthetic softener"
    assert data.state.salt_days == 41
    assert data.settings.water_hardness == 18
    assert data.current_states.current == "Softening"


async def test_refresh_picks_up_new_values(hass: HomeAssistant, backend: FakeBackend) -> None:
    coordinator = await coordinator_of(hass, backend)
    backend.replace_json(f"/device/{DEVICE_UUID}/state", {**load_fixture("state"), "saltDays": 12})
    await coordinator.async_refresh()
    assert coordinator.data.devices[DEVICE_UUID].state.salt_days == 12


async def test_a_failed_poll_keeps_the_last_data_and_recovers(
    hass: HomeAssistant, backend: FakeBackend
) -> None:
    coordinator = await coordinator_of(hass, backend)
    backend.fail_next("GET", f"/device/{DEVICE_UUID}/state", status=500)
    await coordinator.async_refresh()
    assert coordinator.last_update_success is False
    assert coordinator.data.devices[DEVICE_UUID].state.salt_days == 41
    await coordinator.async_refresh()
    assert coordinator.last_update_success is True


async def test_an_expired_session_is_renewed_during_a_poll(
    hass: HomeAssistant, backend: FakeBackend
) -> None:
    coordinator = await coordinator_of(hass, backend)
    backend.expire_tokens()
    await coordinator.async_refresh()
    assert coordinator.last_update_success is True
    assert len(backend.requests_to("POST", "/login-company")) == 2


@pytest.mark.parametrize(("retry_after", "expected"), [("30", 600), ("7200", 7200)])
async def test_rate_limit_waits_for_the_longer_of_retry_after_and_backoff(
    hass: HomeAssistant, backend: FakeBackend, retry_after: str, expected: float
) -> None:
    coordinator = await coordinator_of(hass, backend)
    backend.fail_next(
        "GET",
        f"/device/{DEVICE_UUID}/state",
        status=429,
        headers={"Retry-After": retry_after},
    )
    await coordinator.async_refresh()
    assert coordinator.last_update_success is False
    assert getattr(coordinator.last_exception, "retry_after", None) == expected


async def test_credentials_rejected_during_a_poll_start_reauth(
    hass: HomeAssistant, backend: FakeBackend
) -> None:
    coordinator = await coordinator_of(hass, backend)
    backend.expire_tokens()
    backend.fail_always("POST", "/login-company", status=401)
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert coordinator.last_update_success is False
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert [f["context"]["source"] for f in flows] == ["reauth"]


async def test_a_detached_device_drops_out_of_the_data(
    hass: HomeAssistant, backend: FakeBackend, caplog: pytest.LogCaptureFixture
) -> None:
    coordinator = await coordinator_of(hass, backend)
    caplog.set_level(logging.WARNING)
    backend.fail_next(
        "GET",
        f"/device/{DEVICE_UUID}/state",
        status=400,
        json_body=load_fixture("error_device_detached"),
    )
    await coordinator.async_refresh()
    assert coordinator.last_update_success is True
    assert coordinator.data.devices == {}
    assert "no longer attached" in caplog.text


# --------------------------------------------------------------- entity


def is_available(entity: AquastillaEntity) -> bool:
    return entity.available


async def test_entity_base_carries_device_info_and_availability(
    hass: HomeAssistant, backend: FakeBackend
) -> None:
    coordinator = await coordinator_of(hass, backend)
    entity = AquastillaEntity(coordinator, DEVICE_UUID)
    info = dict(entity.device_info or {})
    assert info["identifiers"] == {(DOMAIN, DEVICE_UUID)}
    assert info["model"] == "SYNTHETIC DUO"
    assert is_available(entity)

    backend.fail_next("GET", f"/device/{DEVICE_UUID}/state", status=500)
    await coordinator.async_refresh()
    assert not is_available(entity)  # the last poll failed

    backend.fail_next(
        "GET",
        f"/device/{DEVICE_UUID}/state",
        status=400,
        json_body=load_fixture("error_device_detached"),
    )
    await coordinator.async_refresh()
    assert coordinator.last_update_success is True
    assert not is_available(entity)  # the device left the account
