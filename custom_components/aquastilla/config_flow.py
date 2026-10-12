"""Config flow: sign in with the app's e-mail and password, and reauthenticate."""

import logging
from collections.abc import Mapping
from typing import Any

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigEntryState,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    BooleanSelector,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .api import AquastillaError, AuthError
from .const import (
    CONF_EMAIL,
    CONF_ENABLE_DEVICE_MANAGEMENT,
    CONF_PASSWORD,
    CONF_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MAX_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
)
from .helpers import create_client
from .validation import vol

_LOGGER = logging.getLogger(__name__)


class NoDevicesError(AquastillaError):
    """The account signs in but has no device on it."""


class AquastillaConfigFlow(ConfigFlow, domain=DOMAIN):
    """Set up one account. All devices on the account are used, none is picked."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> AquastillaOptionsFlow:
        return AquastillaOptionsFlow()

    async def _validate(self, email: str, password: str) -> str:
        """Sign in for real and return the account's identity.

        The identity is the vendor's user id, so the same account cannot be added twice
        under a differently written e-mail address.
        """
        client = create_client(self.hass, email, password)
        await client.login()
        details = await client.get_user_details()
        if not await client.get_devices():
            raise NoDevicesError
        return details.user_id or email.lower()

    def _entry_for(self, identity: str) -> ConfigEntry | None:
        return self.hass.config_entries.async_entry_for_domain_unique_id(DOMAIN, identity)

    async def _repair_existing(
        self, entry: ConfigEntry, email: str, password: str
    ) -> ConfigFlowResult:
        """Signing in again with an account that is set up is not an error to dwell on.

        If the entry holds a stale password, the sign-in just proved a good one, so it
        is stored and the entry set up again. Either way nothing new is created.
        """
        if entry.data.get(CONF_PASSWORD) != password or entry.data.get(CONF_EMAIL) != email:
            self.hass.config_entries.async_update_entry(
                entry, data={**entry.data, CONF_EMAIL: email, CONF_PASSWORD: password}
            )
        if entry.state is not ConfigEntryState.LOADED:
            await self.hass.config_entries.async_reload(entry.entry_id)
        return self.async_abort(reason="already_configured")

    @staticmethod
    def _error_for(err: Exception) -> str:
        if isinstance(err, AuthError):
            return "invalid_auth"
        if isinstance(err, NoDevicesError):
            return "no_devices"
        if isinstance(err, AquastillaError):
            return "cannot_connect"
        _LOGGER.exception("Unexpected error while validating the account", exc_info=err)
        return "unknown"

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            email = user_input[CONF_EMAIL].strip()
            try:
                identity = await self._validate(email, user_input[CONF_PASSWORD])
            except Exception as err:  # mapped to a form error below
                errors["base"] = self._error_for(err)
            else:
                await self.async_set_unique_id(identity)
                if (existing := self._entry_for(identity)) is not None:
                    return await self._repair_existing(existing, email, user_input[CONF_PASSWORD])
                return self.async_create_entry(
                    title=email,
                    data={CONF_EMAIL: email, CONF_PASSWORD: user_input[CONF_PASSWORD]},
                )
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_EMAIL, default=(user_input or {}).get(CONF_EMAIL, "")
                ): TextSelector(
                    TextSelectorConfig(type=TextSelectorType.EMAIL, autocomplete="username")
                ),
                vol.Required(CONF_PASSWORD): TextSelector(
                    TextSelectorConfig(
                        type=TextSelectorType.PASSWORD, autocomplete="current-password"
                    )
                ),
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the password again. The e-mail stays, so this cannot switch accounts."""
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                identity = await self._validate(entry.data[CONF_EMAIL], user_input[CONF_PASSWORD])
            except Exception as err:  # mapped to a form error below
                errors["base"] = self._error_for(err)
            else:
                # An entry made while the backend sent no user id is identified by the
                # lower-cased e-mail. Accept that, and move it to the real id.
                legacy = entry.data[CONF_EMAIL].lower()
                if entry.unique_id not in {identity, legacy}:
                    return self.async_abort(reason="wrong_account")
                self.hass.config_entries.async_update_entry(
                    entry,
                    unique_id=identity,
                    data={**entry.data, CONF_PASSWORD: user_input[CONF_PASSWORD]},
                )
                # Scheduled by hand: the entry has an update listener, and
                # async_update_reload_and_abort is deprecated for such entries.
                self.hass.config_entries.async_schedule_reload(entry.entry_id)
                return self.async_abort(reason="reauth_successful")
        schema = vol.Schema(
            {
                vol.Required(CONF_PASSWORD): TextSelector(
                    TextSelectorConfig(
                        type=TextSelectorType.PASSWORD, autocomplete="current-password"
                    )
                )
            }
        )
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=schema,
            errors=errors,
            description_placeholders={"email": entry.data[CONF_EMAIL]},
        )


class AquastillaOptionsFlow(OptionsFlow):
    """The poll interval. The backend belongs to the vendor, so the default is five minutes
    and a minute is the lowest value offered."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(
                data={
                    CONF_SCAN_INTERVAL: int(user_input[CONF_SCAN_INTERVAL]),
                    CONF_ENABLE_DEVICE_MANAGEMENT: bool(
                        user_input.get(CONF_ENABLE_DEVICE_MANAGEMENT, False)
                    ),
                }
            )
        default = self.config_entry.options.get(
            CONF_SCAN_INTERVAL, int(DEFAULT_SCAN_INTERVAL.total_seconds() // 60)
        )
        schema = vol.Schema(
            {
                vol.Required(CONF_SCAN_INTERVAL, default=default): NumberSelector(
                    NumberSelectorConfig(
                        min=MIN_SCAN_INTERVAL.total_seconds() // 60,
                        max=MAX_SCAN_INTERVAL.total_seconds() // 60,
                        step=1,
                        mode=NumberSelectorMode.BOX,
                        unit_of_measurement="min",
                    )
                ),
                vol.Required(
                    CONF_ENABLE_DEVICE_MANAGEMENT,
                    default=bool(self.config_entry.options.get(CONF_ENABLE_DEVICE_MANAGEMENT)),
                ): BooleanSelector(),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
