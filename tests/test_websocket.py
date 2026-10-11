"""The push channel client against the fake backend, over real sockets."""

import asyncio
import json
from collections.abc import AsyncGenerator, Callable
from typing import Any

import aiohttp
import pytest

from custom_components.aquastilla.api import AquastillaClient
from custom_components.aquastilla.const import TOPIC_EVENT, TOPIC_EVENT_SEEN, TOPIC_PERCENTAGE
from custom_components.aquastilla.websocket import AquastillaWebSocket

from .common import load_fixture
from .fake_backend import VALID_EMAIL, VALID_PASSWORD, FakeBackend


class Recorder:
    """Collects what a subscriber, a state listener and the sleep stub were called with."""

    def __init__(self, backend: FakeBackend) -> None:
        self.backend = backend
        self.delays: list[float] = []
        self.attempts_at_sleep: list[int] = []
        self.states: list[bool] = []
        self.auth_failed = 0

    async def sleep(self, delay: float) -> None:
        self.delays.append(delay)
        self.attempts_at_sleep.append(self.backend.ws_connect_attempts)
        await asyncio.sleep(0.01)  # real, but short: the delay is recorded, not waited out


@pytest.fixture
def recorder(fake_backend: FakeBackend) -> Recorder:
    return Recorder(fake_backend)


@pytest.fixture
async def client(session: aiohttp.ClientSession, fake_backend: FakeBackend) -> AquastillaClient:
    api = AquastillaClient(session, VALID_EMAIL, VALID_PASSWORD, base_url=fake_backend.base_url)
    await api.login()
    return api


@pytest.fixture
async def make_socket(
    session: aiohttp.ClientSession,
    fake_backend: FakeBackend,
    client: AquastillaClient,
    recorder: Recorder,
) -> AsyncGenerator[Callable[..., AquastillaWebSocket]]:
    created: list[AquastillaWebSocket] = []

    def factory(**kwargs: Any) -> AquastillaWebSocket:
        options: dict[str, Any] = {
            "url": fake_backend.ws_url,
            "sleep": recorder.sleep,
            "initial_delay": 1.0,
            "max_delay": 8.0,
            "ack_timeout": 2.0,
        }
        options.update(kwargs)
        socket = AquastillaWebSocket(session, client, **options)
        socket.add_state_listener(recorder.states.append)
        socket.add_auth_failed_listener(
            lambda: setattr(recorder, "auth_failed", recorder.auth_failed + 1)
        )
        created.append(socket)
        return socket

    yield factory
    for socket in created:
        await socket.stop()


async def connected(socket: AquastillaWebSocket, backend: FakeBackend) -> None:
    await backend.wait_for(lambda: socket.connected)


# ------------------------------------------------------------- handshake


async def test_connects_and_authenticates_with_the_token(
    fake_backend: FakeBackend,
    client: AquastillaClient,
    make_socket: Callable[..., AquastillaWebSocket],
    recorder: Recorder,
) -> None:
    socket = make_socket()
    assert not socket.connected
    socket.start()
    await connected(socket, fake_backend)
    auth = fake_backend.ws_frames[0]
    assert auth["path"] == "/authenticate"
    assert auth["headers"] == {"jwt": client.token}
    assert auth["body"] is None
    assert recorder.states == [True]


async def test_start_twice_opens_one_connection(
    fake_backend: FakeBackend, make_socket: Callable[..., AquastillaWebSocket]
) -> None:
    socket = make_socket()
    socket.start()
    socket.start()
    await connected(socket, fake_backend)
    assert fake_backend.ws_connect_attempts == 1


async def test_start_does_not_block_or_raise_when_the_backend_is_down(
    fake_backend: FakeBackend, make_socket: Callable[..., AquastillaWebSocket]
) -> None:
    fake_backend.ws_refuse = True
    socket = make_socket()
    socket.start()  # returns at once
    await fake_backend.wait_for(lambda: fake_backend.ws_connect_attempts >= 1)
    assert not socket.connected


