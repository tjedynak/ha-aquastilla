"""Tracks commands from the request until the device confirms them.

A POST only reaches the vendor's cloud. The cloud relays it to the device, and the
device confirms later with a ``/device/event_seen`` push. So a successful HTTP answer
does not mean the command was applied (ADR-2), and this module never assumes it did.

The life of a command:

1. mark it pending, register for the acknowledgement, then send the request. The
   acknowledgement is registered first because a fast device can answer before the
   HTTP call returns;
2. the matching push arrives: the command is acknowledged, the affected data is read
   again, the pending mark is cleared;
3. no push within ``COMMAND_TIMEOUT``: the command is *unconfirmed*. It is not reported
   as failed, because it may well have worked. The data is read again and entities show
   what the device reports.

A push that arrives after the timeout is not an error: it only triggers another read.

The tracker never changes the data itself. Entities read it from the coordinator, so
they can only ever show what was observed.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from .api import AquastillaError
from .const import COMMAND_TIMEOUT
from .coordinator import AquastillaCoordinator, Dataset
from .models import DeviceEventType, EventSeen

_LOGGER = logging.getLogger(__name__)

# Which data a notification makes stale. Settings changes also touch the cycle (vacation
# mode and the water flow change what the head does), so those read the state as well.
_STATE = (Dataset.STATE,)
_SETTINGS = (Dataset.SETTINGS,)
_BOTH = (Dataset.SETTINGS, Dataset.STATE)
_AFFECTS: dict[str, tuple[Dataset, ...]] = {
    DeviceEventType.SET_DEVICE_MODE: _STATE,
    DeviceEventType.NEW_STATE_ARRIVED: _STATE,
    DeviceEventType.SET_REGENERATION_VACATION_MODE: _STATE,
    DeviceEventType.SET_DELAY_REGENERATION: _STATE,
    DeviceEventType.SET_VACATION_MODE: _BOTH,
    DeviceEventType.SET_WATER_FLOW: _BOTH,
    DeviceEventType.NEW_SETTINGS_ARRIVED: _BOTH,
    DeviceEventType.SET_REGENERATION_DATE_AS_SERVICE_MAN: _BOTH,
    DeviceEventType.SET_WATER_HARDNESS: _SETTINGS,
    DeviceEventType.SET_VOLUME_UNIT: _SETTINGS,
    DeviceEventType.SET_MINIMUM_SALT_LEVEL: _SETTINGS,
    DeviceEventType.SET_FLOOD_SETTINGS: _SETTINGS,
    DeviceEventType.SET_PREFERRED_REGENERATION_TIME: _SETTINGS,
    DeviceEventType.SET_SERVICE_MODE: _SETTINGS,
}
# Notifications that the device raises on its own, not in answer to a command. They are
# subject to the refresh cooldown, so a chatty device cannot cause a burst of requests.
_UNSOLICITED = frozenset({DeviceEventType.NEW_STATE_ARRIVED, DeviceEventType.NEW_SETTINGS_ARRIVED})


class CommandStatus(StrEnum):
    """How a command ended."""

    ACKNOWLEDGED = "acknowledged"
    UNCONFIRMED = "unconfirmed"


@dataclass(frozen=True, slots=True)
class CommandResult:
    """The outcome of one command."""

    uuid: str
    event_type: str
    status: CommandStatus


class CommandPendingError(AquastillaError):
    """The same command is already waiting for its acknowledgement."""


class CommandTracker:
    """Pending-command bookkeeping for one config entry."""

    def __init__(
        self, coordinator: AquastillaCoordinator, *, timeout: float = COMMAND_TIMEOUT
    ) -> None:
        self._coordinator = coordinator
        self._timeout = timeout
        self._waiting: dict[tuple[str, str], asyncio.Future[EventSeen]] = {}
        self._listeners: list[Callable[[], None]] = []

    # --------------------------------------------------------- observation

    def is_pending(self, uuid: str, event_type: str) -> bool:
        return (uuid, str(event_type)) in self._waiting

    def pending(self, uuid: str) -> frozenset[str]:
        """The command types of a device that are waiting for confirmation."""
        return frozenset(kind for device, kind in self._waiting if device == uuid)

    def add_listener(self, listener: Callable[[], None]) -> Callable[[], None]:
        """Call ``listener`` whenever the set of pending commands changes."""
        self._listeners.append(listener)
        return lambda: self._listeners.remove(listener)

    # ------------------------------------------------------------- sending

    async def async_send(
        self,
        uuid: str,
        event_type: DeviceEventType,
        send: Callable[[], Awaitable[Any]],
    ) -> CommandResult:
        """Send a command and wait for the device to confirm it.

        ``send`` performs the HTTP request. If it raises, the command is not pending
        any more and the error propagates. Otherwise the result says whether the
        device confirmed in time; either way the data has been read again by then.
        """
        key = (uuid, str(event_type))
        if key in self._waiting:
            msg = f"{event_type} is already waiting for confirmation"
            raise CommandPendingError(msg)

        future: asyncio.Future[EventSeen] = asyncio.get_running_loop().create_future()
        self._waiting[key] = future
        self._notify()
        try:
            try:
                await send()
            except Exception:
                # The request may have failed on our side after the device had already
                # acted, for example a timeout while waiting for the answer. A push that
                # came meanwhile only sets the future, it does not read the data.
                if future.done() and not future.cancelled():
                    await self._refresh(uuid, str(event_type), force=True)
                raise
            try:
                await asyncio.wait_for(asyncio.shield(future), self._timeout)
                status = CommandStatus.ACKNOWLEDGED
            except TimeoutError:
                _LOGGER.debug(
                    "%s for %s was not confirmed within %ss", event_type, uuid, self._timeout
                )
                status = CommandStatus.UNCONFIRMED
        finally:
            self._waiting.pop(key, None)
            if not future.done():
                future.cancel()
            self._notify()
        await self._refresh(uuid, str(event_type), force=True)
        return CommandResult(uuid=uuid, event_type=str(event_type), status=status)

    # -------------------------------------------------------------- pushes

    def handle_push(self, body: object) -> None:
        """Handle a ``/device/event_seen`` push. Subscribe this to the WebSocket client.

        Runs inline in the receive loop, so it only does bookkeeping and hands the
        reading of data to the event loop.
        """
        event = EventSeen.from_json(body)
        if event.uuid is None or event.event_type is None:
            return
        uuid = self._known_uuid(event.uuid)
        if uuid is None:
            return
        event_type = str(event.event_type)
        waiter = self._waiting.get((uuid, event_type))
        if waiter is not None and not waiter.done():
            waiter.set_result(event)  # the sender reads the data once it wakes up
            return
        # Nobody is waiting: a notification from the device itself, or an answer that
        # came after the command had already timed out.
        force = event_type not in _UNSOLICITED
        self._coordinator.hass.async_create_task(
            self._refresh(uuid, event_type, force=force), f"aquastilla refresh after {event_type}"
        )

    # ------------------------------------------------------------ internals

    def _known_uuid(self, uuid: str) -> str | None:
        """The device uuid as the coordinator knows it. Push uuids may differ in case."""
        wanted = uuid.lower()
        for known in self._coordinator.data.devices if self._coordinator.data else ():
            if known.lower() == wanted:
                return known
        return None

    async def _refresh(self, uuid: str, event_type: str, *, force: bool) -> None:
        datasets = _AFFECTS.get(event_type, _STATE)
        # The coordinator logs and swallows a failed targeted refresh; the next poll
        # catches up.
        if Dataset.STATE in datasets:
            await self._coordinator.async_refresh_state(uuid, force=force)
        if Dataset.SETTINGS in datasets:
            await self._coordinator.async_refresh_settings(uuid, force=force)

    def _notify(self) -> None:
        for listener in list(self._listeners):
            try:
                listener()
            except Exception:
                _LOGGER.exception("A pending-command listener failed")
