"""The one place that converts between vendor timestamps and Home Assistant datetimes.

Contract
--------
* Instants (events, history, state timestamps) come as ISO-8601 strings or epoch
  numbers. They are returned as timezone-aware UTC ``datetime`` objects. A string
  without an offset is assumed to be UTC, which is what the vendor app does.
  Anything missing, empty, non-positive or unparseable becomes ``None``. A
  "no data" marker is never turned into a real date.
* The preferred regeneration time is a wall-clock time, not an instant. The app
  keeps its local digits and labels them UTC (``02:30`` becomes
  ``...T02:30:00.000Z``). ``parse_wall_clock`` and ``format_wall_clock`` strip and
  re-apply that label; the date part carries no meaning.
* ASSUMPTION, to be confirmed against real traffic in AQ-1.7: the wall-clock time
  is in the device's own timezone (``settings.timezone``), not in Home Assistant's.
  The vendor app ignores that field and shows everything in UTC.
"""

from datetime import UTC, date, datetime, time, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

# Epoch values above this are milliseconds (this threshold is year 5138 in seconds).
_MILLISECOND_THRESHOLD = 100_000_000_000


def parse_timestamp(value: object) -> datetime | None:
    """Parse a vendor instant into an aware UTC datetime, or ``None``."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int | float):
        return _from_epoch(value)
    if isinstance(value, str):
        return _from_string(value)
    return None


def to_vendor_timestamp(value: datetime) -> str:
    """Format an aware datetime the way the vendor app does: UTC, milliseconds, ``Z``."""
    if value.tzinfo is None:
        msg = "naive datetime has no defined instant"
        raise ValueError(msg)
    utc = value.astimezone(UTC)
    return f"{utc:%Y-%m-%dT%H:%M:%S}.{utc.microsecond // 1000:03d}Z"


def parse_wall_clock(value: object) -> time | None:
    """Read a regeneration time: keep the digits, drop the UTC label and the date."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip())
    except ValueError:
        return None
    return parsed.time().replace(second=0, microsecond=0, tzinfo=None)


def format_wall_clock(value: time, *, on: date) -> str:
    """Write a wall-clock time the way the app does: its digits, labelled UTC."""
    return f"{on:%Y-%m-%d}T{value.hour:02d}:{value.minute:02d}:00.000Z"


def device_zone(name: object, fallback: tzinfo) -> tzinfo:
    """Resolve the device's timezone name, falling back when absent or unknown."""
    if not isinstance(name, str) or not name:
        return fallback
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError, ValueError, OSError:
        return fallback


def wall_clock_to_utc(value: time, zone: tzinfo, *, on: date) -> datetime:
    """The UTC instant at which ``zone``'s clock shows ``value`` on day ``on``.

    A time skipped by a spring-forward gap is read with the offset in force before
    the gap, so it lands an hour later on the clock. An ambiguous time during a
    fall-back is read as its first occurrence.
    """
    local = datetime.combine(on, value.replace(tzinfo=None), tzinfo=zone)
    return local.astimezone(UTC)


def utc_to_wall_clock(value: datetime, zone: tzinfo) -> time:
    """The time shown on ``zone``'s clock at the aware instant ``value``."""
    if value.tzinfo is None:
        msg = "naive datetime has no defined instant"
        raise ValueError(msg)
    return value.astimezone(zone).time().replace(second=0, microsecond=0, tzinfo=None)


def _from_epoch(value: float) -> datetime | None:
    if value <= 0:
        return None
    seconds = value / 1000 if value > _MILLISECOND_THRESHOLD else value
    try:
        return datetime.fromtimestamp(seconds, UTC)
    except OverflowError, OSError, ValueError:
        return None


def _from_string(value: str) -> datetime | None:
    text = value.strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)