async def test_stop_closes_the_connection(
    fake_backend: FakeBackend, make_socket: Callable[..., AquastillaWebSocket], recorder: Recorder
) -> None:
    socket = make_socket()
    socket.start()
    await connected(socket, fake_backend)
    await socket.stop()
    assert not socket.connected
    await fake_backend.wait_for(lambda: not fake_backend.ws_connected)
    assert recorder.states == [True, False]
    attempts = fake_backend.ws_connect_attempts
    await asyncio.sleep(0.1)
    assert fake_backend.ws_connect_attempts == attempts  # not reconnecting after stop


# ---------------------------------------------------------------- frames


@pytest.mark.parametrize(
    ("topic", "fixture"),
    [
        (TOPIC_EVENT, "ws_device_event"),
        (TOPIC_EVENT_SEEN, "ws_event_seen"),
        (TOPIC_PERCENTAGE, "ws_percentage"),
    ],
)
async def test_pushes_reach_subscribers_decoded(
    fake_backend: FakeBackend,
    make_socket: Callable[..., AquastillaWebSocket],
    topic: str,
    fixture: str,
) -> None:
    socket = make_socket()
    received: list[Any] = []
    socket.subscribe(topic, received.append)
    socket.start()
    await connected(socket, fake_backend)
    await fake_backend.push(topic, load_fixture(fixture))
    await fake_backend.wait_for(lambda: bool(received))
    assert received == [load_fixture(fixture)]  # the JSON string was decoded


async def test_a_push_with_an_object_body_works_too(
    fake_backend: FakeBackend, make_socket: Callable[..., AquastillaWebSocket]
) -> None:
    socket = make_socket()
    received: list[Any] = []
    socket.subscribe(TOPIC_PERCENTAGE, received.append)
    socket.start()
    await connected(socket, fake_backend)
    await fake_backend.push(TOPIC_PERCENTAGE, {"uuid": "x", "percentage": 5}, encode=False)
    await fake_backend.wait_for(lambda: bool(received))
    assert received == [{"uuid": "x", "percentage": 5}]


async def test_a_topic_without_subscribers_is_ignored(
    fake_backend: FakeBackend, make_socket: Callable[..., AquastillaWebSocket]
) -> None:
    socket = make_socket()
    received: list[Any] = []
    socket.subscribe(TOPIC_EVENT, received.append)
    socket.start()
    await connected(socket, fake_backend)
    await fake_backend.push("/something/new", {"a": 1})
    await fake_backend.push(TOPIC_EVENT, {"b": 2})
    await fake_backend.wait_for(lambda: bool(received))
    assert received == [{"b": 2}]
    assert socket.connected


async def test_unsubscribe_stops_delivery(
    fake_backend: FakeBackend, make_socket: Callable[..., AquastillaWebSocket]
) -> None:
    socket = make_socket()
    first: list[Any] = []
    second: list[Any] = []
    unsubscribe = socket.subscribe(TOPIC_EVENT, first.append)
    socket.subscribe(TOPIC_EVENT, second.append)
    socket.start()
    await connected(socket, fake_backend)
    unsubscribe()
    unsubscribe()  # harmless twice
    await fake_backend.push(TOPIC_EVENT, {"n": 1})
    await fake_backend.wait_for(lambda: bool(second))
    assert first == []


async def test_a_failing_handler_does_not_kill_the_connection(
    fake_backend: FakeBackend, make_socket: Callable[..., AquastillaWebSocket]
) -> None:
    socket = make_socket()
    received: list[Any] = []

    def broken(_: Any) -> None:
        msg = "handler bug"
        raise RuntimeError(msg)

    socket.subscribe(TOPIC_EVENT, broken)
    socket.subscribe(TOPIC_EVENT, received.append)
    socket.start()
    await connected(socket, fake_backend)
    await fake_backend.push(TOPIC_EVENT, {"n": 1})
    await fake_backend.push(TOPIC_EVENT, {"n": 2})
    await fake_backend.wait_for(lambda: len(received) == 2)
    assert socket.connected


async def test_ping_is_answered_with_pong(
    fake_backend: FakeBackend,
    client: AquastillaClient,
    make_socket: Callable[..., AquastillaWebSocket],
) -> None:
    socket = make_socket()
    socket.start()
    await connected(socket, fake_backend)
    await fake_backend.send_ping()
    pong = await fake_backend.wait_for_frame("/pong")
    assert pong["headers"] == {"jwt": client.token}
    assert pong["body"] == ""
    ids = [f["id"] for f in fake_backend.ws_frames]
    assert len(set(ids)) == len(ids)  # every frame has its own id


