"""The config flow: sign in, errors, duplicates and reauthentication."""

from typing import Any

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.aquastilla.const import CONF_EMAIL, CONF_PASSWORD, DOMAIN

from .common import load_fixture
from .fake_backend import VALID_EMAIL, VALID_PASSWORD, FakeBackend
from .setup_helpers import ACCOUNT_ID, make_entry, run_setup, setup_entry

CREDENTIALS = {CONF_EMAIL: VALID_EMAIL, CONF_PASSWORD: VALID_PASSWORD}
WRONG_PASSWORD = "wrong-password"  # noqa: S105  # pragma: allowlist secret
OLD_PASSWORD = "old-password"  # noqa: S105  # pragma: allowlist secret


def state_of(entry: Any) -> ConfigEntryState:
    """The entry's state, read afresh so the type checker keeps no earlier narrowing."""
    state: ConfigEntryState = entry.state
    return state


async def start(hass: HomeAssistant) -> Any:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    return result


async def submit(hass: HomeAssistant, result: Any, data: dict[str, str]) -> Any:
    return await hass.config_entries.flow.async_configure(result["flow_id"], data)


# --------------------------------------------------------------- new account


async def test_signing_in_creates_the_entry(hass: HomeAssistant, backend: FakeBackend) -> None:
    result = await submit(hass, await start(hass), CREDENTIALS)
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == VALID_EMAIL
    assert result["data"] == CREDENTIALS
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    assert entry.unique_id == ACCOUNT_ID
    assert entry.state is ConfigEntryState.LOADED


async def test_the_form_asks_for_a_real_login_not_just_a_format_check(
    hass: HomeAssistant, backend: FakeBackend
) -> None:
    await submit(hass, await start(hass), CREDENTIALS)
    assert len(backend.requests_to("POST", "/login-company")) >= 1
    assert backend.requests_to("GET", "/details")
    assert backend.requests_to("GET", "/device/all")


async def test_the_email_is_stored_trimmed(hass: HomeAssistant, backend: FakeBackend) -> None:
    result = await submit(hass, await start(hass), {**CREDENTIALS, CONF_EMAIL: f"  {VALID_EMAIL} "})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_EMAIL] == VALID_EMAIL


async def test_the_account_identity_falls_back_to_the_email(
    hass: HomeAssistant, backend: FakeBackend
) -> None:
    details = {**load_fixture("details")}
    del details["userId"]
    backend.replace_json("/details", details)
    result = await submit(hass, await start(hass), CREDENTIALS)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert hass.config_entries.async_entries(DOMAIN)[0].unique_id == VALID_EMAIL.lower()


# -------------------------------------------------------------------- errors


async def test_wrong_password_is_a_readable_error_and_the_form_stays(
    hass: HomeAssistant, backend: FakeBackend
) -> None:
    result = await submit(hass, await start(hass), {**CREDENTIALS, CONF_PASSWORD: WRONG_PASSWORD})
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}
    assert hass.config_entries.async_entries(DOMAIN) == []


async def test_after_an_error_the_user_can_try_again(
    hass: HomeAssistant, backend: FakeBackend
) -> None:
    result = await submit(hass, await start(hass), {**CREDENTIALS, CONF_PASSWORD: WRONG_PASSWORD})
    result = await submit(hass, result, CREDENTIALS)
    assert result["type"] is FlowResultType.CREATE_ENTRY


@pytest.mark.parametrize("status", [500, 503])
async def test_a_backend_outage_is_cannot_connect_not_bad_credentials(
    hass: HomeAssistant, backend: FakeBackend, status: int
) -> None:
    backend.fail_always("POST", "/login-company", status=status)
    result = await submit(hass, await start(hass), CREDENTIALS)
    assert result["errors"] == {"base": "cannot_connect"}


async def test_a_rate_limit_is_cannot_connect(hass: HomeAssistant, backend: FakeBackend) -> None:
    backend.fail_always("POST", "/login-company", status=429)
    result = await submit(hass, await start(hass), CREDENTIALS)
    assert result["errors"] == {"base": "cannot_connect"}


async def test_an_account_without_devices_gets_its_own_message(
    hass: HomeAssistant, backend: FakeBackend
) -> None:
    backend.replace_json("/device/all", [])
    result = await submit(hass, await start(hass), CREDENTIALS)
    assert result["errors"] == {"base": "no_devices"}


