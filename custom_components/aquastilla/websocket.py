"""Client for the vendor's push channel.

The server pushes everything to an authenticated socket unasked, so subscribing is a
local routing table, not a message. Losing the connection never raises into Home
Assistant: the client keeps trying with an increasing delay, and the coordinator keeps
polling in the meantime.

What the client does, from the vendor app's behaviour:

* after the socket opens, it sends an ``/authenticate`` frame carrying the JWT and waits
  for a reply with ``headers.responseFor`` (ok) or ``headers.ErrorResponseFor``;
* it answers a ``/ping`` frame with a ``/pong`` frame;
* push bodies arrive as JSON strings and are decoded before they reach subscribers.

Where it differs on purpose: the app reconnects every second forever; this client
doubles the delay up to ``WS_MAX_DELAY`` and starts over after a connection that stayed
up for ``WS_STABLE_AFTER``.

Known gaps, to be settled against real traffic (#54):

* once authenticated the client waits for frames without a timeout, because the ping
  interval of the server is unknown. A half-open connection is therefore noticed only
  when the operating system gives up on it; until then polling keeps the data current.
* subscribers are called inline from the receive loop. They must return quickly and
  hand real work to the event loop (``hass.async_create_task``), or pings go unanswered.
"""

import asyncio
import itertools
import json
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

import aiohttp

from . import models
from .api import AquastillaClient, AquastillaError, AuthError
from .const import (
    TOPIC_EVENT,
    TOPIC_EVENT_SEEN,
    TOPIC_PERCENTAGE,
    WS_ACK_TIMEOUT,
    WS_INITIAL_DELAY,
    WS_MAX_DELAY,
    WS_STABLE_AFTER,
    WS_URL,
)

_LOGGER = logging.getLogger(__name__)

TOPICS = (TOPIC_EVENT, TOPIC_EVENT_SEEN, TOPIC_PERCENTAGE)

Handler = Callable[[Any], None]
StateListener = Callable[[bool], None]


class _AuthRejected(Exception):  # internal signal
    """The server answered ``/authenticate`` with an error."""


class _ProtocolError(Exception):
    """The server did not follow the protocol, for example it stayed silent."""


