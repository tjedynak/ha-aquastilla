"""The commands as the vendor's app sends them: method, path, body and content type."""

from datetime import time
from unittest.mock import AsyncMock, patch

import pytest

from custom_components.aquastilla.api import AquastillaClient, DeviceDetachedError

from .common import load_fixture
from .fake_backend import DEVICE_UUID, FakeBackend

BASE = f"/device/{DEVICE_UUID}"


@pytest.fixture
async def client(backend: FakeBackend, session: object) -> AquastillaClient:
    return AquastillaClient(
        session,  # type: ignore[arg-type]
        "user@example.test",
        "Synthetic-Passw0rd1",  # pragma: allowlist secret
        base_url=backend.base_url,
    )


def last(backend: FakeBackend) -> tuple[str, str, object, str]:
    request = backend.requests[-1]
    return request.method, request.path, request.body, request.headers.get("Content-Type", "")


async def test_advance_cycle_sends_an_empty_object(
    backend: FakeBackend, client: AquastillaClient
) -> None:
    await client.advance_cycle(DEVICE_UUID)
    assert last(backend)[:3] == ("POST", f"{BASE}/state", {})


@pytest.mark.parametrize("enabled", [True, False])
async def test_vacation_mode_is_a_bare_boolean(
    backend: FakeBackend, client: AquastillaClient, enabled: bool
) -> None:
    await client.set_vacation_mode(DEVICE_UUID, enabled)
    assert last(backend)[:3] == ("POST", f"{BASE}/vacation_mode", enabled)


async def test_force_and_postpone_have_no_body(
    backend: FakeBackend, client: AquastillaClient
) -> None:
    await client.force_regeneration(DEVICE_UUID)
    assert last(backend)[:3] == ("POST", f"{BASE}/vacation_mode/force_regeneration", None)
    await client.delay_regeneration(DEVICE_UUID)
    assert last(backend)[:3] == ("POST", f"{BASE}/delay_regeneration", None)


async def test_hardness_carries_value_and_unit(
    backend: FakeBackend, client: AquastillaClient
) -> None:
    await client.set_water_hardness(DEVICE_UUID, 18, "dH")
    assert last(backend)[:3] == (
        "POST",
        f"{BASE}/settings/water_hardness",
        {"value": 18, "unit": "dH"},
    )


@pytest.mark.parametrize(
    ("method", "path", "argument"),
    [
        ("set_volume_unit", "/settings/volume_unit", "m3"),
        ("set_note", "/settings/note", "Check the brine tank"),
        ("rename_device", "/settings/rename", "Boiler room"),
    ],
)
async def test_bare_text_goes_as_text_plain(
    backend: FakeBackend, client: AquastillaClient, method: str, path: str, argument: str
) -> None:
    await getattr(client, method)(DEVICE_UUID, argument)
    verb, called, body, content_type = last(backend)
    assert (verb, called, body) == ("POST", f"{BASE}{path}", argument)
    assert content_type.startswith("text/plain")


async def test_regeneration_time_is_a_quoted_utc_labelled_string(
    backend: FakeBackend, client: AquastillaClient
) -> None:
    await client.set_regeneration_time(DEVICE_UUID, time(2, 30))
    _, path, body, _ = last(backend)
    assert path == f"{BASE}/settings/regeneration_time"
    assert isinstance(body, str)
    assert body.endswith("T02:30:00.000Z")


async def test_salt_alarm_is_a_bare_number(backend: FakeBackend, client: AquastillaClient) -> None:
    await client.set_salt_alarm(DEVICE_UUID, 25)
    assert last(backend)[:3] == ("POST", f"{BASE}/settings/alarm/salt", 25)


async def test_flood_alarm_is_patched_with_three_fields(
    backend: FakeBackend, client: AquastillaClient
) -> None:
    await client.set_flood_alarm(DEVICE_UUID, continuous_flow_time=60, threshold=2, max_flow=30)
    assert last(backend)[:3] == (
        "PATCH",
        f"{BASE}/settings/alarm/flood",
        {"continuousFlowTime": 60, "threshold": 2, "maxFlow": 30},
    )


async def test_remote_service_start_and_end_never_reach_the_endpoint(
    client: AquastillaClient,
) -> None:
    """Granting the vendor access is the owner's call: the transport is replaced."""
    with patch.object(AquastillaClient, "_request", new=AsyncMock(return_value=None)) as request:
        await client.start_remote_service(DEVICE_UUID, 12)
        await client.end_remote_service(DEVICE_UUID)
    first, second = (call.args[0] for call in request.await_args_list)
    assert (first.method, first.path, first.json) == (
        "POST",
        f"{BASE}/settings/remote_service",
        12,
    )
    assert (second.method, second.path, second.json) == (
        "POST",
        f"{BASE}/settings/remote_service/disable",
        None,
    )


async def test_set_default_device(backend: FakeBackend, client: AquastillaClient) -> None:
    await client.set_default_device(DEVICE_UUID)
    assert last(backend)[:3] == ("POST", f"/device/default/{DEVICE_UUID}", None)


async def test_a_command_does_not_read_the_answer(
    backend: FakeBackend, client: AquastillaClient
) -> None:
    backend.fail_next("POST", f"{BASE}/delay_regeneration", status=200, text_body="not json")
    await client.delay_regeneration(DEVICE_UUID)  # the app ignores the answer too


async def test_a_command_to_a_detached_device_says_so(
    backend: FakeBackend, client: AquastillaClient
) -> None:
    backend.fail_next(
        "POST",
        f"{BASE}/delay_regeneration",
        status=400,
        json_body=load_fixture("error_device_detached"),
    )
    with pytest.raises(DeviceDetachedError):
        await client.delay_regeneration(DEVICE_UUID)


async def test_closing_the_water_is_a_bare_false_and_never_sent_for_real(
    client: AquastillaClient,
) -> None:
    """The endpoint is never reached: the transport is replaced."""
    with patch.object(AquastillaClient, "_request", new=AsyncMock(return_value=None)) as request:
        await client.close_water_flow(DEVICE_UUID)
    assert request.await_args is not None
    call = request.await_args.args[0]
    assert (call.method, call.path, call.json, call.text) == (
        "POST",
        f"{BASE}/water_flow",
        False,
        None,
    )
    assert call.expect_body is False


async def test_pairing_and_removal_never_reach_the_endpoint(client: AquastillaClient) -> None:
    """Both are irreversible: the transport is replaced."""
    with patch.object(AquastillaClient, "_request", new=AsyncMock(return_value=None)) as request:
        await client.pair_device("user-1", "ABC123")
        await client.delete_device(DEVICE_UUID)
    first, second = (call.args[0] for call in request.await_args_list)
    assert (first.method, first.path, first.json) == (
        "POST",
        "/device",
        {"userId": "user-1", "code": "ABC123"},
    )
    assert (second.method, second.path) == ("DELETE", f"{BASE}/delete")
