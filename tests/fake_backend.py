"""A fake of the vendor backend: a real HTTP and WebSocket server on loopback.

It serves the synthetic fixtures over the same routes the vendor app uses, so
client code runs against real sockets and real aiohttp, not against mocks of its
own internals. Tests can inject faults (``fail_next``, ``delay``), push frames
(``push``) and inspect what the client sent (``requests``, ``ws_frames``).

Two safety rules are built in:

* Endpoints that are irreversible or reach outside the test (water shut-off,
  device deletion, pairing) are never executed. A call is recorded in
  ``forbidden`` and answered with 418, and the ``fake_backend`` fixture fails the
  test at teardown.
* Only loopback is reachable. Every other host stays blocked by the socket guard.
"""

import asyncio
import contextlib
import json
import re
from collections import defaultdict, deque
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from aiohttp import WSMsgType, web
from aiohttp.test_utils import TestServer

from .common import load_fixture

COMPANY_ID = "67adc541461d62305c8168c8"  # public constant baked into the vendor app
VALID_EMAIL = "user@example.test"
VALID_PASSWORD = "Synthetic-Passw0rd1"  # noqa: S105  # pragma: allowlist secret
DEVICE_UUID: str = load_fixture("device")["uuid"]

_FORBIDDEN = (
    ("*", re.compile(r"^/device/[^/]+/water_flow$")),
    ("DELETE", re.compile(r"^/device/[^/]+/delete$")),
    ("POST", re.compile(r"^/device$")),
    ("*", re.compile(r"^/device/[^/]+/settings/remote_service(/disable)?$")),
)

# GET path (relative to the device) -> fixture name
_DEVICE_READS = {
    "": "device",
    "/state": "state",
    "/settings": "settings",
    "/current_states": "current_states",
    "/mode/all": "mode_all",
    "/water_data": "water_data",
    "/year/water_data": "year_water_data",
    "/previous_current_water_usage": "previous_current_water_usage",
}

# GET path -> fixture name, for routes that are not scoped to a device
_ACCOUNT_READS = {
    "/details": "details",
    "/device/all": "device_all",
    "/event/pageable": "event_pageable",
}

# Command and settings writes the fake accepts with an empty 200.
_WRITES = (
    re.compile(r"^/device/[^/]+/state$"),
    re.compile(r"^/device/[^/]+/vacation_mode(/force_regeneration)?$"),
    re.compile(r"^/device/[^/]+/delay_regeneration$"),
    re.compile(r"^/device/[^/]+/settings/(water_hardness|volume_unit|rename|regeneration_time)$"),
    re.compile(r"^/device/[^/]+/settings/note$"),
    re.compile(r"^/device/[^/]+/settings/alarm/(salt|flood)$"),
    re.compile(r"^/device/[^/]+/settings/notification/(salt|flood)$"),
    re.compile(r"^/device/default/[^/]+$"),
    re.compile(r"^/event/set_(seen|hidden)/all$"),
    re.compile(r"^/event/set_hidden/[^/]+$"),
)


@dataclass
class RecordedRequest:
    """One HTTP request as the fake received it."""

    method: str
    path: str
    query: dict[str, str]
    headers: dict[str, str]
    body: Any


@dataclass
class _Override:
    status: int = 200
    json_body: Any = None
    text_body: str | None = None
    delay: float = 0.0
    sticky: bool = False
    headers: dict[str, str] = field(default_factory=dict)


