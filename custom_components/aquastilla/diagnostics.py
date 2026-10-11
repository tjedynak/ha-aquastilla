"""Diagnostics download for bug reports.

The report is built from an allow-list of fields, not by stripping fields from a dump,
so a field added to a model later cannot leak by accident. Device identities are
replaced by ``device_1``, ``device_2`` and so on. Names, notes, addresses, the account
e-mail, the password and the token never appear.
"""

from collections.abc import Iterable
from typing import Any

from homeassistant.core import HomeAssistant

from . import AquastillaConfigEntry
from .coordinator import AquastillaCoordinator, DeviceData

REDACTED = "**REDACTED**"


def _iso(value: Any) -> str | None:
    return None if value is None else value.isoformat()


def _device(data: DeviceData) -> dict[str, Any]:
    device, state, settings, cycles = data.device, data.state, data.settings, data.current_states
    return {
        "model": device.model,
        "brand": device.brand,
        "history": {
            "regeneration": _iso(device.history.regeneration),
            "salt_replenishment": _iso(device.history.salt_replenishment),
            "inspection": _iso(device.history.inspection),
            "head_error": _iso(device.history.head_error),
            "flood": _iso(device.history.flood),
        },
        "salt_notifications": {
            "push": device.salt_notifications.push,
            "email": device.salt_notifications.email,
        },
        "flood_notifications": {
            "push": device.flood_notifications.push,
            "email": device.flood_notifications.email,
        },
        "state": {
            "cycle": None if state.cycle is None else str(state.cycle),
            "is_online": state.is_online,
            "is_update": state.is_update,
            "water_left": state.water_left,
            "water_left_max": state.water_left_max,
            "salt_percent": state.salt_percent,
            "salt_days": state.salt_days,
            "salt_days_max": state.salt_days_max,
            "regeneration_percent": state.regeneration_percent,
            "expected_regeneration": _iso(state.expected_regeneration),
            "timestamp": _iso(state.timestamp),
        },
        "current_states": {
            "current": None if cycles.current is None else str(cycles.current),
            "next": None if cycles.next is None else str(cycles.next),
        },
        "settings": {
            "vacation_mode": settings.vacation_mode,
            "water_flow": settings.water_flow,
            "service_mode": settings.service_mode,
            "service_mode_ends": _iso(settings.service_mode_ends),
            "water_hardness": settings.water_hardness,
            "water_hardness_unit": settings.water_hardness_unit,
            "volume_unit": settings.volume_unit,
            "regeneration_time": _iso(settings.regeneration_time),
            "salt_alarm_level": settings.salt_alarm_level,
            "flood_alarm": {
                "continuous_flow_time": settings.flood_alarm.continuous_flow_time,
                "threshold": settings.flood_alarm.threshold,
                "max_flow": settings.flood_alarm.max_flow,
            },
            "timezone": settings.timezone,
        },
    }


def _coordinator(coordinator: AquastillaCoordinator) -> dict[str, Any]:
    interval = coordinator.update_interval
    exception = coordinator.last_exception
    return {
        "last_update_success": coordinator.last_update_success,
        # Only the class: the message may carry a request path with a device id in it.
        "last_exception": None if exception is None else type(exception).__name__,
        "update_interval_seconds": None if interval is None else interval.total_seconds(),
        "base_interval_seconds": coordinator.base_interval.total_seconds(),
        "consecutive_failures": coordinator.failures,
    }


def pseudonyms(uuids: Iterable[str]) -> dict[str, str]:
    """``device_1``, ``device_2``... in the order of the sorted ids."""
    return {uuid: f"device_{index}" for index, uuid in enumerate(sorted(uuids), 1)}


def _entry(entry: AquastillaConfigEntry) -> dict[str, Any]:
    """Only what is safe by construction. Credentials and identity are never copied."""
    return {
        "title": REDACTED,
        "unique_id": REDACTED,
        "version": entry.version,
        "state": entry.state.value,
        "data": dict.fromkeys(entry.data, REDACTED),  # the key names, never the values
    }


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: AquastillaConfigEntry
) -> dict[str, Any]:
    """Return what a bug report needs, without identities or secrets.

    Works for an entry that failed to set up too, which is when a report is wanted most.
    """
    coordinator: AquastillaCoordinator | None = getattr(entry, "runtime_data", None)
    report: dict[str, Any] = {"entry": _entry(entry)}
    if coordinator is None:
        return {
            **report,
            "coordinator": None,
            "devices": {},
            "pending_commands": None,
            "websocket": None,
        }

    names = pseudonyms(coordinator.data.devices)
    tracker = coordinator.command_tracker
    websocket = coordinator.websocket
    return {
        **report,
        "coordinator": _coordinator(coordinator),
        "devices": {names[uuid]: _device(data) for uuid, data in coordinator.data.devices.items()},
        "pending_commands": None
        if tracker is None
        else {
            names[uuid]: sorted(tracker.pending(uuid)) for uuid in names if tracker.pending(uuid)
        },
        "websocket": None
        if websocket is None
        else {"connected": websocket.connected, "malformed_frames": websocket.malformed_frames},
    }