class AquastillaWebSocket:
    """Keeps one authenticated WebSocket open to the vendor backend."""

    def __init__(  # noqa: PLR0913
        self,
        session: aiohttp.ClientSession,
        client: AquastillaClient,
        *,
        url: str = WS_URL,
        ack_timeout: float = WS_ACK_TIMEOUT,
        initial_delay: float = WS_INITIAL_DELAY,
        max_delay: float = WS_MAX_DELAY,
        stable_after: float = WS_STABLE_AFTER,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._session = session
        self._client = client
        self._url = url
        self._ack_timeout = ack_timeout
        self._initial_delay = initial_delay
        self._max_delay = max_delay
        self._stable_after = stable_after
        self._sleep = sleep
        self._clock = clock
        self._handlers: dict[str, list[Handler]] = {}
        self._state_listeners: list[StateListener] = []
        self._auth_failed_listeners: list[Callable[[], None]] = []
        self._ids = itertools.count(int(time.time() * 1000))
        self._task: asyncio.Task[None] | None = None
        self._connected = False
        self.malformed_frames = 0

    # ------------------------------------------------------------ public API

    @property
    def connected(self) -> bool:
        """True while the socket is open and authenticated."""
        return self._connected

    def subscribe(self, topic: str, handler: Handler) -> Callable[[], None]:
        """Call ``handler`` with the decoded body of every push on ``topic``.

        The handler runs inside the receive loop and must not block.
        """
        self._handlers.setdefault(topic, []).append(handler)

        def unsubscribe() -> None:
            if handler in self._handlers.get(topic, []):
                self._handlers[topic].remove(handler)

        return unsubscribe

    def add_state_listener(self, listener: StateListener) -> Callable[[], None]:
        """Call ``listener(connected)`` whenever the connection state changes."""
        self._state_listeners.append(listener)
        return lambda: self._state_listeners.remove(listener)

    def add_auth_failed_listener(self, listener: Callable[[], None]) -> Callable[[], None]:
        """Call ``listener()`` when the credentials stopped working and the client gave up."""
        self._auth_failed_listeners.append(listener)
        return lambda: self._auth_failed_listeners.remove(listener)

    def start(self) -> None:
        """Start connecting in the background. Returns at once."""
        if self._task is None or self._task.done():
            self._task = asyncio.get_running_loop().create_task(self._run())

    async def stop(self) -> None:
        """Close the socket and stop reconnecting."""
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        self._set_connected(False)

    # ------------------------------------------------------------ the loop

    async def _run(self) -> None:
        failures = 0
        while True:
            started = self._clock()
            try:
                await self._serve()
            except _AuthRejected:
                _LOGGER.debug("The push channel rejected the token, logging in again")
                try:
                    await self._client.login()
                except AuthError:
                    _LOGGER.debug("Credentials rejected, the push channel stops")
                    self._notify_auth_failed()
                    return
                except AquastillaError as err:
                    _LOGGER.debug("Could not renew the token: %s", err)
            except (
                aiohttp.ClientError,
                TimeoutError,
                OSError,
                _ProtocolError,
                AquastillaError,
            ) as err:
                _LOGGER.debug("Push channel lost: %s", type(err).__name__)
            finally:
                self._set_connected(False)
            if self._clock() - started >= self._stable_after:
                failures = 0
            delay = min(self._initial_delay * (1 << min(failures, 30)), self._max_delay)
            failures += 1
            await self._sleep(delay)

    async def _serve(self) -> None:
        if self._client.token is None:
            await self._client.login()
        token = self._client.token
        async with self._session.ws_connect(self._url) as ws:
            auth_id = self._next_id()
            await ws.send_json(
                {"id": auth_id, "path": "/authenticate", "headers": {"jwt": token}, "body": None}
            )
            authenticated = False
            while True:
                timeout = None if authenticated else self._ack_timeout
                try:
                    message = await ws.receive(timeout=timeout)
                except TimeoutError as err:
                    msg = "no reply to /authenticate"
                    raise _ProtocolError(msg) from err
                if message.type is not aiohttp.WSMsgType.TEXT:
                    return  # closed, closing or an error: the caller reconnects
                frame = self._decode(message.data)
                if frame is None:
                    continue
                headers = frame.get("headers")
                if not isinstance(headers, dict):
                    headers = {}
                if headers.get("ErrorResponseFor") == auth_id:
                    raise _AuthRejected
                if headers.get("responseFor") == auth_id:
                    authenticated = True
                    self._set_connected(True)
                    continue
                await self._dispatch(ws, frame, token)

    # ------------------------------------------------------------- frames

    def _decode(self, text: str) -> dict[str, Any] | None:
        try:
            frame = json.loads(text)
        except json.JSONDecodeError:
            frame = None
        if not isinstance(frame, dict):
            self.malformed_frames += 1
            _LOGGER.debug("Ignoring a malformed frame (%d so far)", self.malformed_frames)
            return None
        return frame

    async def _dispatch(
        self, ws: aiohttp.ClientWebSocketResponse, frame: dict[str, Any], token: str | None
    ) -> None:
        path = frame.get("path")
        if path == "/ping":
            await ws.send_json(
                {"id": self._next_id(), "path": "/pong", "headers": {"jwt": token}, "body": ""}
            )
            return
        if not isinstance(path, str):
            return
        body = models.decode_push_body(frame.get("body"))
        for handler in list(self._handlers.get(path, [])):
            try:
                handler(body)
            except Exception:
                _LOGGER.exception("A push handler for %s failed", path)

    def _next_id(self) -> str:
        return str(next(self._ids))

    def _set_connected(self, connected: bool) -> None:
        if connected == self._connected:
            return
        self._connected = connected
        for listener in list(self._state_listeners):
            try:
                listener(connected)
            except Exception:
                _LOGGER.exception("A connection state listener failed")

    def _notify_auth_failed(self) -> None:
        for listener in list(self._auth_failed_listeners):
            try:
                listener()
            except Exception:
                _LOGGER.exception("An auth failure listener failed")
