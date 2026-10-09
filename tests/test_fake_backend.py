"""The fake backend must behave like the vendor backend the client will face."""

import asyncio
import json

import aiohttp
import pytest
from pytest_socket import SocketConnectBlockedError

from .common import load_fixture
from .fake_backend import (
    COMPANY_ID,
    DEVICE_UUID,
    VALID_EMAIL,
    VALID_PASSWORD,
    FakeBackend,
)

LOGIN = {
    "emailOrPhone": VALID_EMAIL,
    "password": VALID_PASSWORD,
    "language": "en",
    "companyId": COMPANY_ID,
}


async def login(session: aiohttp.ClientSession, backend: FakeBackend) -> dict[str, str]:
    async with session.post(f"{backend.base_url}/login-company", json=LOGIN) as resp:
        assert resp.status == 200
        token = (await resp.json())["jwt"]
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------- HTTP


async def test_login_returns_a_token(
    session: aiohttp.ClientSession, fake_backend: FakeBackend
) -> None:
    headers = await login(session, fake_backend)
    assert headers["Authorization"].startswith("Bearer SYNTHETIC-JWT-")


@pytest.mark.parametrize(
    "override",
    [
        {"password": "wrong"},  # pragma: allowlist secret
        {"emailOrPhone": "other@example.test"},
        {"companyId": "x"},
    ],  # pragma: allowlist secret
)
async def test_login_rejects_bad_credentials(
    session: aiohttp.ClientSession, fake_backend: FakeBackend, override: dict[str, str]
) -> None:
    async with session.post(
        f"{fake_backend.base_url}/login-company", json={**LOGIN, **override}
    ) as resp:
        assert resp.status == 401


async def test_login_requires_every_field(
    session: aiohttp.ClientSession, fake_backend: FakeBackend
) -> None:
    body = {k: v for k, v in LOGIN.items() if k != "language"}
    async with session.post(f"{fake_backend.base_url}/login-company", json=body) as resp:
        assert resp.status == 401


async def test_reads_require_a_token(
    session: aiohttp.ClientSession, fake_backend: FakeBackend
) -> None:
    async with session.get(f"{fake_backend.base_url}/device/all") as resp:
        assert resp.status == 401
    async with session.get(
        f"{fake_backend.base_url}/device/all", headers={"Authorization": "Bearer nonsense"}
    ) as resp:
        assert resp.status == 401


@pytest.mark.parametrize(
    ("suffix", "fixture"),
    [
        ("", "device"),
        ("/state", "state"),
        ("/settings", "settings"),
        ("/current_states", "current_states"),
        ("/mode/all", "mode_all"),
        ("/water_data", "water_data"),
        ("/year/water_data", "year_water_data"),
        ("/previous_current_water_usage", "previous_current_water_usage"),
    ],
)
async def test_device_reads_serve_fixtures(
    session: aiohttp.ClientSession, fake_backend: FakeBackend, suffix: str, fixture: str
) -> None:
    headers = await login(session, fake_backend)
    async with session.get(
        f"{fake_backend.base_url}{fake_backend.device_path(suffix)}", headers=headers
    ) as resp:
        assert resp.status == 200
        assert await resp.json() == load_fixture(fixture)


@pytest.mark.parametrize(
    ("path", "fixture"),
    [("/details", "details"), ("/device/all", "device_all"), ("/event/pageable", "event_pageable")],
)
async def test_account_reads_serve_fixtures(
    session: aiohttp.ClientSession, fake_backend: FakeBackend, path: str, fixture: str
) -> None:
    headers = await login(session, fake_backend)
    async with session.get(
        f"{fake_backend.base_url}{path}", headers=headers, params={"page": "0"}
    ) as resp:
        assert await resp.json() == load_fixture(fixture)
    assert fake_backend.requests_to("GET", path)[0].query == {"page": "0"}


async def test_unknown_device_is_reported_as_detached(
    session: aiohttp.ClientSession, fake_backend: FakeBackend
) -> None:
    headers = await login(session, fake_backend)
    async with session.get(
        f"{fake_backend.base_url}/device/not-ours/state", headers=headers
    ) as resp:
        assert resp.status == 400
        assert (await resp.json())["code"] == -3