async def test_malformed_frames_are_counted_and_ignored(
    fake_backend: FakeBackend, make_socket: Callable[..., AquastillaWebSocket]
) -> None:
    socket = make_socket()
    received: list[Any] = []
    socket.subscribe(TOPIC_EVENT, received.append)
    socket.start()
    await connected(socket, fake_backend)
    await fake_backend.send_raw("{not json")
    await fake_backend.send_raw("[1, 2, 3]")
    await fake_backend.send_raw('"just a string"')
    await fake_backend.send_raw(json.dumps({"path": 5, "headers": [], "body": "x"}))
    await fake_backend.push(TOPIC_EVENT, {"after": "garbage"})
    await fake_backend.wait_for(lambda: bool(received))
    assert received == [{"after": "garbage"}]
    assert socket.malformed_frames == 3
    assert socket.connected


# ------------------------------------------------------------ reconnection


async def test_a_dropped_connection_is_reopened(
    fake_backend: FakeBackend, make_socket: Callable[..., AquastillaWebSocket], recorder: Recorder
) -> None:
    socket = make_socket()
    socket.start()
    await connected(socket, fake_backend)
    await fake_backend.drop_connections()
    await fake_backend.wait_for(lambda: recorder.states == [True, False, True])
    assert fake_backend.ws_connect_attempts == 2
    assert recorder.delays[0] == 1.0


async def test_reconnect_delay_grows_and_is_capped_never_a_busy_loop(
    fake_backend: FakeBackend, make_socket: Callable[..., AquastillaWebSocket], recorder: Recorder
) -> None:
    fake_backend.ws_refuse = True
    socket = make_socket()
    socket.start()
    await fake_backend.wait_for(lambda: len(recorder.delays) >= 7)
    assert recorder.delays[:7] == [1.0, 2.0, 4.0, 8.0, 8.0, 8.0, 8.0]
    assert not socket.connected
    # Each attempt is followed by exactly one delay before the next attempt: when the
    # n-th delay starts, exactly n attempts have been made. A hot loop would show more.
    assert recorder.attempts_at_sleep[:7] == [1, 2, 3, 4, 5, 6, 7]


async def test_recovery_after_an_outage(
    fake_backend: FakeBackend, make_socket: Callable[..., AquastillaWebSocket], recorder: Recorder
) -> None:
    fake_backend.ws_refuse = True
    socket = make_socket()
    socket.start()
    await fake_backend.wait_for(lambda: len(recorder.delays) >= 3)
    fake_backend.ws_refuse = False
    await connected(socket, fake_backend)
    assert recorder.states == [True]


async def test_a_long_lived_connection_resets_the_delay(
    fake_backend: FakeBackend, make_socket: Callable[..., AquastillaWebSocket], recorder: Recorder
) -> None:
    now = [0.0]
    socket = make_socket(clock=lambda: now[0], stable_after=60.0)
    fake_backend.ws_refuse = True
    socket.start()
    await fake_backend.wait_for(lambda: len(recorder.delays) >= 3)
    assert recorder.delays[:3] == [1.0, 2.0, 4.0]
    fake_backend.ws_refuse = False
    await connected(socket, fake_backend)
    now[0] += 120  # the connection stays up for two minutes
    await fake_backend.drop_connections()
    await fake_backend.wait_for(lambda: len(recorder.delays) >= 4)
    assert recorder.delays[3] == 1.0  # started over, not 8


async def test_a_flapping_connection_keeps_backing_off(
    fake_backend: FakeBackend, make_socket: Callable[..., AquastillaWebSocket], recorder: Recorder
) -> None:
    now = [0.0]
    socket = make_socket(clock=lambda: now[0], stable_after=60.0)
    socket.start()
    for round_number in (1, 2, 3):
        await fake_backend.wait_for(lambda: recorder.states.count(True) == round_number)  # noqa: B023
        now[0] += 1  # up for one second only
        await fake_backend.drop_connections()
        await fake_backend.wait_for(lambda: len(recorder.delays) >= round_number)  # noqa: B023
    assert recorder.delays[:3] == [1.0, 2.0, 4.0]