async def test_an_unexpected_failure_is_unknown_not_a_crash(
    hass: HomeAssistant, backend: FakeBackend, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def boom(self: Any) -> None:
        msg = "bug"
        raise RuntimeError(msg)

    monkeypatch.setattr("custom_components.aquastilla.api.AquastillaClient.login", boom)
    result = await submit(hass, await start(hass), CREDENTIALS)
    assert result["errors"] == {"base": "unknown"}


# ---------------------------------------------------------------- duplicates


async def test_the_same_account_cannot_be_added_twice(
    hass: HomeAssistant, backend: FakeBackend
) -> None:
    await setup_entry(hass, make_entry())
    result = await submit(hass, await start(hass), CREDENTIALS)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert len(hass.config_entries.async_entries(DOMAIN)) == 1


# -------------------------------------------------------------------- reauth


async def start_reauth(hass: HomeAssistant, backend: FakeBackend) -> tuple[Any, Any]:
    entry = make_entry(password=OLD_PASSWORD)
    await setup_entry(hass, entry)
    assert entry.state is ConfigEntryState.SETUP_ERROR
    flow = hass.config_entries.flow.async_progress_by_handler(DOMAIN)[0]
    return entry, flow


async def test_expired_credentials_start_a_reauth_flow_that_fixes_the_entry(
    hass: HomeAssistant, backend: FakeBackend
) -> None:
    entry, flow = await start_reauth(hass, backend)
    assert flow["step_id"] == "reauth_confirm"

    result = await hass.config_entries.flow.async_configure(
        flow["flow_id"], {CONF_PASSWORD: VALID_PASSWORD}
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data[CONF_PASSWORD] == VALID_PASSWORD
    assert entry.data[CONF_EMAIL] == VALID_EMAIL
    assert entry.state is ConfigEntryState.LOADED


async def test_reauth_with_the_wrong_password_shows_an_error_and_keeps_the_flow(
    hass: HomeAssistant, backend: FakeBackend
) -> None:
    entry, flow = await start_reauth(hass, backend)
    result = await hass.config_entries.flow.async_configure(
        flow["flow_id"], {CONF_PASSWORD: WRONG_PASSWORD}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}
    assert entry.data[CONF_PASSWORD] == OLD_PASSWORD


async def test_reauth_cannot_switch_to_a_different_account(
    hass: HomeAssistant, backend: FakeBackend
) -> None:
    entry, flow = await start_reauth(hass, backend)
    backend.replace_json("/details", {**load_fixture("details"), "userId": "somebody-else"})
    result = await hass.config_entries.flow.async_configure(
        flow["flow_id"], {CONF_PASSWORD: VALID_PASSWORD}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "wrong_account"
    assert entry.data[CONF_PASSWORD] == OLD_PASSWORD


async def test_reauth_during_an_outage_says_cannot_connect(
    hass: HomeAssistant, backend: FakeBackend
) -> None:
    entry, flow = await start_reauth(hass, backend)
    backend.fail_always("POST", "/login-company", status=503)
    result = await hass.config_entries.flow.async_configure(
        flow["flow_id"], {CONF_PASSWORD: VALID_PASSWORD}
    )
    assert result["errors"] == {"base": "cannot_connect"}
    assert entry.state is ConfigEntryState.SETUP_ERROR


async def test_the_reauth_form_names_the_account_and_asks_only_for_the_password(
    hass: HomeAssistant, backend: FakeBackend
) -> None:
    entry, _ = await start_reauth(hass, backend)
    result = await entry.start_reauth_flow(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["description_placeholders"]["email"] == VALID_EMAIL
    assert list(result["data_schema"].schema) == [CONF_PASSWORD]


# ------------------------------------------------- identity and repair paths


async def test_mixed_case_email_falls_back_to_a_lower_cased_identity_and_dedupes(
    hass: HomeAssistant, backend: FakeBackend
) -> None:
    details = {**load_fixture("details")}
    del details["userId"]
    backend.replace_json("/details", details)
    shouty = {**CREDENTIALS, CONF_EMAIL: VALID_EMAIL.upper()}

    first = await submit(hass, await start(hass), shouty)
    assert first["type"] is FlowResultType.CREATE_ENTRY
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    assert entry.unique_id == VALID_EMAIL.lower()

    again = await submit(hass, await start(hass), CREDENTIALS)
    assert again["type"] is FlowResultType.ABORT
    assert again["reason"] == "already_configured"
    assert len(hass.config_entries.async_entries(DOMAIN)) == 1


async def test_signing_in_again_repairs_an_entry_that_waits_for_reauth(
    hass: HomeAssistant, backend: FakeBackend
) -> None:
    entry, _ = await start_reauth(hass, backend)
    assert entry.data[CONF_PASSWORD] == OLD_PASSWORD

    result = await submit(hass, await start(hass), CREDENTIALS)
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert entry.data[CONF_PASSWORD] == VALID_PASSWORD
    assert entry.state is ConfigEntryState.LOADED


async def test_reauth_moves_an_email_identified_entry_to_the_vendor_id(
    hass: HomeAssistant, backend: FakeBackend
) -> None:
    entry = make_entry(password=OLD_PASSWORD)
    entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(entry, unique_id=VALID_EMAIL.lower())
    await run_setup(hass, entry)
    assert entry.state is ConfigEntryState.SETUP_ERROR
    flow = hass.config_entries.flow.async_progress_by_handler(DOMAIN)[0]

    result = await hass.config_entries.flow.async_configure(
        flow["flow_id"], {CONF_PASSWORD: VALID_PASSWORD}
    )
    await hass.async_block_till_done()

    assert result["reason"] == "reauth_successful"
    assert entry.unique_id == ACCOUNT_ID
    assert state_of(entry) is ConfigEntryState.LOADED


async def test_the_password_never_reaches_the_log_even_on_an_unexpected_error(
    hass: HomeAssistant,
    backend: FakeBackend,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def boom(self: Any) -> None:
        msg = "bug"
        raise RuntimeError(msg)

    monkeypatch.setattr("custom_components.aquastilla.api.AquastillaClient.login", boom)
    caplog.set_level("DEBUG")
    result = await submit(hass, await start(hass), CREDENTIALS)
    assert result["errors"] == {"base": "unknown"}
    assert "bug" in caplog.text  # the traceback was logged, so the check below means something
    assert VALID_PASSWORD not in caplog.text
