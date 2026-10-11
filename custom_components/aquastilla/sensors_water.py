"""Water consumption: today, and the last 31 days.

The values are in the unit of the device's volume setting, litres unless the owner
switched to cubic metres in the app (ADR-15).

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

DAYS_SERIES = "WATER_MONTH_DATA"  # 31 daily values


def volume_unit(data: DeviceData) -> str:
    """The unit the service reports consumption in; litres when it says nothing useful."""
    if (data.settings.volume_unit or "").lower() in {"m3", "m³"}:
        return UnitOfVolume.CUBIC_METERS
    return UnitOfVolume.LITERS


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
        value_fn=lambda data: data.state.today_water_usage,
    ),
    AquastillaSensorDescription(
        key="water_31_days",
        translation_key="water_31_days",
        state_class=SensorStateClass.MEASUREMENT,
        unit_fn=volume_unit,
        value_fn=_window_total,
    ),
)
