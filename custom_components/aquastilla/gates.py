"""When a device cannot take a command: one table, used by every write entity (ADR-11).

The vendor app disables its controls in these situations, so the integration does the
same instead of sending a command that cannot work. Everything is derived from data the
coordinator already holds, so a gate costs no request.

An offline device is not a gate: the app does not block either, and the command is
queued by the cloud.
"""

from enum import StrEnum

from .coordinator import DeviceData
from .models import Cycle

_REGENERATING = {
    Cycle.BRINE_REFILL,
    Cycle.SALT_DISSOLVE,
    Cycle.BACKWASH,
    Cycle.BRINE_COLLECT,
    Cycle.FAST_WASH,
}


class Command(StrEnum):
    """What can be asked of a device."""

    ADVANCE_CYCLE = "advance_cycle"
    VACATION_MODE = "vacation_mode"
    FORCE_REGENERATION = "force_regeneration"
    DELAY_REGENERATION = "delay_regeneration"
    CLOSE_WATER_FLOW = "close_water_flow"
    SETTING = "setting"  # any of the settings: hardness, units, time, thresholds
    SERVICE = "service"  # notes, names, remote service: always possible


class Reason(StrEnum):
    """Why a command is refused. The value is also the translation key suffix."""

    SERVICE_MODE = "service_mode"
    WATER_FLOW_CLOSED = "water_flow_closed"
    REGENERATING = "regenerating"
    VACATION_MODE = "vacation_mode"


# Commands that need the water to flow: the app disables them once it is closed.
_NEEDS_FLOW = {
    Command.ADVANCE_CYCLE,
    Command.VACATION_MODE,
    Command.FORCE_REGENERATION,
    Command.DELAY_REGENERATION,
    Command.CLOSE_WATER_FLOW,
}
_NEEDS_IDLE = {Command.FORCE_REGENERATION, Command.DELAY_REGENERATION}
_OWNER_CONTROLS = _NEEDS_FLOW | {Command.SETTING}


def is_regenerating(data: DeviceData) -> bool | None:
    """True while a regeneration runs, ``None`` when the cycle is not known."""
    cycle = data.current_states.current or data.state.cycle
    if cycle is None or not isinstance(cycle, Cycle):
        return None  # a cycle we do not know: neither yes nor no
    return cycle in _REGENERATING


def blocked_reason(data: DeviceData, command: Command) -> Reason | None:
    """The reason the device cannot take ``command`` now, or ``None`` when it can.

    Unknown values never block: a device that does not report its settings is not
    treated as being in service mode.
    """
    settings = data.settings
    if command in _OWNER_CONTROLS and settings.service_mode is True:
        return Reason.SERVICE_MODE
    if command in _NEEDS_FLOW and settings.water_flow is False:
        return Reason.WATER_FLOW_CLOSED
    if command in _NEEDS_IDLE and is_regenerating(data) is True:
        return Reason.REGENERATING
    if command is Command.FORCE_REGENERATION and settings.vacation_mode is True:
        return Reason.VACATION_MODE
    return None
