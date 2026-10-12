"""Water consumption: today, and the last 31 days.

The series are in the unit of the device's volume setting, litres unless the owner
switched to cubic metres in the app (ADR-15). The figure of today in ``/state`` is not:
like ``waterLeft`` it is always in cubic metres (checked on a real device, where it
tracked the fall of ``waterLeft`` to the fourth decimal), so it is converted.

Today only grows and drops to zero when the day rolls over, which is what
``total_increasing`` describes. The 31 day figure is a sliding window: old days fall
off the end, so it can go down, and it must not be declared as a total or Home Assistant
would book the drop as a reset and count the next reading as new consumption. It is a
plain measurement; the energy dashboard is fed from the long-term statistics instead.
"""

from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass
from homeassistant.const import UnitOfVolume

from .coordinator import DeviceData
from .sensor_description import AquastillaSensorDescription

HOURS_SERIES = "WATER_HOUR_DATA"  # 24 hourly values
WEEK_SERIES = "WATER_DAY_DATA"  # 7 daily values
DAYS_SERIES = "WATER_MONTH_DATA"  # 31 daily values
MONTHS_SERIES = "WATER_12_MONTH_DATA"  # 12 monthly values


def volume_unit(data: DeviceData) -> str:
    """The unit the service reports consumption in; litres when it says nothing useful."""
    if (data.settings.volume_unit or "").lower() in {"m3", "m³"}:
        return UnitOfVolume.CUBIC_METERS
    return UnitOfVolume.LITERS


def today_in_unit(data: DeviceData) -> float | None:
    """What the state reports for today, in the unit the sensor is shown in."""
    reported = data.state.today_water_usage  # cubic metres
    if reported is None:
        return None
    factor = 1.0 if volume_unit(data) == UnitOfVolume.CUBIC_METERS else 1000.0
    return round(reported * factor, 3)


def _series(data: DeviceData, name: str) -> list[float | None] | None:
    found = data.water.get(name)
    if found is None:
        return None
    return [None if v is None else round(v, 3) for v in found.values]


def _today_attributes(data: DeviceData) -> dict[str, object]:
    """The series behind the sensor, newest first, to compare with the vendor's app."""
    series = data.water.get(HOURS_SERIES)
    return {
        "reported_m3": data.state.today_water_usage,
        "hourly": _series(data, HOURS_SERIES),
        "daily": _series(data, WEEK_SERIES),
        "series_time": None
        if series is None or series.timestamp is None
        else series.timestamp.isoformat(),
    }


def _window_attributes(data: DeviceData) -> dict[str, object]:
    return {"daily": _series(data, DAYS_SERIES), "monthly": _series(data, MONTHS_SERIES)}


def _window_total(data: DeviceData) -> float | None:
    series = data.water.get(DAYS_SERIES)
    if series is None:
        return None
    values = [v for v in series.values if v is not None]
    return round(sum(values), 3) if values else None


WATER_SENSORS: tuple[AquastillaSensorDescription, ...] = (
    AquastillaSensorDescription(
        key="water_today",
        translation_key="water_today",
        device_class=SensorDeviceClass.WATER,
        state_class=SensorStateClass.TOTAL_INCREASING,
        unit_fn=volume_unit,
        value_fn=today_in_unit,
        attributes_fn=_today_attributes,
    ),
    AquastillaSensorDescription(
        key="water_31_days",
        translation_key="water_31_days",
        state_class=SensorStateClass.MEASUREMENT,
        unit_fn=volume_unit,
        value_fn=_window_total,
        attributes_fn=_window_attributes,
    ),
)