@dataclass
class FakeBackend:
    """State and behaviour of the fake vendor backend."""

    server: TestServer | None = None
    requests: list[RecordedRequest] = field(default_factory=list)
    forbidden: list[RecordedRequest] = field(default_factory=list)
    ws_frames: list[dict[str, Any]] = field(default_factory=list)
    ws_malformed: list[str] = field(default_factory=list)
    ws_auth_replies: bool = True
    ws_refuse: bool = False
    ws_connect_attempts: int = 0
    _overrides: dict[tuple[str, str], deque[_Override]] = field(
        default_factory=lambda: defaultdict(deque)
    )
    _json_replacements: dict[str, Any] = field(default_factory=dict)
    _valid_tokens: set[str] = field(default_factory=set)
    _token_counter: int = 0
    _ws_clients: list[web.WebSocketResponse] = field(default_factory=list)

    # ---------------------------------------------------------------- lifecycle

    async def start(self) -> None:
        app = web.Application()
        app.router.add_get("/websocket", self._handle_ws)
        app.router.add_route("*", "/{tail:.*}", self._handle_http)
        self.server = TestServer(app, host="127.0.0.1")
        await self.server.start_server()

    async def stop(self) -> None:
        for ws in list(self._ws_clients):
            await ws.close()
        if self.server is not None:
            await self.server.close()

    @property
    def base_url(self) -> str:
        assert self.server is not None
        return str(self.server.make_url("")).rstrip("/")

    @property
    def ws_url(self) -> str:
        return self.base_url.replace("http://", "ws://") + "/websocket"

    # ----------------------------------------------------------- test controls

    def device_path(self, suffix: str = "") -> str:
        """Concrete path for the one device the fake knows."""
        return f"/device/{DEVICE_UUID}{suffix}"

    def fail_next(
        self,
        method: str,
        path: str,
        *,
        status: int = 500,
        json_body: Any = None,
        text_body: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        """Answer the next matching request with an error, then behave normally."""
        self._overrides[(method.upper(), path)].append(
            _Override(
                status=status, json_body=json_body, text_body=text_body, headers=headers or {}
            )
        )

    def fail_always(
        self,
        method: str,
        path: str,
        *,
        status: int = 500,
        json_body: Any = None,
        text_body: str | None = None,
    ) -> None:
        """Answer every matching request with an error, for as long as the test runs."""
        self._overrides[(method.upper(), path)].append(
            _Override(status=status, json_body=json_body, text_body=text_body, sticky=True)
        )

    def delay(self, method: str, path: str, seconds: float) -> None:
        """Hold every matching request for ``seconds`` before answering."""
        self._overrides[(method.upper(), path)].append(_Override(delay=seconds, sticky=True))

    def replace_json(self, path: str, payload: Any) -> None:
        """Serve ``payload`` instead of the fixture for a GET path."""
        self._json_replacements[path] = payload

    def expire_tokens(self) -> None:
        """Invalidate every issued token, as an expired session would."""
        self._valid_tokens.clear()

    def requests_to(self, method: str, path: str) -> list[RecordedRequest]:
        return [r for r in self.requests if r.method == method.upper() and r.path == path]

    # ------------------------------------------------------------- WebSocket

    @property
    def ws_connected(self) -> bool:
        return any(not ws.closed for ws in self._ws_clients)

    async def push(self, path: str, body: Any, *, encode: bool = True) -> None:
        """Push a frame to every connected client.

        The real backend sends the body as a JSON-encoded string.
        """
        payload = json.dumps(body) if encode else body
        await self._send_all({"path": path, "headers": {}, "body": payload})

    async def send_ping(self) -> None:
        await self._send_all({"path": "/ping", "headers": {}, "body": ""})

    async def send_raw(self, text: str) -> None:
        for ws in self._open_clients():
            await ws.send_str(text)

    async def drop_connections(self) -> None:
        """Close every WebSocket from the server side."""
        for ws in self._open_clients():
            await ws.close(code=1011)

    async def wait_for(
        self, predicate: Callable[[], bool], within: float = 5.0, interval: float = 0.01
    ) -> None:
        """Poll until ``predicate`` holds; raise ``TimeoutError`` after ``within`` seconds."""
        async with asyncio.timeout(within):
            while not predicate():  # noqa: ASYNC110  # state is set by server tasks
                await asyncio.sleep(interval)  # state changes come from server tasks

    async def wait_for_frame(self, path: str, within: float = 5.0) -> dict[str, Any]:
        await self.wait_for(lambda: any(f.get("path") == path for f in self.ws_frames), within)
        return next(f for f in reversed(self.ws_frames) if f.get("path") == path)

    def _open_clients(self) -> list[web.WebSocketResponse]:
        return [ws for ws in self._ws_clients if not ws.closed]

    async def _send_all(self, frame: dict[str, Any]) -> None:
        for ws in self._open_clients():
            await ws.send_json(frame)

    async def _handle_ws(self, request: web.Request) -> web.StreamResponse:
        self.ws_connect_attempts += 1
        if self.ws_refuse:
            return web.Response(status=503, text="down for maintenance")
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        self._ws_clients.append(ws)
        async for msg in ws:
            if msg.type is not WSMsgType.TEXT:
                continue
            try:
                frame = json.loads(msg.data)
            except json.JSONDecodeError:
                self.ws_malformed.append(msg.data)
                continue
            if not isinstance(frame, dict):
                self.ws_malformed.append(msg.data)
                continue
            self.ws_frames.append(frame)
            if frame.get("path") == "/authenticate" and self.ws_auth_replies:
                await ws.send_json(self._authenticate_reply(frame))
        return ws

    def _authenticate_reply(self, frame: dict[str, Any]) -> dict[str, Any]:
        token = (frame.get("headers") or {}).get("jwt")
        frame_id = frame.get("id")
        if token in self._valid_tokens:
            return {"path": "/authenticate", "headers": {"responseFor": frame_id}, "body": None}
        return {
            "path": "/authenticate",
            "headers": {"ErrorResponseFor": frame_id},
            "body": "Invalid token",
        }

    # ------------------------------------------------------------------- HTTP

    async def _handle_http(self, request: web.Request) -> web.StreamResponse:
        recorded = RecordedRequest(
            method=request.method,
            path=request.path,
            query=dict(request.query),
            headers=dict(request.headers),
            body=await self._read_body(request),
        )
        self.requests.append(recorded)

        if self._is_forbidden(recorded):
            self.forbidden.append(recorded)
            return web.json_response({"message": "refused by the fake backend"}, status=418)

        override = self._next_override(recorded)
        if override is not None:
            if override.delay:
                await asyncio.sleep(override.delay)
            if (
                override.status != 200
                or override.json_body is not None
                or override.text_body is not None
            ):
                return self._override_response(override)

        if recorded.method == "POST" and recorded.path == "/login-company":
            return self._login(recorded)
        if not self._authorised(recorded):
            return web.json_response(
                {"name": "Unauthorized", "message": "Invalid or expired token", "code": -1},
                status=401,
            )
        return self._route(recorded)

    @staticmethod
    async def _read_body(request: web.Request) -> Any:
        raw = await request.text()
        if not raw:
            return None
        if request.content_type == "application/json":
            with contextlib.suppress(json.JSONDecodeError):
                return json.loads(raw)
        return raw

    @staticmethod
    def _is_forbidden(recorded: RecordedRequest) -> bool:
        return any(
            method in {"*", recorded.method} and pattern.match(recorded.path)
            for method, pattern in _FORBIDDEN
        )

    def _next_override(self, recorded: RecordedRequest) -> _Override | None:
        queue = self._overrides.get((recorded.method, recorded.path))
        if not queue:
            return None
        override = queue[0]
        if not override.sticky:
            queue.popleft()
        return override

    @staticmethod
    def _override_response(override: _Override) -> web.StreamResponse:
        if override.json_body is not None:
            return web.json_response(
                override.json_body, status=override.status, headers=override.headers
            )
        return web.Response(
            text=override.text_body or "", status=override.status, headers=override.headers
        )

    def _login(self, recorded: RecordedRequest) -> web.StreamResponse:
        body = recorded.body if isinstance(recorded.body, dict) else {}
        required = {"emailOrPhone", "password", "language", "companyId"}
        if (
            required <= body.keys()
            and body["emailOrPhone"] == VALID_EMAIL
            and body["password"] == VALID_PASSWORD
            and body["companyId"] == COMPANY_ID
        ):
            self._token_counter += 1
            token = f"SYNTHETIC-JWT-{self._token_counter}"
            self._valid_tokens.add(token)
            return web.json_response({**load_fixture("login_company"), "jwt": token})
        return web.json_response(
            {"name": "BadCredentials", "message": "Wrong login or password", "code": -1},
            status=401,
        )

    def _authorised(self, recorded: RecordedRequest) -> bool:
        header = recorded.headers.get("Authorization", "")
        return header.startswith("Bearer ") and header.removeprefix("Bearer ") in self._valid_tokens

    def _route(self, recorded: RecordedRequest) -> web.StreamResponse:
        method, path = recorded.method, recorded.path
        if method == "GET" and path.startswith("/event/set_seen/"):
            return web.Response(status=200)  # the vendor marks a single event seen with a GET
        if method == "GET":
            return self._route_read(path)
        if method in {"POST", "PATCH", "DELETE"} and any(p.match(path) for p in _WRITES):
            if (device := self._device_in(path)) is not None and device != DEVICE_UUID:
                return self._detached()
            return web.Response(status=200)
        return web.json_response({"message": f"no such route {method} {path}"}, status=404)

    def _route_read(self, path: str) -> web.StreamResponse:
        if path in self._json_replacements:
            return web.json_response(self._json_replacements[path])
        if path in _ACCOUNT_READS:
            return web.json_response(load_fixture(_ACCOUNT_READS[path]))
        if (match := re.match(r"^/device/([^/]+)(/.*)?$", path)) is not None:
            device, suffix = match.group(1), match.group(2) or ""
            if suffix in _DEVICE_READS:
                if device != DEVICE_UUID:
                    return self._detached()
                return web.json_response(load_fixture(_DEVICE_READS[suffix]))
        return web.json_response({"message": f"no such route GET {path}"}, status=404)

    @staticmethod
    def _device_in(path: str) -> str | None:
        match = re.match(r"^/device/(?:default/)?([^/]+)", path)
        return match.group(1) if match else None

    @staticmethod
    def _detached() -> web.StreamResponse:
        return web.json_response(load_fixture("error_device_detached"), status=400)
