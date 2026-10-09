"""Fetches the account's devices and keeps their data current."""

import logging
from dataclasses import dataclass
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import (
    AquastillaClient,
    AquastillaError,
    AuthError,
    DeviceDetachedError,
    RateLimitError,
)
from .const import DEFAULT_SCAN_INTERVAL, DOMAIN
from .models import CurrentStates, Device, DeviceSettings, DeviceState

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class DeviceData:
    """Everything known about one device after a poll."""

    device: Device
    state: DeviceState
    settings: DeviceSettings
    current_states: CurrentStates


@dataclass(frozen=True, slots=True)
class AquastillaData:
    """The result of one poll: every device on the account, by uuid."""

    devices: dict[str, DeviceData]


class AquastillaCoordinator(DataUpdateCoordinator[AquastillaData]):
    """Polls the vendor backend for all devices of one account."""

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: ConfigEntry,
        client: AquastillaClient,
        update_interval: timedelta = DEFAULT_SCAN_INTERVAL,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=config_entry,
            name=DOMAIN,
            update_interval=update_interval,
        )
        self.client = client

    async def _async_update_data(self) -> AquastillaData:
        try:
            return await self._fetch()
        except AuthError as err:
            msg = "the vendor backend rejected the stored credentials"
            raise ConfigEntryAuthFailed(msg) from err
        except RateLimitError as err:
            msg = "the vendor backend is rate limiting requests"
            raise UpdateFailed(msg, retry_after=err.retry_after) from err
        except AquastillaError as err:
            msg = f"could not update from the vendor backend: {err}"
            raise UpdateFailed(msg) from err

    async def _fetch(self) -> AquastillaData:
        devices: dict[str, DeviceData] = {}
        for device in await self.client.get_devices():
            try:
                devices[device.uuid] = DeviceData(
                    device=device,
                    state=await self.client.get_state(device.uuid),
                    settings=await self.client.get_settings(device.uuid),
                    current_states=await self.client.get_current_states(device.uuid),
                )
            except DeviceDetachedError:
                _LOGGER.warning("Device %s is no longer attached to the account", device.name)
        return AquastillaData(devices=devices)
