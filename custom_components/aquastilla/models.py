"""Data models for the vendor API, parsed tolerantly.

The shapes come from static analysis of the vendor app and are still unconfirmed
by real traffic (AQ-1.7), so parsing never trusts them:

* unknown keys are ignored, so a new vendor-side field cannot break anything;
* a missing, null or wrongly typed value becomes ``None``;
* "no data" markers never turn into a zero date or a zero number;
* a cycle or event type the integration does not know is kept as the raw string.

Timestamps from the device (the clock, the expected regeneration, the history) keep
the digits the service wrote, which are local time labelled UTC. A consumer that needs
the real instant uses ``DeviceData.instant``. Push timestamps are real UTC already.

Only ``Device`` needs an identity: a device payload without a ``uuid`` is
unusable and raises ``ModelError``. Everything else degrades to ``None``.
"""

import json
import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, time
from enum import StrEnum
from typing import Any, Self

from .timeutil import parse_timestamp, parse_wall_clock

# ------------------------------------------------------------------- helpers


class ModelError(ValueError):
    """A payload is too broken to build a model from."""


def _obj(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _str(value: object) -> str | None:
    return value if isinstance(value, str) and value.strip() != "" else None


def _bool(value: object) -> bool | None:
    return value if isinstance(value, bool) else None


def _number(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        result = float(value)
    elif isinstance(value, str):
        try:
            result = float(value.strip())
        except ValueError:
            return None
    else:
        return None
    return result if math.isfinite(result) else None


def _int(value: object) -> int | None:
    number = _number(value)
    return None if number is None else int(number)


# ------------------------------------------------------------- enumerations


class Cycle(StrEnum):
    """Head work cycles. The authoritative list comes from ``/mode/all`` at runtime."""

    INITIALIZE = "Initialize"
    SOFTENING = "Softening"
    VACATION_MODE = "Vacation_mode"
    FLOW_CLOSED = "Flow_closed"
    BRINE_REFILL = "Brine_refill"
    SALT_DISSOLVE = "Salt_dissolve"
    BACKWASH = "Backwash"
    BRINE_COLLECT = "Brine_collect"
    FAST_WASH = "Fast_wash"


def _cycle_key(raw: str) -> str:
    key = raw.strip().lower()
    for char in (" ", "_", "-"):
        key = key.replace(char, "")
    return key.removeprefix("devicestate").removeprefix("regen")


_CYCLES = {_cycle_key(c.value): c for c in Cycle}
# The mode list of a real device names two cycles differently from the display names:
# "deviceStateVacation" and "deviceStateClosed". "deviceStateNum" has no known meaning.
_CYCLES.update({"vacation": Cycle.VACATION_MODE, "closed": Cycle.FLOW_CLOSED})


def parse_cycle(raw: object) -> Cycle | str | None:
    """Map either vendor vocabulary to ``Cycle``; keep an unknown value as it came.

    The state endpoint speaks ``deviceStateRegenBrineRefill`` while
    ``current_states`` speaks ``Brine refill``. Both reach the same member.
    """
    text = _str(raw)
    if text is None:
        return None
    text = text.strip()
    return _CYCLES.get(_cycle_key(text), text)


class DeviceEventType(StrEnum):
    """Command acknowledgements and device-originated notifications on the push channel."""

    SET_WATER_HARDNESS = "SET_WATER_HARDNESS"
    SET_VOLUME_UNIT = "SET_VOLUME_UNIT"
    SET_MINIMUM_SALT_LEVEL = "SET_MINIMUM_SALT_LEVEL"
    SET_FLOOD_SETTINGS = "SET_FLOOD_SETTINGS"
    SET_PREFERRED_REGENERATION_TIME = "SET_PREFERRED_REGENERATION_TIME"
    SET_DELAY_REGENERATION = "SET_DELAY_REGENERATION"
    SET_VACATION_MODE = "SET_VACATION_MODE"
    SET_REGENERATION_VACATION_MODE = "SET_REGENERATION_VACATION_MODE"
    SET_REGENERATION_DATE_AS_SERVICE_MAN = "SET_REGENERATION_DATE_AS_SERVICE_MAN"
    SET_WATER_FLOW = "SET_WATER_FLOW"
    SET_DEVICE_MODE = "SET_DEVICE_MODE"
    SET_SERVICE_MODE = "SET_SERVICE_MODE"
    NEW_SETTINGS_ARRIVED = "NEW_SETTINGS_ARRIVED"
    NEW_STATE_ARRIVED = "NEW_STATE_ARRIVED"


def parse_event_type(raw: object) -> DeviceEventType | str | None:
    """Map a push event type to ``DeviceEventType``, keeping unknown values raw."""
    text = _str(raw)
    if text is None:
        return None
    try:
        return DeviceEventType(text)
    except ValueError:
        return text


# ------------------------------------------------------------------- account


@dataclass(frozen=True, slots=True)
class LoginResponse:
    """``POST /login-company``: the app reads only ``jwt``."""

    jwt: str | None

    @classmethod
    def from_json(cls, data: object) -> Self:
        return cls(jwt=_str(_obj(data).get("jwt")))


@dataclass(frozen=True, slots=True)
class UserDetails:
    """``GET /details``."""

    user_id: str | None
    email: str | None
    language: str | None
    phone: str | None
    default_device_id: str | None

    @classmethod
    def from_json(cls, data: object) -> Self:
        raw = _obj(data)
        return cls(
            user_id=_str(raw.get("userId")),
            email=_str(raw.get("email")),
            language=_str(raw.get("language")),
            phone=_str(raw.get("phone")),
            default_device_id=_str(raw.get("defaultDeviceId")),
        )


@dataclass(frozen=True, slots=True)
class VendorError:
    """The ``{name, message, code}`` body of a 4xx response. ``code == -3`` means the
    device is no longer attached to the account."""

    name: str | None
    message: str | None
    code: int | None

    DEVICE_DETACHED = -3

    @classmethod
    def from_json(cls, data: object) -> Self:
        raw = _obj(data)
        return cls(
            name=_str(raw.get("name")),
            message=_str(raw.get("message")),
            code=_int(raw.get("code")),
        )

    @property
    def is_device_detached(self) -> bool:
        return self.code == self.DEVICE_DETACHED


# -------------------------------------------------------------------- device


@dataclass(frozen=True, slots=True)
class NotificationSettings:
    """Whether the vendor sends push or e-mail for an alarm."""

    push: bool | None
    email: bool | None

    @classmethod
    def from_json(cls, data: object) -> Self:
        raw = _obj(data)
        return cls(
            push=_bool(raw.get("pushNotification")), email=_bool(raw.get("emailNotification"))
        )


@dataclass(frozen=True, slots=True)
class DeviceHistory:
    """Timestamps of past events. ``None`` is "no data"."""

    regeneration: datetime | None
    salt_replenishment: datetime | None
    inspection: datetime | None
    head_error: datetime | None
    flood: datetime | None

    @classmethod
    def from_json(cls, data: object) -> Self:
        raw = _obj(data)
        return cls(
            regeneration=parse_timestamp(raw.get("regeneration")),
            salt_replenishment=parse_timestamp(raw.get("saltReplenishment")),
            inspection=parse_timestamp(raw.get("inspection")),
            head_error=parse_timestamp(raw.get("headError")),
            flood=parse_timestamp(raw.get("flood")),
        )


@dataclass(frozen=True, slots=True)
class Device:
    """``GET /device/{id}`` and each item of ``GET /device/all``."""

    uuid: str
    serial: str | None
    name: str | None
    note: str | None
    ip_address: str | None
    mac_address: str | None
    model: str | None
    brand: str | None
    brand_service_url: str | None
    history: DeviceHistory
    salt_notifications: NotificationSettings
    flood_notifications: NotificationSettings

    @classmethod
    def from_json(cls, data: object) -> Self:
        raw = _obj(data)
        uuid = _str(raw.get("uuid"))
        if uuid is None:
            msg = "device payload has no uuid"
            raise ModelError(msg)
        return cls(
            uuid=uuid,
            serial=_str(raw.get("serial")),
            name=_str(raw.get("name")),
            note=_str(raw.get("note")),
            ip_address=_str(raw.get("ipAddress")),
            mac_address=_str(raw.get("macAddress")),
            model=_str(_obj(raw.get("model")).get("model")),
            brand=_str(_obj(raw.get("brand")).get("name")),
            brand_service_url=_str(_obj(raw.get("brand")).get("serviceUrl")),
            history=DeviceHistory.from_json(raw.get("deviceHistory")),
            salt_notifications=NotificationSettings.from_json(
                raw.get("saltAlarmNotificationSettings")
            ),
            flood_notifications=NotificationSettings.from_json(
                raw.get("floodAlarmNotificationSettings")
            ),
        )


def parse_device_list(data: object) -> list[Device]:
    """Parse ``GET /device/all``. Items without a uuid cannot be used and are skipped."""
    if not isinstance(data, list):
        return []
    devices = []
    for item in data:
        try:
            devices.append(Device.from_json(item))
        except ModelError:
            continue
    return devices


@dataclass(frozen=True, slots=True)
class DeviceState:
    """``GET /device/{id}/state``."""

    uuid: str | None
    cycle: Cycle | str | None
    is_online: bool | None
    is_update: bool | None
    firmware_update_percentage: float | None
    water_left: float | None
    water_left_max: float | None
    salt_percent: float | None
    salt_days: int | None
    salt_days_max: int | None
    regeneration_percent: float | None
    expected_regeneration: datetime | None
    timestamp: datetime | None
    today_water_usage: float | None
    current_water_usage: float | None

    @classmethod
    def from_json(cls, data: object) -> Self:
        raw = _obj(data)
        return cls(
            uuid=_str(raw.get("uuid")),
            cycle=parse_cycle(raw.get("state")),
            is_online=_bool(raw.get("isOnline")),
            is_update=_bool(raw.get("isUpdate")),
            firmware_update_percentage=_number(raw.get("firmwareUpdatePercentage")),
            water_left=_number(raw.get("waterLeft")),
            water_left_max=_number(raw.get("waterLeftMax")),
            salt_percent=_number(raw.get("saltPercent")),
            salt_days=_int(raw.get("saltDays")),
            salt_days_max=_int(raw.get("saltDaysMax")),
            regeneration_percent=_number(raw.get("regenPercentage")),
            expected_regeneration=parse_timestamp(raw.get("expectedRegenerationDate")),
            timestamp=parse_timestamp(raw.get("timestamp")),
            today_water_usage=_number(raw.get("todayWaterUsage")),
            current_water_usage=_number(raw.get("currentWaterUsage")),
        )


@dataclass(frozen=True, slots=True)
class FloodAlarm:
    """Leak alarm thresholds. A value of ``0`` disables that part of the alarm."""

    continuous_flow_time: float | None
    threshold: float | None
    max_flow: float | None

    @classmethod
    def from_json(cls, data: object) -> Self:
        raw = _obj(data)
        return cls(
            continuous_flow_time=_number(raw.get("continuousFlowTime")),
            threshold=_number(raw.get("threshold")),
            max_flow=_number(raw.get("maxFlow")),
        )


@dataclass(frozen=True, slots=True)
class DeviceSettings:
    """``GET /device/{id}/settings``."""

    vacation_mode: bool | None
    water_flow: bool | None
    service_mode: bool | None
    service_mode_ends: datetime | None
    water_hardness: float | None
    water_hardness_unit: str | None
    volume_unit: str | None
    regeneration_time: time | None
    salt_alarm_level: int | None
    flood_alarm: FloodAlarm
    timezone: str | None

    @classmethod
    def from_json(cls, data: object) -> Self:
        raw = _obj(data)
        return cls(
            vacation_mode=_bool(raw.get("vacationMode")),
            water_flow=_bool(raw.get("waterFlow")),
            service_mode=_bool(raw.get("serviceMode")),
            service_mode_ends=parse_timestamp(raw.get("serviceModeEndingTime")),
            water_hardness=_number(raw.get("waterHardness")),
            water_hardness_unit=_str(raw.get("waterHardnessUnit")),
            volume_unit=_str(raw.get("unitOfVolume")),
            regeneration_time=parse_wall_clock(raw.get("preferredRegenerationTime")),
            salt_alarm_level=_int(
                _obj(raw.get("saltAlarmSettings")).get("minimumSaltLevelPerDays")
            ),
            flood_alarm=FloodAlarm.from_json(raw.get("floodAlarmSettings")),
            timezone=_str(raw.get("timezone")),
        )


@dataclass(frozen=True, slots=True)
class CurrentStates:
    """``GET /device/{id}/current_states``: the running cycle and the next one."""

    current: Cycle | str | None
    next: Cycle | str | None

    @classmethod
    def from_json(cls, data: object) -> Self:
        raw = _obj(data)
        return cls(current=parse_cycle(raw.get("first")), next=parse_cycle(raw.get("second")))


def parse_mode_list(data: object) -> list[Cycle | str]:
    """Parse ``GET /device/{id}/mode/all``, keeping order and dropping blanks."""
    if not isinstance(data, list):
        return []
    modes = (parse_cycle(item) for item in data)
    return [mode for mode in modes if mode is not None]


# --------------------------------------------------------------- water usage


@dataclass(frozen=True, slots=True)
class WaterSeries:
    """One time series. ``values`` keeps the vendor order, which is newest first."""

    values: tuple[float | None, ...]
    chart_line: float | None
    timestamp: datetime | None

    @classmethod
    def from_json(cls, data: object) -> Self:
        raw = _obj(data)
        values = raw.get("data")
        return cls(
            values=tuple(_number(v) for v in values) if isinstance(values, list) else (),
            chart_line=_number(raw.get("chartLine")),
            timestamp=parse_timestamp(raw.get("timestamp")),
        )


def parse_water_data(data: object) -> dict[str, WaterSeries]:
    """Parse ``/water_data``: series keyed by name, such as ``WATER_HOUR_DATA``.

    Volume (``WATER_*``) and flow rate (``FLOW_*``) are different quantities.
    Unknown series names are kept; callers pick the ones they know.
    """
    return {str(name): WaterSeries.from_json(series) for name, series in _obj(data).items()}


def parse_year_water_data(data: object) -> dict[str, WaterSeries]:
    """Parse ``/year/water_data``: one series per date-like key.

    A real device answers with a single key, a date, whose value is a series of twelve
    values, not a group of series.
    """
    return {str(key): WaterSeries.from_json(value) for key, value in _obj(data).items()}


@dataclass(frozen=True, slots=True)
class UsagePair:
    """Two periods of one series from ``previous_current_water_usage``."""

    first: float | None
    second: float | None

    @classmethod
    def from_json(cls, data: object) -> Self:
        raw = _obj(data)
        return cls(first=_number(raw.get("first")), second=_number(raw.get("second")))


def parse_usage_comparison(data: object) -> dict[str, UsagePair]:
    return {str(name): UsagePair.from_json(pair) for name, pair in _obj(data).items()}


# -------------------------------------------------------------------- events


@dataclass(frozen=True, slots=True)
class VendorEvent:
    """An item of ``/event/pageable``, also the body of a ``/device/event`` push."""

    event_id: str | None
    kind: str | None
    seen: bool | None
    title: str | None
    body: str | None
    date: datetime | None
    device_name: str | None

    @classmethod
    def from_json(cls, data: object) -> Self:
        raw = _obj(data)
        return cls(
            event_id=_str(raw.get("eventId")),
            kind=_str(raw.get("type")),
            seen=_bool(raw.get("seen")),
            title=_str(raw.get("title")),
            body=_str(raw.get("body")),
            date=parse_timestamp(raw.get("date")),
            device_name=_str(_obj(raw.get("additionalData")).get("deviceName")),
        )

    @classmethod
    def from_push(cls, body: object) -> Self:
        """Parse the body of a ``/device/event`` frame."""
        return cls.from_json(decode_push_body(body))


@dataclass(frozen=True, slots=True)
class EventPage:
    """``GET /event/pageable``."""

    events: tuple[VendorEvent, ...] = field(default_factory=tuple)
    unseen: int | None = None

    @classmethod
    def from_json(cls, data: object) -> Self:
        raw = _obj(data)
        content = _obj(raw.get("page")).get("content")
        events = (
            tuple(VendorEvent.from_json(item) for item in content)
            if isinstance(content, list)
            else ()
        )
        return cls(events=events, unseen=_int(raw.get("unseen")))


# ----------------------------------------------------------------- WebSocket


def decode_push_body(body: object) -> object:
    """Push bodies arrive as JSON-encoded strings, but accept objects too.

    Returns ``None`` for text that is not valid JSON.
    """
    if isinstance(body, str):
        try:
            return json.loads(body)
        except json.JSONDecodeError:
            return None
    return body


@dataclass(frozen=True, slots=True)
class EventSeen:
    """``/device/event_seen``: a command was applied or the device changed something."""

    event_type: DeviceEventType | str | None
    uuid: str | None
    timestamp: datetime | None
    params: object

    @classmethod
    def from_json(cls, data: object) -> Self:
        raw = _obj(decode_push_body(data))
        return cls(
            event_type=parse_event_type(raw.get("deviceEventType")),
            uuid=_str(raw.get("uuid")),
            timestamp=parse_timestamp(raw.get("timestamp")),
            params=raw.get("params"),
        )


@dataclass(frozen=True, slots=True)
class FirmwareProgress:
    """``/device/percentage``. The uuid is lower-cased because the vendor app compares it so."""

    uuid: str | None
    percentage: float | None

    @classmethod
    def from_json(cls, data: object) -> Self:
        raw = _obj(decode_push_body(data))
        uuid = _str(raw.get("uuid"))
        return cls(
            uuid=None if uuid is None else uuid.lower(), percentage=_number(raw.get("percentage"))
        )