async def test_unknown_route_is_404(
    session: aiohttp.ClientSession, fake_backend: FakeBackend
) -> None:
    headers = await login(session, fake_backend)
    async with session.get(f"{fake_backend.base_url}/nope", headers=headers) as resp:
        assert resp.status == 404


async def test_writes_are_recorded_with_their_bodies(
    session: aiohttp.ClientSession, fake_backend: FakeBackend
) -> None:
    headers = await login(session, fake_backend)
    base = fake_backend.base_url
    async with session.post(
        f"{base}{fake_backend.device_path('/vacation_mode')}", json=True, headers=headers
    ) as resp:
        assert resp.status == 200
    async with session.post(
        f"{base}{fake_backend.device_path('/settings/rename')}",
        data="Boiler room",
        headers={**headers, "Content-Type": "text/plain"},
    ) as resp:
        assert resp.status == 200
    async with session.patch(
        f"{base}{fake_backend.device_path('/settings/alarm/flood')}",
        json={"continuousFlowTime": 30, "threshold": 5, "maxFlow": 20},
        headers=headers,
    ) as resp:
        assert resp.status == 200
    assert (
        fake_backend.requests_to("POST", fake_backend.device_path("/vacation_mode"))[0].body is True
    )
    assert (
        fake_backend.requests_to("POST", fake_backend.device_path("/settings/rename"))[0].body
        == "Boiler room"
    )
    assert fake_backend.requests_to("PATCH", fake_backend.device_path("/settings/alarm/flood"))[
        0
    ].body == {
        "continuousFlowTime": 30,
        "threshold": 5,
        "maxFlow": 20,
    }


async def test_expired_tokens_force_a_new_login(
    session: aiohttp.ClientSession, fake_backend: FakeBackend
) -> None:
    headers = await login(session, fake_backend)
    fake_backend.expire_tokens()
    async with session.get(f"{fake_backend.base_url}/device/all", headers=headers) as resp:
        assert resp.status == 401
    fresh = await login(session, fake_backend)
    assert fresh != headers
    async with session.get(f"{fake_backend.base_url}/device/all", headers=fresh) as resp:
        assert resp.status == 200


async def test_fail_next_injects_one_fault(
    session: aiohttp.ClientSession, fake_backend: FakeBackend
) -> None:
    headers = await login(session, fake_backend)
    path = fake_backend.device_path("/state")
    fake_backend.fail_next("GET", path, status=500, text_body="boom")
    async with session.get(f"{fake_backend.base_url}{path}", headers=headers) as resp:
        assert resp.status == 500
        assert await resp.text() == "boom"
    async with session.get(f"{fake_backend.base_url}{path}", headers=headers) as resp:
        assert resp.status == 200


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/event/set_seen/evt-3"),
        ("POST", "/event/set_seen/all"),
        ("POST", "/event/set_hidden/evt-3"),
        ("POST", "/event/set_hidden/all"),
    ],
)
async def test_event_state_routes_are_accepted(
    session: aiohttp.ClientSession, fake_backend: FakeBackend, method: str, path: str
) -> None:
    headers = await login(session, fake_backend)
    async with session.request(
        method,
        f"{fake_backend.base_url}{path}",
        headers=headers,
        json={} if method == "POST" else None,
    ) as resp:
        assert resp.status == 200
    assert len(fake_backend.requests_to(method, path)) == 1


@pytest.mark.parametrize("status", [401, 403])
async def test_fail_next_can_simulate_either_auth_failure(
    session: aiohttp.ClientSession, fake_backend: FakeBackend, status: int
) -> None:
    headers = await login(session, fake_backend)
    fake_backend.fail_next("GET", "/details", status=status)
    async with session.get(f"{fake_backend.base_url}/details", headers=headers) as resp:
        assert resp.status == status
    async with session.get(f"{fake_backend.base_url}/details", headers=headers) as resp:
        assert resp.status == 200


async def test_fail_next_can_return_a_json_error(
    session: aiohttp.ClientSession, fake_backend: FakeBackend
) -> None:
    headers = await login(session, fake_backend)
    fake_backend.fail_next(
        "GET", "/device/all", status=400, json_body=load_fixture("error_device_detached")
    )
    async with session.get(f"{fake_backend.base_url}/device/all", headers=headers) as resp:
        assert resp.status == 400
        assert (await resp.json())["code"] == -3


