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
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, tzinfo
from enum import StrEnum
from typing import TYPE_CHECKING

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import issue_registry as ir
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
    WATER_DATA_INTERVAL,
)
from .models import CurrentStates, Device, DeviceSettings, DeviceState, WaterSeries
from .timeutil import device_instant, device_zone

if TYPE_CHECKING:
    from .commands import CommandTracker
    from .websocket import AquastillaWebSocket

_LOGGER = logging.getLogger(__name__)


def _issue_id(entry_id: str, uuid: str) -> str:
    """One issue per device and per entry, so two accounts never touch each other's."""
    return f"{entry_id}_device_detached_{uuid}"


class Dataset(StrEnum):
    """Groups of data that are refreshed on their own schedule."""

    DEVICES = "devices"
    STATE = "state"  # the state and the current cycle
    SETTINGS = "settings"
    WATER = "water"  # the consumption series


@dataclass(frozen=True, slots=True)
class DeviceData:
    """Everything known about one device after a poll."""

    device: Device
    state: DeviceState
    settings: DeviceSettings
    current_states: CurrentStates
    # When this device's data was last confirmed by a successful read.
    updated_at: datetime | None = None
    # The consumption series by name, in the unit of the volume setting.
    water: dict[str, WaterSeries] = field(default_factory=dict)

    @property
    def timezone(self) -> tzinfo:
        """The device's timezone, or Home Assistant's when the device reports none."""
        return device_zone(self.settings.timezone, dt_util.get_default_time_zone())

    def instant(self, value: datetime | None) -> datetime | None:
        """The real instant of a timestamp the device produced."""
        return device_instant(value, self.timezone)


@dataclass(frozen=True, slots=True)
class AquastillaData:
    """The result of one poll.

    ``devices`` holds the devices that answered. ``listed`` is every device the account
    has, and ``detached`` those of them the service says it no longer has attached
    (HTTP 400, code -3), which are not polled until the next hourly check.
    """

    devices: dict[str, DeviceData]
    listed: frozenset[str] = frozenset()
    detached: frozenset[str] = frozenset()


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
        self._entry_id = config_entry.entry_id
        # Set by the code that starts the push channel; diagnostics report them if present.
        self.command_tracker: CommandTracker | None = None
        self.websocket: AquastillaWebSocket | None = None
        self._fetched: dict[tuple[str, Dataset], datetime] = {}
        self._failures = 0
        self._refresh_devices_next = False
        self._listed: dict[str, Device] | None = None
        self._detached: set[str] = set()
        self._issues: set[str] = set()
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

        scheduled_list = self._listed is None or self._due(
            ("", Dataset.DEVICES), DEVICE_LIST_INTERVAL, now
        )
        if scheduled_list or self._refresh_devices_next:
            devices = await self.client.get_devices()
            self._listed = {device.uuid: device for device in devices}
            fetched[("", Dataset.DEVICES)] = now
            if scheduled_list:
                # Once an hour every device gets another chance, so one that was attached
                # again is picked up.
                self._detached.clear()
            else:
                # Not listed any more means the account really lost it.
                self._detached &= self._listed.keys()
        listed = self._listed
        if listed is None:  # pragma: no cover  # the first poll always lists
            msg = "the device list was not read"
            raise UpdateFailed(msg)

        result: dict[str, DeviceData] = {}
        for device in listed.values():
            if device.uuid in self._detached:
                continue
            try:
                result[device.uuid] = await self._fetch_device(
                    device, previous.get(device.uuid), now, fetched
                )
            except DeviceDetachedError:
                _LOGGER.warning("Device %s is no longer attached to the account", device.name)
                self._detached.add(device.uuid)
                self._refresh_devices_next = True  # find out whether it is gone from the list

        self._sync_repairs(listed)
        # Only after everything succeeded, so a failed poll cannot make a dataset look fresh.
        if ("", Dataset.DEVICES) in fetched:
            self._refresh_devices_next = False
        self._fetched.update(fetched)
        return AquastillaData(
            devices=result,
            listed=frozenset(listed),
            detached=frozenset(self._detached),
        )

    def _sync_repairs(self, listed: dict[str, Device]) -> None:
        """Keep one repair issue per detached device, and drop it when it no longer applies."""
        entry_id = self._entry_id
        for uuid in self._issues - self._detached:
            ir.async_delete_issue(self.hass, DOMAIN, _issue_id(entry_id, uuid))
        for uuid in self._detached - self._issues:
            device = listed.get(uuid)
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                _issue_id(entry_id, uuid),
                is_fixable=False,
                severity=ir.IssueSeverity.WARNING,
                translation_key="device_detached",
                translation_placeholders={
                    "name": (device.name if device else None) or "Aquastilla"
                },
            )
        self._issues = set(self._detached)

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
        water_key = (device.uuid, Dataset.WATER)
        if old is None or self._due(water_key, WATER_DATA_INTERVAL, now):
            water = await self.client.get_water_data(device.uuid)
            fetched[water_key] = now
        else:
            water = old.water
        return DeviceData(
            device=device,
            state=state,
            settings=settings,
            current_states=current,
            updated_at=now,
            water=water,
        )

    @callback
    def async_clear_issues(self) -> None:
        """Remove this entry's repair issues. They describe a running entry, so they go with it."""
        for uuid in self._issues:
            ir.async_delete_issue(self.hass, DOMAIN, _issue_id(self._entry_id, uuid))
        self._issues.clear()

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
                    updated_at=now,
                )
            else:
                new = replace(old, settings=await self.client.get_settings(uuid), updated_at=now)
        except AquastillaError as err:
            # The next regular poll catches up. A failed push refresh must not raise.
            _LOGGER.debug("Targeted %s refresh of %s failed: %s", dataset, uuid, err)
            self._fetched.pop(key, None)
            return
        self._fetched[key] = now
        devices = {**self.data.devices, uuid: new}
        # replace(): the account's listing and the detached set belong to the poll and
        # must survive a targeted refresh, or the registry would be pruned on every push.
        self.async_set_updated_data(replace(self.data, devices=devices))
