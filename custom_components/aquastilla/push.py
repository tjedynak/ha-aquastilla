"""Everything that arrives on the push channel, in one place.

The channel is an accelerator, never the source of truth: polling keeps going, and a
pushed value is only ever a hint to read again, except for the two things that exist
only on the channel, the device notifications and the firmware update progress.

* ``/device/event_seen`` goes to the command tracker, which resolves pending commands
  and refreshes the affected data;
* ``/device/event`` is a notification. It is kept in a short log, newest first, and
  handed to the event entities. It is read only: nothing is ever marked seen or hidden,
  because those flags are shared with the owner's phone (ADR-13);
* ``/device/percentage`` is the progress of a firmware update.

The notification log is account wide and carries no device id, only the device's
name. A notification is attributed by name, or to the only device there is.
"""

import asyncio
import logging
from collections.abc import Callable
from datetime import datetime

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .api import AquastillaError
from .commands import CommandTracker
from .const import (
    EVENT_LOG_SIZE,
    EVENT_REFRESH_INTERVAL,
    PUSH_REFRESH_COOLDOWN,
    TOPIC_EVENT,
    TOPIC_EVENT_SEEN,
    TOPIC_PERCENTAGE,
)
from .coordinator import AquastillaCoordinator
from .models import FirmwareProgress, VendorEvent
from .websocket import AquastillaWebSocket

_LOGGER = logging.getLogger(__name__)

EventListener = Callable[[str, VendorEvent], None]


class PushHub:
    """Routes push frames and keeps what only the channel knows."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        coordinator: AquastillaCoordinator,
        websocket: AquastillaWebSocket,
        tracker: CommandTracker,
    ) -> None:
        self._hass = hass
        self._entry = entry
        self._coordinator = coordinator
        self._websocket = websocket
        self._tracker = tracker
        self._listeners: list[Callable[[], None]] = []
        self._event_listeners: list[EventListener] = []
        self._unsubscribe: list[Callable[[], None]] = []
        self._events_read: datetime | None = None
        self._events_lock = asyncio.Lock()
        self.events: tuple[VendorEvent, ...] = ()
        self.unseen: int | None = None
        self.progress: dict[str, float] = {}
        # How many times the channel came back and the data was read again.
        self.catch_ups = 0

    # ------------------------------------------------------------ lifecycle

    def start(self) -> None:
        """Subscribe to the topics and start connecting. Returns at once."""
        self._unsubscribe = [
            self._websocket.subscribe(TOPIC_EVENT_SEEN, self._tracker.handle_push),
            self._websocket.subscribe(TOPIC_EVENT, self._on_event),
            self._websocket.subscribe(TOPIC_PERCENTAGE, self._on_percentage),
            self._websocket.add_state_listener(self._on_connection),
            self._websocket.add_auth_failed_listener(self._on_auth_failed),
        ]
        self._websocket.start()

    async def async_stop(self) -> None:
        for unsubscribe in self._unsubscribe:
            unsubscribe()
        self._unsubscribe = []
        await self._websocket.stop()

    # ----------------------------------------------------------- listeners

    def add_listener(self, listener: Callable[[], None]) -> Callable[[], None]:
        """Call ``listener`` when the log or a progress value changed."""
        self._listeners.append(listener)
        return lambda: self._listeners.remove(listener)

    def add_event_listener(self, listener: EventListener) -> Callable[[], None]:
        """Call ``listener(uuid, event)`` for every notification that arrives by push."""
        self._event_listeners.append(listener)
        return lambda: self._event_listeners.remove(listener)

    # --------------------------------------------------------- attribution

    def devices_for(self, event: VendorEvent) -> list[str]:
        """The devices a notification belongs to: by name, or the only device there is."""
        devices = self._coordinator.data.devices
        if len(devices) == 1:
            return list(devices)
        return [
            uuid
            for uuid, data in devices.items()
            if event.device_name is not None and data.device.name == event.device_name
        ]

    def events_of(self, uuid: str) -> tuple[VendorEvent, ...]:
        return tuple(event for event in self.events if uuid in self.devices_for(event))

    # ------------------------------------------------------------ the log

    async def async_read_events(self) -> None:
        """Read the first page of the log. A failure is quiet: the next try is later."""
        async with self._events_lock:
            try:
                page = await self._coordinator.client.get_events(0, EVENT_LOG_SIZE)
            except AquastillaError as err:
                _LOGGER.debug("Could not read the notifications: %s", type(err).__name__)
                return
            self._events_read = dt_util.utcnow()
            self.events = page.events[:EVENT_LOG_SIZE]
            self.unseen = page.unseen
        self._notify()

    def on_coordinator_update(self) -> None:
        """While the channel is down, keep the log roughly current from time to time."""
        # A device that reads as not updating has no progress any more.
        for uuid, data in self._coordinator.data.devices.items():
            if data.state.is_update is False:
                self.progress.pop(uuid, None)
        if self._websocket.connected:
            return
        read = self._events_read
        if read is None or dt_util.utcnow() - read >= EVENT_REFRESH_INTERVAL:
            self._spawn(self.async_read_events(), "aquastilla read notifications")

    # -------------------------------------------------------------- frames

    def _on_event(self, body: object) -> None:
        event = VendorEvent.from_push(body)
        if event.event_id is None and event.title is None and event.body is None:
            return
        known = {e.event_id for e in self.events if e.event_id is not None}
        if event.event_id is not None and event.event_id in known:
            return  # the same notification came by push and by the log
        self.events = (event, *self.events)[:EVENT_LOG_SIZE]
        for uuid in self.devices_for(event):
            for listener in list(self._event_listeners):
                try:
                    listener(uuid, event)
                except Exception:
                    _LOGGER.exception("A notification listener failed")
        self._notify()

    def _on_percentage(self, body: object) -> None:
        reading = FirmwareProgress.from_json(body)
        uuid = self._known_uuid(reading.uuid)
        if uuid is None or reading.percentage is None:
            return
        self.progress[uuid] = min(max(reading.percentage, 0.0), 100.0)
        if self.progress[uuid] >= 100.0:
            # Done: the state says whether the device still reports an update.
            self._spawn(
                self._coordinator.async_refresh_state(uuid, force=True),
                "aquastilla refresh after update",
            )
        self._notify()

    def _on_connection(self, connected: bool) -> None:
        if not connected:
            return
        # Whatever was missed while the channel was down is read again. The cooldown
        # of the refresh makes this free right after a poll.
        self._spawn(self._catch_up(), "aquastilla catch up")

    def _on_auth_failed(self) -> None:
        self._entry.async_start_reauth(self._hass)

    async def _catch_up(self) -> None:
        read = self._events_read
        if read is None or dt_util.utcnow() - read >= PUSH_REFRESH_COOLDOWN:
            await self.async_read_events()
        for uuid in list(self._coordinator.data.devices):
            await self._coordinator.async_refresh_state(uuid)
            await self._coordinator.async_refresh_settings(uuid)
        self.catch_ups += 1

    # ----------------------------------------------------------- internals

    def _known_uuid(self, uuid: str | None) -> str | None:
        if uuid is None:
            return None
        wanted = uuid.lower()
        for known in self._coordinator.data.devices:
            if known.lower() == wanted:
                return known
        return None

    def _spawn(self, coro: object, name: str) -> None:
        self._entry.async_create_background_task(self._hass, coro, name)  # type: ignore[arg-type]

    def _notify(self) -> None:
        for listener in list(self._listeners):
            try:
                listener()
            except Exception:
                _LOGGER.exception("A push listener failed")