async def test_delay_makes_a_client_timeout(
    session: aiohttp.ClientSession, fake_backend: FakeBackend
) -> None:
    headers = await login(session, fake_backend)
    fake_backend.delay("GET", "/device/all", 1.0)
    with pytest.raises(TimeoutError):
        async with session.get(
            f"{fake_backend.base_url}/device/all",
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=0.1),
        ):
            pass


async def test_replace_json_overrides_a_fixture(
    session: aiohttp.ClientSession, fake_backend: FakeBackend
) -> None:
    headers = await login(session, fake_backend)
    path = fake_backend.device_path("/state")
    fake_backend.replace_json(path, {**load_fixture("state"), "isOnline": False})
    async with session.get(f"{fake_backend.base_url}{path}", headers=headers) as resp:
        assert (await resp.json())["isOnline"] is False


# ------------------------------------------------------- irreversible endpoints


@pytest.mark.parametrize(
    ("method", "suffix"),
    [
        ("POST", "/water_flow"),
        ("DELETE", "/delete"),
        ("POST", "/settings/remote_service"),
        ("POST", "/settings/remote_service/disable"),
    ],
)
async def test_irreversible_endpoints_are_refused_and_flagged(
    session: aiohttp.ClientSession, fake_backend: FakeBackend, method: str, suffix: str
) -> None:
    headers = await login(session, fake_backend)
    async with session.request(
        method, f"{fake_backend.base_url}{fake_backend.device_path(suffix)}", headers=headers
    ) as resp:
        assert resp.status == 418
    assert len(fake_backend.forbidden) == 1
    fake_backend.forbidden.clear()  # the fixture would otherwise fail this test on purpose


async def test_pairing_is_refused_and_flagged(
    session: aiohttp.ClientSession, fake_backend: FakeBackend
) -> None:
    headers = await login(session, fake_backend)
    async with session.post(
        f"{fake_backend.base_url}/device", json={"userId": "x", "code": "y"}, headers=headers
    ) as resp:
        assert resp.status == 418
    fake_backend.forbidden.clear()


# ----------------------------------------------------------------- WebSocket


async def authenticate(
    ws: aiohttp.ClientWebSocketResponse, token: str, frame_id: str = "1"
) -> dict[str, object]:
    await ws.send_json(
        {"id": frame_id, "path": "/authenticate", "headers": {"jwt": token}, "body": None}
    )
    reply = await ws.receive_json()
    assert isinstance(reply, dict)
    return reply


async def token_for(session: aiohttp.ClientSession, backend: FakeBackend) -> str:
    return (await login(session, backend))["Authorization"].removeprefix("Bearer ")


async def test_ws_authenticate_success(
    session: aiohttp.ClientSession, fake_backend: FakeBackend
) -> None:
    token = await token_for(session, fake_backend)
    async with session.ws_connect(fake_backend.ws_url) as ws:
        reply = await authenticate(ws, token, "42")
    assert reply["headers"] == {"responseFor": "42"}
    assert fake_backend.ws_frames[0]["headers"] == {"jwt": token}


async def test_ws_authenticate_failure(
    session: aiohttp.ClientSession, fake_backend: FakeBackend
) -> None:
    async with session.ws_connect(fake_backend.ws_url) as ws:
        reply = await authenticate(ws, "not-a-token", "7")
    assert reply["headers"] == {"ErrorResponseFor": "7"}


async def test_ws_authenticate_rejected_after_expiry(
    session: aiohttp.ClientSession, fake_backend: FakeBackend
) -> None:
    token = await token_for(session, fake_backend)
    fake_backend.expire_tokens()
    async with session.ws_connect(fake_backend.ws_url) as ws:
        reply = await authenticate(ws, token)
    assert "ErrorResponseFor" in reply["headers"]  # type: ignore[operator]


async def test_ws_silent_server_triggers_client_timeout(
    session: aiohttp.ClientSession, fake_backend: FakeBackend
) -> None:
    fake_backend.ws_auth_replies = False
    async with session.ws_connect(fake_backend.ws_url) as ws:
        await ws.send_json(
            {"id": "1", "path": "/authenticate", "headers": {"jwt": "x"}, "body": None}
        )
        with pytest.raises(TimeoutError):
            await ws.receive_json(timeout=0.1)


