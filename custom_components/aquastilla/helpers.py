"""Small helpers shared by the setup and the config flow."""

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from . import const
from .api import AquastillaClient


def create_client(hass: HomeAssistant, email: str, password: str) -> AquastillaClient:
    """A client for the vendor backend on Home Assistant's shared HTTP session.

    The base URL is read from ``const`` at call time, so tests can point it at a fake.
    """
    return AquastillaClient(async_get_clientsession(hass), email, password, base_url=const.BASE_URL)
