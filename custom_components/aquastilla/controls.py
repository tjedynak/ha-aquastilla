"""Running a command: gates first, then the tracker, then readable errors.

Every write entity and service action goes through ``async_run``, so a command is
never sent without the gate check and always waits for the device's acknowledgement
(ADR-2). Nothing here changes the data optimistically. When the device does not
confirm in time, the data is read again and entities show what the device reports.
"""

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from homeassistant.exceptions import HomeAssistantError

from .api import AquastillaError
from .commands import CommandPendingError, CommandResult, CommandStatus
from .const import DOMAIN
from .coordinator import AquastillaCoordinator
from .gates import Command, blocked_reason
from .models import DeviceEventType

_LOGGER = logging.getLogger(__name__)


def refusal(key: str, **placeholders: str) -> HomeAssistantError:
    """An error with a translated message."""
    return HomeAssistantError(
        translation_domain=DOMAIN, translation_key=key, translation_placeholders=placeholders
    )


async def async_run(  # noqa: PLR0913
    coordinator: AquastillaCoordinator,
    uuid: str,
    command: Command,
    send: Callable[[], Awaitable[Any]],
    *,
    event_type: DeviceEventType | None = None,
    refresh: Callable[[], Awaitable[None]] | None = None,
) -> CommandResult | None:
    """Send a command to a device and wait for it to be acknowledged.

    ``event_type`` is the acknowledgement the device sends back. Without one the command
    is not tracked (a rename has none) and the data is read again with ``refresh``, by
    default a normal poll.

    Raises ``HomeAssistantError`` when the device cannot take the command, when the same
    command is already waiting, or when the request fails. An unconfirmed command is not
    an error: it may well have worked, and the refreshed data says.
    """
    data = coordinator.data.devices.get(uuid) if coordinator.data else None
    if data is None:
        raise refusal("device_unavailable")
    # The gates read settings that can be half an hour old. A command is rare, so the
    # data is read again first (the refresh has its own cooldown).
    await coordinator.async_refresh_settings(uuid)
    await coordinator.async_refresh_state(uuid)
    data = coordinator.data.devices.get(uuid) if coordinator.data else None
    if data is None:
        raise refusal("device_unavailable")
    if (reason := blocked_reason(data, command)) is not None:
        raise refusal(f"blocked_{reason}")

    tracker = coordinator.command_tracker
    try:
        if event_type is not None and tracker is not None:
            result = await tracker.async_send(uuid, event_type, send)
        else:
            await send()
            await (refresh or coordinator.async_request_refresh)()
            return None
    except CommandPendingError as err:
        raise refusal("command_pending") from err
    except AquastillaError as err:
        # The message of the error can carry a request path with a device id in it.
        _LOGGER.debug("%s failed: %s", command, err)
        raise refusal("command_failed", error=type(err).__name__) from err
    if result.status is CommandStatus.UNCONFIRMED:
        _LOGGER.warning(
            "%s was sent but the device did not confirm it in time; showing what it reports",
            command,
        )
    return result