@pytest.mark.parametrize(
    ("path", "fixture"),
    [
        ("/device/event", "ws_device_event"),
        ("/device/event_seen", "ws_event_seen"),
        ("/device/percentage", "ws_percentage"),
    ],
)
async def test_ws_push_body_is_a_json_string(
    session: aiohttp.ClientSession, fake_backend: FakeBackend, path: str, fixture: str
) -> None:
    async with session.ws_connect(fake_backend.ws_url) as ws:
        await fake_backend.wait_for(lambda: fake_backend.ws_connected)
        await fake_backend.push(path, load_fixture(fixture))
        frame = await ws.receive_json()
    assert frame["path"] == path
    assert isinstance(frame["body"], str)
    assert json.loads(frame["body"]) == load_fixture(fixture)


async def test_ws_push_can_send_an_unencoded_body(
    session: aiohttp.ClientSession, fake_backend: FakeBackend
) -> None:
    async with session.ws_connect(fake_backend.ws_url) as ws:
        await fake_backend.wait_for(lambda: fake_backend.ws_connected)
        await fake_backend.push(
            "/device/percentage", {"uuid": DEVICE_UUID, "percentage": 1}, encode=False
        )
        frame = await ws.receive_json()
    assert frame["body"] == {"uuid": DEVICE_UUID, "percentage": 1}


async def test_ws_ping_pong_round_trip(
    session: aiohttp.ClientSession, fake_backend: FakeBackend
) -> None:
    async with session.ws_connect(fake_backend.ws_url) as ws:
        await fake_backend.wait_for(lambda: fake_backend.ws_connected)
        await fake_backend.send_ping()
        ping = await ws.receive_json()
        assert ping["path"] == "/ping"
        await ws.send_json({"id": "9", "path": "/pong", "headers": {"jwt": "x"}, "body": ""})
        frame = await fake_backend.wait_for_frame("/pong")
    assert frame["id"] == "9"


async def test_ws_malformed_frames_are_recorded_not_fatal(
    session: aiohttp.ClientSession, fake_backend: FakeBackend
) -> None:
    async with session.ws_connect(fake_backend.ws_url) as ws:
        await ws.send_str("{not json")
        await ws.send_str("[1, 2]")
        await fake_backend.wait_for(lambda: len(fake_backend.ws_malformed) == 2)
        await ws.send_json({"id": "1", "path": "/pong", "headers": {}, "body": ""})
        await fake_backend.wait_for_frame("/pong")
    assert fake_backend.ws_malformed == ["{not json", "[1, 2]"]


async def test_ws_server_side_send_raw_reaches_client(
    session: aiohttp.ClientSession, fake_backend: FakeBackend
) -> None:
    async with session.ws_connect(fake_backend.ws_url) as ws:
        await fake_backend.wait_for(lambda: fake_backend.ws_connected)
        await fake_backend.send_raw("garbage")
        assert (await ws.receive_str()) == "garbage"


async def test_ws_drop_closes_the_client_connection(
    session: aiohttp.ClientSession, fake_backend: FakeBackend
) -> None:
    async with session.ws_connect(fake_backend.ws_url) as ws:
        await fake_backend.wait_for(lambda: fake_backend.ws_connected)
        await fake_backend.drop_connections()
        msg = await ws.receive(timeout=2)
        assert msg.type in {aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSED}
    assert not fake_backend.ws_connected


async def test_wait_for_times_out(fake_backend: FakeBackend) -> None:
    with pytest.raises(TimeoutError):
        await fake_backend.wait_for(lambda: False, within=0.05)


# ------------------------------------------------------------------ guard


@pytest.mark.filterwarnings("ignore:A test tried to use socket.socket.connect")
async def test_only_loopback_is_reachable(socket_enabled: None) -> None:
    """Even with sockets enabled for the fake backend, the real world stays blocked."""
    async with aiohttp.ClientSession() as client:
        with pytest.raises(SocketConnectBlockedError):
            await asyncio.wait_for(client.get("http://192.0.2.1:15880/"), timeout=2)