async def test_a_silent_server_is_given_up_on_and_retried(
    fake_backend: FakeBackend, make_socket: Callable[..., AquastillaWebSocket], recorder: Recorder
) -> None:
    fake_backend.ws_auth_replies = False
    socket = make_socket(ack_timeout=0.2)
    socket.start()
    await fake_backend.wait_for(lambda: fake_backend.ws_connect_attempts >= 2)
    assert not socket.connected
    assert recorder.delays[0] == 1.0


# ------------------------------------------------------------ auth failure


async def test_an_expired_token_is_renewed_and_the_connection_succeeds(
    fake_backend: FakeBackend,
    client: AquastillaClient,
    make_socket: Callable[..., AquastillaWebSocket],
) -> None:
    old = client.token
    fake_backend.expire_tokens()
    socket = make_socket()
    socket.start()
    await connected(socket, fake_backend)
    assert client.token != old
    assert len(fake_backend.requests_to("POST", "/login-company")) == 2
    assert fake_backend.ws_frames[-1]["headers"] == {"jwt": client.token}


async def test_rejected_credentials_stop_the_channel_and_say_so(
    fake_backend: FakeBackend, make_socket: Callable[..., AquastillaWebSocket], recorder: Recorder
) -> None:
    fake_backend.expire_tokens()
    fake_backend.fail_always("POST", "/login-company", status=401)
    socket = make_socket()
    socket.start()
    await fake_backend.wait_for(lambda: recorder.auth_failed == 1)
    attempts = fake_backend.ws_connect_attempts
    await asyncio.sleep(0.1)
    assert fake_backend.ws_connect_attempts == attempts  # it gave up instead of looping
    assert recorder.auth_failed == 1
    assert not socket.connected


async def test_polling_keeps_working_while_the_channel_is_down(
    fake_backend: FakeBackend,
    client: AquastillaClient,
    make_socket: Callable[..., AquastillaWebSocket],
) -> None:
    """Losing the push channel degrades to polling; it never takes the REST client down."""
    fake_backend.ws_refuse = True
    socket = make_socket()
    socket.start()
    await fake_backend.wait_for(lambda: fake_backend.ws_connect_attempts >= 2)
    assert not socket.connected
    assert (await client.get_devices())[0].uuid == load_fixture("device")["uuid"]


async def test_a_login_outage_during_renewal_is_retried_not_fatal(
    fake_backend: FakeBackend, make_socket: Callable[..., AquastillaWebSocket], recorder: Recorder
) -> None:
    fake_backend.expire_tokens()
    fake_backend.fail_next("POST", "/login-company", status=503)
    socket = make_socket()
    socket.start()
    await connected(socket, fake_backend)
    assert recorder.auth_failed == 0
    assert recorder.delays  # it waited before the second attempt


async def test_it_logs_in_first_when_the_client_has_no_token(
    session: aiohttp.ClientSession, fake_backend: FakeBackend, recorder: Recorder
) -> None:
    fresh = AquastillaClient(session, VALID_EMAIL, VALID_PASSWORD, base_url=fake_backend.base_url)
    socket = AquastillaWebSocket(session, fresh, url=fake_backend.ws_url, sleep=recorder.sleep)
    socket.start()
    await connected(socket, fake_backend)
    assert fresh.token is not None
    await socket.stop()


async def test_broken_listeners_do_not_break_the_channel(
    fake_backend: FakeBackend, make_socket: Callable[..., AquastillaWebSocket], recorder: Recorder
) -> None:
    def broken(*_: Any) -> None:
        msg = "listener bug"
        raise RuntimeError(msg)

    socket = make_socket()
    socket.add_state_listener(broken)
    socket.start()
    await connected(socket, fake_backend)
    assert recorder.states == [True]  # the well-behaved listener still heard it

    fake_backend.expire_tokens()
    fake_backend.fail_always("POST", "/login-company", status=401)
    failing = make_socket()
    failing.add_auth_failed_listener(broken)
    failing.start()
    await fake_backend.wait_for(lambda: recorder.auth_failed >= 1)
