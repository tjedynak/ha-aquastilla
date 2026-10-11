"""Fetches the account's devices and keeps their data current.

The backend is the vendor's production infrastructure, so the coordinator spends
requests sparingly:

* the state and the current cycle are refreshed every poll (the user's interval);
* the settings every ``SETTINGS_INTERVAL``, the device list every
  ``DEVICE_LIST_INTERVAL``, because they rarely change;
* after a failure the poll interval doubles up to ``MAX_BACKOFF`` and returns to
  normal on the first success;
* a push from the device refreshes only the dataset it concerns, at most once per
  ``PUSH_REFRESH_COOLDOWN``.
"""

import asyncio
import logging
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from enum import StrEnum
from typing import TYPE_CHECKING

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import (
    AquastillaClient,
    AquastillaError,
    AuthError,
    DeviceDetachedError,
    RateLimitError,
)
from .const import (
    DEFAULT_SCAN_INTERVAL,
    DEVICE_LIST_INTERVAL,
    DOMAIN,
    MAX_BACKOFF,
    MIN_SCAN_INTERVAL,
    PUSH_REFRESH_COOLDOWN,
    SETTINGS_INTERVAL,
)
from .models import CurrentStates, Device, DeviceSettings, DeviceState

if TYPE_CHECKING:
    from .commands import CommandTracker
    from .websocket import AquastillaWebSocket

_LOGGER = logging.getLogger(__name__)


