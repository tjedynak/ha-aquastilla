"""Setting up the integration in tests."""

from unittest.mock import patch

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.aquastilla import PLATFORMS, async_setup_entry
from custom_components.aquastilla.const import CONF_EMAIL, CONF_PASSWORD, DOMAIN

from .fake_backend import VALID_EMAIL, VALID_PASSWORD


def make_entry(password: str = VALID_PASSWORD) -> MockConfigEntry:
    return MockConfigEntry(domain=DOMAIN, data={CONF_EMAIL: VALID_EMAIL, CONF_PASSWORD: password})


async def setup_entry(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """Run ``async_setup_entry`` the way Home Assistant does, minus the config flow lookup.

    Going through ``hass.config_entries.async_setup`` needs a config_flow platform, which
    arrives with the config flow story. Until then the entry is put in the in-setup state
    and the setup function is called directly. A failure propagates to the caller.
    """
    entry.add_to_hass(hass)
    await run_setup(hass, entry)


async def run_setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    entry.mock_state(hass, ConfigEntryState.SETUP_IN_PROGRESS)
    # Home Assistant only allows forwarding inside its own setup task; there are no platforms yet.
    with (
        patch.object(type(entry), "async_start_reauth_if_available"),
        patch.object(hass.config_entries, "async_forward_entry_setups") as forward,
    ):
        assert await async_setup_entry(hass, entry)
    forward.assert_awaited_once_with(entry, PLATFORMS)
    await hass.async_block_till_done()
