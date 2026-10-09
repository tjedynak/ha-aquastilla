"""The HTTP client against the fake backend, over real sockets."""

import asyncio
import logging

import aiohttp
import pytest

from custom_components.aquastilla import models
from custom_components.aquastilla.api import (
    ApiError,
    AquastillaClient,
    AuthError,
    CannotConnectError,
    DeviceDetachedError,
    RateLimitError,
)

from .common import load_fixture
from .fake_backend import DEVICE_UUID, VALID_EMAIL, VALID_PASSWORD, FakeBackend


def make_client(
    session: aiohttp.ClientSession,
    backend: FakeBackend,
    *,
    password: str = VALID_PASSWORD,
    timeout: float = 5.0,
) -> AquastillaClient:
    return AquastillaClient(
        session, VALID_EMAIL, password, base_url=backend.base_url, timeout=timeout
    )


def logins(backend: FakeBackend) -> int:
    return len(backend.requests_to("POST", "/login-company"))


# ----------------------------------------------------------- happy path


async def test_first_request_logs_in_then_sends_the_bearer_token(
    session: aiohttp.ClientSession, fake_backend: FakeBackend
) -> None:
    client = make_client(session, fake_backend)
    assert repr(client).endswith("authenticated=False)")
    await client.get_user_details()
    assert client.token == "SYNTHETIC-JWT-1"  # noqa: S105
    assert logins(fake_backend) == 1
    details = fake_backend.requests_to("GET", "/details")[0]
    assert details.headers["Authorization"] == "Bearer SYNTHETIC-JWT-1"


async def test_login_sends_the_documented_body(
    session: aiohttp.ClientSession, fake_backend: FakeBackend
) -> None:
    await make_client(session, fake_backend).login()
    body = fake_backend.requests_to("POST", "/login-company")[0].body
    assert body == {
        "emailOrPhone": VALID_EMAIL,
        "password": VALID_PASSWORD,
        "language": "en",
        "companyId": "67adc541461d62305c8168c8",  # pragma: allowlist secret
    }
    assert "Authorization" not in fake_backend.requests_to("POST", "/login-company")[0].headers


async def test_token_is_reused_between_requests(
    session: aiohttp.ClientSession, fake_backend: FakeBackend
) -> None:
    client = make_client(session, fake_backend)
    await client.get_devices()
    await client.get_user_details()
    await client.get_state(DEVICE_UUID)
    assert logins(fake_backend) == 1


async def test_reads_return_parsed_models(
    session: aiohttp.ClientSession, fake_backend: FakeBackend
) -> None:
    client = make_client(session, fake_backend)
    assert await client.get_user_details() == models.UserDetails.from_json(load_fixture("details"))
    assert await client.get_devices() == models.parse_device_list(load_fixture("device_all"))
    assert await client.get_device(DEVICE_UUID) == models.Device.from_json(load_fixture("device"))
    assert await client.get_state(DEVICE_UUID) == models.DeviceState.from_json(
        load_fixture("state")
    )
    assert await client.get_settings(DEVICE_UUID) == models.DeviceSettings.from_json(
        load_fixture("settings")
    )
    assert await client.get_current_states(DEVICE_UUID) == models.CurrentStates.from_json(
        load_fixture("current_states")
    )
    assert await client.get_modes(DEVICE_UUID) == models.parse_mode_list(load_fixture("mode_all"))
    assert await client.get_water_data(DEVICE_UUID) == models.parse_water_data(
        load_fixture("water_data")
    )
    assert await client.get_year_water_data(DEVICE_UUID) == models.parse_year_water_data(
        load_fixture("year_water_data")
    )
    assert await client.get_usage_comparison(DEVICE_UUID) == models.parse_usage_comparison(
        load_fixture("previous_current_water_usage")
    )


async def test_events_are_requested_with_page_and_size(
    session: aiohttp.ClientSession, fake_backend: FakeBackend
) -> None:
    page = await make_client(session, fake_backend).get_events(2, 70)
    assert page.unseen == 1
    assert fake_backend.requests_to("GET", "/event/pageable")[0].query == {
        "page": "2",
        "size": "70",
    }


# ------------------------------------------------------ bad credentials


@pytest.mark.parametrize("status", [400, 401, 403])
async def test_rejected_login_is_an_auth_error(
    session: aiohttp.ClientSession, fake_backend: FakeBackend, status: int
) -> None:
    fake_backend.fail_next("POST", "/login-company", status=status)
    with pytest.raises(AuthError):
        await make_client(session, fake_backend).login()


async def test_wrong_password_is_an_auth_error_and_nothing_else_is_requested(
    session: aiohttp.ClientSession, fake_backend: FakeBackend
) -> None:
    client = make_client(session, fake_backend, password="wrong-password")  # noqa: S106  # pragma: allowlist secret
    with pytest.raises(AuthError):
        await client.get_devices()
    assert repr(client).endswith("authenticated=False)")
    assert fake_backend.requests_to("GET", "/device/all") == []