class Dataset(StrEnum):
    """Groups of data that are refreshed on their own schedule."""

    DEVICES = "devices"
    STATE = "state"  # the state and the current cycle
    SETTINGS = "settings"


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
        self._base_interval = max(update_interval, MIN_SCAN_INTERVAL)
        super().__init__(
            hass,
            _LOGGER,
            config_entry=config_entry,
            name=DOMAIN,
            update_interval=self._base_interval,
        )
        self.client = client
        # Set by the code that starts the push channel; diagnostics report them if present.
        self.command_tracker: CommandTracker | None = None
        self.websocket: AquastillaWebSocket | None = None
        self._fetched: dict[tuple[str, Dataset], datetime] = {}
        self._failures = 0
        self._refresh_devices_next = False
        self._scheduled = False
        # One fetch at a time: a poll and a push refresh must not overwrite each other
        # with data of different ages.
        self._fetch_lock = asyncio.Lock()

    # ------------------------------------------------------------ interval

    @property
    def failures(self) -> int:
        """Consecutive failed polls; zero after a success."""
        return self._failures

    @property
    def base_interval(self) -> timedelta:
        """The interval the user asked for, before any backoff."""
        return self._base_interval

    def set_base_interval(self, interval: timedelta) -> None:
        """Change the poll interval, never below the minimum."""
        self._base_interval = max(interval, MIN_SCAN_INTERVAL)
        self.update_interval = self._backoff_interval()
        # The timer in flight still has the old interval; restart it so the change
        # takes effect now and not after the next poll. Not during a backoff: the timer
        # then holds a delay the vendor asked for, and the new base applies from the
        # next scheduling anyway. Nothing is started when nobody listens.
        if self._listeners and self._failures == 0:
            self._async_unsub_refresh()
            self._schedule_refresh()

    def _backoff_interval(self) -> timedelta:
        if self._failures == 0:
            return self._base_interval
        return min(
            self._base_interval * (1 << self._failures), max(MAX_BACKOFF, self._base_interval)
        )

    # ------------------------------------------------------------- polling

    async def _async_refresh(
        self,
        log_failures: bool = True,
        raise_on_auth_failed: bool = False,
        scheduled: bool = False,
        raise_on_entry_error: bool = False,
    ) -> None:
        # A refresh someone asked for (not the timer) always re-reads the state.
        self._scheduled = scheduled
        await super()._async_refresh(
            log_failures, raise_on_auth_failed, scheduled, raise_on_entry_error
        )

    async def _async_update_data(self) -> AquastillaData:
        try:
            async with self._fetch_lock:
                data = await self._fetch()
        except AuthError as err:
            msg = "the vendor backend rejected the stored credentials"
            raise ConfigEntryAuthFailed(msg) from err
        except RateLimitError as err:
            self._failures += 1
            self.update_interval = self._backoff_interval()
            retry_after = timedelta(seconds=err.retry_after or 0)
            msg = "the vendor backend is rate limiting requests"
            raise UpdateFailed(
                msg, retry_after=max(retry_after, self.update_interval).total_seconds()
            ) from err
        except AquastillaError as err:
            self._failures += 1
            self.update_interval = self._backoff_interval()
            msg = f"could not update from the vendor backend: {err}"
            raise UpdateFailed(msg) from err
        self._failures = 0
        self.update_interval = self._backoff_interval()
        return data

    def _due(self, key: tuple[str, Dataset], interval: timedelta, now: datetime) -> bool:
        fetched = self._fetched.get(key)
        # A poll fires slightly before a whole interval has passed on the clock, so a
        # dataset counts as due a little early.
        return fetched is None or now - fetched >= interval - timedelta(seconds=1)

    async def _fetch(self) -> AquastillaData:
        now = dt_util.utcnow()
        previous = self.data.devices if self.data else {}
        fetched: dict[tuple[str, Dataset], datetime] = {}

        devices = [data.device for data in previous.values()]
        if (
            not previous
            or self._refresh_devices_next
            or self._due(("", Dataset.DEVICES), DEVICE_LIST_INTERVAL, now)
        ):
            devices = await self.client.get_devices()
            fetched[("", Dataset.DEVICES)] = now

        result: dict[str, DeviceData] = {}
        for device in devices:
            old = previous.get(device.uuid)
            try:
                result[device.uuid] = await self._fetch_device(device, old, now, fetched)
            except DeviceDetachedError:
                _LOGGER.warning("Device %s is no longer attached to the account", device.name)
                self._refresh_devices_next = True

        # Only after everything succeeded, so a failed poll cannot make a dataset look fresh.
        if ("", Dataset.DEVICES) in fetched:
            self._refresh_devices_next = False
        self._fetched.update(fetched)
        return AquastillaData(devices=result)

    async def _fetch_device(
        self,
        device: Device,
        old: DeviceData | None,
        now: datetime,
        fetched: dict[tuple[str, Dataset], datetime],
    ) -> DeviceData:
        state_key = (device.uuid, Dataset.STATE)
        settings_key = (device.uuid, Dataset.SETTINGS)
        if old is None or not self._scheduled or self._due(state_key, self._base_interval, now):
            state = await self.client.get_state(device.uuid)
            current = await self.client.get_current_states(device.uuid)
            fetched[state_key] = now
        else:
            state, current = old.state, old.current_states
        if old is None or self._due(settings_key, SETTINGS_INTERVAL, now):
            settings = await self.client.get_settings(device.uuid)
            fetched[settings_key] = now
        else:
            settings = old.settings
        return DeviceData(device=device, state=state, settings=settings, current_states=current)

    # --------------------------------------------------- targeted refreshes

    async def async_refresh_state(self, uuid: str, *, force: bool = False) -> None:
        """Refresh the state and cycle of one device, for example after a push.

        ``force`` skips the cooldown. Use it when something is known to have changed,
        such as an acknowledged command, not for device-originated notifications.
        """
        await self._refresh_one(uuid, Dataset.STATE, force=force)

    async def async_refresh_settings(self, uuid: str, *, force: bool = False) -> None:
        """Refresh the settings of one device. See ``async_refresh_state`` for ``force``."""
        await self._refresh_one(uuid, Dataset.SETTINGS, force=force)

    async def _refresh_one(self, uuid: str, dataset: Dataset, *, force: bool) -> None:
        async with self._fetch_lock:
            await self._refresh_one_locked(uuid, dataset, force=force)

    async def _refresh_one_locked(self, uuid: str, dataset: Dataset, *, force: bool) -> None:
        # Read the data only now: a poll that held the lock may have replaced it.
        old = self.data.devices.get(uuid) if self.data else None
        if old is None:
            return
        now = dt_util.utcnow()
        key = (uuid, dataset)
        if not force and not self._due(key, PUSH_REFRESH_COOLDOWN, now):
            _LOGGER.debug("Skipping %s refresh of %s, it was refreshed moments ago", dataset, uuid)
            return
        try:
            if dataset is Dataset.STATE:
                new = replace(
                    old,
                    state=await self.client.get_state(uuid),
                    current_states=await self.client.get_current_states(uuid),
                )
            else:
                new = replace(old, settings=await self.client.get_settings(uuid))
        except AquastillaError as err:
            # The next regular poll catches up. A failed push refresh must not raise.
            _LOGGER.debug("Targeted %s refresh of %s failed: %s", dataset, uuid, err)
            self._fetched.pop(key, None)
            return
        self._fetched[key] = now
        devices = {**self.data.devices, uuid: new}
        self.async_set_updated_data(AquastillaData(devices=devices))
