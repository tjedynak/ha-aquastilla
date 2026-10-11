"""Water consumption as long-term statistics, so the water panel has a history.

The device reports consumption as series of buckets, newest first: 24 hours, 7 days,
31 days and 12 months (``/water_data``), and the twelve months of the previous calendar
year (``/year/water_data``). Each series' timestamp is the current period rounded
down, so bucket 0 is the period still running and bucket ``i`` the one ``i`` periods
before. Assumptions that a manual check against the app should confirm (#67):

* bucket 0 is in progress, so it is never imported; a bucket is final only once it is
  followed by another;
* a day or month bucket is a calendar day or month in the device's timezone.

Rows are written once. The newest row already stored (``after``) decides what is new,
so a restart, a second poll or a re-read of the same series writes nothing twice, and a
gap in the device's data stays a gap instead of becoming a jump in the running total.
Coarser buckets fill only the time the finer series do not reach. A day or month that
straddles the start of the finer layer contributes what is left of it after the finer
buckets inside it are taken off, placed at its start, so nothing is counted twice and
nothing is dropped. The consumption of that part is therefore shown at midnight or at
the first of the month, not spread over it.

The hourly series has to be there: without it nothing is imported, because a coarse row
would be the newest stored row and the hours that come later would count it again.

Everything is stored in litres. The device's unit may change between two polls, a
statistic's unit may not.
"""

import logging
import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta, tzinfo
from typing import TYPE_CHECKING

from homeassistant.const import UnitOfVolume
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.recorder import get_instance

from .api import AquastillaError
from .const import DOMAIN
from .coordinator import AquastillaCoordinator, DeviceData
from .models import WaterSeries
from .timeutil import parse_timestamp, wall_clock_to_utc

if TYPE_CHECKING:
    from homeassistant.components.recorder.models import StatisticData

_LOGGER = logging.getLogger(__name__)

HOUR_SERIES = "WATER_HOUR_DATA"
DAY_SERIES = "WATER_MONTH_DATA"  # 31 daily buckets, despite the name
MONTH_SERIES = "WATER_12_MONTH_DATA"
SERIES = (HOUR_SERIES, DAY_SERIES, MONTH_SERIES)

_HOUR = timedelta(hours=1)
_MIDNIGHT = time(0)


@dataclass(frozen=True, slots=True)
class Bucket:
    """Consumption of one finished period. ``start`` is an aware UTC instant."""

    start: datetime
    litres: float


def statistic_id(uuid: str) -> str:
    """The id of the device's statistic, valid under Home Assistant's rules."""
    slug = re.sub(r"[^a-z0-9]+", "_", uuid.lower()).strip("_")
    return f"{DOMAIN}:{slug}_water"


def litres_factor(volume_unit: str | None) -> float:
    """What turns the device's numbers into litres: cubic metres are times a thousand."""
    if (volume_unit or "").lower() in {"m3", "m³"}:
        return 1000.0
    return 1.0


def _add_months(day: date, months: int) -> date:
    index = day.year * 12 + day.month - 1 + months
    year, month = divmod(index, 12)
    return date(year, month + 1, 1)


def _anchor(series: Mapping[str, WaterSeries]) -> datetime | None:
    """The device's current time as the series state it, digits in the device's zone."""
    for name in SERIES:
        found = series.get(name)
        if found is not None and found.timestamp is not None:
            return found.timestamp
    return None


@dataclass(frozen=True, slots=True)
class _Frame:
    """What every layer needs to cut the series into buckets."""

    anchor: datetime  # the series' "now", digits in the device's zone
    zone: tzinfo
    factor: float

    def midnight(self, day: date) -> datetime:
        return wall_clock_to_utc(_MIDNIGHT, self.zone, on=day)


Span = tuple[datetime, datetime]


def _cut(
    frame: _Frame,
    values: Sequence[float | None],
    span: Callable[[int], Span],
    bound: datetime,
    finer: Sequence[Bucket],
) -> tuple[list[Bucket], datetime]:
    """The finished buckets of one layer that end where the finer layers begin.

    ``span(i)`` is the start and end of bucket ``i``; bucket 0 is still running and is
    skipped. A bucket that straddles ``bound`` contributes what is left of it after the
    finer buckets inside it are taken off, placed at its start, so the part of a day or a
    month the finer layers do not reach is not lost. Returns the buckets and where the
    layer begins.
    """
    found: list[Bucket] = []
    begin = bound
    for i, value in enumerate(values):
        if i == 0:
            continue
        start, end = span(i)
        begin = min(begin, start)
        if value is None:
            continue
        litres = value * frame.factor
        if end <= bound:
            found.append(Bucket(start, litres))
        elif start < bound:
            rest = litres - sum(b.litres for b in finer if start <= b.start < end)
            if rest > 0:
                found.append(Bucket(start, rest))
    return found, begin


def _hours(frame: _Frame, series: WaterSeries) -> tuple[list[Bucket], datetime]:
    current = wall_clock_to_utc(time(frame.anchor.hour), frame.zone, on=frame.anchor.date())
    return _cut(
        frame,
        series.values,
        lambda i: (current - i * _HOUR, current - (i - 1) * _HOUR),
        current,
        (),
    )


def _days(
    frame: _Frame, series: WaterSeries | None, bound: datetime, finer: Sequence[Bucket]
) -> tuple[list[Bucket], datetime]:
    def span(i: int) -> Span:
        day = frame.anchor.date() - timedelta(days=i)
        return frame.midnight(day), frame.midnight(day + timedelta(days=1))

    return _cut(frame, series.values if series else (), span, bound, finer)