async def test_after_an_auth_error_the_client_stops_asking(
    session: aiohttp.ClientSession, fake_backend: FakeBackend
) -> None:
    client = make_client(session, fake_backend, password="wrong-password")  # noqa: S106  # pragma: allowlist secret
    with pytest.raises(AuthError):
        await client.get_devices()
    sent = len(fake_backend.requests)
    for _ in range(3):
        with pytest.raises(AuthError):
            await client.get_devices()
    assert len(fake_backend.requests) == sent


async def test_new_credentials_allow_requests_again(
    session: aiohttp.ClientSession, fake_backend: FakeBackend
) -> None:
    client = make_client(session, fake_backend, password="wrong-password")  # noqa: S106  # pragma: allowlist secret
    with pytest.raises(AuthError):
        await client.get_devices()
    client.update_credentials(VALID_EMAIL, VALID_PASSWORD)
    assert (await client.get_devices())[0].uuid == DEVICE_UUID


async def test_login_outage_is_not_an_auth_error(
    session: aiohttp.ClientSession, fake_backend: FakeBackend
) -> None:
    """A 500 during login must not send the user through reauthentication."""
    fake_backend.fail_next("POST", "/login-company", status=503)
    client = make_client(session, fake_backend)
    with pytest.raises(ApiError) as caught:
        await client.login()
    assert not isinstance(caught.value, AuthError)
    assert caught.value.status == 503
    await client.login()  # the next attempt is allowed and succeeds
    assert client.token is not None


async def test_login_without_a_token_in_the_answer_is_an_api_error(
    session: aiohttp.ClientSession, fake_backend: FakeBackend
) -> None:
    fake_backend.fail_next("POST", "/login-company", status=200, json_body={"nothing": "here"})
    with pytest.raises(ApiError, match="no token"):
        await make_client(session, fake_backend).login()


# --------------------------------------------------------- expired token


async def test_expired_token_triggers_exactly_one_relogin(
    session: aiohttp.ClientSession, fake_backend: FakeBackend
) -> None:
    client = make_client(session, fake_backend)
    await client.get_devices()
    fake_backend.expire_tokens()
    assert (await client.get_devices())[0].uuid == DEVICE_UUID
    assert logins(fake_backend) == 2
    assert client.token == "SYNTHETIC-JWT-2"  # noqa: S105
    assert len(fake_backend.requests_to("GET", "/device/all")) == 3  # ok, rejected, ok


@pytest.mark.parametrize("status", [401, 403])
async def test_both_auth_statuses_mean_expired(
    session: aiohttp.ClientSession, fake_backend: FakeBackend, status: int
) -> None:
    client = make_client(session, fake_backend)
    await client.get_devices()
    fake_backend.fail_next("GET", "/device/all", status=status)
    await client.get_devices()
    assert logins(fake_backend) == 2


async def test_persistent_rejection_ends_in_auth_error_without_a_loop(
    session: aiohttp.ClientSession, fake_backend: FakeBackend
) -> None:
    client = make_client(session, fake_backend)
    fake_backend.fail_always("GET", "/device/all", status=401)
    with pytest.raises(AuthError):
        await client.get_devices()
    assert logins(fake_backend) == 2  # the first login plus one renewal
    assert len(fake_backend.requests_to("GET", "/device/all")) == 2
    sent = len(fake_backend.requests)
    with pytest.raises(AuthError):
        await client.get_devices()
    assert len(fake_backend.requests) == sent


async def test_concurrent_requests_share_one_relogin(
    session: aiohttp.ClientSession, fake_backend: FakeBackend
) -> None:
    client = make_client(session, fake_backend)
    await client.get_devices()
    fake_backend.expire_tokens()
    results = await asyncio.gather(*(client.get_state(DEVICE_UUID) for _ in range(5)))
    assert all(r.uuid == DEVICE_UUID for r in results)
    assert logins(fake_backend) == 2


# ---------------------------------------------------------------- failures


async def test_server_error_is_an_api_error_and_does_not_log_in_again(
    session: aiohttp.ClientSession, fake_backend: FakeBackend
) -> None:
    client = make_client(session, fake_backend)
    await client.get_devices()
    fake_backend.fail_next("GET", "/device/all", status=500, text_body="boom")
    with pytest.raises(ApiError) as caught:
        await client.get_devices()
    assert caught.value.status == 500
    assert not isinstance(caught.value, AuthError)
    assert logins(fake_backend) == 1
    assert (await client.get_devices())[0].uuid == DEVICE_UUID  # recovers on its own


