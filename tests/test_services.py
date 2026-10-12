"""Service actions: confirmation, resolution of the device and the guarded pair and remove."""

import asyncio
from typing import Any
from unittest.mock import AsyncMock, patch

import probatio
import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import device_registry as dr
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.aquastilla.api import ApiError, AquastillaClient
from custom_components.aquastilla.const import CONF_ENABLE_DEVICE_MANAGEMENT, DOMAIN
from custom_components.aquastilla.models import DeviceEventType

from .common import load_fixture
from .control_helpers import BASE, SETTINGS, ack, started
from .fake_backend import DEVICE_UUID, FakeBackend


def device_id(hass: HomeAssistant) -> str:
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    device = dr.async_get(hass).async_get_device_by_identifier(
        (DOMAIN, DEVICE_UUID), entry.entry_id
    )
    assert device is not None
    return device.id


async def run(hass: HomeAssistant, service: str, **data: Any) -> None:
    await hass.services.async_call(DOMAIN, service, data, blocking=True)


# ------------------------------------------------------------------ rename


async def test_rename_sends_bare_text_and_reads_the_device_list_again(
    hass: HomeAssistant, backend: FakeBackend
) -> None:
    await started(hass, backend)
    reads = len(backend.requests_to("GET", "/device/all"))
    await run(hass, "rename_device", device_id=device_id(hass), name="  Boiler room ")
    request = backend.requests_to("POST", f"{BASE}/settings/rename")[0]
    assert request.body == "Boiler room"
    assert request.headers["Content-Type"].startswith("text/plain")
    assert len(backend.requests_to("GET", "/device/all")) > reads


async def test_the_new_name_reaches_the_home_assistant_device(
    hass: HomeAssistant, backend: FakeBackend
) -> None:
    await started(hass, backend)
    device = load_fixture("device")
    device["name"] = "Boiler room"
    backend.replace_json("/device/all", [device])
    await run(hass, "rename_device", device_id=device_id(hass), name="Boiler room")
    await hass.async_block_till_done()
    registered = dr.async_get(hass).async_get(device_id(hass))
    assert registered is not None
    assert registered.name == "Boiler room"


async def test_an_empty_name_is_refused_before_anything_is_sent(
    hass: HomeAssistant, backend: FakeBackend
) -> None:
    await started(hass, backend)
    with pytest.raises(probatio.Invalid, match="at least 1"):
        await run(hass, "rename_device", device_id=device_id(hass), name="")
    assert backend.requests_to("POST", f"{BASE}/settings/rename") == []


async def test_a_device_that_is_not_ours_is_refused(
    hass: HomeAssistant, backend: FakeBackend
) -> None:
    entry = await started(hass, backend)
    other = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={("somewhere", "else")}
    )
    with pytest.raises(ServiceValidationError, match="does not belong"):
        await run(hass, "rename_device", device_id=other.id, name="X")
    with pytest.raises(ServiceValidationError, match="Unknown device"):
        await run(hass, "rename_device", device_id="nope", name="X")


# ------------------------------------------------------------ remote service


@pytest.mark.parametrize(
    "data",
    [
        {"hours": 12},
        {"hours": 12, "confirm": False},
        {"hours": 12, "confirm": "yes"},
        {"hours": 0, "confirm": True},
        {"hours": 24, "confirm": True},
    ],
)
async def test_remote_service_needs_a_confirmation_and_sane_hours(
    hass: HomeAssistant, backend: FakeBackend, data: dict[str, Any]
) -> None:
    await started(hass, backend)
    with (
        patch.object(AquastillaClient, "start_remote_service", new=AsyncMock()) as start,
        pytest.raises(probatio.Invalid),
    ):
        await run(hass, "start_remote_service", device_id=device_id(hass), **data)
    start.assert_not_awaited()


async def test_remote_service_starts_and_waits_for_the_device(
    hass: HomeAssistant, backend: FakeBackend
) -> None:
    await started(hass, backend)
    with patch.object(AquastillaClient, "start_remote_service", new=AsyncMock()) as start:
        task = asyncio.ensure_future(
            run(hass, "start_remote_service", device_id=device_id(hass), hours=6, confirm=True)
        )
        await backend.wait_for(lambda: start.await_count == 1)
        await ack(backend, DeviceEventType.SET_SERVICE_MODE)
        await task
    start.assert_awaited_once_with(DEVICE_UUID, 6)


async def test_remote_service_can_be_ended_even_while_it_runs(
    hass: HomeAssistant, backend: FakeBackend
) -> None:
    backend.replace_json(SETTINGS, {**load_fixture("settings"), "serviceMode": True})
    await started(hass, backend)
    with patch.object(AquastillaClient, "end_remote_service", new=AsyncMock()) as end:
        await run(hass, "end_remote_service", device_id=device_id(hass))
    end.assert_awaited_once_with(DEVICE_UUID)


async def test_an_api_error_is_a_readable_error(hass: HomeAssistant, backend: FakeBackend) -> None:
    await started(hass, backend)
    backend.fail_next("POST", f"{BASE}/settings/rename", status=500)
    with pytest.raises(HomeAssistantError, match="could not be sent"):
        await run(hass, "rename_device", device_id=device_id(hass), name="X")


