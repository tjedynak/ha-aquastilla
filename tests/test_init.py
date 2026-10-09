"""Setting up the integration end to end against the fake backend."""

import json
import logging
import re
from pathlib import Path
from unittest.mock import patch

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr
from homeassistant.loader import async_get_integration
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
)

from custom_components.aquastilla import PLATFORMS, async_setup_entry, async_unload_entry, const
from custom_components.aquastilla.const import CONF_EMAIL, CONF_PASSWORD, DOMAIN
from custom_components.aquastilla.coordinator import AquastillaCoordinator
from custom_components.aquastilla.entity import AquastillaEntity

from .common import load_fixture
from .fake_backend import DEVICE_UUID, VALID_EMAIL, VALID_PASSWORD, FakeBackend

MANIFEST = Path(__file__).parent.parent / "custom_components" / DOMAIN / "manifest.json"


@pytest.fixture
def backend(fake_backend: FakeBackend, monkeypatch: pytest.MonkeyPatch) -> FakeBackend:
    """The fake backend, with the integration pointed at it."""
    monkeypatch.setattr("custom_components.aquastilla.BASE_URL", fake_backend.base_url)
    return fake_backend


def make_entry(password: str = VALID_PASSWORD) -> MockConfigEntry:
    return MockConfigEntry(domain=DOMAIN, data={CONF_EMAIL: VALID_EMAIL, CONF_PASSWORD: password})


async def setup_entry(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """Run ``async_setup_entry`` the way Home Assistant does, minus the config flow lookup.

    Going through ``hass.config_entries.async_setup`` needs a config_flow platform, which
    arrives with the config flow story. Until then the entry is put in the in-setup state
    and the setup function is called directly. A failure propagates to the caller.
    """
    entry.add_to_hass(hass)
    await run_setup(hass, entry)


async def run_setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    entry.mock_state(hass, ConfigEntryState.SETUP_IN_PROGRESS)
    # Home Assistant only allows forwarding inside its own setup task; there are no platforms yet.
    with (
        patch.object(type(entry), "async_start_reauth_if_available"),
        patch.object(hass.config_entries, "async_forward_entry_setups") as forward,
    ):
        assert await async_setup_entry(hass, entry)
    forward.assert_awaited_once_with(entry, PLATFORMS)
    await hass.async_block_till_done()


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
    assert (dr.CONNECTION_NETWORK_MAC, "00:00:5e:00:53:01") in device.connections
    assert device.config_entry_id == entry.entry_id


async def test_setting_up_again_does_not_duplicate_the_device(
    hass: HomeAssistant, backend: FakeBackend
) -> None:
    entry = make_entry()
    await setup_entry(hass, entry)
    assert await async_unload_entry(hass, entry)
    await run_setup(hass, entry)
    assert len(dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)) == 1


async def test_setup_stores_the_coordinator_on_the_entry(
    hass: HomeAssistant, backend: FakeBackend
) -> None:
    entry = make_entry()
    await setup_entry(hass, entry)
    assert isinstance(entry.runtime_data, AquastillaCoordinator)
    assert entry.runtime_data.update_interval == const.DEFAULT_SCAN_INTERVAL


async def test_first_poll_cost_is_one_login_one_list_and_three_reads(
    hass: HomeAssistant, backend: FakeBackend
) -> None:
    await setup_entry(hass, make_entry())
    paths = [(r.method, r.path) for r in backend.requests]
    assert paths == [
        ("POST", "/login-company"),
        ("GET", "/device/all"),
        ("GET", f"/device/{DEVICE_UUID}/state"),
        ("GET", f"/device/{DEVICE_UUID}/settings"),
        ("GET", f"/device/{DEVICE_UUID}/current_states"),
    ]


async def test_unload_succeeds(hass: HomeAssistant, backend: FakeBackend) -> None:
    entry = make_entry()
    await setup_entry(hass, entry)
    assert await async_unload_entry(hass, entry)


async def test_wrong_credentials_fail_setup_as_an_auth_failure(
    hass: HomeAssistant, backend: FakeBackend
) -> None:
    entry = make_entry(password="wrong")  # noqa: S106  # pragma: allowlist secret
    with pytest.raises(ConfigEntryAuthFailed):
        await setup_entry(hass, entry)
    assert [r.path for r in backend.requests] == ["/login-company"]


async def test_backend_outage_makes_setup_retry_later(
    hass: HomeAssistant, backend: FakeBackend
) -> None:
    backend.fail_always("GET", "/device/all", status=503)
    with pytest.raises(ConfigEntryNotReady):
        await setup_entry(hass, make_entry())


# --------------------------------------------------------- coordinator


async def coordinator_of(hass: HomeAssistant, backend: FakeBackend) -> AquastillaCoordinator:
    entry = make_entry()
    await setup_entry(hass, entry)
    entry.mock_state(hass, ConfigEntryState.LOADED)  # a reauth only starts for a loaded entry
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
    backend.fail_next("GET", "/device/all", status=500)
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


async def test_rate_limit_passes_retry_after_on(hass: HomeAssistant, backend: FakeBackend) -> None:
    coordinator = await coordinator_of(hass, backend)
    backend.fail_next("GET", "/device/all", status=429, headers={"Retry-After": "30"})
    await coordinator.async_refresh()
    assert coordinator.last_update_success is False
    assert getattr(coordinator.last_exception, "retry_after", None) == 30


async def test_credentials_rejected_during_a_poll_start_reauth(
    hass: HomeAssistant, backend: FakeBackend
) -> None:
    coordinator = await coordinator_of(hass, backend)
    backend.expire_tokens()
    backend.fail_always("POST", "/login-company", status=401)
    with patch.object(type(coordinator.config_entry), "async_start_reauth_if_available") as reauth:
        await coordinator.async_refresh()
    reauth.assert_called_once()
    assert coordinator.last_update_success is False


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

    backend.fail_next("GET", "/device/all", status=500)
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