def _months(
    frame: _Frame, series: WaterSeries | None, bound: datetime, finer: Sequence[Bucket]
) -> tuple[list[Bucket], datetime]:
    first = frame.anchor.date().replace(day=1)

    def span(i: int) -> Span:
        month = _add_months(first, -i)
        return frame.midnight(month), frame.midnight(_add_months(month, 1))

    return _cut(frame, series.values if series else (), span, bound, finer)


def _year(
    frame: _Frame, year: Mapping[str, WaterSeries], bound: datetime, finer: Sequence[Bucket]
) -> list[Bucket]:
    found: list[Bucket] = []
    for key, series in year.items():
        when = parse_timestamp(key)
        if when is None:
            continue
        # The app files the series under the year before the one in the key, and the
        # values run from December back to January.
        december = date(when.year - 1, 12, 1)

        def span(i: int, december: date = december) -> Span:
            month = _add_months(december, 1 - i)
            return frame.midnight(month), frame.midnight(_add_months(month, 1))

        # ``_cut`` skips bucket 0, which here is a finished month: shift by one.
        cut, _ = _cut(frame, [None, *series.values[:12]], span, bound, finer)
        found += cut
    return found


def plan(
    data: DeviceData, year: Mapping[str, WaterSeries] | None, after: datetime | None
) -> list[Bucket]:
    """The finished buckets not stored yet, oldest first, in litres.

    Without the hourly series nothing is planned: coarser rows would end up as the
    newest stored row, and the hours that arrive later would count the same litres
    again. Rows that do not start on a whole UTC hour are dropped, as the recorder
    refuses them (zones with a half hour offset).
    """
    series = data.water
    anchor = _anchor(series)
    hours = series.get(HOUR_SERIES)
    if anchor is None or hours is None:
        return []
    frame = _Frame(
        anchor=anchor,
        zone=data.timezone,
        factor=litres_factor(data.settings.volume_unit),
    )
    found, bound = _hours(frame, hours)
    days, bound = _days(frame, series.get(DAY_SERIES), bound, found)
    found += days
    months, bound = _months(frame, series.get(MONTH_SERIES), bound, found)
    found += months
    found += _year(frame, year or {}, bound, found)
    earliest = None if after is None else after + _HOUR
    wanted = [
        b
        for b in found
        if b.start.minute == 0 and b.start.second == 0 and (earliest is None or b.start >= earliest)
    ]
    wanted.sort(key=lambda bucket: bucket.start)
    return wanted


def rows(buckets: Iterable[Bucket], base: float) -> list[StatisticData]:
    """Statistic rows with a running total that continues from ``base``."""
    total = base
    out: list[StatisticData] = []
    for bucket in buckets:
        total += max(bucket.litres, 0.0)
        out.append({"start": bucket.start, "state": total, "sum": total})
    return out


class WaterStatistics:
    """Writes each device's finished consumption buckets to the recorder.

    Runs when the coordinator has new data, but looks at the recorder only when a
    device's series moved on, which is hourly.
    """

    def __init__(self, hass: HomeAssistant, coordinator: AquastillaCoordinator) -> None:
        self._hass = hass
        self._coordinator = coordinator
        self._handled: dict[str, datetime | None] = {}
        self._running = False

    async def async_update(self) -> None:
        if self._running:
            return
        self._running = True
        try:
            for uuid, data in list(self._coordinator.data.devices.items()):
                try:
                    await self._async_device(uuid, data)
                except HomeAssistantError, OverflowError, ValueError:
                    # One device's odd data must not stop the others.
                    _LOGGER.debug("Could not import the consumption", exc_info=True)
        finally:
            self._running = False

    async def _async_device(self, uuid: str, data: DeviceData) -> None:
        if "recorder" not in self._hass.config.components:
            return  # not an error: there is simply nowhere to put statistics
        # Imported here, not at the top: they pull in the database layer, which is only
        # installed together with the recorder.
        from homeassistant.components.recorder.models import (  # noqa: PLC0415
            StatisticMeanType,
            StatisticMetaData,
        )
        from homeassistant.components.recorder.statistics import (  # noqa: PLC0415
            async_add_external_statistics,
            get_last_statistics,
        )

        anchor = _anchor(data.water)
        if anchor is None or self._handled.get(uuid) == anchor:
            return
        # Handled even when it fails: the next try is the next hour, not the next poll.
        self._handled[uuid] = anchor
        sid = statistic_id(uuid)
        try:
            last = await get_instance(self._hass).async_add_executor_job(
                get_last_statistics, self._hass, 1, sid, False, {"sum"}
            )
        except Exception:
            _LOGGER.debug("Could not read the stored statistics", exc_info=True)
            return
        stored = last.get(sid) or []
        after: datetime | None = None
        base = 0.0
        year: Mapping[str, WaterSeries] | None = None
        if stored:
            after = datetime.fromtimestamp(float(stored[0]["start"]), UTC)
            base = float(stored[0].get("sum") or 0.0)
        else:
            # The first import: the previous year is read once, for the history.
            try:
                year = await self._coordinator.client.get_year_water_data(uuid)
            except AquastillaError as err:
                _LOGGER.debug("Could not read the yearly series: %s", type(err).__name__)
                return
        buckets = plan(data, year, after)
        if not buckets:
            return
        metadata = StatisticMetaData(
            has_sum=True,
            mean_type=StatisticMeanType.NONE,
            name=f"{data.device.name or 'Aquastilla'} water",
            source=DOMAIN,
            statistic_id=sid,
            unit_class="volume",
            unit_of_measurement=UnitOfVolume.LITERS,
        )
        async_add_external_statistics(self._hass, metadata, rows(buckets, base))
        _LOGGER.debug("Imported %d consumption buckets", len(buckets))