# --------------------------------------------------------- pair and remove


async def test_pair_and_remove_are_not_registered_by_default(
    hass: HomeAssistant, backend: FakeBackend
) -> None:
    await started(hass, backend)
    assert hass.services.has_service(DOMAIN, "rename_device")
    assert not hass.services.has_service(DOMAIN, "pair_device")
    assert not hass.services.has_service(DOMAIN, "delete_device")


async def enable_management(hass: HomeAssistant, entry: MockConfigEntry, enabled: bool) -> None:
    hass.config_entries.async_update_entry(
        entry, options={**entry.options, CONF_ENABLE_DEVICE_MANAGEMENT: enabled}
    )
    await hass.async_block_till_done()


async def test_the_option_registers_and_removes_them(
    hass: HomeAssistant, backend: FakeBackend
) -> None:
    entry = await started(hass, backend)
    await enable_management(hass, entry, True)
    assert hass.services.has_service(DOMAIN, "pair_device")
    assert hass.services.has_service(DOMAIN, "delete_device")
    await enable_management(hass, entry, False)
    assert not hass.services.has_service(DOMAIN, "pair_device")
    assert not hass.services.has_service(DOMAIN, "delete_device")


async def test_they_go_when_the_account_is_unloaded(
    hass: HomeAssistant, backend: FakeBackend
) -> None:
    entry = await started(hass, backend)
    await enable_management(hass, entry, True)
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert not hass.services.has_service(DOMAIN, "delete_device")
    assert hass.services.has_service(DOMAIN, "rename_device")  # the harmless ones stay


async def test_removing_needs_a_confirmation_and_never_reaches_the_endpoint(
    hass: HomeAssistant, backend: FakeBackend
) -> None:
    entry = await started(hass, backend)
    await enable_management(hass, entry, True)
    with patch.object(AquastillaClient, "delete_device", new=AsyncMock()) as delete:
        with pytest.raises(probatio.Invalid, match="confirm"):
            await run(hass, "delete_device", device_id=device_id(hass))
        with pytest.raises(probatio.Invalid, match="confirm"):
            await run(hass, "delete_device", device_id=device_id(hass), confirm=False)
        delete.assert_not_awaited()
        await run(hass, "delete_device", device_id=device_id(hass), confirm=True)
    delete.assert_awaited_once_with(DEVICE_UUID)
    assert backend.forbidden == []


async def test_pairing_uses_the_account_and_the_code(
    hass: HomeAssistant, backend: FakeBackend
) -> None:
    entry = await started(hass, backend)
    await enable_management(hass, entry, True)
    with patch.object(AquastillaClient, "pair_device", new=AsyncMock()) as pair:
        await run(
            hass, "pair_device", config_entry_id=entry.entry_id, code=" ABC123 ", confirm=True
        )
    pair.assert_awaited_once_with(load_fixture("details")["userId"], "ABC123")
    assert backend.forbidden == []


async def test_pairing_with_an_unknown_account_is_refused(
    hass: HomeAssistant, backend: FakeBackend
) -> None:
    entry = await started(hass, backend)
    await enable_management(hass, entry, True)
    with pytest.raises(ServiceValidationError, match="Unknown Aquastilla account"):
        await run(hass, "pair_device", config_entry_id="nope", code="X", confirm=True)
    assert entry.state.value == "loaded"


async def test_a_failing_removal_is_a_readable_error(
    hass: HomeAssistant, backend: FakeBackend
) -> None:
    entry = await started(hass, backend)
    await enable_management(hass, entry, True)
    with (
        patch.object(
            AquastillaClient,
            "delete_device",
            new=AsyncMock(side_effect=ApiError("boom")),
        ),
        pytest.raises(HomeAssistantError, match="could not be sent"),
    ):
        await run(hass, "delete_device", device_id=device_id(hass), confirm=True)


async def test_a_device_the_account_does_not_list_is_unavailable(
    hass: HomeAssistant, backend: FakeBackend
) -> None:
    entry = await started(hass, backend)
    ghost = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, "00000000-0000-4000-8000-00000000ffff")},
    )
    with pytest.raises(HomeAssistantError, match="not available"):
        await run(hass, "rename_device", device_id=ghost.id, name="X")


async def test_pairing_fails_readably_when_the_account_has_no_user_id(
    hass: HomeAssistant, backend: FakeBackend
) -> None:
    entry = await started(hass, backend)
    await enable_management(hass, entry, True)
    backend.replace_json("/details", {**load_fixture("details"), "userId": None})
    with pytest.raises(HomeAssistantError, match="not available"):
        await run(hass, "pair_device", config_entry_id=entry.entry_id, code="X", confirm=True)


async def test_pairing_that_fails_is_a_readable_error(
    hass: HomeAssistant, backend: FakeBackend
) -> None:
    entry = await started(hass, backend)
    await enable_management(hass, entry, True)
    with (
        patch.object(AquastillaClient, "pair_device", new=AsyncMock(side_effect=ApiError("no"))),
        pytest.raises(HomeAssistantError, match="could not be sent"),
    ):
        await run(hass, "pair_device", config_entry_id=entry.entry_id, code="X", confirm=True)
