"""The description type shared by the sensor groups."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from homeassistant.components.sensor import SensorEntityDescription
from homeassistant.helpers.typing import StateType

from .coordinator import DeviceData


@dataclass(frozen=True, kw_only=True)
class AquastillaSensorDescription(SensorEntityDescription):
    """A sensor that reads one value from a device's data.

    ``value_fn`` returns ``None`` for "no data", which Home Assistant shows as unknown.
    It must never turn a missing value into a number or a date.
    """

    value_fn: Callable[[DeviceData], StateType | datetime]
    attributes_fn: Callable[[DeviceData], Mapping[str, Any]] | None = None
