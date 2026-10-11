"""The cycle the head is in and the one that follows.

The state endpoint and the cycle endpoint use two vocabularies, and both map to the
``Cycle`` enum. A value the integration does not know is shown as ``other``, with the
raw text in an attribute, so an unexpected cycle never breaks the entity.
"""

from homeassistant.components.sensor import SensorDeviceClass

from .coordinator import DeviceData
from .models import Cycle
from .sensor_description import AquastillaSensorDescription

OTHER = "other"
CYCLE_OPTIONS = [cycle.value.lower() for cycle in Cycle] + [OTHER]


def cycle_option(value: Cycle | str | None) -> str | None:
    """The enum option for a cycle, ``other`` for one we do not know, None for none."""
    if value is None:
        return None
    if isinstance(value, Cycle):
        return value.value.lower()
    return OTHER


def _current(data: DeviceData) -> Cycle | str | None:
    return data.current_states.current or data.state.cycle


def _raw(value: Cycle | str | None) -> dict[str, str | None]:
    return {"raw_value": None if value is None else str(value)}


CYCLE_SENSORS: tuple[AquastillaSensorDescription, ...] = (
    AquastillaSensorDescription(
        key="cycle",
        translation_key="cycle",
        device_class=SensorDeviceClass.ENUM,
        options=CYCLE_OPTIONS,
        value_fn=lambda data: cycle_option(_current(data)),
        attributes_fn=lambda data: _raw(_current(data)),
    ),
    AquastillaSensorDescription(
        key="next_cycle",
        translation_key="next_cycle",
        device_class=SensorDeviceClass.ENUM,
        options=CYCLE_OPTIONS,
        value_fn=lambda data: cycle_option(data.current_states.next),
        attributes_fn=lambda data: _raw(data.current_states.next),
    ),
)
