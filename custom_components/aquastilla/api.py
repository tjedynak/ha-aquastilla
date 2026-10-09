"""HTTP client for the vendor backend.

The password lives only in the client instance. It is never logged, never part of
an exception message and never in ``repr``.

Authentication follows what the vendor backend allows: there is no refresh
endpoint, so an expired token (HTTP 401 or 403) means logging in again. That is
done at most once per request. If the retry is rejected as well, or the login is,
the client raises ``AuthError`` and refuses further requests until the credentials
are replaced with ``update_credentials``. That keeps a wrong password from turning
into a login loop against someone else's production server.
"""

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

import aiohttp

from . import models
from .const import BASE_URL, COMPANY_ID, REQUEST_TIMEOUT

_LOGGER = logging.getLogger(__name__)

_AUTH_STATUSES = frozenset({401, 403})


class AquastillaError(Exception):
    """Base class for everything the client raises."""


class CannotConnectError(AquastillaError):
    """The backend could not be reached or did not answer in time."""


class AuthError(AquastillaError):
    """The credentials are wrong, or the backend keeps rejecting the session."""


class ApiError(AquastillaError):
    """The backend answered, but not with a usable success."""

    def __init__(
        self,
        message: str,
        *,
        status: int | None = None,
        vendor_error: models.VendorError | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.vendor_error = vendor_error


class DeviceDetachedError(ApiError):
    """The device is no longer attached to the account (HTTP 400, code -3)."""


class RateLimitError(ApiError):
    """The backend asked us to slow down (HTTP 429)."""

    def __init__(self, message: str, *, retry_after: float | None = None) -> None:
        super().__init__(message, status=429)
        self.retry_after = retry_after


class _Unauthorized(Exception):  # internal signal, never leaves this module
    """A request was rejected with 401 or 403."""


@dataclass(frozen=True, slots=True)
class _Call:
    """One request to the backend."""

    method: str
    path: str
    params: dict[str, str] | None = None
    json: Any = None


class AquastillaClient:
    """Talks to the vendor REST API."""

    def __init__(  # noqa: PLR0913
        self,
        session: aiohttp.ClientSession,
        email: str,
        password: str,
        *,
        base_url: str = BASE_URL,
        language: str = "en",
        timeout: float = REQUEST_TIMEOUT,
    ) -> None:
        self._session = session
        self._email = email
        self._password = password
        self._base_url = base_url.rstrip("/")
        self._language = language
        self._timeout = aiohttp.ClientTimeout(total=timeout)
        self._token: str | None = None
        self._auth_failed = False
        self._login_lock = asyncio.Lock()

    def __repr__(self) -> str:
        authenticated = self._token is not None
        return f"AquastillaClient(base_url={self._base_url!r}, authenticated={authenticated})"

    @property
    def token(self) -> str | None:
        """The current JWT, for the WebSocket handshake. Never log it."""
        return self._token

    def update_credentials(self, email: str, password: str) -> None:
        """Replace the credentials after a reauth flow and allow requests again."""
        self._email = email
        self._password = password
        self._token = None
        self._auth_failed = False

    # ------------------------------------------------------------ account

    async def login(self) -> None:
        """Log in now. Used by the config flow to validate credentials."""
        await self._renew_token(stale=self._token)

    async def get_user_details(self) -> models.UserDetails:
        return models.UserDetails.from_json(await self._get("/details"))

    async def get_devices(self) -> list[models.Device]:
        return models.parse_device_list(await self._get("/device/all"))

    async def get_events(self, page: int = 0, size: int = 20) -> models.EventPage:
        return models.EventPage.from_json(
            await self._get("/event/pageable", params={"page": str(page), "size": str(size)})
        )

    # ------------------------------------------------------------- device

    async def get_device(self, uuid: str) -> models.Device:
        return models.Device.from_json(await self._get(f"/device/{uuid}"))

    async def get_state(self, uuid: str) -> models.DeviceState:
        return models.DeviceState.from_json(await self._get(f"/device/{uuid}/state"))

    async def get_settings(self, uuid: str) -> models.DeviceSettings:
        return models.DeviceSettings.from_json(await self._get(f"/device/{uuid}/settings"))

    async def get_current_states(self, uuid: str) -> models.CurrentStates:
        return models.CurrentStates.from_json(await self._get(f"/device/{uuid}/current_states"))

    async def get_modes(self, uuid: str) -> list[models.Cycle | str]:
        return models.parse_mode_list(await self._get(f"/device/{uuid}/mode/all"))

    async def get_water_data(self, uuid: str) -> dict[str, models.WaterSeries]:
        return models.parse_water_data(await self._get(f"/device/{uuid}/water_data"))

    async def get_year_water_data(self, uuid: str) -> dict[str, dict[str, models.WaterSeries]]:
        return models.parse_year_water_data(await self._get(f"/device/{uuid}/year/water_data"))

    async def get_usage_comparison(self, uuid: str) -> dict[str, models.UsagePair]:
        return models.parse_usage_comparison(
            await self._get(f"/device/{uuid}/previous_current_water_usage")
        )

    # ----------------------------------------------------------- plumbing

    async def _get(self, path: str, *, params: dict[str, str] | None = None) -> Any:
        return await self._request(_Call("GET", path, params=params))

    async def _request(self, call: _Call) -> Any:
        """Send an authenticated request, logging in first and once more if rejected."""
        if self._auth_failed:
            msg = "authentication failed earlier; update the credentials to continue"
            raise AuthError(msg)
        if self._token is None:
            await self._renew_token(stale=None)
        sent_with = self._token
        try:
            return await self._send(call)
        except _Unauthorized:
            _LOGGER.debug("%s %s was rejected, logging in again", call.method, call.path)
        await self._renew_token(stale=sent_with)
        try:
            return await self._send(call)
        except _Unauthorized:
            self._auth_failed = True
            msg = "the backend rejected the session right after a fresh login"
            raise AuthError(msg) from None

    async def _renew_token(self, *, stale: str | None) -> None:
        """Log in, unless another task already replaced the token we saw as stale."""
        async with self._login_lock:
            if self._token is not None and self._token != stale:
                return
            self._token = None
            await self._login()

    async def _login(self) -> None:
        body = {
            "emailOrPhone": self._email,
            "password": self._password,
            "language": self._language,
            "companyId": COMPANY_ID,
        }
        try:
            payload = await self._send(_Call("POST", "/login-company", json=body), auth=False)
        except ApiError as err:
            if err.status in {400, 401, 403}:
                self._auth_failed = True
                msg = "the backend rejected the e-mail or password"
                raise AuthError(msg) from None
            raise
        token = models.LoginResponse.from_json(payload).jwt
        if token is None:
            msg = "the login response carried no token"
            raise ApiError(msg, status=200)
        self._token = token
        self._auth_failed = False
        _LOGGER.debug("logged in")

    async def _send(self, call: _Call, *, auth: bool = True) -> Any:
        headers = {}
        if auth and self._token is not None:
            headers["Authorization"] = f"Bearer {self._token}"
        try:
            async with self._session.request(
                call.method,
                f"{self._base_url}{call.path}",
                params=call.params,
                json=call.json,
                headers=headers,
                timeout=self._timeout,
                allow_redirects=False,  # never forward the token to another host
            ) as response:
                _LOGGER.debug("%s %s -> %s", call.method, call.path, response.status)
                return await self._handle(response, auth=auth)
        except TimeoutError as err:
            msg = f"{call.method} {call.path} timed out"
            raise CannotConnectError(msg) from err
        except aiohttp.ClientError as err:
            msg = f"{call.method} {call.path} failed: {type(err).__name__}"
            raise CannotConnectError(msg) from err

    async def _handle(self, response: aiohttp.ClientResponse, *, auth: bool) -> Any:
        status = response.status
        if status in _AUTH_STATUSES and auth:
            raise _Unauthorized
        if status == 429:
            raise RateLimitError(
                "the backend is rate limiting requests", retry_after=_retry_after(response)
            )
        if status >= 300:  # redirects are not followed, so a 3xx is a failure like any other
            raise await _error_for(response)
        try:
            return await response.json(content_type=None)
        except (aiohttp.ContentTypeError, ValueError) as err:
            msg = "the backend returned a body that is not JSON"
            raise ApiError(msg, status=status) from err


def _retry_after(response: aiohttp.ClientResponse) -> float | None:
    try:
        return float(response.headers["Retry-After"])
    except KeyError, ValueError:
        return None


async def _error_for(response: aiohttp.ClientResponse) -> ApiError:
    status = response.status
    vendor_error = None
    if status == 400:
        try:
            vendor_error = models.VendorError.from_json(await response.json(content_type=None))
        except aiohttp.ContentTypeError, ValueError:
            vendor_error = None
    if vendor_error is not None and vendor_error.is_device_detached:
        return DeviceDetachedError(
            "the device is no longer attached to the account",
            status=status,
            vendor_error=vendor_error,
        )
    return ApiError(f"the backend answered HTTP {status}", status=status, vendor_error=vendor_error)