async def test_timeout_is_a_connection_error(
    session: aiohttp.ClientSession, fake_backend: FakeBackend
) -> None:
    client = make_client(session, fake_backend, timeout=0.2)
    await client.login()
    fake_backend.delay("GET", "/device/all", 2.0)
    with pytest.raises(CannotConnectError, match="timed out"):
        await client.get_devices()


async def test_refused_connection_is_a_connection_error(
    session: aiohttp.ClientSession, socket_enabled: None
) -> None:
    client = AquastillaClient(
        session, VALID_EMAIL, VALID_PASSWORD, base_url="http://127.0.0.1:1", timeout=2
    )
    with pytest.raises(CannotConnectError):
        await client.login()


async def test_detached_device_has_its_own_error(
    session: aiohttp.ClientSession, fake_backend: FakeBackend
) -> None:
    client = make_client(session, fake_backend)
    with pytest.raises(DeviceDetachedError) as caught:
        await client.get_state("some-other-device")
    assert caught.value.status == 400
    assert caught.value.vendor_error is not None
    assert caught.value.vendor_error.code == -3


async def test_other_400_is_a_plain_api_error(
    session: aiohttp.ClientSession, fake_backend: FakeBackend
) -> None:
    client = make_client(session, fake_backend)
    await client.login()
    fake_backend.fail_next(
        "GET", "/device/all", status=400, json_body={"code": -9, "message": "odd"}
    )
    with pytest.raises(ApiError) as caught:
        await client.get_devices()
    assert not isinstance(caught.value, DeviceDetachedError)
    assert caught.value.vendor_error is not None
    assert caught.value.vendor_error.code == -9


async def test_400_without_a_json_body_is_still_an_api_error(
    session: aiohttp.ClientSession, fake_backend: FakeBackend
) -> None:
    client = make_client(session, fake_backend)
    await client.login()
    fake_backend.fail_next("GET", "/device/all", status=400, text_body="not json")
    with pytest.raises(ApiError) as caught:
        await client.get_devices()
    assert caught.value.vendor_error is None


@pytest.mark.parametrize(
    ("headers", "expected"),
    [({"Retry-After": "12"}, 12.0), ({"Retry-After": "soon"}, None), ({}, None)],
)
async def test_rate_limit_error_carries_retry_after(
    session: aiohttp.ClientSession,
    fake_backend: FakeBackend,
    headers: dict[str, str],
    expected: float | None,
) -> None:
    client = make_client(session, fake_backend)
    await client.login()
    fake_backend.fail_next("GET", "/device/all", status=429, headers=headers)
    with pytest.raises(RateLimitError) as caught:
        await client.get_devices()
    assert caught.value.status == 429
    assert caught.value.retry_after == expected


async def test_a_body_that_is_not_json_is_an_api_error(
    session: aiohttp.ClientSession, fake_backend: FakeBackend
) -> None:
    client = make_client(session, fake_backend)
    await client.login()
    fake_backend.fail_next("GET", "/device/all", status=200, text_body="<html>maintenance</html>")
    with pytest.raises(ApiError, match="not JSON"):
        await client.get_devices()


# ------------------------------------------------------------ secrets


async def test_the_password_never_reaches_logs_reprs_or_errors(
    session: aiohttp.ClientSession, fake_backend: FakeBackend, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    good = make_client(session, fake_backend)
    bad = make_client(session, fake_backend, password="another-wrong-secret")  # noqa: S106  # pragma: allowlist secret
    texts = []
    await good.get_devices()
    fake_backend.expire_tokens()
    await good.get_devices()
    fake_backend.fail_next("GET", "/device/all", status=500)
    try:
        await good.get_devices()
    except ApiError as err:
        texts.append(f"{err!r} {err}")
    try:
        await bad.login()
    except AuthError as err:
        texts.append(f"{err!r} {err}")
    texts.extend([repr(good), repr(bad), caplog.text])
    blob = "\n".join(texts)
    assert VALID_PASSWORD not in blob
    assert "another-wrong-secret" not in blob  # pragma: allowlist secret
    assert (
        "login-company" in caplog.text
    )  # the log was captured, so the check above means something


async def test_the_token_is_not_logged_either(
    session: aiohttp.ClientSession, fake_backend: FakeBackend, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    client = make_client(session, fake_backend)
    await client.get_devices()
    assert client.token is not None
    assert client.token not in caplog.text
    assert client.token not in repr(client)


async def test_redirects_are_not_followed_so_the_token_stays_home(
    session: aiohttp.ClientSession, fake_backend: FakeBackend
) -> None:
    client = make_client(session, fake_backend)
    await client.login()
    fake_backend.fail_next(
        "GET",
        "/device/all",
        status=302,
        text_body="moved",
        headers={"Location": "http://192.0.2.1/steal"},
    )
    with pytest.raises(ApiError) as caught:
        await client.get_devices()
    assert caught.value.status == 302
    assert len(fake_backend.requests) == 2  # the login and the redirected call, nothing after
