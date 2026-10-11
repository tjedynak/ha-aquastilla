"""Setting up the integration in tests, through Home Assistant's own machinery."""

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.aquastilla.const import CONF_EMAIL, CONF_PASSWORD, DOMAIN

from .common import load_fixture
from .fake_backend import VALID_EMAIL, VALID_PASSWORD

ACCOUNT_ID: str = load_fixture("details")["userId"]


def make_entry(password: str = VALID_PASSWORD) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        unique_id=ACCOUNT_ID,
        title=VALID_EMAIL,
        data={CONF_EMAIL: VALID_EMAIL, CONF_PASSWORD: password},
    )


async def run_setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """Set an entry up like Home Assistant does. The outcome is in ``entry.state``."""
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def setup_entry(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """Add the entry and set it up."""
    entry.add_to_hass(hass)
    await run_setup(hass, entry)
